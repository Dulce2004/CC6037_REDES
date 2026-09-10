"""Dynamic MCP tool conversion and bounded provider result rendering."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Iterable

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .manager import RegisteredTool
from .protocol_log import BINARY_OMISSION_MARKER, redact_sensitive_data

DEFAULT_MAX_TOOL_RESULT_CHARS = 12_000
TOOL_RESULT_TRUNCATION_MARKER = "[TOOL RESULT TRUNCATED]"
_ANTHROPIC_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTENT_KEYS = {"content", "oldtext", "newtext", "blob", "data"}


class ToolConversionError(ValueError):
    """A registered MCP tool cannot be represented safely for Anthropic."""


def tools_for_anthropic(
    tools: Iterable[RegisteredTool],
) -> list[dict[str, JsonValue]]:
    """Return only the fields accepted by Anthropic, in registry order."""

    converted: list[dict[str, JsonValue]] = []
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, RegisteredTool):
            raise ToolConversionError("The MCP registry contains an invalid tool.")
        name = tool.namespaced_name
        if not _ANTHROPIC_TOOL_NAME.fullmatch(name):
            raise ToolConversionError(
                f"Tool name '{name}' is incompatible with Anthropic."
            )
        if name in names:
            raise ToolConversionError(f"Duplicate tool name '{name}'.")
        if not isinstance(tool.description, str) or not tool.description.strip():
            raise ToolConversionError(f"Tool '{name}' has no description.")
        if not isinstance(tool.input_schema, dict):
            raise ToolConversionError(f"Tool '{name}' has no valid input schema.")
        if tool.input_schema.get("type") != "object":
            raise ToolConversionError(
                f"Tool '{name}' input schema must describe an object."
            )
        converted.append(
            {
                "name": name,
                "description": tool.description,
                "input_schema": deepcopy(tool.input_schema),
            }
        )
        names.add(name)
    return converted


def mcp_result_for_llm(
    result: JsonValue,
    *,
    max_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS,
) -> tuple[str, bool]:
    """Create a separate, bounded LLM representation of one MCP result."""

    if (
        isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or max_chars < 256
        or max_chars > 1_000_000
    ):
        raise ValueError("'max_chars' must be from 256 through 1000000.")
    if not isinstance(result, dict):
        raise ToolConversionError("The MCP tool returned a non-object result.")
    is_error = result.get("isError") is True
    if "structuredContent" in result:
        rendered = _json_text(_omit_binary(result["structuredContent"]))
    else:
        content = result.get("content")
        if not isinstance(content, list):
            raise ToolConversionError(
                "The MCP tool result has neither structuredContent nor content."
            )
        rendered_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                rendered_parts.append("[UNSUPPORTED MCP CONTENT OMITTED]")
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                rendered_parts.append(block["text"])
            elif block.get("type") in {"image", "audio", "resource"} or any(
                key in block for key in ("blob", "data")
            ):
                rendered_parts.append(BINARY_OMISSION_MARKER)
            else:
                rendered_parts.append("[UNSUPPORTED MCP CONTENT OMITTED]")
        rendered = "\n".join(rendered_parts)
    return _bounded_text(rendered, max_chars), is_error


def mcp_result_for_anthropic(
    result: JsonValue,
    *,
    max_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS,
) -> tuple[str, bool]:
    """Backward-compatible name for the provider-neutral result renderer."""

    return mcp_result_for_llm(result, max_chars=max_chars)


def sanitized_argument_summary(
    arguments: dict[str, JsonValue],
    *,
    max_chars: int = 1_000,
) -> str:
    """Show call shape while omitting secrets and potentially large write bodies."""

    protected = _omit_content(redact_sensitive_data(deepcopy(arguments)))
    return _bounded_text(_json_text(protected), max_chars)


def mutation_effect(server_name: str, tool_name: str) -> str:
    if server_name == "pharmacy" and tool_name == "create_order":
        return "creará una orden y descontará inventario si la validación termina bien"
    if server_name == "git":
        return "puede modificar el índice, commits, ramas o archivos del repositorio"
    if server_name == "filesystem":
        return "puede crear, editar, mover o eliminar datos dentro del directorio permitido"
    return "puede modificar estado persistente del sistema"


def _omit_content(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if key.casefold() in _CONTENT_KEYS and isinstance(item, str):
                result[key] = f"[OMITTED: {len(item)} characters]"
            else:
                result[key] = _omit_content(item)
        return result
    if isinstance(value, list):
        return [_omit_content(item) for item in value]
    return value


def _omit_binary(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: (
                BINARY_OMISSION_MARKER
                if key.casefold() in {"blob", "data"} and isinstance(item, str)
                else _omit_binary(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_omit_binary(item) for item in value]
    return value


def _json_text(value: JsonValue) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ToolConversionError("MCP data is not valid JSON.") from exc


def _bounded_text(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    suffix = f" {TOOL_RESULT_TRUNCATION_MARKER} ({len(value)} characters total)"
    prefix_length = max(0, maximum - len(suffix))
    return f"{value[:prefix_length]}{suffix}"
