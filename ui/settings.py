"""Settings dialog: capture monitor, hotkey display, ElevenLabs voice."""
from __future__ import annotations

import os

import requests
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

        # --- hotkey (read-only for now) ---
        mods = config.get("hotkey.mods", ["ctrl", "alt"])
        vk = config.get("hotkey.vk", "Space")
        hotkey_text = " + ".join([m.capitalize() for m in mods] + [vk])
        hotkey_field = QLineEdit(hotkey_text, self)
        hotkey_field.setReadOnly(True)
        form.addRow("Hotkey:", hotkey_field)

        # --- voice dropdown ---
        self._voice_combo = QComboBox(self)
        current_voice = config.voice_id
        selected_index = 0
        for index, (name, voice_id) in enumerate(_fetch_voices()):
            self._voice_combo.addItem(name, voice_id)
            if voice_id == current_voice:
                selected_index = index
        self._voice_combo.setCurrentIndex(selected_index)
        form.addRow("ElevenLabs voice:", self._voice_combo)

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
        self.accept()
