"""P15 M3: integer evidence math, lineage folding and promotion thresholds."""

from __future__ import annotations

import unittest

from contracts import MemoryDerivationV1
from life_service import memory_promotion


LIFE = "life_p15_math"
PRINCIPAL = "principal_alice"


def l1_derivation(
    *,
    derivation_id: str,
    root: str,
    claim_key: str,
    created_at_ms: int = 2_000,
) -> MemoryDerivationV1:
    return MemoryDerivationV1(
        derivation_id=derivation_id,
        life_id=LIFE,
        memory_id="mem_" + derivation_id.removeprefix("mdr_")[:64],
        memory_revision=1,
        memory_assertion_sha256="11" * 32,
        layer="L1_STREAM",
        semantic_domain="SYSTEM",
        origin="LIFE_EVENT",
        principal_ref=PRINCIPAL,
        workspace_ref=None,
        privacy_scope="private",
        claim_key=claim_key,
        parent_memory_refs=(),
        source_event_ids=(root,),
        lineage_root_event_ids=(root,),
        external_evidence_refs=(),
        promotion_policy_version="p15-layers-v1",
        promotion_reason_codes=(),
        valid_from_ms=created_at_ms,
        expires_at_ms=None,
        context_eligible=True,
        learning_eligible=False,
        temperament_eligible=False,
        self_cognition_eligible=False,
        world_candidate_eligible=False,
        created_at_ms=created_at_ms,
        derivation_sha256="0" * 64,
    ).with_computed_derivation_sha256()


class PromotionMathTests(unittest.TestCase):
    def test_noisy_or_exact_integer_values(self) -> None:
        self.assertEqual(memory_promotion.noisy_or(()), 0)
        self.assertEqual(memory_promotion.noisy_or((1000,)), 1000)
        self.assertEqual(memory_promotion.noisy_or((1000, 1000)), 1000)
        self.assertEqual(memory_promotion.noisy_or((750, 750)), 938)
        self.assertEqual(memory_promotion.noisy_or((500, 500)), 750)
        self.assertEqual(
            memory_promotion.noisy_or((750, 750, 750)),
            938 + 750 - (938 * 750) // 1000,
        )

    def test_noisy_or_rejects_non_integers_and_negatives(self) -> None:
        with self.assertRaises(ValueError):
            memory_promotion.noisy_or((750.5,))
        with self.assertRaises(ValueError):
            memory_promotion.noisy_or((-1,))

    def test_net_support_is_floor_at_zero(self) -> None:
        self.assertEqual(memory_promotion.net_support(900, 400), 500)
        self.assertEqual(memory_promotion.net_support(300, 900), 0)
        self.assertEqual(memory_promotion.net_support(0, 0), 0)

    def test_same_lineage_roots_fold_to_one_group(self) -> None:
        root = "lev_" + "1" * 64
        derivations = tuple(
            l1_derivation(
                derivation_id=f"mdr_{index:064x}",
                root=root,
                claim_key=f"claim:{index}",
            )
            for index in range(4)
        )
        weights = {
            item.derivation_id: 500 + index
            for index, item in enumerate(derivations)
        }
        groups = memory_promotion.fold_independence(derivations, weights)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].weight_milli, 503)

    def test_distinct_roots_form_separate_groups(self) -> None:
        roots = ("lev_" + "1" * 64, "lev_" + "2" * 64)
        derivations = tuple(
            l1_derivation(
                derivation_id=f"mdr_{index:064x}",
                root=roots[index % 2],
                claim_key=f"claim:{index}",
            )
            for index in range(4)
        )
        groups = memory_promotion.fold_independence(
            derivations, {item.derivation_id: 750 for item in derivations}
        )
        self.assertEqual(len(groups), 2)

    def test_repeated_summary_never_inflates_independence(self) -> None:
        root = "lev_" + "9" * 64
        same_event = tuple(
            l1_derivation(
                derivation_id=f"mdr_{index:064x}",
                root=root,
                claim_key="claim:same-event",
            )
            for index in range(6)
        )
        groups = memory_promotion.fold_independence(
            same_event, {item.derivation_id: 750 for item in same_event}
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].weight_milli, 750)

    def test_l3_denied_when_support_below_threshold(self) -> None:
        first = l1_derivation(
            derivation_id="mdr_" + "1" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:a",
        )
        disposition = memory_promotion.evaluate_l3(
            l2_derivations=(first,),
            support_weights={first.derivation_id: 600},
            counter_weights={},
            causal_utility_milli={first.derivation_id: 800},
            recurrence_count=2,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:a",
            semantic_domain="SYSTEM",
            policy_version="p15-l3-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertFalse(disposition.allowed)
        self.assertIn("insufficient_support", disposition.reason_codes)

    def test_l3_denied_when_counter_too_high(self) -> None:
        first = l1_derivation(
            derivation_id="mdr_" + "2" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:b",
        )
        disposition = memory_promotion.evaluate_l3(
            l2_derivations=(first,),
            support_weights={first.derivation_id: 1000},
            counter_weights={first.derivation_id: 900},
            causal_utility_milli={first.derivation_id: 800},
            recurrence_count=2,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:b",
            semantic_domain="SYSTEM",
            policy_version="p15-l3-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertFalse(disposition.allowed)
        self.assertIn("counter_too_high", disposition.reason_codes)

    def test_l3_allowed_via_direct_verified_causal_utility(self) -> None:
        first = l1_derivation(
            derivation_id="mdr_" + "3" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:c",
        )
        disposition = memory_promotion.evaluate_l3(
            l2_derivations=(first,),
            support_weights={first.derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={first.derivation_id: 800},
            recurrence_count=1,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:c",
            semantic_domain="SYSTEM",
            policy_version="p15-l3-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertTrue(disposition.allowed)
        self.assertIn("l3_support_threshold", disposition.reason_codes)

    def test_l3_allowed_via_two_groups_and_recurrence(self) -> None:
        first = l1_derivation(
            derivation_id="mdr_" + "4" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:d",
        )
        second = l1_derivation(
            derivation_id="mdr_" + "5" * 64,
            root="lev_" + "2" * 64,
            claim_key="claim:d",
        )
        disposition = memory_promotion.evaluate_l3(
            l2_derivations=(first, second),
            support_weights={
                first.derivation_id: 750,
                second.derivation_id: 750,
            },
            counter_weights={},
            causal_utility_milli={
                first.derivation_id: 0,
                second.derivation_id: 0,
            },
            recurrence_count=2,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:d",
            semantic_domain="SYSTEM",
            policy_version="p15-l3-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertTrue(disposition.allowed)
        self.assertEqual(disposition.independence_group_count, 2)

    def test_l5_stability_requires_three_groups_and_direct_evidence(self) -> None:
        candidates = tuple(
            l1_derivation(
                derivation_id=f"mdr_{index:064x}",
                root=f"lev_{index:064x}",
                claim_key="claim:core",
            )
            for index in (1, 2, 3)
        )
        disposition = memory_promotion.evaluate_l5(
            candidates=candidates,
            support_weights={
                item.derivation_id: 1000 for item in candidates
            },
            counter_weights={},
            recurrence_count=3,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:core",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            policy_version="p15-l5-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertTrue(disposition.allowed)
        self.assertIn("l5_stability", disposition.reason_codes)
        self.assertEqual(disposition.independence_group_count, 3)

    def test_l5_reconfirm_from_two_explicit_l4(self) -> None:
        first = l1_derivation(
            derivation_id="mdr_" + "a" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:pref",
        ).model_copy(
            update={
                "layer": "L4_EXPLICIT",
                "origin": "USER_EXPLICIT",
                "semantic_domain": "USER_PREFERENCE",
            }
        ).with_computed_derivation_sha256()
        second = first.model_copy(
            update={
                "derivation_id": "mdr_" + "b" * 64,
                "lineage_root_event_ids": ("lev_" + "2" * 64,),
            }
        ).with_computed_derivation_sha256()
        disposition = memory_promotion.evaluate_l5(
            candidates=(first, second),
            support_weights={
                first.derivation_id: 750,
                second.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=0,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:pref",
            semantic_domain="USER_PREFERENCE",
            policy_version="p15-l5-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertTrue(disposition.allowed)
        self.assertIn("l5_reconfirm", disposition.reason_codes)

    def test_l5_fusion_requires_l3_and_l4(self) -> None:
        l3 = l1_derivation(
            derivation_id="mdr_" + "c" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:fusion",
        ).model_copy(
            update={
                "layer": "L3_EXPERIENCE",
                "origin": "PROMOTION",
                "semantic_domain": "WORLD",
            }
        ).with_computed_derivation_sha256()
        l4 = l3.model_copy(
            update={
                "derivation_id": "mdr_" + "d" * 64,
                "layer": "L4_EXPLICIT",
                "origin": "USER_EXPLICIT",
                "lineage_root_event_ids": ("lev_" + "2" * 64,),
            }
        ).with_computed_derivation_sha256()
        disposition = memory_promotion.evaluate_l5(
            candidates=(l3, l4),
            support_weights={
                l3.derivation_id: 1000,
                l4.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=1,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:fusion",
            semantic_domain="WORLD",
            policy_version="p15-l5-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertTrue(disposition.allowed)
        self.assertIn("l5_fusion", disposition.reason_codes)

    def test_temporary_expiry_blocks_l5(self) -> None:
        candidate = l1_derivation(
            derivation_id="mdr_" + "e" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:expiry",
        ).model_copy(
            update={
                "layer": "L4_EXPLICIT",
                "origin": "USER_EXPLICIT",
                "expires_at_ms": 86_400_000,
                "semantic_domain": "USER_PREFERENCE",
            }
        ).with_computed_derivation_sha256()
        disposition = memory_promotion.evaluate_l5(
            candidates=(candidate, candidate),
            support_weights={candidate.derivation_id: 750},
            counter_weights={},
            recurrence_count=0,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:expiry",
            semantic_domain="USER_PREFERENCE",
            policy_version="p15-l5-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
        )
        self.assertFalse(disposition.allowed)
        self.assertIn("temporary_expiry_blocks_l5", disposition.reason_codes)

    def test_l2_requires_episode_boundary_and_l1_inputs(self) -> None:
        first = l1_derivation(
            derivation_id="mdr_" + "f" * 64,
            root="lev_" + "1" * 64,
            claim_key="claim:l2",
        )
        allowed = memory_promotion.evaluate_l2(
            l1_derivations=(first,),
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:l2",
            semantic_domain="SYSTEM",
            policy_version="p15-l2-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
            episode_boundary=True,
        )
        self.assertTrue(allowed.allowed)
        denied = memory_promotion.evaluate_l2(
            l1_derivations=(first,),
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key="claim:l2",
            semantic_domain="SYSTEM",
            policy_version="p15-l2-v1",
            valid_from_ms=3_000,
            created_at_ms=3_000,
            episode_boundary=False,
        )
        self.assertFalse(denied.allowed)
        self.assertIn("no_episode_boundary", denied.reason_codes)


if __name__ == "__main__":
    unittest.main()
