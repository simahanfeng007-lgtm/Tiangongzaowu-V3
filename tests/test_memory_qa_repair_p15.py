"""P15 QA repair regression: correction recovery and temporary-memory expiry."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from life_service.explicit_memory import expiry_deadline_ms
from life_service.memory_coordinator import MemoryCoordinator
from life_service.memory_invalidation import invalidate_cascade
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_qa_repair"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class P15MemoryQARepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "repair.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _l2(self, suffix: str = "01" * 32):
        value = event(1, None, life_id=LIFE, suffix=suffix)
        _assertion, l1, _created = self.coordinator.commit_life_event_l1(value)
        l2 = self.coordinator.promote_l1_to_l2(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l1_derivation_ids=(l1.derivation_id,),
            claim_key="claim:repair:diary",
            semantic_domain="SYSTEM",
            plaintext=b"diary",
            created_at_ms=2_000,
        )
        self.assertIsNotNone(l2)
        return value, l1, l2[1]

    def test_attach_explicit_l4_carries_expiry(self) -> None:
        value = event(1, None, life_id=LIFE)
        _assertion, l1, _created = self.coordinator.commit_life_event_l1(value)
        created_at_ms = 86_400_000 + 1_000
        l4 = self.coordinator.attach_explicit_l4(
            life_id=LIFE,
            memory_id=l1.memory_id,
            user_text="今天先记住，我叫临时名。",
            created_at_ms=created_at_ms,
            principal_ref=value.principal_ref,
        )
        self.assertIsNotNone(l4)
        self.assertEqual(
            l4.expires_at_ms,
            expiry_deadline_ms("today", created_at_ms),
        )

    def test_runtime_correction_event_does_not_alias_l1_and_invalidates_target(self) -> None:
        event_a = "lev_" + "a" * 64
        event_b = "lev_" + "b" * 64
        target_id = "mem_" + "1" * 64
        replacement_id = "mem_" + "2" * 64
        common = dict(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            assertion_kind="user_preference",
            epistemic_status="user_asserted",
            privacy_scope=PRIVACY,
            retention_class="LONG_TERM_MEMORY",
            valid_from_ms=1_000,
        )
        self.coordinator.commit_contract_assertion(
            plaintext=b"target",
            memory_id=target_id,
            lifecycle_status="active",
            source_event_ids=(event_a,),
            created_at_ms=1_000,
            **common,
        )
        target_l1 = self.store.find_derivation(
            memory_id=target_id, memory_revision=1, layer="L1_STREAM"
        )
        self.assertIsNotNone(target_l1)
        self.coordinator.commit_contract_assertion(
            plaintext=b"replacement",
            memory_id=replacement_id,
            lifecycle_status="active",
            source_event_ids=(event_b,),
            created_at_ms=2_000,
            **common,
        )
        replacement_l1 = self.store.find_derivation(
            memory_id=replacement_id, memory_revision=1, layer="L1_STREAM"
        )
        self.assertIsNotNone(replacement_l1)

        _assertion, _seq, created = self.coordinator.commit_contract_assertion(
            plaintext=b"target",
            memory_id=target_id,
            lifecycle_status="corrected",
            source_event_ids=(event_b,),
            created_at_ms=3_000,
            **common,
        )
        self.assertTrue(created)
        self.assertFalse(self.store.is_derivation_active(target_l1.derivation_id))
        self.assertTrue(self.store.is_derivation_active(replacement_l1.derivation_id))
        latest = self.store.get_latest_memory_assertion(target_id)
        self.assertEqual(latest.lifecycle_status, "corrected")

        _assertion2, _seq2, created_again = self.coordinator.commit_contract_assertion(
            plaintext=b"target",
            memory_id=target_id,
            lifecycle_status="corrected",
            source_event_ids=(event_b,),
            created_at_ms=3_000,
            **common,
        )
        self.assertFalse(created_again)

    def test_correct_claim_recovers_after_replacement_commit(self) -> None:
        value, _l1, l2 = self._l2()
        correction_event = "lev_" + "c" * 64
        with mock.patch(
            "life_service.memory_coordinator.invalidate_cascade",
            side_effect=RuntimeError("simulated crash after replacement"),
        ):
            with self.assertRaises(RuntimeError):
                self.coordinator.correct_claim(
                    life_id=LIFE,
                    principal_ref=value.principal_ref,
                    privacy_scope=value.privacy_scope,
                    target_derivation_id=l2.derivation_id,
                    user_message_event_id=correction_event,
                    plaintext=b"corrected diary",
                    created_at_ms=3_000,
                )
        self.assertTrue(self.store.is_derivation_active(l2.derivation_id))

        assertion, replacement, invalidations, created = self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l2.derivation_id,
            user_message_event_id=correction_event,
            plaintext=b"corrected diary",
            created_at_ms=3_000,
        )
        self.assertFalse(created)
        self.assertTrue(invalidations)
        self.assertFalse(self.store.is_derivation_active(l2.derivation_id))
        self.assertTrue(self.store.is_derivation_active(replacement.derivation_id))
        self.assertEqual(assertion.memory_id, replacement.memory_id)

    def test_partial_cascade_resumes_from_inactive_root(self) -> None:
        value, _l1, l2 = self._l2("11" * 32)
        l3 = self.coordinator.promote_l2_to_l3(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            l2_derivation_ids=(l2.derivation_id,),
            claim_key="claim:repair",
            semantic_domain="SYSTEM",
            plaintext=b"experience",
            created_at_ms=3_000,
            support_weights={l2.derivation_id: 1000},
            counter_weights={},
            causal_utility_milli={l2.derivation_id: 800},
            recurrence_count=2,
        )
        self.assertIsNotNone(l3)
        child = l3[1]
        original = self.store.put_memory_invalidation
        calls = {"n": 0}

        def flaky(record):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated cascade crash")
            return original(record)

        with mock.patch.object(
            self.store, "put_memory_invalidation", side_effect=flaky
        ):
            with self.assertRaises(RuntimeError):
                invalidate_cascade(
                    self.store,
                    derivation_id=l2.derivation_id,
                    reason="corrected",
                    invalidated_at_ms=4_000,
                )
        self.assertFalse(self.store.is_derivation_active(l2.derivation_id))
        self.assertTrue(self.store.is_derivation_active(child.derivation_id))

        resumed = invalidate_cascade(
            self.store,
            derivation_id=l2.derivation_id,
            reason="corrected",
            invalidated_at_ms=4_000,
        )
        self.assertTrue(resumed)
        self.assertFalse(self.store.is_derivation_active(child.derivation_id))

    def test_correct_claim_preserves_replacement_from_same_cascade(self) -> None:
        value, _l1, l2 = self._l2("21" * 32)
        _assertion, replacement, _invalidations, _created = self.coordinator.correct_claim(
            life_id=LIFE,
            principal_ref=value.principal_ref,
            privacy_scope=value.privacy_scope,
            target_derivation_id=l2.derivation_id,
            user_message_event_id="lev_" + "d" * 64,
            plaintext=b"corrected diary",
            created_at_ms=3_000,
        )
        self.assertFalse(self.store.is_derivation_active(l2.derivation_id))
        self.assertTrue(self.store.is_derivation_active(replacement.derivation_id))


class _FakeLife:
    def __init__(self, rows):
        self.rows = rows

    def _active(self):
        return {"life_id": LIFE}

    def request(self, verb, path, payload):
        return 200, {"ok": True, "results": self.rows}, None


class _FakeRuntime:
    def __init__(self, rows):
        self.life_service = _FakeLife(rows)


class GatewayExpiryRepairTests(unittest.TestCase):
    def test_expired_temporary_memory_is_not_reinjected(self) -> None:
        from total_gateway.runtime import _gateway_p15_memory_recall

        rows = [
            {
                "content": {"text": "暂时记住，我叫临时名。"},
                "created_at": "2026-08-01T00:00:00Z",
            },
            {
                "content": {"text": "记住，我叫长期名。"},
                "created_at": "2026-08-01T00:00:00Z",
            },
        ]
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        with mock.patch(
            "total_gateway.runtime.time.time_ns",
            return_value=int(now.timestamp() * 1_000_000_000),
        ):
            result = _gateway_p15_memory_recall(_FakeRuntime(rows), "我叫什么？")
        self.assertNotIn("临时名", result)
        self.assertIn("长期名", result)

    def test_unparseable_temporary_timestamp_fails_closed(self) -> None:
        from total_gateway.runtime import _gateway_p15_memory_recall

        rows = [
            {
                "content": {"text": "这次记住，我叫会话名。"},
                "created_at": "",
            }
        ]
        result = _gateway_p15_memory_recall(_FakeRuntime(rows), "我叫什么？")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
