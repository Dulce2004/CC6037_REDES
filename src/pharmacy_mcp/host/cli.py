"""Technical CLI for the configurable terminal MCP host."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .anthropic import (
    AnthropicConfigurationError,
    AnthropicMessagesClient,
    AnthropicSettings,
    HTTPTransport,
)
from .chat import ChatError, ChatLimits, ChatOrchestrator, Confirmation
from .config import DEFAULT_CONFIG_PATH, HostConfigurationError, load_host_config
from .conversation import ConversationHistory, DEFAULT_HISTORY_MAX_MESSAGES
from .manager import MCPServerManager
from .protocol_log import DEFAULT_LOG_PATH, MCPLogError, MCPProtocolLogger
from .stdio_client import MCPHostError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pharmacy-mcp-host",
        description=(
            "Starts configured MCP stdio servers and exposes namespaced tools."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Host JSON configuration (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=(
            "Append MCP exchanges to this JSONL file "
            "(default: runtime/mcp-host.jsonl)."
        ),
    )
    parser.add_argument(
        "--show-log",
        action="store_true",
        help="Mirror redacted JSONL protocol entries to stderr.",
    )
    parser.add_argument(
        "--allow-mutation",
        action="store_true",
        help=(
            "Explicitly authorize a mutable tool for this invocation; configured "
            "repository and filesystem boundaries still apply."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list-servers",
        help="List configured servers without starting them.",
    )

    tools_parser = subparsers.add_parser(
        "list-tools",
        help="Start servers and list their namespaced tools.",
    )
    tools_parser.add_argument(
        "--server",
        help="Start and list only this configured server.",
    )

    call_parser = subparsers.add_parser(
        "call-tool",
        help="Invoke one tool using its '<server>__<tool>' name.",
    )
    call_parser.add_argument("tool", help="Namespaced tool name.")
    call_parser.add_argument(
        "--arguments",
        default="{}",
        help="Tool arguments as one JSON object (default: {}).",
    )
    chat_parser = subparsers.add_parser(
        "chat",
        help="Start an interactive Anthropic conversation with MCP tools.",
    )
    chat_parser.add_argument(
        "--history-max-messages",
        type=int,
        default=DEFAULT_HISTORY_MAX_MESSAGES,
        help=(
            "Maximum in-memory conversation messages; complete old turns are "
            f"trimmed first (default: {DEFAULT_HISTORY_MAX_MESSAGES})."
        ),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    anthropic_transport: HTTPTransport | None = None,
    confirmation: Confirmation | None = None,
) -> int:
    output_stream = stdout if stdout is not None else sys.stdout
    error_stream = stderr if stderr is not None else sys.stderr
    input_stream = stdin if stdin is not None else sys.stdin
    environment = os.environ if environ is None else environ
    parser = build_parser()
    arguments = parser.parse_args(argv)

    try:
        anthropic_settings = (
            AnthropicSettings.from_environ(environment)
            if arguments.command == "chat"
            else None
        )
        config = load_host_config(arguments.config, environ=environment)
        with MCPProtocolLogger(
            arguments.log_file,
            diagnostic_stream=error_stream,
            show_traffic=arguments.show_log,
        ) as protocol_logger:
            manager = MCPServerManager(
                config,
                protocol_logger=protocol_logger,
            )

            if arguments.command == "list-servers":
                _write_json(
                    output_stream,
                    {"servers": [item.to_dict() for item in manager.list_servers()]},
                )
                return 0

            try:
                if arguments.command == "list-tools":
                    if arguments.server is None:
                        manager.start_all()
                    else:
                        manager.start_server(arguments.server)
                    _write_json(
                        output_stream,
                        {
                            "tools": [
                                tool.to_dict()
                                for tool in manager.list_tools(arguments.server)
                            ]
                        },
                    )
                    return 0

                if arguments.command == "call-tool":
                    tool_arguments = _parse_tool_arguments(arguments.arguments)
                    server_name = manager.server_name_from_namespace(arguments.tool)
                    manager.start_server(server_name)
                    result = manager.invoke_tool(
                        arguments.tool,
                        tool_arguments,
                        allow_mutation=arguments.allow_mutation,
                    )
                    _write_json(output_stream, {"result": result})
                    return 0

                if arguments.command == "chat":
                    assert anthropic_settings is not None
                    history = ConversationHistory(
                        max_messages=arguments.history_max_messages
                    )
                    llm_client = AnthropicMessagesClient(
                        anthropic_settings,
                        transport=anthropic_transport,
                    )
                    confirm = confirmation or (
                        lambda prompt: _read_line(
                            input_stream,
                            output_stream,
                            prompt,
                        )
                    )
                    orchestrator = ChatOrchestrator(
                        manager,
                        llm_client,
                        history=history,
                        protocol_logger=protocol_logger,
                        confirmation=confirm,
                        limits=ChatLimits(
                            max_tool_rounds=anthropic_settings.max_tool_rounds
                        ),
                    )
                    failures = manager.start_available()
                    for failure in failures:
                        error_stream.write(
                            f"host warning: server '{failure.server_name}' is "
                            f"unavailable: {failure.error}\n"
                        )
                    error_stream.flush()
                    return _run_chat(
                        orchestrator,
                        manager,
                        stdin=input_stream,
                        stdout=output_stream,
                        stderr=error_stream,
                    )

                raise MCPHostError(
                    f"Unsupported host command: '{arguments.command}'."
                )
            finally:
                manager.stop_all()
    except (
        AnthropicConfigurationError,
        ChatError,
        HostConfigurationError,
        MCPHostError,
        MCPLogError,
        ValueError,
    ) as exc:
        error_stream.write(f"host error: {exc}\n")
        error_stream.flush()
        return 1


def _run_chat(
    orchestrator: ChatOrchestrator,
    manager: MCPServerManager,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    stdout.write(
        "Chat MCP con Anthropic listo. Escribe /help para ver comandos.\n"
    )
    stdout.flush()
    try:
        while True:
            line = _read_line(stdin, stdout, "Tú> ")
            if line == "":
                stdout.write("\n")
                stdout.flush()
                return 0
            text = line.strip()
            if not text:
                continue
            command = text.casefold()
            if command == "/exit":
                return 0
            if command == "/help":
                stdout.write(
                    "/help   muestra esta ayuda\n"
                    "/tools  lista tools MCP disponibles\n"
                    "/servers muestra estado de servidores\n"
                    "/clear  borra el historial en memoria\n"
                    "/exit   termina y cierra los servidores\n"
                )
                stdout.flush()
                continue
            if command == "/tools":
                _write_json(
                    stdout,
                    {"tools": [tool.to_dict() for tool in manager.list_tools()]},
                )
                continue
            if command == "/servers":
                _write_json(
                    stdout,
                    {"servers": [item.to_dict() for item in manager.list_servers()]},
                )
                continue
            if command == "/clear":
                orchestrator.clear()
                stdout.write("Historial eliminado.\n")
                stdout.flush()
                continue
            try:
                response = orchestrator.run_turn(text)
            except ChatError as exc:
                stderr.write(f"chat error: {exc}\n")
                stderr.flush()
                continue
            stdout.write(f"Claude> {response}\n")
            stdout.flush()
    except (EOFError, KeyboardInterrupt):
        stdout.write("\n")
        stdout.flush()
        return 0


def _read_line(stdin: TextIO, stdout: TextIO, prompt: str) -> str:
    stdout.write(prompt)
    stdout.flush()
    return stdin.readline()


def _parse_tool_arguments(payload: str) -> dict[str, JsonValue]:
    try:
        value = json.loads(payload, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Tool arguments are not valid JSON: {exc}.") from exc
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def _write_json(stream: TextIO, value: JsonValue) -> None:
    stream.write(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
    )
    stream.write("\n")
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
