"""Tests for the interactive chat command without network or child processes."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = PROJECT_DIRECTORY / "src"
sys.path.insert(0, str(SOURCE_DIRECTORY))

from pharmacy_mcp.host import (  # noqa: E402
    HTTPResponse,
    RegisteredTool,
    ServerStartFailure,
    ServerSummary,
)
from pharmacy_mcp.host.cli import build_parser, main  # noqa: E402


class HostChatCliTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = PROJECT_DIRECTORY / "runtime"
        runtime.mkdir(exist_ok=True)
        unique = uuid4().hex
        self.config_path = runtime / f"chat-cli-{unique}.json"
        self.log_path = runtime / f"chat-cli-{unique}.jsonl"
        self.config_path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "name": name,
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": ["-V"],
                            "cwd": "..",
                            "env": {},
                        }
                        for name in ("pharmacy", "git", "filesystem")
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.addCleanup(self.config_path.unlink, missing_ok=True)
        self.addCleanup(self.log_path.unlink, missing_ok=True)
        self.credential = f"unit-{uuid4().hex}"
        self.environment = {
            "LLM_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": self.credential,
            "ANTHROPIC_MODEL": "test-model",
        }

    def run_chat(self, input_text, responses=(), *, manager=None, environment=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        transport = _QueueTransport(responses)
        with patch("pharmacy_mcp.host.cli.MCPServerManager") as manager_class:
            fake_manager = manager or _CliManager()
            manager_class.return_value = fake_manager
            exit_code = main(
                [
                    "--config",
                    str(self.config_path),
                    "--log-file",
                    str(self.log_path),
                    "chat",
                ],
                stdin=(
                    io.StringIO(input_text)
                    if isinstance(input_text, str)
                    else input_text
                ),
                stdout=stdout,
                stderr=stderr,
                environ=self.environment if environment is None else environment,
                anthropic_transport=transport,
                gemini_transport=transport,
            )
        return exit_code, stdout.getvalue(), stderr.getvalue(), transport, fake_manager

    def test_chat_is_registered_and_missing_credentials_fail_before_server_start(self) -> None:
        parsed = build_parser().parse_args(["chat"])
        self.assertEqual(parsed.command, "chat")

        for environment, expected in (
            ({"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "test-model"}, "API_KEY"),
            ({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "present"}, "MODEL"),
        ):
            with self.subTest(expected=expected):
                with patch("pharmacy_mcp.host.cli.MCPServerManager") as manager_class:
                    code = main(
                        ["--config", str(self.config_path), "chat"],
                        stdout=io.StringIO(),
                        stderr=(errors := io.StringIO()),
                        environ=environment,
                    )
                self.assertEqual(code, 1)
                self.assertIn(expected, errors.getvalue())
                manager_class.assert_not_called()

    def test_conversation_preserves_context_and_closes_manager(self) -> None:
        responses = [_response("Primera respuesta"), _response("Segunda respuesta")]
        code, output, errors, transport, manager = self.run_chat(
            "¿Quién fue Alan Turing?\n¿En qué fecha nació?\n/exit\n",
            responses,
        )
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("Claude> Primera respuesta", output)
        self.assertIn("Claude> Segunda respuesta", output)
        second_payload = json.loads(transport.requests[1].body)
        self.assertEqual(second_payload["messages"][0]["content"], "¿Quién fue Alan Turing?")
        self.assertEqual(second_payload["messages"][1]["role"], "assistant")
        self.assertEqual(second_payload["messages"][2]["content"], "¿En qué fecha nació?")
        self.assertTrue(manager.stopped)
        self.assertNotIn(self.credential, output + errors)

    def test_help_servers_tools_and_clear_do_not_break_session(self) -> None:
        manager = _CliManager(
            tools=(
                RegisteredTool(
                    namespaced_name="pharmacy__check_stock",
                    server_name="pharmacy",
                    tool_name="check_stock",
                    description="Read stock.",
                    input_schema={"type": "object"},
                ),
            )
        )
        code, output, errors, transport, _ = self.run_chat(
            "/help\n/provider\n/provider gemini\n/servers\n/tools\nfirst\n/clear\nsecond\n/exit\n",
            [_response("one"), _response("two")],
            manager=manager,
        )
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("/clear", output)
        self.assertIn("Proveedor: anthropic", output)
        self.assertIn("no puede cambiarse", output)
        self.assertIn("pharmacy__check_stock", output)
        second_payload = json.loads(transport.requests[1].body)
        self.assertEqual(
            second_payload["messages"],
            [{"role": "user", "content": "second"}],
        )

    def test_blank_input_eof_and_keyboard_interrupt_exit_cleanly(self) -> None:
        code, output, errors, transport, manager = self.run_chat("\n")
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(transport.requests, [])
        self.assertTrue(manager.stopped)

        code, _, errors, _, manager = self.run_chat(_InterruptingInput())
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertTrue(manager.stopped)

    def test_gemini_is_default_and_does_not_require_anthropic_variables(self) -> None:
        environment = {"GEMINI_API_KEY": self.credential}
        code, output, errors, transport, manager = self.run_chat(
            "hola\n/exit\n",
            [_gemini_response("respuesta")],
            environment=environment,
        )
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertIn("Proveedor: gemini", output)
        self.assertIn("Modelo: gemini-3.5-flash-lite", output)
        self.assertIn("Gemini> respuesta", output)
        self.assertEqual(len(transport.requests), 1)
        self.assertTrue(manager.stopped)
        self.assertNotIn(self.credential, output + errors)

    def test_selected_provider_requires_only_its_own_configuration(self) -> None:
        cases = (
            ({"LLM_PROVIDER": "gemini"}, "GEMINI_API_KEY"),
            ({"LLM_PROVIDER": "invalid", "GEMINI_API_KEY": "present"}, "LLM_PROVIDER"),
        )
        for environment, expected in cases:
            with self.subTest(environment=environment):
                with patch("pharmacy_mcp.host.cli.MCPServerManager") as manager_class:
                    code = main(
                        ["--config", str(self.config_path), "chat"],
                        stdout=io.StringIO(),
                        stderr=(errors := io.StringIO()),
                        environ=environment,
                    )
                self.assertEqual(code, 1)
                self.assertIn(expected, errors.getvalue())
                manager_class.assert_not_called()

    def test_api_error_is_safe_and_next_eof_still_closes(self) -> None:
        response = HTTPResponse(
            status=429,
            headers={"request-id": "req-rate"},
            body=b'{"error":{"message":"details"}}',
        )
        code, output, errors, _, manager = self.run_chat("hello\n", [response])
        self.assertEqual(code, 0)
        self.assertIn("rate or spending", errors)
        self.assertNotIn("details", errors)
        self.assertNotIn(self.credential, output + errors)
        self.assertTrue(manager.stopped)

    def test_partial_server_failure_is_reported_while_chat_remains_available(self) -> None:
        manager = _CliManager(
            failures=(
                ServerStartFailure(
                    server_name="filesystem",
                    error="MCPTransportError: server startup failed",
                ),
            )
        )
        code, output, errors, _, manager = self.run_chat(
            "hello\n/exit\n",
            [_response("hola")],
            manager=manager,
        )
        self.assertEqual(code, 0)
        self.assertIn("Claude> hola", output)
        self.assertIn("filesystem", errors)
        self.assertIn("unavailable", errors)
        self.assertTrue(manager.stopped)


class _QueueTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected network call")
        return self.responses.pop(0)


class _CliManager:
    def __init__(self, *, tools=(), failures=()) -> None:
        self.tools = tuple(tools)
        self.failures = tuple(failures)
        self.stopped = False

    def start_available(self):
        return self.failures

    def list_tools(self):
        return self.tools

    def list_servers(self):
        return tuple(
            ServerSummary(
                name=name,
                transport="stdio",
                enabled=True,
                status="error" if name == "filesystem" and self.failures else "ready",
                process_id=None,
            )
            for name in ("pharmacy", "git", "filesystem")
        )

    def stop_all(self):
        self.stopped = True


class _InterruptingInput:
    def readline(self):
        raise KeyboardInterrupt


def _response(text: str) -> HTTPResponse:
    return HTTPResponse(
        status=200,
        headers={"request-id": "req-test"},
        body=json.dumps(
            {
                "id": "msg-test",
                "type": "message",
                "role": "assistant",
                "model": "test-model",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode(),
    )


def _gemini_response(text: str) -> HTTPResponse:
    return HTTPResponse(
        status=200,
        headers={"x-goog-request-id": "req-test"},
        body=json.dumps(
            {
                "responseId": "response-test",
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": text}],
                        },
                        "finishReason": "STOP",
                    }
                ],
            }
        ).encode(),
    )


if __name__ == "__main__":
    unittest.main()
