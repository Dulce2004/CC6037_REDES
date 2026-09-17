"""Provider-neutral contracts for terminal and web chat frontends.

Protocols define the minimal response and client surface consumed by the shared tool
loop, keeping Gemini and Anthropic details outside orchestration. Provider selection
reads only ``LLM_PROVIDER`` and never touches credentials. The module declares
contracts and constants only and has no I/O side effects."""

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
    def log_metadata(self) -> Mapping[str, JsonValue]:
        """Return bounded provider metadata that is safe for protocol logging."""

        ...


class LLMClient(Protocol):
    """Small interface implemented by every supported chat provider."""

    @property
    def provider_name(self) -> str:
        """Return the stable provider identifier exposed by host status views."""

        ...

    @property
    def model_name(self) -> str:
        """Return the configured model name without exposing credentials."""

        ...

    @property
    def max_tool_rounds(self) -> int:
        """Return the provider-side ceiling used by the shared tool loop."""

        ...

    def prepare_tools(
        self,
        tools: Iterable[RegisteredTool],
    ) -> list[dict[str, JsonValue]]:
        """Convert registered MCP tools into the selected provider's schema."""

        ...

    def create_message(
        self,
        *,
        messages: list[dict[str, JsonValue]],
        tools: list[dict[str, JsonValue]] | None = None,
        system: str | None = None,
    ) -> LLMResponse:
        """Perform one bounded provider request and normalize its response."""

        ...


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
