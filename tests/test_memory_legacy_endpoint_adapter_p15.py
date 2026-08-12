"""P15 M2: legacy /memory endpoint adapter keeps API-compatible semantics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore, LifeShadowStoreError
from tests.life_contract_support import event


LIFE = "life_p15_adapter"
MEMORY_ID = "mem_" + "ab" * 32
EVENT_ID = "lev_" + "cd" * 32


class LegacyEndpointAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "adapter.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _record(self, **overrides):
        values = dict(
            plaintext=b"user fact payload",
            memory_id=MEMORY_ID,
            life_id=LIFE,
            principal_ref=LIFE,
            assertion_kind="user_preference",
            epistemic_status="user_asserted",
            lifecycle_status="active",
            privacy_scope="private",
            retention_class="LONG_TERM_MEMORY",
            source_event_ids=(EVENT_ID,),
            causal_utility_milli=0,
            user_importance_milli=900,
            verification_strength_milli=750,
            future_dependency_milli=0,
            valid_from_ms=1_000,
            created_at_ms=2_000,
        )
        values.update(overrides)
        return values

    def test_adapter_returns_same_tuple_shape_as_legacy_store_call(self) -> None:
        assertion, change_seq, created = (
            self.coordinator.commit_contract_assertion(**self._record())
        )
        self.assertTrue(created)
        self.assertGreaterEqual(change_seq, 1)
        self.assertEqual(assertion.memory_id, MEMORY_ID)
        self.assertEqual(assertion.life_id, LIFE)

    def test_duplicate_assert_is_idempotent_with_same_seq(self) -> None:
        _a1, seq1, created1 = self.coordinator.commit_contract_assertion(
            **self._record()
        )
        _a2, seq2, created2 = self.coordinator.commit_contract_assertion(
            **self._record()
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(seq1, seq2)

    def test_content_change_on_same_memory_fails_closed(self) -> None:
        _a1, _seq1, created1 = self.coordinator.commit_contract_assertion(
            **self._record()
        )
        self.assertTrue(created1)
        with self.assertRaises(LifeShadowStoreError):
            self.coordinator.commit_contract_assertion(
                **self._record(plaintext=b"revised payload")
            )

    def test_adapter_attaches_l1_derivation_to_new_assertion(self) -> None:
        assertion, _seq, created = self.coordinator.commit_contract_assertion(
            **self._record()
        )
        self.assertTrue(created)
        derivation = self.store.find_derivation(
            memory_id=assertion.memory_id,
            memory_revision=assertion.revision,
            layer="L1_STREAM",
        )
        self.assertIsNotNone(derivation)
        self.assertEqual(derivation.source_event_ids, (EVENT_ID,))
        self.assertEqual(derivation.memory_assertion_sha256, assertion.assertion_sha256)

    def test_status_change_advances_revision_without_second_l1_slot(self) -> None:
        _a1, _seq1, created1 = self.coordinator.commit_contract_assertion(
            **self._record()
        )
        _a2, _seq2, created2 = self.coordinator.commit_contract_assertion(
            **self._record(lifecycle_status="superseded")
        )
        self.assertTrue(created1)
        self.assertTrue(created2)
        latest = self.store.get_latest_memory_assertion(MEMORY_ID)
        self.assertEqual(latest.revision, 2)
        self.assertEqual(latest.lifecycle_status, "superseded")
        derivations = self.store.list_memory_derivations(
            life_id=LIFE, layer="L1_STREAM"
        )
        self.assertEqual(len(derivations), 1)

    def test_adapter_rejects_invalid_life_id_like_legacy_path(self) -> None:
        with self.assertRaises((LifeShadowStoreError, ValueError)):
            self.coordinator.commit_contract_assertion(
                **self._record(life_id="bad id!")
            )

    def test_endpoint_paths_still_routed_unchanged(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "life_service"
            / "embedded_runtime.py"
        ).read_text(encoding="utf-8")
        for path in (
            "/api/v1/v3/life/memory/assert",
            "/api/v1/v3/life/memory/turn",
            "/api/v1/v3/life/memory/correct",
        ):
            self.assertIn(path, text)


if __name__ == "__main__":
    unittest.main()
