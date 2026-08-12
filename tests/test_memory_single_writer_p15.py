"""P15 M2: exactly one production memory-write authority (the coordinator)."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


ROOT = Path(__file__).resolve().parents[1]
LIFE_SERVICE = ROOT / "src" / "life_service"


class MemorySingleWriterTests(unittest.TestCase):
    def test_derivation_writes_only_owned_by_store_and_coordinator(self) -> None:
        for path in sorted(LIFE_SERVICE.glob("*.py")):
            if path.name in {
                "store.py",
                "memory_coordinator.py",
                "memory_migration.py",
            }:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("put_memory_derivation", text, path.name)
            self.assertNotIn("put_live_memory_assertion", text, path.name)
            self.assertNotIn("put_memory_assertion(", text, path.name)

    def test_memory_migration_uses_store_but_not_derivations(self) -> None:
        text = (LIFE_SERVICE / "memory_migration.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("put_memory_assertion", text)
        self.assertNotIn("put_memory_derivation", text)

    def test_runtime_endpoint_adapter_delegates_to_coordinator(self) -> None:
        text = (LIFE_SERVICE / "embedded_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("store.put_live_memory_assertion", text)
        self.assertIn(
            "_memory_coordinator().commit_contract_assertion", text
        )
        self.assertIn(
            'path == "/api/v1/v3/life/memory/assert"', text
        )
        self.assertIn(
            'path == "/api/v1/v3/life/memory/turn"', text
        )
        self.assertIn(
            'path == "/api/v1/v3/life/memory/correct"', text
        )

    def test_no_second_memory_runtime_or_scheduler_class(self) -> None:
        for path in sorted(LIFE_SERVICE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in ("MemoryRuntime", "MemoryScheduler", "MemoryGateway"):
                self.assertNotIn(token, text, f"{path.name}:{token}")

    def test_coordinator_is_the_single_commit_facade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "single.shadow.sqlite3"
            with LifeShadowStore.open(path, create=True, now_ms=500) as store:
                coordinator = MemoryCoordinator(store)
                value = event(1, None)
                _assertion, derivation, created = (
                    coordinator.commit_life_event_l1(value)
                )
                self.assertTrue(created)
                self.assertEqual(
                    store.list_memory_derivations(life_id=value.life_id),
                    (derivation,),
                )
                # A second coordinator over the same store sees the same
                # authority and cannot create a duplicate write path.
                second = MemoryCoordinator(store)
                _a, _d, created_again = second.commit_life_event_l1(value)
                self.assertFalse(created_again)

    def test_legacy_endpoints_are_adapter_wired_not_second_writers(self) -> None:
        text = (LIFE_SERVICE / "embedded_runtime.py").read_text(
            encoding="utf-8"
        )
        # The old direct-store write call is gone from the endpoint adapter.
        self.assertIsNone(
            re.search(r"store\.put_live_memory_assertion\(", text)
        )


if __name__ == "__main__":
    unittest.main()
