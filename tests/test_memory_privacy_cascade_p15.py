"""P15 M5: invalidated/expired/foreign memories never reach Context."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_context import select_layered_memories
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_privacy"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class MemoryPrivacyCascadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "privacy.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _explicit(
        self,
        *,
        suffix: str,
        text: str,
        plaintext: bytes,
        claim_key: str,
        semantic_domain: str = "USER_PREFERENCE",
        created_at_ms: int = 2_000,
    ):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _a1, l1, _c = self.coordinator.commit_life_event_l1(value)
        _a4, l4, _det, created = self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=l1.derivation_id,
            user_message_event_id=value.event_id,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            user_text=text,
            plaintext=plaintext,
            created_at_ms=created_at_ms,
            claim_key=claim_key,
            semantic_domain=semantic_domain,
        )
        self.assertTrue(created)
        return value, l4

    def test_corrected_memory_is_excluded_from_context(self) -> None:
        value, l4 = self._explicit(
            suffix="21" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            claim_key="claim:lang",
        )
        correction_event = event(1, None, life_id=LIFE, suffix="22" * 32)
        self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            target_derivation_id=l4.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected",
            created_at_ms=3_000,
        )
        instruction, _data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=4_000,
        )
        self.assertFalse(
            any(
                item.derivation_id == l4.derivation_id
                for item in instruction
            )
        )

    def test_invalidated_descendant_is_excluded(self) -> None:
        value, l4 = self._explicit(
            suffix="23" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"always chinese",
            claim_key="claim:lang-cascade",
        )
        correction_event = event(1, None, life_id=LIFE, suffix="24" * 32)
        self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            target_derivation_id=l4.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected",
            created_at_ms=3_000,
        )
        self.assertFalse(self.store.is_derivation_active(l4.derivation_id))

    def test_expired_l4_is_excluded(self) -> None:
        _value, l4 = self._explicit(
            suffix="25" * 32,
            text="今天先叫我C。",
            plaintext=b"call me C today",
            claim_key="claim:alias-c",
            created_at_ms=2_000,
        )
        instruction, _data, _evidence, skipped = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=200_000_000,
        )
        self.assertFalse(
            any(
                item.derivation_id == l4.derivation_id
                for item in instruction
            )
        )
        self.assertGreaterEqual(skipped, 1)

    def test_privacy_scope_mismatch_excluded(self) -> None:
        self._explicit(
            suffix="26" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"private preference",
            claim_key="claim:private",
        )
        _instruction, data, _evidence, skipped = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope="other_scope",
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)
        self.assertGreaterEqual(skipped, 1)

    def test_foreign_principal_excluded(self) -> None:
        self._explicit(
            suffix="27" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"alice preference",
            claim_key="claim:alice",
        )
        _instruction, data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref="principal_bob",
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)

    def test_only_active_derivations_selected(self) -> None:
        _value, l4 = self._explicit(
            suffix="28" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"active preference",
            claim_key="claim:active",
        )
        correction_event = event(1, None, life_id=LIFE, suffix="29" * 32)
        self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            target_derivation_id=l4.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected",
            created_at_ms=3_000,
        )
        _instruction, data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=4_000,
        )
        self.assertEqual(len(data), 0)

    def test_active_preference_still_selected(self) -> None:
        _value, l4 = self._explicit(
            suffix="30" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"active preference",
            claim_key="claim:active-ok",
        )
        instruction, _data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertTrue(
            any(
                item.derivation_id == l4.derivation_id
                for item in instruction
            )
        )

    def test_skipped_counts_each_scope_violation(self) -> None:
        for index in range(3):
            self._explicit(
                suffix=f"{31 + index:02d}" * 32,
                text="记住，以后一直用中文。",
                plaintext=b"preference",
                claim_key=f"claim:violation-{index}",
            )
        _instruction, data, _evidence, skipped = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref="principal_other",
            privacy_scope=PRIVACY,
            now_ms=3_000,
        )
        self.assertEqual(len(data), 0)
        self.assertGreaterEqual(skipped, 3)

    def test_skipped_counts_invalidated_derivations(self) -> None:
        _value, l4 = self._explicit(
            suffix="40" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"to be corrected",
            claim_key="claim:invalidate-count",
        )
        correction_event = event(1, None, life_id=LIFE, suffix="41" * 32)
        self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            target_derivation_id=l4.derivation_id,
            user_message_event_id=correction_event.event_id,
            plaintext=b"corrected",
            created_at_ms=3_000,
        )
        _i, data, _e, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=4_000,
        )
        self.assertFalse(
            any(
                item.derivation_id == l4.derivation_id
                for item in data
            )
        )

    def test_replacement_head_is_selected_after_correction(self) -> None:
        _value, l4 = self._explicit(
            suffix="42" * 32,
            text="记住，以后一直用中文。",
            plaintext=b"original preference",
            claim_key="claim:replacement",
        )
        correction_event = event(1, None, life_id=LIFE, suffix="43" * 32)
        _assertion, replacement, _invalidations, _created = (
            self.coordinator.correct_claim(
                life_id=LIFE,
                principal_ref=PRINCIPAL,
                privacy_scope=PRIVACY,
                target_derivation_id=l4.derivation_id,
                user_message_event_id=correction_event.event_id,
                plaintext=b"corrected preference",
                created_at_ms=3_000,
            )
        )
        instruction, _data, _evidence, _s = select_layered_memories(
            self.store,
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            privacy_scope=PRIVACY,
            now_ms=4_000,
        )
        self.assertTrue(
            any(
                item.derivation_id == replacement.derivation_id
                for item in instruction
            )
        )


if __name__ == "__main__":
    unittest.main()
