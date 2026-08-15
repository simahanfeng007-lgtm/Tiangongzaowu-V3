"""P18-M3.8-M3.12 governance regressions."""
from __future__ import annotations

import unittest

from v3.runtime_adaptive_governance import (
    CheckpointVersionVector,
    FactFreshness,
    InstructionSourcePriority,
    LearningPromotionEvidence,
    SemanticDriftSignals,
    TOOL_RESULT_DATA,
    UNTRUSTED_DATA,
    evaluate_checkpoint_version_compatibility,
    evaluate_fact_freshness,
    evaluate_learning_promotion,
    evaluate_semantic_drift,
    version_vector_from_mapping,
)


class P18M3AdaptiveGovernanceTests(unittest.TestCase):
    def test_stale_fact_requires_revalidation_before_dependency_reuse(self) -> None:
        decision = evaluate_fact_freshness(
            FactFreshness(
                observed_at_ms=1000,
                valid_until_ms=2000,
                revalidation_policy="ttl",
                source_version="rev-a",
            ),
            now_ms=2001,
            current_source_version="rev-a",
        )
        self.assertFalse(decision.reusable)
        self.assertTrue(decision.requires_revalidation)
        self.assertIn("validity_window_expired", decision.reasons)

    def test_volatile_fact_revalidates_even_inside_ttl(self) -> None:
        decision = evaluate_fact_freshness(
            FactFreshness(
                observed_at_ms=1000,
                valid_until_ms=9000,
                revalidation_policy="on_reuse",
                source_version="git-a",
                volatile=True,
            ),
            now_ms=1500,
            current_source_version="git-a",
        )
        self.assertTrue(decision.requires_revalidation)
        self.assertIn("volatile_dependency_revalidation", decision.reasons)

    def test_source_version_change_blocks_stale_reuse(self) -> None:
        decision = evaluate_fact_freshness(
            FactFreshness(
                observed_at_ms=1000,
                valid_until_ms=9000,
                revalidation_policy="source_version",
                source_version="head-a",
            ),
            now_ms=1500,
            current_source_version="head-b",
        )
        self.assertFalse(decision.reusable)
        self.assertIn("source_version_changed", decision.reasons)

    def test_tool_result_is_lower_priority_untrusted_data(self) -> None:
        self.assertEqual(UNTRUSTED_DATA, "UNTRUSTED_DATA")
        self.assertEqual(TOOL_RESULT_DATA, "TOOL_RESULT_DATA")
        self.assertLess(
            InstructionSourcePriority.TOOL_RESULT_DATA,
            InstructionSourcePriority.VERIFIED_USER_INSTRUCTION,
        )
        self.assertLess(
            InstructionSourcePriority.VERIFIED_USER_INSTRUCTION,
            InstructionSourcePriority.TASK_CONTRACT,
        )
        self.assertLess(
            InstructionSourcePriority.TASK_CONTRACT,
            InstructionSourcePriority.SYSTEM_AUTHORITY,
        )

    def test_unverified_one_shot_fact_cannot_promote_learning(self) -> None:
        decision = evaluate_learning_promotion(
            LearningPromotionEvidence(
                fact_status="UNVERIFIED",
                verified=False,
                evidence_count=1,
                source_count=1,
                memory_promotion_eligible=True,
            )
        )
        self.assertFalse(decision.allowed)
        self.assertIn("fact_not_verified", decision.reasons)
        self.assertIn("insufficient_repeated_evidence", decision.reasons)

    def test_revoked_fact_revokes_pending_learning_candidate(self) -> None:
        decision = evaluate_learning_promotion(
            LearningPromotionEvidence(
                fact_status="REVOKED",
                verified=True,
                evidence_count=5,
                source_count=3,
                memory_promotion_eligible=True,
                revoked=True,
            )
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.candidate_revoked)

    def test_verified_repeated_fact_can_promote_when_existing_memory_gate_allows(self) -> None:
        decision = evaluate_learning_promotion(
            LearningPromotionEvidence(
                fact_status="VERIFIED",
                verified=True,
                evidence_count=3,
                source_count=2,
                memory_promotion_eligible=True,
                requires_multi_source=True,
            )
        )
        self.assertTrue(decision.allowed)

    def test_explicit_user_memory_is_not_misclassified_as_unverified_world_fact(self) -> None:
        decision = evaluate_learning_promotion(
            LearningPromotionEvidence(explicit_user_memory=True)
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, ("explicit_user_memory_ssot",))

    def test_high_semantic_drift_forces_audit_rebuild_and_replan(self) -> None:
        decision = evaluate_semantic_drift(
            SemanticDriftSignals(
                root_goal_similarity=0.1,
                task_contract_match=False,
                active_obligation_consistency=0.1,
                authority_reference_match=False,
                frontier_contradiction=True,
                semantic_handoff_contradiction=True,
                repeated_strategy_collapse=1.0,
                unverified_claim_accumulation=1.0,
            )
        )
        self.assertTrue(decision.high_risk)
        self.assertTrue(decision.checkpoint_candidate)
        self.assertTrue(decision.reality_audit)
        self.assertTrue(decision.frontier_rebuild)
        self.assertTrue(decision.replan)
        self.assertFalse(decision.allow_horizon_growth)

    def test_low_semantic_drift_does_not_force_replan(self) -> None:
        decision = evaluate_semantic_drift(SemanticDriftSignals())
        self.assertFalse(decision.high_risk)
        self.assertFalse(decision.replan)
        self.assertTrue(decision.allow_horizon_growth)

    def test_exact_checkpoint_versions_resume(self) -> None:
        vector = CheckpointVersionVector(
            checkpoint_schema_version="cp-v1",
            runtime_version="rt-1",
            provider_profile_hash="provider-a",
            model_version="model-a",
            tool_registry_version="tools-a",
            skill_version="skills-a",
            task_contract_version="contract-a",
        )
        decision = evaluate_checkpoint_version_compatibility(vector, vector)
        self.assertTrue(decision.resume_allowed)
        self.assertFalse(decision.reconcile_required)

    def test_version_mismatch_never_silently_resumes(self) -> None:
        checkpoint = CheckpointVersionVector(
            checkpoint_schema_version="cp-v1",
            runtime_version="rt-1",
            provider_profile_hash="provider-a",
            model_version="model-a",
            tool_registry_version="tools-a",
            skill_version="skills-a",
            task_contract_version="contract-a",
        )
        current = CheckpointVersionVector(
            checkpoint_schema_version="cp-v1",
            runtime_version="rt-2",
            provider_profile_hash="provider-a",
            model_version="model-a",
            tool_registry_version="tools-a",
            skill_version="skills-a",
            task_contract_version="contract-a",
        )
        decision = evaluate_checkpoint_version_compatibility(checkpoint, current)
        self.assertFalse(decision.resume_allowed)
        self.assertTrue(decision.reconcile_required)
        self.assertIn("runtime_version", decision.mismatches)

    def test_explicit_compatible_drift_requires_revalidation_before_resume(self) -> None:
        checkpoint = CheckpointVersionVector(runtime_version="rt-1")
        current = CheckpointVersionVector(runtime_version="rt-2")
        pending = evaluate_checkpoint_version_compatibility(
            checkpoint,
            current,
            compatible_mismatches={"runtime_version"},
        )
        self.assertFalse(pending.resume_allowed)
        self.assertTrue(pending.revalidation_required)
        ready = evaluate_checkpoint_version_compatibility(
            checkpoint,
            current,
            compatible_mismatches={"runtime_version"},
            revalidated=True,
        )
        self.assertTrue(ready.resume_allowed)

    def test_migratable_schema_requires_migration_and_revalidation(self) -> None:
        checkpoint = CheckpointVersionVector(checkpoint_schema_version="cp-v1")
        current = CheckpointVersionVector(checkpoint_schema_version="cp-v2")
        pending = evaluate_checkpoint_version_compatibility(
            checkpoint,
            current,
            migratable_schema_pairs={("cp-v1", "cp-v2")},
        )
        self.assertFalse(pending.resume_allowed)
        self.assertTrue(pending.migration_required)
        self.assertFalse(pending.reconcile_required)
        ready = evaluate_checkpoint_version_compatibility(
            checkpoint,
            current,
            migratable_schema_pairs={("cp-v1", "cp-v2")},
            migration_completed=True,
            revalidated=True,
        )
        self.assertTrue(ready.resume_allowed)

    def test_existing_m2_checkpoint_field_aliases_normalize_into_m3_vector(self) -> None:
        vector = version_vector_from_mapping(
            {
                "schema_version": "cp-v1",
                "runtime_version": "rt-1",
                "provider_version": "provider-a",
                "model_version": "model-a",
                "tool_contract_version": "tools-a",
                "skill_contract_version": "skills-a",
                "task_contract_version": "contract-a",
            }
        )
        self.assertEqual(vector.checkpoint_schema_version, "cp-v1")
        self.assertEqual(vector.provider_profile_hash, "provider-a")
        self.assertEqual(vector.tool_registry_version, "tools-a")
        self.assertEqual(vector.skill_version, "skills-a")


if __name__ == "__main__":
    unittest.main()
