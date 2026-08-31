"""G4 evidence: registration, compiler coverage/monotonic, semantic id, cancel, reconcile, composite."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from contracts import (
    ActionIntentVNext,
    GatewayRegistrationReceipt,
    LifeExecutionProposal,
    derive_occurrence_key,
    derive_stable_step_id,
)
from total_gateway.commitment_compiler import CommitmentCompiler, CommitmentCompilerError
from total_gateway.execution_engine import ExecutionEngine, ExecutionEngineError
from total_gateway.store import GatewayStateStore, StoreConflictError


H = "a" * 64
REQ = "req_" + "1" * 64
RUN = "run_" + "2" * 64


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as temporary:
        instance = GatewayStateStore.open(Path(temporary) / "gateway.sqlite3", now_ms=1)
        try:
            yield instance
        finally:
            instance.close()


def make_proposal_and_intent(*, proposal_id: str = "lpr_1", intent_id: str = "int_1", invocation: str = H):
    sid = derive_stable_step_id(anchor_id="ob_1", semantic_step_role="produce.artifact")
    occurrence = derive_occurrence_key(
        stable_step_id=sid, semantic_target_key="file://a.txt", semantic_occurrence_index=1
    )
    intent = ActionIntentVNext(
        intent_id=intent_id, source="life_scheduler", life_id="life_1",
        request_id=REQ, run_id=RUN, generation=0, action_id="file.write",
        action_version="v1", arguments_sha256=invocation, workspace_id="ws_1",
        workspace_scope_hash=H, run_life_binding_sha256=H,
        root_experience_id="root_1", episode_id="ep_1", episode_sha256=H,
        commitment_kind="none", intent_anchor_sha256=H, route_kind="ad_hoc",
        semantic_step_role="produce.artifact", semantic_target_key="file://a.txt",
        semantic_occurrence_index=1, stable_step_id=sid, occurrence_key=occurrence,
        canonical_invocation_sha256=invocation, absolute_deadline_ms=1000, cancel_generation=0,
        agency_decision_id="agd_1", agency_decision_sha256=H,
        created_at_ms=1, expires_at_ms=2, intent_sha256="0" * 64,
    ).with_computed_sha256()
    proposal = LifeExecutionProposal(
        proposal_id=proposal_id, life_id="life_1", run_life_binding_sha256=H,
        root_experience_id="root_1", episode_id="ep_1", episode_sha256=H,
        agency_decision_id="agd_1", agency_decision_sha256=H,
        action_candidate_object_ref="acd_1", action_candidate_sha256=H,
        commitment_kind="none", intent_anchor_sha256=H, action_id="file.write",
        action_version="v1", args_sha256=invocation, workspace_id="ws_1",
        semantic_step_role="produce.artifact", semantic_target_key="file://a.txt",
        semantic_occurrence_index=1, stable_step_id=sid, occurrence_key=occurrence,
        proposal_sha256="0" * 64,
    ).with_computed_sha256()
    receipt = GatewayRegistrationReceipt(
        registration_id=f"reg_{proposal_id}_{intent_id}", proposal_id=proposal_id,
        proposal_sha256=proposal.proposal_sha256,
        request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        run_life_binding_sha256=H, action_intent_id=intent_id,
        action_intent_sha256=intent.intent_sha256, registered_at_ms=10,
        registration_sha256="0" * 64,
    ).with_computed_sha256()
    return proposal, intent, receipt


def test_t14_proposal_registration_is_deterministic_and_idempotent(store) -> None:
    proposal, intent, receipt = make_proposal_and_intent()
    assert store.register_life_execution_proposal(proposal, intent, receipt, now_ms=10)
    assert not store.register_life_execution_proposal(proposal, intent, receipt, now_ms=11)
    with pytest.raises(ValueError, match="binding"):
        bad_receipt = receipt.model_copy(
            update={"run_life_binding_sha256": "0" * 64, "registration_sha256": "0" * 64}
        ).with_computed_sha256()
        store.register_life_execution_proposal(proposal, intent, bad_receipt, now_ms=12)
    other, other_intent, other_receipt = make_proposal_and_intent(proposal_id="lpr_2", intent_id="int_2")
    assert store.register_life_execution_proposal(other, other_intent, other_receipt, now_ms=13)
    rows = store._connection.execute("SELECT count(*) FROM life_proposal_registration").fetchone()[0]
    assert rows == 2


def test_t06_missing_mandatory_artifact_blocks_completion() -> None:
    compiler = CommitmentCompiler()
    requirements = compiler.compile(
        commitment_id="cm_1", request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        root_experience_id="root_1", raw_user_message="产出报告和表格",
        source_input_refs=("s_1",),
        explicit_output_constraints=(
            {"ref": "out_report", "acceptance_ref": "acceptance:report"},
            {"ref": "out_table", "acceptance_ref": "acceptance:table"},
        ),
    )
    artifact_ids = {
        item.obligation_id for item in requirements.obligations if item.kind == "artifact"
    }
    assert len(artifact_ids) == 2
    mandatory_ids = {
        item.obligation_id for item in requirements.obligations if item.mandatory
    }
    assert not CommitmentCompiler.completion_ready(requirements, mandatory_ids - {min(artifact_ids)})
    assert CommitmentCompiler.completion_ready(requirements, mandatory_ids)


def test_t07_route_cannot_reduce_obligations_without_user_amendment() -> None:
    compiler = CommitmentCompiler()
    requirements = compiler.compile(
        commitment_id="cm_1", request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        root_experience_id="root_1", raw_user_message="产出报告",
        source_input_refs=("s_1",),
        explicit_output_constraints=({"ref": "out_report", "acceptance_ref": "acceptance:report"},),
    )
    with pytest.raises(CommitmentCompilerError, match="cannot reduce"):
        compiler.amend(requirements, user_amendment=False, raw_user_message="产出报告")
    amended = compiler.amend(
        requirements, user_amendment=True, raw_user_message="只要报告即可",
        explicit_output_constraints=(),
    )
    assert amended.commitment_revision == 2
    assert amended.supersedes_sha256 == requirements.requirements_sha256


def test_t15_semantic_effect_id_survives_reorder_and_changes_with_parameters(store) -> None:
    engine = ExecutionEngine(store)
    base = dict(
        origin_request_id=REQ, origin_run_id=RUN, origin_run_sequence=1, origin_generation=0,
        parent_effect_id="eff_" + "3" * 64,
        stable_step_id=derive_stable_step_id(anchor_id="ob_1", semantic_step_role="produce.artifact"),
        occurrence_key=derive_occurrence_key(
            stable_step_id=derive_stable_step_id(anchor_id="ob_1", semantic_step_role="produce.artifact"),
            semantic_target_key="file://a.txt", semantic_occurrence_index=1,
        ),
        action_id="file.write", action_version="v1",
        canonical_invocation_sha256=H, component_manifest_sha256=H,
    )
    first = engine.semantic_effect(**base)
    # Insert a diagnostic step before: parent/step/target/occurrence unchanged -> same id.
    second = engine.semantic_effect(**base)
    assert first.effect_id == second.effect_id
    changed = engine.semantic_effect(**{**base, "canonical_invocation_sha256": "0" * 64})
    assert changed.effect_id != first.effect_id
    engine.ensure_semantic_identity(first, expected_invocation_sha256=H)


def test_t16_cancel_wins_zero_dispatch_dispatch_wins_never_fake_cancel(store) -> None:
    engine = ExecutionEngine(store)
    calls: list[int] = []

    def handler(context):
        calls.append(1)
        return {"status": "SUCCEEDED", "receipt": "r_1"}

    identity = engine.semantic_effect(
        origin_request_id=REQ, origin_run_id=RUN, origin_run_sequence=1, origin_generation=0,
        parent_effect_id="eff_" + "3" * 64,
        stable_step_id=derive_stable_step_id(anchor_id="ob_1", semantic_step_role="write"),
        occurrence_key=derive_occurrence_key(
            stable_step_id=derive_stable_step_id(anchor_id="ob_1", semantic_step_role="write"),
            semantic_target_key="file://b.txt", semantic_occurrence_index=1,
        ),
        action_id="file.write", action_version="v1",
        canonical_invocation_sha256=H, component_manifest_sha256=H,
    )
    cancelled = engine.dispatch(identity, handler=handler, cancel_generation=2, current_generation=1, now_ms=1)
    assert cancelled["status"] == "CANCELLED" and cancelled["dispatched"] is False
    assert calls == []
    dispatched = engine.dispatch(identity, handler=handler, cancel_generation=0, current_generation=1, now_ms=2)
    assert dispatched["status"] == "SUCCEEDED"
    assert calls == [1]


def test_t09_ambiguous_effect_is_never_redispatched(store) -> None:
    engine = ExecutionEngine(store)
    calls: list[int] = []

    def handler(context):
        calls.append(1)
        return {"status": "AMBIGUOUS", "dispatch_evidence": "d_1"}

    identity = engine.semantic_effect(
        origin_request_id=REQ, origin_run_id=RUN, origin_run_sequence=1, origin_generation=0,
        parent_effect_id="eff_" + "3" * 64,
        stable_step_id=derive_stable_step_id(anchor_id="ob_1", semantic_step_role="send"),
        occurrence_key=derive_occurrence_key(
            stable_step_id=derive_stable_step_id(anchor_id="ob_1", semantic_step_role="send"),
            semantic_target_key="recipient://u1", semantic_occurrence_index=1,
        ),
        action_id="message.send", action_version="v1",
        canonical_invocation_sha256=H, component_manifest_sha256=H,
    )
    engine.dispatch(identity, handler=handler, cancel_generation=0, current_generation=1, now_ms=1)
    assert calls == [1]
    with pytest.raises(ExecutionEngineError, match="redispatched"):
        engine.dispatch(identity, handler=handler, cancel_generation=0, current_generation=1, now_ms=2)
    assert calls == [1]


def test_t08_composite_machine_truth_and_retry_required(store) -> None:
    engine = ExecutionEngine(store)
    outcome = engine.aggregate(
        composite_execution_id="comp_1", request_id=REQ, run_id=RUN,
        run_sequence=1, generation=0, parent_effect_id="eff_" + "3" * 64,
        child_results=(("r_1", "SUCCEEDED"), ("r_2", "FAILED_RETRYABLE")),
        created_at_ms=1,
    )
    assert outcome.status == "PARTIAL_WITH_FAILURES"
    assert outcome.retry_required is True
    cancelled = engine.aggregate(
        composite_execution_id="comp_2", request_id=REQ, run_id=RUN,
        run_sequence=1, generation=0, parent_effect_id="eff_" + "3" * 64,
        child_results=(("r_3", "CANCELLED"), ("r_4", "FENCED")),
        created_at_ms=2,
    )
    assert cancelled.status == "CANCELLED"
    with pytest.raises(StoreConflictError):
        engine.aggregate(
            composite_execution_id="comp_1", request_id=REQ, run_id=RUN,
            run_sequence=1, generation=0, parent_effect_id="eff_" + "3" * 64,
            child_results=(("r_1", "SUCCEEDED"),), created_at_ms=3,
        )


def test_reconciliation_advances_outcome_head_with_cas(store) -> None:
    engine = ExecutionEngine(store)
    effect_id = "eff_" + "5" * 64
    store.put_effect_outcome_head(
        effect_id=effect_id, original_execution_result_ref="res_1",
        effective_status="AMBIGUOUS", head_revision=1, head_sha256=H,
        latest_reconciliation_ref=None, updated_at_ms=1, expected_head_sha256=None,
    )
    head = engine.reconcile(
        effect_id=effect_id, previous_outcome_head_sha256=H, attempt_no=1,
        strategy_id="strategy.query_remote", observation_status="APPLIED",
        observation_ref="obs_1", observed_at_ms=2,
    )
    assert head.effective_status == "SUCCEEDED"
    assert head.head_revision == 2
    with pytest.raises(StoreConflictError, match="monotonic"):
        engine.reconcile(
            effect_id=effect_id, previous_outcome_head_sha256=H, attempt_no=1,
            strategy_id="strategy.query_remote", observation_status="APPLIED",
            observation_ref="obs_2", observed_at_ms=3,
        )


def test_t05c_delivery_mode_classification() -> None:
    compiler = CommitmentCompiler()
    requirements = compiler.compile(
        commitment_id="cm_1", request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        root_experience_id="root_1", raw_user_message="产出报告并回复",
        source_input_refs=("s_1",),
        explicit_output_constraints=({"ref": "out_report", "acceptance_ref": "acceptance:report"},),
        delivery_constraints=({"ref": "del_reply", "acceptance_ref": "acceptance:reply"},),
    )
    execution_ids = {
        item.obligation_id
        for item in requirements.obligations
        if item.kind in {"execution", "artifact"}
    }
    assert compiler.classify_delivery_mode(requirements, execution_ids) == "response_delivery"
    assert compiler.classify_delivery_mode(
        requirements, execution_ids | {item.obligation_id for item in requirements.obligations if item.kind == "delivery"}
    ) == "none"
    assert compiler.classify_delivery_mode(requirements, set()) == "invalid"


def test_t26b_security_surface_files_unchanged_since_g0() -> None:
    baseline = {
        "src/contracts/authorization.py": "4f8a71aabbae804f6ce6d22ffc4d3b27f0f4b1b825906c55f49b56948d1a69dc",
        "src/contracts/security.py": "ad65e3cbb1b844333f8f873da5abfb49a2320aabe27056fb34f83baa5978c698",
        "src/contracts/scope.py": "4447d3db1f71dea26cbfd7a19704400f8d058c4fe3ec5b3bfa775de4d821f60e",
        "src/contracts/delivery_authorization.py": "95031e740a22137b5258205fe762fe408768fd3ddb947105d88fbfefa24c31ef",
        "src/total_gateway/completion_gate.py": "7fd7c3726cd11bd4db5b470b70d69581a5657cdb8c380044817fef8b83ef1409",
        "src/total_gateway/skill_authority.py": "41741c054b33cfe47752066b537c44cae2119c94c161156aceefbe734b1941ab",
    }
    root = Path(__file__).resolve().parents[1]
    import hashlib

    for relative, expected_sha256 in baseline.items():
        path = root / relative
        assert path.is_file(), relative
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        assert current == expected_sha256, (
            f"security surface changed since G0: {relative}"
        )
