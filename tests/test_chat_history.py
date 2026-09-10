"""Tests for bounded in-memory conversation state."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIRECTORY / "src"))

from pharmacy_mcp.host import (  # noqa: E402
    ConversationError,
    ConversationHistory,
)


class ConversationHistoryTests(unittest.TestCase):
    def test_second_question_retains_complete_first_exchange(self) -> None:
        history = ConversationHistory(max_messages=10)
        history.begin_turn("¿Quién fue Alan Turing?")
        history.append_assistant(
            [{"type": "text", "text": "Fue un matemático británico."}]
        )
        history.finish_turn()
        history.begin_turn("¿En qué fecha nació?")

        self.assertEqual(
            history.messages,
            [
                {"role": "user", "content": "¿Quién fue Alan Turing?"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Fue un matemático británico."}
                    ],
                },
                {"role": "user", "content": "¿En qué fecha nació?"},
            ],
        )

    def test_clear_and_defensive_copy(self) -> None:
        history = ConversationHistory()
        history.begin_turn("Hola")
        snapshot = history.messages
        snapshot[0]["content"] = "mutado"
        self.assertEqual(history.messages[0]["content"], "Hola")
        history.clear()
        self.assertEqual(history.messages, [])
        self.assertFalse(history.has_active_turn)

    def test_empty_and_oversized_input_are_rejected(self) -> None:
        history = ConversationHistory(max_user_input_chars=5)
        for value in ("", "   "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConversationError, "non-empty"):
                    history.begin_turn(value)
        with self.assertRaisesRegex(ConversationError, "5-character"):
            history.begin_turn("123456")

    def test_trimming_removes_whole_old_turn_not_tool_sequence(self) -> None:
        history = ConversationHistory(max_messages=6)
        history.begin_turn("old")
        history.append_assistant([{"type": "text", "text": "answer"}])
        history.finish_turn()

        history.begin_turn("stock")
        history.reserve(2)
        history.append_assistant(
            [
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "pharmacy__check_stock",
                    "input": {"sku": "MED-ANA-001"},
                }
            ]
        )
        history.append_tool_results(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "25",
                }
            ]
        )
        history.append_assistant([{"type": "text", "text": "Hay 25."}])
        history.finish_turn()
        history.begin_turn("again")

        messages = history.messages
        self.assertNotIn("old", [item.get("content") for item in messages])
        assistant_index = next(
            index
            for index, item in enumerate(messages)
            if item.get("role") == "assistant"
            and isinstance(item.get("content"), list)
            and item["content"][0].get("type") == "tool_use"
        )
        self.assertEqual(messages[assistant_index + 1]["role"], "user")
        self.assertEqual(
            messages[assistant_index + 1]["content"][0]["tool_use_id"],
            "tool-1",
        )

    def test_active_tool_exchange_that_cannot_fit_fails_clearly(self) -> None:
        history = ConversationHistory(max_messages=3)
        history.begin_turn("request")
        history.append_assistant(
            [
                {
                    "type": "tool_use",
                    "id": "a",
                    "name": "server__tool",
                    "input": {},
                }
            ]
        )
        history.append_tool_results(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "a",
                    "content": "ok",
                }
            ]
        )
        with self.assertRaisesRegex(ConversationError, "cannot be trimmed"):
            history.append_assistant([{"type": "text", "text": "final"}])

    def test_result_ids_order_and_count_are_validated(self) -> None:
        history = ConversationHistory()
        history.begin_turn("request")
        history.append_assistant(
            [
                {
                    "type": "tool_use",
                    "id": "a",
                    "name": "server__tool",
                    "input": {},
                },
                {
                    "type": "tool_use",
                    "id": "b",
                    "name": "server__tool",
                    "input": {},
                },
            ]
        )
        with self.assertRaisesRegex(ConversationError, "preserve"):
            history.append_tool_results(
                [
                    {"type": "tool_result", "tool_use_id": "b", "content": "2"},
                    {"type": "tool_result", "tool_use_id": "a", "content": "1"},
                ]
            )


if __name__ == "__main__":
    unittest.main()
