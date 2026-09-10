"""Simulated Gemini workflow over the three real MCP child servers."""

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
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    ChatOrchestrator,
    FilesystemPolicyConfig,
    GeminiGenerateContentClient,
    GeminiSettings,
    HTTPResponse,
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
    "git, uvx, and npx are required for the simulated Gemini integration",
)
class SimulatedGeminiThreeServerTests(unittest.TestCase):
    def test_gemini_context_tools_signatures_policies_and_cleanup(self) -> None:
        temporary_root = Path(
            tempfile.mkdtemp(prefix=f"pharmacy-mcp-gemini-{uuid4().hex}-")
        ).resolve(strict=True)
        temporary_parent = Path(tempfile.gettempdir()).resolve(strict=True)
        processes = {}
        credential = f"simulated-{uuid4().hex}"
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
            signature = "opaque-thought-signature"
            transport = _GeminiQueueTransport(
                [
                    _text("Alan Turing fue un matemático británico."),
                    _text("Nació el 23 de junio de 1912."),
                    _calls(
                        _call(
                            "stock",
                            "pharmacy__check_stock",
                            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
                            signature=signature,
                        )
                    ),
                    _text("Hay inventario disponible."),
                    _calls(
                        _call(
                            "read",
                            "filesystem__read_text_file",
                            {"path": str(seed)},
                        )
                    ),
                    _text("Leí el archivo."),
                    _calls(
                        _call(
                            "git-status",
                            "git__git_status",
                            {"repo_path": str(repository)},
                        )
                    ),
                    _text("Git respondió correctamente."),
                    _calls(
                        _call(
                            "multi-stock",
                            "pharmacy__check_stock",
                            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
                        ),
                        _call(
                            "multi-read",
                            "filesystem__read_text_file",
                            {"path": str(seed)},
                        ),
                        _call(
                            "multi-git",
                            "git__git_status",
                            {"repo_path": str(repository)},
                        ),
                    ),
                    _text("Las tres consultas terminaron."),
                    _calls(
                        _call(
                            "write-no",
                            "filesystem__write_file",
                            {"path": str(rejected), "content": "not written"},
                        )
                    ),
                    _text("La escritura fue rechazada."),
                    _calls(
                        _call(
                            "write-yes",
                            "filesystem__write_file",
                            {"path": str(approved), "content": "approved\n"},
                        )
                    ),
                    _text("La escritura fue autorizada."),
                ]
            )
            client = GeminiGenerateContentClient(
                GeminiSettings(api_key=credential),
                transport=transport,
                event_sink=lambda event, payload: logger.orchestrator_event(
                    "llm", event, payload
                ),
            )
            answers = iter(("no", "sí"))
            prompts = []
            orchestrator = ChatOrchestrator(
                manager,
                client,
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
                    name: mcp_client._process
                    for name, mcp_client in manager._clients.items()
                }
                self.assertEqual(set(processes), {"pharmacy", "git", "filesystem"})
                self.assertIn("Turing", orchestrator.run_turn("¿Quién fue Alan Turing?"))
                self.assertIn("1912", orchestrator.run_turn("¿En qué fecha nació?"))
                orchestrator.run_turn("Consulta el stock")
                orchestrator.run_turn("Lee el archivo")
                orchestrator.run_turn("Consulta Git")
                self.assertEqual(
                    orchestrator.run_turn("Consulta los tres sistemas"),
                    "Las tres consultas terminaron.",
                )
                orchestrator.run_turn("Intenta escribir y rechazaré")
                final = orchestrator.run_turn("Intenta otra escritura")
                self.assertEqual(final, "La escritura fue autorizada.")

                second_payload = json.loads(transport.requests[1].body)
                self.assertEqual(second_payload["contents"][0]["parts"][0]["text"], "¿Quién fue Alan Turing?")
                self.assertEqual(second_payload["contents"][1]["role"], "model")
                tool_payloads = [
                    json.loads(request.body)
                    for request in transport.requests
                    if b"functionResponse" in request.body
                ]
                self.assertTrue(any(signature in json.dumps(payload) for payload in tool_payloads))
                multiple = next(
                    payload
                    for payload in tool_payloads
                    if any(
                        len(content.get("parts", [])) == 3
                        and all("functionResponse" in part for part in content["parts"])
                        for content in payload["contents"]
                    )
                )
                response_parts = next(
                    content["parts"]
                    for content in multiple["contents"]
                    if len(content.get("parts", [])) == 3
                    and all("functionResponse" in part for part in content["parts"])
                )
                self.assertEqual(
                    [part["functionResponse"]["id"] for part in response_parts],
                    ["multi-stock", "multi-read", "multi-git"],
                )
                self.assertFalse(rejected.exists())
                self.assertEqual(approved.read_text(encoding="utf-8"), "approved\n")
                self.assertEqual(len(prompts), 2)

                entries = [
                    json.loads(line)
                    for line in log_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertTrue(
                    {"llm", "mcp", "policy", "host"}.issubset(
                        {entry["category"] for entry in entries}
                    )
                )
                serialized_log = log_path.read_text(encoding="utf-8")
                self.assertNotIn(signature, serialized_log)
                self.assertNotIn(credential, serialized_log)
                self.assertNotIn("¿Quién fue Alan Turing?", serialized_log)
                self.assertTrue(
                    any(
                        entry["message_type"] == "gemini_request_finished"
                        for entry in entries
                    )
                )
                self.assertTrue(
                    any(
                        entry["message_type"] == "mutation_rejected"
                        for entry in entries
                    )
                )
                self.assertTrue(
                    any(
                        entry["message_type"] == "mutation_authorized"
                        for entry in entries
                    )
                )
            finally:
                try:
                    manager.stop_all()
                finally:
                    logger.close()
        finally:
            if processes:
                self.assertTrue(
                    all(process.poll() is not None for process in processes.values())
                )
            if temporary_root.parent.resolve(strict=True) != temporary_parent:
                raise AssertionError("Refusing to remove an unexpected directory")
            if temporary_root.exists():
                shutil.rmtree(temporary_root, onexc=_remove_readonly)
        self.assertFalse(temporary_root.exists())


class _GeminiQueueTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected simulated Gemini request")
        return self.responses.pop(0)


def _text(value: str) -> HTTPResponse:
    return _response([{"text": value}], finish_reason="STOP")


def _calls(*parts) -> HTTPResponse:
    return _response(list(parts), finish_reason="STOP")


def _call(identifier: str, name: str, arguments: dict[str, object], *, signature=None):
    part = {
        "functionCall": {"id": identifier, "name": name, "args": arguments}
    }
    if signature is not None:
        part["thoughtSignature"] = signature
    return part


def _response(parts, *, finish_reason: str) -> HTTPResponse:
    return HTTPResponse(
        status=200,
        headers={"x-goog-request-id": "simulated-request"},
        body=json.dumps(
            {
                "responseId": f"response-{uuid4().hex}",
                "candidates": [
                    {
                        "content": {"role": "model", "parts": parts},
                        "finishReason": finish_reason,
                    }
                ],
            }
        ).encode(),
    )


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
