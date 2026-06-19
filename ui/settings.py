"""Settings dialog: capture monitor, transcript capture, hotkey, voice."""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWidget,
)

import monitors
from config import (
    MAX_BROWSER_TIMEOUT_SECONDS,
    MAX_CLAUDE_TIMEOUT_SECONDS,
    MAX_TTS_REQUEST_TIMEOUT_SECONDS,
    MIN_BROWSER_TIMEOUT_SECONDS,
    MIN_CLAUDE_TIMEOUT_SECONDS,
    MIN_TTS_REQUEST_TIMEOUT_SECONDS,
)

load_dotenv()

# Hardcoded fallback so the picker works offline / without an API key.
# These are current premade voices (usable on the free plan); legacy IDs like
# Rachel's 21m00... are now treated as library voices and 402 on free accounts.
_FALLBACK_VOICES = [
    ("Lily", "pFZP5JQG7iQjIQuC4Bku"),
    ("Sarah", "EXAVITQu4vr4xnSDxMaL"),
    ("George", "JBFqnCBsd6RMkjVDRZzb"),
    ("Bella", "hpp4J3VqNfWAUOO0d1Us"),
    ("Brian", "nPczCjzI2devNBz1zQrb"),
]

_CLAUDE_MODELS = [
    ("Haiku (fastest)", "haiku"),
    ("Sonnet", "sonnet"),
    ("Opus", "opus"),
    ("Fable", "fable"),
]

_CLAUDE_EFFORTS = [
    ("Default", "default"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
    ("Extra high", "xhigh"),
    ("Maximum", "max"),
]

_TTS_MODELS = [
    ("Flash v2.5", "eleven_flash_v2_5"),
    ("Turbo v2.5", "eleven_turbo_v2_5"),
    ("Multilingual v2", "eleven_multilingual_v2"),
    ("Eleven v3", "eleven_v3"),
]

_TTS_PROVIDERS = [
    ("ElevenLabs (API)", "elevenlabs"),
    ("Local — Kokoro (offline)", "local"),
    ("Local — Chatterbox (voice cloning)", "chatterbox"),
]

# A handful of Kokoro voices for the local picker (it's editable, so any voice
# id works). af_/am_ = American female/male, bf_/bm_ = British female/male.
_LOCAL_VOICES = [
    ("Heart (US female)", "af_heart"),
    ("Bella (US female)", "af_bella"),
    ("Nicole (US female)", "af_nicole"),
    ("Michael (US male)", "am_michael"),
    ("Adam (US male)", "am_adam"),
    ("Emma (UK female)", "bf_emma"),
    ("George (UK male)", "bm_george"),
]


def _fetch_voices() -> list[tuple[str, str]]:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return _FALLBACK_VOICES
    try:
        resp = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": key},
            timeout=4,
        )
        resp.raise_for_status()
        voices = resp.json().get("voices", [])
        parsed = [
            (v.get("name", "Voice"), v["voice_id"])
            for v in voices
            if v.get("voice_id")
        ]
        return parsed or _FALLBACK_VOICES
    except (requests.RequestException, ValueError, KeyError):
        return _FALLBACK_VOICES


def _set_combo_value(combo: QComboBox, value: str) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return
    if combo.isEditable():
        combo.setCurrentText(value)


def _combo_value(combo: QComboBox) -> str:
    text = combo.currentText().strip()
    current_index = combo.currentIndex()
    if combo.isEditable() and current_index >= 0 and text != combo.itemText(current_index):
        return text
    data = combo.currentData()
    if data:
        return str(data)
    return text


def _number_input(
    value: float,
    minimum: float,
    maximum: float,
    step: float,
    decimals: int = 2,
) -> QDoubleSpinBox:
    field = QDoubleSpinBox()
    field.setRange(minimum, maximum)
    field.setSingleStep(step)
    field.setDecimals(decimals)
    field.setValue(value)
    return field


class SettingsDialog(QDialog):
    # Emitted when the user asks to relaunch the app (after settings are saved).
    restart_requested = Signal()

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Voice Assistant — Settings")
        self.setMinimumWidth(420)

        form = QFormLayout(self)

        # --- monitor dropdown ---
        self._monitor_combo = QComboBox(self)
        self._monitors = monitors.list_monitors()
        current_device = config.capture_monitor_device
        for index, mon in enumerate(self._monitors):
            label = mon["name"]
            if mon["is_primary"]:
                label += " (Primary)"
            self._monitor_combo.addItem(label, mon["device"])
            if mon["device"] == current_device:
                self._monitor_combo.setCurrentIndex(index)
        if not self._monitors:
            self._monitor_combo.addItem("No monitors detected", None)
        form.addRow("Capture monitor:", self._monitor_combo)

        # --- screenshot toggle ---
        self._screenshot_check = QCheckBox(
            "Send a screenshot of your screen with each question", self
        )
        self._screenshot_check.setChecked(config.include_screenshot)
        self._screenshot_check.setToolTip(
            "Turn off to use it as a plain voice assistant (no screen capture)."
        )
        form.addRow("Screenshot:", self._screenshot_check)

        # --- transcript capture method ---
        self._capture_method_combo = QComboBox(self)
        self._capture_method_combo.addItem("Clipboard (Wispr copy)", "clipboard")
        self._capture_method_combo.addItem("Hidden input (Wispr type)", "hidden_input")
        self._capture_method_combo.addItem(
            "Visible text box (Wispr type)", "visible_input"
        )
        current_method = config.capture_method
        for index in range(self._capture_method_combo.count()):
            if self._capture_method_combo.itemData(index) == current_method:
                self._capture_method_combo.setCurrentIndex(index)
                break
        form.addRow("Transcript capture:", self._capture_method_combo)

        self._capture_delay_input = QSpinBox(self)
        self._capture_delay_input.setRange(0, 10000)
        self._capture_delay_input.setSingleStep(100)
        self._capture_delay_input.setSuffix(" ms")
        self._capture_delay_input.setValue(config.capture_delay_ms)
        form.addRow("Capture delay:", self._capture_delay_input)

        # --- hotkey (read-only for now) ---
        mods = config.hotkey_mods
        vk = config.hotkey_vk
        hotkey_text = (
            " + ".join([m.capitalize() for m in mods] + [vk]) + "  (hold to talk)"
        )
        hotkey_field = QLineEdit(hotkey_text, self)
        hotkey_field.setReadOnly(True)
        form.addRow("Hotkey:", hotkey_field)

        # --- Claude controls ---
        self._claude_model_combo = QComboBox(self)
        self._claude_model_combo.setEditable(True)
        for label, model in _CLAUDE_MODELS:
            self._claude_model_combo.addItem(label, model)
        _set_combo_value(self._claude_model_combo, config.claude_model)
        form.addRow("Claude model:", self._claude_model_combo)

        self._claude_effort_combo = QComboBox(self)
        for label, effort in _CLAUDE_EFFORTS:
            self._claude_effort_combo.addItem(label, effort)
        _set_combo_value(self._claude_effort_combo, config.claude_effort)
        form.addRow("Claude effort:", self._claude_effort_combo)

        self._claude_timeout_input = QSpinBox(self)
        self._claude_timeout_input.setRange(
            MIN_CLAUDE_TIMEOUT_SECONDS,
            MAX_CLAUDE_TIMEOUT_SECONDS,
        )
        self._claude_timeout_input.setSingleStep(10)
        self._claude_timeout_input.setSuffix(" s")
        self._claude_timeout_input.setValue(config.claude_timeout_seconds)
        form.addRow("Claude timeout:", self._claude_timeout_input)

        # --- TTS provider (ElevenLabs API vs local Kokoro) ---
        self._tts_provider_combo = QComboBox(self)
        for label, provider in _TTS_PROVIDERS:
            self._tts_provider_combo.addItem(label, provider)
        _set_combo_value(self._tts_provider_combo, config.tts_provider)
        form.addRow("TTS provider:", self._tts_provider_combo)

        # --- local (Kokoro) voice picker ---
        self._local_voice_combo = QComboBox(self)
        self._local_voice_combo.setEditable(True)
        for label, voice in _LOCAL_VOICES:
            self._local_voice_combo.addItem(label, voice)
        _set_combo_value(self._local_voice_combo, config.tts_local_voice)
        form.addRow("Local voice:", self._local_voice_combo)

        # --- Chatterbox voice sample (for voice cloning) ---
        self._voice_sample_input = QLineEdit(config.tts_voice_sample, self)
        self._voice_sample_input.setPlaceholderText(
            "Blank = bundled models/voice_sample.wav, else built-in voice"
        )
        self._voice_sample_input.setToolTip(
            "Path to a 7–20 s WAV of the voice Chatterbox should clone."
        )
        browse_sample = QPushButton("Browse…", self)
        browse_sample.clicked.connect(self._browse_voice_sample)
        sample_row = QWidget(self)
        sample_layout = QHBoxLayout(sample_row)
        sample_layout.setContentsMargins(0, 0, 0, 0)
        sample_layout.addWidget(self._voice_sample_input)
        sample_layout.addWidget(browse_sample)
        form.addRow("Cloning voice sample:", sample_row)

        # --- voice dropdown ---
        self._voice_combo = QComboBox(self)
        self._voice_combo.setEditable(True)
        current_voice = config.voice_id
        voice_found = False
        selected_index = 0
        for index, (name, voice_id) in enumerate(_fetch_voices()):
            self._voice_combo.addItem(name, voice_id)
            if voice_id == current_voice:
                selected_index = index
                voice_found = True
        if current_voice and not voice_found:
            selected_index = self._voice_combo.count()
            self._voice_combo.addItem(f"Current ({current_voice})", current_voice)
        self._voice_combo.setCurrentIndex(selected_index)
        form.addRow("ElevenLabs voice:", self._voice_combo)

        self._tts_model_combo = QComboBox(self)
        self._tts_model_combo.setEditable(True)
        for label, model in _TTS_MODELS:
            self._tts_model_combo.addItem(label, model)
        _set_combo_value(self._tts_model_combo, config.tts_model)
        form.addRow("TTS model:", self._tts_model_combo)

        self._tts_timeout_input = QSpinBox(self)
        self._tts_timeout_input.setRange(
            MIN_TTS_REQUEST_TIMEOUT_SECONDS,
            MAX_TTS_REQUEST_TIMEOUT_SECONDS,
        )
        self._tts_timeout_input.setSingleStep(5)
        self._tts_timeout_input.setSuffix(" s")
        self._tts_timeout_input.setValue(config.tts_request_timeout_seconds)
        form.addRow("TTS timeout:", self._tts_timeout_input)

        self._stability_input = _number_input(config.tts_stability, 0.0, 1.0, 0.05)
        form.addRow("Voice stability:", self._stability_input)

        self._similarity_input = _number_input(
            config.tts_similarity_boost, 0.0, 1.0, 0.05
        )
        form.addRow("Voice similarity:", self._similarity_input)

        self._speed_input = _number_input(config.tts_speed, 0.7, 1.2, 0.05)
        form.addRow("Voice speed:", self._speed_input)

        # --- agentic browsing (Claude-driven Chrome) ---
        self._browser_enabled_check = QCheckBox(
            "Let the assistant open and drive a web browser", self
        )
        self._browser_enabled_check.setChecked(config.browser_enabled)
        self._browser_enabled_check.setToolTip(
            "When on, asking it to search or open a page launches Chrome and "
            "Claude navigates it on your screen. Needs Node.js + Chrome."
        )
        form.addRow("Agentic browsing:", self._browser_enabled_check)

        self._browser_headless_check = QCheckBox("Run hidden (no window)", self)
        self._browser_headless_check.setChecked(config.browser_headless)
        self._browser_headless_check.setToolTip(
            "Off (default) shows the browser so you can watch it navigate."
        )
        form.addRow("Browser window:", self._browser_headless_check)

        self._browser_monitor_combo = QComboBox(self)
        self._browser_monitor_combo.addItem("Auto (secondary screen)", None)
        current_browser_device = config.browser_monitor_device
        for mon in self._monitors:
            label = mon["name"] + (" (Primary)" if mon["is_primary"] else "")
            self._browser_monitor_combo.addItem(label, mon["device"])
        _set_combo_value(self._browser_monitor_combo, current_browser_device)
        form.addRow("Browser monitor:", self._browser_monitor_combo)

        self._browser_timeout_input = QSpinBox(self)
        self._browser_timeout_input.setRange(
            MIN_BROWSER_TIMEOUT_SECONDS,
            MAX_BROWSER_TIMEOUT_SECONDS,
        )
        self._browser_timeout_input.setSingleStep(30)
        self._browser_timeout_input.setSuffix(" s")
        self._browser_timeout_input.setValue(config.browser_timeout_seconds)
        form.addRow("Browse turn timeout:", self._browser_timeout_input)

        # --- info ---
        info = QLabel("Settings are saved to %APPDATA%\\VoiceAssistant.", self)
        info.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(info)

        # --- buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, self
        )
        # Saves first so any pending changes apply after the relaunch.
        self._restart_button = buttons.addButton(
            "Save && Restart", QDialogButtonBox.ActionRole
        )
        self._restart_button.setToolTip("Save settings, then close and relaunch the app.")
        self._restart_button.clicked.connect(self._on_restart)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_voice_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a voice sample to clone",
            self._voice_sample_input.text().strip() or "",
            "Audio files (*.wav *.mp3 *.flac);;All files (*)",
        )
        if path:
            self._voice_sample_input.setText(path)

    def _on_save(self) -> None:
        if self._save():
            self.accept()

    def _on_restart(self) -> None:
        # Persist first so the new process starts with the latest settings.
        if self._save():
            self.restart_requested.emit()
            self.accept()

    def _save(self) -> bool:
        """Collect the form and persist it. Returns True on success."""
        updates = {}
        device = self._monitor_combo.currentData()
        if device is not None:
            updates["capture_monitor_device"] = device
        voice_id = _combo_value(self._voice_combo)
        if voice_id:
            updates["elevenlabs.voice_id"] = voice_id
        capture_method = self._capture_method_combo.currentData()
        if capture_method:
            updates["capture.method"] = capture_method
        updates["capture.delay_ms"] = self._capture_delay_input.value()
        updates["capture.include_screenshot"] = self._screenshot_check.isChecked()
        claude_model = _combo_value(self._claude_model_combo)
        if claude_model:
            updates["claude.model"] = claude_model
        claude_effort = self._claude_effort_combo.currentData()
        if claude_effort:
            updates["claude.effort"] = claude_effort
        updates["claude.timeout_seconds"] = self._claude_timeout_input.value()
        tts_provider = self._tts_provider_combo.currentData()
        if tts_provider:
            updates["tts.provider"] = tts_provider
        local_voice = _combo_value(self._local_voice_combo)
        if local_voice:
            updates["tts.local_voice"] = local_voice
        updates["tts.voice_sample"] = self._voice_sample_input.text().strip()
        tts_model = _combo_value(self._tts_model_combo)
        if tts_model:
            updates["elevenlabs.model_id"] = tts_model
        updates["elevenlabs.request_timeout_seconds"] = (
            self._tts_timeout_input.value()
        )
        updates["elevenlabs.stability"] = self._stability_input.value()
        updates["elevenlabs.similarity_boost"] = self._similarity_input.value()
        updates["elevenlabs.speed"] = self._speed_input.value()
        updates["browser.enabled"] = self._browser_enabled_check.isChecked()
        updates["browser.headless"] = self._browser_headless_check.isChecked()
        updates["browser.monitor_device"] = self._browser_monitor_combo.currentData()
        updates["browser.timeout_seconds"] = self._browser_timeout_input.value()
        try:
            self._config.set_many(updates)
        except Exception as exc:
            # set_many rolls back in-memory state and re-raises on disk failure;
            # keep the dialog open and tell the user instead of crashing the slot.
            QMessageBox.warning(
                self,
                "Voice Assistant",
                f"Could not save settings:\n{exc}",
            )
            return False
        return True
