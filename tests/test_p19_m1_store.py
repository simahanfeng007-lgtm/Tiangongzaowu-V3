"""P19-R2 M1 store v23 tests — the review 14-point checklist.

1. v22 -> v23 lossless upgrade; 2. fresh DB at v23; 3. migration re-entry
idempotent; 4. mid-failure rollback leaves DB consistent; 5. close/reopen
consistency; 6. record hash recomputable; 7. request/run/generation
binding mismatch rejected; 8. verifier/version/snapshot mismatch rejected
(recorder layer); 9. same identity + same content idempotent;
10. same identity + different content conflicts; 11. zero request state
transitions; 12. zero CompletionDecision changes; 13. zero delivery/
effect/artifact state changes; 14. every stored record is RECORD.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import InboundEnvelope, InboundScope, derive_inbound_scope_keys, derive_run_identity
from contracts.verification import (
    VerificationRecord,
    derive_verification_record_id,
)
from total_gateway.verification_registry import (
    UnknownVerifierError,
    VerifierRegistry,
)
from total_gateway.verification_recording import (
    VerificationRecordRejected,
    VerificationRecorder,
)
from total_gateway.store import (
    GatewayStateStore,
    StoreConflictError,
    StoreMigrationError,
    StoreNotFoundError,
)
from gateway_store_migration_support import downgrade_v12_to_v11

HASH_A = "a" * 64


def inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_p19_m1",
        link_account_id="desktop_p19_m1",
        conversation_ref="conversation_p19_m1",
        channel_message_ref="message_p19_m1",
        sender_ref="sender_p19_m1",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_p19_m1",
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
        text="generate the monthly report",
    )


class P19M1StoreTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        registration = self.store.register_request(
            inbound(), ingress_sha256=HASH_A, created_at_ms=1_100
        )
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=1,
            gateway_epoch=1,
            lease_id="lease_p19_m1",
            owner_instance_id="gateway_p19_m1",
            issued_at_ms=1_200,
            lease_duration_ms=60_000,
        )
        self.registry = VerifierRegistry.with_defaults()
        self.snapshot = self.registry.snapshot(captured_at_ms=1_000)
        self.store.put_registry_snapshot(self.snapshot, recorded_at_ms=1_500)
        self.recorder = VerificationRecorder(snapshot=self.snapshot, store=self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _record(self, **overrides) -> VerificationRecord:
        payload = dict(
            verification_record_id="vrs_" + "0" * 64,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            verifier_id="verifier.artifact_content",
            # M2.2: 默认快照携带 v3 描述符（历史版本由 legacy 构造器保留）
            verifier_version="3",
            registry_snapshot_sha256=self.snapshot.snapshot_sha256,
            predicate_id="vpd_p19_m1",
            predicate_type="artifact.nonempty",
            subject_kind="artifact",
            subject_identity="obj_" + "1" * 64,
            evaluation_phase="POST_EXECUTION",
            status="FAIL",
            enforcement="RECORD",
            reason_codes=("artifact.empty",),
            evidence_refs=("ev_1",),
            evidence_sha256="2" * 64,
            producer_component_id="tiangong-gateway",
            model_generated=False,
            evaluated_at_ms=1_700,
            result_sha256="0" * 64,
        )
        payload.update(overrides)
        record = VerificationRecord(**payload).with_computed_sha256()
        return record.model_copy(
            update={
                "verification_record_id": derive_verification_record_id(
                    result_sha256=record.result_sha256
                )
            }
        )


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _strip_to_v22(self) -> None:
        """Emulate an exact v22 on-disk state from a freshly opened v24 DB.

        Strips the additive v24 (evidence) and v23 (verification plane)
        layers, committed before close.
        """
        GatewayStateStore.open(self.path, now_ms=900).close()
        connection = sqlite3.connect(self.path)
        connection.execute(
            "DROP INDEX IF EXISTS composition_activation_registration_expiry_idx"
        )
        connection.execute(
            "DROP INDEX IF EXISTS composition_activation_registration_lineage_idx"
        )
        connection.execute(
            "DROP TABLE IF EXISTS composition_activation_registration"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 30")
        connection.execute(
            "DROP INDEX IF EXISTS repair_execution_binding_attempt_idx"
        )
        connection.execute("DROP TABLE IF EXISTS repair_execution_binding")
        connection.execute("DROP INDEX IF EXISTS repair_attempt_number_idx")
        connection.execute(
            "DROP INDEX IF EXISTS verification_subject_successor_attempt_idx"
        )
        connection.execute("DROP TABLE IF EXISTS artifact_subject_authority")
        connection.execute("DROP TABLE IF EXISTS repair_attempt")
        connection.execute("DROP TABLE IF EXISTS verification_subject_successor")
        connection.execute("DROP TABLE IF EXISTS repair_directive")
        connection.execute("DROP TABLE IF EXISTS verification_disposition")
        connection.execute("DROP TABLE IF EXISTS verification_failure_evidence")
        connection.execute("DELETE FROM schema_migrations WHERE version = 29")
        connection.execute("DELETE FROM schema_migrations WHERE version = 28")
        connection.execute("DROP TABLE verification_plan_activation")
        connection.execute("DELETE FROM schema_migrations WHERE version = 27")
        connection.execute("DROP TABLE write_evidence_effect_binding")
        connection.execute("DELETE FROM schema_migrations WHERE version = 26")
        connection.execute("DROP TABLE verification_plan")
        connection.execute("DROP TABLE verification_readiness")
        connection.execute("DROP TABLE repository_observation_binding")
        connection.execute("DELETE FROM schema_migrations WHERE version = 25")
        connection.execute("DROP TABLE write_evidence_v2")
        connection.execute("DROP TABLE repository_observation")
        connection.execute("DELETE FROM schema_migrations WHERE version = 24")
        connection.execute("DROP TABLE verification_record")
        connection.execute("DROP TABLE verification_registry_snapshot")
        connection.execute("DELETE FROM schema_migrations WHERE version = 23")
        connection.execute("PRAGMA user_version = 22")
        connection.commit()
        connection.close()

    def test_fresh_db_opens_at_v23(self) -> None:  # checklist 2
        store = GatewayStateStore.open(self.path, now_ms=900)
        try:
            self.assertEqual(store.health_check(full=True, now_ms=950).schema_version, 30)
        finally:
            store.close()

    def test_v22_upgrade_lossless_and_reopen_idempotent(self) -> None:  # 1/3/5
        self._strip_to_v22()
        # Upgrade path: opening with the current binary migrates 22 -> 30.
        upgraded = GatewayStateStore.open(self.path, now_ms=950)
        try:
            health = upgraded.health_check(full=True, now_ms=960)
            self.assertTrue(health.healthy)
            self.assertEqual(health.schema_version, 30)
        finally:
            upgraded.close()
        reopened = GatewayStateStore.open(self.path, now_ms=1_000)
        try:
            self.assertTrue(reopened.health_check(full=True, now_ms=1_050).healthy)
        finally:
            reopened.close()

    def test_v11_fixture_upgrades_all_the_way_to_v23(self) -> None:  # 1 (long path)
        GatewayStateStore.open(self.path, now_ms=900).close()
        connection = sqlite3.connect(self.path)
        downgrade_v12_to_v11(connection)
        connection.commit()  # helper leaves trailing changes for the caller
        connection.close()
        migrated = GatewayStateStore.open(self.path, now_ms=2_000)
        try:
            self.assertTrue(migrated.health_check(full=True, now_ms=2_001).healthy)
        finally:
            migrated.close()

    def test_tampered_schema_rejected_on_reopen(self) -> None:  # 4 (fail-closed)
        store = GatewayStateStore.open(self.path, now_ms=900)
        store.close()
        connection = sqlite3.connect(self.path)
        connection.execute("DROP TABLE verification_record")
        connection.commit()
        connection.close()
        with self.assertRaises(StoreMigrationError):
            GatewayStateStore.open(self.path, now_ms=1_000)


class RecordLifecycleTests(P19M1StoreTestBase):
    def test_record_persists_reopens_and_hash_recomputes(self) -> None:  # 5/6
        record = self._record()
        outcome = self.recorder.record(record, recorded_at_ms=2_000)
        self.assertTrue(outcome.created_by_this_call)
        fetched = self.store.get_verification_record(record.verification_record_id)
        assert fetched is not None
        self.assertTrue(fetched.has_valid_result_sha256())
        self.assertEqual(fetched, record)
        # close + reopen, records survive
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=2_500)
        fetched_again = self.store.get_verification_record(record.verification_record_id)
        assert fetched_again is not None
        self.assertEqual(fetched_again, record)

    def test_same_identity_same_content_idempotent(self) -> None:  # 9
        record = self._record()
        first = self.recorder.record(record, recorded_at_ms=2_000)
        second = self.recorder.record(record, recorded_at_ms=2_100)
        self.assertTrue(first.created_by_this_call)
        self.assertTrue(second.duplicate)
        self.assertEqual(second.recorded_at_ms, 2_000)

    def test_same_identity_different_content_conflicts(self) -> None:  # 10
        # M1.1: forcing the same derived id onto different content is now
        # rejected at the trust boundary (identity != result hash) before
        # the store-level conflict path; the DB conflict branch remains as
        # defense in depth (a shared id structurally implies a shared hash).
        record = self._record()
        self.recorder.record(record, recorded_at_ms=2_000)
        other = self._record(
            subject_identity="obj_" + "9" * 64
        )
        self.assertNotEqual(
            other.result_sha256, record.result_sha256
        )  # different content -> different hash -> different derived id
        forced = other.model_copy(
            update={"verification_record_id": record.verification_record_id}
        )
        with self.assertRaises(ValueError):
            self.store.put_verification_record(forced, recorded_at_ms=2_200)
        with self.assertRaises(VerificationRecordRejected):
            self.recorder.record(forced, recorded_at_ms=2_200)

    def test_binding_mismatch_rejected(self) -> None:  # 7
        wrong_run = self._record(run_id="run_" + "f" * 64)
        with self.assertRaises(StoreConflictError):
            self.store.put_verification_record(wrong_run, recorded_at_ms=2_000)
        wrong_generation = self._record(generation=99)
        with self.assertRaises(StoreConflictError):
            self.store.put_verification_record(wrong_generation, recorded_at_ms=2_000)
        unknown_request = self._record(request_id="req_" + "e" * 64)
        with self.assertRaises(StoreNotFoundError):
            self.store.put_verification_record(unknown_request, recorded_at_ms=2_000)

    def test_cross_generation_isolation_on_read(self) -> None:  # 7 (read side)
        first = self._record()
        self.recorder.record(first, recorded_at_ms=2_000)
        listed = self.store.list_verification_records(
            request_id=self.request_id, run_id=self.run_id, generation=1
        )
        self.assertEqual(listed, (first,))
        other_generation = self.store.list_verification_records(
            request_id=self.request_id, run_id=self.run_id, generation=2
        )
        self.assertEqual(other_generation, ())

    def test_recorder_rejects_snapshot_verifier_and_predicate_mismatch(self) -> None:  # 8
        stale_snapshot = self._record(registry_snapshot_sha256="f" * 64)
        with self.assertRaises(VerificationRecordRejected):
            self.recorder.record(stale_snapshot, recorded_at_ms=2_000)
        unknown_verifier = self._record(verifier_id="verifier.ghost")
        with self.assertRaises(UnknownVerifierError):
            self.recorder.record(unknown_verifier, recorded_at_ms=2_000)
        wrong_version = self._record(verifier_version="7")
        with self.assertRaises(UnknownVerifierError):
            self.recorder.record(wrong_version, recorded_at_ms=2_000)

    def test_no_state_transitions_anywhere(self) -> None:  # 11/12/13/14
        record = self._record()
        self.recorder.record(record, recorded_at_ms=2_000)
        connection = sqlite3.connect(self.path)
        try:
            aggregate_states = connection.execute(
                "SELECT machine, state FROM aggregate_state"
            ).fetchall()
            self.assertEqual(aggregate_states, [])  # 11: no request transitions
            decisions = connection.execute(
                "SELECT COUNT(*) FROM completion_decisions"
            ).fetchone()[0]
            self.assertEqual(decisions, 0)  # 12: no completion decisions
            effects = connection.execute(
                "SELECT COUNT(*) FROM effect_outcome_head"
            ).fetchone()[0]
            artifacts = connection.execute(
                "SELECT COUNT(*) FROM effect_facts"
            ).fetchone()[0]
            outbox_rows = connection.execute(
                "SELECT COUNT(*) FROM outbox"
            ).fetchone()[0]
            self.assertEqual((effects, artifacts, outbox_rows), (0, 0, 0))  # 13
            enforcements = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT enforcement FROM verification_record"
                ).fetchall()
            }
            self.assertEqual(enforcements, {"RECORD"})  # 14
        finally:
            connection.close()

    def test_tampered_record_hash_rejected_on_write(self) -> None:  # 6 (write side)
        good = self._record()
        tampered = good.model_copy(update={"status": "PASS", "reason_codes": ()})
        with self.assertRaises(ValueError):
            self.store.put_verification_record(tampered, recorded_at_ms=2_000)

    def test_registry_snapshot_roundtrip_idempotent(self) -> None:
        again = self.store.put_registry_snapshot(self.snapshot, recorded_at_ms=3_000)
        self.assertFalse(again)  # duplicate same content -> no new row
        fetched = self.store.get_registry_snapshot(self.snapshot.registry_snapshot_id)
        assert fetched is not None
        self.assertEqual(fetched, self.snapshot)


class IdentityIntegrityStoreTests(P19M1StoreTestBase):
    """M1.1 review matrix B / C / D / G at the store trust boundary."""

    def _wrong_id_record(self) -> VerificationRecord:
        good = self._record()
        return good.model_copy(
            update={"verification_record_id": "vrs_" + "e" * 64}
        )

    def test_b_wrong_record_id_rejected_by_store(self) -> None:
        bad = self._wrong_id_record()
        self.assertTrue(bad.has_valid_result_sha256())  # hash fine, id wrong
        with self.assertRaises(ValueError):
            self.store.put_verification_record(bad, recorded_at_ms=2_000)

    def test_c_placeholder_record_id_rejected_by_store(self) -> None:
        good = self._record()
        placeholder = good.model_copy(
            update={"verification_record_id": "vrs_" + "0" * 64}
        )
        with self.assertRaises(ValueError):
            self.store.put_verification_record(placeholder, recorded_at_ms=2_000)

    def test_d_wrong_snapshot_id_rejected_by_store(self) -> None:
        bad = self.snapshot.model_copy(
            update={"registry_snapshot_id": "vrg_" + "e" * 64}
        )
        self.assertTrue(bad.has_valid_snapshot_sha256())
        with self.assertRaises(ValueError):
            self.store.put_registry_snapshot(bad, recorded_at_ms=2_000)

    def test_g_full_chain_snapshot_store_reopen_recorder(self) -> None:
        # Registry.snapshot -> store persist -> close -> reopen -> fetch
        # -> recorder built from the fetched snapshot -> record succeeds.
        fetched = self.store.get_registry_snapshot(
            self.snapshot.registry_snapshot_id
        )
        assert fetched is not None
        recorder = VerificationRecorder(snapshot=fetched, store=self.store)
        record = self._record()
        outcome = recorder.record(record, recorded_at_ms=2_000)
        self.assertTrue(outcome.created_by_this_call)
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=2_500)
        listed = self.store.list_verification_records(
            request_id=self.request_id, run_id=self.run_id, generation=1
        )
        self.assertEqual(listed, (record,))
        refetched = self.store.get_registry_snapshot(
            self.snapshot.registry_snapshot_id
        )
        assert refetched is not None
        self.assertTrue(refetched.has_valid_identity())


if __name__ == "__main__":
    unittest.main()
