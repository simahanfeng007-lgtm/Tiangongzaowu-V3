"""P15 M3: correction cascades invalidate descendants without double edges."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import (
    MemoryCoordinator,
    MemoryCoordinatorError,
)
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


class CorrectionInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "inval.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)
        self.life = "life_p15_inval"

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l3_l4_and_l5(self):
        value = event(1, None, life_id=self.life)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:core:diary",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:core",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        second_event = event(
            2, value.event_hash, life_id=self.life, suffix="2" * 64
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        l4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1b.derivation_id,
            user_message_event_id=second_event.event_id,
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            user_text="记住，这个模式很重要。",
            plaintext=b"explicit core pattern",
            created_at_ms=3_500,
            claim_key="claim:core",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            candidate_derivation_ids=(
                l3[1].derivation_id,
                l4[1].derivation_id,
            ),
            claim_key="claim:core",
            semantic_domain="SELF_BEHAVIOR_PATTERN",
            plaintext=b"core pattern",
            created_at_ms=4_000,
            support_weights={
                l3[1].derivation_id: 1000,
                l4[1].derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=3,
        )
        self.assertIsNotNone(l5)
        return value, l3[1], l4[1], l5[1]

    def test_single_parent_l3_becomes_stale_when_l2_corrected(self) -> None:
        value = event(1, None, life_id=self.life)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:single:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:single",
            semantic_domain="SYSTEM",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        correction_event = event(1, None, life_id=self.life, suffix="ff" * 32)
        _assertion, replacement, invalidations, created = (
            self.coordinator.correct_claim(
                life_id=self.life,
                principal_ref=value.principal_ref,
                privacy_scope=value.privacy_scope,
                target_derivation_id=l2[1].derivation_id,
                user_message_event_id=correction_event.event_id,
                plaintext=b"corrected diary",
                created_at_ms=4_000,
            )
        )
        self.assertTrue(created)
        self.assertGreaterEqual(len(invalidations), 2)
        self.assertFalse(self.store.is_derivation_active(l2[1].derivation_id))
        self.assertFalse(self.store.is_derivation_active(l3[1].derivation_id))
        l3_stale = self.store.list_memory_invalidations(
            derivation_id=l3[1].derivation_id
        )
        self.assertEqual(len(l3_stale), 1)
        self.assertEqual(l3_stale[0].reason, "stale")
        head = self.store.get_active_memory_head(
            life_id=self.life,
            principal_ref=value.principal_ref,
            claim_key="claim:single",
            layer="L3_EXPERIENCE",
        )
        self.assertIsNone(head)

    def test_fusion_l5_stays_until_all_parents_invalidated(self) -> None:
        value, l3, l4, l5 = self._l3_l4_and_l5()
        correction_event = event(1, None, life_id=self.life, suffix="ff" * 32)
        _a, _r, _i, created = self.coordinator.correct_claim(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l3.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected experience",
            created_at_ms=5_000,
        )
        self.assertTrue(created)
        self.assertFalse(self.store.is_derivation_active(l3.derivation_id))
        # The fusion L5 still has the explicit L4 parent, so it survives.
        self.assertTrue(self.store.is_derivation_active(l5.derivation_id))
        second_correction = event(
            2, correction_event.event_hash, life_id=self.life, suffix="02" * 32
        )
        _b, _r2, _i2, created2 = self.coordinator.correct_claim(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l4.derivation_id,
            user_message_event_id=second_correction.event_id,
            plaintext=b"corrected explicit",
            created_at_ms=6_000,
        )
        self.assertTrue(created2)
        self.assertFalse(self.store.is_derivation_active(l5.derivation_id))
        l5_stale = self.store.list_memory_invalidations(
            derivation_id=l5.derivation_id
        )
        self.assertEqual(len(l5_stale), 1)
        self.assertEqual(l5_stale[0].reason, "stale")
        head = self.store.get_active_memory_head(
            life_id=self.life,
            principal_ref=value.principal_ref,
            claim_key="claim:core",
            layer="L3_EXPERIENCE",
        )
        self.assertIsNotNone(head)

    def test_correcting_already_inactive_target_raises(self) -> None:
        value, l3, _l4, _l5 = self._l3_l4_and_l5()
        correction_event = event(1, None, life_id=self.life, suffix="ff" * 32)
        self.coordinator.correct_claim(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l3.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected experience",
            created_at_ms=5_000,
        )
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.correct_claim(
                life_id=self.life,
                principal_ref=value.principal_ref,
                privacy_scope=value.privacy_scope,
                target_derivation_id=l3.derivation_id,
                user_message_event_id=correction_event.event_id,
                plaintext=b"again",
                created_at_ms=6_000,
            )

    def test_child_with_second_independent_parent_survives(self) -> None:
        first_event = event(1, None, life_id=self.life)
        _a1, l1a, _c = self.coordinator.commit_life_event_l1(first_event)
        second_event = event(
            2, first_event.event_hash, life_id=self.life, suffix="2" * 64
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        l2a = self.coordinator.promote_l1_to_l2(
            life_id=self.life,
            principal_ref=first_event.principal_ref,
            privacy_scope=first_event.privacy_scope,
            l1_derivation_ids=(l1a.derivation_id,),
            claim_key="claim:two-parents-a:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary a",
            created_at_ms=3_000,
        )
        l2b = self.coordinator.promote_l1_to_l2(
            life_id=self.life,
            principal_ref=first_event.principal_ref,
            privacy_scope=first_event.privacy_scope,
            l1_derivation_ids=(l1b.derivation_id,),
            claim_key="claim:two-parents-b:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary b",
            created_at_ms=3_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=self.life,
            principal_ref=first_event.principal_ref,
            privacy_scope=first_event.privacy_scope,
            l2_derivation_ids=(l2a[1].derivation_id, l2b[1].derivation_id),
            claim_key="claim:two-parents",
            semantic_domain="SYSTEM",
            plaintext=b"experience with two parents",
            created_at_ms=4_000,
            support_weights={
                l2a[1].derivation_id: 1000,
                l2b[1].derivation_id: 1000,
            },
            counter_weights={},
            causal_utility_milli={
                l2a[1].derivation_id: 800,
                l2b[1].derivation_id: 800,
            },
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        correction_event = event(1, None, life_id=self.life, suffix="ff" * 32)
        _assertion, _replacement, invalidations, created = (
            self.coordinator.correct_claim(
                life_id=self.life,
                principal_ref=first_event.principal_ref,
                privacy_scope=first_event.privacy_scope,
                target_derivation_id=l2a[1].derivation_id,
                user_message_event_id=correction_event.event_id,
                plaintext=b"corrected diary a",
                created_at_ms=5_000,
            )
        )
        self.assertTrue(created)
        self.assertFalse(
            self.store.is_derivation_active(l2a[1].derivation_id)
        )
        # The L3 keeps independent support from the surviving L2 parent.
        self.assertTrue(self.store.is_derivation_active(l3[1].derivation_id))
        self.assertEqual(
            len(
                self.store.list_memory_invalidations(
                    derivation_id=l3[1].derivation_id
                )
            ),
            0,
        )
        self.assertTrue(invalidations)

    def test_invalidation_records_are_append_only_and_idempotent(self) -> None:
        value, l3, l4, _l5 = self._l3_l4_and_l5()
        correction_event = event(1, None, life_id=self.life, suffix="ff" * 32)
        self.coordinator.correct_claim(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l3.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected experience",
            created_at_ms=5_000,
        )
        second_correction = event(
            2, correction_event.event_hash, life_id=self.life, suffix="02" * 32
        )
        self.coordinator.correct_claim(
            life_id=self.life,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l4.derivation_id,
            user_message_event_id=second_correction.event_id,
            plaintext=b"corrected explicit",
            created_at_ms=6_000,
        )
        records = self.store.list_memory_invalidations(life_id=self.life)
        before = len(records)
        self.assertGreaterEqual(before, 3)
        for record in records:
            self.assertTrue(record.has_valid_invalidation_sha256())
        # Replaying invalidation on an already-inactive derivation is a no-op.
        from life_service.memory_invalidation import invalidate_cascade

        replay = invalidate_cascade(
            self.store,
            derivation_id=l3.derivation_id,
            reason="corrected",
            invalidated_at_ms=5_000,
        )
        self.assertEqual(replay, ())
        self.assertEqual(
            len(self.store.list_memory_invalidations(life_id=self.life)),
            before,
        )


if __name__ == "__main__":
    unittest.main()
