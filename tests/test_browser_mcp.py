from __future__ import annotations

import unittest

import browser_mcp


class IntentDetectionTests(unittest.TestCase):
    def test_browse_requests_are_detected(self) -> None:
        for text in [
            "open the browser and search for lock guard",
            "look up the cppreference page for std::vector",
            "search the web for the weather tomorrow",
            "pull up the GitHub repo",
            "go to the website for me",
            "open chrome and navigate to example dot com",
        ]:
            self.assertTrue(
                browser_mcp.looks_like_browse_request(text), msg=text
            )

    def test_plain_turns_are_not_browse_requests(self) -> None:
        for text in [
            "what time is it",
            "tell me a joke",
            "summarize this screen",
            "what does this error mean",
        ]:
            self.assertFalse(
                browser_mcp.looks_like_browse_request(text), msg=text
            )

    def test_exit_phrases_are_detected(self) -> None:
        self.assertTrue(browser_mcp.looks_like_browse_exit("close the browser now"))
        self.assertTrue(browser_mcp.looks_like_browse_exit("ok, stop browsing"))
        self.assertFalse(browser_mcp.looks_like_browse_exit("scroll down a bit"))
        self.assertFalse(browser_mcp.looks_like_browse_exit("close the modal dialog"))


class ConfigGenerationTests(unittest.TestCase):
    def test_claude_mcp_config_points_at_http_port(self) -> None:
        cfg = browser_mcp.build_claude_mcp_config(9123)
        server = cfg["mcpServers"]["playwright"]
        self.assertEqual(server["type"], "http")
        self.assertEqual(server["url"], "http://127.0.0.1:9123/mcp")

    def test_headed_config_places_window_on_monitor(self) -> None:
        # rect = (left, top, width, height) of a secondary monitor.
        cfg = browser_mcp.build_playwright_config(False, (1920, 0, 1920, 1080))
        launch = cfg["browser"]["launchOptions"]
        self.assertEqual(launch["channel"], "chrome")
        self.assertFalse(launch["headless"])
        args = launch["args"]
        self.assertIn("--window-position=1960,40", args)  # left+margin, top+margin
        self.assertIn("--window-size=1840,1000", args)  # width-2m, height-2m

    def test_headless_config_omits_window_args(self) -> None:
        cfg = browser_mcp.build_playwright_config(True, (1920, 0, 1920, 1080))
        launch = cfg["browser"]["launchOptions"]
        self.assertTrue(launch["headless"])
        self.assertNotIn("args", launch)

    def test_headed_without_rect_omits_window_args(self) -> None:
        cfg = browser_mcp.build_playwright_config(False, None)
        self.assertNotIn("args", cfg["browser"]["launchOptions"])


if __name__ == "__main__":
    unittest.main()
