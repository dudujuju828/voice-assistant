"""Config persistence for Voice Assistant.

Stores settings in %APPDATA%\\VoiceAssistant\\config.json. Secrets (the
ElevenLabs API key) live in .env, never here.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --- defaults ---------------------------------------------------------------

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_EFFORT = "default"
DEFAULT_HOTKEY_MODS = ["ctrl"]
DEFAULT_HOTKEY_VK = "Win"
CAPTURE_METHODS = {"clipboard", "hidden_input", "visible_input"}
CLAUDE_EFFORT_LEVELS = {"default", "low", "medium", "high", "xhigh", "max"}
DEFAULT_CAPTURE_DELAY_MS = 500
MAX_CAPTURE_DELAY_MS = 10_000
DEFAULT_TTS_STABILITY = 0.5
DEFAULT_TTS_SIMILARITY_BOOST = 0.75
DEFAULT_TTS_SPEED = 1.0
MIN_TTS_SPEED = 0.7
MAX_TTS_SPEED = 1.2
LEGACY_DEFAULT_HOTKEYS = [
    (["ctrl", "shift"], "Space"),
]


def _default_config() -> dict[str, Any]:
    return {
        # Resolved lazily on first run to the primary monitor device name.
        "capture_monitor_device": None,
        "hotkey": {
            "mods": list(DEFAULT_HOTKEY_MODS),
            "vk": DEFAULT_HOTKEY_VK,
            "semantics": "push_to_talk",
        },
        # How the transcribed text reaches the app after the hotkey is released.
        #   "clipboard"    — Wispr copies the transcription; we read the clipboard.
        #   "hidden_input" — Wispr types into an invisible focused box.
        # delay_ms gives Wispr a moment to finish writing before we read.
        "capture": {"method": "visible_input", "delay_ms": DEFAULT_CAPTURE_DELAY_MS},
        "elevenlabs": {
            "voice_id": DEFAULT_VOICE_ID,
            "model_id": DEFAULT_TTS_MODEL,
            "stability": DEFAULT_TTS_STABILITY,
            "similarity_boost": DEFAULT_TTS_SIMILARITY_BOOST,
            "speed": DEFAULT_TTS_SPEED,
        },
        "claude": {
            "session_id": None,
            "model": DEFAULT_CLAUDE_MODEL,
            "effort": DEFAULT_CLAUDE_EFFORT,
        },
    }


def _config_dir() -> Path:
    base = os.getenv("APPDATA")
    if not base:
        # Non-Windows fallback (dev machines) so the module is importable.
        base = os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "VoiceAssistant"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _backup_corrupt_config(path: Path) -> None:
    """Preserve a bad config before replacing it with defaults."""
    try:
        backup = path.with_suffix(".json.corrupt")
        shutil.copy2(path, backup)
    except OSError:
        pass


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge stored values onto defaults so new keys appear automatically."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _same_hotkey(mods: Any, vk: Any, expected_mods: list[str], expected_vk: str) -> bool:
    if not isinstance(mods, list) or not isinstance(vk, str):
        return False
    normalized_mods = [str(mod).lower() for mod in mods]
    return normalized_mods == expected_mods and vk.lower() == expected_vk.lower()


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _non_empty_str(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    return value.strip() or default


class Config:
    """Thin wrapper over the JSON config file with dotted-key access."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = _default_config()
        self.load()

    # --- persistence --------------------------------------------------------

    def load(self) -> None:
        with self._lock:
            path = _config_path()
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    stored = json.load(fh)
                if not isinstance(stored, dict):
                    raise ValueError("Config root must be an object.")
                self._data = _deep_merge(_default_config(), stored)
                if self._migrate():
                    self.save()
            except FileNotFoundError:
                # First run: start from defaults and persist them.
                self._data = _default_config()
                self.save()
            except (json.JSONDecodeError, ValueError):
                # Corrupt file: keep a copy, then replace with known-good defaults.
                _backup_corrupt_config(path)
                self._data = _default_config()
                self.save()
            except OSError as exc:
                # If the config cannot be read due to permissions/locking, run with
                # defaults in memory and avoid overwriting a file we could not read.
                logger.warning("Could not read config at %s: %s", path, exc)
                self._data = _default_config()

    def save(self) -> None:
        with self._lock:
            path = _config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, path)

    def _migrate(self) -> bool:
        """Apply safe config migrations for historical defaults."""
        mods = self.get("hotkey.mods")
        vk = self.get("hotkey.vk")
        for legacy_mods, legacy_vk in LEGACY_DEFAULT_HOTKEYS:
            if _same_hotkey(mods, vk, legacy_mods, legacy_vk):
                hotkey = self._data.setdefault("hotkey", {})
                if isinstance(hotkey, dict):
                    hotkey["mods"] = list(DEFAULT_HOTKEY_MODS)
                    hotkey["vk"] = DEFAULT_HOTKEY_VK
                    return True
        return False

    # --- generic access -----------------------------------------------------

    def get(self, dotted_key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in dotted_key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, dotted_key: str, value: Any) -> None:
        with self._lock:
            self._set_in_memory(dotted_key, value)
            self.save()

    def set_many(self, values: dict[str, Any]) -> None:
        with self._lock:
            for dotted_key, value in values.items():
                self._set_in_memory(dotted_key, value)
            self.save()

    def _set_in_memory(self, dotted_key: str, value: Any) -> None:
        parts = dotted_key.split(".")
        node = self._data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value

    # --- typed convenience accessors ---------------------------------------

    @property
    def capture_monitor_device(self) -> str | None:
        return self.get("capture_monitor_device")

    @capture_monitor_device.setter
    def capture_monitor_device(self, device: str | None) -> None:
        self.set("capture_monitor_device", device)

    @property
    def voice_id(self) -> str:
        return _non_empty_str(self.get("elevenlabs.voice_id"), DEFAULT_VOICE_ID)

    @voice_id.setter
    def voice_id(self, value: str) -> None:
        self.set("elevenlabs.voice_id", value)

    @property
    def tts_model(self) -> str:
        return _non_empty_str(self.get("elevenlabs.model_id"), DEFAULT_TTS_MODEL)

    @tts_model.setter
    def tts_model(self, value: str) -> None:
        value = (value or "").strip() or DEFAULT_TTS_MODEL
        self.set("elevenlabs.model_id", value)

    @property
    def tts_stability(self) -> float:
        return _bounded_float(
            self.get("elevenlabs.stability"),
            DEFAULT_TTS_STABILITY,
            0.0,
            1.0,
        )

    @tts_stability.setter
    def tts_stability(self, value: float) -> None:
        self.set(
            "elevenlabs.stability",
            _bounded_float(value, DEFAULT_TTS_STABILITY, 0.0, 1.0),
        )

    @property
    def tts_similarity_boost(self) -> float:
        return _bounded_float(
            self.get("elevenlabs.similarity_boost"),
            DEFAULT_TTS_SIMILARITY_BOOST,
            0.0,
            1.0,
        )

    @tts_similarity_boost.setter
    def tts_similarity_boost(self, value: float) -> None:
        self.set(
            "elevenlabs.similarity_boost",
            _bounded_float(value, DEFAULT_TTS_SIMILARITY_BOOST, 0.0, 1.0),
        )

    @property
    def tts_speed(self) -> float:
        return _bounded_float(
            self.get("elevenlabs.speed"),
            DEFAULT_TTS_SPEED,
            MIN_TTS_SPEED,
            MAX_TTS_SPEED,
        )

    @tts_speed.setter
    def tts_speed(self, value: float) -> None:
        self.set(
            "elevenlabs.speed",
            _bounded_float(value, DEFAULT_TTS_SPEED, MIN_TTS_SPEED, MAX_TTS_SPEED),
        )

    @property
    def claude_model(self) -> str:
        return _non_empty_str(self.get("claude.model"), DEFAULT_CLAUDE_MODEL)

    @claude_model.setter
    def claude_model(self, value: str) -> None:
        value = (value or "").strip() or DEFAULT_CLAUDE_MODEL
        self.set("claude.model", value)

    @property
    def claude_effort(self) -> str:
        effort = str(self.get("claude.effort", DEFAULT_CLAUDE_EFFORT) or "").lower()
        return effort if effort in CLAUDE_EFFORT_LEVELS else DEFAULT_CLAUDE_EFFORT

    @claude_effort.setter
    def claude_effort(self, value: str) -> None:
        effort = (value or DEFAULT_CLAUDE_EFFORT).strip().lower()
        if effort not in CLAUDE_EFFORT_LEVELS:
            raise ValueError(f"Unknown Claude effort level: {value!r}")
        self.set("claude.effort", effort)

    @property
    def capture_method(self) -> str:
        """Configured transcript capture source."""
        method = self.get("capture.method", "clipboard")
        return method if method in CAPTURE_METHODS else "clipboard"

    @capture_method.setter
    def capture_method(self, value: str) -> None:
        if value not in CAPTURE_METHODS:
            raise ValueError(f"Unknown capture method: {value!r}")
        self.set("capture.method", value)

    @property
    def capture_delay_ms(self) -> int:
        """How long to wait after hotkey release for Wispr to finish writing."""
        try:
            value = int(self.get("capture.delay_ms", DEFAULT_CAPTURE_DELAY_MS))
        except (TypeError, ValueError):
            return DEFAULT_CAPTURE_DELAY_MS
        return max(0, min(value, MAX_CAPTURE_DELAY_MS))

    @capture_delay_ms.setter
    def capture_delay_ms(self, value: int) -> None:
        try:
            delay = int(value)
        except (TypeError, ValueError):
            delay = DEFAULT_CAPTURE_DELAY_MS
        self.set("capture.delay_ms", max(0, min(delay, MAX_CAPTURE_DELAY_MS)))

    @property
    def session_id(self) -> str | None:
        value = self.get("claude.session_id")
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        return value.strip() or None

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
