"""Local-only tests for the stdlib Pharmacy MCP web interface."""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from copy import deepcopy
from http.client import HTTPConnection
from pathlib import Path
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host.anthropic import HTTPResponse  # noqa: E402
from pharmacy_mcp.host.gemini import (  # noqa: E402
    GeminiGenerateContentClient,
    GeminiMessage,
    GeminiSettings,
)
from pharmacy_mcp.host.manager import (  # noqa: E402
    RegisteredTool,
    ServerSummary,
)
from pharmacy_mcp.host.protocol_log import MCPProtocolLogger  # noqa: E402
from pharmacy_mcp.host.web import (  # noqa: E402
    DEFAULT_WEB_HOST,
    DEFAULT_WEB_PORT,
    DEFAULT_MAX_REQUEST_BYTES,
    PharmacyWebApplication,
    PharmacyWebServer,
    build_parser,
)


class PharmacyWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = _FakeManager()
        self.clients = []
        self.plans: list[list[GeminiMessage]] = []
        self.client_builder = self._scripted_client
        self.logger = None
        self._start()

    def tearDown(self) -> None:
        self._stop()

    def test_html_static_files_and_accessible_controls_are_local(self) -> None:
        status, headers, body, cookie = self._request("GET", "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("<h1>Pharmacy MCP</h1>", html)
        self.assertIn("Sistema académico", html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('label for="message-input"', html)
        self.assertIn("Confirmación individual", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn("HttpOnly", headers["set-cookie"])
        self.assertIn("SameSite=Strict", headers["set-cookie"])
        self.assertIsNotNone(cookie)

        css_status, _, css, _ = self._request("GET", "/static/styles.css")
        js_status, _, javascript, _ = self._request("GET", "/static/app.js")
        self.assertEqual((css_status, js_status), (200, 200))
        self.assertIn(b"@media (max-width: 760px)", css)
        self.assertIn(b"textContent", javascript)
        self.assertIn(b"initialize();", javascript)
        self.assertNotIn(b"localStorage", javascript)
        self.assertNotIn(b"sessionStorage", javascript)
        self.assertNotIn(b"innerHTML", javascript)
        self.assertNotIn(b"cdn", css.lower() + javascript.lower())

    def test_routes_and_methods_are_explicit(self) -> None:
        self.assertEqual(self._request("GET", "/missing")[0], 404)
        status, headers, _, _ = self._request(
            "POST",
            "/",
            payload={},
        )
        self.assertEqual(status, 405)
        self.assertEqual(headers["allow"], "GET")
        status, headers, _, _ = self._request("PUT", "/api/chat")
        self.assertEqual(status, 405)
        self.assertEqual(headers["allow"], "GET, POST")
        self.assertEqual(self._request("OPTIONS", "/api/status")[0], 405)
        self.assertEqual(self._request("GET", "/api/status?secret=x")[0], 404)

    def test_command_defaults_to_loopback_and_server_rejects_other_bindings(self) -> None:
        arguments = build_parser().parse_args([])
        self.assertEqual(arguments.host, DEFAULT_WEB_HOST)
        self.assertEqual(arguments.port, DEFAULT_WEB_PORT)
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            PharmacyWebServer(("0.0.0.0", 0), self.application)

    def test_status_omits_process_ids_and_private_configuration(self) -> None:
        status, _, body, _ = self._request("GET", "/api/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["provider"]["name"], "gemini")
        self.assertEqual(payload["provider"]["model"], "gemini-simulated")
        self.assertEqual(payload["servers"][0]["status"], "ready")
        serialized = body.decode("utf-8")
        self.assertNotIn("process_id", serialized)
        self.assertNotIn("environment", serialized)
        self.assertNotIn("api_key", serialized.casefold())

    def test_security_headers_are_present_and_cors_is_not_open(self) -> None:
        status, headers, _, _ = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["referrer-policy"], "no-referrer")
        self.assertEqual(headers["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("access-control-allow-origin", headers)

    def test_host_and_origin_checks_reject_cross_origin_requests(self) -> None:
        status, _, _, _ = self._request(
            "POST",
            "/api/clear",
            payload={},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        status, _, _, _ = self._request(
            "GET",
            "/api/status",
            headers={"Host": f"attacker.example:{self.port}"},
        )
        self.assertEqual(status, 421)

    def test_browser_sessions_have_independent_history(self) -> None:
        cookie_one = self._new_cookie()
        cookie_two = self._new_cookie()
        self.assertNotEqual(cookie_one, cookie_two)
        self.assertEqual(
            self._request(
                "POST",
                "/api/chat",
                payload={"message": "mensaje uno"},
                cookie=cookie_one,
            )[0],
            202,
        )
        self.assertEqual(
            self._request(
                "POST",
                "/api/chat",
                payload={"message": "mensaje dos"},
                cookie=cookie_two,
            )[0],
            202,
        )
        one = self._wait_for(cookie_one, "idle")
        two = self._wait_for(cookie_two, "idle")
        one_text = json.dumps(one, ensure_ascii=False)
        two_text = json.dumps(two, ensure_ascii=False)
        self.assertIn("mensaje uno", one_text)
        self.assertNotIn("mensaje dos", one_text)
        self.assertIn("mensaje dos", two_text)
        self.assertNotIn("mensaje uno", two_text)

    def test_context_is_preserved_then_clear_removes_it(self) -> None:
        cookie = self._new_cookie()
        self._chat(cookie, "primer turno")
        self._chat(cookie, "segundo turno")
        client = self.clients[0]
        second_request = client.requests[1]
        self.assertEqual(second_request[0]["content"], "primer turno")
        self.assertEqual(second_request[1]["role"], "assistant")
        self.assertEqual(second_request[2]["content"], "segundo turno")

        status, _, body, _ = self._request(
            "POST",
            "/api/clear",
            payload={},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["messages"], [])
        self._chat(cookie, "después de limpiar")
        third_request = client.requests[2]
        self.assertEqual(len(third_request), 1)
        self.assertEqual(third_request[0]["content"], "después de limpiar")

    def test_simulated_gemini_transport_is_used_without_network(self) -> None:
        credential = "gemini-test-secret-never-expose"
        transport = _GeminiQueueTransport([_gemini_text_response("Respuesta Gemini simulada")])
        self.client_builder = lambda: GeminiGenerateContentClient(
            GeminiSettings(api_key=credential, model="gemini-test"),
            transport=transport,
        )
        cookie = self._new_cookie()
        result = self._chat(cookie, "consulta simulada")
        self.assertIn("Respuesta Gemini simulada", json.dumps(result, ensure_ascii=False))
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.requests[0].headers["x-goog-api-key"], credential)
        browser_text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(credential, browser_text)
        self.assertNotIn(credential, self._request("GET", "/")[2].decode("utf-8"))

    def test_single_and_multiple_tool_calls_use_existing_orchestrator(self) -> None:
        self.plans.append(
            [
                _tool_message(
                    _tool_call("stock-1", "pharmacy__check_stock", {"sku": "A"}),
                    _tool_call("stock-2", "pharmacy__check_stock", {"sku": "B"}),
                ),
                _text_message("Ambas consultas terminaron."),
            ]
        )
        cookie = self._new_cookie()
        result = self._chat(cookie, "consulta dos productos")
        self.assertEqual(
            [call[1]["sku"] for call in self.manager.invocations],
            ["A", "B"],
        )
        self.assertTrue(all(not call[2] for call in self.manager.invocations))
        roles = [message["role"] for message in result["messages"]]
        self.assertGreaterEqual(roles.count("tool"), 4)
        self.assertEqual(roles[-1], "assistant")

    def test_mutation_acceptance_is_one_time_and_arguments_are_sanitized(self) -> None:
        sensitive = "secret-prescription-value"
        self.plans.append(
            [
                _tool_message(
                    _tool_call(
                        "order-1",
                        "pharmacy__create_order",
                        {"sku": "A", "prescription_id": sensitive},
                    )
                ),
                _text_message("Orden ficticia creada."),
            ]
        )
        cookie = self._new_cookie()
        self._post_chat(cookie, "crea una orden")
        waiting = self._wait_for(cookie, "awaiting_confirmation")
        pending = waiting["pending_confirmation"]
        self.assertEqual(pending["server"], "pharmacy")
        self.assertEqual(pending["tool"], "create_order")
        self.assertNotIn(sensitive, pending["arguments"])
        self.assertIn("REDACTED", pending["arguments"])
        confirmation_id = pending["id"]
        status, _, _, _ = self._request(
            "POST",
            "/api/confirm",
            payload={"confirmation_id": confirmation_id, "accept": True},
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        finished = self._wait_for(cookie, "idle")
        self.assertEqual(len(self.manager.invocations), 1)
        self.assertTrue(self.manager.invocations[0][2])
        self.assertIn("Orden ficticia creada", json.dumps(finished, ensure_ascii=False))
        replay = self._request(
            "POST",
            "/api/confirm",
            payload={"confirmation_id": confirmation_id, "accept": True},
            cookie=cookie,
        )
        self.assertEqual(replay[0], 409)

    def test_mutation_rejection_and_default_rejection_do_not_invoke(self) -> None:
        for explicit in (True, False):
            with self.subTest(explicit_rejection=explicit):
                self.plans.append(
                    [
                        _tool_message(
                            _tool_call(
                                f"order-{explicit}",
                                "pharmacy__create_order",
                                {"sku": "A"},
                            )
                        ),
                        _text_message("La operación no fue ejecutada."),
                    ]
                )
                cookie = self._new_cookie()
                self._post_chat(cookie, "intenta crear")
                pending = self._wait_for(cookie, "awaiting_confirmation")[
                    "pending_confirmation"
                ]
                body = {"confirmation_id": pending["id"]}
                if explicit:
                    body["accept"] = False
                status = self._request(
                    "POST",
                    "/api/confirm",
                    payload=body,
                    cookie=cookie,
                )[0]
                self.assertEqual(status, 200)
                self._wait_for(cookie, "idle")
        self.assertEqual(self.manager.invocations, [])

    def test_each_mutation_in_multiple_tool_response_is_confirmed(self) -> None:
        self.plans.append(
            [
                _tool_message(
                    _tool_call("order-a", "pharmacy__create_order", {"sku": "A"}),
                    _tool_call("order-b", "pharmacy__create_order", {"sku": "B"}),
                ),
                _text_message("Operaciones revisadas."),
            ]
        )
        cookie = self._new_cookie()
        self._post_chat(cookie, "dos órdenes")
        first = self._wait_for(cookie, "awaiting_confirmation")["pending_confirmation"]
        self.assertEqual(
            self._request(
                "POST",
                "/api/confirm",
                payload={"confirmation_id": first["id"], "accept": True},
                cookie=cookie,
            )[0],
            200,
        )
        second = self._wait_for_new_confirmation(cookie, first["id"])
        self.assertEqual(
            self._request(
                "POST",
                "/api/confirm",
                payload={"confirmation_id": second["id"], "accept": False},
                cookie=cookie,
            )[0],
            200,
        )
        self._wait_for(cookie, "idle")
        self.assertEqual(len(self.manager.invocations), 1)
        self.assertEqual(self.manager.invocations[0][1]["sku"], "A")

    def test_invalid_and_expired_confirmations_are_rejected(self) -> None:
        self._restart(confirmation_timeout_seconds=0.12)
        self.plans.append(
            [
                _tool_message(
                    _tool_call("order-expire", "pharmacy__create_order", {"sku": "A"})
                ),
                _text_message("Final."),
            ]
        )
        cookie = self._new_cookie()
        self._post_chat(cookie, "orden")
        pending = self._wait_for(cookie, "awaiting_confirmation")["pending_confirmation"]
        invalid = self._request(
            "POST",
            "/api/confirm",
            payload={"confirmation_id": "invalid-confirmation", "accept": True},
            cookie=cookie,
        )
        self.assertEqual(invalid[0], 409)
        result = self._wait_for(cookie, "idle", timeout=2.0)
        self.assertEqual(self.manager.invocations, [])
        self.assertIn("expirada", json.dumps(result, ensure_ascii=False))
        expired = self._request(
            "POST",
            "/api/confirm",
            payload={"confirmation_id": pending["id"], "accept": True},
            cookie=cookie,
        )
        self.assertEqual(expired[0], 409)

    def test_busy_session_rejects_new_chat_and_clear(self) -> None:
        release = threading.Event()
        self.client_builder = lambda: _BlockingClient(release)
        cookie = self._new_cookie()
        self._post_chat(cookie, "espera")
        self._wait_for(cookie, "processing")
        self.assertEqual(
            self._request(
                "POST",
                "/api/chat",
                payload={"message": "otro"},
                cookie=cookie,
            )[0],
            409,
        )
        self.assertEqual(
            self._request(
                "POST",
                "/api/clear",
                payload={},
                cookie=cookie,
            )[0],
            409,
        )
        release.set()
        self._wait_for(cookie, "idle")

    def test_content_type_utf8_and_json_shape_are_validated(self) -> None:
        cookie = self._new_cookie()
        cases = [
            ({"Content-Type": "text/plain"}, b"{}", 415),
            ({"Content-Type": "application/json; charset=latin-1"}, b"{}", 415),
            ({"Content-Type": "application/json"}, b"\xff", 400),
            ({"Content-Type": "application/json"}, b"[]", 400),
            ({"Content-Type": "application/json"}, b'{"message":"a","message":"b"}', 400),
            ({"Content-Type": "application/json"}, b'{"message":NaN}', 400),
        ]
        for headers, body, expected in cases:
            with self.subTest(headers=headers, body=body):
                status = self._request(
                    "POST",
                    "/api/chat",
                    raw_body=body,
                    cookie=cookie,
                    headers=headers,
                )[0]
                self.assertEqual(status, expected)

    def test_request_message_and_response_limits_are_enforced(self) -> None:
        cookie = self._new_cookie()
        long_message = "x" * 8_001
        status = self._request(
            "POST",
            "/api/chat",
            payload={"message": long_message},
            cookie=cookie,
        )[0]
        self.assertEqual(status, 413)
        oversized = b"{" + b" " * DEFAULT_MAX_REQUEST_BYTES + b"}"
        status = self._request(
            "POST",
            "/api/clear",
            raw_body=oversized,
            cookie=cookie,
            headers={"Content-Type": "application/json"},
        )[0]
        self.assertEqual(status, 413)

        self._restart(max_response_bytes=4096)
        self.plans.append([_text_message("z" * 20_000)])
        cookie = self._new_cookie()
        self._post_chat(cookie, "respuesta larga")
        status, _, body, _ = self._poll_until_http_complete(cookie)
        self.assertEqual(status, 500)
        self.assertLessEqual(len(body), 4096)
        self.assertIn("excede el límite", body.decode("utf-8"))

    def test_secrets_do_not_appear_in_html_json_log_or_safe_errors(self) -> None:
        self._stop()
        secret = "GEMINI-KEY-DO-NOT-LEAK-123"
        log_path = PROJECT_DIRECTORY / "runtime" / f".web-test-{uuid4().hex}.jsonl"
        logger = None
        try:
            logger = MCPProtocolLogger(log_path)
            self.logger = logger
            self.client_builder = lambda: _ExplodingClient(secret)
            self._start(protocol_logger=logger)
            cookie = self._new_cookie()
            self._post_chat(cookie, "provoca error seguro")
            result = self._wait_for(cookie, "idle")
            combined = (
                self._request("GET", "/")[2]
                + json.dumps(result, ensure_ascii=False).encode("utf-8")
                + log_path.read_bytes()
            ).decode("utf-8")
            self.assertNotIn(secret, combined)
            self.assertNotIn("Traceback", combined)
            self.assertIn("failed safely", combined)
            self._stop()
            logger.close()
            self.logger = None
        finally:
            self._stop()
            if logger is not None:
                logger.close()
            log_path.unlink(missing_ok=True)
            self.logger = None
            self.client_builder = self._scripted_client
            self._start()

    def test_application_close_stops_manager_once_and_is_idempotent(self) -> None:
        app = self.application
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = None
        self.assertTrue(app.close())
        self.assertEqual(self.manager.stop_count, 1)
        self.assertTrue(app.close())
        self.assertEqual(self.manager.stop_count, 1)
        self.application = None

    def test_close_rejects_a_pending_mutation_before_stopping_manager(self) -> None:
        self.plans.append(
            [
                _tool_message(
                    _tool_call("shutdown-order", "pharmacy__create_order", {"sku": "A"})
                ),
                _text_message("No debe invocarse."),
            ]
        )
        cookie = self._new_cookie()
        self._post_chat(cookie, "orden pendiente")
        self._wait_for(cookie, "awaiting_confirmation")
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = None
        self.assertTrue(self.application.close())
        self.assertEqual(self.manager.invocations, [])
        self.assertEqual(self.manager.stop_count, 1)
        self.application = None

    def _scripted_client(self):
        plan = self.plans.pop(0) if self.plans else []
        client = _ScriptedClient(plan)
        self.clients.append(client)
        return client

    def _start(
        self,
        *,
        protocol_logger=None,
        confirmation_timeout_seconds=1.0,
        max_response_bytes=262_144,
    ) -> None:
        self.application = PharmacyWebApplication(
            self.manager,
            lambda: self.client_builder(),
            provider_name="gemini",
            model_name="gemini-simulated",
            protocol_logger=protocol_logger,
            confirmation_timeout_seconds=confirmation_timeout_seconds,
            session_idle_seconds=60,
            max_response_bytes=max_response_bytes,
        )
        self.server = PharmacyWebServer(("127.0.0.1", 0), self.application)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _stop(self) -> None:
        server = getattr(self, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
            self.thread.join(timeout=2)
            self.server = None
        application = getattr(self, "application", None)
        if application is not None:
            application.close()
            self.application = None

    def _restart(self, **kwargs) -> None:
        self._stop()
        self.manager = _FakeManager()
        self.clients.clear()
        self._start(**kwargs)

    def _request(
        self,
        method,
        path,
        *,
        payload=None,
        raw_body=None,
        cookie=None,
        headers=None,
    ):
        request_headers = dict(headers or {})
        body = raw_body
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        if cookie is not None:
            request_headers["Cookie"] = cookie
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            response_body = response.read()
            response_headers = {key.casefold(): value for key, value in response.getheaders()}
            set_cookie = response_headers.get("set-cookie")
            new_cookie = set_cookie.split(";", 1)[0] if set_cookie else None
            return response.status, response_headers, response_body, new_cookie
        finally:
            connection.close()

    def _new_cookie(self):
        status, _, _, cookie = self._request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertIsNotNone(cookie)
        return cookie

    def _post_chat(self, cookie, message):
        status, _, body, _ = self._request(
            "POST",
            "/api/chat",
            payload={"message": message},
            cookie=cookie,
        )
        self.assertEqual(status, 202, body)

    def _chat(self, cookie, message):
        self._post_chat(cookie, message)
        return self._wait_for(cookie, "idle")

    def _wait_for(self, cookie, state, *, timeout=2.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            status, _, body, _ = self._request("GET", "/api/chat", cookie=cookie)
            self.assertEqual(status, 200, body)
            last = json.loads(body)
            if last["state"] == state:
                return last
            time.sleep(0.01)
        self.fail(f"Session never reached {state}; last={last}")

    def _wait_for_new_confirmation(self, cookie, previous_id):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = self._wait_for(cookie, "awaiting_confirmation")
            pending = state["pending_confirmation"]
            if pending["id"] != previous_id:
                return pending
            time.sleep(0.01)
        self.fail("A second distinct confirmation was not created")

    def _poll_until_http_complete(self, cookie):
        deadline = time.monotonic() + 2
        last = None
        while time.monotonic() < deadline:
            last = self._request("GET", "/api/chat", cookie=cookie)
            if last[0] != 200:
                return last
            if json.loads(last[2])["state"] == "idle":
                return last
            time.sleep(0.01)
        self.fail(f"No completed HTTP response; last={last}")


class _FakeManager:
    def __init__(self) -> None:
        self.invocations = []
        self.stop_count = 0
        self.tools = (
            RegisteredTool(
                namespaced_name="pharmacy__check_stock",
                server_name="pharmacy",
                tool_name="check_stock",
                description="Check simulated stock.",
                input_schema={"type": "object"},
            ),
            RegisteredTool(
                namespaced_name="pharmacy__create_order",
                server_name="pharmacy",
                tool_name="create_order",
                description="Create a simulated order.",
                input_schema={"type": "object"},
            ),
        )
        self.by_name = {tool.namespaced_name: tool for tool in self.tools}

    def list_servers(self):
        return (
            ServerSummary(
                name="pharmacy",
                transport="stdio",
                enabled=True,
                status="ready",
                process_id=99999,
            ),
        )

    def list_tools(self):
        return self.tools

    def resolve_tool(self, name):
        return self.by_name[name]

    def requires_confirmation(self, name):
        return name == "pharmacy__create_order"

    def invoke_tool(self, name, arguments, *, allow_mutation=False):
        self.invocations.append((name, deepcopy(arguments), allow_mutation))
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"ok": True, "tool": name}),
                }
            ],
            "isError": False,
        }

    def stop_all(self):
        self.stop_count += 1


class _ScriptedClient:
    provider_name = "gemini"
    model_name = "gemini-simulated"
    max_tool_rounds = 8

    def __init__(self, plan) -> None:
        self.plan = list(plan)
        self.requests = []

    def prepare_tools(self, tools):
        return [{"name": tool.namespaced_name} for tool in tools]

    def create_message(self, *, messages, tools=None, system=None):
        self.requests.append(deepcopy(messages))
        if self.plan:
            return self.plan.pop(0)
        user_messages = [
            message["content"]
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        ]
        return _text_message(f"Respuesta simulada a: {user_messages[-1]}")


class _BlockingClient(_ScriptedClient):
    def __init__(self, release) -> None:
        super().__init__([])
        self.release = release

    def create_message(self, *, messages, tools=None, system=None):
        self.requests.append(deepcopy(messages))
        if not self.release.wait(timeout=2):
            raise RuntimeError("test release timed out")
        return _text_message("Continuó.")


class _ExplodingClient(_ScriptedClient):
    def __init__(self, secret) -> None:
        super().__init__([])
        self.secret = secret

    def create_message(self, *, messages, tools=None, system=None):
        raise RuntimeError(f"provider failed with {self.secret}")


class _GeminiQueueTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected simulated Gemini request")
        return self.responses.pop(0)


def _text_message(text):
    return GeminiMessage(
        message_id=f"message-{uuid4().hex}",
        content=({"type": "text", "text": text},),
        stop_reason="end_turn",
        finish_reason="STOP",
        candidate_count=1,
    )


def _tool_message(*calls):
    return GeminiMessage(
        message_id=f"message-{uuid4().hex}",
        content=tuple(calls),
        stop_reason="tool_use",
        finish_reason="STOP",
        candidate_count=1,
        function_call_count=len(calls),
    )


def _tool_call(identifier, name, arguments):
    return {
        "type": "tool_use",
        "id": identifier,
        "name": name,
        "input": arguments,
    }


def _gemini_text_response(text):
    return HTTPResponse(
        status=200,
        headers={"x-goog-request-id": "simulated-request"},
        body=json.dumps(
            {
                "responseId": "simulated-response",
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": text}]},
                        "finishReason": "STOP",
                    }
                ],
            }
        ).encode("utf-8"),
    )


if __name__ == "__main__":
    unittest.main()
