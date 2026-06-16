"""Voice Assistant — entrypoint and pipeline wiring.

Push-to-talk flow: hold the hotkey (Wispr starts recording on the same keys) →
speak → release the hotkey → the transcribed text is captured silently from the
clipboard (or a hidden input box) → screenshot → Claude (Opus) turn →
ElevenLabs streaming TTS. A tiny corner dot is the only visible footprint.
"""
from __future__ import annotations

import ctypes
import logging
import sys

# DPI awareness MUST be set before QApplication so mss bounds == physical px.
if sys.platform == "win32":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
    except Exception:
        pass

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon  # noqa: E402

import app_logging  # noqa: E402
import capture  # noqa: E402
import runtime_checks  # noqa: E402
from single_instance import SingleInstance  # noqa: E402
import tts  # noqa: E402
from claude_client import (  # noqa: E402
    ClaudeClient,
    ClaudeError,
    ClaudeNotInstalledError,
)
from config import Config  # noqa: E402
from hidden_input import HiddenInput, VisibleInput  # noqa: E402
from hotkey import HotkeyManager  # noqa: E402
from ui.settings import SettingsDialog  # noqa: E402
from ui.status_overlay import StatusOverlay  # noqa: E402
from ui.tray import Tray  # noqa: E402

logger = logging.getLogger(__name__)


class AskWorker(QThread):
    """Captures the screen and runs one Claude turn off the UI thread."""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, client: ClaudeClient, question: str, device: str | None) -> None:
        super().__init__()
        self._client = client
        self._question = question
        self._device = device

    def run(self) -> None:
        try:
            shot = capture.capture_monitor(self._device)
        except Exception as exc:
            self.failed.emit(f"Screenshot failed: {exc}")
            return
        try:
            reply = self._client.ask(self._question, shot)
        except ClaudeError as exc:
            self.failed.emit(f"Claude error: {exc}")
            return
        except Exception as exc:  # defensive
            self.failed.emit(f"Unexpected error: {exc}")
            return
        self.succeeded.emit(reply)


class SpeakWorker(QThread):
    """Streams ElevenLabs TTS playback off the UI thread."""

    finished_speaking = Signal()
    failed = Signal(str)

    def __init__(
        self,
        text: str,
        voice_id: str,
        model_id: str,
        stability: float,
        similarity_boost: float,
        speed: float,
    ) -> None:
        super().__init__()
        self._text = text
        self._voice_id = voice_id
        self._model_id = model_id
        self._stability = stability
        self._similarity_boost = similarity_boost
        self._speed = speed

    def run(self) -> None:
        try:
            tts.speak(
                self._text,
                self._voice_id,
                self._model_id,
                self._stability,
                self._similarity_boost,
                self._speed,
            )
        except Exception as exc:  # defensive; tts.speak should degrade itself
            self.failed.emit(f"TTS error: {exc}")
        finally:
            self.finished_speaking.emit()


class VoiceAssistant(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._config = Config()
        self._config.ensure_capture_monitor()

        self._busy = False
        self._recording = False
        self._active_capture_method: str | None = None
        self._clipboard_capture_active = False
        self._clipboard_changed_during_capture = False
        self._clipboard_text_before_capture: str | None = None
        self._ask_worker: AskWorker | None = None
        self._speak_worker: SpeakWorker | None = None

        # --- UI pieces (near-zero visual footprint) ---
        self._overlay = StatusOverlay(self._config)
        self._hidden = HiddenInput()
        self._visible_input = VisibleInput(self._config)
        self._tray = Tray()
        self._tray.show()
        if runtime_checks.is_running_elevated():
            logger.warning("Application is running elevated; Wispr input may fail.")
            self._tray.notify(
                "Voice Assistant is elevated",
                "Run normally so Wispr can type into the capture input.",
            )

        self._tray.open_settings.connect(self._open_settings)
        self._tray.toggle_pause.connect(self._on_pause_toggled)
        self._tray.quit_requested.connect(self._quit)
        clipboard = self._app.clipboard()
        if clipboard:
            clipboard.dataChanged.connect(self._on_clipboard_changed)

        # --- Claude client ---
        self._client: ClaudeClient | None = None
        try:
            self._client = ClaudeClient(self._config)
        except ClaudeNotInstalledError as exc:
            self._tray.notify("Claude not installed", str(exc))

        # --- push-to-talk hotkey ---
        self._hotkey: HotkeyManager | None = None
        self._init_hotkey()

    # --- pipeline -----------------------------------------------------------

    def _init_hotkey(self) -> None:
        mods = self._config.get("hotkey.mods", ["ctrl"])
        vk = self._config.get("hotkey.vk", "Win")
        try:
            self._hotkey = HotkeyManager(mods, vk)
        except (AttributeError, OSError, ValueError) as exc:
            self._tray.notify("Hotkey unavailable", str(exc))
            return

        # Queued so heavy work never runs inside the keyboard hook callback.
        self._hotkey.pressed.connect(self._on_press, Qt.QueuedConnection)
        self._hotkey.released.connect(self._on_release, Qt.QueuedConnection)
        if not self._hotkey.register():
            self._tray.notify(
                "Hotkey unavailable",
                "Could not install the keyboard hook for the hotkey.",
            )

    def _on_press(self) -> None:
        """Hotkey down: Wispr starts recording; we just show the dot."""
        if self._busy or self._recording:
            return
        if self._client is None:
            self._tray.notify(
                "Claude not installed",
                "Install the Claude CLI: npm i -g @anthropic-ai/claude-code",
            )
            return
        self._recording = True
        self._active_capture_method = self._config.capture_method
        self._overlay.show_recording()
        if self._active_capture_method == "hidden_input":
            self._hidden.focus_for_capture()
        elif self._active_capture_method == "visible_input":
            self._visible_input.focus_for_capture()
        else:
            self._begin_clipboard_capture()

    def _on_release(self) -> None:
        """Hotkey up: give Wispr a moment, then capture and process."""
        if not self._recording:
            return
        self._recording = False
        self._busy = True
        self._overlay.show_processing()
        QTimer.singleShot(self._config.capture_delay_ms, self._capture_and_ask)

    def _capture_and_ask(self) -> None:
        text = self._read_transcript().strip()
        if not text:
            # Nothing was captured — quietly reset, no nagging UI.
            self._overlay.hide()
            self._busy = False
            return
        self._ask_worker = AskWorker(
            self._client, text, self._config.capture_monitor_device
        )
        self._ask_worker.succeeded.connect(self._on_reply)
        self._ask_worker.failed.connect(self._on_ask_failed)
        self._ask_worker.start()

    def _read_transcript(self) -> str:
        """Read the transcribed text from the configured capture source."""
        method = self._active_capture_method or self._config.capture_method
        self._active_capture_method = None
        if method == "hidden_input":
            return self._hidden.read_and_clear()
        if method == "visible_input":
            return self._visible_input.read_and_clear()
        return self._read_clipboard_transcript()

    def _read_clipboard_transcript(self) -> str:
        """Read clipboard text only if Wispr published fresh content."""
        clipboard = self._app.clipboard()
        text = clipboard.text().strip() if clipboard else ""
        changed = self._clipboard_changed_during_capture
        previous = self._clipboard_text_before_capture

        self._clipboard_capture_active = False
        self._clipboard_changed_during_capture = False
        self._clipboard_text_before_capture = None

        if not text:
            return ""
        if not changed and previous is not None and text == previous:
            # Wispr did not publish a fresh transcript; do not replay stale text.
            return ""
        return text

    def _begin_clipboard_capture(self) -> None:
        clipboard = self._app.clipboard()
        self._clipboard_capture_active = True
        self._clipboard_changed_during_capture = False
        self._clipboard_text_before_capture = (
            clipboard.text().strip() if clipboard else None
        )

    def _on_clipboard_changed(self) -> None:
        if self._clipboard_capture_active:
            self._clipboard_changed_during_capture = True

    def _on_reply(self, reply: str) -> None:
        self._overlay.show_speaking()
        self._speak_worker = SpeakWorker(
            reply,
            self._config.voice_id,
            self._config.tts_model,
            self._config.tts_stability,
            self._config.tts_similarity_boost,
            self._config.tts_speed,
        )
        self._speak_worker.failed.connect(self._on_speech_failed)
        self._speak_worker.finished_speaking.connect(self._on_speech_done)
        self._speak_worker.start()

    def _on_speech_done(self) -> None:
        self._overlay.hide()
        self._busy = False

    def _on_speech_failed(self, message: str) -> None:
        logger.warning(message)
        self._tray.notify("Voice Assistant", message)

    def _on_ask_failed(self, message: str) -> None:
        logger.warning(message)
        self._tray.notify("Voice Assistant", message)
        self._overlay.show_error()
        QTimer.singleShot(2500, self._overlay.hide)
        self._busy = False

    # --- tray actions -------------------------------------------------------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config)
        dialog.exec()

    def _on_pause_toggled(self, paused: bool) -> None:
        if self._hotkey is None:
            self._tray.notify("Hotkey unavailable", "No hotkey hook is active.")
            return
        self._hotkey.set_paused(paused)

    def _quit(self) -> None:
        if self._hotkey is not None:
            self._hotkey.unregister()
        self._tray.hide()
        self._app.quit()


def main() -> int:
    app_logging.setup_logging()
    single_instance = SingleInstance()
    if single_instance.already_running:
        logger.info("Another Voice Assistant instance is already running.")
        return 0

    if sys.platform != "win32":
        # The app depends on Win32 APIs; warn but allow import-level testing.
        print("Voice Assistant targets Windows; some features will not work here.")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None, "Voice Assistant", "No system tray available on this system."
        )
        return 1

    assistant = VoiceAssistant(app)  # noqa: F841 (kept alive for app lifetime)
    try:
        return app.exec()
    finally:
        single_instance.close()


if __name__ == "__main__":
    sys.exit(main())
