from __future__ import annotations

import unittest

from pydantic import ValidationError

from contracts import (
    AgencyDecision,
    CapabilityProfile,
    ReflectionCard,
)
from life_service.agency import compute_agency_score
from tests.life_contract_support import HASH_ZERO


EPISODE_ID = "cep_" + "1" * 64


class AgencyContractsV3Tests(unittest.TestCase):
    def score(self):
        return compute_agency_score(
            goal_gain_milli=500,
            viability_gain_milli=200,
            information_gain_milli=100,
            relationship_value_milli=100,
            resource_cost_milli=100,
            expected_harm_milli=100,
            uncertainty_penalty_milli=100,
            irreversibility_penalty_milli=100,
        )

    def decision(self, **overrides) -> AgencyDecision:
        values = {
            "decision_id": "agd_" + "2" * 64,
            "life_id": "life_contract_test",
            "episode_id": EPISODE_ID,
            "candidate_set_sha256": "3" * 64,
            "selected_candidate_id": "action_test",
            "action_impact_sha256": "4" * 64,
            "score_breakdown": self.score(),
            "computed_risk": "A3",
            "policy_ceiling": "A4",
            "required_confirmation": False,
            "confirmation_grant_ref": None,
            "required_skill_activation": False,
            "skill_activation_ref": None,
            "outcome": "execute",
            "reason_codes": ("agency.utility_positive",),
            "state_revision_hashes": ("5" * 64,),
            "policy_snapshot_hash": "6" * 64,
            "created_at_ms": 2_000,
            "decision_sha256": HASH_ZERO,
        }
        values.update(overrides)
        return AgencyDecision(**values)

    def test_execution_is_digest_bound_a4_is_autonomous_and_a5_is_blocked(self) -> None:
        decision = self.decision().with_computed_decision_sha256()
        self.assertTrue(decision.has_valid_decision_sha256())
        autonomous = self.decision(computed_risk="A4").with_computed_decision_sha256()
        self.assertTrue(autonomous.has_valid_decision_sha256())
        with self.assertRaises(ValidationError):
            self.decision(
                computed_risk="A4",
                required_confirmation=True,
                confirmation_grant_ref="confirmation_test",
            )
        with self.assertRaises(ValidationError):
            self.decision(computed_risk="A5")

    def test_a5_and_policy_ceiling_cannot_be_bypassed(self) -> None:
        with self.assertRaises(ValidationError):
            self.decision(
                computed_risk="A5",
                policy_ceiling="A5",
                required_confirmation=True,
                confirmation_grant_ref="confirmation_test",
            )
        with self.assertRaises(ValidationError):
            self.decision(
                computed_risk="A4",
                policy_ceiling="A3",
                required_confirmation=True,
                confirmation_grant_ref="confirmation_test",
            )

    def test_reflection_question_requires_positive_information_value(self) -> None:
        base = {
            "reflection_id": "rfc_" + "7" * 64,
            "life_id": "life_contract_test",
            "episode_id": EPISODE_ID,
            "expected_outcome": "任务成功。",
            "observed_outcome": "任务失败。",
            "prediction_error_milli": 800,
            "success_dimensions": (),
            "failure_dimensions": ("tool_error",),
            "candidate_cause_ids": (),
            "counterevidence_refs": (),
            "alternative_explanations": ("环境异常。",),
            "counterfactual_actions": ("先运行最小探针。",),
            "next_minimal_experiment": "运行只读探针。",
            "lessons": ("执行前验证环境。",),
            "memory_candidate_refs": (),
            "capability_evidence_refs": (),
            "user_question": None,
            "user_question_value_of_information_milli": 0,
            "confidence_milli": 600,
            "reviewer": "model_assisted",
            "created_at_ms": 2_000,
            "reflection_sha256": HASH_ZERO,
        }
        reflection = ReflectionCard(**base).with_computed_reflection_sha256()
        self.assertTrue(reflection.has_valid_reflection_sha256())
        with self.assertRaises(ValidationError):
            ReflectionCard(
                **{
                    **base,
                    "user_question": "你更希望采用哪种方式？",
                    "user_question_value_of_information_milli": 0,
                }
            )

    def test_capability_without_evidence_cannot_claim_proficiency(self) -> None:
        base = {
            "capability_id": "capability_test",
            "life_id": "life_contract_test",
            "version": "v1",
            "profile_revision": 1,
            "supersedes_profile_sha256": None,
            "scope": "仅用于测试。",
            "verified_successes": 0,
            "verified_failures": 0,
            "independent_context_count": 0,
            "calibration_error_milli": 0,
            "rollback_count": 0,
            "last_regression_at_ms": None,
            "proficiency_mean_milli": 0,
            "proficiency_lower_bound_milli": 0,
            "evidence_refs": (),
            "impact_floor": "A1",
            "review_level": "OBSERVE",
            "updated_at_ms": 2_000,
            "profile_sha256": HASH_ZERO,
        }
        empty = CapabilityProfile(**base).with_computed_profile_sha256()
        self.assertTrue(empty.has_valid_profile_sha256())
        with self.assertRaises(ValidationError):
            CapabilityProfile(**{**base, "proficiency_mean_milli": 500})
        with self.assertRaises(ValidationError):
            CapabilityProfile(
                **{
                    **base,
                    "verified_successes": 1,
                    "independent_context_count": 1,
                }
            )


if __name__ == "__main__":
    unittest.main()
