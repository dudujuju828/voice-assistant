"""Monitor enumeration and device-name resolution.

All rects are in virtual-screen *physical* pixels, which lines up 1:1 with
mss capture because the process is per-monitor-V2 DPI aware (see main.py).
"""
from __future__ import annotations

from typing import Optional, TypedDict

try:
    import win32api
    import win32con
except ImportError:  # pragma: no cover - non-Windows dev import
    win32api = None  # type: ignore
    win32con = None  # type: ignore


class MonitorInfo(TypedDict):
    device: str
    name: str
    rect: tuple[int, int, int, int]  # (left, top, width, height)
    is_primary: bool


MONITORINFOF_PRIMARY = 1


def _friendly_name(device: str) -> str:
    """Resolve '\\\\.\\DISPLAY1' to a human-readable monitor name."""
    if win32api is None:
        return device
    try:
        info = win32api.EnumDisplayDevices(device, 0)
        name = getattr(info, "DeviceString", "") or ""
        if name.strip():
            return name.strip()
    except Exception:
        pass
    return device


def list_monitors() -> list[MonitorInfo]:
    """Return every connected display with bounds, friendly name, primary flag."""
    if win32api is None:
        return []

    monitors: list[MonitorInfo] = []
    for hmon, _hdc, _rect in win32api.EnumDisplayMonitors():
        try:
            info = win32api.GetMonitorInfo(hmon)
        except Exception:
            continue
        left, top, right, bottom = info["Monitor"]
        device = info.get("Device", "")
        is_primary = bool(info.get("Flags", 0) & MONITORINFOF_PRIMARY)
        monitors.append(
            MonitorInfo(
                device=device,
                name=_friendly_name(device),
                rect=(left, top, right - left, bottom - top),
                is_primary=is_primary,
            )
        )
    return monitors


def get_primary_monitor() -> Optional[str]:
    """Device name of the primary display, or the first one if none flagged."""
    monitors = list_monitors()
    for mon in monitors:
        if mon["is_primary"]:
            return mon["device"]
    return monitors[0]["device"] if monitors else None


def get_monitor_rect(device_name: Optional[str]) -> Optional[tuple[int, int, int, int]]:
    """Resolve a stored device name to (left, top, width, height).

    Falls back to the primary monitor if the saved device is unplugged/missing.
    """
    monitors = list_monitors()
    if not monitors:
        return None

    if device_name:
        for mon in monitors:
            if mon["device"] == device_name:
                return mon["rect"]

    # Saved device gone — fall back to primary.
    for mon in monitors:
        if mon["is_primary"]:
            return mon["rect"]
    return monitors[0]["rect"]
