"""Provider-neutral contracts for the terminal chatbot."""

from __future__ import annotations

from typing import Iterable, Mapping, Protocol

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .manager import RegisteredTool

DEFAULT_LLM_PROVIDER = "gemini"
SUPPORTED_LLM_PROVIDERS = frozenset({"gemini", "anthropic"})


class LLMConfigurationError(ValueError):
    """The selected LLM provider configuration is missing or invalid."""


class LLMAPIError(RuntimeError):
    """A safe provider request or response failure."""


class LLMResponse(Protocol):
    """Normalized response consumed by the shared MCP tool loop."""

    message_id: str
    content: tuple[dict[str, JsonValue], ...]
    stop_reason: str
    request_id: str | None

    @property
    def log_metadata(self) -> Mapping[str, JsonValue]: ...


class LLMClient(Protocol):
    """Small interface implemented by every supported chat provider."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def max_tool_rounds(self) -> int: ...

    def prepare_tools(
        self,
        tools: Iterable[RegisteredTool],
    ) -> list[dict[str, JsonValue]]: ...

    def create_message(
        self,
        *,
        messages: list[dict[str, JsonValue]],
        tools: list[dict[str, JsonValue]] | None = None,
        system: str | None = None,
    ) -> LLMResponse: ...


def provider_from_environ(environ: Mapping[str, str]) -> str:
    """Return the selected provider without reading either provider's key."""

    if not isinstance(environ, Mapping):
        raise TypeError("'environ' must be a mapping.")
    raw = environ.get("LLM_PROVIDER", DEFAULT_LLM_PROVIDER)
    if not isinstance(raw, str):
        raise LLMConfigurationError(
            "LLM_PROVIDER must be either 'gemini' or 'anthropic'."
        )
    provider = raw.strip().casefold()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise LLMConfigurationError(
            "LLM_PROVIDER must be either 'gemini' or 'anthropic'."
        )
    return provider
