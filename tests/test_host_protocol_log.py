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
    BINARY_OMISSION_MARKER,
    DEFAULT_LOG_PATH,
    REDACTION_MARKER,
    TRUNCATION_MARKER,
    WRITE_CONTENT_OMISSION_MARKER,
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

    def test_local_policy_event_is_distinct_and_redacted(self) -> None:
        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
        ) as logger:
            logger.host_event(
                "git",
                "mutation_authorized",
                {
                    "tool": "git_commit",
                    "global_tool": "git__git_commit",
                    "token": "never-show-this",
                },
            )

        entry = self._read_entries()[0]
        self.assertEqual(entry["server"], "git")
        self.assertEqual(entry["transport"], "stdio")
        self.assertEqual(entry["direction"], "local")
        self.assertEqual(entry["message_type"], "mutation_authorized")
        self.assertEqual(entry["method"], "tools/call")
        self.assertEqual(entry["payload"]["token"], REDACTION_MARKER)
        self.assertNotIn("never-show-this", json.dumps(entry))

    def test_large_payload_is_redacted_then_truncated_as_valid_json(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 8,
            "result": {
                "token": "never-log-this-secret",
                "items": ["x" * 64 for _ in range(40)],
            },
        }
        original = deepcopy(message)
        wire_payload = json.dumps(message)

        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
            max_payload_chars=512,
            max_string_chars=64,
        ) as logger:
            logger.inbound("filesystem", wire_payload)

        self.assertEqual(message, original)
        self.assertEqual(json.dumps(message), wire_payload)
        entry = self._read_entries()[0]
        self.assertTrue(entry["payload"]["truncated"])
        self.assertEqual(entry["payload"]["marker"], TRUNCATION_MARKER)
        self.assertLessEqual(
            len(json.dumps(entry["payload"], separators=(",", ":"))),
            512,
        )
        serialized_log = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("never-log-this-secret", serialized_log)
        self.assertIn(REDACTION_MARKER, serialized_log)

    def test_long_strings_and_binary_fields_are_bounded_or_omitted(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 9,
            "result": {
                "content": [{"type": "text", "text": "a" * 500}],
                "data": "base64" * 200,
                "blob": "binary" * 200,
            },
        }

        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
            max_payload_chars=2_048,
            max_string_chars=80,
        ) as logger:
            logger.inbound("filesystem", json.dumps(payload))

        serialized = self.log_path.read_text(encoding="utf-8")
        self.assertIn(TRUNCATION_MARKER, serialized)
        self.assertIn(BINARY_OMISSION_MARKER, serialized)
        self.assertNotIn("base64" * 20, serialized)
        self.assertNotIn("binary" * 20, serialized)

    def test_filesystem_write_and_edit_bodies_are_not_logged(self) -> None:
        write_body = "private body that must reach the server"
        edit_old = "original private paragraph"
        edit_new = "replacement private paragraph"
        messages = (
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "C:/safe/file.txt", "content": write_body},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "edit_file",
                    "arguments": {
                        "path": "C:/safe/file.txt",
                        "edits": [{"oldText": edit_old, "newText": edit_new}],
                    },
                },
            },
        )
        originals = deepcopy(messages)

        with MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=self.stderr,
        ) as logger:
            for message in messages:
                logger.outbound("filesystem", json.dumps(message))

        self.assertEqual(messages, originals)
        serialized = self.log_path.read_text(encoding="utf-8")
        self.assertIn(WRITE_CONTENT_OMISSION_MARKER, serialized)
        for protected_text in (write_body, edit_old, edit_new):
            self.assertNotIn(protected_text, serialized)

    def test_log_limits_reject_booleans_and_out_of_range_values(self) -> None:
        for options in (
            {"max_payload_chars": True},
            {"max_payload_chars": 255},
            {"max_string_chars": 63},
            {"max_string_chars": 1_000_001},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    MCPProtocolLogger(self.log_path, **options)

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
