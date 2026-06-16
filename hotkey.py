"""Push-to-talk global hotkey via a low-level keyboard hook.

Instead of ``RegisterHotKey`` (which only fires on key *down*), we install a
``WH_KEYBOARD_LL`` hook so we can track the trigger key going down *and* up.
That gives us true push-to-talk: pressing the combo starts recording (Wispr is
bound to the same keys), releasing it stops and triggers a turn.

The hook procedure runs on the GUI thread (the thread that installed it and
which pumps Win32 messages via Qt's event loop), so it emits the
:attr:`pressed` / :attr:`released` signals directly. Connect them with
``Qt.QueuedConnection`` in the caller so heavy work never runs inside the hook
callback — a slow hook proc would stall keyboard input system-wide.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import TypeAlias

from PySide6.QtCore import QObject, Signal

WH_KEYBOARD_LL = 13

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# Virtual-key codes for the modifiers we check via GetAsyncKeyState. These are
# the "either side" codes, so left or right Ctrl/Shift/Alt both count.
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LMENU = 0xA4
VK_RMENU = 0xA5

VkGroup: TypeAlias = int | tuple[int, ...]

_MOD_VKS: dict[str, tuple[int, ...]] = {
    "alt": (VK_MENU, VK_LMENU, VK_RMENU),
    "ctrl": (VK_CONTROL, VK_LCONTROL, VK_RCONTROL),
    "control": (VK_CONTROL, VK_LCONTROL, VK_RCONTROL),
    "shift": (VK_SHIFT, VK_LSHIFT, VK_RSHIFT),
    "win": (VK_LWIN, VK_RWIN),
    "windows": (VK_LWIN, VK_RWIN),
}

# Friendly key name -> virtual-key code for the trigger key. Extend as needed.
_VK_MAP: dict[str, VkGroup] = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "win": (VK_LWIN, VK_RWIN),
    "windows": (VK_LWIN, VK_RWIN),
}


def _as_vk_tuple(vks: int | tuple[int, ...]) -> tuple[int, ...]:
    return vks if isinstance(vks, tuple) else (vks,)


def _resolve_trigger_vks(vk: str) -> tuple[int, ...]:
    key = (vk or "").strip()
    lowered = key.lower()
    if lowered in _VK_MAP:
        return _as_vk_tuple(_VK_MAP[lowered])
    if len(key) == 1:
        return (ord(key.upper()),)
    raise ValueError(f"Unknown hotkey virtual key: {vk!r}")


def _resolve_mod_vks(mods: list[str]) -> list[tuple[int, ...]]:
    out: list[tuple[int, ...]] = []
    for mod in mods:
        vks = _MOD_VKS.get(mod.lower())
        if vks is None:
            raise ValueError(f"Unknown hotkey modifier: {mod!r}")
        out.append(vks)
    return out


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


# LRESULT CALLBACK LowLevelKeyboardProc(int, WPARAM, LPARAM)
#
# ctypes.wintypes does not expose LRESULT on all Python builds. LPARAM is
# pointer-sized on Windows, which matches LRESULT and keeps the hook safe on
# 64-bit interpreters.
_LRESULT = getattr(wintypes, "LRESULT", wintypes.LPARAM)
_CALLBACK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HOOKPROC = _CALLBACK_FACTORY(
    _LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


class HotkeyManager(QObject):
    """Push-to-talk hotkey driven by a low-level keyboard hook.

    Emits :attr:`pressed` when the full combo first goes down and
    :attr:`released` when the trigger key is let go again.
    """

    pressed = Signal()
    released = Signal()

    def __init__(self, mods: list[str], vk: str) -> None:
        QObject.__init__(self)
        # Flatten modifier vk groups; each entry is an int or a tuple of ints
        # where any one being down satisfies that modifier (e.g. left/right Win).
        self._mod_vks = _resolve_mod_vks(mods)
        self._trigger_vks = _resolve_trigger_vks(vk)

        self._active = False   # combo currently held down
        self._paused = False
        self._registered = False

        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        # Declare argtypes too: without them ctypes assumes 32-bit c_int args,
        # which overflows on 64-bit handles/pointers (hmod, the LPARAM struct
        # address, the HHOOK) and raises "int too long to convert".
        self._user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self._user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _HOOKPROC, wintypes.HMODULE, wintypes.DWORD
        ]
        self._user32.CallNextHookEx.restype = _LRESULT
        self._user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        ]
        self._user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        # Keep a strong reference to the callback for the hook's lifetime.
        self._proc = _HOOKPROC(self._on_event)
        self._hook = None

    # --- lifecycle ----------------------------------------------------------

    def register(self) -> bool:
        if self._registered:
            return True
        hmod = self._kernel32.GetModuleHandleW(None)
        self._hook = self._user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, hmod, 0
        )
        self._registered = bool(self._hook)
        return self._registered

    def unregister(self) -> None:
        if self._registered and self._hook:
            self._user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._registered = False
        self._active = False

    def set_paused(self, paused: bool) -> None:
        """Pause/resume by tearing the hook down / reinstalling it."""
        self._paused = paused
        if paused:
            self.unregister()
        else:
            self.register()

    @property
    def paused(self) -> bool:
        return self._paused

    # --- hook callback ------------------------------------------------------

    def _mods_held(self) -> bool:
        for entry in self._mod_vks:
            vks = entry if isinstance(entry, tuple) else (entry,)
            if not any(self._user32.GetAsyncKeyState(v) & 0x8000 for v in vks):
                return False
        return True

    def _on_event(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0 and not self._paused:
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if data.vkCode in self._trigger_vks:
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    # Trigger went down; start only if modifiers are held and we
                    # aren't already active (ignores auto-repeat).
                    if not self._active and self._mods_held():
                        self._active = True
                        self.pressed.emit()
                elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                    if self._active:
                        self._active = False
                        self.released.emit()
        return self._user32.CallNextHookEx(None, n_code, w_param, l_param)
