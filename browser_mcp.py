"""Agentic browsing: an app-owned Playwright MCP server Claude can drive.

The voice assistant already runs Claude as a headless, tool-using agent (see
``claude_client``). This module gives that agent a *web browser* to use: it
launches Microsoft's Playwright MCP server (``@playwright/mcp`` via ``npx``) as a
long-lived process bound to a local SSE port. On a browsing turn the app passes
Claude an ``--mcp-config`` pointing at that port, so Claude gets browser tools
(navigate / click / type / read) and does the navigation itself.

Why a persistent server rather than a per-turn one: a stdio MCP server dies with
the ``claude -p`` subprocess, which would close the browser the moment the turn
ends. A long-lived server with ``--shared-browser-context`` keeps the same Chrome
window open across turns — so the page stays on screen for the user to read, and
follow-ups ("scroll down", "click that") continue in the same session.

Heavy / OS bits (the npx subprocess, win32 monitor geometry) are kept behind
functions so the pure pieces — intent detection and config generation — import
and unit-test anywhere without Node or Windows.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_RUNTIME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime")

# Phrases that mark a turn as a browsing request. Deterministic and cheap, so a
# normal turn pays no latency. Matched as substrings against the lowered text;
# add to this list to broaden coverage.
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


def build_playwright_config(headless: bool, rect: Optional[tuple]) -> dict:
    """Playwright MCP ``--config`` payload: Chrome channel + window placement.

    ``rect`` is (left, top, width, height) of the target monitor; when headed we
    position and size the window to fill that monitor (minus a margin) so it
    opens on the screen the user picked. Ignored when headless.
    """
    launch: dict = {"channel": "chrome", "headless": bool(headless)}
    if not headless and rect:
        left, top, width, height = rect
        margin = 40
        win_w = max(640, width - 2 * margin)
        win_h = max(480, height - 2 * margin)
        launch["args"] = [
            f"--window-position={left + margin},{top + margin}",
            f"--window-size={win_w},{win_h}",
        ]
    return {"browser": {"browserName": "chromium", "launchOptions": launch}}


def build_claude_mcp_config(port: int) -> dict:
    """Claude Code ``--mcp-config`` payload pointing at the MCP server.

    Uses the streamable-HTTP ``/mcp`` endpoint (the server's recommended
    transport; ``/sse`` is legacy) on an explicit IPv4 address so it matches the
    server's ``--host 127.0.0.1`` bind.
    """
    return {
        "mcpServers": {
            "playwright": {
                "type": "http",
                "url": f"http://127.0.0.1:{port}/mcp",
            }
        }
    }


def resolve_browser_rect(device: Optional[str]) -> Optional[tuple]:
    """Monitor rect to place the browser on; default to a secondary display.

    With no saved device we prefer a non-primary monitor (so the browser opens
    "next to" the user by default), falling back to the primary one.
    """
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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class BrowserMcpServer:
    """Lifecycle for the persistent Playwright MCP server (one per app).

    Started lazily on the first browsing turn (so normal startup needs no Node
    and pops no window) and stopped on quit/restart. ``ensure_ready`` is safe to
    call from a worker thread — it blocks on the (potentially slow, first-run
    npx download) startup there rather than on the UI thread.
    """

    def __init__(self, config, runtime_dir: str = _RUNTIME_DIR) -> None:
        self._config = config
        self._runtime_dir = runtime_dir
        self._port: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._mcp_config_path: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def mcp_config_path(self) -> Optional[str]:
        return self._mcp_config_path

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_ready(self, start_timeout: float = 120.0) -> bool:
        """Start the server if needed and wait until its port accepts a socket."""
        with self._lock:
            if not self.is_running():
                self._start()
            return self._wait_ready(start_timeout)

    def stop(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None:
                return
            try:
                if sys.platform == "win32":
                    # Kill the whole tree so the spawned Chrome closes too.
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        creationflags=_NO_WINDOW,
                    )
                else:
                    proc.terminate()
            except Exception as exc:
                logger.warning("Failed to stop browser MCP server: %s", exc)
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # --- internals ----------------------------------------------------------

    def _start(self) -> None:
        npx = shutil.which("npx") or "npx"
        os.makedirs(self._runtime_dir, exist_ok=True)
        self._port = _free_port()

        rect = resolve_browser_rect(self._config.browser_monitor_device)
        pw_config = build_playwright_config(self._config.browser_headless, rect)
        pw_path = os.path.join(self._runtime_dir, "playwright-config.json")
        self._write_json(pw_path, pw_config)

        mcp_path = os.path.join(self._runtime_dir, "browser-mcp.json")
        self._write_json(mcp_path, build_claude_mcp_config(self._port))
        self._mcp_config_path = mcp_path

        profile = os.path.join(self._runtime_dir, "browser-profile")
        args = [
            npx,
            "-y",
            "@playwright/mcp@latest",
            # Bind IPv4 explicitly: the default "localhost" can bind only IPv6
            # (::1), which our 127.0.0.1 readiness probe and Claude's connection
            # would never reach.
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "--user-data-dir",
            profile,
            "--shared-browser-context",
            "--config",
            pw_path,
        ]
        logger.info("Starting browser MCP server on port %s.", self._port)
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
            cwd=self._runtime_dir,
        )

    def _wait_ready(self, timeout: float) -> bool:
        if self._port is None:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                logger.warning("Browser MCP server exited before becoming ready.")
                return False
            if _port_open(self._port):
                return True
            time.sleep(0.25)
        logger.warning("Browser MCP server not ready after %.0fs.", timeout)
        return False

    @staticmethod
    def _write_json(path: str, payload: dict) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
