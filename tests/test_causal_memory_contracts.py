from __future__ import annotations

import unittest

from pydantic import ValidationError

from contracts import (
    CausalContextEdge,
    CausalContextPack,
    ContextTokenBudget,
    MemoryAssertionV3,
    MemoryRelationV3,
    PrivacyDeletionTombstone,
    retention_priority,
)
from tests.test_continuity_capsule import capsule


EVENT_ID = "lev_" + "1" * 64
MEMORY_ID = "mem_" + "2" * 64
HYPOTHESIS_ID = "chy_" + "3" * 64


def assertion(**overrides) -> MemoryAssertionV3:
    values = {
        "memory_id": MEMORY_ID,
        "life_id": "life_memory_contract",
        "revision": 1,
        "supersedes_assertion_sha256": None,
        "assertion_kind": "hard_constraint",
        "epistemic_status": "verified",
        "lifecycle_status": "active",
        "protected_payload_id": "protected_payload_memory_1",
        "protected_payload_sha256": "4" * 64,
        "deletion_tombstone_id": None,
        "privacy_scope": "privacy_owner_only",
        "retention_class": "LONG_TERM_MEMORY",
        "source_event_ids": (EVENT_ID,),
        "causal_hypothesis_ids": (),
        "causal_utility_milli": 900,
        "user_importance_milli": 800,
        "verification_strength_milli": 1000,
        "recurrence_count": 2,
        "future_dependency_milli": 700,
        "privacy_cost_milli": 300,
        "contradiction_penalty_milli": 0,
        "staleness_milli": 100,
        "valid_from_ms": 1_000,
        "expires_at_ms": None,
        "created_at_ms": 1_000,
        "assertion_sha256": "0" * 64,
    }
    values.update(overrides)
    return MemoryAssertionV3(**values)


def budget(current: int) -> ContextTokenBudget:
    usable = 120_000
    utilization = min(1000, (current * 1000) // usable)
    watermark = (
        "BELOW_75"
        if utilization < 750
        else "CANDIDATE_75"
        if utilization < 850
        else "MUST_PERSIST_85"
        if utilization < 920
        else "MUST_SWITCH_92"
    )
    return ContextTokenBudget(
        model_context_limit_tokens=160_000,
        product_limit_tokens=120_000,
        output_reserve_tokens=20_000,
        tool_schema_reserve_tokens=10_000,
        authority_reserve_tokens=5_000,
        protocol_reserve_tokens=5_000,
        usable_budget_tokens=usable,
        current_context_tokens=current,
        utilization_milli=utilization,
        watermark=watermark,
    )


class CausalMemoryContractTests(unittest.TestCase):
    def test_memory_revision_is_tamper_evident_and_retention_math_is_exact(self) -> None:
        value = assertion().with_computed_assertion_sha256()
        self.assertTrue(value.has_valid_assertion_sha256())
        self.assertEqual(retention_priority(value), 3_200)
        self.assertFalse(
            value.model_copy(update={"user_importance_milli": 1}).has_valid_assertion_sha256()
        )
        with self.assertRaises(ValidationError):
            assertion(user_importance_milli=0.5)

    def test_deleted_memory_has_no_payload_and_keeps_only_a_tombstone(self) -> None:
        tombstone = PrivacyDeletionTombstone(
            tombstone_id="ptm_" + "5" * 64,
            life_id="life_memory_contract",
            target_kind="memory",
            target_ref_hash="6" * 64,
            privacy_scope="privacy_owner_only",
            destroyed_payload_ids=("protected_payload_memory_1",),
            removed_index_entry_count=3,
            affected_capsule_ids=(),
            created_at_ms=2_000,
            deletion_proof_sha256="0" * 64,
        ).with_computed_deletion_proof_sha256()
        deleted = assertion(
            revision=2,
            supersedes_assertion_sha256="7" * 64,
            lifecycle_status="deleted",
            protected_payload_id=None,
            protected_payload_sha256=None,
            deletion_tombstone_id=tombstone.tombstone_id,
        ).with_computed_assertion_sha256()
        self.assertTrue(tombstone.has_valid_deletion_proof_sha256())
        self.assertTrue(deleted.has_valid_assertion_sha256())
        with self.assertRaises(ValidationError):
            assertion(lifecycle_status="deleted")

    def test_ordinary_memory_relations_cannot_claim_causality(self) -> None:
        relation = MemoryRelationV3(
            relation_id="mrl_" + "8" * 64,
            life_id="life_memory_contract",
            source_memory_id=MEMORY_ID,
            relation_kind="supports",
            original_relation_label=None,
            target_ref="memory_target",
            evidence_class="user_asserted",
            supporting_event_ids=(EVENT_ID,),
            created_at_ms=2_000,
            relation_sha256="0" * 64,
        ).with_computed_relation_sha256()
        self.assertTrue(relation.has_valid_relation_sha256())
        with self.assertRaises(ValidationError):
            MemoryRelationV3.model_validate(
                {**relation.model_dump(mode="python"), "relation_kind": "causes"},
                strict=True,
            )

    def test_context_edge_preserves_hypothesis_status_and_evidence_ceiling(self) -> None:
        candidate = CausalContextEdge(
            hypothesis_id=HYPOTHESIS_ID,
            revision=1,
            cause_ref="memory_cause",
            effect_ref="memory_effect",
            relation="correlated_with",
            causal_basis="correlation",
            status="candidate",
            confidence_milli=600,
            supporting_event_ids=(EVENT_ID,),
            counterevidence_event_ids=(),
        )
        self.assertEqual(candidate.status, "candidate")
        with self.assertRaises(ValidationError):
            candidate.model_copy(
                update={"relation": "causes", "causal_basis": "correlation"}
            ).__class__.model_validate(
                candidate.model_copy(
                    update={"relation": "causes", "causal_basis": "correlation"}
                ).model_dump(mode="python"),
                strict=True,
            )

    def test_token_budget_has_exact_75_85_92_watermarks(self) -> None:
        self.assertEqual(budget(89_999).watermark, "BELOW_75")
        self.assertEqual(budget(90_000).watermark, "CANDIDATE_75")
        self.assertEqual(budget(102_000).watermark, "MUST_PERSIST_85")
        self.assertEqual(budget(110_400).watermark, "MUST_SWITCH_92")
        with self.assertRaises(ValidationError):
            budget(90_000).model_copy(update={"watermark": "BELOW_75"}).__class__.model_validate(
                budget(90_000).model_copy(update={"watermark": "BELOW_75"}).model_dump(mode="python"),
                strict=True,
            )

    def test_context_pack_is_continuity_bound_and_contains_no_raw_tool_process(self) -> None:
        continuity = capsule().with_computed_capsule_sha256()
        pack = CausalContextPack(
            pack_id="ccp_" + "9" * 64,
            life_id=continuity.life_id,
            continuity=continuity,
            seed_refs=(),
            items=(),
            edges=(),
            token_budget=budget(500_000),
            selected_token_count=10,
            omitted_item_count=100_000,
            visible_raw_tool_process_count=0,
            integrity_status="VERIFIED",
            model_input_switched=False,
            created_at_ms=3_000,
            pack_sha256="0" * 64,
        ).with_computed_pack_sha256()
        self.assertTrue(pack.has_valid_pack_sha256())
        self.assertEqual(pack.token_budget.watermark, "MUST_SWITCH_92")
        self.assertEqual(pack.visible_raw_tool_process_count, 0)
        self.assertFalse(pack.model_input_switched)
        with self.assertRaises(ValidationError):
            CausalContextPack.model_validate(
                {**pack.model_dump(mode="python"), "life_id": "other_life"},
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
