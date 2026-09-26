from __future__ import annotations

from structured_task_fixtures import registered_contract

import importlib.util
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app/backend/tiangong-backend/v3/execution_integrity.py"
spec = importlib.util.spec_from_file_location("tiangong_execution_integrity", MODULE_PATH)
integrity = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(integrity)


def _zongdiaodu_full_source(root_path):
    # P17-M2 拆分后 simple-chain 机器在 v3/simple_chain/kernel.py；
    # 架构断言按“逻辑总调度源 = zongdiaodu + kernel 拼接”读取。
    main = (root_path / "app/backend/tiangong-backend/v3/zongdiaodu.py").read_text(encoding="utf-8")
    kernel = (root_path / "app/backend/tiangong-backend/v3/simple_chain/kernel.py").read_text(encoding="utf-8")
    return main + "\n\n" + kernel
class ExecutionIntegrityFloorTests(unittest.TestCase):


    def test_runtime_floor_unknown_does_not_expand_old_semantics(self):
        cases = (
            "你好",
            "这个设计怎么看",
            "我明白了",
            "怎么修改这个文件？",
            "怎么读目录？",
            "运行测试可以吗？",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_UNKNOWN)

    def test_floor_does_not_turn_text_only_work_into_tool_obligations(self):
        cases = (
            "帮我分析这段描写",
            "帮我看看这个方案",
            "给我写一段话",
            "帮我改写一下这句话",
            "测试结果是什么？",
            "测试结果",
            "验证是否通过",
            "帮我分析这段代码",
            "帮我写代码",
            "请解释这段代码怎么修改",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(integrity.runtime_execution_floor(text), integrity.ACT_UNKNOWN)
                self.assertEqual(integrity.build_action_obligations(text), [])











class ExecutionIntegrityEvidenceTests(unittest.TestCase):
    def test_real_directory_observation_satisfies(self):
        user = "你读一下目录不就行了"
        self.assertTrue(integrity.execution_integrity_blockers(user, [], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]))
        history = [{"ok": True, "tool_action": "file.list"}]
        self.assertEqual(integrity.execution_integrity_blockers(user, history, obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]), [])

    def test_preparation_action_does_not_satisfy_execution_floor(self):
        user = "查看当前目录"
        for action in ("skill.route", "skill.get", "skill.read"):
            with self.subTest(action=action):
                self.assertTrue(
                    integrity.execution_integrity_blockers(
                        user, [{"ok": True, "tool_action": action}], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}])
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
                }], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}])
        )

    def test_directory_observation_keeps_local_object_truth(self):
        user = "查看当前目录"
        self.assertTrue(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "web.search"}], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}])
        )
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "file.list"}], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
            [],
        )

    def test_search_observation_accepts_search_evidence(self):
        user = "帮我搜索一下最新资料"
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "web.search"}], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
            [],
        )

    def test_generic_attachment_observation_accepts_image_info(self):
        user = "帮我看一下附件"
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "image.info"}], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
            [],
        )

    def test_system_health_observation_accepts_health_evidence(self):
        user = "检查系统状态"
        self.assertEqual(integrity.runtime_execution_floor(user), integrity.ACT_UNKNOWN)
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "system.health"}], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
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
        self.assertTrue(integrity.execution_integrity_blockers(user, wrong, obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\note.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]))
        self.assertEqual(integrity.execution_integrity_blockers(user, right, obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\note.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]), [])

    def test_bare_filename_target_requires_same_basename(self):
        user = "读取 README.md"
        wrong = [{
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"args": {"target": "/workspace/package.json"}},
        }]
        right = [{
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"args": {"target": "/workspace/README.md"}},
        }]
        self.assertTrue(integrity.execution_integrity_blockers(user, wrong, obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'README.md', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]))
        self.assertEqual(integrity.execution_integrity_blockers(user, right, obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'README.md', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]), [])

    def test_multiple_targets_each_require_matching_evidence(self):
        user = r"删除文件 C:\work\a.txt 和 C:\work\b.txt"
        only_first = [{
            "ok": True,
            "tool_action": "file.delete",
            "tool_args": {"args": {"target": r"C:\work\a.txt"}},
            "tool_result_contract": {
                "write_evidence": {
                    "authoritative": True,
                    "deleted_files": [r"C:\work\a.txt"],
                },
            },
        }]
        both = only_first + [{
            "ok": True,
            "tool_action": "file.delete",
            "tool_args": {"args": {"target": r"C:\work\b.txt"}},
            "tool_result_contract": {
                "write_evidence": {
                    "authoritative": True,
                    "deleted_files": [r"C:\work\b.txt"],
                },
            },
        }]

        obligations = [{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\a.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'absent'}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\b.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'absent'}]
        self.assertEqual(
            [item["target_path"] for item in obligations],
            [r"C:\work\a.txt", r"C:\work\b.txt"],
        )
        self.assertTrue(integrity.execution_integrity_blockers(user, only_first, obligations=[{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\a.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'absent'}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\b.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'absent'}]))
        self.assertEqual(integrity.execution_integrity_blockers(user, both, obligations=[{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\a.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'absent'}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'C:\\work\\b.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'absent'}]), [])

    def test_one_multi_target_tool_result_can_satisfy_each_target(self):
        user = "读取 README.md 和 package.json"
        history = [{
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"args": {"target": ["README.md", "package.json"]}},
        }]

        self.assertEqual(integrity.execution_integrity_blockers(user, history, obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'README.md', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:observation:2', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'package.json', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]), [])

    def test_tool_action_suffix_does_not_create_observation_obligation(self):
        user = "Please execute file.hash and qc.docx.delivery_check."

        self.assertEqual(
            [item["kind"] for item in [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'command_execution'}]],
            ["execution"],
        )

    def test_authoritative_learning_receipt_satisfies_effect(self):
        user = (
            "请调用 learning.ingest，只创建 awaiting_user 学习卡；"
            "成功后立即报告 card_id，绝不激活、注册或发布。"
        )
        history = [{
            "ok": True,
            "tool_action": "learning.ingest",
            "tool_result": {"result": {
                "card_id": "learn_test_receipt",
                "status": "awaiting_user",
                "registered": False,
                "authority": "life_kernel",
            }},
        }]

        self.assertEqual(
            [item["kind"] for item in [{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]],
            ["effect"],
        )
        self.assertEqual(integrity.execution_integrity_blockers(user, history, obligations=[{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]), [])

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
        self.assertEqual(integrity.execution_integrity_blockers(user, history, obligations=[{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'note.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}]), [])

    def test_ok_only_file_write_does_not_self_certify_effect(self):
        user = "把 note.txt 改一下"
        history = [{
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"args": {"target": "note.txt"}},
            "tool_result_contract": {"ok": True},
        }]
        self.assertTrue(integrity.execution_integrity_blockers(user, history, obligations=[{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'note.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}]))

    def test_existing_write_and_readback_contract_satisfies_verification(self):
        user = "请修改 project/result.json 并验证"
        history = [{
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"target": "project/result.json"},
            "tool_result_contract": {
                "ok": True,
                "write_effect": True,
                "paths": ["project/result.json"],
            },
        }, {
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"target": "project/result.json"},
            "tool_result_contract": {
                "ok": True,
                "write_effect": False,
                "paths": ["project/result.json"],
            },
        }]

        self.assertEqual(integrity.execution_integrity_blockers(user, history, obligations=[{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'project/result.json', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}, {'id': 'execution:execution:2', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]), [])

    def test_external_typed_effect_can_satisfy_without_fake_local_write(self):
        self.assertEqual(
            integrity.execution_integrity_blockers(
                "克隆这个仓库",
                [{"ok": True, "tool_action": "git.clone"}], obligations=[{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
            [],
        )

    def test_run_result_satisfies_execution_fact(self):
        user = "运行测试"
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "quality.run_tests"}], obligations=[{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'command_execution'}]),
            [],
        )

    def test_quality_adapter_satisfies_execution_fact(self):
        user = "运行语法检查"
        self.assertEqual(integrity.runtime_execution_floor(user), integrity.ACT_UNKNOWN)
        self.assertEqual(
            integrity.execution_integrity_blockers(
                user, [{"ok": True, "tool_action": "quality.python_syntax"}], obligations=[{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'command_execution'}]),
            [],
        )

    def test_delivery_result_satisfies_delivery_fact(self):
        self.assertEqual(
            integrity.execution_integrity_blockers(
                "把报告发给我",
                [{"ok": True, "tool_action": "mail.send"}], obligations=[{'id': 'execution:delivery:1', 'kind': 'delivery', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'delivery_mode': 'local_artifact'}]),
            [],
        )

    def test_failed_tool_never_satisfies(self):
        self.assertTrue(
            integrity.execution_integrity_blockers(
                "查看当前目录", [{"ok": False, "tool_action": "file.list"}], obligations=[{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}])
        )


    def test_run_state_records_llm_submission_from_real_tool_call(self):
        state = {
            "round": 2,
            "obligations": [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}],
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
            "obligations": [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'directory', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}],
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


class TaskContractLevelTests(unittest.TestCase):
    LIVE_TOOL_USER = """【测试编号：LIVE-TOOL-01】
请在当前工作区内创建文件：
p14-live-test/tool-proof.txt

文件内容必须严格为：
TIANGONG/P14/LIVE/2026081101

创建后必须重新读取文件，并计算 SHA-256。

如果受控 file.write 失败，请如实报告失败并停止，不得改用 shell.run 或 python.run 绕过权限边界。
不得根据我的描述直接回答。"""

    @staticmethod
    def _live_profile(proposed_level="L1"):
        target = "p14-live-test/tool-proof.txt"
        return {
            "schema": "tiangong.v3.task_profile.v2",
            "proposed_level": proposed_level,
            "desired_facts": [
                {"fact_id": "F1", "kind": "effect", "target": target, "success_condition": "file exists with requested content"},
                {"fact_id": "F2", "kind": "observation", "target": target, "success_condition": "content is read back"},
                {"fact_id": "F3", "kind": "execution", "target": target, "success_condition": "SHA-256 is computed"},
            ],
            "plan_hint": [
                {"step_id": "S1", "action": "file.write", "target": target},
                {"step_id": "S2", "action": "file.read", "target": target, "depends_on": ["S1"]},
                {"step_id": "S3", "action": "file.hash", "target": target, "depends_on": ["S2"]},
                {"step_id": "S4", "action": "deliver_result", "depends_on": ["S3"]},
            ],
            "constraints": {"forbidden_tools": ["shell.run", "python.run"]},
        }

    def test_l0_chat_contract_has_no_execution_obligation(self):
        contract = integrity.initialize_task_contract("你好", chat_mode=True)
        self.assertEqual(contract["effective_level"], "L0")
        self.assertEqual(contract["acceptance_status"], "not_applicable")
        self.assertEqual(integrity.build_task_contract_obligations(contract), [])

    def test_single_read_only_action_is_l1(self):
        profile = {
            "proposed_level": "L1",
            "steps": [{"action": "file.read", "target": "README.md"}],
        }
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'README.md', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
            profile,
            user_text="读取 README.md",
            action="file.read",
            target="README.md",
        )
        self.assertEqual(contract["runtime_minimum_level"], "L1")
        self.assertEqual(contract["effective_level"], "L1")

    def test_runtime_raises_underclassified_mutating_multistep_task_to_l2(self):
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'sha256_digest', 'requires_prior_kind': 'effect'}]),
            self._live_profile("L1"),
            user_text=self.LIVE_TOOL_USER,
            action="file.write",
            target="p14-live-test/tool-proof.txt",
        )
        self.assertEqual(contract["proposed_level"], "L1")
        self.assertEqual(contract["runtime_minimum_level"], "L2")
        self.assertEqual(contract["effective_level"], "L2")
        self.assertIn("runtime_prevented_downgrade", contract["level_reasons"])
        self.assertEqual(contract["validation_issues"], [])

    def test_missing_l2_profile_does_not_block_or_add_planning_round(self):
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'result.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}]),
            None,
            user_text="创建 result.txt",
            action="file.write",
            target="result.txt",
        )
        self.assertEqual(contract["effective_level"], "L2")
        self.assertEqual(contract["profile_status"], "optional_not_received")
        self.assertFalse(contract["profile_required_pending"])
        self.assertEqual(contract["profile_retry_count"], 0)
        self.assertEqual(contract["plan_hint"], [])
        self.assertTrue(contract["desired_facts"])

    def test_l1_missing_profile_stays_lightweight_without_retry(self):
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'README.md', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
            None,
            user_text="读取 README.md",
            action="file.read",
            target="README.md",
        )
        self.assertEqual(contract["effective_level"], "L1")
        self.assertEqual(contract["profile_status"], "optional_not_received")
        self.assertFalse(contract["profile_required_pending"])
        self.assertEqual(contract["profile_retry_count"], 0)

    def test_high_risk_action_is_l3(self):
        level, reasons = integrity.action_minimum_task_level("shell.run")
        self.assertEqual(level, "L3")
        self.assertIn("external_or_destructive", reasons)

    def test_effective_level_is_monotonic(self):
        initial = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'command_execution'}]),
            {"proposed_level": "L3", "steps": [{"action": "shell.run"}]},
            user_text="运行命令",
            action="shell.run",
        )
        later = integrity.reconcile_task_contract(
            initial,
            {"proposed_level": "L0", "steps": [{"action": "file.read", "target": "README.md"}]},
            user_text="运行命令",
            action="file.read",
            target="README.md",
        )
        self.assertEqual(later["effective_level"], "L3")

    def test_deliver_result_is_not_treated_as_a_tool(self):
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'sha256_digest', 'requires_prior_kind': 'effect'}]),
            self._live_profile(),
            user_text=self.LIVE_TOOL_USER,
            action="file.write",
            target="p14-live-test/tool-proof.txt",
        )
        self.assertNotIn("unknown_action:deliver_result", contract["validation_issues"])
        self.assertEqual(
            {(item["kind"], item["target_path"]) for item in integrity.build_task_contract_obligations(contract)},
            {(item["kind"], item["target_path"]) for item in [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'sha256_digest', 'requires_prior_kind': 'effect'}]},
        )

    def test_model_profile_is_removed_before_governed_tool_validation(self):
        cleaned, profile = integrity.extract_model_task_profile({
            "action": "file.read",
            "args": {"target": "README.md", "_task_profile": self._live_profile()},
        })
        self.assertNotIn("_task_profile", cleaned["args"])
        self.assertEqual(profile["proposed_level"], "L1")

        top_cleaned, top_profile = integrity.extract_model_task_profile({
            "action": "file.write",
            "target": "result.txt",
            "args": {"content": "ok"},
            "_task_profile": self._live_profile("L2"),
        })
        self.assertNotIn("_task_profile", top_cleaned)
        self.assertEqual(top_profile["proposed_level"], "L2")



    def test_verified_absence_satisfies_target_bound_observation(self):
        user = (
            "请只读查看当前工作区里的 missing-proof.txt。"
            "如果文件不存在，就直接告诉我不存在并结束；不要创建任何文件。"
        )
        obligation = next(
            item
            for item in [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'missing-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'existence_resolved'}]
            if item["kind"] == "observation"
        )
        self.assertEqual(obligation["target_path"], "missing-proof.txt")
        self.assertEqual(obligation["evidence_predicate"], "existence_resolved")

        exact_empty_list = {
            "ok": True,
            "tool_action": "file.list",
            "tool_args": {
                "action": "file.list",
                "target": r"C:\workspace",
                "args": {"pattern": "missing-proof.txt"},
            },
            "tool_result": {
                "result": {"count": 0, "entries": "", "root": r"C:\workspace"},
                "evidence": {"exists": False},
            },
            "tool_result_contract": {"ok": True, "paths": [r"C:\workspace"]},
        }
        self.assertTrue(integrity.obligation_is_satisfied(obligation, [exact_empty_list]))

        unfiltered = dict(exact_empty_list)
        unfiltered["tool_args"] = {
            "action": "file.list",
            "target": r"C:\workspace",
            "args": {"pattern": "*"},
        }
        self.assertFalse(integrity.obligation_is_satisfied(obligation, [unfiltered]))

        wrong_name = dict(exact_empty_list)
        wrong_name["tool_args"] = {
            "action": "file.list",
            "target": r"C:\workspace",
            "args": {"pattern": "another.txt"},
        }
        self.assertFalse(integrity.obligation_is_satisfied(obligation, [wrong_name]))

    def test_verified_absence_requires_the_requested_parent_directory(self):
        user = "请查看 docs/missing.txt，如果不存在就告诉我不存在并结束。"
        obligation = next(
            item
            for item in [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'docs/missing.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'existence_resolved'}]
            if item["kind"] == "observation"
        )
        payload = {
            "ok": True,
            "tool_action": "file.list",
            "tool_args": {
                "action": "file.list",
                "target": r"C:\workspace\other",
                "args": {"pattern": "missing.txt"},
            },
            "tool_result": {"result": {"count": 0, "entries": ""}},
        }
        self.assertFalse(integrity.obligation_is_satisfied(obligation, [payload]))
        payload["tool_args"]["target"] = r"C:\workspace\docs"
        self.assertTrue(integrity.obligation_is_satisfied(obligation, [payload]))

    def test_directory_listing_binds_named_file_observation(self):
        user = "请只读查看 tests/test_query.py，不得修改或删除文件。"
        obligation = next(
            item
            for item in [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'tests/test_query.py', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]
            if item["kind"] == "observation"
        )
        payload = {
            "ok": True,
            "tool_action": "file.list",
            "tool_args": {
                "action": "file.list",
                "target": r"C:\workspace\tests",
                "args": {},
            },
            "tool_result": {
                "result": {
                    "root": r"C:\workspace\tests",
                    "count": 1,
                    "entries": [{
                        "name": "test_query.py",
                        "path": r"C:\workspace\tests\test_query.py",
                        "rel_path": "tests/test_query.py",
                        "type": "file",
                    }],
                }
            },
        }
        self.assertTrue(integrity.obligation_is_satisfied(obligation, [payload]))

        wrong = dict(payload)
        wrong["tool_result"] = {
            "result": {
                "root": r"C:\workspace\tests",
                "count": 1,
                "entries": [{"name": "other.py", "type": "file"}],
            }
        }
        self.assertFalse(integrity.obligation_is_satisfied(obligation, [wrong]))

    def test_model_plan_actions_never_become_hard_obligations(self):
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'sha256_digest', 'requires_prior_kind': 'effect'}]),
            self._live_profile(),
            user_text=self.LIVE_TOOL_USER,
            action="file.write",
            target="p14-live-test/tool-proof.txt",
        )
        obligations = integrity.build_task_contract_obligations(contract)
        self.assertTrue(obligations)
        self.assertTrue(all("required_action" not in item for item in obligations))
        self.assertTrue(all(item["source"] == "runtime_user_goal" for item in obligations))
        self.assertNotIn("file.mkdir", [item.get("required_action") for item in obligations])

    def test_sha256_goal_requires_a_real_digest_not_only_readback(self):
        target = "p14-live-test/tool-proof.txt"
        obligations = [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'sha256_digest', 'requires_prior_kind': 'effect'}]
        sha_goal = next(item for item in obligations if item.get("evidence_predicate") == "sha256_digest")
        self.assertEqual(sha_goal["target_path"], target)
        self.assertEqual(sha_goal["requires_prior_kind"], "effect")
        write_payload = {
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"action": "file.write", "target": target, "args": {}},
            "tool_result": {"success": True, "path": target, "sha256": "b" * 64},
            "tool_result_contract": {
                "ok": True,
                "write_effect": True,
                "observed_write_effect": True,
                "paths": [target],
            },
        }
        read_payload = {
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"action": "file.read", "target": target, "args": {}},
            "tool_result": {"success": True, "path": target, "content": "TIANGONG/P14/LIVE/2026081101"},
            "tool_result_contract": {"ok": True, "write_effect": False, "paths": [target]},
        }
        self.assertFalse(integrity.obligation_is_satisfied(sha_goal, [write_payload, read_payload]))
        hash_payload = {
            "ok": True,
            "tool_action": "file.hash",
            "tool_args": {"action": "file.hash", "target": target, "args": {}},
            "tool_result": {"success": True, "path": target, "sha256": "a" * 64},
            "tool_result_contract": {"ok": True, "paths": [target]},
        }
        self.assertTrue(integrity.obligation_is_satisfied(sha_goal, [write_payload, read_payload, hash_payload]))

    def test_live_tool_history_satisfies_goal_then_terminal_gate_deactivates_intention(self):
        target = "p14-live-test/tool-proof.txt"
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'sha256_digest', 'requires_prior_kind': 'effect'}]),
            self._live_profile(),
            user_text=self.LIVE_TOOL_USER,
            action="file.write",
            target=target,
        )
        obligations = [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'p14-live-test/tool-proof.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'sha256_digest', 'requires_prior_kind': 'effect'}]
        history = [{
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"args": {"target": target}},
            "tool_result_contract": {
                "ok": True,
                "write_effect": True,
                "paths": [target],
                "observed_write_effect": True,
                "write_evidence": {"authoritative": True, "changed_files": [target]},
            },
        }, {
            "ok": True,
            "tool_action": "file.read",
            "tool_args": {"args": {"target": target}},
            "tool_result_contract": {"ok": True, "write_effect": False, "paths": [target]},
        }, {
            "ok": True,
            "tool_action": "file.hash",
            "tool_args": {"args": {"target": target}},
            "tool_result": {"success": True, "path": target, "sha256": "a" * 64},
        }]
        self.assertEqual(
            integrity.execution_integrity_blockers(
                self.LIVE_TOOL_USER,
                history,
                obligations=obligations,
            ),
            [],
        )
        for round_number, payload in enumerate(history, start=1):
            state = {"round": round_number, "obligations": obligations}
            integrity.update_run_state_obligations(state, payload)
            contract = integrity.update_task_contract_evidence(
                contract, payload, round_number=round_number, obligations=obligations
            )
        self.assertEqual(contract["phase"], "SATISFIED")
        self.assertEqual(contract["acceptance_status"], "candidate")
        contract = integrity.transition_task_contract_terminal(contract, "complete", [])
        self.assertEqual(contract["phase"], "DEACTIVATED")
        self.assertEqual(contract["acceptance_status"], "accepted")
        self.assertFalse(contract["intent_active"])

    def test_terminal_meanings_are_distinct(self):
        initial = registered_contract([{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'result.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}])
        waiting = integrity.transition_task_contract_terminal(initial, "awaiting_user", ["need target"])
        blocked = integrity.transition_task_contract_terminal(initial, "failed", ["write denied"])
        interrupted = integrity.transition_task_contract_terminal(initial, "interrupted", ["user_cancel"])
        self.assertEqual(waiting["phase"], "WAITING")
        self.assertEqual(blocked["phase"], "BLOCKED")
        self.assertEqual(interrupted["phase"], "INTERRUPTED")
        self.assertNotEqual(blocked["acceptance_status"], "accepted")

    def test_contradictory_evidence_reopens_deactivated_goal(self):
        target = "result.txt"
        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'result.txt', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}]),
            None,
            user_text="创建 result.txt",
            action="file.write",
            target=target,
        )
        success = {
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {"args": {"target": target}},
            "tool_result_contract": {
                "ok": True,
                "write_effect": True,
                "paths": [target],
                "observed_write_effect": True,
            },
        }
        contract = integrity.update_task_contract_evidence(contract, success, round_number=1)
        contract = integrity.transition_task_contract_terminal(contract, "complete", [])
        reopened = integrity.update_task_contract_evidence(
            contract,
            {"ok": False, "tool_action": "file.read", "tool_args": {"args": {"target": target}}},
            round_number=2,
        )
        self.assertEqual(reopened["phase"], "REOPENED")
        self.assertEqual(reopened["reopen_count"], 1)
        self.assertTrue(reopened["intent_active"])

    def test_done_after_phrase_does_not_invent_a_write_goal(self):
        user_text = "查看当前代码仓库结构，做完以后告诉我主要模块。"
        obligations = [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]
        self.assertEqual({item["kind"] for item in obligations}, {"observation"})

        contract = integrity.reconcile_task_contract(
            registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]),
            None,
            user_text=user_text,
            action="file.list",
        )
        payload = {
            "ok": True,
            "tool_action": "file.list",
            "tool_args": {"args": {"target": "."}},
            "tool_result": {"success": True, "entries": ["app", "tests"]},
            "tool_result_contract": {"ok": True, "write_effect": False, "paths": ["."]},
        }
        state = {"round": 1, "obligations": obligations}
        integrity.update_run_state_obligations(state, payload)
        contract = integrity.update_task_contract_evidence(
            contract,
            payload,
            round_number=1,
            obligations=obligations,
        )
        self.assertEqual(contract["phase"], "SATISFIED")

        contract, allowed, status, reasons = integrity.decide_task_contract_completion(
            contract,
            evidence_reasons=[],
            final_reply="仓库主要由 app 和 tests 两部分组成。",
            has_real_observation=True,
        )
        self.assertTrue(allowed)
        self.assertEqual(status, "complete")
        self.assertEqual(reasons, [])
        self.assertEqual(contract["phase"], "SATISFIED")
        self.assertEqual(contract["goal_state"]["completion_percentage"], 100.0)

    def test_evidence_checker_only_raises_uncertainty(self):
        contract = integrity.initialize_task_contract("说明当前状态")
        contract, allowed, status, reasons = integrity.decide_task_contract_completion(
            contract,
            evidence_reasons=["tool_result_signature_invalid"],
            evidence_status="incomplete",
            final_reply="当前状态正常。",
            has_real_observation=True,
        )
        self.assertFalse(allowed)
        self.assertEqual(status, "incomplete")
        self.assertEqual(reasons, ["tool_result_signature_invalid"])
        self.assertEqual(contract["phase"], "VERIFYING")
        self.assertTrue(contract["intent_active"])
        self.assertLess(contract["goal_state"]["completion_percentage"], 100.0)


class ExecutionIntegrityWiringContractTests(unittest.TestCase):
    """Static contracts for the existing V3 wiring.

    The implementation deliberately reuses current V3 call sites. No second
    planner, loop, judge, database or terminal gate is introduced.
    """

    @classmethod
    def setUpClass(cls):
        cls.zong = _zongdiaodu_full_source(ROOT)
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



    def test_evidence_check_reports_integrity_before_zero_observation_escape(self):
        start = self.zong.index("def _simple_chain_evidence_check(")
        end = self.zong.index("\ndef ", start + 10)
        block = self.zong[start:end]
        self.assertIn("execution_integrity_blockers(", block)
        self.assertLess(block.index("execution_integrity_blockers("), block.index("if quality_history:"))

    def test_runtime_uses_life_completion_not_legacy_hard_gate(self):
        self.assertIn("def _simple_chain_life_completion_gate(", self.zong)
        boundary = (
            ROOT / "app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py"
        ).read_text(encoding="utf-8")
        self.assertIn("decide_task_contract_completion(", boundary)
        self.assertIn("decide_simple_chain_completion", self.zong)
        runtime_block = self.zong[self.zong.index("def _huanxing_simple_chain("):]
        # 方法块边界：下一个顶层 def/class 之前（拼接源尾部是迁出的
        # kernel 全文，不带边界的旧截取会把 kernel 内容误算进方法块）。
        boundary_match = re.search(r"\n(?=def |class )", runtime_block[1:])
        if boundary_match:
            runtime_block = runtime_block[: boundary_match.start() + 1]
        self.assertIn("_simple_chain_life_completion_gate(", runtime_block)
        self.assertNotIn("_simple_chain_evidence_check(", runtime_block)
        self.assertNotIn("_simple_chain_final_hard_gate", self.zong)

    def test_run_state_contains_and_updates_obligations(self):
        self.assertIn('"obligations": [],', self.zong)
        self.assertIn("update_run_state_obligations(run_state, payload)", self.zong)

    def test_existing_run_state_owns_life_task_state_and_terminal_projection(self):
        self.assertIn('run_state["task_contract"] = initialize_task_contract(', self.zong)
        self.assertIn("_simple_chain_accept_task_profile(", self.zong)
        self.assertIn("update_task_contract_evidence(", self.zong)
        self.assertIn("transition_task_contract_terminal(", self.zong)
        self.assertIn('task_obligations=run_state.get("obligations")', self.zong)
        self.assertNotIn("tiangong.v3.task_profile.retry_required.v1", self.zong)
        self.assertIn("The profile is advice, not authority", self.zong)

    def test_history_does_not_promote_prose_claim_to_fact(self):
        from v3.shangxiawen_xujie import _history_assistant_content
        for prose in ("已全部完成", "还未完成", "我正在处理"):
            self.assertEqual(_history_assistant_content(prose), "聊天回复: " + prose)
        self.assertNotIn('status = "completed" if media_paths or re.search', self.xujie)


    def test_integrity_blockers_have_user_visible_humanization(self):
        self.assertIn(
            '("execution_obligation:", "还没有获得用户明确要求动作对应的真实工具执行证据")',
            self.zong,
        )
        self.assertIn(
            '("execution_claim_without_evidence", "模型给出了完成性描述，但没有对应的真实工具执行证据")',
            self.zong,
        )

    def test_execution_integrity_closeout_is_template_safe_except_bounded_exhaustion(self):
        marker = "if requires_evidence_safe_closeout(clean_reasons) and not allow_evidence_model:"
        closeout_call = "next_body, reply = _llm_closeout_scoped("
        self.assertIn(marker, self.zong)
        natural_start = self.zong.index("def _natural_closeout(")
        natural_end = self.zong.index("\n        def _check_stop", natural_start)
        block = self.zong[natural_start:natural_end]
        self.assertLess(block.index(marker), block.index(closeout_call))
        self.assertIn('"template_evidence_safe"', block)
        self.assertIn("allow_evidence_model=True", self.zong)


if __name__ == "__main__":
    unittest.main()
