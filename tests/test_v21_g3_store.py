"""G3 gateway store evidence: model attempt saga persistence and head CAS."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from contracts import (
    AssistantCommit,
    ModelAttemptPlan,
    ModelAttemptPlanOutcome,
    ModelAttemptResult,
    ProviderSlot,
    SystemStatusRecord,
)
from total_gateway.store import GatewayStateStore, StoreConflictError


H = "a" * 64
REQ = "req_" + "1" * 64
RUN = "run_" + "2" * 64


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        instance = GatewayStateStore.open(path, now_ms=1)
        try:
            yield instance
        finally:
            instance.close()


def make_plan(**overrides):
    values = dict(
        model_attempt_plan_id="map_" + "3" * 64, model_effect_id="eff_" + "4" * 64,
        request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        run_life_binding_sha256=H, root_experience_id="root_1", response_episode_id="ep_1",
        response_episode_sha256=H, context_pack_ref="ctx_1", context_pack_sha256=H,
        response_basis_kind="conversation", response_basis_sha256=H,
        capability_profile_sha256=H,
        provider_slots=(ProviderSlot(slot_no=1, provider="minimax", model="m1", transport_profile_sha256=H),),
        plan_revision=1, request_sha256=H, conversation_basis_ref="cb_1",
        plan_sha256="0" * 64,
    )
    values.update(overrides)
    return ModelAttemptPlan(**values).with_computed_plan_sha256()


def make_result(**overrides):
    values = dict(
        model_attempt_receipt_id="mar_" + "5" * 64, model_attempt_plan_id="map_" + "3" * 64,
        model_attempt_plan_sha256=H, model_effect_id="eff_" + "4" * 64,
        request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        run_life_binding_sha256=H, root_experience_id="root_1", response_episode_id="ep_1",
        attempt_id="mat_" + "6" * 64, slot_no=1, provider="minimax", model="m1",
        status="SUCCEEDED", attempt_plan_revision=1, request_sha256=H,
        dispatched=True, started_at_ms=1, completed_at_ms=2, response_schema_valid=True,
        dispatch_marker_ref="dm_1", transport_run_id="trn_1",
        text_object_id="obj_1", output_text_sha256=H, finish_reason="stop",
    )
    values.update(overrides)
    return ModelAttemptResult(**values)


def test_model_attempt_plan_persists_idempotently_and_reads_back(store) -> None:
    plan = make_plan()
    assert store.put_model_attempt_plan(plan, now_ms=10)
    assert not store.put_model_attempt_plan(plan, now_ms=11)
    loaded = store.get_model_attempt_plan(plan.model_attempt_plan_id)
    assert loaded is not None and loaded.plan_sha256 == plan.plan_sha256
    with pytest.raises(StoreConflictError):
        store.put_model_attempt_plan(
            make_plan(plan_revision=2),
            now_ms=12,
        )


def test_dispatch_marker_and_result_are_unique_per_slot(store) -> None:
    assert store.put_model_attempt_plan(make_plan(), now_ms=10)
    assert store.create_dispatch_marker(marker_id="dm_1", plan_id="map_" + "3" * 64, attempt_id="mat_" + "6" * 64, slot_no=1, now_ms=20)
    assert not store.create_dispatch_marker(marker_id="dm_2", plan_id="map_" + "3" * 64, attempt_id="mat_" + "6" * 64, slot_no=1, now_ms=21)
    assert store.mark_dispatch_marker_dispatched(marker_id="dm_1", now_ms=30)
    marker = store.get_dispatch_marker(plan_id="map_" + "3" * 64, slot_no=1)
    assert marker is not None and marker["status"] == "dispatched"
    result = make_result()
    assert store.put_model_attempt_result(result)
    assert not store.put_model_attempt_result(result)
    with pytest.raises(StoreConflictError):
        store.put_model_attempt_result(make_result(model_attempt_receipt_id="mar_" + "7" * 64, attempt_id="mat_" + "8" * 64))
    loaded = store.get_model_attempt_result(plan_id="map_" + "3" * 64, slot_no=1)
    assert loaded is not None and loaded.status == "SUCCEEDED"


def test_plan_outcome_and_assistant_commit_and_system_status(store) -> None:
    winner = "mar_" + "5" * 64
    outcome = ModelAttemptPlanOutcome(
        model_attempt_plan_outcome_id="mapo_" + "9" * 64,
        model_attempt_plan_id="map_" + "3" * 64, model_attempt_plan_sha256=H,
        status="SUCCEEDED", ordered_attempt_refs=(winner,), winner_attempt_ref=winner,
        completed_at_ms=40, outcome_sha256="0" * 64,
    ).with_computed_outcome_sha256()
    assert store.put_model_attempt_plan_outcome(outcome)
    assert not store.put_model_attempt_plan_outcome(outcome)
    commit = AssistantCommit(
        assistant_commit_id="asc_" + "a" * 64, assistant_message_id="asm_" + "b" * 64,
        life_turn_commit_ref="tc_1", life_turn_commit_sha256=H,
        response_episode_id="ep_1", model_attempt_plan_outcome_ref="mapo_" + "9" * 64,
        model_attempt_receipt_id=winner, output_text_sha256=H, committed_text_sha256=H,
        text_object_id="obj_1", committed_at_ms=50, commit_sha256="0" * 64,
    ).with_computed_commit_sha256()
    assert store.put_assistant_commit(commit)
    assert not store.put_assistant_commit(commit)
    with pytest.raises(StoreConflictError):
        store.put_assistant_commit(
            AssistantCommit(
                assistant_commit_id="asc_" + "c" * 64, assistant_message_id="asm_" + "d" * 64,
                life_turn_commit_ref="tc_1", life_turn_commit_sha256=H,
                response_episode_id="ep_1", model_attempt_plan_outcome_ref="mapo_" + "9" * 64,
                model_attempt_receipt_id=winner, output_text_sha256=H, committed_text_sha256=H,
                text_object_id="obj_1", committed_at_ms=51, commit_sha256="0" * 64,
            ).with_computed_commit_sha256()
        )
    status = SystemStatusRecord(
        system_status_id="sys_" + "e" * 64, request_id=REQ, run_id=RUN,
        run_sequence=1, generation=0, response_episode_id="ep_1",
        status_code="all_models_unavailable", severity="error",
        source_component="gateway.response", source_fact_refs=(), display_object_ref="obj_1",
        created_at_ms=60, system_status_sha256="0" * 64,
    ).with_computed_status_sha256()
    assert store.put_system_status(status)
    assert not store.put_system_status(status)


def test_effect_outcome_head_cas(store) -> None:
    effect_id = "eff_" + "f" * 64
    assert store.put_effect_outcome_head(
        effect_id=effect_id, original_execution_result_ref="res_1", effective_status="SUCCEEDED",
        head_revision=1, head_sha256=H, latest_reconciliation_ref=None,
        updated_at_ms=70, expected_head_sha256=None,
    )
    assert not store.put_effect_outcome_head(
        effect_id=effect_id, original_execution_result_ref="res_1", effective_status="SUCCEEDED",
        head_revision=1, head_sha256=H, latest_reconciliation_ref=None,
        updated_at_ms=71, expected_head_sha256=H,
    )
    with pytest.raises(StoreConflictError):
        store.put_effect_outcome_head(
            effect_id=effect_id, original_execution_result_ref="res_2", effective_status="AMBIGUOUS",
            head_revision=2, head_sha256="0" * 64, latest_reconciliation_ref=None,
            updated_at_ms=72, expected_head_sha256="0" * 64,
        )
    assert store.put_effect_outcome_head(
        effect_id=effect_id, original_execution_result_ref="res_2", effective_status="AMBIGUOUS",
        head_revision=2, head_sha256="0" * 64, latest_reconciliation_ref=None,
        updated_at_ms=73, expected_head_sha256=H,
    )
