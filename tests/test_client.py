"""Pruebas unitarias del cliente MCP local."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.client import ClientError, PharmacyMCPClient  # noqa: E402
from pharmacy_mcp.jsonrpc import (  # noqa: E402
    INVALID_PARAMS,
    ErrorResponse,
    Request,
    Response,
)
from pharmacy_mcp.server import PharmacyMCPServer  # noqa: E402


class RecordingServer(PharmacyMCPServer):
    """Servidor real que conserva los IDs recibidos para una prueba."""

    def __init__(self) -> None:
        super().__init__()
        self.received_ids: list[object] = []

    def process_request(self, request: Request) -> Response | ErrorResponse:
        self.received_ids.append(request.to_dict().get("id"))
        return super().process_request(request)


class PharmacyMCPClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PharmacyMCPServer()
        self.client = PharmacyMCPClient(self.server)

    def initialize_client(self) -> None:
        response = self.client.initialize()
        self.assertIsInstance(response, Response)

    def classify(self, symptoms: list[str]) -> object:
        return self.client.call_tool(
            "classify_symptoms",
            {"symptoms": symptoms},
        )

    def result_text(self, result: object) -> str:
        return result["content"][0]["text"]

    def test_client_can_initialize(self) -> None:
        response = self.client.initialize()

        self.assertIsInstance(response, Response)
        self.assertTrue(self.client.is_initialized)
        self.assertEqual(self.client.server_info["name"], "Pharmacy MCP Server")

    def test_client_lists_server_tools(self) -> None:
        self.initialize_client()

        tools = self.client.list_tools()

        self.assertNotIsInstance(tools, ClientError)
        self.assertIn("classify_symptoms", [tool["name"] for tool in tools])

    def test_client_invokes_classify_symptoms(self) -> None:
        self.initialize_client()

        result = self.classify(["fever", "cough"])

        self.assertNotIsInstance(result, ClientError)
        self.assertIn("Classification:", self.result_text(result))

    def test_client_receives_respiratory_classification(self) -> None:
        self.initialize_client()

        result = self.classify(["fever", "cough", "sore_throat"])

        self.assertIn("Classification: respiratory", self.result_text(result))

    def test_client_receives_allergy_classification(self) -> None:
        self.initialize_client()

        result = self.classify(
            ["sneezing", "nasal_congestion", "itchy_eyes"]
        )

        self.assertIn("Classification: allergy", self.result_text(result))

    def test_client_receives_gastrointestinal_classification(self) -> None:
        self.initialize_client()

        result = self.classify(["nausea", "diarrhea", "abdominal_pain"])

        self.assertIn("Classification: gastrointestinal", self.result_text(result))

    def test_client_receives_unclassified_result(self) -> None:
        self.initialize_client()

        result = self.classify(["fever", "itchy_eyes"])

        self.assertIn("Classification: unclassified", self.result_text(result))

    def test_client_handles_unknown_symptom_error(self) -> None:
        self.initialize_client()

        result = self.classify(["fever", "magic_symptom"])

        self.assertIsInstance(result, ClientError)
        self.assertEqual(result.code, INVALID_PARAMS)
        self.assertIn("magic_symptom", result.message)

    def test_tool_cannot_run_before_initialize(self) -> None:
        result = self.classify(["fever", "cough"])

        self.assertIsInstance(result, ClientError)
        self.assertEqual(result.message, "Client has not been initialized.")

    def test_request_ids_increment_deterministically(self) -> None:
        server = RecordingServer()
        client = PharmacyMCPClient(server)

        client.initialize()
        client.list_tools()
        client.call_tool("classify_symptoms", {"symptoms": ["fever", "cough"]})

        self.assertEqual(server.received_ids, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
