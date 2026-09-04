"""General host-side policies applied before an MCP tool is invoked."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path, PurePath

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .config import RepositoryPolicyConfig


class RepositoryPolicyViolation(ValueError):
    """A tool invocation violated the configured repository boundary."""

    def __init__(self, message: str, *, event_type: str) -> None:
        super().__init__(message)
        self.event_type = event_type


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


def _same_path(first: Path, second: Path) -> bool:
    """Compare canonical paths with Windows case handling and filesystem identity."""

    if os.path.normcase(str(first)) == os.path.normcase(str(second)):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False
