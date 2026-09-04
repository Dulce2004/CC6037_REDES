"""Tests for strict MCP host configuration."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
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
        self.repository_path = runtime_directory / f"git-config-{uuid4().hex}"
        self.repository_path.mkdir()
        self.filesystem_path = runtime_directory / f"filesystem-config-{uuid4().hex}"
        self.filesystem_path.mkdir()
        self.addCleanup(self.config_path.unlink, missing_ok=True)
        self.addCleanup(self.repository_path.rmdir)
        self.addCleanup(self.filesystem_path.rmdir)

    def write_config(self, value: object) -> None:
        self.config_path.write_text(
            json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_default_config_contains_three_fixed_servers(self) -> None:
        config = load_host_config(
            DEFAULT_CONFIG_PATH,
            environ={
                "MCP_GIT_REPOSITORY_PATH": str(self.repository_path),
                "MCP_FILESYSTEM_ROOT": str(self.filesystem_path),
            },
        )

        self.assertEqual(len(config.servers), 3)
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
        git = config.servers[1]
        self.assertEqual(git.name, "git")
        self.assertEqual(git.command, "uvx")
        self.assertEqual(
            git.args,
            (
                "--from",
                "mcp-server-git==2026.8.18",
                "mcp-server-git",
                "--repository",
                str(self.repository_path),
            ),
        )
        self.assertEqual(git.cwd, self.repository_path)
        self.assertEqual(git.repository_policy.root, self.repository_path)
        self.assertIn("git_commit", git.repository_policy.mutable_tools)
        filesystem = config.servers[2]
        self.assertEqual(filesystem.name, "filesystem")
        if os.name == "nt":
            self.assertEqual(Path(filesystem.command).name.casefold(), "cmd.exe")
            self.assertEqual(filesystem.args[:4], ("/d", "/s", "/c", "npx"))
            package_index = 5
        else:
            self.assertEqual(filesystem.command, "npx")
            self.assertEqual(filesystem.args[0], "-y")
            package_index = 1
        self.assertEqual(
            filesystem.args[package_index],
            "@modelcontextprotocol/server-filesystem@2026.8.31",
        )
        self.assertEqual(filesystem.args[-1], str(self.filesystem_path))
        self.assertEqual(filesystem.cwd, self.filesystem_path)
        self.assertEqual(filesystem.filesystem_policy.root, self.filesystem_path)
        self.assertEqual(
            filesystem.filesystem_policy.path_arguments,
            ("path", "paths", "source", "destination"),
        )
        self.assertEqual(
            config.variables,
            ("MCP_GIT_REPOSITORY_PATH", "MCP_FILESYSTEM_ROOT"),
        )

    def test_npx_builtin_uses_controlled_platform_launchers(self) -> None:
        self.write_config(
            {
                "servers": [
                    {
                        **self.server_config("filesystem"),
                        "command": "${NPX_EXECUTABLE}",
                        "args": ["-y", "example-package", "root"],
                    }
                ]
            }
        )

        with patch("pharmacy_mcp.host.config._WINDOWS_PLATFORM", False):
            portable = load_host_config(self.config_path)
        self.assertEqual(portable.servers[0].argv, ("npx", "-y", "example-package", "root"))

        with patch("pharmacy_mcp.host.config._WINDOWS_PLATFORM", True):
            windows = load_host_config(self.config_path)
        self.assertEqual(Path(windows.servers[0].command).name.casefold(), "cmd.exe")
        self.assertEqual(
            windows.servers[0].args,
            ("/d", "/s", "/c", "npx", "-y", "example-package", "root"),
        )

    def test_filesystem_policy_is_validated_canonical_and_immutable(self) -> None:
        server = {
            **self.server_config("filesystem"),
            "filesystem_policy": {
                "root": str(self.filesystem_path),
                "path_arguments": ["path", "paths", "source", "destination"],
                "creation_arguments": {
                    "write_file": ["path"],
                    "move_file": ["destination"],
                },
            },
        }
        self.write_config({"servers": [server]})

        policy = load_host_config(self.config_path).servers[0].filesystem_policy

        self.assertEqual(policy.root, self.filesystem_path.resolve(strict=True))
        self.assertEqual(policy.creation_arguments["write_file"], frozenset({"path"}))
        with self.assertRaises(TypeError):
            policy.creation_arguments["other"] = frozenset({"path"})

    def test_filesystem_policy_rejects_unsafe_roots_and_invalid_shapes(self) -> None:
        root_file = self.filesystem_path / "not-a-directory.txt"
        root_file.write_text("file", encoding="utf-8")
        self.addCleanup(root_file.unlink, missing_ok=True)
        valid_policy = {
            "root": str(self.filesystem_path),
            "path_arguments": ["path", "paths", "source", "destination"],
            "creation_arguments": {"write_file": ["path"]},
        }
        cases = (
            {**valid_policy, "root": str(Path(self.filesystem_path.anchor))},
            {**valid_policy, "root": str(Path.home())},
            {**valid_policy, "root": str(PROJECT_DIRECTORY)},
            {
                **valid_policy,
                "root": str(
                    self.filesystem_path.parent
                    / "unused"
                    / ".."
                    / self.filesystem_path.name
                ),
            },
            {**valid_policy, "root": str(self.filesystem_path / "missing")},
            {**valid_policy, "root": str(root_file)},
            {**valid_policy, "path_arguments": ["path", "path"]},
            {**valid_policy, "creation_arguments": {"write_file": ["unknown"]}},
        )
        for policy in cases:
            with self.subTest(policy=policy):
                self.write_config(
                    {
                        "servers": [
                            {
                                **self.server_config("filesystem"),
                                "filesystem_policy": policy,
                            }
                        ]
                    }
                )
                with self.assertRaises(HostConfigurationError):
                    load_host_config(self.config_path)

    def test_repository_and_filesystem_policy_are_mutually_exclusive(self) -> None:
        self.write_config(
            {
                "servers": [
                    {
                        **self.server_config("filesystem"),
                        "repository_policy": {
                            "root": str(self.repository_path),
                            "mutable_tools": ["git_add"],
                        },
                        "filesystem_policy": {
                            "root": str(self.filesystem_path),
                            "path_arguments": ["path"],
                            "creation_arguments": {"write_file": ["path"]},
                        },
                    }
                ]
            }
        )
        with self.assertRaisesRegex(HostConfigurationError, "cannot combine"):
            load_host_config(self.config_path)

    def test_required_declared_variable_must_be_present_and_nonempty(self) -> None:
        self.write_config(
            {
                "variables": ["TEST_REPOSITORY"],
                "servers": [
                    {
                        **self.server_config("git"),
                        "cwd": "${TEST_REPOSITORY}",
                    }
                ],
            }
        )

        for environment in ({}, {"TEST_REPOSITORY": ""}):
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(HostConfigurationError, "missing or empty"):
                    load_host_config(self.config_path, environ=environment)

    def test_only_explicitly_declared_variables_are_expanded(self) -> None:
        self.write_config(
            {
                "servers": [
                    {
                        **self.server_config("git"),
                        "cwd": "${PATH}",
                    }
                ]
            }
        )

        with self.assertRaisesRegex(HostConfigurationError, "undeclared"):
            load_host_config(
                self.config_path,
                environ={"PATH": str(self.repository_path)},
            )

    def test_repository_policy_is_validated_and_canonicalized(self) -> None:
        self.write_config(
            {
                "variables": ["TEST_REPOSITORY"],
                "servers": [
                    {
                        **self.server_config("git"),
                        "cwd": "${TEST_REPOSITORY}",
                        "repository_policy": {
                            "root": "${TEST_REPOSITORY}",
                            "argument": "repo_path",
                            "mutable_tools": ["git_add", "git_commit"],
                        },
                    }
                ],
            }
        )

        config = load_host_config(
            self.config_path,
            environ={"TEST_REPOSITORY": str(self.repository_path)},
        )

        policy = config.servers[0].repository_policy
        self.assertIsNotNone(policy)
        self.assertEqual(policy.root, self.repository_path.resolve(strict=True))
        self.assertEqual(policy.argument_name, "repo_path")
        self.assertEqual(policy.mutable_tools, frozenset({"git_add", "git_commit"}))

    def test_repository_policy_rejects_missing_duplicate_or_nonexistent_data(
        self,
    ) -> None:
        base = {
            **self.server_config("git"),
            "repository_policy": {
                "root": str(self.repository_path),
                "mutable_tools": ["git_add"],
            },
        }
        cases = (
            {**base, "repository_policy": {"root": str(self.repository_path)}},
            {
                **base,
                "repository_policy": {
                    "root": str(self.repository_path),
                    "mutable_tools": ["git_add", "git_add"],
                },
            },
            {
                **base,
                "repository_policy": {
                    "root": str(self.repository_path / "missing"),
                    "mutable_tools": ["git_add"],
                },
            },
        )

        for server in cases:
            with self.subTest(server=server):
                self.write_config({"servers": [server]})
                with self.assertRaises(HostConfigurationError):
                    load_host_config(self.config_path)

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
