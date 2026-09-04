"""D-14 澄清不是确认（最小集）回归测试。

验证目标：
- NEEDS_CLARIFICATION 保留为独立于确认的 outcome；澄清发生在 effect 前，
  只写未决问题（clarification_questions），不产生任何 effect/fact；
- 同 generation 已有 effect head 时登记澄清问题 fail-closed；
- 用户答复创建 generation+1（旧 generation fence 被 SUPERSEDED），
  同 generation 其余 OPEN 问题一并 SUPERSEDED；
- 澄清答复本身不是副作用凭证：答复 + 翻代全程 effect 台账保持为空。
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from contracts import canonical_sha256, derive_effect_identity, derive_run_identity
from runtime_security import EphemeralTestProtector
from total_gateway.effects import EffectClaim
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.store import (
    GatewayStateStore,
    StoreConflictError,
    StoreNotFoundError,
)

ROOT = Path(__file__).resolve().parents[1]


class ClarificationD14Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = Path(self._tmp.name).resolve()
        self.now_ms = int(time.time() * 1000)
        self.store = GatewayStateStore.open(
            self.state_root / "gateway-state" / "gateway.sqlite3", now_ms=self.now_ms
        )
        self.addCleanup(self.store.close)
        self.objects = ContentAddressedObjectStore.open(
            self.state_root / "gateway-objects",
            now_ms=self.now_ms,
        )
        self.addCleanup(self.objects.close)
        config = SimpleNamespace(
            release_manifest_path=None,
            release_source_root=ROOT,
            environment="development",
            state_root=self.state_root,
            workspace_root=self.state_root,
            backend_internal_token="b" * 48,
            life_internal_token="l" * 48,
            communication_api_token="c" * 48,
            runtime_key_protector=EphemeralTestProtector(),
        )
        self.worker = GatewayOrchestrationWorker.from_runtime_config(
            config=config,
            activator=SimpleNamespace(),
            store=self.store,
            objects=self.objects,
            facts=SimpleNamespace(),
            gateway_epoch=11,
            gateway_instance_id="gateway-clarification-test",
            now_ms=self.now_ms,
        )
        self.addCleanup(self.worker.close)
        self.request_id = "req_" + canonical_sha256({"case": "clarification"})
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.view, _ = self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=1,
            gateway_epoch=11,
            lease_id="lease-clarification-1",
            owner_instance_id="gateway-clarification-test",
            issued_at_ms=self.now_ms,
            lease_duration_ms=60_000,
        )

    def _effect_count(self) -> int:
        with self.store._lock:  # 测试断言直读：澄清全程不得产生任何 effect head
            row = self.store._connection.execute(
                "SELECT COUNT(*) AS n FROM effect_ledger WHERE request_id = ?",
                (self.request_id,),
            ).fetchone()
            return int(row["n"])

    def _fact_count(self) -> int:
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT COUNT(*) AS n FROM effect_facts"
            ).fetchone()
            return int(row["n"])

    def test_pause_records_unresolved_question_before_any_effect(self) -> None:
        outcome = self.worker.pause_for_clarification(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            question="要发到哪个频道？",
            now_ms=self.now_ms + 1,
        )
        self.assertEqual(outcome["outcome"], "NEEDS_CLARIFICATION")
        self.assertIs(outcome["side_effect_credential"], False)
        questions = self.store.list_clarification_questions(self.request_id, state="OPEN")
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"], "要发到哪个频道？")
        self.assertEqual(questions[0]["generation"], 1)
        # 幂等：同内容重复登记返回同一问题，不产生重复行
        again = self.worker.pause_for_clarification(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            question="要发到哪个频道？",
            now_ms=self.now_ms + 2,
        )
        self.assertEqual(again["question_id"], outcome["question_id"])
        self.assertEqual(len(self.store.list_clarification_questions(self.request_id)), 1)
        # 澄清本身不产生任何 effect / fact
        self.assertEqual(self._effect_count(), 0)
        self.assertEqual(self._fact_count(), 0)

    def test_pause_after_effect_is_fail_closed(self) -> None:
        identity = derive_effect_identity(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=1,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=canonical_sha256({"intent": "already-running"}),
        )
        self.store.claim_effect(
            EffectClaim(
                effect_id=identity.effect_id,
                request_id=self.request_id,
                run_id=self.run_id,
                run_sequence=1,
                generation=1,
                effect_kind="execution",
                ordinal=0,
                intent_sha256=canonical_sha256({"intent": "already-running"}),
                owner_component_id="tiangong-backend",
                claimed_at_ms=self.now_ms + 1,
                claim_sha256="0" * 64,
            ).with_computed_sha256()
        )
        with self.assertRaises(StoreConflictError):
            self.worker.pause_for_clarification(
                request_id=self.request_id,
                run_id=self.run_id,
                generation=1,
                question="迟到的澄清",
                now_ms=self.now_ms + 2,
            )

    def test_answer_advances_generation_and_fences_old(self) -> None:
        first = self.worker.pause_for_clarification(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            question="要发到哪个频道？",
            now_ms=self.now_ms + 1,
        )
        second = self.worker.pause_for_clarification(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            question="附件要不要一起发？",
            now_ms=self.now_ms + 2,
        )
        resumed = self.worker.resume_from_clarification(
            question_id=first["question_id"],
            lease_id="lease-clarification-2",
            answered_at_ms=self.now_ms + 3,
        )
        self.assertEqual(resumed["outcome"], "CLARIFICATION_ANSWERED")
        self.assertIs(resumed["side_effect_credential"], False)
        self.assertEqual(resumed["previous_generation"], 1)
        self.assertEqual(resumed["generation"], 2)
        self.assertEqual(resumed["run_id"], self.run_id)
        # 旧 fence 被 SUPERSEDED，新 generation ACTIVE
        current = self.store.get_generation(self.request_id)
        self.assertEqual(current.generation, 2)
        self.assertEqual(current.status, "ACTIVE")
        superseded = {
            q["question_id"]: q["state"]
            for q in self.store.list_clarification_questions(self.request_id)
        }
        self.assertEqual(superseded[first["question_id"]], "ANSWERED")
        self.assertEqual(superseded[second["question_id"]], "SUPERSEDED")
        # 答复幂等：重复答复同一问题不产生新状态
        answered_again = self.store.answer_clarification_question(
            question_id=first["question_id"], answered_at_ms=self.now_ms + 4
        )
        self.assertEqual(answered_again["state"], "ANSWERED")
        # 答复 + 翻代全程不写任何 effect / fact —— 答复不是副作用凭证
        self.assertEqual(self._effect_count(), 0)
        self.assertEqual(self._fact_count(), 0)

    def test_answer_unknown_question_is_fail_closed(self) -> None:
        with self.assertRaises(StoreNotFoundError):
            self.store.answer_clarification_question(
                question_id="clq_" + "0" * 64, answered_at_ms=self.now_ms + 1
            )

    def test_resume_requires_current_generation(self) -> None:
        first = self.worker.pause_for_clarification(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            question="要发到哪个频道？",
            now_ms=self.now_ms + 1,
        )
        self.worker.resume_from_clarification(
            question_id=first["question_id"],
            lease_id="lease-clarification-2",
            answered_at_ms=self.now_ms + 2,
        )
        # 已翻代后对旧 generation 的问题再答复：answer 幂等返回 ANSWERED，
        # 但 resume 不得二次翻代
        from total_gateway.orchestration import OrchestrationError

        with self.assertRaises(OrchestrationError):
            self.worker.resume_from_clarification(
                question_id=first["question_id"],
                lease_id="lease-clarification-3",
                answered_at_ms=self.now_ms + 3,
            )


if __name__ == "__main__":
    unittest.main()
