"""Pruebas MCP de evaluación de síntomas e interacciones simuladas."""

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

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        request_id: int = 1,
    ) -> Response | ErrorResponse:
        response = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": name, "arguments": arguments},
                id=request_id,
            )
        )
        self.assertIsNotNone(response)
        return response

    def response_text(self, response: Response) -> str:
        return response.result["content"][0]["text"]

    def test_tools_list_publishes_assessment_and_not_classifier(self) -> None:
        response = self.server.process_request(
            Request(method="tools/list", params={}, id=1)
        )

        self.assertIsInstance(response, Response)
        names = [tool["name"] for tool in response.result["tools"]]
        self.assertIn("assess_symptoms", names)
        self.assertNotIn("classify_symptoms", names)

    def test_assess_symptoms_accepts_natural_language(self) -> None:
        response = self.call_tool(
            "assess_symptoms",
            {
                "symptoms": "Tengo fiebre y dolor de garganta desde ayer",
                "age": 24,
                "duration_days": 1,
            },
        )

        self.assertIsInstance(response, Response)
        assessment = response.result["structuredContent"]
        self.assertEqual(assessment["severity"], "mild")
        self.assertEqual(assessment["category"], "respiratory")
        self.assertIn("not a diagnosis", self.response_text(response))

    def test_assess_symptoms_prioritizes_urgent_red_flag(self) -> None:
        response = self.call_tool(
            "assess_symptoms",
            {
                "symptoms": "Tengo tos, fiebre y dificultad para respirar",
                "age": 40,
                "duration_days": 1,
            },
        )

        self.assertIsInstance(response, Response)
        assessment = response.result["structuredContent"]
        self.assertEqual(assessment["severity"], "urgent")
        self.assertFalse(assessment["medication_purchase_recommended"])
        self.assertIn("Seek urgent medical care", self.response_text(response))
        self.assertIn("Do not use this result", self.response_text(response))

    def test_assess_symptoms_rejects_invalid_arguments(self) -> None:
        cases = (
            {},
            {"symptoms": ["fever"]},
            {"symptoms": ""},
            {"symptoms": "Tengo tos", "age": True},
            {"symptoms": "Tengo tos", "duration_days": -1},
            {"symptoms": "Tengo tos", "extra": "value"},
        )

        for index, arguments in enumerate(cases, start=1):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "assess_symptoms",
                    arguments,
                    request_id=index,
                )
                self.assertIsInstance(response, ErrorResponse)
                self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_removed_classifier_name_is_not_callable(self) -> None:
        response = self.call_tool(
            "classify_symptoms",
            {"symptoms": ["fever", "cough"]},
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("Tool not found", response.error.message)

    def test_check_interactions_reports_medication_and_allergy_alerts(self) -> None:
        response = self.call_tool(
            "check_interactions",
            {
                "medication_sku": "MED-ANA-002",
                "current_medications": ["MED-GAS-001"],
                "allergies": ["AINEs"],
            },
        )

        self.assertIsInstance(response, Response)
        result = response.result["structuredContent"]
        self.assertEqual(result["alert_count"], 2)
        self.assertEqual(result["highest_severity"], "high")
        self.assertFalse(result["exhaustive"])
        self.assertFalse(result["safety_established"])
        self.assertIn("does not guarantee safety", self.response_text(response))

    def test_check_interactions_never_declares_no_findings_safe(self) -> None:
        response = self.call_tool(
            "check_interactions",
            {
                "medication_sku": "MED-ANT-001",
                "current_medications": [],
                "allergies": [],
            },
        )

        self.assertIsInstance(response, Response)
        result = response.result["structuredContent"]
        self.assertEqual(result["alert_count"], 0)
        self.assertEqual(result["highest_severity"], "none")
        self.assertIn("does not establish", self.response_text(response))

    def test_check_interactions_does_not_recommend_prescription_item(self) -> None:
        response = self.call_tool(
            "check_interactions",
            {"medication_sku": "MED-RX-001"},
        )

        self.assertIsInstance(response, Response)
        self.assertIn("requires a prescription", self.response_text(response))
        self.assertIn("does not recommend", self.response_text(response))

    def test_check_interactions_returns_unknown_skus_as_tool_errors(self) -> None:
        cases = (
            {"medication_sku": "MED-MISSING"},
            {
                "medication_sku": "MED-ANA-001",
                "current_medications": ["MED-MISSING"],
            },
        )

        for request_id, arguments in enumerate(cases, start=8):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "check_interactions",
                    arguments,
                    request_id=request_id,
                )

                self.assertIsInstance(response, Response)
                self.assertEqual(response.id, request_id)
                self.assertTrue(response.result["isError"])
                self.assertIn(
                    "Unknown medication SKU",
                    self.response_text(response),
                )
                self.assertIn("not exhaustive", self.response_text(response))

    def test_check_interactions_rejects_malformed_arguments(self) -> None:
        cases = (
            {},
            {"medication_sku": "bad sku"},
            {"medication_sku": "MED-ANA-001", "current_medications": None},
            {"medication_sku": "MED-ANA-001", "allergies": [123]},
            {
                "medication_sku": "MED-ANA-001",
                "current_medications": ["MED-ANA-001"],
            },
            {"medication_sku": "MED-ANA-001", "extra": True},
        )

        for index, arguments in enumerate(cases, start=20):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "check_interactions",
                    arguments,
                    request_id=index,
                )
                self.assertIsInstance(response, ErrorResponse)
                self.assertEqual(response.error.code, INVALID_PARAMS)


if __name__ == "__main__":
    unittest.main()
