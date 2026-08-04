"""G3 contract evidence: model attempt plan/result/outcome, assistant/status, life turn commit."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import (
    AssistantCommit,
    AssistantMessage,
    AssistantSystemEnvelope,
    LifeTurnCommit,
    ModelAttemptPlan,
    ModelAttemptPlanOutcome,
    ModelAttemptResult,
    ProviderSlot,
    SystemStatusRecord,
    derive_assistant_commit_id,
    derive_turn_commit_id,
    derive_model_attempt_id,
    derive_model_attempt_plan_id,
    derive_model_inference_effect_id,
)
from contracts.life import LifeTurnCommit
from life_service.store import LifeShadowStore, LifeShadowStoreError


H = "a" * 64
REQ = "req_" + "1" * 64
RUN = "run_" + "2" * 64


def plan_values(**overrides):
    values = dict(
        model_attempt_plan_id="map_" + "3" * 64, model_effect_id="eff_" + "4" * 64,
        request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        run_life_binding_sha256=H, root_experience_id="root_1", response_episode_id="ep_1",
        response_episode_sha256=H, context_pack_ref="ctx_1", context_pack_sha256=H,
        response_basis_kind="conversation", response_basis_sha256=H,
        capability_profile_sha256=H,
        provider_slots=(ProviderSlot(slot_no=1, provider="minimax", model="m1", transport_profile_sha256=H),),
        plan_revision=1, request_sha256=H, conversation_basis_ref="cb_1", plan_sha256="0" * 64,
    )
    values.update(overrides)
    return values


def test_model_attempt_plan_conversation_and_commitment_shapes() -> None:
    conversation = ModelAttemptPlan(**plan_values()).with_computed_plan_sha256()
    assert conversation.has_valid_plan_sha256()
    with pytest.raises(ValidationError):
        ModelAttemptPlan(**plan_values(conversation_basis_ref=None))
    commitment = ModelAttemptPlan(**plan_values(
        response_basis_kind="commitment", conversation_basis_ref=None,
        completion_delivery_mode="none", completion_decision_ref="cd_1",
        completion_decision_sha256=H, response_basis_sha256=H,
    )).with_computed_plan_sha256()
    assert commitment.completion_delivery_mode == "none"
    with pytest.raises(ValidationError):
        ModelAttemptPlan(**plan_values(
            response_basis_kind="commitment", conversation_basis_ref=None,
            completion_delivery_mode=None, completion_decision_ref="cd_1",
            completion_decision_sha256=H,
        ))
    with pytest.raises(ValidationError):
        ModelAttemptPlan(**plan_values(
            provider_slots=(
                ProviderSlot(slot_no=2, provider="a", model="m", transport_profile_sha256=H),
                ProviderSlot(slot_no=1, provider="b", model="m", transport_profile_sha256=H),
            )
        ))


def test_model_attempt_result_dispatch_and_success_shape() -> None:
    base = dict(
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
    assert ModelAttemptResult(**base)
    with pytest.raises(ValidationError):
        ModelAttemptResult(**{**base, "dispatch_marker_ref": None})
    with pytest.raises(ValidationError):
        ModelAttemptResult(**{**base, "transport_run_id": None})
    with pytest.raises(ValidationError):
        ModelAttemptResult(**{**base, "output_text_sha256": None})
    with pytest.raises(ValidationError):
        ModelAttemptResult(**{**base, "response_schema_valid": False})
    undispatched = ModelAttemptResult(**{**base, "dispatched": False, "dispatch_marker_ref": None, "transport_run_id": None, "status": "CANCELLED", "text_object_id": None, "output_text_sha256": None, "finish_reason": None})
    assert undispatched.status == "CANCELLED"
    with pytest.raises(ValidationError):
        ModelAttemptResult(**{**base, "dispatched": False, "dispatch_marker_ref": None, "transport_run_id": None, "status": "SUCCEEDED"})


def test_model_attempt_plan_outcome_winner_and_exhausted() -> None:
    winner = "mar_" + "7" * 64
    outcome = ModelAttemptPlanOutcome(
        model_attempt_plan_outcome_id="mapo_" + "8" * 64,
        model_attempt_plan_id="map_" + "3" * 64, model_attempt_plan_sha256=H,
        status="SUCCEEDED", ordered_attempt_refs=(winner,), winner_attempt_ref=winner,
        completed_at_ms=3, outcome_sha256="0" * 64,
    ).with_computed_outcome_sha256()
    assert outcome.has_valid_outcome_sha256()
    with pytest.raises(ValidationError):
        ModelAttemptPlanOutcome(
            model_attempt_plan_outcome_id="mapo_" + "8" * 64,
            model_attempt_plan_id="map_" + "3" * 64, model_attempt_plan_sha256=H,
            status="SUCCEEDED", ordered_attempt_refs=(winner,), winner_attempt_ref=None,
            completed_at_ms=3, outcome_sha256="0" * 64,
        )
    with pytest.raises(ValidationError):
        ModelAttemptPlanOutcome(
            model_attempt_plan_outcome_id="mapo_" + "8" * 64,
            model_attempt_plan_id="map_" + "3" * 64, model_attempt_plan_sha256=H,
            status="EXHAUSTED", ordered_attempt_refs=(winner,), winner_attempt_ref=winner,
            completed_at_ms=3, outcome_sha256="0" * 64,
        )


def test_assistant_status_envelope_slots_and_all_models_down_rule() -> None:
    status = SystemStatusRecord(
        system_status_id="sys_" + "9" * 64, request_id=REQ, run_id=RUN,
        run_sequence=1, generation=0, response_episode_id="ep_1",
        status_code="all_models_unavailable", severity="error",
        source_component="gateway.response", source_fact_refs=(), display_object_ref="obj_1",
        created_at_ms=1, system_status_sha256="0" * 64,
    ).with_computed_status_sha256()
    envelope = AssistantSystemEnvelope(system_status=status)
    assert envelope.assistant_message is None
    with pytest.raises(ValidationError):
        AssistantSystemEnvelope()
    message = AssistantMessage(
        assistant_message_id="asm_" + "a" * 64, assistant_commit_id="asc_" + "b" * 64,
        assistant_commit_sha256=H, text="hello", text_object_id="obj_1",
        committed_text_sha256=H, life_id="life_1", root_experience_id="root_1",
        response_episode_id="ep_1", model_attempt_receipt_id="mar_" + "5" * 64,
        provider="minimax", model="m1", committed_at_ms=2,
    )
    with pytest.raises(ValidationError):
        AssistantSystemEnvelope(assistant_message=message, system_status=status)
    both_ok = SystemStatusRecord(**{
        **status.model_dump(), "system_status_id": "sys_" + "c" * 64,
        "status_code": "tool_failed", "system_status_sha256": "0" * 64,
    }).with_computed_status_sha256()
    assert AssistantSystemEnvelope(assistant_message=message, system_status=both_ok)


def test_assistant_commit_text_binding() -> None:
    values = dict(
        assistant_commit_id="asc_" + "d" * 64, assistant_message_id="asm_" + "a" * 64,
        life_turn_commit_ref="tc_1", life_turn_commit_sha256=H,
        response_episode_id="ep_1", model_attempt_plan_outcome_ref="mapo_" + "8" * 64,
        model_attempt_receipt_id="mar_" + "5" * 64, output_text_sha256=H,
        committed_text_sha256=H, text_object_id="obj_1", committed_at_ms=3,
        commit_sha256="0" * 64,
    )
    commit = AssistantCommit(**values).with_computed_commit_sha256()
    assert commit.computed_commit_sha256() == commit.commit_sha256
    with pytest.raises(ValidationError):
        AssistantCommit(**{**values, "committed_text_sha256": "0" * 64})


def test_life_turn_commit_stage_shapes_and_expression_rules() -> None:
    common = dict(
        turn_commit_id="tc_1", life_id="life_1", run_life_binding_sha256=H,
        root_experience_id="root_1", child_episode_id="cep_1", response_episode_id="ep_1",
        response_basis_kind="conversation", response_basis_sha256=H,
        commit_sha256="0" * 64,
    )
    outcome = LifeTurnCommit(**{**common, "stage": "OUTCOME_COMMITTED_RESPONSE_OPEN", "fact_refs": ("fact_1",)})
    assert outcome.stage == "OUTCOME_COMMITTED_RESPONSE_OPEN"
    with pytest.raises(ValidationError):
        LifeTurnCommit(**{**common, "stage": "OUTCOME_COMMITTED_RESPONSE_OPEN"})
    with pytest.raises(ValidationError):
        LifeTurnCommit(**{**common, "stage": "RESPONSE_COMMITTED", "fact_refs": ("fact_1",)})
    committed = LifeTurnCommit(**{
        **common, "stage": "RESPONSE_COMMITTED",
        "model_attempt_plan_ref": "map_" + "3" * 64,
        "model_attempt_refs": ("mar_" + "5" * 64,),
        "model_attempt_plan_outcome_ref": "mapo_" + "8" * 64,
        "expression_status": "model_available",
        "winner_attempt_ref": "mar_" + "5" * 64,
        "assistant_candidate_id": "asm_" + "a" * 64,
    })
    assert committed.expression_status == "model_available"
    with pytest.raises(ValidationError):
        LifeTurnCommit(**{
            **common, "stage": "RESPONSE_COMMITTED",
            "model_attempt_plan_ref": "map_" + "3" * 64,
            "model_attempt_refs": ("mar_" + "5" * 64,),
            "model_attempt_plan_outcome_ref": "mapo_" + "8" * 64,
            "expression_status": "model_available",
        })
    with pytest.raises(ValidationError):
        LifeTurnCommit(**{
            **common, "stage": "RESPONSE_COMMITTED",
            "model_attempt_plan_ref": "map_" + "3" * 64,
            "model_attempt_refs": ("mar_" + "5" * 64,),
            "model_attempt_plan_outcome_ref": "mapo_" + "8" * 64,
            "expression_status": "model_unavailable",
            "winner_attempt_ref": "mar_" + "5" * 64,
        })
    terminal = LifeTurnCommit(**{
        **common, "stage": "ROOT_TERMINAL",
        "root_terminal_status": "CLOSED", "root_terminal_reason": "delivery.observed",
        "root_terminal_at_ms": 9, "terminal_basis_ref": "tb_1",
    })
    assert terminal.root_terminal_status == "CLOSED"
    with pytest.raises(ValidationError):
        LifeTurnCommit(**{**common, "stage": "ROOT_TERMINAL", "terminal_basis_ref": "tb_1"})


def test_g3_derive_ids_are_deterministic() -> None:
    effect = derive_model_inference_effect_id(
        origin_request_id=REQ, origin_run_id=RUN, root_experience_id="root_1",
        response_episode_id="ep_1", request_sha256=H,
    )
    plan = derive_model_attempt_plan_id(model_effect_id=effect, response_episode_id="ep_1", request_sha256=H, plan_revision=1)
    attempt = derive_model_attempt_id(model_attempt_plan_id=plan, slot_no=1)
    commit = derive_assistant_commit_id(response_episode_id="ep_1", assistant_message_id="asm_" + "a" * 64)
    assert effect.startswith("eff_") and plan.startswith("map_") and attempt.startswith("mat_") and commit.startswith("asc_")
    assert derive_model_inference_effect_id(
        origin_request_id=REQ, origin_run_id=RUN, root_experience_id="root_1",
        response_episode_id="ep_1", request_sha256=H,
    ) == effect
    assert derive_model_attempt_id(model_attempt_plan_id=plan, slot_no=2) != attempt


def test_life_turn_commit_stage_chain_is_ordered_and_immutable() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "life.shadow.sqlite3"
        with LifeShadowStore.open(path, create=True, now_ms=1) as store:
            common = dict(
                life_id="life_1", run_life_binding_sha256=H,
                root_experience_id="root_1", child_episode_id="cep_1",
                response_episode_id="ep_1", response_basis_kind="conversation",
                response_basis_sha256=H,
            )
            outcome = LifeTurnCommit(**{
                **common, "turn_commit_id": "tc_1", "stage": "OUTCOME_COMMITTED_RESPONSE_OPEN",
                "fact_refs": ("fact_1",), "commit_sha256": "0" * 64,
            }).with_computed_commit_sha256()
            assert store.put_life_turn_commit(outcome, now_ms=2)
            assert not store.put_life_turn_commit(outcome, now_ms=3)
            with pytest.raises(LifeShadowStoreError, match="transition"):
                skipped = LifeTurnCommit(**{
                    **common, "turn_commit_id": "tc_2", "stage": "DELIVERY_OBSERVED",
                    "predecessor_commit_sha256": outcome.commit_sha256,
                    "delivery_ref": "del_1", "terminal_basis_ref": "tb_1",
                    "commit_sha256": "0" * 64,
                }).with_computed_commit_sha256()
                store.put_life_turn_commit(skipped, now_ms=3)
            committed = LifeTurnCommit(**{
                **common, "turn_commit_id": "tc_3", "stage": "RESPONSE_COMMITTED",
                "predecessor_commit_sha256": outcome.commit_sha256,
                "model_attempt_plan_ref": "map_" + "3" * 64,
                "model_attempt_refs": ("mar_" + "5" * 64,),
                "model_attempt_plan_outcome_ref": "mapo_" + "8" * 64,
                "expression_status": "model_available",
                "winner_attempt_ref": "mar_" + "5" * 64,
                "assistant_candidate_id": "asm_" + "a" * 64,
                "commit_sha256": "0" * 64,
            }).with_computed_commit_sha256()
            assert store.put_life_turn_commit(committed, now_ms=4)
            wrong_predecessor = LifeTurnCommit(**{
                **common, "turn_commit_id": "tc_4", "stage": "DELIVERY_OBSERVED",
                "predecessor_commit_sha256": "0" * 64,
                "delivery_ref": "del_1", "terminal_basis_ref": "tb_1",
                "commit_sha256": "0" * 64,
            }).with_computed_commit_sha256()
            with pytest.raises(LifeShadowStoreError, match="predecessor"):
                store.put_life_turn_commit(wrong_predecessor, now_ms=5)
            delivered = LifeTurnCommit(**{
                **common, "turn_commit_id": "tc_5", "stage": "DELIVERY_OBSERVED",
                "predecessor_commit_sha256": committed.commit_sha256,
                "delivery_ref": "del_1", "terminal_basis_ref": "tb_1",
                "commit_sha256": "0" * 64,
            }).with_computed_commit_sha256()
            assert store.put_life_turn_commit(delivered, now_ms=6)
            terminal = LifeTurnCommit(**{
                **common, "turn_commit_id": "tc_6", "stage": "ROOT_TERMINAL",
                "predecessor_commit_sha256": delivered.commit_sha256,
                "root_terminal_status": "CLOSED", "root_terminal_reason": "delivery.observed",
                "root_terminal_at_ms": 7, "terminal_basis_ref": "tb_1",
                "commit_sha256": "0" * 64,
            }).with_computed_commit_sha256()
            assert store.put_life_turn_commit(terminal, now_ms=8)
            chain = store.list_life_turn_commits(root_experience_id="root_1", response_episode_id="ep_1")
            assert [item.stage for item in chain] == [
                "OUTCOME_COMMITTED_RESPONSE_OPEN", "RESPONSE_COMMITTED",
                "DELIVERY_OBSERVED", "ROOT_TERMINAL",
            ]
            with pytest.raises(LifeShadowStoreError, match="reused"):
                duplicate = LifeTurnCommit(**{
                    **common, "turn_commit_id": derive_turn_commit_id(
                        life_id="life_1", root_experience_id="root_1",
                        child_episode_id="cep_1", stage="DELIVERY_OBSERVED",
                        predecessor_commit_sha256=committed.commit_sha256,
                    ),
                    "stage": "DELIVERY_OBSERVED",
                    "predecessor_commit_sha256": committed.commit_sha256,
                    "delivery_ref": "del_2", "terminal_basis_ref": "tb_2",
                    "commit_sha256": "0" * 64,
                }).with_computed_commit_sha256()
                store.put_life_turn_commit(duplicate, now_ms=9)
