"""Tests for strict MCP host configuration."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    HostConfigurationError,
    load_host_config,
)


class HostConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_directory = PROJECT_DIRECTORY / "runtime"
        runtime_directory.mkdir(exist_ok=True)
        self.config_path = runtime_directory / f"host-{uuid4().hex}.json"
        self.addCleanup(self.config_path.unlink, missing_ok=True)

    def write_config(self, value: object) -> None:
        self.config_path.write_text(
            json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_default_config_contains_only_local_pharmacy_server(self) -> None:
        config = load_host_config(DEFAULT_CONFIG_PATH)

        self.assertEqual(len(config.servers), 1)
        pharmacy = config.servers[0]
        self.assertEqual(pharmacy.name, "pharmacy")
        self.assertEqual(pharmacy.transport, "stdio")
        self.assertEqual(pharmacy.command, sys.executable)
        self.assertEqual(pharmacy.cwd, PROJECT_DIRECTORY)
        self.assertEqual(
            pharmacy.args,
            ("-B", "-m", "pharmacy_mcp.server.stdio"),
        )
        self.assertEqual(pharmacy.env["PYTHONPATH"], "src")

    def test_config_supports_multiple_unique_stdio_servers(self) -> None:
        self.write_config(
            {
                "servers": [
                    self.server_config("pharmacy"),
                    self.server_config("pharmacy_backup"),
                ]
            }
        )

        config = load_host_config(self.config_path)

        self.assertEqual(
            [server.name for server in config.servers],
            ["pharmacy", "pharmacy_backup"],
        )

    def test_duplicate_server_names_are_rejected(self) -> None:
        self.write_config(
            {
                "servers": [
                    self.server_config("pharmacy"),
                    self.server_config("pharmacy"),
                ]
            }
        )

        with self.assertRaisesRegex(HostConfigurationError, "unique"):
            load_host_config(self.config_path)

    def test_server_names_reject_dots_ambiguous_separator_and_unsafe_characters(
        self,
    ) -> None:
        for name in (
            "pharmacy.local",
            "pharmacy__backup",
            "pharmacy/local",
            "pharmacy_",
        ):
            with self.subTest(name=name):
                self.write_config({"servers": [self.server_config(name)]})
                with self.assertRaises(HostConfigurationError):
                    load_host_config(self.config_path)

    def test_non_stdio_transport_is_rejected(self) -> None:
        server = self.server_config("pharmacy")
        server["transport"] = "http"
        self.write_config({"servers": [server]})

        with self.assertRaisesRegex(HostConfigurationError, "stdio"):
            load_host_config(self.config_path)

    def test_unknown_fields_and_invalid_timeouts_are_rejected(self) -> None:
        cases = (
            {**self.server_config("pharmacy"), "unknown": True},
            {
                **self.server_config("pharmacy"),
                "request_timeout_seconds": True,
            },
            {
                **self.server_config("pharmacy"),
                "shutdown_timeout_seconds": 0,
            },
            {
                **self.server_config("pharmacy"),
                "request_timeout_seconds": float("nan"),
            },
        )

        for server in cases:
            with self.subTest(server=server):
                self.write_config({"servers": [server]})
                with self.assertRaises(HostConfigurationError):
                    load_host_config(self.config_path)

    def test_empty_or_malformed_server_collection_is_rejected(self) -> None:
        for value in ({}, {"servers": []}, {"servers": "pharmacy"}):
            with self.subTest(value=value):
                self.write_config(value)
                with self.assertRaises(HostConfigurationError):
                    load_host_config(self.config_path)

    @staticmethod
    def server_config(name: str) -> dict[str, object]:
        return {
            "name": name,
            "transport": "stdio",
            "command": "${PYTHON_EXECUTABLE}",
            "args": ["-B", "-m", "pharmacy_mcp.server.stdio"],
            "cwd": "..",
            "env": {"PYTHONPATH": "src"},
        }


if __name__ == "__main__":
    unittest.main()
