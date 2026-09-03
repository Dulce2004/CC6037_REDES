"""Núcleo local y manual del servidor MCP educativo."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto
from typing import TypeAlias

from pharmacy_mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    ErrorObject,
    ErrorResponse,
    InvalidParamsError,
    InvalidRequestError,
    JsonRpcError,
    MethodNotFoundError,
    Request,
    Response,
    ServerNotInitializedError,
)
from pharmacy_mcp.jsonrpc.messages import JsonRpcId, JsonValue
from pharmacy_mcp.pharmacy import (
    load_default_catalog,
    load_default_interactions,
    load_default_inventory,
)

from .catalog_tools import (
    CHECK_INTERACTIONS_DESCRIPTION,
    CHECK_INTERACTIONS_INPUT_SCHEMA,
    CHECK_INTERACTIONS_NAME,
    CHECK_STOCK_DESCRIPTION,
    CHECK_STOCK_INPUT_SCHEMA,
    CHECK_STOCK_NAME,
    GET_MEDICATION_DETAILS_DESCRIPTION,
    GET_MEDICATION_DETAILS_INPUT_SCHEMA,
    GET_MEDICATION_DETAILS_NAME,
    SEARCH_MEDICATIONS_DESCRIPTION,
    SEARCH_MEDICATIONS_INPUT_SCHEMA,
    SEARCH_MEDICATIONS_NAME,
    PharmacyQueryHandlers,
)
from .handlers import Tool, ToolHandler
from .pharmacy_tool import (
    ASSESS_SYMPTOMS_DESCRIPTION,
    ASSESS_SYMPTOMS_INPUT_SCHEMA,
    ASSESS_SYMPTOMS_NAME,
    assess_symptoms_handler,
)

SUPPORTED_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "Pharmacy MCP Server"
SERVER_VERSION = "0.1.0"

ServerResult: TypeAlias = Response | ErrorResponse | None
MethodHandler: TypeAlias = Callable[[Request], JsonValue]


class ServerState(Enum):
    """Estados mínimos del ciclo de vida inicial de una conexión MCP."""

    UNINITIALIZED = auto()
    INITIALIZING = auto()
    READY = auto()


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
        self.state = ServerState.UNINITIALIZED
        self._tools: dict[str, Tool] = {}
        self._method_handlers: dict[str, MethodHandler] = {
            "initialize": self._handle_initialize,
            "notifications/initialized": self._handle_initialized_notification,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
        }
        self.register_tool(
            name=ASSESS_SYMPTOMS_NAME,
            description=ASSESS_SYMPTOMS_DESCRIPTION,
            input_schema=ASSESS_SYMPTOMS_INPUT_SCHEMA,
            handler=assess_symptoms_handler,
        )
        catalog = load_default_catalog()
        inventory = load_default_inventory(catalog)
        interactions = load_default_interactions(catalog)
        query_handlers = PharmacyQueryHandlers(
            catalog=catalog,
            inventory=inventory,
            interactions=interactions,
        )
        self.register_tool(
            name=SEARCH_MEDICATIONS_NAME,
            description=SEARCH_MEDICATIONS_DESCRIPTION,
            input_schema=SEARCH_MEDICATIONS_INPUT_SCHEMA,
            handler=query_handlers.search_medications,
        )
        self.register_tool(
            name=GET_MEDICATION_DETAILS_NAME,
            description=GET_MEDICATION_DETAILS_DESCRIPTION,
            input_schema=GET_MEDICATION_DETAILS_INPUT_SCHEMA,
            handler=query_handlers.get_medication_details,
        )
        self.register_tool(
            name=CHECK_INTERACTIONS_NAME,
            description=CHECK_INTERACTIONS_DESCRIPTION,
            input_schema=CHECK_INTERACTIONS_INPUT_SCHEMA,
            handler=query_handlers.check_interactions,
        )
        self.register_tool(
            name=CHECK_STOCK_NAME,
            description=CHECK_STOCK_DESCRIPTION,
            input_schema=CHECK_STOCK_INPUT_SCHEMA,
            handler=query_handlers.check_stock,
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
        """Despacha un mensaje y omite toda respuesta para notificaciones."""

        if not isinstance(request, Request):
            return self._error_response(
                None,
                code=INVALID_REQUEST,
                message="Invalid Request",
            )

        request_id = self._request_id(request)

        try:
            handler = self._method_handlers.get(request.method)
            if handler is None:
                raise MethodNotFoundError(
                    f"Method not found: '{request.method}'."
                )
            result = handler(request)
        except JsonRpcError as exc:
            if request.is_notification:
                return None
            return self._error_response(
                request_id,
                code=exc.code,
                message=exc.message,
            )
        except Exception:
            if request.is_notification:
                return None
            return self._error_response(
                request_id,
                code=INTERNAL_ERROR,
                message="Internal error",
            )

        if request.is_notification:
            return None

        try:
            return Response(result=result, id=request_id)
        except JsonRpcError:
            return self._error_response(
                request_id,
                code=INTERNAL_ERROR,
                message="Internal error",
            )

    def _handle_initialize(self, request: Request) -> JsonValue:
        if request.is_notification:
            raise InvalidRequestError("'initialize' requires a request id.")
        if self.state is not ServerState.UNINITIALIZED:
            raise InvalidRequestError("Server initialization has already started.")

        params = self._object_params(request)
        requested_version = params.get("protocolVersion")
        if not isinstance(requested_version, str) or not requested_version.strip():
            raise InvalidParamsError(
                "'protocolVersion' must be a non-empty string."
            )

        client_capabilities = params.get("capabilities")
        if not isinstance(client_capabilities, dict):
            raise InvalidParamsError("'capabilities' must be an object.")

        client_info = params.get("clientInfo")
        if not isinstance(client_info, dict):
            raise InvalidParamsError("'clientInfo' must be an object.")
        client_name = client_info.get("name")
        if not isinstance(client_name, str) or not client_name.strip():
            raise InvalidParamsError("'clientInfo.name' must be a non-empty string.")
        client_version = client_info.get("version")
        if not isinstance(client_version, str) or not client_version.strip():
            raise InvalidParamsError(
                "'clientInfo.version' must be a non-empty string."
            )

        if requested_version != self.protocol_version:
            raise InvalidParamsError(
                f"Unsupported protocol version: '{requested_version}'. "
                f"Supported version: '{self.protocol_version}'."
            )

        self.state = ServerState.INITIALIZING

        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _handle_initialized_notification(self, request: Request) -> JsonValue:
        if not request.is_notification:
            raise InvalidRequestError(
                "'notifications/initialized' must not include a request id."
            )
        self._object_params(request)
        if self.state is ServerState.INITIALIZING:
            self.state = ServerState.READY
        return {}

    def _handle_tools_list(self, request: Request) -> JsonValue:
        self._require_ready()
        self._object_params(request)
        return {
            "tools": [tool.to_definition() for tool in self._tools.values()]
        }

    def _handle_tools_call(self, request: Request) -> JsonValue:
        self._require_ready()
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

    def _require_ready(self) -> None:
        if self.state is not ServerState.READY:
            raise ServerNotInitializedError(
                "Server is not ready; complete MCP initialization first."
            )

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
