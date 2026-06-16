from __future__ import annotations

import unittest

from hotkey import (
    VK_CONTROL,
    VK_LCONTROL,
    VK_LWIN,
    VK_RCONTROL,
    VK_RWIN,
    _resolve_mod_vks,
    _resolve_trigger_vks,
)


class HotkeyResolverTests(unittest.TestCase):
    def test_win_trigger_accepts_left_or_right_windows_key(self) -> None:
        self.assertEqual(_resolve_trigger_vks("Win"), (VK_LWIN, VK_RWIN))
        self.assertEqual(_resolve_trigger_vks("windows"), (VK_LWIN, VK_RWIN))

    def test_ctrl_modifier_accepts_generic_left_or_right_control(self) -> None:
        self.assertEqual(
            _resolve_mod_vks(["ctrl"]),
            [(VK_CONTROL, VK_LCONTROL, VK_RCONTROL)],
        )


if __name__ == "__main__":
    unittest.main()
