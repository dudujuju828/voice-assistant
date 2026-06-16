from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import DEFAULT_CAPTURE_DELAY_MS, MAX_CAPTURE_DELAY_MS, Config


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


if __name__ == "__main__":
    unittest.main()
