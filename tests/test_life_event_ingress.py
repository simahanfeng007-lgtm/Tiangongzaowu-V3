from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import (
    CausalEpisodeVNext,
    InboundEnvelope,
    InboundScope,
    LifeEventIngress,
    TransitionEvent,
    derive_inbound_scope_keys,
    derive_life_ingress_id,
    derive_run_identity,
    new_state_snapshot,
)
from contracts.life import LifeAuthorityHead, RootExperienceHead, RunLifeBinding
from life_service import store as life_store_module
from life_service.ingest import (
    LifeEventIngestor,
    LifeIngressAuthenticationError,
    verify_life_event_signature,
)
from life_service.store import LifeShadowStore, LifeShadowStoreError
from total_gateway.life_events import (
    GatewayLifeEventPublisher,
    LifeEventOutboxWorker,
)
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore


HASH_A = "a" * 64
LIFE_ID = "life_ingress_integration"


def inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_life_ingress",
        link_account_id="desktop_life_ingress",
        conversation_ref="conversation_life_ingress",
        channel_message_ref="message_life_ingress",
        sender_ref="principal_life_ingress",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_life_ingress",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=1_000,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A,
        text="verify durable life ingress",
    )


class _CrashAfterCommitOnce:
    def __init__(self, ingestor: LifeEventIngestor) -> None:
        self.ingestor = ingestor
        self.crashed = False

    def ingest(self, ingress: LifeEventIngress, *, received_at_ms: int):
        result = self.ingestor.ingest(ingress, received_at_ms=received_at_ms)
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("simulated response loss after durable life commit")
        return result


class LifeEventIngressIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.gateway = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=900)
        self.objects = ContentAddressedObjectStore.open(root / "objects", now_ms=900)
        self.life = LifeShadowStore.open(
            root / "life.shadow.sqlite3", create=True, now_ms=900
        )
        registration = self.gateway.register_request(
            inbound(), ingress_sha256=HASH_A, created_at_ms=1_100
        )
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.gateway.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=1,
            gateway_epoch=1,
            lease_id="lease_life_ingress",
            owner_instance_id="gateway_life_ingress",
            issued_at_ms=1_200,
            lease_duration_ms=120_000,
        )
        self.gateway.initialize_snapshot(
            new_state_snapshot(
                "request",
                entity_id="request_state_life_ingress",
                request_id=self.request_id,
                run_id=self.run_id,
                generation=1,
                created_at_ms=1_300,
            )
        )
        self.source_key = Ed25519PrivateKey.generate()
        self.writer_key = Ed25519PrivateKey.generate()
        self.publisher = GatewayLifeEventPublisher(
            self.gateway,
            self.objects,
            life_id=LIFE_ID,
            source_epoch=1,
            signer_key_id="gateway_source_key_1",
            signing_key=self.source_key,
        )
        self.ingestor = LifeEventIngestor(
            self.life,
            writer_epoch=1,
            writer_key_id="life_writer_key_1",
            writer_private_key=self.writer_key,
            trusted_source_keys={
                (
                    "tiangong-total-gateway",
                    1,
                    "gateway_source_key_1",
                ): self.source_key.public_key()
            },
        )

    def tearDown(self) -> None:
        self.life.close()
        self.objects.close()
        self.gateway.close()
        self.temporary.cleanup()

    def _event(
        self,
        event_id: str,
        *,
        expected_revision: int,
        to_state: str,
        event_type: str,
        occurred_at_ms: int,
    ) -> TransitionEvent:
        return TransitionEvent(
            event_id=event_id,
            event_type=event_type,
            source_component_id="tiangong-total-gateway",
            machine="request",
            entity_id="request_state_life_ingress",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            expected_revision=expected_revision,
            to_state=to_state,
            occurred_at_ms=occurred_at_ms,
            event_sha256=HASH_A,
        ).with_computed_event_sha256()

    def test_crash_after_remote_commit_retries_without_duplication_or_reordering(self) -> None:
        first = self._event(
            "event_life_first",
            expected_revision=0,
            to_state="PLANNING",
            event_type="request.planning_started",
            occurred_at_ms=2_000,
        )
        rejected = self._event(
            "event_life_rejected",
            expected_revision=0,
            to_state="QUEUED",
            event_type="request.queued",
            occurred_at_ms=2_100,
        )
        second = self._event(
            "event_life_second",
            expected_revision=1,
            to_state="EXECUTING",
            event_type="request.execution_started",
            occurred_at_ms=2_200,
        )
        self.assertTrue(self.gateway.apply_event(first, recorded_at_ms=2_010).decision.accepted)
        self.assertFalse(
            self.gateway.apply_event(rejected, recorded_at_ms=2_110).decision.accepted
        )
        self.assertTrue(self.gateway.apply_event(second, recorded_at_ms=2_210).decision.accepted)
        publications = self.publisher.recover_missing(published_at_ms=2_300)
        self.assertEqual(
            tuple(item.ingress.source_sequence for item in publications), (1, 2)
        )
        self.assertEqual(self.gateway.list_state_events_missing_life_outbox(), ())

        transport = _CrashAfterCommitOnce(self.ingestor)
        worker = LifeEventOutboxWorker(
            self.gateway, self.objects, transport, worker_id="life_worker_1"
        )
        with self.assertRaisesRegex(RuntimeError, "response loss"):
            worker.dispatch_next(now_ms=2_400)
        self.assertFalse(worker.dispatch_next(now_ms=2_500))
        self.assertTrue(worker.dispatch_next(now_ms=32_400))
        self.assertTrue(worker.dispatch_next(now_ms=32_500))
        self.assertFalse(worker.dispatch_next(now_ms=32_600))

        events = self.life.load_events(LIFE_ID)
        self.assertEqual(tuple(item.sequence for item in events), (1, 2))
        self.assertEqual(tuple(item.source_service for item in events), (
            "tiangong-total-gateway", "tiangong-total-gateway"
        ))
        self.assertTrue(
            all(verify_life_event_signature(item, self.writer_key.public_key()) for item in events)
        )
        self.assertEqual(self.life.health()["event_count"], 2)
        self.assertTrue(self.gateway.health_check(now_ms=33_000, full=True).healthy)

    def test_signature_gap_and_dedupe_are_fail_closed(self) -> None:
        first = self._event(
            "event_life_auth",
            expected_revision=0,
            to_state="PLANNING",
            event_type="request.planning_started",
            occurred_at_ms=2_000,
        )
        self.gateway.apply_event(first, recorded_at_ms=2_010)
        ingress = self.publisher.publish_state_event(
            first, published_at_ms=2_100
        ).ingress
        forged = ingress.model_copy(update={"signature": "0" * 128})
        with self.assertRaises(LifeIngressAuthenticationError):
            self.ingestor.ingest(forged, received_at_ms=2_200)

        gap_id = derive_life_ingress_id(
            life_id=ingress.life_id,
            source_component_id=ingress.source_component_id,
            source_epoch=ingress.source_epoch,
            source_sequence=2,
            dedupe_key=ingress.dedupe_key,
        )
        gap_unsigned = ingress.model_copy(
            update={
                "ingress_id": gap_id,
                "source_sequence": 2,
                "ingress_sha256": "0" * 64,
                "signature": "0" * 128,
            }
        )
        gap_hashed = gap_unsigned.model_copy(
            update={"ingress_sha256": gap_unsigned.computed_ingress_sha256()}
        )
        gap = gap_hashed.model_copy(
            update={
                "signature": self.source_key.sign(
                    gap_hashed.ingress_sha256.encode("ascii")
                ).hex()
            }
        )
        with self.assertRaisesRegex(LifeShadowStoreError, "discontinuous"):
            self.ingestor.ingest(gap, received_at_ms=2_200)

        committed = self.ingestor.ingest(ingress, received_at_ms=2_200)
        duplicate = self.ingestor.ingest(gap, received_at_ms=2_300)
        self.assertTrue(committed.event_created)
        self.assertFalse(duplicate.event_created)
        self.assertTrue(duplicate.receipt.duplicate)
        self.assertEqual(duplicate.event.event_id, committed.event.event_id)
        self.assertEqual(self.life.health()["event_count"], 1)

    def test_crash_after_life_ack_yields_one_binding_one_root_one_first_child(self) -> None:
        first = self._event(
            "event_life_saga",
            expected_revision=0,
            to_state="PLANNING",
            event_type="request.planning_started",
            occurred_at_ms=2_000,
        )
        self.assertTrue(self.gateway.apply_event(first, recorded_at_ms=2_010).decision.accepted)
        self.publisher.publish_state_event(first, published_at_ms=2_100)

        transport = _CrashAfterCommitOnce(self.ingestor)
        worker = LifeEventOutboxWorker(
            self.gateway, self.objects, transport, worker_id="life_worker_saga"
        )
        with self.assertRaisesRegex(RuntimeError, "response loss"):
            worker.dispatch_next(now_ms=2_200)
        self.assertFalse(worker.dispatch_next(now_ms=2_300))
        self.assertTrue(worker.dispatch_next(now_ms=32_200))
        self.assertFalse(worker.dispatch_next(now_ms=32_300))
        self.assertEqual(self.life.health()["event_count"], 1)

        head = LifeAuthorityHead(
            life_id=LIFE_ID, writer_epoch=1, identity_revision=1, identity_sha256=HASH_A,
            soul_revision=1, soul_sha256=HASH_A, affect_revision=1, affect_sha256=HASH_A,
            deletion_epoch=0, head_sha256="0" * 64,
        ).with_computed_head_sha256()
        self.assertTrue(self.life.put_life_authority_head(head, expected_head_sha256=None))
        binding = RunLifeBinding(
            binding_id="bind_life_ingress", life_id=LIFE_ID, binding_subject_kind="request",
            binding_subject_id=self.run_id, binding_subject_sha256=HASH_A,
            life_authority_head_sha256=head.head_sha256, writer_epoch=1, identity_revision=1,
            identity_sha256=HASH_A, soul_revision=1, soul_sha256=HASH_A, affect_revision=1,
            affect_sha256=HASH_A, deletion_epoch=0, bound_at_ms=2_400, binding_source="gateway",
            request_id=self.request_id, run_id=self.run_id, run_sequence=1, generation=1,
            binding_sha256="0" * 64,
        ).with_computed_binding_sha256()
        self.assertTrue(self.life.put_run_life_binding(binding))
        root = RootExperienceHead(
            root_experience_id="root_life_ingress", life_id=LIFE_ID,
            initial_run_life_binding_sha256=binding.binding_sha256,
            active_run_life_binding_sha256=binding.binding_sha256,
            root_trigger_event_id="lev_" + HASH_A, root_trigger_event_sha256=HASH_A,
            next_sequence_no=1, root_status="OPEN", head_sha256="0" * 64,
        ).with_computed_head_sha256()
        self.assertTrue(self.life.put_root_experience_head(root, expected_head_sha256=None))
        child = CausalEpisodeVNext(
            episode_id="cep_" + HASH_A, life_id=LIFE_ID, root_experience_id=root.root_experience_id,
            sequence_no=1, episode_kind="external_action",
            run_life_binding_sha256=binding.binding_sha256, candidate_ids=("c_1",),
            selected_candidate_id="c_1", terminal_status="CLOSED", terminal_reason_code="done",
            created_at_ms=2_500, closed_at_ms=2_600, episode_sha256="0" * 64,
        ).with_computed_episode_sha256()
        self.assertTrue(self.life.put_causal_episode_vnext(child))

        # Idempotent replay of the same projection transaction: exactly one of each.
        self.assertFalse(self.life.put_life_authority_head(head, expected_head_sha256=head.head_sha256))
        self.assertFalse(self.life.put_run_life_binding(binding))
        self.assertFalse(self.life.put_root_experience_head(root, expected_head_sha256=root.head_sha256))
        self.assertFalse(self.life.put_causal_episode_vnext(child))
        self.assertEqual(self.gateway.list_state_events_missing_life_outbox(), ())


class LifeShadowStoreP2MigrationTests(unittest.TestCase):
    def test_p1_shadow_store_migrates_without_rewriting_event_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "life.shadow.sqlite3"
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                connection.executescript(life_store_module._P1_SCHEMA_SQL)  # noqa: SLF001
                connection.execute(
                    "INSERT INTO schema_migrations VALUES (1, ?, ?, ?)",
                    (
                        "p1-initial-shadow-schema",
                        life_store_module._P1_SCHEMA_SHA256,  # noqa: SLF001
                        500,
                    ),
                )
                connection.execute(
                    "INSERT INTO schema_metadata VALUES ('purpose', 'life-shadow-only')"
                )
                connection.execute(
                    "INSERT INTO schema_metadata VALUES ('schema_sha256', ?)",
                    (life_store_module._P1_SCHEMA_SHA256,),  # noqa: SLF001
                )
                connection.execute(
                    f"PRAGMA application_id={life_store_module.SHADOW_STORE_APPLICATION_ID}"
                )
                connection.execute("PRAGMA user_version=1")
            finally:
                connection.close()
            with LifeShadowStore.open(path, create=False, now_ms=1_000) as store:
                health = store.health()
                self.assertEqual(health["schema_version"], life_store_module.SHADOW_STORE_SCHEMA_VERSION)
                self.assertEqual(health["event_count"], 0)


if __name__ == "__main__":
    unittest.main()
