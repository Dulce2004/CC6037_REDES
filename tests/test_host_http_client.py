"""Tests for the host's manual MCP Streamable HTTP client."""

from __future__ import annotations

import io
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    HTTPMCPClient,
    HTTPServerConfig,
    MCPHTTPRequest,
    MCPHTTPResponse,
    MCPProtocolError,
    MCPProtocolLogger,
    MCPTransportError,
)
from pharmacy_mcp.server.http import (  # noqa: E402
    PharmacyHTTPSettings,
    create_http_server,
)


class HTTPMCPClientLoopbackTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT_DIRECTORY / "runtime"
        runtime.mkdir(exist_ok=True)
        unique = uuid4().hex
        self.database_path = runtime / f"http-client-{unique}.sqlite3"
        self.log_path = runtime / f"http-client-{unique}.jsonl"
        self.token = "client-loopback-secret"
        self.server = create_http_server(
            PharmacyHTTPSettings(
                host="127.0.0.1",
                port=0,
                token=self.token,
                database_path=self.database_path,
            ),
            diagnostic_stream=io.StringIO(),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name=f"http-client-server-{unique}",
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.logger = MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=io.StringIO(),
        )
        self.client = HTTPMCPClient(
            HTTPServerConfig(
                name="pharmacy-remote",
                url=f"http://{host}:{port}/mcp",
                token=self.token,
                token_env="PHARMACY_MCP_HTTP_TOKEN",
                mutable_tools=frozenset({"create_order"}),
            ),
            protocol_logger=self.logger,
        )

    def tearDown(self) -> None:
        try:
            if self.client.is_running:
                self.client.stop()
        finally:
            self.logger.close()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.assertFalse(self.thread.is_alive())
            self.log_path.unlink(missing_ok=True)
            for suffix in ("", "-shm", "-wal"):
                Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    def test_real_handshake_tool_call_logging_and_delete(self) -> None:
        self.client.start()
        self.assertTrue(self.client.is_ready)
        self.assertIsNone(self.client.process_id)
        self.assertEqual(len(self.client.list_tools()), 7)

        stock = self.client.call_tool(
            "check_stock",
            {"sku": "MED-ANA-001", "branch_id": "zona-5"},
        )
        self.assertEqual(stock["structuredContent"]["stock"][0]["quantity"], 25)
        self.client.stop()
        self.assertFalse(self.client.is_running)
        self.assertEqual(self.server.sessions.active_count, 0)

        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertIn('"transport":"http"', log_text)
        self.assertNotIn(self.token, log_text)

    def test_reproducible_two_session_loopback_demonstration(self) -> None:
        second = HTTPMCPClient(
            self.client.config,
            protocol_logger=self.logger,
        )
        try:
            self.client.start()
            second.start()
            self.assertEqual(self.server.sessions.active_count, 2)
            self.assertEqual(len(self.client.list_tools()), 7)
            stock = self.client.call_tool(
                "check_stock",
                {"sku": "MED-ANA-001", "branch_id": "zona-5"},
            )
            self.assertEqual(
                stock["structuredContent"]["stock"][0]["quantity"],
                25,
            )

            self.client.stop()
            self.assertEqual(self.server.sessions.active_count, 1)
            self.assertTrue(second.is_ready)
            second.stop()
            self.assertEqual(self.server.sessions.active_count, 0)
        finally:
            if second.is_running:
                second.stop()


class HTTPMCPClientProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT_DIRECTORY / "runtime"
        runtime.mkdir(exist_ok=True)
        self.log_path = runtime / f"http-protocol-{uuid4().hex}.jsonl"
        self.logger = MCPProtocolLogger(
            self.log_path,
            diagnostic_stream=io.StringIO(),
        )

    def tearDown(self) -> None:
        self.logger.close()
        self.log_path.unlink(missing_ok=True)

    def config(
        self,
        *,
        maximum: int = 4_096,
        request_maximum: int = 4_096,
    ) -> HTTPServerConfig:
        return HTTPServerConfig(
            name="pharmacy-remote",
            url="https://pharmacy.example.test/mcp",
            token="transport-secret",
            token_env="PHARMACY_MCP_HTTP_TOKEN",
            max_request_bytes=request_maximum,
            max_response_bytes=maximum,
        )

    @staticmethod
    def initialize_response(
        *,
        content_type: str = "application/json",
        session_id: str = "session-secret-value",
        body_truncated: bool = False,
    ) -> MCPHTTPResponse:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "remote", "version": "1"},
                },
            }
        ).encode()
        return MCPHTTPResponse(
            status=200,
            headers=MappingProxyType(
                {"Content-Type": content_type, "MCP-Session-Id": session_id}
            ),
            body=body,
            body_truncated=body_truncated,
        )

    def test_headers_ids_and_repr_do_not_disclose_credentials_or_session(self) -> None:
        requests: list[MCPHTTPRequest] = []

        def transport(request: MCPHTTPRequest) -> MCPHTTPResponse:
            requests.append(request)
            if len(requests) == 1:
                return self.initialize_response()
            if len(requests) == 2:
                return MCPHTTPResponse(status=202, headers={}, body=b"")
            return MCPHTTPResponse(status=204, headers={}, body=b"")

        client = HTTPMCPClient(
            self.config(),
            protocol_logger=self.logger,
            transport=transport,
        )
        client.start()
        client.stop()

        self.assertEqual([request.method for request in requests], ["POST", "POST", "DELETE"])
        self.assertNotIn("MCP-Session-Id", requests[0].headers)
        self.assertNotIn("MCP-Protocol-Version", requests[0].headers)
        self.assertEqual(
            requests[1].headers["MCP-Protocol-Version"],
            "2025-11-25",
        )
        self.assertEqual(requests[1].headers["MCP-Session-Id"], "session-secret-value")
        rendered = repr(requests[1]) + repr(client.config)
        self.assertNotIn("transport-secret", rendered)
        self.assertNotIn("session-secret-value", rendered)
        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("transport-secret", log_text)
        self.assertNotIn("session-secret-value", log_text)

    def test_sse_and_oversized_responses_fail_explicitly(self) -> None:
        for response, error_type, fragment in (
            (
                self.initialize_response(content_type="text/event-stream"),
                MCPProtocolError,
                "SSE",
            ),
            (
                self.initialize_response(body_truncated=True),
                MCPTransportError,
                "size limit",
            ),
        ):
            with self.subTest(fragment=fragment):
                client = HTTPMCPClient(
                    self.config(),
                    protocol_logger=self.logger,
                    transport=lambda request, selected=response: selected,
                )
                with self.assertRaisesRegex(error_type, fragment):
                    client.start()

        client = HTTPMCPClient(
            self.config(),
            protocol_logger=self.logger,
            transport=lambda request: self.initialize_response(
                content_type="application/json; charset=iso-8859-1"
            ),
        )
        with self.assertRaisesRegex(MCPProtocolError, "non-UTF-8"):
            client.start()

    def test_timeout_and_safe_http_status_errors_are_reported_without_body(self) -> None:
        def timeout(request: MCPHTTPRequest) -> MCPHTTPResponse:
            raise socket.timeout("transport-secret should stay hidden")

        client = HTTPMCPClient(
            self.config(),
            protocol_logger=self.logger,
            transport=timeout,
        )
        with self.assertRaisesRegex(MCPTransportError, "Timed out") as timeout_error:
            client.start()
        self.assertNotIn("transport-secret", str(timeout_error.exception))

        for status in (400, 401, 403, 404, 405, 408, 429, 500, 503):
            with self.subTest(status=status):
                response = MCPHTTPResponse(
                    status=status,
                    headers={"Content-Type": "application/json"},
                    body=b'{"error":"sensitive remote body"}',
                )
                client = HTTPMCPClient(
                    self.config(),
                    protocol_logger=self.logger,
                    transport=lambda request, selected=response: selected,
                )
                with self.assertRaises(MCPTransportError) as caught:
                    client.start()
                self.assertNotIn("sensitive remote body", str(caught.exception))

    def test_outbound_request_body_is_bounded_without_transport_call(self) -> None:
        requests: list[MCPHTTPRequest] = []

        def transport(request: MCPHTTPRequest) -> MCPHTTPResponse:
            requests.append(request)
            if len(requests) == 1:
                return self.initialize_response()
            if len(requests) == 2:
                return MCPHTTPResponse(status=202, headers={}, body=b"")
            return MCPHTTPResponse(status=204, headers={}, body=b"")

        client = HTTPMCPClient(
            self.config(request_maximum=1_024),
            protocol_logger=self.logger,
            transport=transport,
        )
        client.start()
        with self.assertRaisesRegex(MCPTransportError, "request exceeded"):
            client.call_tool("search_medications", {"query": "x" * 2_000})
        self.assertEqual(len(requests), 2)
        client.stop()

    def test_mutable_call_is_never_retried_after_http_failure(self) -> None:
        requests: list[MCPHTTPRequest] = []

        def transport(request: MCPHTTPRequest) -> MCPHTTPResponse:
            requests.append(request)
            if len(requests) == 1:
                return self.initialize_response()
            if len(requests) == 2:
                return MCPHTTPResponse(status=202, headers={}, body=b"")
            if request.method == "DELETE":
                return MCPHTTPResponse(status=204, headers={}, body=b"")
            return MCPHTTPResponse(
                status=503,
                headers={"Content-Type": "application/json"},
                body=b'{"error":"unavailable"}',
            )

        client = HTTPMCPClient(
            self.config(),
            protocol_logger=self.logger,
            transport=transport,
        )
        client.start()
        with self.assertRaisesRegex(MCPTransportError, "temporarily unavailable"):
            client.call_tool(
                "create_order",
                {
                    "branch_id": "zona-5",
                    "items": [{"sku": "MED-ANA-001", "quantity": 1}],
                },
            )
        self.assertEqual(len(requests), 3)
        client.stop()


if __name__ == "__main__":
    unittest.main()
