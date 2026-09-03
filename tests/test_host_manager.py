"""Tests for the multi-server manager and namespaced tool registry."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    HostConfig,
    MCPHostError,
    MCPProtocolLogger,
    MCPServerManager,
    StdioServerConfig,
)


class MCPServerManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_directory = PROJECT_DIRECTORY / "runtime"
        runtime_directory.mkdir(exist_ok=True)
        self.database_paths = tuple(
            runtime_directory / f"host-manager-{uuid4().hex}.sqlite3"
            for _ in range(2)
        )
        self.log_path = runtime_directory / f"host-manager-{uuid4().hex}.jsonl"
        self.addCleanup(self._remove_database_files)
        self.addCleanup(self.log_path.unlink, missing_ok=True)
        config = HostConfig(
            servers=(
                self.server_config("pharmacy", self.database_paths[0]),
                self.server_config("backup", self.database_paths[1]),
            )
        )
        self.log_stream = io.StringIO()
        self.protocol_logger = MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.log_stream,
            show_traffic=True,
        )
        self.addCleanup(self.protocol_logger.close)
        self.manager = MCPServerManager(
            config,
            protocol_logger=self.protocol_logger,
        )
        self.addCleanup(self.manager.stop_all)

    def test_lists_multiple_configured_servers_without_starting_them(self) -> None:
        summaries = self.manager.list_servers()

        self.assertEqual([item.name for item in summaries], ["pharmacy", "backup"])
        self.assertTrue(all(item.status == "stopped" for item in summaries))
        self.assertTrue(all(item.process_id is None for item in summaries))

    def test_start_server_registers_namespaced_tools(self) -> None:
        self.manager.start_server("pharmacy")

        names = [tool.namespaced_name for tool in self.manager.list_tools()]
        self.assertEqual(
            names,
            [
                "pharmacy__assess_symptoms",
                "pharmacy__search_medications",
                "pharmacy__get_medication_details",
                "pharmacy__check_interactions",
                "pharmacy__check_stock",
                "pharmacy__create_order",
                "pharmacy__get_order_status",
            ],
        )
        self.assertTrue(all("." not in name for name in names))
        summary = self.manager.list_servers()[0]
        self.assertEqual(summary.status, "ready")
        self.assertIsNotNone(summary.process_id)

    def test_start_all_keeps_same_tool_names_separate(self) -> None:
        self.manager.start_all()

        names = [tool.namespaced_name for tool in self.manager.list_tools()]
        self.assertEqual(len(names), 14)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("pharmacy__check_stock", names)
        self.assertIn("backup__check_stock", names)

    def test_namespaced_invocation_routes_to_independent_server_state(self) -> None:
        self.manager.start_all()

        created = self.manager.invoke_tool(
            "pharmacy__create_order",
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": 2}],
            },
        )
        pharmacy_stock = self.manager.invoke_tool(
            "pharmacy__check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
        )
        backup_stock = self.manager.invoke_tool(
            "backup__check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
        )

        self.assertEqual(
            created["structuredContent"]["order"]["status"],
            "created",
        )
        self.assertEqual(
            pharmacy_stock["structuredContent"]["stock"][0]["quantity"],
            23,
        )
        self.assertEqual(
            backup_stock["structuredContent"]["stock"][0]["quantity"],
            25,
        )

    def test_stopping_one_server_removes_only_its_tools(self) -> None:
        self.manager.start_all()

        self.manager.stop_server("pharmacy")

        names = [tool.namespaced_name for tool in self.manager.list_tools()]
        self.assertEqual(len(names), 7)
        self.assertTrue(all(name.startswith("backup__") for name in names))
        self.assertEqual(self.manager.list_servers()[0].status, "stopped")
        self.assertEqual(self.manager.list_servers()[1].status, "ready")

    def test_invalid_namespace_and_unknown_tool_are_rejected(self) -> None:
        with self.assertRaisesRegex(MCPHostError, "<server>__<tool>"):
            self.manager.server_name_from_namespace("check_stock")
        with self.assertRaisesRegex(MCPHostError, "Unknown configured server"):
            self.manager.server_name_from_namespace("unknown__check_stock")
        with self.assertRaisesRegex(MCPHostError, "exactly one"):
            self.manager.server_name_from_namespace(
                "pharmacy__group__check_stock"
            )
        with self.assertRaisesRegex(MCPHostError, "incompatible"):
            self.manager.server_name_from_namespace("pharmacy__check.stock")

        self.manager.start_server("pharmacy")
        with self.assertRaisesRegex(MCPHostError, "not registered"):
            self.manager.invoke_tool("pharmacy__unknown", {})

    def test_global_name_resolves_to_server_and_original_tool(self) -> None:
        self.manager.start_server("pharmacy")

        tool = self.manager.resolve_tool("pharmacy__check_stock")

        self.assertEqual(tool.server_name, "pharmacy")
        self.assertEqual(tool.tool_name, "check_stock")

    def test_server_tool_name_with_separator_or_unsafe_character_is_rejected(
        self,
    ) -> None:
        config = self.manager._config.servers[0]
        for tool_name in (
            "group__tool",
            "unsafe.tool",
            "unsafe/tool",
            "_ambiguous",
        ):
            with self.subTest(tool_name=tool_name):
                with self.assertRaisesRegex(MCPHostError, "incompatible"):
                    self.manager._registered_definitions(
                        config,
                        (
                            {
                                "name": tool_name,
                                "description": "Test tool.",
                                "inputSchema": {"type": "object"},
                            },
                        ),
                    )

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

    def _remove_database_files(self) -> None:
        for database_path in self.database_paths:
            for suffix in ("", "-shm", "-wal"):
                Path(f"{database_path}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
