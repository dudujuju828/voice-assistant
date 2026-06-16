"""Claude Code CLI integration.

One subprocess per turn (no persistent stdin pipe). The conversation is kept
alive across turns via the stored ``session_id`` and ``--resume``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

from config import Config
SESSION_ERROR_PATTERNS = (
    "no conversation found",
    "conversation not found",
    "session not found",
    "invalid session",
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
# This string is passed to the Claude CLI on every turn via
# ``--append-system-prompt`` so it shapes both new and resumed sessions. It is
# tuned for a hands-free voice assistant: replies are spoken aloud by a text to
# speech voice, so they must be short, plainly worded, and free of any markdown
# or symbols that would sound wrong when read out. Read it aloud before editing
# — if a sentence sounds awkward spoken, it will sound awkward to the user too.
SYSTEM_PROMPT = (
    "You are a voice assistant. The person talks to you out loud and hears "
    "your replies spoken by a text to speech voice, so everything you say has "
    "to sound natural when it is read aloud.\n\n"
    "Keep your replies short and direct, usually two to four sentences. Get to "
    "the point and skip filler and pleasantries.\n\n"
    "Never use formatting of any kind. That means no markdown, no bold or "
    "italics, no headings, no bullet points, no numbered lists, and no code "
    "blocks. Write in plain spoken sentences only. Avoid symbols, asterisks, "
    "and anything that would sound strange when spoken, and spell out short "
    "forms, so say for example in full rather than writing it as a short "
    "form.\n\n"
    "Talk like a helpful friend sitting next to the person. Be warm but "
    "professional, and use natural contractions like you'll, it's, and "
    "don't.\n\n"
    "With each question you also get a screenshot of the person's screen. Use "
    "it to ground your answer, and point to what you see in plain language, "
    "like the button near the top right, or the menu on the left side. If you "
    "are not sure what they mean, ask one quick question to clarify."
)


class ClaudeNotInstalledError(RuntimeError):
    """Raised when the `claude` CLI cannot be found on PATH."""


class ClaudeError(RuntimeError):
    """Raised when a Claude turn fails for any other reason."""


def _is_session_error(message: str) -> bool:
    normalized = (message or "").lower()
    return any(pattern in normalized for pattern in SESSION_ERROR_PATTERNS)


class ClaudeClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._claude_path = shutil.which("claude")
        if not self._claude_path:
            raise ClaudeNotInstalledError(
                "The 'claude' CLI was not found. Install it with "
                "`npm i -g @anthropic-ai/claude-code`."
            )

    # --- public API ---------------------------------------------------------

    def ask(self, question: str, screenshot_path: Optional[str]) -> str:
        """Run one Claude turn and return the reply text.

        Resumes the persistent session when one exists. On a session error
        (exit code != 0 or "No conversation found"), clears the stored
        session_id and retries once as a fresh conversation.
        """
        prompt = self._build_prompt(question, screenshot_path)
        add_dir = (
            os.path.dirname(os.path.abspath(screenshot_path))
            if screenshot_path
            else None
        )
        session_id = self._config.session_id

        try:
            return self._run_turn(prompt, session_id, add_dir)
        except ClaudeError as exc:
            if session_id is None or not _is_session_error(str(exc)):
                raise
            # Stale/expired session — start fresh once.
            self._config.session_id = None
            return self._run_turn(prompt, None, add_dir)

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _build_prompt(question: str, screenshot_path: Optional[str]) -> str:
        if screenshot_path:
            return (
                f"Here is my current screen: {screenshot_path}\n\n{question}"
            )
        return question

    def _run_turn(
        self,
        prompt: str,
        session_id: Optional[str],
        add_dir: Optional[str],
    ) -> str:
        cmd = [
            self._claude_path,
            "-p",
            prompt,
            "--model",
            self._config.claude_model,
            "--output-format",
            "json",
            "--append-system-prompt",
            SYSTEM_PROMPT,
        ]
        if self._config.claude_effort != "default":
            cmd += ["--effort", self._config.claude_effort]
        if add_dir:
            cmd += ["--add-dir", add_dir]
        if session_id:
            cmd += ["--resume", session_id]

        timeout_seconds = self._config.claude_timeout_seconds
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeError(
                f"Claude timed out after {timeout_seconds} seconds."
            ) from exc
        except OSError as exc:
            raise ClaudeError(f"Failed to launch Claude: {exc}") from exc

        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0 or "No conversation found" in stderr:
            raise ClaudeError(stderr or f"Claude exited with code {proc.returncode}.")

        return self._parse_result(proc.stdout)

    def _parse_result(self, stdout: str) -> str:
        stdout = (stdout or "").strip()
        if not stdout:
            raise ClaudeError("Claude returned no output.")
        payload = self._load_json_payload(stdout)

        new_session = payload.get("session_id")
        if new_session:
            self._config.session_id = new_session

        if payload.get("is_error"):
            raise ClaudeError(str(payload.get("result", "Claude reported an error.")))

        result = payload.get("result")
        if not result:
            raise ClaudeError("Claude returned an empty result.")
        return str(result).strip()

    @staticmethod
    def _load_json_payload(stdout: str) -> dict:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if not isinstance(payload, dict):
            raise ClaudeError("Could not parse Claude's JSON output.")
        return payload
