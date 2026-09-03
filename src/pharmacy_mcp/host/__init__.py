"""Configurable host for local MCP servers over stdio."""

from .config import (
    DEFAULT_CONFIG_PATH,
    HostConfig,
    HostConfigurationError,
    StdioServerConfig,
    load_host_config,
)
from .manager import (
    NAMESPACE_SEPARATOR,
    MCPServerManager,
    RegisteredTool,
    ServerSummary,
)
from .protocol_log import (
    DEFAULT_LOG_PATH,
    REDACTION_MARKER,
    MCPLogError,
    MCPProtocolLogger,
    redact_sensitive_data,
)
from .stdio_client import (
    HOST_NAME,
    HOST_VERSION,
    MCP_PROTOCOL_VERSION,
    MCPHostError,
    MCPProtocolError,
    MCPServerResponseError,
    MCPTransportError,
    StdioMCPClient,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_LOG_PATH",
    "HOST_NAME",
    "HOST_VERSION",
    "MCP_PROTOCOL_VERSION",
    "NAMESPACE_SEPARATOR",
    "REDACTION_MARKER",
    "HostConfig",
    "HostConfigurationError",
    "MCPHostError",
    "MCPLogError",
    "MCPProtocolError",
    "MCPProtocolLogger",
    "MCPServerManager",
    "MCPServerResponseError",
    "MCPTransportError",
    "RegisteredTool",
    "ServerSummary",
    "StdioMCPClient",
    "StdioServerConfig",
    "load_host_config",
    "redact_sensitive_data",
]
