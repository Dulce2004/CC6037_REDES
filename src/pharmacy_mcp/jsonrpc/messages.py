"""Estructuras de datos y validaciones básicas para mensajes JSON-RPC 2.0."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeAlias

from .errors import InvalidParamsError, InvalidRequestError

JSONRPC_VERSION = "2.0"

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonRpcId: TypeAlias = None | int | float | str
JsonRpcParams: TypeAlias = list[JsonValue] | dict[str, JsonValue]


class _NotProvided:
    """Distingue un miembro ausente de un miembro cuyo valor JSON es null."""

    __slots__ = ()


_NOT_PROVIDED = _NotProvided()


def _validate_version(version: object) -> None:
    if version != JSONRPC_VERSION:
        raise InvalidRequestError("'jsonrpc' must be exactly '2.0'.")


def _validate_id(message_id: object) -> None:
    if isinstance(message_id, bool) or not isinstance(
        message_id, (str, int, float, type(None))
    ):
        raise InvalidRequestError("'id' must be a string, number, or null.")
    if isinstance(message_id, float) and not math.isfinite(message_id):
        raise InvalidRequestError("'id' must be a finite number.")


def _validate_json_value(value: object, field_name: str) -> None:
    """Comprueba recursivamente que un valor pueda representarse como JSON."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise InvalidRequestError(f"'{field_name}' cannot contain non-finite numbers.")
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, field_name)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidRequestError(
                    f"'{field_name}' must use strings as object keys."
                )
            _validate_json_value(item, field_name)
        return
    raise InvalidRequestError(f"'{field_name}' contains a value unsupported by JSON.")


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    """Solicitud JSON-RPC; la ausencia de ``id`` representa una notificación."""

    method: str
    params: JsonRpcParams | _NotProvided = _NOT_PROVIDED
    id: JsonRpcId | _NotProvided = _NOT_PROVIDED
    jsonrpc: str = JSONRPC_VERSION

    def __post_init__(self) -> None:
        _validate_version(self.jsonrpc)
        if not isinstance(self.method, str):
            raise InvalidRequestError("'method' must be a string.")
        if self.params is not _NOT_PROVIDED:
            if not isinstance(self.params, (list, dict)):
                raise InvalidParamsError("'params' must be an object or an array.")
            try:
                _validate_json_value(self.params, "params")
            except InvalidRequestError as exc:
                raise InvalidParamsError(str(exc)) from exc
        if self.id is not _NOT_PROVIDED:
            _validate_id(self.id)

    def to_dict(self) -> dict[str, JsonValue]:
        """Convierte la solicitud a un diccionario listo para serializar."""

        message: dict[str, JsonValue] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
        }
        if self.params is not _NOT_PROVIDED:
            message["params"] = self.params
        if self.id is not _NOT_PROVIDED:
            message["id"] = self.id
        return message

    @property
    def is_notification(self) -> bool:
        """Indica si el miembro ``id`` está ausente, incluso frente a ``null``."""

        return self.id is _NOT_PROVIDED


@dataclass(frozen=True, slots=True, kw_only=True)
class Response:
    """Respuesta JSON-RPC exitosa."""

    result: JsonValue
    id: JsonRpcId
    jsonrpc: str = JSONRPC_VERSION

    def __post_init__(self) -> None:
        _validate_version(self.jsonrpc)
        _validate_id(self.id)
        _validate_json_value(self.result, "result")

    def to_dict(self) -> dict[str, JsonValue]:
        """Convierte la respuesta exitosa a un diccionario serializable."""

        return {"jsonrpc": self.jsonrpc, "result": self.result, "id": self.id}


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorObject:
    """Contenido del miembro ``error`` de una respuesta JSON-RPC."""

    code: int
    message: str
    data: JsonValue | _NotProvided = _NOT_PROVIDED

    def __post_init__(self) -> None:
        if isinstance(self.code, bool) or not isinstance(self.code, int):
            raise InvalidRequestError("Error 'code' must be an integer.")
        if not isinstance(self.message, str):
            raise InvalidRequestError("Error 'message' must be a string.")
        if self.data is not _NOT_PROVIDED:
            _validate_json_value(self.data, "error.data")

    def to_dict(self) -> dict[str, JsonValue]:
        """Convierte el error a un diccionario serializable."""

        error: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
        }
        if self.data is not _NOT_PROVIDED:
            error["data"] = self.data
        return error


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorResponse:
    """Respuesta JSON-RPC que contiene un ``ErrorObject``."""

    error: ErrorObject
    id: JsonRpcId
    jsonrpc: str = JSONRPC_VERSION

    def __post_init__(self) -> None:
        _validate_version(self.jsonrpc)
        _validate_id(self.id)
        if not isinstance(self.error, ErrorObject):
            raise InvalidRequestError("'error' must be an ErrorObject.")

    def to_dict(self) -> dict[str, JsonValue]:
        """Convierte la respuesta de error a un diccionario serializable."""

        return {
            "jsonrpc": self.jsonrpc,
            "error": self.error.to_dict(),
            "id": self.id,
        }
