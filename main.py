"""Voice Assistant — entrypoint and pipeline wiring.

Flow: global hotkey → input window (Wispr types) → Enter → screenshot →
Claude (Opus) turn → ElevenLabs streaming TTS, with a non-intrusive status
overlay throughout.
"""
from __future__ import annotations

import ctypes
import sys

# DPI awareness MUST be set before QApplication so mss bounds == physical px.
if sys.platform == "win32":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
    except Exception:
        pass

from PySide6.QtCore import QObject, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon  # noqa: E402

import capture  # noqa: E402
import tts  # noqa: E402
from claude_client import (  # noqa: E402
    ClaudeClient,
    ClaudeError,
    ClaudeNotInstalledError,
)
from config import Config  # noqa: E402
from hotkey import HotkeyManager  # noqa: E402
from ui.input_window import InputWindow  # noqa: E402
from ui.settings import SettingsDialog  # noqa: E402
from ui.status_overlay import StatusOverlay  # noqa: E402
from ui.tray import Tray  # noqa: E402


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

    def __init__(self, text: str, voice_id: str, model_id: str) -> None:
        super().__init__()
        self._text = text
        self._voice_id = voice_id
        self._model_id = model_id

    def run(self) -> None:
        tts.speak(self._text, self._voice_id, self._model_id)
        self.finished_speaking.emit()


class VoiceAssistant(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._config = Config()
        self._config.ensure_capture_monitor()

        self._busy = False
        self._ask_worker: AskWorker | None = None
        self._speak_worker: SpeakWorker | None = None

        # --- UI pieces ---
        self._overlay = StatusOverlay(self._config)
        self._input = InputWindow(self._config)
        self._tray = Tray()
        self._tray.show()

        self._input.submitted.connect(self._on_question)
        self._input.cancelled.connect(self._on_cancelled)
        self._tray.open_settings.connect(self._open_settings)
        self._tray.toggle_pause.connect(self._on_pause_toggled)
        self._tray.quit_requested.connect(self._quit)

        # --- Claude client ---
        self._client: ClaudeClient | None = None
        try:
            self._client = ClaudeClient(self._config)
        except ClaudeNotInstalledError as exc:
            self._tray.notify("Claude not installed", str(exc))

        # --- hotkey ---
        mods = self._config.get("hotkey.mods", ["ctrl", "alt"])
        vk = self._config.get("hotkey.vk", "Space")
        self._hotkey = HotkeyManager(mods, vk)
        self._hotkey.activated.connect(self._on_hotkey)
        self._app.installNativeEventFilter(self._hotkey)
        if not self._hotkey.register():
            self._tray.notify(
                "Hotkey unavailable",
                "Could not register the global hotkey (already in use?).",
            )

    # --- pipeline -----------------------------------------------------------

    def _on_hotkey(self) -> None:
        if self._busy:
            return
        if self._client is None:
            self._tray.notify(
                "Claude not installed",
                "Install the Claude CLI: npm i -g @anthropic-ai/claude-code",
            )
            return
        self._busy = True
        self._overlay.show_listening()
        self._input.show_and_focus()

    def _on_cancelled(self) -> None:
        self._overlay.hide()
        self._busy = False

    def _on_question(self, text: str) -> None:
        if self._client is None:
            self._reset_with_error("Claude not installed")
            return
        self._overlay.show_thinking()
        self._ask_worker = AskWorker(
            self._client, text, self._config.capture_monitor_device
        )
        self._ask_worker.succeeded.connect(self._on_reply)
        self._ask_worker.failed.connect(self._on_ask_failed)
        self._ask_worker.start()

    def _on_reply(self, reply: str) -> None:
        self._overlay.show_speaking()
        self._speak_worker = SpeakWorker(
            reply, self._config.voice_id, self._config.tts_model
        )
        self._speak_worker.finished_speaking.connect(self._on_speech_done)
        self._speak_worker.start()

    def _on_speech_done(self) -> None:
        self._overlay.hide()
        self._busy = False

    def _on_ask_failed(self, message: str) -> None:
        self._tray.notify("Voice Assistant", message)
        self._reset_with_error(message)

    def _reset_with_error(self, message: str) -> None:
        self._overlay.show_error(message[:48])
        QTimer.singleShot(3000, self._overlay.hide)
        self._busy = False

    # --- tray actions -------------------------------------------------------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config)
        dialog.exec()

    def _on_pause_toggled(self, paused: bool) -> None:
        self._hotkey.set_paused(paused)

    def _quit(self) -> None:
        self._hotkey.unregister()
        self._tray.hide()
        self._app.quit()


def main() -> int:
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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
