"""P15 M4: bounded learning scope and repository lineage inheritance."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.activity_scope import build_activity_scope
from life_service.learning_executor import _source
from life_service.life_learning_memory import (
    MAX_LEARNING_L3_REFS,
    build_learning_scope,
)
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_repo_lineage"


class LearningRepositoryLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "reposcope.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_learning_scope_is_bounded_per_evidence_class(self) -> None:
        l3_refs = tuple(
            {"derivation_id": f"mdr_{index:064x}"}
            for index in range(MAX_LEARNING_L3_REFS + 8)
        )
        repository = tuple(
            {"frame_id": f"frame_{index}"} for index in range(16)
        )
        world = tuple(
            {"candidate_id": f"candidate_{index}"} for index in range(8)
        )
        scope = build_learning_scope(
            active_l3_refs=l3_refs,
            repository_evidence=repository,
            world_evidence=world,
        )
        self.assertEqual(
            len(scope["active_l3_refs"]), MAX_LEARNING_L3_REFS
        )
        self.assertEqual(len(scope["repository_evidence"]), 8)
        self.assertEqual(len(scope["world_evidence"]), 4)
        self.assertEqual(len(str(scope["source_sha256"])), 64)

    def test_learning_scope_hash_is_deterministic(self) -> None:
        refs = ({"derivation_id": "mdr_" + "a" * 64},)
        first = build_learning_scope(active_l3_refs=refs)
        second = build_learning_scope(active_l3_refs=refs)
        self.assertEqual(first["source_sha256"], second["source_sha256"])

    def test_activity_scope_exposes_active_l3_refs_only(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:scope:diary",
            semantic_domain="WORLD",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2[1].derivation_id,),
            claim_key="claim:scope",
            semantic_domain="WORLD",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2[1].derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2[1].derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        scope = build_activity_scope(
            life_id=LIFE,
            soul={"prompt": "test"},
            scope={
                "memories": {
                    "mem_1": {
                        "memory_id": "mem_1",
                        "content": "old raw memory",
                        "status": "active",
                    }
                }
            },
            derivation_store=self.store,
        )
        self.assertEqual(len(scope["active_l3_refs"]), 1)
        self.assertEqual(
            scope["active_l3_refs"][0]["derivation_id"],
            l3[1].derivation_id,
        )

    def test_learning_source_prefers_active_l3_refs(self) -> None:
        scope = {
            "recent_memories": [
                {
                    "memory_id": "mem_raw_l2",
                    "content": "raw L2 content",
                }
            ],
            "active_l3_refs": [
                {
                    "derivation_id": "mdr_" + "1" * 64,
                    "memory_id": "mem_l3_a",
                    "content": "active L3 content",
                }
            ],
            "repository_evidence": [],
        }
        source = _source({"title": "topic"}, scope)
        self.assertEqual(source["memory_refs"], ["mem_l3_a"])
        self.assertEqual(
            source["derivation_refs"], ["mdr_" + "1" * 64]
        )
        self.assertNotIn("mem_raw_l2", source["memory_refs"])

    def test_legacy_scope_without_l3_refs_still_works(self) -> None:
        scope = {
            "recent_memories": [
                {"memory_id": "mem_legacy", "content": "legacy content"}
            ],
            "repository_evidence": [],
        }
        source = _source({"title": "topic"}, scope)
        self.assertEqual(source["memory_refs"], ["mem_legacy"])

    def test_activity_scope_without_store_has_empty_l3_refs(self) -> None:
        scope = build_activity_scope(
            life_id=LIFE,
            soul={"prompt": "test"},
            scope={"memories": {}},
        )
        self.assertEqual(scope["active_l3_refs"], [])

    def test_learning_source_truncates_l3_refs_to_twelve(self) -> None:
        scope = {
            "active_l3_refs": [
                {
                    "derivation_id": f"mdr_{index:064x}",
                    "memory_id": f"mem_{index:064x}",
                }
                for index in range(20)
            ],
            "repository_evidence": [],
        }
        source = _source({"title": "topic"}, scope)
        self.assertEqual(len(source["memory_refs"]), 12)


if __name__ == "__main__":
    unittest.main()
