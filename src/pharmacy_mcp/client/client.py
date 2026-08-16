"""Cliente MCP local que se comunica en memoria con el servidor educativo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from pharmacy_mcp.jsonrpc import (
    INVALID_REQUEST,
    ErrorResponse,
    JsonRpcError,
    Request,
    Response,
)
from pharmacy_mcp.jsonrpc.messages import JsonValue
from pharmacy_mcp.server import PharmacyMCPServer


@dataclass(frozen=True, slots=True)
class ClientError:
    """Representación controlada de un error local o de JSON-RPC."""

    message: str
    code: int | None = None

    def __str__(self) -> str:
        if self.code is None:
            return f"Error: {self.message}"
        return f"Error {self.code}: {self.message}"


ClientExchange: TypeAlias = Response | ClientError
ToolListResult: TypeAlias = list[dict[str, JsonValue]] | ClientError
ToolCallResult: TypeAlias = JsonValue | ClientError


class PharmacyMCPClient:
    """Administra estado e intercambios JSON-RPC con un servidor MCP local."""

    def __init__(self, server: PharmacyMCPServer) -> None:
        self._server = server
        self._next_request_id = 1
        self.is_initialized = False
        self.protocol_version: str | None = None
        self.server_info: dict[str, JsonValue] | None = None
        self.server_capabilities: dict[str, JsonValue] | None = None

    def initialize(self) -> ClientExchange:
        """Inicializa el cliente y guarda la información devuelta por el servidor."""

        response = self._send_request("initialize", {})
        if isinstance(response, ClientError):
            return response

        result = response.result
        if not isinstance(result, dict):
            return ClientError("Invalid initialize result.", INVALID_REQUEST)

        protocol_version = result.get("protocolVersion")
        server_info = result.get("serverInfo")
        capabilities = result.get("capabilities")
        if (
            not isinstance(protocol_version, str)
            or not isinstance(server_info, dict)
            or not isinstance(capabilities, dict)
        ):
            return ClientError("Invalid initialize result.", INVALID_REQUEST)

        self.protocol_version = protocol_version
        self.server_info = server_info
        self.server_capabilities = capabilities
        self.is_initialized = True
        return response

    def list_tools(self) -> ToolListResult:
        """Solicita al servidor las definiciones de sus herramientas."""

        state_error = self._require_initialized()
        if state_error is not None:
            return state_error

        response = self._send_request("tools/list", {})
        if isinstance(response, ClientError):
            return response
        if not isinstance(response.result, dict):
            return ClientError("Invalid tools/list result.", INVALID_REQUEST)

        tools = response.result.get("tools")
        if not isinstance(tools, list) or not all(
            isinstance(tool, dict) for tool in tools
        ):
            return ClientError("Invalid tools/list result.", INVALID_REQUEST)
        return tools

    def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> ToolCallResult:
        """Ejecuta una herramienta exclusivamente mediante ``tools/call``."""

        state_error = self._require_initialized()
        if state_error is not None:
            return state_error

        response = self._send_request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if isinstance(response, ClientError):
            return response
        return response.result

    def _send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> ClientExchange:
        request_id = self._next_request_id
        self._next_request_id += 1

        try:
            request = Request(method=method, params=params, id=request_id)
        except JsonRpcError as exc:
            return ClientError(exc.message, exc.code)

        response = self._server.process_request(request)
        if response.id != request_id:
            return ClientError("Response id does not match request id.", INVALID_REQUEST)
        if isinstance(response, ErrorResponse):
            return ClientError(response.error.message, response.error.code)
        if not isinstance(response, Response):
            return ClientError("Invalid response type.", INVALID_REQUEST)
        return response

    def _require_initialized(self) -> ClientError | None:
        if self.is_initialized:
            return None
        return ClientError("Client has not been initialized.")
