from __future__ import annotations

import unittest
from unittest.mock import patch

from single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    def test_non_windows_never_reports_duplicate(self) -> None:
        with patch("single_instance.sys.platform", "linux"):
            guard = SingleInstance()

        self.assertFalse(guard.already_running)
        guard.close()


if __name__ == "__main__":
    unittest.main()
