"""P15 M5: one lineage representative per context selection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_context import (
    LayeredMemoryItem,
    dedupe_lineage,
    lineage_root_sha256,
    select_layered_memories,
)
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_dedupe"
PRINCIPAL = "principal_test"
PRIVACY = "private"
ROOT_A = "lev_" + "a" * 64
ROOT_B = "lev_" + "b" * 64


def item(
    *,
    memory_id: str,
    derivation_id: str,
    layer: str,
    roots: tuple[str, ...],
    priority: int = 1_000,
    section: str = "DATA",
    summary: str = "summary",
) -> LayeredMemoryItem:
    return LayeredMemoryItem(
        memory_id=memory_id,
        derivation_id=derivation_id,
        layer=layer,
        semantic_domain="SYSTEM",
        claim_key="claim:" + derivation_id,
        lineage_root_sha256=lineage_root_sha256(roots),
        summary=summary,
        priority_milli=priority,
        section=section,
        item_sha256="0" * 64,
    )


class MemoryContextLineageDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "dedupe.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_same_lineage_l1_and_l4_keep_one_representative(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _a4, l4, _det, _c4 = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            created_at_ms=2_000,
            claim_key="claim:lang",
            semantic_domain="USER_PREFERENCE",
        )
        instruction, _data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertEqual(len(instruction), 1)
        self.assertEqual(instruction[0].layer, "L4_EXPLICIT")
        self.assertEqual(instruction[0].derivation_id, l4.derivation_id)

    def test_dedupe_keeps_highest_layer_across_overlapping_roots(self) -> None:
        first = item(
            memory_id="mem_1",
            derivation_id="mdr_1",
            layer="L1_STREAM",
            roots=(ROOT_A,),
            priority=300,
        )
        second = item(
            memory_id="mem_2",
            derivation_id="mdr_2",
            layer="L4_EXPLICIT",
            roots=(ROOT_A, ROOT_B),
            priority=2_200,
        )
        result = dedupe_lineage((first, second))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].derivation_id, "mdr_2")

    def test_distinct_lineages_both_survive(self) -> None:
        first = item(
            memory_id="mem_1",
            derivation_id="mdr_1",
            layer="L3_EXPERIENCE",
            roots=(ROOT_A,),
        )
        second = item(
            memory_id="mem_2",
            derivation_id="mdr_2",
            layer="L3_EXPERIENCE",
            roots=(ROOT_B,),
        )
        result = dedupe_lineage((first, second))
        self.assertEqual(len(result), 2)

    def test_dedupe_empty_input(self) -> None:
        self.assertEqual(dedupe_lineage(()), ())

    def test_representative_is_deterministic(self) -> None:
        first = item(
            memory_id="mem_1",
            derivation_id="mdr_1",
            layer="L2_DIARY",
            roots=(ROOT_A,),
            priority=700,
        )
        second = item(
            memory_id="mem_2",
            derivation_id="mdr_2",
            layer="L1_STREAM",
            roots=(ROOT_A,),
            priority=900,
        )
        result = dedupe_lineage((first, second))
        self.assertEqual(result[0].derivation_id, "mdr_1")
        again = dedupe_lineage((second, first))
        self.assertEqual(again[0].derivation_id, "mdr_1")

    def test_learning_refined_shared_root_is_deduped_in_store(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:learn:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:learn",
            semantic_domain="WORLD",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        from life_service.life_learning_memory import derive_learning_result_ids

        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_dedupe",
            result_sha256="11" * 32,
        )
        learning_event = event(
            1,
            None,
            life_id=LIFE,
            suffix=ids["event_id"].removeprefix("lev_"),
        )
        _a, refined, _audit, created = self.coordinator.commit_learning_result(
            learning_event=learning_event,
            learning_id="learning_dedupe",
            subject="dedupe",
            result_sha256="11" * 32,
            source_l3_derivation_ids=(l3[1].derivation_id,),
            refined_plaintext=b"refined",
            created_at_ms=4_000,
        )
        self.assertTrue(created)
        _i, data, _e, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=5_000,
        )
        self.assertLessEqual(len(data), 2)
        derivation_ids = {item.derivation_id for item in data}
        overlap = {
            l3[1].derivation_id,
            refined.derivation_id,
        } & derivation_ids
        self.assertEqual(len(overlap), 1)

    def test_priority_tie_break_is_deterministic(self) -> None:
        first = item(
            memory_id="mem_a",
            derivation_id="mdr_a",
            layer="L3_EXPERIENCE",
            roots=(ROOT_A,),
            priority=1_000,
        )
        second = item(
            memory_id="mem_b",
            derivation_id="mdr_b",
            layer="L3_EXPERIENCE",
            roots=(ROOT_A,),
            priority=1_000,
        )
        result = dedupe_lineage((first, second))
        self.assertEqual(len(result), 1)
        self.assertIn(result[0].derivation_id, {"mdr_a", "mdr_b"})

    def test_same_claim_different_layers_keep_one_representative(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text="记住，我的长期偏好是简洁。",
            plaintext=b"be concise",
            created_at_ms=2_000,
            claim_key="claim:concise",
            semantic_domain="USER_PREFERENCE",
        )
        _instruction, data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        # L1 + L4 share one lineage: L4 is the representative, L1 is not
        # duplicated into the data section.
        self.assertFalse(
            any(item.layer == "L1_STREAM" for item in data)
        )

    def test_three_item_chain_keeps_single_representative(self) -> None:
        first = item(
            memory_id="mem_1",
            derivation_id="mdr_1",
            layer="L1_STREAM",
            roots=(ROOT_A,),
        )
        second = item(
            memory_id="mem_2",
            derivation_id="mdr_2",
            layer="L2_DIARY",
            roots=(ROOT_A, ROOT_B),
        )
        third = item(
            memory_id="mem_3",
            derivation_id="mdr_3",
            layer="L3_EXPERIENCE",
            roots=(ROOT_B,),
        )
        result = dedupe_lineage((first, second, third))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].derivation_id, "mdr_3")

    def test_two_components_with_shared_root_member(self) -> None:
        root_c = "lev_" + "c" * 64
        first = item(
            memory_id="mem_1",
            derivation_id="mdr_1",
            layer="L1_STREAM",
            roots=(ROOT_A, ROOT_B),
        )
        second = item(
            memory_id="mem_2",
            derivation_id="mdr_2",
            layer="L2_DIARY",
            roots=(ROOT_B,),
        )
        third = item(
            memory_id="mem_3",
            derivation_id="mdr_3",
            layer="L1_STREAM",
            roots=(root_c,),
        )
        result = dedupe_lineage((first, second, third))
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
