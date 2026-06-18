from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject

from main import VoiceAssistant


class FakeOverlay:
    def __init__(self) -> None:
        self.events: list[str] = []

    def show_recording(self) -> None:
        self.events.append("recording")

    def show_processing(self) -> None:
        self.events.append("processing")

    def show_speaking(self) -> None:
        self.events.append("speaking")

    def show_error(self) -> None:
        self.events.append("error")

    def hide(self) -> None:
        self.events.append("hide")


class FakeTray:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.notifications.append((title, message))


class FailingCaptureInput:
    def focus_for_capture(self) -> None:
        raise RuntimeError("focus failed")


class RecordingCaptureInput:
    def __init__(self) -> None:
        self.focused = False

    def focus_for_capture(self) -> None:
        self.focused = True


class FakeClipboard:
    def __init__(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


class MainStateTests(unittest.TestCase):
    def _assistant(self) -> VoiceAssistant:
        assistant = VoiceAssistant.__new__(VoiceAssistant)
        assistant._busy = False
        assistant._recording = False
        assistant._active_capture_method = None
        assistant._clipboard_capture_active = False
        assistant._clipboard_changed_during_capture = False
        assistant._clipboard_text_before_capture = None
        assistant._capture_gen = 0
        assistant._capture_last_peek = ""
        assistant._capture_seen_text = False
        assistant._capture_started_at = 0.0
        assistant._client = object()
        assistant._config = SimpleNamespace(
            capture_method="visible_input",
            capture_monitor_device=None,
            capture_delay_ms=500,
        )
        assistant._overlay = FakeOverlay()
        assistant._tray = FakeTray()
        return assistant

    def test_press_failure_returns_to_idle(self) -> None:
        assistant = self._assistant()
        assistant._visible_input = FailingCaptureInput()

        with (
            patch("main.QTimer.singleShot", lambda *_args: None),
            patch("main.logger.warning"),
        ):
            assistant._on_press()

        self.assertFalse(assistant._recording)
        self.assertFalse(assistant._busy)
        self.assertIsNone(assistant._active_capture_method)
        self.assertIn("error", assistant._overlay.events)
        self.assertIn("Transcript input failed", assistant._tray.notifications[0][1])

    def test_transcript_read_failure_returns_to_idle(self) -> None:
        assistant = self._assistant()
        assistant._busy = True
        assistant._active_capture_method = "visible_input"

        def fail_read() -> str:
            raise RuntimeError("read failed")

        assistant._read_transcript = fail_read  # type: ignore[method-assign]

        with (
            patch("main.QTimer.singleShot", lambda *_args: None),
            patch("main.logger.warning"),
        ):
            assistant._capture_and_ask()

        self.assertFalse(assistant._recording)
        self.assertFalse(assistant._busy)
        self.assertIsNone(assistant._active_capture_method)
        self.assertIn("error", assistant._overlay.events)
        self.assertIn("Transcript capture failed", assistant._tray.notifications[0][1])

    def test_clipboard_capture_ignores_unchanged_text(self) -> None:
        assistant = self._assistant()
        assistant._app = SimpleNamespace(clipboard=lambda: FakeClipboard("old text"))
        assistant._clipboard_capture_active = True
        assistant._clipboard_changed_during_capture = False
        assistant._clipboard_text_before_capture = "old text"

        self.assertEqual(assistant._read_clipboard_transcript(), "")
        self.assertFalse(assistant._clipboard_capture_active)
        self.assertFalse(assistant._clipboard_changed_during_capture)
        self.assertIsNone(assistant._clipboard_text_before_capture)

    def test_clipboard_capture_accepts_changed_text(self) -> None:
        assistant = self._assistant()
        assistant._app = SimpleNamespace(clipboard=lambda: FakeClipboard("new text"))
        assistant._clipboard_capture_active = True
        assistant._clipboard_changed_during_capture = True
        assistant._clipboard_text_before_capture = "old text"

        self.assertEqual(assistant._read_clipboard_transcript(), "new text")

    def test_watchdog_recovers_stuck_turn(self) -> None:
        assistant = self._assistant()
        assistant._watchdog = None  # _stop_watchdog tolerates this
        assistant._busy = True
        assistant._recording = False

        with patch("main.logger.warning"):
            assistant._on_watchdog_timeout()

        self.assertFalse(assistant._busy)
        self.assertFalse(assistant._recording)
        self.assertIn("hide", assistant._overlay.events)
        self.assertTrue(assistant._tray.notifications)

    def test_watchdog_noop_when_idle(self) -> None:
        assistant = self._assistant()
        assistant._watchdog = None
        assistant._busy = False
        assistant._recording = False

        assistant._on_watchdog_timeout()

        # Nothing to recover: no notification, no overlay change.
        self.assertEqual(assistant._tray.notifications, [])
        self.assertEqual(assistant._overlay.events, [])

    def test_barge_in_interrupts_turn_and_starts_recording(self) -> None:
        # Pressing the hotkey mid-turn must cancel the live playback, detach the
        # in-flight workers, and begin a fresh recording.
        assistant = self._assistant()
        assistant._busy = True
        assistant._recording = False
        assistant._watchdog = None  # _stop_watchdog tolerates this
        cancel = threading.Event()
        assistant._cancel_event = cancel
        assistant._ask_worker = object()
        assistant._speak_worker = object()
        assistant._visible_input = RecordingCaptureInput()

        with patch("main.logger.info"):
            assistant._on_press()

        self.assertTrue(cancel.is_set())
        self.assertIsNone(assistant._ask_worker)
        self.assertIsNone(assistant._speak_worker)
        self.assertTrue(assistant._recording)
        self.assertFalse(assistant._busy)
        self.assertTrue(assistant._visible_input.focused)
        self.assertIn("recording", assistant._overlay.events)

    def test_release_refocuses_capture_box_before_capture(self) -> None:
        # Wispr types on release, so the box must be re-focused then in case
        # focus drifted to another window while recording.
        assistant = self._assistant()
        assistant._recording = True
        assistant._active_capture_method = "visible_input"
        assistant._visible_input = RecordingCaptureInput()
        assistant._start_watchdog = lambda: None  # type: ignore[method-assign]

        with patch("main.QTimer.singleShot", lambda *_args: None):
            assistant._on_release()

        self.assertTrue(assistant._visible_input.focused)
        self.assertFalse(assistant._recording)
        self.assertTrue(assistant._busy)
        self.assertIn("processing", assistant._overlay.events)

    def test_release_refocus_failure_does_not_crash_turn(self) -> None:
        # A focus error on release is logged but must not abort the turn.
        assistant = self._assistant()
        assistant._recording = True
        assistant._active_capture_method = "visible_input"
        assistant._visible_input = FailingCaptureInput()
        assistant._start_watchdog = lambda: None  # type: ignore[method-assign]

        with (
            patch("main.QTimer.singleShot", lambda *_args: None),
            patch("main.logger.warning"),
        ):
            assistant._on_release()

        self.assertTrue(assistant._busy)
        self.assertIn("processing", assistant._overlay.events)

    def test_poll_waits_for_long_transcript_to_settle(self) -> None:
        # A long message arrives over several polls; we must wait until it stops
        # changing before reading, not fire on the first (empty/partial) poll.
        assistant = self._assistant()
        assistant._busy = True
        assistant._capture_gen = 3
        assistant._capture_started_at = time.monotonic()
        peeks = iter(["", "the start of a", "the start of a long question",
                      "the start of a long question"])
        assistant._peek_transcript = lambda: next(peeks)  # type: ignore[method-assign]
        asked: list[bool] = []
        assistant._capture_and_ask = lambda: asked.append(True)  # type: ignore[method-assign]

        with patch("main.QTimer.singleShot", lambda *_args: None):
            assistant._poll_capture(3)  # ""        -> keep waiting
            self.assertEqual(asked, [])
            assistant._poll_capture(3)  # partial   -> keep waiting
            self.assertEqual(asked, [])
            assistant._poll_capture(3)  # grew      -> keep waiting
            self.assertEqual(asked, [])
            assistant._poll_capture(3)  # unchanged -> settled, ask
            self.assertEqual(asked, [True])

    def test_poll_bails_when_superseded(self) -> None:
        assistant = self._assistant()
        assistant._busy = True
        assistant._capture_gen = 7
        assistant._capture_started_at = time.monotonic()
        assistant._peek_transcript = lambda: "anything"  # type: ignore[method-assign]
        asked: list[bool] = []
        assistant._capture_and_ask = lambda: asked.append(True)  # type: ignore[method-assign]

        with patch("main.QTimer.singleShot", lambda *_args: None):
            assistant._poll_capture(6)  # stale generation

        self.assertEqual(asked, [])

    def test_poll_gives_up_when_nothing_arrives(self) -> None:
        assistant = self._assistant()
        assistant._busy = True
        assistant._capture_gen = 1
        # Started well in the past so the empty-timeout has elapsed.
        assistant._capture_started_at = time.monotonic() - 100
        assistant._peek_transcript = lambda: ""  # type: ignore[method-assign]
        asked: list[bool] = []
        assistant._capture_and_ask = lambda: asked.append(True)  # type: ignore[method-assign]

        with patch("main.QTimer.singleShot", lambda *_args: None):
            assistant._poll_capture(1)

        # Finalizes (which then quietly resets on empty text).
        self.assertEqual(asked, [True])

    def test_stale_speech_failure_is_ignored(self) -> None:
        # A failure from a barged-in/timed-out speak worker must not notify.
        assistant = self._assistant()
        QObject.__init__(assistant)  # so self.sender() is usable
        assistant._speak_worker = object()  # current worker is something else

        assistant._on_speech_failed("late failure")  # sender() is None

        self.assertEqual(assistant._tray.notifications, [])

    def test_stale_reply_is_ignored(self) -> None:
        # A reply from a timed-out (non-current) worker must not start playback.
        assistant = self._assistant()
        QObject.__init__(assistant)  # so self.sender() is usable
        assistant._ask_worker = object()  # current worker is something else
        assistant._speak_worker = None

        assistant._on_reply("late reply")  # called directly -> sender() is None

        self.assertIsNone(assistant._speak_worker)
        self.assertNotIn("speaking", assistant._overlay.events)


if __name__ == "__main__":
    unittest.main()
