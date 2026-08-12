"""P15 M5: layer-aware context priority and selection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_context import (
    LAYER_BONUS_MILLI,
    context_priority_milli,
    layer_bonus_milli,
    render_context_sections,
    select_layered_memories,
)
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_ctx_layers"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class MemoryContextLayersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "ctx.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l1(self, *, sequence: int = 1, suffix: str | None = None):
        value = event(sequence, None, life_id=LIFE, suffix=suffix)
        _a, derivation, _created = self.coordinator.commit_life_event_l1(
            value, event_payload=b"stream event"
        )
        return value, derivation

    def test_layer_bonus_values_match_plan(self) -> None:
        self.assertEqual(LAYER_BONUS_MILLI["L5_CORE"], 2500)
        self.assertEqual(LAYER_BONUS_MILLI["L4_EXPLICIT"], 2200)
        self.assertEqual(LAYER_BONUS_MILLI["L3_EXPERIENCE"], 1400)
        self.assertEqual(LAYER_BONUS_MILLI["L2_DIARY"], 700)
        self.assertEqual(LAYER_BONUS_MILLI["L1_STREAM"], 300)

    def test_invalid_layer_raises(self) -> None:
        with self.assertRaises(ValueError):
            layer_bonus_milli("L6_FAKE")

    def test_priority_ranks_l5_above_l4_above_l3(self) -> None:
        _v, l1 = self._l1(suffix="01" * 32)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:rank:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:rank",
            semantic_domain="SYSTEM",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        priorities = []
        for derivation in (l1, l2[1], l3[1]):
            assertion = self.store.get_memory_assertion(
                derivation.memory_id, derivation.memory_revision
            )
            priorities.append(
                context_priority_milli(
                    derivation, assertion, now_ms=10_000
                )
            )
        self.assertGreater(priorities[2], priorities[1])
        self.assertGreater(priorities[1], priorities[0])

    def test_expired_derivation_gets_large_penalty(self) -> None:
        _v, l1 = self._l1(suffix="02" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_v.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="今天先叫我A。",
            plaintext=b"call me A today",
            created_at_ms=2_000,
            claim_key="claim:alias",
            semantic_domain="USER_PREFERENCE",
        )
        assertion = self.store.get_memory_assertion(l4.memory_id, 1)
        expired = context_priority_milli(
            l4, assertion, now_ms=200_000_000
        )
        fresh = context_priority_milli(
            l4, assertion, now_ms=2_000
        )
        self.assertLess(expired, fresh)

    def test_l4_is_selected_immediately_and_l5_participates(self) -> None:
        _v, l1 = self._l1(suffix="03" * 32)
        self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_v.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            created_at_ms=2_000,
            claim_key="claim:lang",
            semantic_domain="USER_PREFERENCE",
        )
        instruction, data, evidence, skipped = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertGreaterEqual(len(instruction), 1)
        self.assertTrue(
            any(item.layer == "L4_EXPLICIT" for item in instruction)
        )
        self.assertEqual(skipped, 0)

    def test_expired_l4_is_excluded(self) -> None:
        _v, l1 = self._l1(suffix="04" * 32)
        _a, l4, _det, _c = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_v.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="今天先叫我B。",
            plaintext=b"call me B today",
            created_at_ms=2_000,
            claim_key="claim:alias-b",
            semantic_domain="USER_PREFERENCE",
        )
        _instruction, data, _evidence, skipped = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=200_000_000,
        )
        self.assertFalse(
            any(item.derivation_id == l4.derivation_id for item in data)
        )
        self.assertGreaterEqual(skipped, 1)

    def test_l1_stream_is_available_when_no_higher_layer(self) -> None:
        self._l1(suffix="05" * 32)
        _instruction, data, _evidence, _skipped = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertTrue(any(item.layer == "L1_STREAM" for item in data))

    def test_selection_limit_is_enforced(self) -> None:
        for index in range(1, 6):
            self._l1(sequence=1, suffix=f"{index:02d}" * 32)
        _i, data, _e, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
            limit=2,
        )
        self.assertLessEqual(len(data), 2)

    def test_item_sha256_is_deterministic(self) -> None:
        _v, l1 = self._l1(suffix="06" * 32)
        first = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        second = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertEqual(first, second)
        for item in first[0] + first[1] + first[2]:
            self.assertEqual(len(item.item_sha256), 64)

    def test_render_sections_are_bounded(self) -> None:
        _v, l1 = self._l1(suffix="07" * 32)
        self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=_v.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            created_at_ms=2_000,
            claim_key="claim:lang-render",
            semantic_domain="USER_PREFERENCE",
        )
        instruction, data, evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        rendered = render_context_sections(
            instruction_items=instruction,
            data_items=data,
            evidence_items=evidence,
            max_chars=1_000,
        )
        self.assertIn("instruction", rendered)
        self.assertIn("data", rendered)
        self.assertIn("evidence", rendered)
        self.assertLessEqual(len(rendered["instruction"]), 1_000)

    def test_priority_includes_user_importance_and_verification(self) -> None:
        _v, l1 = self._l1(suffix="08" * 32)
        assertion = self.store.get_memory_assertion(l1.memory_id, 1)
        low = context_priority_milli(
            l1,
            assertion.model_copy(
                update={
                    "user_importance_milli": 0,
                    "verification_strength_milli": 0,
                }
            ),
            now_ms=3_000,
        )
        high = context_priority_milli(
            l1,
            assertion.model_copy(
            update={
                "user_importance_milli": 500,
                "verification_strength_milli": 500,
            }
            ),
            now_ms=3_000,
        )
        self.assertGreater(high, low)

    def test_select_returns_empty_for_unknown_life(self) -> None:
        self._l1(suffix="09" * 32)
        _i, data, _e, _s = select_layered_memories(
            self.store,
            life_id="life_unknown",
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)


if __name__ == "__main__":
    unittest.main()
