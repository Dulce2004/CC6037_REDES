"""Tests for Gemini history, tools, responses, IDs, and thought signatures."""

from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIRECTORY / "src"))

from pharmacy_mcp.host import (  # noqa: E402
    AnthropicMessagesClient,
    AnthropicSettings,
    GeminiAPIError,
    GeminiConfigurationError,
    GeminiGenerateContentClient,
    GeminiSettings,
    HTTPResponse,
    PROVIDER_METADATA_FIELD,
    RegisteredTool,
    messages_for_gemini,
    tools_for_gemini,
)


class GeminiToolConversionTests(unittest.TestCase):
    def test_function_declarations_preserve_order_schema_and_namespace(self) -> None:
        tools = [
            _tool("pharmacy__check_stock"),
            _tool("git__git_status"),
            _tool("filesystem__read_text_file"),
        ]
        originals = deepcopy([tool.input_schema for tool in tools])
        converted = tools_for_gemini(tools)
        declarations = converted[0]["functionDeclarations"]
        self.assertEqual(
            [item["name"] for item in declarations],
            [tool.namespaced_name for tool in tools],
        )
        self.assertEqual(
            set(declarations[0]),
            {"name", "description", "parametersJsonSchema"},
        )
        declarations[0]["parametersJsonSchema"]["properties"]["changed"] = {}
        self.assertEqual([tool.input_schema for tool in tools], originals)

    def test_empty_tools_are_omitted_and_invalid_tools_fail_clearly(self) -> None:
        self.assertEqual(tools_for_gemini([]), [])
        with self.assertRaisesRegex(GeminiConfigurationError, "Duplicate"):
            tools_for_gemini([_tool("server__one"), _tool("server__one")])
        with self.assertRaisesRegex(GeminiConfigurationError, "incompatible"):
            tools_for_gemini([_tool("server.tool")])
        with self.assertRaisesRegex(GeminiConfigurationError, "schema"):
            tools_for_gemini([_tool("server__bad", schema={"type": "array"})])

    def test_mcp_only_fields_are_not_sent_to_gemini(self) -> None:
        converted = tools_for_gemini(
            [
                _tool(
                    "filesystem__write_file",
                    annotations={"readOnlyHint": False},
                    extra_fields={"outputSchema": {}, "execution": {"taskSupport": "forbidden"}},
                )
            ]
        )
        serialized = json.dumps(converted)
        self.assertNotIn("annotations", serialized)
        self.assertNotIn("outputSchema", serialized)
        self.assertNotIn("execution", serialized)


class GeminiHistoryConversionTests(unittest.TestCase):
    def test_roles_text_tool_calls_and_results_are_converted_in_order(self) -> None:
        messages = [
            {"role": "user", "content": "stock"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Consultando."},
                    {
                        "type": "tool_use",
                        "id": "call-a",
                        "name": "pharmacy__check_stock",
                        "input": {"sku": "MED-ANA-001"},
                    },
                    {
                        "type": "tool_use",
                        "id": "call-b",
                        "name": "git__git_status",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-a",
                        "content": "25",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-b",
                        "content": "denied",
                        "is_error": True,
                    },
                ],
            },
        ]
        original = deepcopy(messages)

        converted = messages_for_gemini(messages)

        self.assertEqual([message["role"] for message in converted], ["user", "model", "user"])
        self.assertEqual(converted[0]["parts"], [{"text": "stock"}])
        calls = converted[1]["parts"][1:]
        self.assertEqual([part["functionCall"]["id"] for part in calls], ["call-a", "call-b"])
        responses = converted[2]["parts"]
        self.assertEqual(
            responses[0]["functionResponse"]["response"], {"result": "25"}
        )
        self.assertEqual(
            responses[1]["functionResponse"]["response"], {"error": "denied"}
        )
        self.assertEqual(messages, original)

    def test_function_response_requires_an_earlier_correlated_request(self) -> None:
        with self.assertRaisesRegex(GeminiAPIError, "no matching"):
            messages_for_gemini(
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "missing",
                                "content": "result",
                            }
                        ],
                    }
                ]
            )

    def test_thought_signature_is_restored_on_the_same_function_call(self) -> None:
        signature = "opaque-signature-value"
        converted = messages_for_gemini(
            [
                {"role": "user", "content": "stock"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-a",
                            "name": "pharmacy__check_stock",
                            "input": {},
                            PROVIDER_METADATA_FIELD: {
                                "gemini": {"thoughtSignature": signature}
                            },
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-a",
                            "content": "ok",
                        }
                    ],
                },
            ]
        )
        call_part = converted[1]["parts"][0]
        self.assertEqual(call_part["thoughtSignature"], signature)
        self.assertEqual(call_part["functionCall"]["id"], "call-a")

    def test_private_gemini_metadata_is_never_sent_to_anthropic(self) -> None:
        transport = _QueueTransport([_anthropic_response("ok")])
        client = AnthropicMessagesClient(
            AnthropicSettings(api_key="simulated", model="test-model"),
            transport=transport,
        )
        client.create_message(
            messages=[
                {"role": "user", "content": "stock"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-a",
                            "name": "pharmacy__check_stock",
                            "input": {},
                            PROVIDER_METADATA_FIELD: {
                                "gemini": {"thoughtSignature": "private-signature"}
                            },
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-a",
                            "content": "ok",
                        }
                    ],
                },
            ]
        )
        body = transport.requests[0].body.decode()
        self.assertNotIn(PROVIDER_METADATA_FIELD, body)
        self.assertNotIn("thoughtSignature", body)
        self.assertNotIn("private-signature", body)


class GeminiResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = GeminiSettings(api_key="simulated")

    def test_text_response_is_normalized(self) -> None:
        message = self._message([{"text": "Hola"}])
        self.assertEqual(message.stop_reason, "end_turn")
        self.assertEqual(message.content, ({"type": "text", "text": "Hola"},))
        self.assertEqual(message.finish_reason, "STOP")

    def test_function_call_id_name_args_and_signature_are_preserved(self) -> None:
        signature = "opaque"
        message = self._message(
            [
                {"text": "Voy a consultar."},
                {
                    "functionCall": {
                        "id": "provider-call",
                        "name": "pharmacy__check_stock",
                        "args": {"sku": "MED-ANA-001"},
                    },
                    "thoughtSignature": signature,
                },
            ],
            finish_reason="STOP",
        )
        self.assertEqual(message.stop_reason, "tool_use")
        self.assertEqual(message.content[1]["id"], "provider-call")
        self.assertEqual(message.content[1]["input"], {"sku": "MED-ANA-001"})
        self.assertEqual(
            message.content[1][PROVIDER_METADATA_FIELD],
            {"gemini": {"thoughtSignature": signature}},
        )

    def test_multiple_calls_preserve_order_and_generate_deterministic_ids(self) -> None:
        client = GeminiGenerateContentClient(
            self.settings,
            transport=_QueueTransport(
                [
                    _gemini_response(
                        [
                            {"functionCall": {"name": "server__a", "args": {}}},
                            {"functionCall": {"name": "server__b", "args": {}}},
                        ]
                    ),
                    _gemini_response(
                        [{"functionCall": {"name": "server__c", "args": {}}}]
                    ),
                ]
            ),
        )
        first = client.create_message(messages=[])
        second = client.create_message(messages=[])
        self.assertEqual(
            [block["id"] for block in first.content],
            ["gemini-call-000001", "gemini-call-000002"],
        )
        self.assertEqual(second.content[0]["id"], "gemini-call-000003")

    def test_duplicate_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(GeminiAPIError, "duplicate"):
            self._message(
                [
                    {"functionCall": {"id": "same", "name": "server__a", "args": {}}},
                    {"functionCall": {"id": "same", "name": "server__b", "args": {}}},
                ]
            )

    def test_thought_only_parts_are_not_exposed_as_text(self) -> None:
        message = self._message(
            [{"thought": True, "text": "hidden reasoning"}, {"text": "visible"}]
        )
        self.assertEqual(message.content, ({"type": "text", "text": "visible"},))
        self.assertNotIn("hidden reasoning", repr(message.content))
        with self.assertRaisesRegex(GeminiAPIError, "no visible"):
            self._message([{"thought": True, "text": "hidden only"}])

    def test_missing_candidates_content_parts_and_blocks_are_safe_errors(self) -> None:
        bodies = [
            {},
            {"candidates": []},
            {"candidates": [{"finishReason": "STOP"}]},
            {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]},
            {
                "promptFeedback": {"blockReason": "SAFETY", "private": "detail"},
                "candidates": [],
            },
            {"promptFeedback": "malformed", "candidates": []},
            {
                "candidates": [
                    {"finishReason": "SAFETY", "content": {"parts": [{"text": "x"}]}}
                ]
            },
            {
                "candidates": [
                    {
                        "finishReason": "MALFORMED_FUNCTION_CALL",
                        "content": {"parts": [{"text": "private"}]},
                    }
                ]
            },
        ]
        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(GeminiAPIError) as context:
                    self._raw_message(body)
                self.assertNotIn("private", str(context.exception))

    def test_malformed_calls_and_args_are_rejected(self) -> None:
        for part in (
            {"functionCall": "bad"},
            {"functionCall": {"name": "", "args": {}}},
            {"functionCall": {"name": "server__tool", "args": []}},
            {"functionCall": {"id": "", "name": "server__tool", "args": {}}},
        ):
            with self.subTest(part=part):
                with self.assertRaises(GeminiAPIError):
                    self._message([part])

    def _message(self, parts, *, finish_reason="STOP"):
        return self._raw_message(
            {
                "responseId": "response-test",
                "candidates": [
                    {
                        "content": {"role": "model", "parts": parts},
                        "finishReason": finish_reason,
                    }
                ],
            }
        )

    def _raw_message(self, body):
        client = GeminiGenerateContentClient(
            self.settings,
            transport=_QueueTransport(
                [HTTPResponse(status=200, headers={}, body=json.dumps(body).encode())]
            ),
        )
        return client.create_message(messages=[])


class _QueueTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def _tool(name, *, schema=None, annotations=None, extra_fields=None):
    server, _, tool_name = name.partition("__")
    return RegisteredTool(
        namespaced_name=name,
        server_name=server,
        tool_name=tool_name,
        description=f"Description for {name}.",
        input_schema=deepcopy(schema or {"type": "object", "properties": {"sku": {"type": "string"}}}),
        annotations=deepcopy(annotations),
        extra_fields=deepcopy(extra_fields or {}),
    )


def _gemini_response(parts):
    return HTTPResponse(
        status=200,
        headers={},
        body=json.dumps(
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": parts},
                        "finishReason": "STOP",
                    }
                ]
            }
        ).encode(),
    )


def _anthropic_response(text):
    return HTTPResponse(
        status=200,
        headers={},
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


if __name__ == "__main__":
    unittest.main()
