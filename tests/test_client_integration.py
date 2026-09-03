"""Pruebas de extremo a extremo del cliente, servidor y herramienta."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.client import ClientError, PharmacyMCPClient  # noqa: E402
from pharmacy_mcp.jsonrpc import INVALID_PARAMS, Response  # noqa: E402
from pharmacy_mcp.server import PharmacyMCPServer  # noqa: E402


class ClientEndToEndTests(unittest.TestCase):
    def test_initialize_list_and_assess_end_to_end(self) -> None:
        client = PharmacyMCPClient(PharmacyMCPServer())

        initialization = client.initialize()
        tools = client.list_tools()
        result = client.call_tool(
            "assess_symptoms",
            {
                "symptoms": "Tengo fiebre, tos y dolor de garganta",
                "age": 24,
                "duration_days": 1,
            },
        )

        self.assertIsInstance(initialization, Response)
        names = [tool["name"] for tool in tools]
        self.assertIn("assess_symptoms", names)
        self.assertNotIn("classify_symptoms", names)
        self.assertIn("Category: respiratory", result["content"][0]["text"])

    def test_invalid_assessment_error_end_to_end(self) -> None:
        client = PharmacyMCPClient(PharmacyMCPServer())
        client.initialize()

        result = client.call_tool(
            "assess_symptoms",
            {"symptoms": "Tengo tos", "age": "24"},
        )

        self.assertIsInstance(result, ClientError)
        self.assertEqual(result.code, INVALID_PARAMS)
        self.assertEqual(result.message, "'age' must be an integer.")

    def test_interaction_check_end_to_end(self) -> None:
        client = PharmacyMCPClient(PharmacyMCPServer())
        client.initialize()

        result = client.call_tool(
            "check_interactions",
            {
                "medication_sku": "MED-ANA-002",
                "current_medications": ["MED-GAS-001"],
                "allergies": [],
            },
        )

        self.assertNotIsInstance(result, ClientError)
        self.assertEqual(result["structuredContent"]["alert_count"], 1)


if __name__ == "__main__":
    unittest.main()
