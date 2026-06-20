"""Claude Code CLI integration.

One subprocess per turn (no persistent stdin pipe). The conversation is kept
alive across turns via the stored ``session_id`` and ``--resume``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Optional

from config import Config

# The `claude` CLI is a .cmd/script wrapper on Windows; spawning it from our
# windowless (pythonw) parent would otherwise pop a visible console window each
# turn. CREATE_NO_WINDOW keeps the child's console hidden.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
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
    "don't."
)

# Appended only when a screenshot is actually attached to the turn. With the
# screenshot toggle off we must not tell Claude it has an image, or it will
# reference a screen it never received.
SCREENSHOT_PROMPT = (
    "With each question you also get a screenshot of the person's screen. Use "
    "it to ground your answer, and point to what you see in plain language, "
    "like the button near the top right, or the menu on the left side. If you "
    "are not sure what they mean, ask one quick question to clarify."
)

# Appended only on a coding turn (when a codebase working directory is set). The
# reply is still spoken, so it must end in a short plain summary — but unlike a
# normal turn the model should actually edit files, not just talk about it.
CODING_PROMPT = (
    "This turn is a coding or file editing request about a project on the "
    "person's computer. You are already running inside that project folder as "
    "your working directory, so use your tools to read and edit its files "
    "directly. Actually make the change they asked for, do not just describe "
    "it. When you are done, give a short spoken summary, one or two sentences, "
    "of what you changed and which file. Never read code, file contents, long "
    "paths, or command output aloud, since everything you say is spoken by a "
    "text to speech voice; summarize it in plain words and offer to go into "
    "detail if they want."
)

# Appended only on a browsing turn (when the Playwright MCP browser is attached).
BROWSER_PROMPT = (
    "You can control a web browser that is open on the person's screen using "
    "your browser tools. When they ask you to look something up, search, open a "
    "page, or navigate, actually do it in the browser so they can watch. Then "
    "give a short spoken summary, two or three sentences, of what you found or "
    "did. Never read out long lists, web addresses, or raw page text; summarize "
    "and offer to go deeper. The browser stays open for them to read, so you do "
    "not need to close it.\n\n"
    "There is one shortcut you must always take for C plus plus. For any "
    "question about a C plus plus symbol, header, or standard library feature, "
    "like a smart pointer or a scoped lock, do not search or click around. Just "
    "call the open_cppreference tool once with the symbol or a short "
    "description, for example lock_guard or scoped mutex, and it jumps straight "
    "to the right page. You have already worked out which symbol they mean, so "
    "pass that, never a web address. Use open_cppreference for every C plus plus "
    "documentation or reference question, then give your short spoken summary."
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
        self._proc_lock = threading.Lock()
        self._cancelled = False
        self._active_proc: subprocess.Popen | None = None

    # --- public API ---------------------------------------------------------

    def cancel(self) -> None:
        with self._proc_lock:
            self._cancelled = True
            if self._active_proc is not None:
                self._active_proc.kill()

    def ask(
        self,
        question: str,
        screenshot_path: Optional[str],
        mcp_config_path: Optional[str] = None,
        coding_cwd: Optional[str] = None,
    ) -> str:
        """Run one Claude turn and return the reply text.

        Resumes the persistent session when one exists. On a session error
        (exit code != 0 or "No conversation found"), clears the stored
        session id and retries once as a fresh conversation. ``mcp_config_path``,
        when given, attaches the browser MCP server so this is a browsing turn.
        ``coding_cwd``, when given, runs Claude Code with that codebase folder as
        the working directory and uses a separate coding session, so coding turns
        never mix with the casual voice conversation.
        """
        # Coding turns persist their own session id so the codebase conversation
        # stays separate from the voice one (and vice versa).
        session_attr = "coding_session_id" if coding_cwd else "session_id"
        prompt = self._build_prompt(question, screenshot_path)
        add_dir = (
            os.path.dirname(os.path.abspath(screenshot_path))
            if screenshot_path
            else None
        )
        session_id = getattr(self._config, session_attr)

        try:
            return self._run_turn(
                prompt, session_id, add_dir, mcp_config_path, coding_cwd, session_attr
            )
        except ClaudeError as exc:
            if session_id is None or not _is_session_error(str(exc)):
                raise
            # Stale/expired session — start fresh once.
            setattr(self._config, session_attr, None)
            return self._run_turn(
                prompt, None, add_dir, mcp_config_path, coding_cwd, session_attr
            )

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
        mcp_config_path: Optional[str] = None,
        cwd: Optional[str] = None,
        session_attr: str = "session_id",
    ) -> str:
        system_prompt = SYSTEM_PROMPT
        if add_dir:  # a screenshot is attached for this turn
            system_prompt = f"{system_prompt}\n\n{SCREENSHOT_PROMPT}"
        if cwd:  # a codebase working directory is set for this turn
            system_prompt = f"{system_prompt}\n\n{CODING_PROMPT}"
        if mcp_config_path:  # a browser is attached for this turn
            system_prompt = f"{system_prompt}\n\n{BROWSER_PROMPT}"
        cmd = [
            self._claude_path,
            "-p",
            prompt,
            "--model",
            self._config.claude_model,
            "--output-format",
            "json",
            "--append-system-prompt",
            system_prompt,
            # Headless (-p) mode can't show permission prompts, so without this
            # any tool use (writing files, running commands) is denied. The voice
            # assistant is meant to act freely on the user's behalf, so it always
            # bypasses permission checks.
            "--dangerously-skip-permissions",
        ]
        if self._config.claude_effort != "default":
            cmd += ["--effort", self._config.claude_effort]
        if add_dir:
            cmd += ["--add-dir", add_dir]
        if mcp_config_path:
            cmd += ["--mcp-config", mcp_config_path]
        if session_id:
            cmd += ["--resume", session_id]

        # Browsing turns navigate pages and run many tool round-trips, so they
        # get a longer budget than a normal spoken reply.
        timeout_seconds = (
            self._config.browser_timeout_seconds
            if mcp_config_path
            else self._config.claude_timeout_seconds
        )
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=_NO_WINDOW,
                # Coding turns run inside the codebase so Claude treats it as the
                # project root; other turns keep the app's own working directory.
                cwd=cwd or None,
            )
        except OSError as exc:
            raise ClaudeError(f"Failed to launch Claude: {exc}") from exc

        with self._proc_lock:
            if self._cancelled:
                proc.kill()
            self._active_proc = proc
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.communicate()
            raise ClaudeError(
                f"Claude timed out after {timeout_seconds} seconds."
            ) from exc
        finally:
            with self._proc_lock:
                self._active_proc = None

        stderr = (stderr or "").strip()
        if proc.returncode != 0 or "No conversation found" in stderr:
            # The CLI usually reports failures on stderr, but sometimes only in
            # the JSON on stdout. Fold a trimmed stdout into the message so the
            # session-error detection (and fresh-session retry) still fires.
            detail = stderr
            snippet = (stdout or "").strip()[:500]
            if snippet and snippet not in detail:
                detail = f"{detail} {snippet}".strip()
            raise ClaudeError(detail or f"Claude exited with code {proc.returncode}.")

        return self._parse_result(stdout, session_attr)

    def _parse_result(self, stdout: str, session_attr: str = "session_id") -> str:
        stdout = (stdout or "").strip()
        if not stdout:
            raise ClaudeError("Claude returned no output.")
        payload = self._load_json_payload(stdout)

        new_session = payload.get("session_id")
        if isinstance(new_session, str) and new_session.strip():
            setattr(self._config, session_attr, new_session)

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
            decoder = json.JSONDecoder()
            for index, char in enumerate(stdout):
                if char != "{":
                    continue
                try:
                    candidate, _end = decoder.raw_decode(stdout[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    payload = candidate

        if not isinstance(payload, dict):
            raise ClaudeError("Could not parse Claude's JSON output.")
        return payload
