"""Self-iteration upgrade card lifecycle tests.

Covers the user-gated state machine added for autonomous self-code upgrade
cards: draft validation, dedup, confirm/cancel/complete transitions, and the
executor bridge that applies confirmed patch changes.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeError, EmbeddedLifeRuntime


class SelfIterationUpgradeTests(unittest.TestCase):
    def _runtime(self, root: Path) -> EmbeddedLifeRuntime:
        return EmbeddedLifeRuntime(
            data_root=root / "life-data",
            runtime_root=root / "runtime",
            mode="embedded",
        )

    def _draft(self, life: EmbeddedLifeRuntime, **overrides):
        life_id = str(life._active()["life_id"])
        decision = {
            "target": "upgrade",
            "title": "收紧学习卡渲染空态",
            "summary": "空态文案与当前数据口径不一致，缩小误导。",
            "risk_level": "A2",
            "goals": ["统一空态口径"],
            "changes": [
                {"target": "app/frontend-v2/renderer/plugins/life-panel.mjs", "find": "a", "replace": "b", "count": 1}
            ],
        }
        decision.update(overrides)
        return life._upgrade_draft(life_id, decision, source="autonomous")

    def test_draft_creates_awaiting_user_card_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                created = self._draft(life)
                card = created["upgrade"]
                self.assertTrue(card["card_id"].startswith("upg_"))
                self.assertEqual(card["status"], "awaiting_user")
                self.assertEqual(card["review_level"], "HUMAN_REVIEW")
                duplicate = self._draft(life)
                self.assertTrue(duplicate["duplicate"])
                panel = life.request("GET", "/api/v1/v3/life/panel")[1]
                self.assertEqual(len(panel["upgrade_cards"]), 1)
            finally:
                life.close()

    def test_draft_rejects_forbidden_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                for bad in (
                    {"target": "../outside.py", "find": "a", "replace": "b"},
                    {"target": "src/__pycache__/x.py", "find": "a", "replace": "b"},
                    {"target": "C:/abs/x.py", "find": "a", "replace": "b"},
                    {"target": "src/x.exe", "find": "a", "replace": "b"},
                    {"target": "src/x.py", "find": "", "replace": "b"},
                ):
                    with self.assertRaises(EmbeddedLifeError):
                        self._draft(life, title=f"bad-{bad['target']}", changes=[bad])
            finally:
                life.close()

    def test_confirm_runs_executor_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            calls = []
            life.set_upgrade_executor(lambda material: calls.append(material) or {"ok": True, "results": [{"target": "x", "ok": True}]})
            try:
                card = self._draft(life)["upgrade"]
                status, payload, _ = life.request(
                    "POST", "/api/v1/v3/life/upgrade/confirm", {"card_id": card["card_id"], "actor": "test"}
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload["upgrade"]["status"], "completed")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["card_id"], card["card_id"])
            finally:
                life.close()

    def test_confirm_executor_failure_marks_failed_with_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            life.set_upgrade_executor(lambda _material: {"ok": False, "error": "syntax check failed: line 3"})
            try:
                card = self._draft(life)["upgrade"]
                _, payload, _ = life.request(
                    "POST", "/api/v1/v3/life/upgrade/confirm", {"card_id": card["card_id"]}
                )
                self.assertEqual(payload["upgrade"]["status"], "failed")
                self.assertIn("syntax check failed", payload["upgrade"]["error"])
                # 失败卡不能再确认
                status2, payload2, _ = life.request(
                    "POST", "/api/v1/v3/life/upgrade/confirm", {"card_id": card["card_id"]}
                )
                self.assertEqual(status2, 409)
                self.assertEqual(payload2["reason_code"], "life.upgrade.not_confirmable")
            finally:
                life.close()

    def test_cancel_only_from_open_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                card = self._draft(life)["upgrade"]
                _, payload, _ = life.request(
                    "POST", "/api/v1/v3/life/upgrade/cancel", {"card_id": card["card_id"]}
                )
                self.assertEqual(payload["upgrade"]["status"], "cancelled")
                self.assertTrue(payload["deleted"])
                # 计划已删除，二次取消返回 404
                status2, payload2, _ = life.request(
                    "POST", "/api/v1/v3/life/upgrade/cancel", {"card_id": card["card_id"]}
                )
                self.assertEqual(status2, 404)
                self.assertEqual(payload2["reason_code"], "life.upgrade.not_found")
                # 面板不再出现该卡
                panel = life.request("GET", "/api/v1/v3/life/panel")[1]
                self.assertEqual(len(panel["upgrade_cards"]), 0)
            finally:
                life.close()

    def test_open_card_survives_day_boundary_in_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            life = self._runtime(Path(temporary))
            try:
                card = self._draft(life)["upgrade"]
                scope = life._scope_state()
                scope["upgrades"][card["card_id"]]["created_at"] = "2020-01-01T00:00:00.000Z"
                scope["upgrades"][card["card_id"]]["updated_at"] = "2020-01-01T00:00:00.000Z"
                panel = life.request("GET", "/api/v1/v3/life/panel")[1]
                self.assertEqual(len(panel["upgrade_cards"]), 1)
            finally:
                life.close()


if __name__ == "__main__":
    unittest.main()
