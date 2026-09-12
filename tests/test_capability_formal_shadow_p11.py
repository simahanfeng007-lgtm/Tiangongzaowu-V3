from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from total_gateway.skill_selection import (
    SkillCatalog,
    SkillDefinition,
    SkillSelectionService,
)
from world_understanding.capability_composition import (
    REQUIRED_FAULT_KINDS,
    P11DynamicPathObservationV1,
    P11FaultCaseV1,
    P11FormalShadowError,
    P11ModelProfileV1,
    P11SinglePathExecutionTraceV1,
    P11StaticPathObservationV1,
    P11TaskCaseV1,
    build_p11_formal_shadow_report,
    observe_dynamic_composition_path,
    observe_static_skill_path,
)

from tests.test_composition_activation_shadow_p7a import (
    _propose,
    _validated_read_plan,
)
from tests.test_skill_selection import manifest


H = "a" * 64
H2 = "b" * 64
REQ = "req_" + "c" * 64
RUN = "run_" + "d" * 64


def _profile(profile_id: str, role: str) -> P11ModelProfileV1:
    value = P11ModelProfileV1(
        profile_id=profile_id,
        provider_id="recorded.protocol",
        model_id="recorded." + profile_id,
        model_revision="fixture.v1",
        role=role,  # type: ignore[arg-type]
        profile_sha256="0" * 64,
    )
    return value.with_computed_sha256()


PROFILES = tuple(
    sorted(
        (
            _profile("model.primary", "PRIMARY"),
            _profile("model.secondary-a", "SECONDARY_A"),
            _profile("model.secondary-b", "SECONDARY_B"),
            _profile("model.weak", "WEAK"),
        ),
        key=lambda item: item.profile_id,
    )
)
PROFILE_BY_ROLE = {item.role: item for item in PROFILES}


def _static(task_id: str) -> P11StaticPathObservationV1:
    value = P11StaticPathObservationV1(
        task_id=task_id,
        query_sha256=hashlib.sha256(("task:" + task_id).encode()).hexdigest(),
        selection_id="selection." + task_id,
        selection_sha256=hashlib.sha256(
            ("selection:" + task_id).encode()
        ).hexdigest(),
        skill_catalog_sha256=H,
        capability_manifest_sha256=H2,
        selected_skill_id="skill.legacy.fixture",
        planned_action_ids=("artifact.read",),
        context_tokens=2_400,
        proposed_only=True,
        authorizes=False,
        may_execute=False,
        observation_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _dynamic(
    task_id: str, profile: P11ModelProfileV1
) -> P11DynamicPathObservationV1:
    value = P11DynamicPathObservationV1(
        task_id=task_id,
        model=profile,
        goal_fingerprint_sha256=hashlib.sha256(
            ("goal:" + task_id).encode()
        ).hexdigest(),
        context_tokens=620 if profile.role != "WEAK" else 680,
        parse_succeeded=True,
        repair_attempted=profile.role == "WEAK",
        repaired=profile.role == "WEAK",
        plan_succeeded=True,
        activation_ready=True,
        error_code=None,
        composition_plan_sha256=hashlib.sha256(
            ("plan:" + task_id + profile.profile_id).encode()
        ).hexdigest(),
        validation_sha256=hashlib.sha256(
            ("validation:" + task_id + profile.profile_id).encode()
        ).hexdigest(),
        shadow_proposal_sha256=hashlib.sha256(
            ("shadow:" + task_id + profile.profile_id).encode()
        ).hexdigest(),
        validation_result="PROVED_VALID",
        composition_risk="A0",
        planned_action_ids=("artifact.read",),
        static_comparison_action_ids=("artifact.read",),
        added_vs_static=(),
        removed_vs_static=(),
        source_manifest_sha256=H,
        proposed_only=True,
        authorizes=False,
        may_execute=False,
        observation_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _execution(
    task_id: str, active_path: str
) -> P11SinglePathExecutionTraceV1:
    dynamic = active_path == "DYNAMIC"
    value = P11SinglePathExecutionTraceV1(
        task_id=task_id,
        active_path=active_path,  # type: ignore[arg-type]
        active_model_profile_id=(
            PROFILE_BY_ROLE["PRIMARY"].profile_id if dynamic else None
        ),
        static_execution_count=0 if dynamic else 1,
        dynamic_execution_count=1 if dynamic else 0,
        authorized_action_ids=("artifact.read",),
        executed_action_ids=("artifact.read",),
        effective_risk="A0",
        pinned_source_manifest_sha256=H,
        observed_source_manifest_sha256=H,
        experience_lifecycles_used=("STABLE",),
        verification_state="PASS",
        completion_state="ACCEPTED",
        effect_refs=("effect." + task_id,),
        terminal_fact_sha256s=(
            hashlib.sha256(("fact:" + task_id).encode()).hexdigest(),
        ),
        verification_record_refs=("verification." + task_id,),
        completion_decision_sha256=hashlib.sha256(
            ("completion:" + task_id).encode()
        ).hexdigest(),
        restart_identity_before_sha256=H2,
        restart_identity_after_sha256=H2,
        trace_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _task(task_id: str, cohort: str, ordinal: int) -> P11TaskCaseV1:
    profiles = (
        PROFILES
        if cohort == "CORE"
        else tuple(
            item for item in PROFILES if item.role in {"PRIMARY", "WEAK"}
        )
    )
    active_path = "STATIC" if ordinal % 2 else "DYNAMIC"
    value = P11TaskCaseV1(
        task_id=task_id,
        cohort=cohort,  # type: ignore[arg-type]
        task_input_sha256=hashlib.sha256(
            ("task:" + task_id).encode()
        ).hexdigest(),
        goal_fingerprint_sha256=hashlib.sha256(
            ("goal:" + task_id).encode()
        ).hexdigest(),
        acceptance_profile_sha256=hashlib.sha256(
            ("acceptance:" + task_id).encode()
        ).hexdigest(),
        static_path=_static(task_id),
        dynamic_paths=tuple(_dynamic(task_id, item) for item in profiles),
        execution=_execution(task_id, active_path),
        case_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def _cases() -> tuple[P11TaskCaseV1, ...]:
    values = [
        _task(f"core.{ordinal:03d}", "CORE", ordinal)
        for ordinal in range(1, 81)
    ]
    values.extend(
        _task(f"long-tail.{ordinal:03d}", "LONG_TAIL", ordinal)
        for ordinal in range(1, 121)
    )
    return tuple(sorted(values, key=lambda item: item.task_id))


def _faults() -> tuple[P11FaultCaseV1, ...]:
    kinds = tuple(sorted(REQUIRED_FAULT_KINDS))
    values = []
    for ordinal in range(1, 41):
        kind = kinds[(ordinal - 1) % len(kinds)]
        task_id = f"core.{((ordinal - 1) % 80) + 1:03d}"
        value = P11FaultCaseV1(
            fault_case_id=f"fault.{ordinal:03d}",
            task_id=task_id,
            fault_kind=kind,  # type: ignore[arg-type]
            model_results=tuple(
                sorted(
                    (
                        (
                            PROFILE_BY_ROLE["PRIMARY"].profile_id,
                            "shadow.fault.contained",
                        ),
                        (
                            PROFILE_BY_ROLE["WEAK"].profile_id,
                            "shadow.fault.contained",
                        ),
                    )
                )
            ),
            active_path_before="STATIC",
            active_path_after="STATIC",
            authority_identity_before_sha256=H2,
            authority_identity_after_sha256=H2,
            containment_evidence_sha256s=(
                hashlib.sha256(("fault:" + str(ordinal)).encode()).hexdigest(),
            ),
            shadow_authorization_count=0,
            shadow_execution_count=0,
            case_sha256="0" * 64,
        )
        values.append(value.with_computed_sha256())
    return tuple(values)


def test_recorded_200_task_matrix_proves_formal_contract_not_cutover() -> None:
    report = build_p11_formal_shadow_report(
        _cases(), _faults(), evidence_mode="RECORDED_FIXTURE"
    )

    assert report.has_valid_sha256()
    assert report.task_count == 200
    assert report.core_task_count == 80
    assert report.long_tail_task_count == 120
    assert report.model_observation_count == 560
    assert report.fault_case_count == 40
    assert report.exact_plan_match_count == 560
    assert report.action_addition_count == 0
    assert report.action_removal_count == 0
    assert report.static_active_count == 100
    assert report.dynamic_active_count == 100
    assert report.model_matrix_complete is True
    assert report.fault_matrix_complete is True
    assert report.formal_gate_passed is True
    assert report.cutover_gate_passed is False
    assert report.cutover_blockers == (
        "p11.independent_production_review.required",
        "p11.production_shadow_trace.required",
    )
    assert report.static_verified_success_milli == 1000
    assert report.dynamic_verified_success_milli == 1000
    assert report.false_completion_count == 0
    assert report.a5_bypass_count == 0
    assert report.unauthorized_action_count == 0
    assert report.source_drift_misuse_count == 0
    assert report.stale_experience_reuse_count == 0
    assert report.restart_identity_drift_count == 0
    assert report.shadow_authority_violation_count == 0
    assert report.fault_containment_failure_count == 0
    assert report.final_parse_failure_milli == 0
    assert report.weak_model_plan_success_milli == 1000
    assert report.weak_model_gap_milli == 0
    assert report.median_dynamic_context_tokens < (
        report.median_static_context_tokens
    )


def test_only_one_path_can_have_an_execution_trace() -> None:
    with pytest.raises(P11FormalShadowError, match="not_single_path"):
        replace(
            _execution("core.001", "STATIC"),
            dynamic_execution_count=1,
        )


def test_shadow_authority_or_fault_escape_fails_the_formal_gate() -> None:
    cases = list(_cases())
    first = cases[0]
    dynamic = list(first.dynamic_paths)
    dynamic[0] = replace(dynamic[0], authorizes=True, observation_sha256="0" * 64)
    dynamic[0] = dynamic[0].with_computed_sha256()
    changed = replace(
        first,
        dynamic_paths=tuple(dynamic),
        case_sha256="0" * 64,
    ).with_computed_sha256()
    cases[0] = changed

    report = build_p11_formal_shadow_report(
        tuple(cases), _faults(), evidence_mode="RECORDED_FIXTURE"
    )
    assert report.formal_gate_passed is False
    assert report.cutover_gate_passed is False
    assert report.shadow_authority_violation_count == 1
    assert "p11.shadow_authority_violation.nonzero" in report.cutover_blockers


@pytest.mark.parametrize(
    ("updates", "blocker"),
    (
        (
            {"verification_state": "FAIL"},
            "p11.false_completion.nonzero",
        ),
        ({"effective_risk": "A5"}, "p11.a5_bypass.nonzero"),
        (
            {"executed_action_ids": ("artifact.read", "invented.write")},
            "p11.unauthorized_action.nonzero",
        ),
        (
            {"observed_source_manifest_sha256": H2},
            "p11.source_drift_misuse.nonzero",
        ),
        (
            {"experience_lifecycles_used": ("STALE",)},
            "p11.stale_experience_reuse.nonzero",
        ),
        (
            {"restart_identity_after_sha256": H},
            "p11.restart_identity_drift.nonzero",
        ),
    ),
)
def test_security_metric_violation_blocks_formal_gate(
    updates: dict[str, object], blocker: str
) -> None:
    cases = list(_cases())
    first = cases[0]
    trace = replace(
        first.execution,
        **updates,
        trace_sha256="0" * 64,
    ).with_computed_sha256()
    cases[0] = replace(
        first, execution=trace, case_sha256="0" * 64
    ).with_computed_sha256()

    report = build_p11_formal_shadow_report(
        tuple(cases), _faults(), evidence_mode="RECORDED_FIXTURE"
    )
    assert report.formal_gate_passed is False
    assert blocker in report.cutover_blockers


def test_fault_path_or_authority_drift_is_derived_not_self_attested() -> None:
    faults = list(_faults())
    first = faults[0]
    faults[0] = replace(
        first,
        active_path_after="DYNAMIC",
        authority_identity_after_sha256=H,
        case_sha256="0" * 64,
    ).with_computed_sha256()
    assert faults[0].active_path_preserved is False
    assert faults[0].current_authority_preserved is False
    assert faults[0].contained is False

    report = build_p11_formal_shadow_report(
        _cases(), tuple(faults), evidence_mode="RECORDED_FIXTURE"
    )
    assert report.formal_gate_passed is False
    assert report.fault_containment_failure_count == 1
    assert "p11.fault_containment.failed" in report.cutover_blockers


def test_incomplete_model_or_fault_matrix_cannot_pass() -> None:
    missing_task = build_p11_formal_shadow_report(
        _cases()[:-1], _faults(), evidence_mode="RECORDED_FIXTURE"
    )
    assert missing_task.model_matrix_complete is False
    assert missing_task.formal_gate_passed is False
    assert "p11.model_matrix.incomplete" in missing_task.cutover_blockers

    missing_fault = build_p11_formal_shadow_report(
        _cases(), _faults()[:-1], evidence_mode="RECORDED_FIXTURE"
    )
    assert missing_fault.fault_matrix_complete is False
    assert missing_fault.formal_gate_passed is False
    assert "p11.fault_matrix.incomplete" in missing_fault.cutover_blockers


def test_static_dynamic_differential_must_bind_the_same_task_static_plan() -> None:
    first = _cases()[0]
    dynamic = list(first.dynamic_paths)
    dynamic[0] = replace(
        dynamic[0],
        static_comparison_action_ids=("different.read",),
        added_vs_static=("artifact.read",),
        removed_vs_static=("different.read",),
        observation_sha256="0" * 64,
    ).with_computed_sha256()
    with pytest.raises(
        P11FormalShadowError, match="static_dynamic_comparison_invalid"
    ):
        replace(
            first,
            dynamic_paths=tuple(dynamic),
            case_sha256="0" * 64,
        )


def test_matrix_requires_distinct_task_and_goal_identities() -> None:
    cases = list(_cases())
    first, second = cases[:2]
    rebound_static = replace(
        second.static_path,
        query_sha256=first.task_input_sha256,
        observation_sha256="0" * 64,
    ).with_computed_sha256()
    rebound_dynamic = tuple(
        replace(
            item,
            goal_fingerprint_sha256=first.goal_fingerprint_sha256,
            observation_sha256="0" * 64,
        ).with_computed_sha256()
        for item in second.dynamic_paths
    )
    cases[1] = replace(
        second,
        task_input_sha256=first.task_input_sha256,
        goal_fingerprint_sha256=first.goal_fingerprint_sha256,
        static_path=rebound_static,
        dynamic_paths=rebound_dynamic,
        case_sha256="0" * 64,
    ).with_computed_sha256()
    report = build_p11_formal_shadow_report(
        tuple(cases), _faults(), evidence_mode="RECORDED_FIXTURE"
    )
    assert report.model_matrix_complete is False
    assert "p11.model_matrix.incomplete" in report.cutover_blockers


def test_recorded_profiles_cannot_be_relabelled_as_production_evidence() -> None:
    with pytest.raises(
        P11FormalShadowError, match="recorded_identity_forbidden"
    ):
        build_p11_formal_shadow_report(
            _cases(),
            _faults(),
            evidence_mode="PRODUCTION_SHADOW_TRACE",
        )


def test_raw_report_cannot_self_approve_cutover() -> None:
    report = build_p11_formal_shadow_report(
        _cases(), _faults(), evidence_mode="RECORDED_FIXTURE"
    )
    with pytest.raises(P11FormalShadowError, match="self_approval_forbidden"):
        replace(report, cutover_gate_passed=True)


def test_existing_static_and_p7a_dynamic_outputs_feed_the_p11_observers() -> None:
    content = "# Legacy static fixture\nRead the artifact and verify it.\n"
    definition = SkillDefinition(
        skill_id="skill.legacy.read",
        version="v1",
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_ref="source.legacy.read",
        title="Artifact read",
        summary="Read artifact",
        category="document",
        keywords=("artifact", "read"),
        task_intents=("artifact read",),
        required_actions=("artifact.read",),
        content=content,
    )
    selection = SkillSelectionService(SkillCatalog((definition,))).system_recommend(
        "artifact read",
        request_id=REQ,
        run_id=RUN,
        generation=0,
        capability_manifest=manifest("artifact.read"),
        decided_at_ms=20,
    )
    static = observe_static_skill_path(
        task_id="integration.001",
        selection=selection,
        context_tokens=2_400,
    )
    action_registry, plan, validation, verifier_registry, bindings = (
        _validated_read_plan()
    )
    shadow = _propose(
        action_registry,
        plan,
        validation,
        verifier_registry,
        bindings,
        legacy_allowed_action_ids=static.planned_action_ids,
    )
    dynamic = observe_dynamic_composition_path(
        task_id="integration.001",
        model=PROFILE_BY_ROLE["PRIMARY"],
        context_tokens=620,
        parse_succeeded=True,
        plan=plan,
        validation=validation,
        shadow=shadow,
    )

    assert static.has_valid_sha256()
    assert static.proposed_only and not static.authorizes and not static.may_execute
    assert static.planned_action_ids == ("artifact.read",)
    assert dynamic.has_valid_sha256()
    assert dynamic.activation_ready
    assert dynamic.shadow_proposal_sha256 == shadow.proposal_sha256
    assert dynamic.planned_action_ids == plan.permission_requirements
    assert dynamic.static_comparison_action_ids == static.planned_action_ids
    assert dynamic.added_vs_static == ()
    assert dynamic.removed_vs_static == ()
    assert dynamic.proposed_only and not dynamic.authorizes and not dynamic.may_execute


def test_p11_layer_has_no_gateway_runtime_or_store_imports() -> None:
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src"
        / "world_understanding"
        / "capability_composition"
        / "formal_shadow.py"
    ).read_text(encoding="utf-8")
    assert "GatewayStateStore" not in source
    assert "ExecutionTicket" not in source
    assert "OmniCapabilityGrant" not in source
    assert "BodyRuntime" not in source
    assert "CompletionGate" not in source
    assert "put_verification_plan" not in source
