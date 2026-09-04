"""Configurable host for local MCP servers over stdio."""

from .config import (
    DEFAULT_CONFIG_PATH,
    HostConfig,
    HostConfigurationError,
    RepositoryPolicyConfig,
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
from .policy import RepositoryPolicyViolation, prepare_repository_invocation
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
    "RepositoryPolicyConfig",
    "RepositoryPolicyViolation",
    "ServerSummary",
    "StdioMCPClient",
    "StdioServerConfig",
    "load_host_config",
    "prepare_repository_invocation",
    "redact_sensitive_data",
]
