"""P15 M3: explicit L4 detection and coordinator closure."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.explicit_memory import (
    detect_explicit_intent,
    expiry_deadline_ms,
)
from life_service.memory_coordinator import (
    MemoryCoordinator,
    MemoryCoordinatorError,
)
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


class ExplicitIntentDetectionTests(unittest.TestCase):
    def test_explicit_patterns_are_detected(self) -> None:
        cases = {
            "记住，地球是平的。": {"explicit_remember"},
            "以后记得每天备份。": {"future_remember"},
            "请长期保存这条规则。": {"long_term_remember"},
            "不要忘记这个邮箱。": {"do_not_forget"},
            "我的长期偏好是简洁。": {"long_term_preference"},
            "以后一直用中文回复。": {"ongoing_behavior"},
        }
        for text, expected in cases.items():
            result = detect_explicit_intent(text)
            self.assertTrue(result.triggered, text)
            self.assertTrue(expected.issubset(set(result.reason_codes)))
            self.assertEqual(result.span_text, text)

    def test_plain_turn_is_not_explicit(self) -> None:
        for text in ("请解释一下这个设计？", "好的，我明白了。", "今天天气如何？"):
            result = detect_explicit_intent(text)
            self.assertFalse(result.triggered, text)

    def test_expiry_kinds_map_to_deadlines(self) -> None:
        self.assertEqual(expiry_deadline_ms("today", 1_000), 86_400_000)
        self.assertEqual(
            expiry_deadline_ms("this_session", 1_000), 1_000 + 86_400_000
        )
        self.assertEqual(
            expiry_deadline_ms("temporary", 1_000), 1_000 + 86_400_000
        )
        self.assertEqual(
            expiry_deadline_ms("this_turn", 1_000), 1_000 + 3_600_000
        )
        self.assertIsNone(expiry_deadline_ms(None, 1_000))

    def test_today_expiry_detected(self) -> None:
        result = detect_explicit_intent("今天先叫我小A。")
        self.assertTrue(result.triggered)
        self.assertEqual(result.expiry_kind, "today")

    def test_span_hash_is_deterministic(self) -> None:
        first = detect_explicit_intent("记住这个。")
        second = detect_explicit_intent("记住这个。")
        self.assertEqual(first.span_sha256, second.span_sha256)


class ExplicitL4CoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "l4.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)
        self.user_event = event(1, None)
        self.l1_assertion, self.l1, _created = (
            self.coordinator.commit_life_event_l1(
                self.user_event, event_payload=b"user message"
            )
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _explicit(
        self,
        *,
        text: str,
        plaintext: bytes,
        claim_key: str,
        semantic_domain: str = "USER_PREFERENCE",
        created_at_ms: int = 3_000,
    ):
        return self.coordinator.commit_user_explicit(
            l1_parent_derivation_id=self.l1.derivation_id,
            user_message_event_id=self.user_event.event_id,
            life_id=self.user_event.life_id,
            principal_ref=self.user_event.principal_ref,
            privacy_scope=self.user_event.privacy_scope,
            user_text=text,
            plaintext=plaintext,
            created_at_ms=created_at_ms,
            claim_key=claim_key,
            semantic_domain=semantic_domain,
        )

    def test_remember_flat_earth_is_l4_user_asserted_not_verified(self) -> None:
        assertion, derivation, detection, created = self._explicit(
            text="记住，地球是平的。",
            plaintext=b"earth is flat",
            claim_key="claim:earth",
            semantic_domain="WORLD",
        )
        self.assertTrue(created)
        self.assertTrue(detection.triggered)
        self.assertEqual(derivation.layer, "L4_EXPLICIT")
        self.assertEqual(derivation.origin, "USER_EXPLICIT")
        self.assertEqual(derivation.semantic_domain, "WORLD")
        self.assertEqual(assertion.epistemic_status, "user_asserted")
        self.assertNotEqual(assertion.epistemic_status, "verified")
        self.assertTrue(derivation.world_candidate_eligible)
        self.assertTrue(derivation.context_eligible)
        self.assertFalse(derivation.temperament_eligible)
        self.assertEqual(
            derivation.source_event_ids, (self.user_event.event_id,)
        )
        parents = self.store.list_derivation_parents(derivation.derivation_id)
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0].parent_derivation_id, self.l1.derivation_id)
        head = self.store.get_active_memory_head(
            life_id=self.user_event.life_id,
            principal_ref=self.user_event.principal_ref,
            claim_key="claim:earth",
            layer="L4_EXPLICIT",
        )
        self.assertEqual(head.derivation_id, derivation.derivation_id)

    def test_explicit_l4_is_idempotent(self) -> None:
        first = self._explicit(
            text="记住，地球是平的。",
            plaintext=b"earth is flat",
            claim_key="claim:earth",
            semantic_domain="WORLD",
        )
        second = self._explicit(
            text="记住，地球是平的。",
            plaintext=b"earth is flat",
            claim_key="claim:earth",
            semantic_domain="WORLD",
        )
        self.assertTrue(first[3])
        self.assertFalse(second[3])
        self.assertEqual(first[1].derivation_id, second[1].derivation_id)

    def test_today_alias_gets_expiry_and_cannot_promote_to_l5(self) -> None:
        assertion, derivation, detection, created = self._explicit(
            text="今天先叫我小A。",
            plaintext=b"call me A today",
            claim_key="claim:alias",
            semantic_domain="USER_PREFERENCE",
            created_at_ms=4_000,
        )
        self.assertTrue(created)
        self.assertEqual(detection.expiry_kind, "today")
        self.assertIsNotNone(derivation.expires_at_ms)
        self.assertIsNotNone(assertion.expires_at_ms)
        promoted = self.coordinator.promote_to_l5(
            life_id=self.user_event.life_id,
            principal_ref=self.user_event.principal_ref,
            privacy_scope=self.user_event.privacy_scope,
            candidate_derivation_ids=(derivation.derivation_id,),
            claim_key="claim:alias",
            semantic_domain="USER_PREFERENCE",
            plaintext=b"call me A forever",
            created_at_ms=5_000,
            support_weights={derivation.derivation_id: 750},
            counter_weights={},
            recurrence_count=0,
        )
        self.assertIsNone(promoted)

    def test_non_explicit_span_is_rejected(self) -> None:
        with self.assertRaises(MemoryCoordinatorError):
            self._explicit(
                text="请解释一下这个设计？",
                plaintext=b"explain",
                claim_key="claim:plain",
            )

    def test_l4_requires_existing_l1_parent(self) -> None:
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.commit_user_explicit(
                l1_parent_derivation_id="mdr_" + "0" * 64,
                user_message_event_id=self.user_event.event_id,
                life_id=self.user_event.life_id,
                principal_ref=self.user_event.principal_ref,
                privacy_scope=self.user_event.privacy_scope,
                user_text="记住这个。",
                plaintext=b"remember",
                created_at_ms=3_000,
                claim_key="claim:missing-parent",
            )


if __name__ == "__main__":
    unittest.main()
