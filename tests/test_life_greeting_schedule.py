"""Greeting scheduler and desire-aware activity selection tests."""
from __future__ import annotations

import tempfile
import time
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
        self.assertTrue(cls._activity_window_open("", 12))  # 未知窗口不挡路

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
                    "vigilance": 900, "interest": 300, "calm": 0,
                }
                affinity = life._desire_affinity(scope)
                # vigilance 900 → system_health 90*0.9=81
                self.assertEqual(affinity.get("system_health"), 81)
                # interest 300 → creative_exploration 90*0.3=27
                self.assertEqual(affinity.get("creative_exploration"), 27)
                self.assertNotIn("relationship_care", affinity)
            finally:
                life.close()


class GreetingSchedulerTests(unittest.TestCase):
    def _runtime(self, root: Path) -> EmbeddedLifeRuntime:
        return EmbeddedLifeRuntime(
            data_root=root / "life-data",
            runtime_root=root / "runtime",
            mode="embedded",
        )

    def test_disabled_when_share_off(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                scope = life._scope_state()
                scope["settings"]["share_enabled"] = False
                life._schedule_greeting(life_id=str(life._active()["life_id"]))
                scheduler = scope["scheduler"]
                self.assertEqual(scheduler["last_greeting_decision_reason"], "life.greeting.disabled")
            finally:
                life.close()

    def test_first_call_only_sets_random_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                scope = life._scope_state()
                life._schedule_greeting(life_id=str(life._active()["life_id"])
                )
                next_at = int(scope["scheduler"].get("next_greeting_at_ms") or 0)
                now_ms = time.time_ns() // 1_000_000
                self.assertGreater(next_at, now_ms + 40 * 60_000)
                self.assertLess(next_at, now_ms + 125 * 60_000)
            finally:
                life.close()

    def test_due_greeting_publishes_fallback_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                scope = life._scope_state()
                now_ms = time.time_ns() // 1_000_000
                scheduler = scope["scheduler"]
                # 直接放到到期状态，且无写手 → 走情绪回退文案
                scheduler["next_greeting_at_ms"] = now_ms - 1000
                # 该用例验证发布路径，不应随执行机器的本地时钟落入
                # 产品默认 23:00–08:00 免打扰窗口而变成夜间必失败。
                scope["settings"]["share_dnd_start"] = "00:00"
                scope["settings"]["share_dnd_end"] = "00:00"
                scope["affect"]["emotions"] = {"interest": 900, "calm": 0}
                life._schedule_greeting(life_id=str(life._active()["life_id"]))
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not scope["proactive_chats"]:
                    time.sleep(0.05)
                self.assertEqual(len(scope["proactive_chats"]), 1)
                message = scope["proactive_chats"][0]
                self.assertEqual(message["kind"], "greeting")
                self.assertIn("有意思", message["text"])
                self.assertEqual(
                    scheduler["last_greeting_decision_reason"], "life.greeting.published"
                )
            finally:
                life.close()

    def test_greeting_writer_text_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            life.set_greeting_writer(lambda material: "嘿，我刚巡检完，心情不错，来跟你打个招呼！")
            try:
                scope = life._scope_state()
                now_ms = time.time_ns() // 1_000_000
                scope["scheduler"]["next_greeting_at_ms"] = now_ms - 1000
                scope["settings"]["share_dnd_start"] = "00:00"
                scope["settings"]["share_dnd_end"] = "00:00"
                life._schedule_greeting(life_id=str(life._active()["life_id"]))
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not scope["proactive_chats"]:
                    time.sleep(0.05)
                self.assertEqual(
                    scope["proactive_chats"][0]["text"],
                    "嘿，我刚巡检完，心情不错，来跟你打个招呼！",
                )
            finally:
                life.close()


if __name__ == "__main__":
    unittest.main()
