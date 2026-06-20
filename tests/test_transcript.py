from __future__ import annotations

import json
import queue
import tempfile
import unittest
from pathlib import Path

from transcript import (
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_PROCESSING,
    STATUS_RECORDING,
    STATUS_SPEAKING,
    TranscriptStore,
)


class TranscriptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = TranscriptStore(self.dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- live state ---------------------------------------------------------

    def test_begin_recording_sets_state_and_clears_partial(self) -> None:
        self.store.set_partial("half a sentence")
        self.store.begin_recording()
        snap = self.store.snapshot()
        self.assertEqual(snap["status"], STATUS_RECORDING)
        self.assertEqual(snap["partial"], "")

    def test_partial_then_user_then_assistant_flow(self) -> None:
        self.store.begin_recording()
        self.store.begin_processing()
        self.store.set_partial("edit the")
        self.assertEqual(self.store.snapshot()["partial"], "edit the")

        conv_id = self.store.record_user("edit the file", session_id=None)
        snap = self.store.snapshot()
        self.assertEqual(snap["status"], STATUS_PROCESSING)
        self.assertEqual(snap["partial"], "")  # final text replaces the partial
        self.assertEqual(snap["active_id"], conv_id)
        self.assertEqual(snap["conversation"]["turns"][-1]["user"], "edit the file")
        self.assertEqual(snap["conversation"]["turns"][-1]["state"], "pending")

        self.store.record_assistant("Done, I edited it.", session_id="sess-1")
        snap = self.store.snapshot()
        self.assertEqual(snap["status"], STATUS_SPEAKING)
        turn = snap["conversation"]["turns"][-1]
        self.assertEqual(turn["assistant"], "Done, I edited it.")
        self.assertEqual(turn["state"], "complete")
        self.assertEqual(snap["conversation"]["session_id"], "sess-1")

    def test_set_idle_and_error(self) -> None:
        self.store.record_user("do a thing", session_id=None)
        self.store.record_error("Claude error: boom")
        snap = self.store.snapshot()
        self.assertEqual(snap["status"], STATUS_ERROR)
        self.assertEqual(snap["message"], "Claude error: boom")
        self.assertEqual(snap["conversation"]["turns"][-1]["state"], "error")

        self.store.set_idle()
        self.assertEqual(self.store.snapshot()["status"], STATUS_IDLE)

    # --- conversation resolution -------------------------------------------

    def test_same_session_appends_to_one_conversation(self) -> None:
        first = self.store.record_user("hi", session_id=None)
        self.store.record_assistant("hello", session_id="sess-1")
        second = self.store.record_user("again", session_id="sess-1")
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.snapshot()["conversation"]["turns"]), 2)

    def test_fresh_session_each_starts_new_conversation(self) -> None:
        a = self.store.record_user("first ask", session_id=None)
        # No assistant/session yet; another sessionless turn is a new conversation.
        b = self.store.record_user("second ask", session_id=None)
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.store.list_conversations()), 2)

    def test_voice_and_coding_sessions_are_separate_conversations(self) -> None:
        voice = self.store.record_user("what's up", session_id="vs-1", kind="voice")
        self.store.record_assistant("not much", session_id="vs-1")
        coding = self.store.record_user(
            "edit the file", session_id="cs-1", kind="coding", path="C:/proj"
        )
        self.assertNotEqual(voice, coding)
        convs = {c["id"]: c for c in self.store.list_conversations()}
        self.assertEqual(convs[coding]["kind"], "coding")
        self.assertEqual(convs[coding]["path"], "C:/proj")
        self.assertEqual(convs[voice]["kind"], "voice")

    def test_resumes_persisted_conversation_by_session(self) -> None:
        conv_id = self.store.record_user("q1", session_id=None)
        self.store.record_assistant("a1", session_id="sess-9")
        # A brand-new store (e.g. after restart) should resume the same file when
        # the persisted session id comes back.
        reopened = TranscriptStore(self.dir)
        resumed = reopened.record_user("q2", session_id="sess-9")
        self.assertEqual(resumed, conv_id)
        self.assertEqual(len(reopened.snapshot()["conversation"]["turns"]), 2)

    # --- persistence + reads -----------------------------------------------

    def test_conversation_is_persisted_to_disk(self) -> None:
        conv_id = self.store.record_user("remember me", session_id=None)
        path = self.dir / f"{conv_id}.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["turns"][0]["user"], "remember me")

    def test_list_is_sorted_most_recent_first(self) -> None:
        old = self.store.record_user("older", session_id=None)
        self.store.record_assistant("ok", session_id="s-old")
        new = self.store.record_user("newer", session_id=None)
        ids = [c["id"] for c in self.store.list_conversations()]
        self.assertEqual(ids[0], new)
        self.assertIn(old, ids)
        self.assertEqual(self.store.list_conversations()[0]["title"], "newer")

    def test_get_conversation_rejects_bad_ids(self) -> None:
        self.assertIsNone(self.store.get_conversation("../../etc/passwd"))
        self.assertIsNone(self.store.get_conversation("not-an-id"))
        self.assertIsNone(self.store.get_conversation("20260101-000000-zzzz"))

    def test_snapshot_is_a_copy(self) -> None:
        self.store.record_user("mutate me", session_id=None)
        snap = self.store.snapshot()
        snap["conversation"]["turns"].append({"user": "injected"})
        # The store's own copy must be unaffected.
        self.assertEqual(len(self.store.snapshot()["conversation"]["turns"]), 1)

    # --- downloads ----------------------------------------------------------

    def test_render_formats(self) -> None:
        conv_id = self.store.record_user("hello there", session_id=None)
        self.store.record_assistant("general kenobi", session_id="s-1")

        name, ctype, body = self.store.render(conv_id, "md")
        self.assertTrue(name.endswith(".md"))
        self.assertIn("text/markdown", ctype)
        self.assertIn(b"**You:** hello there", body)
        self.assertIn(b"general kenobi", body)

        name, ctype, body = self.store.render(conv_id, "txt")
        self.assertTrue(name.endswith(".txt"))
        self.assertIn(b"You: hello there", body)

        name, ctype, body = self.store.render(conv_id, "json")
        self.assertIn("application/json", ctype)
        self.assertEqual(json.loads(body)["id"], conv_id)

    def test_render_missing_conversation_returns_none(self) -> None:
        self.assertIsNone(self.store.render("20260101-000000-abcd", "md"))

    # --- pub/sub ------------------------------------------------------------

    def test_subscribers_receive_snapshots(self) -> None:
        q = self.store.subscribe()
        self.store.begin_recording()
        snap = q.get_nowait()
        self.assertEqual(snap["status"], STATUS_RECORDING)
        self.store.unsubscribe(q)
        self.store.begin_processing()
        with self.assertRaises(queue.Empty):
            q.get_nowait()


if __name__ == "__main__":
    unittest.main()
