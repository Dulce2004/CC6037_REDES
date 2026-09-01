"""Pruebas de integración entre la herramienta de farmacia y el servidor MCP."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.jsonrpc import (  # noqa: E402
    INVALID_PARAMS,
    ErrorResponse,
    Request,
    Response,
)
from pharmacy_mcp.server import (  # noqa: E402
    SUPPORTED_PROTOCOL_VERSION,
    PharmacyMCPServer,
)


class PharmacyToolIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PharmacyMCPServer()
        initialization = self.server.process_request(
            Request(
                method="initialize",
                params={
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "Pharmacy Tool Test Client",
                        "version": "1.0.0",
                    },
                },
                id=100,
            )
        )
        self.assertIsInstance(initialization, Response)
        notification_result = self.server.process_request(
            Request(method="notifications/initialized", params={})
        )
        self.assertIsNone(notification_result)

    def call_classifier(self, symptoms: object, request_id: int = 1) -> object:
        return self.server.process_request(
            Request(
                method="tools/call",
                params={
                    "name": "classify_symptoms",
                    "arguments": {"symptoms": symptoms},
                },
                id=request_id,
            )
        )

    def response_text(self, response: Response) -> str:
        return response.result["content"][0]["text"]

    def test_tools_list_includes_classify_symptoms(self) -> None:
        response = self.server.process_request(
            Request(method="tools/list", params={}, id=1)
        )
        definitions = response.result["tools"]
        tool = next(
            item for item in definitions if item["name"] == "classify_symptoms"
        )

        self.assertIn("description", tool)
        self.assertIn("inputSchema", tool)
        self.assertIn("symptoms", tool["inputSchema"]["required"])
        self.assertNotIn("handler", tool)

    def test_tools_call_classifies_respiratory_case(self) -> None:
        response = self.call_classifier(["fever", "cough", "sore_throat"])

        self.assertIsInstance(response, Response)
        self.assertIn("Classification: respiratory", self.response_text(response))

    def test_tools_call_classifies_allergy_case(self) -> None:
        response = self.call_classifier(
            ["sneezing", "nasal_congestion", "itchy_eyes"]
        )

        self.assertIsInstance(response, Response)
        self.assertIn("Classification: allergy", self.response_text(response))

    def test_tools_call_classifies_gastrointestinal_case(self) -> None:
        response = self.call_classifier(
            ["nausea", "diarrhea", "abdominal_pain"]
        )

        self.assertIsInstance(response, Response)
        self.assertIn(
            "Classification: gastrointestinal", self.response_text(response)
        )

    def test_tools_call_returns_unclassified_case(self) -> None:
        response = self.call_classifier(["fever", "itchy_eyes"])

        self.assertIsInstance(response, Response)
        self.assertIn("Classification: unclassified", self.response_text(response))

    def test_tools_call_rejects_unknown_symptom(self) -> None:
        response = self.call_classifier(["fever", "magic_symptom"])

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("magic_symptom", response.error.message)

    def test_tools_call_rejects_invalid_arguments(self) -> None:
        response = self.call_classifier("fever")

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_tools_call_rejects_missing_symptoms(self) -> None:
        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": "classify_symptoms", "arguments": {}},
                id=2,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
