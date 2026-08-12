"""P15 M6: only temperament-eligible active L5 core memory adapts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from life_service.temperament import (
    generate_innate_temperament,
    initial_temperament_state,
)
from tests.life_contract_support import event


LIFE = "life_p15_core_only"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class TemperamentCoreOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "coreonly.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)
        self.innate = generate_innate_temperament(
            life_id=LIFE, seed=9, created_at="2026-08-12T00:00:00Z"
        )
        self.state = initial_temperament_state(self.innate)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l1(self, *, suffix: str):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _a, derivation, _c = self.coordinator.commit_life_event_l1(value)
        return value, derivation

    def test_l4_explicit_never_adapts_temperament(self) -> None:
        _value, l1 = self._l1(suffix="21" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，我比较外向。",
            plaintext=b"i am extraverted",
            created_at_ms=2_000,
            claim_key="claim:extravert",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
        )
        self.assertFalse(l4.temperament_eligible)
        adapted, receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=self.state,
            now_ms=3_000,
        )
        self.assertEqual(receipts, ())
        self.assertEqual(adapted, self.state)

    def test_l5_user_preference_never_adapts(self) -> None:
        _value, l1 = self._l1(suffix="22" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            created_at_ms=2_000,
            claim_key="claim:lang-only",
            semantic_domain="USER_PREFERENCE",
        )
        second_event = event(
            2, _value.event_hash, life_id=LIFE, suffix="23" * 32
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        _a3, l4b, _det2, _c3 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=second_event.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese again",
            created_at_ms=3_000,
            claim_key="claim:lang-only",
            semantic_domain="USER_PREFERENCE",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            candidate_derivation_ids=(
                l4.derivation_id,
                l4b.derivation_id,
            ),
            claim_key="claim:lang-only",
            semantic_domain="USER_PREFERENCE",
            plaintext=b"language core",
            created_at_ms=4_000,
            support_weights={
                l4.derivation_id: 750,
                l4b.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=0,
        )
        self.assertIsNotNone(l5)
        self.assertFalse(l5[1].temperament_eligible)
        adapted, receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=self.state,
            now_ms=5_000,
        )
        self.assertEqual(receipts, ())
        self.assertEqual(adapted, self.state)

    def test_l5_world_never_adapts(self) -> None:
        _value, l1 = self._l1(suffix="24" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，地球是平的。",
            plaintext=b"earth flat",
            created_at_ms=2_000,
            claim_key="claim:earth",
            semantic_domain="WORLD",
        )
        _adapted, receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=self.state,
            now_ms=3_000,
        )
        self.assertEqual(receipts, ())

    def test_corrected_l5_is_skipped_and_replacement_adapts(self) -> None:
        value = event(1, None, life_id=LIFE, suffix="25" * 32)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:inactive:diary",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:inactive",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        l4_event = event(
            2, value.event_hash, life_id=LIFE, suffix="26" * 32
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(l4_event)
        _a4, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=l4_event.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，这个模式很重要。",
            plaintext=b"pattern",
            created_at_ms=3_500,
            claim_key="claim:inactive",
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
            claim_key="claim:inactive",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"core",
            created_at_ms=5_000,
            support_weights={
                l3[1].derivation_id: 1000,
                l4.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=3,
        )
        self.assertIsNotNone(l5)
        correction_event = event(1, None, life_id=LIFE, suffix="27" * 32)
        _assertion, replacement, _invalidations, _created = (
            self.coordinator.correct_claim(
                life_id=LIFE,
                principal_ref=PRINCIPAL,
                privacy_scope=PRIVACY,
                target_derivation_id=l5[1].derivation_id,
                user_message_event_id=correction_event.event_id,
                plaintext=b"corrected",
                created_at_ms=6_000,
            )
        )
        self.assertFalse(
            self.store.is_derivation_active(l5[1].derivation_id)
        )
        _adapted, receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=self.state,
            now_ms=7_000,
        )
        self.assertEqual(len(receipts), 1)
        self.assertEqual(
            receipts[0]["derivation_id"], replacement.derivation_id
        )
        self.assertNotEqual(replacement.derivation_id, l5[1].derivation_id)

    def test_provider_deltas_are_respected(self) -> None:
        value = event(1, None, life_id=LIFE, suffix="28" * 32)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:provider:diary",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:provider",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        l4_event = event(
            2, value.event_hash, life_id=LIFE, suffix="29" * 32
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(l4_event)
        _a4, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=l4_event.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，这个模式很重要。",
            plaintext=b"pattern",
            created_at_ms=3_500,
            claim_key="claim:provider",
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
            claim_key="claim:provider",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"core",
            created_at_ms=5_000,
            support_weights={
                l3[1].derivation_id: 1000,
                l4.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=3,
        )
        self.assertIsNotNone(l5)
        adapted, _receipts = self.coordinator.adapt_temperament_from_core(
            life_id=LIFE,
            innate=self.innate,
            current_temperament=self.state,
            now_ms=6_000,
            trait_delta_provider=lambda _item: {"conscientiousness": 10},
        )
        self.assertEqual(
            adapted["traits_micro"]["conscientiousness"],
            self.state["traits_micro"]["conscientiousness"] + 10,
        )


if __name__ == "__main__":
    unittest.main()
