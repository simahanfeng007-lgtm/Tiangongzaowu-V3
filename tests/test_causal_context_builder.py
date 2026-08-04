from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import CausalHypothesis, MemoryAssertionV3
from life_service.context import (
    CausalContextBuilder,
    ContextBuildError,
    build_token_budget,
    conservative_token_count,
)
from life_service.store import LifeShadowStore
from tests.test_continuity_capsule import capsule


EVENT_ID = "lev_" + "1" * 64


def memory_id(marker: str) -> str:
    return "mem_" + marker * 64


def assertion(
    protected_id: str,
    protected_sha256: str,
    *,
    marker: str,
    assertion_kind: str,
    retention_class: str,
    importance: int,
) -> MemoryAssertionV3:
    return MemoryAssertionV3(
        memory_id=memory_id(marker),
        life_id="life_contract_test",
        revision=1,
        supersedes_assertion_sha256=None,
        assertion_kind=assertion_kind,
        epistemic_status="verified",
        lifecycle_status="active",
        protected_payload_id=protected_id,
        protected_payload_sha256=protected_sha256,
        deletion_tombstone_id=None,
        privacy_scope="private",
        retention_class=retention_class,
        source_event_ids=(EVENT_ID,),
        causal_hypothesis_ids=(),
        causal_utility_milli=importance,
        user_importance_milli=importance,
        verification_strength_milli=1000,
        recurrence_count=1,
        future_dependency_milli=importance,
        privacy_cost_milli=0,
        contradiction_penalty_milli=0,
        staleness_milli=0,
        valid_from_ms=1_000,
        expires_at_ms=None,
        created_at_ms=1_000,
        assertion_sha256="0" * 64,
    ).with_computed_assertion_sha256()


class CausalContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "context.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.continuity = capsule(
            user_goal="完成跨越百万 token 的工程任务。",
            hard_constraints=("不得丢失用户硬约束。", "不得把假设写成事实。"),
            active_plan=("已完成：建立审计链", "未完成：继续实现压缩切换"),
            latest_safe_step="因果记忆已经持久化。",
            next_step="继续实现上下文切换并对抗验证。",
            created_at_ms=2_000,
        ).with_computed_capsule_sha256()
        self.store.put_context_capsule(self.continuity)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def add_memory(
        self,
        text: str,
        *,
        marker: str,
        assertion_kind: str = "observation",
        retention_class: str = "ACTIVE_WORKING",
        importance: int = 500,
    ) -> MemoryAssertionV3:
        protected = self.store.put_protected_payload(
            text.encode("utf-8"),
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_000,
        )
        value = assertion(
            protected.payload_id,
            protected.ciphertext_sha256,
            marker=marker,
            assertion_kind=assertion_kind,
            retention_class=retention_class,
            importance=importance,
        )
        self.store.put_memory_assertion(value, search_terms=(text[:8],))
        return value

    def test_exact_watermarks_are_shared_by_100k_500k_and_1m_chains(self) -> None:
        self.assertEqual(build_token_budget(
            model_context_limit_tokens=160_000,
            current_context_tokens=89_999,
        ).watermark, "BELOW_75")
        self.assertEqual(build_token_budget(
            model_context_limit_tokens=160_000,
            current_context_tokens=90_000,
        ).watermark, "CANDIDATE_75")
        self.assertEqual(build_token_budget(
            model_context_limit_tokens=160_000,
            current_context_tokens=102_000,
        ).watermark, "MUST_PERSIST_85")
        self.assertEqual(build_token_budget(
            model_context_limit_tokens=160_000,
            current_context_tokens=110_400,
        ).watermark, "MUST_SWITCH_92")
        for chain_tokens in (100_000, 500_000, 1_000_000):
            pack = CausalContextBuilder(self.store).build(
                self.continuity,
                current_context_tokens=chain_tokens,
                created_at_ms=3_000 + chain_tokens,
            )
            self.assertEqual(pack.continuity.hard_constraints, self.continuity.hard_constraints)
            self.assertEqual(pack.continuity.next_step, self.continuity.next_step)
            self.assertLessEqual(pack.selected_token_count, pack.token_budget.usable_budget_tokens)
            self.assertEqual(pack.visible_raw_tool_process_count, 0)
            self.assertFalse(pack.model_input_switched)

    def test_bounded_graph_keeps_required_memory_and_candidate_epistemic_state(self) -> None:
        constraint = self.add_memory(
            "所有压缩结果必须保留未完成工作与硬约束。",
            marker="2",
            assertion_kind="hard_constraint",
            retention_class="LONG_TERM_MEMORY",
            importance=1000,
        )
        observation = self.add_memory(
            "一次观察显示较早压缩可能与遗漏有关，但尚未证明因果。",
            marker="3",
            importance=600,
        )
        hypothesis = CausalHypothesis(
            hypothesis_id="chy_" + "4" * 64,
            life_id="life_contract_test",
            cause_ref=observation.memory_id,
            effect_ref=constraint.memory_id,
            relation="correlated_with",
            causal_basis="correlation",
            mechanism_summary="",
            confidence_milli=600,
            evidence_class="model_inference",
            supporting_event_ids=(),
            counterevidence_event_ids=(),
            alternative_hypothesis_ids=(),
            confounder_refs=("unknown_context_pressure",),
            intervention_status="none",
            valid_from_ms=2_000,
            valid_until_ms=None,
            supersedes_id=None,
            status="candidate",
            revision=1,
            hypothesis_sha256="0" * 64,
        ).with_computed_hypothesis_sha256()
        self.store.put_causal_hypothesis(hypothesis)
        pack = CausalContextBuilder(self.store, max_graph_hops=1).build(
            self.continuity,
            current_context_tokens=500_000,
            created_at_ms=4_000,
            seed_refs=(observation.memory_id,),
        )
        self.assertIn(constraint.memory_id, {item.item_ref for item in pack.items})
        self.assertEqual(len(pack.edges), 1)
        self.assertEqual(pack.edges[0].relation, "correlated_with")
        self.assertEqual(pack.edges[0].status, "candidate")
        self.assertNotEqual(pack.edges[0].relation, "causes")

    def test_corrupt_candidate_never_replaces_previous_verified_projection(self) -> None:
        builder = CausalContextBuilder(self.store)
        previous = builder.build(
            self.continuity,
            current_context_tokens=100_000,
            created_at_ms=3_000,
        )
        previous_result = builder.persist_verified(
            previous,
            privacy_scope="private",
            previous_verified_pack=None,
        )
        self.assertEqual(previous_result.active_pack, previous)
        candidate = builder.build(
            self.continuity,
            current_context_tokens=1_000_000,
            created_at_ms=4_000,
        )
        corrupt = candidate.model_copy(update={"pack_sha256": "f" * 64})
        rejected = builder.persist_verified(
            corrupt,
            privacy_scope="private",
            previous_verified_pack=previous,
        )
        self.assertFalse(rejected.persisted)
        self.assertFalse(rejected.replaced_previous)
        self.assertEqual(rejected.active_pack, previous)
        self.assertEqual(rejected.reason_code, "context.integrity_rejected")

    def test_tokenizer_is_pluggable_and_invalid_or_oversized_hard_state_fails(self) -> None:
        calls: list[str] = []

        def exact_counter(value: str) -> int:
            calls.append(value)
            return max(1, len(value))

        pack = CausalContextBuilder(self.store, token_counter=exact_counter).build(
            self.continuity,
            current_context_tokens=110_400,
            created_at_ms=3_000,
        )
        self.assertTrue(calls)
        self.assertGreater(pack.selected_token_count, 0)
        self.assertGreaterEqual(
            conservative_token_count("中文"),
            exact_counter("中文"),
        )
        with self.assertRaisesRegex(ContextBuildError, "entire model window"):
            CausalContextBuilder(self.store).build(
                self.continuity,
                current_context_tokens=10,
                created_at_ms=3_000,
                model_context_limit_tokens=40_000,
            )


if __name__ == "__main__":
    unittest.main()
