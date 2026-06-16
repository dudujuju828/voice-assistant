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

TURN_TIMEOUT_SECONDS = 120


class ClaudeNotInstalledError(RuntimeError):
    """Raised when the `claude` CLI cannot be found on PATH."""


class ClaudeError(RuntimeError):
    """Raised when a Claude turn fails for any other reason."""


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
        except ClaudeError:
            if session_id is None:
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
        ]
        if add_dir:
            cmd += ["--add-dir", add_dir]
        if session_id:
            cmd += ["--resume", session_id]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TURN_TIMEOUT_SECONDS,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeError("Claude timed out.") from exc
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
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeError("Could not parse Claude's JSON output.") from exc

        new_session = payload.get("session_id")
        if new_session:
            self._config.session_id = new_session

        if payload.get("is_error"):
            raise ClaudeError(str(payload.get("result", "Claude reported an error.")))

        result = payload.get("result")
        if not result:
            raise ClaudeError("Claude returned an empty result.")
        return str(result).strip()
