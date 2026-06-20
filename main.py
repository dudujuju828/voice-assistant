"""Voice Assistant — entrypoint and pipeline wiring.

Push-to-talk flow: hold the hotkey (Wispr starts recording on the same keys) →
speak → release the hotkey → the transcribed text is captured silently from the
clipboard (or a hidden input box) → screenshot → Claude (Opus) turn →
ElevenLabs streaming TTS. A tiny corner dot is the only visible footprint.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser

# DPI awareness MUST be set before QApplication so mss bounds == physical px.
if sys.platform == "win32":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
    except Exception:
        pass

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon  # noqa: E402

import app_logging  # noqa: E402
import browser_mcp  # noqa: E402
import capture  # noqa: E402
import coding  # noqa: E402
import runtime_checks  # noqa: E402
from single_instance import SingleInstance  # noqa: E402
import tts  # noqa: E402
import tts_chatterbox  # noqa: E402
import tts_local  # noqa: E402
from claude_client import (  # noqa: E402
    ClaudeClient,
    ClaudeError,
    ClaudeNotInstalledError,
)
from config import Config  # noqa: E402
from hidden_input import HiddenInput, VisibleInput  # noqa: E402
from hotkey import HotkeyManager  # noqa: E402
from transcript import TranscriptStore  # noqa: E402
from transcript_server import TranscriptServer  # noqa: E402
from ui.settings import SettingsDialog  # noqa: E402
from ui.status_overlay import StatusOverlay  # noqa: E402
from ui.tray import Tray  # noqa: E402

logger = logging.getLogger(__name__)

# Transcript-capture polling (see VoiceAssistant._poll_capture). Wispr delivers
# the transcript after release; a long message can take a few seconds to finish
# arriving, so we poll instead of reading once at a fixed delay.
_CAPTURE_POLL_INTERVAL_MS = 150
_CAPTURE_EMPTY_TIMEOUT_MS = 2000  # give up if nothing ever arrives
_CAPTURE_MAX_WAIT_MS = 6000       # hard cap once some text has appeared


class AskWorker(QThread):
    """Captures the screen and runs one Claude turn off the UI thread."""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        client: ClaudeClient,
        question: str,
        device: str | None,
        include_screenshot: bool,
        browser_server: "browser_mcp.BrowserSession | None" = None,
        coding_cwd: str | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._question = question
        self._device = device
        self._include_screenshot = include_screenshot
        self._browser_server = browser_server
        self._coding_cwd = coding_cwd

    def run(self) -> None:
        shot = None
        if self._include_screenshot:
            try:
                shot = capture.capture_monitor(self._device)
            except Exception as exc:
                self.failed.emit(f"Screenshot failed: {exc}")
                return
        # If this is a browsing turn, make sure the persistent browser MCP server
        # is up before the turn. The (possibly slow, first-run) startup happens
        # here on the worker thread, not the UI thread. If it can't start, fall
        # back to a normal turn so the user still gets an answer.
        mcp_config_path = None
        if self._browser_server is not None:
            try:
                if self._browser_server.ensure_ready():
                    mcp_config_path = self._browser_server.mcp_config_path
                else:
                    logger.warning("Browser not ready; answering without it.")
            except Exception as exc:
                logger.warning("Browser MCP server failed to start: %s", exc)
        try:
            reply = self._client.ask(
                self._question, shot, mcp_config_path, self._coding_cwd
            )
        except ClaudeError as exc:
            self.failed.emit(f"Claude error: {exc}")
            return
        except Exception as exc:  # defensive
            self.failed.emit(f"Unexpected error: {exc}")
            return
        self.succeeded.emit(reply)


class SpeakWorker(QThread):
    """Plays a reply via the configured TTS provider off the UI thread.

    Dispatches on ``provider``: the ElevenLabs API (tts.speak), the local Kokoro
    model (tts_local.speak_local), or local Chatterbox voice cloning
    (tts_chatterbox.speak_chatterbox). All honour ``cancel`` so barge-in can
    abort playback mid-sentence.
    """

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
        request_timeout: float,
        cancel: threading.Event,
        provider: str = "elevenlabs",
        local_voice: str = "af_heart",
        voice_sample: str = "",
    ) -> None:
        super().__init__()
        self._text = text
        self._voice_id = voice_id
        self._model_id = model_id
        self._stability = stability
        self._similarity_boost = similarity_boost
        self._speed = speed
        self._request_timeout = request_timeout
        self._cancel = cancel
        self._provider = provider
        self._local_voice = local_voice
        self._voice_sample = voice_sample

    def run(self) -> None:
        try:
            if self._provider == "chatterbox":
                played = tts_chatterbox.speak_chatterbox(
                    self._text,
                    self._voice_sample,
                    cancel=self._cancel,
                )
            elif self._provider == "local":
                played = tts_local.speak_local(
                    self._text,
                    self._local_voice,
                    self._speed,
                    self._cancel,
                )
            else:
                played = tts.speak(
                    self._text,
                    self._voice_id,
                    self._model_id,
                    self._stability,
                    self._similarity_boost,
                    self._speed,
                    self._request_timeout,
                    self._cancel,
                )
            if not played and not self._cancel.is_set():
                self.failed.emit(
                    "TTS playback failed. Check ElevenLabs, audio, and logs."
                )
        except Exception as exc:  # defensive; tts.speak should degrade itself
            self.failed.emit(f"TTS error: {exc}")
        finally:
            self.finished_speaking.emit()


class VoiceAssistant(QObject):
    def __init__(self, app: QApplication, single_instance=None) -> None:
        super().__init__()
        self._app = app
        # Held so a user-requested restart can release the mutex before
        # relaunching (otherwise the new instance sees us and exits).
        self._single_instance = single_instance
        self._config = Config()
        self._config.ensure_capture_monitor()

        self._busy = False
        self._recording = False
        self._error_token = 0
        # True while the in-flight turn is a coding turn, so the reply is linked
        # back to the (separate) coding session in the transcript.
        self._pending_turn_coding = False
        # Agentic browsing: once a browse is triggered we stay in browsing mode
        # (tools stay attached across turns) until an exit phrase. The MCP server
        # is created lazily on the first browse and owned for the app's lifetime.
        self._browsing = False
        # True from when a browse turn starts until it completes. If a new browse
        # request arrives while this is still set, the previous browse never
        # finished (barge-in, timeout), so we start the new one in a fresh Claude
        # session instead of resuming the half-done task.
        self._browse_pending = False
        self._browser_server: browser_mcp.BrowserSession | None = None
        self._active_capture_method: str | None = None
        self._clipboard_capture_active = False
        self._clipboard_changed_during_capture = False
        self._clipboard_text_before_capture: str | None = None
        self._ask_worker: AskWorker | None = None
        self._speak_worker: SpeakWorker | None = None
        # Set to interrupt the current TTS playback when the user barges in.
        self._cancel_event: threading.Event | None = None
        # Transcript-capture polling state (see _poll_capture). The generation
        # counter lets a stale poll from a superseded turn bail out.
        self._capture_gen = 0
        self._capture_last_peek = ""
        self._capture_seen_text = False
        self._capture_started_at = 0.0
        # Strong refs to every live worker. A finished worker is removed and
        # deleteLater'd; a *hung* worker stays here so it is never garbage
        # collected mid-run (which crashes with "QThread destroyed while still
        # running"). It simply leaks until it unblocks.
        self._workers: set[QThread] = set()
        # Last-resort guard against a stuck turn (e.g. an audio device that
        # never drains): force the app back to idle so it can't wedge forever.
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog_timeout)

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
        self._tray.open_transcript.connect(self._open_transcript)
        self._tray.reset_session.connect(self._reset_claude_session)
        self._tray.toggle_pause.connect(self._on_pause_toggled)
        self._tray.restart_requested.connect(self._restart)
        self._tray.quit_requested.connect(self._quit)
        clipboard = self._app.clipboard()
        if clipboard:
            clipboard.dataChanged.connect(self._on_clipboard_changed)

        # --- transcript: record every turn and serve a live local page ---
        self._transcript: TranscriptStore | None = None
        self._transcript_server: TranscriptServer | None = None
        if self._config.transcript_enabled:
            try:
                self._transcript = TranscriptStore()
                self._transcript_server = TranscriptServer(
                    self._transcript, port=self._config.transcript_port
                )
                if self._transcript_server.start() is None:
                    self._transcript_server = None
            except Exception as exc:  # never let transcripts break startup
                logger.warning("Transcript page unavailable: %s", exc)
                self._transcript = None
                self._transcript_server = None

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
        mods = self._config.hotkey_mods
        vk = self._config.hotkey_vk
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
        """Hotkey down: Wispr starts recording; we just show the dot.

        If a turn is already in flight (thinking or speaking), this is a
        barge-in: interrupt it and start recording a fresh question.
        """
        if self._recording:
            return
        if self._client is None:
            self._tray.notify(
                "Claude not installed",
                "Install the Claude CLI: npm i -g @anthropic-ai/claude-code",
            )
            return
        if self._busy:
            self._barge_in()
        self._recording = True
        self._error_token += 1
        self._active_capture_method = self._config.capture_method
        try:
            self._overlay.show_recording()
            tx = self._tx()
            if tx is not None:
                tx.begin_recording()
            if self._active_capture_method == "clipboard":
                self._begin_clipboard_capture()
            else:
                self._focus_capture_box()
        except Exception as exc:
            self._fail_current_turn(f"Transcript input failed: {exc}")

    def _on_release(self) -> None:
        """Hotkey up: re-assert capture focus, give Wispr a moment, then read."""
        if not self._recording:
            return
        self._recording = False
        self._busy = True
        # Wispr types its transcript on key release. If focus drifted while
        # recording (e.g. the user clicked another window), the keystrokes would
        # land there instead, so re-grab the capture box now — right before
        # those keystrokes arrive.
        if self._active_capture_method in ("hidden_input", "visible_input"):
            try:
                self._focus_capture_box()
            except Exception as exc:
                logger.warning("Could not re-focus capture box on release: %s", exc)
        self._start_watchdog()
        self._overlay.show_processing()
        tx = self._tx()
        if tx is not None:
            tx.begin_processing()
        # Poll until the transcript settles rather than reading once at a fixed
        # delay — a long message may still be arriving from Wispr at that point.
        self._capture_gen += 1
        self._capture_last_peek = ""
        self._capture_seen_text = False
        self._capture_started_at = time.monotonic()
        gen = self._capture_gen
        QTimer.singleShot(
            self._config.capture_delay_ms, lambda: self._poll_capture(gen)
        )

    def _focus_capture_box(self) -> None:
        """Focus the active input box so Wispr's keystrokes land in it."""
        if self._active_capture_method == "hidden_input":
            self._hidden.focus_for_capture()
        elif self._active_capture_method == "visible_input":
            self._visible_input.focus_for_capture()

    def _poll_capture(self, gen: int) -> None:
        """Wait for the transcript to stop changing, then run the turn.

        Proceeds as soon as the text settles (unchanged between polls), so short
        messages are still snappy, but keeps waiting for a long message that is
        still being typed. Gives up early only if nothing ever arrives.
        """
        if gen != self._capture_gen or not self._busy:
            return  # Superseded by a new turn, barge-in, or reset.
        text = self._peek_transcript()
        if text:
            self._capture_seen_text = True
            tx = self._tx()
            if tx is not None:
                tx.set_partial(text)
        settled = bool(text) and text == self._capture_last_peek
        self._capture_last_peek = text
        elapsed_ms = (time.monotonic() - self._capture_started_at) * 1000
        gave_up_empty = (
            not self._capture_seen_text and elapsed_ms >= _CAPTURE_EMPTY_TIMEOUT_MS
        )
        if settled or gave_up_empty or elapsed_ms >= _CAPTURE_MAX_WAIT_MS:
            self._capture_and_ask()
            return
        QTimer.singleShot(_CAPTURE_POLL_INTERVAL_MS, lambda: self._poll_capture(gen))

    def _peek_transcript(self) -> str:
        """Read the current transcript without consuming it (for polling)."""
        method = self._active_capture_method or self._config.capture_method
        if method == "hidden_input":
            return self._hidden.peek_text()
        if method == "visible_input":
            return self._visible_input.peek_text()
        if not self._clipboard_changed_during_capture:
            return ""
        clipboard = self._app.clipboard()
        return clipboard.text().strip() if clipboard else ""

    def _capture_and_ask(self) -> None:
        try:
            text = self._read_transcript().strip()
        except Exception as exc:
            self._fail_current_turn(f"Transcript capture failed: {exc}")
            return
        if not text:
            # Nothing was captured — quietly reset, no nagging UI.
            self._return_to_idle()
            return
        if self._client is None:
            self._fail_current_turn("Claude is not available.")
            return
        browser_server = self._resolve_browser_for_turn(text)
        if browser_server is not None:
            # Browse turns run longer; widen the watchdog so it only fires on a
            # true hang, not on a normal (slow) navigation.
            self._start_watchdog(browsing=True)
            # Mark this browse as in-flight; cleared only when it completes.
            self._browse_pending = True
        # A coding turn runs Claude Code inside the configured codebase. Browsing
        # is a stateful mode and takes precedence, so coding is only considered
        # when this isn't a browse turn.
        coding_cwd = (
            None if browser_server is not None else self._resolve_coding_for_turn(text)
        )
        self._pending_turn_coding = coding_cwd is not None
        # Coding turns are grounded in the codebase, not the screen.
        include_screenshot = self._config.include_screenshot and coding_cwd is None
        self._record_user_turn(text, coding_cwd)
        self._ask_worker = AskWorker(
            self._client,
            text,
            self._config.capture_monitor_device,
            include_screenshot,
            browser_server,
            coding_cwd,
        )
        self._ask_worker.succeeded.connect(self._on_reply)
        self._ask_worker.failed.connect(self._on_ask_failed)
        self._track_worker(self._ask_worker)
        self._ask_worker.start()

    def _resolve_browser_for_turn(self, text: str):
        """Decide whether this turn may browse, managing browsing mode.

        Returns the (lazily created) browser session when browsing is on, else
        None. An explicit "close the browser" phrase exits the mode and shuts the
        window; a browse request enters it; otherwise we stay in whatever mode
        the previous turn left us in, so follow-ups like "scroll down" continue.

        A *new* browse request that arrives while a previous browse never
        completed (``_browse_pending``) starts a fresh Claude session, so the new
        task doesn't resume and continue the abandoned one.
        """
        if not self._config.browser_enabled:
            self._browsing = False
            return None
        if browser_mcp.looks_like_browse_exit(text):
            self._browsing = False
            self._stop_browser_server()
            return None
        if browser_mcp.looks_like_browse_request(text):
            if self._browse_pending:
                logger.info(
                    "New browse request supersedes an unfinished one; "
                    "starting a fresh Claude session."
                )
                self._config.session_id = None
            self._browsing = True
        if not self._browsing:
            return None
        if self._browser_server is None:
            self._browser_server = browser_mcp.BrowserSession(self._config)
        return self._browser_server

    def _stop_browser_server(self) -> None:
        server = getattr(self, "_browser_server", None)
        if server is not None:
            server.stop()

    def _tx(self) -> "TranscriptStore | None":
        """The transcript store, or None when transcripts are off/unavailable.

        Read via getattr so it's safe before __init__ finishes (and in tests
        that build the assistant directly).
        """
        return getattr(self, "_transcript", None)

    def _resolve_coding_for_turn(self, text: str) -> str | None:
        """Codebase path to run Claude Code against for a coding turn, else None.

        Returns a path only when coding mode is enabled, the request sounds like
        a coding / file-editing task, and the configured codebase folder exists.
        Anything else falls through to a normal turn, so plain voice use is
        untouched.
        """
        if not self._config.coding_enabled:
            return None
        if not coding.looks_like_coding_request(text):
            return None
        path = self._config.coding_path
        if not path:
            return None
        if not os.path.isdir(path):
            logger.warning(
                "Coding path is not a folder; answering normally: %s", path
            )
            return None
        return path

    def _record_user_turn(self, text: str, coding_cwd: str | None) -> None:
        """Record what the user said under the right conversation."""
        tx = self._tx()
        if tx is None:
            return
        if coding_cwd:
            tx.record_user(
                text,
                session_id=self._config.coding_session_id,
                kind="coding",
                path=coding_cwd,
            )
        else:
            tx.record_user(text, session_id=self._config.session_id, kind="voice")

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
        if self.sender() is not self._ask_worker:
            return  # Stale worker from a timed-out turn; ignore.
        self._overlay.show_speaking()
        tx = self._tx()
        if tx is not None:
            session_id = (
                self._config.coding_session_id
                if getattr(self, "_pending_turn_coding", False)
                else self._config.session_id
            )
            tx.record_assistant(reply, session_id=session_id)
        self._cancel_event = threading.Event()
        self._speak_worker = SpeakWorker(
            reply,
            self._config.voice_id,
            self._config.tts_model,
            self._config.tts_stability,
            self._config.tts_similarity_boost,
            self._config.tts_speed,
            self._config.tts_request_timeout_seconds,
            self._cancel_event,
            self._config.tts_provider,
            self._config.tts_local_voice,
            self._config.tts_voice_sample,
        )
        self._speak_worker.failed.connect(self._on_speech_failed)
        self._speak_worker.finished_speaking.connect(self._on_speech_done)
        self._track_worker(self._speak_worker)
        self._speak_worker.start()

    def _on_speech_done(self) -> None:
        if self.sender() is not self._speak_worker:
            return  # Stale worker from a timed-out turn; ignore.
        # The turn finished cleanly, so any in-flight browse is now complete.
        self._browse_pending = False
        self._return_to_idle()

    def _on_speech_failed(self, message: str) -> None:
        if self.sender() is not self._speak_worker:
            return  # Stale worker from a barged-in/timed-out turn; ignore.
        logger.warning(message)
        self._tray.notify("Voice Assistant", message)

    def _on_ask_failed(self, message: str) -> None:
        if self.sender() is not self._ask_worker:
            return  # Stale worker from a timed-out turn; ignore.
        self._fail_current_turn(message)

    # --- worker + watchdog lifecycle ----------------------------------------

    def _track_worker(self, worker: QThread) -> None:
        """Hold a strong ref until the worker finishes, then clean it up."""
        self._workers.add(worker)
        worker.finished.connect(self._on_worker_finished)

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker is not None:
            self._workers.discard(worker)
            worker.deleteLater()

    def _start_watchdog(self, browsing: bool = False) -> None:
        # Budget beyond every internal timeout so it only fires on a true hang.
        # Browse turns use the (longer) browser timeout, plus extra slack for the
        # first-run server startup.
        if browsing:
            turn_budget = self._config.browser_timeout_seconds + 120
        else:
            turn_budget = self._config.claude_timeout_seconds
        budget = turn_budget + self._config.tts_request_timeout_seconds + 60
        self._watchdog.start(budget * 1000)

    def _stop_watchdog(self) -> None:
        watchdog = getattr(self, "_watchdog", None)
        if watchdog is not None:
            watchdog.stop()

    def _on_watchdog_timeout(self) -> None:
        if not self._busy and not self._recording:
            return
        logger.warning("Turn watchdog fired; a worker is stuck. Returning to idle.")
        self._tray.notify(
            "Voice Assistant", "That turn timed out. Ready for the next one."
        )
        self._reset_state()
        self._overlay.hide()
        tx = self._tx()
        if tx is not None:
            tx.set_idle()

    def _barge_in(self) -> None:
        """Interrupt the in-flight turn so a new recording can start.

        Signals the current TTS playback to stop and detaches the ask/speak
        workers. The workers keep running on their threads until they unwind,
        but their results and finish signals are ignored: every slot guards on
        ``self.sender() is self._ask_worker`` / ``self._speak_worker``, and we
        clear both here so the stale callbacks no-op instead of dragging the
        app back to idle on top of the new recording.
        """
        logger.info("Barge-in: interrupting the current turn.")
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._ask_worker = None
        self._speak_worker = None
        self._stop_watchdog()
        self._busy = False

    def _reset_state(self) -> None:
        self._recording = False
        self._busy = False
        self._error_token += 1
        self._active_capture_method = None
        self._clipboard_capture_active = False
        self._clipboard_changed_during_capture = False
        self._clipboard_text_before_capture = None
        # Invalidate any in-flight capture poll.
        self._capture_gen += 1
        self._capture_last_peek = ""
        self._capture_seen_text = False
        self._stop_watchdog()

    def _return_to_idle(self) -> None:
        self._reset_state()
        self._overlay.hide()
        tx = self._tx()
        if tx is not None:
            tx.set_idle()

    def _fail_current_turn(self, message: str) -> None:
        logger.warning(message)
        self._tray.notify("Voice Assistant", message)
        self._reset_state()
        self._overlay.show_error()
        tx = self._tx()
        if tx is not None:
            tx.record_error(message)
        self._error_token += 1
        token = self._error_token
        QTimer.singleShot(2500, lambda: self._hide_error(token))

    def _hide_error(self, token: int) -> None:
        if token == self._error_token:
            self._overlay.hide()

    # --- tray actions -------------------------------------------------------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config)
        # Restart is deferred until the modal dialog closes, so we don't tear
        # the app down from inside its event loop.
        restart_requested = {"value": False}
        dialog.restart_requested.connect(
            lambda: restart_requested.__setitem__("value", True)
        )
        dialog.exec()
        if restart_requested["value"]:
            self._restart()

    def _open_transcript(self) -> None:
        """Open the live transcript page in the default browser."""
        server = getattr(self, "_transcript_server", None)
        url = server.url if server is not None else None
        if not url:
            self._tray.notify(
                "Voice Assistant",
                "The transcript page isn't running. Enable it in Settings.",
            )
            return
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.warning("Could not open transcript page: %s", exc)
            self._tray.notify("Voice Assistant", f"Transcript page: {url}")

    def _stop_transcript_server(self) -> None:
        server = getattr(self, "_transcript_server", None)
        if server is not None:
            server.stop()

    def _reset_claude_session(self) -> None:
        self._config.session_id = None
        # The conversation is gone, so leave browsing mode too (the open window
        # stays put for the user; a new browse request will re-enter the mode).
        self._browsing = False
        logger.info("Claude session reset by user.")
        self._tray.notify("Voice Assistant", "Claude session reset.")

    def _on_pause_toggled(self, paused: bool) -> None:
        if self._hotkey is None:
            self._tray.notify("Hotkey unavailable", "No hotkey hook is active.")
            self._tray.set_paused(False)
            return
        if not self._hotkey.set_paused(paused):
            self._tray.set_paused(True)
            self._tray.notify(
                "Hotkey unavailable",
                "Could not reinstall the keyboard hook for the hotkey.",
            )

    def _restart(self) -> None:
        """Relaunch the app in a fresh process, then quit this one.

        Releases the keyboard hook and the single-instance mutex first so the
        new process can claim them, then spawns a detached instance using the
        same (venv) interpreter so it stays windowless and keeps our packages.
        """
        logger.info("Restarting Voice Assistant by user request.")
        try:
            # Prefer the venv's windowless pythonw so no console window pops up.
            exe = os.path.join(sys.prefix, "Scripts", "pythonw.exe")
            if not os.path.exists(exe):
                exe = sys.executable
            script = os.path.abspath(sys.argv[0])
            if self._hotkey is not None:
                self._hotkey.unregister()
            self._stop_browser_server()
            self._stop_transcript_server()
            if self._single_instance is not None:
                self._single_instance.close()
            subprocess.Popen(
                [exe, script],
                cwd=os.path.dirname(script),
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
                close_fds=True,
            )
        except Exception as exc:
            logger.warning("Restart failed: %s", exc)
            self._tray.notify("Voice Assistant", f"Could not restart: {exc}")
            return
        self._tray.hide()
        self._app.quit()

    def _quit(self) -> None:
        if self._hotkey is not None:
            self._hotkey.unregister()
        self._stop_browser_server()
        self._stop_transcript_server()
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

    # Pass the single-instance guard so a user-triggered restart can release
    # the mutex before relaunching.
    assistant = VoiceAssistant(app, single_instance)  # noqa: F841 (kept alive)
    try:
        return app.exec()
    finally:
        single_instance.close()


if __name__ == "__main__":
    sys.exit(main())
