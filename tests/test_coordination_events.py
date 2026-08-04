from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contracts import TransitionEvent, new_state_snapshot
from total_gateway.coordination_events import (
    CoordinationResolution,
    create_coordination_event,
)
from total_gateway.store import (
    STORE_SCHEMA_VERSION,
    GatewayStateStore,
    StoreConflictError,
)
from tests.gateway_store_migration_support import downgrade_v12_to_v11


REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
HASH_A = "a" * 64
HASH_B = "b" * 64


def snapshot():
    return new_state_snapshot(
        "request",
        entity_id="request_state_coordination",
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        created_at_ms=1_000,
    )


def transition(event_id: str, *, expected_revision: int, to_state: str, occurred_at_ms: int):
    return TransitionEvent(
        event_id=event_id,
        event_type="request." + to_state.lower(),
        source_component_id="tiangong-total-gateway",
        machine="request",
        entity_id="request_state_coordination",
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        expected_revision=expected_revision,
        to_state=to_state,
        occurred_at_ms=occurred_at_ms,
        event_sha256=HASH_A,
    ).with_computed_event_sha256()


def coordination(kind: str, *, ordinal: int = 1, created_at_ms: int = 2_000, expires_at_ms: int = 20_000):
    return create_coordination_event(
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        kind=kind,
        ordinal=ordinal,
        payload_object_id="payload_coordination_001",
        payload_sha256=HASH_A,
        created_at_ms=created_at_ms,
        expires_at_ms=expires_at_ms,
    )


def resolution(event_id: str, outcome: str, *, resolved_at_ms: int = 3_000):
    positive = outcome in {"SKILL_SELECTED", "CONFIRMED"}
    resolver = "tiangong-total-gateway" if outcome.startswith("SKILL") or outcome == "NO_SKILL" else "tiangong-desktop"
    return CoordinationResolution(
        event_id=event_id,
        outcome=outcome,
        resolver_component_id=resolver,
        result_object_id="result_coordination_001" if positive else None,
        result_sha256=HASH_B if positive else None,
        resolved_at_ms=resolved_at_ms,
        resolution_sha256=HASH_A,
    ).with_computed_sha256()


class CoordinationEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        self.store.initialize_snapshot(snapshot())

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_need_skill_is_atomic_with_planning_and_never_resolves_inline(self) -> None:
        need = coordination("NEED_SKILL")
        applied = self.store.apply_event_with_coordination(
            transition("event_need_skill", expected_revision=0, to_state="PLANNING", occurred_at_ms=2_000),
            (need,),
            recorded_at_ms=2_100,
        )
        self.assertTrue(applied.decision.accepted)
        record = self.store.get_coordination_event(need.event_id)
        self.assertEqual(record.state, "PENDING")
        self.assertIsNone(record.resolution)
        self.assertEqual(self.store.list_dispatchable_coordination(
            consumer="skill_resolver", now_ms=2_100
        ), (record,))
        self.assertEqual(self.store.list_dispatchable_coordination(
            consumer="user_confirmation", now_ms=2_100
        ), ())

        duplicate = self.store.apply_event_with_coordination(
            transition("event_need_skill", expected_revision=0, to_state="PLANNING", occurred_at_ms=2_000),
            (need,),
            recorded_at_ms=2_200,
        )
        self.assertTrue(duplicate.duplicate)

    def test_need_confirmation_is_bound_to_waiting_state(self) -> None:
        self.store.apply_event(
            transition("event_planning", expected_revision=0, to_state="PLANNING", occurred_at_ms=2_000),
            recorded_at_ms=2_050,
        )
        need = coordination("NEED_CONFIRMATION", created_at_ms=2_100)
        applied = self.store.apply_event_with_coordination(
            transition(
                "event_need_confirmation",
                expected_revision=1,
                to_state="WAITING_CONFIRMATION",
                occurred_at_ms=2_100,
            ),
            (need,),
            recorded_at_ms=2_200,
        )
        self.assertTrue(applied.decision.accepted)
        self.assertEqual(self.store.get_snapshot("request", "request_state_coordination").state, "WAITING_CONFIRMATION")
        self.assertEqual(self.store.get_coordination_event(need.event_id).state, "PENDING")

        bad = coordination("NEED_CONFIRMATION", ordinal=2, created_at_ms=2_300)
        with self.assertRaisesRegex(ValueError, "resulting request state"):
            self.store.apply_event_with_coordination(
                transition("event_bad_confirmation", expected_revision=2, to_state="PLANNING", occurred_at_ms=2_300),
                (bad,),
                recorded_at_ms=2_400,
            )

    def test_claim_and_machine_resolution_are_leased_idempotent_and_authorized(self) -> None:
        need = coordination("NEED_SKILL")
        self.store.emit_coordination_event(need)
        with self.assertRaises(StoreConflictError):
            self.store.claim_coordination_event(
                need.event_id,
                consumer="user_confirmation",
                worker_id="ui",
                now_ms=2_100,
                lease_ms=5_000,
            )
        claimed = self.store.claim_coordination_event(
            need.event_id,
            consumer="skill_resolver",
            worker_id="resolver_1",
            now_ms=2_100,
            lease_ms=5_000,
        )
        self.assertEqual(claimed.state, "CLAIMED")
        with self.assertRaises(StoreConflictError):
            self.store.resolve_coordination_event(
                CoordinationResolution(
                    event_id=need.event_id,
                    outcome="CONFIRMED",
                    resolver_component_id="tiangong-desktop",
                    result_object_id="result_unauthorized",
                    result_sha256=HASH_B,
                    resolved_at_ms=3_000,
                    resolution_sha256=HASH_A,
                ).with_computed_sha256(),
                worker_id="resolver_1",
            )
        result = resolution(need.event_id, "SKILL_SELECTED")
        completed = self.store.resolve_coordination_event(result, worker_id="resolver_1")
        self.assertEqual(completed.state, "RESOLVED")
        self.assertEqual(completed.resolution, result)
        self.assertEqual(self.store.resolve_coordination_event(result, worker_id="resolver_1"), completed)
        changed = result.model_copy(update={"result_sha256": HASH_A, "resolution_sha256": HASH_A}).with_computed_sha256()
        with self.assertRaises(StoreConflictError):
            self.store.resolve_coordination_event(changed, worker_id="resolver_1")

    def test_expired_claim_cancels_and_lease_can_be_recovered(self) -> None:
        need = coordination("NEED_SKILL", expires_at_ms=10_000)
        self.store.emit_coordination_event(need)
        first = self.store.claim_coordination_event(
            need.event_id,
            consumer="skill_resolver",
            worker_id="resolver_1",
            now_ms=2_000,
            lease_ms=1_000,
        )
        self.assertEqual(first.attempt_count, 1)
        second = self.store.claim_coordination_event(
            need.event_id,
            consumer="skill_resolver",
            worker_id="resolver_2",
            now_ms=3_000,
            lease_ms=1_000,
        )
        self.assertEqual(second.attempt_count, 2)
        self.assertEqual(second.claimed_by, "resolver_2")
        expired = self.store.claim_coordination_event(
            need.event_id,
            consumer="skill_resolver",
            worker_id="resolver_3",
            now_ms=10_000,
            lease_ms=1_000,
        )
        self.assertEqual(expired.state, "CANCELLED")
        self.assertEqual(expired.cancel_reason_code, "coordination.expired")

    def test_coordination_insert_fault_rolls_back_request_state(self) -> None:
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER abort_coordination_insert
            BEFORE INSERT ON coordination_events
            BEGIN SELECT RAISE(ABORT, 'fault injection'); END
            """
        )
        need = coordination("NEED_SKILL")
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.apply_event_with_coordination(
                    transition("event_fault", expected_revision=0, to_state="PLANNING", occurred_at_ms=2_000),
                    (need,),
                    recorded_at_ms=2_100,
                )
        finally:
            self.store._connection.execute("DROP TRIGGER abort_coordination_insert")  # noqa: SLF001
        self.assertEqual(self.store.get_snapshot("request", "request_state_coordination").revision, 0)
        self.assertEqual(self.store.count_events(), 0)
        self.assertIsNone(self.store.get_coordination_event(need.event_id))

    def test_restart_health_tamper_and_v6_migration(self) -> None:
        need = coordination("NEED_SKILL")
        self.store.emit_coordination_event(need)
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=3_000)
        self.assertEqual(self.store.get_coordination_event(need.event_id).event, need)
        self.assertTrue(self.store.health_check(now_ms=3_000, full=True).healthy)
        self.store._connection.execute(  # noqa: SLF001 - deliberate semantic tamper
            "UPDATE coordination_events SET payload_sha256 = ? WHERE event_id = ?",
            (HASH_B, need.event_id),
        )
        self.assertFalse(self.store.health_check(now_ms=3_100, full=True).healthy)

        self.store.close()
        connection = sqlite3.connect(self.path)
        try:
            downgrade_v12_to_v11(connection)
            connection.execute("UPDATE coordination_events SET payload_sha256 = ? WHERE event_id = ?", (HASH_A, need.event_id))
            connection.execute("DROP INDEX outbox_dispatch_boundary_started")
            connection.execute("DROP TABLE outbox_dispatch_boundary")
            connection.execute("DROP TABLE request_inbound_payload")
            connection.execute("DROP INDEX channel_one_active_lease")
            connection.execute("DROP INDEX channel_cutover_scope_epoch")
            connection.execute("DROP TABLE channel_ownership_lease")
            connection.execute("DROP TABLE channel_drain_evidence")
            connection.execute("DROP TABLE channel_cutover")
            connection.execute("DROP INDEX shadow_decision_compare")
            connection.execute("DROP TABLE shadow_decision")
            connection.execute("DROP TABLE shadow_ingress")
            connection.execute("DROP INDEX coordination_dispatch_ready")
            connection.execute("DROP TABLE coordination_events")
            connection.execute("DELETE FROM schema_migrations WHERE version > 6")
            connection.execute("PRAGMA user_version = 6")
            connection.commit()
        finally:
            connection.close()
        self.store = GatewayStateStore.open(self.path, now_ms=4_000)
        self.assertEqual(self.store.health_check(now_ms=4_000, full=True).schema_version, STORE_SCHEMA_VERSION)
        self.assertIsNone(self.store.get_coordination_event(need.event_id))

    def test_two_connections_allow_only_one_active_claim(self) -> None:
        need = coordination("NEED_SKILL")
        self.store.emit_coordination_event(need)
        other = GatewayStateStore.open(self.path, now_ms=2_000)
        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def claim(store: GatewayStateStore, worker: str) -> None:
            barrier.wait()
            try:
                store.claim_coordination_event(
                    need.event_id,
                    consumer="skill_resolver",
                    worker_id=worker,
                    now_ms=2_100,
                    lease_ms=5_000,
                )
                outcomes.append("claimed")
            except StoreConflictError:
                outcomes.append("conflict")

        threads = [
            threading.Thread(target=claim, args=(self.store, "resolver_1")),
            threading.Thread(target=claim, args=(other, "resolver_2")),
        ]
        for item in threads:
            item.start()
        for item in threads:
            item.join(timeout=5)
        other.close()
        self.assertEqual(sorted(outcomes), ["claimed", "conflict"])


if __name__ == "__main__":
    unittest.main()
