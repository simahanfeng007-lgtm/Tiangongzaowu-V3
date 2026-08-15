"""P18-M3 learning-poisoning certification over the existing Memory SSoT."""
from __future__ import annotations

import unittest

from contracts import MemoryDerivationV1
from life_service import memory_promotion


LIFE = "life_p18_m3_learning"
PRINCIPAL = "principal_p18_m3"


def derivation(
    *,
    derivation_id: str,
    root: str,
    layer: str,
    origin: str,
    claim_key: str = "claim:p18-m3",
) -> MemoryDerivationV1:
    return MemoryDerivationV1(
        derivation_id=derivation_id,
        life_id=LIFE,
        memory_id="mem_" + derivation_id.removeprefix("mdr_")[:64],
        memory_revision=1,
        memory_assertion_sha256="11" * 32,
        layer=layer,
        semantic_domain="SYSTEM",
        origin=origin,
        principal_ref=PRINCIPAL,
        workspace_ref=None,
        privacy_scope="private",
        claim_key=claim_key,
        parent_memory_refs=(),
        source_event_ids=(root,),
        lineage_root_event_ids=(root,),
        external_evidence_refs=(),
        promotion_policy_version="p18-m3-learning-v1",
        promotion_reason_codes=(),
        valid_from_ms=2_000,
        expires_at_ms=None,
        context_eligible=True,
        learning_eligible=True,
        temperament_eligible=False,
        self_cognition_eligible=False,
        world_candidate_eligible=False,
        created_at_ms=2_000,
        derivation_sha256="0" * 64,
    ).with_computed_derivation_sha256()


class P18M3LearningPoisoningTests(unittest.TestCase):
    def test_one_model_inference_has_zero_promotion_evidence_weight(self) -> None:
        self.assertEqual(memory_promotion.BASE_EVIDENCE_WEIGHT_MILLI["model_inference"], 0)
        self.assertEqual(memory_promotion.BASE_EVIDENCE_WEIGHT_MILLI["reflection"], 0)
        self.assertEqual(memory_promotion.BASE_EVIDENCE_WEIGHT_MILLI["prospective"], 0)

    def test_model_inference_only_cannot_promote_to_l3(self) -> None:
        candidate = derivation(
            derivation_id="mdr_" + "1" * 64,
            root="lev_" + "1" * 64,
            layer="L2_DIARY",
            origin="MODEL_INFERENCE",
        )
        disposition = memory_promotion.evaluate_l3(
            l2_derivations=(candidate,),
            support_weights={
                candidate.derivation_id: memory_promotion.BASE_EVIDENCE_WEIGHT_MILLI["model_inference"]
            },
            counter_weights={},
            causal_utility_milli={candidate.derivation_id: 0},
            recurrence_count=1,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key=candidate.claim_key,
            semantic_domain="SYSTEM",
            policy_version="p18-m3-learning-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertFalse(disposition.allowed)
        self.assertEqual(disposition.support_milli, 0)
        self.assertIn("insufficient_support", disposition.reason_codes)

    def test_repeated_same_model_inference_lineage_does_not_create_independence(self) -> None:
        root = "lev_" + "2" * 64
        candidates = tuple(
            derivation(
                derivation_id=f"mdr_{index:064x}",
                root=root,
                layer="L2_DIARY",
                origin="MODEL_INFERENCE",
            )
            for index in range(1, 6)
        )
        groups = memory_promotion.fold_independence(
            candidates,
            {
                item.derivation_id: memory_promotion.BASE_EVIDENCE_WEIGHT_MILLI["model_inference"]
                for item in candidates
            },
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].weight_milli, 0)

    def test_two_explicit_user_memories_remain_valid_l5_reconfirmation_path(self) -> None:
        first = derivation(
            derivation_id="mdr_" + "3" * 64,
            root="lev_" + "3" * 64,
            layer="L4_EXPLICIT",
            origin="USER_EXPLICIT",
            claim_key="user:preference",
        )
        second = derivation(
            derivation_id="mdr_" + "4" * 64,
            root="lev_" + "4" * 64,
            layer="L4_EXPLICIT",
            origin="USER_EXPLICIT",
            claim_key="user:preference",
        )
        disposition = memory_promotion.evaluate_l5(
            candidates=(first, second),
            support_weights={first.derivation_id: 750, second.derivation_id: 750},
            counter_weights={},
            recurrence_count=2,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="user:preference",
            semantic_domain="SYSTEM",
            policy_version="p18-m3-learning-v1",
            valid_from_ms=4_000,
            created_at_ms=4_000,
        )
        self.assertTrue(disposition.allowed)
        self.assertIn("l5_reconfirm", disposition.reason_codes)

    def test_explicit_user_memory_is_not_zero_weight_model_inference(self) -> None:
        self.assertGreater(memory_promotion.BASE_EVIDENCE_WEIGHT_MILLI["user_asserted"], 0)
        self.assertEqual(memory_promotion.BASE_EVIDENCE_WEIGHT_MILLI["model_inference"], 0)


if __name__ == "__main__":
    unittest.main()
