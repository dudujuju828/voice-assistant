"""Application logging setup."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILENAME = "voice-assistant.log"
_previous_excepthook = None
_excepthook_installed = False


def log_path() -> Path:
    base = os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "VoiceAssistant" / LOG_FILENAME


def setup_logging() -> None:
    """Write diagnostics to a small rotating log under the config directory."""
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        return

    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    if any(
        isinstance(existing, RotatingFileHandler)
        and getattr(existing, "baseFilename", None) == handler.baseFilename
        for existing in root.handlers
    ):
        handler.close()
    else:
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    install_excepthook()


def install_excepthook() -> None:
    """Log otherwise-unhandled exceptions once before Python's normal hook."""
    global _excepthook_installed, _previous_excepthook
    if _excepthook_installed:
        return
    _previous_excepthook = sys.excepthook
    sys.excepthook = _log_uncaught_exception
    _excepthook_installed = True


def _log_uncaught_exception(exc_type, exc_value, traceback) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        if _previous_excepthook is not None:
            _previous_excepthook(exc_type, exc_value, traceback)
        return

    logging.getLogger(__name__).critical(
        "Unhandled exception",
        exc_info=(exc_type, exc_value, traceback),
    )
    if _previous_excepthook is not None:
        _previous_excepthook(exc_type, exc_value, traceback)
