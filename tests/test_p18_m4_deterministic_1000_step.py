from __future__ import annotations

from pathlib import Path

from p18_m4_deterministic_harness import (
    FAULT_INJECTIONS,
    DeterministicLongHorizonHarness,
)


def run_report():
    return DeterministicLongHorizonHarness().run()


def test_m4_fault_schedule_is_exact_plan_vector() -> None:
    assert [(item.step, item.code) for item in FAULT_INJECTIONS] == [
        (49, "LLM_TIMEOUT"),
        (83, "LOCAL_GOAL_DRIFT"),
        (121, "TOOL_FALSE_SUCCESS"),
        (173, "TOOL_FAILURE"),
        (241, "SSE_DISCONNECT"),
        (307, "STALE_FACT"),
        (377, "PROCESS_RESTART"),
        (421, "TOOL_PROMPT_INJECTION"),
        (489, "FORCED_CONTEXT_COMPACT"),
        (533, "BAD_SEMANTIC_SUMMARY"),
        (577, "ARTIFACT_REVISION_CONFLICT"),
        (641, "AMBIGUOUS_SIDE_EFFECT"),
        (702, "FALSE_COMPLETE"),
        (777, "PROVIDER_TRANSIENT_5XX"),
        (850, "DUPLICATE_STREAM_CHUNK"),
        (884, "FALSE_FACT_MEMORY_PROMOTION"),
        (921, "RESTART_AFTER_CHECKPOINT"),
        (953, "PROVIDER_PROFILE_VERSION_CHANGE"),
        (965, "LEDGER_TORN_TAIL"),
        (972, "CHECKPOINT_CHECKSUM_CORRUPTION"),
        (981, "PREPARED_WITHOUT_DISPATCH"),
    ]


def test_m4_harness_completes_1000_steps_and_200_plus_model_rounds() -> None:
    report = run_report()
    assert report.tool_steps == 1000
    assert report.model_decision_rounds >= 200
    assert report.model_decision_rounds == 250
    assert report.epoch_count >= 5
    assert report.checkpoint_count >= 5
    assert report.fault_steps_seen == tuple(item.step for item in FAULT_INJECTIONS)


def test_m4_1000_step_hard_metrics_are_all_zero_or_false() -> None:
    report = run_report()
    assert report.metrics.is_clean(), report.metrics.zero_violation_contract()
    assert report.metrics.zero_violation_contract() == {
        "missing_required_steps": 0,
        "duplicate_committed_irreversible_effects": 0,
        "authority_changes": 0,
        "request_id_changes": 0,
        "run_id_changes": 0,
        "illegal_generation_changes": 0,
        "unreconciled_ambiguous_effects": 0,
        "completed_obligation_loss": 0,
        "root_goal_hash_changes": 0,
        "task_contract_hash_illegal_changes": 0,
        "false_verified_facts": 0,
        "false_completion_accepts": 0,
        "tool_prompt_injection_authority_escalation": 0,
        "invalid_learning_promotions": 0,
        "silent_concurrency_overwrites": 0,
        "model_working_set_linear_growth": False,
        "run_snapshot_unbounded_growth": False,
        "ledger_replay_mismatch": 0,
        "ledger_seq_conflict": 0,
        "torn_tail_undetected": 0,
        "checkpoint_corruption_silent_accept": 0,
        "prepared_before_dispatch_violations": 0,
        "logical_effect_duplicate_commit": 0,
    }


def test_m4_faults_are_remediated_without_identity_or_authority_reset() -> None:
    harness = DeterministicLongHorizonHarness()
    baseline = harness.identity_fingerprint
    report = harness.run()
    assert report.identity_fingerprint == baseline
    assert report.remediations == tuple(
        (item.step, item.expected_remediation) for item in FAULT_INJECTIONS
    )
    assert report.duplicate_prevented_count == 1
    assert report.false_completion_rejected == 1
    assert report.learning_promotion_rejected == 1
    assert report.prompt_injection_blocked == 1
    assert report.torn_tail_recovered == 1
    assert report.checkpoint_fallback_count == 1


def test_m4_working_set_and_snapshot_remain_bounded() -> None:
    report = run_report()
    assert report.max_model_working_set <= 64
    assert report.max_snapshot_items <= DeterministicLongHorizonHarness.MAX_SNAPSHOT_ITEMS
    assert report.metrics.model_working_set_linear_growth is False
    assert report.metrics.run_snapshot_unbounded_growth is False


def test_m4_harness_uses_existing_turn_loop_and_does_not_define_parallel_runtime() -> None:
    harness_source = (
        Path(__file__).resolve().parent / "p18_m4_deterministic_harness.py"
    ).read_text(encoding="utf-8")
    assert "from v3.runtime_turn_orchestration import TurnLoopState" in harness_source
    assert "class LongChainRuntime" not in harness_source
    assert "GatewayStateStore.open" not in harness_source
    assert "sqlite3.connect" not in harness_source
    assert "subprocess.run" not in harness_source

    zong = (
        Path(__file__).resolve().parents[1]
        / "app" / "backend" / "tiangong-backend" / "v3" / "zongdiaodu.py"
    ).read_text(encoding="utf-8")
    assert "_SIMPLE_CHAIN_MAX_GLOBAL_TOOL_ROUNDS" in zong
    assert "_simple_chain_regenerative_execute_tool(" in zong
