from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.status_overlay import StatusOverlay  # noqa: E402


class StatusOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_resolve_rect_falls_back_to_primary_screen(self) -> None:
        overlay = StatusOverlay(SimpleNamespace(capture_monitor_device="missing"))
        screen = QApplication.primaryScreen()
        self.assertIsNotNone(screen)
        geometry = screen.availableGeometry()

        with patch("ui.status_overlay.monitors.get_monitor_rect", return_value=None):
            self.assertEqual(
                overlay._resolve_rect(),
                (
                    geometry.x(),
                    geometry.y(),
                    geometry.width(),
                    geometry.height(),
                ),
            )


if __name__ == "__main__":
    unittest.main()
