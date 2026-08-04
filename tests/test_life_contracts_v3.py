from __future__ import annotations

import unittest

from pydantic import ValidationError

from contracts import AppraisalVectorV3, LifeEventEnvelope, ViabilityDimension
from tests.life_contract_support import HASH_ZERO, SIGNATURE, dimension, event, viability_state


class LifeContractsV3Tests(unittest.TestCase):
    def test_event_and_viability_digests_are_deterministic_and_tamper_evident(self) -> None:
        first = event(1, None)
        self.assertTrue(first.has_valid_event_hash())
        self.assertEqual(first.event_hash, event(1, None).event_hash)
        self.assertFalse(
            first.model_copy(update={"content_sha256": "f" * 64}).has_valid_event_hash()
        )
        state = viability_state()
        self.assertTrue(state.has_valid_state_sha256())
        self.assertFalse(
            state.model_copy(update={"revision": 2}).has_valid_state_sha256()
        )

    def test_milli_values_are_strict_integers_and_chain_shape_fails_closed(self) -> None:
        payload = event(1, None).model_dump(mode="python")
        payload["source_credibility_milli"] = 1.0
        with self.assertRaises(ValidationError):
            LifeEventEnvelope(**payload)
        payload["source_credibility_milli"] = True
        with self.assertRaises(ValidationError):
            LifeEventEnvelope(**payload)
        with self.assertRaises(ValidationError):
            event(1, None).model_copy(
                update={"previous_event_hash": "1" * 64}
            ).model_validate(
                {
                    **event(1, None).model_dump(mode="python"),
                    "previous_event_hash": "1" * 64,
                }
            )
        with self.assertRaises(ValidationError):
            ViabilityDimension(
                value_milli=500,
                target_low_milli=900,
                target_high_milli=800,
                confidence_milli=900,
                source_event_ids=("lev_" + "1" * 64,),
                measured_at_ms=2_000,
                stale_after_ms=3_000,
            )

    def test_zero_credibility_cannot_create_affective_intensity(self) -> None:
        base = dict(
            appraisal_id="appraisal_test",
            life_id="life_contract_test",
            source_event_ids=("lev_" + "1" * 64,),
            viability_revision=1,
            novelty_milli=100,
            goal_congruence_milli=0,
            threat_milli=0,
            loss_milli=0,
            obstruction_milli=0,
            certainty_milli=100,
            controllability_milli=100,
            social_warmth_milli=0,
            social_trust_milli=0,
            intensity_milli=1,
            source_credibility_milli=0,
            self_relevance_milli=100,
            impact_on_others_milli=0,
            norm_relevance_milli=0,
            urgency_milli=0,
            repetition_factor_milli=1000,
            appraised_at_ms=2_000,
            appraisal_sha256=HASH_ZERO,
        )
        with self.assertRaises(ValidationError):
            AppraisalVectorV3(**base)
        valid = AppraisalVectorV3(
            **{**base, "intensity_milli": 0}
        ).with_computed_appraisal_sha256()
        self.assertTrue(valid.has_valid_appraisal_sha256())

    def test_event_rejects_future_occurrence_and_unsorted_subjects(self) -> None:
        base = event(1, None).model_dump(mode="python")
        with self.assertRaises(ValidationError):
            LifeEventEnvelope(
                **{
                    **base,
                    "occurred_at_ms": base["observed_at_ms"] + 1,
                }
            )
        with self.assertRaises(ValidationError):
            LifeEventEnvelope(
                **{
                    **base,
                    "subject_refs": ("z", "a"),
                    "event_hash": HASH_ZERO,
                    "signature": SIGNATURE,
                }
            )


if __name__ == "__main__":
    unittest.main()
