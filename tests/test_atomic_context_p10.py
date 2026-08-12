from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import life_service.store as store_module
from life_service.context_api import (
    LifeContextApiError,
    LifeContextCompileAuthorizeApi,
    LifeProjectionInputs,
)
from life_service.context_authority import (
    LifeContextAuthority,
    LifeContextAuthorityError,
)
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.store import SHADOW_STORE_SCHEMA_VERSION, LifeShadowStore
from tests.test_continuity_capsule import capsule
from total_gateway.life_client import LifeClient, LifeProfileBindings
from total_gateway.frozen_backend_compat import FrozenBackendCompatibilityTransport
from total_gateway.object_store import ContentAddressedObjectStore


HASH_A = "a" * 64


class AtomicContextP10Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "p10.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=100)
        self.continuity = capsule(
            user_goal="第一次对话也必须直接编译上下文。",
            created_at_ms=1_000,
        ).with_computed_capsule_sha256()
        self.store.put_context_capsule(self.continuity)
        self.authority = LifeContextAuthority(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def compile(self, *, principal: str = HASH_A):
        return self.authority.compile_and_authorize(
            self.continuity,
            current_request="这是第一条消息，没有 latest_context。",
            principal_scope_hash=principal,
            writer_epoch=3,
            identity_revision=3,
            soul_revision=2,
            current_context_tokens=0,
            issued_at_ms=2_000,
        )

    def test_embedded_memory_projection_canonicalizes_newest_first_records(self) -> None:
        """A UI-oriented newest-first memory map must satisfy authority ordering."""
        runtime = object.__new__(EmbeddedLifeRuntime)
        runtime._scope_state = lambda: {
            "memories": {
                "mem-z": {
                    "memory_id": "mem-z",
                    "content": {"value": "oldest"},
                    "revision": 1,
                },
                "mem-a": {
                    "memory_id": "mem-a",
                    "content": {"value": "newest"},
                    "revision": 2,
                },
            }
        }

        projected = runtime._external_memory_items()

        projected_refs = [item.item_ref for item in projected]
        self.assertEqual(projected_refs[0], "affect_life_projection")
        self.assertTrue(projected_refs[1].startswith("life_activity_life_projection_"))
        self.assertEqual(projected_refs[2:], ["mem-a", "mem-z"])
        affect = projected[0]
        self.assertEqual(affect.item_kind, "constraint")
        self.assertEqual(affect.epistemic_status, "observed")
        self.assertIn("Affect only modulates attention", affect.summary)
        self.assertTrue(
            all(item.token_count == len(item.summary.encode("utf-8")) for item in projected)
        )

    def test_first_conversation_compiles_without_latest_context(self) -> None:
        self.assertIsNone(
            self.store.get_latest_causal_context_pack(
                self.continuity.request_id,
                run_id=self.continuity.run_id,
                generation=self.continuity.generation,
            )
        )
        result = self.compile()
        self.assertTrue(result.initial_context)
        self.assertTrue(result.authorization.initial_context)
        self.assertEqual(result.context_pack.continuity, self.continuity)
        self.assertTrue(result.authorization.has_valid_authorization_sha256())
        self.assertTrue(result.authorization.revisions.has_valid_vector_sha256())
        self.assertEqual(result.authorization.revisions.writer_epoch, 3)
        self.assertEqual(result.authorization.revisions.soul_revision, 2)
        self.assertEqual(result.context_pack.visible_raw_tool_process_count, 0)
        self.assertEqual(
            self.store.get_latest_causal_context_pack_for_life(
                self.continuity.life_id
            ),
            result.context_pack,
        )
        panel_context = EmbeddedLifeRuntime._context_panel_projection(
            result.context_pack
        )
        self.assertTrue(panel_context["available"])
        self.assertTrue(panel_context["verified"])
        self.assertEqual(
            panel_context["context_hash"], result.context_pack.pack_sha256
        )
        self.assertEqual(
            panel_context["token_budget"],
            result.context_pack.token_budget.usable_budget_tokens,
        )
        self.assertEqual(
            self.store.get_context_authorization(
                self.continuity.request_id,
                run_id=self.continuity.run_id,
                generation=self.continuity.generation,
            ),
            result.authorization,
        )

    def test_retry_is_exact_and_cross_principal_rebind_fails(self) -> None:
        first = self.compile()
        second = self.compile()
        self.assertEqual(first, second)
        with self.assertRaisesRegex(
            LifeContextAuthorityError, "rebound to different authority"
        ):
            self.compile(principal="b" * 64)

    def test_pack_and_authorization_commit_roll_back_together(self) -> None:
        original = self.store._put_context_authorization_locked  # noqa: SLF001

        def fail(*_args, **_kwargs):
            raise RuntimeError("fault after protected context write")

        self.store._put_context_authorization_locked = fail  # type: ignore[method-assign]  # noqa: SLF001
        try:
            with self.assertRaises(LifeContextAuthorityError):
                self.compile()
        finally:
            self.store._put_context_authorization_locked = original  # type: ignore[method-assign]  # noqa: SLF001
        self.assertIsNone(
            self.store.get_latest_causal_context_pack(
                self.continuity.request_id,
                run_id=self.continuity.run_id,
                generation=self.continuity.generation,
            )
        )
        self.assertIsNone(
            self.store.get_context_authorization(
                self.continuity.request_id,
                run_id=self.continuity.run_id,
                generation=self.continuity.generation,
            )
        )

    def test_v6_store_migrates_to_atomic_context_v7(self) -> None:
        self.store.close()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TABLE memory_world_candidate_outbox")
            connection.execute("DROP TABLE temperament_adaptation_receipts")
            connection.execute("DROP TABLE memory_derivation_invalidations")
            connection.execute("DROP TABLE memory_derivation_parents")
            connection.execute("DROP TABLE memory_active_heads")
            connection.execute("DROP TABLE memory_consumer_offsets")
            connection.execute("DROP TABLE memory_derivations")
            connection.execute("DROP TABLE root_continuation_bindings")
            connection.execute("DROP TABLE root_experience_heads")
            connection.execute("DROP TABLE run_life_bindings")
            connection.execute("DROP TABLE life_authority_heads")
            connection.execute("DROP TABLE causal_episodes_vnext")
            connection.execute("DROP TABLE stimulus_inbox")
            connection.execute("DROP TABLE cognition_lane_leases")
            connection.execute("DROP TABLE cognition_state")
            connection.execute("DROP TABLE model_attempt_shadow")
            connection.execute("DROP TABLE life_turn_commits")
            connection.execute("DROP TABLE capability_candidate_artifacts")
            connection.execute("DROP TABLE capability_pointer_heads")
            connection.execute("DROP TABLE memory_outbox")
            connection.execute("DROP TABLE memory_change_log")
            connection.execute("DROP INDEX context_authorizations_life_time_idx")
            connection.execute("DROP TABLE context_authorizations")
            connection.execute("DELETE FROM schema_migrations WHERE version >= 7")
            connection.execute(
                "UPDATE schema_metadata SET value = ? WHERE key = 'schema_sha256'",
                (store_module._P6_SCHEMA_SHA256,),  # noqa: SLF001
            )
            connection.execute("PRAGMA user_version=6")
            connection.commit()
        finally:
            connection.close()
        self.store = LifeShadowStore.open(self.path, create=False, now_ms=3_000)
        health = self.store.health()
        self.assertEqual(health["schema_version"], SHADOW_STORE_SCHEMA_VERSION)
        self.assertGreaterEqual(health["strict_table_count"], 49)

    def test_revision_drift_before_commit_fails_closed(self) -> None:
        original = self.store.build_revision_vector
        reads = 0

        def drift(*args, **kwargs):
            nonlocal reads
            reads += 1
            value = original(*args, **kwargs)
            if reads == 1:
                return value
            return value.model_copy(
                update={"causal_revision": value.causal_revision + 1}
            ).with_computed_vector_sha256()

        self.store.build_revision_vector = drift  # type: ignore[method-assign]
        try:
            with self.assertRaisesRegex(
                LifeContextAuthorityError, "authorization commit failed"
            ):
                self.compile()
        finally:
            self.store.build_revision_vector = original  # type: ignore[method-assign]
        self.assertIsNone(
            self.store.get_context_authorization(
                self.continuity.request_id,
                run_id=self.continuity.run_id,
                generation=self.continuity.generation,
            )
        )

    def test_api_and_gateway_client_use_one_bound_call(self) -> None:
        request_id = "req_" + "7" * 64
        run_id = "run_" + "8" * 64
        api = LifeContextCompileAuthorizeApi(self.store)
        projection = LifeProjectionInputs(
            life_id="life_contract_test",
            writer_epoch=3,
            identity_revision=3,
            soul={
                "life_id": "life_contract_test",
                "revision": 2,
                "revision_id": "soul_revision_test",
                "name": "起源",
                "prompt": "这是由生命权威签名的人格底稿。",
            },
            capabilities={"active_skills": ["skill.document"]},
        )

        class AtomicTransport:
            def __init__(self):
                self.requests = []

            def get_json(self, _path):
                raise AssertionError("atomic snapshot must not issue GET requests")

            def post_json(self, path, payload):
                self.requests.append((path, dict(payload)))
                return api.compile_and_authorize(payload, projection)

        transport = AtomicTransport()
        objects = ContentAddressedObjectStore.open(
            Path(self.temporary.name) / "objects", now_ms=100
        )
        try:
            pinned = LifeClient(transport, objects).compile_and_authorize_snapshot(
                request_id=request_id,
                run_id=run_id,
                generation=1,
                current_request="第一次对话直接编译。",
                tenant_id="desktop",
                link_account_id="local-user",
                conversation_scope_hash="c" * 64,
                profile=LifeProfileBindings(
                    user_callsign="夏平", user_avatar_ref="avatar_user"
                ),
                observed_at_ms=2_000,
            )
            class NoSecondLifeCall:
                def request(self, *_args, **_kwargs):
                    raise AssertionError("atomic projection must be consumed locally")

            compatibility = object.__new__(FrozenBackendCompatibilityTransport)
            compatibility._objects = objects  # noqa: SLF001
            compatibility._life = NoSecondLifeCall()  # noqa: SLF001
            compatibility._on_context_compaction = None  # noqa: SLF001
            ticket = SimpleNamespace(
                payload=SimpleNamespace(
                    life_snapshot_hash=pinned.snapshot.sha256,
                    life_snapshot_revision=pinned.snapshot.revision,
                    request_id=request_id,
                    run_id=run_id,
                    generation=1,
                    channel="desktop",
                )
            )
            with patch(
                "total_gateway.frozen_backend_compat.time.time_ns",
                return_value=2_001_000_000,
            ):
                prepared = compatibility._prepare_life(  # noqa: SLF001
                    ticket,
                    {
                        "life_snapshot": pinned.snapshot.model_dump(mode="python"),
                        "text": "第一次对话直接编译。",
                        "recent_messages": [],
                    },
                )
        finally:
            objects.close()
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(
            transport.requests[0][0],
            "/api/v1/v3/life/context/compile-and-authorize",
        )
        measured_context_tokens = transport.requests[0][1]["current_context_tokens"]
        self.assertGreater(measured_context_tokens, 0)
        persisted_context_pack = self.store.get_latest_causal_context_pack(
            request_id,
            run_id=run_id,
            generation=1,
        )
        self.assertIsNotNone(persisted_context_pack)
        self.assertEqual(
            persisted_context_pack.token_budget.current_context_tokens,
            measured_context_tokens,
        )
        self.assertEqual(pinned.snapshot.identity_ref, "life_contract_test")
        self.assertEqual(pinned.snapshot.user_callsign, "夏平")
        self.assertEqual(pinned.snapshot.causal_revision, 0)
        self.assertIsNotNone(pinned.context_authorization_id)
        self.assertIsNotNone(pinned.revision_vector_sha256)
        self.assertEqual(prepared["cycle_id"], pinned.context_authorization_id)
        self.assertEqual(prepared["context_envelope"]["soul"]["prompt"], "这是由生命权威签名的人格底稿。")

        with self.assertRaises(LifeContextApiError):
            api.compile_and_authorize(
                {**transport.requests[0][1], "unknown": True}, projection
            )


if __name__ == "__main__":
    unittest.main()
