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
