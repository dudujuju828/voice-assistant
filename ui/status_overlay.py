"""Non-intrusive status overlay (Listening / Thinking / Speaking).

Frameless, always-on-top, click-through, and — critically — never steals
focus. The no-activate / transparent ex-styles are applied via Win32 after
``show()`` because Qt does not expose WS_EX_NOACTIVATE directly.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QLabel, QWidget

import monitors

try:
    import win32con
    import win32gui
except ImportError:  # pragma: no cover - non-Windows dev import
    win32con = None  # type: ignore
    win32gui = None  # type: ignore

WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
GWL_EXSTYLE = -20

MARGIN = 32
WIDTH = 240
HEIGHT = 64


class StatusOverlay(QWidget):
    def __init__(self, config) -> None:
        super().__init__()
        self._config = config

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(WIDTH, HEIGHT)

        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setGeometry(0, 0, WIDTH, HEIGHT)
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        self._label.setFont(font)
        self._label.setStyleSheet(
            "color: white;"
            "background-color: rgba(20, 20, 24, 200);"
            "border-radius: 12px;"
        )
        # Silence unused-import style checkers for QColor (kept for theming).
        _ = QColor

    # --- public status API --------------------------------------------------

    def show_listening(self) -> None:
        self._show_with_text("🎤  Listening…")

    def show_thinking(self) -> None:
        self._show_with_text("🤔  Thinking…")

    def show_speaking(self) -> None:
        self._show_with_text("🔊  Speaking…")

    def show_error(self, message: str = "Error") -> None:
        self._show_with_text(f"⚠️  {message}")

    # --- internals ----------------------------------------------------------

    def _show_with_text(self, text: str) -> None:
        self._label.setText(text)
        self._reposition()
        if not self.isVisible():
            self.show()
            self._apply_no_activate()
        self.raise_()

    def _reposition(self) -> None:
        rect = self._resolve_rect()
        if rect is None:
            return
        left, top, width, height = rect
        x = left + width - WIDTH - MARGIN
        y = top + height - HEIGHT - MARGIN
        self.move(x, y)

    def _resolve_rect(self) -> Optional[tuple[int, int, int, int]]:
        try:
            return monitors.get_monitor_rect(self._config.capture_monitor_device)
        except Exception:
            return None

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
