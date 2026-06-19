from __future__ import annotations

import unittest

import browser_mcp
import cppreference_mcp

ENDPOINT = "http://127.0.0.1:9999"


class HandshakeTests(unittest.TestCase):
    def test_initialize_echoes_protocol_and_advertises_tools(self) -> None:
        resp = cppreference_mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            ENDPOINT,
        )
        self.assertEqual(resp["id"], 1)
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "cppreference")

    def test_initialized_notification_gets_no_response(self) -> None:
        resp = cppreference_mcp.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, ENDPOINT
        )
        self.assertIsNone(resp)


class ToolsTests(unittest.TestCase):
    def test_tools_list_exposes_open_cppreference(self) -> None:
        resp = cppreference_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ENDPOINT
        )
        tools = resp["result"]["tools"]
        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool["name"], "open_cppreference")
        self.assertEqual(tool["inputSchema"]["required"], ["query"])
        # The description must steer the model to pass a symbol, not a URL.
        self.assertIn("NOT a URL", tool["description"])

    def test_tools_call_navigates_and_returns_confirmation(self) -> None:
        captured = {}

        async def fake_open(endpoint, query):
            captured["endpoint"] = endpoint
            captured["query"] = query
            return f"Opening the {query} reference."

        original = browser_mcp.open_cppreference
        browser_mcp.open_cppreference = fake_open
        try:
            resp = cppreference_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "open_cppreference",
                        "arguments": {"query": "lock_guard"},
                    },
                },
                ENDPOINT,
            )
        finally:
            browser_mcp.open_cppreference = original

        self.assertEqual(captured, {"endpoint": ENDPOINT, "query": "lock_guard"})
        text = resp["result"]["content"][0]["text"]
        self.assertEqual(text, "Opening the lock_guard reference.")

    def test_tools_call_unknown_tool_is_an_error(self) -> None:
        resp = cppreference_mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "do_something_else", "arguments": {}},
            },
            ENDPOINT,
        )
        self.assertIn("error", resp)

    def test_tools_call_never_raises_when_navigation_fails(self) -> None:
        async def boom(endpoint, query):
            raise RuntimeError("CDP unreachable")

        original = browser_mcp.open_cppreference
        browser_mcp.open_cppreference = boom
        try:
            resp = cppreference_mcp.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "open_cppreference",
                        "arguments": {"query": "std::sort"},
                    },
                },
                ENDPOINT,
            )
        finally:
            browser_mcp.open_cppreference = original

        text = resp["result"]["content"][0]["text"]
        self.assertIn("std::sort", text)


class UnknownMethodTests(unittest.TestCase):
    def test_unknown_method_returns_method_not_found(self) -> None:
        resp = cppreference_mcp.handle_message(
            {"jsonrpc": "2.0", "id": 6, "method": "resources/list"}, ENDPOINT
        )
        self.assertEqual(resp["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
