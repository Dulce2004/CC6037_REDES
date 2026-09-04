"""Real combined workflow for Pharmacy, Git, and official Filesystem MCP."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
RUNTIME_DIRECTORY = PROJECT_DIRECTORY / "runtime"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    FilesystemPolicyConfig,
    HostConfig,
    MCPHostError,
    MCPProtocolLogger,
    MCPServerManager,
    RepositoryPolicyConfig,
    StdioServerConfig,
    WRITE_CONTENT_OMISSION_MARKER,
)

GIT_PACKAGE = "mcp-server-git==2026.8.18"
FILESYSTEM_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.8.31"
EXPECTED_FILESYSTEM_TOOLS = {
    "read_file",
    "read_text_file",
    "read_media_file",
    "read_multiple_files",
    "write_file",
    "edit_file",
    "create_directory",
    "list_directory",
    "list_directory_with_sizes",
    "directory_tree",
    "move_file",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
}
MINIMUM_GIT_TOOLS = {
    "git_status",
    "git_diff_unstaged",
    "git_diff_staged",
    "git_add",
    "git_commit",
    "git_log",
}


@unittest.skipUnless(
    shutil.which("git") and shutil.which("uvx") and shutil.which("npx"),
    "git, uvx, and npx are required for the real combined MCP test",
)
class OfficialFilesystemGitIntegrationTests(unittest.TestCase):
    def test_real_three_server_filesystem_to_git_workflow_is_isolated(self) -> None:
        main_head_before = self.git_output(PROJECT_DIRECTORY, "rev-parse", "HEAD")
        main_status_before = self.git_output(
            PROJECT_DIRECTORY,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        removed_root: Path | None = None

        with generated_runtime_directory() as temporary_root:
            removed_root = temporary_root
            repository = temporary_root / "filesystem-git-demo"
            repository.mkdir()
            external = temporary_root / "outside-filesystem-root"
            external.mkdir()
            external_file = external / "private.txt"
            external_file.write_text("outside", encoding="utf-8")
            self.git(repository, "init")
            self.git(repository, "config", "--local", "user.name", "Academic Demo")
            self.git(
                repository,
                "config",
                "--local",
                "user.email",
                "student@example.invalid",
            )

            log_path = temporary_root / "mcp-host.jsonl"
            logger = MCPProtocolLogger(log_path, diagnostic_stream=io.StringIO())
            manager = MCPServerManager(
                HostConfig(
                    servers=(
                        self.pharmacy_config(temporary_root / "pharmacy.sqlite3"),
                        self.git_config(repository),
                        self.filesystem_config(repository),
                    )
                ),
                protocol_logger=logger,
            )
            processes: dict[str, subprocess.Popen[str]] = {}
            try:
                manager.start_all()
                processes = {
                    name: client._process
                    for name, client in manager._clients.items()
                }
                self.assertEqual(set(processes), {"pharmacy", "git", "filesystem"})
                self.assertTrue(all(process.poll() is None for process in processes.values()))

                self.assertEqual(len(manager.list_tools("pharmacy")), 7)
                git_names = {tool.tool_name for tool in manager.list_tools("git")}
                self.assertTrue(MINIMUM_GIT_TOOLS.issubset(git_names))
                filesystem_tools = manager.list_tools("filesystem")
                filesystem_names = {tool.tool_name for tool in filesystem_tools}
                self.assertEqual(filesystem_names, EXPECTED_FILESYSTEM_TOOLS)
                self.assertEqual(
                    {tool.namespaced_name for tool in filesystem_tools},
                    {f"filesystem__{name}" for name in EXPECTED_FILESYSTEM_TOOLS},
                )
                self.assertTrue(
                    manager.resolve_tool(
                        "filesystem__read_text_file"
                    ).annotations["readOnlyHint"]
                )
                self.assertFalse(
                    manager.resolve_tool(
                        "filesystem__write_file"
                    ).annotations["readOnlyHint"]
                )
                write_annotations = manager.resolve_tool(
                    "filesystem__write_file"
                ).annotations
                self.assertTrue(write_annotations["destructiveHint"])
                self.assertTrue(write_annotations["idempotentHint"])
                self.assertFalse(write_annotations["openWorldHint"])
                self.assertIn(
                    "outputSchema",
                    manager.resolve_tool("filesystem__read_text_file").to_dict(),
                )
                filesystem_client = manager._clients["filesystem"]
                self.assertEqual(
                    filesystem_client.server_info["name"],
                    "secure-filesystem-server",
                )
                self.assertEqual(
                    filesystem_client.server_capabilities["tools"]["listChanged"],
                    True,
                )

                pharmacy_stock = manager.invoke_tool(
                    "pharmacy__check_stock",
                    {"sku": "MED-ANA-001", "branch_id": "zona-5"},
                )
                self.assertEqual(
                    pharmacy_stock["structuredContent"]["stock"][0]["quantity"],
                    25,
                )

                allowed = manager.invoke_tool(
                    "filesystem__list_allowed_directories", {}
                )
                self.assert_text_result(allowed, str(repository))
                listing = manager.invoke_tool(
                    "filesystem__list_directory", {"path": str(repository)}
                )
                self.assertIsInstance(listing.get("content"), list)

                readme = repository / "README.md"
                readme_text = (
                    "# Filesystem MCP demo\n\n"
                    "Created by the official Filesystem MCP server.\n"
                )
                sent_before = self.outbound_tool_calls(log_path, "filesystem")
                with self.assertRaisesRegex(MCPHostError, "--allow-mutation"):
                    manager.invoke_tool(
                        "filesystem__write_file",
                        {"path": str(readme), "content": readme_text},
                    )
                self.assertFalse(readme.exists())
                self.assertEqual(
                    self.outbound_tool_calls(log_path, "filesystem"),
                    sent_before,
                )

                written = manager.invoke_tool(
                    "filesystem__write_file",
                    {"path": str(readme), "content": readme_text},
                    allow_mutation=True,
                )
                self.assert_text_result(written, "successfully wrote")
                self.assertEqual(readme.read_text(encoding="utf-8"), readme_text)
                read_back = manager.invoke_tool(
                    "filesystem__read_text_file", {"path": str(readme)}
                )
                self.assert_text_result(read_back, "Created by the official")

                sent_before_escape = self.outbound_tool_calls(log_path, "filesystem")
                with self.assertRaisesRegex(MCPHostError, "outside"):
                    manager.invoke_tool(
                        "filesystem__read_text_file",
                        {"path": str(external_file)},
                    )
                self.assertEqual(
                    self.outbound_tool_calls(log_path, "filesystem"),
                    sent_before_escape,
                )

                repo_argument = {"repo_path": str(repository)}
                status = manager.invoke_tool("git__git_status", repo_argument)
                self.assert_text_result(status, "README.md")
                unstaged = manager.invoke_tool(
                    "git__git_diff_unstaged", repo_argument
                )
                self.assert_text_result(unstaged, "Unstaged changes")
                manager.invoke_tool(
                    "git__git_add",
                    {**repo_argument, "files": ["README.md"]},
                    allow_mutation=True,
                )
                staged = manager.invoke_tool("git__git_diff_staged", repo_argument)
                self.assert_text_result(staged, "Filesystem MCP demo")
                committed = manager.invoke_tool(
                    "git__git_commit",
                    {
                        **repo_argument,
                        "message": "docs: add filesystem MCP demo",
                    },
                    allow_mutation=True,
                )
                commit_hash = self.git_output(repository, "rev-parse", "HEAD")
                self.assertIn(commit_hash, self.result_text(committed))
                history = manager.invoke_tool(
                    "git__git_log", {**repo_argument, "max_count": 1}
                )
                self.assert_text_result(history, "docs: add filesystem MCP demo")
                self.assertEqual(
                    self.git_output(repository, "status", "--porcelain=v1"),
                    "",
                )
                self.assertEqual(readme.read_text(encoding="utf-8"), readme_text)
            finally:
                try:
                    manager.stop_all()
                finally:
                    logger.close()

            self.assertTrue(processes)
            self.assertTrue(all(process.poll() is not None for process in processes.values()))
            entries = self.read_entries(log_path)
            self.assertEqual(
                {entry["server"] for entry in entries},
                {"pharmacy", "git", "filesystem"},
            )
            for server in ("pharmacy", "git", "filesystem"):
                methods = {
                    entry.get("method")
                    for entry in entries
                    if entry["server"] == server
                }
                self.assertTrue(
                    {"initialize", "notifications/initialized", "tools/list"}
                    .issubset(methods)
                )
            event_types = {
                entry["message_type"]
                for entry in entries
                if entry["server"] == "filesystem"
                and entry["direction"] == "local"
            }
            self.assertTrue(
                {
                    "filesystem_read_allowed",
                    "filesystem_rejected",
                    "mutation_rejected",
                    "mutation_authorized",
                }.issubset(event_types)
            )
            filesystem_initialize = next(
                entry
                for entry in entries
                if entry["server"] == "filesystem"
                and entry["direction"] == "inbound"
                and entry.get("id") == 1
            )
            self.assertEqual(
                filesystem_initialize["payload"]["result"]["protocolVersion"],
                "2025-11-25",
            )
            write_request = next(
                entry
                for entry in entries
                if entry["server"] == "filesystem"
                and entry["direction"] == "outbound"
                and entry.get("method") == "tools/call"
                and entry["payload"]["params"]["name"] == "write_file"
            )
            self.assertIn(
                WRITE_CONTENT_OMISSION_MARKER,
                write_request["payload"]["params"]["arguments"]["content"],
            )

        self.assertIsNotNone(removed_root)
        self.assertFalse(removed_root.exists())
        self.assertEqual(
            self.git_output(PROJECT_DIRECTORY, "rev-parse", "HEAD"),
            main_head_before,
        )
        self.assertEqual(
            self.git_output(
                PROJECT_DIRECTORY,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ),
            main_status_before,
        )
        self.assertFalse((PROJECT_DIRECTORY / "node_modules").exists())

    @staticmethod
    def filesystem_config(root: Path) -> StdioServerConfig:
        if os.name == "nt":
            command = os.environ.get("COMSPEC", "cmd.exe")
            args = ("/d", "/s", "/c", "npx", "-y", FILESYSTEM_PACKAGE, str(root))
        else:
            command = "npx"
            args = ("-y", FILESYSTEM_PACKAGE, str(root))
        return StdioServerConfig(
            name="filesystem",
            command=command,
            args=args,
            cwd=root,
            env=MappingProxyType(
                {
                    "npm_config_offline": "true",
                    "npm_config_update_notifier": "false",
                }
            ),
            request_timeout_seconds=60,
            shutdown_timeout_seconds=10,
            filesystem_policy=FilesystemPolicyConfig(
                root=root.resolve(strict=True),
                path_arguments=("path", "paths", "source", "destination"),
                creation_arguments=MappingProxyType(
                    {
                        "write_file": frozenset({"path"}),
                        "create_directory": frozenset({"path"}),
                        "move_file": frozenset({"destination"}),
                    }
                ),
            ),
        )

    @staticmethod
    def git_config(repository: Path) -> StdioServerConfig:
        return StdioServerConfig(
            name="git",
            command="uvx",
            args=(
                "--from",
                GIT_PACKAGE,
                "mcp-server-git",
                "--repository",
                str(repository),
            ),
            cwd=repository,
            env=MappingProxyType({}),
            request_timeout_seconds=60,
            shutdown_timeout_seconds=10,
            repository_policy=RepositoryPolicyConfig(
                root=repository.resolve(strict=True),
                argument_name="repo_path",
                mutable_tools=frozenset(
                    {
                        "git_add",
                        "git_commit",
                        "git_reset",
                        "git_checkout",
                        "git_create_branch",
                    }
                ),
            ),
        )

    @staticmethod
    def pharmacy_config(database_path: Path) -> StdioServerConfig:
        return StdioServerConfig(
            name="pharmacy",
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
    def git(repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    @staticmethod
    def git_output(repository: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()

    @staticmethod
    def result_text(result: object) -> str:
        if not isinstance(result, dict):
            raise AssertionError("MCP result is not an object")
        content = result.get("content")
        if not isinstance(content, list):
            raise AssertionError("MCP result has no content array")
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )

    def assert_text_result(self, result: object, expected: str) -> None:
        self.assertIn(expected.casefold(), self.result_text(result).casefold())

    @staticmethod
    def read_entries(log_path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]

    def outbound_tool_calls(self, log_path: Path, server: str) -> int:
        return sum(
            entry["server"] == server
            and entry["direction"] == "outbound"
            and entry.get("method") == "tools/call"
            for entry in self.read_entries(log_path)
        )


@contextmanager
def generated_runtime_directory() -> Iterator[Path]:
    RUNTIME_DIRECTORY.mkdir(exist_ok=True)
    path = RUNTIME_DIRECTORY / f"filesystem-git-integration-{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        if path.parent.resolve(strict=True) != RUNTIME_DIRECTORY.resolve(strict=True):
            raise AssertionError("Refusing to remove a directory outside runtime")
        if path.exists():
            shutil.rmtree(path, onexc=_remove_readonly)


def _remove_readonly(function: object, path: str, exception: BaseException) -> None:
    if not isinstance(exception, PermissionError) or not callable(function):
        raise exception
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    function(path)


if __name__ == "__main__":
    unittest.main()
