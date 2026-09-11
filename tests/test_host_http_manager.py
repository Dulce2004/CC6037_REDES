"""Integration tests for mixed stdio and HTTP servers in the MCP manager."""

from __future__ import annotations

import io
import sys
import threading
import unittest
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    HTTPServerConfig,
    HostConfig,
    MCPProtocolLogger,
    MCPServerManager,
    StdioServerConfig,
)
from pharmacy_mcp.server.http import (  # noqa: E402
    PharmacyHTTPSettings,
    create_http_server,
)


class MixedTransportManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT_DIRECTORY / "runtime"
        runtime.mkdir(exist_ok=True)
        unique = uuid4().hex
        self.local_database = runtime / f"mixed-local-{unique}.sqlite3"
        self.remote_database = runtime / f"mixed-remote-{unique}.sqlite3"
        self.log_path = runtime / f"mixed-host-{unique}.jsonl"
        self.token = "mixed-transport-token"
        self.http_server = create_http_server(
            PharmacyHTTPSettings(
                host="127.0.0.1",
                port=0,
                token=self.token,
                database_path=self.remote_database,
            ),
            diagnostic_stream=io.StringIO(),
        )
        self.thread = threading.Thread(
            target=self.http_server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name=f"mixed-http-{unique}",
        )
        self.thread.start()
        self.logger = MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=io.StringIO(),
        )
        self.managers: list[MCPServerManager] = []

    def tearDown(self) -> None:
        for manager in reversed(self.managers):
            try:
                manager.stop_all()
            except Exception:
                pass
        self.logger.close()
        self.http_server.shutdown()
        self.http_server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.log_path.unlink(missing_ok=True)
        for database in (self.local_database, self.remote_database):
            for suffix in ("", "-shm", "-wal"):
                Path(f"{database}{suffix}").unlink(missing_ok=True)

    def local_config(self) -> StdioServerConfig:
        return StdioServerConfig(
            name="pharmacy",
            command=sys.executable,
            args=("-B", "-m", "pharmacy_mcp.server.stdio"),
            cwd=PROJECT_DIRECTORY,
            env=MappingProxyType(
                {
                    "PYTHONPATH": "src",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PHARMACY_MCP_DATABASE_PATH": str(self.local_database),
                }
            ),
        )

    def remote_config(self, *, url: str | None = None) -> HTTPServerConfig:
        host, port = self.http_server.server_address[:2]
        return HTTPServerConfig(
            name="pharmacy-remote",
            url=url or f"http://{host}:{port}/mcp",
            token=self.token,
            token_env="PHARMACY_MCP_HTTP_TOKEN",
            timeout_seconds=1,
            mutable_tools=frozenset({"create_order"}),
        )

    def manager(self, remote: HTTPServerConfig) -> MCPServerManager:
        manager = MCPServerManager(
            HostConfig(servers=(self.local_config(), remote)),
            protocol_logger=self.logger,
        )
        self.managers.append(manager)
        return manager

    def test_mixed_manager_registers_and_routes_both_namespaces(self) -> None:
        manager = self.manager(self.remote_config())
        manager.start_all()

        tools = manager.list_tools()
        names = {tool.namespaced_name for tool in tools}
        self.assertEqual(len(names), 14)
        self.assertIn("pharmacy__check_stock", names)
        self.assertIn("pharmacy-remote__check_stock", names)
        remote_stock = manager.invoke_tool(
            "pharmacy-remote__check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
        )
        self.assertEqual(
            remote_stock["structuredContent"]["stock"][0]["quantity"],
            25,
        )
        self.assertTrue(manager.requires_confirmation("pharmacy-remote__create_order"))
        self.assertFalse(manager.requires_confirmation("pharmacy-remote__check_stock"))

        summaries = {summary.name: summary for summary in manager.list_servers()}
        self.assertEqual(summaries["pharmacy"].status, "ready")
        self.assertIsNotNone(summaries["pharmacy"].process_id)
        self.assertEqual(summaries["pharmacy-remote"].status, "ready")
        self.assertIsNone(summaries["pharmacy-remote"].process_id)

    def test_unavailable_remote_preserves_local_server_and_tools(self) -> None:
        manager = self.manager(
            self.remote_config(url="http://127.0.0.1:1/mcp")
        )
        failures = manager.start_available()

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].server_name, "pharmacy-remote")
        self.assertNotIn(self.token, failures[0].error)
        self.assertEqual(
            {tool.server_name for tool in manager.list_tools()},
            {"pharmacy"},
        )
        summaries = {summary.name: summary for summary in manager.list_servers()}
        self.assertEqual(summaries["pharmacy"].status, "ready")
        self.assertEqual(summaries["pharmacy-remote"].status, "error")


if __name__ == "__main__":
    unittest.main()
