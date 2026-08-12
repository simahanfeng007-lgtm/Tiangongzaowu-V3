"""P15 M4: zero-gain backoff and max open-learning bounds."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.life_learning_memory import (
    LEARNING_BACKOFF_BASE_MS,
    LEARNING_BACKOFF_MAX_MS,
    MAX_OPEN_LEARNING,
    zero_gain_backoff_ms,
)
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore


class LearningBackoffTests(unittest.TestCase):
    def test_zero_gain_backoff_is_exponential_and_capped(self) -> None:
        self.assertEqual(zero_gain_backoff_ms(0), 0)
        self.assertEqual(zero_gain_backoff_ms(1), LEARNING_BACKOFF_BASE_MS)
        self.assertEqual(
            zero_gain_backoff_ms(2), LEARNING_BACKOFF_BASE_MS * 2
        )
        self.assertEqual(
            zero_gain_backoff_ms(3), LEARNING_BACKOFF_BASE_MS * 4
        )
        self.assertLessEqual(
            zero_gain_backoff_ms(100), LEARNING_BACKOFF_MAX_MS
        )
        self.assertEqual(
            zero_gain_backoff_ms(100), LEARNING_BACKOFF_MAX_MS
        )

    def test_zero_gain_backoff_rejects_bad_inputs(self) -> None:
        with self.assertRaises(ValueError):
            zero_gain_backoff_ms(-1)
        with self.assertRaises(ValueError):
            zero_gain_backoff_ms(True)

    def test_open_learning_bounded_by_max_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "backoff.shadow.sqlite3"
            with LifeShadowStore.open(path, create=True, now_ms=500) as store:
                coordinator = MemoryCoordinator(store)
                opened = [
                    coordinator.open_learning(
                        life_id="life_backoff",
                        subject=f"subject_{index}",
                        now_ms=1_000,
                    )
                    for index in range(MAX_OPEN_LEARNING)
                ]
                self.assertTrue(all(opened))
                self.assertFalse(
                    coordinator.open_learning(
                        life_id="life_backoff",
                        subject="subject_overflow",
                        now_ms=1_000,
                    )
                )
                allowed, retry = coordinator.can_open_learning(
                    life_id="life_backoff",
                    subject="subject_overflow",
                    now_ms=1_000,
                )
                self.assertFalse(allowed)
                self.assertEqual(retry, 0)

    def test_zero_gain_backoff_blocks_then_expires(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "backoff2.shadow.sqlite3"
            with LifeShadowStore.open(path, create=True, now_ms=500) as store:
                coordinator = MemoryCoordinator(store)
                self.assertTrue(
                    coordinator.open_learning(
                        life_id="life_backoff",
                        subject="api-docs",
                        now_ms=1_000,
                    )
                )
                coordinator.record_zero_gain(
                    life_id="life_backoff", subject="api-docs", now_ms=2_000
                )
                coordinator.record_zero_gain(
                    life_id="life_backoff", subject="api-docs", now_ms=3_000
                )
                allowed, retry = coordinator.can_open_learning(
                    life_id="life_backoff",
                    subject="api-docs",
                    now_ms=4_000,
                )
                self.assertFalse(allowed)
                backoff = LEARNING_BACKOFF_BASE_MS * 2
                self.assertEqual(retry, 3_000 + backoff - 4_000)
                allowed, retry = coordinator.can_open_learning(
                    life_id="life_backoff",
                    subject="api-docs",
                    now_ms=3_000 + backoff,
                )
                self.assertTrue(allowed)
                self.assertEqual(retry, 0)

    def test_successful_learning_resets_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "backoff3.shadow.sqlite3"
            with LifeShadowStore.open(path, create=True, now_ms=500) as store:
                coordinator = MemoryCoordinator(store)
                coordinator.record_zero_gain(
                    life_id="life_backoff", subject="topic", now_ms=1_000
                )
                coordinator.reset_zero_gain(
                    life_id="life_backoff", subject="topic"
                )
                allowed, _retry = coordinator.can_open_learning(
                    life_id="life_backoff", subject="topic", now_ms=1_000
                )
                self.assertTrue(allowed)

    def test_close_learning_frees_a_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "backoff4.shadow.sqlite3"
            with LifeShadowStore.open(path, create=True, now_ms=500) as store:
                coordinator = MemoryCoordinator(store)
                for index in range(MAX_OPEN_LEARNING):
                    coordinator.open_learning(
                        life_id="life_backoff",
                        subject=f"subject_{index}",
                        now_ms=1_000,
                    )
                coordinator.close_learning(
                    life_id="life_backoff", subject="subject_0"
                )
                self.assertTrue(
                    coordinator.open_learning(
                        life_id="life_backoff",
                        subject="subject_new",
                        now_ms=1_000,
                    )
                )


if __name__ == "__main__":
    unittest.main()
