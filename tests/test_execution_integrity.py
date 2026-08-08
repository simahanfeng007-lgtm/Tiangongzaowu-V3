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

    def test_runtime_floor_forbids_high_confidence_discussion_only(self):
        cases = (
            "你会读目录吗？",
            "如果让你读目录，你会怎么做？",
            "先别读，只告诉我怎么处理",
            "不要使用工具，只分析目录读取方案",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_FORBIDDEN)

    def test_runtime_floor_unknown_for_normal_chat(self):
        for text in ("你好", "这个设计怎么看", "我明白了"):
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_UNKNOWN)

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

    def test_public_api_required_by_zongdiaodu_is_preserved(self):
        for name in (
            "build_action_obligations",
            "execution_integrity_blockers",
            "is_execution_discussion_only",
            "requires_evidence_safe_closeout",
            "update_run_state_obligations",
        ):
            self.assertTrue(callable(getattr(integrity, name, None)), name)


if __name__ == "__main__":
    unittest.main()
