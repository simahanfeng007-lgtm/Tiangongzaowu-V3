from __future__ import annotations

from dataclasses import replace
import ast
import tempfile
from pathlib import Path

import pytest

from contracts import canonical_sha256
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
    build_negative_capability_evidence,
    commit_capability_experience_via_memory_coordinator,
    compile_capability_composition_plan,
    completion_evidence_from_decision,
    computed_experience_sha256,
    evaluate_attribution_integrity,
    evaluate_capability_experience_admission,
    exact_source_hashes,
    experience_has_valid_sha256,
    mark_capability_experience_source_change,
    parse_composition_proposal,
    posterior_success_milli,
    recall_capability_experiences,
    source_revision_family,
    wilson_lower_confidence_milli,
)

from tests.life_contract_support import LIFE_ID, event
from tests.test_capability_composition_p4 import H, _single_read_fixture


PRIVACY_HASH = canonical_sha256({"privacy_scope": "private"})
VERIFICATION_PLAN_HASH = "e" * 64
VERIFICATION_READINESS_HASH = "f" * 64


def _scoped_plan(
    ordinal: int,
    *,
    context_fingerprint: str | None = None,
):
    registry, candidates, context, document = _single_read_fixture()
    proposal = parse_composition_proposal(document, candidates)
    scoped_context = replace(
        context,
        request_id="req_" + f"{ordinal + 100:064x}",
        run_id="run_" + f"{ordinal + 200:064x}",
        context_fingerprint_sha256=(
            context_fingerprint
            or canonical_sha256({"independent-context": ordinal})
        ),
        context_sha256="0" * 64,
    ).with_computed_sha256()
    return compile_capability_composition_plan(
        proposal, candidates, scoped_context, registry
    )


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
    return CompletionDecision(
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
    ).with_computed_sha256()


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
    evidence = completion_evidence_from_decision(decision)
    fact_id = decision.supporting_fact_ids[0]
    value = AttributionTraceV1(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=principal_scope_hash,
        privacy_scope_hash=privacy_scope_hash,
        composition_plan_sha256=plan.plan_sha256,
        completion=evidence,
        active_verification_plan_sha256=(
            decision.verification_plan_sha256
        ),
        verification_record_refs=(
            (f"verification_record_{ordinal}",)
            if decision.verification_mode == "PLAN_BOUND"
            else ()
        ),
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
) -> CapabilityExperienceObservationV1:
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


def _admission(observation: CapabilityExperienceObservationV1):
    return evaluate_capability_experience_admission(
        observation,
        expected_principal_scope_hash=observation.principal_scope_hash,
        expected_privacy_scope_hash=observation.privacy_scope_hash,
        decided_at_ms=observation.observed_at_ms + 1,
    )


def test_exact_verified_lineage_passes_attribution() -> None:
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
    "change,reason",
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
    ),
)
def test_broken_lineage_cannot_pass_attribution(change: dict, reason: str) -> None:
    plan = _scoped_plan(2)
    attribution = evaluate_attribution_integrity(
        plan,
        _trace(plan, ordinal=2, **change),
        expected_principal_scope_hash=H,
        expected_privacy_scope_hash=PRIVACY_HASH,
        checked_at_ms=32,
    )
    assert attribution.state == "FAIL"
    assert reason in attribution.reason_codes


def test_incomplete_work_cannot_form_positive_experience() -> None:
    plan = _scoped_plan(3)
    trace = _trace(
        plan,
        completion=_completion(
            plan,
            outcome="FAILED",
            verification_ready=False,
            fact_id="fact_failed_3",
        ),
        active_verification_plan_complete=False,
        effect_fact_lineage_complete=False,
        ordinal=3,
    )
    observation = _observation(
        3,
        plan=plan,
        trace=trace,
        outcome="FAILURE",
        quality_milli=0,
        failure_category="RUNTIME_FAILURE",
        failure_reason_codes=("runtime.failed",),
    )
    admission = _admission(observation)
    assert admission.decision == "NEGATIVE_EVIDENCE"
    assert admission.positive_allowed is False
    assert admission.negative_allowed is True
    assert "runtime.failed" in admission.reason_codes


def test_failure_enters_only_negative_data_pool() -> None:
    plan = _scoped_plan(4)
    trace = _trace(
        plan,
        completion=_completion(
            plan,
            outcome="FAILED",
            verification_ready=False,
            fact_id="fact_failed_4",
        ),
        active_verification_plan_complete=False,
        ordinal=4,
    )
    observation = _observation(
        4,
        plan=plan,
        trace=trace,
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


def test_new_experience_is_probation_then_stable_after_5_by_4_gate() -> None:
    state = None
    last_observation = None
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
        last_observation = observation
        if ordinal == 10:
            assert state.experience.lifecycle == "PROBATION"
    assert state is not None and last_observation is not None
    assert state.experience.success_count == 5
    assert state.experience.failure_count == 0
    assert state.experience.independent_context_count == 4
    assert state.experience.lower_confidence_milli >= 700
    assert state.experience.lifecycle == "STABLE"
    assert state.has_valid_sha256()
    assert experience_has_valid_sha256(state.experience)

    duplicate, duplicate_negative = apply_capability_experience_observation(
        state, last_observation, _admission(last_observation)
    )
    assert duplicate == state
    assert duplicate_negative is None


def test_fixed_point_statistics_are_deterministic_and_float_free() -> None:
    assert posterior_success_milli(5, 0) == 857
    assert wilson_lower_confidence_milli(5, 0) == 752
    assert wilson_lower_confidence_milli(5, 0) == (
        wilson_lower_confidence_milli(5, 0)
    )
    source = (
        Path(__file__).resolve().parents[1]
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


def test_source_revision_and_family_changes_are_invalidated() -> None:
    observation = _observation(20)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    sources = (
        *observation.plan.method_source_refs,
        *observation.plan.action_source_refs,
    )
    revision_changed = (
        *sources[:-1],
        sources[-1].model_copy(update={"source_sha256": "6" * 64}),
    )
    assert assess_capability_experience_source_freshness(
        state, revision_changed
    ) == "REVALIDATION_REQUIRED"
    revalidation_state, revalidation_intent = (
        mark_capability_experience_source_change(
            state, revision_changed, requested_at_ms=100
        )
    )
    assert revalidation_state.experience.lifecycle == "REVALIDATION_REQUIRED"
    assert revalidation_intent is not None
    assert revalidation_intent.reason_code == (
        "capability_experience.source_revision_changed"
    )

    family_changed = (
        *sources[:-1],
        sources[-1].model_copy(update={"version": "omni-registry-v2"}),
    )
    assert assess_capability_experience_source_freshness(
        state, family_changed
    ) == "STALE"
    stale_state, stale_intent = mark_capability_experience_source_change(
        state, family_changed, requested_at_ms=100
    )
    assert stale_state.experience.lifecycle == "STALE"
    assert stale_intent is not None
    assert stale_intent.reason_code == (
        "capability_experience.source_family_changed"
    )


def test_recall_is_exactly_principal_privacy_source_and_lifecycle_scoped() -> None:
    observation = _observation(30)
    state, _ = apply_capability_experience_observation(
        None, observation, _admission(observation)
    )
    sources = (
        *observation.plan.method_source_refs,
        *observation.plan.action_source_refs,
    )
    base = dict(
        principal_scope_hash=state.principal_scope_hash,
        privacy_scope_hash=state.privacy_scope_hash,
        goal_class=state.experience.goal_class,
        environment_class=state.experience.environment_class,
        current_source_revision_family=source_revision_family(sources),
        current_exact_source_hashes=exact_source_hashes(sources),
        now_ms=100,
        include_probation=True,
        limit=8,
    )
    query = CapabilityExperienceRecallQueryV1(**base)
    assert len(recall_capability_experiences((state,), query)) == 1
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

    stale_experience = state.experience.model_copy(
        update={"lifecycle": "STALE", "experience_sha256": "0" * 64}
    )
    stale_experience = stale_experience.model_copy(
        update={
            "experience_sha256": computed_experience_sha256(
                stale_experience
            )
        }
    )
    stale_state = state.model_copy(
        update={
            "experience": stale_experience,
            "state_sha256": "0" * 64,
        }
    ).with_computed_sha256()
    assert recall_capability_experiences((stale_state,), query) == ()


def test_memory_intent_and_existing_coordinator_preserve_data_authority() -> None:
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
                40,
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
                created_at_ms=2_000,
            )
            assert intent.layer == "L3_EXPERIENCE"
            assert intent.semantic_domain == "CAPABILITY_KNOWLEDGE"
            assert intent.context_section == "DATA"
            assert intent.instruction_authority is False
            assert intent.world_candidate_eligible is False
            assert intent.may_authorize is False
            assert intent.may_execute is False
            assert intent.may_write_store is False
            assert intent.coordinator_required is True

            assertion, derivation, stored = (
                commit_capability_experience_via_memory_coordinator(
                    coordinator,
                    state,
                    intent,
                    (parent,),
                    plaintext,
                )
            )
            assert stored is True
            assert derivation.layer == "L3_EXPERIENCE"
            assert derivation.semantic_domain == "CAPABILITY_KNOWLEDGE"
            assert derivation.world_candidate_eligible is False
            assert classify_instruction_authority(
                derivation, assertion, plaintext.decode("utf-8")
            ) == "DATA"


def test_p5_modules_have_no_direct_store_or_execution_writer() -> None:
    root = Path(__file__).resolve().parents[1]
    package = root / "src" / "world_understanding" / "capability_composition"
    paths = (
        package / "capability_experience_api.py",
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
