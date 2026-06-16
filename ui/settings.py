"""Settings dialog: capture monitor, transcript capture, hotkey, voice."""
from __future__ import annotations

import os

import requests
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)

import monitors

# Hardcoded fallback so the picker works offline / without an API key.
_FALLBACK_VOICES = [
    ("Rachel", "21m00Tcm4TlvDq8ikWAM"),
    ("Adam", "pNInz6obpgDQGcFmaJgB"),
    ("Antoni", "ErXwobaYiN019PkySvjV"),
    ("Bella", "EXAVITQu4vr4xnSDxMaL"),
]

_CLAUDE_MODELS = [
    ("Opus", "opus"),
    ("Sonnet", "sonnet"),
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
            timeout=8,
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

        # --- hotkey (read-only for now) ---
        mods = config.get("hotkey.mods", ["ctrl"])
        vk = config.get("hotkey.vk", "Win")
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

        # --- voice dropdown ---
        self._voice_combo = QComboBox(self)
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
        device = self._monitor_combo.currentData()
        if device is not None:
            self._config.capture_monitor_device = device
        voice_id = self._voice_combo.currentData()
        if voice_id:
            self._config.voice_id = voice_id
        capture_method = self._capture_method_combo.currentData()
        if capture_method:
            self._config.capture_method = capture_method
        claude_model = _combo_value(self._claude_model_combo)
        if claude_model:
            self._config.claude_model = claude_model
        claude_effort = self._claude_effort_combo.currentData()
        if claude_effort:
            self._config.claude_effort = claude_effort
        tts_model = _combo_value(self._tts_model_combo)
        if tts_model:
            self._config.tts_model = tts_model
        self._config.tts_stability = self._stability_input.value()
        self._config.tts_similarity_boost = self._similarity_input.value()
        self._config.tts_speed = self._speed_input.value()
        self.accept()
