"""Fully simulated LLM demonstration over the three real MCP child servers."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    AnthropicMessage,
    AnthropicSettings,
    ChatOrchestrator,
    FilesystemPolicyConfig,
    HostConfig,
    MCPProtocolLogger,
    MCPServerManager,
    RepositoryPolicyConfig,
    StdioServerConfig,
)

GIT_PACKAGE = "mcp-server-git==2026.8.18"
FILESYSTEM_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.8.31"


@unittest.skipUnless(
    shutil.which("git") and shutil.which("uvx") and shutil.which("npx"),
    "git, uvx, and npx are required for the simulated chat integration",
)
class SimulatedThreeServerChatIntegrationTests(unittest.TestCase):
    def test_complete_fake_llm_workflow_is_safe_stateful_and_cleaned_up(self) -> None:
        temporary_root = Path(
            tempfile.mkdtemp(prefix=f"pharmacy-mcp-chat-{uuid4().hex}-")
        ).resolve(strict=True)
        temporary_parent = Path(tempfile.gettempdir()).resolve(strict=True)
        processes = {}
        try:
            repository = temporary_root / "repository"
            repository.mkdir()
            _git(repository, "init")
            seed = repository / "seed.txt"
            seed.write_text("safe seed\n", encoding="utf-8")
            rejected = repository / "rejected.txt"
            approved = repository / "approved.txt"
            log_path = temporary_root / "host.jsonl"
            diagnostics = io.StringIO()
            logger = MCPProtocolLogger(log_path, diagnostic_stream=diagnostics)
            manager = MCPServerManager(
                HostConfig(
                    servers=(
                        _pharmacy_config(temporary_root / "pharmacy.sqlite3"),
                        _git_config(repository),
                        _filesystem_config(repository),
                    )
                ),
                protocol_logger=logger,
            )
            fake = _FakeLLM(
                [
                    _text("Alan Turing fue un matemático británico."),
                    _text("Nació el 23 de junio de 1912."),
                    _tools(
                        _use(
                            "stock",
                            "pharmacy__check_stock",
                            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
                        )
                    ),
                    _text("Hay inventario disponible."),
                    _tools(
                        _use(
                            "read",
                            "filesystem__read_text_file",
                            {"path": str(seed)},
                        )
                    ),
                    _text("Leí el archivo."),
                    _tools(
                        _use(
                            "write-no",
                            "filesystem__write_file",
                            {"path": str(rejected), "content": "not written"},
                        )
                    ),
                    _text("La escritura fue rechazada."),
                    _tools(
                        _use(
                            "write-yes",
                            "filesystem__write_file",
                            {"path": str(approved), "content": "approved\n"},
                        )
                    ),
                    _text("La escritura fue autorizada."),
                    _tools(
                        _use(
                            "git-status",
                            "git__git_status",
                            {"repo_path": str(repository)},
                        )
                    ),
                    _text("Git reporta archivos sin seguimiento."),
                    _tools(
                        _use(
                            "multi-stock",
                            "pharmacy__check_stock",
                            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
                        ),
                        _use(
                            "multi-read",
                            "filesystem__read_text_file",
                            {"path": str(seed)},
                        ),
                        _use(
                            "multi-git",
                            "git__git_status",
                            {"repo_path": str(repository)},
                        ),
                    ),
                    _text("Las tres consultas terminaron."),
                ]
            )
            answers = iter(("no", "sí"))
            prompts = []
            orchestrator = ChatOrchestrator(
                manager,
                fake,
                protocol_logger=logger,
                confirmation=lambda prompt: prompts.append(prompt) or next(answers),
            )
            try:
                try:
                    manager.start_all()
                except Exception as exc:
                    self.fail(
                        f"Three-server startup failed: {exc}; "
                        f"diagnostics: {diagnostics.getvalue()}"
                    )
                processes = {
                    name: client._process for name, client in manager._clients.items()
                }
                self.assertEqual(set(processes), {"pharmacy", "git", "filesystem"})
                self.assertIn("Turing", orchestrator.run_turn("¿Quién fue Alan Turing?"))
                self.assertIn("1912", orchestrator.run_turn("¿En qué fecha nació?"))
                orchestrator.run_turn("Consulta el stock")
                orchestrator.run_turn("Lee el archivo")
                orchestrator.run_turn("Intenta escribir y rechazaré")
                orchestrator.run_turn("Intenta otra escritura")
                orchestrator.run_turn("Consulta Git")
                final = orchestrator.run_turn("Consulta los tres sistemas")
                self.assertEqual(final, "Las tres consultas terminaron.")

                second_messages = fake.requests[1]["messages"]
                self.assertEqual(second_messages[0]["content"], "¿Quién fue Alan Turing?")
                self.assertEqual(second_messages[1]["role"], "assistant")
                self.assertFalse(rejected.exists())
                self.assertEqual(approved.read_text(encoding="utf-8"), "approved\n")
                self.assertEqual(len(prompts), 2)
                multi_results = fake.requests[-1]["messages"][-1]["content"]
                self.assertEqual(
                    [block["tool_use_id"] for block in multi_results],
                    ["multi-stock", "multi-read", "multi-git"],
                )
                entries = [
                    json.loads(line)
                    for line in log_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertTrue({"llm", "mcp", "policy", "host"}.issubset(
                    {entry["category"] for entry in entries}
                ))
                self.assertTrue(any(
                    entry["message_type"] == "mutation_rejected" for entry in entries
                ))
                self.assertTrue(any(
                    entry["message_type"] == "mutation_authorized" for entry in entries
                ))
                serialized_log = log_path.read_text(encoding="utf-8")
                self.assertNotIn("¿Quién fue Alan Turing?", serialized_log)
                self.assertNotIn("Nació el 23 de junio", serialized_log)
            finally:
                try:
                    manager.stop_all()
                finally:
                    logger.close()
        finally:
            if processes:
                self.assertTrue(all(process.poll() is not None for process in processes.values()))
            if temporary_root.parent.resolve(strict=True) != temporary_parent:
                raise AssertionError("Refusing to remove an unexpected directory")
            if temporary_root.exists():
                shutil.rmtree(temporary_root, onexc=_remove_readonly)
        self.assertFalse(temporary_root.exists())


class _FakeLLM:
    def __init__(self, responses) -> None:
        self.settings = AnthropicSettings(api_key="simulated", model="simulated-model")
        self.responses = list(responses)
        self.requests = []

    def create_message(self, *, messages, tools=None, system=None):
        self.requests.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
                "system": system,
            }
        )
        return self.responses.pop(0)


def _text(value: str) -> AnthropicMessage:
    return AnthropicMessage(
        message_id="simulated-text",
        content=({"type": "text", "text": value},),
        stop_reason="end_turn",
    )


def _tools(*blocks) -> AnthropicMessage:
    return AnthropicMessage(
        message_id="simulated-tools",
        content=tuple(blocks),
        stop_reason="tool_use",
    )


def _use(identifier: str, name: str, arguments: dict[str, object]):
    return {"type": "tool_use", "id": identifier, "name": name, "input": arguments}


def _pharmacy_config(database_path: Path) -> StdioServerConfig:
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


def _git_config(repository: Path) -> StdioServerConfig:
    return StdioServerConfig(
        name="git",
        command="uvx",
        args=("--from", GIT_PACKAGE, "mcp-server-git", "--repository", str(repository)),
        cwd=repository,
        env=MappingProxyType({}),
        request_timeout_seconds=60,
        shutdown_timeout_seconds=10,
        repository_policy=RepositoryPolicyConfig(
            root=repository.resolve(strict=True),
            argument_name="repo_path",
            mutable_tools=frozenset(
                {"git_add", "git_commit", "git_reset", "git_checkout", "git_create_branch"}
            ),
        ),
    )


def _filesystem_config(repository: Path) -> StdioServerConfig:
    if os.name == "nt":
        command = os.environ.get("COMSPEC", "cmd.exe")
        args = ("/d", "/s", "/c", "npx", "-y", FILESYSTEM_PACKAGE, str(repository))
    else:
        command = "npx"
        args = ("-y", FILESYSTEM_PACKAGE, str(repository))
    return StdioServerConfig(
        name="filesystem",
        command=command,
        args=args,
        cwd=repository,
        env=MappingProxyType(
            {"npm_config_offline": "true", "npm_config_update_notifier": "false"}
        ),
        request_timeout_seconds=60,
        shutdown_timeout_seconds=10,
        filesystem_policy=FilesystemPolicyConfig(
            root=repository.resolve(strict=True),
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


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _remove_readonly(function: object, path: str, exception: BaseException) -> None:
    if not isinstance(exception, PermissionError) or not callable(function):
        raise exception
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    function(path)


if __name__ == "__main__":
    unittest.main()
