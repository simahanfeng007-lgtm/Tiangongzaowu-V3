"""D-06 统一 admission：omni 子 effect 进台账 + nonce 落库 + 幂等命中。

验证目标：
- issue() 成功后子 effect 在 effect 台账完成 claim/complete（head SUCCEEDED、
  attempt 终态、CLAIM→STARTED→RECEIPT 事实链、fence epoch 锚定）；
- 子票 nonce 与 grant nonce 都落入 security_nonce_ledger（持久重放拦截，
  不再是 60s 内存缓存）；
- effect_id 对 (parent_ticket, call_id, action/target/args) 确定：
  60s 内存缓存过期、甚至 authority 换新实例（模拟网关重启）后，同 call_id
  幂等命中并逐字节重放首个响应，绝不产生新 effect_id / 新 nonce；
- admission CAS 与 action_fence_epoch 同行：fence 推进后陈旧 admission
  fail-closed；
- 子 effect 终态落地，不挂 STARTED（不污染 unreconciled 计数）。
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from contracts import canonical_sha256, derive_run_identity
from runtime_security import EphemeralTestProtector
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.store import (
    GatewayStateStore,
    StoreCasConflict,
    StoreConflictError,
)
from tests.test_execution_contracts import execution_ticket

ROOT = Path(__file__).resolve().parents[1]


class OmniSubEffectAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_root = Path(self._tmp.name).resolve()
        (self.state_root / "sample.txt").write_text("trusted", encoding="utf-8")
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
        self._protector = EphemeralTestProtector()
        self.worker = self._new_worker()
        self.addCleanup(lambda: self.worker.close())
        self.authority = self.worker.omni_grant_authority
        self.outer = self._register_outer("main", generation=3)

    def _new_worker(self) -> GatewayOrchestrationWorker:
        config = SimpleNamespace(
            release_manifest_path=None,
            release_source_root=ROOT,
            environment="development",
            state_root=self.state_root,
            workspace_root=self.state_root,
            backend_internal_token="b" * 48,
            life_internal_token="l" * 48,
            communication_api_token="c" * 48,
            runtime_key_protector=self._protector,
        )
        return GatewayOrchestrationWorker.from_runtime_config(
            config=config,
            activator=SimpleNamespace(),
            store=self.store,
            objects=self.objects,
            facts=SimpleNamespace(),
            gateway_epoch=71,
            gateway_instance_id="gateway-omni-admission",
            now_ms=self.now_ms,
        )

    def _register_outer(self, suffix: str, *, generation: int = 1):
        request_id = "req_" + canonical_sha256({"suffix": suffix})
        outer = execution_ticket(
            ticket_id=f"ticket_admission_{suffix}",
            nonce=f"nonce_admission_{suffix}",
            issued_at_ms=self.now_ms,
            not_before_ms=self.now_ms,
            expires_at_ms=self.now_ms + 60_000,
            gateway_epoch=71,
            request_id=request_id,
            run_id=derive_run_identity(request_id, 1).run_id,
            generation=generation,
            principal_scope_hash=canonical_sha256({"principal": suffix}),
            workspace_id=self.authority.workspace_id,
            max_runtime_ms=3_600_000,
            max_tool_calls=10_000,
        )
        self.authority.register(
            outer,
            life_id=f"life_{suffix}",
            life_evidence_ref="lev_" + canonical_sha256({"life": suffix}),
            session_id=f"session_{suffix}",
            registered_at_ms=self.now_ms,
            # 注册窗口放宽到 1h：缓存过期（61s）幂等用例必须越过 60s 内存缓存
            authority_expires_at_ms=self.now_ms + 3_600_000,
        )
        return outer

    def _payload(self, call_seed: str, *, args=None, outer=None):
        outer = outer or self.outer
        return {
            "ticket_id": outer.payload.ticket_id,
            "call_id": "toolcall_" + canonical_sha256({"call": call_seed}),
            "request_id": outer.payload.request_id,
            "run_id": outer.payload.run_id,
            "generation": outer.payload.generation,
            "principal_scope_hash": outer.payload.principal_scope_hash,
            "action": "system.health",
            "target": "",
            "args": {} if args is None else args,
            "workspace": str(self.state_root),
        }

    def _effect_id_of(self, response: dict) -> str:
        return response["grant"]["payload"]["effect_id"]

    # 1) 台账：claim/complete + 事实链 + fence 锚定
    def test_issue_records_sub_effect_in_ledger(self) -> None:
        issued = self.authority.issue(self._payload("ledger"), now_ms=self.now_ms + 1)
        effect_id = self._effect_id_of(issued)
        head = self.store.get_effect(effect_id)
        self.assertIsNotNone(head)
        self.assertEqual(head.state, "SUCCEEDED")
        self.assertEqual(head.claim.effect_id, effect_id)
        self.assertEqual(head.claim.run_sequence, 1)
        self.assertEqual(head.claim.claim_revision, 1)
        self.assertEqual(head.claim.lease_epoch, 71)
        self.assertIsNotNone(head.result)
        self.assertEqual(head.result.status, "SUCCEEDED")
        # head 投影与子票 effect_id 一致（双源同源）
        self.assertEqual(effect_id, head.claim.effect_id)
        attempts = self.store.list_effect_attempts(effect_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["state"], "SUCCEEDED")
        self.assertEqual(attempts[0]["terminal_kind"], "SUCCEEDED")
        self.assertIsNotNone(attempts[0]["ticket_id"])
        self.assertIsNotNone(attempts[0]["ticket_sha256"])
        self.assertIsNotNone(attempts[0]["grant_sha256"])
        self.assertIsNotNone(attempts[0]["nonce_sha256"])
        facts = self.store.list_effect_facts(effect_id)
        self.assertEqual([fact["fact_kind"] for fact in facts], ["CLAIM", "STARTED", "RECEIPT"])
        # 链式 prev 哈希 + fence epoch 锚定
        self.assertEqual(facts[0]["prev_fact_sha256"], "0" * 64)
        self.assertEqual(facts[1]["prev_fact_sha256"], facts[0]["payload_sha256"])
        self.assertEqual(facts[2]["prev_fact_sha256"], facts[1]["payload_sha256"])
        import json

        claim_payload = json.loads(facts[0]["payload_json"])
        self.assertEqual(
            claim_payload["action_fence_epoch"],
            self.store.action_fence_status()["action_fence_epoch"],
        )
        # RECEIPT 记录首个响应（幂等重放来源）
        receipt_payload = json.loads(facts[2]["payload_json"])
        self.assertEqual(
            receipt_payload["omni_admission_response"]["grant"], issued["grant"]
        )
        # 子票绑定 claim（claim-before-ticket）
        self.assertEqual(
            issued["grant"]["payload"]["ticket_id"], attempts[0]["ticket_id"]
        )

    # 2) nonce 落 security_nonce_ledger（持久，重启后仍拦截）
    def test_grant_and_ticket_nonces_are_persisted(self) -> None:
        issued = self.authority.issue(self._payload("nonce"), now_ms=self.now_ms + 1)
        grant_nonce = issued["grant"]["payload"]["nonce"]
        grant_payload_sha256 = canonical_sha256(issued["grant"]["payload"])
        effect_id = self._effect_id_of(issued)
        attempt = self.store.list_effect_attempts(effect_id)[0]
        self.assertIsNotNone(attempt["nonce_sha256"])
        self.assertTrue(issued["runtime"]["execution_ticket_id"])
        # 同参数再消费 → consumed_by_this_call=False 证明持久命中；
        replay = self.store.consume_security_nonce(
            issuer="tiangong-total-gateway",
            audience="tiangong-backend",
            purpose="omni_capability_grant",
            nonce=grant_nonce,
            payload_sha256=grant_payload_sha256,
            gateway_epoch=71,
            consumer_instance_id="tiangong-total-gateway:omni-grant-authority",
            consumed_at_ms=self.now_ms + 1,
            expires_at_ms=issued["grant"]["payload"]["expires_at_ms"],
        )
        self.assertFalse(replay.consumed_by_this_call)
        # 不同 claims 再消费 → 冲突证明重放拦截。
        with self.assertRaises(StoreConflictError):
            self.store.consume_security_nonce(
                issuer="tiangong-total-gateway",
                audience="tiangong-backend",
                purpose="omni_capability_grant",
                nonce=grant_nonce,
                payload_sha256=canonical_sha256({"probe": "different-claims"}),
                gateway_epoch=71,
                consumer_instance_id="probe",
                consumed_at_ms=self.now_ms + 3,
                expires_at_ms=self.now_ms + 60_000,
            )

    # 3) 缓存过期不再产生新 effect_id（D-06 核心）
    def test_cache_expiry_still_hits_same_effect_id(self) -> None:
        payload = self._payload("expiry")
        first = self.authority.issue(payload, now_ms=self.now_ms + 1)
        # 61s 后内存缓存必然过期
        second = self.authority.issue(payload, now_ms=self.now_ms + 61_001)
        self.assertEqual(first, second)
        self.assertEqual(self._effect_id_of(first), self._effect_id_of(second))
        # 台账仍只有一个 effect、一条 RECEIPT
        effects = self.store.list_effects_for_request(
            self.outer.payload.request_id,
            run_id=self.outer.payload.run_id,
            generation=self.outer.payload.generation,
        )
        self.assertEqual(len(effects), 1)

    # 4) authority 换新实例（模拟网关重启）仍幂等命中
    def test_authority_restart_replays_recorded_response(self) -> None:
        payload = self._payload("restart")
        first = self.authority.issue(payload, now_ms=self.now_ms + 1)
        self.worker.close()
        self.worker = self._new_worker()
        self.authority = self.worker.omni_grant_authority
        # 新实例内存缓存为空；重新注册同一 outer
        self.authority.register(
            self.outer,
            life_id="life_main",
            life_evidence_ref="lev_" + canonical_sha256({"life": "main"}),
            session_id="session_main",
            registered_at_ms=self.now_ms,
            authority_expires_at_ms=self.now_ms + 3_600_000,
        )
        second = self.authority.issue(payload, now_ms=self.now_ms + 2)
        self.assertEqual(first, second)
        self.assertEqual(self._effect_id_of(first), self._effect_id_of(second))

    # 5) admission CAS：fence 推进后陈旧 admission fail-closed
    def test_stale_fence_epoch_admission_is_rejected(self) -> None:
        claim = None
        result = None
        from total_gateway.effects import EffectClaim, EffectResult
        from contracts import derive_effect_identity

        identity = derive_effect_identity(
            request_id=self.outer.payload.request_id,
            run_id=self.outer.payload.run_id,
            run_sequence=1,
            generation=3,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=canonical_sha256({"probe": "fence"}),
        )
        claim = EffectClaim(
            effect_id=identity.effect_id,
            request_id=self.outer.payload.request_id,
            run_id=self.outer.payload.run_id,
            run_sequence=1,
            generation=3,
            effect_kind="execution",
            ordinal=1,
            intent_sha256=canonical_sha256({"probe": "fence"}),
            owner_component_id="tiangong-backend",
            claimed_at_ms=self.now_ms,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        result = EffectResult(
            result_id="effect_result_probe",
            effect_id=identity.effect_id,
            status="SUCCEEDED",
            fact_id="fact_probe",
            evidence_sha256=canonical_sha256({"evidence": "probe"}),
            observed_at_ms=self.now_ms,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        stale_epoch = self.store.action_fence_status()["action_fence_epoch"]
        self.store.increment_action_fence(reason="operator-stop", now_ms=self.now_ms + 1)
        with self.assertRaises(StoreCasConflict):
            self.store.admit_sub_effect(
                claim=claim,
                result=result,
                started_at_ms=self.now_ms,
                expected_fence_epoch=stale_epoch,
            )
        # fence 之后的新 admission 锚定新 epoch，仍可落地（fence 杀在途，不杀未来）
        record, created, _ = self.store.admit_sub_effect(
            claim=claim,
            result=result,
            started_at_ms=self.now_ms,
            expected_fence_epoch=stale_epoch + 1,
        )
        self.assertTrue(created)
        self.assertEqual(record.state, "SUCCEEDED")

    # 6) 子 effect 不悬挂：不污染未对账计数
    def test_admission_does_not_leave_unreconciled_attempts(self) -> None:
        before = self.store.count_unreconciled_attempts()
        self.authority.issue(self._payload("terminal"), now_ms=self.now_ms + 1)
        self.assertEqual(self.store.count_unreconciled_attempts(), before)


if __name__ == "__main__":
    unittest.main()
