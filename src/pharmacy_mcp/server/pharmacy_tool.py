"""Adaptador MCP para la evaluación educativa de síntomas."""

from __future__ import annotations

from pharmacy_mcp.jsonrpc import InvalidParamsError
from pharmacy_mcp.jsonrpc.messages import JsonValue
from pharmacy_mcp.pharmacy import (
    SymptomAssessmentValidationError,
    assess_symptoms,
)

from .handlers import ToolArguments

ASSESS_SYMPTOMS_NAME = "assess_symptoms"
ASSESS_SYMPTOMS_DESCRIPTION = (
    "Assesses natural-language symptoms using controlled simulated rules, "
    "including severity and urgent red flags."
)
ASSESS_SYMPTOMS_INPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "symptoms": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1000,
        },
        "age": {
            "type": "integer",
            "minimum": 0,
            "maximum": 120,
        },
        "duration_days": {
            "type": "integer",
            "minimum": 0,
            "maximum": 365,
        },
    },
    "required": ["symptoms"],
    "additionalProperties": False,
}


def assess_symptoms_handler(arguments: ToolArguments) -> JsonValue:
    """Valida el contrato MCP y presenta la evaluación simulada."""

    unexpected = sorted(set(arguments) - {"symptoms", "age", "duration_days"})
    if unexpected:
        raise InvalidParamsError(
            "Unexpected tool arguments: " + ", ".join(unexpected) + "."
        )
    for optional_name in ("age", "duration_days"):
        if optional_name in arguments and arguments[optional_name] is None:
            raise InvalidParamsError(f"'{optional_name}' must be an integer.")

    try:
        result = assess_symptoms(
            arguments.get("symptoms"),
            age=arguments.get("age"),
            duration_days=arguments.get("duration_days"),
        )
    except SymptomAssessmentValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc

    category = result["category"] or "unclassified"
    recognized = result["recognized_symptoms"]
    recognized_text = ", ".join(recognized) if recognized else "none"
    red_flags = result["red_flags"]
    red_flag_text = ", ".join(red_flags) if red_flags else "none"
    details = (
        f"Severity: {result['severity']}. Category: {category}. "
        f"Recognized symptoms: {recognized_text}. Red flags: {red_flag_text}."
    )
    if result["severity"] == "urgent":
        text = (
            f"URGENT: {result['recommended_action']} {details} "
            "No medication purchase is recommended by this result. "
            f"{result['disclaimer']}"
        )
    else:
        text = (
            f"{details} {result['recommended_action']} No medication purchase "
            f"is recommended by this result. {result['disclaimer']}"
        )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
    }
