"""Pruebas MCP de consulta para catálogo e inventario de farmacia."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.jsonrpc import (  # noqa: E402
    INVALID_PARAMS,
    SERVER_NOT_INITIALIZED,
    ErrorResponse,
    Request,
    Response,
)
from pharmacy_mcp.server import (  # noqa: E402
    SUPPORTED_PROTOCOL_VERSION,
    PharmacyMCPServer,
)


class PharmacyQueryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PharmacyMCPServer()
        initialization = self.server.process_request(
            Request(
                method="initialize",
                params={
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "Pharmacy Query Test Client",
                        "version": "1.0.0",
                    },
                },
                id=100,
            )
        )
        self.assertIsInstance(initialization, Response)
        notification = self.server.process_request(
            Request(method="notifications/initialized", params={})
        )
        self.assertIsNone(notification)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        request_id: int = 1,
    ) -> Response | ErrorResponse:
        result = self.server.process_request(
            Request(
                method="tools/call",
                params={"name": name, "arguments": arguments},
                id=request_id,
            )
        )
        self.assertIsNotNone(result)
        return result

    def assert_tool_execution_error(
        self,
        response: Response | ErrorResponse,
        *,
        request_id: int,
        message_fragment: str,
    ) -> None:
        self.assertIsInstance(response, Response)
        self.assertEqual(response.id, request_id)
        self.assertNotIn("error", response.to_dict())
        self.assertTrue(response.result["isError"])
        content = response.result["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertIn(message_fragment, content[0]["text"])

    def test_tools_list_publishes_exactly_five_tools_in_workflow_order(self) -> None:
        response = self.server.process_request(
            Request(method="tools/list", params={}, id=1)
        )

        self.assertIsInstance(response, Response)
        self.assertEqual(
            [tool["name"] for tool in response.result["tools"]],
            [
                "assess_symptoms",
                "search_medications",
                "get_medication_details",
                "check_interactions",
                "check_stock",
            ],
        )

    def test_query_tool_schemas_publish_required_and_optional_fields(self) -> None:
        response = self.server.process_request(
            Request(method="tools/list", params={}, id=1)
        )
        definitions = {
            tool["name"]: tool["inputSchema"]
            for tool in response.result["tools"]
        }

        assessment_schema = definitions["assess_symptoms"]
        self.assertEqual(assessment_schema["required"], ["symptoms"])
        self.assertEqual(
            assessment_schema["properties"]["symptoms"]["type"],
            "string",
        )
        self.assertEqual(
            assessment_schema["properties"]["age"]["type"],
            "integer",
        )
        search_schema = definitions["search_medications"]
        self.assertEqual(search_schema["required"], ["query"])
        self.assertEqual(
            search_schema["properties"]["otc_only"]["type"], "boolean"
        )
        details_schema = definitions["get_medication_details"]
        self.assertEqual(details_schema["required"], ["sku"])
        interaction_schema = definitions["check_interactions"]
        self.assertEqual(interaction_schema["required"], ["medication_sku"])
        self.assertEqual(
            interaction_schema["properties"]["current_medications"]["type"],
            "array",
        )
        self.assertEqual(
            interaction_schema["properties"]["allergies"]["type"],
            "array",
        )
        stock_schema = definitions["check_stock"]
        self.assertEqual(stock_schema["required"], ["sku"])
        self.assertEqual(
            set(stock_schema["properties"]["branch_id"]["enum"]),
            {"zona-5", "zona-15", "mixco"},
        )
        self.assertTrue(
            all(
                schema["additionalProperties"] is False
                for schema in definitions.values()
            )
        )

    def test_search_medications_matches_alias(self) -> None:
        response = self.call_tool(
            "search_medications",
            {"query": "paracetamol"},
        )

        self.assertIsInstance(response, Response)
        structured = response.result["structuredContent"]
        self.assertEqual(structured["count"], 1)
        self.assertEqual(structured["medications"][0]["sku"], "MED-ANA-001")
        self.assertIn("not medical advice", response.result["content"][0]["text"])

    def test_search_medications_matches_ingredient_and_category(self) -> None:
        ingredient_response = self.call_tool(
            "search_medications",
            {"query": "cloruro de sodio"},
        )
        category_response = self.call_tool(
            "search_medications",
            {"query": "antihistamine"},
            request_id=2,
        )

        self.assertEqual(
            ingredient_response.result["structuredContent"]["medications"][0][
                "sku"
            ],
            "MED-RES-002",
        )
        self.assertEqual(
            {
                item["sku"]
                for item in category_response.result["structuredContent"][
                    "medications"
                ]
            },
            {"MED-ANT-001", "MED-ANT-002"},
        )

    def test_search_medications_filters_prescription_items(self) -> None:
        response = self.call_tool(
            "search_medications",
            {"query": "500 mg", "otc_only": True},
        )

        medications = response.result["structuredContent"]["medications"]
        self.assertEqual([item["sku"] for item in medications], ["MED-ANA-001"])
        self.assertTrue(
            all(item["requires_prescription"] is False for item in medications)
        )

    def test_search_medications_returns_successful_empty_result(self) -> None:
        response = self.call_tool(
            "search_medications",
            {"query": "medicine that is not present"},
        )

        self.assertIsInstance(response, Response)
        self.assertEqual(response.result["structuredContent"]["count"], 0)
        self.assertEqual(response.result["structuredContent"]["medications"], [])

    def test_search_medications_rejects_invalid_arguments(self) -> None:
        cases = (
            {},
            {"query": ""},
            {"query": 123},
            {"query": "fever", "otc_only": "true"},
            {"query": "fever", "extra": True},
        )

        for index, arguments in enumerate(cases, start=1):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "search_medications",
                    arguments,
                    request_id=index,
                )
                self.assertIsInstance(response, ErrorResponse)
                self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_get_medication_details_returns_every_catalog_field(self) -> None:
        response = self.call_tool(
            "get_medication_details",
            {"sku": "med-ana-001"},
        )

        self.assertIsInstance(response, Response)
        medication = response.result["structuredContent"]["medication"]
        self.assertEqual(
            set(medication),
            {
                "sku",
                "name",
                "aliases",
                "active_ingredient",
                "therapeutic_category",
                "dosage_information",
                "contraindications",
                "requires_prescription",
                "price",
            },
        )
        self.assertEqual(medication["sku"], "MED-ANA-001")
        self.assertEqual(medication["price"], {"amount": "18.95", "currency": "GTQ"})
        self.assertIsInstance(medication["price"]["amount"], str)

    def test_get_medication_details_reports_prescription_status(self) -> None:
        response = self.call_tool(
            "get_medication_details",
            {"sku": "MED-RX-001"},
        )

        medication = response.result["structuredContent"]["medication"]
        self.assertTrue(medication["requires_prescription"])
        self.assertIn(
            "prescription required: yes",
            response.result["content"][0]["text"],
        )

    def test_get_medication_details_rejects_malformed_arguments(self) -> None:
        cases = ({}, {"sku": ""}, {"sku": 123}, {"sku": "invalid sku!"})

        for index, arguments in enumerate(cases, start=1):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "get_medication_details",
                    arguments,
                    request_id=index,
                )
                self.assertIsInstance(response, ErrorResponse)
                self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_get_medication_details_reports_unknown_sku_as_tool_error(self) -> None:
        response = self.call_tool(
            "get_medication_details",
            {"sku": "MED-MISSING"},
            request_id=23,
        )

        self.assert_tool_execution_error(
            response,
            request_id=23,
            message_fragment="Unknown medication SKU",
        )

    def test_get_medication_details_rejects_unexpected_argument(self) -> None:
        response = self.call_tool(
            "get_medication_details",
            {"sku": "MED-ANA-001", "include_stock": True},
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)
        self.assertIn("Unexpected tool arguments", response.error.message)

    def test_check_stock_returns_all_branches_when_branch_is_omitted(self) -> None:
        response = self.call_tool(
            "check_stock",
            {"sku": "MED-ANA-001"},
        )

        self.assertIsInstance(response, Response)
        structured = response.result["structuredContent"]
        self.assertEqual(structured["sku"], "MED-ANA-001")
        self.assertEqual(
            [item["branch_id"] for item in structured["stock"]],
            ["zona-5", "zona-15", "mixco"],
        )
        self.assertEqual(
            [item["quantity"] for item in structured["stock"]],
            [25, 12, 0],
        )
        self.assertEqual(
            [item["available"] for item in structured["stock"]],
            [True, True, False],
        )

    def test_check_stock_returns_one_requested_branch(self) -> None:
        response = self.call_tool(
            "check_stock",
            {"sku": "med-ant-002", "branch_id": "zona-15"},
        )

        stock = response.result["structuredContent"]["stock"]
        self.assertEqual(
            stock,
            [
                {
                    "branch_id": "zona-15",
                    "branch_name": "Zona 15",
                    "quantity": 16,
                    "available": True,
                }
            ],
        )

    def test_check_stock_reports_zero_as_unavailable(self) -> None:
        response = self.call_tool(
            "check_stock",
            {"sku": "MED-ANT-002", "branch_id": "zona-5"},
        )

        stock = response.result["structuredContent"]["stock"][0]
        self.assertEqual(stock["quantity"], 0)
        self.assertFalse(stock["available"])

    def test_check_stock_rejects_malformed_arguments(self) -> None:
        cases = (
            {},
            {"sku": ""},
            {"sku": None},
            {"sku": "MED-ANA 001"},
            {"sku": "MED-ANA-001", "branch_id": None},
            {"sku": "MED-ANA-001", "branch_id": ""},
            {"sku": "MED-ANA-001", "branch_id": "zona 10"},
        )

        for index, arguments in enumerate(cases, start=1):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "check_stock",
                    arguments,
                    request_id=index,
                )
                self.assertIsInstance(response, ErrorResponse)
                self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_check_stock_reports_domain_lookup_failures_as_tool_errors(self) -> None:
        cases = (
            ({"sku": "MED-MISSING"}, "Unknown medication SKU"),
            (
                {"sku": "MED-ANA-001", "branch_id": "zona-10"},
                "Unknown branch",
            ),
        )

        for index, (arguments, message) in enumerate(cases, start=31):
            with self.subTest(arguments=arguments):
                response = self.call_tool(
                    "check_stock",
                    arguments,
                    request_id=index,
                )
                self.assert_tool_execution_error(
                    response,
                    request_id=index,
                    message_fragment=message,
                )

    def test_check_stock_rejects_unexpected_arguments(self) -> None:
        response = self.call_tool(
            "check_stock",
            {"sku": "MED-ANA-001", "quantity": 1},
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, INVALID_PARAMS)

    def test_check_stock_is_repeatable_and_read_only(self) -> None:
        first = self.call_tool(
            "check_stock",
            {"sku": "MED-RX-001", "branch_id": "mixco"},
        )
        second = self.call_tool(
            "check_stock",
            {"sku": "MED-RX-001", "branch_id": "mixco"},
            request_id=2,
        )

        self.assertEqual(
            first.result["structuredContent"],
            second.result["structuredContent"],
        )
        self.assertEqual(
            first.result["structuredContent"]["stock"][0]["quantity"],
            11,
        )

    def test_new_query_tool_requires_ready_server(self) -> None:
        server = PharmacyMCPServer()

        response = server.process_request(
            Request(
                method="tools/call",
                params={
                    "name": "search_medications",
                    "arguments": {"query": "loratadina"},
                },
                id=1,
            )
        )

        self.assertIsInstance(response, ErrorResponse)
        self.assertEqual(response.error.code, SERVER_NOT_INITIALIZED)


if __name__ == "__main__":
    unittest.main()
