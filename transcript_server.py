"""Local web server for the transcript page.

Serves a single self-contained page that shows the **current conversation live**
— reacting in real time to the same states as the tray dot (listening,
thinking, speaking) — alongside a **browsable history** of past conversations
and **download** links. It binds to 127.0.0.1 only, so transcripts never leave
the machine, and uses nothing outside the standard library (matching the
project's no-extra-dependencies posture for the browsing/MCP pieces).

Live updates use Server-Sent Events: each open page holds a ``/api/events``
connection and the server streams the store's snapshots to it as turns happen.
The HTTP work runs on its own daemon threads; it reads the shared
``TranscriptStore`` (which is thread-safe), so the Qt pipeline thread is never
blocked by a connected page.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from transcript import TranscriptStore

logger = logging.getLogger(__name__)

_HTML_PATH = Path(__file__).resolve().parent / "ui" / "transcript.html"

_FALLBACK_HTML = (
    "<!doctype html><meta charset='utf-8'><title>Voice Assistant Transcript</title>"
    "<body style='font-family:sans-serif;padding:2rem'>"
    "<h1>Transcript</h1><p>The transcript page asset is missing.</p></body>"
)

# Heartbeat so idle SSE connections (and proxies) stay open, and the loop wakes
# often enough to notice a shutdown.
_SSE_HEARTBEAT_SECONDS = 15


class _TranscriptHTTPServer(ThreadingHTTPServer):
    # Daemon request threads so a page left open (especially a long-lived SSE
    # connection) never keeps the process alive on quit/restart.
    daemon_threads = True
    allow_reuse_address = True

    store: TranscriptStore
    html: str
    stopping: bool = False


class _Handler(BaseHTTPRequestHandler):
    server_version = "VoiceAssistantTranscript/1.0"

    # Route the noisy default access log into our debug log instead of stderr.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        logger.debug("transcript http: " + fmt, *args)

    @property
    def _store(self) -> TranscriptStore:
        return self.server.store  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler signature)
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if path == "/" or path == "/index.html":
                self._send_html(self.server.html)  # type: ignore[attr-defined]
            elif path == "/api/state":
                self._send_json(self._store.snapshot())
            elif path == "/api/conversations":
                self._send_json({"conversations": self._store.list_conversations()})
            elif path == "/api/events":
                self._serve_events()
            elif path.startswith("/api/conversations/"):
                self._serve_conversation(path, parse_qs(parts.query))
            else:
                self._send_error_json(404, "Not found")
        except (BrokenPipeError, ConnectionResetError):
            pass  # The page navigated away mid-response; nothing to do.
        except Exception as exc:  # defensive: one bad request must not crash a thread
            logger.warning("transcript request error: %s", exc)
            try:
                self._send_error_json(500, "Internal error")
            except Exception:
                pass

    # --- routes ------------------------------------------------------------

    def _serve_conversation(self, path: str, query: dict) -> None:
        rest = path[len("/api/conversations/") :]
        if rest.endswith("/download"):
            conv_id = rest[: -len("/download")]
            fmt = (query.get("format", ["md"])[0] or "md").lower()
            if fmt not in ("md", "txt", "json"):
                fmt = "md"
            rendered = self._store.render(conv_id, fmt)
            if rendered is None:
                self._send_error_json(404, "Conversation not found")
                return
            filename, content_type, body = rendered
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        conv = self._store.get_conversation(rest)
        if conv is None:
            self._send_error_json(404, "Conversation not found")
            return
        self._send_json(conv)

    def _serve_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # disable any proxy buffering
        self.end_headers()

        store = self._store
        q = store.subscribe()
        try:
            self._sse_send(store.snapshot())  # prime with the current state
            while not getattr(self.server, "stopping", False):
                try:
                    snap = q.get(timeout=_SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                self._sse_send(snap)
        except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
            pass  # Page closed the connection.
        finally:
            store.unsubscribe(q)

    def _sse_send(self, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.wfile.write(b"data: " + payload + b"\n\n")
        self.wfile.flush()

    # --- response helpers --------------------------------------------------

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)


class TranscriptServer:
    """Runs the transcript page over HTTP on localhost in a background thread."""

    def __init__(
        self,
        store: TranscriptStore,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self._store = store
        self._host = host
        self._port = port
        self._httpd: Optional[_TranscriptHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.url: Optional[str] = None

    def start(self) -> Optional[str]:
        """Bind and start serving; return the page URL, or None on failure.

        Tries the configured port first, then an OS-assigned free port, so a
        taken port degrades to a working (if different) URL rather than failing.
        """
        html = self._load_html()
        for port in self._candidate_ports():
            try:
                httpd = _TranscriptHTTPServer((self._host, port), _Handler)
            except OSError as exc:
                logger.info("Transcript port %s unavailable: %s", port, exc)
                continue
            httpd.store = self._store
            httpd.html = html
            httpd.stopping = False
            self._httpd = httpd
            actual_port = httpd.server_address[1]
            self.url = f"http://{self._host}:{actual_port}/"
            self._thread = threading.Thread(
                target=httpd.serve_forever,
                name="transcript-http",
                daemon=True,
            )
            self._thread.start()
            logger.info("Transcript page available at %s", self.url)
            return self.url
        logger.warning("Could not start the transcript server on any port.")
        return None

    def stop(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        httpd.stopping = True
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        self._httpd = None

    # --- internals ---------------------------------------------------------

    def _candidate_ports(self) -> list[int]:
        # Port 0 lets the OS pick a free port. Always fall back to it.
        if self._port and self._port > 0:
            return [self._port, 0]
        return [0]

    def _load_html(self) -> str:
        try:
            return _HTML_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read transcript page asset: %s", exc)
            return _FALLBACK_HTML
