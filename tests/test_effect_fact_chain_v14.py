import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import derive_effect_identity, derive_run_identity
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.store import (
    GatewayStateStore,
    STORE_SCHEMA_VERSION,
    StoreCasConflict,
    StoreConflictError,
    StoreNotFoundError,
)

REQUEST_ID = "req_" + "2" * 64
RUN_ID = derive_run_identity(REQUEST_ID, 1).run_id
HASH_A = "a" * 64
HASH_B = "b" * 64


def make_claim(*, intent_sha256: str = HASH_A, claimed_at_ms: int = 1_000) -> EffectClaim:
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


def make_result(effect_id: str, *, status: str = "SUCCEEDED", observed_at_ms: int = 1_200) -> EffectResult:
    return EffectResult(
        result_id="effect_result_v14_001",
        effect_id=effect_id,
        status=status,
        fact_id="fact_execution_v14_001",
        result_object_id="result_object_v14_001",
        result_object_sha256=HASH_B,
        evidence_sha256=HASH_A,
        observed_at_ms=observed_at_ms,
        result_sha256=HASH_B,
    ).with_computed_sha256()


class EffectFactChainV14Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_v14_schema_present(self) -> None:
        tables = {
            r[0]
            for r in self.store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("effect_attempts", tables)
        self.assertIn("effect_facts", tables)
        self.assertIn("action_fence", tables)
        version = self.store._connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, STORE_SCHEMA_VERSION)

    def test_claim_writes_attempt_and_claim_fact(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        attempts = self.store.list_effect_attempts(claim.effect_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["state"], "CLAIMED")
        self.assertEqual(attempts[0]["attempt"], 1)
        facts = self.store.list_effect_facts(claim.effect_id)
        self.assertEqual([f["fact_kind"] for f in facts], ["CLAIM"])
        self.assertEqual(facts[0]["prev_fact_sha256"], "0" * 64)

    def test_started_and_complete_extend_chain(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
        self.store.complete_effect(make_result(claim.effect_id))
        kinds = [f["fact_kind"] for f in self.store.list_effect_facts(claim.effect_id)]
        self.assertEqual(kinds, ["CLAIM", "STARTED", "RECEIPT"])
        facts = self.store.list_effect_facts(claim.effect_id)
        # 链式 prev 哈希逐环相连
        self.assertEqual(facts[1]["prev_fact_sha256"], facts[0]["payload_sha256"])
        self.assertEqual(facts[2]["prev_fact_sha256"], facts[1]["payload_sha256"])
        attempt = self.store.get_effect_attempt(claim.effect_id, 1)
        self.assertEqual(attempt["state"], "SUCCEEDED")
        self.assertIsNotNone(attempt["terminal_at_ms"])

    def test_reconciliation_pna_then_continuation_reservation(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
        out = self.store.record_effect_reconciliation(
            effect_id=claim.effect_id,
            attempt=1,
            verdict="PROVEN_NOT_APPLIED",
            evidence={"absence_verifier": "workspace_hash_unchanged", "nonce_consumed": True},
            observed_at_ms=1_300,
        )
        self.assertFalse(out["contradiction"])
        self.assertEqual(out["attempt_state"], "RECONCILE_REQUIRED")
        cont = self.store.continue_effect_after_pna(
            effect_id=claim.effect_id, old_attempt=1, now_ms=1_400
        )
        self.assertEqual(cont["new_attempt"], 2)
        self.assertEqual(cont["claim_revision"], 2)
        attempts = self.store.list_effect_attempts(claim.effect_id)
        self.assertEqual(attempts[0]["state"], "FENCED")
        self.assertEqual(attempts[1]["state"], "RESERVED")

    def test_reconciliation_contradiction_detected(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
        self.store.record_effect_reconciliation(
            effect_id=claim.effect_id, attempt=1, verdict="PROVEN_NOT_APPLIED",
            evidence={"absence_verifier": "x"}, observed_at_ms=1_300,
        )
        out = self.store.record_effect_reconciliation(
            effect_id=claim.effect_id, attempt=1, verdict="APPLIED",
            evidence={"late_receipt": "target_watermark_advanced"}, observed_at_ms=1_500,
        )
        self.assertTrue(out["contradiction"])
        kinds = [f["fact_kind"] for f in self.store.list_effect_facts(claim.effect_id)]
        self.assertIn("CONTRADICTION", kinds)

    def test_reconciliation_final_verdict_conflict_rejected(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
        self.store.record_effect_reconciliation(
            effect_id=claim.effect_id, attempt=1, verdict="APPLIED",
            evidence={"receipt": "ok"}, observed_at_ms=1_300,
        )
        with self.assertRaises(StoreConflictError):
            self.store.record_effect_reconciliation(
                effect_id=claim.effect_id, attempt=1, verdict="PROVEN_NOT_APPLIED",
                evidence={"absence_verifier": "x"}, observed_at_ms=1_400,
            )

    def test_action_fence_dispatch_permit_epoch(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        status = self.store.action_fence_status()
        self.assertEqual(status["action_fence_epoch"], 0)
        self.assertFalse(status["fenced"])
        permit = self.store.acquire_dispatch_permit(
            effect_id=claim.effect_id, attempt=1,
            expected_fence_epoch=0, nonce_sha256="n" * 64,
            ticket_id="ticket-1", ticket_sha256="t" * 64, now_ms=1_100,
        )
        self.assertEqual(permit["fence_epoch"], 0)
        status = self.store.action_fence_status()
        self.assertEqual(status["inflight_count"], 1)
        # 全局 fence 递增后，旧 epoch 票据永不复活
        epoch = self.store.increment_action_fence(reason="P0 test", now_ms=1_200)
        self.assertEqual(epoch, 1)
        with self.assertRaises(StoreConflictError):
            self.store.acquire_dispatch_permit(
                effect_id=claim.effect_id, attempt=1,
                expected_fence_epoch=0, nonce_sha256="n" * 64, now_ms=1_300,
            )
        # inflight 未清零前是 fenced/draining，不宣称零流量
        status = self.store.action_fence_status()
        self.assertEqual(status["display"], "fenced/draining")
        self.assertFalse(status["zero_traffic_declared"])
        # receipt 落地后 inflight 归还
        self.store.complete_effect(make_result(claim.effect_id, observed_at_ms=1_400))
        self.store.release_dispatch_permit(effect_id=claim.effect_id, attempt=1, now_ms=1_500)
        status = self.store.action_fence_status()
        self.assertEqual(status["inflight_count"], 0)

    def test_dispatch_permit_requires_pre_start_claim(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
        with self.assertRaises(StoreConflictError):
            self.store.acquire_dispatch_permit(
                effect_id=claim.effect_id, attempt=1,
                expected_fence_epoch=0, nonce_sha256="n" * 64, now_ms=1_200,
            )

    def test_recover_started_attempts_marks_reconcile_required(self) -> None:
        claim = make_claim()
        self.store.claim_effect(claim)
        self.store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
        recovered = self.store.recover_started_attempts(now_ms=2_000)
        self.assertEqual(recovered, ({"effect_id": claim.effect_id, "attempt": 1},))
        attempt = self.store.get_effect_attempt(claim.effect_id, 1)
        self.assertEqual(attempt["state"], "RECONCILE_REQUIRED")
        verdicts = [
            f for f in self.store.list_effect_facts(claim.effect_id)
            if f["fact_kind"] == "RECONCILIATION"
        ]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "INCONCLUSIVE")


class EffectFactChainMigrationTests(unittest.TestCase):
    """存量 effect_ledger 行迁移为 attempt 1 + 合成事实锚点。"""

    def test_existing_rows_gain_attempts_and_anchor_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gateway.sqlite3"
            store = GatewayStateStore.open(path, now_ms=900)
            claim = make_claim()
            store.claim_effect(claim)
            store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
            store.complete_effect(make_result(claim.effect_id))
            store.close()
            store = GatewayStateStore.open(path, now_ms=1_000)
            attempts = store.list_effect_attempts(claim.effect_id)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["state"], "SUCCEEDED")
            kinds = [f["fact_kind"] for f in store.list_effect_facts(claim.effect_id)]
            self.assertEqual(kinds, ["CLAIM", "STARTED", "RECEIPT"])
            store.close()


if __name__ == "__main__":
    unittest.main()
