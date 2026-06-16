from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import capture


class FakeMss:
    def __enter__(self) -> "FakeMss":
        return self

    def __exit__(self, *args) -> None:
        return None

    def grab(self, _region):
        return SimpleNamespace(rgb=b"rgb", size=(1, 1))


class CaptureTests(unittest.TestCase):
    def test_rejects_invalid_monitor_bounds(self) -> None:
        with patch("capture.monitors.get_monitor_rect", return_value=(0, 0, 0, 100)):
            with self.assertRaises(RuntimeError):
                capture.capture_monitor("display")

    def test_capture_replaces_screenshot_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screenshot.png"

            def write_png(_rgb, _size, output):
                Path(output).write_bytes(b"new")

            with (
                patch("capture.monitors.get_monitor_rect", return_value=(0, 0, 1, 1)),
                patch("capture.screenshot_path", return_value=str(path)),
                patch("capture.mss.mss", return_value=FakeMss()),
                patch("capture.mss.tools.to_png", side_effect=write_png),
            ):
                result = capture.capture_monitor("display")

            self.assertEqual(result, str(path))
            self.assertEqual(path.read_bytes(), b"new")
            self.assertFalse(path.with_suffix(".png.tmp").exists())


if __name__ == "__main__":
    unittest.main()
