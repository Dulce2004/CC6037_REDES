"""Tests for dynamic tools, sequential tool use, and chat safety policy."""

from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIRECTORY / "src"))

from pharmacy_mcp.host import (  # noqa: E402
    AnthropicMessage,
    AnthropicSettings,
    ChatError,
    ChatLimits,
    ChatOrchestrator,
    ConversationHistory,
    MCPHostError,
    MCPServerResponseError,
    RegisteredTool,
    TOOL_RESULT_TRUNCATION_MARKER,
    ToolConversionError,
    mcp_result_for_anthropic,
    tools_for_anthropic,
)


class ToolConversionTests(unittest.TestCase):
    def test_dynamic_conversion_uses_only_anthropic_fields_and_copies_schema(self) -> None:
        tool = _tool(
            "filesystem__read_text_file",
            annotations={"readOnlyHint": True},
            extra_fields={"outputSchema": {"type": "object"}},
        )
        original = tool.to_dict()

        converted = tools_for_anthropic([tool])

        self.assertEqual(
            set(converted[0]), {"name", "description", "input_schema"}
        )
        self.assertEqual(converted[0]["name"], "filesystem__read_text_file")
        converted[0]["input_schema"]["type"] = "changed"
        self.assertEqual(tool.input_schema["type"], "object")
        self.assertEqual(tool.to_dict(), original)

    def test_registry_order_is_preserved_for_all_server_types(self) -> None:
        tools = [
            _tool("pharmacy__check_stock"),
            _tool("git__git_status"),
            _tool("filesystem__read_text_file"),
        ]
        self.assertEqual(
            [item["name"] for item in tools_for_anthropic(tools)],
            [item.namespaced_name for item in tools],
        )

    def test_duplicates_invalid_names_and_non_object_schemas_are_rejected(self) -> None:
        with self.assertRaisesRegex(ToolConversionError, "Duplicate"):
            tools_for_anthropic([_tool("server__one"), _tool("server__one")])
        with self.assertRaisesRegex(ToolConversionError, "incompatible"):
            tools_for_anthropic([_tool("server.tool")])
        invalid = _tool("server__tool", schema={"type": "array"})
        with self.assertRaisesRegex(ToolConversionError, "object"):
            tools_for_anthropic([invalid])

    def test_mcp_results_preserve_errors_bound_size_and_omit_media(self) -> None:
        structured, is_error = mcp_result_for_anthropic(
            {"structuredContent": {"stock": 25}, "isError": True}
        )
        self.assertEqual(json.loads(structured), {"stock": 25})
        self.assertTrue(is_error)

        media, is_error = mcp_result_for_anthropic(
            {
                "content": [
                    {"type": "text", "text": "prefix"},
                    {"type": "image", "data": "x" * 10_000},
                ]
            }
        )
        self.assertIn("prefix", media)
        self.assertIn("BINARY OMITTED", media)
        self.assertFalse(is_error)

        bounded, _ = mcp_result_for_anthropic(
            {"content": [{"type": "text", "text": "x" * 2_000}]},
            max_chars=256,
        )
        self.assertLessEqual(len(bounded), 256)
        self.assertIn(TOOL_RESULT_TRUNCATION_MARKER, bounded)


class ChatOrchestratorTests(unittest.TestCase):
    def test_general_questions_keep_complete_context_between_requests(self) -> None:
        client = _FakeClient(
            [
                _message(text="Alan Turing fue un matemático británico."),
                _message(text="Nació el 23 de junio de 1912."),
            ]
        )
        orchestrator = ChatOrchestrator(_FakeManager([]), client)

        first = orchestrator.run_turn("¿Quién fue Alan Turing?")
        second = orchestrator.run_turn("¿En qué fecha nació?")

        self.assertIn("matemático", first)
        self.assertIn("1912", second)
        second_messages = client.requests[1]["messages"]
        self.assertEqual(second_messages[0]["content"], "¿Quién fue Alan Turing?")
        self.assertEqual(second_messages[1]["role"], "assistant")
        self.assertEqual(second_messages[2]["content"], "¿En qué fecha nació?")
        self.assertIn("datos académicos simulados", client.requests[0]["system"])

    def test_one_tool_use_executes_and_returns_correlated_result(self) -> None:
        tool = _tool("pharmacy__check_stock")
        manager = _FakeManager([tool])
        client = _FakeClient(
            [
                _message(
                    stop_reason="tool_use",
                    content=[
                        {"type": "text", "text": "Consultaré el inventario."},
                        {
                            "type": "tool_use",
                            "id": "use-1",
                            "name": tool.namespaced_name,
                            "input": {"sku": "MED-ANA-001"},
                        },
                    ],
                ),
                _message(text="Hay 25 unidades."),
            ]
        )
        orchestrator = ChatOrchestrator(manager, client)

        final = orchestrator.run_turn("¿Hay existencias?")

        self.assertEqual(final, "Hay 25 unidades.")
        self.assertEqual(manager.calls[0][0], tool.namespaced_name)
        followup = client.requests[1]["messages"][-1]
        self.assertEqual(followup["role"], "user")
        self.assertEqual(followup["content"][0]["tool_use_id"], "use-1")
        self.assertNotIn("is_error", followup["content"][0])
        assistant = client.requests[1]["messages"][-2]
        self.assertEqual(assistant["content"][0]["type"], "text")

    def test_multiple_tools_are_sequential_and_share_one_result_message(self) -> None:
        tools = [
            _tool("pharmacy__check_stock"),
            _tool("filesystem__read_text_file"),
            _tool("git__git_status"),
        ]
        manager = _FakeManager(tools)
        client = _FakeClient(
            [
                _message(
                    stop_reason="tool_use",
                    content=[
                        _use("a", tools[0].namespaced_name),
                        _use("b", tools[1].namespaced_name),
                        _use("c", tools[2].namespaced_name),
                    ],
                ),
                _message(text="Terminado."),
            ]
        )

        ChatOrchestrator(manager, client).run_turn("Consulta todo")

        self.assertEqual([call[0] for call in manager.calls], [t.namespaced_name for t in tools])
        result_message = client.requests[1]["messages"][-1]
        self.assertEqual(result_message["role"], "user")
        self.assertEqual(
            [item["tool_use_id"] for item in result_message["content"]],
            ["a", "b", "c"],
        )

    def test_tool_errors_do_not_omit_other_results(self) -> None:
        tools = [_tool("server__ok"), _tool("server__rpc"), _tool("server__boom")]
        manager = _FakeManager(tools)
        manager.outcomes["server__rpc"] = MCPServerResponseError(
            server_name="server", code=-32602, message="Invalid params"
        )
        manager.outcomes["server__boom"] = RuntimeError("private detail")
        client = _FakeClient(
            [
                _message(
                    stop_reason="tool_use",
                    content=[
                        _use("ok", "server__ok"),
                        _use("rpc", "server__rpc"),
                        _use("boom", "server__boom"),
                        _use("missing", "server__missing"),
                    ],
                ),
                _message(text="Expliqué los errores."),
            ]
        )

        ChatOrchestrator(manager, client).run_turn("Ejecuta")

        blocks = client.requests[1]["messages"][-1]["content"]
        self.assertEqual(len(blocks), 4)
        self.assertNotIn("is_error", blocks[0])
        self.assertTrue(all(item.get("is_error") is True for item in blocks[1:]))
        self.assertIn("JSON-RPC", blocks[1]["content"])
        self.assertNotIn("private detail", blocks[2]["content"])
        self.assertIn("not registered", blocks[3]["content"])

    def test_mcp_is_error_is_preserved(self) -> None:
        tool = _tool("pharmacy__check_stock")
        manager = _FakeManager([tool])
        manager.outcomes[tool.namespaced_name] = {
            "content": [{"type": "text", "text": "Unknown SKU"}],
            "isError": True,
        }
        client = _FakeClient(
            [
                _message(
                    stop_reason="tool_use",
                    content=[_use("bad", tool.namespaced_name)],
                ),
                _message(text="No encontré el SKU."),
            ]
        )
        ChatOrchestrator(manager, client).run_turn("Busca")
        result = client.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertEqual(result["content"], "Unknown SKU")

    def test_each_mutation_requires_confirmation_and_rejection_is_not_sent(self) -> None:
        for answer, accepted in (("sí", True), ("yes", True), ("", False), ("no", False)):
            with self.subTest(answer=answer):
                tool = _tool("pharmacy__create_order")
                manager = _FakeManager([tool], mutable={tool.namespaced_name})
                prompts = []
                client = _FakeClient(
                    [
                        _message(
                            stop_reason="tool_use",
                            content=[
                                _use(
                                    "order",
                                    tool.namespaced_name,
                                    {
                                        "content": "private file body",
                                        "api_key": "private credential",
                                    },
                                )
                            ],
                        ),
                        _message(text="Resultado final."),
                    ]
                )

                ChatOrchestrator(
                    manager,
                    client,
                    confirmation=lambda prompt: prompts.append(prompt) or answer,
                ).run_turn("Crea orden")

                self.assertEqual(bool(manager.calls), accepted)
                self.assertIn("Servidor: pharmacy", prompts[0])
                self.assertIn("Tool: create_order", prompts[0])
                self.assertIn("OMITTED", prompts[0])
                self.assertIn("REDACTED", prompts[0])
                self.assertNotIn("private file body", prompts[0])
                self.assertNotIn("private credential", prompts[0])
                result = client.requests[1]["messages"][-1]["content"][0]
                self.assertEqual(result.get("is_error") is True, not accepted)

    def test_read_only_call_never_prompts(self) -> None:
        tool = _tool("git__git_status")
        manager = _FakeManager([tool])
        client = _FakeClient(
            [
                _message(stop_reason="tool_use", content=[_use("read", tool.namespaced_name)]),
                _message(text="Limpio."),
            ]
        )
        ChatOrchestrator(
            manager,
            client,
            confirmation=lambda prompt: self.fail("read prompted"),
        ).run_turn("Estado")
        self.assertFalse(manager.calls[0][2])

    def test_round_limit_pairs_unexecuted_tools_and_makes_no_further_call(self) -> None:
        tool = _tool("server__tool")
        manager = _FakeManager([tool])
        client = _FakeClient(
            [
                _message(stop_reason="tool_use", content=[_use("first", tool.namespaced_name)]),
                _message(stop_reason="tool_use", content=[_use("second", tool.namespaced_name)]),
            ]
        )
        result = ChatOrchestrator(
            manager,
            client,
            limits=ChatLimits(max_tool_rounds=1),
        ).run_turn("loop")
        self.assertIn("límite", result)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(manager.calls), 1)

    def test_too_many_tools_executes_none(self) -> None:
        tool = _tool("server__tool")
        manager = _FakeManager([tool])
        client = _FakeClient(
            [
                _message(
                    stop_reason="tool_use",
                    content=[_use("a", tool.namespaced_name), _use("b", tool.namespaced_name)],
                )
            ]
        )
        result = ChatOrchestrator(
            manager,
            client,
            limits=ChatLimits(max_tools_per_response=1),
        ).run_turn("too many")
        self.assertIn("límite", result)
        self.assertEqual(manager.calls, [])
        self.assertEqual(len(client.requests), 1)

    def test_invalid_stop_reason_discards_active_turn(self) -> None:
        history = ConversationHistory()
        orchestrator = ChatOrchestrator(
            _FakeManager([]),
            _FakeClient([_message(text="wait", stop_reason="pause_turn")]),
            history=history,
        )
        with self.assertRaisesRegex(ChatError, "unsupported stop reason"):
            orchestrator.run_turn("question")
        self.assertEqual(history.messages, [])

    def test_api_failure_after_tool_keeps_completed_tool_exchange(self) -> None:
        tool = _tool("pharmacy__check_stock")
        history = ConversationHistory()
        client = _FakeClient(
            [
                _message(
                    stop_reason="tool_use",
                    content=[_use("stock", tool.namespaced_name)],
                )
            ]
        )
        orchestrator = ChatOrchestrator(
            _FakeManager([tool]),
            client,
            history=history,
        )
        with self.assertRaisesRegex(ChatError, "failed safely"):
            orchestrator.run_turn("stock")
        messages = history.messages
        self.assertEqual([message["role"] for message in messages], ["user", "assistant", "user", "assistant"])
        self.assertEqual(messages[2]["content"][0]["tool_use_id"], "stock")


class _FakeClient:
    def __init__(self, responses: list[AnthropicMessage]) -> None:
        self.settings = AnthropicSettings(api_key="present", model="test-model")
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create_message(self, *, messages, tools=None, system=None):
        self.requests.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
                "system": system,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected fake LLM call")
        return self.responses.pop(0)


class _FakeManager:
    def __init__(self, tools, *, mutable=frozenset()) -> None:
        self.tools = tuple(tools)
        self.mutable = set(mutable)
        self.calls = []
        self.outcomes = {}

    def list_tools(self):
        return self.tools

    def resolve_tool(self, name):
        for tool in self.tools:
            if tool.namespaced_name == name:
                return tool
        raise MCPHostError(f"Namespaced tool '{name}' is not registered.")

    def requires_confirmation(self, name):
        self.resolve_tool(name)
        return name in self.mutable

    def invoke_tool(self, name, arguments, *, allow_mutation=False):
        self.calls.append((name, deepcopy(arguments), allow_mutation))
        outcome = self.outcomes.get(
            name,
            {"structuredContent": {"tool": name, "ok": True}},
        )
        if isinstance(outcome, Exception):
            raise outcome
        return deepcopy(outcome)


def _tool(
    name: str,
    *,
    schema=None,
    annotations=None,
    extra_fields=None,
) -> RegisteredTool:
    server, _, original = name.partition("__")
    return RegisteredTool(
        namespaced_name=name,
        server_name=server,
        tool_name=original or name,
        description=f"Description for {name}.",
        input_schema=deepcopy(schema or {"type": "object", "properties": {}}),
        annotations=deepcopy(annotations),
        extra_fields=deepcopy(extra_fields or {}),
    )


def _message(*, text=None, content=None, stop_reason="end_turn") -> AnthropicMessage:
    blocks = content if content is not None else [{"type": "text", "text": text}]
    return AnthropicMessage(
        message_id="msg-test",
        content=tuple(deepcopy(blocks)),
        stop_reason=stop_reason,
        request_id="req-test",
    )


def _use(identifier: str, name: str, arguments=None):
    return {
        "type": "tool_use",
        "id": identifier,
        "name": name,
        "input": deepcopy(arguments or {}),
    }


if __name__ == "__main__":
    unittest.main()
