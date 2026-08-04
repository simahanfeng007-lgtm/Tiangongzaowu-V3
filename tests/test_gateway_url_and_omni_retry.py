from __future__ import annotations

import contextlib
import http.server
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Iterator
from unittest import mock

from total_gateway.bootstrap import GatewayConfig, GatewayConfigurationError
from total_gateway.gateway_url import GatewayUrlError, normalize_gateway_url
from v3.jineng.omni_grant_client import OmniGrantClientError, issue_omni_grant
from v3.run_context import RunContext, bind_run_context


class _ScriptedGrantHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bodies: list[bytes] = []
    reject = False

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        type(self).bodies.append(body)
        if not type(self).reject and len(type(self).bodies) == 1:
            # Simulate the exact ambiguous failure that makes retries dangerous:
            # the authority received the complete request but the response was
            # lost before the client observed any HTTP status.
            self.close_connection = True
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return

        if type(self).reject:
            status = 403
            payload = {"status": "ERROR", "reason_code": "omni.policy.rejected"}
        else:
            status = 200
            payload = {
                "status": "OK",
                "grant": {"grant_id": "grant-replayed-safely"},
                "runtime": {"gateway_epoch": 7},
            }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)


@contextlib.contextmanager
def _grant_server(*, reject: bool = False) -> Iterator[tuple[int, list[bytes]]]:
    handler = type(
        "PerTestGrantHandler",
        (_ScriptedGrantHandler,),
        {"bodies": [], "reject": reject},
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1]), handler.bodies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class GatewayUrlContractTests(unittest.TestCase):
    def test_loopback_origin_is_normalized_and_non_default_test_port_is_supported(self) -> None:
        self.assertEqual(
            normalize_gateway_url("http://localhost:43187/"),
            "http://127.0.0.1:43187",
        )
        with tempfile.TemporaryDirectory() as temporary:
            config = GatewayConfig.from_environment(
                {
                    "TIANGONG_GATEWAY_ENVIRONMENT": "test",
                    "TIANGONG_GATEWAY_PORT": "43187",
                    "TIANGONG_GATEWAY_URL": "http://localhost:43187",
                    "TIANGONG_GATEWAY_STATE_ROOT": str(Path(temporary) / "state"),
                }
            )
        self.assertEqual(config.port, 43187)
        self.assertEqual(config.gateway_url, "http://127.0.0.1:43187")

    def test_unsafe_or_ambiguous_gateway_origins_fail_closed(self) -> None:
        unsafe = (
            "https://127.0.0.1:7184",
            "http://example.com:7184",
            "http://user:password@127.0.0.1:7184",
            "http://127.0.0.1:7184/api",
            "http://127.0.0.1:7184?redirect=evil",
            "http://127.0.0.1",
        )
        for value in unsafe:
            with self.subTest(value=value), self.assertRaises(GatewayUrlError):
                normalize_gateway_url(value)

    def test_listener_port_and_callback_port_cannot_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            derived = GatewayConfig(
                environment="test",
                port=43187,
                state_root=Path(temporary) / "derived-state",
            )
            self.assertEqual(derived.gateway_url, "http://127.0.0.1:43187")
            with self.assertRaises(ValueError):
                GatewayConfig(
                    environment="test",
                    port=43187,
                    gateway_url="http://127.0.0.1:43188",
                    state_root=Path(temporary) / "state",
                )
            with self.assertRaises(GatewayConfigurationError):
                GatewayConfig.from_environment(
                    {
                        "TIANGONG_GATEWAY_ENVIRONMENT": "test",
                        "TIANGONG_GATEWAY_PORT": "43187",
                        "TIANGONG_GATEWAY_URL": "http://127.0.0.1:43188",
                        "TIANGONG_GATEWAY_STATE_ROOT": str(Path(temporary) / "state"),
                    }
                )


class OmniGrantRetryContractTests(unittest.TestCase):
    @staticmethod
    def _context(port: int) -> RunContext:
        return RunContext(
            request_id="request-retry-contract",
            run_id="run-retry-contract",
            generation=4,
            principal_scope_hash="a" * 64,
            outer_execution_ticket_id="outer-ticket-retry-contract",
            workspace_id="workspace-retry-contract",
            gateway_url=f"http://localhost:{port}",
        )

    def test_lost_response_retries_byte_identical_request_and_call_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, _grant_server() as (port, bodies):
            workspace = Path(temporary).resolve()
            with (
                mock.patch.dict(
                    os.environ,
                    {"TIANGONG_BACKEND_INTERNAL_TOKEN": "b" * 48},
                    clear=True,
                ),
                bind_run_context(self._context(port)),
            ):
                result = issue_omni_grant(
                    {"action": "system.health", "target": "", "args": {}},
                    workspace=workspace,
                    call_id="one-logical-tool-occurrence",
                    timeout_seconds=2,
                )

        self.assertEqual(result["grant"]["grant_id"], "grant-replayed-safely")
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        first = json.loads(bodies[0])
        second = json.loads(bodies[1])
        self.assertEqual(first["call_id"], second["call_id"])
        self.assertRegex(first["call_id"], r"^toolcall_[0-9a-f]{64}$")

    def test_received_policy_rejection_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, _grant_server(reject=True) as (port, bodies):
            workspace = Path(temporary).resolve()
            with (
                mock.patch.dict(
                    os.environ,
                    {"TIANGONG_BACKEND_INTERNAL_TOKEN": "b" * 48},
                    clear=True,
                ),
                bind_run_context(self._context(port)),
                self.assertRaisesRegex(OmniGrantClientError, "omni.policy.rejected"),
            ):
                issue_omni_grant(
                    {"action": "system.health", "target": "", "args": {}},
                    workspace=workspace,
                    call_id="rejected-occurrence",
                    timeout_seconds=2,
                )
        self.assertEqual(len(bodies), 1)


if __name__ == "__main__":
    unittest.main()
