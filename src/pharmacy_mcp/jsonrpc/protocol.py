"""Conversión estricta entre objetos Python y texto JSON-RPC 2.0.

La deserialización rechaza constantes no JSON, formas ambiguas y miembros inválidos
antes de construir modelos tipados; la serialización acepta únicamente mensajes ya
validados. Los IDs se preservan para correlación y una notificación continúa siendo
una solicitud sin respuesta. El módulo no realiza E/S."""

from __future__ import annotations

import json
from typing import TypeAlias

from .errors import InvalidRequestError, ParseError
from .messages import ErrorObject, ErrorResponse, Request, Response

JsonRpcMessage: TypeAlias = Request | Response | ErrorResponse


def serialize_message(message: JsonRpcMessage) -> str:
    """Serializa un mensaje JSON-RPC validado a texto JSON."""

    if not isinstance(message, (Request, Response, ErrorResponse)):
        raise InvalidRequestError("Unsupported JSON-RPC message type.")

    try:
        return json.dumps(
            message.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"Message cannot be serialized: {exc}") from exc


def deserialize_message(payload: str) -> JsonRpcMessage:
    """Interpreta texto JSON y devuelve el tipo de mensaje correspondiente."""

    if not isinstance(payload, str):
        raise ParseError("JSON input must be a string.")

    try:
        raw_message = json.loads(payload, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc
    except ValueError as exc:
        raise ParseError(str(exc)) from exc

    if not isinstance(raw_message, dict):
        raise InvalidRequestError("A JSON-RPC message must be an object.")

    return _message_from_dict(raw_message)


def _reject_json_constant(value: str) -> None:
    """Reject Python's permissive NaN and Infinity spellings during JSON decoding."""
    raise ValueError(f"Invalid JSON numeric constant: {value}.")


def _message_from_dict(raw_message: dict[str, object]) -> JsonRpcMessage:
    """Classify an object as request, success response or error response."""
    if "jsonrpc" not in raw_message:
        raise InvalidRequestError("Missing required field 'jsonrpc'.")
    if raw_message["jsonrpc"] != "2.0":
        raise InvalidRequestError("'jsonrpc' must be exactly '2.0'.")

    if "method" in raw_message:
        if "result" in raw_message or "error" in raw_message:
            raise InvalidRequestError(
                "A request cannot contain 'result' or 'error'."
            )
        return _request_from_dict(raw_message)

    has_result = "result" in raw_message
    has_error = "error" in raw_message
    if has_result and has_error:
        raise InvalidRequestError(
            "A response must contain either 'result' or 'error', not both."
        )
    if not has_result and not has_error:
        raise InvalidRequestError(
            "A request requires 'method'; a response requires 'result' or 'error'."
        )
    if "id" not in raw_message:
        raise InvalidRequestError("A response requires 'id'.")

    if has_result:
        return Response(
            jsonrpc=raw_message["jsonrpc"],
            result=raw_message["result"],
            id=raw_message["id"],
        )
    return ErrorResponse(
        jsonrpc=raw_message["jsonrpc"],
        error=_error_object_from_value(raw_message["error"]),
        id=raw_message["id"],
    )


def _request_from_dict(raw_message: dict[str, object]) -> Request:
    """Preserve absence of optional ``params`` and ``id`` while constructing a request."""
    arguments: dict[str, object] = {
        "jsonrpc": raw_message["jsonrpc"],
        "method": raw_message["method"],
    }
    if "params" in raw_message:
        arguments["params"] = raw_message["params"]
    if "id" in raw_message:
        arguments["id"] = raw_message["id"]
    return Request(**arguments)


def _error_object_from_value(value: object) -> ErrorObject:
    """Validate required error members and preserve optional structured data."""
    if not isinstance(value, dict):
        raise InvalidRequestError("'error' must be an object.")
    if "code" not in value or "message" not in value:
        raise InvalidRequestError("'error' requires 'code' and 'message'.")

    arguments: dict[str, object] = {
        "code": value["code"],
        "message": value["message"],
    }
    if "data" in value:
        arguments["data"] = value["data"]
    return ErrorObject(**arguments)
