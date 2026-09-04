"""General host-side policies applied before an MCP tool is invoked."""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePath

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .config import FilesystemPolicyConfig, RepositoryPolicyConfig


class RepositoryPolicyViolation(ValueError):
    """A tool invocation violated the configured repository boundary."""

    def __init__(self, message: str, *, event_type: str) -> None:
        super().__init__(message)
        self.event_type = event_type


class FilesystemPolicyViolation(ValueError):
    """A tool invocation violated the configured filesystem boundary."""

    def __init__(
        self,
        message: str,
        *,
        event_type: str,
        path_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.event_type = event_type
        self.path_count = path_count


@dataclass(frozen=True, slots=True, kw_only=True)
class FilesystemInvocation:
    """Validated arguments plus the policy facts needed for safe logging."""

    arguments: dict[str, JsonValue]
    mutation_required: bool
    path_count: int


def prepare_repository_invocation(
    policy: RepositoryPolicyConfig,
    *,
    tool_name: str,
    arguments: dict[str, JsonValue],
    allow_mutation: bool,
) -> dict[str, JsonValue]:
    """Validate one call, returning a copied argument object with a canonical path."""

    if not isinstance(allow_mutation, bool):
        raise TypeError("'allow_mutation' must be a boolean.")
    prepared = deepcopy(arguments)
    raw_path = prepared.get(policy.argument_name)
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RepositoryPolicyViolation(
            f"Tool '{tool_name}' requires a non-empty "
            f"'{policy.argument_name}' string.",
            event_type="repository_rejected",
        )

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise RepositoryPolicyViolation(
            "Repository path must be absolute.",
            event_type="repository_rejected",
        )
    if ".." in PurePath(raw_path).parts:
        raise RepositoryPolicyViolation(
            "Repository path must not contain '..'.",
            event_type="repository_rejected",
        )
    try:
        canonical_candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RepositoryPolicyViolation(
            "Repository path does not exist.",
            event_type="repository_rejected",
        ) from exc
    if not canonical_candidate.is_dir():
        raise RepositoryPolicyViolation(
            "Repository path must identify a directory.",
            event_type="repository_rejected",
        )
    if not _same_path(canonical_candidate, policy.root):
        raise RepositoryPolicyViolation(
            "Repository path is outside the configured repository boundary.",
            event_type="repository_rejected",
        )

    if tool_name in policy.mutable_tools and not allow_mutation:
        raise RepositoryPolicyViolation(
            f"Mutable tool '{tool_name}' requires explicit --allow-mutation "
            "authorization.",
            event_type="mutation_rejected",
        )

    prepared[policy.argument_name] = str(policy.root)
    return prepared


def prepare_filesystem_invocation(
    policy: FilesystemPolicyConfig,
    *,
    tool_name: str,
    arguments: dict[str, JsonValue],
    annotations: dict[str, JsonValue] | None,
    allow_mutation: bool,
) -> FilesystemInvocation:
    """Validate all declared path values without rewriting the outgoing call."""

    if not isinstance(allow_mutation, bool):
        raise TypeError("'allow_mutation' must be a boolean.")
    if not isinstance(arguments, dict):
        raise TypeError("'arguments' must be an object.")

    prepared = deepcopy(arguments)
    path_values: list[tuple[str, str]] = []
    for argument_name in policy.path_arguments:
        if argument_name not in prepared:
            continue
        raw_value = prepared[argument_name]
        if argument_name == "paths":
            if not isinstance(raw_value, list) or not raw_value:
                raise FilesystemPolicyViolation(
                    "Filesystem argument 'paths' must be a non-empty array.",
                    event_type="filesystem_rejected",
                )
            for item in raw_value:
                if not isinstance(item, str) or not item.strip():
                    raise FilesystemPolicyViolation(
                        "Every filesystem path must be a non-empty string.",
                        event_type="filesystem_rejected",
                        path_count=len(path_values),
                    )
                path_values.append((argument_name, item))
            continue
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise FilesystemPolicyViolation(
                f"Filesystem argument '{argument_name}' must be a non-empty string.",
                event_type="filesystem_rejected",
                path_count=len(path_values),
            )
        path_values.append((argument_name, raw_value))

    creation_arguments = policy.creation_arguments.get(tool_name, frozenset())
    for index, (argument_name, raw_path) in enumerate(path_values, start=1):
        _validate_filesystem_path(
            policy.root,
            raw_path,
            allow_nonexistent=argument_name in creation_arguments,
            path_count=index,
        )

    mutation_required = not _is_read_only_tool(annotations)
    if mutation_required and not allow_mutation:
        raise FilesystemPolicyViolation(
            f"Filesystem tool '{tool_name}' requires explicit --allow-mutation "
            "authorization.",
            event_type="mutation_rejected",
            path_count=len(path_values),
        )

    return FilesystemInvocation(
        arguments=prepared,
        mutation_required=mutation_required,
        path_count=len(path_values),
    )


def _validate_filesystem_path(
    root: Path,
    raw_path: str,
    *,
    allow_nonexistent: bool,
    path_count: int,
) -> None:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise FilesystemPolicyViolation(
            "Filesystem paths must be absolute.",
            event_type="filesystem_rejected",
            path_count=path_count,
        )
    if ".." in candidate.parts:
        raise FilesystemPolicyViolation(
            "Filesystem paths must not contain '..'.",
            event_type="filesystem_rejected",
            path_count=path_count,
        )

    try:
        canonical_candidate = candidate.resolve(strict=not allow_nonexistent)
    except (OSError, RuntimeError) as exc:
        raise FilesystemPolicyViolation(
            "Filesystem path does not exist.",
            event_type="filesystem_rejected",
            path_count=path_count,
        ) from exc
    if not allow_nonexistent and not candidate.exists():
        raise FilesystemPolicyViolation(
            "Filesystem path does not exist.",
            event_type="filesystem_rejected",
            path_count=path_count,
        )
    if not _is_within(canonical_candidate, root):
        raise FilesystemPolicyViolation(
            "Filesystem path is outside the configured root.",
            event_type="filesystem_rejected",
            path_count=path_count,
        )

    if allow_nonexistent and not candidate.exists():
        ancestor = candidate
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        try:
            canonical_ancestor = ancestor.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FilesystemPolicyViolation(
                "Filesystem path has no existing safe ancestor.",
                event_type="filesystem_rejected",
                path_count=path_count,
            ) from exc
        if not _is_within(canonical_ancestor, root):
            raise FilesystemPolicyViolation(
                "Filesystem path is outside the configured root.",
                event_type="filesystem_rejected",
                path_count=path_count,
            )


def _is_read_only_tool(annotations: dict[str, JsonValue] | None) -> bool:
    return (
        isinstance(annotations, dict)
        and annotations.get("readOnlyHint") is True
        and annotations.get("destructiveHint") is not True
    )


def _is_within(candidate: Path, root: Path) -> bool:
    candidate_text = os.path.normcase(str(candidate))
    root_text = os.path.normcase(str(root))
    try:
        return os.path.commonpath((candidate_text, root_text)) == root_text
    except ValueError:
        return False


def _same_path(first: Path, second: Path) -> bool:
    """Compare canonical paths with Windows case handling and filesystem identity."""

    if os.path.normcase(str(first)) == os.path.normcase(str(second)):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
