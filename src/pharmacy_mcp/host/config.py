"""Strict loader for stdio MCP servers configured for the host."""

from __future__ import annotations

import json
import math
import os
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
    "repository_policy",
    "filesystem_policy",
}
_ROOT_KEYS = {"servers", "variables"}
_VARIABLE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VARIABLE_REFERENCE_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_ARGUMENT_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-][A-Za-z0-9_-]*$")
_REPOSITORY_POLICY_KEYS = {"root", "argument", "mutable_tools"}
_FILESYSTEM_POLICY_KEYS = {
    "root",
    "path_arguments",
    "creation_arguments",
}
_PROJECT_DIRECTORY = Path(__file__).resolve().parents[3]
_WINDOWS_PLATFORM = os.name == "nt"


class HostConfigurationError(ValueError):
    """The local host configuration is invalid."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryPolicyConfig:
    """Restrict a server's repository argument and mutable tool calls."""

    root: Path
    argument_name: str
    mutable_tools: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise HostConfigurationError(
                "Repository policy 'root' must resolve to an absolute path."
            )
        try:
            canonical_root = self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise HostConfigurationError(
                "Repository policy 'root' must be an existing directory."
            ) from exc
        if not canonical_root.is_dir():
            raise HostConfigurationError(
                "Repository policy 'root' must be an existing directory."
            )
        object.__setattr__(self, "root", canonical_root)
        if (
            not isinstance(self.argument_name, str)
            or not _ARGUMENT_NAME_PATTERN.fullmatch(self.argument_name)
        ):
            raise HostConfigurationError(
                "Repository policy 'argument' must be a valid argument name."
            )
        if not isinstance(self.mutable_tools, frozenset) or not self.mutable_tools:
            raise HostConfigurationError(
                "Repository policy 'mutable_tools' must be a non-empty array."
            )
        if not all(
            isinstance(name, str)
            and _TOOL_NAME_PATTERN.fullmatch(name)
            and "__" not in name
            for name in self.mutable_tools
        ):
            raise HostConfigurationError(
                "Repository policy mutable tool names are invalid."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class FilesystemPolicyConfig:
    """Restrict every filesystem path and infer mutation from annotations."""

    root: Path
    path_arguments: tuple[str, ...]
    creation_arguments: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise HostConfigurationError(
                "Filesystem policy 'root' must resolve to an absolute path."
            )
        if ".." in self.root.parts:
            raise HostConfigurationError(
                "Filesystem policy 'root' must not contain '..'."
            )
        try:
            canonical_root = self.root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise HostConfigurationError(
                "Filesystem policy 'root' must be an existing directory."
            ) from exc
        if not canonical_root.is_dir():
            raise HostConfigurationError(
                "Filesystem policy 'root' must be an existing directory."
            )
        _reject_unsafe_filesystem_root(canonical_root)
        object.__setattr__(self, "root", canonical_root)

        if (
            not isinstance(self.path_arguments, tuple)
            or not self.path_arguments
            or len(self.path_arguments) != len(set(self.path_arguments))
            or not all(
                isinstance(name, str)
                and _ARGUMENT_NAME_PATTERN.fullmatch(name)
                for name in self.path_arguments
            )
        ):
            raise HostConfigurationError(
                "Filesystem policy 'path_arguments' must be a non-empty array "
                "of unique argument names."
            )
        if not isinstance(self.creation_arguments, Mapping):
            raise HostConfigurationError(
                "Filesystem policy 'creation_arguments' must be an object."
            )
        normalized_creation_arguments: dict[str, frozenset[str]] = {}
        for tool_name, argument_names in self.creation_arguments.items():
            if (
                not isinstance(tool_name, str)
                or not _TOOL_NAME_PATTERN.fullmatch(tool_name)
                or "__" in tool_name
                or not isinstance(argument_names, frozenset)
                or not argument_names
                or not all(
                    isinstance(name, str) and name in self.path_arguments
                    for name in argument_names
                )
            ):
                raise HostConfigurationError(
                    "Filesystem policy creation tool/argument names are invalid."
                )
            normalized_creation_arguments[tool_name] = argument_names
        object.__setattr__(
            self,
            "creation_arguments",
            MappingProxyType(normalized_creation_arguments),
        )


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
    repository_policy: RepositoryPolicyConfig | None = None
    filesystem_policy: FilesystemPolicyConfig | None = None

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
        if self.repository_policy is not None and not isinstance(
            self.repository_policy, RepositoryPolicyConfig
        ):
            raise HostConfigurationError(
                "Server 'repository_policy' must be a repository policy."
            )
        if self.filesystem_policy is not None and not isinstance(
            self.filesystem_policy, FilesystemPolicyConfig
        ):
            raise HostConfigurationError(
                "Server 'filesystem_policy' must be a filesystem policy."
            )
        if self.repository_policy is not None and self.filesystem_policy is not None:
            raise HostConfigurationError(
                "A server cannot combine repository and filesystem policies."
            )

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.command, *self.args)


@dataclass(frozen=True, slots=True, kw_only=True)
class HostConfig:
    """Non-empty server collection with unique names."""

    servers: tuple[StdioServerConfig, ...]
    variables: tuple[str, ...] = ()

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
        if (
            not isinstance(self.variables, tuple)
            or len(self.variables) != len(set(self.variables))
            or not all(
                isinstance(name, str) and _VARIABLE_NAME_PATTERN.fullmatch(name)
                for name in self.variables
            )
        ):
            raise HostConfigurationError(
                "Host configuration variables must be unique uppercase names."
            )


def load_host_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    environ: Mapping[str, str] | None = None,
) -> HostConfig:
    """Read JSON, expand only declared variables, and validate all servers."""

    path = Path(config_path).resolve()
    try:
        raw_config = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, RuntimeError) as exc:
        raise HostConfigurationError(
            f"Cannot read host configuration '{path}'."
        ) from exc
    except json.JSONDecodeError as exc:
        raise HostConfigurationError(
            f"Host configuration is not valid JSON: line {exc.lineno}, "
            f"column {exc.colno}."
        ) from exc

    if not isinstance(raw_config, dict) or not set(raw_config).issubset(_ROOT_KEYS):
        raise HostConfigurationError(
            "Host configuration may contain only 'servers' and 'variables'."
        )
    if "servers" not in raw_config:
        raise HostConfigurationError("Host configuration requires 'servers'.")
    raw_variables = raw_config.get("variables", [])
    if not isinstance(raw_variables, list) or not all(
        isinstance(name, str) and _VARIABLE_NAME_PATTERN.fullmatch(name)
        for name in raw_variables
    ):
        raise HostConfigurationError(
            "'variables' must be an array of uppercase environment variable names."
        )
    if len(raw_variables) != len(set(raw_variables)):
        raise HostConfigurationError("Host configuration variables must be unique.")
    declared_variables = tuple(raw_variables)
    environment = os.environ if environ is None else environ
    raw_servers = raw_config["servers"]
    if not isinstance(raw_servers, list) or not raw_servers:
        raise HostConfigurationError("'servers' must be a non-empty array.")

    servers = tuple(
        _parse_server(
            raw_server,
            path.parent,
            index,
            declared_variables,
            environment,
        )
        for index, raw_server in enumerate(raw_servers)
    )
    return HostConfig(servers=servers, variables=declared_variables)


def _parse_server(
    value: object,
    config_directory: Path,
    index: int,
    declared_variables: tuple[str, ...],
    environ: Mapping[str, str],
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
    expanded_cwd = _substitute_variables(
        raw_cwd, declared_variables, environ, f"{label}.cwd"
    )
    cwd = (config_directory / expanded_cwd).resolve()

    expanded_args = tuple(
        _substitute_variables(
            argument,
            declared_variables,
            environ,
            f"{label}.args[{argument_index}]",
        )
        for argument_index, argument in enumerate(raw_args)
    )
    raw_command = value["command"]
    if raw_command == "${PYTHON_EXECUTABLE}":
        command = sys.executable
        command_args = expanded_args
    elif raw_command == "${NPX_EXECUTABLE}":
        command, command_args = _npx_launcher(expanded_args)
    elif isinstance(raw_command, str):
        command = _substitute_variables(
            raw_command, declared_variables, environ, f"{label}.command"
        )
        command_args = expanded_args
    else:
        raise HostConfigurationError(f"'{label}.command' must be a string.")

    return StdioServerConfig(
        name=value["name"],
        transport=value["transport"],
        command=command,
        args=command_args,
        cwd=cwd,
        env=MappingProxyType(
            {
                key: _substitute_variables(
                    env_value,
                    declared_variables,
                    environ,
                    f"{label}.env.{key}",
                )
                for key, env_value in raw_env.items()
            }
        ),
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
        repository_policy=_parse_repository_policy(
            value.get("repository_policy"),
            label,
            declared_variables,
            environ,
        ),
        filesystem_policy=_parse_filesystem_policy(
            value.get("filesystem_policy"),
            label,
            declared_variables,
            environ,
        ),
    )


def _parse_repository_policy(
    value: object,
    label: str,
    declared_variables: tuple[str, ...],
    environ: Mapping[str, str],
) -> RepositoryPolicyConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise HostConfigurationError(
            f"'{label}.repository_policy' must be an object."
        )
    unexpected = sorted(set(value) - _REPOSITORY_POLICY_KEYS)
    if unexpected:
        raise HostConfigurationError(
            f"Unexpected fields in '{label}.repository_policy': "
            f"{', '.join(unexpected)}."
        )
    for required in ("root", "mutable_tools"):
        if required not in value:
            raise HostConfigurationError(
                f"'{label}.repository_policy.{required}' is required."
            )

    raw_root = value["root"]
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise HostConfigurationError(
            f"'{label}.repository_policy.root' must be a non-empty string."
        )
    expanded_root = _substitute_variables(
        raw_root,
        declared_variables,
        environ,
        f"{label}.repository_policy.root",
    )
    root = Path(expanded_root)
    if not root.is_absolute():
        raise HostConfigurationError(
            f"'{label}.repository_policy.root' must be an absolute path."
        )
    try:
        canonical_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HostConfigurationError(
            f"'{label}.repository_policy.root' must exist."
        ) from exc

    argument_name = value.get("argument", "repo_path")
    if not isinstance(argument_name, str):
        raise HostConfigurationError(
            f"'{label}.repository_policy.argument' must be a string."
        )
    mutable_tools = value["mutable_tools"]
    if not isinstance(mutable_tools, list):
        raise HostConfigurationError(
            f"'{label}.repository_policy.mutable_tools' must be an array."
        )
    try:
        mutable_set = frozenset(mutable_tools)
    except TypeError as exc:
        raise HostConfigurationError(
            f"'{label}.repository_policy.mutable_tools' must contain strings."
        ) from exc
    if len(mutable_set) != len(mutable_tools):
        raise HostConfigurationError(
            f"'{label}.repository_policy.mutable_tools' must not contain duplicates."
        )
    return RepositoryPolicyConfig(
        root=canonical_root,
        argument_name=argument_name,
        mutable_tools=mutable_set,
    )


def _parse_filesystem_policy(
    value: object,
    label: str,
    declared_variables: tuple[str, ...],
    environ: Mapping[str, str],
) -> FilesystemPolicyConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise HostConfigurationError(
            f"'{label}.filesystem_policy' must be an object."
        )
    unexpected = sorted(set(value) - _FILESYSTEM_POLICY_KEYS)
    if unexpected:
        raise HostConfigurationError(
            f"Unexpected fields in '{label}.filesystem_policy': "
            f"{', '.join(unexpected)}."
        )
    for required in ("root", "path_arguments", "creation_arguments"):
        if required not in value:
            raise HostConfigurationError(
                f"'{label}.filesystem_policy.{required}' is required."
            )

    raw_root = value["root"]
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise HostConfigurationError(
            f"'{label}.filesystem_policy.root' must be a non-empty string."
        )
    expanded_root = _substitute_variables(
        raw_root,
        declared_variables,
        environ,
        f"{label}.filesystem_policy.root",
    )
    root = Path(expanded_root)
    if not root.is_absolute():
        raise HostConfigurationError(
            f"'{label}.filesystem_policy.root' must be an absolute path."
        )

    raw_path_arguments = value["path_arguments"]
    if not isinstance(raw_path_arguments, list):
        raise HostConfigurationError(
            f"'{label}.filesystem_policy.path_arguments' must be an array."
        )
    try:
        path_arguments = tuple(raw_path_arguments)
        if len(path_arguments) != len(set(path_arguments)):
            raise HostConfigurationError(
                f"'{label}.filesystem_policy.path_arguments' must not contain "
                "duplicates."
            )
    except TypeError as exc:
        raise HostConfigurationError(
            f"'{label}.filesystem_policy.path_arguments' must contain strings."
        ) from exc

    raw_creation_arguments = value["creation_arguments"]
    if not isinstance(raw_creation_arguments, dict):
        raise HostConfigurationError(
            f"'{label}.filesystem_policy.creation_arguments' must be an object."
        )
    creation_arguments: dict[str, frozenset[str]] = {}
    for tool_name, raw_names in raw_creation_arguments.items():
        if not isinstance(raw_names, list):
            raise HostConfigurationError(
                f"Creation arguments for '{tool_name}' must be an array."
            )
        try:
            names = frozenset(raw_names)
        except TypeError as exc:
            raise HostConfigurationError(
                f"Creation arguments for '{tool_name}' must contain strings."
            ) from exc
        if len(names) != len(raw_names):
            raise HostConfigurationError(
                f"Creation arguments for '{tool_name}' must not contain duplicates."
            )
        creation_arguments[tool_name] = names

    return FilesystemPolicyConfig(
        root=root,
        path_arguments=path_arguments,
        creation_arguments=MappingProxyType(creation_arguments),
    )


def _npx_launcher(arguments: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Return a platform-safe npx argv without enabling a general shell."""

    if _WINDOWS_PLATFORM:
        command = os.environ.get("COMSPEC", "cmd.exe")
        return command, ("/d", "/s", "/c", "npx", *arguments)
    return "npx", arguments


def _reject_unsafe_filesystem_root(root: Path) -> None:
    """Reject roots that grant access to an unnecessarily broad filesystem."""

    anchor = Path(root.anchor).resolve()
    candidates = (anchor, Path.home().resolve(), _PROJECT_DIRECTORY)
    if any(_same_config_path(root, candidate) for candidate in candidates):
        raise HostConfigurationError(
            "Filesystem policy root must be a dedicated directory, not a system "
            "root, the full user home, or this project's repository root."
        )


def _same_config_path(first: Path, second: Path) -> bool:
    if os.path.normcase(str(first)) == os.path.normcase(str(second)):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _substitute_variables(
    value: str,
    declared_variables: tuple[str, ...],
    environ: Mapping[str, str],
    label: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        variable_name = match.group(1)
        if variable_name not in declared_variables:
            raise HostConfigurationError(
                f"'{label}' references undeclared variable '{variable_name}'."
            )
        replacement = environ.get(variable_name)
        if not isinstance(replacement, str) or not replacement.strip():
            raise HostConfigurationError(
                f"Required variable '{variable_name}' is missing or empty."
            )
        return replacement

    expanded = _VARIABLE_REFERENCE_PATTERN.sub(replace, value)
    if "${" in expanded:
        raise HostConfigurationError(
            f"'{label}' contains an invalid variable reference."
        )
    return expanded


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
