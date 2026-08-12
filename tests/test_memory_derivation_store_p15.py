"""P15 M1 store tests: derivation persistence, DAG, heads and offsets."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contracts import MemoryDerivationV1, MemoryParentRef
from life_service import store as life_store_module
from life_service.store import (
    SHADOW_STORE_SCHEMA_VERSION,
    LifeShadowStore,
    LifeShadowStoreError,
)


LIFE = "life_p15_store"
PRINCIPAL = "principal_alice"
PRIVACY = "privacy_alice_v1"
EVENT_A = "lev_" + "a" * 64
ROOT_A = "lev_" + "0" * 64
ROOT_B = "lev_" + "1" * 64
MEMORY_A = "mem_" + "a1" * 32
MEMORY_B = "mem_" + "b2" * 32
MEMORY_C = "mem_" + "c3" * 32


def put_assertion(
    store: LifeShadowStore,
    *,
    memory_id: str,
    created_at_ms: int,
    life_id: str = LIFE,
    privacy_scope: str = PRIVACY,
):
    assertion, _seq, _created = store.put_live_memory_assertion(
        b"p15 protected plaintext",
        memory_id=memory_id,
        life_id=life_id,
        assertion_kind="observation",
        epistemic_status="observed",
        lifecycle_status="active",
        privacy_scope=privacy_scope,
        retention_class="ACTIVE_WORKING",
        source_event_ids=(EVENT_A,),
        causal_utility_milli=0,
        user_importance_milli=0,
        verification_strength_milli=0,
        future_dependency_milli=0,
        valid_from_ms=created_at_ms,
        created_at_ms=created_at_ms,
        search_terms=(),
    )
    return assertion


def derivation(
    *,
    derivation_id: str,
    memory_id: str,
    assertion_sha256: str,
    created_at_ms: int,
    layer: str = "L1_STREAM",
    origin: str = "LIFE_EVENT",
    claim_key: str | None = None,
    parent_refs: tuple[MemoryParentRef, ...] = (),
    **overrides,
) -> MemoryDerivationV1:
    values = dict(
        derivation_id=derivation_id,
        life_id=LIFE,
        memory_id=memory_id,
        memory_revision=1,
        memory_assertion_sha256=assertion_sha256,
        layer=layer,
        semantic_domain="SYSTEM",
        origin=origin,
        principal_ref=PRINCIPAL,
        workspace_ref=None,
        privacy_scope=PRIVACY,
        claim_key=claim_key or ("event:" + derivation_id),
        parent_memory_refs=parent_refs,
        source_event_ids=(EVENT_A,),
        lineage_root_event_ids=(ROOT_A,),
        external_evidence_refs=(),
        promotion_policy_version="p15-layers-v1",
        promotion_reason_codes=(),
        valid_from_ms=created_at_ms,
        expires_at_ms=None,
        context_eligible=True,
        learning_eligible=False,
        temperament_eligible=False,
        self_cognition_eligible=False,
        world_candidate_eligible=False,
        created_at_ms=created_at_ms,
        derivation_sha256="0" * 64,
    )
    values.update(overrides)
    return MemoryDerivationV1(**values).with_computed_derivation_sha256()


def parent_ref(
    *, parent_derivation_id: str, memory_id: str, assertion_sha256: str
) -> MemoryParentRef:
    return MemoryParentRef(
        parent_derivation_id=parent_derivation_id,
        memory_id=memory_id,
        memory_revision=1,
        assertion_sha256=assertion_sha256,
        parent_ref_sha256="0" * 64,
    ).with_computed_parent_ref_sha256()


class MemoryDerivationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "p15-derivation.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_fresh_store_is_schema_15_with_derivation_tables(self) -> None:
        health = self.store.health()
        self.assertEqual(health["schema_version"], 15)
        self.assertEqual(health["schema_version"], SHADOW_STORE_SCHEMA_VERSION)
        for table in (
            "memory_derivations",
            "memory_derivation_parents",
            "memory_derivation_invalidations",
            "memory_active_heads",
            "memory_consumer_offsets",
        ):
            self.assertIsNotNone(
                self.store._connection.execute(  # noqa: SLF001
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
            )

    def test_migration_from_v14_adds_invalidation_table(self) -> None:
        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE memory_derivation_invalidations")
        connection.execute("DELETE FROM schema_migrations WHERE version = 15")
        v14_sql = (
            life_store_module._P7_SCHEMA_SQL  # noqa: SLF001
            + "\n"
            + life_store_module._P8_MEMORY_CHANGE_SQL  # noqa: SLF001
            + "\n"
            + life_store_module._P9_V21_LIFE_BINDING_SQL  # noqa: SLF001
            + "\n"
            + life_store_module._P10_V21_CAUSAL_CHILD_SQL  # noqa: SLF001
            + "\n"
            + life_store_module._P11_V21_COGNITION_SHADOW_SQL  # noqa: SLF001
            + "\n"
            + life_store_module._P12_V21_LIFE_TURN_COMMIT_SQL  # noqa: SLF001
            + "\n"
            + life_store_module._P13_V21_CAPABILITY_LIFECYCLE_SQL  # noqa: SLF001
            + "\n"
            + life_store_module._P14_MEMORY_DERIVATION_SQL  # noqa: SLF001
        )
        v14_sha = hashlib.sha256(v14_sql.encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE schema_metadata SET value = ? WHERE key = 'schema_sha256'",
            (v14_sha,),
        )
        connection.execute("PRAGMA user_version = 14")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.commit()
        connection.close()
        with LifeShadowStore.open(self.path, create=False, now_ms=1_000) as migrated:
            health = migrated.health()
            self.assertEqual(health["schema_version"], 15)
            rows = migrated._connection.execute(  # noqa: SLF001
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual(
                [int(row["version"]) for row in rows], list(range(1, 16))
            )

    def test_put_l1_derivation_round_trip_and_idempotent(self) -> None:
        assertion = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        record = derivation(
            derivation_id="mdr_" + "1" * 64,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
        )
        self.assertTrue(self.store.put_memory_derivation(record))
        self.assertFalse(self.store.put_memory_derivation(record))
        stored = self.store.get_memory_derivation(record.derivation_id)
        self.assertEqual(stored, record)
        self.assertTrue(stored.has_valid_derivation_sha256())
        self.assertEqual(
            self.store.list_memory_derivations(life_id=LIFE),
            (record,),
        )

    def test_put_requires_existing_assertion(self) -> None:
        record = derivation(
            derivation_id="mdr_" + "2" * 64,
            memory_id="mem_" + "00" * 32,
            assertion_sha256="99" * 32,
            created_at_ms=2_000,
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(record)

    def test_put_rejects_invalid_derivation_digest(self) -> None:
        assertion = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        record = derivation(
            derivation_id="mdr_" + "3" * 64,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
        )
        invalid = record.model_copy(update={"derivation_sha256": "0" * 64})
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(invalid)

    def test_same_assertion_same_layer_slot_rejected(self) -> None:
        assertion = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        record = derivation(
            derivation_id="mdr_" + "4" * 64,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
        )
        self.assertTrue(self.store.put_memory_derivation(record))
        twin = derivation(
            derivation_id="mdr_" + "5" * 64,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(twin)

    def test_promotion_origin_requires_parents(self) -> None:
        assertion = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        record = derivation(
            derivation_id="mdr_" + "6" * 64,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
            layer="L2_DIARY",
            origin="PROMOTION",
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(record)

    def test_l2_from_l1_round_trip_with_parent_edges(self) -> None:
        first = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        second = put_assertion(
            self.store, memory_id=MEMORY_B, created_at_ms=2_000
        )
        parent_id = "mdr_" + "a1" * 32
        parent = derivation(
            derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
            created_at_ms=3_000,
        )
        self.assertTrue(self.store.put_memory_derivation(parent))
        ref = parent_ref(
            parent_derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
        )
        child = derivation(
            derivation_id="mdr_" + "b2" * 32,
            memory_id=MEMORY_B,
            assertion_sha256=second.assertion_sha256,
            created_at_ms=4_000,
            layer="L2_DIARY",
            origin="PROMOTION",
            claim_key="diary:episode-1",
            parent_refs=(ref,),
        )
        self.assertTrue(self.store.put_memory_derivation(child))
        self.assertEqual(
            self.store.list_derivation_parents(child.derivation_id),
            (ref,),
        )
        self.assertEqual(
            self.store.list_derivation_children(parent_id),
            (child,),
        )

    def test_dag_parent_must_predate_child(self) -> None:
        first = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        second = put_assertion(
            self.store, memory_id=MEMORY_B, created_at_ms=2_000
        )
        parent_id = "mdr_" + "c3" * 32
        parent = derivation(
            derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
            created_at_ms=3_000,
        )
        self.assertTrue(self.store.put_memory_derivation(parent))
        ref = parent_ref(
            parent_derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
        )
        child = derivation(
            derivation_id="mdr_" + "d4" * 32,
            memory_id=MEMORY_B,
            assertion_sha256=second.assertion_sha256,
            created_at_ms=3_000,
            layer="L2_DIARY",
            origin="PROMOTION",
            claim_key="diary:episode-2",
            parent_refs=(ref,),
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(child)

    def test_lineage_root_drop_rejected(self) -> None:
        first = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        second = put_assertion(
            self.store, memory_id=MEMORY_B, created_at_ms=2_000
        )
        parent_id = "mdr_" + "e5" * 32
        parent = derivation(
            derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
            created_at_ms=3_000,
        )
        self.assertTrue(self.store.put_memory_derivation(parent))
        ref = parent_ref(
            parent_derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
        )
        child = derivation(
            derivation_id="mdr_" + "f6" * 32,
            memory_id=MEMORY_B,
            assertion_sha256=second.assertion_sha256,
            created_at_ms=4_000,
            layer="L2_DIARY",
            origin="PROMOTION",
            claim_key="diary:episode-3",
            parent_refs=(ref,),
            lineage_root_event_ids=("lev_" + "9" * 64,),
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(child)

    def test_principal_scope_mismatch_rejected(self) -> None:
        first = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        second = put_assertion(
            self.store, memory_id=MEMORY_B, created_at_ms=2_000
        )
        parent_id = "mdr_" + "17" * 32
        parent = derivation(
            derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
            created_at_ms=3_000,
        )
        self.assertTrue(self.store.put_memory_derivation(parent))
        ref = parent_ref(
            parent_derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
        )
        child = derivation(
            derivation_id="mdr_" + "28" * 32,
            memory_id=MEMORY_B,
            assertion_sha256=second.assertion_sha256,
            created_at_ms=4_000,
            layer="L2_DIARY",
            origin="PROMOTION",
            claim_key="diary:episode-4",
            parent_refs=(ref,),
            principal_ref="principal_bob",
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(child)

    def test_privacy_scope_mismatch_rejected(self) -> None:
        first = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        second = put_assertion(
            self.store, memory_id=MEMORY_B, created_at_ms=2_000
        )
        parent_id = "mdr_" + "39" * 32
        parent = derivation(
            derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
            created_at_ms=3_000,
        )
        self.assertTrue(self.store.put_memory_derivation(parent))
        ref = parent_ref(
            parent_derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
        )
        child = derivation(
            derivation_id="mdr_" + "40" * 32,
            memory_id=MEMORY_B,
            assertion_sha256=second.assertion_sha256,
            created_at_ms=4_000,
            layer="L2_DIARY",
            origin="PROMOTION",
            claim_key="diary:episode-5",
            parent_refs=(ref,),
            privacy_scope="privacy_bob_v1",
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(child)

    def test_assertion_binding_mismatch_rejected(self) -> None:
        assertion = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        record = derivation(
            derivation_id="mdr_" + "7" * 64,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
            life_id="life_other",
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(record)

    def test_active_head_replace_keeps_history(self) -> None:
        old_assertion = put_assertion(
            self.store, memory_id=MEMORY_B, created_at_ms=1_000
        )
        new_assertion = put_assertion(
            self.store, memory_id=MEMORY_C, created_at_ms=2_000
        )
        claim = "claim:long-term-preference"
        old_id = "mdr_" + "8" * 64
        new_id = "mdr_" + "9" * 64
        old_record = derivation(
            derivation_id=old_id,
            memory_id=MEMORY_B,
            assertion_sha256=old_assertion.assertion_sha256,
            created_at_ms=3_000,
            layer="L4_EXPLICIT",
            origin="USER_EXPLICIT",
            claim_key=claim,
            lineage_root_event_ids=(ROOT_A,),
        )
        new_record = derivation(
            derivation_id=new_id,
            memory_id=MEMORY_C,
            assertion_sha256=new_assertion.assertion_sha256,
            created_at_ms=4_000,
            layer="L4_EXPLICIT",
            origin="USER_EXPLICIT",
            claim_key=claim,
            lineage_root_event_ids=(ROOT_B,),
        )
        self.assertTrue(
            self.store.put_memory_derivation(old_record, activate_head=True)
        )
        self.assertTrue(
            self.store.put_memory_derivation(new_record, activate_head=True)
        )
        head = self.store.get_active_memory_head(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key=claim,
            layer="L4_EXPLICIT",
        )
        self.assertEqual(head, new_record)
        self.assertEqual(
            self.store.list_active_memory_heads(life_id=LIFE),
            (new_record,),
        )
        self.assertEqual(
            set(
                item.derivation_id
                for item in self.store.list_memory_derivations(life_id=LIFE)
            ),
            {old_id, new_id},
        )

    def test_consumer_offset_advance_idempotent_and_no_backwards(self) -> None:
        self.assertEqual(
            self.store.get_memory_consumer_offset("consumer-x", LIFE), 0
        )
        self.assertTrue(
            self.store.advance_memory_consumer_offset(
                "consumer-x", LIFE, 12, updated_at_ms=1_000
            )
        )
        self.assertTrue(
            self.store.advance_memory_consumer_offset(
                "consumer-x", LIFE, 12, updated_at_ms=1_100
            )
        )
        self.assertEqual(
            self.store.get_memory_consumer_offset("consumer-x", LIFE), 12
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.advance_memory_consumer_offset(
                "consumer-x", LIFE, 3, updated_at_ms=1_200
            )
        self.assertEqual(
            self.store.get_memory_consumer_offset("consumer-x", LIFE), 12
        )

    def test_invalid_parent_ref_unknown_derivation_rejected(self) -> None:
        assertion = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        ref = parent_ref(
            parent_derivation_id="mdr_" + "00" * 32,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
        )
        child = derivation(
            derivation_id="mdr_" + "a0" * 32,
            memory_id=MEMORY_A,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
            layer="L2_DIARY",
            origin="PROMOTION",
            claim_key="diary:episode-6",
            parent_refs=(ref,),
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(child)
        self.assertIsNone(self.store.get_memory_derivation(child.derivation_id))

    def test_transaction_rollback_on_invalid_parent(self) -> None:
        first = put_assertion(
            self.store, memory_id=MEMORY_A, created_at_ms=1_000
        )
        second = put_assertion(
            self.store, memory_id=MEMORY_B, created_at_ms=2_000
        )
        parent_id = "mdr_" + "b1" * 32
        parent = derivation(
            derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
            created_at_ms=3_000,
        )
        self.assertTrue(self.store.put_memory_derivation(parent))
        ref = parent_ref(
            parent_derivation_id=parent_id,
            memory_id=MEMORY_A,
            assertion_sha256=first.assertion_sha256,
        )
        child = derivation(
            derivation_id="mdr_" + "c2" * 32,
            memory_id=MEMORY_B,
            assertion_sha256=second.assertion_sha256,
            created_at_ms=4_000,
            layer="L2_DIARY",
            origin="PROMOTION",
            claim_key="diary:episode-7",
            parent_refs=(ref,),
            lineage_root_event_ids=("lev_" + "9" * 64,),
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(child, activate_head=True)
        self.assertIsNone(self.store.get_memory_derivation(child.derivation_id))
        self.assertEqual(
            self.store.list_memory_derivations(life_id=LIFE),
            (parent,),
        )
        self.assertEqual(
            self.store.list_active_memory_heads(life_id=LIFE),
            (),
        )


if __name__ == "__main__":
    unittest.main()
