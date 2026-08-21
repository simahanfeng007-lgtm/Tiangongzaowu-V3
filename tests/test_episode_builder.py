"""F3 步骤 2：episode_builder 纯函数单测（B1-B5）。"""

from __future__ import annotations

import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import LifeEventEnvelope
from life_service.capability_learning import _impact_floor
from life_service.episode_builder import (
    DEFAULT_PREDICTED_SUCCESS_MILLI,
    build_action_impact,
    build_life_event,
    build_open_episode,
    build_outcome_evidence,
    build_prediction,
    failure_category_from_error,
    failure_category_from_step_error,
    fingerprint,
    observed_quality_from_steps,
)


LIFE_ID = "life_builder_test"


def make_signer():
    key = Ed25519PrivateKey.generate()
    key_id = "life_reflection_test_signer"
    return key_id, key.sign


def test_b1_prediction_snapshot_is_deterministic_and_auditable():
    first = build_prediction(
        basis_inputs={"activity_id": "daily_planning", "history_note": "近30次"},
        successes=21,
        uses=30,
    )
    second = build_prediction(
        basis_inputs={"activity_id": "daily_planning", "history_note": "近30次"},
        successes=21,
        uses=30,
    )
    # B1：确定性——相同输入相同快照哈希，可重放审计。
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.predicted_success_milli == 700
    assert first.basis == "history"
    parsed = json.loads(first.prior_prediction)
    assert parsed["predicted_success_milli"] == 700
    assert parsed["sample_successes"] == 21
    assert parsed["sample_uses"] == 30
    # 无历史回退确定性基线，不随机猜测。
    fallback = build_prediction(basis_inputs={"activity_id": "x"}, successes=0, uses=0)
    assert fallback.predicted_success_milli == DEFAULT_PREDICTED_SUCCESS_MILLI
    assert fallback.basis == "default"
    # 依据进入哈希：不同依据不同快照。
    other = build_prediction(
        basis_inputs={"activity_id": "system_health", "history_note": "近30次"},
        successes=21,
        uses=30,
    )
    assert other.snapshot_sha256 != first.snapshot_sha256
    assert fingerprint({"life_id": LIFE_ID, "task_id": "t1"}) == fingerprint(
        {"task_id": "t1", "life_id": LIFE_ID}
    )


def test_b2_life_events_form_a_hash_chain_with_signatures():
    key_id, sign = make_signer()
    first = build_life_event(
        life_id=LIFE_ID,
        sequence=1,
        writer_epoch=1,
        previous_event_hash=None,
        event_kind="life.reflection.episode.opened",
        content={"task_id": "t1", "intention": "验证链形"},
        occurred_at_ms=1_000,
        observed_at_ms=1_000,
        correlation_id="corr-t1",
        signer_key_id=key_id,
        sign=sign,
    )
    second = build_life_event(
        life_id=LIFE_ID,
        sequence=2,
        writer_epoch=1,
        previous_event_hash=first.event_hash,
        event_kind="life.reflection.episode.committed",
        content={"task_id": "t1", "outcome": "success"},
        occurred_at_ms=1_500,
        observed_at_ms=1_600,
        correlation_id="corr-t1",
        causation_id=first.event_id,
        signer_key_id=key_id,
        sign=sign,
    )
    assert first.previous_event_hash is None
    assert second.previous_event_hash == first.event_hash
    for event in (first, second):
        assert isinstance(event, LifeEventEnvelope)
        assert event.has_valid_event_hash()
        assert event.signature != "0" * 128
        assert event.signer_key_id == key_id
    assert first.event_id != second.event_id


def test_b3_action_impact_floor_always_matches_risk_class():
    for risk in ("A0", "A1", "A2", "A3", "A4", "A5"):
        impact = build_action_impact(
            life_id=LIFE_ID,
            action_id="action_probe",
            risk_class=risk,
            source_event_ids=("lev_" + "1" * 64,),
            created_at_ms=2_000,
        )
        assert impact.has_valid_impact_sha256()
        assert _impact_floor(impact) == risk, risk
    try:
        build_action_impact(
            life_id=LIFE_ID,
            action_id="a",
            risk_class="A6",
            source_event_ids=("lev_" + "1" * 64,),
            created_at_ms=2_000,
        )
        raise AssertionError("A6 should be rejected")
    except ValueError:
        pass


def test_b4_failure_categories_map_from_errors_and_steps():
    assert failure_category_from_error(None) == "unknown"
    assert failure_category_from_error(PermissionError("denied")) == "insufficient_permission"
    assert failure_category_from_error(TimeoutError("too slow")) == "environment_error"
    assert failure_category_from_error(ValueError("bad input schema")) == "input_error"
    assert failure_category_from_error(RuntimeError("action policy blocked by gate")) == "policy_block"
    assert failure_category_from_error(RuntimeError("stale context conflict")) == "stale_context"
    assert failure_category_from_error(RuntimeError("完全未知的怪错")) == "unknown"
    assert failure_category_from_step_error({"error_code": "artifact.executor.tool_error"}) == "tool_error"
    assert failure_category_from_step_error({"error_code": "permission.denied"}) == "insufficient_permission"
    assert failure_category_from_step_error({}) == "tool_error"
    assert observed_quality_from_steps([]) == 800
    assert observed_quality_from_steps([{"ok": True}, {"ok": True}, {"ok": False}, {"ok": True}]) == 750


def test_b5_outcome_evidence_honest_baseline_and_failure_templates():
    prediction = build_prediction(basis_inputs={"activity_id": "x"}, successes=7, uses=10)
    episode = build_open_episode(
        life_id=LIFE_ID,
        trigger_event_ids=("lev_" + "1" * 64,),
        context_state_hashes=(fingerprint({"scope": 1}),),
        intention="完成一次可验证的内部整理",
        prediction=prediction,
        candidate_action_ids=("action_probe",),
        selected_action_id="action_probe",
        created_at_ms=1_000,
    )
    assert episode.terminal_status == "OPEN"
    assert episode.has_valid_episode_sha256()
    # 同触发+同预测 → 同 identity（失败重试不产生新 episode）。
    replay = build_open_episode(
        life_id=LIFE_ID,
        trigger_event_ids=("lev_" + "1" * 64,),
        context_state_hashes=(fingerprint({"scope": 1}),),
        intention="完成一次可验证的内部整理",
        prediction=prediction,
        candidate_action_ids=("action_probe",),
        selected_action_id="action_probe",
        created_at_ms=9_999,
    )
    assert replay.episode_id == episode.episode_id

    success = build_outcome_evidence(
        life_id=LIFE_ID,
        episode_id=episode.episode_id,
        outcome_status="success",
        observed_outcome="任务完成，产出了当日计划。",
        observed_quality_milli=900,
        prediction=prediction,
        completion_decision_sha256="4" * 64,
        terminal_fact_hashes=("5" * 64,),
        outcome_event_ids=("lev_" + "2" * 64,),
        context_fingerprint_sha256=fingerprint({"scope": 1}),
        action_risk="A1",
        occurred_at_ms=2_000,
    )
    assert success.has_valid_evidence_sha256()
    # 诚实基线：无假设 → correlation_only，成功证据不可晋升（eligible=False）。
    assert success.supported_cause_ids == ()
    assert success.failure_category is None
    try:
        build_outcome_evidence(
            life_id=LIFE_ID,
            episode_id=episode.episode_id,
            outcome_status="success",
            observed_outcome="任务完成。",
            observed_quality_milli=900,
            prediction=prediction,
            completion_decision_sha256="4" * 64,
            terminal_fact_hashes=("5" * 64,),
            outcome_event_ids=("lev_" + "2" * 64,),
            context_fingerprint_sha256=fingerprint({"scope": 1}),
            action_risk="A1",
            failure_category="tool_error",
            occurred_at_ms=2_000,
        )
        raise AssertionError("success with category should be rejected")
    except ValueError:
        pass

    failure = build_outcome_evidence(
        life_id=LIFE_ID,
        episode_id=episode.episode_id,
        outcome_status="failure",
        observed_outcome="任务失败：工具返回错误。",
        observed_quality_milli=100,
        prediction=prediction,
        completion_decision_sha256="4" * 64,
        terminal_fact_hashes=("5" * 64,),
        outcome_event_ids=("lev_" + "3" * 64,),
        context_fingerprint_sha256=fingerprint({"scope": 1}),
        action_risk="A1",
        occurred_at_ms=2_000,
    )
    assert failure.failure_category == "unknown"
    assert failure.counterfactual_actions
    assert failure.next_minimal_experiment

    aborted = build_outcome_evidence(
        life_id=LIFE_ID,
        episode_id=episode.episode_id,
        outcome_status="aborted",
        observed_outcome="运行中断，任务未收尾。",
        observed_quality_milli=0,
        prediction=prediction,
        completion_decision_sha256="4" * 64,
        terminal_fact_hashes=("5" * 64,),
        outcome_event_ids=("lev_" + "4" * 64,),
        context_fingerprint_sha256=fingerprint({"scope": 1}),
        action_risk="A1",
        occurred_at_ms=2_000,
    )
    assert aborted.failure_category == "stale_context"
