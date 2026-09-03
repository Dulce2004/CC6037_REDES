"""Line-delimited stdio transport for the local pharmacy MCP server."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TextIO

from pharmacy_mcp.jsonrpc import (
    INVALID_REQUEST,
    ErrorObject,
    ErrorResponse,
    JsonRpcError,
    Request,
    deserialize_message,
    serialize_message,
)

from .server import PharmacyMCPServer, ServerResult

DATABASE_PATH_ENVIRONMENT_VARIABLE = "PHARMACY_MCP_DATABASE_PATH"
DEFAULT_RUNTIME_DATABASE_PATH = Path("runtime") / "pharmacy.sqlite3"


def process_line(server: PharmacyMCPServer, line: str) -> ServerResult:
    """Deserialize and process one JSON-RPC message from a physical line."""

    try:
        message = deserialize_message(line)
    except JsonRpcError as exc:
        return ErrorResponse(
            error=ErrorObject(code=exc.code, message=exc.message),
            id=None,
        )

    if not isinstance(message, Request):
        return ErrorResponse(
            error=ErrorObject(
                code=INVALID_REQUEST,
                message="Invalid Request",
            ),
            id=None,
        )

    return server.process_request(message)


def serve_stdio(
    server: PharmacyMCPServer | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Serve newline-delimited JSON-RPC messages until stdin reaches EOF."""

    active_server = server if server is not None else PharmacyMCPServer()
    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
    diagnostic_stream = stderr if stderr is not None else sys.stderr

    try:
        for line in input_stream:
            response = process_line(active_server, line)
            if response is None:
                continue

            output_stream.write(f"{serialize_message(response)}\n")
            output_stream.flush()
    except Exception as exc:
        diagnostic_stream.write(
            f"pharmacy MCP stdio transport failure: {type(exc).__name__}\n"
        )
        diagnostic_stream.flush()
        return 1

    return 0


def _configure_standard_streams() -> None:
    """Use an explicit UTF-8 encoding and LF framing for the process streams."""

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", newline="\n")


def main() -> int:
    """Run the pharmacy MCP server over the current process stdio streams."""

    _configure_standard_streams()
    server = PharmacyMCPServer(database_path=_runtime_database_path())
    return serve_stdio(server=server)


def _runtime_database_path() -> Path:
    configured_path = os.environ.get(DATABASE_PATH_ENVIRONMENT_VARIABLE)
    if configured_path is not None:
        if not configured_path.strip():
            raise ValueError(
                f"{DATABASE_PATH_ENVIRONMENT_VARIABLE} must not be empty."
            )
        return Path(configured_path)
    return Path.cwd() / DEFAULT_RUNTIME_DATABASE_PATH


if __name__ == "__main__":
    raise SystemExit(main())
