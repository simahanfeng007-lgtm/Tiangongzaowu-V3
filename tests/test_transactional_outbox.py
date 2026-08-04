import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import TransitionEvent, new_state_snapshot
from total_gateway.outbox import OutboxIntent, derive_outbox_id
from total_gateway.store import GatewayStateStore, StoreConflictError


REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
EFFECT_ID = "eff_" + "3" * 64
HASH_A = "a" * 64
HASH_B = "b" * 64


def snapshot():
    return new_state_snapshot(
        "request",
        entity_id="request_state_001",
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        created_at_ms=1_000,
    )


def event(event_id: str = "event_001", *, expected_revision: int = 0) -> TransitionEvent:
    return TransitionEvent(
        event_id=event_id,
        event_type="request.planning_started",
        source_component_id="tiangong-total-gateway",
        machine="request",
        entity_id="request_state_001",
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        expected_revision=expected_revision,
        to_state="PLANNING",
        occurred_at_ms=2_000,
        event_sha256=HASH_A,
    ).with_computed_event_sha256()


def intent(*, payload_sha256: str = HASH_A) -> OutboxIntent:
    destination = "tiangong-backend"
    return OutboxIntent(
        outbox_id=derive_outbox_id(EFFECT_ID, destination, payload_sha256),
        effect_id=EFFECT_ID,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        destination_component_id=destination,
        intent_kind="EXECUTION",
        payload_object_id="payload_001",
        payload_sha256=payload_sha256,
        created_at_ms=2_000,
        intent_sha256=HASH_B,
    ).with_computed_sha256()


class TransactionalOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        self.store.initialize_snapshot(snapshot())

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_state_event_and_outbox_commit_together(self) -> None:
        outgoing = intent()
        result = self.store.apply_event_with_outbox(event(), (outgoing,), recorded_at_ms=2_100)
        self.assertTrue(result.decision.accepted)
        self.assertEqual(self.store.get_snapshot("request", "request_state_001").revision, 1)
        record = self.store.get_outbox(outgoing.outbox_id)
        self.assertEqual(record.intent, outgoing)
        self.assertEqual(record.state, "PENDING")
        self.assertEqual(self.store.list_dispatchable_outbox(now_ms=2_100)[0], record)

    def test_outbox_insert_fault_rolls_back_event_and_state(self) -> None:
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_outbox_insert
            BEFORE INSERT ON outbox
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        outgoing = intent()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.apply_event_with_outbox(event(), (outgoing,), recorded_at_ms=2_100)
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_outbox_insert")  # noqa: SLF001
        self.assertEqual(self.store.count_events(), 0)
        self.assertEqual(self.store.get_snapshot("request", "request_state_001").revision, 0)
        self.assertIsNone(self.store.get_outbox(outgoing.outbox_id))

    def test_duplicate_event_requires_identical_outbox_set(self) -> None:
        outgoing = intent()
        self.store.apply_event_with_outbox(event(), (outgoing,), recorded_at_ms=2_100)
        duplicate = self.store.apply_event_with_outbox(event(), (outgoing,), recorded_at_ms=2_200)
        self.assertTrue(duplicate.duplicate)
        with self.assertRaises(StoreConflictError):
            self.store.apply_event_with_outbox(event(), (), recorded_at_ms=2_300)

    def test_rejected_event_is_durable_but_cannot_emit_outbox(self) -> None:
        self.store.apply_event(event(), recorded_at_ms=2_100)
        rejected = event("event_stale", expected_revision=0).model_copy(
            update={"event_sha256": HASH_A}
        ).with_computed_event_sha256()
        result = self.store.apply_event_with_outbox(rejected, (intent(payload_sha256=HASH_B),), recorded_at_ms=2_200)
        self.assertFalse(result.decision.accepted)
        self.assertEqual(self.store.count_events(), 2)
        self.assertEqual(self.store.list_dispatchable_outbox(now_ms=3_000), ())

    def test_claim_lease_and_terminal_result_are_cas_and_idempotent(self) -> None:
        outgoing = intent()
        self.store.apply_event_with_outbox(event(), (outgoing,), recorded_at_ms=2_100)
        claimed = self.store.claim_outbox(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            now_ms=2_200,
            lease_ms=5_000,
        )
        self.assertEqual(claimed.state, "CLAIMED")
        self.assertEqual(claimed.attempt_count, 1)
        with self.assertRaises(StoreConflictError):
            self.store.claim_outbox(
                outgoing.outbox_id,
                worker_id="dispatcher_002",
                now_ms=2_300,
                lease_ms=5_000,
            )
        completed = self.store.record_outbox_result(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            outcome="ACKED",
            result_sha256=HASH_B,
            dispatched_at_ms=2_400,
        )
        self.assertEqual(completed.state, "ACKED")
        duplicate = self.store.record_outbox_result(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            outcome="ACKED",
            result_sha256=HASH_B,
            dispatched_at_ms=2_500,
        )
        self.assertEqual(duplicate, completed)

    def test_dispatch_boundary_result_and_finalization_are_durable(self) -> None:
        outgoing = intent()
        self.store.apply_event_with_outbox(event(), (outgoing,), recorded_at_ms=2_100)
        self.store.claim_outbox(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            now_ms=2_200,
            lease_ms=5_000,
        )
        boundary = self.store.mark_outbox_dispatch_started(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            gateway_epoch=7,
            ticket_object_id="obj_" + "4" * 64,
            ticket_sha256=HASH_A,
            started_at_ms=2_300,
        )
        self.assertEqual(self.store.get_outbox_dispatch_boundary(outgoing.outbox_id), boundary)
        completed = self.store.record_outbox_dispatch_result(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            outcome="ACKED",
            result_object_id="obj_" + "5" * 64,
            result_sha256=HASH_B,
            completed_at_ms=2_400,
        )
        self.assertEqual(completed.state, "ACKED")
        pending = self.store.list_unfinalized_outbox_results()
        self.assertEqual(len(pending), 1)
        finalized = self.store.mark_outbox_finalized(
            outgoing.outbox_id,
            finalized_at_ms=2_500,
            finalization_sha256=HASH_A,
        )
        self.assertEqual(finalized.finalization_sha256, HASH_A)
        self.assertEqual(self.store.list_unfinalized_outbox_results(), ())
        self.assertTrue(self.store.health_check(now_ms=2_600, full=True).healthy)

    def test_crossed_boundary_after_expired_claim_becomes_ambiguous_without_reclaim(self) -> None:
        outgoing = intent()
        self.store.apply_event_with_outbox(event(), (outgoing,), recorded_at_ms=2_100)
        self.store.claim_outbox(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            now_ms=2_200,
            lease_ms=1_000,
        )
        self.store.mark_outbox_dispatch_started(
            outgoing.outbox_id,
            worker_id="dispatcher_001",
            gateway_epoch=7,
            ticket_object_id="obj_" + "4" * 64,
            ticket_sha256=HASH_A,
            started_at_ms=2_300,
        )
        recovered = self.store.mark_expired_outbox_ambiguous(
            outgoing.outbox_id,
            observed_at_ms=3_200,
            result_object_id="obj_" + "5" * 64,
            result_sha256=HASH_B,
        )
        self.assertEqual(recovered.state, "AMBIGUOUS")
        self.assertEqual(self.store.list_dispatchable_outbox(now_ms=4_000), ())
        self.assertTrue(self.store.health_check(now_ms=4_000, full=True).healthy)


if __name__ == "__main__":
    unittest.main()
