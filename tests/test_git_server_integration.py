"""Real integration coverage for the pinned official Git MCP server."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
RUNTIME_DIRECTORY = PROJECT_DIRECTORY / "runtime"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    HostConfig,
    MCPHostError,
    MCPProtocolLogger,
    MCPServerManager,
    RepositoryPolicyConfig,
    StdioServerConfig,
)

GIT_PACKAGE = "mcp-server-git==2026.8.18"
MINIMUM_GIT_TOOLS = {
    "git_status",
    "git_diff_unstaged",
    "git_diff_staged",
    "git_add",
    "git_commit",
    "git_log",
}


@unittest.skipUnless(shutil.which("uvx"), "uvx is required for real Git MCP tests")
class OfficialGitServerIntegrationTests(unittest.TestCase):
    def test_real_two_server_workflow_is_isolated_and_closes_every_process(
        self,
    ) -> None:
        main_head_before = self.git_output(PROJECT_DIRECTORY, "rev-parse", "HEAD")
        main_status_before = self.git_output(
            PROJECT_DIRECTORY, "status", "--porcelain=v1", "--untracked-files=all"
        )

        with generated_runtime_directory() as temporary_root:
            repository = temporary_root / "demo-repository"
            repository.mkdir()
            self.git(repository, "init")
            self.git(repository, "config", "--local", "user.name", "Academic Demo")
            self.git(
                repository,
                "config",
                "--local",
                "user.email",
                "student@example.invalid",
            )
            readme = repository / "README.md"
            readme.write_text(
                "# Temporary Git MCP demo\n\n"
                "Filesystem MCP will be integrated in a later phase.\n",
                encoding="utf-8",
            )

            log_path = temporary_root / "mcp-host.jsonl"
            database_path = temporary_root / "pharmacy.sqlite3"
            logger = MCPProtocolLogger(log_path, diagnostic_stream=io.StringIO())
            manager = MCPServerManager(
                HostConfig(
                    servers=(
                        self.pharmacy_config(database_path),
                        self.git_config(repository),
                    )
                ),
                protocol_logger=logger,
            )
            pharmacy_process = None
            git_process = None
            try:
                manager.start_all()
                pharmacy_client = manager._clients["pharmacy"]
                git_client = manager._clients["git"]
                pharmacy_process = pharmacy_client._process
                git_process = git_client._process

                self.assertEqual(git_client.server_info["name"], "mcp-git")
                self.assertEqual(git_client.server_info["version"], "1.29.1")
                self.assertEqual(len(manager.list_tools("pharmacy")), 7)
                git_tools = manager.list_tools("git")
                original_names = {tool.tool_name for tool in git_tools}
                self.assertTrue(MINIMUM_GIT_TOOLS.issubset(original_names))
                self.assertEqual(
                    {tool.namespaced_name for tool in git_tools},
                    {f"git__{name}" for name in original_names},
                )
                status_tool = manager.resolve_tool("git__git_status")
                self.assertTrue(status_tool.annotations["readOnlyHint"])

                pharmacy_result = manager.invoke_tool(
                    "pharmacy__check_stock",
                    {"sku": "MED-ANA-001", "branch_id": "zona-5"},
                )
                self.assertEqual(
                    pharmacy_result["structuredContent"]["stock"][0]["quantity"],
                    25,
                )

                repo_argument = {"repo_path": str(repository)}
                status = manager.invoke_tool("git__git_status", repo_argument)
                self.assert_text_result(status, "README.md")
                unstaged = manager.invoke_tool(
                    "git__git_diff_unstaged", repo_argument
                )
                self.assert_text_result(unstaged, "Unstaged changes")

                sent_calls_before = self.outbound_tool_calls(log_path, "git")
                with self.assertRaisesRegex(MCPHostError, "--allow-mutation"):
                    manager.invoke_tool(
                        "git__git_add",
                        {**repo_argument, "files": ["README.md"]},
                    )
                self.assertEqual(
                    self.outbound_tool_calls(log_path, "git"),
                    sent_calls_before,
                )

                added = manager.invoke_tool(
                    "git__git_add",
                    {**repo_argument, "files": ["README.md"]},
                    allow_mutation=True,
                )
                self.assert_text_result(added, "staged")
                staged = manager.invoke_tool(
                    "git__git_diff_staged", repo_argument
                )
                self.assert_text_result(staged, "Temporary Git MCP demo")

                committed = manager.invoke_tool(
                    "git__git_commit",
                    {**repo_argument, "message": "docs: add demo readme"},
                    allow_mutation=True,
                )
                self.assertNotIn("structuredContent", committed)
                commit_text = self.result_text(committed)
                commit_hash = self.git_output(repository, "rev-parse", "HEAD")
                self.assertIn(commit_hash, commit_text)

                history = manager.invoke_tool(
                    "git__git_log",
                    {**repo_argument, "max_count": 1},
                )
                self.assert_text_result(history, "docs: add demo readme")
                final_status = manager.invoke_tool("git__git_status", repo_argument)
                self.assert_text_result(final_status, "working tree clean")
                self.assertEqual(
                    self.git_output(repository, "config", "--local", "user.name"),
                    "Academic Demo",
                )
                self.assertEqual(
                    self.git_output(repository, "config", "--local", "user.email"),
                    "student@example.invalid",
                )
            finally:
                try:
                    manager.stop_all()
                finally:
                    logger.close()

            self.assertIsNotNone(pharmacy_process)
            self.assertIsNotNone(git_process)
            self.assertIsNotNone(pharmacy_process.poll())
            self.assertIsNotNone(git_process.poll())
            entries = self.read_entries(log_path)
            self.assertEqual({entry["server"] for entry in entries}, {"pharmacy", "git"})
            self.assertTrue(
                all(entry["transport"] == "stdio" for entry in entries)
            )
            self.assertTrue(
                any(
                    entry.get("method") == "initialize"
                    and entry["server"] == "git"
                    for entry in entries
                )
            )
            self.assertTrue(
                any(
                    entry.get("method") == "notifications/initialized"
                    and entry["server"] == "git"
                    for entry in entries
                )
            )
            self.assertTrue(
                any(
                    entry.get("method") == "tools/list"
                    and entry["server"] == "git"
                    for entry in entries
                )
            )
            initialize_response = next(
                entry
                for entry in entries
                if entry["server"] == "git"
                and entry["direction"] == "inbound"
                and entry.get("id") == 1
            )
            initialize_result = initialize_response["payload"]["result"]
            self.assertEqual(initialize_result["protocolVersion"], "2025-11-25")
            self.assertEqual(initialize_result["serverInfo"]["name"], "mcp-git")
            self.assertFalse(
                initialize_result["capabilities"]["tools"]["listChanged"]
            )
            event_types = {
                entry["message_type"]
                for entry in entries
                if entry["direction"] == "local"
            }
            self.assertIn("mutation_rejected", event_types)
            self.assertIn("mutation_authorized", event_types)

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

    def git(self, repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def git_output(self, repository: Path, *arguments: str) -> str:
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
    path = RUNTIME_DIRECTORY / f"git-integration-{uuid4().hex}"
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
