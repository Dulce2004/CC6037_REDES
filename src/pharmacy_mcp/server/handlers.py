"""Representación mínima de herramientas registradas en el servidor MCP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, TypeAlias

from pharmacy_mcp.jsonrpc.messages import JsonValue

ToolArguments: TypeAlias = dict[str, JsonValue]
ToolHandler: TypeAlias = Callable[[ToolArguments], JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class Tool:
    """Define los metadatos públicos y el handler Python de una herramienta."""

    name: str
    description: str
    input_schema: dict[str, JsonValue]
    handler: ToolHandler = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Tool name must be a non-empty string.")
        if not isinstance(self.description, str):
            raise ValueError("Tool description must be a string.")
        if not isinstance(self.input_schema, dict):
            raise ValueError("Tool input schema must be an object.")
        required = self.input_schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(item, str) for item in required
        ):
            raise ValueError("Tool schema 'required' must be an array of strings.")
        if not callable(self.handler):
            raise ValueError("Tool handler must be callable.")

    def to_definition(self) -> dict[str, JsonValue]:
        """Devuelve la definición pública esperada por ``tools/list``."""

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
