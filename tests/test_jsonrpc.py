"""Pruebas de la implementación manual de JSON-RPC 2.0."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Permite ejecutar el comando de descubrimiento desde un repositorio con layout src/.
SOURCE_DIRECTORY = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.jsonrpc import (  # noqa: E402
    METHOD_NOT_FOUND,
    ErrorObject,
    ErrorResponse,
    InvalidParamsError,
    InvalidRequestError,
    ParseError,
    Request,
    Response,
    deserialize_message,
    serialize_message,
)


class JsonRpcMessageTests(unittest.TestCase):
    def test_create_valid_request(self) -> None:
        request = Request(method="example.method", params={}, id=1)

        self.assertEqual(
            request.to_dict(),
            {"jsonrpc": "2.0", "method": "example.method", "params": {}, "id": 1},
        )

    def test_serialize_request(self) -> None:
        request = Request(method="example.method", params={"value": 3}, id="req-1")

        serialized = serialize_message(request)

        self.assertEqual(json.loads(serialized), request.to_dict())

    def test_deserialize_request(self) -> None:
        payload = '{"jsonrpc":"2.0","method":"example.method","params":{},"id":1}'

        message = deserialize_message(payload)

        self.assertEqual(message, Request(method="example.method", params={}, id=1))

    def test_create_success_response(self) -> None:
        response = Response(result={"accepted": True}, id=1)

        self.assertEqual(
            response.to_dict(),
            {"jsonrpc": "2.0", "result": {"accepted": True}, "id": 1},
        )

    def test_create_error_response(self) -> None:
        response = ErrorResponse(
            error=ErrorObject(code=METHOD_NOT_FOUND, message="Method not found"),
            id=1,
        )

        self.assertEqual(
            response.to_dict(),
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Method not found"},
                "id": 1,
            },
        )

    def test_reject_request_without_jsonrpc(self) -> None:
        with self.assertRaisesRegex(InvalidRequestError, "jsonrpc"):
            deserialize_message('{"method":"example.method","id":1}')

    def test_reject_request_with_wrong_version(self) -> None:
        with self.assertRaisesRegex(InvalidRequestError, "2.0"):
            deserialize_message(
                '{"jsonrpc":"1.0","method":"example.method","id":1}'
            )

    def test_reject_request_without_method(self) -> None:
        with self.assertRaisesRegex(InvalidRequestError, "method"):
            deserialize_message('{"jsonrpc":"2.0","params":{},"id":1}')

    def test_reject_invalid_json(self) -> None:
        with self.assertRaisesRegex(ParseError, "Invalid JSON"):
            deserialize_message('{"jsonrpc":"2.0",')

    def test_reject_response_with_result_and_error(self) -> None:
        payload = (
            '{"jsonrpc":"2.0","result":{},'
            '"error":{"code":-32603,"message":"Internal error"},"id":1}'
        )

        with self.assertRaisesRegex(InvalidRequestError, "not both"):
            deserialize_message(payload)

    def test_reject_non_string_method(self) -> None:
        with self.assertRaisesRegex(InvalidRequestError, "method"):
            deserialize_message('{"jsonrpc":"2.0","method":7,"id":1}')

    def test_reject_scalar_params(self) -> None:
        with self.assertRaisesRegex(InvalidParamsError, "params"):
            Request(method="example.method", params="invalid", id=1)

    def test_reject_boolean_id(self) -> None:
        with self.assertRaisesRegex(InvalidRequestError, "id"):
            Request(method="example.method", id=True)

    def test_reject_error_without_code(self) -> None:
        payload = (
            '{"jsonrpc":"2.0","error":{"message":"Internal error"},"id":1}'
        )

        with self.assertRaisesRegex(InvalidRequestError, "code"):
            deserialize_message(payload)

    def test_round_trip_preserves_request(self) -> None:
        original = Request(
            method="example.method",
            params={"items": [1, "two", None]},
            id="request-1",
        )

        restored = deserialize_message(serialize_message(original))

        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
