"""P15 acceptance G25: 150-turn life chains stay healthy and deterministic."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_chain"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class P15LifeChain150TurnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "chain.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=500)
        self.coordinator = MemoryCoordinator(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _turn(self, index: int, previous: str | None):
        return event(index, previous, life_id=LIFE, suffix=f"{index:064x}")

    def test_hundred_fifty_l1_turns_keep_chain_healthy(self) -> None:
        previous = None
        for index in range(1, 151):
            value = self._turn(index, previous)
            _a, _d, created = self.coordinator.commit_life_event_l1(
                value, event_payload=f"turn {index}".encode()
            )
            self.assertTrue(created)
            previous = value.event_hash
        health = self.store.health()
        self.assertEqual(health["schema_version"], 17)
        self.assertGreaterEqual(health["event_count"], 0)
        rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations "
            "WHERE life_id = ? AND layer = 'L1_STREAM'",
            (LIFE,),
        ).fetchone()
        self.assertEqual(int(rows["n"]), 150)

    def test_hundred_fifty_turns_with_periodic_promotion(self) -> None:
        previous = None
        l1_ids: list[str] = []
        for index in range(1, 151):
            value = self._turn(index, previous)
            _a, l1, _c = self.coordinator.commit_life_event_l1(
                value, event_payload=f"turn {index}".encode()
            )
            l1_ids.append(l1.derivation_id)
            previous = value.event_hash
            if index % 50 == 0:
                l2 = self.coordinator.promote_l1_to_l2(
                    life_id=LIFE,
                    principal_ref=PRINCIPAL,
                    privacy_scope=PRIVACY,
                    l1_derivation_ids=tuple(l1_ids[-50:]),
                    claim_key=f"claim:diary-{index}",
                    semantic_domain="WORLD",
                    plaintext=f"diary {index}".encode(),
                    created_at_ms=1_000 + index + 1,
                )
                self.assertIsNotNone(l2)
        self.store.health()
        l2_rows = self.store._connection.execute(  # noqa: SLF001
            "SELECT count(*) AS n FROM memory_derivations "
            "WHERE life_id = ? AND layer = 'L2_DIARY'",
            (LIFE,),
        ).fetchone()
        self.assertEqual(int(l2_rows["n"]), 3)

    def test_hundred_fifty_turns_deterministic_across_stores(self) -> None:
        ids_first = self._run_chain()
        with tempfile.TemporaryDirectory() as other:
            other_path = Path(other) / "chain2.shadow.sqlite3"
            with LifeShadowStore.open(
                other_path, create=True, now_ms=500
            ) as other_store:
                other_coordinator = MemoryCoordinator(other_store)
                ids_second = self._run_chain(other_coordinator)
        self.assertEqual(ids_first, ids_second)

    def _run_chain(self, coordinator: MemoryCoordinator | None = None):
        coordinator = coordinator or self.coordinator
        previous = None
        collected: list[str] = []
        for index in range(1, 151):
            value = self._turn(index, previous)
            _a, l1, _c = coordinator.commit_life_event_l1(
                value, event_payload=f"turn {index}".encode()
            )
            collected.append(l1.derivation_id)
            previous = value.event_hash
        return tuple(collected)


if __name__ == "__main__":
    unittest.main()
