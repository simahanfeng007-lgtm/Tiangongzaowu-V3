"""Watchdog regression: stale effects must terminalize as AMBIGUOUS.

The watchdog is the last-resort bound for the legacy compatibility path.
This test pins the exact contract: a CLAIMED/SIDE_EFFECT_STARTED effect that
never resolves becomes AMBIGUOUS (never replayed), and terminal effects are
never touched.
"""

from __future__ import annotations

import hashlib
import unittest


class _FakeEffect:
    def __init__(self, state: str) -> None:
        self.state = state


class _FakeStore:
    def __init__(self, effects: dict[str, _FakeEffect]) -> None:
        self.effects = effects
        self.completed: list[object] = []

    def list_stale_non_terminal_effect_ids(self, *, stale_before_ms: int):
        return list(self.effects)

    def get_effect(self, effect_id: str):
        return self.effects.get(effect_id)

    def complete_effect(self, result: object) -> None:
        self.completed.append(result)


class WatchdogStaleEffectRegressionTests(unittest.TestCase):
    def _effect_id(self, seed: str) -> str:
        return "eff_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def _worker(self, store: _FakeStore):
        from total_gateway.orchestration import GatewayOrchestrationWorker

        worker = object.__new__(GatewayOrchestrationWorker)
        worker._store = store
        return worker

    def test_stale_claimed_effect_becomes_ambiguous(self) -> None:
        stale_id = self._effect_id("stale_claimed")
        store = _FakeStore(
            {
                stale_id: _FakeEffect("CLAIMED"),
            }
        )
        worker = self._worker(store)
        reconciled = worker._reconcile_stale_effects(now_ms=1_000_000, stale_after_ms=0)
        self.assertEqual(reconciled, 1)
        self.assertEqual(len(store.completed), 1)
        result = store.completed[0]
        self.assertEqual(result.status, "AMBIGUOUS")
        self.assertEqual(result.error_code, "effect_execution_timeout_reconcile")

    def test_stale_side_effect_started_becomes_ambiguous(self) -> None:
        stale_id = self._effect_id("stale_started")
        store = _FakeStore(
            {
                stale_id: _FakeEffect("SIDE_EFFECT_STARTED"),
            }
        )
        worker = self._worker(store)
        reconciled = worker._reconcile_stale_effects(now_ms=1_000_000, stale_after_ms=0)
        self.assertEqual(reconciled, 1)
        self.assertEqual(store.completed[0].status, "AMBIGUOUS")

    def test_terminal_effects_are_never_reconciled(self) -> None:
        completed_id = self._effect_id("completed")
        failed_id = self._effect_id("failed")
        store = _FakeStore(
            {
                completed_id: _FakeEffect("COMPLETED"),
                failed_id: _FakeEffect("FAILED"),
            }
        )
        worker = self._worker(store)
        reconciled = worker._reconcile_stale_effects(now_ms=1_000_000, stale_after_ms=0)
        self.assertEqual(reconciled, 0)
        self.assertEqual(store.completed, [])


if __name__ == "__main__":
    unittest.main()
