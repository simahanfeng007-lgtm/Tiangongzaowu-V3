from __future__ import annotations

import unittest

from pydantic import ValidationError

from contracts import TaskContinuityCapsule, WorkspaceFileRef
from tests.life_contract_support import HASH_ZERO


REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
EFFECT_ID = "eff_" + "3" * 64


def capsule(**overrides) -> TaskContinuityCapsule:
    values = {
        "capsule_id": "lcp_" + "1" * 64,
        "life_id": "life_contract_test",
        "capsule_kind": "WORKING_CHECKPOINT",
        "request_id": REQUEST_ID,
        "run_id": RUN_ID,
        "generation": 1,
        "episode_id": "cep_" + "4" * 64,
        "user_goal": "完成长任务。",
        "hard_constraints": ("不得丢失用户约束。",),
        "active_plan": ("核验输入", "继续执行"),
        "verified_fact_ids": ("fact_verified",),
        "causal_hypothesis_ids": (),
        "workspace_manifest": (
            WorkspaceFileRef(
                relative_path="project/plan.md",
                sha256="5" * 64,
                size_bytes=100,
                revision=1,
            ),
        ),
        "artifact_refs": (),
        "unresolved_questions": (),
        "pending_effect_ids": (EFFECT_ID,),
        "latest_safe_step": "核验输入完成。",
        "next_step": "协调未完成工具事务。",
        "recovery_preconditions": ("确认 effect 状态。",),
        "continuation_token_sha256": "6" * 64,
        "final_result": None,
        "supersedes_capsule_id": None,
        "retention_class": "CHECKPOINT",
        "created_at_ms": 2_000,
        "capsule_sha256": HASH_ZERO,
    }
    values.update(overrides)
    return TaskContinuityCapsule(**values)


class ContinuityCapsuleTests(unittest.TestCase):
    def test_checkpoint_is_tamper_evident_and_contains_recovery_state(self) -> None:
        value = capsule().with_computed_capsule_sha256()
        self.assertTrue(value.has_valid_capsule_sha256())
        self.assertFalse(
            value.model_copy(update={"next_step": "跳过协调"}).has_valid_capsule_sha256()
        )

    def test_terminal_result_forgets_pending_process_state(self) -> None:
        terminal = capsule(
            capsule_kind="TERMINAL_RESULT",
            pending_effect_ids=(),
            latest_safe_step=None,
            next_step=None,
            recovery_preconditions=(),
            continuation_token_sha256=None,
            final_result="最终交付物已经生成并验证。",
            retention_class="TERMINAL_RESULT",
        ).with_computed_capsule_sha256()
        self.assertTrue(terminal.has_valid_capsule_sha256())
        self.assertEqual(terminal.pending_effect_ids, ())
        self.assertIsNone(terminal.next_step)

    def test_terminal_cannot_keep_pending_effects_and_checkpoint_cannot_claim_completion(self) -> None:
        with self.assertRaises(ValidationError):
            capsule(
                capsule_kind="TERMINAL_RESULT",
                final_result="声称完成。",
                retention_class="TERMINAL_RESULT",
            )
        with self.assertRaises(ValidationError):
            capsule(final_result="错误完成声明。")
        with self.assertRaises(ValidationError):
            capsule(next_step=None)

    def test_workspace_manifest_and_set_like_ids_must_be_unique_and_sorted(self) -> None:
        with self.assertRaises(ValidationError):
            capsule(
                workspace_manifest=(
                    WorkspaceFileRef(
                        relative_path="z/file.md",
                        sha256="7" * 64,
                        size_bytes=1,
                        revision=1,
                    ),
                    WorkspaceFileRef(
                        relative_path="a/file.md",
                        sha256="8" * 64,
                        size_bytes=1,
                        revision=1,
                    ),
                )
            )
        with self.assertRaises(ValidationError):
            capsule(pending_effect_ids=(EFFECT_ID, EFFECT_ID))


if __name__ == "__main__":
    unittest.main()
