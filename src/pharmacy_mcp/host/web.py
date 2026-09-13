"""Loopback-only web interface for the existing LLM-to-MCP chat host."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO
from urllib.parse import urlsplit

from pharmacy_mcp.jsonrpc.messages import JsonValue

from .anthropic import AnthropicMessagesClient, AnthropicSettings
from .chat import ChatError, ChatLimits, ChatOrchestrator
from .config import DEFAULT_CONFIG_PATH, load_host_config
from .conversation import (
    DEFAULT_HISTORY_MAX_MESSAGES,
    DEFAULT_MAX_USER_INPUT_CHARS,
    ConversationHistory,
)
from .gemini import GeminiGenerateContentClient, GeminiSettings
from .llm import LLMClient, provider_from_environ
from .manager import MCPServerManager, ServerStartFailure
from .protocol_log import MCPProtocolLogger

DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8081
DEFAULT_SESSION_IDLE_SECONDS = 30 * 60
DEFAULT_CONFIRMATION_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_SESSIONS = 64
DEFAULT_MAX_REQUEST_BYTES = 16_384
DEFAULT_MAX_RESPONSE_BYTES = 262_144
DEFAULT_MAX_DISPLAY_MESSAGES = 120
DEFAULT_MAX_DISPLAY_TEXT_CHARS = 16_000
DEFAULT_MAX_DISPLAY_TOTAL_CHARS = 120_000
DEFAULT_WEB_LOG_PATH = (
    Path(__file__).resolve().parents[3] / "runtime" / "mcp-web.jsonl"
)

SESSION_COOKIE_NAME = "pharmacy_mcp_session"
STATIC_DIRECTORY = Path(__file__).with_name("web_static")
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_CONFIRMATION_LINE = re.compile(
    r"^(Servidor|Tool|Argumentos|Efecto esperado):\s*(.*)$"
)


class WebHTTPError(RuntimeError):
    """A controlled HTTP error whose message is safe for the browser."""

    def __init__(
        self,
        status: int | HTTPStatus,
        message: str,
        *,
        allow: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.allow = allow


@dataclass(slots=True)
class PendingConfirmation:
    confirmation_id: str
    server: str
    tool: str
    arguments: str
    effect: str
    deadline: float
    decision: bool | None = None


class _CombinedSessionLogger:
    """Send safe orchestrator metadata to the durable log and the UI model."""

    def __init__(
        self,
        session: WebConversationSession,
        durable_logger: MCPProtocolLogger | None,
    ) -> None:
        self._session = session
        self._durable_logger = durable_logger

    def orchestrator_event(
        self,
        category: str,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        if self._durable_logger is not None:
            self._durable_logger.orchestrator_event(
                category,
                event_type,
                payload,
            )
        self._session.record_orchestrator_event(category, event_type, payload)


class WebConversationSession:
    """One browser's isolated history and explicit mutation decisions."""

    def __init__(
        self,
        manager: MCPServerManager,
        client: LLMClient,
        *,
        protocol_logger: MCPProtocolLogger | None = None,
        history_max_messages: int = DEFAULT_HISTORY_MAX_MESSAGES,
        confirmation_timeout_seconds: float = (
            DEFAULT_CONFIRMATION_TIMEOUT_SECONDS
        ),
        max_display_messages: int = DEFAULT_MAX_DISPLAY_MESSAGES,
    ) -> None:
        if confirmation_timeout_seconds < 0.05:
            raise ValueError("confirmation_timeout_seconds must be at least 0.05")
        if not 8 <= max_display_messages <= 1_000:
            raise ValueError("max_display_messages must be from 8 through 1000")
        self._manager = manager
        self._condition = threading.Condition(threading.RLock())
        self._state = "idle"
        self._messages: list[dict[str, JsonValue]] = []
        self._revision = 0
        self._pending: PendingConfirmation | None = None
        self._worker: threading.Thread | None = None
        self._closing = False
        self._last_access = time.monotonic()
        self._confirmation_timeout_seconds = confirmation_timeout_seconds
        self._max_display_messages = max_display_messages
        history = ConversationHistory(max_messages=history_max_messages)
        event_logger = _CombinedSessionLogger(self, protocol_logger)
        self.orchestrator = ChatOrchestrator(
            manager,
            client,
            history=history,
            protocol_logger=event_logger,  # type: ignore[arg-type]
            confirmation=self._confirm_mutation,
            limits=ChatLimits(max_tool_rounds=client.max_tool_rounds),
        )

    @property
    def last_access(self) -> float:
        with self._condition:
            return self._last_access

    @property
    def is_idle(self) -> bool:
        with self._condition:
            return self._state == "idle" and self._worker is None

    def touch(self) -> None:
        with self._condition:
            self._last_access = time.monotonic()

    def submit(self, text: str) -> dict[str, JsonValue]:
        if not isinstance(text, str):
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "El mensaje debe ser texto.")
        normalized = text.strip()
        if not normalized:
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "El mensaje está vacío.")
        if len(normalized) > DEFAULT_MAX_USER_INPUT_CHARS:
            raise WebHTTPError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "El mensaje excede el límite de 8000 caracteres.",
            )
        with self._condition:
            self._last_access = time.monotonic()
            if self._closing:
                raise WebHTTPError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "La sesión se está cerrando.",
                )
            if self._state != "idle" or self._worker is not None:
                raise WebHTTPError(
                    HTTPStatus.CONFLICT,
                    "Ya hay una respuesta en proceso.",
                )
            self._append_message("user", normalized)
            self._state = "processing"
            worker = threading.Thread(
                target=self._run_turn,
                args=(normalized,),
                name="pharmacy-mcp-web-turn",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return self._snapshot_locked()

    def confirm(
        self,
        confirmation_id: str,
        accept: bool,
    ) -> dict[str, JsonValue]:
        if not isinstance(confirmation_id, str) or not confirmation_id:
            raise WebHTTPError(
                HTTPStatus.BAD_REQUEST,
                "El identificador de confirmación es inválido.",
            )
        if not isinstance(accept, bool):
            raise WebHTTPError(
                HTTPStatus.BAD_REQUEST,
                "La decisión de confirmación debe ser booleana.",
            )
        with self._condition:
            self._last_access = time.monotonic()
            pending = self._pending
            if pending is None or self._state != "awaiting_confirmation":
                raise WebHTTPError(
                    HTTPStatus.CONFLICT,
                    "No existe una confirmación pendiente.",
                )
            if not hmac.compare_digest(
                pending.confirmation_id,
                confirmation_id,
            ):
                raise WebHTTPError(
                    HTTPStatus.CONFLICT,
                    "La confirmación no corresponde a la operación pendiente.",
                )
            if time.monotonic() >= pending.deadline:
                pending.decision = False
                self._condition.notify_all()
                raise WebHTTPError(
                    HTTPStatus.CONFLICT,
                    "La confirmación expiró y fue rechazada.",
                )
            pending.decision = accept
            self._condition.notify_all()
            handoff_deadline = time.monotonic() + 0.5
            while self._pending is pending:
                remaining = handoff_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._snapshot_locked()

    def clear(self) -> dict[str, JsonValue]:
        with self._condition:
            self._last_access = time.monotonic()
            if self._state != "idle" or self._worker is not None:
                raise WebHTTPError(
                    HTTPStatus.CONFLICT,
                    "No se puede limpiar mientras hay una respuesta en proceso.",
                )
            self.orchestrator.clear()
            self._messages.clear()
            self._revision += 1
            return self._snapshot_locked()

    def snapshot(self) -> dict[str, JsonValue]:
        with self._condition:
            self._last_access = time.monotonic()
            return self._snapshot_locked()

    def record_orchestrator_event(
        self,
        category: str,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        if category != "mcp" and category != "policy":
            return
        with self._condition:
            tool = payload.get("tool")
            if category == "mcp" and event_type in {
                "tool_requested",
                "tool_completed",
                "tool_failed",
            }:
                label = _safe_tool_label(tool)
                status = {
                    "tool_requested": "solicitud en proceso",
                    "tool_completed": "solicitud completada",
                    "tool_failed": "solicitud con error",
                }[event_type]
                self._append_message("tool", f"{label}: {status}.")
            elif category == "policy" and event_type in {
                "mutation_authorized",
                "mutation_rejected",
            }:
                server = _safe_label(payload.get("server"), "servidor")
                label = _safe_label(tool, "tool")
                decision = (
                    "confirmación aceptada"
                    if event_type == "mutation_authorized"
                    else "confirmación rechazada"
                )
                self._append_message("tool", f"{server} · {label}: {decision}.")

    def close(self, *, timeout_seconds: float = 35.0) -> bool:
        with self._condition:
            self._closing = True
            if self._pending is not None and self._pending.decision is None:
                self._pending.decision = False
                self._condition.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(0.0, timeout_seconds))
        with self._condition:
            return self._worker is None

    def _run_turn(self, text: str) -> None:
        try:
            response = self.orchestrator.run_turn(text)
        except ChatError as exc:
            with self._condition:
                self._append_message("error", _bounded_text(str(exc)))
        except Exception:
            with self._condition:
                self._append_message(
                    "error",
                    "La respuesta falló de forma segura. Revisa el estado del host.",
                )
        else:
            with self._condition:
                self._append_message("assistant", _bounded_text(response))
        finally:
            with self._condition:
                self._pending = None
                self._state = "idle"
                self._worker = None
                self._last_access = time.monotonic()
                self._revision += 1
                self._condition.notify_all()

    def _confirm_mutation(self, prompt: str) -> str:
        details = _confirmation_details(prompt)
        with self._condition:
            pending = PendingConfirmation(
                confirmation_id=secrets.token_urlsafe(24),
                server=details["server"],
                tool=details["tool"],
                arguments=details["arguments"],
                effect=details["effect"],
                deadline=time.monotonic() + self._confirmation_timeout_seconds,
            )
            self._pending = pending
            self._state = "awaiting_confirmation"
            self._revision += 1
            self._condition.notify_all()
            expired = False
            while pending.decision is None and not self._closing:
                remaining = pending.deadline - time.monotonic()
                if remaining <= 0:
                    pending.decision = False
                    expired = True
                    break
                self._condition.wait(timeout=remaining)
            accepted = pending.decision is True and not self._closing
            if pending.decision is None:
                pending.decision = False
            self._pending = None
            self._state = "processing"
            self._revision += 1
            self._condition.notify_all()
            if expired:
                self._append_message(
                    "tool",
                    f"{pending.server} · {pending.tool}: confirmación expirada; "
                    "operación rechazada.",
                )
            return "sí" if accepted else "no"

    def _append_message(self, role: str, text: str) -> None:
        self._messages.append(
            {
                "role": role,
                "text": _bounded_text(text),
            }
        )
        overflow = len(self._messages) - self._max_display_messages
        if overflow > 0:
            del self._messages[:overflow]
        total_characters = sum(
            len(message["text"])
            for message in self._messages
            if isinstance(message.get("text"), str)
        )
        while (
            total_characters > DEFAULT_MAX_DISPLAY_TOTAL_CHARS
            and len(self._messages) > 1
        ):
            removed = self._messages.pop(0)
            removed_text = removed.get("text")
            if isinstance(removed_text, str):
                total_characters -= len(removed_text)
        self._revision += 1

    def _snapshot_locked(self) -> dict[str, JsonValue]:
        pending: dict[str, JsonValue] | None = None
        if self._pending is not None:
            pending = {
                "id": self._pending.confirmation_id,
                "server": self._pending.server,
                "tool": self._pending.tool,
                "arguments": self._pending.arguments,
                "effect": self._pending.effect,
                "expires_in_seconds": max(
                    0,
                    int(self._pending.deadline - time.monotonic() + 0.999),
                ),
            }
        return {
            "state": self._state,
            "revision": self._revision,
            "messages": [dict(message) for message in self._messages],
            "pending_confirmation": pending,
        }


class WebSessionStore:
    """Bounded process-local collection of browser chat sessions."""

    def __init__(
        self,
        session_factory: Callable[[], WebConversationSession],
        *,
        idle_seconds: int = DEFAULT_SESSION_IDLE_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ) -> None:
        if not 60 <= idle_seconds <= 86_400:
            raise ValueError("idle_seconds must be from 60 through 86400")
        if not 1 <= max_sessions <= 1_000:
            raise ValueError("max_sessions must be from 1 through 1000")
        self._session_factory = session_factory
        self._idle_seconds = idle_seconds
        self._max_sessions = max_sessions
        self._sessions: dict[str, WebConversationSession] = {}
        self._lock = threading.RLock()

    @property
    def idle_seconds(self) -> int:
        return self._idle_seconds

    @property
    def max_sessions(self) -> int:
        return self._max_sessions

    def get_or_create(
        self,
        candidate_id: str | None,
    ) -> tuple[WebConversationSession, str, bool]:
        stale: list[WebConversationSession] = []
        now = time.monotonic()
        with self._lock:
            for session_id, session in tuple(self._sessions.items()):
                if (
                    session.is_idle
                    and now - session.last_access >= self._idle_seconds
                ):
                    stale.append(self._sessions.pop(session_id))
            if candidate_id is not None and _SESSION_ID_PATTERN.fullmatch(
                candidate_id
            ):
                existing = self._sessions.get(candidate_id)
                if existing is not None:
                    existing.touch()
                    result = (existing, candidate_id, False)
                else:
                    result = self._create_locked()
            else:
                result = self._create_locked()
        for session in stale:
            session.close(timeout_seconds=1.0)
        return result

    def close(self, *, timeout_seconds: float = 35.0) -> bool:
        with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        clean = True
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for session in sessions:
            remaining = max(0.0, deadline - time.monotonic())
            clean = session.close(timeout_seconds=remaining) and clean
        return clean

    def _create_locked(
        self,
    ) -> tuple[WebConversationSession, str, bool]:
        if len(self._sessions) >= self._max_sessions:
            raise WebHTTPError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Se alcanzó el límite de sesiones locales.",
            )
        session_id = secrets.token_urlsafe(32)
        while session_id in self._sessions:
            session_id = secrets.token_urlsafe(32)
        session = self._session_factory()
        self._sessions[session_id] = session
        return session, session_id, True


class PharmacyWebApplication:
    """Shared MCP manager plus one orchestrator/history per browser session."""

    def __init__(
        self,
        manager: MCPServerManager,
        client_factory: Callable[[], LLMClient],
        *,
        provider_name: str,
        model_name: str,
        protocol_logger: MCPProtocolLogger | None = None,
        owns_protocol_logger: bool = False,
        startup_failures: Sequence[ServerStartFailure] = (),
        history_max_messages: int = DEFAULT_HISTORY_MAX_MESSAGES,
        confirmation_timeout_seconds: float = (
            DEFAULT_CONFIRMATION_TIMEOUT_SECONDS
        ),
        session_idle_seconds: int = DEFAULT_SESSION_IDLE_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not 1_024 <= max_request_bytes <= 1_000_000:
            raise ValueError("max_request_bytes must be from 1024 through 1000000")
        if not 4_096 <= max_response_bytes <= 2_000_000:
            raise ValueError("max_response_bytes must be from 4096 through 2000000")
        self.manager = manager
        self.provider_name = _safe_label(provider_name, "provider")
        self.model_name = _bounded_text(_safe_label(model_name, "model"), 200)
        self.protocol_logger = protocol_logger
        self._owns_protocol_logger = owns_protocol_logger
        self._startup_failures = {
            failure.server_name for failure in startup_failures
        }
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self._closed = False
        self._close_lock = threading.Lock()

        def new_session() -> WebConversationSession:
            return WebConversationSession(
                manager,
                client_factory(),
                protocol_logger=protocol_logger,
                history_max_messages=history_max_messages,
                confirmation_timeout_seconds=confirmation_timeout_seconds,
            )

        self.sessions = WebSessionStore(
            new_session,
            idle_seconds=session_idle_seconds,
            max_sessions=max_sessions,
        )

    def status(self, session: WebConversationSession) -> dict[str, JsonValue]:
        servers: list[dict[str, JsonValue]] = []
        try:
            summaries = self.manager.list_servers()
        except Exception:
            summaries = ()
        for summary in summaries:
            status = summary.status
            if summary.name in self._startup_failures and status != "ready":
                status = "error"
            servers.append(
                {
                    "name": summary.name,
                    "transport": summary.transport,
                    "enabled": summary.enabled,
                    "status": status,
                }
            )
        return {
            "application": "Pharmacy MCP",
            "provider": {
                "name": self.provider_name,
                "model": self.model_name,
                "status": "configured",
            },
            "servers": servers,
            "limits": {
                "request_bytes": self.max_request_bytes,
                "message_characters": DEFAULT_MAX_USER_INPUT_CHARS,
                "response_bytes": self.max_response_bytes,
                "sessions": self.sessions.max_sessions,
            },
            "session": session.snapshot(),
        }

    def close(self) -> bool:
        with self._close_lock:
            if self._closed:
                return True
            self._closed = True
            sessions_clean = self.sessions.close()
            manager_clean = True
            try:
                self.manager.stop_all()
            except Exception:
                manager_clean = False
            logger_clean = True
            if self._owns_protocol_logger and self.protocol_logger is not None:
                try:
                    self.protocol_logger.close()
                except Exception:
                    logger_clean = False
            return sessions_clean and manager_clean and logger_clean


class PharmacyWebServer(ThreadingHTTPServer):
    """Threaded loopback HTTP server carrying application state."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        application: PharmacyWebApplication,
    ) -> None:
        if server_address[0] != DEFAULT_WEB_HOST:
            raise ValueError("The web host must bind to 127.0.0.1.")
        self.application = application
        super().__init__(server_address, PharmacyWebRequestHandler)

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(10.0)
        return request, client_address


class PharmacyWebRequestHandler(BaseHTTPRequestHandler):
    """Explicit same-origin routes with bounded JSON parsing."""

    protocol_version = "HTTP/1.1"
    server_version = "PharmacyMCP"
    sys_version = ""

    @property
    def web_server(self) -> PharmacyWebServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _dispatch(self, method: str) -> None:
        try:
            self._validate_host()
            path = self._request_path()
            get_routes = {
                "/",
                "/static/styles.css",
                "/static/app.js",
                "/api/status",
                "/api/chat",
            }
            post_routes = {"/api/chat", "/api/confirm", "/api/clear"}
            if method == "GET" and path in get_routes:
                self._handle_get(path)
                return
            if method == "POST" and path in post_routes:
                self._validate_same_origin()
                self._handle_post(path)
                return
            if path in get_routes or path in post_routes:
                allowed = []
                if path in get_routes:
                    allowed.append("GET")
                if path in post_routes:
                    allowed.append("POST")
                raise WebHTTPError(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Método HTTP no permitido.",
                    allow=", ".join(allowed),
                )
            raise WebHTTPError(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")
        except WebHTTPError as exc:
            self.close_connection = True
            self._send_json(
                exc.status,
                {"error": str(exc)},
                allow=exc.allow,
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self.close_connection = True
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "La solicitud falló de forma segura."},
            )

    def _handle_get(self, path: str) -> None:
        if path == "/":
            self._session()
            self._send_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/static/styles.css":
            self._send_static("styles.css", "text/css; charset=utf-8")
            return
        if path == "/static/app.js":
            self._send_static("app.js", "text/javascript; charset=utf-8")
            return
        session = self._session()
        if path == "/api/status":
            self._send_json(HTTPStatus.OK, self.web_server.application.status(session))
            return
        self._send_json(HTTPStatus.OK, session.snapshot())

    def _handle_post(self, path: str) -> None:
        session = self._session()
        payload = self._read_json_object()
        if path == "/api/chat":
            self._reject_unknown_keys(payload, {"message"})
            message = payload.get("message")
            if not isinstance(message, str):
                raise WebHTTPError(
                    HTTPStatus.BAD_REQUEST,
                    "El campo 'message' debe ser texto.",
                )
            self._send_json(HTTPStatus.ACCEPTED, session.submit(message))
            return
        if path == "/api/confirm":
            self._reject_unknown_keys(payload, {"confirmation_id", "accept"})
            confirmation_id = payload.get("confirmation_id")
            accept = payload.get("accept", False)
            if not isinstance(confirmation_id, str):
                raise WebHTTPError(
                    HTTPStatus.BAD_REQUEST,
                    "El campo 'confirmation_id' debe ser texto.",
                )
            if not isinstance(accept, bool):
                raise WebHTTPError(
                    HTTPStatus.BAD_REQUEST,
                    "El campo 'accept' debe ser booleano.",
                )
            self._send_json(
                HTTPStatus.OK,
                session.confirm(confirmation_id, accept),
            )
            return
        self._reject_unknown_keys(payload, set())
        self._send_json(HTTPStatus.OK, session.clear())

    def _session(self) -> WebConversationSession:
        candidate: str | None = None
        raw_cookie = self.headers.get("Cookie")
        if raw_cookie:
            cookie = SimpleCookie()
            try:
                cookie.load(raw_cookie)
            except Exception:
                cookie = SimpleCookie()
            morsel = cookie.get(SESSION_COOKIE_NAME)
            if morsel is not None:
                candidate = morsel.value
        session, session_id, created = (
            self.web_server.application.sessions.get_or_create(candidate)
        )
        if created:
            self._new_session_cookie = (
                f"{SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; "
                "SameSite=Strict; "
                f"Max-Age={self.web_server.application.sessions.idle_seconds}"
            )
        return session

    def _request_path(self) -> str:
        if len(self.path) > 2_048:
            raise WebHTTPError(
                HTTPStatus.REQUEST_URI_TOO_LONG,
                "La ruta solicitada es demasiado larga.",
            )
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise WebHTTPError(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")
        return parsed.path

    def _validate_host(self) -> None:
        host_values = self.headers.get_all("Host", [])
        if len(host_values) != 1 or not host_values[0]:
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "Falta el header Host.")
        raw_host = host_values[0]
        try:
            parsed = urlsplit(f"//{raw_host}")
            hostname = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError as exc:
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "Host inválido.") from exc
        expected_port = self.web_server.server_address[1]
        effective_port = 80 if port is None else port
        if (
            hostname not in {"127.0.0.1", "localhost"}
            or effective_port != expected_port
        ):
            raise WebHTTPError(
                HTTPStatus.MISDIRECTED_REQUEST,
                "El host solicitado no es el listener local.",
            )

    def _validate_same_origin(self) -> None:
        fetch_site = self.headers.get("Sec-Fetch-Site", "").casefold()
        if fetch_site in {"cross-site", "same-site"}:
            raise WebHTTPError(
                HTTPStatus.FORBIDDEN,
                "La solicitud no es del mismo origen.",
            )
        origin = self.headers.get("Origin")
        if origin is None:
            return
        host = self.headers.get("Host", "")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.netloc.casefold() != host.casefold()
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise WebHTTPError(
                HTTPStatus.FORBIDDEN,
                "La solicitud no es del mismo origen.",
            )

    def _read_json_object(self) -> dict[str, JsonValue]:
        transfer_encoding = self.headers.get("Transfer-Encoding")
        if transfer_encoding:
            raise WebHTTPError(
                HTTPStatus.BAD_REQUEST,
                "Transfer-Encoding no está permitido.",
            )
        content_type = self.headers.get("Content-Type")
        if content_type is None:
            raise WebHTTPError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type debe ser application/json con UTF-8.",
            )
        parsed_type = Message()
        parsed_type["content-type"] = content_type
        if parsed_type.get_content_type().casefold() != "application/json":
            raise WebHTTPError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type debe ser application/json con UTF-8.",
            )
        charset = parsed_type.get_param("charset", header="content-type")
        if charset is not None and str(charset).casefold() not in {
            "utf-8",
            "utf8",
        }:
            raise WebHTTPError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "El JSON debe usar UTF-8.",
            )
        length_values = self.headers.get_all("Content-Length", [])
        if len(length_values) != 1:
            raise WebHTTPError(
                HTTPStatus.LENGTH_REQUIRED,
                "Content-Length debe aparecer exactamente una vez.",
            )
        raw_length = length_values[0]
        try:
            length = int(raw_length, 10)
        except ValueError as exc:
            raise WebHTTPError(
                HTTPStatus.BAD_REQUEST,
                "Content-Length es inválido.",
            ) from exc
        if length < 0:
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "Content-Length es inválido.")
        if length > self.web_server.application.max_request_bytes:
            raise WebHTTPError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "La solicitud excede el límite permitido.",
            )
        try:
            body = self.rfile.read(length)
        except TimeoutError as exc:
            raise WebHTTPError(
                HTTPStatus.REQUEST_TIMEOUT,
                "El cuerpo de la solicitud no llegó a tiempo.",
            ) from exc
        if len(body) != length:
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "El cuerpo está incompleto.")
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "El JSON no es UTF-8 válido.") from exc
        try:
            value = json.loads(
                text,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "El cuerpo JSON es inválido.") from exc
        if not isinstance(value, dict):
            raise WebHTTPError(HTTPStatus.BAD_REQUEST, "El cuerpo JSON debe ser un objeto.")
        return value

    def _reject_unknown_keys(
        self,
        payload: Mapping[str, JsonValue],
        allowed: set[str],
    ) -> None:
        if set(payload) - allowed:
            raise WebHTTPError(
                HTTPStatus.BAD_REQUEST,
                "El cuerpo JSON contiene campos no permitidos.",
            )

    def _send_static(self, filename: str, content_type: str) -> None:
        try:
            body = (STATIC_DIRECTORY / filename).read_bytes()
        except OSError as exc:
            raise WebHTTPError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "El recurso local no está disponible.",
            ) from exc
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def _send_json(
        self,
        status: int | HTTPStatus,
        value: Mapping[str, JsonValue],
        *,
        allow: str | None = None,
    ) -> None:
        body = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        limit = self.web_server.application.max_response_bytes
        if len(body) > limit:
            body = json.dumps(
                {"error": "La respuesta excede el límite permitido."},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            allow=allow,
        )

    def _send_bytes(
        self,
        status: int | HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        allow: str | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _content_security_policy())
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        if allow is not None:
            self.send_header("Allow", allow)
        new_cookie = getattr(self, "_new_session_cookie", None)
        if new_cookie is not None:
            self.send_header("Set-Cookie", new_cookie)
            del self._new_session_cookie
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def build_application(
    *,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    log_path: str | Path = DEFAULT_WEB_LOG_PATH,
    diagnostic_stream: TextIO | None = None,
    history_max_messages: int = DEFAULT_HISTORY_MAX_MESSAGES,
    confirmation_timeout_seconds: float = DEFAULT_CONFIRMATION_TIMEOUT_SECONDS,
    session_idle_seconds: int = DEFAULT_SESSION_IDLE_SECONDS,
) -> PharmacyWebApplication:
    """Build production dependencies without exposing provider credentials."""

    environment = os.environ if environ is None else environ
    provider = provider_from_environ(environment)
    logger = MCPProtocolLogger(log_path, diagnostic_stream=diagnostic_stream)
    manager: MCPServerManager | None = None
    try:
        if provider == "gemini":
            settings = GeminiSettings.from_environ(environment)

            def client_factory() -> LLMClient:
                return GeminiGenerateContentClient(
                    settings,
                    event_sink=lambda event_type, payload: (
                        logger.orchestrator_event("llm", event_type, payload)
                    ),
                )

        else:
            settings = AnthropicSettings.from_environ(environment)

            def client_factory() -> LLMClient:
                return AnthropicMessagesClient(settings)

        config = load_host_config(config_path, environ=environment)
        manager = MCPServerManager(config, protocol_logger=logger)
        failures = manager.start_available()
        return PharmacyWebApplication(
            manager,
            client_factory,
            provider_name=provider,
            model_name=settings.model,
            protocol_logger=logger,
            owns_protocol_logger=True,
            startup_failures=failures,
            history_max_messages=history_max_messages,
            confirmation_timeout_seconds=confirmation_timeout_seconds,
            session_idle_seconds=session_idle_seconds,
        )
    except Exception:
        if manager is not None:
            try:
                manager.stop_all()
            except Exception:
                pass
        try:
            logger.close()
        except Exception:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pharmacy-mcp-web",
        description="Local loopback web interface for the Pharmacy MCP chat host.",
    )
    parser.add_argument("--host", default=DEFAULT_WEB_HOST, choices=(DEFAULT_WEB_HOST,))
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--log-file", type=Path, default=DEFAULT_WEB_LOG_PATH)
    parser.add_argument(
        "--history-max-messages",
        type=int,
        default=DEFAULT_HISTORY_MAX_MESSAGES,
    )
    parser.add_argument(
        "--confirmation-timeout-seconds",
        type=float,
        default=DEFAULT_CONFIRMATION_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--session-idle-seconds",
        type=int,
        default=DEFAULT_SESSION_IDLE_SECONDS,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    error = sys.stderr if stderr is None else stderr
    arguments = build_parser().parse_args(argv)
    if not 1 <= arguments.port <= 65_535:
        error.write("web host error: --port must be from 1 through 65535.\n")
        return 1
    application: PharmacyWebApplication | None = None
    server: PharmacyWebServer | None = None
    try:
        application = build_application(
            environ=environ,
            config_path=arguments.config,
            log_path=arguments.log_file,
            diagnostic_stream=error,
            history_max_messages=arguments.history_max_messages,
            confirmation_timeout_seconds=arguments.confirmation_timeout_seconds,
            session_idle_seconds=arguments.session_idle_seconds,
        )
        server = PharmacyWebServer((arguments.host, arguments.port), application)
        output.write(
            f"Pharmacy MCP web disponible en http://{arguments.host}:"
            f"{server.server_address[1]}/\n"
        )
        output.write("Presiona Ctrl+C para cerrar el host y los servidores MCP.\n")
        output.flush()
        server.serve_forever(poll_interval=0.25)
        return 0
    except KeyboardInterrupt:
        output.write("\n")
        output.flush()
        return 0
    except Exception as exc:
        error.write(f"web host error: {type(exc).__name__}: startup failed safely.\n")
        error.flush()
        return 1
    finally:
        if server is not None:
            server.server_close()
        if application is not None and not application.close():
            error.write("web host warning: shutdown did not finish cleanly.\n")
            error.flush()


def _confirmation_details(prompt: str) -> dict[str, str]:
    details = {
        "server": "servidor MCP",
        "tool": "operación mutable",
        "arguments": "Resumen no disponible.",
        "effect": "Puede modificar estado.",
    }
    if not isinstance(prompt, str):
        return details
    key_map = {
        "Servidor": "server",
        "Tool": "tool",
        "Argumentos": "arguments",
        "Efecto esperado": "effect",
    }
    for line in prompt.splitlines():
        match = _CONFIRMATION_LINE.match(line.strip())
        if match is None:
            continue
        value = _bounded_text(match.group(2).strip(), 1_200)
        if value:
            details[key_map[match.group(1)]] = value
    return details


def _safe_tool_label(value: JsonValue | None) -> str:
    if isinstance(value, str) and "__" in value:
        server, tool = value.split("__", 1)
        return f"{_safe_label(server, 'servidor')} · {_safe_label(tool, 'tool')}"
    return _safe_label(value, "tool MCP")


def _safe_label(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(character for character in value.strip() if character.isprintable())
    return _bounded_text(cleaned, 200) or fallback


def _bounded_text(value: str, limit: int = DEFAULT_MAX_DISPLAY_TEXT_CHARS) -> str:
    if len(value) <= limit:
        return value
    marker = "\n[Respuesta truncada por límite.]"
    return f"{value[: max(0, limit - len(marker))]}{marker}"[:limit]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _content_security_policy() -> str:
    return (
        "default-src 'self'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; "
        "script-src 'self'; style-src 'self'; connect-src 'self'"
    )


if __name__ == "__main__":
    raise SystemExit(main())
