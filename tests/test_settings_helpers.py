from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from ui.settings import _combo_value, _set_combo_value  # noqa: E402


class SettingsHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_combo_value_returns_custom_editable_text(self) -> None:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem("Rachel", "voice-id")

        combo.setCurrentText("custom-voice-id")

        self.assertEqual(_combo_value(combo), "custom-voice-id")

    def test_combo_value_returns_selected_item_data(self) -> None:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem("Rachel", "voice-id")

        self.assertEqual(_combo_value(combo), "voice-id")

    def test_set_combo_value_preserves_unknown_editable_value(self) -> None:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem("Rachel", "voice-id")

        _set_combo_value(combo, "custom-model")

        self.assertEqual(combo.currentText(), "custom-model")


if __name__ == "__main__":
    unittest.main()
