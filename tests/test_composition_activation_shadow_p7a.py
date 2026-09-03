from __future__ import annotations

from pathlib import Path

import pytest

from contracts import (
    AcceptancePredicate,
    CompositionValidationResultV1,
)
from total_gateway.composition_activation_shadow import (
    CompositionShadowActivationError,
    activation_has_valid_sha256,
    build_system_verification_binding,
    propose_shadow_composition_activation,
)
from total_gateway.verification_registry import VerifierRegistry
from world_understanding.capability_composition import (
    build_candidate_snapshot,
    compile_capability_composition_plan,
    computed_plan_sha256,
    computed_validation_sha256,
    parse_composition_proposal,
    validate_capability_composition_plan,
)

from tests.test_capability_composition_p4 import (
    _context,
    _proposal_document,
    _single_read_fixture,
    _worlds,
)


def _validated_read_plan():
    action_registry, candidates, context, document = _single_read_fixture()
    proposal = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        proposal, candidates, context, action_registry
    )
    validation = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        action_registry,
        available_verifiers=frozenset(plan.verification_intents),
        validated_at_ms=11,
    )
    assert validation.result == "PROVED_VALID"
    verification_registry = VerifierRegistry.with_defaults().snapshot(
        captured_at_ms=12
    )
    predicate = AcceptancePredicate.create(
        predicate_type="artifact.nonempty",
        subject_kind="artifact",
        params={},
    )
    binding = build_system_verification_binding(
        intent_ref=plan.verification_intents[0],
        predicate=predicate,
        subject_identity="object:artifact-read-output",
        evaluation_phase="POST_EXECUTION",
        registry_snapshot=verification_registry,
    )
    return action_registry, plan, validation, verification_registry, (binding,)


def _validation_for(plan, *, result="PROVED_VALID"):
    value = CompositionValidationResultV1(
        plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256,
        result=result,
        unknown_disposition=(
            "PROVISIONAL_ALLOW" if result == "UNKNOWN" else "NOT_APPLICABLE"
        ),
        findings=(),
        mandatory_verification=result == "UNKNOWN",
        validated_at_ms=max(11, plan.created_at_ms),
        validation_sha256="0" * 64,
    )
    return value.model_copy(
        update={"validation_sha256": computed_validation_sha256(value)}
    )


def test_shadow_adapter_proposes_exact_activation_and_p19_plan() -> None:
    action_registry, plan, validation, verifier_registry, bindings = (
        _validated_read_plan()
    )
    result = propose_shadow_composition_activation(
        plan,
        validation,
        action_registry,
        verifier_registry,
        bindings,
        issued_at_ms=20,
        expires_at_ms=60,
        legacy_allowed_action_ids=(),
    )
    activation = result.activation_contract
    verification_plan = result.verification_plan
    assert result.has_valid_sha256()
    assert activation_has_valid_sha256(activation)
    assert verification_plan.has_valid_identity()
    assert activation.composition_plan_id == plan.plan_id
    assert activation.composition_plan_sha256 == plan.plan_sha256
    assert activation.request_id == plan.request_id
    assert activation.run_id == plan.run_id
    assert activation.generation == plan.generation
    assert activation.principal_scope_hash == plan.principal_scope_hash
    assert activation.world_state_sha256 == plan.world_state_sha256
    assert activation.source_manifest_sha256 == plan.source_manifest_sha256
    assert (
        activation.capability_manifest_sha256
        == action_registry.source_manifest_sha256
    )
    assert activation.allowed_action_ids == plan.permission_requirements
    assert activation.allowed_action_versions == ("omni-registry-v1",)
    assert activation.verification_plan_ref == (
        verification_plan.verification_plan_id
    )
    assert verification_plan.registry_snapshot_sha256 == (
        verifier_registry.snapshot_sha256
    )
    assert verification_plan.entries[0].predicate.predicate_type == (
        "artifact.nonempty"
    )
    assert result.proposed_only is True
    assert result.persistence_allowed is False
    assert result.authorizes is False
    assert result.confirms is False
    assert result.changes_risk is False
    assert result.may_execute is False
    assert result.differential_trace.persisted is False
    assert result.differential_trace.authorizes is False
    assert result.differential_trace.may_execute is False
    assert result.differential_trace.registry_subset is True
    assert result.differential_trace.exact_action_set is True
    assert result.differential_trace.verification_bindings_complete is True
    assert result.differential_trace.limited_production_eligible is True


def test_shadow_adapter_is_deterministic_and_records_legacy_difference() -> None:
    action_registry, plan, validation, verifier_registry, bindings = (
        _validated_read_plan()
    )
    kwargs = dict(
        issued_at_ms=20,
        expires_at_ms=60,
        legacy_allowed_action_ids=("legacy.only",),
    )
    first = propose_shadow_composition_activation(
        plan,
        validation,
        action_registry,
        verifier_registry,
        bindings,
        **kwargs,
    )
    second = propose_shadow_composition_activation(
        plan,
        validation,
        action_registry,
        verifier_registry,
        bindings,
        **kwargs,
    )
    assert first == second
    assert first.proposal_sha256 == second.proposal_sha256
    trace = first.differential_trace
    assert trace.added_vs_legacy == plan.permission_requirements
    assert trace.removed_vs_legacy == ("legacy.only",)


def test_model_cannot_name_verifier_and_resolution_requires_one_exact_match() -> None:
    _action_registry, plan, _validation, registry, _bindings = (
        _validated_read_plan()
    )
    predicate = AcceptancePredicate.create(
        predicate_type="artifact.nonempty",
        subject_kind="artifact",
        params={},
    )
    without_artifact = VerifierRegistry(
        tuple(
            descriptor
            for descriptor in registry.verifiers
            if descriptor.verifier_id != "verifier.artifact_content"
        )
    ).snapshot(captured_at_ms=13)
    with pytest.raises(
        CompositionShadowActivationError,
        match="verifier_resolution.not_unique",
    ):
        build_system_verification_binding(
            intent_ref=plan.verification_intents[0],
            predicate=predicate,
            subject_identity="object:artifact-read-output",
            evaluation_phase="POST_EXECUTION",
            registry_snapshot=without_artifact,
        )


def test_missing_or_drifted_verification_binding_fails_closed() -> None:
    action_registry, plan, validation, verifier_registry, bindings = (
        _validated_read_plan()
    )
    with pytest.raises(
        CompositionShadowActivationError,
        match="verification_bindings.incomplete",
    ):
        propose_shadow_composition_activation(
            plan,
            validation,
            action_registry,
            verifier_registry,
            (),
            issued_at_ms=20,
            expires_at_ms=60,
        )

    drifted = bindings[0].model_copy(
        update={"registry_snapshot_sha256": "a" * 64}
    )
    with pytest.raises(
        CompositionShadowActivationError,
        match="verification_binding.invalid",
    ):
        propose_shadow_composition_activation(
            plan,
            validation,
            action_registry,
            verifier_registry,
            (drifted,),
            issued_at_ms=20,
            expires_at_ms=60,
        )


def test_permission_expansion_and_source_drift_fail_closed() -> None:
    action_registry, plan, _validation, verifier_registry, bindings = (
        _validated_read_plan()
    )
    expanded = plan.model_copy(
        update={
            "permission_requirements": (
                *plan.permission_requirements,
                "invented.action",
            ),
            "plan_sha256": "0" * 64,
        }
    )
    expanded = expanded.model_copy(
        update={"plan_sha256": computed_plan_sha256(expanded)}
    )
    with pytest.raises(
        CompositionShadowActivationError,
        match="plan.action_set_inconsistent",
    ):
        propose_shadow_composition_activation(
            expanded,
            _validation_for(expanded),
            action_registry,
            verifier_registry,
            bindings,
            issued_at_ms=20,
            expires_at_ms=60,
        )

    source = plan.action_source_refs[0].model_copy(
        update={"manifest_sha256": "b" * 64}
    )
    drifted = plan.model_copy(
        update={
            "action_source_refs": (source,),
            "plan_sha256": "0" * 64,
        }
    )
    drifted = drifted.model_copy(
        update={"plan_sha256": computed_plan_sha256(drifted)}
    )
    with pytest.raises(
        CompositionShadowActivationError,
        match="source_manifest.hash_invalid|version_or_source_mismatch",
    ):
        propose_shadow_composition_activation(
            drifted,
            _validation_for(drifted),
            action_registry,
            verifier_registry,
            bindings,
            issued_at_ms=20,
            expires_at_ms=60,
        )


def test_only_valid_or_provisional_unknown_validation_is_shadow_activatable() -> None:
    action_registry, plan, _validation, verifier_registry, bindings = (
        _validated_read_plan()
    )
    unknown = _validation_for(plan, result="UNKNOWN")
    proposed = propose_shadow_composition_activation(
        plan,
        unknown,
        action_registry,
        verifier_registry,
        bindings,
        issued_at_ms=20,
        expires_at_ms=60,
    )
    assert proposed.validation_mode == "PROVISIONAL_UNKNOWN"
    assert proposed.verification_plan.entries[0].required is True

    invalid = _validation_for(plan, result="PROVED_INVALID")
    with pytest.raises(
        CompositionShadowActivationError,
        match="validation.not_activatable",
    ):
        propose_shadow_composition_activation(
            plan,
            invalid,
            action_registry,
            verifier_registry,
            bindings,
            issued_at_ms=20,
            expires_at_ms=60,
        )


def test_a2_write_can_be_compared_in_shadow_but_is_not_limited_eligible() -> None:
    specs = (
        {
            "action_id": "artifact.write",
            "risk": "A2",
            "effect": "write",
            "side_effects": ("local_write", "read"),
            "write_set": ("resource:artifact",),
        },
    )
    action_registry, tool_world, method_world = _worlds(specs)
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.write",),
    )
    document = _proposal_document(
        goal_ref="goal.shadow-write",
        methods=("M01",),
        actions=("A01",),
        steps=(("step.01", "A01", ()),),
    )
    proposal = parse_composition_proposal(document, candidates)
    context = _context(goal_ref="goal.shadow-write")
    plan = compile_capability_composition_plan(
        proposal, candidates, context, action_registry
    )
    validation = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        action_registry,
        available_verifiers=frozenset(plan.verification_intents),
        validated_at_ms=11,
    )
    assert validation.result == "PROVED_VALID"
    verifier_registry = VerifierRegistry.with_defaults().snapshot(
        captured_at_ms=12
    )
    binding = build_system_verification_binding(
        intent_ref=plan.verification_intents[0],
        predicate=AcceptancePredicate.create(
            predicate_type="artifact.nonempty",
            subject_kind="artifact",
            params={},
        ),
        subject_identity="object:written-artifact",
        evaluation_phase="POST_EXECUTION",
        registry_snapshot=verifier_registry,
    )
    result = propose_shadow_composition_activation(
        plan,
        validation,
        action_registry,
        verifier_registry,
        (binding,),
        issued_at_ms=20,
        expires_at_ms=60,
    )
    assert result.proposed_only is True
    assert result.differential_trace.limited_production_eligible is False
    assert "limited.composition_risk_not_a0_a1" in (
        result.differential_trace.limited_rejection_codes
    )
    assert "limited.effect_not_read_verify" in (
        result.differential_trace.limited_rejection_codes
    )


def test_p7a_module_has_no_store_runtime_ticket_or_verifier_execution_path() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "total_gateway"
        / "composition_activation_shadow.py"
    )
    source = path.read_text(encoding="utf-8")
    forbidden = (
        "GatewayStateStore",
        "put_verification_plan",
        "ExecutionTicket(",
        "OmniCapabilityGrant(",
        "BodyRuntime",
        "VerificationPlanExecutor",
        "VerificationRecorder",
        ".execute(",
        ".dispatch(",
        "status=\"PASS\"",
        "status='PASS'",
    )
    for token in forbidden:
        assert token not in source, token
