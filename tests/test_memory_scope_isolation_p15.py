"""P15 M1 scope isolation tests: principals, privacy and life never merge."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import MemoryDerivationV1, MemoryParentRef
from life_service.store import LifeShadowStore, LifeShadowStoreError


def put_assertion(
    store: LifeShadowStore,
    *,
    memory_id: str,
    created_at_ms: int,
    life_id: str,
    privacy_scope: str,
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
        source_event_ids=(),
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
    life_id: str,
    principal_ref: str,
    privacy_scope: str,
    memory_id: str,
    assertion_sha256: str,
    created_at_ms: int,
    claim_key: str,
    layer: str = "L4_EXPLICIT",
    origin: str = "USER_EXPLICIT",
    root_event: str,
    parent_refs: tuple[MemoryParentRef, ...] = (),
) -> MemoryDerivationV1:
    return MemoryDerivationV1(
        derivation_id=derivation_id,
        life_id=life_id,
        memory_id=memory_id,
        memory_revision=1,
        memory_assertion_sha256=assertion_sha256,
        layer=layer,
        semantic_domain="USER_PREFERENCE",
        origin=origin,
        principal_ref=principal_ref,
        workspace_ref=None,
        privacy_scope=privacy_scope,
        claim_key=claim_key,
        parent_memory_refs=parent_refs,
        source_event_ids=(root_event,),
        lineage_root_event_ids=(root_event,),
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
    ).with_computed_derivation_sha256()


class MemoryScopeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "p15-scope.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_same_claim_different_principals_keep_separate_heads(self) -> None:
        alice = put_assertion(
            self.store,
            memory_id="mem_" + "a1" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        bob = put_assertion(
            self.store,
            memory_id="mem_" + "b2" * 32,
            created_at_ms=1_000,
            life_id="life_bob",
            privacy_scope="privacy_bob_v1",
        )
        claim = "claim:company-hours"
        alice_head = derivation(
            derivation_id="mdr_" + "1a" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "a1" * 32,
            assertion_sha256=alice.assertion_sha256,
            created_at_ms=2_000,
            claim_key=claim,
            root_event="lev_" + "1" * 64,
        )
        bob_head = derivation(
            derivation_id="mdr_" + "2b" * 32,
            life_id="life_bob",
            principal_ref="principal_bob",
            privacy_scope="privacy_bob_v1",
            memory_id="mem_" + "b2" * 32,
            assertion_sha256=bob.assertion_sha256,
            created_at_ms=2_000,
            claim_key=claim,
            root_event="lev_" + "2" * 64,
        )
        self.assertTrue(
            self.store.put_memory_derivation(alice_head, activate_head=True)
        )
        self.assertTrue(
            self.store.put_memory_derivation(bob_head, activate_head=True)
        )
        self.assertEqual(
            self.store.get_active_memory_head(
                life_id="life_alice",
                principal_ref="principal_alice",
                claim_key=claim,
                layer="L4_EXPLICIT",
            ),
            alice_head,
        )
        self.assertEqual(
            self.store.get_active_memory_head(
                life_id="life_bob",
                principal_ref="principal_bob",
                claim_key=claim,
                layer="L4_EXPLICIT",
            ),
            bob_head,
        )
        self.assertEqual(
            len(self.store.list_active_memory_heads()), 2
        )

    def test_active_heads_filter_by_principal(self) -> None:
        alice = put_assertion(
            self.store,
            memory_id="mem_" + "c3" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        alice_head = derivation(
            derivation_id="mdr_" + "3c" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "c3" * 32,
            assertion_sha256=alice.assertion_sha256,
            created_at_ms=2_000,
            claim_key="claim:alpha",
            root_event="lev_" + "3" * 64,
        )
        self.assertTrue(
            self.store.put_memory_derivation(alice_head, activate_head=True)
        )
        self.assertEqual(
            self.store.list_active_memory_heads(
                principal_ref="principal_alice"
            ),
            (alice_head,),
        )
        self.assertEqual(
            self.store.list_active_memory_heads(
                principal_ref="principal_bob"
            ),
            (),
        )

    def test_cross_principal_parent_rejected(self) -> None:
        alice = put_assertion(
            self.store,
            memory_id="mem_" + "d4" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        bob = put_assertion(
            self.store,
            memory_id="mem_" + "e5" * 32,
            created_at_ms=1_000,
            life_id="life_bob",
            privacy_scope="privacy_bob_v1",
        )
        parent_id = "mdr_" + "4d" * 32
        parent = derivation(
            derivation_id=parent_id,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "d4" * 32,
            assertion_sha256=alice.assertion_sha256,
            created_at_ms=2_000,
            claim_key="claim:parent",
            root_event="lev_" + "4" * 64,
        )
        self.assertTrue(self.store.put_memory_derivation(parent))
        ref = MemoryParentRef(
            parent_derivation_id=parent_id,
            memory_id="mem_" + "d4" * 32,
            memory_revision=1,
            assertion_sha256=alice.assertion_sha256,
            parent_ref_sha256="0" * 64,
        ).with_computed_parent_ref_sha256()
        child = derivation(
            derivation_id="mdr_" + "5e" * 32,
            life_id="life_bob",
            principal_ref="principal_bob",
            privacy_scope="privacy_bob_v1",
            memory_id="mem_" + "e5" * 32,
            assertion_sha256=bob.assertion_sha256,
            created_at_ms=3_000,
            claim_key="claim:child",
            root_event="lev_" + "5" * 64,
            layer="L2_DIARY",
            origin="PROMOTION",
            parent_refs=(ref,),
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(child)

    def test_derivation_list_filtered_by_principal(self) -> None:
        alice = put_assertion(
            self.store,
            memory_id="mem_" + "f6" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        bob = put_assertion(
            self.store,
            memory_id="mem_" + "07" * 32,
            created_at_ms=1_000,
            life_id="life_bob",
            privacy_scope="privacy_bob_v1",
        )
        alice_rec = derivation(
            derivation_id="mdr_" + "6f" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "f6" * 32,
            assertion_sha256=alice.assertion_sha256,
            created_at_ms=2_000,
            claim_key="claim:list-a",
            root_event="lev_" + "6" * 64,
        )
        bob_rec = derivation(
            derivation_id="mdr_" + "70" * 32,
            life_id="life_bob",
            principal_ref="principal_bob",
            privacy_scope="privacy_bob_v1",
            memory_id="mem_" + "07" * 32,
            assertion_sha256=bob.assertion_sha256,
            created_at_ms=2_000,
            claim_key="claim:list-b",
            root_event="lev_" + "7" * 64,
        )
        self.store.put_memory_derivation(alice_rec)
        self.store.put_memory_derivation(bob_rec)
        self.assertEqual(
            self.store.list_memory_derivations(
                principal_ref="principal_alice"
            ),
            (alice_rec,),
        )
        self.assertEqual(
            self.store.list_memory_derivations(
                principal_ref="principal_bob"
            ),
            (bob_rec,),
        )

    def test_same_claim_same_principal_layer_has_single_active_head(self) -> None:
        public = put_assertion(
            self.store,
            memory_id="mem_" + "18" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_public_v1",
        )
        secret = put_assertion(
            self.store,
            memory_id="mem_" + "29" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_secret_v1",
        )
        claim = "claim:same-claim"
        public_rec = derivation(
            derivation_id="mdr_" + "81" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_public_v1",
            memory_id="mem_" + "18" * 32,
            assertion_sha256=public.assertion_sha256,
            created_at_ms=2_000,
            claim_key=claim,
            root_event="lev_" + "8" * 64,
        )
        secret_rec = derivation(
            derivation_id="mdr_" + "92" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_secret_v1",
            memory_id="mem_" + "29" * 32,
            assertion_sha256=secret.assertion_sha256,
            created_at_ms=2_000,
            claim_key=claim,
            root_event="lev_" + "9" * 64,
        )
        self.store.put_memory_derivation(public_rec, activate_head=True)
        self.store.put_memory_derivation(secret_rec, activate_head=True)
        self.assertEqual(
            len(self.store.list_active_memory_heads()), 1
        )
        self.assertEqual(
            self.store.get_active_memory_head(
                life_id="life_alice",
                principal_ref="principal_alice",
                claim_key=claim,
                layer="L4_EXPLICIT",
            ),
            secret_rec,
        )
        self.assertEqual(
            len(self.store.list_memory_derivations(life_id="life_alice")),
            2,
        )

    def test_derivation_assertion_privacy_must_match(self) -> None:
        assertion = put_assertion(
            self.store,
            memory_id="mem_" + "3a" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        mismatch = derivation(
            derivation_id="mdr_" + "a3" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_bob_v1",
            memory_id="mem_" + "3a" * 32,
            assertion_sha256=assertion.assertion_sha256,
            created_at_ms=2_000,
            claim_key="claim:privacy-mismatch",
            root_event="lev_" + "a" * 64,
        )
        with self.assertRaises(LifeShadowStoreError):
            self.store.put_memory_derivation(mismatch)

    def test_same_claim_same_principal_layers_keep_separate_heads(self) -> None:
        parent_assertion = put_assertion(
            self.store,
            memory_id="mem_" + "4b" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        l2_assertion = put_assertion(
            self.store,
            memory_id="mem_" + "5c" * 32,
            created_at_ms=1_100,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        l4_assertion = put_assertion(
            self.store,
            memory_id="mem_" + "6d" * 32,
            created_at_ms=1_200,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        claim = "claim:layered"
        parent_id = "mdr_" + "b4" * 32
        parent = derivation(
            derivation_id=parent_id,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "4b" * 32,
            assertion_sha256=parent_assertion.assertion_sha256,
            created_at_ms=2_000,
            claim_key="claim:layered-parent",
            root_event="lev_" + "b" * 64,
        )
        self.store.put_memory_derivation(parent)
        ref = MemoryParentRef(
            parent_derivation_id=parent_id,
            memory_id="mem_" + "4b" * 32,
            memory_revision=1,
            assertion_sha256=parent_assertion.assertion_sha256,
            parent_ref_sha256="0" * 64,
        ).with_computed_parent_ref_sha256()
        l2 = derivation(
            derivation_id="mdr_" + "c5" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "5c" * 32,
            assertion_sha256=l2_assertion.assertion_sha256,
            created_at_ms=3_000,
            claim_key=claim,
            root_event="lev_" + "b" * 64,
            layer="L2_DIARY",
            origin="PROMOTION",
            parent_refs=(ref,),
        )
        l4 = derivation(
            derivation_id="mdr_" + "d6" * 32,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "6d" * 32,
            assertion_sha256=l4_assertion.assertion_sha256,
            created_at_ms=3_000,
            claim_key=claim,
            root_event="lev_" + "c" * 64,
        )
        self.store.put_memory_derivation(l2, activate_head=True)
        self.store.put_memory_derivation(l4, activate_head=True)
        self.assertEqual(
            len(self.store.list_active_memory_heads()), 2
        )
        self.assertEqual(
            self.store.get_active_memory_head(
                life_id="life_alice",
                principal_ref="principal_alice",
                claim_key=claim,
                layer="L2_DIARY",
            ),
            l2,
        )
        self.assertEqual(
            self.store.get_active_memory_head(
                life_id="life_alice",
                principal_ref="principal_alice",
                claim_key=claim,
                layer="L4_EXPLICIT",
            ),
            l4,
        )

    def test_consumer_offsets_isolated_by_consumer_and_life(self) -> None:
        self.store.advance_memory_consumer_offset(
            "consumer-x", "life_alice", 5, updated_at_ms=1_000
        )
        self.store.advance_memory_consumer_offset(
            "consumer-x", "life_bob", 9, updated_at_ms=1_000
        )
        self.store.advance_memory_consumer_offset(
            "consumer-y", "life_alice", 2, updated_at_ms=1_000
        )
        self.assertEqual(
            self.store.get_memory_consumer_offset("consumer-x", "life_alice"), 5
        )
        self.assertEqual(
            self.store.get_memory_consumer_offset("consumer-x", "life_bob"), 9
        )
        self.assertEqual(
            self.store.get_memory_consumer_offset("consumer-y", "life_alice"), 2
        )
        self.assertEqual(
            self.store.get_memory_consumer_offset("consumer-y", "life_bob"), 0
        )

    def test_no_cross_principal_children_listing(self) -> None:
        alice = put_assertion(
            self.store,
            memory_id="mem_" + "6d" * 32,
            created_at_ms=1_000,
            life_id="life_alice",
            privacy_scope="privacy_alice_v1",
        )
        bob = put_assertion(
            self.store,
            memory_id="mem_" + "7e" * 32,
            created_at_ms=1_000,
            life_id="life_bob",
            privacy_scope="privacy_bob_v1",
        )
        parent_id = "mdr_" + "d6" * 32
        parent = derivation(
            derivation_id=parent_id,
            life_id="life_alice",
            principal_ref="principal_alice",
            privacy_scope="privacy_alice_v1",
            memory_id="mem_" + "6d" * 32,
            assertion_sha256=alice.assertion_sha256,
            created_at_ms=2_000,
            claim_key="claim:parent-only",
            root_event="lev_" + "d" * 64,
        )
        self.store.put_memory_derivation(parent)
        bob_rec = derivation(
            derivation_id="mdr_" + "e7" * 32,
            life_id="life_bob",
            principal_ref="principal_bob",
            privacy_scope="privacy_bob_v1",
            memory_id="mem_" + "7e" * 32,
            assertion_sha256=bob.assertion_sha256,
            created_at_ms=3_000,
            claim_key="claim:bob-only",
            root_event="lev_" + "e" * 64,
        )
        self.store.put_memory_derivation(bob_rec)
        self.assertEqual(
            self.store.list_derivation_children(parent_id), ()
        )
        self.assertEqual(
            self.store.list_derivation_parents(bob_rec.derivation_id), ()
        )


if __name__ == "__main__":
    unittest.main()
