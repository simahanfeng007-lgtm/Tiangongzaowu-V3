"""P15 M2: LifeEvent -> L1 stream derivation (atomic, idempotent, traceable)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import (
    MemoryCoordinator,
    MemoryCoordinatorError,
    l1_derivation_id,
    l1_memory_id,
)
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_l1"

LIFE_EVENT_KINDS = (
    "user.message.observed",
    "user.feedback.observed",
    "execution.requested",
    "execution.tool.receipt",
    "execution.tool.result",
    "execution.action.observed",
    "execution.completed",
    "tool.output.observed",
    "tool.write.declared",
    "filesystem.write.observed",
    "filesystem.read.observed",
    "git.observation",
    "repository.observation",
    "network.observation",
    "web.source.claim",
    "system.health.observed",
    "system.startup.observed",
    "system.shutdown.observed",
    "life.learning.requested",
    "life.learning.result",
    "life.learning.confirmed",
    "life.learning.discarded",
    "memory.asserted",
    "memory.corrected",
    "memory.deleted",
    "reflection.completed",
    "cognition.stimulus.observed",
    "temperament.adapted",
    "relationship.observed",
    "identity.observed",
)


def chained(sequence: int, previous: str | None, *, suffix: str | None = None):
    return event(sequence, previous, suffix=suffix)


class LifeEventL1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "l1.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_thirty_life_event_kinds_all_produce_l1_stream(self) -> None:
        previous = None
        for index, kind in enumerate(LIFE_EVENT_KINDS, start=1):
            value = chained(index, previous, suffix=f"{index:064x}")
            value = value.model_copy(
                update={"event_kind": kind, "source_kind": "mixed"}
            ).with_computed_event_hash()
            previous = value.event_hash
            assertion, derivation, created = (
                self.coordinator.commit_life_event_l1(
                    value, event_payload=("payload:" + kind).encode("utf-8")
                )
            )
            self.assertTrue(created)
            self.assertEqual(derivation.layer, "L1_STREAM")
            self.assertEqual(derivation.origin, "LIFE_EVENT")
            self.assertEqual(derivation.source_event_ids, (value.event_id,))
            self.assertEqual(
                derivation.lineage_root_event_ids, (value.event_id,)
            )
            self.assertTrue(self.store.is_derivation_active(derivation.derivation_id))
            self.assertEqual(assertion.source_event_ids, (value.event_id,))
            self.assertEqual(assertion.epistemic_status, "observed")

    def test_twenty_duplicate_ingress_is_idempotent(self) -> None:
        value = chained(1, None)
        first = self.coordinator.commit_life_event_l1(
            value, event_payload=b"payload"
        )
        self.assertTrue(first[2])
        for _ in range(20):
            _assertion, _derivation, created = (
                self.coordinator.commit_life_event_l1(
                    value, event_payload=b"payload"
                )
            )
            self.assertFalse(created)
        rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations WHERE life_id = ?",
            (value.life_id,),
        ).fetchone()
        self.assertEqual(int(rows["n"]), 1)
        assertion_rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_assertions WHERE life_id = ?",
            (value.life_id,),
        ).fetchone()
        self.assertEqual(int(assertion_rows["n"]), 1)
        expected_id = l1_derivation_id(
            life_id=value.life_id,
            source_event_id=value.event_id,
        )
        self.assertEqual(first[1].derivation_id, expected_id)

    def test_l1_ids_are_deterministic_across_stores(self) -> None:
        value = chained(1, None)
        with tempfile.TemporaryDirectory() as other:
            other_path = Path(other) / "other.shadow.sqlite3"
            with LifeShadowStore.open(
                other_path, create=True, now_ms=500
            ) as other_store:
                other_coordinator = MemoryCoordinator(other_store)
                _a1, d1, _c1 = self.coordinator.commit_life_event_l1(value)
                _a2, d2, _c2 = other_coordinator.commit_life_event_l1(value)
                self.assertEqual(d1.derivation_id, d2.derivation_id)
                self.assertEqual(d1.memory_id, d2.memory_id)
                self.assertEqual(d1.source_event_ids, d2.source_event_ids)
                expected_memory = l1_memory_id(
                    life_id=value.life_id,
                    source_event_id=value.event_id,
                )
                self.assertEqual(d1.memory_id, expected_memory)

    def test_l1_assertion_payload_is_traceable_to_event(self) -> None:
        value = chained(1, None)
        assertion, _derivation, _created = self.coordinator.commit_life_event_l1(
            value, event_payload=b"event payload body"
        )
        plaintext = self.store.read_protected_payload(
            assertion.protected_payload_id
        )
        self.assertIn(b"event payload body", plaintext)
        self.assertEqual(assertion.source_event_ids, (value.event_id,))
        self.assertEqual(assertion.life_id, value.life_id)

    def test_l1_eligibility_is_context_only(self) -> None:
        value = chained(1, None)
        _assertion, derivation, _created = self.coordinator.commit_life_event_l1(
            value
        )
        self.assertTrue(derivation.context_eligible)
        self.assertFalse(derivation.learning_eligible)
        self.assertFalse(derivation.temperament_eligible)
        self.assertFalse(derivation.self_cognition_eligible)
        self.assertFalse(derivation.world_candidate_eligible)

    def test_l1_rejects_non_envelope_input(self) -> None:
        with self.assertRaises(MemoryCoordinatorError):
            self.coordinator.commit_life_event_l1({"event_id": "lev_" + "1" * 64})


if __name__ == "__main__":
    unittest.main()
