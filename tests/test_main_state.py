from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main import VoiceAssistant


class FakeOverlay:
    def __init__(self) -> None:
        self.events: list[str] = []

    def show_recording(self) -> None:
        self.events.append("recording")

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
        assistant._client = object()
        assistant._config = SimpleNamespace(
            capture_method="visible_input",
            capture_monitor_device=None,
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


if __name__ == "__main__":
    unittest.main()
