"""Agentic browsing: an app-owned Chrome that Claude drives via Playwright MCP.

The voice assistant already runs Claude as a headless, tool-using agent (see
``claude_client``). This module gives that agent a *web browser* to use, in a way
that keeps the window open on screen for the user to read.

Design (two decoupled pieces):

1. **App-owned Chrome.** This module launches a real Chrome with remote debugging
   enabled (CDP), placed on the user's chosen monitor, and owns its lifetime —
   so the window persists across turns and is closed only on quit/restart.

2. **Per-turn stdio MCP server.** On a browsing turn the app passes Claude an
   ``--mcp-config`` describing a *stdio* Playwright MCP server (spawned via
   ``cmd /c npx`` on Windows) pointed at the Chrome's CDP endpoint with
   ``--cdp-endpoint``. Claude spawns that server itself, it attaches to the
   already-open Chrome, drives it, and is torn down when the turn ends — but the
   Chrome stays up because *we* own it, not the MCP server.

Why this shape: ``--mcp-config`` over HTTP/SSE does not connect from the CLI's
headless ``-p`` mode (verified — the server never receives a connection), whereas
a stdio server spawned by Claude works reliably. But a stdio server's own browser
would die with the turn, so we host the browser ourselves and attach over CDP.

Heavy / OS bits (the Chrome subprocess, win32 monitor geometry) are kept behind
functions so the pure pieces — intent detection and config/arg generation —
import and unit-test anywhere without Chrome, Node, or Windows.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_RUNTIME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime")

# Common Chrome install locations on Windows.
_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(
        os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"
    ),
)

# Phrases that mark a turn as a browsing request. Deterministic and cheap, so a
# normal turn pays no latency. Matched as substrings against the lowered text.
_BROWSE_TRIGGERS = (
    "open the browser",
    "open a browser",
    "open up the browser",
    "open chrome",
    "in the browser",
    "in chrome",
    "search for",
    "search the web",
    "look up",
    "look it up",
    "google ",
    "pull up",
    "browse to",
    "browse for",
    "navigate to",
    "go to the website",
    "open the page",
    "open the website",
    "open the site",
    "on cppreference",
    "find the documentation",
    "find the docs",
    "show me online",
    # C++ documentation requests route through the fast cppreference path (see
    # ``open_cppreference``); these get the browser attached so the tool exists.
    "cppreference",
    "the docs for",
    "the documentation for",
    "the reference for",
    "reference page for",
)

# Phrases that end browsing mode (and close the window).
_BROWSE_EXIT_TRIGGERS = (
    "close the browser",
    "close the tab",
    "close chrome",
    "stop browsing",
    "done browsing",
    "exit the browser",
    "you can close the browser",
)


def looks_like_browse_request(text: str) -> bool:
    """True if the transcript clearly asks the assistant to use the browser."""
    lowered = (text or "").lower()
    return any(trigger in lowered for trigger in _BROWSE_TRIGGERS)


def looks_like_browse_exit(text: str) -> bool:
    """True if the transcript asks to close the browser / stop browsing."""
    lowered = (text or "").lower()
    return any(trigger in lowered for trigger in _BROWSE_EXIT_TRIGGERS)


def find_chrome() -> Optional[str]:
    """Locate chrome.exe (standard install dirs, then PATH)."""
    for path in _CHROME_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return shutil.which("chrome") or shutil.which("chrome.exe")


def build_chrome_args(
    chrome: str,
    cdp_port: int,
    profile_dir: str,
    headless: bool,
    rect: Optional[tuple],
) -> list:
    """Command line for the app-owned Chrome (CDP enabled, placed on a monitor)."""
    args = [
        chrome,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")
    elif rect:
        left, top, width, height = rect
        margin = 40
        args.append(f"--window-position={left + margin},{top + margin}")
        args.append(
            f"--window-size={max(640, width - 2 * margin)},"
            f"{max(480, height - 2 * margin)}"
        )
    args.append("about:blank")
    return args


def build_stdio_mcp_config(cdp_port: int) -> dict:
    """Claude ``--mcp-config`` payload: a stdio Playwright MCP attached over CDP.

    Spawned via ``cmd /c npx`` on Windows so the ``npx.cmd`` shim runs in a shell
    (Claude's launcher cannot exec a ``.cmd`` directly). ``--cdp-endpoint`` points
    it at our already-running Chrome rather than launching its own.
    """
    endpoint = f"http://127.0.0.1:{cdp_port}"
    if sys.platform == "win32":
        command, prefix = "cmd", ["/c", "npx"]
    else:
        command, prefix = "npx", []
    return {
        "mcpServers": {
            "playwright": {
                "command": command,
                "args": prefix
                + ["-y", "@playwright/mcp@latest", "--cdp-endpoint", endpoint],
            },
            # The fast C++ docs path: one tool that navigates this same owned
            # Chrome straight to cppreference, no agentic snapshot/click loop.
            "cppreference": build_cppreference_mcp_server(endpoint),
        }
    }


def build_cppreference_mcp_server(cdp_endpoint: str) -> dict:
    """Claude ``--mcp-config`` entry for the in-repo cppreference fast-path tool.

    A tiny stdio MCP server (``cppreference_mcp.py``) run with this app's own
    Python interpreter. It exposes a single tool, ``open_cppreference``, that
    navigates the same app-owned Chrome (over ``cdp_endpoint``) directly to the
    docs. No npx/Node and no extra Chrome — it shares the one we already own.
    """
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cppreference_mcp.py"
    )
    return {
        "command": sys.executable,
        "args": [script, "--cdp-endpoint", cdp_endpoint],
    }


# ---------------------------------------------------------------------------
# Fast path: cppreference
# ---------------------------------------------------------------------------
# C++ documentation requests skip the agentic snapshot -> reason -> click ->
# read loop. The model is already in the loop every turn, so inferring the symbol
# ("the scoped mutex thing" -> lock_guard) is free; it just needs a tool to say
# it. cppreference runs on MediaWiki, whose go-search endpoint resolves a loose
# symbol to the exact page SERVER-SIDE, so the model passes only a rough symbol,
# never the (non-obvious) page path -- e.g. lock_guard lives under
# /w/cpp/thread/lock_guard. One navigate, no snapshot, no read-back.


def build_cppreference_url(query: str) -> str:
    """Build the cppreference go-search URL for a loose C++ symbol or concept.

    MediaWiki's ``Special:Search`` with ``go=Go`` jumps straight to the matching
    article, so the caller passes a rough symbol (``lock_guard``, ``std::sort``)
    rather than a page path.
    """
    encoded = urllib.parse.quote(query.strip())
    return (
        "https://en.cppreference.com/mwiki/index.php"
        f"?title=Special:Search&search={encoded}&go=Go"
    )


async def open_cppreference(cdp_endpoint: str, query: str) -> str:
    """Navigate the app-owned Chrome to the cppreference page for ``query``.

    Tries Playwright first (reusing the live tab over CDP, as Playwright MCP
    does); if Playwright is not installed or fails, falls back to the
    dependency-free CDP HTTP endpoint (opens the page in a new tab of the *same*
    owned window). Never launches a second Chrome, never snapshots or reads the
    page, and never raises -- on any failure it returns a spoken-safe message.
    Returns a short, text-to-speech-friendly confirmation.
    """
    spoken = query.strip() or "C++"
    url = build_cppreference_url(query)
    try:
        if await _navigate_via_playwright(cdp_endpoint, url):
            return f"Opening the {spoken} reference."
    except Exception as exc:  # nav error -- fall back, don't crash
        logger.warning("cppreference Playwright navigation failed: %s", exc)
    try:
        _navigate_via_cdp_http(cdp_endpoint, url)
        return f"Opening the {spoken} reference."
    except Exception as exc:
        logger.warning("cppreference navigation failed: %s", exc)
        return f"Sorry, I couldn't open the {spoken} reference just now."


async def _navigate_via_playwright(cdp_endpoint: str, url: str) -> bool:
    """Navigate the owned Chrome by reusing its tab over CDP via Playwright.

    Returns True on success, False if Playwright is not installed (so the caller
    falls back). Raises only on a genuine navigation error. The app-owned Chrome
    is never closed -- we only drop our CDP connection when the driver stops.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
        context = (
            browser.contexts[0] if browser.contexts else await browser.new_context()
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
    # Leaving the async_playwright() block stops the driver and drops our CDP
    # connection; Chrome is a separate process we own, so it stays open.
    return True


def _navigate_via_cdp_http(cdp_endpoint: str, url: str) -> None:
    """Dependency-free fallback: open ``url`` in a new tab of the owned Chrome.

    Uses the CDP HTTP endpoint (stdlib only). Modern Chrome requires PUT for
    ``/json/new``; older builds accept GET. Raises if neither works.
    """
    target = f"{cdp_endpoint.rstrip('/')}/json/new?{url}"
    last_error: Optional[Exception] = None
    for method in ("PUT", "GET"):
        try:
            request = urllib.request.Request(target, method=method)
            urllib.request.urlopen(request, timeout=5).read()
            return
        except Exception as exc:
            last_error = exc
    raise last_error if last_error else RuntimeError("CDP navigation failed.")


def resolve_browser_rect(device: Optional[str]) -> Optional[tuple]:
    """Monitor rect to place the browser on; default to a secondary display."""
    try:
        import monitors
    except Exception:
        return None
    if device:
        return monitors.get_monitor_rect(device)
    for mon in monitors.list_monitors():
        if not mon["is_primary"]:
            return mon["rect"]
    return monitors.get_monitor_rect(None)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BrowserSession:
    """Lifecycle for the app-owned Chrome that Claude drives over CDP.

    Started lazily on the first browsing turn (so normal startup launches no
    browser) and stopped on quit/restart. ``ensure_ready`` is safe to call from a
    worker thread — it blocks on the (first-run) Chrome startup there rather than
    on the UI thread, and returns the path to the stdio MCP config to hand Claude.
    """

    def __init__(self, config, runtime_dir: str = _RUNTIME_DIR) -> None:
        self._config = config
        self._runtime_dir = runtime_dir
        self._cdp_port: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._mcp_config_path: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def mcp_config_path(self) -> Optional[str]:
        return self._mcp_config_path

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_ready(self, start_timeout: float = 60.0) -> bool:
        """Launch Chrome if needed and wait until its CDP endpoint responds."""
        with self._lock:
            if not self.is_running():
                if not self._start():
                    return False
            return self._wait_ready(start_timeout)

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None:
                return
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        creationflags=_NO_WINDOW,
                    )
                else:
                    proc.terminate()
            except Exception as exc:
                logger.warning("Failed to stop browser: %s", exc)
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # --- internals ----------------------------------------------------------

    def _start(self) -> bool:
        chrome = find_chrome()
        if not chrome:
            logger.warning("Chrome not found; cannot start agentic browsing.")
            return False
        os.makedirs(self._runtime_dir, exist_ok=True)
        self._cdp_port = _free_port()

        mcp_path = os.path.join(self._runtime_dir, "browser-mcp.json")
        with open(mcp_path, "w", encoding="utf-8") as fh:
            json.dump(build_stdio_mcp_config(self._cdp_port), fh, indent=2)
        self._mcp_config_path = mcp_path

        rect = resolve_browser_rect(self._config.browser_monitor_device)
        profile = os.path.join(self._runtime_dir, "browser-profile")
        args = build_chrome_args(
            chrome, self._cdp_port, profile, self._config.browser_headless, rect
        )
        logger.info("Launching browser with CDP on port %s.", self._cdp_port)
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        return True

    def _wait_ready(self, timeout: float) -> bool:
        if self._cdp_port is None:
            return False
        url = f"http://127.0.0.1:{self._cdp_port}/json/version"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                logger.warning("Browser exited before its CDP endpoint came up.")
                return False
            try:
                urllib.request.urlopen(url, timeout=1).read()
                return True
            except Exception:
                time.sleep(0.25)
        logger.warning("Browser CDP endpoint not ready after %.0fs.", timeout)
        return False
