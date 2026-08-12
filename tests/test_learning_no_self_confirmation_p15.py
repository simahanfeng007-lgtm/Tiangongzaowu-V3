"""P15 M4: Learning never confirms itself as independent evidence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service import memory_promotion
from life_service.life_learning_memory import derive_learning_result_ids
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_no_self"


class LearningNoSelfConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "noself.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _source_l3(self, *, suffix: str, claim_key: str):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key=claim_key + ":diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key=claim_key,
            semantic_domain="WORLD",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        return value, l3[1]

    def _commit_learning(self, value, l3, *, learning_id: str, result: str):
        ids = derive_learning_result_ids(
            life_id=LIFE, learning_id=learning_id, result_sha256=result
        )
        learning_event = event(
            1,
            None,
            life_id=LIFE,
            suffix=ids["event_id"].removeprefix("lev_"),
        )
        return self.coordinator.commit_learning_result(
            learning_event=learning_event,
            learning_id=learning_id,
            subject="no-self",
            result_sha256=result,
            source_l3_derivation_ids=(l3.derivation_id,),
            refined_plaintext=b"refined",
            created_at_ms=4_000,
        )

    def test_refined_learning_folds_into_parent_independence_group(self) -> None:
        _value, l3 = self._source_l3(suffix="11" * 32, claim_key="claim:noself")
        _a, refined, _audit, created = self._commit_learning(
            _value, l3, learning_id="learning_x", result="11" * 32
        )
        self.assertTrue(created)
        groups = memory_promotion.fold_independence(
            (l3, refined),
            {
                l3.derivation_id: 1000,
                refined.derivation_id: 750,
            },
        )
        self.assertEqual(len(groups), 1)

    def test_promotion_after_learning_keeps_same_group_count(self) -> None:
        _value, l3 = self._source_l3(suffix="12" * 32, claim_key="claim:noself-2")
        _a, refined, _audit, _created = self._commit_learning(
            _value, l3, learning_id="learning_y", result="22" * 32
        )
        l5 = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=_value.principal_ref,
            privacy_scope=_value.privacy_scope,
            candidate_derivation_ids=(l3.derivation_id, refined.derivation_id),
            claim_key="claim:noself-2",
            semantic_domain="WORLD",
            plaintext=b"core?",
            created_at_ms=5_000,
            support_weights={
                l3.derivation_id: 1000,
                refined.derivation_id: 750,
            },
            counter_weights={},
            recurrence_count=2,
        )
        # The refined record shares the parent root, so it cannot add a
        # second independence group and L5 stability is not reached.
        self.assertIsNone(l5)

    def test_repeated_learning_of_same_event_never_inflates_groups(self) -> None:
        _value, l3 = self._source_l3(suffix="13" * 32, claim_key="claim:noself-3")
        results = []
        for index in range(3):
            _a, refined, _audit, created = self._commit_learning(
                _value,
                l3,
                learning_id=f"learning_repeat_{index}",
                result=f"{index + 1:064x}",
            )
            self.assertTrue(created)
            results.append(refined)
        groups = memory_promotion.fold_independence(
            (l3, *results),
            {
                item.derivation_id: 750
                for item in (l3, *results)
            },
        )
        self.assertEqual(len(groups), 1)

    def test_learning_result_never_grants_new_evidence_weight(self) -> None:
        _value, l3 = self._source_l3(suffix="14" * 32, claim_key="claim:noself-4")
        _a, refined, _audit, _created = self._commit_learning(
            _value, l3, learning_id="learning_z", result="44" * 32
        )
        # evaluate_l3 sees the same one group whether or not the refined
        # record is included.
        base = memory_promotion.evaluate_l3(
            l2_derivations=(l3,),
            support_weights={l3.derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l3.derivation_id: 800},
            recurrence_count=2,
            life_id=LIFE,
            principal_ref=_value.principal_ref,
            claim_key="claim:noself-4",
            semantic_domain="WORLD",
            policy_version="p15-l3-v1",
            valid_from_ms=4_000,
            created_at_ms=4_000,
        )
        with_refined = memory_promotion.evaluate_l3(
            l2_derivations=(l3, refined),
            support_weights={
                l3.derivation_id: 1000,
                refined.derivation_id: 750,
            },
            counter_weights={},
            causal_utility_milli={
                l3.derivation_id: 800,
                refined.derivation_id: 0,
            },
            recurrence_count=2,
            life_id=LIFE,
            principal_ref=_value.principal_ref,
            claim_key="claim:noself-4",
            semantic_domain="WORLD",
            policy_version="p15-l3-v1",
            valid_from_ms=4_000,
            created_at_ms=4_000,
        )
        self.assertEqual(
            base.independence_group_count,
            with_refined.independence_group_count,
        )
        self.assertEqual(base.support_milli, with_refined.support_milli)

    def test_three_overlapping_refined_records_stay_one_group(self) -> None:
        _value, l3 = self._source_l3(suffix="15" * 32, claim_key="claim:noself-5")
        refined = []
        for index in range(3):
            _a, item, _audit, created = self._commit_learning(
                _value,
                l3,
                learning_id=f"learning_overlap_{index}",
                result=f"{50 + index:064x}",
            )
            self.assertTrue(created)
            refined.append(item)
        groups = memory_promotion.fold_independence(
            (l3, *refined),
            {item.derivation_id: 750 for item in (l3, *refined)},
        )
        self.assertEqual(len(groups), 1)

    def test_l5_with_refined_only_is_denied(self) -> None:
        _value, l3 = self._source_l3(suffix="16" * 32, claim_key="claim:noself-6")
        _a, refined, _audit, _created = self._commit_learning(
            _value, l3, learning_id="learning_only", result="60" * 32
        )
        promoted = self.coordinator.promote_to_l5(
            life_id=LIFE,
            principal_ref=_value.principal_ref,
            privacy_scope=_value.privacy_scope,
            candidate_derivation_ids=(refined.derivation_id,),
            claim_key="claim:noself-6",
            semantic_domain="WORLD",
            plaintext=b"core?",
            created_at_ms=5_000,
            support_weights={refined.derivation_id: 1000},
            counter_weights={},
            recurrence_count=2,
        )
        self.assertIsNone(promoted)

    def test_refined_promotion_key_differs_but_group_is_shared(self) -> None:
        _value, l3 = self._source_l3(suffix="17" * 32, claim_key="claim:noself-7")
        _a, refined, _audit, _created = self._commit_learning(
            _value, l3, learning_id="learning_key", result="61" * 32
        )
        source_key = self.store.get_derivation_promotion_key(l3.derivation_id)
        refined_key = self.store.get_derivation_promotion_key(
            refined.derivation_id
        )
        self.assertIsNotNone(source_key)
        self.assertIsNotNone(refined_key)
        self.assertNotEqual(source_key, refined_key)
        groups = memory_promotion.fold_independence(
            (l3, refined),
            {
                l3.derivation_id: 1000,
                refined.derivation_id: 750,
            },
        )
        self.assertEqual(len(groups), 1)


if __name__ == "__main__":
    unittest.main()
