from __future__ import annotations

import unittest

from pydantic import ValidationError

from contracts import AppraisalVectorV3
from tests.life_contract_support import HASH_ZERO


def appraisal(**overrides) -> AppraisalVectorV3:
    values = {
        "appraisal_id": "appraisal_test",
        "life_id": "life_contract_test",
        "source_event_ids": ("lev_" + "1" * 64,),
        "viability_revision": 1,
        "novelty_milli": 500,
        "goal_congruence_milli": -200,
        "threat_milli": 300,
        "loss_milli": 100,
        "obstruction_milli": 100,
        "certainty_milli": 800,
        "controllability_milli": 600,
        "social_warmth_milli": 200,
        "social_trust_milli": 500,
        "intensity_milli": 300,
        "source_credibility_milli": 900,
        "self_relevance_milli": 400,
        "impact_on_others_milli": 600,
        "norm_relevance_milli": 700,
        "urgency_milli": 300,
        "repetition_factor_milli": 1000,
        "appraised_at_ms": 2_000,
        "appraisal_sha256": HASH_ZERO,
    }
    values.update(overrides)
    return AppraisalVectorV3(**values)


class AffectAppraisalV3Tests(unittest.TestCase):
    def test_signed_goal_congruence_and_digest_are_deterministic(self) -> None:
        value = appraisal().with_computed_appraisal_sha256()
        self.assertEqual(value.goal_congruence_milli, -200)
        self.assertTrue(value.has_valid_appraisal_sha256())
        self.assertEqual(
            value.appraisal_sha256,
            appraisal().with_computed_appraisal_sha256().appraisal_sha256,
        )

    def test_appraisal_rejects_out_of_range_float_and_duplicate_source(self) -> None:
        with self.assertRaises(ValidationError):
            appraisal(threat_milli=1001)
        with self.assertRaises(ValidationError):
            appraisal(threat_milli=1.0)
        with self.assertRaises(ValidationError):
            appraisal(
                source_event_ids=(
                    "lev_" + "1" * 64,
                    "lev_" + "1" * 64,
                )
            )

    def test_tampering_any_appraisal_dimension_invalidates_the_digest(self) -> None:
        value = appraisal().with_computed_appraisal_sha256()
        self.assertFalse(
            value.model_copy(update={"urgency_milli": 301}).has_valid_appraisal_sha256()
        )


if __name__ == "__main__":
    unittest.main()
