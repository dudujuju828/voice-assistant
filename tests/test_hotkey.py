from __future__ import annotations

import ctypes
import unittest
from ctypes import wintypes

from hotkey import (
    VK_CONTROL,
    VK_LCONTROL,
    VK_LWIN,
    VK_RCONTROL,
    VK_RWIN,
    _HOOKPROC,
    _LRESULT,
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

    def test_hook_callback_uses_pointer_sized_result(self) -> None:
        expected_lresult = getattr(wintypes, "LRESULT", wintypes.LPARAM)

        self.assertIs(_LRESULT, expected_lresult)
        self.assertEqual(ctypes.sizeof(_LRESULT), ctypes.sizeof(wintypes.LPARAM))
        self.assertIs(_HOOKPROC._restype_, _LRESULT)


if __name__ == "__main__":
    unittest.main()
