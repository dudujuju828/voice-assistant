"""Activatable input window that grabs focus on hotkey.

Wispr Flow (or the keyboard) types into the focused QLineEdit. Enter submits
and emits :attr:`submitted` with the text; Escape cancels.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

import monitors

try:
    import win32gui
except ImportError:  # pragma: no cover - non-Windows dev import
    win32gui = None  # type: ignore

WIDTH = 640
HEIGHT = 72


class InputWindow(QWidget):
    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, config) -> None:
        super().__init__()
        self._config = config

        # Frameless + on-top, but intentionally activatable (no WS_EX_NOACTIVATE)
        # so it can take keyboard focus for Wispr / typing.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(WIDTH, HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText("Speak or type your question, then press Enter…")
        font = QFont()
        font.setPointSize(16)
        self._edit.setFont(font)
        self._edit.setStyleSheet(
            "QLineEdit {"
            "  color: white;"
            "  background-color: rgba(24, 24, 28, 235);"
            "  border: 2px solid rgba(120, 130, 255, 220);"
            "  border-radius: 14px;"
            "  padding: 14px 18px;"
            "}"
        )
        self._edit.returnPressed.connect(self._on_return)
        layout.addWidget(self._edit)

    # --- show / focus -------------------------------------------------------

    def show_and_focus(self) -> None:
        self._edit.clear()
        self._reposition()
        self.show()
        self.raise_()
        self.activateWindow()

        # Hotkey granted foreground rights — claim them explicitly.
        if win32gui is not None:
            try:
                win32gui.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass

        self._edit.setFocus(Qt.OtherFocusReason)

    def _reposition(self) -> None:
        try:
            rect = monitors.get_monitor_rect(self._config.capture_monitor_device)
        except Exception:
            rect = None
        if rect is None:
            return
        left, top, width, height = rect
        x = left + (width - WIDTH) // 2
        y = top + int(height * 0.32)
        self.move(x, y)

    # --- events -------------------------------------------------------------

    def _on_return(self) -> None:
        text = self._edit.text().strip()
        self.hide()
        if text:
            self.submitted.emit(text)
        else:
            self.cancelled.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt signature)
        if event.key() == Qt.Key_Escape:
            self.hide()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)
