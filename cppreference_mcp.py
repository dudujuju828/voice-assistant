"""Stdio MCP server exposing the cppreference fast-path tool to Claude.

Registered alongside the Playwright MCP server (see ``browser_mcp``) on every
browsing turn. It exposes exactly one tool, ``open_cppreference(query)``, which
navigates the app-owned Chrome straight to the C++ docs for a loose symbol --
skipping the agentic snapshot -> reason -> click -> read loop entirely.

Transport: the MCP stdio protocol -- newline-delimited JSON-RPC 2.0 on
stdin/stdout. Logging goes to stderr so it never corrupts the JSON-RPC channel.
Kept dependency-free (stdlib only) to match the project's no-pip-deps browsing;
the actual navigation lives in ``browser_mcp.open_cppreference``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# Spawned by the Claude CLI from an arbitrary working directory, so make the
# repo importable before pulling in the shared navigation helpers.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import browser_mcp  # noqa: E402

logger = logging.getLogger("cppreference_mcp")

# Used only if the client omits a protocolVersion in initialize (it won't); we
# otherwise echo whatever the client asks for.
PROTOCOL_VERSION = "2025-06-18"

TOOL_DEFINITION = {
    "name": "open_cppreference",
    "description": (
        "Open the C++ reference documentation for a symbol or concept in the "
        "browser the assistant controls. Use this for ANY C++ documentation, "
        "standard-library, or reference request -- for example 'show me "
        "lock_guard', 'open the docs for std::sort', or 'what's the RAII mutex "
        "wrapper'. Always prefer this over searching the web or browsing page by "
        "page for C++ docs; it jumps straight to the right page and is much "
        "faster. Pass the symbol or a short concept you already have in mind "
        "(for example 'lock_guard', 'std::vector', 'scoped mutex'), NOT a URL or "
        "a page path -- the exact page is resolved for you."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A C++ symbol or short concept, such as 'lock_guard', "
                    "'std::sort', or 'scoped mutex wrapper'. Never a URL."
                ),
            }
        },
        "required": ["query"],
    },
}


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _run_open_cppreference(cdp_endpoint, query):
    """Run the async navigation to completion. Never raises."""
    try:
        return asyncio.run(browser_mcp.open_cppreference(cdp_endpoint, query))
    except Exception as exc:  # defensive: degrade, don't crash the server
        logger.warning("open_cppreference failed: %s", exc)
        spoken = (query or "").strip() or "C++"
        return f"Sorry, I couldn't open the {spoken} reference just now."


def handle_message(message, cdp_endpoint):
    """Map one JSON-RPC request to a response dict, or None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")

    # Notifications carry no id and never get a response.
    if "id" not in message:
        return None

    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        version = requested if isinstance(requested, str) and requested else PROTOCOL_VERSION
        return _result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cppreference", "version": "1.0.0"},
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [TOOL_DEFINITION]})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name != "open_cppreference":
            return _error(msg_id, -32602, f"Unknown tool: {name}")
        query = arguments.get("query")
        if not isinstance(query, str):
            query = ""
        text = _run_open_cppreference(cdp_endpoint, query)
        return _result(msg_id, {"content": [{"type": "text", "text": text}]})
    return _error(msg_id, -32601, f"Method not found: {method}")


def serve(cdp_endpoint, stdin=None, stdout=None):
    """Read newline-delimited JSON-RPC from stdin and answer on stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Ignoring non-JSON line on stdin.")
            continue
        try:
            response = handle_message(message, cdp_endpoint)
        except Exception as exc:  # one bad message must not kill the server
            logger.warning("Error handling message: %s", exc)
            response = _error(message.get("id"), -32603, "Internal error")
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="cppreference fast-path MCP server")
    parser.add_argument("--cdp-endpoint", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    # JSON-RPC is UTF-8 and newline-delimited; keep stdio from mangling either.
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except Exception:
            pass
    serve(args.cdp_endpoint)


if __name__ == "__main__":
    main()
