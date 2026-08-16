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
    def test_initialize_list_and_classify_end_to_end(self) -> None:
        client = PharmacyMCPClient(PharmacyMCPServer())

        initialization = client.initialize()
        tools = client.list_tools()
        result = client.call_tool(
            "classify_symptoms",
            {"symptoms": ["fever", "cough", "sore_throat"]},
        )

        self.assertIsInstance(initialization, Response)
        self.assertIn("classify_symptoms", [tool["name"] for tool in tools])
        self.assertIn("Classification: respiratory", result["content"][0]["text"])

    def test_unknown_symptom_error_end_to_end(self) -> None:
        client = PharmacyMCPClient(PharmacyMCPServer())
        client.initialize()

        result = client.call_tool(
            "classify_symptoms",
            {"symptoms": ["fever", "magic_symptom"]},
        )

        self.assertIsInstance(result, ClientError)
        self.assertEqual(result.code, INVALID_PARAMS)
        self.assertEqual(result.message, "Unknown symptom: 'magic_symptom'.")


if __name__ == "__main__":
    unittest.main()
