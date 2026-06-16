"""Single-instance guard for the tray application."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\VoiceAssistantTrayApp"


class SingleInstance:
    """Hold a Windows mutex for the process lifetime."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._handle = None
        self.already_running = False
        if sys.platform != "win32":
            return

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        self._kernel32 = kernel32
        self._handle = kernel32.CreateMutexW(None, True, name)
        self.already_running = bool(
            self._handle and kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        )

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
