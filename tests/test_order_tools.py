"""Pruebas MCP de creación y consulta de órdenes simuladas."""

from __future__ import annotations

import json
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


class PharmacyOrderToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PharmacyMCPServer()
        initialized = self.server.process_request(
            Request(
                method="initialize",
                params={
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "Order Tests", "version": "1.0"},
                },
                id=1,
            )
        )
        self.assertIsInstance(initialized, Response)
        self.assertIsNone(
            self.server.process_request(
                Request(method="notifications/initialized", params={})
            )
        )

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        request_id: int = 2,
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

    def stock_quantity(self, sku: str, branch_id: str) -> int:
        response = self.call_tool(
            "check_stock",
            {"sku": sku, "branch_id": branch_id},
            request_id=90,
        )
        self.assertIsInstance(response, Response)
        return response.result["structuredContent"]["stock"][0]["quantity"]

    def assert_tool_error(
        self,
        response: Response | ErrorResponse,
        message_fragment: str,
    ) -> None:
        self.assertIsInstance(response, Response)
        self.assertTrue(response.result["isError"])
        self.assertNotIn("error", response.to_dict())
        self.assertIn(
            message_fragment,
            response.result["content"][0]["text"],
        )

    def test_create_order_then_status_and_stock_form_one_workflow(self) -> None:
        self.assertEqual(self.stock_quantity("MED-ANA-001", "zona-5"), 25)

        created = self.call_tool(
            "create_order",
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": 2}],
            },
        )

        self.assertIsInstance(created, Response)
        order = created.result["structuredContent"]["order"]
        self.assertEqual(order["status"], "created")
        self.assertTrue(order["order_id"].startswith("ORD-"))
        self.assertEqual(order["total"], {"amount": "37.90", "currency": "GTQ"})
        self.assertIsInstance(order["total"]["amount"], str)
        self.assertEqual(self.stock_quantity("MED-ANA-001", "zona-5"), 23)

        status = self.call_tool(
            "get_order_status",
            {"order_id": order["order_id"]},
            request_id=3,
        )
        self.assertIsInstance(status, Response)
        self.assertEqual(status.result["structuredContent"]["order"], order)

    def test_multi_item_order_uses_exact_catalog_prices(self) -> None:
        response = self.call_tool(
            "create_order",
            {
                "branch_id": "zona-5",
                "items": [
                    {"sku": "MED-ANA-001", "quantity": 2},
                    {"sku": "MED-ANA-002", "quantity": 1},
                ],
            },
        )

        self.assertIsInstance(response, Response)
        order = response.result["structuredContent"]["order"]
        self.assertEqual(order["total"], {"amount": "62.65", "currency": "GTQ"})
        self.assertEqual(
            [item["line_total"]["amount"] for item in order["items"]],
            ["37.90", "24.75"],
        )

    def test_missing_prescription_is_tool_error_and_does_not_change_stock(self) -> None:
        before = self.stock_quantity("MED-RX-001", "zona-5")
        response = self.call_tool(
            "create_order",
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-RX-001", "quantity": 1}],
            },
        )

        self.assert_tool_error(response, "prescription identifier is required")
        self.assertEqual(self.stock_quantity("MED-RX-001", "zona-5"), before)

    def test_simulated_prescription_reference_allows_rx_order(self) -> None:
        prescription_id = "RX-ACADEMIC-2026"
        response = self.call_tool(
            "create_order",
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-RX-001", "quantity": 1}],
                "prescription_id": prescription_id,
            },
        )

        self.assertIsInstance(response, Response)
        order = response.result["structuredContent"]["order"]
        self.assertTrue(order["prescription_required"])
        self.assertTrue(order["prescription_reference_provided"])
        self.assertEqual(
            order["prescription_validation_scope"], "format_only_simulation"
        )
        self.assertNotIn(prescription_id, json.dumps(response.to_dict()))
        self.assertIn("not a real purchase", response.result["content"][0]["text"])

    def test_failed_multi_item_order_is_atomic(self) -> None:
        before = self.stock_quantity("MED-ANA-001", "zona-5")
        response = self.call_tool(
            "create_order",
            {
                "branch_id": "zona-5",
                "items": [
                    {"sku": "MED-ANA-001", "quantity": 2},
                    {"sku": "MED-ANT-002", "quantity": 1},
                ],
            },
        )

        self.assert_tool_error(response, "Insufficient stock")
        self.assertEqual(self.stock_quantity("MED-ANA-001", "zona-5"), before)

    def test_well_formed_domain_failures_are_successful_tool_errors(self) -> None:
        cases = (
            (
                {
                    "branch_id": "zona-10",
                    "items": [{"sku": "MED-ANA-001", "quantity": 1}],
                },
                "Unknown branch",
            ),
            (
                {
                    "branch_id": "zona-5",
                    "items": [{"sku": "MED-MISSING", "quantity": 1}],
                },
                "Unknown medication SKU",
            ),
            (
                {
                    "branch_id": "zona-5",
                    "items": [{"sku": "MED-ANA-001", "quantity": 999}],
                },
                "Insufficient stock",
            ),
        )

        for index, (arguments, message) in enumerate(cases, start=10):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "create_order", arguments, request_id=index
                )
                self.assert_tool_error(response, message)

    def test_malformed_create_order_arguments_are_invalid_params(self) -> None:
        cases = (
            {},
            {"branch_id": "zona-5"},
            {"branch_id": "zona-5", "items": []},
            {"branch_id": "zona-5", "items": ["MED-ANA-001"]},
            {"branch_id": "zona-5", "items": [{}]},
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": True}],
            },
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": 0}],
            },
            {
                "branch_id": "zona-5",
                "items": [
                    {"sku": "MED-ANA-001", "quantity": 1},
                    {"sku": "med-ana-001", "quantity": 1},
                ],
            },
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": 1, "x": 1}],
            },
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": 1}],
                "prescription_id": "NOT-RX",
            },
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": 1}],
                "extra": True,
            },
        )

        for index, arguments in enumerate(cases, start=20):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "create_order", arguments, request_id=index
                )
                self.assertIsInstance(response, ErrorResponse)
                self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_get_order_status_distinguishes_shape_and_lookup_errors(self) -> None:
        malformed = (
            {},
            {"order_id": ""},
            {"order_id": 123},
            {"order_id": "invalid order"},
            {"order_id": "ORD-MISSING", "extra": True},
        )
        for index, arguments in enumerate(malformed, start=40):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "get_order_status", arguments, request_id=index
                )
                self.assertIsInstance(response, ErrorResponse)
                self.assertEqual(response.error.code, INVALID_PARAMS)

        unknown = self.call_tool(
            "get_order_status",
            {"order_id": "ORD-MISSING"},
            request_id=50,
        )
        self.assert_tool_error(unknown, "Unknown order ID")


if __name__ == "__main__":
    unittest.main()
