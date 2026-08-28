"""执行链效率修复的回归测试。

覆盖三处手术：
1. deadline 只经 ContextVar 传播（env 通道移除，防跨链毒害）
2. artifact-only 回复可交付（桥接层 + 网关合成确定性交付说明）
3. 模型载荷注入剩余执行预算（预算可见）
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeadlineChannelTests(unittest.TestCase):
    def test_orchestration_no_longer_writes_deadline_env(self) -> None:
        text = (ROOT / "src" / "total_gateway" / "orchestration.py").read_text(encoding="utf-8")
        self.assertIn("set_execution_deadline_ms", text)
        self.assertNotIn('os.environ["TIANGONG_EFFECT_DEADLINE_MS"]', text)

    def test_backend_keeps_contextvar_first_env_fallback(self) -> None:
        # 后端读者保留 env 兜底（死通道，无害）；ContextVar 必须优先。
        kernel = (
            ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "simple_chain" / "kernel.py"
        ).read_text(encoding="utf-8")
        self.assertIn("current_execution_deadline_ms()", kernel)
        # 兜底读取存在但排在 ContextVar 之后
        self.assertLess(
            kernel.index("current_execution_deadline_ms()"),
            kernel.index('os.environ.get("TIANGONG_EFFECT_DEADLINE_MS"'),
        )


class ArtifactOnlyDeliveryTests(unittest.TestCase):
    def test_bridge_accepts_artifact_only_success(self) -> None:
        text = (ROOT / "src" / "total_gateway" / "frozen_backend_compat.py").read_text(encoding="utf-8")
        self.assertIn("(reply or outputs)", text)

    def test_orchestration_synthesizes_reply_from_artifacts(self) -> None:
        text = (ROOT / "src" / "total_gateway" / "orchestration.py").read_text(encoding="utf-8")
        self.assertIn("artifact-only delivery", text)
        self.assertIn("orchestration.reply.empty", text)


class BudgetVisibilityTests(unittest.TestCase):
    def test_model_payload_includes_remaining_budget_when_bound(self) -> None:
        from contracts.reliability import reset_execution_deadline, set_execution_deadline_ms
        from v3.simple_chain.kernel import _simple_chain_model_payload

        import time as _time

        token = set_execution_deadline_ms(int(_time.time() * 1000) + 300_000)
        try:
            payload = _simple_chain_model_payload({"ok": True, "status": "success", "paths": ["a.docx"]})
            self.assertIsInstance(payload, dict)
            budget = payload.get("execution_budget")
            self.assertIsInstance(budget, dict)
            self.assertGreater(budget.get("remaining_seconds", 0), 0)
            self.assertLess(budget.get("remaining_seconds", 10**9), 300)
        finally:
            reset_execution_deadline(token)

    def test_model_payload_omits_budget_when_unbound(self) -> None:
        from v3.simple_chain.kernel import _simple_chain_model_payload

        payload = _simple_chain_model_payload({"ok": True, "status": "success", "paths": ["a.docx"]})
        self.assertIsInstance(payload, dict)
        self.assertNotIn("execution_budget", payload)


if __name__ == "__main__":
    unittest.main()
