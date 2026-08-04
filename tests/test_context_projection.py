from __future__ import annotations

import unittest
from types import SimpleNamespace

from contracts import canonical_json_bytes
from total_gateway.context_projection import (
    ConversationProjectionPolicy,
    SessionContextProjector,
    estimate_projected_context_tokens,
)


class _Store:
    def __init__(self) -> None:
        self.queue = (
            SimpleNamespace(sequence=1, request_id="request-one", state="COMPLETED"),
            SimpleNamespace(sequence=2, request_id="request-two", state="COMPLETED"),
            SimpleNamespace(sequence=3, request_id="request-current", state="ACTIVE"),
        )
        self.envelopes = {
            "request-one": SimpleNamespace(text="先生成一份方案"),
            "request-two": SimpleNamespace(text="继续检查交付物"),
        }
        self.effects = {
            "request-one": (
                SimpleNamespace(
                    claim=SimpleNamespace(effect_kind="execution"),
                    result=SimpleNamespace(
                        status="SUCCEEDED",
                        result_object_id="result-one",
                        error_code=None,
                    ),
                ),
            ),
            "request-two": (
                SimpleNamespace(
                    claim=SimpleNamespace(effect_kind="execution"),
                    result=SimpleNamespace(
                        status="FAILED_FINAL",
                        result_object_id=None,
                        error_code="tool.timeout",
                    ),
                ),
            ),
        }
        self.terminals = {}
        self.active_capsules = {}

    def get_session_queue(self, _scope):
        return self.queue

    def get_request_envelope(self, request_id):
        return self.envelopes.get(request_id)

    def get_generation(self, request_id):
        return SimpleNamespace(run_id="run-" + request_id, generation=1)

    def list_effects_for_request(self, request_id, *, run_id, generation):
        assert run_id == "run-" + request_id
        assert generation == 1
        return self.effects.get(request_id, ())

    def get_terminal_request_capsule(self, request_id, *, run_id, generation):
        return self.terminals.get(request_id)

    def get_active_request_capsule(self, request_id, *, run_id, generation):
        return self.active_capsules.get(request_id)


class _Objects:
    def __init__(self) -> None:
        self.values = {
            "result-one": canonical_json_bytes(
                {
                    "reply_text": "最终文件已生成并通过检查。",
                    "process_summary": "已执行最终质量门。",
                    "key_facts": ["输出文件可打开。"],
                    "tool_calls": [{"name": "raw-tool-secret"}],
                    "tool_results": ["raw-tool-output"],
                }
            )
        }

    def read_bytes(self, object_id):
        return self.values[object_id]


class SessionContextProjectionTests(unittest.TestCase):
    def test_context_token_estimate_tracks_retained_conversation_not_recall_pack(self) -> None:
        estimated = estimate_projected_context_tokens(
            [
                {"role": "user", "content": "abcd"},
                {"role": "assistant", "content": "你好"},
            ],
            "xy",
        )

        # ASCII is conservatively four characters per token, CJK is one token
        # per character, and every retained message/request has framing cost.
        self.assertEqual(estimated, 16)
        self.assertEqual(
            estimate_projected_context_tokens(
                [{"role": "assistant", "content": "中" * 10_000_001}],
            ),
            10_000_000,
        )

    def test_persistent_capsule_is_shadow_compared_without_switching_model_input(self) -> None:
        store = _Store()
        store.terminals["request-one"] = SimpleNamespace(
            capsule=SimpleNamespace(
                capsule_kind="TERMINAL_RESULT",
                final_result=(
                    "最终文件已生成并通过检查。\n\n[过程关键信息]\n"
                    "- 已执行最终质量门。\n- 输出文件可打开。"
                ),
            )
        )
        comparison = SessionContextProjector(store, _Objects()).compare_persistent_capsule(
            "request-one"
        )
        self.assertTrue(comparison.equivalent)
        self.assertFalse(comparison.model_input_switched)
        self.assertEqual(comparison.capsule_kind, "TERMINAL_RESULT")

    def test_completed_work_converges_to_final_result_and_interruption_checkpoint(self) -> None:
        projection = SessionContextProjector(_Store(), _Objects()).project(
            session_scope_hash="session",
            before_sequence=3,
            current_request_id="request-current",
        )
        self.assertEqual([item["role"] for item in projection.messages], ["user", "assistant"] * 2)
        combined = "\n".join(item["content"] for item in projection.messages)
        self.assertIn("最终文件已生成并通过检查", combined)
        self.assertIn("过程关键信息", combined)
        self.assertIn("[断点快照]", combined)
        self.assertIn("tool.timeout", combined)
        self.assertNotIn("raw-tool-secret", combined)
        self.assertNotIn("raw-tool-output", combined)
        self.assertEqual(projection.terminal_capsules, 1)
        self.assertEqual(projection.checkpoint_capsules, 1)
        self.assertFalse(projection.metadata()["raw_tool_calls_included"])
        self.assertFalse(projection.metadata()["raw_tool_results_included"])

    def test_projection_keeps_the_most_recent_turn_under_the_bound(self) -> None:
        projection = SessionContextProjector(
            _Store(),
            _Objects(),
            policy=ConversationProjectionPolicy(max_turns=1),
        ).project(
            session_scope_hash="session",
            before_sequence=3,
            current_request_id="request-current",
        )
        self.assertEqual(len(projection.messages), 2)
        self.assertEqual(projection.messages[0]["content"], "继续检查交付物")
        self.assertEqual(projection.checkpoint_capsules, 1)
        self.assertEqual(projection.omitted_turns, 1)

    def test_a5_wait_is_projected_as_confirmation_checkpoint_not_backend_failure(self) -> None:
        store = _Store()
        store.effects["request-two"] = (
            SimpleNamespace(
                claim=SimpleNamespace(effect_kind="execution"),
                result=SimpleNamespace(
                    status="FAILED_FINAL",
                    result_object_id=None,
                    error_code="compat.backend.waiting_for_user",
                ),
            ),
        )
        projection = SessionContextProjector(store, _Objects()).project(
            session_scope_hash="session",
            before_sequence=3,
            current_request_id="request-current",
        )
        combined = "\n".join(item["content"] for item in projection.messages)
        self.assertIn("[A5授权断点]", combined)
        self.assertIn("等待用户明确授权", combined)
        self.assertNotIn("compat.backend.terminal_failure", combined)

    def test_oversized_latest_turn_is_compacted_instead_of_skipped(self) -> None:
        store = _Store()
        store.queue = (
            SimpleNamespace(sequence=1, request_id="request-one", state="COMPLETED"),
            SimpleNamespace(sequence=2, request_id="request-current", state="ACTIVE"),
        )
        store.envelopes["request-one"] = SimpleNamespace(text="最新请求" * 2_000)
        objects = _Objects()
        objects.values["result-one"] = canonical_json_bytes(
            {"reply_text": "最新最终结果" * 2_000, "key_facts": []}
        )
        projection = SessionContextProjector(
            store,
            objects,
            policy=ConversationProjectionPolicy(
                max_turns=4,
                max_characters=2_000,
                max_message_characters=2_000,
            ),
        ).project(
            session_scope_hash="session",
            before_sequence=2,
            current_request_id="request-current",
        )
        self.assertEqual(len(projection.messages), 2)
        self.assertIn("最新请求", projection.messages[0]["content"])
        self.assertIn("最新最终结果", projection.messages[1]["content"])
        self.assertLessEqual(sum(len(item["content"]) for item in projection.messages), 2_000)

    def test_projection_never_backfills_older_turns_across_a_recency_gap(self) -> None:
        store = _Store()
        request_ids = ["request-one", "request-two", "request-three", "request-four"]
        store.queue = tuple(
            SimpleNamespace(sequence=index, request_id=request_id, state="COMPLETED")
            for index, request_id in enumerate(request_ids, start=1)
        ) + (SimpleNamespace(sequence=5, request_id="request-current", state="ACTIVE"),)
        store.envelopes = {
            "request-one": SimpleNamespace(text="OLDEST_USER " + "一" * 300),
            "request-two": SimpleNamespace(text="OLDER_USER " + "二" * 300),
            "request-three": SimpleNamespace(text="RECENT_LARGE_USER " + "三" * 5_000),
            "request-four": SimpleNamespace(text="NEWEST_USER " + "四" * 1_500),
        }
        store.effects = {
            request_id: (
                SimpleNamespace(
                    claim=SimpleNamespace(effect_kind="execution"),
                    result=SimpleNamespace(
                        status="SUCCEEDED",
                        result_object_id=f"result-{request_id}",
                        error_code=None,
                    ),
                ),
            )
            for request_id in request_ids
        }
        objects = _Objects()
        objects.values.update(
            {
                "result-request-one": canonical_json_bytes({"reply_text": "OLDEST_FINAL " + "甲" * 300}),
                "result-request-two": canonical_json_bytes({"reply_text": "OLDER_FINAL " + "乙" * 300}),
                "result-request-three": canonical_json_bytes({"reply_text": "RECENT_LARGE_FINAL " + "丙" * 5_000}),
                "result-request-four": canonical_json_bytes({"reply_text": "NEWEST_FINAL " + "丁" * 1_500}),
            }
        )

        projection = SessionContextProjector(
            store,
            objects,
            policy=ConversationProjectionPolicy(
                max_turns=12,
                max_characters=10_000,
                max_message_characters=4_000,
            ),
        ).project(
            session_scope_hash="session",
            before_sequence=5,
            current_request_id="request-current",
        )

        combined = "\n".join(item["content"] for item in projection.messages)
        self.assertIn("NEWEST_USER", combined)
        self.assertIn("NEWEST_FINAL", combined)
        self.assertNotIn("RECENT_LARGE_USER", combined)
        self.assertNotIn("OLDER_USER", combined)
        self.assertNotIn("OLDEST_USER", combined)
        self.assertEqual(projection.omitted_turns, 3)


if __name__ == "__main__":
    unittest.main()
