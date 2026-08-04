import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contracts import GatePromotionRecord, TransitionEvent, new_state_snapshot
from contracts.cutover import derive_gate_promotion_id
from total_gateway.store import (
    APPLICATION_ID,
    STORE_SCHEMA_VERSION,
    GatewayStateStore,
    StoreConflictError,
    StoreCorruptionError,
    StoreMigrationError,
    expected_store_schema_sha256,
)


REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
HASH_A = "a" * 64


def gate_promotion(**overrides):
    values = {
        "promotion_epoch": 1, "expected_current_promotion_sha256": "0" * 64,
        "from_gate": "BASELINE", "to_gate": "G0", "from_mode": "legacy_observe",
        "to_mode": "legacy_observe", "build_id": "v21-g0-store-test",
        "source_manifest_sha256": HASH_A, "contract_set_hash": HASH_A,
        "config_hash": HASH_A, "evidence_refs": ("receipt:g0",),
        "rollback_target": "current_source_baseline", "promoted_at_ms": 2_000,
        "promotion_sha256": "0" * 64,
    }
    values.update(overrides)
    values["promotion_id"] = derive_gate_promotion_id(
        values["to_gate"], values["promotion_epoch"], values["build_id"], values["source_manifest_sha256"]
    )
    return GatePromotionRecord(**values).with_computed_sha256()


def snapshot():
    return new_state_snapshot(
        "request",
        entity_id="request_state_001",
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        created_at_ms=1_000,
    )


def event(event_id="event_001", **overrides):
    values = {
        "event_id": event_id,
        "event_type": "request.planning_started",
        "source_component_id": "tiangong-total-gateway",
        "machine": "request",
        "entity_id": "request_state_001",
        "request_id": REQUEST_ID,
        "run_id": RUN_ID,
        "generation": 1,
        "expected_revision": 0,
        "to_state": "PLANNING",
        "occurred_at_ms": 2_000,
        "event_sha256": HASH_A,
    }
    values.update(overrides)
    return TransitionEvent(**values).with_computed_event_sha256()


class GatewayStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=1_000)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_migration_pragmas_schema_and_health_are_verified(self) -> None:
        health = self.store.health_check(now_ms=1_000, full=True)
        self.assertTrue(health.healthy)
        self.assertEqual(health.schema_version, STORE_SCHEMA_VERSION)
        self.assertEqual(health.schema_sha256, expected_store_schema_sha256())
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID)
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                STORE_SCHEMA_VERSION,
            )
        finally:
            connection.close()

    def test_v21_gate_promotion_is_head_cas_idempotent_and_persistent(self) -> None:
        first = gate_promotion()
        self.assertTrue(self.store.promote_v21_gate(first))
        self.assertFalse(self.store.promote_v21_gate(first))
        self.assertEqual(
            self.store.get_v21_gate_promotion_head(),
            (1, "G0", "legacy_observe", first.promotion_sha256),
        )
        with self.assertRaises(StoreConflictError):
            self.store.promote_v21_gate(gate_promotion(build_id="conflict"))
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=3_000)
        self.assertEqual(self.store.get_v21_gate_promotion_head()[3], first.promotion_sha256)

    def test_initial_snapshot_is_idempotent_but_conflicting_identity_is_rejected(self) -> None:
        initial = snapshot()
        self.assertTrue(self.store.initialize_snapshot(initial))
        self.assertFalse(self.store.initialize_snapshot(initial))
        changed = initial.model_copy(update={"generation": 2})
        with self.assertRaises(StoreConflictError):
            self.store.initialize_snapshot(changed)

    def test_event_and_state_cas_commit_together_and_survive_reopen(self) -> None:
        initial = snapshot()
        self.store.initialize_snapshot(initial)
        result = self.store.apply_event(event(), recorded_at_ms=2_100)
        self.assertTrue(result.persisted_by_this_call)
        self.assertTrue(result.decision.accepted)
        self.assertEqual(result.decision.current.revision, 1)
        self.assertEqual(self.store.count_events(), 1)
        self.store.close()

        self.store = GatewayStateStore.open(self.path, now_ms=3_000)
        restored = self.store.get_snapshot("request", "request_state_001")
        self.assertIsNotNone(restored)
        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.state, "PLANNING")
        self.assertEqual(restored.last_event_id, "event_001")

    def test_duplicate_returns_first_decision_and_same_id_cannot_change_content(self) -> None:
        self.store.initialize_snapshot(snapshot())
        first = self.store.apply_event(event(), recorded_at_ms=2_100)
        duplicate = self.store.apply_event(event(), recorded_at_ms=2_200)
        self.assertFalse(duplicate.persisted_by_this_call)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.decision, first.decision)
        self.assertEqual(self.store.count_events(), 1)

        changed = event(to_state="QUEUED")
        with self.assertRaises(StoreConflictError):
            self.store.apply_event(changed, recorded_at_ms=2_300)

    def test_rejected_revision_conflict_is_durable_and_cannot_replay_later(self) -> None:
        self.store.initialize_snapshot(snapshot())
        self.store.apply_event(event(), recorded_at_ms=2_100)
        stale = event(
            "event_stale_001",
            event_type="request.queue_requested",
            to_state="QUEUED",
            occurred_at_ms=2_200,
        )
        rejected = self.store.apply_event(stale, recorded_at_ms=2_300)
        self.assertFalse(rejected.decision.accepted)
        self.assertEqual(rejected.decision.disposition, "REVISION_CONFLICT")
        self.assertEqual(self.store.count_events(), 2)
        repeated = self.store.apply_event(stale, recorded_at_ms=2_400)
        self.assertTrue(repeated.duplicate)
        self.assertEqual(repeated.decision, rejected.decision)
        self.assertEqual(self.store.get_snapshot("request", "request_state_001").revision, 1)

    def test_two_connections_serialize_same_revision_and_only_one_event_applies(self) -> None:
        self.store.initialize_snapshot(snapshot())
        other = GatewayStateStore.open(self.path, now_ms=1_500)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def apply(store, candidate):
            try:
                barrier.wait(timeout=5)
                results.append(store.apply_event(candidate, recorded_at_ms=2_100))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        first = threading.Thread(target=apply, args=(self.store, event("event_writer_001")))
        second = threading.Thread(
            target=apply,
            args=(
                other,
                event(
                    "event_writer_002",
                    event_type="request.queued",
                    to_state="QUEUED",
                ),
            ),
        )
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)
        other.close()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(item.decision.accepted for item in results), 1)
        self.assertEqual(
            {item.decision.disposition for item in results},
            {"APPLIED", "REVISION_CONFLICT"},
        )
        self.assertEqual(self.store.get_snapshot("request", "request_state_001").revision, 1)
        self.assertEqual(self.store.count_events(), 2)

    def test_fault_between_event_insert_and_state_update_rolls_back_both(self) -> None:
        self.store.initialize_snapshot(snapshot())
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_state_update
            BEFORE UPDATE ON aggregate_state
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.apply_event(event(), recorded_at_ms=2_100)
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_state_update")  # noqa: SLF001
        self.assertEqual(self.store.count_events(), 0)
        self.assertEqual(self.store.get_snapshot("request", "request_state_001").revision, 0)

    def test_application_payload_tamper_fails_quick_health_even_when_sqlite_is_valid(self) -> None:
        self.store.initialize_snapshot(snapshot())
        self.store._connection.execute(  # noqa: SLF001 - deliberate corruption injection
            """
            UPDATE aggregate_state
            SET snapshot_json = json_set(snapshot_json, '$.state', 'QUEUED')
            WHERE machine = 'request' AND entity_id = 'request_state_001'
            """
        )
        health = self.store.health_check(now_ms=2_000)
        self.assertFalse(health.healthy)
        self.assertEqual(health.reason_code, "store.check.failed")


class GatewayStoreCorruptionTests(unittest.TestCase):
    def test_random_database_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gateway.sqlite3"
            original = b"not-a-sqlite-database"
            path.write_bytes(original)
            with self.assertRaises(StoreCorruptionError):
                GatewayStateStore.open(path, now_ms=1_000)
            self.assertEqual(path.read_bytes(), original)

    def test_schema_tamper_or_newer_version_is_rejected_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gateway.sqlite3"
            store = GatewayStateStore.open(path, now_ms=1_000)
            store.close()
            connection = sqlite3.connect(path)
            connection.execute("DROP INDEX event_request_sequence")
            connection.commit()
            connection.close()
            with self.assertRaises(StoreMigrationError):
                GatewayStateStore.open(path, now_ms=2_000)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gateway.sqlite3"
            store = GatewayStateStore.open(path, now_ms=1_000)
            store.close()
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version = 999")
            connection.close()
            with self.assertRaises(StoreMigrationError):
                GatewayStateStore.open(path, now_ms=2_000)


if __name__ == "__main__":
    unittest.main()
