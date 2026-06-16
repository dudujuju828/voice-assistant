"""Global hotkey via RegisterHotKey + a Qt native event filter.

Registers a thread-level hotkey (hWnd = NULL) so WM_HOTKEY lands in the Qt
GUI thread's message queue, where a QAbstractNativeEventFilter picks it up.
The process receiving WM_HOTKEY is granted foreground rights, which is what
lets the input window legitimately call SetForegroundWindow.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

WM_HOTKEY = 0x0312
HOTKEY_ID = 1

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MOD_MAP = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}

# Friendly key name -> virtual-key code. Extend as needed.
_VK_MAP = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
}


def _resolve_vk(vk: str) -> int:
    key = (vk or "").strip()
    lowered = key.lower()
    if lowered in _VK_MAP:
        return _VK_MAP[lowered]
    if len(key) == 1:
        return ord(key.upper())
    raise ValueError(f"Unknown hotkey virtual key: {vk!r}")


def _resolve_mods(mods: list[str]) -> int:
    value = MOD_NOREPEAT
    for mod in mods:
        flag = _MOD_MAP.get(mod.lower())
        if flag is None:
            raise ValueError(f"Unknown hotkey modifier: {mod!r}")
        value |= flag
    return value


class HotkeyManager(QObject, QAbstractNativeEventFilter):
    """Registers a global hotkey and emits :attr:`activated` when pressed."""

    activated = Signal()

    def __init__(self, mods: list[str], vk: str) -> None:
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self._mods = _resolve_mods(mods)
        self._vk = _resolve_vk(vk)
        self._registered = False
        self._paused = False
        self._user32 = ctypes.windll.user32

    # --- lifecycle ----------------------------------------------------------

    def register(self) -> bool:
        if self._registered:
            return True
        ok = bool(
            self._user32.RegisterHotKey(None, HOTKEY_ID, self._mods, self._vk)
        )
        self._registered = ok
        return ok

    def unregister(self) -> None:
        if self._registered:
            self._user32.UnregisterHotKey(None, HOTKEY_ID)
            self._registered = False

    def set_paused(self, paused: bool) -> None:
        """Pause/resume by unregistering/re-registering the hotkey."""
        self._paused = paused
        if paused:
            self.unregister()
        else:
            self.register()

    @property
    def paused(self) -> bool:
        return self._paused

    # --- native event filter ------------------------------------------------

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt signature)
        if event_type == "windows_generic_MSG":
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.activated.emit()
        return False, 0
