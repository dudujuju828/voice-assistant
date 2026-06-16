from __future__ import annotations

import unittest
from unittest.mock import patch

import runtime_checks


class RuntimeChecksTests(unittest.TestCase):
    def test_non_windows_is_not_elevated(self) -> None:
        with patch("runtime_checks.sys.platform", "linux"):
            self.assertFalse(runtime_checks.is_running_elevated())

    def test_windows_api_failure_is_not_elevated(self) -> None:
        with (
            patch("runtime_checks.sys.platform", "win32"),
            patch.object(runtime_checks.ctypes, "windll", new=None, create=True),
        ):
            self.assertFalse(runtime_checks.is_running_elevated())


if __name__ == "__main__":
    unittest.main()
