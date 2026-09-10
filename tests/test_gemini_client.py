"""Tests for the manual Gemini generateContent REST client."""

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
    DEFAULT_GEMINI_MODEL,
    DEFAULT_LLM_PROVIDER,
    GeminiAPIError,
    GeminiConfigurationError,
    GeminiGenerateContentClient,
    GeminiSettings,
    GeminiUrllibHTTPTransport,
    HTTPRequest,
    HTTPResponse,
    LLMConfigurationError,
    provider_from_environ,
)


class ProviderSelectionTests(unittest.TestCase):
    def test_provider_defaults_to_gemini_and_accepts_both_values(self) -> None:
        self.assertEqual(DEFAULT_LLM_PROVIDER, "gemini")
        self.assertEqual(provider_from_environ({}), "gemini")
        self.assertEqual(provider_from_environ({"LLM_PROVIDER": " gemini "}), "gemini")
        self.assertEqual(provider_from_environ({"LLM_PROVIDER": "ANTHROPIC"}), "anthropic")

    def test_invalid_provider_is_rejected_without_credentials(self) -> None:
        with self.assertRaisesRegex(LLMConfigurationError, "gemini.*anthropic"):
            provider_from_environ({"LLM_PROVIDER": "openai"})


class GeminiSettingsTests(unittest.TestCase):
    def test_key_is_required_hidden_and_default_model_is_normalized(self) -> None:
        with self.assertRaisesRegex(GeminiConfigurationError, "GEMINI_API_KEY"):
            GeminiSettings.from_environ({})
        credential = f"unit-{uuid4().hex}"
        settings = GeminiSettings(api_key=credential)
        self.assertEqual(settings.model, DEFAULT_GEMINI_MODEL)
        self.assertNotIn(credential, repr(settings))
        self.assertNotIn(credential, settings.endpoint)

    def test_model_override_and_models_prefix_are_normalized(self) -> None:
        direct = GeminiSettings(api_key="present", model="gemini-3.5-flash-lite")
        prefixed = GeminiSettings(
            api_key="present", model="models/gemini-3.5-flash-lite"
        )
        override = GeminiSettings.from_environ(
            {"GEMINI_API_KEY": "present", "GEMINI_MODEL": "gemini-test_1.0"}
        )
        self.assertEqual(direct.model, prefixed.model)
        self.assertEqual(override.model, "gemini-test_1.0")

    def test_invalid_model_names_are_rejected(self) -> None:
        for model in (
            "",
            "models/",
            "models/models/gemini",
            "family/gemini",
            "../gemini",
            "gemini/../other",
            "gemini?key=value",
            "gemini#fragment",
            "gemini%2Fother",
        ):
            with self.subTest(model=model):
                with self.assertRaises(GeminiConfigurationError):
                    GeminiSettings(api_key="present", model=model)

    def test_environment_values_and_numeric_ranges_are_validated(self) -> None:
        settings = GeminiSettings.from_environ(
            {
                "GEMINI_API_KEY": "present",
                "GEMINI_MAX_OUTPUT_TOKENS": "2048",
                "GEMINI_HTTP_TIMEOUT_SECONDS": "4.5",
                "GEMINI_MAX_RETRIES": "2",
                "MCP_MAX_TOOL_ROUNDS": "6",
            }
        )
        self.assertEqual(settings.max_output_tokens, 2048)
        self.assertEqual(settings.timeout_seconds, 4.5)
        self.assertEqual(settings.max_retries, 2)
        self.assertEqual(settings.max_tool_rounds, 6)
        for kwargs in (
            {"max_output_tokens": True},
            {"max_output_tokens": 0},
            {"timeout_seconds": True},
            {"timeout_seconds": float("inf")},
            {"max_retries": True},
            {"max_retries": 4},
            {"max_tool_rounds": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(GeminiConfigurationError):
                    GeminiSettings(api_key="present", **kwargs)

    def test_base_url_requires_https_except_explicit_localhost(self) -> None:
        official = GeminiSettings(api_key="present")
        self.assertEqual(
            official.endpoint,
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.5-flash-lite:generateContent",
        )
        local = GeminiSettings(
            api_key="present", base_url="http://localhost:8080/v1beta/"
        )
        self.assertEqual(
            local.endpoint,
            "http://localhost:8080/v1beta/models/"
            "gemini-3.5-flash-lite:generateContent",
        )
        for url in (
            "http://example.com",
            "https://user:pass@example.com",
            "https://example.com?key=value",
            "https://example.com/#fragment",
        ):
            with self.subTest(url=url):
                with self.assertRaises(GeminiConfigurationError):
                    GeminiSettings(api_key="present", base_url=url)


class GeminiHTTPClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credential = f"unit-{uuid4().hex}"
        self.settings = GeminiSettings(
            api_key=self.credential,
            max_output_tokens=321,
            timeout_seconds=7.5,
        )

    def test_builds_exact_post_headers_and_basic_json_body(self) -> None:
        transport = _QueueTransport([_valid_response(text="Hola")])
        client = GeminiGenerateContentClient(self.settings, transport=transport)
        messages = [{"role": "user", "content": "Hola"}]
        tools = [
            {
                "functionDeclarations": [
                    {
                        "name": "pharmacy__check_stock",
                        "description": "Read stock.",
                        "parametersJsonSchema": {"type": "object"},
                    }
                ]
            }
        ]
        original = json.loads(json.dumps([messages, tools]))

        result = client.create_message(
            messages=messages,
            tools=tools,
            system="System v1",
        )

        request = transport.requests[0]
        payload = json.loads(request.body)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, self.settings.endpoint)
        self.assertEqual(request.timeout_seconds, 7.5)
        self.assertEqual(request.headers["x-goog-api-key"], self.credential)
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(request.headers["accept"], "application/json")
        self.assertNotIn(self.credential, request.url)
        self.assertNotIn(self.credential, request.body.decode())
        self.assertEqual(payload["contents"], [{"role": "user", "parts": [{"text": "Hola"}]}])
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "System v1"}]})
        self.assertEqual(payload["generationConfig"], {"maxOutputTokens": 321})
        self.assertEqual(payload["tools"], tools)
        self.assertEqual([messages, tools], original)
        self.assertEqual(result.content[0]["text"], "Hola")

    def test_tools_and_system_are_omitted_when_unavailable(self) -> None:
        transport = _QueueTransport([_valid_response(text="ok")])
        GeminiGenerateContentClient(self.settings, transport=transport).create_message(
            messages=[]
        )
        payload = json.loads(transport.requests[0].body)
        self.assertNotIn("tools", payload)
        self.assertNotIn("systemInstruction", payload)

    def test_http_statuses_are_safe_and_request_id_is_sanitized(self) -> None:
        for status, expected in (
            (400, "rejected"),
            (401, "authentication"),
            (403, "denied"),
            (404, "model or endpoint"),
            (408, "timed out"),
            (429, "quota or rate"),
            (500, "unavailable"),
            (502, "unavailable"),
            (503, "unavailable"),
            (504, "unavailable"),
        ):
            with self.subTest(status=status):
                client = GeminiGenerateContentClient(
                    self.settings,
                    transport=_QueueTransport(
                        [
                            HTTPResponse(
                                status=status,
                                headers={"x-goog-request-id": "req-test"},
                                body=b'{"error":{"message":"private"}}',
                            )
                        ]
                    ),
                )
                with self.assertRaisesRegex(GeminiAPIError, expected) as context:
                    client.create_message(messages=[])
                self.assertEqual(context.exception.status, status)
                self.assertEqual(context.exception.request_id, "req-test")
                self.assertNotIn("private", str(context.exception))
                self.assertNotIn(self.credential, str(context.exception))

        unsafe = GeminiGenerateContentClient(
            self.settings,
            transport=_QueueTransport(
                [HTTPResponse(status=500, headers={"x-request-id": "bad\nvalue"}, body=b"x")]
            ),
        )
        with self.assertRaises(GeminiAPIError) as context:
            unsafe.create_message(messages=[])
        self.assertIsNone(context.exception.request_id)

    def test_timeout_connection_invalid_transport_and_oversize_are_safe(self) -> None:
        for failure, expected in (
            (TimeoutError(), "timed out"),
            (socket.timeout(), "timed out"),
            (OSError(), "connect"),
        ):
            with self.subTest(failure=type(failure).__name__):
                client = GeminiGenerateContentClient(
                    self.settings, transport=_RaisingTransport(failure)
                )
                with self.assertRaisesRegex(GeminiAPIError, expected):
                    client.create_message(messages=[])
        invalid = GeminiGenerateContentClient(
            self.settings, transport=lambda request: None
        )
        with self.assertRaisesRegex(GeminiAPIError, "invalid HTTP response"):
            invalid.create_message(messages=[])
        oversized = GeminiGenerateContentClient(
            self.settings,
            transport=_QueueTransport(
                [HTTPResponse(status=200, headers={}, body=b"{}", body_truncated=True)]
            ),
        )
        with self.assertRaisesRegex(GeminiAPIError, "size limit"):
            oversized.create_message(messages=[])
        dishonest_transport = GeminiGenerateContentClient(
            GeminiSettings(api_key=self.credential, max_response_bytes=1024),
            transport=_QueueTransport(
                [HTTPResponse(status=200, headers={}, body=b"x" * 1025)]
            ),
        )
        with self.assertRaisesRegex(GeminiAPIError, "size limit"):
            dishonest_transport.create_message(messages=[])

    def test_invalid_utf8_json_and_response_shape_are_rejected(self) -> None:
        for body in (b"\xff", b"not-json", b"{}", b'{"candidates":[]}'):
            with self.subTest(body=body):
                client = GeminiGenerateContentClient(
                    self.settings,
                    transport=_QueueTransport(
                        [HTTPResponse(status=200, headers={}, body=body)]
                    ),
                )
                with self.assertRaises(GeminiAPIError) as context:
                    client.create_message(messages=[])
                self.assertNotIn(self.credential, str(context.exception))

    def test_default_transport_closes_success_and_http_error_responses(self) -> None:
        success = _ClosableResponse(_valid_response(text="ok").body)
        with patch("urllib.request.urlopen", return_value=success):
            result = GeminiUrllibHTTPTransport()(_request(self.credential))
        self.assertEqual(result.status, 200)
        self.assertTrue(success.closed_by_client)

        body = io.BytesIO(b'{"error":{"message":"denied"}}')
        error = urllib.error.HTTPError(
            self.settings.endpoint,
            403,
            "Forbidden",
            Message(),
            body,
        )
        with patch("urllib.request.urlopen", side_effect=error):
            result = GeminiUrllibHTTPTransport()(_request(self.credential))
        self.assertEqual(result.status, 403)
        self.assertTrue(body.closed)

    def test_optional_retry_uses_retry_after_and_logs_metadata_only(self) -> None:
        transport = _QueueTransport(
            [
                HTTPResponse(status=429, headers={"Retry-After": "0"}, body=b"{}"),
                _valid_response(text="ok"),
            ]
        )
        sleeps = []
        events = []
        settings = GeminiSettings(api_key=self.credential, max_retries=1)
        client = GeminiGenerateContentClient(
            settings,
            transport=transport,
            sleep=sleeps.append,
            event_sink=lambda event, payload: events.append((event, payload)),
        )
        client.create_message(messages=[])
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(sleeps, [0.0])
        self.assertTrue(any(event == "gemini_retry_scheduled" for event, _ in events))
        serialized = json.dumps(events)
        self.assertNotIn(self.credential, serialized)
        self.assertNotIn("x-goog-api-key", serialized)


class _QueueTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected simulated HTTP request")
        return self.responses.pop(0)


class _RaisingTransport:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def __call__(self, request):
        raise self.failure


class _ClosableResponse:
    status = 200
    headers = MappingProxyType({"x-goog-request-id": "req"})

    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)
        self.closed_by_client = False

    def read(self, amount: int) -> bytes:
        return self._body.read(amount)

    def close(self) -> None:
        self.closed_by_client = True
        self._body.close()


def _request(credential: str) -> HTTPRequest:
    return HTTPRequest(
        method="POST",
        url="https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",
        headers={"x-goog-api-key": credential},
        body=b"{}",
        timeout_seconds=1.0,
        max_response_bytes=1024,
    )


def _valid_response(*, text: str) -> HTTPResponse:
    return HTTPResponse(
        status=200,
        headers={"x-goog-request-id": "req-test"},
        body=json.dumps(
            {
                "responseId": "response-test",
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": text}]},
                        "finishReason": "STOP",
                    }
                ],
            }
        ).encode(),
    )


if __name__ == "__main__":
    unittest.main()
