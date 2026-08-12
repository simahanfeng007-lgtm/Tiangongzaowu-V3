"""P15 final compliance fixes: I06 edges, I17 privacy cascade, I19 secret,
plan section 12 consolidation and section 15 incremental promotion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.activity_scope import build_activity_scope
from life_service.life_learning_memory import derive_learning_result_ids
from life_service.memory_compaction import maybe_consolidate
from life_service.memory_coordinator import (
    MemoryCoordinator,
    MemoryCoordinatorError,
)
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_compliance"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class P15PlanComplianceFixesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = LifeShadowStore.open(
            self.root / "compliance.shadow.sqlite3",
            create=True,
            now_ms=500,
        )
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l1(self, *, suffix: str, privacy: str = PRIVACY):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        if value.privacy_scope != privacy:
            value = value.model_copy(
                update={"privacy_scope": privacy}
            ).with_computed_event_hash()
        _a, derivation, _c = self.coordinator.commit_life_event_l1(
            value, event_payload=b"event"
        )
        return value, derivation

    def _l2(self, *, suffix: str, claim_key: str, l1=None, privacy: str = PRIVACY):
        if l1 is None:
            _value, l1 = self._l1(suffix=suffix, privacy=privacy)
        return self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=privacy,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key=claim_key + ":diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )

    def test_i06_skip_promotions_are_rejected(self) -> None:
        _v, l1 = self._l1(suffix="01" * 32)
        l2 = self._l2(suffix="02" * 32, claim_key="claim:edge")
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.promote_l1_to_l2(
                life_id=LIFE,
                principal_ref=PRINCIPAL,
                privacy_scope=PRIVACY,
                l1_derivation_ids=(l2[1].derivation_id,),
                claim_key="claim:bad",
                semantic_domain="WORLD",
                plaintext=b"x",
                created_at_ms=3_000,
            )
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.promote_l2_to_l3(
                life_id=LIFE,
                principal_ref=PRINCIPAL,
                privacy_scope=PRIVACY,
                l2_derivation_ids=(l1.derivation_id,),
                claim_key="claim:bad",
                semantic_domain="WORLD",
                plaintext=b"x",
                created_at_ms=3_000,
                support_weights={l1.derivation_id: 1000},
                counter_weights={},
                causal_utility_milli={l1.derivation_id: 800},
                recurrence_count=2,
            )
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.promote_to_l5(
                life_id=LIFE,
                principal_ref=PRINCIPAL,
                privacy_scope=PRIVACY,
                candidate_derivation_ids=(l1.derivation_id,),
                claim_key="claim:bad",
                semantic_domain="WORLD",
                plaintext=b"x",
                created_at_ms=3_000,
                support_weights={l1.derivation_id: 1000},
                counter_weights={},
                recurrence_count=3,
            )

    def test_i17_privacy_delete_cascades_to_descendants(self) -> None:
        _v, l1 = self._l1(suffix="11" * 32)
        l2 = self._l2(suffix="12" * 32, claim_key="claim:privacy", l1=l1)
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:privacy",
            semantic_domain="WORLD",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        l4_event = event(2, _v.event_hash, life_id=LIFE, suffix="13" * 32)
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
            claim_key="claim:privacy",
            semantic_domain="WORLD",
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            candidate_derivation_ids=(
                l3[1].derivation_id,
                l4.derivation_id,
            ),
            claim_key="claim:privacy",
            semantic_domain="WORLD",
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
        invalidations = self.coordinator.delete_memory_with_privacy_cascade(
            life_id=LIFE,
            memory_id=l3[1].memory_id,
            deleted_at_ms=6_000,
        )
        self.assertGreaterEqual(invalidations, 2)
        self.assertFalse(
            self.store.is_derivation_active(l3[1].derivation_id)
        )
        self.assertFalse(
            self.store.is_derivation_active(l5[1].derivation_id)
        )
        l5_stale = self.store.list_memory_invalidations(
            derivation_id=l5[1].derivation_id
        )
        self.assertEqual(l5_stale[0].reason, "stale")
        # Calling again after tombstone is idempotent.
        again = self.coordinator.delete_memory_with_privacy_cascade(
            life_id=LIFE,
            memory_id=l3[1].memory_id,
            deleted_at_ms=6_100,
        )
        self.assertEqual(again, 0)

    def test_i19_secret_l3_excluded_from_learning_scope(self) -> None:
        _v, l1 = self._l1(suffix="21" * 32, privacy="secret")
        l2 = self._l2(
            suffix="22" * 32,
            claim_key="claim:secret",
            l1=l1,
            privacy="secret",
        )
        self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope="secret",
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:secret",
            semantic_domain="WORLD",
            plaintext=b"secret experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        scope = build_activity_scope(
            life_id=LIFE,
            soul={"prompt": "test"},
            scope={"memories": {}},
            derivation_store=self.store,
        )
        self.assertEqual(scope["active_l3_refs"], [])

    def test_section12_consolidation_folds_duplicate_l2(self) -> None:
        first_event = event(1, None, life_id=LIFE, suffix="31" * 32)
        _a1, l1a, _c = self.coordinator.commit_life_event_l1(first_event)
        l2a = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1a.derivation_id,),
            claim_key="claim:dup:diary",
            semantic_domain="WORLD",
            plaintext=b"diary a",
            created_at_ms=2_000,
        )
        second_event = event(
            2, first_event.event_hash, life_id=LIFE, suffix="32" * 32
        )
        _a2, l1b, _c2 = self.coordinator.commit_life_event_l1(second_event)
        l2b = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1a.derivation_id, l1b.derivation_id),
            claim_key="claim:dup:diary",
            semantic_domain="WORLD",
            plaintext=b"diary b",
            created_at_ms=3_000,
        )
        self.assertIsNotNone(l2a)
        self.assertIsNotNone(l2b)
        result = maybe_consolidate(
            self.store, life_id=LIFE, now_ms=4_000, threshold=3
        )
        self.assertTrue(result["triggered"])
        consolidated = result["consolidated"]
        self.assertGreaterEqual(consolidated["duplicate_l2_invalidated"], 1)
        self.assertFalse(
            self.store.is_derivation_active(l2a[1].derivation_id)
        )
        self.assertTrue(
            self.store.is_derivation_active(l2b[1].derivation_id)
        )
        self.assertEqual(consolidated["l4_l5_touched"], 0)
        self.assertEqual(consolidated["life_event_ledger_touched"], 0)
        # Watermark advanced; the next call does not re-trigger.
        second = maybe_consolidate(
            self.store, life_id=LIFE, now_ms=4_100, threshold=3
        )
        self.assertFalse(second["triggered"])

    def test_section12_consolidation_never_touches_l4_l5(self) -> None:
        _v, l1 = self._l1(suffix="33" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_v.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"chinese",
            created_at_ms=2_000,
            claim_key="claim:l4-keep",
            semantic_domain="USER_PREFERENCE",
        )
        maybe_consolidate(
            self.store, life_id=LIFE, now_ms=3_000, threshold=3
        )
        self.assertTrue(
            self.store.is_derivation_active(l4.derivation_id)
        )

    def test_section15_promotion_cycle_consumes_watermark(self) -> None:
        _v, l1 = self._l1(suffix="41" * 32)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:cycle:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
            causal_utility_milli=900,
        )
        self.assertIsNotNone(l2)
        first = self.coordinator.run_promotion_cycle(
            life_id=LIFE, now_ms=4_000
        )
        self.assertGreaterEqual(first["consumed"], 1)
        self.assertTrue(
            any(kind == "L3" for kind, _id in first["promotions"])
        )
        self.assertGreater(first["last_watermark"], 0)
        second = self.coordinator.run_promotion_cycle(
            life_id=LIFE, now_ms=4_100
        )
        self.assertEqual(second["promotions"], ())

    def test_section15_cycle_promotes_l5_fusion(self) -> None:
        _v, l1 = self._l1(suffix="51" * 32)
        l2 = self._l2(suffix="52" * 32, claim_key="claim:l5cyc", l1=l1)
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:l5cyc",
            semantic_domain="WORLD",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        l4_event = event(2, _v.event_hash, life_id=LIFE, suffix="53" * 32)
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
            claim_key="claim:l5cyc",
            semantic_domain="WORLD",
        )
        result = self.coordinator.run_promotion_cycle(
            life_id=LIFE, now_ms=5_000
        )
        self.assertTrue(
            any(kind == "L5" for kind, _id in result["promotions"])
        )


if __name__ == "__main__":
    unittest.main()
