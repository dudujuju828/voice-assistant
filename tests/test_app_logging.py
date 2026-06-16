from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import app_logging


class AppLoggingTests(unittest.TestCase):
    def test_setup_logging_does_not_duplicate_file_handler(self) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_hook = sys.excepthook
        original_installed = app_logging._excepthook_installed
        original_previous = app_logging._previous_excepthook
        try:
            root.handlers = []
            app_logging._excepthook_installed = False
            app_logging._previous_excepthook = None
            sys.excepthook = original_hook
            with tempfile.TemporaryDirectory() as tmp:
                with patch.dict("os.environ", {"APPDATA": str(Path(tmp))}):
                    app_logging.setup_logging()
                    app_logging.setup_logging()

                file_handlers = [
                    handler
                    for handler in root.handlers
                    if isinstance(handler, RotatingFileHandler)
                ]
                self.assertEqual(len(file_handlers), 1)
                for handler in file_handlers:
                    handler.close()
                    root.removeHandler(handler)
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers = original_handlers
            sys.excepthook = original_hook
            app_logging._excepthook_installed = original_installed
            app_logging._previous_excepthook = original_previous

    def test_install_excepthook_is_idempotent(self) -> None:
        original_hook = sys.excepthook
        original_installed = app_logging._excepthook_installed
        original_previous = app_logging._previous_excepthook
        try:
            app_logging._excepthook_installed = False
            app_logging._previous_excepthook = None
            sys.excepthook = original_hook

            app_logging.install_excepthook()
            installed_hook = sys.excepthook
            app_logging.install_excepthook()

            self.assertIs(sys.excepthook, installed_hook)
            self.assertIs(app_logging._previous_excepthook, original_hook)
        finally:
            sys.excepthook = original_hook
            app_logging._excepthook_installed = original_installed
            app_logging._previous_excepthook = original_previous

    def test_uncaught_exception_hook_logs_exception(self) -> None:
        calls = []
        original_previous = app_logging._previous_excepthook
        try:
            app_logging._previous_excepthook = lambda *args: calls.append(args)
            exc = RuntimeError("boom")

            with self.assertLogs("app_logging", level="CRITICAL") as captured:
                app_logging._log_uncaught_exception(RuntimeError, exc, None)

            self.assertIn("Unhandled exception", captured.output[0])
            self.assertEqual(len(calls), 1)
        finally:
            app_logging._previous_excepthook = original_previous


if __name__ == "__main__":
    unittest.main()
