"""Tests for Filesystem MCP path boundaries and mutation authorization."""

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
    FilesystemPolicyConfig,
    FilesystemPolicyViolation,
    HostConfig,
    MCPHostError,
    MCPProtocolLogger,
    MCPServerManager,
    RegisteredTool,
    StdioServerConfig,
    prepare_filesystem_invocation,
)


class FilesystemInvocationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        RUNTIME_DIRECTORY.mkdir(exist_ok=True)
        self.temporary_root = RUNTIME_DIRECTORY / f"filesystem-policy-{uuid4().hex}"
        self.temporary_root.mkdir()
        self.addCleanup(_remove_generated_tree, self.temporary_root)
        self.root = self.temporary_root / "allowed"
        self.root.mkdir()
        self.nested = self.root / "nested"
        self.nested.mkdir()
        self.file = self.nested / "notes.txt"
        self.file.write_text("safe", encoding="utf-8")
        self.external = self.temporary_root / "external"
        self.external.mkdir()
        self.external_file = self.external / "private.txt"
        self.external_file.write_text("outside", encoding="utf-8")
        self.policy = _filesystem_policy(self.root)

    def prepare(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        annotations: dict[str, object] | None = None,
        allow_mutation: bool = False,
    ):
        return prepare_filesystem_invocation(
            self.policy,
            tool_name=tool_name,
            arguments=arguments,
            annotations=(
                {"readOnlyHint": True, "openWorldHint": False}
                if annotations is None
                else annotations
            ),
            allow_mutation=allow_mutation,
        )

    def test_exact_root_and_descendant_are_allowed_without_rewriting(self) -> None:
        arguments = {"path": str(self.file), "extra": {"value": 1}}
        original = deepcopy(arguments)

        invocation = self.prepare("read_text_file", arguments)
        root_invocation = self.prepare(
            "list_directory", {"path": str(self.root)}
        )

        self.assertEqual(arguments, original)
        self.assertIsNot(invocation.arguments, arguments)
        self.assertEqual(invocation.arguments["path"], str(self.file))
        self.assertEqual(invocation.path_count, 1)
        self.assertFalse(invocation.mutation_required)
        self.assertEqual(root_invocation.arguments["path"], str(self.root))

    def test_relative_parent_sibling_external_and_missing_reads_are_rejected(
        self,
    ) -> None:
        sibling_prefix = self.root.parent / f"{self.root.name}-sibling"
        sibling_prefix.mkdir()
        candidates = (
            ("nested/notes.txt", "absolute"),
            (str(self.root / "nested" / ".." / "notes.txt"), "must not contain"),
            (str(sibling_prefix), "outside"),
            (str(self.external_file), "outside"),
            (str(self.root / "missing.txt"), "does not exist"),
        )
        for candidate, message in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(FilesystemPolicyViolation, message):
                    self.prepare("read_text_file", {"path": candidate})

    def test_creation_paths_require_safe_existing_ancestor(self) -> None:
        inside = self.root / "new" / "README.md"
        outside = self.external / "new" / "README.md"

        invocation = self.prepare(
            "write_file",
            {"path": str(inside), "content": "hello"},
            annotations={"readOnlyHint": False, "destructiveHint": True},
            allow_mutation=True,
        )
        self.assertEqual(invocation.arguments["path"], str(inside))

        with self.assertRaisesRegex(FilesystemPolicyViolation, "outside"):
            self.prepare(
                "write_file",
                {"path": str(outside), "content": "hello"},
                annotations={"readOnlyHint": False},
                allow_mutation=True,
            )

    def test_every_array_path_and_both_move_endpoints_are_checked(self) -> None:
        invocation = self.prepare(
            "read_multiple_files",
            {"paths": [str(self.file), str(self.root / "nested")]},
        )
        self.assertEqual(invocation.path_count, 2)

        with self.assertRaisesRegex(FilesystemPolicyViolation, "outside"):
            self.prepare(
                "read_multiple_files",
                {"paths": [str(self.file), str(self.external_file)]},
            )

        destination = self.root / "moved.txt"
        moved = self.prepare(
            "move_file",
            {"source": str(self.file), "destination": str(destination)},
            annotations={"readOnlyHint": False, "destructiveHint": True},
            allow_mutation=True,
        )
        self.assertEqual(moved.path_count, 2)
        with self.assertRaisesRegex(FilesystemPolicyViolation, "outside"):
            self.prepare(
                "move_file",
                {
                    "source": str(self.file),
                    "destination": str(self.external / "moved.txt"),
                },
                annotations={"readOnlyHint": False},
                allow_mutation=True,
            )

    def test_invalid_array_and_empty_path_values_are_rejected(self) -> None:
        for arguments in (
            {"paths": []},
            {"paths": str(self.file)},
            {"paths": [str(self.file), ""]},
            {"path": ""},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(FilesystemPolicyViolation):
                    self.prepare("read_multiple_files", arguments)

    def test_annotations_are_interpreted_conservatively(self) -> None:
        self.prepare(
            "list_allowed_directories",
            {},
            annotations={"readOnlyHint": True},
        )
        for annotations in (
            None,
            {},
            {"readOnlyHint": False},
            {"readOnlyHint": True, "destructiveHint": True},
        ):
            with self.subTest(annotations=annotations):
                effective = {} if annotations is None else annotations
                with self.assertRaisesRegex(
                    FilesystemPolicyViolation, "--allow-mutation"
                ):
                    self.prepare(
                        "unknown_or_write_tool",
                        {"path": str(self.file)},
                        annotations=effective,
                    )

        allowed = self.prepare(
            "unknown_or_write_tool",
            {"path": str(self.file)},
            annotations={},
            allow_mutation=True,
        )
        self.assertTrue(allowed.mutation_required)

    @unittest.skipUnless(os.name == "nt", "Windows path-case behavior")
    def test_windows_case_alias_is_allowed_and_not_rewritten(self) -> None:
        alias = str(self.file).swapcase()
        invocation = self.prepare("read_text_file", {"path": alias})
        self.assertEqual(invocation.arguments["path"], alias)

    def test_symlink_or_junction_escape_is_rejected(self) -> None:
        link = self.root / "escape"
        _create_directory_link_or_skip(self, link, self.external)
        try:
            for candidate, tool, allow_mutation in (
                (link / "private.txt", "read_text_file", False),
                (link / "new.txt", "write_file", True),
            ):
                with self.subTest(candidate=candidate):
                    with self.assertRaisesRegex(FilesystemPolicyViolation, "outside"):
                        self.prepare(
                            tool,
                            {"path": str(candidate)},
                            annotations={
                                "readOnlyHint": not allow_mutation,
                                "destructiveHint": allow_mutation,
                            },
                            allow_mutation=allow_mutation,
                        )
        finally:
            _remove_directory_link(link)


class FilesystemManagerPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        RUNTIME_DIRECTORY.mkdir(exist_ok=True)
        self.temporary_root = RUNTIME_DIRECTORY / f"filesystem-manager-{uuid4().hex}"
        self.temporary_root.mkdir()
        self.addCleanup(_remove_generated_tree, self.temporary_root)
        self.root = self.temporary_root / "allowed"
        self.root.mkdir()
        self.log_path = self.temporary_root / "host.jsonl"
        self.logger = MCPProtocolLogger(self.log_path, diagnostic_stream=io.StringIO())
        self.addCleanup(self.logger.close)
        config = StdioServerConfig(
            name="filesystem",
            command=sys.executable,
            args=("-V",),
            cwd=self.root,
            env=MappingProxyType({}),
            filesystem_policy=_filesystem_policy(self.root),
        )
        self.manager = MCPServerManager(
            HostConfig(servers=(config,)),
            protocol_logger=self.logger,
        )
        self.client = _RecordingClient()
        self.manager._clients["filesystem"] = self.client

    def register(self, name: str, annotations: dict[str, object]) -> None:
        self.manager._tools[f"filesystem__{name}"] = RegisteredTool(
            namespaced_name=f"filesystem__{name}",
            server_name="filesystem",
            tool_name=name,
            description="Filesystem tool.",
            input_schema={"type": "object"},
            annotations=annotations,
        )

    def test_rejection_is_local_and_log_never_contains_path_or_content(self) -> None:
        self.register("write_file", {"readOnlyHint": False, "destructiveHint": True})
        secret_content = "do-not-log-this-content"
        with self.assertRaisesRegex(MCPHostError, "--allow-mutation"):
            self.manager.invoke_tool(
                "filesystem__write_file",
                {"path": str(self.root / "new.txt"), "content": secret_content},
            )

        self.assertEqual(self.client.calls, [])
        serialized = self.log_path.read_text(encoding="utf-8")
        entry = json.loads(serialized)
        self.assertEqual(entry["message_type"], "mutation_rejected")
        self.assertEqual(entry["payload"]["path_count"], 1)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(secret_content, serialized)

    def test_authorized_call_preserves_original_paths_and_logs_minimum_metadata(
        self,
    ) -> None:
        self.register("write_file", {"readOnlyHint": False})
        path_alias = str(self.root / "new.txt")
        arguments = {"path": path_alias, "content": "not-in-policy-log"}

        result = self.manager.invoke_tool(
            "filesystem__write_file",
            arguments,
            allow_mutation=True,
        )

        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual(self.client.calls, [("write_file", arguments)])
        entry = json.loads(self.log_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["message_type"], "mutation_authorized")
        self.assertEqual(
            entry["payload"],
            {
                "tool": "write_file",
                "global_tool": "filesystem__write_file",
                "path_count": 1,
                "reason": "Explicit mutation authorization accepted.",
            },
        )


class _RecordingClient:
    is_ready = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        self.calls.append((tool_name, deepcopy(arguments)))
        return {"content": [{"type": "text", "text": "ok"}]}


def _filesystem_policy(root: Path) -> FilesystemPolicyConfig:
    return FilesystemPolicyConfig(
        root=root.resolve(strict=True),
        path_arguments=("path", "paths", "source", "destination"),
        creation_arguments=MappingProxyType(
            {
                "write_file": frozenset({"path"}),
                "create_directory": frozenset({"path"}),
                "move_file": frozenset({"destination"}),
            }
        ),
    )


def _create_directory_link_or_skip(
    case: unittest.TestCase,
    link: Path,
    target: Path,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as exc:
        if os.name != "nt":
            case.skipTest(f"Symbolic links are unavailable: {exc}")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        case.skipTest("Neither a symbolic link nor a junction is available")


def _remove_directory_link(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        path.rmdir()


def _remove_generated_tree(path: Path) -> None:
    if path.parent.resolve(strict=True) != RUNTIME_DIRECTORY.resolve(strict=True):
        raise AssertionError("Refusing to remove a directory outside runtime")
    if path.exists():
        shutil.rmtree(path)


if __name__ == "__main__":
    unittest.main()
