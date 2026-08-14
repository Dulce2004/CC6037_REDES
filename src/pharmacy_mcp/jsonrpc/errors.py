"""Excepciones y códigos de error estándar de JSON-RPC 2.0."""

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(ValueError):
    """Error base producido al validar o interpretar un mensaje JSON-RPC."""

    code = INVALID_REQUEST
    default_message = "Invalid Request"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class ParseError(JsonRpcError):
    """Indica que el texto recibido no contiene JSON válido."""

    code = PARSE_ERROR
    default_message = "Parse error"


class InvalidRequestError(JsonRpcError):
    """Indica que un objeto no cumple la estructura de JSON-RPC 2.0."""

    code = INVALID_REQUEST
    default_message = "Invalid Request"


class MethodNotFoundError(JsonRpcError):
    """Error preparado para métodos que un servidor futuro no reconozca."""

    code = METHOD_NOT_FOUND
    default_message = "Method not found"


class InvalidParamsError(JsonRpcError):
    """Indica que los parámetros de una solicitud no son válidos."""

    code = INVALID_PARAMS
    default_message = "Invalid params"


class InternalError(JsonRpcError):
    """Error preparado para fallos internos de un servidor futuro."""

    code = INTERNAL_ERROR
    default_message = "Internal error"
