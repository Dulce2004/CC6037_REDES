"""Manual MCP Streamable HTTP transport for the Pharmacy server."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping, TextIO
from urllib.parse import urlsplit

from pharmacy_mcp.jsonrpc import (
    INVALID_REQUEST,
    ErrorObject,
    ErrorResponse,
    JsonRpcError,
    Request,
    Response,
    deserialize_message,
    serialize_message,
)

from .server import PharmacyMCPServer, SUPPORTED_PROTOCOL_VERSION
from .stdio import (
    DATABASE_PATH_ENVIRONMENT_VARIABLE,
    DEFAULT_RUNTIME_DATABASE_PATH,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MCP_ENDPOINT = "/mcp"
HEALTH_ENDPOINT = "/health"
SESSION_HEADER = "MCP-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"
TOKEN_ENVIRONMENT_VARIABLE = "PHARMACY_MCP_HTTP_TOKEN"
ORIGINS_ENVIRONMENT_VARIABLE = "PHARMACY_MCP_ALLOWED_ORIGINS"
INSECURE_OVERRIDE_ENVIRONMENT_VARIABLE = (
    "PHARMACY_MCP_HTTP_ALLOW_INSECURE_NO_AUTH"
)
DEFAULT_MAX_REQUEST_BYTES = 1_000_000
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_SESSIONS = 100
DEFAULT_SESSION_TTL_SECONDS = 1_800.0

_BEARER_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/-]{1,4096}=*$")


class PharmacyHTTPConfigurationError(ValueError):
    """The HTTP server cannot start with the supplied settings."""


class SessionCapacityError(RuntimeError):
    """The bounded local session registry is full."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PharmacyHTTPSettings:
    """Validated runtime settings; the Bearer token is hidden from repr."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str | None = field(default=None, repr=False)
    allowed_origins: tuple[str, ...] = ()
    allow_insecure_no_auth: bool = False
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_sessions: int = DEFAULT_MAX_SESSIONS
    session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS
    database_path: str | Path = field(
        default_factory=lambda: Path.cwd() / DEFAULT_RUNTIME_DATABASE_PATH
    )

    def __post_init__(self) -> None:
        if (
            not isinstance(self.host, str)
            or not self.host.strip()
            or len(self.host) > 253
            or any(ord(char) < 0x21 for char in self.host)
        ):
            raise PharmacyHTTPConfigurationError("HOST is invalid.")
        object.__setattr__(self, "host", self.host.strip())
        _validate_integer(self.port, "PORT", 0, 65_535)
        if self.token is not None:
            if (
                not isinstance(self.token, str)
                or not _BEARER_TOKEN_PATTERN.fullmatch(self.token)
            ):
                raise PharmacyHTTPConfigurationError(
                    "PHARMACY_MCP_HTTP_TOKEN is invalid."
                )
        if not isinstance(self.allowed_origins, tuple):
            raise PharmacyHTTPConfigurationError(
                "PHARMACY_MCP_ALLOWED_ORIGINS must be a comma-separated list."
            )
        normalized_origins = tuple(
            _validate_origin(origin) for origin in self.allowed_origins
        )
        if len(normalized_origins) != len(set(normalized_origins)):
            raise PharmacyHTTPConfigurationError(
                "PHARMACY_MCP_ALLOWED_ORIGINS contains duplicates."
            )
        object.__setattr__(self, "allowed_origins", normalized_origins)
        if not isinstance(self.allow_insecure_no_auth, bool):
            raise PharmacyHTTPConfigurationError(
                "PHARMACY_MCP_HTTP_ALLOW_INSECURE_NO_AUTH must be boolean."
            )
        _validate_integer(
            self.max_request_bytes,
            "PHARMACY_MCP_HTTP_MAX_REQUEST_BYTES",
            1_024,
            32_000_000,
        )
        _validate_number(
            self.request_timeout_seconds,
            "PHARMACY_MCP_HTTP_REQUEST_TIMEOUT_SECONDS",
            0.1,
            300.0,
        )
        _validate_integer(
            self.max_sessions,
            "PHARMACY_MCP_HTTP_MAX_SESSIONS",
            1,
            10_000,
        )
        _validate_number(
            self.session_ttl_seconds,
            "PHARMACY_MCP_HTTP_SESSION_TTL_SECONDS",
            0.1,
            86_400.0,
        )
        raw_database_path = str(self.database_path)
        if not raw_database_path.strip():
            raise PharmacyHTTPConfigurationError(
                "PHARMACY_MCP_DATABASE_PATH must not be empty."
            )
        object.__setattr__(self, "database_path", Path(self.database_path))
        if (
            self.token is None
            and not _is_loopback_host(self.host)
            and not self.allow_insecure_no_auth
        ):
            raise PharmacyHTTPConfigurationError(
                "A Bearer token is required outside loopback. The insecure "
                "override is for isolated tests only."
            )

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str],
    ) -> PharmacyHTTPSettings:
        if not isinstance(environ, Mapping):
            raise TypeError("'environ' must be a mapping.")
        raw_token = environ.get(TOKEN_ENVIRONMENT_VARIABLE)
        token = raw_token if isinstance(raw_token, str) and raw_token else None
        raw_origins = environ.get(ORIGINS_ENVIRONMENT_VARIABLE, "")
        if not isinstance(raw_origins, str):
            raise PharmacyHTTPConfigurationError(
                "PHARMACY_MCP_ALLOWED_ORIGINS must be a string."
            )
        origins = tuple(
            origin.strip() for origin in raw_origins.split(",") if origin.strip()
        )
        database_path = environ.get(DATABASE_PATH_ENVIRONMENT_VARIABLE)
        if database_path is None:
            selected_database_path: str | Path = (
                Path.cwd() / DEFAULT_RUNTIME_DATABASE_PATH
            )
        else:
            selected_database_path = database_path
        return cls(
            host=environ.get("HOST", DEFAULT_HOST),
            port=_environment_integer(environ, "PORT", DEFAULT_PORT),
            token=token,
            allowed_origins=origins,
            allow_insecure_no_auth=_environment_boolean(
                environ,
                INSECURE_OVERRIDE_ENVIRONMENT_VARIABLE,
                False,
            ),
            max_request_bytes=_environment_integer(
                environ,
                "PHARMACY_MCP_HTTP_MAX_REQUEST_BYTES",
                DEFAULT_MAX_REQUEST_BYTES,
            ),
            request_timeout_seconds=_environment_float(
                environ,
                "PHARMACY_MCP_HTTP_REQUEST_TIMEOUT_SECONDS",
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            ),
            max_sessions=_environment_integer(
                environ,
                "PHARMACY_MCP_HTTP_MAX_SESSIONS",
                DEFAULT_MAX_SESSIONS,
            ),
            session_ttl_seconds=_environment_float(
                environ,
                "PHARMACY_MCP_HTTP_SESSION_TTL_SECONDS",
                DEFAULT_SESSION_TTL_SECONDS,
            ),
            database_path=selected_database_path,
        )


@dataclass(slots=True)
class _Session:
    server: PharmacyMCPServer
    last_activity: float
    lock: threading.RLock = field(default_factory=threading.RLock)
    active: bool = True


class SessionRegistry:
    """Thread-safe, bounded and lazily expiring collection of MCP sessions."""

    def __init__(
        self,
        *,
        maximum: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate_integer(maximum, "maximum", 1, 10_000)
        _validate_number(ttl_seconds, "ttl_seconds", 0.1, 86_400.0)
        self._maximum = maximum
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}

    @property
    def active_count(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._sessions)

    def register(self, server: PharmacyMCPServer) -> str:
        if not isinstance(server, PharmacyMCPServer):
            raise TypeError("'server' must be a PharmacyMCPServer instance.")
        with self._lock:
            self._prune_locked()
            if len(self._sessions) >= self._maximum:
                raise SessionCapacityError("The MCP session limit was reached.")
            session_id = secrets.token_urlsafe(32)
            while session_id in self._sessions:
                session_id = secrets.token_urlsafe(32)
            self._sessions[session_id] = _Session(
                server=server,
                last_activity=self._clock(),
            )
            return session_id

    def get(self, session_id: str) -> _Session | None:
        with self._lock:
            self._prune_locked()
            session = self._sessions.get(session_id)
            if session is not None and session.active:
                session.last_activity = self._clock()
                return session
            return None

    def delete(self, session_id: str) -> bool:
        with self._lock:
            self._prune_locked()
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False
            session.active = False
        with session.lock:
            session.server.close()
        return True

    def close_all(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
            for session in sessions:
                session.active = False
        for session in sessions:
            with session.lock:
                session.server.close()

    def _prune_locked(self) -> None:
        now = self._clock()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_activity >= self._ttl_seconds
        ]
        for session_id in expired:
            session = self._sessions[session_id]
            if not session.lock.acquire(blocking=False):
                continue
            try:
                current = self._sessions.get(session_id)
                if current is not session:
                    continue
                self._sessions.pop(session_id)
                session.active = False
                session.server.close()
            finally:
                session.lock.release()


class PharmacyHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server holding the shared session registry."""

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True
    request_queue_size = 64

    def __init__(
        self,
        settings: PharmacyHTTPSettings,
        server_factory: Callable[[], PharmacyMCPServer],
        *,
        diagnostic_stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.server_factory = server_factory
        self.diagnostic_stream = diagnostic_stream or sys.stderr
        self.sessions = SessionRegistry(
            maximum=settings.max_sessions,
            ttl_seconds=settings.session_ttl_seconds,
            clock=clock,
        )
        super().__init__((settings.host, settings.port), PharmacyHTTPRequestHandler)

    def server_close(self) -> None:
        self.sessions.close_all()
        super().server_close()


class PharmacyHTTPRequestHandler(BaseHTTPRequestHandler):
    """One-request-per-POST Streamable HTTP handler without SSE support."""

    protocol_version = "HTTP/1.1"
    server_version = "PharmacyMCPHTTP"
    sys_version = ""

    @property
    def pharmacy_server(self) -> PharmacyHTTPServer:
        assert isinstance(self.server, PharmacyHTTPServer)
        return self.server

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(
            self.pharmacy_server.settings.request_timeout_seconds
        )

    def log_message(self, format: str, *args: object) -> None:
        try:
            status = args[1] if len(args) > 1 else "unknown"
            self.pharmacy_server.diagnostic_stream.write(
                f"pharmacy MCP HTTP response status={status}\n"
            )
            self.pharmacy_server.diagnostic_stream.flush()
        except OSError:
            pass

    def do_POST(self) -> None:
        if self.path != MCP_ENDPOINT:
            if self._read_request_body() is None:
                return
            self.close_connection = True
            self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found.")
            return
        body = self._read_request_body()
        if body is None:
            return
        if (
            not self._validate_origin()
            or not self._validate_authorization()
            or not self._validate_post_content_headers()
        ):
            self.close_connection = True
            return
        try:
            payload = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self._json_error(HTTPStatus.BAD_REQUEST, "Request body is not valid UTF-8.")
            return
        try:
            message = deserialize_message(payload)
        except JsonRpcError as exc:
            self._jsonrpc_error(exc.code, _canonical_jsonrpc_message(exc.code))
            return
        if not isinstance(message, Request):
            self._jsonrpc_error(INVALID_REQUEST, "Invalid Request")
            return

        session_id = self._single_header(SESSION_HEADER)
        if session_id is False:
            return
        if message.method == "initialize" and session_id is None:
            self._initialize_session(message)
            return
        if session_id is None:
            self._json_error(
                HTTPStatus.BAD_REQUEST,
                "MCP-Session-Id is required after initialization.",
            )
            return
        if not self._validate_protocol_header():
            return
        session = self.pharmacy_server.sessions.get(session_id)
        if session is None:
            self._json_error(HTTPStatus.NOT_FOUND, "MCP session not found.")
            return
        with session.lock:
            if not session.active:
                self._json_error(HTTPStatus.NOT_FOUND, "MCP session not found.")
                return
            result = session.server.process_request(message)
        self._write_server_result(result)

    def do_GET(self) -> None:
        if not self._validate_origin():
            return
        if self.path == HEALTH_ENDPOINT:
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == MCP_ENDPOINT:
            if not self._validate_authorization():
                return
            self._method_not_allowed()
            return
        self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found.")

    def do_DELETE(self) -> None:
        if self.path != MCP_ENDPOINT:
            self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found.")
            return
        if not self._validate_origin() or not self._validate_authorization():
            return
        session_id = self._single_header(SESSION_HEADER)
        if session_id is False:
            return
        if session_id is None:
            self._json_error(HTTPStatus.BAD_REQUEST, "MCP-Session-Id is required.")
            return
        if not self._validate_protocol_header():
            return
        if not self.pharmacy_server.sessions.delete(session_id):
            self._json_error(HTTPStatus.NOT_FOUND, "MCP session not found.")
            return
        self._write_empty(HTTPStatus.NO_CONTENT)

    def do_PUT(self) -> None:
        self._unsupported_method()

    def do_PATCH(self) -> None:
        self._unsupported_method()

    def do_OPTIONS(self) -> None:
        self._unsupported_method()

    def do_HEAD(self) -> None:
        self._unsupported_method()

    def _unsupported_method(self) -> None:
        if self.path == MCP_ENDPOINT:
            if not self._validate_origin() or not self._validate_authorization():
                return
            self._method_not_allowed()
            return
        self._json_error(HTTPStatus.NOT_FOUND, "Endpoint not found.")

    def _initialize_session(self, message: Request) -> None:
        protocol_header = self._single_header(PROTOCOL_HEADER)
        if protocol_header is False:
            return
        if (
            protocol_header is not None
            and protocol_header != SUPPORTED_PROTOCOL_VERSION
        ):
            self._json_error(
                HTTPStatus.BAD_REQUEST,
                "Unsupported MCP protocol version.",
            )
            return
        try:
            server = self.pharmacy_server.server_factory()
        except Exception:
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Pharmacy server initialization failed.",
            )
            return
        result = server.process_request(message)
        if not isinstance(result, Response):
            server.close()
            self._write_server_result(result)
            return
        try:
            session_id = self.pharmacy_server.sessions.register(server)
        except SessionCapacityError:
            server.close()
            self._json_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "MCP session capacity was reached.",
            )
            return
        self._write_jsonrpc(result, session_id=session_id)

    def _write_server_result(
        self,
        result: Response | ErrorResponse | None,
    ) -> None:
        if result is None:
            self._write_empty(HTTPStatus.ACCEPTED)
        else:
            self._write_jsonrpc(result)

    def _validate_post_content_headers(self) -> bool:
        content_type = self._single_header("Content-Type")
        if content_type is False:
            return False
        if content_type is None or not _is_json_content_type(content_type):
            self._json_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type must be application/json with UTF-8 encoding.",
            )
            return False
        accept = self._single_header("Accept")
        if accept is False:
            return False
        if accept is None or not _accepts_required_media(accept):
            self._json_error(
                HTTPStatus.NOT_ACCEPTABLE,
                "Accept must include application/json and text/event-stream.",
            )
            return False
        return True

    def _read_request_body(self) -> bytes | None:
        transfer_encoding = self._single_header("Transfer-Encoding")
        if transfer_encoding is False:
            return None
        if transfer_encoding is not None:
            self.close_connection = True
            self._json_error(
                HTTPStatus.BAD_REQUEST,
                "Chunked request bodies are not supported.",
            )
            return None
        raw_length = self._single_header("Content-Length")
        if raw_length is False:
            return None
        if raw_length is None:
            self.close_connection = True
            self._json_error(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required.")
            return None
        try:
            length = int(raw_length, 10)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._json_error(HTTPStatus.BAD_REQUEST, "Content-Length is invalid.")
            return None
        if length > self.pharmacy_server.settings.max_request_bytes:
            if not self._discard_request_body(length):
                return None
            self.close_connection = True
            self._json_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "Request body exceeds the configured size limit.",
            )
            return None
        try:
            body = self.rfile.read(length)
        except (TimeoutError, socket.timeout):
            self.close_connection = True
            self._json_error(HTTPStatus.REQUEST_TIMEOUT, "Request body timed out.")
            return None
        if len(body) != length:
            self.close_connection = True
            self._json_error(HTTPStatus.BAD_REQUEST, "Request body is incomplete.")
            return None
        return body

    def _discard_request_body(self, length: int) -> bool:
        """Drain an oversized body in bounded chunks before closing the socket."""

        remaining = length
        try:
            while remaining:
                chunk = self.rfile.read(min(remaining, 65_536))
                if not chunk:
                    self.close_connection = True
                    return False
                remaining -= len(chunk)
        except (TimeoutError, socket.timeout):
            self.close_connection = True
            self._json_error(HTTPStatus.REQUEST_TIMEOUT, "Request body timed out.")
            return False
        return True

    def _validate_origin(self) -> bool:
        origin = self._single_header("Origin")
        if origin is False:
            return False
        if origin is None:
            return True
        if origin not in self.pharmacy_server.settings.allowed_origins:
            self._json_error(HTTPStatus.FORBIDDEN, "Origin is not allowed.")
            return False
        return True

    def _validate_authorization(self) -> bool:
        expected_token = self.pharmacy_server.settings.token
        if expected_token is None:
            return True
        authorization = self._single_header("Authorization")
        if authorization is False:
            return False
        supplied = ""
        if isinstance(authorization, str) and authorization.startswith("Bearer "):
            supplied = authorization[len("Bearer ") :]
        if not hmac.compare_digest(supplied, expected_token):
            self._json_error(
                HTTPStatus.UNAUTHORIZED,
                "Bearer authentication is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
            return False
        return True

    def _validate_protocol_header(self) -> bool:
        version = self._single_header(PROTOCOL_HEADER)
        if version is False:
            return False
        if version != SUPPORTED_PROTOCOL_VERSION:
            self._json_error(
                HTTPStatus.BAD_REQUEST,
                "MCP-Protocol-Version must be 2025-11-25.",
            )
            return False
        return True

    def _single_header(self, name: str) -> str | None | bool:
        values = self.headers.get_all(name, failobj=[])
        if len(values) > 1:
            self._json_error(HTTPStatus.BAD_REQUEST, f"Duplicate {name} header.")
            return False
        return values[0].strip() if values else None

    def _method_not_allowed(self) -> None:
        self._json_error(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "Method not allowed.",
            headers={"Allow": "POST, GET, DELETE"},
        )

    def _jsonrpc_error(self, code: int, message: str) -> None:
        self._write_jsonrpc(
            ErrorResponse(error=ErrorObject(code=code, message=message), id=None)
        )

    def _write_jsonrpc(
        self,
        response: Response | ErrorResponse,
        *,
        session_id: str | None = None,
    ) -> None:
        body = serialize_message(response).encode("utf-8")
        headers = {SESSION_HEADER: session_id} if session_id is not None else None
        self._write_bytes(
            HTTPStatus.OK,
            body,
            content_type="application/json",
            headers=headers,
        )

    def _write_json(
        self,
        status: HTTPStatus,
        value: dict[str, str],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._write_bytes(
            status,
            body,
            content_type="application/json",
            headers=headers,
        )

    def _json_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._write_json(status, {"error": message}, headers=headers)

    def _write_empty(self, status: HTTPStatus) -> None:
        self._write_bytes(status, b"", content_type=None)

    def _write_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str | None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        if headers is not None:
            for name, value in headers.items():
                self.send_header(name, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()


def create_http_server(
    settings: PharmacyHTTPSettings,
    *,
    server_factory: Callable[[], PharmacyMCPServer] | None = None,
    diagnostic_stream: TextIO | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> PharmacyHTTPServer:
    """Create, bind and return a configured HTTP server without starting it."""

    if not isinstance(settings, PharmacyHTTPSettings):
        raise TypeError("'settings' must be PharmacyHTTPSettings.")
    factory = server_factory or (
        lambda: PharmacyMCPServer(database_path=settings.database_path)
    )
    return PharmacyHTTPServer(
        settings,
        factory,
        diagnostic_stream=diagnostic_stream,
        clock=clock,
    )


def main() -> int:
    """Run the Streamable HTTP server until interrupted."""

    try:
        settings = PharmacyHTTPSettings.from_environ(os.environ)
        server = create_http_server(settings)
    except Exception as exc:
        sys.stderr.write(
            f"pharmacy MCP HTTP startup failure: {type(exc).__name__}\n"
        )
        sys.stderr.flush()
        return 1
    host, port = server.server_address[:2]
    sys.stderr.write(
        f"pharmacy MCP HTTP listening on {host}:{port}{MCP_ENDPOINT}; "
        f"authentication={'enabled' if settings.token else 'loopback-only'}\n"
    )
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _canonical_jsonrpc_message(code: int) -> str:
    return "Parse error" if code == -32700 else "Invalid Request"


def _is_json_content_type(value: str) -> bool:
    segments = [segment.strip() for segment in value.split(";")]
    if not segments or segments[0].casefold() != "application/json":
        return False
    for parameter in segments[1:]:
        if not parameter:
            continue
        name, separator, raw_value = parameter.partition("=")
        if separator != "=" or name.strip().casefold() != "charset":
            return False
        if raw_value.strip().strip('"').casefold() not in {"utf-8", "utf8"}:
            return False
    return True


def _accepts_required_media(value: str) -> bool:
    accepted: set[str] = set()
    for entry in value.split(","):
        segments = [segment.strip() for segment in entry.split(";")]
        media_type = segments[0].casefold()
        quality = 1.0
        for parameter in segments[1:]:
            name, separator, raw_value = parameter.partition("=")
            if separator == "=" and name.strip().casefold() == "q":
                try:
                    quality = float(raw_value.strip())
                except ValueError:
                    quality = 0.0
        if quality > 0:
            accepted.add(media_type)
    return {"application/json", "text/event-stream"}.issubset(accepted)


def _validate_origin(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value == "*":
        raise PharmacyHTTPConfigurationError("Configured Origin is invalid.")
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PharmacyHTTPConfigurationError("Configured Origin is invalid.")
    return f"{parsed.scheme}://{parsed.netloc}"


def _is_loopback_host(value: str) -> bool:
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _environment_integer(
    environ: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise PharmacyHTTPConfigurationError(f"{name} must be an integer.") from exc


def _environment_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise PharmacyHTTPConfigurationError(f"{name} must be numeric.") from exc


def _environment_boolean(
    environ: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw = environ.get(name)
    if raw is None:
        return default
    if not isinstance(raw, str) or raw.casefold() not in {"true", "false"}:
        raise PharmacyHTTPConfigurationError(f"{name} must be true or false.")
    return raw.casefold() == "true"


def _validate_integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise PharmacyHTTPConfigurationError(
            f"{name} must be an integer from {minimum} through {maximum}."
        )


def _validate_number(
    value: object,
    name: str,
    minimum: float,
    maximum: float,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not (minimum <= float(value) <= maximum)
    ):
        raise PharmacyHTTPConfigurationError(
            f"{name} must be from {minimum} through {maximum}."
        )


if __name__ == "__main__":
    raise SystemExit(main())
