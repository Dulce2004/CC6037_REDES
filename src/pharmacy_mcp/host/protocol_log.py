"""Durable, redacted JSON Lines logging for MCP protocol traffic."""

from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from pharmacy_mcp.jsonrpc.messages import JsonValue

DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parents[3] / "runtime" / "mcp-host.jsonl"
)
REDACTION_MARKER = "[REDACTED]"

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
    ) -> None:
        if not isinstance(show_traffic, bool):
            raise TypeError("'show_traffic' must be a boolean.")
        try:
            self.path = Path(log_path).resolve()
        except (TypeError, OSError) as exc:
            raise MCPLogError("MCP log path is invalid.") from exc
        self._diagnostic_stream = (
            diagnostic_stream if diagnostic_stream is not None else sys.stderr
        )
        self._show_traffic = show_traffic
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

        payload = _sanitize_unstructured_text(text)
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
            "payload": redact_sensitive_data(decoded),
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


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")
