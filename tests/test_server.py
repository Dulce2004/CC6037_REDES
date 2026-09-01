"""Pruebas del núcleo local del servidor MCP manual."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Permite ejecutar el comando de descubrimiento desde un repositorio con layout src/.
SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.jsonrpc import (  # noqa: E402
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    ErrorResponse,
    Request,
    Response,
)
from pharmacy_mcp.server import (  # noqa: E402
    SUPPORTED_PROTOCOL_VERSION,
    PharmacyMCPServer,
)


def initialize_params() -> dict[str, object]:
    return {
        "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "Server Test Client", "version": "1.0.0"},
    }


class PharmacyMCPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PharmacyMCPServer()

    def make_server_ready(self) -> None:
        initialization = self.server.process_request(
            Request(method="initialize", params=initialize_params(), id=100)
        )
        self.assertIsInstance(initialization, Response)
        notification_result = self.server.process_request(
            Request(method="notifications/initialized", params={})
        )
        self.assertIsNone(notification_result)

    @staticmethod
    def echo_handler(arguments: dict[str, object]) -> dict[str, object]:
        text = arguments.get("text")
        if not isinstance(text, str):
            raise ValueError("Echo requires a text string.")
        return {"content": [{"type": "text", "text": text}]}

    def register_echo(self) -> None:
        self.server.register_tool(
            name="echo",
            description="Returns the provided text.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=self.echo_handler,
        )

    def test_initialize_returns_valid_response(self) -> None:
        response = self.server.process_request(
            Request(method="initialize", params=initialize_params(), id=1)
        )

        self.assertIsInstance(response, Response)
        self.assertEqual(response.id, 1)
        self.assertEqual(response.jsonrpc, "2.0")

    def test_initialize_includes_server_information(self) -> None:
        response = self.server.process_request(
            Request(method="initialize", params=initialize_params(), id=1)
        )

        self.assertEqual(
            response.result["serverInfo"],
            {"name": "Pharmacy MCP Server", "version": "0.1.0"},
        )
        self.assertIn("protocolVersion", response.result)
        self.assertIn("capabilities", response.result)

    def test_tools_list_returns_list(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/list", params={}, id=2)
        )

        self.assertIsInstance(response, Response)
        self.assertIsInstance(response.result["tools"], list)

    def test_tools_list_includes_default_pharmacy_tool(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/list", params={}, id=2)
        )

        tool_names = [tool["name"] for tool in response.result["tools"]]
        self.assertIn("classify_symptoms", tool_names)

    def test_unknown_tool_returns_invalid_params_error(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "missing", "arguments": {}},
                id=3,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("Tool not found", response.error.message)

    def test_unknown_method_returns_method_not_found_error(self) -> None:
        response = self.server.process_request(
            Request(method="unknown/method", params={}, id=4)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, METHOD_NOT_FOUND)

    def test_register_fictitious_tool(self) -> None:
        self.register_echo()
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/list", params={}, id=5)
        )

        tool_names = [tool["name"] for tool in response.result["tools"]]
        self.assertIn("echo", tool_names)

    def test_tools_list_shows_registered_tool(self) -> None:
        self.register_echo()
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/list", params={}, id=6)
        )
        definition = next(
            tool for tool in response.result["tools"] if tool["name"] == "echo"
        )

        self.assertEqual(definition["name"], "echo")
        self.assertEqual(definition["description"], "Returns the provided text.")
        self.assertIn("inputSchema", definition)
        self.assertNotIn("handler", definition)

    def test_tools_call_executes_fictitious_tool(self) -> None:
        self.register_echo()
        self.make_server_ready()

        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "echo", "arguments": {"text": "hello"}},
                id=7,
            )
        )

        self.assertIsInstance(response, Response)
        self.assertEqual(
            response.result,
            {"content": [{"type": "text", "text": "hello"}]},
        )

    def test_handler_exception_becomes_internal_error_response(self) -> None:
        def failing_handler(arguments: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("Technical failure")

        self.server.register_tool(
            name="failure",
            description="Always fails for testing.",
            input_schema={"type": "object"},
            handler=failing_handler,
        )
        self.make_server_ready()

        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "failure", "arguments": {}},
                id=8,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INTERNAL_ERROR)
        self.assertEqual(response.error.message, "Internal error")

    def test_tools_call_requires_name(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/call", params={"arguments": {}}, id=9)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_tools_call_rejects_non_object_arguments(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "echo", "arguments": []},
                id=10,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_tools_call_requires_arguments_declared_by_schema(self) -> None:
        self.register_echo()
        self.make_server_ready()

        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "echo", "arguments": {}},
                id=11,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("text", response.error.message)


if __name__ == "__main__":
    unittest.main()
