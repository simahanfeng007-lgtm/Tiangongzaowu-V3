from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from communication_service.bootstrap import CommunicationConfig
from communication_service.channel_authority import ChannelAuthorityGate
from communication_service.inbox import InboxIngress, cursor_token_sha256, derive_cursor_stream_key
from communication_service.runtime import CommunicationRuntime
from contracts import (
    InboundEnvelope,
    InboundScope,
    ProductionInboundSubmission,
    begin_channel_cutover,
    build_channel_drain_evidence,
    build_production_inbound_submission,
    canonical_json_bytes,
    derive_inbound_scope_keys,
)
from pydantic import ValidationError
from total_gateway.bootstrap import GatewayConfig
from total_gateway.server import GatewayHttpServer
from total_gateway.runtime import GatewayRuntime


TOKEN = "communication-gateway-token-" + "a" * 40
MANIFEST_SHA256 = "d" * 64


def inbound_envelope(*, received_at_ms: int) -> InboundEnvelope:
    scope = InboundScope(
        channel="wechat",
        tenant_id="tenant-production",
        link_account_id="account-production",
        conversation_ref="conversation-production",
        channel_message_ref="message-production",
        sender_ref="sender-production",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound-production",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=received_at_ms,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash="a" * 64,
        text="hello through the candidate channel",
    )


class ProductionChannelIngressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.base_ms = int(time.time() * 1_000) - 1_000
        gateway_config = GatewayConfig(
            environment="test",
            port=0,
            state_root=root / "gateway",
            communication_api_token=TOKEN,
        )
        self.gateway = GatewayRuntime.start(gateway_config, now_ms=self.base_ms)
        self.server = GatewayHttpServer(self.gateway)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        communication_config = CommunicationConfig(
            environment="test",
            port=0,
            state_root=root / "communication",
            total_gateway_origin=(
                f"http://127.0.0.1:{self.server.server_address[1]}"
            ),
            gateway_api_token=TOKEN,
        )
        self.communication = CommunicationRuntime.start(
            communication_config,
            now_ms=self.base_ms,
        )
        snapshot = begin_channel_cutover(
            channel="wechat",
            tenant_id="tenant-production",
            link_account_id="account-production",
            gateway_epoch=self.gateway.lease.gateway_epoch,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            candidate_owner_instance_id=self.communication.instance_id,
            started_at_ms=self.base_ms + 10,
        )
        evidence = build_channel_drain_evidence(
            channel="wechat",
            tenant_id="tenant-production",
            link_account_id="account-production",
            gateway_epoch=self.gateway.lease.gateway_epoch,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            inbox_ledger_sha256="b" * 64,
            delivery_ledger_sha256="c" * 64,
            last_cursor_sha256=None,
            observed_at_ms=self.base_ms + 20,
        )
        self.gateway.begin_channel_cutover(snapshot)
        self.gateway.record_channel_drain(evidence)
        registration = self.gateway.activate_channel_candidate(
            snapshot.cutover_id,
            component_manifest_sha256=MANIFEST_SHA256,
            issued_at_ms=self.base_ms + 30,
        )
        self.lease = registration.lease
        self.communication.bind_channel_authority(
            ChannelAuthorityGate(
                owner_instance_id=self.communication.instance_id,
                expected_gateway_epoch=self.gateway.lease.gateway_epoch,
                expected_component_manifest_sha256=MANIFEST_SHA256,
            )
        )
        self.communication.install_channel_lease(
            self.lease,
            now_ms=self.base_ms + 40,
        )
        self.envelope = inbound_envelope(received_at_ms=self.base_ms + 50)
        ingress = InboxIngress(
            ingress_id=self.envelope.inbound_id,
            envelope=self.envelope,
            raw_payload_object_id="raw-production",
            raw_payload_sha256="e" * 64,
            raw_payload_size_bytes=32,
            cursor_stream_key=derive_cursor_stream_key(
                self.envelope.channel,
                self.envelope.tenant_id,
                self.envelope.link_account_id,
            ),
            previous_cursor_sha256=None,
            next_cursor_token="cursor-production",
            next_cursor_sha256=cursor_token_sha256("cursor-production"),
            captured_at_ms=self.base_ms + 50,
            ingress_sha256="0" * 64,
        ).with_computed_sha256()
        self.permit = self.communication.inbox.persist_and_advance_cursor(
            ingress,
            persisted_at_ms=self.base_ms + 60,
        ).permit

    def tearDown(self) -> None:
        self.communication.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.gateway.close()
        self.temporary.cleanup()

    def raw_request(
        self,
        body: bytes,
        *,
        token: str = TOKEN,
        origin: str | None = None,
    ) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )
        headers = {
            "Content-Type": "application/json",
            "X-Tiangong-Communication-Token": token,
        }
        if origin is not None:
            headers["Origin"] = origin
        try:
            connection.request(
                "POST",
                "/api/v1/gateway/internal/channel-inbound",
                body=body,
                headers=headers,
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def submission(self, *, lease_sha256: str | None = None):
        return build_production_inbound_submission(
            self.envelope,
            self.permit,
            source_instance_id=self.communication.instance_id,
            gateway_epoch=self.gateway.lease.gateway_epoch,
            channel_ownership_lease_sha256=(
                self.lease.lease_sha256 if lease_sha256 is None else lease_sha256
            ),
            submitted_at_ms=int(time.time() * 1_000),
        )

    def test_contract_binds_ack_envelope_and_cannot_claim_effect_authority(self) -> None:
        submission = self.submission()
        self.assertTrue(submission.has_valid_sha256())
        self.assertTrue(submission.request_creation_permitted)
        self.assertFalse(submission.effects_permitted)

        payload = submission.model_dump(mode="json")
        payload["effects_permitted"] = True
        with self.assertRaises(ValidationError):
            ProductionInboundSubmission.model_validate(payload, strict=True)

        bad_permit = self.permit.model_copy(update={"permit_sha256": "f" * 64})
        with self.assertRaises(ValueError):
            build_production_inbound_submission(
                self.envelope,
                bad_permit,
                source_instance_id=self.communication.instance_id,
                gateway_epoch=self.gateway.lease.gateway_epoch,
                channel_ownership_lease_sha256=self.lease.lease_sha256,
                submitted_at_ms=int(time.time() * 1_000),
            )

    def test_real_7176_to_7184_http_registers_one_request_without_effects(self) -> None:
        outcome = SimpleNamespace(
            should_forward=True,
            envelope=self.envelope,
            ack_permit=self.permit,
        )
        first = self.communication.forward_wechat_outcome(
            outcome,
            now_ms=int(time.time() * 1_000),
        )
        repeated = self.communication.forward_wechat_outcome(
            outcome,
            now_ms=int(time.time() * 1_000),
        )
        self.assertTrue(first.request_created)
        self.assertFalse(first.duplicate)
        self.assertFalse(repeated.request_created)
        self.assertTrue(repeated.duplicate)
        self.assertEqual(first.request_id, repeated.request_id)
        self.assertFalse(first.effects_started)
        self.assertFalse(first.completion_claimed)
        self.assertEqual(self.gateway.store.count_journal_entries(), 1)
        self.assertEqual(self.gateway.store.get_request_envelope(first.request_id), self.envelope)
        self.assertEqual(
            self.gateway.store._connection.execute(  # noqa: SLF001
                "SELECT count(*) FROM outbox"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.gateway.store._connection.execute(  # noqa: SLF001
                "SELECT count(*) FROM effect_ledger"
            ).fetchone()[0],
            0,
        )

    def test_auth_browser_noncanonical_and_stale_lease_fail_before_journal(self) -> None:
        body = canonical_json_bytes(self.submission().model_dump(mode="json"))
        status, payload = self.raw_request(body, token="wrong-token-" + "x" * 40)
        self.assertEqual(status, 401)
        self.assertFalse(payload["request_created"])
        status, payload = self.raw_request(body, origin="null")
        self.assertEqual(status, 403)
        self.assertFalse(payload["effects_started"])
        noncanonical = json.dumps(
            self.submission().model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        status, _ = self.raw_request(noncanonical)
        self.assertEqual(status, 400)
        stale = self.submission(lease_sha256="f" * 64)
        status, payload = self.raw_request(
            canonical_json_bytes(stale.model_dump(mode="json"))
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["reason_code"], "channel_ingress.channel_ownership.mismatch")
        self.assertEqual(self.gateway.store.count_journal_entries(), 0)


if __name__ == "__main__":
    unittest.main()
