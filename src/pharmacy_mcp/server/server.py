"""Núcleo local y manual del servidor MCP educativo."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from pharmacy_mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    ErrorObject,
    ErrorResponse,
    InvalidParamsError,
    JsonRpcError,
    MethodNotFoundError,
    Request,
    Response,
)
from pharmacy_mcp.jsonrpc.messages import JsonRpcId, JsonValue

from .handlers import Tool, ToolHandler
from .pharmacy_tool import (
    CLASSIFY_SYMPTOMS_DESCRIPTION,
    CLASSIFY_SYMPTOMS_INPUT_SCHEMA,
    CLASSIFY_SYMPTOMS_NAME,
    classify_symptoms_handler,
)

SUPPORTED_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "Pharmacy MCP Server"
SERVER_VERSION = "0.1.0"

ServerResult: TypeAlias = Response | ErrorResponse
MethodHandler: TypeAlias = Callable[[Request], JsonValue]


class PharmacyMCPServer:
    """Procesa solicitudes MCP locales sin implementar ningún transporte."""

    def __init__(
        self,
        *,
        name: str = SERVER_NAME,
        version: str = SERVER_VERSION,
        protocol_version: str = SUPPORTED_PROTOCOL_VERSION,
    ) -> None:
        self.name = name
        self.version = version
        self.protocol_version = protocol_version
        self._tools: dict[str, Tool] = {}
        self._method_handlers: dict[str, MethodHandler] = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
        }
        self.register_tool(
            name=CLASSIFY_SYMPTOMS_NAME,
            description=CLASSIFY_SYMPTOMS_DESCRIPTION,
            input_schema=CLASSIFY_SYMPTOMS_INPUT_SCHEMA,
            handler=classify_symptoms_handler,
        )

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, JsonValue],
        handler: ToolHandler,
    ) -> None:
        """Registra una herramienta para listarla y ejecutarla posteriormente."""

        tool = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def process_request(self, request: Request) -> ServerResult:
        """Despacha una solicitud y siempre devuelve una respuesta JSON-RPC."""

        if not isinstance(request, Request):
            return self._error_response(
                None,
                code=INVALID_REQUEST,
                message="Invalid Request",
            )

        request_id = self._request_id(request)
        handler = self._method_handlers.get(request.method)
        if handler is None:
            error = MethodNotFoundError(
                f"Method not found: '{request.method}'."
            )
            return self._error_response(
                request_id,
                code=error.code,
                message=error.message,
            )

        try:
            result = handler(request)
        except JsonRpcError as exc:
            return self._error_response(
                request_id,
                code=exc.code,
                message=exc.message,
            )
        except Exception:
            return self._error_response(
                request_id,
                code=INTERNAL_ERROR,
                message="Internal error",
            )

        try:
            return Response(result=result, id=request_id)
        except JsonRpcError:
            return self._error_response(
                request_id,
                code=INTERNAL_ERROR,
                message="Internal error",
            )

    def _handle_initialize(self, request: Request) -> JsonValue:
        params = self._object_params(request)
        requested_version = params.get("protocolVersion")
        if requested_version is not None and not isinstance(requested_version, str):
            raise InvalidParamsError("'protocolVersion' must be a string.")

        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _handle_tools_list(self, request: Request) -> JsonValue:
        self._object_params(request)
        return {
            "tools": [tool.to_definition() for tool in self._tools.values()]
        }

    def _handle_tools_call(self, request: Request) -> JsonValue:
        params = self._object_params(request)
        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise InvalidParamsError("'tools/call' requires a non-empty tool name.")

        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise InvalidParamsError("Tool 'arguments' must be an object.")

        tool = self._tools.get(tool_name)
        if tool is None:
            raise InvalidParamsError(f"Tool not found: '{tool_name}'.")

        required_arguments = tool.input_schema.get("required", [])
        missing_arguments = [
            name for name in required_arguments if name not in arguments
        ]
        if missing_arguments:
            missing = ", ".join(missing_arguments)
            raise InvalidParamsError(f"Missing required tool arguments: {missing}.")

        return tool.handler(arguments)

    @staticmethod
    def _object_params(request: Request) -> dict[str, JsonValue]:
        params = request.to_dict().get("params", {})
        if not isinstance(params, dict):
            raise InvalidParamsError("Method params must be an object.")
        return params

    @staticmethod
    def _request_id(request: Request) -> JsonRpcId:
        return request.to_dict().get("id")

    @staticmethod
    def _error_response(
        request_id: JsonRpcId,
        *,
        code: int,
        message: str,
    ) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorObject(code=code, message=message),
            id=request_id,
        )
