"""Strict loader for stdio MCP servers configured for the host."""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "mcp-servers.json"
)

_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_NAMESPACE_SEPARATOR = "__"
_SERVER_KEYS = {
    "name",
    "transport",
    "command",
    "args",
    "cwd",
    "env",
    "request_timeout_seconds",
    "shutdown_timeout_seconds",
    "enabled",
}


class HostConfigurationError(ValueError):
    """The local host configuration is invalid."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StdioServerConfig:
    """Validated configuration for starting one server without a shell."""

    name: str
    command: str
    args: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    request_timeout_seconds: float = 10.0
    shutdown_timeout_seconds: float = 5.0
    enabled: bool = True
    transport: str = "stdio"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not _SERVER_NAME_PATTERN.fullmatch(self.name)
            or _NAMESPACE_SEPARATOR in self.name
            or self.name.endswith("_")
        ):
            raise HostConfigurationError(
                "Server names must start with a letter and contain only "
                "letters, digits, underscores, or hyphens, without '__' and "
                "without a trailing underscore."
            )
        if self.transport != "stdio":
            raise HostConfigurationError("Only the 'stdio' transport is supported.")
        if not isinstance(self.command, str) or not self.command.strip():
            raise HostConfigurationError("Server 'command' must be non-empty.")
        if not isinstance(self.args, tuple) or not all(
            isinstance(value, str) and value for value in self.args
        ):
            raise HostConfigurationError("Server 'args' must be strings.")
        if not isinstance(self.cwd, Path) or not self.cwd.is_dir():
            raise HostConfigurationError("Server 'cwd' must be an existing directory.")
        if not isinstance(self.env, Mapping) or not all(
            isinstance(key, str)
            and key
            and isinstance(value, str)
            for key, value in self.env.items()
        ):
            raise HostConfigurationError(
                "Server 'env' must map non-empty names to string values."
            )
        _validate_timeout(self.request_timeout_seconds, "request_timeout_seconds")
        _validate_timeout(self.shutdown_timeout_seconds, "shutdown_timeout_seconds")
        if not isinstance(self.enabled, bool):
            raise HostConfigurationError("Server 'enabled' must be a boolean.")

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.command, *self.args)


@dataclass(frozen=True, slots=True, kw_only=True)
class HostConfig:
    """Non-empty server collection with unique names."""

    servers: tuple[StdioServerConfig, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.servers, tuple) or not self.servers:
            raise HostConfigurationError(
                "Host configuration must contain at least one server."
            )
        if not all(isinstance(server, StdioServerConfig) for server in self.servers):
            raise HostConfigurationError(
                "Host configuration contains an invalid server."
            )
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise HostConfigurationError("Server names must be unique.")


def load_host_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> HostConfig:
    """Read local JSON and resolve ``cwd`` relative to its file."""

    path = Path(config_path).resolve()
    try:
        raw_config = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except OSError as exc:
        raise HostConfigurationError(
            f"Cannot read host configuration '{path}'."
        ) from exc
    except json.JSONDecodeError as exc:
        raise HostConfigurationError(
            f"Host configuration is not valid JSON: line {exc.lineno}, "
            f"column {exc.colno}."
        ) from exc

    if not isinstance(raw_config, dict) or set(raw_config) != {"servers"}:
        raise HostConfigurationError(
            "Host configuration must contain only a 'servers' array."
        )
    raw_servers = raw_config["servers"]
    if not isinstance(raw_servers, list) or not raw_servers:
        raise HostConfigurationError("'servers' must be a non-empty array.")

    servers = tuple(
        _parse_server(raw_server, path.parent, index)
        for index, raw_server in enumerate(raw_servers)
    )
    return HostConfig(servers=servers)


def _parse_server(
    value: object,
    config_directory: Path,
    index: int,
) -> StdioServerConfig:
    label = f"servers[{index}]"
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise HostConfigurationError(f"'{label}' must be an object.")
    unexpected = sorted(set(value) - _SERVER_KEYS)
    if unexpected:
        raise HostConfigurationError(
            f"Unexpected fields in '{label}': {', '.join(unexpected)}."
        )

    for required in ("name", "transport", "command"):
        if required not in value:
            raise HostConfigurationError(f"'{label}.{required}' is required.")

    raw_args = value.get("args", [])
    if not isinstance(raw_args, list) or not all(
        isinstance(argument, str) and argument for argument in raw_args
    ):
        raise HostConfigurationError(f"'{label}.args' must be an array of strings.")

    raw_env = value.get("env", {})
    if not isinstance(raw_env, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(env_value, str)
        for key, env_value in raw_env.items()
    ):
        raise HostConfigurationError(
            f"'{label}.env' must map names to string values."
        )

    raw_cwd = value.get("cwd", ".")
    if not isinstance(raw_cwd, str) or not raw_cwd.strip():
        raise HostConfigurationError(f"'{label}.cwd' must be a non-empty string.")
    cwd = (config_directory / raw_cwd).resolve()

    raw_command = value["command"]
    if raw_command == "${PYTHON_EXECUTABLE}":
        command = sys.executable
    elif isinstance(raw_command, str):
        command = raw_command
    else:
        raise HostConfigurationError(f"'{label}.command' must be a string.")

    return StdioServerConfig(
        name=value["name"],
        transport=value["transport"],
        command=command,
        args=tuple(raw_args),
        cwd=cwd,
        env=MappingProxyType(dict(raw_env)),
        request_timeout_seconds=_optional_timeout(
            value,
            "request_timeout_seconds",
            10.0,
            label,
        ),
        shutdown_timeout_seconds=_optional_timeout(
            value,
            "shutdown_timeout_seconds",
            5.0,
            label,
        ),
        enabled=value.get("enabled", True),
    )


def _optional_timeout(
    value: dict[str, object],
    field_name: str,
    default: float,
    label: str,
) -> float:
    timeout = value.get(field_name, default)
    _validate_timeout(timeout, f"{label}.{field_name}")
    return float(timeout)


def _validate_timeout(value: object, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > 300
    ):
        raise HostConfigurationError(
            f"'{field_name}' must be a number greater than 0 and at most 300."
        )


def _reject_json_constant(value: str) -> None:
    raise HostConfigurationError(
        f"Host configuration contains invalid JSON numeric constant: {value}."
    )
