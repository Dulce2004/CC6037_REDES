"""Provider-neutral in-memory conversation history with safe turn trimming."""

from __future__ import annotations

from copy import deepcopy

from pharmacy_mcp.jsonrpc.messages import JsonValue

DEFAULT_HISTORY_MAX_MESSAGES = 48
DEFAULT_MAX_USER_INPUT_CHARS = 8_000


class ConversationError(ValueError):
    """The conversation is invalid or cannot be safely retained."""


class ConversationHistory:
    """Store complete turns and never split a tool-use/tool-result exchange."""

    def __init__(
        self,
        *,
        max_messages: int = DEFAULT_HISTORY_MAX_MESSAGES,
        max_user_input_chars: int = DEFAULT_MAX_USER_INPUT_CHARS,
    ) -> None:
        _validate_limit(max_messages, "max_messages", 3, 10_000)
        _validate_limit(
            max_user_input_chars,
            "max_user_input_chars",
            1,
            1_000_000,
        )
        self.max_messages = max_messages
        self.max_user_input_chars = max_user_input_chars
        self._completed_turns: list[list[dict[str, JsonValue]]] = []
        self._current_turn: list[dict[str, JsonValue]] | None = None

    @property
    def messages(self) -> list[dict[str, JsonValue]]:
        flattened = [
            message
            for turn in self._completed_turns
            for message in turn
        ]
        if self._current_turn is not None:
            flattened.extend(self._current_turn)
        return deepcopy(flattened)

    @property
    def has_active_turn(self) -> bool:
        return self._current_turn is not None

    @property
    def active_turn_has_tool_results(self) -> bool:
        if self._current_turn is None or len(self._current_turn) < 3:
            return False
        last = self._current_turn[-1]
        content = last.get("content")
        return (
            last.get("role") == "user"
            and isinstance(content, list)
            and bool(content)
            and all(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
        )

    def begin_turn(self, text: str) -> None:
        if self._current_turn is not None:
            raise ConversationError("A conversation turn is already active.")
        if not isinstance(text, str) or not text.strip():
            raise ConversationError("User input must be a non-empty string.")
        if len(text) > self.max_user_input_chars:
            raise ConversationError(
                f"User input exceeds the {self.max_user_input_chars}-character limit."
            )
        self._current_turn = [{"role": "user", "content": text}]
        self.reserve(0)

    def reserve(self, additional_messages: int) -> None:
        """Make room by dropping complete old turns, never partial messages."""

        if (
            isinstance(additional_messages, bool)
            or not isinstance(additional_messages, int)
            or additional_messages < 0
        ):
            raise ConversationError("Reserved message count must be non-negative.")
        current_count = len(self._current_turn or ())
        while (
            sum(len(turn) for turn in self._completed_turns)
            + current_count
            + additional_messages
            > self.max_messages
            and self._completed_turns
        ):
            self._completed_turns.pop(0)
        if (
            sum(len(turn) for turn in self._completed_turns)
            + current_count
            + additional_messages
            > self.max_messages
        ):
            raise ConversationError(
                "The active turn exceeds the history limit and cannot be trimmed "
                "without splitting a tool exchange."
            )

    def append_assistant(self, content: list[dict[str, JsonValue]]) -> None:
        self._require_current()
        _validate_assistant_content(content)
        self.reserve(1)
        assert self._current_turn is not None
        self._current_turn.append(
            {"role": "assistant", "content": deepcopy(content)}
        )

    def append_tool_results(self, blocks: list[dict[str, JsonValue]]) -> None:
        current = self._require_current()
        if not current or current[-1].get("role") != "assistant":
            raise ConversationError(
                "Tool results must immediately follow an assistant tool request."
            )
        assistant_content = current[-1].get("content")
        expected_ids = [
            block.get("id")
            for block in assistant_content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ] if isinstance(assistant_content, list) else []
        _validate_tool_results(blocks, expected_ids)
        self.reserve(1)
        current.append({"role": "user", "content": deepcopy(blocks)})

    def finish_turn(self) -> None:
        current = self._require_current()
        if len(current) < 2 or current[-1].get("role") != "assistant":
            raise ConversationError(
                "A completed turn must end with an assistant response."
            )
        final_content = current[-1].get("content")
        if isinstance(final_content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in final_content
        ):
            raise ConversationError(
                "A completed turn cannot end with an unresolved tool request."
            )
        self._completed_turns.append(current)
        self._current_turn = None

    def discard_active_turn(self) -> None:
        self._current_turn = None

    def clear(self) -> None:
        self._completed_turns.clear()
        self._current_turn = None

    def _require_current(self) -> list[dict[str, JsonValue]]:
        if self._current_turn is None:
            raise ConversationError("No conversation turn is active.")
        return self._current_turn


def _validate_assistant_content(content: object) -> None:
    if not isinstance(content, list):
        raise ConversationError("Assistant content must be an array.")
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in {
            "text",
            "tool_use",
        }:
            raise ConversationError("Assistant content contains an invalid block.")
        if block["type"] == "text" and not isinstance(block.get("text"), str):
            raise ConversationError("Assistant text must be a string.")
        if block["type"] == "tool_use" and (
            not isinstance(block.get("id"), str)
            or not block["id"]
            or not isinstance(block.get("name"), str)
            or not block["name"]
            or not isinstance(block.get("input"), dict)
        ):
            raise ConversationError("Assistant tool use is invalid.")
        metadata = block.get("_provider_metadata")
        if metadata is not None and not _valid_provider_metadata(metadata):
            raise ConversationError("Assistant provider metadata is invalid.")


def _validate_tool_results(blocks: object, expected_ids: list[object]) -> None:
    if not expected_ids:
        raise ConversationError("The assistant response did not request tools.")
    if not isinstance(blocks, list) or len(blocks) != len(expected_ids):
        raise ConversationError("Every tool request must have exactly one result.")
    actual_ids: list[object] = []
    for block in blocks:
        if (
            not isinstance(block, dict)
            or block.get("type") != "tool_result"
            or not isinstance(block.get("tool_use_id"), str)
            or not isinstance(block.get("content"), str)
            or (
                "is_error" in block
                and not isinstance(block.get("is_error"), bool)
            )
        ):
            raise ConversationError("Tool result content is invalid.")
        actual_ids.append(block["tool_use_id"])
    if actual_ids != expected_ids:
        raise ConversationError(
            "Tool results must preserve tool request order and identifiers."
        )


def _validate_limit(value: object, name: str, minimum: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ConversationError(
            f"'{name}' must be an integer from {minimum} through {maximum}."
        )


def _valid_provider_metadata(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"gemini"}:
        return False
    gemini = value.get("gemini")
    return (
        isinstance(gemini, dict)
        and set(gemini) == {"thoughtSignature"}
        and isinstance(gemini.get("thoughtSignature"), str)
        and bool(gemini["thoughtSignature"])
    )
