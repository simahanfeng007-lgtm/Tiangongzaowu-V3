import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contracts import derive_run_identity
from total_gateway.store import GatewayStateStore, StoreConflictError


REQUEST_ID = "req_" + "1" * 64
RUN_1 = derive_run_identity(REQUEST_ID, 1).run_id
RUN_2 = derive_run_identity(REQUEST_ID, 2).run_id
HASH_A = "a" * 64
HASH_B = "b" * 64


class GenerationCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def acquire(self, **overrides):
        values = {
            "request_id": REQUEST_ID,
            "run_id": RUN_1,
            "run_sequence": 1,
            "generation": 1,
            "gateway_epoch": 3,
            "lease_id": "lease_001",
            "owner_instance_id": "gateway_instance_001",
            "issued_at_ms": 1_000,
            "lease_duration_ms": 10_000,
        }
        values.update(overrides)
        return self.store.acquire_generation_lease(**values)

    def test_acquire_duplicate_heartbeat_and_release_are_persistent(self) -> None:
        lease, created = self.acquire()
        self.assertTrue(created)
        duplicate, created = self.acquire()
        self.assertFalse(created)
        self.assertEqual(duplicate, lease)
        heartbeat = self.store.heartbeat_generation_lease(
            REQUEST_ID,
            lease_id="lease_001",
            owner_instance_id="gateway_instance_001",
            now_ms=2_000,
            lease_duration_ms=10_000,
        )
        self.assertGreater(heartbeat.fence.expires_at_ms, lease.fence.expires_at_ms)
        self.assertEqual(heartbeat.fence.supersedes_fence_id, lease.fence.fence_id)
        released = self.store.release_generation(REQUEST_ID, released_at_ms=3_000)
        self.assertEqual(released.status, "RELEASED")
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=4_000)
        self.assertEqual(self.store.get_generation(REQUEST_ID), released)

    def test_current_result_is_accepted_but_superseded_generation_is_late_ignored(self) -> None:
        first, _ = self.acquire()
        accepted = self.store.record_fenced_result(
            first.fence,
            result_id="result_current_001",
            result_sha256=HASH_A,
            observed_at_ms=2_000,
        )
        self.assertEqual(accepted.disposition, "ACCEPTED")
        second, _ = self.acquire(
            run_id=RUN_2,
            run_sequence=2,
            generation=2,
            lease_id="lease_002",
            issued_at_ms=3_000,
        )
        late = self.store.record_fenced_result(
            first.fence,
            result_id="result_late_001",
            result_sha256=HASH_B,
            observed_at_ms=4_000,
        )
        self.assertEqual(late.disposition, "LATE_IGNORED")
        current = self.store.record_fenced_result(
            second.fence,
            result_id="result_current_002",
            result_sha256=HASH_B,
            observed_at_ms=4_000,
        )
        self.assertEqual(current.disposition, "ACCEPTED")

    def test_cancelled_generation_ignores_late_result_and_rejects_heartbeat(self) -> None:
        lease, _ = self.acquire()
        cancelled = self.store.cancel_generation(
            REQUEST_ID,
            reason_code="request.user_cancelled",
            cancelled_at_ms=2_000,
        )
        self.assertEqual(cancelled.status, "CANCELLED")
        decision = self.store.record_fenced_result(
            lease.fence,
            result_id="result_cancelled_001",
            result_sha256=HASH_A,
            observed_at_ms=2_100,
        )
        self.assertEqual(decision.disposition, "CANCELLED_IGNORED")
        with self.assertRaises(StoreConflictError):
            self.store.heartbeat_generation_lease(
                REQUEST_ID,
                lease_id="lease_001",
                owner_instance_id="gateway_instance_001",
                now_ms=2_200,
                lease_duration_ms=10_000,
            )

    def test_expired_fence_is_persistently_ignored(self) -> None:
        lease, _ = self.acquire(lease_duration_ms=1_000)
        decision = self.store.record_fenced_result(
            lease.fence,
            result_id="result_expired_001",
            result_sha256=HASH_A,
            observed_at_ms=8_000,
        )
        self.assertEqual(decision.disposition, "FENCED_IGNORED")
        duplicate = self.store.record_fenced_result(
            lease.fence,
            result_id="result_expired_001",
            result_sha256=HASH_A,
            observed_at_ms=8_000,
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.disposition, decision.disposition)

    def test_fence_insert_fault_rolls_back_request_generation(self) -> None:
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_generation_fence
            BEFORE INSERT ON generation_fences
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.acquire()
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_generation_fence")  # noqa: SLF001
        self.assertIsNone(self.store.get_generation(REQUEST_ID))

    def test_two_connections_cannot_own_same_generation_with_different_lease(self) -> None:
        other = GatewayStateStore.open(self.path, now_ms=950)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def acquire(store, lease_id):
            try:
                barrier.wait(timeout=5)
                results.append(
                    store.acquire_generation_lease(
                        request_id=REQUEST_ID,
                        run_id=RUN_1,
                        run_sequence=1,
                        generation=1,
                        gateway_epoch=3,
                        lease_id=lease_id,
                        owner_instance_id=lease_id,
                        issued_at_ms=1_000,
                        lease_duration_ms=10_000,
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = (
            threading.Thread(target=acquire, args=(self.store, "lease_001")),
            threading.Thread(target=acquire, args=(other, "lease_002")),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        other.close()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], StoreConflictError)

    def test_health_detects_generation_column_tamper(self) -> None:
        self.acquire()
        self.store._connection.execute(  # noqa: SLF001 - deliberate corruption injection
            "UPDATE request_generation SET owner_instance_id = 'tampered'"
        )
        # Owner is not embedded in GenerationFence, so tamper a bound field too.
        self.store._connection.execute(  # noqa: SLF001
            "UPDATE request_generation SET current_generation = 9"
        )
        self.assertFalse(self.store.health_check(now_ms=2_000).healthy)


if __name__ == "__main__":
    unittest.main()
