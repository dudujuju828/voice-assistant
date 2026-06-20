from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from transcript import TranscriptStore
from transcript_server import TranscriptServer


class TranscriptServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = TranscriptStore(Path(self._tmp.name))
        # Port 0 = OS-assigned free port, so tests never collide.
        self.server = TranscriptServer(self.store, port=0)
        self.url = self.server.start()
        self.assertIsNotNone(self.url)

    def tearDown(self) -> None:
        self.server.stop()
        self._tmp.cleanup()

    def _get(self, path: str):
        with urllib.request.urlopen(self.url.rstrip("/") + path, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()

    def test_serves_html_page(self) -> None:
        status, ctype, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Voice Assistant", body)

    def test_state_endpoint_returns_status(self) -> None:
        status, ctype, body = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(body)["status"], "idle")

    def test_conversations_list_and_fetch_and_download(self) -> None:
        conv_id = self.store.record_user("hello server", session_id=None)
        self.store.record_assistant("hi there", session_id="s-1")

        _, _, body = self._get("/api/conversations")
        convs = json.loads(body)["conversations"]
        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]["id"], conv_id)

        _, _, body = self._get(f"/api/conversations/{conv_id}")
        conv = json.loads(body)
        self.assertEqual(conv["turns"][0]["user"], "hello server")

        status, ctype, body = self._get(
            f"/api/conversations/{conv_id}/download?format=md"
        )
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", ctype)
        self.assertIn(b"hello server", body)

    def test_unknown_conversation_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/conversations/20260101-000000-abcd")
        self.assertEqual(ctx.exception.code, 404)

    def test_events_stream_primes_with_current_state(self) -> None:
        self.store.record_user("streamed", session_id=None)
        # Read just the first SSE "data:" line, then close.
        with urllib.request.urlopen(self.url.rstrip("/") + "/api/events", timeout=5) as resp:
            payload = None
            for _ in range(10):
                line = resp.readline()
                if line.startswith(b"data:"):
                    payload = json.loads(line[len(b"data:"):].strip())
                    break
        self.assertIsNotNone(payload)
        self.assertEqual(payload["status"], "processing")
        self.assertEqual(payload["conversation"]["turns"][0]["user"], "streamed")


if __name__ == "__main__":
    unittest.main()
