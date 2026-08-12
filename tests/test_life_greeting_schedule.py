"""Desire selection plus post-P15 legacy greeting freeze tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime


class DesireSelectionTests(unittest.TestCase):
    def test_activity_window_open(self) -> None:
        cls = EmbeddedLifeRuntime
        self.assertTrue(cls._activity_window_open("上午", 8))
        self.assertFalse(cls._activity_window_open("上午", 15))
        self.assertTrue(cls._activity_window_open("下午", 15))
        self.assertTrue(cls._activity_window_open("白天", 10))
        self.assertFalse(cls._activity_window_open("晚间", 10))
        self.assertTrue(cls._activity_window_open("空闲时", 3))
        self.assertTrue(cls._activity_window_open("", 12))

    def test_desire_affinity_follows_strongest_emotions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = EmbeddedLifeRuntime(
                data_root=Path(temporary) / "life-data",
                runtime_root=Path(temporary) / "runtime",
                mode="embedded",
            )
            try:
                scope = life._scope_state()
                scope["affect"]["emotions"] = {
                    "vigilance": 900,
                    "interest": 300,
                    "calm": 0,
                }
                affinity = life._desire_affinity(scope)
                self.assertEqual(affinity.get("system_health"), 81)
                self.assertEqual(affinity.get("creative_exploration"), 27)
                self.assertNotIn("relationship_care", affinity)
            finally:
                life.close()


class GreetingSchedulerTests(unittest.TestCase):
    def _runtime(self, root: Path) -> EmbeddedLifeRuntime:
        life = EmbeddedLifeRuntime(
            data_root=root / "life-data",
            runtime_root=root / "runtime",
            mode="embedded",
        )
        life.scheduler.stop(timeout_seconds=2)
        return life

    def test_legacy_greeting_is_hard_frozen_independent_of_share_setting(self) -> None:
        for share_enabled in (False, True):
            with self.subTest(share_enabled=share_enabled):
                with tempfile.TemporaryDirectory() as temporary:
                    life = self._runtime(Path(temporary))
                    try:
                        scope = life._scope_state()
                        scope["settings"]["share_enabled"] = share_enabled
                        before_scheduler = dict(scope["scheduler"])
                        life._schedule_greeting(
                            life_id=str(life._active()["life_id"])
                        )
                        self.assertEqual(scope["proactive_chats"], [])
                        self.assertEqual(scope["scheduler"], before_scheduler)
                    finally:
                        life.close()

    def test_legacy_greeting_does_not_schedule_random_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                scope = life._scope_state()
                life._schedule_greeting(life_id=str(life._active()["life_id"]))
                self.assertEqual(
                    int(scope["scheduler"].get("next_greeting_at_ms") or 0),
                    0,
                )
                self.assertEqual(scope["proactive_chats"], [])
            finally:
                life.close()

    def test_due_legacy_greeting_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                scope = life._scope_state()
                scope["scheduler"]["next_greeting_at_ms"] = 1
                scope["affect"]["emotions"] = {"interest": 900, "calm": 0}
                before = dict(scope["scheduler"])
                life._schedule_greeting(life_id=str(life._active()["life_id"]))
                self.assertEqual(scope["proactive_chats"], [])
                self.assertEqual(scope["scheduler"], before)
            finally:
                life.close()

    def test_legacy_greeting_writer_is_never_called(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            calls: list[dict] = []
            life.set_greeting_writer(lambda material: calls.append(dict(material)) or "旧问候")
            try:
                scope = life._scope_state()
                scope["scheduler"]["next_greeting_at_ms"] = 1
                life._schedule_greeting(life_id=str(life._active()["life_id"]))
                self.assertEqual(calls, [])
                self.assertEqual(scope["proactive_chats"], [])
            finally:
                life.close()


if __name__ == "__main__":
    unittest.main()
