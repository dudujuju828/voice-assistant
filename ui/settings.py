"""Settings dialog: capture monitor, transcript capture, hotkey, voice."""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)

import monitors
from config import (
    MAX_CLAUDE_TIMEOUT_SECONDS,
    MAX_TTS_REQUEST_TIMEOUT_SECONDS,
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

        # --- info ---
        info = QLabel("Settings are saved to %APPDATA%\\VoiceAssistant.", self)
        info.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(info)

        # --- buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_save(self) -> None:
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
        tts_model = _combo_value(self._tts_model_combo)
        if tts_model:
            updates["elevenlabs.model_id"] = tts_model
        updates["elevenlabs.request_timeout_seconds"] = (
            self._tts_timeout_input.value()
        )
        updates["elevenlabs.stability"] = self._stability_input.value()
        updates["elevenlabs.similarity_boost"] = self._similarity_input.value()
        updates["elevenlabs.speed"] = self._speed_input.value()
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
            return
        self.accept()
