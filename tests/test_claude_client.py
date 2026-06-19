from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch

from claude_client import (
    ClaudeClient,
    ClaudeError,
    SCREENSHOT_PROMPT,
    SYSTEM_PROMPT,
)


class FakeConfig:
    def __init__(self) -> None:
        self.session_id = None
        self.claude_model = "opus"
        self.claude_effort = "default"
        self.claude_timeout_seconds = 45
        self.browser_timeout_seconds = 300


class FakePopen:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.timeout = None
        self.killed = False

    def communicate(self, timeout=None):
        self.timeout = timeout
        return self._stdout, self._stderr

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class ClaudeClientParseTests(unittest.TestCase):
    def _client(self) -> ClaudeClient:
        client = object.__new__(ClaudeClient)
        client._config = FakeConfig()
        client._proc_lock = threading.Lock()
        client._active_proc = None
        client._cancelled = False
        return client

    def test_parse_result_returns_text_and_persists_session(self) -> None:
        client = self._client()
        stdout = json.dumps({"result": "hello", "session_id": "session-123"})

        result = client._parse_result(stdout)

        self.assertEqual(result, "hello")
        self.assertEqual(client._config.session_id, "session-123")

    def test_parse_result_rejects_empty_output(self) -> None:
        client = self._client()

        with self.assertRaises(ClaudeError):
            client._parse_result("")

    def test_parse_result_rejects_error_payload(self) -> None:
        client = self._client()
        stdout = json.dumps({"is_error": True, "result": "bad request"})

        with self.assertRaises(ClaudeError):
            client._parse_result(stdout)

    def test_parse_result_ignores_non_string_session_id(self) -> None:
        client = self._client()
        client._config.session_id = "existing-session"
        stdout = json.dumps({"result": "hello", "session_id": 123})

        result = client._parse_result(stdout)

        self.assertEqual(result, "hello")
        self.assertEqual(client._config.session_id, "existing-session")

    def test_parse_result_accepts_last_json_line(self) -> None:
        client = self._client()
        stdout = "\n".join(
            [
                "warning: ignored non-json output",
                json.dumps({"result": "from json line", "session_id": "session-456"}),
            ]
        )

        result = client._parse_result(stdout)

        self.assertEqual(result, "from json line")
        self.assertEqual(client._config.session_id, "session-456")

    def test_parse_result_accepts_embedded_pretty_json(self) -> None:
        client = self._client()
        stdout = "warning: ignored non-json output\n" + json.dumps(
            {"result": "from pretty json", "session_id": "session-pretty"},
            indent=2,
        )

        result = client._parse_result(stdout)

        self.assertEqual(result, "from pretty json")
        self.assertEqual(client._config.session_id, "session-pretty")

    def test_parse_result_rejects_non_object_json(self) -> None:
        client = self._client()

        with self.assertRaises(ClaudeError):
            client._parse_result(json.dumps(["not", "an", "object"]))

    def test_run_turn_omits_default_effort(self) -> None:
        client = self._client()
        client._claude_path = "claude"
        fake = FakePopen(stdout=json.dumps({"result": "ok", "session_id": "session-789"}))

        with patch("claude_client.subprocess.Popen", return_value=fake) as popen:
            client._run_turn("prompt", None, None)

        command = popen.call_args.args[0]
        self.assertNotIn("--effort", command)
        # The turn timeout is applied to communicate(), not the Popen call.
        self.assertEqual(fake.timeout, 45)

    def test_run_turn_includes_configured_effort(self) -> None:
        client = self._client()
        client._claude_path = "claude"
        client._config.claude_effort = "high"
        fake = FakePopen(stdout=json.dumps({"result": "ok", "session_id": "session-789"}))

        with patch("claude_client.subprocess.Popen", return_value=fake) as popen:
            client._run_turn("prompt", None, None)

        command = popen.call_args.args[0]
        self.assertIn("--effort", command)
        effort_index = command.index("--effort")
        self.assertEqual(command[effort_index + 1], "high")

    def test_run_turn_bypasses_permissions(self) -> None:
        # Headless mode can't prompt, so the voice assistant always passes
        # --dangerously-skip-permissions to act on the user's behalf.
        client = self._client()
        client._claude_path = "claude"
        fake = FakePopen(stdout=json.dumps({"result": "ok", "session_id": "s"}))

        with patch("claude_client.subprocess.Popen", return_value=fake) as popen:
            client._run_turn("prompt", None, None)

        command = popen.call_args.args[0]
        self.assertIn("--dangerously-skip-permissions", command)

    def test_browse_turn_attaches_mcp_and_uses_browser_timeout(self) -> None:
        client = self._client()
        client._claude_path = "claude"
        fake = FakePopen(stdout=json.dumps({"result": "ok", "session_id": "s"}))

        with patch("claude_client.subprocess.Popen", return_value=fake) as popen:
            client._run_turn("prompt", None, None, "C:/mcp.json")

        command = popen.call_args.args[0]
        self.assertIn("--mcp-config", command)
        self.assertEqual(command[command.index("--mcp-config") + 1], "C:/mcp.json")
        # Browse turns use the longer browser timeout on communicate().
        self.assertEqual(fake.timeout, 300)

    def test_normal_turn_has_no_mcp_config(self) -> None:
        client = self._client()
        client._claude_path = "claude"
        fake = FakePopen(stdout=json.dumps({"result": "ok", "session_id": "s"}))

        with patch("claude_client.subprocess.Popen", return_value=fake) as popen:
            client._run_turn("prompt", None, None)

        command = popen.call_args.args[0]
        self.assertNotIn("--mcp-config", command)
        self.assertEqual(fake.timeout, 45)

    def test_screenshot_prompt_appended_only_with_screenshot(self) -> None:
        client = self._client()
        client._claude_path = "claude"

        def system_prompt_for(add_dir) -> str:
            fake = FakePopen(stdout=json.dumps({"result": "ok", "session_id": "s"}))
            with patch("claude_client.subprocess.Popen", return_value=fake) as popen:
                client._run_turn("prompt", None, add_dir)
            command = popen.call_args.args[0]
            return command[command.index("--append-system-prompt") + 1]

        # add_dir set -> a screenshot is attached -> include the screenshot guidance.
        with_shot = system_prompt_for("C:/shots")
        self.assertIn(SYSTEM_PROMPT, with_shot)
        self.assertIn(SCREENSHOT_PROMPT, with_shot)

        # No screenshot -> base prompt only, no mention of an image.
        without_shot = system_prompt_for(None)
        self.assertIn(SYSTEM_PROMPT, without_shot)
        self.assertNotIn(SCREENSHOT_PROMPT, without_shot)

    def test_ask_retries_and_clears_stale_session(self) -> None:
        client = self._client()
        client._config.session_id = "stale-session"

        with patch.object(
            client,
            "_run_turn",
            side_effect=[ClaudeError("No conversation found"), "ok"],
        ) as run_turn:
            result = client.ask("question", None)

        self.assertEqual(result, "ok")
        self.assertIsNone(client._config.session_id)
        self.assertEqual(run_turn.call_count, 2)

    def test_ask_does_not_clear_session_for_unrelated_error(self) -> None:
        client = self._client()
        client._config.session_id = "keep-session"

        with (
            patch.object(
                client,
                "_run_turn",
                side_effect=ClaudeError("Unknown model alias"),
            ),
            self.assertRaises(ClaudeError),
        ):
            client.ask("question", None)

        self.assertEqual(client._config.session_id, "keep-session")


if __name__ == "__main__":
    unittest.main()
