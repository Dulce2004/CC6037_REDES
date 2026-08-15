"""Adaptador MCP para la herramienta educativa de clasificación de síntomas."""

from __future__ import annotations

from pharmacy_mcp.jsonrpc import InvalidParamsError
from pharmacy_mcp.jsonrpc.messages import JsonValue
from pharmacy_mcp.pharmacy import (
    RECOGNIZED_SYMPTOMS,
    SymptomValidationError,
    classify_symptoms,
)

from .handlers import ToolArguments

CLASSIFY_SYMPTOMS_NAME = "classify_symptoms"
CLASSIFY_SYMPTOMS_DESCRIPTION = (
    "Classifies controlled symptom identifiers into educational categories."
)
CLASSIFY_SYMPTOMS_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "symptoms": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(RECOGNIZED_SYMPTOMS)},
            "minItems": 1,
        }
    },
    "required": ["symptoms"],
    "additionalProperties": False,
}


def classify_symptoms_handler(arguments: ToolArguments) -> JsonValue:
    """Adapta los argumentos MCP al clasificador y crea contenido de texto."""

    if "symptoms" not in arguments:
        raise InvalidParamsError("Missing required tool arguments: symptoms.")

    try:
        result = classify_symptoms(arguments["symptoms"])
    except SymptomValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc

    category = result["category"] or "unclassified"
    matched = result["matchedSymptoms"]
    matched_text = ", ".join(matched) if matched else "none"
    text = (
        f"Classification: {category}. Matched symptoms: {matched_text}. "
        f"{result['message']} Educational use only; not a medical diagnosis."
    )
    return {"content": [{"type": "text", "text": text}]}
