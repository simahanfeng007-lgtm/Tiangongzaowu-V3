from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_migration import (
    LegacyMemoryRecord,
    LegacyMemoryRelation,
    migrate_legacy_memory_records,
)
from life_service.store import SHADOW_STORE_SCHEMA_VERSION, LifeShadowStore


class LegacyMemoryMigrationTests(unittest.TestCase):
    def test_legacy_content_becomes_protected_nodes_without_invented_causality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "migration.shadow.sqlite3"
            with LifeShadowStore.open(path, create=True, now_ms=500) as store:
                records = (
                    LegacyMemoryRecord(
                        legacy_memory_id="legacy_001",
                        memory_type="preference",
                        status="active",
                        content={"fact": "用户喜欢雨天"},
                        search_terms=("雨天", "用户"),
                    ),
                    LegacyMemoryRecord(
                        legacy_memory_id="legacy_002",
                        memory_type="fact",
                        status="archived",
                        content={"fact": "一次旧观察"},
                        search_terms=("旧观察",),
                    ),
                )
                relations = (
                    LegacyMemoryRelation(
                        source_legacy_memory_id="legacy_001",
                        target_legacy_memory_id="legacy_002",
                        relation_label="causes",
                    ),
                )
                report = migrate_legacy_memory_records(
                    store,
                    life_id="life_legacy",
                    records=records,
                    relations=relations,
                    migrated_at_ms=1_000,
                    privacy_scope="private",
                )
                self.assertEqual(report.assertion_count, 2)
                self.assertEqual(report.causal_node_count, 2)
                self.assertEqual(report.ordinary_relation_count, 1)
                self.assertEqual(report.causal_hypothesis_count, 0)
                self.assertTrue(report.has_valid_report_sha256())
                self.assertEqual(len(store.list_latest_memory_assertions("life_legacy")), 1)
                self.assertEqual(len(store.list_causal_nodes("life_legacy")), 1)
                all_nodes = store.list_causal_nodes("life_legacy", recallable_only=False)
                self.assertEqual(len(all_nodes), 2)
                relation = store.list_memory_relations(
                    "life_legacy", recallable_only=False
                )[0]
                self.assertEqual(relation.relation_kind, "legacy_unclassified")
                self.assertEqual(relation.original_relation_label, "causes")
                self.assertEqual(store.list_latest_causal_hypotheses("life_legacy"), ())
                self.assertEqual(
                    store.search_memory_assertions("life_legacy", ("雨天",))[0].assertion_kind,
                    "user_preference",
                )
                retry = migrate_legacy_memory_records(
                    store,
                    life_id="life_legacy",
                    records=records,
                    relations=relations,
                    migrated_at_ms=1_000,
                    privacy_scope="private",
                )
                self.assertEqual(retry, report)
                self.assertEqual(store.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
