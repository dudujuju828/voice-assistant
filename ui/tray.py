"""System tray icon + menu (Settings / Pause hotkey / Quit)."""
from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon, QMenu


def _build_icon() -> QIcon:
    """Generate a simple round microphone-dot icon so we ship no binary asset."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor(90, 110, 255)))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(6, 6, 52, 52)
    painter.setPen(QColor(255, 255, 255))
    font = QFont()
    font.setPointSize(28)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "V")
    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    open_settings = Signal()
    reset_session = Signal()
    toggle_pause = Signal(bool)
    restart_requested = Signal()
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__(_build_icon())
        self.setToolTip("Voice Assistant")

        menu = QMenu()

        self._settings_action = QAction("Settings", menu)
        self._settings_action.triggered.connect(self.open_settings.emit)
        menu.addAction(self._settings_action)

        self._reset_session_action = QAction("Reset Claude Session", menu)
        self._reset_session_action.triggered.connect(self.reset_session.emit)
        menu.addAction(self._reset_session_action)

        self._pause_action = QAction("Pause Hotkey", menu)
        self._pause_action.setCheckable(True)
        self._pause_action.toggled.connect(self._on_pause_toggled)
        menu.addAction(self._pause_action)

        menu.addSeparator()

        self._restart_action = QAction("Restart Voice Assistant", menu)
        self._restart_action.triggered.connect(self.restart_requested.emit)
        menu.addAction(self._restart_action)

        self._quit_action = QAction("Quit", menu)
        self._quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(self._quit_action)

        self.setContextMenu(menu)

    def _on_pause_toggled(self, checked: bool) -> None:
        self._pause_action.setText("Resume Hotkey" if checked else "Pause Hotkey")
        self.toggle_pause.emit(checked)

    def set_paused(self, paused: bool) -> None:
        blocker = QSignalBlocker(self._pause_action)
        try:
            self._pause_action.setChecked(paused)
            self._pause_action.setText("Resume Hotkey" if paused else "Pause Hotkey")
        finally:
            del blocker

    def notify(self, title: str, message: str) -> None:
        """Balloon notification for errors / status."""
        self.showMessage(title, message, QSystemTrayIcon.Information, 5000)
