"""P15 M8: legacy memory derives conservative layers, never L5."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import MemoryAssertionV3
from life_service.legacy_layer_migration import (
    LEGACY_MIGRATION_POLICY,
    build_legacy_derivation,
    legacy_layer_for_assertion,
)
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_legacy"
PRIVACY = "private"
EVENT_ID = "lev_" + "a" * 64


def legacy_assertion(
    store: LifeShadowStore,
    *,
    memory_id: str,
    retention_class: str,
    assertion_kind: str = "legacy",
    epistemic_status: str = "observed",
    source_event_ids: tuple[str, ...] = (),
    created_at_ms: int = 1_000,
) -> MemoryAssertionV3:
    protected = store.put_protected_payload(
        b"legacy content",
        life_id=LIFE,
        privacy_scope=PRIVACY,
        created_at_ms=created_at_ms,
    )
    assertion = MemoryAssertionV3(
        memory_id=memory_id,
        life_id=LIFE,
        revision=1,
        supersedes_assertion_sha256=None,
        assertion_kind=assertion_kind,
        epistemic_status=epistemic_status,
        lifecycle_status="active",
        protected_payload_id=protected.payload_id,
        protected_payload_sha256=protected.ciphertext_sha256,
        deletion_tombstone_id=None,
        privacy_scope=PRIVACY,
        retention_class=retention_class,
        source_event_ids=source_event_ids,
        causal_hypothesis_ids=(),
        causal_utility_milli=0,
        user_importance_milli=0,
        verification_strength_milli=0,
        recurrence_count=0,
        future_dependency_milli=0,
        privacy_cost_milli=0,
        contradiction_penalty_milli=0,
        staleness_milli=0,
        valid_from_ms=created_at_ms,
        expires_at_ms=None,
        created_at_ms=created_at_ms,
        assertion_sha256="0" * 64,
    ).with_computed_assertion_sha256()
    store.put_memory_assertion(assertion)
    return assertion


class LegacyLayerMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "legacy.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_turn_episodic_maps_to_l1(self) -> None:
        assertion = legacy_assertion(
            self.store,
            memory_id="mem_" + "1" * 64,
            retention_class="ACTIVE_WORKING",
            assertion_kind="observation",
            source_event_ids=(EVENT_ID,),
        )
        self.assertEqual(legacy_layer_for_assertion(assertion), "L1_STREAM")

    def test_checkpoint_summary_maps_to_l2(self) -> None:
        assertion = legacy_assertion(
            self.store,
            memory_id="mem_" + "2" * 64,
            retention_class="CHECKPOINT",
        )
        self.assertEqual(legacy_layer_for_assertion(assertion), "L2_DIARY")

    def test_long_term_without_provenance_maps_to_l3_candidate(self) -> None:
        assertion = legacy_assertion(
            self.store,
            memory_id="mem_" + "3" * 64,
            retention_class="LONG_TERM_MEMORY",
            assertion_kind="observation",
            epistemic_status="observed",
            source_event_ids=(),
        )
        self.assertEqual(
            legacy_layer_for_assertion(assertion), "L3_EXPERIENCE"
        )

    def test_explicit_provenance_maps_to_l4(self) -> None:
        assertion = legacy_assertion(
            self.store,
            memory_id="mem_" + "4" * 64,
            retention_class="LONG_TERM_MEMORY",
            assertion_kind="user_preference",
            epistemic_status="user_asserted",
            source_event_ids=(EVENT_ID,),
        )
        self.assertEqual(legacy_layer_for_assertion(assertion), "L4_EXPLICIT")

    def test_legacy_never_maps_to_l5(self) -> None:
        assertion = legacy_assertion(
            self.store,
            memory_id="mem_" + "5" * 64,
            retention_class="LONG_TERM_MEMORY",
        )
        with self.assertRaises(ValueError):
            build_legacy_derivation(
                assertion, layer="L5_CORE", created_at_ms=2_000
            )

    def test_migration_derivation_is_audited(self) -> None:
        assertion = legacy_assertion(
            self.store,
            memory_id="mem_" + "6" * 64,
            retention_class="CHECKPOINT",
        )
        derivation = build_legacy_derivation(
            assertion, layer="L2_DIARY", created_at_ms=2_000
        )
        self.assertEqual(derivation.origin, "MIGRATION")
        self.assertEqual(
            derivation.promotion_policy_version, LEGACY_MIGRATION_POLICY
        )
        self.assertIn("legacy_migration", derivation.promotion_reason_codes)
        self.assertTrue(
            any(
                code.startswith("migration:")
                for code in derivation.promotion_reason_codes
            )
        )
        self.assertEqual(
            derivation.memory_assertion_sha256,
            assertion.assertion_sha256,
        )
        self.assertTrue(derivation.has_valid_derivation_sha256())

    def test_migrate_legacy_memories_is_idempotent(self) -> None:
        legacy_assertion(
            self.store,
            memory_id="mem_" + "7" * 64,
            retention_class="ACTIVE_WORKING",
            assertion_kind="observation",
            source_event_ids=(EVENT_ID,),
        )
        legacy_assertion(
            self.store,
            memory_id="mem_" + "8" * 64,
            retention_class="CHECKPOINT",
        )
        legacy_assertion(
            self.store,
            memory_id="mem_" + "9" * 64,
            retention_class="LONG_TERM_MEMORY",
        )
        legacy_assertion(
            self.store,
            memory_id="mem_" + "0a" * 32,
            retention_class="LONG_TERM_MEMORY",
            assertion_kind="user_preference",
            epistemic_status="user_asserted",
            source_event_ids=(EVENT_ID,),
        )
        first = self.coordinator.migrate_legacy_memories(
            life_id=LIFE, now_ms=2_000
        )
        self.assertEqual(first["migrated_count"], 4)
        self.assertEqual(first["migrated_by_layer"]["L1_STREAM"], 1)
        self.assertEqual(first["migrated_by_layer"]["L2_DIARY"], 1)
        self.assertEqual(first["migrated_by_layer"]["L3_EXPERIENCE"], 1)
        self.assertEqual(first["migrated_by_layer"]["L4_EXPLICIT"], 1)
        second = self.coordinator.migrate_legacy_memories(
            life_id=LIFE, now_ms=3_000
        )
        self.assertEqual(second["migrated_count"], 0)
        self.assertGreaterEqual(second["skipped_count"], 4)

    def test_migration_never_creates_l5(self) -> None:
        legacy_assertion(
            self.store,
            memory_id="mem_" + "ab" * 32,
            retention_class="LONG_TERM_MEMORY",
        )
        self.coordinator.migrate_legacy_memories(
            life_id=LIFE, now_ms=2_000
        )
        rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations "
            "WHERE life_id = ? AND layer = 'L5_CORE'",
            (LIFE,),
        ).fetchone()
        self.assertEqual(int(rows["n"]), 0)

    def test_migration_is_per_life_isolated(self) -> None:
        legacy_assertion(
            self.store,
            memory_id="mem_" + "bc" * 32,
            retention_class="CHECKPOINT",
        )
        self.coordinator.migrate_legacy_memories(
            life_id=LIFE, now_ms=2_000
        )
        other = self.coordinator.migrate_legacy_memories(
            life_id="life_other", now_ms=2_000
        )
        self.assertEqual(other["migrated_count"], 0)

    def test_assertions_with_existing_derivation_are_skipped(self) -> None:
        value = event(1, None, life_id=LIFE)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _ = l1
        result = self.coordinator.migrate_legacy_memories(
            life_id=LIFE, now_ms=2_000
        )
        self.assertEqual(result["migrated_count"], 0)
        self.assertGreaterEqual(result["skipped_count"], 1)


if __name__ == "__main__":
    unittest.main()
