"""P15 M5: context selection never crosses principal or privacy scopes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_context import select_layered_memories
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE_A = "life_principal_a"
LIFE_B = "life_principal_b"
PRIVACY_A = "privacy_a_v1"
PRIVACY_B = "privacy_b_v1"


class MemoryContextPrincipalIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "principal.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _explicit(
        self,
        *,
        life_id: str,
        principal_ref: str,
        privacy_scope: str,
        suffix: str,
        text: str,
        plaintext: bytes,
        claim_key: str,
    ):
        value = event(1, None, life_id=life_id, suffix=suffix)
        if value.principal_ref != principal_ref:
            value = value.model_copy(
                update={
                    "principal_ref": principal_ref,
                    "privacy_scope": privacy_scope,
                }
            ).with_computed_event_hash()
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _a4, l4, _det, created = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=life_id,
            principal_ref=principal_ref,
            privacy_scope=privacy_scope,
            user_text=text,
            plaintext=plaintext,
            created_at_ms=2_000,
            claim_key=claim_key,
            semantic_domain="USER_PREFERENCE",
        )
        self.assertTrue(created)
        return l4

    def test_alice_memory_never_appears_for_bob(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a1" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice preference",
            claim_key="claim:alice-lang",
        )
        _instruction, data, _evidence, skipped = select_layered_memories(
            self.store,
            life_id=LIFE_A,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY_A,
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)
        self.assertGreaterEqual(skipped, 1)

    def test_bob_sees_only_his_own_scope(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a2" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice preference",
            claim_key="claim:alice-lang",
        )
        bob = self._explicit(
            life_id=LIFE_B,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY_B,
            suffix="b2" * 32,
            text="记住，以后一直用英文。",
            plaintext=b"bob preference",
            claim_key="claim:bob-lang",
        )
        instruction, _data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE_B,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY_B,
            now_ms=3_000,
        )
        self.assertEqual(len(instruction), 1)
        self.assertEqual(instruction[0].derivation_id, bob.derivation_id)

    def test_privacy_scope_mismatch_is_excluded(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a3" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice preference",
            claim_key="claim:alice-lang",
        )
        _instruction, data, _evidence, skipped = select_layered_memories(
            self.store,
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_B,
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)
        self.assertGreaterEqual(skipped, 1)

    def test_same_claim_different_principals_never_merge(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a4" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice language",
            claim_key="claim:language",
        )
        self._explicit(
            life_id=LIFE_B,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY_B,
            suffix="b4" * 32,
            text="记住，以后一直用英文。",
            plaintext=b"bob language",
            claim_key="claim:language",
        )
        alice_selection = select_layered_memories(
            self.store,
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            now_ms=3_000,
        )
        bob_selection = select_layered_memories(
            self.store,
            life_id=LIFE_B,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY_B,
            now_ms=3_000,
        )
        self.assertEqual(len(alice_selection[0]), 1)
        self.assertEqual(len(bob_selection[0]), 1)
        self.assertNotEqual(
            alice_selection[0][0].memory_id,
            bob_selection[0][0].memory_id,
        )

    def test_other_life_is_not_listed(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a5" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice preference",
            claim_key="claim:alice-lang",
        )
        _i, data, _e, _s = select_layered_memories(
            self.store,
            life_id="life_unrelated",
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)

    def test_no_cross_principal_instruction_leak(self) -> None:
        alice = self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a6" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice rule",
            claim_key="claim:alice-rule",
        )
        bob_selection = select_layered_memories(
            self.store,
            life_id=LIFE_A,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY_A,
            now_ms=3_000,
        )
        self.assertFalse(
            any(
                item.derivation_id == alice.derivation_id
                for section in bob_selection[:3]
                for item in section
            )
        )

    def test_skipped_counts_foreign_scope_records(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a7" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice preference",
            claim_key="claim:alice-lang",
        )
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY_B,
            suffix="b7" * 32,
            text="记住，以后一直用英文。",
            plaintext=b"bob preference",
            claim_key="claim:bob-lang",
        )
        _i, data, _e, skipped = select_layered_memories(
            self.store,
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)
        self.assertGreaterEqual(skipped, 1)

    def test_life_a_and_life_b_are_totally_isolated(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a8" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice only",
            claim_key="claim:alice-only",
        )
        _i, data_a, _e, _s = select_layered_memories(
            self.store,
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            now_ms=3_000,
        )
        _i2, data_b, _e2, _s2 = select_layered_memories(
            self.store,
            life_id=LIFE_B,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            now_ms=3_000,
        )
        self.assertEqual(len(data_a), 0)
        self.assertEqual(len(data_b), 0)
        alice_l4 = self.store.list_memory_derivations(
            life_id=LIFE_A, layer="L4_EXPLICIT"
        )
        self.assertEqual(len(alice_l4), 1)

    def test_privacy_mismatch_same_principal_excluded(self) -> None:
        self._explicit(
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope=PRIVACY_A,
            suffix="a9" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice preference",
            claim_key="claim:alice-privacy",
        )
        _i, data, _e, skipped = select_layered_memories(
            self.store,
            life_id=LIFE_A,
            principal_ref="principal_alice",
            privacy_scope="privacy_c_v1",
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)
        self.assertGreaterEqual(skipped, 1)


if __name__ == "__main__":
    unittest.main()
