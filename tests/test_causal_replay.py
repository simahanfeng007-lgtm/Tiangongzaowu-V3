from __future__ import annotations

import unittest

from pydantic import ValidationError

from contracts import CausalHypothesis
from life_service.replay import replay_life_events
from tests.life_contract_support import HASH_ZERO, event


class CausalReplayTests(unittest.TestCase):
    def test_event_chain_replays_to_one_deterministic_digest(self) -> None:
        first = event(1, None)
        second = event(2, first.event_hash, writer_epoch=2)
        summary = replay_life_events((first, second))
        self.assertEqual(summary.event_count, 2)
        self.assertEqual(summary.writer_epoch, 2)
        self.assertEqual(summary.head_event_hash, second.event_hash)
        self.assertEqual(summary, replay_life_events((first, second)))

    def test_gap_mixed_identity_epoch_regression_and_tamper_fail_closed(self) -> None:
        first = event(1, None, writer_epoch=2)
        with self.assertRaisesRegex(ValueError, "sequence"):
            replay_life_events((first, event(3, first.event_hash, writer_epoch=2)))
        with self.assertRaisesRegex(ValueError, "identities"):
            replay_life_events(
                (first, event(2, first.event_hash, life_id="other_life", writer_epoch=2))
            )
        with self.assertRaisesRegex(ValueError, "epoch"):
            replay_life_events((first, event(2, first.event_hash, writer_epoch=1)))
        with self.assertRaisesRegex(ValueError, "digest"):
            replay_life_events(
                (first, event(2, first.event_hash, writer_epoch=2).model_copy(
                    update={"content_sha256": "f" * 64}
                ))
            )

    def hypothesis(self, **overrides) -> CausalHypothesis:
        values = {
            "hypothesis_id": "chy_" + "1" * 64,
            "life_id": "life_contract_test",
            "cause_ref": "cause_test",
            "effect_ref": "effect_test",
            "relation": "correlated_with",
            "causal_basis": "correlation",
            "mechanism_summary": "",
            "confidence_milli": 600,
            "evidence_class": "observed",
            "supporting_event_ids": ("lev_" + "1" * 64,),
            "counterevidence_event_ids": (),
            "alternative_hypothesis_ids": (),
            "confounder_refs": (),
            "intervention_status": "none",
            "valid_from_ms": 1_000,
            "valid_until_ms": None,
            "supersedes_id": None,
            "status": "supported",
            "revision": 1,
            "hypothesis_sha256": HASH_ZERO,
        }
        values.update(overrides)
        return CausalHypothesis(**values)

    def test_correlation_and_model_inference_cannot_claim_strong_causality(self) -> None:
        correlation = self.hypothesis().with_computed_hypothesis_sha256()
        self.assertTrue(correlation.has_valid_hypothesis_sha256())
        with self.assertRaises(ValidationError):
            self.hypothesis(confidence_milli=701)
        with self.assertRaises(ValidationError):
            self.hypothesis(
                relation="causes",
                causal_basis="correlation",
                confidence_milli=600,
            )
        with self.assertRaises(ValidationError):
            self.hypothesis(
                relation="contributes_to",
                causal_basis="model_hypothesis",
                evidence_class="model_inference",
                confidence_milli=751,
            )

    def test_supported_causes_requires_mechanism_and_intervention_evidence(self) -> None:
        causal = self.hypothesis(
            relation="causes",
            causal_basis="intervention_supported",
            mechanism_summary="在受控试验中改变输入会稳定改变结果。",
            confidence_milli=900,
            evidence_class="execution_verified",
            intervention_status="repeated_intervention",
        ).with_computed_hypothesis_sha256()
        self.assertTrue(causal.has_valid_hypothesis_sha256())
        with self.assertRaises(ValidationError):
            self.hypothesis(
                relation="causes",
                causal_basis="intervention_supported",
                mechanism_summary="",
                intervention_status="repeated_intervention",
            )


if __name__ == "__main__":
    unittest.main()
