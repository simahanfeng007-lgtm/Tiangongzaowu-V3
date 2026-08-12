"""P15 M6: core-memory temperament adaptation and exactly-once receipts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from life_service.temperament import (
    adapt_from_core_memory,
    generate_innate_temperament,
    initial_temperament_state,
)
from tests.life_contract_support import event


LIFE = "life_p15_temperament"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class LifeTemperamentP15Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "temperament.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)
        self.innate = generate_innate_temperament(
            life_id=LIFE, seed=7, created_at="2026-08-12T00:00:00Z"
        )
        self.state = initial_temperament_state(self.innate)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l5_behavioral(self, *, suffix: str, claim_key: str):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key=claim_key + ":diary",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key=claim_key,
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        l4_event = event(
            2, value.event_hash, life_id=LIFE, suffix="04" * 32
        )
        _a4, l1b, _c4 = self.coordinator.commit_life_event_l1(l4_event)
        _a, l4, _det, _created = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=l4_event.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，这个模式很重要。",
            plaintext=b"explicit pattern",
            created_at_ms=3_500,
            claim_key=claim_key,
            semantic_domain="SELF_BEHAVIOR_PATTERN",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            candidate_derivation_ids=(
                l3[1].derivation_id,
                l4.derivation_id,
            ),
            claim_key=claim_key,
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"core behavioral pattern",
            created_at_ms=5_000,
            support_weights={
                l3[1].derivation_id: 1000,
                l4.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=3,
        )
        self.assertIsNotNone(l5)
        self.assertTrue(l5[1].temperament_eligible)
        return value, l5[1]

    def test_adapt_from_core_memory_applies_bounded_delta(self) -> None:
        state, outcome = adapt_from_core_memory(
            self.innate,
            self.state,
            evidence_refs=("mdr_" + "1" * 64,),
            trait_delta_micro={"openness": 5},
        )
        self.assertTrue(outcome["applied"])
        self.assertEqual(
            state["traits_micro"]["openness"],
            self.state["traits_micro"]["openness"] + 5,
        )
        self.assertIn("mdr_" + "1" * 64, state["core_memory_evidence_ids"])

    def test_adaptation_is_idempotent_per_evidence(self) -> None:
        evidence = ("mdr_" + "2" * 64,)
        first, _outcome = adapt_from_core_memory(
            self.innate,
            self.state,
            evidence_refs=evidence,
            trait_delta_micro={"openness": 3},
        )
        second, second_outcome = adapt_from_core_memory(
            self.innate,
            first,
            evidence_refs=evidence,
            trait_delta_micro={"openness": 3},
        )
        self.assertFalse(second_outcome["applied"])
        self.assertEqual(
            second["traits_micro"]["openness"],
            first["traits_micro"]["openness"],
        )

    def test_delta_exceeding_bound_is_clamped(self) -> None:
        state, _outcome = adapt_from_core_memory(
            self.innate,
            self.state,
            evidence_refs=("mdr_" + "3" * 64,),
            trait_delta_micro={"openness": 10_000},
        )
        self.assertLessEqual(
            state["traits_micro"]["openness"]
            - self.state["traits_micro"]["openness"],
            100,
        )

    def test_empty_evidence_raises(self) -> None:
        with self.assertRaises(ValueError):
            adapt_from_core_memory(
                self.innate,
                self.state,
                evidence_refs=(),
                trait_delta_micro={},
            )

    def test_hundred_plain_cycles_without_l5_never_change_temperament(self) -> None:
        before = dict(self.state)
        for _ in range(100):
            adapted, receipts = self.coordinator.adapt_temperament_from_core(
                life_id=LIFE,
                innate=self.innate,
                current_temperament=self.state,
                now_ms=1_000,
            )
            self.assertEqual(receipts, ())
            self.assertEqual(adapted, before)
        self.assertEqual(self.state, before)

    def test_eligible_l5_adapts_exactly_once(self) -> None:
        _value, l5 = self._l5_behavioral(
            suffix="11" * 32, claim_key="claim:exactly-once"
        )
        first_state, first_receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=self.state,
            now_ms=6_000,
        )
        self.assertEqual(len(first_receipts), 1)
        self.assertEqual(
            first_receipts[0]["derivation_id"], l5.derivation_id
        )
        second_state, second_receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=first_state,
            now_ms=7_000,
        )
        self.assertEqual(second_receipts, ())
        self.assertEqual(
            second_state["traits_micro"],
            first_state["traits_micro"],
        )
        self.assertTrue(
            self.store.has_temperament_receipt(LIFE, l5.derivation_id)
        )
        receipts = self.store.list_temperament_receipts(life_id=LIFE)
        self.assertEqual(len(receipts), 1)

    def test_receipt_records_evidence_and_delta(self) -> None:
        _value, l5 = self._l5_behavioral(
            suffix="12" * 32, claim_key="claim:receipt-evidence"
        )
        _state, receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=self.state,
            now_ms=6_000,
        )
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(receipts[0]["trait_delta_sha256"]), 64)
        stored = self.store.list_temperament_receipts(life_id=LIFE)
        self.assertEqual(stored[0]["derivation_id"], l5.derivation_id)


if __name__ == "__main__":
    unittest.main()
