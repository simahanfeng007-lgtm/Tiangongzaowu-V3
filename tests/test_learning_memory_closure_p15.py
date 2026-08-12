"""P15 M4: Learning Result closes into L1 audit + refined L3 experience."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import (
    MemoryCoordinator,
    MemoryCoordinatorError,
)
from life_service.life_learning_memory import derive_learning_result_ids
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_learning"


class LearningMemoryClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "learning.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _source_l3(self, *, sequence: int, suffix: str, claim_key: str):
        value = event(sequence, None, life_id=LIFE, suffix=suffix)
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
        self.assertIsNotNone(l2)
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
        self.assertIsNotNone(l3)
        return value, l3[0], l3[1]

    def _learning_event(self, ids: dict[str, str]):
        return event(
            1,
            None,
            life_id=LIFE,
            suffix=ids["event_id"].removeprefix("lev_"),
        )

    def test_learning_result_closes_into_l1_audit_and_refined_l3(self) -> None:
        value, _l3_assertion, l3 = self._source_l3(
            sequence=1, suffix="01" * 32, claim_key="claim:api"
        )
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_abc",
            result_sha256="11" * 32,
        )
        result_event = self._learning_event(ids)
        assertion, refined, audit, created = (
            self.coordinator.commit_learning_result(
                learning_event=result_event,
                learning_id="learning_abc",
                subject="api-docs",
                result_sha256="11" * 32,
                source_l3_derivation_ids=(l3.derivation_id,),
                refined_plaintext=b"refined api knowledge",
                created_at_ms=4_000,
            )
        )
        self.assertTrue(created)
        l1_assertion, l1_derivation = audit
        self.assertEqual(l1_derivation.layer, "L1_STREAM")
        self.assertEqual(l1_derivation.origin, "LIFE_EVENT")
        self.assertEqual(refined.layer, "L3_EXPERIENCE")
        self.assertEqual(refined.origin, "LEARNING_RESULT")
        self.assertEqual(refined.derivation_id, ids["refined_derivation_id"])
        self.assertEqual(refined.claim_key, "learned:learning_abc")
        self.assertEqual(assertion.memory_id, ids["refined_memory_id"])
        parents = self.store.list_derivation_parents(refined.derivation_id)
        self.assertEqual(len(parents), 2)
        self.assertEqual(
            set(item.parent_derivation_id for item in parents),
            {l3.derivation_id, l1_derivation.derivation_id},
        )
        head = self.store.get_active_memory_head(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            claim_key="learned:learning_abc",
            layer="L3_EXPERIENCE",
        )
        self.assertEqual(head.derivation_id, refined.derivation_id)

    def test_learning_result_is_idempotent(self) -> None:
        _value, _a, l3 = self._source_l3(
            sequence=1, suffix="02" * 32, claim_key="claim:api-2"
        )
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_def",
            result_sha256="22" * 32,
        )
        result_event = self._learning_event(ids)
        first = self.coordinator.commit_learning_result(
            learning_event=result_event,
            learning_id="learning_def",
            subject="api-docs-2",
            result_sha256="22" * 32,
            source_l3_derivation_ids=(l3.derivation_id,),
            refined_plaintext=b"refined",
            created_at_ms=4_000,
        )
        second = self.coordinator.commit_learning_result(
            learning_event=result_event,
            learning_id="learning_def",
            subject="api-docs-2",
            result_sha256="22" * 32,
            source_l3_derivation_ids=(l3.derivation_id,),
            refined_plaintext=b"refined",
            created_at_ms=4_000,
        )
        self.assertTrue(first[3])
        self.assertFalse(second[3])
        self.assertEqual(first[1].derivation_id, second[1].derivation_id)

    def test_learning_never_writes_l5_or_temperament(self) -> None:
        _value, _a, l3 = self._source_l3(
            sequence=1, suffix="03" * 32, claim_key="claim:api-3"
        )
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_ghi",
            result_sha256="33" * 32,
        )
        _assertion, refined, _audit, created = (
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(ids),
                learning_id="learning_ghi",
                subject="no-l5",
                result_sha256="33" * 32,
                source_l3_derivation_ids=(l3.derivation_id,),
                refined_plaintext=b"refined",
                created_at_ms=4_000,
            )
        )
        self.assertTrue(created)
        self.assertFalse(refined.temperament_eligible)
        self.assertFalse(refined.self_cognition_eligible)
        l5_rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations "
            "WHERE life_id = ? AND layer = 'L5_CORE'",
            (LIFE,),
        ).fetchone()
        self.assertEqual(int(l5_rows["n"]), 0)

    def test_learning_inherits_all_parent_evidence_roots(self) -> None:
        first_value, _a, l3a = self._source_l3(
            sequence=1, suffix="04" * 32, claim_key="claim:repo-a"
        )
        second_event = event(2, first_value.event_hash, life_id=LIFE, suffix="05" * 32)
        _a2, l1b, _c = self.coordinator.commit_life_event_l1(second_event)
        l2b = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=second_event.principal_ref,
            privacy_scope=second_event.privacy_scope,
            l1_derivation_ids=(l1b.derivation_id,),
            claim_key="claim:repo-b:diary",
            semantic_domain="WORLD",
            plaintext=b"diary b",
            created_at_ms=3_000,
        )
        l3b = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=second_event.principal_ref,
            privacy_scope=second_event.privacy_scope,
            l2_derivation_ids=(l2b[1].derivation_id,),
            claim_key="claim:repo-b",
            semantic_domain="WORLD",
            plaintext=b"experience b",
            created_at_ms=4_000,
            support_weights={l2b[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2b[1].derivation_id: 800},
            recurrence_count=2,
        )
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_jkl",
            result_sha256="44" * 32,
        )
        _assertion, refined, _audit, created = (
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(ids),
                learning_id="learning_jkl",
                subject="repo-lineage",
                result_sha256="44" * 32,
                source_l3_derivation_ids=(
                    l3a.derivation_id,
                    l3b[1].derivation_id,
                ),
                refined_plaintext=b"merged repository knowledge",
                created_at_ms=5_000,
            )
        )
        self.assertTrue(created)
        self.assertTrue(
            {
                l3a.lineage_root_event_ids[0],
                l3b[1].lineage_root_event_ids[0],
            }
            <= set(refined.lineage_root_event_ids)
        )
        self.assertIn(
            ids["event_id"], refined.lineage_root_event_ids
        )

    def test_learning_rejects_inactive_or_non_l3_source(self) -> None:
        value, _a, l3 = self._source_l3(
            sequence=1, suffix="06" * 32, claim_key="claim:bad-source"
        )
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_bad",
            result_sha256="55" * 32,
        )
        # L1 is not an L3 source.
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(ids),
                learning_id="learning_bad",
                subject="bad",
                result_sha256="55" * 32,
                source_l3_derivation_ids=(
                    self.store.list_memory_derivations(
                        life_id=LIFE, layer="L1_STREAM"
                    )[0].derivation_id,
                ),
                refined_plaintext=b"x",
                created_at_ms=4_000,
            )
        # Inactive L3 is rejected.
        correction_event = event(1, None, life_id=LIFE, suffix="07" * 32)
        self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l3.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected",
            created_at_ms=4_000,
        )
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(ids),
                learning_id="learning_bad",
                subject="bad",
                result_sha256="55" * 32,
                source_l3_derivation_ids=(l3.derivation_id,),
                refined_plaintext=b"x",
                created_at_ms=5_000,
            )

    def test_learning_requires_at_least_one_l3_ref(self) -> None:
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_empty",
            result_sha256="66" * 32,
        )
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(ids),
                learning_id="learning_empty",
                subject="empty",
                result_sha256="66" * 32,
                source_l3_derivation_ids=(),
                refined_plaintext=b"x",
                created_at_ms=4_000,
            )

    def test_learning_refined_head_replaces_previous_for_same_learning(self) -> None:
        _value, _a, l3 = self._source_l3(
            sequence=1, suffix="08" * 32, claim_key="claim:replace"
        )
        first_ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_replace",
            result_sha256="77" * 32,
        )
        second_ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_replace",
            result_sha256="88" * 32,
        )
        _a1, first_refined, _audit1, created1 = (
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(first_ids),
                learning_id="learning_replace",
                subject="replace",
                result_sha256="77" * 32,
                source_l3_derivation_ids=(l3.derivation_id,),
                refined_plaintext=b"first",
                created_at_ms=4_000,
            )
        )
        _a2, second_refined, _audit2, created2 = (
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(second_ids),
                learning_id="learning_replace",
                subject="replace",
                result_sha256="88" * 32,
                source_l3_derivation_ids=(l3.derivation_id,),
                refined_plaintext=b"second",
                created_at_ms=5_000,
            )
        )
        self.assertTrue(created1)
        self.assertTrue(created2)
        self.assertNotEqual(
            first_refined.derivation_id, second_refined.derivation_id
        )
        head = self.store.get_active_memory_head(
            life_id=LIFE,
            principal_ref=_value.principal_ref,
            claim_key="learned:learning_replace",
            layer="L3_EXPERIENCE",
        )
        self.assertEqual(head.derivation_id, second_refined.derivation_id)

    def test_learning_audit_l1_is_idempotent(self) -> None:
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_audit",
            result_sha256="99" * 32,
        )
        learning_event = self._learning_event(ids)
        _a, _d, created1 = self.coordinator.commit_life_event_l1(
            learning_event
        )
        _a2, _d2, created2 = self.coordinator.commit_life_event_l1(
            learning_event
        )
        self.assertTrue(created1)
        self.assertFalse(created2)

    def test_learning_l3_bound_is_enforced(self) -> None:
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_overflow",
            result_sha256="aa" * 32,
        )
        refs = tuple(
            f"mdr_{index:064x}" for index in range(17)
        )
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(ids),
                learning_id="learning_overflow",
                subject="overflow",
                result_sha256="aa" * 32,
                source_l3_derivation_ids=refs,
                refined_plaintext=b"x",
                created_at_ms=4_000,
            )

    def test_learning_refined_epistemic_stays_user_asserted(self) -> None:
        _value, _a, l3 = self._source_l3(
            sequence=1, suffix="09" * 32, claim_key="claim:epistemic"
        )
        ids = derive_learning_result_ids(
            life_id=LIFE,
            learning_id="learning_epistemic",
            result_sha256="bb" * 32,
        )
        assertion, _refined, _audit, created = (
            self.coordinator.commit_learning_result(
                learning_event=self._learning_event(ids),
                learning_id="learning_epistemic",
                subject="epistemic",
                result_sha256="bb" * 32,
                source_l3_derivation_ids=(l3.derivation_id,),
                refined_plaintext=b"refined",
                created_at_ms=4_000,
            )
        )
        self.assertTrue(created)
        self.assertEqual(assertion.epistemic_status, "user_asserted")


if __name__ == "__main__":
    unittest.main()
