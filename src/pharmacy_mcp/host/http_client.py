"""Synchronous manual client for MCP Streamable HTTP without SSE."""

from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping

from pharmacy_mcp.jsonrpc import (
    ErrorResponse,
    JsonRpcError,
    Request,
    Response,
    deserialize_message,
    serialize_message,
)
from pharmacy_mcp.jsonrpc.messages import JsonValue

from .config import HTTPServerConfig
from .protocol_log import MCPProtocolLogger
from .stdio_client import (
    HOST_NAME,
    HOST_VERSION,
    MCP_PROTOCOL_VERSION,
    MCPProtocolError,
    MCPServerResponseError,
    MCPTransportError,
)

ACCEPT_HEADER = "application/json, text/event-stream"
SESSION_HEADER = "MCP-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"


@dataclass(frozen=True, slots=True, kw_only=True)
class MCPHTTPRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: float
    max_response_bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MCPHTTPResponse:
    status: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    body_truncated: bool = False


HTTPMCPTransport = Callable[[MCPHTTPRequest], MCPHTTPResponse]


class MCPUrllibHTTPTransport:
    """Perform one bounded HTTP exchange and close every response stream."""

    def __call__(self, request: MCPHTTPRequest) -> MCPHTTPResponse:
        raw_request = urllib.request.Request(
            request.url,
            data=request.body if request.method == "POST" else None,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            response = urllib.request.urlopen(
                raw_request,
                timeout=request.timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            try:
                body, truncated = _bounded_read(exc, request.max_response_bytes)
                headers = getattr(exc, "headers", None)
                return MCPHTTPResponse(
                    status=exc.code,
                    headers=MappingProxyType(
                        dict(headers.items()) if headers is not None else {}
                    ),
                    body=body,
                    body_truncated=truncated,
                )
            finally:
                exc.close()
        except (TimeoutError, socket.timeout) as exc:
            raise MCPTransportError(
                "Timed out while contacting the MCP HTTP server; the request "
                "may have been processed."
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise MCPTransportError(
                    "Timed out while contacting the MCP HTTP server; the request "
                    "may have been processed."
                ) from exc
            raise MCPTransportError("Could not connect to the MCP HTTP server.") from exc
        except OSError as exc:
            raise MCPTransportError("Could not connect to the MCP HTTP server.") from exc

        try:
            body, truncated = _bounded_read(response, request.max_response_bytes)
            return MCPHTTPResponse(
                status=response.status,
                headers=MappingProxyType(dict(response.headers.items())),
                body=body,
                body_truncated=truncated,
            )
        finally:
            response.close()


class HTTPMCPClient:
    """Manage one stateful MCP session over request/response HTTP."""

    def __init__(
        self,
        config: HTTPServerConfig,
        *,
        protocol_logger: MCPProtocolLogger | None = None,
        transport: HTTPMCPTransport | None = None,
    ) -> None:
        if not isinstance(config, HTTPServerConfig):
            raise TypeError("'config' must be an HTTPServerConfig instance.")
        if config.url is None:
            raise MCPTransportError(
                f"Server '{config.name}' has no configured HTTP URL."
            )
        self.config = config
        self._owns_protocol_logger = protocol_logger is None
        self.protocol_logger = protocol_logger or MCPProtocolLogger()
        self._transport = transport or MCPUrllibHTTPTransport()
        self._exchange_lock = threading.RLock()
        self._next_request_id = 1
        self._session_id: str | None = None
        self._ready = False
        self.server_info: dict[str, JsonValue] | None = None
        self.server_capabilities: dict[str, JsonValue] | None = None

    @property
    def is_running(self) -> bool:
        return self._session_id is not None

    @property
    def is_ready(self) -> bool:
        return self._ready and self._session_id is not None

    @property
    def process_id(self) -> None:
        return None

    def start(self) -> None:
        """Create a session and complete the MCP initialization lifecycle."""

        with self._exchange_lock:
            if self._session_id is not None or self._ready:
                raise MCPTransportError(
                    f"Server '{self.config.name}' has already been started."
                )
            self._next_request_id = 1
            self.server_info = None
            self.server_capabilities = None
            try:
                result, headers = self._send_request(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": HOST_NAME,
                            "version": HOST_VERSION,
                        },
                    },
                    initializing=True,
                )
                session_id = _header(headers, SESSION_HEADER)
                if session_id is None or not _valid_session_id(session_id):
                    raise MCPProtocolError(
                        f"Server '{self.config.name}' did not provide a valid MCP session."
                    )
                self._session_id = session_id
                self._validate_initialize_result(result)
                self._send_notification("notifications/initialized", {})
                self._ready = True
            except Exception:
                try:
                    self._delete_session(suppress_errors=True)
                finally:
                    self._reset_state()
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
            if (
                not isinstance(definition.get("name"), str)
                or not definition["name"].strip()
                or not isinstance(definition.get("description"), str)
                or not isinstance(definition.get("inputSchema"), dict)
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
        with self._exchange_lock:
            self._require_ready()
            result, _ = self._send_request(method, params, initializing=False)
            return result

    def notify(self, method: str, params: dict[str, JsonValue]) -> None:
        with self._exchange_lock:
            self._require_ready()
            self._send_notification(method, params)

    def stop(self) -> None:
        """Best-effort DELETE of the remote session followed by local cleanup."""

        failure: Exception | None = None
        with self._exchange_lock:
            try:
                if self._session_id is not None:
                    self._delete_session(suppress_errors=False)
            except Exception as exc:
                failure = exc
            finally:
                self._reset_state()
                if self._owns_protocol_logger:
                    try:
                        self.protocol_logger.close()
                    except Exception as exc:
                        if failure is None:
                            failure = exc
        if failure is not None:
            raise failure

    def __enter__(self) -> HTTPMCPClient:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()

    def _send_request(
        self,
        method: str,
        params: dict[str, JsonValue],
        *,
        initializing: bool,
    ) -> tuple[JsonValue, Mapping[str, str]]:
        request_id = self._next_request_id
        self._next_request_id += 1
        try:
            payload = serialize_message(
                Request(method=method, params=params, id=request_id)
            )
        except JsonRpcError as exc:
            raise MCPProtocolError(exc.message) from exc
        self.protocol_logger.outbound(
            self.config.name,
            payload,
            self.config.transport,
        )
        response = self._post(
            payload.encode("utf-8"),
            initializing=initializing,
            method=method,
        )
        self._require_status(response, {200}, method)
        raw_payload = self._json_response_payload(response)
        self.protocol_logger.inbound(
            self.config.name,
            raw_payload,
            self.config.transport,
        )
        message = self._deserialize_response(raw_payload)
        if message.id != request_id:
            raise MCPProtocolError(
                f"Server '{self.config.name}' response ID {message.id!r} "
                f"does not match request ID {request_id!r}."
            )
        if isinstance(message, ErrorResponse):
            error_data = message.error.to_dict().get("data")
            raise MCPServerResponseError(
                server_name=self.config.name,
                code=message.error.code,
                message=message.error.message,
                data=error_data,
            )
        return message.result, response.headers

    def _send_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        try:
            payload = serialize_message(Request(method=method, params=params))
        except JsonRpcError as exc:
            raise MCPProtocolError(exc.message) from exc
        self.protocol_logger.outbound(
            self.config.name,
            payload,
            self.config.transport,
        )
        response = self._post(
            payload.encode("utf-8"),
            initializing=False,
            method=method,
        )
        self._require_status(response, {202}, method)
        if response.body:
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned a body for an MCP notification."
            )

    def _post(
        self,
        body: bytes,
        *,
        initializing: bool,
        method: str,
    ) -> MCPHTTPResponse:
        if len(body) > self.config.max_request_bytes:
            raise MCPTransportError(
                f"Server '{self.config.name}' request exceeded the size limit."
            )
        headers = self._headers(
            include_session=not initializing,
            include_protocol=not initializing,
        )
        request = MCPHTTPRequest(
            method="POST",
            url=self.config.url or "",
            headers=MappingProxyType(headers),
            body=body,
            timeout_seconds=self.config.timeout_seconds,
            max_response_bytes=self.config.max_response_bytes,
        )
        response = self._call_transport(request)
        self._validate_http_response(response)
        self.protocol_logger.host_event(
            self.config.name,
            "http_response",
            {"status": response.status},
            transport="http",
            method=method,
            category="mcp",
        )
        return response

    def _delete_session(self, *, suppress_errors: bool) -> None:
        if self._session_id is None:
            return
        request = MCPHTTPRequest(
            method="DELETE",
            url=self.config.url or "",
            headers=MappingProxyType(
                self._headers(include_session=True, include_protocol=True)
            ),
            body=b"",
            timeout_seconds=self.config.timeout_seconds,
            max_response_bytes=self.config.max_response_bytes,
        )
        try:
            response = self._call_transport(request)
            self._validate_http_response(response)
            if response.status not in {200, 204, 404}:
                self._require_status(response, {200, 204, 404}, "DELETE")
            if response.status != 404 and response.body:
                raise MCPProtocolError(
                    f"Server '{self.config.name}' returned a body while closing "
                    "the MCP session."
                )
            self.protocol_logger.host_event(
                self.config.name,
                "http_session_closed",
                {"status": response.status},
                transport="http",
                method="DELETE",
                category="mcp",
            )
        except Exception:
            if not suppress_errors:
                raise

    def _headers(
        self,
        *,
        include_session: bool,
        include_protocol: bool,
    ) -> dict[str, str]:
        headers = {
            "Accept": ACCEPT_HEADER,
            "Content-Type": "application/json",
        }
        if self.config.token is not None:
            headers["Authorization"] = f"Bearer {self.config.token}"
        if include_session:
            if self._session_id is None:
                raise MCPTransportError(
                    f"Server '{self.config.name}' has no active MCP session."
                )
            headers[SESSION_HEADER] = self._session_id
        if include_protocol:
            headers[PROTOCOL_HEADER] = MCP_PROTOCOL_VERSION
        return headers

    def _call_transport(self, request: MCPHTTPRequest) -> MCPHTTPResponse:
        try:
            return self._transport(request)
        except MCPTransportError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise MCPTransportError(
                "Timed out while contacting the MCP HTTP server; the request "
                "may have been processed."
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise MCPTransportError("Could not connect to the MCP HTTP server.") from exc
        except Exception as exc:
            raise MCPTransportError("MCP HTTP transport failed.") from exc

    def _validate_http_response(self, response: object) -> None:
        if (
            not isinstance(response, MCPHTTPResponse)
            or isinstance(response.status, bool)
            or not isinstance(response.status, int)
            or response.status < 100
            or response.status > 599
            or not isinstance(response.headers, Mapping)
            or not isinstance(response.body, bytes)
            or not isinstance(response.body_truncated, bool)
        ):
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned an invalid HTTP response."
            )
        if (
            response.body_truncated
            or len(response.body) > self.config.max_response_bytes
        ):
            raise MCPTransportError(
                f"Server '{self.config.name}' response exceeded the size limit."
            )

    def _require_status(
        self,
        response: MCPHTTPResponse,
        expected: set[int],
        method: str,
    ) -> None:
        if response.status in expected:
            return
        if response.status == 404:
            self._ready = False
            self._session_id = None
        labels = {
            400: "rejected the MCP HTTP request",
            401: "requires valid Bearer authentication",
            403: "rejected the request origin or authorization",
            404: "does not recognize the MCP session or endpoint",
            405: "does not support the requested HTTP method",
            408: "timed out while processing the MCP request",
            429: "is rate limiting MCP requests",
            500: "reported an internal HTTP failure",
            502: "is unavailable through its gateway",
            503: "is temporarily unavailable",
            504: "timed out through its gateway",
        }
        detail = labels.get(response.status, f"returned HTTP {response.status}")
        raise MCPTransportError(
            f"Server '{self.config.name}' {detail} during {method}."
        )

    def _json_response_payload(self, response: MCPHTTPResponse) -> str:
        content_type = _header(response.headers, "Content-Type") or ""
        segments = [segment.strip() for segment in content_type.split(";")]
        media_type = segments[0].casefold()
        if media_type == "text/event-stream":
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned SSE, which this client "
                "does not implement."
            )
        if media_type != "application/json":
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned an unsupported content type."
            )
        for parameter in segments[1:]:
            name, separator, value = parameter.partition("=")
            if (
                separator == "="
                and name.strip().casefold() == "charset"
                and value.strip().strip('"').casefold() not in {"utf-8", "utf8"}
            ):
                raise MCPProtocolError(
                    f"Server '{self.config.name}' returned a non-UTF-8 charset."
                )
        if not response.body:
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned an empty JSON-RPC response."
            )
        try:
            return response.body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned invalid UTF-8."
            ) from exc

    def _deserialize_response(self, payload: str) -> Response | ErrorResponse:
        try:
            message = deserialize_message(payload)
        except JsonRpcError as exc:
            raise MCPProtocolError(
                f"Server '{self.config.name}' returned invalid JSON-RPC: "
                f"{exc.message}"
            ) from exc
        if not isinstance(message, (Response, ErrorResponse)):
            raise MCPProtocolError(
                f"Server '{self.config.name}' sent an unsupported request."
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

    def _reset_state(self) -> None:
        self._ready = False
        self._session_id = None
        self.server_info = None
        self.server_capabilities = None


def _bounded_read(stream: object, maximum: int) -> tuple[bytes, bool]:
    body = stream.read(maximum + 1)
    if not isinstance(body, bytes):
        raise MCPTransportError("MCP HTTP server returned a non-binary body.")
    return body[:maximum], len(body) > maximum


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.casefold() == name.casefold() and isinstance(value, str):
            return value
    return None


def _valid_session_id(value: str) -> bool:
    return 1 <= len(value) <= 512 and all(0x21 <= ord(char) <= 0x7E for char in value)
