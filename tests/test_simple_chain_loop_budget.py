"""CC-style loop termination regression tests.

Covers the structural fix for the unbounded simple-chain loop:
platform budgets (iterations, tool rounds, final-gap retries, repeated
observations), artifact protection after verified writes, and the
completion gate reading deliverables that already exist on disk.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class SimpleChainLoopBudgetTests(unittest.TestCase):
    def test_content_requirement_is_bound_to_target_path(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_content_requirement_for,
            _simple_chain_parse_requirements,
        )

        message = "创建 README.md（至少 300 字）和 清单.txt（列出 5 项核心功能）"
        reqs = _simple_chain_parse_requirements(message)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["path_pattern"], "README.md")
        self.assertEqual(reqs[0]["min_chars"], 300)
        # 300 字只约束 README，不套到清单.txt。
        self.assertEqual(_simple_chain_content_requirement_for("清单.txt", message, reqs), (0, ""))
        self.assertEqual(_simple_chain_content_requirement_for("README.md", message, reqs), (300, "nonspace"))

    def test_unbound_requirement_falls_back_to_global(self) -> None:
        from v3.zongdiaodu import _simple_chain_content_requirement_for

        message = "写一个至少 500 字的文档"
        self.assertEqual(_simple_chain_content_requirement_for("out.txt", message), (500, "nonspace"))

    def test_force_stopped_reply_explains_system_cutoff(self) -> None:
        from v3.zongdiaodu import _simple_chain_force_stopped_reply

        text = _simple_chain_force_stopped_reply(["no effective progress for 4 consecutive steps"], 1)
        self.assertIn("强制切断", text)
        self.assertIn("不会自动", text)
        self.assertIn("请重新发起", text)

    def test_packaging_checklist_is_not_zip_delivery(self) -> None:
        from v3.zongdiaodu import _has_delivery_intent, _requests_zip_delivery

        message = "生成打包发布清单，保存为 output/e2e/29-packaging.md。"
        self.assertFalse(_has_delivery_intent(message))
        self.assertFalse(_requests_zip_delivery(message))
        self.assertTrue(_requests_zip_delivery("把 output/e2e 打包成 zip 发给我"))

    def test_mutation_request_without_write_is_classified_write(self) -> None:
        from v3.zongdiaodu import _simple_chain_task_kind

        read_only_history = [
            {
                "ok": True,
                "tool_action": "file.read",
                "tool_args": {"action": "file.read", "target": "output/e2e/18-seo.md", "args": {}},
                "tool_result": {"ok": True},
                "tool_result_contract": {"ok": True},
            }
        ]
        self.assertEqual(
            _simple_chain_task_kind(read_only_history, "生成 SEO 友好文章，保存为 output/e2e/18-seo.md。"),
            "write",
        )
        self.assertEqual(_simple_chain_task_kind(read_only_history), "read")

    def test_readonly_repeat_after_verified_write_accepts_delivery(self) -> None:
        from v3.zongdiaodu import (
            _SIMPLE_CHAIN_MAX_READONLY_REPEAT_OBSERVATIONS,
            _contract_observed_write,
            _simple_chain_has_post_mutation_verification,
        )

        self.assertGreater(_SIMPLE_CHAIN_MAX_READONLY_REPEAT_OBSERVATIONS, 3)
        target = "output/e2e/25-cleanup.md"
        write_payload = {
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"action": "file.write", "target": target, "args": {"content": "x" * 400, "path": target}},
            "tool_result": {"ok": True, "readback": {"ok": True}, "evidence": {"exists": True}},
            "tool_result_contract": {
                "ok": True,
                "observed_write_effect": True,
                "write_evidence": {"authoritative": True, "changed_files": [target]},
                "paths": [target],
            },
        }
        read_payload = {
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"action": "file.read", "target": target, "args": {}},
            "tool_result": {"ok": True},
            "tool_result_contract": {"ok": True, "paths": [target]},
        }
        self.assertTrue(_simple_chain_has_post_mutation_verification([write_payload, read_payload]))
        self.assertTrue(_contract_observed_write(write_payload["tool_result_contract"]))
        self.assertFalse(_contract_observed_write(read_payload["tool_result_contract"]))

    def test_execution_deadline_context_roundtrip(self) -> None:
        from contracts.reliability import (
            current_execution_deadline_ms,
            reset_execution_deadline,
            set_execution_deadline_ms,
        )

        self.assertEqual(current_execution_deadline_ms(), 0)
        token = set_execution_deadline_ms(1234567890)
        try:
            self.assertEqual(current_execution_deadline_ms(), 1234567890)
        finally:
            reset_execution_deadline(token)
        self.assertEqual(current_execution_deadline_ms(), 0)

    def test_llm_call_has_hard_wall_clock_deadline(self) -> None:
        from v3.jineng.http_kehuduan import _LLM_CALL_MAX_SECONDS

        # CC-loop structure: one LLM streaming call must never wedge a worker
        # thread indefinitely, even when SSE keepalive resets per-read timeouts.
        self.assertGreater(_LLM_CALL_MAX_SECONDS, 0)
        self.assertLessEqual(_LLM_CALL_MAX_SECONDS, 900)

    def test_llm_call_deadline_guard_is_enforced_inside_stream(self) -> None:
        from v3.jineng import http_kehuduan
        from pathlib import Path as _Path

        source = _Path(http_kehuduan.__file__).read_text(encoding="utf-8")
        self.assertIn("llm_call_wall_clock_deadline", source)
        self.assertIn("_LLM_CALL_MAX_SECONDS", source)
        self.assertIn("current_execution_deadline_ms", source)

    def test_simple_chain_honors_gateway_effect_deadline(self) -> None:
        from pathlib import Path as _Path

        source = _Path(__file__).resolve().parents[1] / "app" / "backend" / "tiangong-backend" / "v3" / "zongdiaodu.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("current_execution_deadline_ms", text)
        self.assertIn("effective_wall_clock_seconds", text)
        self.assertIn("_simple_chain_remaining_deadline_seconds", text)
        self.assertIn("[EXECUTION_DEADLINE]", text)
        self.assertIn("_SIMPLE_CHAIN_MAX_TOOL_EXECUTION_SECONDS", text)
        self.assertIn("TIANGONG_EFFECT_DEADLINE_MS", text)

    def test_gateway_publishes_effect_deadline_env_fallback(self) -> None:
        from pathlib import Path as _Path

        orchestration = _Path(__file__).resolve().parents[1] / "src" / "total_gateway" / "orchestration.py"
        text = orchestration.read_text(encoding="utf-8")
        self.assertIn("TIANGONG_EFFECT_DEADLINE_MS", text)
        self.assertIn("previous_deadline_env", text)
        self.assertIn('os.environ.pop("TIANGONG_EFFECT_DEADLINE_MS", None)', text)
        # 执行预算 3 倍放宽：网关默认效果截止 720s（12 分钟），单次动作上限 1800s。
        self.assertIn("watchdog_ms = 720_000", text)
        self.assertIn("3_600_000", text)

    def test_budget_defaults_tripled(self) -> None:
        from v3.zongdiaodu import (
            _SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES,
            _SIMPLE_CHAIN_MAX_LOOP_TURNS,
            _SIMPLE_CHAIN_MAX_READONLY_REPEAT_OBSERVATIONS,
            _SIMPLE_CHAIN_MAX_REPEAT_OBSERVATIONS,
            _SIMPLE_CHAIN_MAX_TOOL_EXECUTION_SECONDS,
            _SIMPLE_CHAIN_MAX_TOOL_ROUNDS,
            _SIMPLE_CHAIN_MAX_WALL_CLOCK_SECONDS,
        )

        self.assertEqual(_SIMPLE_CHAIN_MAX_TOOL_ROUNDS, 75)
        self.assertEqual(_SIMPLE_CHAIN_MAX_LOOP_TURNS, 180)
        self.assertEqual(_SIMPLE_CHAIN_MAX_WALL_CLOCK_SECONDS, 5400)
        self.assertEqual(_SIMPLE_CHAIN_MAX_REPEAT_OBSERVATIONS, 90)
        self.assertEqual(_SIMPLE_CHAIN_MAX_READONLY_REPEAT_OBSERVATIONS, 90)
        self.assertEqual(_SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES, 9)
        self.assertEqual(_SIMPLE_CHAIN_MAX_TOOL_EXECUTION_SECONDS, 540)

    def test_work_status_question_is_not_mutation(self) -> None:
        from v3.zongdiaodu import _requires_real_mutation, _runtime_detects_work_intent

        # 纯询问/汇报：不进入写操作，也不会被“无工具观察”按预算上限收尾。
        for question in (
            "整理什么内容了",
            "现在到哪了",
            "你刚才做了什么",
            "整理得怎么样了",
            "上次的进度如何",
        ):
            self.assertFalse(_requires_real_mutation(question), question)
            self.assertFalse(_runtime_detects_work_intent(question), question)
        # 真命令仍然识别为 mutation。
        for command in (
            "帮我整理一下这些文件",
            "把 output/e2e 整理成表格",
            "生成打包发布清单，保存为 output/e2e/29-packaging.md。",
        ):
            self.assertTrue(_requires_real_mutation(command), command)

    def test_no_observation_query_reply_passes_gate(self) -> None:
        from v3.zongdiaodu import _simple_chain_final_hard_gate

        ok, status, reasons = _simple_chain_final_hard_gate(
            "整理什么内容了",
            [],
            [],
            None,
            final_reply="上一轮我在整理知识笔记，草稿还没落盘。",
        )
        self.assertTrue(ok)
        self.assertEqual(status, "complete")
        self.assertEqual(reasons, [])

        # 真写任务零观察仍然 fail-closed，不允许无证据谎报完成。
        ok_write, status_write, reasons_write = _simple_chain_final_hard_gate(
            "生成打包发布清单，保存为 output/e2e/29-packaging.md。",
            [],
            [],
            None,
            final_reply="已完成。",
        )
        self.assertFalse(ok_write)
        self.assertEqual(status_write, "incomplete")
        self.assertTrue(
            any("no omni_body observation exists" in reason for reason in reasons_write),
            reasons_write,
        )

    def _ok_payload(self, action: str = "file.read", target: str = "a.txt") -> dict:
        return {
            "ok": True,
            "tool_action": action,
            "tool_args": {"action": action, "target": target},
            "tool_result": {"ok": True, "content": "x" * 100},
            "tool_result_contract": {"ok": True, "paths": [target]},
        }

    def test_progress_fingerprint_tracks_state_changes(self) -> None:
        from v3.zongdiaodu import _simple_chain_progress_fingerprint

        fp_empty = _simple_chain_progress_fingerprint("整理什么内容了", [], [])
        fp_one = _simple_chain_progress_fingerprint("整理什么内容了", [self._ok_payload()], [])
        fp_one_again = _simple_chain_progress_fingerprint("整理什么内容了", [self._ok_payload()], [])
        fp_attachment = _simple_chain_progress_fingerprint(
            "整理什么内容了",
            [self._ok_payload()],
            [{"path": "output/out.md"}],
        )
        fp_new_observation = _simple_chain_progress_fingerprint(
            "整理什么内容了",
            [self._ok_payload(), self._ok_payload(action="web.search", target="query")],
            [],
        )
        self.assertNotEqual(fp_empty, fp_one)
        self.assertEqual(fp_one, fp_one_again)
        self.assertNotEqual(fp_one, fp_attachment)
        self.assertNotEqual(fp_one, fp_new_observation)

    def test_intent_near_duplicate_detection(self) -> None:
        from v3.zongdiaodu import _simple_chain_intent_is_near_duplicate

        self.assertTrue(_simple_chain_intent_is_near_duplicate("我再看看这个文件", "我再看一下这个文件"))
        self.assertTrue(_simple_chain_intent_is_near_duplicate("换个方式继续", "换个思路继续"))
        self.assertTrue(_simple_chain_intent_is_near_duplicate("我再检查一下这个文件", "我再检查一遍这个文件"))
        self.assertFalse(_simple_chain_intent_is_near_duplicate("我再看看这个文件", "文件已写完，直接交付"))
        self.assertFalse(_simple_chain_intent_is_near_duplicate("", "随便"))

    def test_progress_monitor_stuck_rules(self) -> None:
        from v3.zongdiaodu import _SimpleChainProgressMonitor

        # 1) 状态连续无变化 → 卡死（即使措辞每次不同）。
        monitor = _SimpleChainProgressMonitor(
            max_no_progress_steps=3,
            max_cycle_hits=10,
            max_duplicate_intent_streak=10,
        )
        for text in ("第一步", "换个说法", "再试一次", "换一种方式"):
            stuck, reason = monitor.update("A", text)
        self.assertTrue(stuck)
        self.assertIn("no effective progress", reason)

        # 2) 状态变化即重置无进展计数。
        monitor_reset = _SimpleChainProgressMonitor(
            max_no_progress_steps=4,
            max_cycle_hits=10,
            max_duplicate_intent_streak=10,
        )
        for _ in range(3):
            monitor_reset.update("A", "重复")
        stuck, _ = monitor_reset.update("B", "新状态")
        self.assertFalse(stuck)

        # 3) 状态回环 → 卡死。
        monitor_cycle = _SimpleChainProgressMonitor(
            max_no_progress_steps=100,
            max_cycle_hits=2,
            max_duplicate_intent_streak=100,
        )
        monitor_cycle.update("A", "x")
        monitor_cycle.update("B", "y")
        monitor_cycle.update("A", "z")
        stuck, reason = monitor_cycle.update("B", "w")
        self.assertTrue(stuck)
        self.assertIn("cycled", reason)

        # 4) 状态不变 + 意图文本连续重复 → 卡死。
        monitor_intent = _SimpleChainProgressMonitor(
            max_no_progress_steps=100,
            max_cycle_hits=100,
            max_duplicate_intent_streak=3,
        )
        monitor_intent.update("A", "我想再看看这个文件")
        monitor_intent.update("A", "我再看看这个文件")
        monitor_intent.update("A", "我再检查一遍这个文件")
        stuck, reason = monitor_intent.update("A", "我再检查一下这个文件")
        self.assertTrue(stuck)
        self.assertIn("same intent", reason)

    def test_stuck_close_reply_and_natural_text(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_natural_reply_text,
            _simple_chain_stuck_close_reply,
        )

        reply = _simple_chain_stuck_close_reply(["[stuck] no effective progress"], 3)
        self.assertIn("无有效进展", reply)
        self.assertIn("本轮不再继续执行", reply)
        natural = _simple_chain_natural_reply_text(
            '先看一下\n<invoke name="omni_body"><parameter name="action">file.read</parameter></invoke>'
        )
        self.assertEqual(natural.strip(), "先看一下")

    def test_natural_closeout_payload_asks_for_persona_voice(self) -> None:
        from v3.zongdiaodu import _simple_chain_natural_closeout_payload

        payload = _simple_chain_natural_closeout_payload(
            status="incomplete",
            reasons=["[stuck] no effective progress for 4 consecutive steps"],
            quality_history=[self._ok_payload()],
            generated_attachments=[],
            tool_count=2,
        )
        self.assertEqual(payload["authoritative_status"], "incomplete")
        self.assertIn("natural", payload["instruction"].lower())
        self.assertIn("Never claim success", payload["instruction"])
        self.assertEqual(payload["blocking_reasons"][0], "[stuck] no effective progress for 4 consecutive steps")

    def test_force_stopped_closeout_payload_explains_forced_stop(self) -> None:
        from v3.zongdiaodu import _simple_chain_natural_closeout_payload

        payload = _simple_chain_natural_closeout_payload(
            status="force_stopped",
            reasons=["[stuck] no effective progress for 4 consecutive steps"],
            quality_history=[],
            generated_attachments=[],
            tool_count=0,
        )
        self.assertEqual(payload["authoritative_status"], "force_stopped")
        self.assertEqual(payload["terminal_kind"], "force_stopped")
        self.assertIn("forcibly stopped", payload["instruction"])
        self.assertIn("re-initiate", payload["instruction"])
        self.assertEqual(payload["blocking_reasons"][0], "[stuck] no effective progress for 4 consecutive steps")

    def test_continue_decision_payload_allows_model_choice(self) -> None:
        from v3.zongdiaodu import _simple_chain_continue_decision_payload

        payload = _simple_chain_continue_decision_payload("req_x", ["no_write_effect"], None)
        self.assertEqual(payload["schema"], "tiangong.v3.simple_chain.continue_decision.v1")
        self.assertIn(
            "If you continue, return exactly one concrete omni_body tool call",
            payload["instruction"],
        )
        self.assertIn("cannot continue productively", payload["instruction"])
        self.assertEqual(payload["blocking_reasons"], ["no_write_effect"])

    def test_budget_close_reply_is_terminal_and_honest(self) -> None:
        from v3.zongdiaodu import _simple_chain_budget_close_reply

        reply = _simple_chain_budget_close_reply(
            ["[loop_budget_exhausted] loop iteration budget exhausted"],
            12,
        )
        self.assertIn("平台执行预算", reply)
        self.assertIn("本轮不再继续执行", reply)
        self.assertIn("未完成", reply)
        self.assertNotIn("我会按现有检查点继续处理", reply)
        self.assertNotIn("继续执行", reply.replace("本轮不再继续执行", ""))

    def test_requested_paths_extracts_target_and_nested_args(self) -> None:
        from v3.zongdiaodu import _simple_chain_requested_paths

        paths = _simple_chain_requested_paths(
            {
                "action": "file.write",
                "target": "output/e2e/16-knowledge.md",
                "args": {"path": "output/e2e/16-knowledge.md", "content": "正文"},
            }
        )
        self.assertTrue(any("16-knowledge.md" in item for item in paths))

    def test_requested_paths_extracts_destructive_command_tokens(self) -> None:
        from v3.zongdiaodu import _simple_chain_requested_paths

        paths = _simple_chain_requested_paths(
            {
                "action": "shell.run",
                "target": "cmd",
                "args": {
                    "command": 'if exist "C:\\Work\\15-course.pptx" del /f /q "C:\\Work\\15-course.pptx"'
                },
            }
        )
        self.assertTrue(any("15-course.pptx" in item for item in paths))

    def test_desktop_topic_keyword_does_not_hijack_workspace_path(self) -> None:
        import os

        from v3.zongdiaodu import _simple_chain_requested_target_paths

        os.environ["TIANGONG_DESKTOP_PATH"] = "C:/fake/desktop"
        paths = _simple_chain_requested_target_paths(
            "生成桌面清理计划，保存为 output/e2e/25-cleanup.md。"
        )
        self.assertTrue(any("25-cleanup.md" in item for item in paths))
        self.assertFalse(any("fake" in item for item in paths))
        self.assertTrue(any("output/e2e/25-cleanup.md" in item for item in paths))

    def test_request_payload_uses_cache_friendly_stable_prefix(self) -> None:
        from v3.gutong.gutong_ceng import JIXU_ZHILING_WENBEN
        from v3.jineng.moxing_shipei import MOXING_SHIPEI
        from v3.shenti_zhuangtai import ShentiZhuangtai

        payload = MOXING_SHIPEI.goujian_qingqiu(
            "minimax_m3",
            "系统",
            "稳定短指令",
            ShentiZhuangtai(),
            prior_assistant_messages=["结果1", "结果2"],
            stable_user_message="原始请求+上下文",
        )
        roles = [item["role"] for item in payload["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "assistant", "user"])
        self.assertEqual(payload["messages"][1]["content"], "原始请求+上下文")
        self.assertEqual(payload["messages"][-1]["content"], "稳定短指令")
        self.assertIn("提示注入", JIXU_ZHILING_WENBEN)

    def test_cache_prior_text_is_deterministic(self) -> None:
        import json

        from v3.zongdiaodu import _contract_observed_write

        payload = {
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"action": "file.write", "target": "output/e2e/02-core.md", "args": {"content": "x" * 5000}},
            "tool_result": {"evidence": {"exists": True, "path": "output/e2e/02-core.md", "sha256": "abc" * 30}, "preview": "y" * 3000},
            "tool_result_contract": {"ok": True, "observed_write_effect": True, "write_evidence": {"authoritative": True, "changed_files": ["output/e2e/02-core.md"]}},
        }
        first = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
        second = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
        self.assertEqual(first, second)
        self.assertIn("02-core.md", first)
        self.assertTrue(_contract_observed_write(payload["tool_result_contract"]))

    def test_command_touches_protected_only_with_destructive_verbs(self) -> None:
        from v3.zongdiaodu import _simple_chain_command_touches_protected

        protected = {"c:/work/15-course.pptx"}
        self.assertEqual(
            _simple_chain_command_touches_protected(
                'del /f /q "C:\\Work\\15-course.pptx"',
                protected,
            ),
            ["c:/work/15-course.pptx"],
        )
        # The helper reports any command text that touches the path; the
        # destructive-verb gate lives in _simple_chain_protected_block.
        self.assertEqual(
            _simple_chain_command_touches_protected(
                'dir "C:\\Work\\15-course.pptx"',
                protected,
            ),
            ["c:/work/15-course.pptx"],
        )

    def test_protected_block_blocks_destructive_overwrite(self) -> None:
        from v3.zongdiaodu import (
            _delivery_workspace_root,
            _simple_chain_protected_block,
            _simple_chain_protected_key,
        )

        base = _delivery_workspace_root()
        protected = {
            _simple_chain_protected_key(
                str(Path(base) / "output" / "e2e" / "15-course.pptx")
            )
        }
        hits = _simple_chain_protected_block(
            "omni_body",
            {
                "action": "file.delete_to_trash",
                "target": "output/e2e/15-course.pptx",
            },
            protected,
        )
        self.assertEqual(len(hits), 1)
        # A read of the same artifact is not destructive.
        self.assertEqual(
            _simple_chain_protected_block(
                "omni_body",
                {"action": "file.read", "target": "output/e2e/15-course.pptx"},
                protected,
            ),
            [],
        )
        # An unrelated path is not blocked.
        self.assertEqual(
            _simple_chain_protected_block(
                "omni_body",
                {"action": "file.delete_to_trash", "target": "output/e2e/other.pptx"},
                protected,
            ),
            [],
        )

    def test_protected_block_scans_destructive_shell_commands(self) -> None:
        from v3.zongdiaodu import _simple_chain_protected_block

        protected = {"c:/work/15-course.pptx"}
        hits = _simple_chain_protected_block(
            "omni_body",
            {
                "action": "shell.run",
                "target": "cmd",
                "args": {"command": 'del /f /q "C:\\Work\\15-course.pptx"'},
            },
            protected,
        )
        self.assertEqual(hits, ["c:/work/15-course.pptx"])
        self.assertEqual(
            _simple_chain_protected_block(
                "omni_body",
                {
                    "action": "shell.run",
                    "target": "cmd",
                    "args": {"command": 'dir "C:\\Work\\15-course.pptx"'},
                },
                protected,
            ),
            [],
        )

    def test_missing_deliverables_reads_existing_disk_artifact(self) -> None:
        from v3.zongdiaodu import _simple_chain_missing_deliverable_paths

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target_dir = workspace / "output" / "e2e"
            target_dir.mkdir(parents=True)
            target = target_dir / "15-course.pptx"
            target.write_bytes(b"pptx-bytes")
            with mock.patch(
                "v3.zongdiaodu._delivery_workspace_root",
                return_value=str(workspace),
            ):
                missing = _simple_chain_missing_deliverable_paths(
                    "请生成 output/e2e/15-course.pptx 并验证",
                    [],
                    [],
                )
        self.assertEqual(missing, [])

    def test_budget_constants_are_sane(self) -> None:
        from v3.zongdiaodu import (
            _SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES,
            _SIMPLE_CHAIN_MAX_LOOP_TURNS,
            _SIMPLE_CHAIN_MAX_REPEAT_OBSERVATIONS,
            _SIMPLE_CHAIN_MAX_TOOL_ROUNDS,
            _SIMPLE_CHAIN_MAX_WALL_CLOCK_SECONDS,
        )

        self.assertGreaterEqual(_SIMPLE_CHAIN_MAX_FINAL_GAP_RETRIES, 1)
        self.assertGreaterEqual(_SIMPLE_CHAIN_MAX_TOOL_ROUNDS, 1)
        self.assertGreaterEqual(_SIMPLE_CHAIN_MAX_LOOP_TURNS, _SIMPLE_CHAIN_MAX_TOOL_ROUNDS)
        self.assertGreaterEqual(_SIMPLE_CHAIN_MAX_REPEAT_OBSERVATIONS, 1)
        self.assertGreaterEqual(_SIMPLE_CHAIN_MAX_WALL_CLOCK_SECONDS, 60)


if __name__ == "__main__":
    unittest.main()
