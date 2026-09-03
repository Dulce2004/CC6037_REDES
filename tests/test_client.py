"""Pruebas unitarias del cliente MCP local."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.client import ClientError, PharmacyMCPClient  # noqa: E402
from pharmacy_mcp.jsonrpc import (  # noqa: E402
    INTERNAL_ERROR,
    INVALID_PARAMS,
    ErrorResponse,
    Request,
    Response,
)
from pharmacy_mcp.server import (  # noqa: E402
    PharmacyMCPServer,
    ServerState,
)


class RecordingServer(PharmacyMCPServer):
    """Servidor real que conserva los IDs recibidos para una prueba."""

    def __init__(self) -> None:
        super().__init__()
        self.received_ids: list[object] = []

    def process_request(
        self, request: Request
    ) -> Response | ErrorResponse | None:
        self.received_ids.append(request.to_dict().get("id"))
        return super().process_request(request)


class RespondingNotificationServer(PharmacyMCPServer):
    """Servidor incorrecto que responde a notificaciones para probar el cliente."""

    def process_request(
        self, request: Request
    ) -> Response | ErrorResponse | None:
        response = super().process_request(request)
        if request.is_notification:
            return Response(result={}, id=None)
        return response


class PharmacyMCPClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PharmacyMCPServer()
        self.client = PharmacyMCPClient(self.server)

    def initialize_client(self) -> None:
        response = self.client.initialize()
        self.assertIsInstance(response, Response)

    def assess(self, symptoms: object) -> object:
        return self.client.call_tool(
            "assess_symptoms",
            {"symptoms": symptoms},
        )

    def result_text(self, result: object) -> str:
        return result["content"][0]["text"]

    def test_client_can_initialize(self) -> None:
        response = self.client.initialize()

        self.assertIsInstance(response, Response)
        self.assertTrue(self.client.is_initialized)
        self.assertEqual(self.client.server_info["name"], "Pharmacy MCP Server")
        self.assertIs(self.server.state, ServerState.READY)

    def test_client_lists_server_tools(self) -> None:
        self.initialize_client()

        tools = self.client.list_tools()

        self.assertNotIsInstance(tools, ClientError)
        names = [tool["name"] for tool in tools]
        self.assertIn("assess_symptoms", names)
        self.assertNotIn("classify_symptoms", names)

    def test_client_invokes_assess_symptoms(self) -> None:
        self.initialize_client()

        result = self.assess("Tengo fiebre y tos")

        self.assertNotIsInstance(result, ClientError)
        self.assertIn("Severity:", self.result_text(result))

    def test_client_receives_respiratory_assessment(self) -> None:
        self.initialize_client()

        result = self.assess("Tengo fiebre, tos y dolor de garganta")

        self.assertIn("Category: respiratory", self.result_text(result))

    def test_client_receives_allergy_assessment(self) -> None:
        self.initialize_client()

        result = self.assess(
            "Tengo estornudos, congestión nasal y picazón en los ojos"
        )

        self.assertIn("Category: allergy", self.result_text(result))

    def test_client_receives_gastrointestinal_assessment(self) -> None:
        self.initialize_client()

        result = self.assess("Tengo náuseas, diarrea y dolor abdominal")

        self.assertIn("Category: gastrointestinal", self.result_text(result))

    def test_client_receives_unclassified_result(self) -> None:
        self.initialize_client()

        result = self.assess("Tengo fiebre y picazón en los ojos")

        self.assertIn("Category: unclassified", self.result_text(result))

    def test_client_handles_invalid_assessment_arguments(self) -> None:
        self.initialize_client()

        result = self.assess(["fever"])

        self.assertIsInstance(result, ClientError)
        self.assertEqual(result.code, INVALID_PARAMS)
        self.assertIn("non-empty string", result.message)

    def test_tool_cannot_run_before_initialize(self) -> None:
        result = self.assess("Tengo fiebre y tos")

        self.assertIsInstance(result, ClientError)
        self.assertEqual(result.message, "Client has not been initialized.")

    def test_request_ids_increment_deterministically(self) -> None:
        server = RecordingServer()
        client = PharmacyMCPClient(server)

        client.initialize()
        client.list_tools()
        client.call_tool("assess_symptoms", {"symptoms": "Tengo fiebre y tos"})

        self.assertEqual(server.received_ids, [1, None, 2, 3])

    def test_initialized_notification_does_not_consume_request_id(self) -> None:
        server = RecordingServer()
        client = PharmacyMCPClient(server)

        initialization = client.initialize()
        tools = client.list_tools()

        self.assertIsInstance(initialization, Response)
        self.assertNotIsInstance(tools, ClientError)
        self.assertEqual(server.received_ids, [1, None, 2])

    def test_client_rejects_a_response_to_its_notification(self) -> None:
        client = PharmacyMCPClient(RespondingNotificationServer())

        result = client.initialize()

        self.assertIsInstance(result, ClientError)
        self.assertEqual(result.code, INTERNAL_ERROR)
        self.assertIn("response to a notification", result.message)
        self.assertFalse(client.is_initialized)


if __name__ == "__main__":
    unittest.main()
