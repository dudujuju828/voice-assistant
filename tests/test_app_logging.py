from __future__ import annotations

import logging
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
        try:
            root.handlers = []
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


if __name__ == "__main__":
    unittest.main()
