"""Tests for the durable, redacted MCP protocol log."""

from __future__ import annotations

import io
import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    DEFAULT_LOG_PATH,
    REDACTION_MARKER,
    MCPLogError,
    MCPProtocolLogger,
)


class MCPProtocolLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_directory = PROJECT_DIRECTORY / "runtime"
        runtime_directory.mkdir(exist_ok=True)
        self.directory = runtime_directory / f"host-log-{uuid4().hex}"
        self.log_path = self.directory / "host.jsonl"
        self.addCleanup(self._remove_test_directory)
        self.stderr = io.StringIO()

    def test_default_path_is_portable_and_ignored_runtime_location(self) -> None:
        self.assertEqual(
            DEFAULT_LOG_PATH,
            PROJECT_DIRECTORY / "runtime" / "mcp-host.jsonl",
        )

    def test_log_creates_parent_flushes_and_appends_valid_json_lines(self) -> None:
        first = MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
        )
        first.outbound(
            "pharmacy",
            '{"jsonrpc":"2.0","method":"initialize","id":1}',
        )

        self.assertTrue(self.log_path.exists())
        self.assertEqual(len(self._read_entries()), 1)
        first.close()
        self.assertFalse(first.is_open)

        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
        ) as second:
            second.inbound(
                "pharmacy",
                '{"jsonrpc":"2.0","result":{},"id":1}',
            )

        entries = self._read_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [entry["direction"] for entry in entries],
            ["outbound", "inbound"],
        )

    def test_entry_has_utc_timestamp_server_transport_type_method_and_id(
        self,
    ) -> None:
        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
        ) as logger:
            logger.outbound(
                "pharmacy",
                '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}',
            )

        entry = self._read_entries()[0]
        timestamp = entry["timestamp"]
        self.assertIsInstance(timestamp, str)
        self.assertTrue(timestamp.endswith("Z"))
        self.assertIsNotNone(datetime.fromisoformat(timestamp.replace("Z", "+00:00")))
        self.assertEqual(entry["server"], "pharmacy")
        self.assertEqual(entry["transport"], "stdio")
        self.assertEqual(entry["direction"], "outbound")
        self.assertEqual(entry["message_type"], "request")
        self.assertEqual(entry["method"], "tools/list")
        self.assertEqual(entry["id"], 2)

    def test_recursive_case_insensitive_redaction_does_not_mutate_payload(
        self,
    ) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "API_KEY": "alpha",
                "nested": [
                    {"ApiKey": "bravo", "AUTHORIZATION": "charlie"},
                    {
                        "token": "delta",
                        "Access_Token": "echo",
                        "PASSWORD": "foxtrot",
                    },
                    {"Secret": "golf", "CLIENT_SECRET": "hotel"},
                ],
            },
            "id": 3,
        }
        original = deepcopy(payload)

        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
            show_traffic=True,
        ) as logger:
            logger.outbound("pharmacy", json.dumps(payload))

        self.assertEqual(payload, original)
        serialized_log = self.log_path.read_text(encoding="utf-8")
        visible_log = self.stderr.getvalue()
        for secret in (
            "alpha",
            "bravo",
            "charlie",
            "delta",
            "echo",
            "foxtrot",
            "golf",
            "hotel",
        ):
            self.assertNotIn(secret, serialized_log)
            self.assertNotIn(secret, visible_log)
        self.assertIn(REDACTION_MARKER, serialized_log)
        self.assertIn(REDACTION_MARKER, visible_log)

    def test_protocol_visualization_is_optional_and_diagnostics_are_sanitized(
        self,
    ) -> None:
        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
            show_traffic=False,
        ) as logger:
            logger.outbound(
                "pharmacy",
                '{"jsonrpc":"2.0","method":"tools/list","id":1}',
            )
            logger.diagnostic(
                "pharmacy",
                '{"Password":"never-show-this"}',
            )

        self.assertNotIn("tools/list", self.stderr.getvalue())
        self.assertNotIn("never-show-this", self.stderr.getvalue())
        self.assertIn(REDACTION_MARKER, self.stderr.getvalue())

    def test_log_open_failure_is_explicit(self) -> None:
        self.directory.write_text("block", encoding="utf-8")

        with self.assertRaisesRegex(MCPLogError, "Cannot open MCP log"):
            MCPProtocolLogger(self.directory / "host.jsonl")

    def _read_entries(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]

    def _remove_test_directory(self) -> None:
        if self.directory.is_dir():
            for path in self.directory.iterdir():
                path.unlink(missing_ok=True)
            self.directory.rmdir()
        else:
            self.directory.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
