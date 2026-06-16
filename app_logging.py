"""Application logging setup."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILENAME = "voice-assistant.log"


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
    if not any(
        isinstance(existing, RotatingFileHandler)
        and getattr(existing, "baseFilename", None) == handler.baseFilename
        for existing in root.handlers
    ):
        root.addHandler(handler)
    root.setLevel(logging.INFO)
