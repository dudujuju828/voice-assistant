from __future__ import annotations

import json
import unittest

from claude_client import ClaudeClient, ClaudeError


class FakeConfig:
    def __init__(self) -> None:
        self.session_id = None


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


if __name__ == "__main__":
    unittest.main()
