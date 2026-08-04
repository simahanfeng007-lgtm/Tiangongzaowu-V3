"""D-01：/ready 载荷 process_ready 与 action_ready 分离（非内嵌路径）。

- 两字段始终成对出现；
- fence 激活（已知安全缺陷/全局停止）→ action_ready=false 而 process_ready 可保持 true；
- 账本未对齐（未对账 attempt>0）→ action_ready=false；
- 安全事实读取失败 → fail-closed，action_ready=false（不留默认 true 兜底）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from total_gateway.bootstrap import GatewayConfig
from total_gateway.runtime import GatewayRuntime
from tests.test_gateway_http import COMPONENTS, readiness_inputs


class _RuntimeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        config = GatewayConfig(
            environment="test",
            port=0,
            state_root=Path(self.temporary.name) / "state",
            min_free_bytes=1_048_576,
            disk_probe_interval_ms=100,
            max_evidence_age_ms=5_000,
        )
        self.runtime = GatewayRuntime.start(config, now_ms=1_000)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def _make_process_ready(self) -> None:
        expectation, evidence = readiness_inputs(self.runtime.lease.gateway_epoch)
        component_ids = {item[0] for item in COMPONENTS}
        self.runtime.readiness.update(
            expectation,
            evidence,
            authenticated_component_ids=component_ids,
            binary_verified_component_ids=component_ids,
        )


class TestActionReadinessSeparation(_RuntimeCase):
    def test_payload_always_carries_separated_fields(self):
        status, payload = self.runtime.ready_payload()
        self.assertEqual(status, 503)  # 未配置证据：进程未就绪
        self.assertIn("process_ready", payload)
        self.assertIn("action_ready", payload)
        self.assertEqual(payload["process_ready"], False)
        self.assertEqual(payload["action_ready"], False)
        self.assertIn("action_fence", payload)
        self.assertIn("unreconciled_attempts", payload)

    def test_action_ready_true_when_ready_and_unfenced(self):
        self._make_process_ready()
        status, payload = self.runtime.ready_payload()
        self.assertEqual(status, 200, payload.get("reason_codes"))
        self.assertEqual(payload["process_ready"], True)
        self.assertEqual(payload["action_ready"], True)
        self.assertEqual(payload["action_fence"]["fenced"], False)
        self.assertEqual(payload["unreconciled_attempts"], 0)

    def test_fence_blocks_action_ready_but_not_process_ready(self):
        self._make_process_ready()
        self.runtime.store.increment_action_fence(reason="safety.stop", now_ms=2_000)
        status, payload = self.runtime.ready_payload()
        # 进程级仍然 READY，动作级必须 false
        self.assertEqual(status, 200, payload.get("reason_codes"))
        self.assertEqual(payload["process_ready"], True)
        self.assertEqual(payload["action_ready"], False)
        self.assertEqual(payload["action_fence"]["fenced"], True)

    def test_unreconciled_attempts_block_action_ready(self):
        self._make_process_ready()
        original = self.runtime.store.count_unreconciled_attempts
        self.runtime.store.count_unreconciled_attempts = lambda: 2
        try:
            status, payload = self.runtime.ready_payload()
        finally:
            del self.runtime.store.count_unreconciled_attempts
        self.assertEqual(status, 200, payload.get("reason_codes"))
        self.assertEqual(payload["process_ready"], True)
        self.assertEqual(payload["action_ready"], False)
        self.assertEqual(payload["unreconciled_attempts"], 2)
        self.assertTrue(callable(original))

    def test_fence_read_failure_fails_closed(self):
        self._make_process_ready()
        def _boom():
            raise RuntimeError("store gone")
        self.runtime.store.action_fence_status = _boom
        status, payload = self.runtime.ready_payload()
        self.assertEqual(status, 200, payload.get("reason_codes"))
        self.assertEqual(payload["process_ready"], True)
        # 安全事实缺失：绝不默认 true
        self.assertEqual(payload["action_ready"], False)
        self.assertEqual(payload["action_fence"]["display"], "unknown")
        self.assertEqual(payload["unreconciled_attempts"], -1)


if __name__ == "__main__":
    unittest.main()
