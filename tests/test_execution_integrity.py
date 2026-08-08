from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app/backend/tiangong-backend/v3/execution_integrity.py"
spec = importlib.util.spec_from_file_location("tiangong_execution_integrity", MODULE_PATH)
integrity = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(integrity)


class ExecutionIntegrityPureTests(unittest.TestCase):
    def test_explicit_directory_read_creates_actionable_obligation(self):
        for text in ("你读一下目录不就行了", "看看当前目录", "列出这个目录", "查看工作区"):
            with self.subTest(text=text):
                items = integrity.build_action_obligations(text)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["kind"], "observe_directory")
                self.assertTrue(items[0]["actionable"])

    def test_meta_and_response_only_do_not_force_execution(self):
        cases = (
            "你会读目录吗？",
            "如果让你读目录，你会怎么做？",
            "怎么读目录？",
            "先别读，只告诉我怎么处理",
            "不要使用工具，只分析目录读取方案",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(integrity.build_action_obligations(text), [])


    def test_execution_discussion_only_gate_is_narrow(self):
        positives = (
            "你会读目录吗？",
            "如果让你读目录，你会怎么做？",
            "假如要重构这个项目，你的方案是什么？",
            "先别读，只告诉我你准备怎么处理",
        )
        negatives = (
            "你读一下目录不就行了",
            "你能帮我读一下目录吗？",
            "如果当前目录里有 package.json，就读一下当前目录",
            "如果发现错误，那就修复",
            "看看当前目录里有哪些文件，先别改任何东西",
        )
        for text in positives:
            with self.subTest(text=text):
                self.assertTrue(integrity.is_execution_discussion_only(text))
        for text in negatives:
            with self.subTest(text=text):
                self.assertFalse(integrity.is_execution_discussion_only(text))

    def test_ambiguous_target_preserves_clarification(self):
        items = integrity.build_action_obligations("读一下那个目录")
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["actionable"])
        self.assertEqual(items[0]["status"], "needs_clarification")
        self.assertEqual(integrity.execution_integrity_blockers("读一下那个目录", []), [])

    def test_real_observation_satisfies_without_prescribing_tool(self):
        user = "你读一下目录不就行了"
        self.assertTrue(integrity.execution_integrity_blockers(user, []))
        history = [{"ok": True, "tool_action": "file.list"}]
        self.assertEqual(integrity.execution_integrity_blockers(user, history), [])
        # No fuzzy action-name matching: unrelated or merely read-looking actions
        # cannot satisfy a directory obligation.
        for action in ("workspace.tree", "thread.read", "spreadsheet.analysis.plan.create", "web.read", "file.read"):
            with self.subTest(action=action):
                self.assertTrue(integrity.execution_integrity_blockers(user, [{"ok": True, "tool_action": action}]))

    def test_irrelevant_or_failed_tool_does_not_satisfy(self):
        user = "查看当前目录"
        self.assertTrue(integrity.execution_integrity_blockers(user, [{"ok": True, "tool_action": "file.write"}]))
        self.assertTrue(integrity.execution_integrity_blockers(user, [{"ok": False, "tool_action": "file.list"}]))

    def test_explicit_target_requires_matching_tool_target(self):
        user = r"查看 C:\work\demo 目录"
        wrong = [{"ok": True, "tool_action": "file.list", "tool_args": {"action": "file.list", "args": {"target": r"C:\work\other"}}}]
        right = [{"ok": True, "tool_action": "file.list", "tool_args": {"action": "file.list", "args": {"target": r"C:\work\demo"}}}]
        self.assertTrue(integrity.execution_integrity_blockers(user, wrong))
        self.assertEqual(integrity.execution_integrity_blockers(user, right), [])

    def test_file_obligation_accepts_only_file_observation_capabilities(self):
        user = r"读取 C:\work\note.txt"
        for action in ("file.read", "code.read"):
            history = [{"ok": True, "tool_action": action, "tool_args": {"args": {"target": r"C:\work\note.txt"}}}]
            self.assertEqual(integrity.execution_integrity_blockers(user, history), [])
        self.assertTrue(integrity.execution_integrity_blockers(user, [{"ok": True, "tool_action": "web.read"}]))

    def test_completion_claim_requires_evidence(self):
        blockers = integrity.execution_integrity_blockers(
            "查看当前目录",
            [],
            final_reply="我已经查看完毕，目录里有三个文件。",
        )
        self.assertIn("execution_claim_without_evidence", blockers)
        blockers = integrity.execution_integrity_blockers(
            "查看当前目录",
            [{"ok": True, "tool_action": "file.list"}],
            final_reply="我已经查看完毕。",
        )
        self.assertEqual(blockers, [])

    def test_run_state_obligation_tracks_real_evidence(self):
        state = {"round": 1, "obligations": integrity.build_action_obligations("查看当前目录")}
        integrity.update_run_state_obligations(state, {"ok": True, "tool_action": "file.list"})
        self.assertEqual(state["obligations"][0]["status"], "satisfied")
        self.assertEqual(state["obligations"][0]["satisfied_by_action"], "file.list")

    def test_deviation_signal_is_narrow(self):
        for text in ("?", "？", "？？"):
            self.assertTrue(integrity.is_deviation_signal(text))
        for text in ("继续", "为什么", "好的", "??为什么"):
            self.assertFalse(integrity.is_deviation_signal(text))

    def test_execution_integrity_terminal_reasons_require_deterministic_closeout(self):
        self.assertTrue(integrity.requires_evidence_safe_closeout([
            "execution_obligation:observe_directory:missing_evidence"
        ]))
        self.assertTrue(integrity.requires_evidence_safe_closeout([
            "execution_claim_without_evidence"
        ]))
        self.assertFalse(integrity.requires_evidence_safe_closeout([
            "explicitly named deliverables are missing: report.md"
        ]))


class ExecutionIntegrityWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zong = (ROOT / "app/backend/tiangong-backend/v3/zongdiaodu.py").read_text(encoding="utf-8")
        cls.xujie = (ROOT / "app/backend/tiangong-backend/v3/shangxiawen_xujie.py").read_text(encoding="utf-8")


    def test_discussion_only_gate_controls_native_tool_exposure(self):
        self.assertIn("is_execution_discussion_only,", self.zong)
        self.assertIn("or is_execution_discussion_only(xiaoxi)", self.zong)

    def test_work_intent_uses_obligation_classifier(self):
        self.assertIn("if build_action_obligations(text):", self.zong)

    def test_final_gate_checks_integrity_before_zero_observation_escape(self):
        start = self.zong.index("def _simple_chain_final_hard_gate(")
        end = self.zong.index("\ndef ", start + 10)
        block = self.zong[start:end]
        self.assertIn("execution_integrity_blockers(", block)
        self.assertLess(block.index("execution_integrity_blockers("), block.index("if not quality_history:"))

    def test_run_state_contains_and_updates_obligations(self):
        self.assertIn('"obligations": [],', self.zong)
        self.assertIn("update_run_state_obligations(run_state, payload)", self.zong)

    def test_history_does_not_promote_prose_claim_to_fact(self):
        self.assertIn('assistant_claim_unverified', self.xujie)
        self.assertNotIn('status = "completed" if media_paths or re.search', self.xujie)

    def test_question_mark_gets_soft_deviation_signal(self):
        self.assertIn("_EXECUTION_DEVIATION_SIGNALS", self.xujie)
        self.assertIn("[执行偏差信号]", self.xujie)

    def test_integrity_blockers_have_user_visible_humanization(self):
        self.assertIn('("execution_obligation:", "还没有获得用户明确要求动作对应的真实工具执行证据")', self.zong)
        self.assertIn('("execution_claim_without_evidence", "模型给出了完成性描述，但没有对应的真实工具执行证据")', self.zong)

    def test_execution_integrity_closeout_bypasses_llm_polish(self):
        marker = "if requires_evidence_safe_closeout(clean_reasons):"
        closeout_call = "next_body, reply = _llm_closeout_scoped("
        self.assertIn(marker, self.zong)
        natural_start = self.zong.index("def _natural_closeout(")
        natural_end = self.zong.index("\n        def _check_stop", natural_start)
        block = self.zong[natural_start:natural_end]
        self.assertLess(block.index(marker), block.index(closeout_call))
        self.assertIn('"template_evidence_safe"', block)


if __name__ == "__main__":
    unittest.main()
