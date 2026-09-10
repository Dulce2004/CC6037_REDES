"""Manual, synchronous client for Gemini Developer API generateContent."""

from __future__ import annotations

import json
import math
import re
import socket
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .anthropic import HTTPRequest, HTTPResponse
from .llm import LLMAPIError, LLMConfigurationError
from .manager import RegisteredTool

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_GEMINI_MAX_OUTPUT_TOKENS = 1_024
DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS = 30.0
DEFAULT_GEMINI_MAX_RETRIES = 0
DEFAULT_GEMINI_MAX_TOOL_ROUNDS = 8
DEFAULT_GEMINI_MAX_HTTP_RESPONSE_BYTES = 2_000_000
MAX_GEMINI_RETRIES = 3
MAX_RETRY_DELAY_SECONDS = 30.0
PROVIDER_METADATA_FIELD = "_provider_metadata"

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFETY_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
    }
)
_INVALID_FUNCTION_FINISH_REASONS = frozenset(
    {"MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"}
)
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class GeminiConfigurationError(LLMConfigurationError):
    """Gemini environment configuration is absent or invalid."""


class GeminiAPIError(LLMAPIError):
    """A safe, controlled Gemini request or response failure."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        suffix = f" (request ID: {request_id})" if request_id else ""
        super().__init__(f"{message}{suffix}")
        self.status = status
        self.request_id = request_id


@dataclass(frozen=True, slots=True, kw_only=True)
class GeminiSettings:
    """Validated Gemini settings; credentials are never included in repr."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_GEMINI_MODEL
    base_url: str = DEFAULT_GEMINI_BASE_URL
    max_output_tokens: int = DEFAULT_GEMINI_MAX_OUTPUT_TOKENS
    timeout_seconds: float = DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_GEMINI_MAX_RETRIES
    max_tool_rounds: int = DEFAULT_GEMINI_MAX_TOOL_ROUNDS
    max_response_bytes: int = DEFAULT_GEMINI_MAX_HTTP_RESPONSE_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is required when LLM_PROVIDER is gemini."
            )
        object.__setattr__(self, "api_key", self.api_key.strip())
        object.__setattr__(self, "model", _normalize_model(self.model))
        object.__setattr__(self, "base_url", _validate_base_url(self.base_url))
        _validate_integer(
            self.max_output_tokens,
            "GEMINI_MAX_OUTPUT_TOKENS",
            1,
            32_000,
        )
        _validate_number(
            self.timeout_seconds,
            "GEMINI_HTTP_TIMEOUT_SECONDS",
            0.1,
            300.0,
        )
        _validate_integer(
            self.max_retries,
            "GEMINI_MAX_RETRIES",
            0,
            MAX_GEMINI_RETRIES,
        )
        _validate_integer(self.max_tool_rounds, "MCP_MAX_TOOL_ROUNDS", 1, 32)
        _validate_integer(
            self.max_response_bytes,
            "max_response_bytes",
            1_024,
            32_000_000,
        )

    @property
    def endpoint(self) -> str:
        parsed = urlsplit(self.base_url)
        path = parsed.path.rstrip("/")
        if path.endswith("/v1beta"):
            api_path = path
        else:
            api_path = f"{path}/v1beta"
        endpoint_path = f"{api_path}/models/{self.model}:generateContent"
        return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> GeminiSettings:
        if not isinstance(environ, Mapping):
            raise TypeError("'environ' must be a mapping.")
        return cls(
            api_key=environ.get("GEMINI_API_KEY", ""),
            model=environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            base_url=environ.get("GEMINI_BASE_URL", DEFAULT_GEMINI_BASE_URL),
            max_output_tokens=_environment_integer(
                environ,
                "GEMINI_MAX_OUTPUT_TOKENS",
                DEFAULT_GEMINI_MAX_OUTPUT_TOKENS,
            ),
            timeout_seconds=_environment_float(
                environ,
                "GEMINI_HTTP_TIMEOUT_SECONDS",
                DEFAULT_GEMINI_HTTP_TIMEOUT_SECONDS,
            ),
            max_retries=_environment_integer(
                environ,
                "GEMINI_MAX_RETRIES",
                DEFAULT_GEMINI_MAX_RETRIES,
            ),
            max_tool_rounds=_environment_integer(
                environ,
                "MCP_MAX_TOOL_ROUNDS",
                DEFAULT_GEMINI_MAX_TOOL_ROUNDS,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GeminiMessage:
    """Gemini response normalized to the host's provider-neutral blocks."""

    message_id: str
    content: tuple[dict[str, JsonValue], ...]
    stop_reason: str
    request_id: str | None = None
    finish_reason: str = ""
    candidate_count: int = 0
    function_call_count: int = 0

    @property
    def log_metadata(self) -> Mapping[str, JsonValue]:
        return MappingProxyType(
            {
                "finish_reason": self.finish_reason,
                "candidate_count": self.candidate_count,
                "function_calls": self.function_call_count,
            }
        )


GeminiHTTPTransport = Callable[[HTTPRequest], HTTPResponse]
GeminiEventSink = Callable[[str, dict[str, JsonValue]], None]
Sleep = Callable[[float], None]


class GeminiUrllibHTTPTransport:
    """Bounded urllib transport that always closes response streams."""

    def __call__(self, request: HTTPRequest) -> HTTPResponse:
        raw_request = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            response = urllib.request.urlopen(
                raw_request,
                timeout=request.timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            try:
                body, truncated = _bounded_read(exc, request.max_response_bytes)
                headers = getattr(exc, "headers", None)
                return HTTPResponse(
                    status=exc.code,
                    headers=MappingProxyType(
                        dict(headers.items()) if headers is not None else {}
                    ),
                    body=body,
                    body_truncated=truncated,
                )
            finally:
                exc.close()
        except (TimeoutError, socket.timeout) as exc:
            raise GeminiAPIError(
                "Gemini request timed out; it may have been processed."
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise GeminiAPIError(
                    "Gemini request timed out; it may have been processed."
                ) from exc
            raise GeminiAPIError("Could not connect to the Gemini API.") from exc
        except OSError as exc:
            raise GeminiAPIError("Could not connect to the Gemini API.") from exc

        try:
            body, truncated = _bounded_read(response, request.max_response_bytes)
            return HTTPResponse(
                status=response.status,
                headers=MappingProxyType(dict(response.headers.items())),
                body=body,
                body_truncated=truncated,
            )
        finally:
            response.close()


class GeminiGenerateContentClient:
    """Manual non-streaming Gemini REST client with injectable transport."""

    def __init__(
        self,
        settings: GeminiSettings,
        *,
        transport: GeminiHTTPTransport | None = None,
        sleep: Sleep | None = None,
        event_sink: GeminiEventSink | None = None,
    ) -> None:
        if not isinstance(settings, GeminiSettings):
            raise TypeError("'settings' must be GeminiSettings.")
        self.settings = settings
        self._transport = transport or GeminiUrllibHTTPTransport()
        self._sleep = sleep or time.sleep
        self._event_sink = event_sink
        self._next_call_id = 1
        self._next_response_id = 1

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self.settings.model

    @property
    def max_tool_rounds(self) -> int:
        return self.settings.max_tool_rounds

    def prepare_tools(
        self,
        tools: Iterable[RegisteredTool],
    ) -> list[dict[str, JsonValue]]:
        return tools_for_gemini(tools)

    def create_message(
        self,
        *,
        messages: list[dict[str, JsonValue]],
        tools: list[dict[str, JsonValue]] | None = None,
        system: str | None = None,
    ) -> GeminiMessage:
        payload: dict[str, JsonValue] = {
            "contents": messages_for_gemini(messages),
            "generationConfig": {
                "maxOutputTokens": self.settings.max_output_tokens
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = deepcopy(tools)
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise GeminiAPIError(
                "The Gemini request could not be encoded as JSON."
            ) from exc

        request = HTTPRequest(
            method="POST",
            url=self.settings.endpoint,
            headers=MappingProxyType(
                {
                    "x-goog-api-key": self.settings.api_key,
                    "content-type": "application/json",
                    "accept": "application/json",
                }
            ),
            body=body,
            timeout_seconds=self.settings.timeout_seconds,
            max_response_bytes=self.settings.max_response_bytes,
        )
        try:
            response = self._request_with_retries(request)
            request_id = _request_id(response.headers)
            if response.status < 200 or response.status >= 300:
                raise _http_error(response.status, request_id=request_id)
            if (
                response.body_truncated
                or len(response.body) > self.settings.max_response_bytes
            ):
                raise GeminiAPIError(
                    "Gemini response exceeded the configured size limit.",
                    status=response.status,
                    request_id=request_id,
                )
            decoded = _decode_json(response.body, request_id=request_id)
            message = self._parse_message(decoded, request_id=request_id)
        except GeminiAPIError as exc:
            self._event(
                "gemini_request_finished",
                {
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "status": exc.status if exc.status is not None else "error",
                    "error_type": type(exc).__name__,
                    "timeout": "timed out" in str(exc),
                },
            )
            raise
        self._event(
            "gemini_request_finished",
            {
                "provider": self.provider_name,
                "model": self.model_name,
                "status": response.status,
                "finish_reason": message.finish_reason,
                "candidate_count": message.candidate_count,
                "function_calls": message.function_call_count,
            },
        )
        return message

    def _request_with_retries(self, request: HTTPRequest) -> HTTPResponse:
        attempts = self.settings.max_retries + 1
        for attempt in range(1, attempts + 1):
            self._event(
                "gemini_request_started",
                {
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "attempt": attempt,
                    "status": "started",
                },
            )
            try:
                response = self._call_transport(request)
            except GeminiAPIError as exc:
                retry = attempt < attempts and _is_transient_exception(exc)
                self._event(
                    "gemini_request_failed",
                    {
                        "provider": self.provider_name,
                        "model": self.model_name,
                        "attempt": attempt,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "timeout": "timed out" in str(exc),
                        "will_retry": retry,
                    },
                )
                if not retry:
                    raise
                self._sleep(_backoff_delay(attempt))
                continue

            _validate_http_response(response)
            self._event(
                "gemini_http_response",
                {
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "attempt": attempt,
                    "status": response.status,
                },
            )
            if (
                response.status in _RETRYABLE_STATUS_CODES
                and attempt < attempts
            ):
                delay = _retry_delay(response.headers, attempt)
                self._event(
                    "gemini_retry_scheduled",
                    {
                        "provider": self.provider_name,
                        "model": self.model_name,
                        "attempt": attempt,
                        "status": response.status,
                        "delay_seconds": delay,
                    },
                )
                self._sleep(delay)
                continue
            return response
        raise GeminiAPIError("Gemini request failed after retries.")

    def _call_transport(self, request: HTTPRequest) -> HTTPResponse:
        try:
            return self._transport(request)
        except GeminiAPIError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise GeminiAPIError(
                "Gemini request timed out; it may have been processed."
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise GeminiAPIError("Could not connect to the Gemini API.") from exc
        except Exception as exc:
            raise GeminiAPIError("Gemini HTTP transport failed.") from exc

    def _parse_message(
        self,
        value: JsonValue,
        *,
        request_id: str | None,
    ) -> GeminiMessage:
        if not isinstance(value, dict):
            raise GeminiAPIError(
                "Gemini returned a malformed response.", request_id=request_id
            )
        feedback = value.get("promptFeedback")
        if feedback is not None:
            if not isinstance(feedback, dict):
                raise GeminiAPIError(
                    "Gemini returned malformed prompt feedback.",
                    request_id=request_id,
                )
            block_reason = feedback.get("blockReason")
            if block_reason is not None and not isinstance(block_reason, str):
                raise GeminiAPIError(
                    "Gemini returned malformed prompt feedback.",
                    request_id=request_id,
                )
            if block_reason:
                raise GeminiAPIError(
                    "Gemini blocked the prompt for safety reasons.",
                    request_id=request_id,
                )
        candidates = value.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiAPIError(
                "Gemini returned no response candidates.", request_id=request_id
            )
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise GeminiAPIError(
                "Gemini returned an invalid response candidate.",
                request_id=request_id,
            )
        finish_reason = candidate.get("finishReason")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise GeminiAPIError(
                "Gemini returned a candidate without a finish reason.",
                request_id=request_id,
            )
        if finish_reason in _SAFETY_FINISH_REASONS:
            raise GeminiAPIError(
                "Gemini stopped the response for safety reasons.",
                request_id=request_id,
            )
        if finish_reason in _INVALID_FUNCTION_FINISH_REASONS:
            raise GeminiAPIError(
                "Gemini could not produce a valid function call.",
                request_id=request_id,
            )
        content = candidate.get("content")
        if not isinstance(content, dict):
            raise GeminiAPIError(
                "Gemini returned a candidate without content.",
                request_id=request_id,
            )
        if "role" in content and content.get("role") != "model":
            raise GeminiAPIError(
                "Gemini returned content with an invalid role.",
                request_id=request_id,
            )
        parts = content.get("parts")
        if not isinstance(parts, list) or not parts:
            raise GeminiAPIError(
                "Gemini returned empty candidate content.", request_id=request_id
            )

        explicit_ids: set[str] = set()
        for part in parts:
            if not isinstance(part, dict):
                raise GeminiAPIError(
                    "Gemini returned an invalid content part.",
                    request_id=request_id,
                )
            call = part.get("functionCall")
            if isinstance(call, dict) and "id" in call:
                identifier = call["id"]
                if not isinstance(identifier, str) or not identifier:
                    raise GeminiAPIError(
                        "Gemini returned an invalid function call identifier.",
                        request_id=request_id,
                    )
                if identifier in explicit_ids:
                    raise GeminiAPIError(
                        "Gemini returned duplicate function call identifiers.",
                        request_id=request_id,
                    )
                explicit_ids.add(identifier)

        blocks: list[dict[str, JsonValue]] = []
        used_ids = set(explicit_ids)
        function_calls = 0
        for part in parts:
            assert isinstance(part, dict)
            if "functionCall" in part:
                call = part["functionCall"]
                if not isinstance(call, dict):
                    raise GeminiAPIError(
                        "Gemini returned a malformed function call.",
                        request_id=request_id,
                    )
                name = call.get("name")
                arguments = call.get("args", {})
                if (
                    not isinstance(name, str)
                    or not name
                    or not _TOOL_NAME.fullmatch(name)
                    or not isinstance(arguments, dict)
                ):
                    raise GeminiAPIError(
                        "Gemini returned a malformed function call.",
                        request_id=request_id,
                    )
                identifier = call.get("id")
                if identifier is None:
                    identifier = self._new_call_id(used_ids)
                    used_ids.add(identifier)
                assert isinstance(identifier, str)
                block: dict[str, JsonValue] = {
                    "type": "tool_use",
                    "id": identifier,
                    "name": name,
                    "input": deepcopy(arguments),
                }
                if "thoughtSignature" in part:
                    signature = part["thoughtSignature"]
                    if not isinstance(signature, str) or not signature:
                        raise GeminiAPIError(
                            "Gemini returned an invalid thought signature.",
                            request_id=request_id,
                        )
                    block[PROVIDER_METADATA_FIELD] = {
                        "gemini": {"thoughtSignature": signature}
                    }
                blocks.append(block)
                function_calls += 1
                continue
            if part.get("thought") is True:
                continue
            if "text" in part and isinstance(part.get("text"), str):
                blocks.append({"type": "text", "text": part["text"]})
                continue
            raise GeminiAPIError(
                "Gemini returned an unsupported content part.",
                request_id=request_id,
            )
        visible_text = any(
            block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and bool(block["text"].strip())
            for block in blocks
        )
        if not blocks or (not function_calls and not visible_text):
            raise GeminiAPIError(
                "Gemini returned no visible text or function calls.",
                request_id=request_id,
            )
        if function_calls:
            normalized_stop = "tool_use"
        elif finish_reason == "STOP":
            normalized_stop = "end_turn"
        elif finish_reason == "MAX_TOKENS":
            normalized_stop = "max_tokens"
        else:
            raise GeminiAPIError(
                "Gemini returned an unsupported finish reason.",
                request_id=request_id,
            )
        response_id = value.get("responseId")
        if not isinstance(response_id, str) or not response_id:
            response_id = f"gemini-response-{self._next_response_id:06d}"
            self._next_response_id += 1
        return GeminiMessage(
            message_id=response_id,
            content=tuple(blocks),
            stop_reason=normalized_stop,
            request_id=request_id,
            finish_reason=finish_reason,
            candidate_count=len(candidates),
            function_call_count=function_calls,
        )

    def _new_call_id(self, reserved: set[str]) -> str:
        while True:
            identifier = f"gemini-call-{self._next_call_id:06d}"
            self._next_call_id += 1
            if identifier not in reserved:
                return identifier

    def _event(self, event_type: str, payload: dict[str, JsonValue]) -> None:
        if self._event_sink is not None:
            self._event_sink(event_type, payload)


def tools_for_gemini(
    tools: Iterable[RegisteredTool],
) -> list[dict[str, JsonValue]]:
    """Convert registered MCP tools to one Gemini function declaration set."""

    declarations: list[dict[str, JsonValue]] = []
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, RegisteredTool):
            raise GeminiConfigurationError(
                "The MCP registry contains an invalid tool."
            )
        name = tool.namespaced_name
        if not _TOOL_NAME.fullmatch(name):
            raise GeminiConfigurationError(
                f"Tool name '{name}' is incompatible with Gemini."
            )
        if name in names:
            raise GeminiConfigurationError(f"Duplicate tool name '{name}'.")
        if not isinstance(tool.description, str) or not tool.description.strip():
            raise GeminiConfigurationError(f"Tool '{name}' has no description.")
        if (
            not isinstance(tool.input_schema, dict)
            or tool.input_schema.get("type") != "object"
        ):
            raise GeminiConfigurationError(
                f"Tool '{name}' input schema must describe an object."
            )
        declarations.append(
            {
                "name": name,
                "description": tool.description,
                "parametersJsonSchema": deepcopy(tool.input_schema),
            }
        )
        names.add(name)
    if not declarations:
        return []
    return [{"functionDeclarations": declarations}]


def messages_for_gemini(
    messages: list[dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    """Convert normalized history and correlate function response names."""

    converted: list[dict[str, JsonValue]] = []
    tool_names: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "user",
            "assistant",
        }:
            raise GeminiAPIError("The conversation contains an invalid message.")
        internal_role = message["role"]
        content = message.get("content")
        if isinstance(content, str):
            if internal_role != "user":
                raise GeminiAPIError(
                    "The conversation contains invalid assistant content."
                )
            converted.append({"role": "user", "parts": [{"text": content}]})
            continue
        if not isinstance(content, list) or not content:
            raise GeminiAPIError("The conversation contains invalid content.")
        parts: list[dict[str, JsonValue]] = []
        for block in content:
            if not isinstance(block, dict):
                raise GeminiAPIError(
                    "The conversation contains an invalid content block."
                )
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                parts.append({"text": block["text"]})
                continue
            if (
                block_type == "tool_use"
                and isinstance(block.get("id"), str)
                and block["id"]
                and isinstance(block.get("name"), str)
                and block["name"]
                and isinstance(block.get("input"), dict)
            ):
                identifier = block["id"]
                if identifier in tool_names:
                    raise GeminiAPIError(
                        "The conversation contains duplicate tool identifiers."
                    )
                tool_names[identifier] = block["name"]
                part: dict[str, JsonValue] = {
                    "functionCall": {
                        "id": identifier,
                        "name": block["name"],
                        "args": deepcopy(block["input"]),
                    }
                }
                metadata = block.get(PROVIDER_METADATA_FIELD)
                if metadata is not None:
                    if not isinstance(metadata, dict):
                        raise GeminiAPIError(
                            "The conversation contains invalid provider metadata."
                        )
                    gemini = metadata.get("gemini")
                    if not isinstance(gemini, dict) or not isinstance(
                        gemini.get("thoughtSignature"), str
                    ) or not gemini["thoughtSignature"]:
                        raise GeminiAPIError(
                            "The conversation contains invalid Gemini metadata."
                        )
                    part["thoughtSignature"] = gemini["thoughtSignature"]
                parts.append(part)
                continue
            if (
                block_type == "tool_result"
                and isinstance(block.get("tool_use_id"), str)
                and isinstance(block.get("content"), str)
            ):
                identifier = block["tool_use_id"]
                name = tool_names.get(identifier)
                if name is None:
                    raise GeminiAPIError(
                        "A tool result has no matching earlier tool request."
                    )
                response_key = "error" if block.get("is_error") is True else "result"
                parts.append(
                    {
                        "functionResponse": {
                            "id": identifier,
                            "name": name,
                            "response": {response_key: block["content"]},
                        }
                    }
                )
                continue
            raise GeminiAPIError(
                "The conversation contains an invalid content block."
            )
        converted.append(
            {
                "role": "model" if internal_role == "assistant" else "user",
                "parts": parts,
            }
        )
    return converted


def _validate_http_response(response: object) -> None:
    if (
        not isinstance(response, HTTPResponse)
        or isinstance(response.status, bool)
        or not isinstance(response.status, int)
        or response.status < 100
        or response.status > 599
        or not isinstance(response.headers, Mapping)
        or not isinstance(response.body, bytes)
        or not isinstance(response.body_truncated, bool)
    ):
        raise GeminiAPIError("Gemini returned an invalid HTTP response.")


def _decode_json(body: bytes, *, request_id: str | None) -> JsonValue:
    try:
        return json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GeminiAPIError(
            "Gemini returned invalid JSON.", request_id=request_id
        ) from exc


def _http_error(status: int, *, request_id: str | None) -> GeminiAPIError:
    labels = {
        400: "Gemini rejected the request.",
        401: "Gemini authentication failed.",
        403: "Gemini denied access to the requested resource.",
        404: "Gemini could not find the configured model or endpoint.",
        408: "Gemini request timed out; it may have been processed.",
        429: "Gemini quota or rate limit was reached.",
        500: "Gemini is temporarily unavailable.",
        502: "Gemini is temporarily unavailable.",
        503: "Gemini is temporarily unavailable.",
        504: "Gemini is temporarily unavailable.",
    }
    return GeminiAPIError(
        labels.get(status, f"Gemini returned HTTP {status}."),
        status=status,
        request_id=request_id,
    )


def _request_id(headers: Mapping[str, str]) -> str | None:
    for wanted in ("x-goog-request-id", "x-request-id", "request-id"):
        for key, value in headers.items():
            if key.casefold() == wanted and isinstance(value, str):
                return _safe_request_id(value)
    return None


def _safe_request_id(value: str) -> str | None:
    candidate = value.strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", candidate):
        return candidate
    return None


def _normalize_model(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeminiConfigurationError("GEMINI_MODEL must be a model identifier.")
    candidate = value.strip()
    if candidate.startswith("models/"):
        candidate = candidate[len("models/") :]
    if (
        not _MODEL_NAME.fullmatch(candidate)
        or "/" in candidate
        or ".." in candidate
        or "?" in candidate
        or "#" in candidate
    ):
        raise GeminiConfigurationError("GEMINI_MODEL is invalid.")
    return candidate


def _validate_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2_048:
        raise GeminiConfigurationError(
            "GEMINI_BASE_URL must be a non-empty URL."
        )
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GeminiConfigurationError("GEMINI_BASE_URL is invalid.")
    if parsed.scheme == "http" and parsed.hostname.casefold() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise GeminiConfigurationError(
            "HTTP is permitted only for an explicit local simulated endpoint."
        )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _environment_integer(
    environ: Mapping[str, str], name: str, default: int
) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise GeminiConfigurationError(f"{name} must be an integer.") from exc


def _environment_float(
    environ: Mapping[str, str], name: str, default: float
) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise GeminiConfigurationError(f"{name} must be numeric.") from exc


def _validate_integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise GeminiConfigurationError(
            f"{name} must be an integer from {minimum} through {maximum}."
        )


def _validate_number(
    value: object, name: str, minimum: float, maximum: float
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
        or value > maximum
    ):
        raise GeminiConfigurationError(
            f"{name} must be from {minimum} through {maximum}."
        )


def _bounded_read(stream: object, maximum: int) -> tuple[bytes, bool]:
    body = stream.read(maximum + 1)
    if not isinstance(body, bytes):
        raise GeminiAPIError("Gemini returned a non-binary HTTP body.")
    return body[:maximum], len(body) > maximum


def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
    for key, value in headers.items():
        if key.casefold() != "retry-after" or not isinstance(value, str):
            continue
        candidate = value.strip()
        try:
            seconds = float(candidate)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(candidate)
                seconds = retry_at.timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                break
        if math.isfinite(seconds):
            return max(0.0, min(seconds, MAX_RETRY_DELAY_SECONDS))
        break
    return _backoff_delay(attempt)


def _backoff_delay(attempt: int) -> float:
    return min(float(2 ** (attempt - 1)), 8.0)


def _is_transient_exception(error: GeminiAPIError) -> bool:
    text = str(error)
    return "timed out" in text or "connect" in text


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")
