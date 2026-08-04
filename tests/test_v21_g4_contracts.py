"""G4 contract evidence: proposals, intents, effect identity, results, composite, requirements."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts import (
    ActionIntentVNext,
    CompletionObligation,
    CompletionRequirementsVNext,
    CompositeExecutionOutcome,
    CoverageProofRow,
    EffectIdentityVNext,
    EffectOutcomeHead,
    ExecutionResultVNext,
    LifeExecutionProposal,
    derive_execution_effect_id_vnext,
    derive_occurrence_key,
    derive_stable_step_id,
    semantic_tuple_conflict,
)
from contracts import canonical_sha256


H = "a" * 64
REQ = "req_" + "1" * 64
RUN = "run_" + "2" * 64


def intent_values(**overrides):
    values = dict(
        intent_id="int_1", source="life_scheduler", life_id="life_1",
        request_id=REQ, run_id=RUN, generation=0, action_id="file.write",
        action_version="v1", arguments_sha256=H, workspace_id="ws_1",
        workspace_scope_hash=H, run_life_binding_sha256=H,
        root_experience_id="root_1", episode_id="ep_1", episode_sha256=H,
        commitment_kind="none", intent_anchor_sha256=H, route_kind="ad_hoc",
        semantic_step_role="produce.artifact", semantic_target_key="file://a.txt",
        semantic_occurrence_index=1, stable_step_id=H, occurrence_key=H,
        canonical_invocation_sha256=H, absolute_deadline_ms=1000, cancel_generation=0,
        agency_decision_id="agd_1", agency_decision_sha256=H,
        created_at_ms=1, expires_at_ms=2, intent_sha256="0" * 64,
    )
    values.update(overrides)
    return values


def test_action_intent_vnext_work_and_none_shapes() -> None:
    none_intent = ActionIntentVNext(**intent_values()).with_computed_sha256()
    assert none_intent.has_valid_sha256()
    with pytest.raises(ValidationError):
        ActionIntentVNext(**intent_values(agency_decision_id=None))
    work = ActionIntentVNext(**intent_values(
        commitment_kind="work", agency_decision_id=None, agency_decision_sha256=None,
        commitment_id="cm_1", commitment_sha256=H, obligation_id="ob_1",
        obligation_set_sha256=H,
    )).with_computed_sha256()
    assert work.commitment_kind == "work"
    with pytest.raises(ValidationError):
        ActionIntentVNext(**intent_values(
            commitment_kind="work", agency_decision_id=None, agency_decision_sha256=None,
            commitment_id="cm_1", commitment_sha256="0" * 64, obligation_id="ob_1",
            obligation_set_sha256=H,
        ))
    with pytest.raises(ValidationError):
        ActionIntentVNext(**intent_values(
            route_kind="release_skill", skill_id="sk_1", skill_version=None, skill_sha256=H,
        ))


def test_life_execution_proposal_shapes() -> None:
    values = dict(
        proposal_id="lpr_1", life_id="life_1", run_life_binding_sha256=H,
        root_experience_id="root_1", episode_id="ep_1", episode_sha256=H,
        agency_decision_id="agd_1", agency_decision_sha256=H,
        action_candidate_object_ref="acd_1", action_candidate_sha256=H,
        commitment_kind="none", intent_anchor_sha256=H, action_id="file.write",
        action_version="v1", args_sha256=H, workspace_id="ws_1",
        semantic_step_role="produce.artifact", semantic_target_key="file://a.txt",
        semantic_occurrence_index=1, stable_step_id=H, occurrence_key=H,
        proposal_sha256="0" * 64,
    )
    none_proposal = LifeExecutionProposal(**values).with_computed_sha256()
    assert none_proposal.has_valid_sha256()
    with pytest.raises(ValidationError):
        LifeExecutionProposal(**{**values, "intent_anchor_sha256": "0" * 64})
    work = LifeExecutionProposal(**{
        **values, "commitment_kind": "work", "commitment_id": "cm_1",
        "commitment_sha256": H, "obligation_id": "ob_1", "obligation_set_sha256": H,
    }).with_computed_sha256()
    assert work.commitment_kind == "work"


def test_effect_identity_vnext_kinds_and_semantic_conflict() -> None:
    sid = derive_stable_step_id(anchor_id="ob_1", semantic_step_role="produce.artifact")
    occurrence = derive_occurrence_key(
        stable_step_id=sid, semantic_target_key="file://a.txt", semantic_occurrence_index=1
    )
    effect_1 = derive_execution_effect_id_vnext(
        parent_effect_id="eff_" + "3" * 64, stable_step_id=sid, occurrence_key=occurrence,
        action_id="file.write", action_version="v1", canonical_invocation_sha256=H,
    )
    left = EffectIdentityVNext(
        effect_id=effect_1, origin_request_id=REQ, origin_run_id=RUN,
        origin_run_sequence=1, origin_generation=0, effect_kind="execution",
        parent_effect_id="eff_" + "3" * 64, semantic_step_role="produce.artifact",
        semantic_target_key="file://a.txt", semantic_occurrence_index=1,
        stable_step_id=sid, occurrence_key=occurrence, action_id="file.write",
        action_version="v1", canonical_invocation_sha256=H, component_manifest_sha256=H,
    )
    assert left.has_valid_effect_id()
    right = left.model_copy(update={"canonical_invocation_sha256": "0" * 64})
    assert semantic_tuple_conflict(left, right)
    different_occurrence = left.model_copy(
        update={"semantic_occurrence_index": 2, "occurrence_key": "0" * 64}
    )
    assert not semantic_tuple_conflict(left, different_occurrence)
    with pytest.raises(ValidationError):
        EffectIdentityVNext(
            effect_id=effect_1, origin_request_id=REQ, origin_run_id=RUN,
            origin_run_sequence=1, origin_generation=0, effect_kind="execution",
            parent_effect_id="eff_" + "3" * 64, semantic_step_role=None,
            semantic_target_key="file://a.txt", semantic_occurrence_index=1,
            stable_step_id=sid, occurrence_key=occurrence, action_id="file.write",
            action_version="v1", canonical_invocation_sha256=H, component_manifest_sha256=H,
        )


def test_execution_result_vnext_evidence_rules() -> None:
    base = dict(
        result_id="res_1", ticket_id="t_1", request_id=REQ, run_id=RUN, generation=0,
        effect_id="eff_" + "3" * 64, action_id="file.write", action_version="v1",
        status="SUCCEEDED", attempt=1, started_at_ms=1, finished_at_ms=2,
        side_effect_started=True, result_payload_sha256=H,
        dispatch_evidence_sha256=H, remote_receipt_sha256=H,
        output_object_refs=(), fact_ids=("f_1",),
    )
    assert ExecutionResultVNext(**base)
    with pytest.raises(ValidationError):
        ExecutionResultVNext(**{**base, "remote_receipt_sha256": None})
    with pytest.raises(ValidationError):
        ExecutionResultVNext(**{**base, "dispatch_evidence_sha256": None})
    ambiguous = ExecutionResultVNext(**{
        **base, "status": "AMBIGUOUS", "remote_receipt_sha256": None,
        "error_code": "remote.receipt_lost",
    })
    assert ambiguous.status == "AMBIGUOUS"
    failed_started = ExecutionResultVNext(**{
        **base, "status": "FAILED_FINAL", "remote_receipt_sha256": None,
        "conclusive_remote_rejection_sha256": H, "error_code": "remote.rejected",
    })
    assert failed_started.status == "FAILED_FINAL"
    with pytest.raises(ValidationError):
        ExecutionResultVNext(**{
            **base, "status": "FAILED_RETRYABLE", "side_effect_started": True,
            "error_code": "remote.retryable",
        })


def test_effect_outcome_head_reconcile_mapping() -> None:
    assert EffectOutcomeHead.reconcile_mapping("APPLIED") == "SUCCEEDED"
    assert EffectOutcomeHead.reconcile_mapping("PROVEN_NOT_APPLIED") == "FAILED_RETRYABLE"
    assert EffectOutcomeHead.reconcile_mapping("INCONCLUSIVE") == "AMBIGUOUS"


def test_composite_outcome_machine_matrix() -> None:
    derive = CompositeExecutionOutcome.derive_status
    assert derive(("SUCCEEDED",)) == "SUCCEEDED"
    assert derive(("SUCCEEDED", "SUCCEEDED"), warning_refs=("w",)) == "SUCCEEDED_WITH_WARNINGS"
    assert derive(("CANCELLED", "FENCED")) == "CANCELLED"
    assert derive(("SUCCEEDED", "FAILED_FINAL")) == "PARTIAL_WITH_FAILURES"
    assert derive(("FAILED_FINAL", "FAILED_FINAL")) == "FAILED"
    assert derive(("FAILED_RETRYABLE", "FAILED_RETRYABLE")) == "RETRY_REQUIRED"
    assert derive(("SUCCEEDED", "AMBIGUOUS")) == "RECONCILE_REQUIRED"
    composite = CompositeExecutionOutcome(
        composite_execution_id="c_1", request_id=REQ, run_id=RUN, run_sequence=1,
        generation=0, parent_effect_id="eff_" + "3" * 64, child_result_refs=("r_1",),
        status="SUCCEEDED", retry_required=False, summary_sha256=H, created_at_ms=1,
        composite_outcome_sha256="0" * 64,
    ).with_computed_sha256()
    assert composite.has_valid_sha256()


def test_completion_requirements_vnext_coverage_and_revision() -> None:
    obligation = CompletionObligation(
        obligation_id="ob_1", kind="delivery", source_kind="user",
        source_requirement_stable_key="user#1", source_refs=("s_1",),
        mandatory=True, acceptance_ref="ac_1", delivery_phase="response",
    )
    obligation_set_sha256 = canonical_sha256([obligation.model_dump(mode="json")])
    requirements = CompletionRequirementsVNext(
        commitment_id="cm_1", commitment_sha256="0" * 64,
        request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        root_experience_id="root_1", raw_goal_sha256=H, source_input_refs=("s_1",),
        source_input_set_sha256=canonical_sha256(["s_1"]), commitment_revision=1,
        obligations=(obligation,), obligation_set_sha256=obligation_set_sha256,
        coverage_proof=(CoverageProofRow(
            source_requirement_stable_key="user#1", source_refs=("s_1",),
            obligation_ids=("ob_1",), coverage_status="COVERED",
        ),),
        requirements_sha256="0" * 64,
    ).with_computed_sha256()
    assert requirements.has_valid_requirements_sha256()
    assert requirements.commitment_sha256 == requirements.requirements_sha256
    with pytest.raises(ValidationError):
        CompletionRequirementsVNext(
            commitment_id="cm_1", commitment_sha256="0" * 64,
            request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
            root_experience_id="root_1", raw_goal_sha256=H, source_input_refs=("s_1",),
            source_input_set_sha256=canonical_sha256(["s_1"]), commitment_revision=2,
            obligations=(obligation,), obligation_set_sha256=obligation_set_sha256,
            coverage_proof=(CoverageProofRow(
                source_requirement_stable_key="user#1", source_refs=("s_1",),
                obligation_ids=("ob_1",), coverage_status="COVERED",
            ),),
            requirements_sha256="0" * 64,
        )
    with pytest.raises(ValidationError):
        CompletionRequirementsVNext(
            commitment_id="cm_1", commitment_sha256="0" * 64,
            request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
            root_experience_id="root_1", raw_goal_sha256=H, source_input_refs=("s_1",),
            source_input_set_sha256=canonical_sha256(["s_1"]), commitment_revision=1,
            obligations=(obligation,), obligation_set_sha256=obligation_set_sha256,
            coverage_proof=(CoverageProofRow(
                source_requirement_stable_key="user#1", source_refs=("s_1",),
                obligation_ids=(), coverage_status="COVERED",
            ),),
            requirements_sha256="0" * 64,
        )
