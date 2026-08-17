import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contracts import derive_effect_identity, derive_run_identity
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.store import GatewayStateStore, StoreConflictError


REQUEST_ID = "req_" + "1" * 64
RUN_ID = derive_run_identity(REQUEST_ID, 1).run_id
HASH_A = "a" * 64
HASH_B = "b" * 64


def claim(*, intent_sha256: str = HASH_A, claimed_at_ms: int = 1_000) -> EffectClaim:
    identity = derive_effect_identity(
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        run_sequence=1,
        generation=1,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=intent_sha256,
    )
    return EffectClaim(
        **identity.model_dump(),
        owner_component_id="tiangong-backend",
        claimed_at_ms=claimed_at_ms,
        claim_sha256=HASH_B,
    ).with_computed_sha256()


def result(effect_id: str, *, evidence_sha256: str = HASH_A) -> EffectResult:
    return EffectResult(
        result_id="effect_result_001",
        effect_id=effect_id,
        status="SUCCEEDED",
        fact_id="fact_execution_001",
        result_object_id="result_object_001",
        result_object_sha256=HASH_B,
        evidence_sha256=evidence_sha256,
        observed_at_ms=1_200,
        result_sha256=HASH_B,
    ).with_computed_sha256()


class EffectLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_stable_effect_identity_and_duplicate_claim_return_first_record(self) -> None:
        first_claim = claim()
        record, created = self.store.claim_effect(first_claim)
        self.assertTrue(created)
        duplicate, created = self.store.claim_effect(claim(claimed_at_ms=1_100))
        self.assertFalse(created)
        self.assertEqual(duplicate, record)
        changed = claim(intent_sha256=HASH_B)
        self.assertNotEqual(changed.effect_id, first_claim.effect_id)

    def test_started_effect_commits_one_immutable_first_result(self) -> None:
        effect_claim = claim()
        self.store.claim_effect(effect_claim)
        self.store.mark_effect_started(effect_claim.effect_id, started_at_ms=1_100)
        first = self.store.complete_effect(result(effect_claim.effect_id))
        self.assertEqual(first.state, "SUCCEEDED")
        self.assertEqual(self.store.complete_effect(result(effect_claim.effect_id)), first)
        with self.assertRaises(StoreConflictError):
            self.store.complete_effect(result(effect_claim.effect_id, evidence_sha256=HASH_B))

    def test_restart_recovery_turns_orchestration_started_effect_failed_final(self) -> None:
        effect_claim = claim()
        self.store.claim_effect(effect_claim)
        self.store.mark_effect_started(effect_claim.effect_id, started_at_ms=1_100)
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=1_200)
        recovered = self.store.recover_started_effects(now_ms=1_300)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, "FAILED_FINAL")
        self.assertEqual(
            recovered[0].result.error_code, "effect.execution_interrupted_by_restart"
        )
        self.assertEqual(self.store.recover_started_effects(now_ms=1_400), ())

    def test_restart_recovery_turns_provider_started_effect_ambiguous(self) -> None:
        effect_claim = claim().model_copy(
            update={"owner_component_id": "tiangong-total-gateway"}
        ).with_computed_sha256()
        self.store.claim_effect(effect_claim)
        self.store.mark_effect_started(effect_claim.effect_id, started_at_ms=1_100)
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=1_200)
        recovered = self.store.recover_started_effects(now_ms=1_300)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, "AMBIGUOUS")
        self.assertEqual(
            recovered[0].result.error_code, "effect.result_missing_after_restart"
        )

    def test_result_write_fault_leaves_effect_at_started_boundary(self) -> None:
        effect_claim = claim()
        self.store.claim_effect(effect_claim)
        self.store.mark_effect_started(effect_claim.effect_id, started_at_ms=1_100)
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_effect_result
            BEFORE UPDATE OF result_json ON effect_ledger
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.complete_effect(result(effect_claim.effect_id))
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_effect_result")  # noqa: SLF001
        self.assertEqual(self.store.get_effect(effect_claim.effect_id).state, "SIDE_EFFECT_STARTED")

    def test_two_connections_claim_same_effect_once(self) -> None:
        other = GatewayStateStore.open(self.path, now_ms=950)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def consume(store: GatewayStateStore, candidate: EffectClaim) -> None:
            try:
                barrier.wait(timeout=5)
                results.append(store.claim_effect(candidate))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = (
            threading.Thread(target=consume, args=(self.store, claim(claimed_at_ms=1_000))),
            threading.Thread(target=consume, args=(other, claim(claimed_at_ms=1_001))),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        other.close()
        self.assertEqual(errors, [])
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(results[0][0], results[1][0])

    def test_health_detects_effect_column_tamper(self) -> None:
        self.store.claim_effect(claim())
        self.store._connection.execute(  # noqa: SLF001 - deliberate corruption injection
            "UPDATE effect_ledger SET owner_component_id = 'tampered'"
        )
        self.assertFalse(self.store.health_check(now_ms=2_000).healthy)


if __name__ == "__main__":
    unittest.main()
