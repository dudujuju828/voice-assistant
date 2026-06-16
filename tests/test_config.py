from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from config import (
    DEFAULT_CAPTURE_DELAY_MS,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_STABILITY,
    DEFAULT_VOICE_ID,
    MAX_CAPTURE_DELAY_MS,
    MAX_TTS_SPEED,
    MIN_TTS_SPEED,
    Config,
)


class ConfigTests(unittest.TestCase):
    def _load_with_appdata(self, appdata: Path) -> Config:
        with patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
            return Config()

    def test_loads_utf8_sig_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "VoiceAssistant"
            config_dir.mkdir()
            path = config_dir / "config.json"
            path.write_text(
                json.dumps({"capture": {"method": "visible_input"}}),
                encoding="utf-8-sig",
            )

            config = self._load_with_appdata(Path(tmp))

            self.assertEqual(config.capture_method, "visible_input")

    def test_corrupt_config_is_backed_up_and_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "VoiceAssistant"
            config_dir.mkdir()
            path = config_dir / "config.json"
            path.write_text("{not json", encoding="utf-8")

            config = self._load_with_appdata(Path(tmp))

            self.assertEqual(config.capture_method, "clipboard")
            self.assertTrue(path.with_suffix(".json.corrupt").exists())
            self.assertEqual(json.loads(path.read_text())["capture"]["method"], "clipboard")

    def test_capture_delay_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load_with_appdata(Path(tmp))

            config.set("capture.delay_ms", "not a number")
            self.assertEqual(config.capture_delay_ms, DEFAULT_CAPTURE_DELAY_MS)

            config.set("capture.delay_ms", MAX_CAPTURE_DELAY_MS + 1)
            self.assertEqual(config.capture_delay_ms, MAX_CAPTURE_DELAY_MS)

            config.set("capture.delay_ms", -1)
            self.assertEqual(config.capture_delay_ms, 0)

            config.capture_delay_ms = MAX_CAPTURE_DELAY_MS + 1
            self.assertEqual(config.capture_delay_ms, MAX_CAPTURE_DELAY_MS)

    def test_tts_settings_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load_with_appdata(Path(tmp))

            config.set("elevenlabs.stability", "bad")
            self.assertEqual(config.tts_stability, DEFAULT_TTS_STABILITY)

            config.set("elevenlabs.similarity_boost", 2)
            self.assertEqual(config.tts_similarity_boost, 1.0)

            config.set("elevenlabs.speed", 99)
            self.assertEqual(config.tts_speed, MAX_TTS_SPEED)

            config.set("elevenlabs.speed", -1)
            self.assertEqual(config.tts_speed, MIN_TTS_SPEED)

    def test_claude_effort_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load_with_appdata(Path(tmp))

            config.claude_effort = "high"
            self.assertEqual(config.claude_effort, "high")

            config.set("claude.effort", "not-real")
            self.assertEqual(config.claude_effort, "default")

            with self.assertRaises(ValueError):
                config.claude_effort = "not-real"

    def test_set_many_updates_nested_values_with_one_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load_with_appdata(Path(tmp))

            config.set_many(
                {
                    "claude.model": "sonnet",
                    "claude.effort": "low",
                    "elevenlabs.speed": 1.1,
                }
            )

            self.assertEqual(config.claude_model, "sonnet")
            self.assertEqual(config.claude_effort, "low")
            self.assertEqual(config.tts_speed, 1.1)

    def test_concurrent_writes_leave_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load_with_appdata(Path(tmp))
            path = Path(tmp) / "VoiceAssistant" / "config.json"

            def write_value(index: int) -> None:
                for _ in range(5):
                    config.set_many(
                        {
                            "claude.model": f"model-{index}",
                            "elevenlabs.speed": 1.0,
                        }
                    )

            threads = [
                threading.Thread(target=write_value, args=(i,))
                for i in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            payload = json.loads(path.read_text())
            self.assertIn("claude", payload)
            self.assertIn("elevenlabs", payload)

    def test_string_settings_fall_back_on_wrong_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load_with_appdata(Path(tmp))

            config.set_many(
                {
                    "elevenlabs.voice_id": 123,
                    "elevenlabs.model_id": "",
                    "claude.model": 456,
                    "claude.session_id": 789,
                }
            )

            self.assertEqual(config.voice_id, DEFAULT_VOICE_ID)
            self.assertEqual(config.tts_model, DEFAULT_TTS_MODEL)
            self.assertEqual(config.claude_model, DEFAULT_CLAUDE_MODEL)
            self.assertIsNone(config.session_id)

    def test_legacy_default_hotkey_migrates_to_ctrl_win(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "VoiceAssistant"
            config_dir.mkdir()
            path = config_dir / "config.json"
            path.write_text(
                json.dumps({"hotkey": {"mods": ["ctrl", "shift"], "vk": "Space"}}),
                encoding="utf-8",
            )

            config = self._load_with_appdata(Path(tmp))

            self.assertEqual(config.get("hotkey.mods"), ["ctrl"])
            self.assertEqual(config.get("hotkey.vk"), "Win")
            persisted = json.loads(path.read_text())
            self.assertEqual(persisted["hotkey"]["mods"], ["ctrl"])
            self.assertEqual(persisted["hotkey"]["vk"], "Win")


if __name__ == "__main__":
    unittest.main()
