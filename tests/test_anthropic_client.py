"""Network-free tests for the manual Anthropic Messages API client."""

from __future__ import annotations

import io
import json
import socket
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIRECTORY / "src"))

from pharmacy_mcp.host import (  # noqa: E402
    ANTHROPIC_API_VERSION,
    AnthropicAPIError,
    AnthropicConfigurationError,
    AnthropicMessagesClient,
    AnthropicSettings,
    HTTPResponse,
    UrllibHTTPTransport,
)


class AnthropicSettingsTests(unittest.TestCase):
    def test_required_values_and_repr_do_not_expose_credentials(self) -> None:
        with self.assertRaisesRegex(AnthropicConfigurationError, "API_KEY"):
            AnthropicSettings.from_environ({"ANTHROPIC_MODEL": "test-model"})
        with self.assertRaisesRegex(AnthropicConfigurationError, "MODEL"):
            AnthropicSettings.from_environ({"ANTHROPIC_API_KEY": "present"})

        credential = f"unit-{uuid4().hex}"
        settings = AnthropicSettings(api_key=credential, model="test-model")
        self.assertNotIn(credential, repr(settings))

    def test_environment_values_and_ranges_are_validated(self) -> None:
        base = {
            "ANTHROPIC_API_KEY": "present",
            "ANTHROPIC_MODEL": "test-model",
            "ANTHROPIC_MAX_TOKENS": "2048",
            "ANTHROPIC_HTTP_TIMEOUT_SECONDS": "12.5",
            "MCP_MAX_TOOL_ROUNDS": "4",
        }
        settings = AnthropicSettings.from_environ(base)
        self.assertEqual(settings.max_tokens, 2048)
        self.assertEqual(settings.timeout_seconds, 12.5)
        self.assertEqual(settings.max_tool_rounds, 4)

        for key, value in (
            ("ANTHROPIC_MAX_TOKENS", "0"),
            ("ANTHROPIC_MAX_TOKENS", "NaN"),
            ("ANTHROPIC_HTTP_TIMEOUT_SECONDS", "0"),
            ("ANTHROPIC_HTTP_TIMEOUT_SECONDS", "inf"),
            ("MCP_MAX_TOOL_ROUNDS", "33"),
        ):
            with self.subTest(key=key, value=value):
                invalid = dict(base)
                invalid[key] = value
                with self.assertRaises(AnthropicConfigurationError):
                    AnthropicSettings.from_environ(invalid)

    def test_endpoint_requires_https_except_explicit_local_simulators(self) -> None:
        for url in (
            "http://example.com",
            "ftp://localhost",
            "https://user:password@example.com",
            "https://example.com?token=value",
        ):
            with self.subTest(url=url):
                with self.assertRaises(AnthropicConfigurationError):
                    AnthropicSettings(
                        api_key="present",
                        model="test-model",
                        base_url=url,
                    )

        local = AnthropicSettings(
            api_key="present",
            model="test-model",
            base_url="http://127.0.0.1:8765/mock",
        )
        self.assertEqual(
            local.endpoint,
            "http://127.0.0.1:8765/mock/v1/messages",
        )
        official = AnthropicSettings(api_key="present", model="test-model")
        self.assertEqual(
            official.endpoint,
            "https://api.anthropic.com/v1/messages",
        )


class AnthropicMessagesClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credential = f"unit-{uuid4().hex}"
        self.settings = AnthropicSettings(
            api_key=self.credential,
            model="test-model",
            max_tokens=321,
            timeout_seconds=7.5,
        )

    def test_builds_expected_post_headers_and_json_without_mutating_inputs(self) -> None:
        transport = _RecordingTransport(_valid_response("Hola"))
        client = AnthropicMessagesClient(self.settings, transport=transport)
        messages = [{"role": "user", "content": "Hola"}]
        tools = [
            {
                "name": "pharmacy__check_stock",
                "description": "Read stock.",
                "input_schema": {"type": "object"},
            }
        ]
        original = json.loads(json.dumps([messages, tools]))

        response = client.create_message(
            messages=messages,
            tools=tools,
            system="System v1",
        )

        request = transport.requests[0]
        self.assertEqual(request.url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.timeout_seconds, 7.5)
        self.assertEqual(request.headers["anthropic-version"], ANTHROPIC_API_VERSION)
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(len(request.headers["x-api-key"]), len(self.credential))
        self.assertNotIn("beta", {key.casefold() for key in request.headers})
        payload = json.loads(request.body)
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["max_tokens"], 321)
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["tools"], tools)
        self.assertEqual(payload["system"], "System v1")
        self.assertEqual([messages, tools], original)
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(response.content[0]["text"], "Hola")

    def test_invalid_json_and_missing_required_fields_are_safe_errors(self) -> None:
        for body in (
            b"not-json",
            json.dumps({"type": "message", "role": "assistant"}).encode(),
            json.dumps(
                {
                    "id": "msg",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "x"}],
                    "stop_reason": "tool_use",
                }
            ).encode(),
        ):
            with self.subTest(body=body[:10]):
                client = AnthropicMessagesClient(
                    self.settings,
                    transport=_RecordingTransport(
                        HTTPResponse(status=200, headers={}, body=body)
                    ),
                )
                with self.assertRaises(AnthropicAPIError) as context:
                    client.create_message(messages=[])
                self.assertNotIn(self.credential, str(context.exception))

    def test_http_statuses_are_classified_and_request_id_is_preserved(self) -> None:
        for status, expected in (
            (401, "authentication"),
            (403, "denied"),
            (429, "limit"),
            (500, "unavailable"),
            (529, "unavailable"),
        ):
            with self.subTest(status=status):
                client = AnthropicMessagesClient(
                    self.settings,
                    transport=_RecordingTransport(
                        HTTPResponse(
                            status=status,
                            headers={"request-id": "req-test"},
                            body=b'{"error":{"message":"private"}}',
                        )
                    ),
                )
                with self.assertRaisesRegex(AnthropicAPIError, expected) as context:
                    client.create_message(messages=[])
                self.assertEqual(context.exception.status, status)
                self.assertEqual(context.exception.request_id, "req-test")
                self.assertNotIn("private", str(context.exception))
                self.assertNotIn(self.credential, str(context.exception))

        body_id_client = AnthropicMessagesClient(
            self.settings,
            transport=_RecordingTransport(
                HTTPResponse(
                    status=400,
                    headers={},
                    body=b'{"request_id":"req-from-body"}',
                )
            ),
        )
        with self.assertRaises(AnthropicAPIError) as body_context:
            body_id_client.create_message(messages=[])
        self.assertEqual(body_context.exception.request_id, "req-from-body")

        unsafe_id_client = AnthropicMessagesClient(
            self.settings,
            transport=_RecordingTransport(
                HTTPResponse(
                    status=500,
                    headers={"request-id": "unsafe\nheader"},
                    body=b"x",
                    body_truncated=True,
                )
            ),
        )
        with self.assertRaises(AnthropicAPIError) as unsafe_context:
            unsafe_id_client.create_message(messages=[])
        self.assertIsNone(unsafe_context.exception.request_id)

    def test_timeout_connection_failure_and_oversized_body_are_bounded(self) -> None:
        for failure, expected in (
            (TimeoutError(), "timed out"),
            (socket.timeout(), "timed out"),
            (OSError(), "connect"),
        ):
            with self.subTest(failure=type(failure).__name__):
                client = AnthropicMessagesClient(
                    self.settings,
                    transport=_RaisingTransport(failure),
                )
                with self.assertRaisesRegex(AnthropicAPIError, expected):
                    client.create_message(messages=[])

        client = AnthropicMessagesClient(
            self.settings,
            transport=_RecordingTransport(
                HTTPResponse(
                    status=200,
                    headers={},
                    body=b"{}",
                    body_truncated=True,
                )
            ),
        )
        with self.assertRaisesRegex(AnthropicAPIError, "size limit"):
            client.create_message(messages=[])

    def test_default_transport_closes_success_response(self) -> None:
        response = _ClosableResponse(_valid_response("ok").body)
        with patch("urllib.request.urlopen", return_value=response):
            result = UrllibHTTPTransport()(
                _request_for_transport(self.credential)
            )
        self.assertEqual(result.status, 200)
        self.assertTrue(response.closed_by_client)

    def test_default_transport_closes_http_error_response(self) -> None:
        body = io.BytesIO(b'{"error":{"message":"denied"}}')
        error = urllib.error.HTTPError(
            "https://api.anthropic.com/v1/messages",
            403,
            "Forbidden",
            Message(),
            body,
        )
        with patch("urllib.request.urlopen", side_effect=error):
            response = UrllibHTTPTransport()(
                _request_for_transport(self.credential)
            )
        self.assertEqual(response.status, 403)
        self.assertTrue(body.closed)

    def test_injected_transport_must_return_a_valid_response_object(self) -> None:
        client = AnthropicMessagesClient(
            self.settings,
            transport=lambda request: None,
        )
        with self.assertRaisesRegex(AnthropicAPIError, "invalid HTTP response"):
            client.create_message(messages=[])


class _RecordingTransport:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.response


class _RaisingTransport:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def __call__(self, request):
        raise self.failure


class _ClosableResponse:
    status = 200
    headers = MappingProxyType({"request-id": "req"})

    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)
        self.closed_by_client = False

    def read(self, size: int) -> bytes:
        return self._body.read(size)

    def close(self) -> None:
        self.closed_by_client = True


def _valid_response(text: str) -> HTTPResponse:
    return HTTPResponse(
        status=200,
        headers={"request-id": "req-test"},
        body=json.dumps(
            {
                "id": "msg-test",
                "type": "message",
                "role": "assistant",
                "model": "test-model",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode(),
    )


def _request_for_transport(credential: str):
    from pharmacy_mcp.host import HTTPRequest

    return HTTPRequest(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        headers={"x-api-key": credential},
        body=b"{}",
        timeout_seconds=1,
        max_response_bytes=1024,
    )


if __name__ == "__main__":
    unittest.main()
