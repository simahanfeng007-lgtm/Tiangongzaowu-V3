from __future__ import annotations

from dataclasses import replace
import ast
import tempfile
from pathlib import Path

import pytest

from contracts import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from life_service.memory_context import classify_instruction_authority
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from total_gateway.completion_gate import CompletionDecision
from world_understanding.capability_composition import (
    AttributionTraceV1,
    CapabilityExperienceObservationV1,
    CapabilityExperienceRecallQueryV1,
    apply_capability_experience_observation,
    assess_capability_experience_source_freshness,
    attribution_has_valid_sha256,
    build_capability_experience_memory_intent,
    build_memory_coordinator_disposition,
    build_negative_capability_evidence,
    completion_evidence_from_decision,
    evaluate_attribution_integrity,
    evaluate_capability_experience_admission,
    exact_source_hashes,
    experience_has_valid_sha256,
    mark_capability_experience_source_change,
    posterior_success_milli,
    recall_capability_experiences,
    source_revision_family,
    wilson_lower_confidence_milli,
)

from tests.life_contract_support import LIFE_ID, event
from tests.test_capability_composition_p4 import (
    H,
    _single_read_fixture,
)


PRIVACY_HASH = canonical_sha256({"privacy_scope": "private"})
VERIFICATION_PLAN_HASH = "e" * 64
VERIFICATION_READINESS_HASH = "f" * 64


def _scoped_plan(
    ordinal: int,
    *,
    context_fingerprint: str | None = None,
):
    registry, candidates, context, document = _single_read_fixture()
    proposal = __import__(
        "world_understanding.capability_composition",
        fromlist=["parse_composition_proposal"],
    ).parse_composition_proposal(document, candidates)
    request_id = "req_" + f"{ordinal + 100:064x}"
    run_id = "run_" + f"{ordinal + 200:064x}"
    fingerprint = context_fingerprint or canonical_sha256(
        {"context": ordinal}
    )
    scoped_context = replace(
        context,
        request_id=request_id,
        run_id=run_id,
        context_fingerprint_sha256=fingerprint,
        context_sha256="0" * 64,
    ).with_computed_sha256()
    plan = __import__(
        "world_understanding.capability_composition",
        fromlist=["compile_capability_composition_plan"],
    ).compile_capability_composition_plan(
        proposal, candidates, scoped_context, registry
    )
    return plan


def _completion(
    plan,
    *,
    outcome: str = "COMPLETED",
    verification_mode: str = "PLAN_BOUND",
    verification_ready: bool = True,
    needs_reconciliation: bool = False,
    fact_id: str = "fact_terminal",
) -> CompletionDecision:
    completed = outcome == "COMPLETED"
    value = CompletionDecision(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        outcome=outcome,
        reason_code="completion.p5-test",
        text_ready=completed,
        execution_ready=completed,
        artifacts_ready=completed,
        delivery_ready=completed,
        can_transition_request_completed=completed,
        can_claim_platform_delivered=False,
        needs_reconciliation=needs_reconciliation,
        execution_effect_states=(),
        artifact_revision_states=(),
        delivery_parts=(),
        supporting_fact_ids=(fact_id,),
        outbound_plan_sha256=None,
        delivery_receipt_sha256=None,
        candidate_text_sha256=H,
        verification_mode=verification_mode,
        verification_ready=verification_ready,
        verification_plan_sha256=(
            VERIFICATION_PLAN_HASH
            if verification_mode == "PLAN_BOUND"
            else None
        ),
        verification_readiness_sha256=(
            VERIFICATION_READINESS_HASH
            if verification_mode == "PLAN_BOUND"
            else None
        ),
        model_generated=False,
        decision_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _trace(
    plan,
    *,
    completion: CompletionDecision | None = None,
    principal_scope_hash: str = H,
    privacy_scope_hash: str = PRIVACY_HASH,
    has_acceptance_obligations: bool = True,
    active_verification_plan_complete: bool = True,
    effect_fact_lineage_complete: bool = True,
    source_refs_complete: bool = True,
    source_revisions_continuous: bool = True,
    request_scope_continuous: bool = True,
    human_takeover: bool = False,
    alternate_execution_chain: bool = False,
    unknown_external_overwrite: bool = False,
    unknown_side_effects: bool = False,
    unresolved_reconciliation: bool = False,
    secret_or_credential_present: bool = False,
    prompt_injection_present: bool = False,
    context_identity_truncated: bool = False,
    observed_method_source_refs=None,
    observed_action_source_refs=None,
    ordinal: int = 1,
) -> AttributionTraceV1:
    decision = completion or _completion(
        plan, fact_id=f"fact_terminal_{ordinal}"
    )
    completion_evidence = completion_evidence_from_decision(decision)
    verification_records = (
        (f"verification_record_{ordinal}",)
        if decision.verification_mode == "PLAN_BOUND"
        else ()
    )
    fact_id = decision.supporting_fact_ids[0]
    value = AttributionTraceV1(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=principal_scope_hash,
        privacy_scope_hash=privacy_scope_hash,
        composition_plan_sha256=plan.plan_sha256,
        completion=completion_evidence,
        active_verification_plan_sha256=(
            decision.verification_plan_sha256
        ),
        verification_record_refs=verification_records,
        terminal_effect_ids=(f"effect_terminal_{ordinal}",),
        terminal_fact_ids=(fact_id,),
        terminal_fact_hashes=(canonical_sha256({"fact": ordinal}),),
        observed_method_source_refs=(
            plan.method_source_refs
            if observed_method_source_refs is None
            else observed_method_source_refs
        ),
        observed_action_source_refs=(
            plan.action_source_refs
            if observed_action_source_refs is None
            else observed_action_source_refs
        ),
        has_acceptance_obligations=has_acceptance_obligations,
        active_verification_plan_complete=active_verification_plan_complete,
        effect_fact_lineage_complete=effect_fact_lineage_complete,
        source_refs_complete=source_refs_complete,
        source_revisions_continuous=source_revisions_continuous,
        request_scope_continuous=request_scope_continuous,
        human_takeover=human_takeover,
        alternate_execution_chain=alternate_execution_chain,
        unknown_external_overwrite=unknown_external_overwrite,
        unknown_side_effects=unknown_side_effects,
        unresolved_reconciliation=unresolved_reconciliation,
        secret_or_credential_present=secret_or_credential_present,
        prompt_injection_present=prompt_injection_present,
        context_identity_truncated=context_identity_truncated,
        collected_at_ms=20 + ordinal,
        trace_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _observation(
    ordinal: int,
    *,
    plan=None,
    trace: AttributionTraceV1 | None = None,
    outcome: str = "SUCCESS",
    quality_milli: int = 900,
    failure_category: str | None = None,
    failure_reason_codes: tuple[str, ...] = (),
    life_id: str = "life_capability_experience",
    principal_ref: str = "principal_test",
    principal_scope_hash: str = H,
    privacy_scope: str = "private",
    privacy_scope_hash: str = PRIVACY_HASH,
):
    plan_value = plan or _scoped_plan(ordinal)
    trace_value = trace or _trace(
        plan_value,
        principal_scope_hash=principal_scope_hash,
        privacy_scope_hash=privacy_scope_hash,
        ordinal=ordinal,
    )
    attribution = evaluate_attribution_integrity(
        plan_value,
        trace_value,
        expected_principal_scope_hash=principal_scope_hash,
        expected_privacy_scope_hash=privacy_scope_hash,
        checked_at_ms=30 + ordinal,
    )
    value = CapabilityExperienceObservationV1(
        observation_id=f"capability_observation_{ordinal}",
        life_id=life_id,
        principal_ref=principal_ref,
        principal_scope_hash=principal_scope_hash,
        privacy_scope=privacy_scope,
        privacy_scope_hash=privacy_scope_hash,
        goal_class="goalclass.artifact-read",
        environment_class=plan_value.environment_class,
        scene_fingerprint=canonical_sha256({"scene": ordinal}),
        context_fingerprint_sha256=(
            plan_value.context_fingerprint_sha256
        ),
        composition_topology_sha256=(
            plan_value.dependency_graph_sha256
        ),
        plan=plan_value,
        trace=trace_value,
        attribution=attribution,
        outcome=outcome,
        quality_milli=quality_milli,
        failure_category=failure_category,
        failure_reason_codes=failure_reason_codes,
        observed_at_ms=40 + ordinal,
        observation_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _admission(observation):
    return evaluate_capability_experience_admission(
        observation,
        expected_principal_scope_hash=observation.principal_scope_hash,
        expected_privacy_scope_hash=observation.privacy_scope_hash,
        decided_at_ms=observation.observed_at_ms + 1,
    )


def test_attribution_passes_only_for_exact_machine_verified_lineage() -> None:
    plan = _scoped_plan(1)
    trace = _trace(plan, ordinal=1)
    attribution = evaluate_attribution_integrity(
        plan,
        trace,
        expected_principal_scope_hash=H,
        expected_privacy_scope_hash=PRIVACY_HASH,
        checked_at_ms=31,
    )
    assert attribution.state == "PASS"
    assert attribution.reason_codes == ()
    assert attribution_has_valid_sha256(attribution)


@pytest.mark.parametrize(
    "trace_change,expected_reason",
    (
        ({"human_takeover": True}, "attribution.human_takeover"),
        (
            {"alternate_execution_chain": True},
            "attribution.alternate_execution_chain",
        ),
        (
            {"unknown_external_overwrite": True},
            "attribution.unknown_external_overwrite",
        ),
        (
            {"unknown_side_effects": True},
            "attribution.unknown_side_effects",
        ),
        (
            {"secret_or_credential_present": True},
            "attribution.secret_or_credential_present",
        ),
        (
            {"prompt_injection_present": True},
            "attribution.prompt_injection_present",
        ),
        (
            {"context_identity_truncated": True},
            "attribution.context_identity_truncated",
        ),
        (
            {"effect_fact_lineage_complete": False},
            "attribution.effect_fact_lineage_incomplete",
        ),
        (
            {"source_revisions_continuous": False},
            "attribution.source_revision_discontinuous",
        ),
        (
            {"request_scope_continuous": False},
            "attribution.request_scope_discontinuous",
        ),
    ),
)
def test_attribution_rejects_broken_chain_flags(
    trace_change: dict, expected_reason: str
) -> None:
    plan = _scoped_plan(2)
    trace = _trace(plan, ordinal=2, **trace_change)
    attribution = evaluate_attribution_integrity(
        plan,
        trace,
        expected_principal_scope_hash=H,
        expected_privacy_scope_hash=PRIVACY_HASH,
        checked_at_ms=32,
    )
    assert attribution.state == "FAIL"
    assert expected_reason in attribution.reason_codes


def test_attribution_rejects_incomplete_completion_and_verification() -> None:
    plan = _scoped_plan(3)
    decision = _completion(
        plan,
        outcome="FAILED",
        verification_ready=False,
        fact_id="fact_failed",
    )
    trace = _trace(
        plan,
        completion=decision,
        active_verification_plan_complete=False,
        effect_fact_lineage_complete=False,
        ordinal=3,
    )
    attribution = evaluate_attribution_integrity(
        plan,
        trace,
        expected_principal_scope_hash=H,
        expected_privacy_scope_hash=PRIVACY_HASH,
        checked_at_ms=33,
    )
    assert attribution.state == "FAIL"
    assert "attribution.completion_not_completed" in attribution.reason_codes
    assert "attribution.verification_not_ready" in attribution.reason_codes
    assert "attribution.verification_plan_incomplete" in attribution.reason_codes


def test_attribution_rejects_source_and_scope_drift() -> None:
    plan = _scoped_plan(4)
    changed_source = plan.action_source_refs[0].model_copy(
        update={"source_sha256": "9" * 64}
    )
    trace = _trace(
        plan,
        principal_scope_hash="8" * 64,
        privacy_scope_hash="7" * 64,
        observed_action_source_refs=(changed_source,),
        ordinal=4,
    )
    attribution = evaluate_attribution_integrity(
        plan,
        trace,
        expected_principal_scope_hash=H,
        expected_privacy_scope_hash=PRIVACY_HASH,
        checked_at_ms=34,
    )
    assert attribution.state == "FAIL"
    assert "attribution.principal_scope_mismatch" in attribution.reason_codes
    assert "attribution.privacy_scope_mismatch" in attribution.reason_codes
    assert "attribution.action_source_mismatch" in attribution.reason_codes


def test_positive_gate_requires_completed_verified_attributed_reality() -> None:
    observation = _observation(5)
    admission = _admission(observation)
    assert admission.decision == "POSITIVE_EXPERIENCE"
    assert admission.positive_allowed is True
    assert admission.negative_allowed is False
    assert admission.failure_category is None
    assert admission.has_valid_sha256()

    failed_plan = _scoped_plan(6)
    failed_trace = _trace(
        failed_plan,
        completion=_completion(
            failed_plan,
            outcome="FAILED",
            verification_ready=False,
            fact_id="fact_failed_6",
        ),
        active_verification_plan_complete=False,
        effect_fact_lineage_complete=False,
        ordinal=6,
    )
    failed = _observation(
        6,
        plan=failed_plan,
        trace=failed_trace,
        outcome="FAILURE",
        quality_milli=0,
        failure_category="RUNTIME_FAILURE",
        failure_reason_codes=("runtime.failed",),
    )
    rejected = _admission(failed)
    assert rejected.decision == "NEGATIVE_EVIDENCE"
    assert rejected.positive_allowed is False
    assert rejected.negative_allowed is True


def test_failure_creates_negative_data_not_positive_muscle_memory() -> None:
    plan = _scoped_plan(7)
    failed_trace = _trace(
        plan,
        completion=_completion(
            plan,
            outcome="FAILED",
            verification_ready=False,
            fact_id="fact_failed_7",
        ),
        active_verification_plan_complete=False,
        ordinal=7,
    )
    observation = _observation(
        7,
        plan=plan,
        trace=failed_trace,
        outcome="FAILURE",
        quality_milli=0,
        failure_category="TOOL_UNAVAILABLE",
        failure_reason_codes=("tool.unavailable",),
    )
    admission = _admission(observation)
    evidence = build_negative_capability_evidence(observation, admission)
    state, returned = apply_capability_experience_observation(
        None, observation, admission
    )
    assert evidence.has_valid_sha256()
    assert returned == evidence
    assert evidence.context_section == "DATA"
    assert evidence.instruction_authority is False
    assert evidence.world_authority is False
    assert evidence.may_authorize is False
    assert state.experience.success_count == 0
    assert state.experience.failure_count == 1
    assert state.experience.lifecycle == "PROBATION"
    assert state.context_section == "DATA"
    assert state.instruction_authority is False
    assert state.world_authority is False


def test_new_positive_experience_is_always_probation() -> None:
    observation = _observation(8)
    state, negative = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    assert negative is None
    assert state.has_valid_sha256()
    assert experience_has_valid_sha256(state.experience)
    assert state.experience.success_count == 1
    assert state.experience.failure_count == 0
    assert state.experience.independent_context_count == 1
    assert state.experience.lifecycle == "PROBATION"


def test_five_successes_four_contexts_promote_to_stable_deterministically() -> None:
    state = None
    for ordinal in range(10, 15):
        context_index = ordinal if ordinal < 14 else 13
        plan = _scoped_plan(
            ordinal,
            context_fingerprint=canonical_sha256(
                {"independent-context": context_index}
            ),
        )
        observation = _observation(ordinal, plan=plan)
        state, negative = apply_capability_experience_observation(
            state, observation, _admission(observation)
        )
        assert negative is None
    assert state is not None
    assert state.experience.success_count == 5
    assert state.experience.failure_count == 0
    assert state.experience.independent_context_count == 4
    assert state.experience.lower_confidence_milli >= 700
    assert state.experience.lifecycle == "STABLE"
    assert state.has_valid_sha256()


def test_duplicate_observation_is_idempotent() -> None:
    observation = _observation(20)
    admission = _admission(observation)
    first, negative = apply_capability_experience_observation(
        None, observation, admission
    )
    second, duplicate_negative = apply_capability_experience_observation(
        first, observation, admission
    )
    assert negative is None
    assert duplicate_negative is None
    assert second == first


def test_positive_and_negative_counts_remain_disjoint() -> None:
    success = _observation(21)
    state, _ = apply_capability_experience_observation(
        None, success, _admission(success)
    )
    plan = _scoped_plan(22)
    trace = _trace(
        plan,
        completion=_completion(
            plan,
            outcome="FAILED",
            verification_ready=False,
            fact_id="fact_failed_22",
        ),
        active_verification_plan_complete=False,
        ordinal=22,
    )
    failure = _observation(
        22,
        plan=plan,
        trace=trace,
        outcome="FAILURE",
        quality_milli=0,
        failure_category="VERIFICATION_FAILURE",
        failure_reason_codes=("verification.failed",),
    )
    updated, evidence = apply_capability_experience_observation(
        state, failure, _admission(failure)
    )
    assert evidence is not None
    assert updated.experience.success_count == 1
    assert updated.experience.failure_count == 1
    assert updated.quality_observation_count == 1
    assert updated.quality_sum_milli == success.quality_milli


def test_fixed_point_statistics_are_replay_stable_and_float_free() -> None:
    assert posterior_success_milli(5, 0) == 857
    assert wilson_lower_confidence_milli(5, 0) == 752
    assert wilson_lower_confidence_milli(5, 0) == (
        wilson_lower_confidence_milli(5, 0)
    )
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "src"
        / "world_understanding"
        / "capability_composition"
        / "capability_experience_policy.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Constant) and isinstance(node.value, float)
        for node in ast.walk(tree)
    )


def test_source_revision_change_requires_revalidation() -> None:
    observation = _observation(30)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    current_sources = (
        *observation.plan.method_source_refs,
        *observation.plan.action_source_refs,
    )
    changed_action = current_sources[-1].model_copy(
        update={"source_sha256": "6" * 64}
    )
    changed_sources = (*current_sources[:-1], changed_action)
    assert (
        assess_capability_experience_source_freshness(state, changed_sources)
        == "REVALIDATION_REQUIRED"
    )
    updated, intent = mark_capability_experience_source_change(
        state, changed_sources, requested_at_ms=100
    )
    assert updated.experience.lifecycle == "REVALIDATION_REQUIRED"
    assert intent is not None and intent.has_valid_sha256()
    assert intent.reason_code == (
        "capability_experience.source_revision_changed"
    )


def test_source_family_change_marks_experience_stale() -> None:
    observation = _observation(31)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    current_sources = (
        *observation.plan.method_source_refs,
        *observation.plan.action_source_refs,
    )
    changed_action = current_sources[-1].model_copy(
        update={"version": "omni-registry-v2"}
    )
    changed_sources = (*current_sources[:-1], changed_action)
    assert (
        assess_capability_experience_source_freshness(state, changed_sources)
        == "STALE"
    )
    updated, intent = mark_capability_experience_source_change(
        state, changed_sources, requested_at_ms=100
    )
    assert updated.experience.lifecycle == "STALE"
    assert intent is not None and intent.reason_code == (
        "capability_experience.source_family_changed"
    )


def test_recall_is_exactly_principal_privacy_and_source_scoped() -> None:
    observation = _observation(40)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    sources = (
        *observation.plan.method_source_refs,
        *observation.plan.action_source_refs,
    )
    base = dict(
        principal_scope_hash=observation.principal_scope_hash,
        privacy_scope_hash=observation.privacy_scope_hash,
        goal_class=observation.goal_class,
        environment_class=observation.environment_class,
        current_source_revision_family=source_revision_family(sources),
        current_exact_source_hashes=exact_source_hashes(sources),
        now_ms=100,
        include_probation=True,
        limit=8,
    )
    assert len(recall_capability_experiences((state,), CapabilityExperienceRecallQueryV1(**base))) == 1
    assert recall_capability_experiences(
        (state,),
        CapabilityExperienceRecallQueryV1(
            **{**base, "principal_scope_hash": "4" * 64}
        ),
    ) == ()
    assert recall_capability_experiences(
        (state,),
        CapabilityExperienceRecallQueryV1(
            **{**base, "privacy_scope_hash": "5" * 64}
        ),
    ) == ()
    stale_state = state.model_copy(
        update={
            "experience": state.experience.model_copy(
                update={"lifecycle": "STALE", "experience_sha256": "0" * 64}
            )
        }
    )
    stale_experience = stale_state.experience.model_copy(
        update={
            "experience_sha256": stale_state.experience.computed_sha256()
            if hasattr(stale_state.experience, "computed_sha256")
            else state.experience.experience_sha256
        }
    )
    # Use the production helper rather than relying on a method not present on
    # the frozen P1 contract.
    from world_understanding.capability_composition import computed_experience_sha256
    stale_experience = stale_experience.model_copy(
        update={"experience_sha256": computed_experience_sha256(stale_experience)}
    )
    stale_state = stale_state.model_copy(
        update={"experience": stale_experience, "state_sha256": "0" * 64}
    ).with_computed_sha256()
    assert recall_capability_experiences(
        (stale_state,), CapabilityExperienceRecallQueryV1(**base)
    ) == ()


def test_memory_intent_is_l3_capability_knowledge_data_only() -> None:
    observation = _observation(50)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    intent, plaintext = build_capability_experience_memory_intent(
        state,
        parent_derivation_ids=("mdr_parent",),
        created_at_ms=100,
    )
    assert intent.has_valid_sha256()
    assert intent.layer == "L3_EXPERIENCE"
    assert intent.semantic_domain == "CAPABILITY_KNOWLEDGE"
    assert intent.context_section == "DATA"
    assert intent.instruction_authority is False
    assert intent.world_candidate_eligible is False
    assert intent.may_authorize is False
    assert intent.may_execute is False
    assert intent.may_write_store is False
    assert intent.coordinator_required is True
    assert b'"instruction_authority":false' in plaintext
    assert b'"world_authority":false' in plaintext


def test_existing_memory_coordinator_materializes_the_only_l3_write() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "p5-memory.shadow.sqlite3"
        with LifeShadowStore.open(path, create=True, now_ms=500) as store:
            coordinator = MemoryCoordinator(store)
            life_event = event(1, None, life_id=LIFE_ID)
            _assertion, parent, created = coordinator.commit_life_event_l1(
                life_event
            )
            assert created is True

            observation = _observation(
                60,
                life_id=LIFE_ID,
                principal_ref=life_event.principal_ref,
                privacy_scope=life_event.privacy_scope,
            )
            state, _ = apply_capability_experience_observation(
                None, observation, _admission(observation)
            )
            intent, plaintext = build_capability_experience_memory_intent(
                state,
                parent_derivation_ids=(parent.derivation_id,),
                created_at_ms=100,
            )
            disposition = build_memory_coordinator_disposition(
                state, intent, (parent,)
            )
            assertion, derivation, stored = coordinator._materialize_promotion(
                disposition=disposition,
                parents=(parent,),
                principal_ref=state.principal_ref,
                privacy_scope=state.privacy_scope,
                plaintext=plaintext,
                created_at_ms=intent.created_at_ms,
                policy_version=intent.policy_version,
            )
            assert stored is True
            assert derivation.layer == "L3_EXPERIENCE"
            assert derivation.semantic_domain == "CAPABILITY_KNOWLEDGE"
            assert derivation.world_candidate_eligible is False
            assert derivation.principal_ref == life_event.principal_ref
            assert derivation.privacy_scope == life_event.privacy_scope
            assert classify_instruction_authority(
                derivation, assertion, plaintext.decode("utf-8")
            ) == "DATA"


def test_p5_modules_do_not_open_store_or_execution_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    package = (
        root
        / "src"
        / "world_understanding"
        / "capability_composition"
    )
    paths = (
        package / "capability_experience_attribution.py",
        package / "capability_experience_policy.py",
        package / "capability_experience_memory.py",
    )
    forbidden = (
        "sqlite3.connect",
        "LifeShadowStore.open",
        "put_live_memory_assertion(",
        "put_memory_derivation(",
        "WorldStateStore",
        "threading.Thread",
        "subprocess",
        "from total_gateway",
        "import total_gateway",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name}:{token}"

    generic = (
        root / "src" / "life_service" / "memory_promotion.py"
    ).read_text(encoding="utf-8")
    assert "capability_experience_policy" not in generic
    assert "capability_experience_attribution" not in generic
