from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from claude_client import ClaudeClient, ClaudeError


class FakeConfig:
    def __init__(self) -> None:
        self.session_id = None
        self.claude_model = "opus"
        self.claude_effort = "default"
        self.claude_timeout_seconds = 45


class ClaudeClientParseTests(unittest.TestCase):
    def _client(self) -> ClaudeClient:
        client = object.__new__(ClaudeClient)
        client._config = FakeConfig()
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

    def test_parse_result_rejects_non_object_json(self) -> None:
        client = self._client()

        with self.assertRaises(ClaudeError):
            client._parse_result(json.dumps(["not", "an", "object"]))

    def test_run_turn_omits_default_effort(self) -> None:
        client = self._client()
        client._claude_path = "claude"
        completed = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"result": "ok", "session_id": "session-789"}),
        )

        with patch("claude_client.subprocess.run", return_value=completed) as run:
            client._run_turn("prompt", None, None)

        command = run.call_args.args[0]
        self.assertNotIn("--effort", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 45)

    def test_run_turn_includes_configured_effort(self) -> None:
        client = self._client()
        client._claude_path = "claude"
        client._config.claude_effort = "high"
        completed = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"result": "ok", "session_id": "session-789"}),
        )

        with patch("claude_client.subprocess.run", return_value=completed) as run:
            client._run_turn("prompt", None, None)

        command = run.call_args.args[0]
        self.assertIn("--effort", command)
        effort_index = command.index("--effort")
        self.assertEqual(command[effort_index + 1], "high")

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
