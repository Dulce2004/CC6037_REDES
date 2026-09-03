"""Synchronous stdio MCP client backed by a local child process."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from copy import deepcopy

from pharmacy_mcp.jsonrpc import (
    ErrorResponse,
    JsonRpcError,
    Request,
    Response,
    deserialize_message,
    serialize_message,
)
from pharmacy_mcp.jsonrpc.messages import JsonValue

from .config import StdioServerConfig
from .protocol_log import MCPLogError, MCPProtocolLogger

MCP_PROTOCOL_VERSION = "2025-11-25"
HOST_NAME = "Pharmacy MCP Terminal Host"
HOST_VERSION = "0.1.0"


class MCPHostError(RuntimeError):
    """Base error raised for controlled MCP host failures."""


class MCPTransportError(MCPHostError):
    """Failure while starting, writing, reading, or stopping a child."""


class MCPProtocolError(MCPHostError):
    """Unexpected or invalid MCP/JSON-RPC message."""


class MCPServerResponseError(MCPHostError):
    """JSON-RPC error response returned by the child server."""

    def __init__(
        self,
        *,
        server_name: str,
        code: int,
        message: str,
        data: JsonValue | None = None,
    ) -> None:
        super().__init__(f"Server '{server_name}' returned {code}: {message}")
        self.server_name = server_name
        self.code = code
        self.message = message
        self.data = data


class StdioMCPClient:
    """Manage lifecycle and requests for one child MCP server."""

    def __init__(
        self,
        config: StdioServerConfig,
        *,
        protocol_logger: MCPProtocolLogger | None = None,
    ) -> None:
        if not isinstance(config, StdioServerConfig):
            raise TypeError("'config' must be a StdioServerConfig instance.")
        self.config = config
        self._owns_protocol_logger = protocol_logger is None
        self.protocol_logger = protocol_logger or MCPProtocolLogger()
        self._process: subprocess.Popen[str] | None = None
        self._stdout_lines: queue.Queue[str | MCPLogError | None] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._exchange_lock = threading.Lock()
        self._next_request_id = 1
        self._ready = False
        self.server_info: dict[str, JsonValue] | None = None
        self.server_capabilities: dict[str, JsonValue] | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def is_ready(self) -> bool:
        return self._ready and self.is_running

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def start(self) -> None:
        """Start the process and complete the required MCP handshake."""

        if self._process is not None:
            raise MCPTransportError(
                f"Server '{self.config.name}' has already been started."
            )

        self._stdout_lines = queue.Queue()
        self._next_request_id = 1
        self.server_info = None
        self.server_capabilities = None
        environment = os.environ.copy()
        environment.update(self.config.env)
        try:
            self._process = subprocess.Popen(
                self.config.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                cwd=self.config.cwd,
                env=environment,
                shell=False,
            )
        except OSError as exc:
            self._process = None
            if self._owns_protocol_logger:
                self.protocol_logger.close()
            raise MCPTransportError(
                f"Cannot start server '{self.config.name}': {exc}."
            ) from exc

        self._start_reader_threads()
        try:
            result = self.request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": HOST_NAME,
                        "version": HOST_VERSION,
                    },
                },
            )
            self._validate_initialize_result(result)
            self.notify("notifications/initialized", {})
            self._ready = True
        except Exception:
            self.stop()
            raise

    def list_tools(self) -> tuple[dict[str, JsonValue], ...]:
        self._require_ready()
        result = self.request("tools/list", {})
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned an invalid tools/list result."
            )

        definitions: list[dict[str, JsonValue]] = []
        for index, definition in enumerate(result["tools"]):
            if not isinstance(definition, dict):
                raise MCPProtocolError(
                    f"Server '{self.config.name}' returned a non-object tool at "
                    f"index {index}."
                )
            name = definition.get("name")
            description = definition.get("description")
            input_schema = definition.get("inputSchema")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(description, str)
                or not isinstance(input_schema, dict)
            ):
                raise MCPProtocolError(
                    f"Server '{self.config.name}' returned an invalid tool "
                    f"definition at index {index}."
                )
            definitions.append(deepcopy(definition))
        return tuple(definitions)

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
    ) -> JsonValue:
        self._require_ready()
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise MCPProtocolError("Tool name must be a non-empty string.")
        if not isinstance(arguments, dict):
            raise MCPProtocolError("Tool arguments must be an object.")
        return self.request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

    def request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> JsonValue:
        """Send a request and wait for its correlated response."""

        with self._exchange_lock:
            self._require_process()
            request_id = self._next_request_id
            self._next_request_id += 1
            try:
                request = Request(method=method, params=params, id=request_id)
                payload = serialize_message(request)
            except JsonRpcError as exc:
                raise MCPProtocolError(exc.message) from exc
            self._write_payload(payload)
            response = self._read_response(request_id)
            if isinstance(response, ErrorResponse):
                error_data = response.error.to_dict().get("data")
                raise MCPServerResponseError(
                    server_name=self.config.name,
                    code=response.error.code,
                    message=response.error.message,
                    data=error_data,
                )
            return response.result

    def notify(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        """Send a notification without attempting to read a response."""

        with self._exchange_lock:
            self._require_process()
            try:
                payload = serialize_message(Request(method=method, params=params))
            except JsonRpcError as exc:
                raise MCPProtocolError(exc.message) from exc
            self._write_payload(payload)

    def stop(self) -> None:
        """Close stdin for EOF and terminate only if the child does not exit."""

        process = self._process
        if process is None:
            if self._owns_protocol_logger:
                self.protocol_logger.close()
            return
        self._ready = False

        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass

        try:
            process.wait(timeout=self.config.shutdown_timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=self.config.shutdown_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.config.shutdown_timeout_seconds)

        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=self.config.shutdown_timeout_seconds)

        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass

        self._process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self.server_info = None
        self.server_capabilities = None
        if self._owns_protocol_logger:
            self.protocol_logger.close()

    def __enter__(self) -> StdioMCPClient:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()

    def _start_reader_threads(self) -> None:
        process = self._require_process()
        if process.stdout is None or process.stderr is None:
            raise MCPTransportError(
                f"Server '{self.config.name}' streams are unavailable."
            )

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout,
            name=f"mcp-{self.config.name}-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr,
            name=f"mcp-{self.config.name}-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _pump_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._stdout_lines.put(None)
            return
        try:
            for line in process.stdout:
                self._stdout_lines.put(line)
        finally:
            self._stdout_lines.put(None)

    def _pump_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                self.protocol_logger.diagnostic(
                    self.config.name,
                    line.rstrip("\r\n"),
                    self.config.transport,
                )
        except MCPLogError as exc:
            self._stdout_lines.put(exc)

    def _write_payload(self, payload: str) -> None:
        process = self._require_process()
        if process.stdin is None or process.stdin.closed:
            raise MCPTransportError(
                f"Server '{self.config.name}' stdin is unavailable."
            )
        self.protocol_logger.outbound(
            self.config.name,
            payload,
            self.config.transport,
        )
        try:
            process.stdin.write(f"{payload}\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPTransportError(
                f"Cannot write to server '{self.config.name}'."
            ) from exc

    def _read_response(self, request_id: int) -> Response | ErrorResponse:
        deadline = time.monotonic() + self.config.request_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTransportError(
                    f"Timed out waiting for server '{self.config.name}'."
                )
            try:
                line = self._stdout_lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise MCPTransportError(
                    f"Timed out waiting for server '{self.config.name}'."
                ) from exc
            if line is None:
                return_code = (
                    self._process.poll() if self._process is not None else None
                )
                raise MCPTransportError(
                    f"Server '{self.config.name}' closed stdout "
                    f"(exit code {return_code})."
                )

            if isinstance(line, MCPLogError):
                raise line

            payload = line.rstrip("\r\n")
            self.protocol_logger.inbound(
                self.config.name,
                payload,
                self.config.transport,
            )
            try:
                message = deserialize_message(payload)
            except JsonRpcError as exc:
                raise MCPProtocolError(
                    f"Server '{self.config.name}' returned invalid JSON-RPC: "
                    f"{exc.message}"
                ) from exc

            if isinstance(message, Request) and message.is_notification:
                continue
            if not isinstance(message, (Response, ErrorResponse)):
                raise MCPProtocolError(
                    f"Server '{self.config.name}' sent an unsupported request."
                )
            if message.id != request_id:
                raise MCPProtocolError(
                    f"Server '{self.config.name}' response ID {message.id!r} "
                    f"does not match request ID {request_id!r}."
                )
            return message

    def _validate_initialize_result(self, result: JsonValue) -> None:
        if not isinstance(result, dict):
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned invalid initialize data."
            )
        if result.get("protocolVersion") != MCP_PROTOCOL_VERSION:
            raise MCPProtocolError(
                f"Server '{self.config.name}' negotiated an unsupported protocol."
            )
        server_info = result.get("serverInfo")
        capabilities = result.get("capabilities")
        if not isinstance(server_info, dict) or not isinstance(capabilities, dict):
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned incomplete initialize data."
            )
        if not isinstance(server_info.get("name"), str) or not isinstance(
            server_info.get("version"), str
        ):
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned invalid serverInfo."
            )
        self.server_info = deepcopy(server_info)
        self.server_capabilities = deepcopy(capabilities)

    def _require_ready(self) -> None:
        if not self.is_ready:
            raise MCPTransportError(
                f"Server '{self.config.name}' is not initialized."
            )

    def _require_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None:
            raise MCPTransportError(
                f"Server '{self.config.name}' has not been started."
            )
        if process.poll() is not None:
            raise MCPTransportError(
                f"Server '{self.config.name}' exited with code "
                f"{process.returncode}."
            )
        return process
