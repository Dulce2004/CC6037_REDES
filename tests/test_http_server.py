"""Real loopback tests for the manual Pharmacy Streamable HTTP server."""

from __future__ import annotations

import http.client
import io
import json
import sys
import threading
import unittest
from pathlib import Path
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.server.http import (  # noqa: E402
    MCP_ENDPOINT,
    PROTOCOL_HEADER,
    SESSION_HEADER,
    PharmacyHTTPConfigurationError,
    PharmacyHTTPSettings,
    create_http_server,
)

PROTOCOL_VERSION = "2025-11-25"
TOKEN = "http-test-token"
ORIGIN = "https://client.example.test"
TOOL_NAMES = [
    "assess_symptoms",
    "search_medications",
    "get_medication_details",
    "check_interactions",
    "check_stock",
    "create_order",
    "get_order_status",
]


class PharmacyHTTPServerTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT_DIRECTORY / "runtime"
        runtime.mkdir(exist_ok=True)
        self.database_path = runtime / f"http-test-{uuid4().hex}.sqlite3"
        self.clock_value = 1_000.0
        settings = PharmacyHTTPSettings(
            host="127.0.0.1",
            port=0,
            token=TOKEN,
            allowed_origins=(ORIGIN,),
            max_request_bytes=8_192,
            request_timeout_seconds=2,
            max_sessions=4,
            session_ttl_seconds=30,
            database_path=self.database_path,
        )
        self.server = create_http_server(
            settings,
            diagnostic_stream=io.StringIO(),
            clock=lambda: self.clock_value,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name=f"pharmacy-http-test-{uuid4().hex}",
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive(), "HTTP server thread did not stop")
        for suffix in ("", "-shm", "-wal"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    def exchange(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(*self.address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def post(
        self,
        value: object | bytes,
        *,
        session_id: str | None = None,
        protocol: str | None = PROTOCOL_VERSION,
        token: str | None = TOKEN,
        origin: str | None = None,
        content_type: str = "application/json; charset=utf-8",
        accept: str = "application/json, text/event-stream",
    ) -> tuple[int, dict[str, str], bytes]:
        body = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        headers = {"Content-Type": content_type, "Accept": accept}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if session_id is not None:
            headers[SESSION_HEADER] = session_id
        if protocol is not None:
            headers[PROTOCOL_HEADER] = protocol
        if origin is not None:
            headers["Origin"] = origin
        return self.exchange("POST", MCP_ENDPOINT, body, headers)

    def initialize(self, *, origin: str | None = None) -> str:
        status, headers, body = self.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "HTTP tests", "version": "1.0"},
                },
            },
            protocol=None,
            origin=origin,
        )
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertEqual(payload["result"]["protocolVersion"], PROTOCOL_VERSION)
        session_id = headers.get(SESSION_HEADER)
        self.assertIsInstance(session_id, str)
        self.assertGreaterEqual(len(session_id), 32)
        return session_id

    def initialize_ready(self) -> str:
        session_id = self.initialize()
        status, _, body = self.post(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            session_id=session_id,
        )
        self.assertEqual((status, body), (202, b""))
        return session_id

    def request(
        self,
        session_id: str,
        method: str,
        params: dict[str, object],
        request_id: int,
    ) -> dict[str, object]:
        status, _, body = self.post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def call_tool(
        self,
        session_id: str,
        name: str,
        arguments: dict[str, object],
        request_id: int,
    ) -> dict[str, object]:
        return self.request(
            session_id,
            "tools/call",
            {"name": name, "arguments": arguments},
            request_id,
        )

    def test_start_health_initialize_notification_list_and_call(self) -> None:
        status, headers, body = self.exchange("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(self.server.sessions.active_count, 0)

        session_id = self.initialize_ready()
        listed = self.request(session_id, "tools/list", {}, 2)
        self.assertEqual(
            [tool["name"] for tool in listed["result"]["tools"]],
            TOOL_NAMES,
        )
        searched = self.call_tool(
            session_id,
            "search_medications",
            {"query": "paracetamol"},
            3,
        )
        self.assertEqual(
            searched["result"]["structuredContent"]["medications"][0]["sku"],
            "MED-ANA-001",
        )

    def test_all_seven_tools_form_one_http_workflow(self) -> None:
        session_id = self.initialize_ready()
        listed = self.request(session_id, "tools/list", {}, 2)
        self.assertEqual(len(listed["result"]["tools"]), 7)

        calls = [
            ("assess_symptoms", {"symptoms": "Tengo fiebre"}),
            ("search_medications", {"query": "paracetamol"}),
            ("get_medication_details", {"sku": "MED-ANA-001"}),
            (
                "check_interactions",
                {"medication_sku": "MED-ANA-001", "current_medications": []},
            ),
            ("check_stock", {"sku": "MED-ANA-001", "branch_id": "zona-5"}),
        ]
        for request_id, (name, arguments) in enumerate(calls, start=3):
            with self.subTest(tool=name):
                response = self.call_tool(session_id, name, arguments, request_id)
                self.assertFalse(response["result"].get("isError", False))

        created = self.call_tool(
            session_id,
            "create_order",
            {
                "branch_id": "zona-5",
                "items": [{"sku": "MED-ANA-001", "quantity": 1}],
            },
            8,
        )
        order = created["result"]["structuredContent"]["order"]
        status = self.call_tool(
            session_id,
            "get_order_status",
            {"order_id": order["order_id"]},
            9,
        )
        self.assertEqual(
            status["result"]["structuredContent"]["order"]["status"],
            "created",
        )
        stock = self.call_tool(
            session_id,
            "check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
            10,
        )
        self.assertEqual(
            stock["result"]["structuredContent"]["stock"][0]["quantity"],
            24,
        )

    def test_sessions_are_independent_and_delete_ends_only_one(self) -> None:
        first = self.initialize_ready()
        second = self.initialize_ready()
        self.assertNotEqual(first, second)

        status, _, body = self.exchange(
            "DELETE",
            MCP_ENDPOINT,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                SESSION_HEADER: first,
                PROTOCOL_HEADER: PROTOCOL_VERSION,
            },
        )
        self.assertEqual((status, body), (204, b""))
        status, _, _ = self.post(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
            session_id=first,
        )
        self.assertEqual(status, 404)
        response = self.request(second, "tools/list", {}, 5)
        self.assertEqual(len(response["result"]["tools"]), 7)

    def test_missing_unknown_deleted_and_expired_sessions_are_rejected(self) -> None:
        message = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        status, _, _ = self.post(message)
        self.assertEqual(status, 400)
        status, _, _ = self.post(message, session_id="unknown-session")
        self.assertEqual(status, 404)

        deleted = self.initialize_ready()
        self.exchange(
            "DELETE",
            MCP_ENDPOINT,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                SESSION_HEADER: deleted,
                PROTOCOL_HEADER: PROTOCOL_VERSION,
            },
        )
        status, _, _ = self.post(message, session_id=deleted)
        self.assertEqual(status, 404)

        expired = self.initialize_ready()
        self.clock_value += 31
        status, _, _ = self.post(message, session_id=expired)
        self.assertEqual(status, 404)

    def test_protocol_header_and_second_initialize_are_validated(self) -> None:
        session_id = self.initialize_ready()
        message = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        self.assertEqual(self.post(message, session_id=session_id, protocol=None)[0], 400)
        self.assertEqual(self.post(message, session_id=session_id, protocol="old")[0], 400)
        self.assertEqual(self.post(message, session_id=session_id)[0], 200)

        second_initialize = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "again", "version": "1"},
            },
        }
        status, _, body = self.post(second_initialize, session_id=session_id)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32600)

    def test_invalid_json_invalid_request_and_notification_semantics(self) -> None:
        status, _, body = self.post(b"{")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32700)
        status, _, body = self.post([])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32600)

        status, headers, body = self.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
            protocol=None,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32602)
        self.assertNotIn(SESSION_HEADER, headers)
        self.assertEqual(self.server.sessions.active_count, 0)

        session_id = self.initialize_ready()
        status, _, body = self.post(
            {"jsonrpc": "2.0", "method": "unknown/notification", "params": {}},
            session_id=session_id,
        )
        self.assertEqual((status, body), (202, b""))

    def test_methods_content_type_and_accept_are_strict(self) -> None:
        auth = {"Authorization": f"Bearer {TOKEN}"}
        status, headers, body = self.exchange("GET", MCP_ENDPOINT, headers=auth)
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST, GET, DELETE")
        self.assertNotIn(b"<html", body.lower())
        self.assertEqual(self.exchange("PUT", MCP_ENDPOINT, headers=auth)[0], 405)

        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        self.assertEqual(self.post(initialize, content_type="text/plain")[0], 415)
        self.assertEqual(self.post(initialize, accept="application/json")[0], 406)

    def test_origin_and_bearer_are_enforced_without_disclosure(self) -> None:
        self.assertTrue(self.initialize(origin=ORIGIN))
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        status, _, body = self.post(initialize, origin="https://evil.example.test")
        self.assertEqual(status, 403)
        self.assertNotIn(TOKEN.encode(), body)
        self.assertEqual(self.post(initialize, token=None)[0], 401)
        status, _, body = self.post(initialize, token="wrong-token")
        self.assertEqual(status, 401)
        self.assertNotIn(b"wrong-token", body)

    def test_request_size_and_utf8_are_bounded(self) -> None:
        oversized = b"{" + b" " * 8_192 + b"}"
        self.assertEqual(self.post(oversized)[0], 413)
        self.assertEqual(self.post(b"\xff")[0], 400)

    def test_session_capacity_is_bounded(self) -> None:
        sessions = [self.initialize() for _ in range(4)]
        self.assertEqual(len(set(sessions)), 4)
        initialize = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "capacity", "version": "1"},
            },
        }
        self.assertEqual(self.post(initialize, protocol=None)[0], 503)

    def test_failed_order_rolls_back_and_concurrent_orders_do_not_oversell(self) -> None:
        session_id = self.initialize_ready()
        failed = self.call_tool(
            session_id,
            "create_order",
            {
                "branch_id": "zona-5",
                "items": [
                    {"sku": "MED-ANA-001", "quantity": 2},
                    {"sku": "MED-ANT-002", "quantity": 1},
                ],
            },
            2,
        )
        self.assertTrue(failed["result"]["isError"])
        stock = self.call_tool(
            session_id,
            "check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
            3,
        )
        self.assertEqual(
            stock["result"]["structuredContent"]["stock"][0]["quantity"],
            25,
        )

        sessions = (session_id, self.initialize_ready())
        barrier = threading.Barrier(2)
        results: list[dict[str, object]] = []

        def order(active_session: str, request_id: int) -> None:
            barrier.wait(timeout=3)
            results.append(
                self.call_tool(
                    active_session,
                    "create_order",
                    {
                        "branch_id": "zona-5",
                        "items": [{"sku": "MED-ANA-001", "quantity": 20}],
                    },
                    request_id,
                )
            )

        workers = [
            threading.Thread(target=order, args=(session, index), name=f"order-{index}")
            for index, session in enumerate(sessions, start=20)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "order worker did not stop")
        self.assertEqual(len(results), 2)
        self.assertEqual(
            sum(not result["result"].get("isError", False) for result in results),
            1,
        )
        final_stock = self.call_tool(
            session_id,
            "check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
            30,
        )
        self.assertEqual(
            final_stock["result"]["structuredContent"]["stock"][0]["quantity"],
            5,
        )


class PharmacyHTTPSettingsTests(unittest.TestCase):
    def test_no_auth_is_loopback_only_without_explicit_unsafe_override(self) -> None:
        PharmacyHTTPSettings(host="localhost", token=None)
        with self.assertRaisesRegex(PharmacyHTTPConfigurationError, "Bearer token"):
            PharmacyHTTPSettings(host="0.0.0.0", token=None)
        PharmacyHTTPSettings(
            host="0.0.0.0",
            token=None,
            allow_insecure_no_auth=True,
        )

    def test_environment_configuration_never_exposes_token_in_repr(self) -> None:
        settings = PharmacyHTTPSettings.from_environ(
            {
                "HOST": "127.0.0.1",
                "PORT": "0",
                "PHARMACY_MCP_HTTP_TOKEN": "environment-secret",
                "PHARMACY_MCP_ALLOWED_ORIGINS": ORIGIN,
            }
        )
        self.assertEqual(settings.port, 0)
        self.assertEqual(settings.allowed_origins, (ORIGIN,))
        self.assertNotIn("environment-secret", repr(settings))


if __name__ == "__main__":
    unittest.main()
