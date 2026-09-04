"""Tests for the multi-server manager and namespaced tool registry."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch
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

    def test_partial_failure_rolls_back_every_server_started_by_start_all(self) -> None:
        configs = tuple(
            self.server_config(name, self.database_paths[index % 2])
            for index, name in enumerate(("pharmacy", "git", "filesystem"))
        )
        manager = MCPServerManager(
            HostConfig(servers=configs),
            protocol_logger=self.protocol_logger,
        )
        clients: dict[str, _StartClient] = {}

        def create_client(config, *, protocol_logger):
            client = _StartClient(
                config.name,
                fail_on_start=config.name == "filesystem",
            )
            clients[config.name] = client
            return client

        with patch(
            "pharmacy_mcp.host.manager.StdioMCPClient",
            side_effect=create_client,
        ):
            with self.assertRaisesRegex(RuntimeError, "start failed"):
                manager.start_all()

        self.assertEqual(set(clients), {"pharmacy", "git", "filesystem"})
        self.assertTrue(all(client.stopped for client in clients.values()))
        self.assertEqual(manager._clients, {})
        self.assertEqual(manager.list_tools(), ())

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

    def test_real_tool_annotations_schemas_and_extra_fields_are_preserved(self) -> None:
        config = self.manager._config.servers[0]
        definition = {
            "name": "read_text_file",
            "title": "Read Text File",
            "description": "Read text.",
            "inputSchema": {"type": "object", "required": ["path"]},
            "outputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
            "execution": {"taskSupport": "forbidden"},
        }

        tool = self.manager._registered_definitions(config, (definition,))[0]
        public = tool.to_dict()

        self.assertEqual(public["name"], "pharmacy__read_text_file")
        self.assertEqual(public["tool"], "read_text_file")
        self.assertEqual(public["inputSchema"], definition["inputSchema"])
        self.assertEqual(public["outputSchema"], definition["outputSchema"])
        self.assertEqual(public["annotations"], definition["annotations"])
        self.assertEqual(public["execution"], definition["execution"])

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


class _StartClient:
    def __init__(self, name: str, *, fail_on_start: bool) -> None:
        self.name = name
        self.fail_on_start = fail_on_start
        self.is_ready = False
        self.stopped = False

    @property
    def process_id(self) -> int | None:
        return None

    def start(self) -> None:
        if self.fail_on_start:
            raise RuntimeError("start failed")
        self.is_ready = True

    def list_tools(self):
        return (
            {
                "name": "sample_tool",
                "description": "Sample.",
                "inputSchema": {"type": "object"},
            },
        )

    def stop(self) -> None:
        self.stopped = True
        self.is_ready = False


if __name__ == "__main__":
    unittest.main()
