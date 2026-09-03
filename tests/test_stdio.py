"""Pruebas del transporte stdio JSON-RPC delimitado por líneas."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.jsonrpc import (  # noqa: E402
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
)
from pharmacy_mcp.server import (  # noqa: E402
    SUPPORTED_PROTOCOL_VERSION,
    PharmacyMCPServer,
    ServerState,
)
from pharmacy_mcp.server.stdio import serve_stdio  # noqa: E402


def json_line(message: dict[str, object]) -> str:
    return f"{json.dumps(message, ensure_ascii=False)}\n"


def initialize_request(request_id: object = 1) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Cliente Farmacia Ñ", "version": "1.0.0"},
        },
        "id": request_id,
    }


class TrackingOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


class FailingInput:
    def __iter__(self) -> "FailingInput":
        return self

    def __next__(self) -> str:
        raise OSError("simulated read failure")


class StdioTransportTests(unittest.TestCase):
    def run_transport(
        self,
        payload: str,
        server: PharmacyMCPServer | None = None,
    ) -> tuple[int, list[dict[str, object]], str, PharmacyMCPServer]:
        active_server = server if server is not None else PharmacyMCPServer()
        stdout = TrackingOutput()
        stderr = io.StringIO()

        exit_code = serve_stdio(
            server=active_server,
            stdin=io.StringIO(payload),
            stdout=stdout,
            stderr=stderr,
        )
        messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
        return exit_code, messages, stderr.getvalue(), active_server

    def test_eof_closes_cleanly_without_output(self) -> None:
        exit_code, messages, diagnostics, server = self.run_transport("")

        self.assertEqual(exit_code, 0)
        self.assertEqual(messages, [])
        self.assertEqual(diagnostics, "")
        self.assertEqual(server.state, ServerState.UNINITIALIZED)

    def test_initialize_is_processed_as_one_line(self) -> None:
        exit_code, messages, diagnostics, server = self.run_transport(
            json_line(initialize_request())
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["id"], 1)
        self.assertEqual(
            messages[0]["result"]["protocolVersion"], SUPPORTED_PROTOCOL_VERSION
        )
        self.assertEqual(server.state, ServerState.INITIALIZING)

    def test_server_state_is_preserved_across_input_lines(self) -> None:
        payload = "".join(
            (
                json_line(initialize_request()),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                ),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/list",
                        "params": {},
                        "id": 2,
                    }
                ),
            )
        )

        exit_code, messages, diagnostics, server = self.run_transport(payload)

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        self.assertEqual([message["id"] for message in messages], [1, 2])
        self.assertIn("tools", messages[1]["result"])
        self.assertEqual(server.state, ServerState.READY)

    def test_notifications_do_not_write_responses(self) -> None:
        payload = json_line(
            {"jsonrpc": "2.0", "method": "unknown/notification", "params": {}}
        )

        exit_code, messages, diagnostics, _ = self.run_transport(payload)

        self.assertEqual(exit_code, 0)
        self.assertEqual(messages, [])
        self.assertEqual(diagnostics, "")

    def test_invalid_json_writes_parse_error_to_stdout(self) -> None:
        exit_code, messages, diagnostics, _ = self.run_transport("{invalid}\n")

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        self.assertEqual(messages[0]["error"]["code"], PARSE_ERROR)
        self.assertIsNone(messages[0]["id"])

    def test_blank_line_is_a_parse_error_message(self) -> None:
        _, messages, diagnostics, _ = self.run_transport("\n")

        self.assertEqual(messages[0]["error"]["code"], PARSE_ERROR)
        self.assertEqual(diagnostics, "")

    def test_two_json_objects_on_one_line_are_rejected(self) -> None:
        payload = (
            json.dumps(initialize_request())
            + json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 2})
            + "\n"
        )

        _, messages, _, _ = self.run_transport(payload)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["error"]["code"], PARSE_ERROR)

    def test_incoming_response_is_rejected_as_invalid_request(self) -> None:
        payload = json_line({"jsonrpc": "2.0", "result": {}, "id": 1})

        _, messages, diagnostics, _ = self.run_transport(payload)

        self.assertEqual(messages[0]["error"]["code"], INVALID_REQUEST)
        self.assertIsNone(messages[0]["id"])
        self.assertEqual(diagnostics, "")

    def test_request_with_null_id_receives_response(self) -> None:
        _, messages, _, _ = self.run_transport(json_line(initialize_request(None)))

        self.assertEqual(len(messages), 1)
        self.assertIsNone(messages[0]["id"])

    def test_unknown_request_method_returns_method_not_found(self) -> None:
        payload = json_line(
            {"jsonrpc": "2.0", "method": "unknown/method", "params": {}, "id": 8}
        )

        _, messages, _, _ = self.run_transport(payload)

        self.assertEqual(messages[0]["error"]["code"], METHOD_NOT_FOUND)
        self.assertEqual(messages[0]["id"], 8)

    def test_each_response_is_flushed(self) -> None:
        stdout = TrackingOutput()
        payload = json_line(initialize_request()) + "{invalid}\n"

        exit_code = serve_stdio(
            stdin=io.StringIO(payload), stdout=stdout, stderr=io.StringIO()
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.flush_count, 2)
        self.assertTrue(stdout.getvalue().endswith("\n"))

    def test_transport_failure_is_reported_only_to_stderr(self) -> None:
        stdout = TrackingOutput()
        stderr = io.StringIO()

        exit_code = serve_stdio(
            stdin=FailingInput(),  # type: ignore[arg-type]
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("stdio transport failure: OSError", stderr.getvalue())

    def test_module_entry_point_handles_utf8_lifecycle_until_eof(self) -> None:
        payload = "".join(
            (
                json_line(initialize_request()),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                ),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/list",
                        "params": {},
                        "id": 2,
                    }
                ),
            )
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE_DIRECTORY)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"

        completed = subprocess.run(
            [sys.executable, "-B", "-m", "pharmacy_mcp.server.stdio"],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=PROJECT_DIRECTORY,
            env=environment,
            timeout=10,
            check=False,
        )

        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertEqual([response["id"] for response in responses], [1, 2])

    def test_stdio_can_call_read_only_stock_tool(self) -> None:
        payload = "".join(
            (
                json_line(initialize_request()),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                ),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "check_stock",
                            "arguments": {
                                "sku": "MED-ANA-001",
                                "branch_id": "zona-5",
                            },
                        },
                        "id": 2,
                    }
                ),
            )
        )

        exit_code, messages, diagnostics, _ = self.run_transport(payload)

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        self.assertEqual([message["id"] for message in messages], [1, 2])
        stock = messages[1]["result"]["structuredContent"]["stock"]
        self.assertEqual(stock[0]["quantity"], 25)

    def test_stdio_returns_domain_failure_as_successful_tool_result(self) -> None:
        payload = "".join(
            (
                json_line(initialize_request()),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                ),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "check_stock",
                            "arguments": {
                                "sku": "MED-ANA-001",
                                "branch_id": "zona-10",
                            },
                        },
                        "id": 7,
                    }
                ),
            )
        )

        exit_code, messages, diagnostics, _ = self.run_transport(payload)

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        tool_response = messages[1]
        self.assertEqual(tool_response["id"], 7)
        self.assertNotIn("error", tool_response)
        self.assertTrue(tool_response["result"]["isError"])
        self.assertIn(
            "Unknown branch",
            tool_response["result"]["content"][0]["text"],
        )

    def test_stdio_supports_assessment_and_interaction_workflow(self) -> None:
        payload = "".join(
            (
                json_line(initialize_request()),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                ),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "assess_symptoms",
                            "arguments": {
                                "symptoms": (
                                    "Tengo tos y dificultad para respirar"
                                ),
                                "age": 24,
                                "duration_days": 1,
                            },
                        },
                        "id": 8,
                    }
                ),
                json_line(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "check_interactions",
                            "arguments": {
                                "medication_sku": "MED-ANA-002",
                                "current_medications": ["MED-GAS-001"],
                                "allergies": [],
                            },
                        },
                        "id": 9,
                    }
                ),
            )
        )

        exit_code, messages, diagnostics, _ = self.run_transport(payload)

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        self.assertEqual([message["id"] for message in messages], [1, 8, 9])
        assessment = messages[1]["result"]["structuredContent"]
        interactions = messages[2]["result"]["structuredContent"]
        self.assertEqual(assessment["severity"], "urgent")
        self.assertFalse(assessment["medication_purchase_recommended"])
        self.assertEqual(interactions["alert_count"], 1)
        self.assertFalse(interactions["exhaustive"])


if __name__ == "__main__":
    unittest.main()
