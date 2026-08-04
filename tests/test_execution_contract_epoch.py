"""V17 execution contract epoch（草案 §3.3 ExecutionContractCutover 第 5 步）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import derive_effect_identity, derive_run_identity
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.store import GatewayStateStore, StoreConflictError

REQUEST_ID = "req_" + "5" * 64
RUN_ID = derive_run_identity(REQUEST_ID, 1).run_id
HASH_A = "a" * 64
HASH_B = "b" * 64


def make_claim() -> EffectClaim:
    identity = derive_effect_identity(
        request_id=REQUEST_ID, run_id=RUN_ID, run_sequence=1, generation=1,
        effect_kind="execution", ordinal=0, intent_sha256=HASH_A,
    )
    return EffectClaim(
        **identity.model_dump(), owner_component_id="tiangong-backend",
        claimed_at_ms=1_000, claim_sha256=HASH_B,
    ).with_computed_sha256()


def make_result(effect_id: str) -> EffectResult:
    return EffectResult(
        result_id="r1", effect_id=effect_id, status="SUCCEEDED", fact_id="f1",
        result_object_id="o1", result_object_sha256=HASH_B, evidence_sha256=HASH_A,
        observed_at_ms=1_200, result_sha256=HASH_B,
    ).with_computed_sha256()


class ExecutionContractEpochTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = GatewayStateStore.open(Path(self.temporary.name) / "gateway.sqlite3", now_ms=900)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_activation_requires_fence_drain_and_disposition(self) -> None:
        # 无 fence → 拒绝
        with self.assertRaises(StoreConflictError):
            self.store.activate_execution_contract_epoch(contract_epoch="vNext", dispositions=[], now_ms=1_000)
        self.store.increment_action_fence(reason="cutover", now_ms=1_100)
        # 有非终态 effect → 拒绝
        claim = make_claim()
        self.store.claim_effect(claim)
        with self.assertRaises(StoreConflictError):
            self.store.activate_execution_contract_epoch(contract_epoch="vNext", dispositions=[], now_ms=1_200)
        # 完成 disposition → 允许
        self.store.mark_effect_started(claim.effect_id, started_at_ms=1_150)
        self.store.complete_effect(make_result(claim.effect_id))
        digest = self.store.activate_execution_contract_epoch(
            contract_epoch="vNext",
            dispositions=[{"effect_id": claim.effect_id, "disposition": "SUCCEEDED"}],
            now_ms=1_300,
        )
        self.assertEqual(len(digest), 64)
        status = self.store.execution_contract_epoch_status()
        self.assertTrue(status["activated"])
        self.assertEqual(status["contract_epoch"], "vNext")
        self.assertEqual(status["fence_epoch_at_activation"], 1)
        # 幂等
        again = self.store.activate_execution_contract_epoch(
            contract_epoch="vNext",
            dispositions=[{"effect_id": claim.effect_id, "disposition": "SUCCEEDED"}],
            now_ms=1_400,
        )
        self.assertEqual(again, digest)

    def test_activation_conflict_on_different_epoch(self) -> None:
        self.store.increment_action_fence(reason="cutover", now_ms=1_000)
        self.store.activate_execution_contract_epoch(contract_epoch="vNext", dispositions=[], now_ms=1_100)
        with self.assertRaises(StoreConflictError):
            self.store.activate_execution_contract_epoch(contract_epoch="vOther", dispositions=[], now_ms=1_200)


if __name__ == "__main__":
    unittest.main()
