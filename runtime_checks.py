"""Runtime environment checks for Windows-specific failure modes."""
from __future__ import annotations

import ctypes
import sys


def is_running_elevated() -> bool:
    """Return True when the process is running with administrator elevation."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
