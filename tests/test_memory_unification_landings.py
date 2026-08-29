"""记忆归一三项落地的回归测试。

1. key_facts 工作指纹管道：桥接层写入 process_summary/key_facts，
   投影侧消费进下一轮胶囊（修恒空插槽）。
2. 叙事日记：narrative_diary 目录活动经执行回路完成，日记落回记忆。
3. 身体镜像刷新：每轮情感更新时刷新记忆统计镜像（进化信号不再恒零）。
"""

from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkFingerprintPipelineTests(unittest.TestCase):
    def test_bridge_writes_fingerprint_fields(self) -> None:
        text = (ROOT / "src" / "total_gateway" / "frozen_backend_compat.py").read_text(encoding="utf-8")
        self.assertIn('"process_summary": process_summary', text)
        self.assertIn('"key_facts": key_facts[:12]', text)
        # 失败路径也写（断点胶囊需要诊断信息）
        self.assertIn('"process_summary": f"执行失败: {exc.code}"', text)

    def test_projection_consumes_key_facts(self) -> None:
        from total_gateway.context_projection import (
            ConversationProjectionPolicy,
            SessionContextProjector,
        )

        class _ProjectorStub:
            _policy = ConversationProjectionPolicy()

        payload = {
            "reply_text": "已完成文档。",
            "key_facts": ["生成交付文件: 报告.docx（docx）", "工具轮次: 4"],
            "process_summary": "工具轮次4，交付文件1个",
        }
        facts = SessionContextProjector._key_facts(_ProjectorStub(), payload)
        # process_summary 排首位，key_facts 逐条随后
        self.assertEqual(facts[0], "工具轮次4，交付文件1个")
        self.assertIn("生成交付文件: 报告.docx（docx）", facts)
        self.assertIn("工具轮次: 4", facts)
        # process_summary 单独存在时也应被消费
        summary_only = SessionContextProjector._key_facts(
            _ProjectorStub(), {"process_summary": "工具轮次2"}
        )
        self.assertEqual(summary_only, ("工具轮次2",))


class NarrativeDiaryTests(unittest.TestCase):
    def test_diary_activity_executes_and_lands_in_memory(self) -> None:
        import tempfile

        from life_service.embedded_runtime import EmbeddedLifeRuntime

        called = threading.Event()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            life = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "life-runtime",
                mode="embedded",
            )
            try:
                status, body, _ = life.request(
                    "POST",
                    "/api/v1/v3/life/settings",
                    {"settings": {"autonomy_activity_types": ["narrative_diary"]}},
                )
                assert status == 200, body
                life.request(
                    "POST",
                    "/api/v1/v3/life/memory/assert",
                    {
                        "memory_id": "mem_day_event",
                        "content": {"text": "今天上午完成了天工造物的企划案讨论，下午修好了记账工具。"},
                        "confidence_milli": 900,
                    },
                )

                def decide(scope: dict, task: dict) -> dict:
                    assert task["activity_id"] == "narrative_diary"
                    called.set()
                    return {
                        "title": "充实的一天",
                        "summary": "今天和公子把企划案定了下来，下午工具也跑通了。虽然累，心里踏实。",
                        "findings": ["上午讨论企划案", "下午修复记账工具"],
                        "next_steps": [],
                        "uncertainties": [],
                    }

                life.set_autonomy_decider(decide)
                life.request(
                    "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "diary-test"}
                )
                assert called.wait(2)
                deadline = time.monotonic() + 4
                completed = None
                while time.monotonic() < deadline:
                    tasks = life._scope_state()["autonomy"]["tasks"]
                    done = [
                        t for t in tasks.values()
                        if isinstance(t, dict)
                        and t.get("activity_id") == "narrative_diary"
                        and t.get("status") == "completed"
                    ]
                    if done:
                        completed = done[0]
                        break
                    time.sleep(0.02)
                assert completed is not None, "日记任务应完成"

                memories = life._scope_state()["memories"]
                diary_rows = [
                    row for row in memories.values()
                    if isinstance(row, dict) and "心灵日记" in str(row.get("content") or "")
                ]
                self.assertTrue(diary_rows, "日记应写回记忆系统")
            finally:
                life.close()


class BodyMirrorRefreshTests(unittest.TestCase):
    def test_emotion_update_refreshes_memory_stats_mirror(self) -> None:
        from unittest import mock

        from v3.shenti_zhuangtai import ShentiZhuangtai
        from v3 import zongdiaodu

        shenti = ShentiZhuangtai()
        with mock.patch.object(
            zongdiaodu, "_jiyi_tongji_state", wraps=zongdiaodu._jiyi_tongji_state
        ) as spy:
            zongdiaodu._gengxin_qinggan(shenti, "今天做得不错", "谢谢公子", 2)
            spy.assert_called_once_with(shenti)


if __name__ == "__main__":
    unittest.main()
