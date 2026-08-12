from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from contracts import (
    CausalContextItem,
    CausalContextPack,
    CausalNodeV3,
    ContextTokenBudget,
    MemoryAssertionV3,
    MemoryRelationV3,
)
from life_service.store import SHADOW_STORE_SCHEMA_VERSION, LifeShadowStore, LifeShadowStoreError
from life_service import store as life_store_module
from tests.life_contract_support import event
from tests.test_continuity_capsule import capsule


EVENT_ID = "lev_" + "1" * 64
MEMORY_ID = "mem_" + "2" * 64
NODE_ID = "cnd_" + "3" * 64


def token_budget(current: int = 110_400) -> ContextTokenBudget:
    utilization = min(1000, (current * 1000) // 120_000)
    watermark = (
        "BELOW_75"
        if utilization < 750
        else "CANDIDATE_75"
        if utilization < 850
        else "MUST_PERSIST_85"
        if utilization < 920
        else "MUST_SWITCH_92"
    )
    return ContextTokenBudget(
        model_context_limit_tokens=160_000,
        product_limit_tokens=120_000,
        output_reserve_tokens=20_000,
        tool_schema_reserve_tokens=10_000,
        authority_reserve_tokens=5_000,
        protocol_reserve_tokens=5_000,
        usable_budget_tokens=120_000,
        current_context_tokens=current,
        utilization_milli=utilization,
        watermark=watermark,
    )


def memory_assertion(
    payload_id: str,
    payload_sha256: str,
    *,
    revision: int = 1,
    supersedes: str | None = None,
    retention_class: str = "LONG_TERM_MEMORY",
) -> MemoryAssertionV3:
    return MemoryAssertionV3(
        memory_id=MEMORY_ID,
        life_id="life_contract_test",
        revision=revision,
        supersedes_assertion_sha256=supersedes,
        assertion_kind="hard_constraint",
        epistemic_status="verified",
        lifecycle_status="active",
        protected_payload_id=payload_id,
        protected_payload_sha256=payload_sha256,
        deletion_tombstone_id=None,
        privacy_scope="private",
        retention_class=retention_class,
        source_event_ids=(EVENT_ID,),
        causal_hypothesis_ids=(),
        causal_utility_milli=900,
        user_importance_milli=1000,
        verification_strength_milli=1000,
        recurrence_count=2,
        future_dependency_milli=1000,
        privacy_cost_milli=100,
        contradiction_penalty_milli=0,
        staleness_milli=0,
        valid_from_ms=1_000,
        expires_at_ms=None,
        created_at_ms=1_000 + revision,
        assertion_sha256="0" * 64,
    ).with_computed_assertion_sha256()


class CausalMemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "causal-memory.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_protected_memory_revision_relation_node_and_context_round_trip(self) -> None:
        plaintext = "绝不能丢失用户的硬约束。".encode("utf-8")
        protected = self.store.put_protected_payload(
            plaintext,
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_000,
        )
        self.assertEqual(self.store.read_protected_payload(protected.payload_id), plaintext)
        row = self.store._connection.execute(  # noqa: SLF001
            "SELECT ciphertext FROM protected_payloads WHERE payload_id = ?",
            (protected.payload_id,),
        ).fetchone()
        self.assertNotIn(plaintext, bytes(row["ciphertext"]))

        first = memory_assertion(protected.payload_id, protected.ciphertext_sha256)
        self.assertTrue(
            self.store.put_memory_assertion(first, search_terms=("硬约束", "用户"))
        )
        self.assertFalse(
            self.store.put_memory_assertion(first, search_terms=("不会重建索引",))
        )
        self.assertEqual(
            self.store.search_memory_assertions("life_contract_test", ("硬约束",)),
            (first,),
        )

        replacement_payload = self.store.put_protected_payload(
            "必须保留用户硬约束。".encode("utf-8"),
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_100,
        )
        second = memory_assertion(
            replacement_payload.payload_id,
            replacement_payload.ciphertext_sha256,
            revision=2,
            supersedes=first.assertion_sha256,
        )
        self.assertTrue(
            self.store.put_memory_assertion(second, search_terms=("硬约束", "必须"))
        )
        self.assertEqual(
            self.store.search_memory_assertions("life_contract_test", ("硬约束",)),
            (second,),
        )

        relation = MemoryRelationV3(
            relation_id="mrl_" + "4" * 64,
            life_id="life_contract_test",
            source_memory_id=MEMORY_ID,
            relation_kind="legacy_unclassified",
            original_relation_label="旧系统的 affects 关系",
            target_ref="memory_target",
            evidence_class="model_inference",
            supporting_event_ids=(),
            created_at_ms=1_200,
            relation_sha256="0" * 64,
        ).with_computed_relation_sha256()
        self.assertTrue(self.store.put_memory_relation(relation))
        self.assertEqual(self.store.list_memory_relations("life_contract_test"), (relation,))

        node = CausalNodeV3(
            node_id=NODE_ID,
            life_id="life_contract_test",
            node_kind="constraint",
            source_ref=MEMORY_ID,
            protected_payload_id=replacement_payload.payload_id,
            protected_payload_sha256=replacement_payload.ciphertext_sha256,
            privacy_scope="private",
            retention_class="LONG_TERM_MEMORY",
            recall_status="active",
            source_event_ids=(),
            created_at_ms=1_300,
            node_sha256="0" * 64,
        ).with_computed_node_sha256()
        self.assertTrue(self.store.put_causal_node(node, search_terms=("约束",)))
        self.assertEqual(self.store.list_causal_nodes("life_contract_test"), (node,))

        continuity = capsule(created_at_ms=1_400).with_computed_capsule_sha256()
        self.assertTrue(self.store.put_context_capsule(continuity))
        item = CausalContextItem(
            item_ref=MEMORY_ID,
            item_kind="constraint",
            source_revision=2,
            summary="必须保留用户硬约束。",
            epistemic_status="verified",
            confidence_milli=1000,
            priority=4_000,
            privacy_scope="private",
            token_count=12,
            supporting_event_ids=(EVENT_ID,),
        )
        pack = CausalContextPack(
            pack_id="ccp_" + "5" * 64,
            life_id="life_contract_test",
            continuity=continuity,
            seed_refs=(MEMORY_ID,),
            items=(item,),
            edges=(),
            token_budget=token_budget(),
            selected_token_count=128,
            omitted_item_count=0,
            visible_raw_tool_process_count=0,
            integrity_status="VERIFIED",
            model_input_switched=False,
            created_at_ms=1_500,
            pack_sha256="0" * 64,
        ).with_computed_pack_sha256()
        persisted = self.store.put_causal_context_pack(pack, privacy_scope="private")
        self.assertTrue(persisted.created_by_this_call)
        self.assertEqual(self.store.read_causal_context_pack(pack.pack_id), pack)
        duplicate = self.store.put_causal_context_pack(pack, privacy_scope="private")
        self.assertFalse(duplicate.created_by_this_call)

    def test_privacy_delete_destroys_recall_paths_but_keeps_minimal_proof(self) -> None:
        secret = "仅用于删除测试的敏感记忆"
        protected = self.store.put_protected_payload(
            secret.encode("utf-8"),
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_000,
        )
        assertion = memory_assertion(protected.payload_id, protected.ciphertext_sha256)
        self.store.put_memory_assertion(assertion, search_terms=("敏感", "删除"))
        node = CausalNodeV3(
            node_id=NODE_ID,
            life_id="life_contract_test",
            node_kind="constraint",
            source_ref=MEMORY_ID,
            protected_payload_id=protected.payload_id,
            protected_payload_sha256=protected.ciphertext_sha256,
            privacy_scope="private",
            retention_class="LONG_TERM_MEMORY",
            recall_status="active",
            source_event_ids=(),
            created_at_ms=1_050,
            node_sha256="0" * 64,
        ).with_computed_node_sha256()
        self.store.put_causal_node(node, search_terms=("敏感",))
        continuity = capsule(created_at_ms=1_100).with_computed_capsule_sha256()
        self.store.put_context_capsule(continuity)
        item = CausalContextItem(
            item_ref=MEMORY_ID,
            item_kind="constraint",
            source_revision=1,
            summary=secret,
            epistemic_status="verified",
            confidence_milli=1000,
            priority=4_000,
            privacy_scope="private",
            token_count=20,
            supporting_event_ids=(EVENT_ID,),
        )
        pack = CausalContextPack(
            pack_id="ccp_" + "6" * 64,
            life_id="life_contract_test",
            continuity=continuity,
            seed_refs=(MEMORY_ID,),
            items=(item,),
            edges=(),
            token_budget=token_budget(),
            selected_token_count=140,
            omitted_item_count=0,
            created_at_ms=1_200,
            pack_sha256="0" * 64,
        ).with_computed_pack_sha256()
        persisted = self.store.put_causal_context_pack(pack, privacy_scope="private")

        result = self.store.delete_memory(
            MEMORY_ID, expected_revision=1, deleted_at_ms=2_000
        )
        self.assertEqual(result.deleted_assertion.lifecycle_status, "deleted")
        self.assertIn(protected.payload_id, result.destroyed_payload_ids)
        self.assertIn(persisted.protected_payload.payload_id, result.destroyed_payload_ids)
        self.assertEqual(
            self.store.search_memory_assertions("life_contract_test", ("敏感",)), ()
        )
        self.assertEqual(self.store.list_latest_memory_assertions("life_contract_test"), ())
        self.assertEqual(self.store.list_causal_nodes("life_contract_test"), ())
        with self.assertRaisesRegex(LifeShadowStoreError, "key is unavailable"):
            self.store.read_protected_payload(protected.payload_id)
        with self.assertRaisesRegex(LifeShadowStoreError, "key is unavailable"):
            self.store.read_causal_context_pack(pack.pack_id)
        self.assertIsNone(
            self.store.get_latest_causal_context_pack(
                continuity.request_id,
                run_id=continuity.run_id,
                generation=continuity.generation,
            )
        )
        tombstone_bytes = self.store._connection.execute(  # noqa: SLF001
            "SELECT payload FROM privacy_deletion_tombstones"
        ).fetchone()["payload"]
        self.assertNotIn(secret.encode("utf-8"), bytes(tombstone_bytes))
        self.assertEqual(
            self.store._connection.execute(  # noqa: SLF001
                "SELECT count(*) FROM protected_payload_keys WHERE payload_id IN (?, ?)",
                (protected.payload_id, persisted.protected_payload.payload_id),
            ).fetchone()[0],
            0,
        )
        retry = self.store.delete_memory(
            MEMORY_ID, expected_revision=1, deleted_at_ms=3_000
        )
        self.assertEqual(retry.tombstone, result.tombstone)
        self.assertEqual(self.store.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)

    def test_legal_hold_is_fail_closed(self) -> None:
        protected = self.store.put_protected_payload(
            b"legal-hold",
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_000,
        )
        value = memory_assertion(
            protected.payload_id,
            protected.ciphertext_sha256,
            retention_class="LEGAL_HOLD",
        )
        self.store.put_memory_assertion(value)
        with self.assertRaisesRegex(LifeShadowStoreError, "legal-hold"):
            self.store.delete_memory(MEMORY_ID, expected_revision=1, deleted_at_ms=2_000)
        self.assertEqual(self.store.read_protected_payload(protected.payload_id), b"legal-hold")

    def test_direct_database_contains_no_memory_plaintext(self) -> None:
        protected = self.store.put_protected_payload(
            "数据库中不可见的句子".encode("utf-8"),
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_000,
        )
        self.store.put_memory_assertion(
            memory_assertion(protected.payload_id, protected.ciphertext_sha256),
            search_terms=("数据库中不可见的句子",),
        )
        self.store.close()
        self.store = LifeShadowStore.open(self.path, create=False, now_ms=1_500)
        connection = sqlite3.connect(self.path)
        try:
            dump = b"\n".join(
                bytes(value)
                for row in connection.execute(
                    "SELECT ciphertext FROM protected_payloads"
                ).fetchall()
                for value in row
            )
        finally:
            connection.close()
        self.assertNotIn("数据库中不可见的句子".encode("utf-8"), dump)

    def test_health_rejects_ciphertext_and_stale_index_tampering(self) -> None:
        protected = self.store.put_protected_payload(
            b"authenticated",
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_000,
        )
        self.store.put_memory_assertion(
            memory_assertion(protected.payload_id, protected.ciphertext_sha256),
            search_terms=("authenticated",),
        )
        self.store._connection.execute(  # noqa: SLF001
            "UPDATE memory_search_terms SET revision = 2"
        )
        with self.assertRaisesRegex(LifeShadowStoreError, "stale content"):
            self.store.health()

        self.store._connection.execute(  # noqa: SLF001
            "UPDATE memory_search_terms SET revision = 1"
        )
        self.store._connection.execute(  # noqa: SLF001
            """
            UPDATE protected_payloads
            SET ciphertext = zeroblob(length(ciphertext))
            WHERE payload_id = ?
            """,
            (protected.payload_id,),
        )
        with self.assertRaisesRegex(LifeShadowStoreError, "ciphertext digest"):
            self.store.health()

    def test_concurrent_revision_writers_have_one_winner(self) -> None:
        first_payload = self.store.put_protected_payload(
            b"revision-one",
            life_id="life_contract_test",
            privacy_scope="private",
            created_at_ms=1_000,
        )
        first = memory_assertion(
            first_payload.payload_id, first_payload.ciphertext_sha256
        )
        self.store.put_memory_assertion(first)
        contenders = []
        for marker in (b"revision-two-a", b"revision-two-b"):
            protected = self.store.put_protected_payload(
                marker,
                life_id="life_contract_test",
                privacy_scope="private",
                created_at_ms=1_100,
            )
            contenders.append(
                memory_assertion(
                    protected.payload_id,
                    protected.ciphertext_sha256,
                    revision=2,
                    supersedes=first.assertion_sha256,
                )
            )

        def commit(value: MemoryAssertionV3) -> str:
            with LifeShadowStore.open(self.path, create=False, now_ms=1_200) as store:
                try:
                    return "created" if store.put_memory_assertion(value) else "duplicate"
                except LifeShadowStoreError:
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(commit, contenders))
        self.assertEqual(sorted(outcomes), ["conflict", "created"])
        self.assertEqual(self.store.get_latest_memory_assertion(MEMORY_ID).revision, 2)

    def test_v2_to_v3_migration_does_not_rewrite_event_history(self) -> None:
        self.store.close()
        self.store = LifeShadowStore.open(self.path, create=False, now_ms=600)
        value = event(1, None)
        self.store.append_event(value)
        before = bytes(
            self.store._connection.execute(  # noqa: SLF001
                "SELECT envelope FROM life_events WHERE event_id = ?", (value.event_id,)
            ).fetchone()["envelope"]
        )
        self.store.close()
        connection = sqlite3.connect(self.path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "memory_world_candidate_outbox",
                "temperament_adaptation_receipts",
                "memory_derivation_invalidations",
                "memory_derivation_parents",
                "memory_active_heads",
                "memory_consumer_offsets",
                "memory_derivations",
                "root_continuation_bindings",
                "root_experience_heads",
                "run_life_bindings",
                "life_authority_heads",
                "causal_episodes_vnext",
                "stimulus_inbox",
                "cognition_lane_leases",
                "cognition_state",
                "model_attempt_shadow",
                "life_turn_commits",
                "capability_candidate_artifacts",
                "capability_pointer_heads",
                "memory_change_log",
                "memory_outbox",
                "context_authorizations",
                "capability_invalidations",
                "capability_learning_decisions",
                "capability_rollbacks",
                "episode_outcomes",
                "reflection_question_decisions",
                "autonomy_usage_snapshots",
                "autonomy_policies",
                "action_candidates",
                "viability_observations",
                "affect_dedupe",
                "affect_source_offsets",
                "affect_signal_receipts",
                "affect_source_policies",
                "causal_context_pack_members",
                "causal_context_packs",
                "causal_node_terms",
                "memory_search_terms",
                "memory_assertion_contracts",
                "privacy_suppressions",
                "privacy_deletion_tombstones",
                "protected_payload_keys",
                "life_index_keys",
                "protected_payloads",
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("DELETE FROM schema_migrations WHERE version >= 3")
            connection.execute(
                "UPDATE schema_metadata SET value = ? WHERE key = 'schema_sha256'",
                (life_store_module._P2_SCHEMA_SHA256,),  # noqa: SLF001
            )
            connection.execute("PRAGMA user_version=2")
            connection.execute("COMMIT")
        finally:
            connection.close()
        self.store = LifeShadowStore.open(self.path, create=False, now_ms=2_000)
        after = bytes(
            self.store._connection.execute(  # noqa: SLF001
                "SELECT envelope FROM life_events WHERE event_id = ?", (value.event_id,)
            ).fetchone()["envelope"]
        )
        self.assertEqual(after, before)
        self.assertEqual(self.store.replay(value.life_id).head_event_hash, value.event_hash)
        self.assertEqual(self.store.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
