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


class ExecutionIntegrityFloorTests(unittest.TestCase):
    def test_runtime_floor_requires_explicit_action(self):
        cases = (
            "你读一下目录不就行了",
            "看看当前目录",
            "把这个文件改一下",
            "运行一下测试",
            "把报告发给我",
            "read the file",
            "please run the tests",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_REQUIRED)

    def test_runtime_floor_forbids_existing_high_confidence_discussion_only(self):
        cases = (
            "你会读目录吗？",
            "如果让你读目录，你会怎么做？",
            "先别读，只告诉我怎么处理",
            "不要使用工具，只分析目录读取方案",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_FORBIDDEN)

    def test_runtime_floor_unknown_does_not_expand_old_semantics(self):
        cases = (
            "你好",
            "这个设计怎么看",
            "我明白了",
            "怎么修改这个文件？",
            "怎么读目录？",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_UNKNOWN)

    def test_floor_preserves_existing_command_ambiguity(self):
        # V3 already treats this wording as executable. This refactor must not
        # silently redefine global chat/work semantics while fixing integrity.
        self.assertEqual(
            integrity.runtime_execution_floor("运行测试可以吗？"),
            integrity.ACT_REQUIRED,
        )

    def test_scoped_negation_does_not_create_forbidden_effect_obligation(self):
        text = "看看当前目录里有哪些文件，先别改任何东西"
        items = integrity.build_action_obligations(text)
        self.assertEqual([item["kind"] for item in items], ["observation"])
        self.assertEqual(
            integrity.execution_integrity_blockers(
                text, [{"ok": True, "tool_action": "file.list"}]
            ),
            [],
        )

    def test_scoped_negation_preserves_other_explicit_actions(self):
        cases = {
            "先别读这个文件，查看一下当前目录": ["observation"],
            "别删除，先运行测试": ["execution"],
            "不要运行测试，只修改代码": ["effect"],
            "先别读，只修改这个文件": ["effect"],
            "把这个文件修改一下，但不要运行测试": ["effect"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_REQUIRED)
                self.assertEqual([item["kind"] for item in integrity.build_action_obligations(text)], expected)

    def test_global_stop_stays_forbidden(self):
        for text in ("先不要执行", "不要做任何操作", "不要使用工具，只分析方案"):
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_FORBIDDEN)
                self.assertEqual(integrity.build_action_obligations(text), [])

    def test_conditional_fix_is_actionable(self):
        text = "如果发现错误，那就修复"
        self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_REQUIRED)
        self.assertEqual([item["kind"] for item in integrity.build_action_obligations(text)], ["effect"])

    def test_obligations_are_fact_classes_not_tool_plans(self):
        cases = {
            "查看当前目录": "observation",
            "修改这个文件": "effect",
            "运行测试": "execution",
            "把报告发给我": "delivery",
        }
        for text, kind in cases.items():
            with self.subTest(text=text):
                items = integrity.build_action_obligations(text)
                self.assertTrue(items)
                self.assertIn(kind, [item["kind"] for item in items])
                self.assertTrue(all("required_tool" not in item for item in items))
                self.assertTrue(all(item["floor"] == integrity.ACT_REQUIRED for item in items))

    def test_conditional_real_command_remains_actionable(self):
        text = "如果当前目录里有 package.json，就读一下当前目录"
        self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_REQUIRED)
        self.assertTrue(integrity.build_action_obligations(text))

    def test_ambiguous_target_allows_clarification(self):
        items = integrity.build_action_obligations("读一下那个目录")
        self.assertTrue(items)
        self.assertTrue(all(item["status"] == "needs_clarification" for item in items))
        self.assertTrue(all(not item["actionable"] for item in items))
        self.assertEqual(integrity.execution_integrity_blockers("读一下那个目录", []), [])


class ExecutionIntegrityEvidenceTests(unittest.TestCase):
    def test_real_directory_observation_satisfies(self):
        user = "你读一下目录不就行了"
        self.assertTrue(integrity.execution_integrity_blockers(user, []))
        history = [{"ok": True, "tool_action": "file.list"}]
        self.assertEqual(integrity.execution_integrity_blockers(user, history), [])

    def test_preparation_action_does_not_satisfy_execution_floor(self):
        user = "查看当前目录"
        for action in ("skill.route", "skill.get", "skill.read"):
            with self.subTest(action=action):
                self.assertTrue(
                    integrity.execution_integrity_blockers(
                        user, [{"ok": True, "tool_action": action}]
                    )
                )

    def test_unrelated_fact_class_does_not_satisfy_observation(self):
        user = "查看当前目录"
        self.assertTrue(
            integrity.execution_integrity_blockers(
                user,
                [{
                    "ok": True,
                    "tool_action": "file.write",
                    "tool_result_contract": {
                        "observed_write_effect": True,
                        "write_evidence": {
                            "authoritative": True,
                            "changed_files": ["note.txt"],
                        },
                    },
                }],
            )
        )

    def test_directory_observation_keeps_local_object_truth(self):
        user = "查看当前目录"
        self.assertTrue(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "web.search"}]
            )
        )
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "file.list"}]
            ),
            [],
        )

    def test_explicit_target_still_requires_matching_target(self):
        user = r"读取 C:\work\note.txt"
        wrong = [{
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"args": {"target": r"C:\work\other.txt"}},
        }]
        right = [{
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"args": {"target": r"C:\work\note.txt"}},
        }]
        self.assertTrue(integrity.execution_integrity_blockers(user, wrong))
        self.assertEqual(integrity.execution_integrity_blockers(user, right), [])

    def test_write_effect_uses_existing_authoritative_contract(self):
        user = "把 note.txt 改一下"
        history = [{
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"args": {"target": "note.txt"}},
            "tool_result_contract": {
                "observed_write_effect": True,
                "write_evidence": {
                    "authoritative": True,
                    "changed_files": ["note.txt"],
                },
            },
        }]
        self.assertEqual(integrity.execution_integrity_blockers(user, history), [])

    def test_ok_only_file_write_does_not_self_certify_effect(self):
        user = "把 note.txt 改一下"
        history = [{
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"args": {"target": "note.txt"}},
            "tool_result_contract": {"ok": True},
        }]
        self.assertTrue(integrity.execution_integrity_blockers(user, history))

    def test_run_result_satisfies_execution_fact(self):
        user = "运行测试"
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "quality.run_tests"}]
            ),
            [],
        )

    def test_failed_tool_never_satisfies(self):
        self.assertTrue(
            integrity.execution_integrity_blockers(
                "查看当前目录", [{"ok": False, "tool_action": "file.list"}]
            )
        )

    def test_completion_claim_without_evidence_is_blocked(self):
        blockers = integrity.execution_integrity_blockers(
            "查看当前目录",
            [],
            final_reply="我已经查看完毕。",
        )
        self.assertIn("execution_claim_without_evidence", blockers)

    def test_run_state_records_llm_submission_from_real_tool_call(self):
        state = {
            "round": 2,
            "obligations": integrity.build_action_obligations("查看当前目录"),
        }
        integrity.update_run_state_obligations(
            state, {"ok": True, "tool_action": "file.list"}
        )
        item = state["obligations"][0]
        self.assertEqual(item["status"], "satisfied")
        self.assertEqual(item["llm_submission_action"], "file.list")
        self.assertTrue(item["evidence_ok"])

    def test_nonmatching_submission_is_recorded_but_not_satisfied(self):
        state = {
            "round": 2,
            "obligations": integrity.build_action_obligations("查看当前目录"),
        }
        integrity.update_run_state_obligations(
            state,
            {
                "ok": True,
                "tool_action": "file.write",
                "tool_result_contract": {
                    "observed_write_effect": True,
                    "write_evidence": {
                        "authoritative": True,
                        "changed_files": ["x.txt"],
                    },
                },
            },
        )
        item = state["obligations"][0]
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["last_attempt_action"], "file.write")

    def test_deviation_signal_remains_narrow(self):
        for text in ("?", "？", "？？"):
            self.assertTrue(integrity.is_deviation_signal(text))
        for text in ("继续", "为什么", "好的", "??为什么"):
            self.assertFalse(integrity.is_deviation_signal(text))

    def test_integrity_terminal_reasons_require_deterministic_closeout(self):
        self.assertTrue(
            integrity.requires_evidence_safe_closeout(
                ["execution_obligation:observation:missing_evidence"]
            )
        )
        self.assertTrue(
            integrity.requires_evidence_safe_closeout(
                ["execution_claim_without_evidence"]
            )
        )
        self.assertFalse(
            integrity.requires_evidence_safe_closeout(
                ["explicitly named deliverables are missing: report.md"]
            )
        )


class ExecutionIntegrityWiringContractTests(unittest.TestCase):
    """Static contracts for the existing V3 wiring.

    The implementation deliberately reuses the current call sites. No second
    planner, loop, judge, database or terminal gate is introduced.
    """

    @classmethod
    def setUpClass(cls):
        cls.zong = (ROOT / "app/backend/tiangong-backend/v3/zongdiaodu.py").read_text(encoding="utf-8")
        cls.xujie = (ROOT / "app/backend/tiangong-backend/v3/shangxiawen_xujie.py").read_text(encoding="utf-8")

    def test_public_api_required_by_zongdiaodu_is_preserved(self):
        for name in (
            "build_action_obligations",
            "execution_integrity_blockers",
            "is_execution_discussion_only",
            "requires_evidence_safe_closeout",
            "update_run_state_obligations",
        ):
            self.assertTrue(callable(getattr(integrity, name, None)), name)

    def test_discussion_only_gate_controls_native_tool_exposure(self):
        self.assertIn("is_execution_discussion_only,", self.zong)
        self.assertIn("or is_execution_discussion_only(xiaoxi)", self.zong)

    def test_work_intent_uses_runtime_floor_via_obligations(self):
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
        self.assertIn("assistant_claim_unverified", self.xujie)
        self.assertNotIn('status = "completed" if media_paths or re.search', self.xujie)

    def test_question_mark_gets_soft_deviation_signal(self):
        self.assertIn("_EXECUTION_DEVIATION_SIGNALS", self.xujie)
        self.assertIn("[执行偏差信号]", self.xujie)

    def test_integrity_blockers_have_user_visible_humanization(self):
        self.assertIn(
            '("execution_obligation:", "还没有获得用户明确要求动作对应的真实工具执行证据")',
            self.zong,
        )
        self.assertIn(
            '("execution_claim_without_evidence", "模型给出了完成性描述，但没有对应的真实工具执行证据")',
            self.zong,
        )

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
