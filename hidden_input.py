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
from PySide6.QtWidgets import QLineEdit, QWidget

try:
    import win32gui
except ImportError:  # pragma: no cover - non-Windows dev import
    win32gui = None  # type: ignore


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
