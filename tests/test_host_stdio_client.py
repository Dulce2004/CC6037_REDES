"""Tests for the MCP client controlling a real stdio server."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    MCPProtocolLogger,
    MCPServerResponseError,
    MCPTransportError,
    StdioMCPClient,
    StdioServerConfig,
)


class StdioMCPClientTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_directory = PROJECT_DIRECTORY / "runtime"
        runtime_directory.mkdir(exist_ok=True)
        self.database_path = runtime_directory / (
            f"host-client-{uuid4().hex}.sqlite3"
        )
        self.log_path = runtime_directory / f"host-client-{uuid4().hex}.jsonl"
        self.addCleanup(self._remove_database_files, self.database_path)
        self.addCleanup(self.log_path.unlink, missing_ok=True)
        self.log_stream = io.StringIO()
        self.protocol_logger = MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.log_stream,
            show_traffic=True,
        )
        self.addCleanup(self.protocol_logger.close)
        self.client = StdioMCPClient(
            self.server_config("pharmacy", self.database_path),
            protocol_logger=self.protocol_logger,
        )
        self.addCleanup(self.client.stop)

    def test_start_completes_lifecycle_and_lists_seven_tools(self) -> None:
        self.client.start()

        tools = self.client.list_tools()

        self.assertTrue(self.client.is_ready)
        self.assertIsNotNone(self.client.process_id)
        self.assertEqual(len(tools), 7)
        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "assess_symptoms",
                "search_medications",
                "get_medication_details",
                "check_interactions",
                "check_stock",
                "create_order",
                "get_order_status",
            ],
        )

    def test_call_tool_uses_real_child_process(self) -> None:
        self.client.start()

        result = self.client.call_tool(
            "check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
        )

        self.assertEqual(
            result["structuredContent"]["stock"][0]["quantity"],
            25,
        )

    def test_every_request_notification_and_response_is_visible_in_log(self) -> None:
        self.client.start()
        self.client.list_tools()
        self.client.call_tool(
            "get_medication_details",
            {"sku": "MED-ANA-001"},
        )

        entries = [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]
        outbound = [
            entry for entry in entries if entry["direction"] == "outbound"
        ]
        inbound = [
            entry for entry in entries if entry["direction"] == "inbound"
        ]
        self.assertEqual(len(outbound), 4)
        self.assertEqual(len(inbound), 3)
        self.assertTrue(
            any(entry.get("method") == "initialize" for entry in outbound)
        )
        self.assertTrue(
            any(
                entry.get("method") == "notifications/initialized"
                for entry in outbound
            )
        )
        self.assertTrue(
            any(entry.get("method") == "tools/list" for entry in outbound)
        )
        self.assertTrue(
            any(entry.get("method") == "tools/call" for entry in outbound)
        )
        self.assertTrue(
            all(
                line.startswith("[MCP log] ")
                for line in self.log_stream.getvalue().splitlines()
            )
        )

    def test_json_rpc_error_response_is_logged_and_exposed(self) -> None:
        self.client.start()

        with self.assertRaises(MCPServerResponseError) as context:
            self.client.request("unknown/method", {})

        self.assertEqual(context.exception.code, -32601)
        self.assertIn('"message_type":"error"', self.log_stream.getvalue())

    def test_tool_execution_error_remains_a_successful_mcp_result(self) -> None:
        self.client.start()

        result = self.client.call_tool(
            "get_order_status",
            {"order_id": "ORD-MISSING"},
        )

        self.assertTrue(result["isError"])
        self.assertIn("Unknown order ID", result["content"][0]["text"])

    def test_stop_delivers_eof_and_child_exits_cleanly(self) -> None:
        self.client.start()
        process = self.client._process
        self.assertIsNotNone(process)

        self.client.stop()

        self.assertFalse(self.client.is_running)
        self.assertEqual(process.returncode, 0)

    def test_client_can_restart_after_a_clean_stop(self) -> None:
        self.client.start()
        first_process_id = self.client.process_id
        self.client.stop()

        self.client.start()
        tools = self.client.list_tools()

        self.assertTrue(self.client.is_ready)
        self.assertNotEqual(self.client.process_id, first_process_id)
        self.assertEqual(len(tools), 7)

    def test_request_before_start_is_rejected(self) -> None:
        with self.assertRaisesRegex(MCPTransportError, "not been started"):
            self.client.request("tools/list", {})

    def test_missing_executable_is_reported_as_transport_error(self) -> None:
        missing = StdioMCPClient(
            StdioServerConfig(
                name="missing",
                command=str(PROJECT_DIRECTORY / "missing-executable"),
                args=(),
                cwd=PROJECT_DIRECTORY,
                env=MappingProxyType({}),
            ),
            protocol_logger=self.protocol_logger,
        )

        with self.assertRaisesRegex(MCPTransportError, "Cannot start"):
            missing.start()

    @staticmethod
    def server_config(name: str, database_path: Path) -> StdioServerConfig:
        return StdioServerConfig(
            name=name,
            command=sys.executable,
            args=("-B", "-m", "pharmacy_mcp.server.stdio"),
            cwd=PROJECT_DIRECTORY,
            env=MappingProxyType(
                {
                    "PYTHONPATH": str(SOURCE_DIRECTORY),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PHARMACY_MCP_DATABASE_PATH": str(database_path),
                }
            ),
            request_timeout_seconds=10,
            shutdown_timeout_seconds=5,
        )

    @staticmethod
    def _remove_database_files(database_path: Path) -> None:
        for suffix in ("", "-shm", "-wal"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
