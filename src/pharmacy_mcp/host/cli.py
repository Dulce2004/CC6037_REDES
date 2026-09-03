"""Technical CLI for the configurable terminal MCP host."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .config import DEFAULT_CONFIG_PATH, HostConfigurationError, load_host_config
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
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output_stream = stdout if stdout is not None else sys.stdout
    error_stream = stderr if stderr is not None else sys.stderr
    parser = build_parser()
    arguments = parser.parse_args(argv)

    try:
        config = load_host_config(arguments.config)
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
                    result = manager.invoke_tool(arguments.tool, tool_arguments)
                    _write_json(output_stream, {"result": result})
                    return 0

                raise MCPHostError(
                    f"Unsupported host command: '{arguments.command}'."
                )
            finally:
                manager.stop_all()
    except (HostConfigurationError, MCPHostError, MCPLogError, ValueError) as exc:
        error_stream.write(f"host error: {exc}\n")
        error_stream.flush()
        return 1


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
