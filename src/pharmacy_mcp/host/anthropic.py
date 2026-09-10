"""Small, synchronous client for Anthropic's Messages HTTP API."""

from __future__ import annotations

import json
import math
import re
import socket
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from pharmacy_mcp.jsonrpc.messages import JsonValue

ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_TOOL_ROUNDS = 8
DEFAULT_MAX_HTTP_RESPONSE_BYTES = 2_000_000


class AnthropicConfigurationError(ValueError):
    """Anthropic environment configuration is absent or invalid."""


class AnthropicAPIError(RuntimeError):
    """A safe, controlled Anthropic request or response failure."""

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
class AnthropicSettings:
    """Validated Messages API settings; the credential is hidden from repr."""

    api_key: str = field(repr=False)
    model: str
    base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS
    max_response_bytes: int = DEFAULT_MAX_HTTP_RESPONSE_BYTES

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise AnthropicConfigurationError(
                "ANTHROPIC_API_KEY is required for the chat command."
            )
        if (
            not isinstance(self.model, str)
            or not self.model.strip()
            or len(self.model) > 200
        ):
            raise AnthropicConfigurationError(
                "ANTHROPIC_MODEL must be a non-empty string of at most 200 characters."
            )
        object.__setattr__(self, "base_url", _validate_base_url(self.base_url))
        _validate_integer(self.max_tokens, "ANTHROPIC_MAX_TOKENS", 1, 32_000)
        _validate_number(
            self.timeout_seconds,
            "ANTHROPIC_HTTP_TIMEOUT_SECONDS",
            0.1,
            300.0,
        )
        _validate_integer(
            self.max_tool_rounds,
            "MCP_MAX_TOOL_ROUNDS",
            1,
            32,
        )
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
        if path.endswith("/v1/messages"):
            endpoint_path = path
        elif path.endswith("/v1"):
            endpoint_path = f"{path}/messages"
        else:
            endpoint_path = f"{path}/v1/messages"
        return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str],
    ) -> AnthropicSettings:
        """Build settings without ever placing the credential in an error."""

        if not isinstance(environ, Mapping):
            raise TypeError("'environ' must be a mapping.")
        api_key = environ.get("ANTHROPIC_API_KEY", "")
        model = environ.get("ANTHROPIC_MODEL", "")
        return cls(
            api_key=api_key,
            model=model,
            base_url=environ.get(
                "ANTHROPIC_BASE_URL", DEFAULT_ANTHROPIC_BASE_URL
            ),
            max_tokens=_environment_integer(
                environ,
                "ANTHROPIC_MAX_TOKENS",
                DEFAULT_MAX_TOKENS,
            ),
            timeout_seconds=_environment_float(
                environ,
                "ANTHROPIC_HTTP_TIMEOUT_SECONDS",
                DEFAULT_HTTP_TIMEOUT_SECONDS,
            ),
            max_tool_rounds=_environment_integer(
                environ,
                "MCP_MAX_TOOL_ROUNDS",
                DEFAULT_MAX_TOOL_ROUNDS,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HTTPRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout_seconds: float
    max_response_bytes: int


@dataclass(frozen=True, slots=True, kw_only=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)
    body_truncated: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class AnthropicMessage:
    message_id: str
    content: tuple[dict[str, JsonValue], ...]
    stop_reason: str
    request_id: str | None = None

    def assistant_message(self) -> dict[str, JsonValue]:
        return {
            "role": "assistant",
            "content": deepcopy(list(self.content)),
        }


HTTPTransport = Callable[[HTTPRequest], HTTPResponse]


class UrllibHTTPTransport:
    """Bounded urllib transport. It performs one request and never retries."""

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
                return HTTPResponse(
                    status=exc.code,
                    headers=MappingProxyType(dict(exc.headers.items())),
                    body=body,
                    body_truncated=truncated,
                )
            finally:
                exc.close()
        except (TimeoutError, socket.timeout) as exc:
            raise AnthropicAPIError("Anthropic request timed out.") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise AnthropicAPIError("Anthropic request timed out.") from exc
            raise AnthropicAPIError(
                "Could not connect to the Anthropic API."
            ) from exc
        except OSError as exc:
            raise AnthropicAPIError(
                "Could not connect to the Anthropic API."
            ) from exc

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


class AnthropicMessagesClient:
    """Manual REST client for one non-streaming Messages API request."""

    def __init__(
        self,
        settings: AnthropicSettings,
        *,
        transport: HTTPTransport | None = None,
    ) -> None:
        if not isinstance(settings, AnthropicSettings):
            raise TypeError("'settings' must be AnthropicSettings.")
        self.settings = settings
        self._transport = transport or UrllibHTTPTransport()

    def create_message(
        self,
        *,
        messages: list[dict[str, JsonValue]],
        tools: list[dict[str, JsonValue]] | None = None,
        system: str | None = None,
    ) -> AnthropicMessage:
        payload: dict[str, JsonValue] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "messages": deepcopy(messages),
        }
        if tools:
            payload["tools"] = deepcopy(tools)
        if system:
            payload["system"] = system
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AnthropicAPIError(
                "The Anthropic request could not be encoded as JSON."
            ) from exc

        request = HTTPRequest(
            method="POST",
            url=self.settings.endpoint,
            headers=MappingProxyType(
                {
                    "x-api-key": self.settings.api_key,
                    "anthropic-version": ANTHROPIC_API_VERSION,
                    "content-type": "application/json",
                    "accept": "application/json",
                }
            ),
            body=body,
            timeout_seconds=self.settings.timeout_seconds,
            max_response_bytes=self.settings.max_response_bytes,
        )
        try:
            response = self._transport(request)
        except AnthropicAPIError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise AnthropicAPIError("Anthropic request timed out.") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise AnthropicAPIError(
                "Could not connect to the Anthropic API."
            ) from exc
        except Exception as exc:
            raise AnthropicAPIError("Anthropic HTTP transport failed.") from exc

        if not isinstance(response, HTTPResponse):
            raise AnthropicAPIError("Anthropic returned an invalid HTTP response.")
        if (
            isinstance(response.status, bool)
            or not isinstance(response.status, int)
            or response.status < 100
            or response.status > 599
            or not isinstance(response.headers, Mapping)
            or not isinstance(response.body, bytes)
            or not isinstance(response.body_truncated, bool)
        ):
            raise AnthropicAPIError("Anthropic returned an invalid HTTP response.")
        request_id = _header(response.headers, "request-id")
        if response.status < 200 or response.status >= 300:
            raise _http_error(response, request_id=request_id)
        if response.body_truncated:
            raise AnthropicAPIError(
                "Anthropic response exceeded the configured size limit.",
                status=response.status,
                request_id=request_id,
            )
        decoded = _decode_json(response.body, request_id=request_id)
        return _parse_message(decoded, request_id=request_id)


def _parse_message(
    value: JsonValue,
    *,
    request_id: str | None,
) -> AnthropicMessage:
    if not isinstance(value, dict):
        raise AnthropicAPIError(
            "Anthropic returned a malformed message.", request_id=request_id
        )
    message_id = value.get("id")
    content = value.get("content")
    stop_reason = value.get("stop_reason")
    stop_sequence = value.get("stop_sequence")
    if (
        value.get("type") != "message"
        or value.get("role") != "assistant"
        or not isinstance(message_id, str)
        or not message_id
        or not isinstance(content, list)
        or not isinstance(stop_reason, str)
        or not stop_reason
        or not isinstance(value.get("model"), str)
        or not value["model"]
        or "stop_sequence" not in value
        or (stop_sequence is not None and not isinstance(stop_sequence, str))
        or not isinstance(value.get("usage"), dict)
    ):
        raise AnthropicAPIError(
            "Anthropic returned a message without required fields.",
            request_id=request_id,
        )
    validated: list[dict[str, JsonValue]] = []
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise AnthropicAPIError(
                "Anthropic returned an invalid content block.",
                request_id=request_id,
            )
        block_type = block["type"]
        if block_type == "text":
            if not isinstance(block.get("text"), str):
                raise AnthropicAPIError(
                    "Anthropic returned an invalid text block.",
                    request_id=request_id,
                )
        elif block_type == "tool_use":
            if (
                not isinstance(block.get("id"), str)
                or not block["id"]
                or not isinstance(block.get("name"), str)
                or not block["name"]
                or not isinstance(block.get("input"), dict)
            ):
                raise AnthropicAPIError(
                    "Anthropic returned an invalid tool request.",
                    request_id=request_id,
                )
        else:
            raise AnthropicAPIError(
                f"Anthropic returned unsupported content type '{block_type}'.",
                request_id=request_id,
            )
        validated.append(deepcopy(block))
    return AnthropicMessage(
        message_id=message_id,
        content=tuple(validated),
        stop_reason=stop_reason,
        request_id=request_id,
    )


def _decode_json(body: bytes, *, request_id: str | None) -> JsonValue:
    try:
        return json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AnthropicAPIError(
            "Anthropic returned invalid JSON.", request_id=request_id
        ) from exc


def _http_error(
    response: HTTPResponse,
    *,
    request_id: str | None,
) -> AnthropicAPIError:
    labels = {
        401: "Anthropic authentication failed.",
        403: "Anthropic denied access to the requested resource.",
        429: "Anthropic rate or spending limit was reached.",
    }
    if response.status in labels:
        message = labels[response.status]
    elif response.status >= 500:
        message = "Anthropic is temporarily unavailable."
    else:
        message = f"Anthropic returned HTTP {response.status}."
    body_request_id = None
    if not response.body_truncated:
        try:
            decoded = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, dict) and isinstance(decoded.get("request_id"), str):
            body_request_id = _safe_request_id(decoded["request_id"])
    return AnthropicAPIError(
        message,
        status=response.status,
        request_id=request_id or body_request_id,
    )


def _validate_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2_048:
        raise AnthropicConfigurationError(
            "ANTHROPIC_BASE_URL must be a non-empty URL."
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
        raise AnthropicConfigurationError("ANTHROPIC_BASE_URL is invalid.")
    if parsed.scheme == "http" and parsed.hostname.casefold() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise AnthropicConfigurationError(
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
        raise AnthropicConfigurationError(f"{name} must be an integer.") from exc


def _environment_float(
    environ: Mapping[str, str], name: str, default: float
) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise AnthropicConfigurationError(f"{name} must be numeric.") from exc


def _validate_integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise AnthropicConfigurationError(
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
        raise AnthropicConfigurationError(
            f"{name} must be from {minimum} through {maximum}."
        )


def _bounded_read(stream: object, maximum: int) -> tuple[bytes, bool]:
    body = stream.read(maximum + 1)
    if not isinstance(body, bytes):
        raise AnthropicAPIError("Anthropic returned a non-binary HTTP body.")
    return body[:maximum], len(body) > maximum


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.casefold() == name.casefold() and isinstance(value, str):
            return _safe_request_id(value)
    return None


def _safe_request_id(value: str) -> str | None:
    candidate = value.strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", candidate):
        return candidate
    return None


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")
