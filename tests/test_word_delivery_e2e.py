from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from communication_service.delivery_ledger import DeliveryLedger
from communication_service.wechat_file_outbound import (
    WechatCdnUploadResponse,
    WechatFileDeliveryService,
)
from communication_service.wechat_session import WechatSessionLedger
from communication_service.gateway_artifact_source import LoopbackGatewayArtifactSource
from communication_service.wechat_text_outbound import (
    WechatIlinkResponse,
    default_wechat_text_policy,
)
from contracts import (
    OutboundPart,
    OutboundPlan,
    canonical_sha256,
    derive_artifact_revision_identity,
    derive_delivery_identity,
    derive_effect_identity,
    derive_request_identity,
    derive_run_identity,
    grant_from_outbound_part,
)
from total_gateway.artifact_content import VerifiedArtifactContentSource
from total_gateway.artifact_gate import ArtifactCandidate, ArtifactGate, ArtifactGateError
from total_gateway.artifact_open import ArtifactOpenError, ArtifactOpenService
from total_gateway.backend_client import BackendClient
from total_gateway.docx_qc import DocxQcPolicy, DocxQcService
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore
from total_gateway.server import GatewayHttpServer
from tests.protocol_simulators import WechatProtocolSimulator
from tests.security_file_corpus import security_file_corpus
from tests.test_backend_client import FakeBackendTransport, backend_response, signed_ticket
from tests.test_delivery_contracts import (
    consume_verified_delivery_for_test,
    delivery_ticket,
)


HASH_B = "b" * 64
HASH_D = "d" * 64
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _Protector:
    def protect(self, plaintext, entropy):
        key = hashlib.sha256(entropy).digest()
        return b"TEST" + bytes(
            value ^ key[index % len(key)] for index, value in enumerate(plaintext)
        )

    def unprotect(self, ciphertext, entropy):
        key = hashlib.sha256(entropy).digest()
        return bytearray(
            value ^ key[index % len(key)]
            for index, value in enumerate(ciphertext[4:])
        )


class _Clock:
    def __init__(self, value=23_000):
        self.value = value

    def now(self):
        value = self.value
        self.value += 1
        return value

    def sleep(self, seconds):
        self.value += max(1, int(seconds * 1_000))


class WordDeliveryE2ETests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.gateway_store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=1_000)
        self.object_store = ContentAddressedObjectStore.open(root / "objects", now_ms=1_000)
        self.fact_ledger = FactLedger.open(
            root / "facts.sqlite3",
            self.object_store,
            now_ms=1_000,
        )
        self.delivery_ledger = DeliveryLedger.open(
            root / "delivery.sqlite3",
            now_ms=1_000,
        )
        self.sessions = WechatSessionLedger.open(
            root / "sessions.sqlite3",
            now_ms=1_000,
            protector=_Protector(),
        )
        self.staging = root / "staging"
        self.open_cache = root / "artifact-open"
        self.gate = ArtifactGate(self.object_store, self.fact_ledger)
        self.qc = DocxQcService(self.object_store, self.fact_ledger)
        self.clock = _Clock()
        self.policy = default_wechat_text_policy().model_copy(
            update={"min_attempt_interval_ms": 0, "policy_sha256": "0" * 64}
        ).with_computed_sha256()
        self.request = derive_request_identity("7" * 64)
        self.run = derive_run_identity(self.request.request_id, 1)
        self.execution_effect = derive_effect_identity(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=0,
            intent_sha256="6" * 64,
        )

    def tearDown(self):
        self.sessions.close()
        self.delivery_ledger.close()
        self.fact_ledger.close()
        self.object_store.close()
        self.gateway_store.close()
        self.temporary.cleanup()

    def _put_artifact(self, data: bytes):
        return self.object_store.put_bytes(
            data,
            kind="artifact",
            tenant_id="tenant_001",
            link_account_id="wechat_001",
            conversation_scope_hash=HASH_B,
            created_at_ms=20_000,
        ).reference

    def _record_producer(self, object_ids: tuple[str, ...]):
        arguments = {"content": "create checked word document"}
        ticket, capability, trust = signed_ticket(
            arguments,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            effect_id=self.execution_effect.effect_id,
        )
        transport = FakeBackendTransport()
        envelope = backend_response(ticket, {"objects": sorted(object_ids)})
        result = dict(envelope["execution_result"])
        result["result_id"] = "execution_result_word_delivery_e2e"
        result["fact_ids"] = ["fact_word_delivery_producer"]
        result["output_object_refs"] = sorted(object_ids)
        envelope["execution_result"] = result
        transport.response = envelope
        response = BackendClient(
            transport,
            self.gateway_store,
            ticket_consumer_instance_id="word_delivery_e2e",
        ).execute(
            ticket,
            arguments,
            capability_manifest=capability,
            trust_bundle=trust,
            now_ms=20_000,
            expected_gateway_epoch=3,
            minimum_generation=2,
        )
        self.fact_ledger.record_execution(response, observed_at_ms=20_200)

    @staticmethod
    def _candidate(reference, *, intent_id: str, filename: str):
        return ArtifactCandidate(
            producer_fact_id="fact_word_delivery_producer",
            object_id=reference.object_id,
            expected_sha256=reference.sha256,
            expected_size_bytes=reference.size_bytes,
            run_sequence=1,
            artifact_intent_id=intent_id,
            revision=1,
            workspace_id="workspace_001",
            filename=filename,
            declared_mime=DOCX_MIME,
            format_id="docx",
            created_at_ms=20_300,
        )

    def _passed_manifest(self, reference):
        gate_result = self.gate.accept(
            self._candidate(
                reference,
                intent_id="primary_document",
                filename="word-report.docx",
            )
        )
        outcome = self.qc.evaluate(
            gate_result,
            run_sequence=1,
            policy=DocxQcPolicy(minimum_word_count=1_000),
            checked_at_ms=20_500,
        )
        self.assertTrue(outcome.passed)
        return outcome.registration.record.manifest

    def _plan(self, manifest, *, ordinal: int):
        delivery = derive_delivery_identity(
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            run_sequence=1,
            generation=manifest.generation,
            recipient_scope_hash=HASH_D,
            reply_to_message_ref="message_001",
            payload_manifest_sha256=manifest.manifest_sha256,
        )
        effect = derive_effect_identity(
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            run_sequence=1,
            generation=manifest.generation,
            effect_kind="delivery",
            ordinal=ordinal,
            intent_sha256=canonical_sha256(
                {
                    "delivery_id": delivery.delivery_id,
                    "artifact_manifest_sha256": manifest.manifest_sha256,
                }
            ),
        )
        part = OutboundPart(
            part_id=f"part_word_{ordinal}",
            index=0,
            kind="artifact",
            artifact=manifest,
        )
        return OutboundPlan(
            outbound_plan_id=f"outbound_word_{ordinal}",
            delivery_id=delivery.delivery_id,
            effect_id=effect.effect_id,
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            generation=manifest.generation,
            channel="wechat",
            tenant_id=manifest.tenant_id,
            link_account_id=manifest.link_account_id,
            conversation_ref="conversation_001",
            conversation_scope_hash=manifest.conversation_scope_hash,
            recipient_scope_hash=HASH_D,
            reply_to_message_ref="message_001",
            channel_policy_hash=self.policy.policy_sha256,
            created_at_ms=21_000 + ordinal,
            parts=(part,),
            plan_sha256="0" * 64,
        ).with_computed_plan_sha256()

    def _bind_session(self, conversation_scope_hash: str):
        return self.sessions.decide(
            account_id="ilink-account",
            sender_ref="wxuser_" + "1" * 64,
            conversation_scope_hash=conversation_scope_hash,
            message_ref="wxmsg_" + "2" * 64,
            message_fingerprint="3" * 64,
            envelope_sha256="4" * 64,
            preliminary_classification="ACCEPTED",
            recipient_user_id="synthetic-recipient",
            sequence=1,
            received_at_ms=2_000,
            incoming_context_token="synthetic-context",
        ).decision.session_key

    def _send(self, source, plan, simulator, session_key, *, ticket_id: str):
        ticket = delivery_ticket(
            plan=plan,
            ticket_id=ticket_id,
            allow_text=False,
            allow_files=True,
            max_text_parts=0,
            max_file_parts=1,
        )
        consume_verified_delivery_for_test(self.delivery_ledger, ticket, at_ms=22_000)
        service = WechatFileDeliveryService(
            self.delivery_ledger,
            self.sessions,
            source,
            simulator,
            staging_root=self.staging,
            clock_ms=self.clock.now,
            sleeper=self.clock.sleep,
        )
        return service.send(
            ticket.payload,
            plan,
            policy=self.policy,
            bot_token="synthetic-bot-token",
            ilink_account_id="ilink-account",
            session_key=session_key,
        )

    def test_real_1000_character_docx_passes_qc_opens_by_revision_and_reaches_wechat(self):
        valid = next(
            case for case in security_file_corpus() if case.case_id == "valid.docx.1000-chars"
        )
        reference = self._put_artifact(valid.content)
        self._record_producer((reference.object_id,))
        manifest = self._passed_manifest(reference)
        source = VerifiedArtifactContentSource(
            self.object_store,
            self.fact_ledger,
            (manifest,),
        )
        plan = self._plan(manifest, ordinal=0)
        session_key = self._bind_session(plan.conversation_scope_hash)
        padded_size = ((len(valid.content) // 16) + 1) * 16
        simulator = WechatProtocolSimulator()
        simulator.script(
            "upload.authorize",
            WechatIlinkResponse(
                200,
                {"ret": 0, "errcode": 0, "data": {"upload_param": "synthetic-upload"}},
                "1" * 64,
            ),
        )
        simulator.script(
            "upload.ciphertext",
            WechatCdnUploadResponse(200, "synthetic-media-ref", "2" * 64, padded_size),
        )
        simulator.script(
            "message.send",
            WechatIlinkResponse(200, {"ret": 0, "errcode": 0}, "3" * 64),
        )

        receipt = self._send(
            source,
            plan,
            simulator,
            session_key,
            ticket_id="delivery_ticket_word_e2e",
        )

        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual(receipt.parts[0].artifact_id, manifest.artifact_id)
        self.assertEqual(
            receipt.parts[0].artifact_revision_id,
            manifest.artifact_revision_id,
        )
        self.assertEqual(
            [fact.stage for fact in self.delivery_ledger.list_part_stages(plan.effect_id)],
            [
                "FETCHED",
                "ENCRYPTED",
                "UPLOAD_URL_GRANTED",
                "UPLOADED",
                "SEND_STARTED",
                "CHANNEL_ACCEPTED",
            ],
        )
        self.assertEqual(
            [call.operation for call in simulator.calls],
            ["upload.authorize", "upload.ciphertext", "message.send"],
        )
        self.assertFalse(tuple(self.staging.iterdir()))

    def test_7176_streams_only_fact_verified_artifact_from_authenticated_7184(self):
        valid = next(
            case for case in security_file_corpus() if case.case_id == "valid.docx.1000-chars"
        )
        reference = self._put_artifact(valid.content)
        self._record_producer((reference.object_id,))
        manifest = self._passed_manifest(reference)
        plan = self._plan(manifest, ordinal=7)
        grant = grant_from_outbound_part(plan.parts[0])
        token = "artifact-egress-test-token-" + "x" * 40
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                bind_host="127.0.0.1",
                port=0,
                communication_api_token=token,
                shadow_api_token="",
            ),
            objects=self.object_store,
            facts=self.fact_ledger,
        )
        server = GatewayHttpServer(runtime, desktop_api=object())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            source = LoopbackGatewayArtifactSource(
                f"http://127.0.0.1:{port}",
                token,
                port=port,
            )
            stream = source.open_artifact(grant, timeout_seconds=30)
            try:
                self.assertEqual(stream.read(), valid.content)
            finally:
                stream.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3.0)

    def test_artifact_card_is_fact_bound_and_cached_copy_tampering_is_blocked(self):
        valid = next(
            case for case in security_file_corpus() if case.case_id == "valid.docx.1000-chars"
        )
        reference = self._put_artifact(valid.content)
        self._record_producer((reference.object_id,))
        manifest = self._passed_manifest(reference)
        service = ArtifactOpenService(
            self.fact_ledger,
            self.object_store,
            self.open_cache,
        )

        cards = service.list_cards(
            manifest.request_id,
            run_id=manifest.run_id,
            generation=manifest.generation,
        )
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card.gateway_request_id, manifest.request_id)
        self.assertEqual(card.run_id, manifest.run_id)
        self.assertEqual(card.generation, manifest.generation)
        self.assertEqual(card.artifact_revision_id, manifest.artifact_revision_id)
        self.assertEqual(card.manifest_sha256, manifest.manifest_sha256)
        self.assertNotIn("path", card.model_dump(mode="json"))
        self.assertTrue(card.card_sha256 == card.computed_sha256())
        self.assertFalse(
            service.list_cards(
                manifest.request_id,
                run_id=manifest.run_id,
                generation=manifest.generation + 1,
            )
        )
        self.assertEqual(
            self.fact_ledger.get_artifact_manifest(manifest.artifact_revision_id),
            manifest,
        )

        opened_card, target = service.materialize(
            gateway_request_id=manifest.request_id,
            run_id=manifest.run_id,
            generation=manifest.generation,
            artifact_revision_id=manifest.artifact_revision_id,
            manifest_sha256=manifest.manifest_sha256,
            card_sha256=card.card_sha256,
        )
        self.assertEqual(opened_card, card)
        self.assertEqual(target.read_bytes(), valid.content)

        with self.assertRaisesRegex(ArtifactOpenError, "artifact.open.manifest_binding_invalid"):
            service.materialize(
                gateway_request_id=manifest.request_id,
                run_id=manifest.run_id,
                generation=manifest.generation + 1,
                artifact_revision_id=manifest.artifact_revision_id,
                manifest_sha256=manifest.manifest_sha256,
                card_sha256=card.card_sha256,
            )
        with self.assertRaisesRegex(ArtifactOpenError, "artifact.open.card_binding_invalid"):
            service.materialize(
                gateway_request_id=manifest.request_id,
                run_id=manifest.run_id,
                generation=manifest.generation,
                artifact_revision_id=manifest.artifact_revision_id,
                manifest_sha256=manifest.manifest_sha256,
                card_sha256="0" * 64,
            )

        target.write_bytes(b"X" * len(valid.content))
        with self.assertRaisesRegex(ArtifactOpenError, "artifact.open.cached_copy_invalid"):
            service.materialize(
                gateway_request_id=manifest.request_id,
                run_id=manifest.run_id,
                generation=manifest.generation,
                artifact_revision_id=manifest.artifact_revision_id,
                manifest_sha256=manifest.manifest_sha256,
                card_sha256=card.card_sha256,
            )

    def test_artifact_materialization_avoids_windows_path_limit_and_unsafe_leaf_names(self):
        service = object.__new__(ArtifactOpenService)
        service._cache_root = self.open_cache
        data = b"artifact-path-boundary"
        manifest = SimpleNamespace(
            artifact_revision_id="arv_" + "a" * 64,
            manifest_sha256="b" * 64,
            filename="../" + ("报告" * 120) + ".txt",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

        target = service._materialize_bytes(manifest, data)

        self.assertEqual(target.read_bytes(), data)
        self.assertEqual(target.parent.parent.resolve(), self.open_cache.resolve())
        self.assertNotIn("/", target.name)
        self.assertNotIn("\\", target.name)
        self.assertTrue(target.name.endswith(".txt"))
        self.assertLessEqual(len(target.name.encode("utf-8")), 240)
        if os.name == "nt":
            self.assertLessEqual(len(str(target)), 240)
        self.assertFalse(tuple(self.open_cache.glob(".materialize-*.tmp")))

    def test_246_byte_and_1kb_fake_docx_cannot_open_or_reach_wechat_transport(self):
        corpus = {case.case_id: case for case in security_file_corpus()}
        valid = corpus["valid.docx.1000-chars"]
        fake_cases = (
            corpus["invalid.docx.246-byte-placeholder"],
            corpus["invalid.docx.1kb-placeholder"],
        )
        valid_reference = self._put_artifact(valid.content)
        fake_references = tuple(self._put_artifact(case.content) for case in fake_cases)
        self._record_producer(
            tuple(
                sorted(
                    (valid_reference.object_id,)
                    + tuple(reference.object_id for reference in fake_references)
                )
            )
        )
        valid_manifest = self._passed_manifest(valid_reference)
        session_key = self._bind_session(valid_manifest.conversation_scope_hash)

        for index, (case, reference) in enumerate(
            zip(fake_cases, fake_references, strict=True),
            start=1,
        ):
            with self.subTest(case=case.case_id):
                with self.assertRaises(ArtifactGateError):
                    self.gate.accept(
                        self._candidate(
                            reference,
                            intent_id=f"blocked_fake_{index}",
                            filename=f"blocked-fake-{index}.docx",
                        )
                    )

                identity = derive_artifact_revision_identity(
                    request_id=valid_manifest.request_id,
                    run_id=valid_manifest.run_id,
                    run_sequence=1,
                    generation=valid_manifest.generation,
                    artifact_intent_id=f"forged_fake_{index}",
                    revision=1,
                    content_sha256=reference.sha256,
                )
                forged_manifest = valid_manifest.model_copy(
                    update={
                        "artifact_id": identity.artifact_id,
                        "artifact_revision_id": identity.artifact_revision_id,
                        "content_object_id": reference.object_id,
                        "sha256": reference.sha256,
                        "size_bytes": reference.size_bytes,
                        "filename": f"forged-fake-{index}.docx",
                        "manifest_sha256": "0" * 64,
                    }
                ).with_computed_manifest_sha256()
                source = VerifiedArtifactContentSource(
                    self.object_store,
                    self.fact_ledger,
                    (forged_manifest,),
                )
                plan = self._plan(forged_manifest, ordinal=index)
                simulator = WechatProtocolSimulator()
                receipt = self._send(
                    source,
                    plan,
                    simulator,
                    session_key,
                    ticket_id=f"delivery_ticket_word_fake_{index}",
                )

                self.assertEqual(receipt.status, "FAILED_RETRYABLE")
                self.assertEqual(
                    [fact.stage for fact in self.delivery_ledger.list_part_stages(plan.effect_id)],
                    ["FAILED_RETRYABLE"],
                )
                self.assertFalse(simulator.calls)
                self.assertFalse(tuple(self.staging.iterdir()))


if __name__ == "__main__":
    unittest.main()
