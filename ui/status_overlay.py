"""Minimal status indicator — a tiny coloured dot, bottom-right.

The whole point is to be nearly invisible: no text, no emojis, no panel. Just a
small low-opacity dot in the corner that changes colour with state, and is
completely gone when idle. It never steals focus and is click-through, so it
can't interfere with the user's work.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

import monitors

try:
    import win32gui
except ImportError:  # pragma: no cover - non-Windows dev import
    win32gui = None  # type: ignore

WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
GWL_EXSTYLE = -20

# Tiny footprint, tucked into the very corner, barely there.
MARGIN = 16
SIZE = 40         # widget box
DOT = 12          # the visible dot inside it
OPACITY = 0.55

# Per-state dot colours (R, G, B).
_COLORS = {
    "recording": (235, 70, 70),    # red — capturing your voice
    "processing": (235, 180, 60),  # amber — thinking
    "speaking": (80, 200, 120),    # green — talking back
    "error": (200, 60, 60),        # deep red — something went wrong
}


class StatusOverlay(QWidget):
    def __init__(self, config) -> None:
        super().__init__()
        self._config = config
        self._color = _COLORS["recording"]

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowOpacity(OPACITY)
        self.resize(SIZE, SIZE)

    # --- public status API --------------------------------------------------

    def show_recording(self) -> None:
        self._show_dot("recording")

    def show_processing(self) -> None:
        self._show_dot("processing")

    def show_speaking(self) -> None:
        self._show_dot("speaking")

    def show_error(self, _message: str = "") -> None:
        self._show_dot("error")

    # --- painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt signature)
        from PySide6.QtGui import QBrush, QColor, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(*self._color)))
        offset = (SIZE - DOT) // 2
        painter.drawEllipse(offset, offset, DOT, DOT)
        painter.end()

    # --- internals ----------------------------------------------------------

    def _show_dot(self, state: str) -> None:
        self._color = _COLORS.get(state, _COLORS["recording"])
        self._reposition()
        if not self.isVisible():
            self.show()
            self._apply_no_activate()
        self.raise_()
        self.update()

    def _reposition(self) -> None:
        rect = self._resolve_rect()
        if rect is None:
            return
        left, top, width, height = rect
        x = left + width - SIZE - MARGIN
        y = top + height - SIZE - MARGIN
        self.move(x, y)

    def _resolve_rect(self) -> Optional[tuple[int, int, int, int]]:
        try:
            rect = monitors.get_monitor_rect(self._config.capture_monitor_device)
        except Exception:
            rect = None
        if rect is not None:
            return rect

        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        geometry = screen.availableGeometry()
        return (
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        )

    def _apply_no_activate(self) -> None:
        """Make the overlay non-activating + click-through at the Win32 level."""
        if win32gui is None:
            return
        try:
            hwnd = int(self.winId())
            ex_style = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
            ex_style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT
            win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ex_style)
        except Exception:
            pass
