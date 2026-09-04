"""Tests for repository boundaries and explicit mutation authorization."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from copy import deepcopy
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
    RegisteredTool,
    RepositoryPolicyConfig,
    RepositoryPolicyViolation,
    StdioServerConfig,
    prepare_repository_invocation,
)


class RepositoryInvocationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        RUNTIME_DIRECTORY.mkdir(exist_ok=True)
        self.temporary_root = RUNTIME_DIRECTORY / f"policy-{uuid4().hex}"
        self.temporary_root.mkdir()
        self.addCleanup(_remove_generated_tree, self.temporary_root)
        self.root = self.temporary_root / "allowed"
        self.root.mkdir()
        self.external = self.temporary_root / "external"
        self.external.mkdir()
        self.policy = RepositoryPolicyConfig(
            root=self.root.resolve(strict=True),
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
        )

    def prepare(
        self,
        tool_name: str,
        repo_path: str,
        *,
        allow_mutation: bool = False,
    ) -> dict[str, object]:
        return prepare_repository_invocation(
            self.policy,
            tool_name=tool_name,
            arguments={"repo_path": repo_path},
            allow_mutation=allow_mutation,
        )

    def test_read_is_allowed_and_arguments_are_copied_with_canonical_path(
        self,
    ) -> None:
        arguments = {"repo_path": str(self.root), "nested": {"value": 1}}
        original = deepcopy(arguments)

        prepared = prepare_repository_invocation(
            self.policy,
            tool_name="git_status",
            arguments=arguments,
            allow_mutation=False,
        )

        self.assertEqual(arguments, original)
        self.assertIsNot(prepared, arguments)
        self.assertEqual(prepared["repo_path"], str(self.policy.root))

    def test_mutation_is_blocked_by_default_and_allowed_explicitly(self) -> None:
        with self.assertRaisesRegex(
            RepositoryPolicyViolation, "--allow-mutation"
        ) as context:
            self.prepare("git_add", str(self.root))
        self.assertEqual(context.exception.event_type, "mutation_rejected")

        prepared = self.prepare(
            "git_add",
            str(self.root),
            allow_mutation=True,
        )
        self.assertEqual(prepared["repo_path"], str(self.policy.root))

    def test_parent_segments_external_and_nonexistent_paths_are_rejected(
        self,
    ) -> None:
        parent_alias = self.root / ".." / self.root.name
        missing = self.temporary_root / "missing"
        for candidate, message in (
            (parent_alias, "must not contain"),
            (self.external, "outside"),
            (missing, "does not exist"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(RepositoryPolicyViolation, message):
                    self.prepare("git_status", str(candidate))

    def test_relative_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(RepositoryPolicyViolation, "absolute"):
            self.prepare("git_status", "allowed")

    @unittest.skipUnless(os.name == "nt", "Windows path-case behavior")
    def test_windows_case_alias_is_accepted(self) -> None:
        alias = str(self.root).swapcase()

        prepared = self.prepare("git_status", alias)

        self.assertEqual(prepared["repo_path"], str(self.policy.root))

    def test_symlink_escape_is_rejected_when_symlinks_are_available(self) -> None:
        link = self.root / "escape"
        try:
            link.symlink_to(self.external, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                self.skipTest(f"Symbolic links are unavailable: {exc}")
            result = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(link),
                    str(self.external),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                self.skipTest("Neither a symbolic link nor a junction is available")

        try:
            with self.assertRaisesRegex(RepositoryPolicyViolation, "outside"):
                self.prepare("git_status", str(link))
        finally:
            if link.exists():
                link.rmdir()


class ManagerPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        RUNTIME_DIRECTORY.mkdir(exist_ok=True)
        self.root = RUNTIME_DIRECTORY / f"manager-policy-{uuid4().hex}"
        self.root.mkdir()
        self.root = self.root.resolve(strict=True)
        self.addCleanup(_remove_generated_tree, self.root)
        self.log_path = self.root / "host.jsonl"
        self.logger = MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=io.StringIO(),
        )
        self.addCleanup(self.logger.close)
        policy = RepositoryPolicyConfig(
            root=self.root,
            argument_name="repo_path",
            mutable_tools=frozenset({"git_add"}),
        )
        self.config = StdioServerConfig(
            name="git",
            command=sys.executable,
            args=("-V",),
            cwd=self.root,
            env=MappingProxyType({}),
            repository_policy=policy,
        )
        self.manager = MCPServerManager(
            HostConfig(servers=(self.config,)),
            protocol_logger=self.logger,
        )
        self.client = _RecordingClient()
        self.manager._clients["git"] = self.client
        self.manager._tools["git__git_add"] = RegisteredTool(
            namespaced_name="git__git_add",
            server_name="git",
            tool_name="git_add",
            description="Stage files.",
            input_schema={"type": "object"},
        )

    def test_local_rejection_is_logged_and_never_sent_to_server(self) -> None:
        with self.assertRaisesRegex(MCPHostError, "--allow-mutation"):
            self.manager.invoke_tool(
                "git__git_add",
                {"repo_path": str(self.root), "files": ["README.md"]},
            )

        self.assertEqual(self.client.calls, [])
        entries = self.read_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["server"], "git")
        self.assertEqual(entries[0]["direction"], "local")
        self.assertEqual(entries[0]["message_type"], "mutation_rejected")
        self.assertFalse(any(entry["direction"] == "outbound" for entry in entries))

    def test_authorization_is_logged_and_original_registered_name_is_sent(
        self,
    ) -> None:
        result = self.manager.invoke_tool(
            "git__git_add",
            {"repo_path": str(self.root), "files": ["README.md"]},
            allow_mutation=True,
        )

        self.assertEqual(result, {"content": [{"type": "text", "text": "ok"}]})
        self.assertEqual(self.client.calls[0][0], "git_add")
        self.assertEqual(self.client.calls[0][1]["repo_path"], str(self.root))
        entries = self.read_entries()
        self.assertEqual(entries[0]["message_type"], "mutation_authorized")
        self.assertEqual(entries[0]["payload"]["tool"], "git_add")

    def test_repository_rejection_is_logged_without_exposing_path(self) -> None:
        outside = self.root.parent

        with self.assertRaisesRegex(MCPHostError, "outside"):
            self.manager.invoke_tool(
                "git__git_add",
                {"repo_path": str(outside), "files": ["README.md"]},
                allow_mutation=True,
            )

        self.assertEqual(self.client.calls, [])
        entry = self.read_entries()[0]
        self.assertEqual(entry["message_type"], "repository_rejected")
        self.assertNotIn(str(outside), json.dumps(entry))

    def read_entries(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]


class ManagerCleanupTests(unittest.TestCase):
    def test_failure_stopping_either_server_does_not_skip_the_other(self) -> None:
        RUNTIME_DIRECTORY.mkdir(exist_ok=True)
        root = RUNTIME_DIRECTORY / f"manager-close-{uuid4().hex}"
        root.mkdir()
        try:
            configs = tuple(
                StdioServerConfig(
                    name=name,
                    command=sys.executable,
                    args=("-V",),
                    cwd=root,
                    env=MappingProxyType({}),
                )
                for name in ("pharmacy", "git")
            )
            for failing_name in ("pharmacy", "git"):
                with self.subTest(failing_name=failing_name):
                    logger = MCPProtocolLogger(root / f"{failing_name}.jsonl")
                    try:
                        manager = MCPServerManager(
                            HostConfig(servers=configs),
                            protocol_logger=logger,
                        )
                        clients = {
                            name: _RecordingClient(fail_on_stop=name == failing_name)
                            for name in ("pharmacy", "git")
                        }
                        manager._clients.update(clients)

                        with self.assertRaisesRegex(RuntimeError, "stop failed"):
                            manager.stop_all()

                        self.assertTrue(
                            all(client.stopped for client in clients.values())
                        )
                        self.assertEqual(manager._clients, {})
                    finally:
                        logger.close()
        finally:
            _remove_generated_tree(root)


class _RecordingClient:
    def __init__(self, *, fail_on_stop: bool = False) -> None:
        self.is_ready = True
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_on_stop = fail_on_stop
        self.stopped = False

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        self.calls.append((tool_name, deepcopy(arguments)))
        return {"content": [{"type": "text", "text": "ok"}]}

    def stop(self) -> None:
        self.stopped = True
        if self.fail_on_stop:
            raise RuntimeError("stop failed")


def _remove_generated_tree(path: Path) -> None:
    canonical_parent = path.parent.resolve(strict=True)
    if canonical_parent != RUNTIME_DIRECTORY.resolve(strict=True):
        raise AssertionError("Refusing to remove a directory outside runtime")
    if path.exists():
        shutil.rmtree(path)


if __name__ == "__main__":
    unittest.main()
