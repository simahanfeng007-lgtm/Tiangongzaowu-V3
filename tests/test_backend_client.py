from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import (
    ExecutionAuthorizationError,
    ExecutionResult,
    PublicKeyDescriptor,
    TrustBundle,
    TrustScope,
    canonical_sha256,
)
from total_gateway.backend_client import (
    BACKEND_EXECUTION_PATH,
    BackendClient,
    BackendClientError,
    LoopbackBackendExecutionTransport,
)
from total_gateway.store import GatewayStateStore
from total_gateway.tickets import TicketSigner, TicketVerificationError
from tests.test_execution_contracts import capability_manifest, execution_ticket


HASH_A = "a" * 64
HASH_B = "b" * 64


class FakeBackendTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, float]] = []
        self.response: dict[str, object] | None = None
        self.error: Exception | None = None

    def execute(self, body: bytes, *, timeout_seconds: float) -> dict[str, object]:
        self.calls.append((body, timeout_seconds))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def signed_ticket(arguments: dict[str, object], **payload_overrides: object):
    manifest = capability_manifest()
    arguments_sha256 = canonical_sha256(arguments)
    payload_overrides.setdefault("ticket_id", "ticket_" + arguments_sha256[:32])
    payload_overrides.setdefault("nonce", "nonce_" + arguments_sha256[:32])
    payload = execution_ticket(
        manifest=manifest,
        arguments_hash=arguments_sha256,
        **payload_overrides,
    ).payload
    private = Ed25519PrivateKey.generate()
    raw_public = private.public_key().public_bytes_raw()
    descriptor = PublicKeyDescriptor(
        kid="execution_backend_client_key",
        issuer="tiangong-total-gateway",
        audience="tiangong-backend",
        purpose="execution_ticket",
        public_key_base64url=base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode("ascii"),
        public_key_sha256=hashlib.sha256(raw_public).hexdigest(),
        state="ACTIVE",
        not_before_ms=0,
        not_after_ms=100_000,
        component_manifest_hash=payload.component_manifest_hash,
    )
    trust = TrustBundle(
        bundle_id="trust_backend_client",
        revision=1,
        gateway_epoch=payload.gateway_epoch,
        generated_at_ms=10_000,
        required_scopes=(
            TrustScope(
                issuer=descriptor.issuer,
                audience=descriptor.audience,
                purpose=descriptor.purpose,
            ),
        ),
        keys=(descriptor,),
        production_ready=True,
        bundle_sha256=HASH_A,
    ).with_computed_sha256()
    return TicketSigner(descriptor.kid, private).sign_execution(payload), manifest, trust


def backend_response(ticket, result_payload: object, **overrides: object) -> dict[str, object]:
    result = ExecutionResult(
        result_id="execution_result_backend_client",
        ticket_id=ticket.payload.ticket_id,
        request_id=ticket.payload.request_id,
        run_id=ticket.payload.run_id,
        generation=ticket.payload.generation,
        effect_id=ticket.payload.effect_id,
        action_id=ticket.payload.action_id,
        action_version=ticket.payload.action_version,
        status="SUCCEEDED",
        attempt=1,
        started_at_ms=20_000,
        finished_at_ms=20_100,
        side_effect_started=False,
        result_payload_sha256=canonical_sha256(result_payload),
        output_object_refs=(),
        fact_ids=("fact_backend_client",),
    )
    values = {
        "ok": True,
        "api_contract": "tiangong.desktop.backend.v3",
        "execution_result": result.model_dump(mode="json"),
        "result_payload": result_payload,
    }
    values.update(overrides)
    return values


class BackendClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = GatewayStateStore.open(
            Path(self.temporary.name) / "gateway.sqlite3",
            now_ms=1_000,
        )
        self.transport = FakeBackendTransport()
        self.client = BackendClient(
            self.transport,
            self.store,
            ticket_consumer_instance_id="gateway_instance_001",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def execute(self, ticket, manifest, trust, arguments):
        return self.client.execute(
            ticket,
            arguments,
            capability_manifest=manifest,
            trust_bundle=trust,
            now_ms=20_000,
            expected_gateway_epoch=3,
            minimum_generation=2,
        )

    def test_valid_ticket_calls_only_exact_wire_contract_and_binds_result(self) -> None:
        arguments = {"content": "hello", "output_root_id": "workspace_output"}
        ticket, manifest, trust = signed_ticket(arguments)
        self.transport.response = backend_response(ticket, {"created": True})
        response = self.execute(ticket, manifest, trust, arguments)
        self.assertEqual(response.result.ticket_id, ticket.payload.ticket_id)
        self.assertEqual(response.result_payload, {"created": True})
        self.assertEqual(len(self.transport.calls), 1)
        wire = json.loads(self.transport.calls[0][0])
        self.assertEqual(set(wire), {"schema", "ticket", "arguments"})
        self.assertEqual(wire["schema"], "tiangong.backend.execute-ticket.v1")
        self.assertEqual(wire["ticket"]["payload"]["effect_id"], ticket.payload.effect_id)
        self.assertEqual(self.transport.calls[0][1], ticket.payload.max_runtime_ms / 1000)

    def test_argument_digest_and_host_paths_fail_before_ticket_consumption(self) -> None:
        arguments = {"content": "hello"}
        ticket, manifest, trust = signed_ticket(arguments)
        with self.assertRaisesRegex(BackendClientError, "digest_mismatch"):
            self.execute(ticket, manifest, trust, {"content": "changed"})
        with self.assertRaisesRegex(BackendClientError, "host_path_forbidden"):
            self.execute(ticket, manifest, trust, {"content": r"C:\Users\77571\secret.txt"})
        self.assertEqual(self.transport.calls, [])

        self.transport.response = backend_response(ticket, {"ok": True})
        self.execute(ticket, manifest, trust, arguments)
        self.assertEqual(len(self.transport.calls), 1)

    def test_chat_arguments_with_host_paths_in_recent_messages_are_accepted(self) -> None:
        # Regression: RequestProcessor.process places raw conversation history
        # under recent_messages and the current utterance under text.  Users
        # legitimately paste host paths into chat; that must not trip the
        # host-path guard (previously every later message was rejected).
        arguments = {
            "attachments": [],
            "channel_message_ref": "msg_001",
            "conversation_ref": "conv_001",
            "knowledge_references": [],
            "life_snapshot": {"revision": 4},
            "recent_messages": [
                {
                    "role": "user",
                    "content": r"帮我把 C:\Users\77571\Desktop\报表\final.docx 整理一下",
                },
                {
                    "role": "assistant",
                    "content": "是指 //nas/share/final.docx 还是 file:C:/Users/77571/Desktop 下的？",
                },
            ],
            "conversation_projection": {"message_count": 2},
            "skill_recommendation": None,
            "text": "你好",
            "user_callsign": "起源",
        }
        ticket, manifest, trust = signed_ticket(arguments)
        self.transport.response = backend_response(ticket, {"ok": True})
        response = self.execute(ticket, manifest, trust, arguments)
        self.assertEqual(response.result.ticket_id, ticket.payload.ticket_id)
        self.assertEqual(len(self.transport.calls), 1)
        wire = json.loads(self.transport.calls[0][0])
        self.assertEqual(wire["arguments"], arguments)

    def test_top_level_path_argument_still_rejected(self) -> None:
        # The guard must stay strict for genuine tool/file path parameters:
        # top-level or nested outside the natural-language branches.
        arguments = {"text": "你好"}
        ticket, manifest, trust = signed_ticket(arguments)
        with self.assertRaisesRegex(BackendClientError, "host_path_forbidden"):
            self.execute(ticket, manifest, trust, {"content": r"C:\Users\77571\secret.txt"})
        with self.assertRaisesRegex(BackendClientError, "host_path_forbidden"):
            self.execute(ticket, manifest, trust, {"path": r"C:\Users\77571\secret.txt"})
        with self.assertRaisesRegex(BackendClientError, "host_path_forbidden"):
            self.execute(
                ticket,
                manifest,
                trust,
                {"text": "你好", "options": {"output": "/etc/passwd"}},
            )
        self.assertEqual(self.transport.calls, [])

    def test_ticket_replay_never_calls_backend_twice(self) -> None:
        arguments = {"content": "hello"}
        ticket, manifest, trust = signed_ticket(arguments)
        self.transport.response = backend_response(ticket, {"ok": True})
        self.execute(ticket, manifest, trust, arguments)
        with self.assertRaisesRegex(BackendClientError, "replay_forbidden"):
            self.execute(ticket, manifest, trust, arguments)
        self.assertEqual(len(self.transport.calls), 1)

    def test_signature_manifest_epoch_and_generation_fail_closed(self) -> None:
        arguments = {"content": "hello"}
        ticket, manifest, trust = signed_ticket(arguments)
        tampered = ticket.model_copy(
            update={"payload": ticket.payload.model_copy(update={"workspace_id": "workspace_tampered"})}
        )
        with self.assertRaisesRegex(TicketVerificationError, "signature.invalid"):
            self.execute(tampered, manifest, trust, arguments)

        with self.assertRaisesRegex(ExecutionAuthorizationError, "digest.invalid"):
            self.client.execute(
                ticket,
                arguments,
                capability_manifest=manifest.model_copy(update={"sha256": HASH_A}),
                trust_bundle=trust,
                now_ms=20_000,
                expected_gateway_epoch=3,
                minimum_generation=2,
            )
        with self.assertRaisesRegex(ExecutionAuthorizationError, "gateway_epoch.mismatch"):
            self.client.execute(
                ticket,
                arguments,
                capability_manifest=manifest,
                trust_bundle=trust,
                now_ms=20_000,
                expected_gateway_epoch=4,
                minimum_generation=2,
            )
        with self.assertRaisesRegex(ExecutionAuthorizationError, "generation.fenced"):
            self.client.execute(
                ticket,
                arguments,
                capability_manifest=manifest,
                trust_bundle=trust,
                now_ms=20_000,
                expected_gateway_epoch=3,
                minimum_generation=3,
            )
        self.assertEqual(self.transport.calls, [])

    def test_unknown_transport_or_swapped_result_is_ambiguous_and_not_retriable(self) -> None:
        arguments = {"content": "hello"}
        ticket, manifest, trust = signed_ticket(arguments)
        self.transport.error = BackendClientError("backend.http.outcome_unknown", ambiguous=True)
        with self.assertRaises(BackendClientError) as caught:
            self.execute(ticket, manifest, trust, arguments)
        self.assertTrue(caught.exception.ambiguous)
        self.transport.error = None
        self.transport.response = backend_response(ticket, {"ok": True})
        with self.assertRaisesRegex(BackendClientError, "replay_forbidden"):
            self.execute(ticket, manifest, trust, arguments)
        self.assertEqual(len(self.transport.calls), 1)

        other_arguments = {"content": "other"}
        other, other_manifest, other_trust = signed_ticket(other_arguments)
        swapped = backend_response(other, {"ok": True})
        swapped_result = dict(swapped["execution_result"])
        swapped_result["request_id"] = "req_" + "9" * 64
        swapped["execution_result"] = swapped_result
        self.transport.response = swapped
        with self.assertRaises(BackendClientError) as caught:
            self.execute(other, other_manifest, other_trust, other_arguments)
        self.assertTrue(caught.exception.ambiguous)


class LoopbackTransportTests(unittest.TestCase):
    def test_posts_only_to_ticket_endpoint_and_has_no_legacy_fallback(self) -> None:
        requests: list[tuple[str, str | None, bytes]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                requests.append(
                    (
                        self.path,
                        self.headers.get("X-Tiangong-Service-Auth"),
                        self.rfile.read(length),
                    )
                )
                body = b'{"error_code":"backend.route.not_implemented"}'
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            transport = LoopbackBackendExecutionTransport(
                f"http://127.0.0.1:{server.server_port}",
                service_auth_assertion="signed-service-assertion",
            )
            with self.assertRaisesRegex(BackendClientError, "backend.route.not_implemented") as caught:
                transport.execute(b"{}", timeout_seconds=2)
            self.assertEqual(caught.exception.status, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(
            requests,
            [(BACKEND_EXECUTION_PATH, "signed-service-assertion", b"{}")],
        )

    def test_rejects_non_loopback_origin(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            LoopbackBackendExecutionTransport(
                "http://example.com:7174",
                service_auth_assertion="signed-service-assertion",
            )


if __name__ == "__main__":
    unittest.main()
