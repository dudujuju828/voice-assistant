from __future__ import annotations

import asyncio
import sys
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
            # C++ documentation phrasings route through the fast path.
            "open the docs for std::sort",
            "show me the documentation for std::vector",
            "what's on cppreference for lock_guard",
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


class StdioMcpConfigTests(unittest.TestCase):
    def test_stdio_config_attaches_to_cdp_endpoint(self) -> None:
        cfg = browser_mcp.build_stdio_mcp_config(9123)
        server = cfg["mcpServers"]["playwright"]
        # A stdio server (has a command), not an http url.
        self.assertIn("command", server)
        self.assertNotIn("url", server)
        self.assertIn("@playwright/mcp@latest", server["args"])
        self.assertIn("--cdp-endpoint", server["args"])
        idx = server["args"].index("--cdp-endpoint")
        self.assertEqual(server["args"][idx + 1], "http://127.0.0.1:9123")


class CppreferenceMcpServerTests(unittest.TestCase):
    def test_cppreference_server_registered_alongside_playwright(self) -> None:
        cfg = browser_mcp.build_stdio_mcp_config(9123)
        servers = cfg["mcpServers"]
        # Additive: the existing Playwright server is untouched.
        self.assertIn("playwright", servers)
        self.assertIn("cppreference", servers)
        cpp = servers["cppreference"]
        # Runs with this app's own Python interpreter (no npx/Node), pointed at
        # the same CDP endpoint the Playwright server attaches to.
        self.assertEqual(cpp["command"], sys.executable)
        self.assertIn("--cdp-endpoint", cpp["args"])
        idx = cpp["args"].index("--cdp-endpoint")
        self.assertEqual(cpp["args"][idx + 1], "http://127.0.0.1:9123")
        self.assertTrue(cpp["args"][0].endswith("cppreference_mcp.py"))


class CppreferenceUrlTests(unittest.TestCase):
    def test_url_uses_mediawiki_go_search(self) -> None:
        url = browser_mcp.build_cppreference_url("lock_guard")
        self.assertEqual(
            url,
            "https://en.cppreference.com/mwiki/index.php"
            "?title=Special:Search&search=lock_guard&go=Go",
        )

    def test_url_percent_encodes_symbol(self) -> None:
        self.assertIn(
            "search=std%3A%3Asort",
            browser_mcp.build_cppreference_url("std::sort"),
        )
        self.assertIn(
            "search=scoped%20mutex",
            browser_mcp.build_cppreference_url("  scoped mutex  "),
        )


class OpenCppreferenceDegradeTests(unittest.TestCase):
    def test_unreachable_endpoint_returns_spoken_safe_message(self) -> None:
        # Port 1 refuses instantly; Playwright is absent in CI -> HTTP fallback
        # fails too. Must degrade to a spoken message, never raise.
        reply = asyncio.run(
            browser_mcp.open_cppreference("http://127.0.0.1:1", "lock_guard")
        )
        self.assertIsInstance(reply, str)
        self.assertIn("lock_guard", reply)


class ChromeArgsTests(unittest.TestCase):
    def test_headed_places_window_on_monitor(self) -> None:
        # rect = (left, top, width, height) of a secondary monitor.
        args = browser_mcp.build_chrome_args(
            "chrome.exe", 9222, "C:/profile", False, (1920, 0, 1920, 1080)
        )
        self.assertIn("--remote-debugging-port=9222", args)
        self.assertIn("--user-data-dir=C:/profile", args)
        self.assertIn("--window-position=1960,40", args)  # left+margin, top+margin
        self.assertIn("--window-size=1840,1000", args)  # width-2m, height-2m
        self.assertNotIn("--headless=new", args)

    def test_headless_omits_window_args_and_adds_headless(self) -> None:
        args = browser_mcp.build_chrome_args(
            "chrome.exe", 9222, "C:/profile", True, (1920, 0, 1920, 1080)
        )
        self.assertIn("--headless=new", args)
        self.assertFalse(any(a.startswith("--window-position") for a in args))

    def test_headed_without_rect_omits_window_args(self) -> None:
        args = browser_mcp.build_chrome_args(
            "chrome.exe", 9222, "C:/profile", False, None
        )
        self.assertFalse(any(a.startswith("--window-position") for a in args))


if __name__ == "__main__":
    unittest.main()
