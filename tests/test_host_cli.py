"""Tests for the technical MCP host CLI."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host.cli import main  # noqa: E402


class MCPHostCliTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_directory = PROJECT_DIRECTORY / "runtime"
        runtime_directory.mkdir(exist_ok=True)
        unique = uuid4().hex
        self.database_path = runtime_directory / f"host-cli-{unique}.sqlite3"
        self.config_path = runtime_directory / f"host-cli-{unique}.json"
        self.log_path = runtime_directory / f"host-cli-{unique}.jsonl"
        self.config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": "pharmacy",
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [
                                "-B",
                                "-m",
                                "pharmacy_mcp.server.stdio",
                            ],
                            "cwd": "..",
                            "env": {
                                "PYTHONPATH": str(SOURCE_DIRECTORY),
                                "PYTHONDONTWRITEBYTECODE": "1",
                                "PHARMACY_MCP_DATABASE_PATH": str(
                                    self.database_path
                                ),
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.addCleanup(self._remove_runtime_files)

    def run_cli(
        self,
        *arguments: str,
        show_log: bool = False,
    ) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        host_arguments = [
            "--config",
            str(self.config_path),
            "--log-file",
            str(self.log_path),
        ]
        if show_log:
            host_arguments.append("--show-log")
        host_arguments.extend(arguments)
        exit_code = main(
            host_arguments,
            stdout=stdout,
            stderr=stderr,
        )
        parsed_output = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
        return exit_code, parsed_output, stderr.getvalue()

    def test_list_servers_does_not_start_processes(self) -> None:
        exit_code, output, diagnostics = self.run_cli("list-servers")

        self.assertEqual(exit_code, 0)
        self.assertEqual(diagnostics, "")
        self.assertTrue(self.log_path.exists())
        self.assertEqual(self.log_path.read_text(encoding="utf-8"), "")
        self.assertEqual(
            output["servers"],
            [
                {
                    "name": "pharmacy",
                    "transport": "stdio",
                    "enabled": True,
                    "status": "stopped",
                    "process_id": None,
                }
            ],
        )

    def test_list_tools_outputs_namespaced_registry_and_protocol_log(self) -> None:
        exit_code, output, diagnostics = self.run_cli(
            "list-tools",
            show_log=True,
        )

        self.assertEqual(exit_code, 0)
        names = [tool["name"] for tool in output["tools"]]
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
        self.assertNotIn("[MCP", json.dumps(output))
        self.assertIn("[MCP log]", diagnostics)
        self.assertIn('"method":"tools/list"', diagnostics)

    def test_call_tool_invokes_namespaced_tool_with_json_arguments(self) -> None:
        exit_code, output, diagnostics = self.run_cli(
            "call-tool",
            "pharmacy__check_stock",
            "--arguments",
            '{"sku":"MED-ANA-001","branch_id":"zona-5"}',
            show_log=True,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output["result"]["structuredContent"]["stock"][0]["quantity"],
            25,
        )
        self.assertIn('"method":"tools/call"', diagnostics)
        self.assertIn('"name":"check_stock"', diagnostics)

    def test_invalid_argument_json_fails_without_starting_server(self) -> None:
        exit_code, output, diagnostics = self.run_cli(
            "call-tool",
            "pharmacy__check_stock",
            "--arguments",
            "[]",
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(output, {})
        self.assertIn("must be a JSON object", diagnostics)
        self.assertNotIn("[MCP log]", diagnostics)

    def test_previous_dotted_namespace_is_rejected_without_protocol_traffic(
        self,
    ) -> None:
        exit_code, output, diagnostics = self.run_cli(
            "call-tool",
            "pharmacy.check_stock",
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(output, {})
        self.assertIn("not registered", diagnostics)
        self.assertIn("<server>__<tool>", diagnostics)
        self.assertNotIn("[MCP log]", diagnostics)

    def test_unknown_namespaced_tool_is_reported_after_clean_shutdown(self) -> None:
        exit_code, output, diagnostics = self.run_cli(
            "call-tool",
            "pharmacy__unknown",
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(output, {})
        self.assertIn("not registered", diagnostics)

    def test_jsonl_records_complete_handshake_and_tool_call(self) -> None:
        exit_code, output, diagnostics = self.run_cli(
            "call-tool",
            "pharmacy__check_stock",
            "--arguments",
            '{"sku":"MED-ANA-001","branch_id":"zona-5"}',
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("result", output)
        self.assertEqual(diagnostics, "")
        entries = [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [entry["message_type"] for entry in entries],
            [
                "request",
                "response",
                "notification",
                "request",
                "response",
                "request",
                "response",
            ],
        )
        self.assertEqual(
            [entry["direction"] for entry in entries],
            [
                "outbound",
                "inbound",
                "outbound",
                "outbound",
                "inbound",
                "outbound",
                "inbound",
            ],
        )
        self.assertEqual(
            [entry.get("method") for entry in entries],
            [
                "initialize",
                None,
                "notifications/initialized",
                "tools/list",
                None,
                "tools/call",
                None,
            ],
        )
        self.assertEqual(
            [entry.get("id") for entry in entries],
            [1, 1, None, 2, 2, 3, 3],
        )
        self.assertTrue(
            all(entry["server"] == "pharmacy" for entry in entries)
        )
        self.assertTrue(all(entry["transport"] == "stdio" for entry in entries))

    def test_log_open_failure_returns_nonzero_without_protocol_traffic(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = main(
            [
                "--config",
                str(self.config_path),
                "--log-file",
                str(self.config_path / "cannot-open.jsonl"),
                "list-tools",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Cannot open MCP log", stderr.getvalue())
        self.assertNotIn("[MCP log]", stderr.getvalue())

    def _remove_runtime_files(self) -> None:
        self.config_path.unlink(missing_ok=True)
        self.log_path.unlink(missing_ok=True)
        for suffix in ("", "-shm", "-wal"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
