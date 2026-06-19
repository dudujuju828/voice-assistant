from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject

from main import AskWorker, SpeakWorker, VoiceAssistant


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
        assistant._error_token = 0
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


class SpeakWorkerDispatchTests(unittest.TestCase):
    def _worker(self, provider: str) -> SpeakWorker:
        return SpeakWorker(
            "hello there",
            "voice-id",
            "eleven_turbo_v2_5",
            0.5,
            0.75,
            1.0,
            30,
            threading.Event(),
            provider,
            "am_adam",
            "C:/voices/me.wav",
        )

    def test_local_provider_calls_kokoro_not_elevenlabs(self) -> None:
        worker = self._worker("local")
        with (
            patch("main.tts_local.speak_local", return_value=True) as local,
            patch("main.tts.speak") as eleven,
            patch("main.tts_chatterbox.speak_chatterbox") as chatter,
        ):
            worker.run()

        eleven.assert_not_called()
        chatter.assert_not_called()
        local.assert_called_once()
        args = local.call_args.args
        self.assertEqual(args[0], "hello there")
        self.assertEqual(args[1], "am_adam")  # local voice, not the EL voice id

    def test_elevenlabs_provider_calls_api_not_local(self) -> None:
        worker = self._worker("elevenlabs")
        with (
            patch("main.tts.speak", return_value=True) as eleven,
            patch("main.tts_local.speak_local") as local,
            patch("main.tts_chatterbox.speak_chatterbox") as chatter,
        ):
            worker.run()

        local.assert_not_called()
        chatter.assert_not_called()
        eleven.assert_called_once()

    def test_chatterbox_provider_calls_chatterbox_with_voice_sample(self) -> None:
        worker = self._worker("chatterbox")
        with (
            patch("main.tts_chatterbox.speak_chatterbox", return_value=True) as chatter,
            patch("main.tts.speak") as eleven,
            patch("main.tts_local.speak_local") as local,
        ):
            worker.run()

        eleven.assert_not_called()
        local.assert_not_called()
        chatter.assert_called_once()
        args = chatter.call_args.args
        self.assertEqual(args[0], "hello there")
        self.assertEqual(args[1], "C:/voices/me.wav")  # the cloning sample path


class _FakeAskClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def ask(self, question: str, shot, mcp_config_path=None):
        self.calls.append((question, shot))
        return "reply"


class AskWorkerScreenshotTests(unittest.TestCase):
    def test_skips_capture_when_screenshot_disabled(self) -> None:
        client = _FakeAskClient()
        worker = AskWorker(
            client, "what time is it", "\\\\.\\DISPLAY2", include_screenshot=False
        )

        with patch("main.capture.capture_monitor") as cap:
            worker.run()

        cap.assert_not_called()
        self.assertEqual(client.calls, [("what time is it", None)])

    def test_captures_when_screenshot_enabled(self) -> None:
        client = _FakeAskClient()
        worker = AskWorker(client, "q", "dev", include_screenshot=True)

        with patch("main.capture.capture_monitor", return_value="C:/shot.png") as cap:
            worker.run()

        cap.assert_called_once_with("dev")
        self.assertEqual(client.calls, [("q", "C:/shot.png")])


class RestartTests(unittest.TestCase):
    def _assistant(self):
        assistant = VoiceAssistant.__new__(VoiceAssistant)
        assistant._hotkey = SimpleNamespace(unregister=lambda: events.append("unhook"))
        assistant._single_instance = SimpleNamespace(
            close=lambda: events.append("mutex_released")
        )
        assistant._tray = SimpleNamespace(
            hide=lambda: events.append("hide"),
            notify=lambda *a: events.append(("notify", a)),
        )
        assistant._app = SimpleNamespace(quit=lambda: events.append("quit"))
        events: list = []
        assistant._events = events
        return assistant

    def test_restart_releases_resources_relaunches_and_quits(self) -> None:
        assistant = self._assistant()

        with patch("main.subprocess.Popen") as popen, patch("main.logger.info"):
            assistant._restart()

        popen.assert_called_once()
        # Mutex + hotkey must be released before the relaunch so the new
        # instance can claim them; the old process then quits.
        self.assertIn("unhook", assistant._events)
        self.assertIn("mutex_released", assistant._events)
        self.assertIn("quit", assistant._events)

    def test_restart_failure_keeps_app_running_and_notifies(self) -> None:
        assistant = self._assistant()

        with (
            patch("main.subprocess.Popen", side_effect=OSError("nope")),
            patch("main.logger.warning"),
        ):
            assistant._restart()

        self.assertTrue(any(e[0] == "notify" for e in assistant._events if isinstance(e, tuple)))
        self.assertNotIn("quit", assistant._events)


if __name__ == "__main__":
    unittest.main()
