"""Pruebas del lifecycle MCP inicial y las notificaciones JSON-RPC."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.jsonrpc import (  # noqa: E402
    INVALID_PARAMS,
    INVALID_REQUEST,
    SERVER_NOT_INITIALIZED,
    ErrorResponse,
    Request,
    Response,
)
from pharmacy_mcp.server import (  # noqa: E402
    SUPPORTED_PROTOCOL_VERSION,
    PharmacyMCPServer,
    ServerState,
)


def valid_initialize_params() -> dict[str, object]:
    return {
        "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "Lifecycle Test Client", "version": "1.0.0"},
    }


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PharmacyMCPServer()

    def initialize_server(self, request_id: object = 1) -> Response:
        response = self.server.process_request(
            Request(
                method="initialize",
                params=valid_initialize_params(),
                id=request_id,
            )
        )
        self.assertIsInstance(response, Response)
        return response

    def make_server_ready(self) -> None:
        self.initialize_server()
        response = self.server.process_request(
            Request(method="notifications/initialized", params={})
        )
        self.assertIsNone(response)
        self.assertIs(self.server.state, ServerState.READY)

    def test_initial_state_is_uninitialized(self) -> None:
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_valid_initialize_transitions_to_initializing(self) -> None:
        response = self.initialize_server(request_id="initialize-1")

        self.assertEqual(response.id, "initialize-1")
        self.assertEqual(response.result["protocolVersion"], SUPPORTED_PROTOCOL_VERSION)
        self.assertEqual(
            response.result["serverInfo"],
            {"name": "Pharmacy MCP Server", "version": "0.1.0"},
        )
        self.assertEqual(
            response.result["capabilities"],
            {"tools": {"listChanged": False}},
        )
        self.assertIs(self.server.state, ServerState.INITIALIZING)

    def test_null_id_is_processed_as_a_request(self) -> None:
        response = self.initialize_server(request_id=None)

        self.assertIsInstance(response, Response)
        self.assertIsNone(response.id)
        self.assertIs(self.server.state, ServerState.INITIALIZING)

    def test_initialized_notification_transitions_to_ready_without_response(self) -> None:
        self.initialize_server()

        response = self.server.process_request(
            Request(method="notifications/initialized", params={})
        )

        self.assertIsNone(response)
        self.assertIs(self.server.state, ServerState.READY)

    def test_initialize_requires_protocol_version(self) -> None:
        params = valid_initialize_params()
        del params["protocolVersion"]

        response = self.server.process_request(
            Request(method="initialize", params=params, id=1)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("protocolVersion", response.error.message)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_initialize_requires_capabilities(self) -> None:
        params = valid_initialize_params()
        del params["capabilities"]

        response = self.server.process_request(
            Request(method="initialize", params=params, id=1)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("capabilities", response.error.message)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_initialize_requires_client_info(self) -> None:
        params = valid_initialize_params()
        del params["clientInfo"]

        response = self.server.process_request(
            Request(method="initialize", params=params, id=1)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("clientInfo", response.error.message)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_initialize_requires_client_name(self) -> None:
        params = valid_initialize_params()
        params["clientInfo"] = {"version": "1.0.0"}

        response = self.server.process_request(
            Request(method="initialize", params=params, id=1)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("clientInfo.name", response.error.message)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_initialize_requires_client_version(self) -> None:
        params = valid_initialize_params()
        params["clientInfo"] = {"name": "Lifecycle Test Client"}

        response = self.server.process_request(
            Request(method="initialize", params=params, id=1)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("clientInfo.version", response.error.message)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_unsupported_protocol_version_does_not_change_state(self) -> None:
        params = valid_initialize_params()
        params["protocolVersion"] = "2024-11-05"

        response = self.server.process_request(
            Request(method="initialize", params=params, id=1)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("Unsupported protocol version", response.error.message)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_second_initialize_is_rejected_while_initializing(self) -> None:
        self.initialize_server()

        response = self.server.process_request(
            Request(method="initialize", params=valid_initialize_params(), id=2)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_REQUEST)
        self.assertIs(self.server.state, ServerState.INITIALIZING)

    def test_second_initialize_is_rejected_while_ready(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="initialize", params=valid_initialize_params(), id=2)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_REQUEST)
        self.assertIs(self.server.state, ServerState.READY)

    def test_tools_list_is_rejected_before_initialize(self) -> None:
        response = self.server.process_request(
            Request(method="tools/list", params={}, id=1)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, SERVER_NOT_INITIALIZED)

    def test_tools_list_is_rejected_while_initializing(self) -> None:
        self.initialize_server()

        response = self.server.process_request(
            Request(method="tools/list", params={}, id=2)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, SERVER_NOT_INITIALIZED)
        self.assertIs(self.server.state, ServerState.INITIALIZING)

    def test_tools_call_is_rejected_before_ready(self) -> None:
        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "assess_symptoms", "arguments": {}},
                id=1,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, SERVER_NOT_INITIALIZED)

    def test_tools_call_is_rejected_while_initializing(self) -> None:
        self.initialize_server()

        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "assess_symptoms", "arguments": {}},
                id=2,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, SERVER_NOT_INITIALIZED)
        self.assertIs(self.server.state, ServerState.INITIALIZING)

    def test_valid_notification_never_returns_a_response(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/list", params={})
        )

        self.assertIsNone(response)

    def test_unknown_method_notification_never_returns_a_response(self) -> None:
        response = self.server.process_request(
            Request(method="unknown/notification", params={})
        )

        self.assertIsNone(response)

    def test_invalid_params_notification_never_returns_a_response(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/call", params=[])
        )

        self.assertIsNone(response)

    def test_handler_exception_notification_never_returns_a_response(self) -> None:
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
            )
        )

        self.assertIsNone(response)

    def test_initialized_notification_out_of_order_is_ignored(self) -> None:
        response = self.server.process_request(
            Request(method="notifications/initialized", params={})
        )

        self.assertIsNone(response)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_repeated_initialized_notification_is_ignored_when_ready(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="notifications/initialized", params={})
        )

        self.assertIsNone(response)
        self.assertIs(self.server.state, ServerState.READY)

    def test_initialize_notification_is_ignored_without_state_change(self) -> None:
        response = self.server.process_request(
            Request(method="initialize", params=valid_initialize_params())
        )

        self.assertIsNone(response)
        self.assertIs(self.server.state, ServerState.UNINITIALIZED)

    def test_initialized_message_with_id_is_rejected(self) -> None:
        self.initialize_server()

        response = self.server.process_request(
            Request(method="notifications/initialized", params={}, id=2)
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_REQUEST)
        self.assertIs(self.server.state, ServerState.INITIALIZING)

    def test_normal_request_preserves_its_id(self) -> None:
        self.make_server_ready()

        response = self.server.process_request(
            Request(method="tools/list", params={}, id="tools-42")
        )

        self.assertIsInstance(response, Response)
        self.assertEqual(response.id, "tools-42")


if __name__ == "__main__":
    unittest.main()
