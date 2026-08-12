"""P15 M3: L5 core promotion paths (stability / reconfirm / fusion)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


class L5CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "l5.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l3_chain(
        self, *, sequence: int, previous: str | None, suffix: str, claim_key: str
    ):
        value = event(sequence, previous, suffix=suffix)
        base = value.observed_at_ms
        _a1, l1, _c = self.coordinator.commit_life_event_l1(
            value, event_payload=b"event"
        )
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key=claim_key + ":diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=base + 1_000,
        )
        self.assertIsNotNone(l2)
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key=claim_key,
            semantic_domain="WORLD",
            plaintext=b"experience",
            created_at_ms=base + 2_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        return value, l3[0], l3[1]

    def test_l5_stability_promotion_with_eligibility_flags(self) -> None:
        first_event = event(1, None, suffix="01" * 32)
        second_event = event(2, first_event.event_hash, suffix="02" * 32)
        third_event = event(3, second_event.event_hash, suffix="03" * 32)
        chains = (
            self._l3_chain(
                sequence=1,
                previous=None,
                suffix="01" * 32,
                claim_key="claim:core-1",
            ),
            self._l3_chain(
                sequence=2,
                previous=first_event.event_hash,
                suffix="02" * 32,
                claim_key="claim:core-2",
            ),
            self._l3_chain(
                sequence=3,
                previous=second_event.event_hash,
                suffix="03" * 32,
                claim_key="claim:core-3",
            ),
        )
        value = chains[0][0]
        candidates = tuple(item[2] for item in chains)
        promoted = self.coordinator.promote_to_l5(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            candidate_derivation_ids=tuple(
                item.derivation_id for item in candidates
            ),
            claim_key="claim:core-merged",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"core behavioral pattern",
            created_at_ms=20_000,
            support_weights={
                item.derivation_id: 1000 for item in candidates
            },
            counter_weights={},
            recurrence_count=3,
        )
        self.assertIsNotNone(promoted)
        assertion, derivation, created = promoted
        self.assertTrue(created)
        self.assertEqual(derivation.layer, "L5_CORE")
        self.assertEqual(derivation.semantic_domain, "SELF_BEHAVIOR_PATTERN")
        self.assertTrue(derivation.temperament_eligible)
        self.assertFalse(derivation.self_cognition_eligible)
        self.assertTrue(derivation.context_eligible)
        self.assertTrue(derivation.learning_eligible)
        self.assertEqual(assertion.retention_class, "LONG_TERM_MEMORY")
        head = self.store.get_active_memory_head(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            claim_key="claim:core-merged",
            layer="L5_CORE",
        )
        self.assertEqual(head.derivation_id, derivation.derivation_id)

    def test_l5_user_preference_is_not_temperament_eligible(self) -> None:
        value = event(1, None)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:pref:diary",
            semantic_domain="USER_PREFERENCE",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:pref",
            semantic_domain="USER_PREFERENCE",
            plaintext=b"preference experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        l4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            user_text="记住，我的长期偏好是简洁。",
            plaintext=b"be concise",
            created_at_ms=4_000,
            claim_key="claim:pref",
            semantic_domain="USER_PREFERENCE",
        )
        promoted = self.coordinator.promote_to_l5(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            candidate_derivation_ids=(
                l3[1].derivation_id,
                l4[1].derivation_id,
            ),
            claim_key="claim:pref",
            semantic_domain="USER_PREFERENCE",
            plaintext=b"long term preference",
            created_at_ms=5_000,
            support_weights={
                l3[1].derivation_id: 1000,
                l4[1].derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=1,
        )
        self.assertIsNotNone(promoted)
        _assertion, derivation, created = promoted
        self.assertTrue(created)
        self.assertEqual(derivation.layer, "L5_CORE")
        self.assertFalse(derivation.temperament_eligible)
        self.assertFalse(derivation.self_cognition_eligible)
        self.assertIn("l5_fusion", derivation.promotion_reason_codes)

    def test_l5_reconfirm_keeps_user_asserted_epistemic(self) -> None:
        value = event(1, None)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l4a = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            created_at_ms=2_000,
            claim_key="claim:language",
            semantic_domain="USER_PREFERENCE",
        )
        second_event = event(2, value.event_hash, suffix="2" * 64)
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        l4b = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=second_event.event_id,
            life_id=second_event.life_id,
            principal_ref=second_event.principal_ref,
            privacy_scope=second_event.privacy_scope,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese again",
            created_at_ms=3_000,
            claim_key="claim:language",
            semantic_domain="USER_PREFERENCE",
        )
        promoted = self.coordinator.promote_to_l5(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            candidate_derivation_ids=(l4a[1].derivation_id, l4b[1].derivation_id),
            claim_key="claim:language",
            semantic_domain="USER_PREFERENCE",
            plaintext=b"always chinese core",
            created_at_ms=4_000,
            support_weights={
                l4a[1].derivation_id: 750,
                l4b[1].derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=0,
        )
        self.assertIsNotNone(promoted)
        assertion, derivation, created = promoted
        self.assertTrue(created)
        self.assertIn("l5_reconfirm", derivation.promotion_reason_codes)
        self.assertEqual(assertion.epistemic_status, "user_asserted")

    def test_l5_denied_with_single_low_evidence_group(self) -> None:
        value = event(1, None)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:weak:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:weak",
            semantic_domain="WORLD",
            plaintext=b"weak experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=1,
        )
        self.assertIsNotNone(l3)
        promoted = self.coordinator.promote_to_l5(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            candidate_derivation_ids=(l3[1].derivation_id,),
            claim_key="claim:weak",
            semantic_domain="WORLD",
            plaintext=b"core?",
            created_at_ms=4_000,
            support_weights={l3[1].derivation_id: 700},
            counter_weights={},
            recurrence_count=1,
        )
        self.assertIsNone(promoted)

    def test_l5_derivation_always_has_semantic_domain(self) -> None:
        value = event(1, None)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            user_text="记住，我擅长写作。",
            plaintext=b"good at writing",
            created_at_ms=2_000,
            claim_key="claim:skill",
            semantic_domain="CAPABILITY_SELF",
        )
        second_event = event(2, value.event_hash, suffix="2" * 64)
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        l4b = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=second_event.event_id,
            life_id=second_event.life_id,
            principal_ref=second_event.principal_ref,
            privacy_scope=second_event.privacy_scope,
            user_text="记住，我擅长写作。",
            plaintext=b"good at writing again",
            created_at_ms=3_000,
            claim_key="claim:skill",
            semantic_domain="CAPABILITY_SELF",
        )
        promoted = self.coordinator.promote_to_l5(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            candidate_derivation_ids=(l4[1].derivation_id, l4b[1].derivation_id),
            claim_key="claim:skill",
            semantic_domain="CAPABILITY_SELF",
            plaintext=b"writing capability core",
            created_at_ms=4_000,
            support_weights={
                l4[1].derivation_id: 750,
                l4b[1].derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=0,
        )
        self.assertIsNotNone(promoted)
        _assertion, derivation, _created = promoted
        self.assertEqual(derivation.semantic_domain, "CAPABILITY_SELF")
        self.assertTrue(derivation.self_cognition_eligible)


if __name__ == "__main__":
    unittest.main()
