"""Invisible capture box for the "hidden_input" method.

Some users prefer Wispr Flow to *type* its transcription rather than copy it to
the clipboard. Typing goes to whatever control has focus, so we give Wispr an
invisible, off-screen QLineEdit to type into. The window has no border, never
appears on screen or in the taskbar, and is parked far outside the visible
desktop — but it does briefly take keyboard focus so the keystrokes land
somewhere. After the hotkey is released we read the text back and clear it.

For most setups the clipboard method is simpler and is the default; this is the
fallback for people who can't or don't want to use clipboard copy.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

import monitors

try:
    import win32gui
except ImportError:  # pragma: no cover - non-Windows dev import
    win32gui = None  # type: ignore

CAPTURE_PLACEHOLDER = "Listening..."
VISIBLE_MARGIN = 48
VISIBLE_WIDTH = 720
VISIBLE_HEIGHT = 52


class HiddenInput(QWidget):
    def __init__(self) -> None:
        super().__init__()
        # Frameless tool window with no taskbar entry. Parked far off-screen and
        # sized to nothing so it has zero visual presence even while focused.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(1, 1)
        self.move(-10000, -10000)

        self._edit = QLineEdit(self)
        self._edit.setGeometry(0, 0, 1, 1)

    # --- capture lifecycle --------------------------------------------------

    def focus_for_capture(self) -> None:
        """Show off-screen and grab focus so Wispr's keystrokes land here."""
        self._edit.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        if win32gui is not None:
            try:
                win32gui.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass
        self._edit.setFocus(Qt.OtherFocusReason)

    def read_and_clear(self) -> str:
        """Return whatever was typed, then reset and hide the box."""
        text = self._edit.text().strip()
        self._edit.clear()
        self.hide()
        return text


class VisibleInput(QWidget):
    """Small focused text box for Wispr setups that need visible selection."""

    def __init__(self, config) -> None:
        super().__init__()
        self._config = config
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(VISIBLE_WIDTH, VISIBLE_HEIGHT)

        self._edit = QLineEdit(self)
        self._edit.setGeometry(0, 0, VISIBLE_WIDTH, VISIBLE_HEIGHT)
        self._edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._edit.setStyleSheet(
            """
            QLineEdit {
                background: rgba(18, 22, 28, 235);
                border: 1px solid rgba(255, 255, 255, 90);
                border-radius: 8px;
                color: white;
                font-size: 18px;
                padding: 8px 14px;
                selection-background-color: #2f6fed;
                selection-color: white;
            }
            """
        )

    def focus_for_capture(self) -> None:
        """Show, select placeholder text, and focus for Wispr insertion."""
        self._reposition()
        self._edit.setText(CAPTURE_PLACEHOLDER)
        self._edit.selectAll()
        self.show()
        self.raise_()
        self.activateWindow()
        if win32gui is not None:
            try:
                win32gui.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass
        self._edit.setFocus(Qt.OtherFocusReason)
        self._edit.selectAll()

    def read_and_clear(self) -> str:
        """Return typed transcript, then reset and hide the box."""
        text = self._edit.text().strip()
        self._edit.clear()
        self.hide()
        if text == CAPTURE_PLACEHOLDER:
            return ""
        if text.startswith(CAPTURE_PLACEHOLDER):
            return text[len(CAPTURE_PLACEHOLDER) :].strip()
        return text

    def _reposition(self) -> None:
        rect = self._resolve_rect()
        if rect is None:
            return
        left, top, width, height = rect
        available_width = max(1, width - (VISIBLE_MARGIN * 2))
        box_width = min(VISIBLE_WIDTH, available_width)
        self.resize(box_width, VISIBLE_HEIGHT)
        self._edit.setGeometry(0, 0, box_width, VISIBLE_HEIGHT)
        x = left + ((width - box_width) // 2)
        y = top + max(0, height - VISIBLE_HEIGHT - VISIBLE_MARGIN)
        self.move(x, y)

    def _resolve_rect(self) -> tuple[int, int, int, int] | None:
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
