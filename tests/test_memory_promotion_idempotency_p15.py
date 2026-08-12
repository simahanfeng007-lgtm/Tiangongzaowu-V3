"""P15 M3: promotion materialization is promotion-key idempotent."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import (
    MemoryCoordinator,
    promotion_derivation_id,
)
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


class PromotionIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "idem.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l2(self):
        value = event(1, None)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:idem:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        return value, l1, l2[1]

    def _promote_l3(self, value, l2, *, created_at_ms: int = 3_000):
        return self.coordinator.promote_l2_to_l3(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2.derivation_id,),
            claim_key="claim:idem",
            semantic_domain="SYSTEM",
            plaintext=b"experience",
            created_at_ms=created_at_ms,
            support_weights={l2.derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2.derivation_id: 800},
            recurrence_count=2,
        )

    def test_same_promotion_runs_once(self) -> None:
        value, _l1, l2 = self._l2()
        first = self._promote_l3(value, l2)
        second = self._promote_l3(value, l2)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertTrue(first[2])
        self.assertFalse(second[2])
        self.assertEqual(first[1].derivation_id, second[1].derivation_id)
        derivations = self.store.list_memory_derivations(
            life_id=value.life_id, layer="L3_EXPERIENCE"
        )
        self.assertEqual(len(derivations), 1)

    def test_promotion_derivation_id_matches_key_formula(self) -> None:
        value, _l1, l2 = self._l2()
        first = self._promote_l3(value, l2)
        expected = promotion_derivation_id(
            promotion_key=self.store.get_derivation_promotion_key(
                first[1].derivation_id
            ),
            target_layer="L3_EXPERIENCE",
            policy_version="p15-l3-v1",
        )
        self.assertEqual(first[1].derivation_id, expected)

    def test_parent_order_does_not_change_promotion_id(self) -> None:
        first_event = event(1, None)
        _a1, l1a, _c = self.coordinator.commit_life_event_l1(first_event)
        second_event = event(2, first_event.event_hash, suffix="2" * 64)
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        l2a = self.coordinator.promote_l1_to_l2(
            life_id=first_event.life_id,
            principal_ref=first_event.principal_ref,
            privacy_scope=first_event.privacy_scope,
            l1_derivation_ids=(l1a.derivation_id,),
            claim_key="claim:order-a:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary a",
            created_at_ms=3_000,
        )
        l2b = self.coordinator.promote_l1_to_l2(
            life_id=first_event.life_id,
            principal_ref=first_event.principal_ref,
            privacy_scope=first_event.privacy_scope,
            l1_derivation_ids=(l1b.derivation_id,),
            claim_key="claim:order-b:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary b",
            created_at_ms=4_000,
        )
        kwargs = dict(
            life_id=first_event.life_id,
            principal_ref=first_event.principal_ref,
            privacy_scope=first_event.privacy_scope,
            claim_key="claim:order",
            semantic_domain="SYSTEM",
            plaintext=b"experience",
            created_at_ms=5_000,
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
        forward = self.coordinator.promote_l2_to_l3(
            l2_derivation_ids=(l2a[1].derivation_id, l2b[1].derivation_id),
            **kwargs,
        )
        reversed_order = self.coordinator.promote_l2_to_l3(
            l2_derivation_ids=(l2b[1].derivation_id, l2a[1].derivation_id),
            **kwargs,
        )
        self.assertIsNotNone(forward)
        self.assertIsNotNone(reversed_order)
        self.assertEqual(
            forward[1].derivation_id, reversed_order[1].derivation_id
        )
        self.assertTrue(forward[2])
        self.assertFalse(reversed_order[2])

    def test_corrected_parent_reuses_existing_child_idempotently(self) -> None:
        value, _l1, l2 = self._l2()
        first = self._promote_l3(value, l2)
        correction_event = event(1, None, suffix="ff" * 32)
        self.coordinator.correct_claim(
            life_id=value.life_id,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=first[1].derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected experience",
            created_at_ms=4_000,
        )
        replay = self._promote_l3(value, l2, created_at_ms=3_000)
        self.assertIsNotNone(replay)
        self.assertEqual(replay[1].derivation_id, first[1].derivation_id)
        self.assertFalse(replay[2])


if __name__ == "__main__":
    unittest.main()
