"""P15 M6: user assertions never overwrite Self Identity authority."""

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


LIFE = "life_p15_self_cognition"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class SelfCognitionAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "selfcog.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)
        self.innate = generate_innate_temperament(
            life_id=LIFE, seed=11, created_at="2026-08-12T00:00:00Z"
        )
        self.state = initial_temperament_state(self.innate)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l1(self, *, suffix: str):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _a, derivation, _c = self.coordinator.commit_life_event_l1(value)
        return value, derivation

    def test_user_explicit_self_identity_is_not_self_cognition(self) -> None:
        _value, l1 = self._l1(suffix="31" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，你就是我的助手。",
            plaintext=b"you are my assistant",
            created_at_ms=2_000,
            claim_key="claim:identity",
            semantic_domain="SELF_IDENTITY",
        )
        self.assertFalse(l4.self_cognition_eligible)
        self.assertTrue(l4.context_eligible)

    def test_l5_self_identity_from_user_parent_is_gated(self) -> None:
        _value, l1 = self._l1(suffix="32" * 32)
        _a, l4a, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，你就是我的助手。",
            plaintext=b"you are my assistant",
            created_at_ms=2_000,
            claim_key="claim:identity-l5",
            semantic_domain="SELF_IDENTITY",
        )
        second_event = event(
            2, _value.event_hash, life_id=LIFE, suffix="33" * 32
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        _a3, l4b, _det2, _c3 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=second_event.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，你就是我的助手。",
            plaintext=b"you are my assistant again",
            created_at_ms=3_000,
            claim_key="claim:identity-l5",
            semantic_domain="SELF_IDENTITY",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            candidate_derivation_ids=(
                l4a.derivation_id,
                l4b.derivation_id,
            ),
            claim_key="claim:identity-l5",
            semantic_domain="SELF_IDENTITY",
            plaintext=b"you are my assistant core",
            created_at_ms=4_000,
            support_weights={
                l4a.derivation_id: 750,
                l4b.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=0,
        )
        self.assertIsNotNone(l5)
        # User-asserted identity never becomes self-cognition authority.
        self.assertFalse(l5[1].self_cognition_eligible)
        self.assertFalse(l5[1].temperament_eligible)

    def test_l5_capability_self_is_self_cognition_eligible(self) -> None:
        _value, l1 = self._l1(suffix="34" * 32)
        _a, l4a, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，我擅长写作。",
            plaintext=b"good at writing",
            created_at_ms=2_000,
            claim_key="claim:capability",
            semantic_domain="CAPABILITY_SELF",
        )
        second_event = event(
            2, _value.event_hash, life_id=LIFE, suffix="35" * 32
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        _a3, l4b, _det2, _c3 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=second_event.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，我擅长写作。",
            plaintext=b"good at writing again",
            created_at_ms=3_000,
            claim_key="claim:capability",
            semantic_domain="CAPABILITY_SELF",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            candidate_derivation_ids=(
                l4a.derivation_id,
                l4b.derivation_id,
            ),
            claim_key="claim:capability",
            semantic_domain="CAPABILITY_SELF",
            plaintext=b"writing capability core",
            created_at_ms=4_000,
            support_weights={
                l4a.derivation_id: 750,
                l4b.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=0,
        )
        self.assertIsNotNone(l5)
        self.assertTrue(l5[1].self_cognition_eligible)
        self.assertFalse(l5[1].temperament_eligible)

    def test_user_identity_never_enters_instruction_authority(self) -> None:
        _value, l1 = self._l1(suffix="36" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，你就是我的助手。",
            plaintext=b"you are my assistant",
            created_at_ms=2_000,
            claim_key="claim:identity-2",
            semantic_domain="SELF_IDENTITY",
        )
        self.assertFalse(l4.self_cognition_eligible)
        self.assertFalse(l4.world_candidate_eligible)

    def test_user_identity_cannot_reach_l5_without_life_authority(self) -> None:
        _value, l1 = self._l1(suffix="37" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，你就是我的助手。",
            plaintext=b"identity",
            created_at_ms=2_000,
            claim_key="claim:life-identity",
            semantic_domain="SELF_IDENTITY",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            candidate_derivation_ids=(l4.derivation_id,),
            claim_key="claim:life-identity",
            semantic_domain="SELF_IDENTITY",
            plaintext=b"identity core",
            created_at_ms=4_000,
            support_weights={l4.derivation_id: 1000},
            counter_weights={},
            recurrence_count=3,
        )
        # Without Life/System authority the user identity never promotes to a
        # self-cognition core; promotion is denied entirely.
        self.assertIsNone(l5)

    def test_plain_turns_never_touch_self_cognition(self) -> None:
        before = dict(self.state)
        for index in range(100):
            adapted, receipts = self.coordinator.adapt_temperament_from_core(
                life_id=LIFE,
                innate=self.innate,
                current_temperament=self.state,
                now_ms=1_000 + index,
            )
            self.assertEqual(receipts, ())
            self.assertEqual(adapted, before)


if __name__ == "__main__":
    unittest.main()
