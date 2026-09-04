"""Durable, redacted JSON Lines logging for MCP protocol traffic."""

from __future__ import annotations

import json
import re
import sys
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from pharmacy_mcp.jsonrpc.messages import JsonValue

DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parents[3] / "runtime" / "mcp-host.jsonl"
)
REDACTION_MARKER = "[REDACTED]"
TRUNCATION_MARKER = "[TRUNCATED]"
BINARY_OMISSION_MARKER = "[BINARY OMITTED]"
WRITE_CONTENT_OMISSION_MARKER = "[WRITE CONTENT OMITTED]"
DEFAULT_MAX_LOG_PAYLOAD_CHARS = 16_384
DEFAULT_MAX_LOG_STRING_CHARS = 4_096

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "token",
    "access_token",
    "password",
    "secret",
    "client_secret",
}
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)\b(api_key|apikey|authorization|token|access_token|password|secret|"
    r"client_secret)\b(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BINARY_VALUE_KEYS = {"blob", "data"}


class MCPLogError(RuntimeError):
    """Raised when the durable MCP log cannot be opened or written."""


def redact_sensitive_data(value: JsonValue) -> JsonValue:
    """Return a recursively redacted copy without mutating ``value``."""

    if isinstance(value, dict):
        redacted: dict[str, JsonValue] = {}
        for key, item in value.items():
            redacted[key] = (
                REDACTION_MARKER
                if key.casefold() in _SENSITIVE_KEYS
                else redact_sensitive_data(item)
            )
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    return value


class MCPProtocolLogger:
    """Append every MCP exchange to JSONL and optionally mirror it to stderr."""

    def __init__(
        self,
        log_path: str | Path = DEFAULT_LOG_PATH,
        *,
        diagnostic_stream: TextIO | None = None,
        show_traffic: bool = False,
        max_payload_chars: int = DEFAULT_MAX_LOG_PAYLOAD_CHARS,
        max_string_chars: int = DEFAULT_MAX_LOG_STRING_CHARS,
    ) -> None:
        if not isinstance(show_traffic, bool):
            raise TypeError("'show_traffic' must be a boolean.")
        _validate_log_limit(max_payload_chars, "max_payload_chars", minimum=256)
        _validate_log_limit(max_string_chars, "max_string_chars", minimum=64)
        try:
            self.path = Path(log_path).resolve()
        except (TypeError, OSError) as exc:
            raise MCPLogError("MCP log path is invalid.") from exc
        self._diagnostic_stream = (
            diagnostic_stream if diagnostic_stream is not None else sys.stderr
        )
        self._show_traffic = show_traffic
        self._max_payload_chars = max_payload_chars
        self._max_string_chars = max_string_chars
        self._lock = threading.RLock()
        self._file: TextIO | None = None
        self._failure: MCPLogError | None = None
        self._open()

    @property
    def is_open(self) -> bool:
        return self._file is not None and not self._file.closed

    def outbound(
        self,
        server_name: str,
        payload: str,
        transport: str = "stdio",
    ) -> None:
        self._record_message(server_name, transport, "outbound", payload)

    def inbound(
        self,
        server_name: str,
        payload: str,
        transport: str = "stdio",
    ) -> None:
        self._record_message(server_name, transport, "inbound", payload)

    def diagnostic(
        self,
        server_name: str,
        text: str,
        transport: str = "stdio",
    ) -> None:
        """Record child stderr and keep diagnostics visible on host stderr."""

        payload = self._bounded_payload(_sanitize_unstructured_text(text))
        entry: dict[str, JsonValue] = {
            "timestamp": _utc_timestamp(),
            "server": server_name,
            "transport": transport,
            "direction": "diagnostic",
            "message_type": "diagnostic",
            "payload": payload,
        }
        line = self._append_entry(entry)
        self._write_diagnostic(f"[MCP {server_name} stderr] {payload}")
        if self._show_traffic:
            self._write_diagnostic(f"[MCP log] {line}")

    def host_event(
        self,
        server_name: str,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        transport: str = "stdio",
        method: str = "tools/call",
    ) -> None:
        """Record a local policy decision that was not sent to a server."""

        entry: dict[str, JsonValue] = {
            "timestamp": _utc_timestamp(),
            "server": server_name,
            "transport": transport,
            "direction": "local",
            "message_type": event_type,
            "method": method,
            "payload": self._bounded_payload(payload),
        }
        line = self._append_entry(entry)
        if self._show_traffic:
            self._write_diagnostic(f"[MCP log] {line}")

    def close(self) -> None:
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.close()
            except OSError as exc:
                failure = MCPLogError(
                    f"Cannot close MCP log '{self.path}': {exc}."
                )
                self._failure = failure
                raise failure from exc
            finally:
                self._file = None

    def __enter__(self) -> MCPProtocolLogger:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _record_message(
        self,
        server_name: str,
        transport: str,
        direction: str,
        payload: str,
    ) -> None:
        decoded, message_type = _decode_message(payload)
        entry: dict[str, JsonValue] = {
            "timestamp": _utc_timestamp(),
            "server": server_name,
            "transport": transport,
            "direction": direction,
            "message_type": message_type,
            "payload": self._bounded_payload(
                _omit_write_content(decoded) if direction == "outbound" else decoded
            ),
        }
        if isinstance(decoded, dict):
            method = decoded.get("method")
            if isinstance(method, str):
                entry["method"] = method
            if "id" in decoded:
                entry["id"] = decoded["id"]

        line = self._append_entry(entry)
        if self._show_traffic:
            self._write_diagnostic(f"[MCP log] {line}")

    def _bounded_payload(self, value: JsonValue) -> JsonValue:
        return _prepare_log_payload(
            value,
            max_payload_chars=self._max_payload_chars,
            max_string_chars=self._max_string_chars,
        )

    def _append_entry(self, entry: dict[str, JsonValue]) -> str:
        line = json.dumps(
            entry,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        with self._lock:
            if self._failure is not None:
                raise self._failure
            if not self.is_open:
                self._open()
            assert self._file is not None
            try:
                self._file.write(f"{line}\n")
                self._file.flush()
            except OSError as exc:
                failure = MCPLogError(
                    f"Cannot write MCP log '{self.path}': {exc}."
                )
                self._failure = failure
                raise failure from exc
        return line

    def _open(self) -> None:
        with self._lock:
            if self._failure is not None:
                raise self._failure
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._file = self.path.open("a", encoding="utf-8", newline="\n")
            except OSError as exc:
                failure = MCPLogError(
                    f"Cannot open MCP log '{self.path}': {exc}."
                )
                self._failure = failure
                raise failure from exc

    def _write_diagnostic(self, line: str) -> None:
        try:
            self._diagnostic_stream.write(f"{line}\n")
            self._diagnostic_stream.flush()
        except OSError as exc:
            raise MCPLogError("Cannot write MCP diagnostics to stderr.") from exc


def _decode_message(payload: str) -> tuple[JsonValue, str]:
    try:
        decoded = json.loads(payload, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError):
        return _sanitize_unstructured_text(payload), "invalid"

    if not isinstance(decoded, dict):
        return decoded, "invalid"
    if isinstance(decoded.get("method"), str):
        return decoded, "request" if "id" in decoded else "notification"
    if "error" in decoded:
        return decoded, "error"
    if "result" in decoded:
        return decoded, "response"
    return decoded, "invalid"


def _sanitize_unstructured_text(value: str) -> str:
    try:
        decoded = json.loads(value, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError):
        return _SENSITIVE_TEXT_PATTERN.sub(
            lambda match: (
                f"{match.group(1)}{match.group(2)}\"{REDACTION_MARKER}\""
            ),
            value,
        )
    return json.dumps(
        redact_sensitive_data(decoded),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _prepare_log_payload(
    value: JsonValue,
    *,
    max_payload_chars: int,
    max_string_chars: int,
) -> JsonValue:
    """Redact first, then bound strings and the complete logged payload."""

    redacted = redact_sensitive_data(value)
    bounded = _truncate_values(redacted, max_string_chars=max_string_chars)
    serialized = json.dumps(
        bounded,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(serialized) <= max_payload_chars:
        return bounded

    wrapper: dict[str, JsonValue] = {
        "truncated": True,
        "marker": TRUNCATION_MARKER,
        "original_characters": len(serialized),
        "preview": "",
    }
    preview = serialized[:max_payload_chars]
    while preview:
        wrapper["preview"] = preview
        wrapper_text = json.dumps(
            wrapper,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(wrapper_text) <= max_payload_chars:
            break
        overflow = len(wrapper_text) - max_payload_chars
        preview = preview[: max(0, len(preview) - overflow - 1)]
    wrapper["preview"] = preview
    return wrapper


def _omit_write_content(value: JsonValue) -> JsonValue:
    """Omit write/edit bodies while retaining the observable MCP request shape."""

    if not isinstance(value, dict) or value.get("method") != "tools/call":
        return value
    params = value.get("params")
    if not isinstance(params, dict):
        return value
    tool_name = params.get("name")
    if tool_name not in {"write_file", "edit_file"}:
        return value
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return value

    protected = deepcopy(value)
    protected_arguments = protected["params"]["arguments"]
    if tool_name == "write_file":
        if "content" in protected_arguments:
            content = protected_arguments["content"]
            detail = (
                f"{len(content)} characters"
                if isinstance(content, str)
                else "non-string value"
            )
            protected_arguments["content"] = (
                f"{WRITE_CONTENT_OMISSION_MARKER} ({detail})"
            )
    else:
        edits = protected_arguments.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                for key in ("oldText", "newText"):
                    if key in edit:
                        content = edit[key]
                        detail = (
                            f"{len(content)} characters"
                            if isinstance(content, str)
                            else "non-string value"
                        )
                        edit[key] = (
                            f"{WRITE_CONTENT_OMISSION_MARKER} "
                            f"({detail})"
                        )
    return protected


def _truncate_values(value: JsonValue, *, max_string_chars: int) -> JsonValue:
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if key.casefold() in _BINARY_VALUE_KEYS and isinstance(item, str):
                result[key] = f"{BINARY_OMISSION_MARKER} ({len(item)} characters)"
            else:
                result[key] = _truncate_values(
                    item,
                    max_string_chars=max_string_chars,
                )
        return result
    if isinstance(value, list):
        return [
            _truncate_values(item, max_string_chars=max_string_chars)
            for item in value
        ]
    if isinstance(value, str) and len(value) > max_string_chars:
        omitted = len(value) - max_string_chars
        return (
            f"{value[:max_string_chars]}{TRUNCATION_MARKER} "
            f"({omitted} characters omitted)"
        )
    return value


def _validate_log_limit(value: object, name: str, *, minimum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > 1_000_000
    ):
        raise ValueError(
            f"'{name}' must be an integer from {minimum} through 1000000."
        )


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")
