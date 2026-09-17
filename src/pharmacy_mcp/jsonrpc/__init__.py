"""Capa manual de mensajes JSON-RPC 2.0.

Reexporta tipos, errores y funciones de conversión usados por clientes y servidores.
La capa conserva IDs, distingue notificaciones de solicitudes con ``null`` y limita
sus valores a datos JSON válidos. Importarla no realiza serialización ni E/S."""

from .errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SERVER_NOT_INITIALIZED,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JsonRpcError,
    MethodNotFoundError,
    ParseError,
    ServerNotInitializedError,
)
from .messages import ErrorObject, ErrorResponse, Request, Response
from .protocol import deserialize_message, serialize_message

__all__ = [
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "SERVER_NOT_INITIALIZED",
    "ErrorObject",
    "ErrorResponse",
    "InternalError",
    "InvalidParamsError",
    "InvalidRequestError",
    "JsonRpcError",
    "MethodNotFoundError",
    "ParseError",
    "Request",
    "Response",
    "ServerNotInitializedError",
    "deserialize_message",
    "serialize_message",
]
