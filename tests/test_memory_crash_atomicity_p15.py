"""P15 M2: LifeEvent->L1 commits are crash-atomic and retry-recoverable."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from life_service.memory_coordinator import (
    MemoryCoordinator,
    l1_derivation_id,
    l1_memory_id,
)
from life_service.store import LifeShadowStore, LifeShadowStoreError
from tests.life_contract_support import event


class MemoryCrashAtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "crash.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_assertion_and_derivation_roll_back_together_twenty_rounds(self) -> None:
        for index in range(1, 21):
            value = event(1, None, suffix=f"{index:064x}")
            with mock.patch.object(
                self.store._memory_repository,
                "_put_memory_derivation_locked",
                side_effect=LifeShadowStoreError("simulated crash"),
            ):
                with self.assertRaises(LifeShadowStoreError):
                    self.coordinator.commit_life_event_l1(
                        value, event_payload=b"payload"
                    )
            # Nothing was persisted for the failed commit.
            derivation = self.store.get_memory_derivation(
                l1_derivation_id(
                    life_id=value.life_id, source_event_id=value.event_id
                )
            )
            self.assertIsNone(derivation)
            memory_id = l1_memory_id(
                life_id=value.life_id, source_event_id=value.event_id
            )
            self.assertIsNone(self.store.get_latest_memory_assertion(memory_id))
            # Retry after the crash succeeds exactly once.
            _a, _d, created = self.coordinator.commit_life_event_l1(
                value, event_payload=b"payload"
            )
            self.assertTrue(created)

    def test_recovery_creates_missing_l1_for_existing_assertion(self) -> None:
        value = event(1, None)
        memory_id = l1_memory_id(
            life_id=value.life_id, source_event_id=value.event_id
        )
        assertion, _seq, _created = self.store.put_live_memory_assertion(
            b"payload",
            memory_id=memory_id,
            life_id=value.life_id,
            assertion_kind="observation",
            epistemic_status="observed",
            lifecycle_status="active",
            privacy_scope=value.privacy_scope,
            retention_class="ACTIVE_WORKING",
            source_event_ids=(value.event_id,),
            valid_from_ms=value.observed_at_ms,
            created_at_ms=value.observed_at_ms,
        )
        # Simulated crash window: assertion committed, derivation missing.
        self.assertIsNone(
            self.store.find_derivation(
                memory_id=memory_id,
                memory_revision=1,
                layer="L1_STREAM",
            )
        )
        self.coordinator._ensure_l1_derivation(
            assertion=assertion,
            source_event_id=value.event_id,
            principal_ref=value.principal_ref,
        )
        derivation = self.store.find_derivation(
            memory_id=memory_id,
            memory_revision=1,
            layer="L1_STREAM",
        )
        self.assertIsNotNone(derivation)
        self.assertEqual(
            derivation.memory_assertion_sha256, assertion.assertion_sha256
        )
        # Replaying recovery is idempotent.
        self.coordinator._ensure_l1_derivation(
            assertion=assertion,
            source_event_id=value.event_id,
            principal_ref=value.principal_ref,
        )
        self.assertEqual(
            len(self.store.list_memory_derivations(life_id=value.life_id)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
