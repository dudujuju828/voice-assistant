"""Config persistence for Voice Assistant.

Stores settings in %APPDATA%\\VoiceAssistant\\config.json. Secrets (the
ElevenLabs API key) live in .env, never here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# --- defaults ---------------------------------------------------------------

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
DEFAULT_CLAUDE_MODEL = "opus"


def _default_config() -> dict[str, Any]:
    return {
        # Resolved lazily on first run to the primary monitor device name.
        "capture_monitor_device": None,
        "hotkey": {
            "mods": ["ctrl", "shift"],
            "vk": "Space",
            "semantics": "push_to_talk",
        },
        # How the transcribed text reaches the app after the hotkey is released.
        #   "clipboard"    — Wispr copies the transcription; we read the clipboard.
        #   "hidden_input" — Wispr types into an invisible focused box.
        # delay_ms gives Wispr a moment to finish writing before we read.
        "capture": {"method": "clipboard", "delay_ms": 500},
        "elevenlabs": {
            "voice_id": DEFAULT_VOICE_ID,
            "model_id": DEFAULT_TTS_MODEL,
        },
        "claude": {"session_id": None, "model": DEFAULT_CLAUDE_MODEL},
    }


def _config_dir() -> Path:
    base = os.getenv("APPDATA")
    if not base:
        # Non-Windows fallback (dev machines) so the module is importable.
        base = os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "VoiceAssistant"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge stored values onto defaults so new keys appear automatically."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """Thin wrapper over the JSON config file with dotted-key access."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = _default_config()
        self.load()

    # --- persistence --------------------------------------------------------

    def load(self) -> None:
        path = _config_path()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            self._data = _deep_merge(_default_config(), stored)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            # First run or corrupt file: start from defaults and persist them.
            self._data = _default_config()
            self.save()

    def save(self) -> None:
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, path)

    # --- generic access -----------------------------------------------------

    def get(self, dotted_key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted_key: str, value: Any) -> None:
        parts = dotted_key.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        self.save()

    # --- typed convenience accessors ---------------------------------------

    @property
    def capture_monitor_device(self) -> str | None:
        return self.get("capture_monitor_device")

    @capture_monitor_device.setter
    def capture_monitor_device(self, device: str | None) -> None:
        self.set("capture_monitor_device", device)

    @property
    def voice_id(self) -> str:
        return self.get("elevenlabs.voice_id", DEFAULT_VOICE_ID)

    @voice_id.setter
    def voice_id(self, value: str) -> None:
        self.set("elevenlabs.voice_id", value)

    @property
    def tts_model(self) -> str:
        return self.get("elevenlabs.model_id", DEFAULT_TTS_MODEL)

    @property
    def claude_model(self) -> str:
        return self.get("claude.model", DEFAULT_CLAUDE_MODEL)

    @property
    def capture_method(self) -> str:
        """Either "clipboard" (default) or "hidden_input"."""
        return self.get("capture.method", "clipboard")

    @property
    def capture_delay_ms(self) -> int:
        """How long to wait after hotkey release for Wispr to finish writing."""
        return int(self.get("capture.delay_ms", 500))

    @property
    def session_id(self) -> str | None:
        return self.get("claude.session_id")

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self.set("claude.session_id", value)

    def ensure_capture_monitor(self) -> str | None:
        """On first run, resolve the capture monitor to the primary display."""
        if self.capture_monitor_device:
            return self.capture_monitor_device
        try:
            import monitors

            primary = monitors.get_primary_monitor()
        except Exception:
            primary = None
        if primary:
            self.capture_monitor_device = primary
        return primary
