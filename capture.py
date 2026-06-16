"""Screenshot capture of a chosen monitor via mss."""
from __future__ import annotations

import os
import tempfile
from typing import Optional

import mss
import mss.tools

import monitors

SCREENSHOT_FILENAME = "voice-assistant-screenshot.png"


def screenshot_path() -> str:
    return os.path.join(tempfile.gettempdir(), SCREENSHOT_FILENAME)


def capture_monitor(device_name: Optional[str]) -> str:
    """Capture the given monitor to a PNG and return its absolute path.

    Resolves the device name to physical-pixel bounds, grabs with mss, and
    writes to %TEMP%. Raises RuntimeError if no monitor can be resolved.
    """
    rect = monitors.get_monitor_rect(device_name)
    if rect is None:
        raise RuntimeError("No monitor available to capture.")

    left, top, width, height = rect
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid monitor bounds: {rect!r}")
    region = {"left": left, "top": top, "width": width, "height": height}

    path = screenshot_path()
    tmp_path = f"{path}.tmp"
    try:
        with mss.mss() as sct:
            shot = sct.grab(region)
            mss.tools.to_png(shot.rgb, shot.size, output=tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return path
