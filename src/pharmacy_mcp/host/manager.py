"""MCP process manager and namespaced tool registry."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .config import HostConfig, StdioServerConfig
from .protocol_log import MCPProtocolLogger
from .stdio_client import MCPHostError, MCPProtocolError
from .stdio_client import StdioMCPClient

NAMESPACE_SEPARATOR = "__"
_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-][A-Za-z0-9_-]*$")


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisteredTool:
    """Public tool definition linked to its source server."""

    namespaced_name: str
    server_name: str
    tool_name: str
    description: str
    input_schema: dict[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.namespaced_name,
            "server": self.server_name,
            "tool": self.tool_name,
            "description": self.description,
            "inputSchema": deepcopy(self.input_schema),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerSummary:
    """Observable state of a configured server."""

    name: str
    transport: str
    enabled: bool
    status: str
    process_id: int | None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "transport": self.transport,
            "enabled": self.enabled,
            "status": self.status,
            "process_id": self.process_id,
        }


class MCPServerManager:
    """Start multiple servers and route tools by ``server__tool``."""

    def __init__(
        self,
        config: HostConfig,
        *,
        protocol_logger: MCPProtocolLogger | None = None,
    ) -> None:
        if not isinstance(config, HostConfig):
            raise TypeError("'config' must be a HostConfig instance.")
        self._config = config
        self._configs = {server.name: server for server in config.servers}
        self._owns_protocol_logger = protocol_logger is None
        self._protocol_logger = (
            protocol_logger if protocol_logger is not None else MCPProtocolLogger()
        )
        self._clients: dict[str, StdioMCPClient] = {}
        self._tools: dict[str, RegisteredTool] = {}

    def list_servers(self) -> tuple[ServerSummary, ...]:
        """List configuration and state without starting new processes."""

        summaries: list[ServerSummary] = []
        for config in self._config.servers:
            client = self._clients.get(config.name)
            summaries.append(
                ServerSummary(
                    name=config.name,
                    transport=config.transport,
                    enabled=config.enabled,
                    status=(
                        "ready"
                        if client is not None and client.is_ready
                        else "stopped"
                    ),
                    process_id=client.process_id if client is not None else None,
                )
            )
        return tuple(summaries)

    def start_server(self, server_name: str) -> None:
        """Start a server, negotiate MCP, and register all its tools."""

        config = self._require_config(server_name)
        if not config.enabled:
            raise MCPHostError(f"Server '{server_name}' is disabled.")
        existing = self._clients.get(server_name)
        if existing is not None and existing.is_ready:
            return
        if existing is not None:
            self.stop_server(server_name)

        client = StdioMCPClient(
            config,
            protocol_logger=self._protocol_logger,
        )
        try:
            client.start()
            definitions = client.list_tools()
            registered = self._registered_definitions(config, definitions)
        except Exception:
            client.stop()
            raise

        self._clients[server_name] = client
        for tool in registered:
            self._tools[tool.namespaced_name] = tool

    def start_all(self) -> None:
        """Start enabled servers and roll back servers started by this call."""

        started_here: list[str] = []
        try:
            for config in self._config.servers:
                if not config.enabled:
                    continue
                existing = self._clients.get(config.name)
                if existing is not None and existing.is_ready:
                    continue
                self.start_server(config.name)
                started_here.append(config.name)
        except Exception:
            for server_name in reversed(started_here):
                self.stop_server(server_name)
            raise

    def stop_server(self, server_name: str) -> None:
        """Remove one server's registry and close its stdin cleanly."""

        self._require_config(server_name)
        client = self._clients.pop(server_name, None)
        self._tools = {
            name: tool
            for name, tool in self._tools.items()
            if tool.server_name != server_name
        }
        if client is not None:
            client.stop()

    def stop_all(self) -> None:
        try:
            for server_name in reversed(tuple(self._clients)):
                self.stop_server(server_name)
        finally:
            if self._owns_protocol_logger:
                self._protocol_logger.close()

    def list_tools(
        self,
        server_name: str | None = None,
    ) -> tuple[RegisteredTool, ...]:
        """Return the deterministic registry without ambiguous global names."""

        if server_name is not None:
            self._require_config(server_name)
        return tuple(
            tool
            for tool in self._tools.values()
            if server_name is None or tool.server_name == server_name
        )

    def invoke_tool(
        self,
        namespaced_name: str,
        arguments: dict[str, JsonValue],
    ) -> JsonValue:
        """Resolve ``server__tool`` and invoke the original server tool name."""

        tool = self.resolve_tool(namespaced_name)
        client = self._clients.get(tool.server_name)
        if client is None or not client.is_ready:
            raise MCPHostError(f"Server '{tool.server_name}' is not ready.")
        return client.call_tool(tool.tool_name, arguments)

    def resolve_tool(self, namespaced_name: str) -> RegisteredTool:
        """Map one global name back to its server and original tool name."""

        tool = self._tools.get(namespaced_name)
        if tool is None:
            raise MCPHostError(
                f"Namespaced tool '{namespaced_name}' is not registered."
            )
        return tool

    def server_name_from_namespace(self, namespaced_name: str) -> str:
        if not isinstance(namespaced_name, str):
            raise MCPHostError("Namespaced tool name must be a string.")
        if namespaced_name.count(NAMESPACE_SEPARATOR) != 1:
            raise MCPHostError(
                f"Namespaced tool '{namespaced_name}' is not registered; "
                "tool names must use exactly one '<server>__<tool>' separator."
            )
        server_name, tool_name = namespaced_name.split(NAMESPACE_SEPARATOR)
        if (
            not _SERVER_NAME_PATTERN.fullmatch(server_name)
            or server_name.endswith("_")
            or not _TOOL_NAME_PATTERN.fullmatch(tool_name)
        ):
            raise MCPHostError(
                "Namespaced tool contains incompatible characters."
            )
        self._require_config(server_name)
        return server_name

    def _registered_definitions(
        self,
        config: StdioServerConfig,
        definitions: tuple[dict[str, JsonValue], ...],
    ) -> tuple[RegisteredTool, ...]:
        registered: list[RegisteredTool] = []
        names_seen: set[str] = set()
        for definition in definitions:
            tool_name = definition["name"]
            description = definition["description"]
            input_schema = definition["inputSchema"]
            if not isinstance(tool_name, str):
                raise MCPProtocolError("Validated tool name changed type.")
            if not isinstance(description, str) or not isinstance(input_schema, dict):
                raise MCPProtocolError("Validated tool definition changed type.")
            if (
                not _TOOL_NAME_PATTERN.fullmatch(tool_name)
                or NAMESPACE_SEPARATOR in tool_name
            ):
                raise MCPProtocolError(
                    f"Server '{config.name}' published incompatible tool name "
                    f"'{tool_name}'."
                )
            namespaced_name = (
                f"{config.name}{NAMESPACE_SEPARATOR}{tool_name}"
            )
            if namespaced_name in names_seen or namespaced_name in self._tools:
                raise MCPProtocolError(
                    f"Duplicate namespaced tool '{namespaced_name}'."
                )
            registered.append(
                RegisteredTool(
                    namespaced_name=namespaced_name,
                    server_name=config.name,
                    tool_name=tool_name,
                    description=description,
                    input_schema=deepcopy(input_schema),
                )
            )
            names_seen.add(namespaced_name)
        return tuple(registered)

    def _require_config(self, server_name: str) -> StdioServerConfig:
        try:
            return self._configs[server_name]
        except (KeyError, TypeError) as exc:
            raise MCPHostError(f"Unknown configured server: '{server_name}'.") from exc

    def __enter__(self) -> MCPServerManager:
        self.start_all()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop_all()
