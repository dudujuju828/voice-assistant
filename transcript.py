"""Conversation transcripts: persistence, live state, and change notifications.

Every turn — what the user said and what the assistant replied — is recorded
here so it can be viewed as a local web page (see ``transcript_server``). The
store keeps three things:

1. **History on disk.** One JSON file per conversation under
   ``%APPDATA%\\VoiceAssistant\\transcripts``, next to the config and log. A
   conversation maps to one Claude session id, so it carries across turns and
   restarts exactly like the session does; a session reset (or the separate
   coding session) starts a new conversation file. Nothing leaves the machine.

2. **Live state.** The current status (idle / recording / processing / speaking
   / error) and the partial transcript as it streams in, mirroring the tray dot
   so the page can react in real time.

3. **Change notifications.** A tiny publish/subscribe layer (thread-safe
   queues) the HTTP server drains to push Server-Sent Events to open pages.

The store is updated from the Qt thread and read from the HTTP server threads,
so every mutation is guarded by a lock and file writes are atomic. It never
raises into the caller: a transcript hiccup must never break a voice turn.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Conversation file ids we generate, e.g. "20260619-233012-1a2b". Validated
# before any filesystem lookup so a page request can never escape the folder.
_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

# Live status values, mirroring the tray status overlay states.
STATUS_IDLE = "idle"
STATUS_RECORDING = "recording"
STATUS_PROCESSING = "processing"
STATUS_SPEAKING = "speaking"
STATUS_ERROR = "error"

_TITLE_MAX = 70
_SUB_QUEUE_MAX = 64


def _transcripts_dir() -> Path:
    base = os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "VoiceAssistant" / "transcripts"


def _now() -> float:
    return time.time()


def _title_of(conv: dict) -> str:
    """A short label for a conversation: its first thing the user said."""
    for turn in conv.get("turns", []):
        user = (turn.get("user") or "").strip()
        if user:
            return user[:_TITLE_MAX] + ("…" if len(user) > _TITLE_MAX else "")
    return "Empty conversation"


class TranscriptStore:
    """Thread-safe transcript history + live state with change notifications."""

    def __init__(self, base_dir: Optional[str | os.PathLike] = None) -> None:
        self._dir = Path(base_dir) if base_dir is not None else _transcripts_dir()
        self._lock = threading.RLock()
        self._subs_lock = threading.Lock()
        self._subs: set[queue.Queue] = set()

        # Live state (mirrors the tray dot).
        self._status = STATUS_IDLE
        self._message = ""
        self._partial = ""
        # The conversation turns are being appended to right now.
        self._active: Optional[dict] = None

    # --- live updates from the pipeline ------------------------------------

    def begin_recording(self) -> None:
        """The user started speaking: show the listening state, clear partial."""
        with self._lock:
            self._status = STATUS_RECORDING
            self._message = ""
            self._partial = ""
        self._notify()

    def begin_processing(self) -> None:
        """The user released the key: move to processing while the words arrive."""
        with self._lock:
            self._status = STATUS_PROCESSING
            self._message = ""
        self._notify()

    def set_partial(self, text: str) -> None:
        """Update the transcript-in-progress as Wispr's words stream in."""
        text = text or ""
        with self._lock:
            if text == self._partial:
                return
            self._partial = text
        self._notify()

    def record_user(
        self,
        text: str,
        session_id: Optional[str] = None,
        kind: str = "voice",
        path: str = "",
    ) -> Optional[str]:
        """Begin a turn with what the user said; move to the processing state.

        Resolves which conversation the turn belongs to from ``session_id`` and
        returns that conversation's id. ``kind`` is "voice" or "coding"; ``path``
        is the codebase folder for coding turns (shown on the page).
        """
        with self._lock:
            conv = self._resolve_active(session_id, kind, path)
            now = _now()
            conv["turns"].append(
                {
                    "user": text,
                    "assistant": "",
                    "state": "pending",
                    "started_at": now,
                    "ended_at": None,
                }
            )
            conv["updated_at"] = now
            if kind:
                conv["kind"] = kind
            if path:
                conv["path"] = path
            self._active = conv
            self._status = STATUS_PROCESSING
            self._message = ""
            self._partial = ""
            self._persist(conv)
            conv_id = conv["id"]
        self._notify()
        return conv_id

    def record_assistant(self, text: str, session_id: Optional[str] = None) -> None:
        """Complete the in-flight turn with the reply; move to the speaking state.

        ``session_id`` links the conversation to the Claude session once it is
        known (the first turn of a fresh session has no id until the reply).
        """
        with self._lock:
            conv = self._active
            if conv is not None:
                now = _now()
                if conv["turns"]:
                    turn = conv["turns"][-1]
                    turn["assistant"] = text
                    turn["state"] = "complete"
                    turn["ended_at"] = now
                if session_id:
                    conv["session_id"] = session_id
                conv["updated_at"] = now
                self._persist(conv)
            self._status = STATUS_SPEAKING
            self._message = ""
            self._partial = ""
        self._notify()

    def record_error(self, message: str) -> None:
        """Mark the in-flight turn (if any) failed and show the error state."""
        with self._lock:
            conv = self._active
            if conv is not None and conv["turns"]:
                turn = conv["turns"][-1]
                if turn.get("state") == "pending":
                    turn["state"] = "error"
                    turn["error"] = message
                    turn["ended_at"] = _now()
                    conv["updated_at"] = _now()
                    self._persist(conv)
            self._status = STATUS_ERROR
            self._message = message
            self._partial = ""
        self._notify()

    def set_idle(self) -> None:
        """Return to the idle state (turn finished or quietly reset)."""
        with self._lock:
            self._status = STATUS_IDLE
            self._message = ""
            self._partial = ""
        self._notify()

    # --- reads for the web page --------------------------------------------

    def snapshot(self) -> dict:
        """Current live state plus the full active conversation (deep-copied)."""
        with self._lock:
            return {
                "status": self._status,
                "message": self._message,
                "partial": self._partial,
                "active_id": self._active["id"] if self._active else None,
                "conversation": copy.deepcopy(self._active) if self._active else None,
            }

    def list_conversations(self) -> list[dict]:
        """Summaries of every saved conversation, most recently updated first."""
        summaries: list[dict] = []
        for conv in self._load_all():
            summaries.append(
                {
                    "id": conv.get("id"),
                    "session_id": conv.get("session_id"),
                    "kind": conv.get("kind", "voice"),
                    "path": conv.get("path", ""),
                    "title": _title_of(conv),
                    "started_at": conv.get("started_at"),
                    "updated_at": conv.get("updated_at"),
                    "turn_count": len(conv.get("turns", [])),
                }
            )
        summaries.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
        return summaries

    def get_conversation(self, conv_id: str) -> Optional[dict]:
        """Load one conversation by id, or None if missing / invalid id."""
        if not conv_id or not _ID_RE.match(conv_id):
            return None
        # Prefer the in-memory active copy (it is the freshest).
        with self._lock:
            if self._active is not None and self._active.get("id") == conv_id:
                return copy.deepcopy(self._active)
        return self._read(self._dir / f"{conv_id}.json")

    def render(self, conv_id: str, fmt: str = "md") -> Optional[tuple[str, str, bytes]]:
        """Render a conversation for download.

        Returns (filename, content_type, body) for fmt in {md, txt, json}, or
        None if the conversation is missing.
        """
        conv = self.get_conversation(conv_id)
        if conv is None:
            return None
        if fmt == "json":
            body = json.dumps(conv, indent=2, ensure_ascii=False)
            return (f"transcript-{conv_id}.json", "application/json", body.encode("utf-8"))
        if fmt == "txt":
            body = self._as_text(conv)
            return (f"transcript-{conv_id}.txt", "text/plain; charset=utf-8", body.encode("utf-8"))
        body = self._as_markdown(conv)
        return (f"transcript-{conv_id}.md", "text/markdown; charset=utf-8", body.encode("utf-8"))

    # --- pub/sub for Server-Sent Events ------------------------------------

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_SUB_QUEUE_MAX)
        with self._subs_lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._subs_lock:
            self._subs.discard(q)

    def _notify(self) -> None:
        """Push the latest snapshot to every open page. Never blocks a turn."""
        snap = self.snapshot()
        with self._subs_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(snap)
            except queue.Full:
                # Page is slow; drop its oldest queued snapshot for the newest.
                try:
                    q.get_nowait()
                    q.put_nowait(snap)
                except (queue.Empty, queue.Full):
                    pass

    # --- conversation resolution -------------------------------------------

    def _resolve_active(self, session_id: Optional[str], kind: str, path: str) -> dict:
        """Pick the conversation this turn belongs to (reuse, resume, or new)."""
        # Same session still going: keep appending to the active conversation.
        if (
            session_id
            and self._active is not None
            and self._active.get("session_id") == session_id
        ):
            return self._active
        # Known session (e.g. after a restart, or switching voice<->coding):
        # resume its conversation from disk.
        if session_id:
            existing = self._find_by_session(session_id)
            if existing is not None:
                return existing
        # No session yet (first turn / after reset): start a fresh conversation.
        return self._new_conversation(session_id, kind, path)

    def _new_conversation(self, session_id: Optional[str], kind: str, path: str) -> dict:
        now = _now()
        return {
            "id": self._new_id(),
            "session_id": session_id or None,
            "kind": kind or "voice",
            "path": path or "",
            "started_at": now,
            "updated_at": now,
            "turns": [],
        }

    def _new_id(self) -> str:
        for _ in range(8):
            candidate = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
            if not (self._dir / f"{candidate}.json").exists():
                return candidate
        return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

    def _find_by_session(self, session_id: str) -> Optional[dict]:
        for conv in self._load_all():
            if conv.get("session_id") == session_id:
                return conv
        return None

    # --- persistence -------------------------------------------------------

    def _persist(self, conv: dict) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{conv['id']}.json"
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(conv, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Could not persist transcript %s: %s", conv.get("id"), exc)

    def _read(self, path: Path) -> Optional[dict]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _load_all(self) -> list[dict]:
        out: list[dict] = []
        try:
            names = sorted(p for p in self._dir.glob("*.json") if _ID_RE.match(p.stem))
        except OSError:
            return out
        for path in names:
            conv = self._read(path)
            if conv is not None and conv.get("id"):
                out.append(conv)
        return out

    # --- download rendering ------------------------------------------------

    @staticmethod
    def _as_text(conv: dict) -> str:
        lines = [f"Conversation {conv.get('id', '')}"]
        if conv.get("kind") == "coding" and conv.get("path"):
            lines.append(f"Codebase: {conv['path']}")
        lines.append("")
        for turn in conv.get("turns", []):
            lines.append(f"You: {turn.get('user', '').strip()}")
            assistant = (turn.get("assistant") or "").strip()
            if assistant:
                lines.append(f"Assistant: {assistant}")
            elif turn.get("state") == "error":
                lines.append(f"Assistant: [error: {turn.get('error', 'failed')}]")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _as_markdown(conv: dict) -> str:
        lines = [f"# Conversation {conv.get('id', '')}", ""]
        if conv.get("kind") == "coding" and conv.get("path"):
            lines.append(f"_Coding session — `{conv['path']}`_")
            lines.append("")
        for turn in conv.get("turns", []):
            lines.append(f"**You:** {turn.get('user', '').strip()}")
            lines.append("")
            assistant = (turn.get("assistant") or "").strip()
            if assistant:
                lines.append(f"**Assistant:** {assistant}")
            elif turn.get("state") == "error":
                lines.append(f"**Assistant:** _error: {turn.get('error', 'failed')}_")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
