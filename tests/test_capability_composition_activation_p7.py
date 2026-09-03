from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from contracts import canonical_sha256
from world_understanding.capability_composition import (
    compile_capability_composition_plan,
    computed_plan_sha256,
    parse_composition_proposal,
    validate_capability_composition_plan,
)
from total_gateway.capability_composition_activation import (
    CapabilityCompositionActivationAuthority,
    CapabilityCompositionActivationError,
    activation_contract_has_valid_sha256,
)

from tests.test_capability_composition_p4 import (
    H,
    _context,
    _proposal_document,
    _single_read_fixture,
    _worlds,
)


class ExistingGatewayPortProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    def authorize_and_execute_composition_step(
        self, *, step_authorization, arguments
    ):
        self.calls.append((step_authorization, dict(arguments)))
        return {
            "routed_through_existing_gateway": True,
            "step_id": step_authorization.step_id,
            "action_id": step_authorization.action_id,
        }


def _proved_valid_activation_fixture():
    registry, candidates, context, document = _single_read_fixture()
    proposal = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    validation = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert validation.result == "PROVED_VALID"
    authority = CapabilityCompositionActivationAuthority(registry)
    activation = authority.activate(
        plan,
        validation,
        current_request_id=plan.request_id,
        current_run_id=plan.run_id,
        current_generation=plan.generation,
        current_principal_scope_hash=plan.principal_scope_hash,
        current_world_state_ref=plan.world_state_ref,
        current_world_state_sha256=plan.world_state_sha256,
        issued_at_ms=12,
        expires_at_ms=1_012,
    )
    return authority, registry, candidates, context, proposal, plan, validation, activation


def test_proved_valid_plan_freezes_exact_activation_and_step() -> None:
    authority, registry, _candidates, _context_value, _proposal, plan, validation, activation = (
        _proved_valid_activation_fixture()
    )
    assert activation.has_valid_sha256()
    assert activation_contract_has_valid_sha256(
        activation.activation_contract
    )
    assert activation.plan_sha256 == plan.plan_sha256
    assert activation.validation_sha256 == validation.validation_sha256
    assert activation.action_registry_sha256 == registry.registry_sha256
    assert activation.capability_manifest_sha256 == (
        registry.source_manifest_sha256
    )
    assert activation.world_state_sha256 == plan.world_state_sha256
    assert activation.allowed_action_ids == plan.permission_requirements
    assert activation.may_execute is False
    assert activation.model_generated is False

    arguments = {"path": "workspace/readme.md"}
    authorization = authority.authorize_step(
        activation,
        plan,
        validation,
        step_id=plan.steps[0].step_id,
        completed_step_ids=(),
        arguments_sha256=canonical_sha256(arguments),
        issued_at_ms=13,
        expires_at_ms=100,
    )
    assert authorization.has_valid_sha256()
    assert authorization.action_id == plan.steps[0].action_id
    assert authorization.action_version == plan.steps[0].action_version
    assert authorization.requires_existing_policy_ticket_grant is True
    assert authorization.may_execute is False
    assert authorization.model_generated is False

    port = ExistingGatewayPortProbe()
    result = authority.dispatch_via_existing_gateway(
        port, authorization, arguments=arguments
    )
    assert result["routed_through_existing_gateway"] is True
    assert len(port.calls) == 1


def test_activation_rejects_scope_generation_world_and_manifest_drift() -> None:
    authority, _registry, _candidates, _context_value, _proposal, plan, validation, _activation = (
        _proved_valid_activation_fixture()
    )
    common = dict(
        current_request_id=plan.request_id,
        current_run_id=plan.run_id,
        current_generation=plan.generation,
        current_principal_scope_hash=plan.principal_scope_hash,
        current_world_state_ref=plan.world_state_ref,
        current_world_state_sha256=plan.world_state_sha256,
        issued_at_ms=12,
        expires_at_ms=1_012,
    )
    for field, value in (
        ("current_request_id", "req_" + "1" * 64),
        ("current_run_id", "run_" + "2" * 64),
        ("current_generation", plan.generation + 1),
        ("current_principal_scope_hash", "3" * 64),
        ("current_world_state_ref", "world.other"),
        ("current_world_state_sha256", "4" * 64),
    ):
        changed = {**common, field: value}
        with pytest.raises(
            CapabilityCompositionActivationError,
            match="scope_or_world_drift",
        ):
            authority.activate(plan, validation, **changed)

    drifted_plan = plan.model_copy(
        update={"capability_manifest_sha256": "5" * 64}
    )
    drifted_plan = drifted_plan.model_copy(
        update={"plan_sha256": computed_plan_sha256(drifted_plan)}
    )
    with pytest.raises(
        CapabilityCompositionActivationError,
        match="capability_manifest_drift",
    ):
        authority.activate(drifted_plan, validation, **common)


def test_activation_rejects_non_activatable_validation() -> None:
    authority, _registry, _candidates, _context_value, _proposal, plan, validation, _activation = (
        _proved_valid_activation_fixture()
    )
    rejected = validation.model_copy(
        update={
            "result": "UNKNOWN",
            "unknown_disposition": "REJECT",
            "mandatory_verification": False,
            "validation_sha256": "0" * 64,
        }
    )
    from world_understanding.capability_composition import computed_validation_sha256

    rejected = rejected.model_copy(
        update={
            "validation_sha256": computed_validation_sha256(rejected)
        }
    )
    with pytest.raises(
        CapabilityCompositionActivationError,
        match="validation_not_activatable",
    ):
        authority.activate(
            plan,
            rejected,
            current_request_id=plan.request_id,
            current_run_id=plan.run_id,
            current_generation=plan.generation,
            current_principal_scope_hash=plan.principal_scope_hash,
            current_world_state_ref=plan.world_state_ref,
            current_world_state_sha256=plan.world_state_sha256,
            issued_at_ms=12,
            expires_at_ms=1_012,
        )


def test_provisional_unknown_requires_mandatory_verification() -> None:
    registry, candidates, context, document = _single_read_fixture(
        idempotency="UNKNOWN",
        determinism="NONDETERMINISTIC",
    )
    proposal = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    validation = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        registry,
        available_verifiers=frozenset({"verifier:artifact"}),
        validated_at_ms=11,
    )
    assert validation.result == "UNKNOWN"
    assert validation.unknown_disposition == "PROVISIONAL_ALLOW"
    assert validation.mandatory_verification is True
    activation = CapabilityCompositionActivationAuthority(registry).activate(
        plan,
        validation,
        current_request_id=plan.request_id,
        current_run_id=plan.run_id,
        current_generation=plan.generation,
        current_principal_scope_hash=plan.principal_scope_hash,
        current_world_state_ref=plan.world_state_ref,
        current_world_state_sha256=plan.world_state_sha256,
        issued_at_ms=12,
        expires_at_ms=1_012,
    )
    assert activation.mandatory_verification is True


def test_a5_composition_cannot_activate() -> None:
    specs = (
        {
            "action_id": "credential.read",
            "risk": "A1",
            "effect": "read",
            "side_effects": ("read",),
            "resource_scope": ("credential",),
            "produces": ("type:credential",),
            "read_set": ("resource:credential",),
        },
        {
            "action_id": "http.send",
            "risk": "A2",
            "effect": "write",
            "side_effects": ("external_write", "read"),
            "resource_scope": ("network",),
            "consumes": ("type:credential",),
            "produces": ("type:delivery-result",),
            "write_set": ("resource:external-endpoint",),
        },
    )
    registry, tool_world, method_world = _worlds(specs)
    from world_understanding.capability_composition import build_candidate_snapshot

    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("credential.read", "http.send"),
    )
    proposal = parse_composition_proposal(
        _proposal_document(
            goal_ref="goal.p7-a5",
            methods=("M01",),
            actions=("A01", "A02"),
            steps=(
                ("step.01", "A01", ()),
                ("step.02", "A02", ("step.01",)),
            ),
        ),
        candidates,
    )
    context = _context(goal_ref="goal.p7-a5")
    plan = compile_capability_composition_plan(
        proposal, candidates, context, registry
    )
    assert plan.composition_risk == "A5"
    # Even a forged structurally valid-looking validation result cannot bypass
    # the independent A5 gate.
    _authority, _r, _c, _ctx, _p, safe_plan, safe_validation, _a = (
        _proved_valid_activation_fixture()
    )
    forged = safe_validation.model_copy(
        update={
            "plan_id": plan.plan_id,
            "plan_sha256": plan.plan_sha256,
            "validation_sha256": "0" * 64,
        }
    )
    from world_understanding.capability_composition import computed_validation_sha256

    forged = forged.model_copy(
        update={"validation_sha256": computed_validation_sha256(forged)}
    )
    with pytest.raises(
        CapabilityCompositionActivationError, match="a5_forbidden"
    ):
        CapabilityCompositionActivationAuthority(registry).activate(
            plan,
            forged,
            current_request_id=plan.request_id,
            current_run_id=plan.run_id,
            current_generation=plan.generation,
            current_principal_scope_hash=plan.principal_scope_hash,
            current_world_state_ref=plan.world_state_ref,
            current_world_state_sha256=plan.world_state_sha256,
            issued_at_ms=12,
            expires_at_ms=1_012,
        )


def test_step_dependencies_permissions_expiry_and_arguments_fail_closed() -> None:
    authority, _registry, _candidates, _context_value, _proposal, plan, validation, activation = (
        _proved_valid_activation_fixture()
    )
    with pytest.raises(
        CapabilityCompositionActivationError, match="unknown_step"
    ):
        authority.authorize_step(
            activation,
            plan,
            validation,
            step_id="step.99",
            completed_step_ids=(),
            arguments_sha256=canonical_sha256({}),
            issued_at_ms=13,
            expires_at_ms=100,
        )
    with pytest.raises(
        CapabilityCompositionActivationError,
        match="activation_expired_or_not_yet_valid",
    ):
        authority.authorize_step(
            activation,
            plan,
            validation,
            step_id=plan.steps[0].step_id,
            completed_step_ids=(),
            arguments_sha256=canonical_sha256({}),
            issued_at_ms=activation.expires_at_ms + 1,
            expires_at_ms=activation.expires_at_ms + 2,
        )

    arguments = {"path": "workspace/a.txt"}
    authorization = authority.authorize_step(
        activation,
        plan,
        validation,
        step_id=plan.steps[0].step_id,
        completed_step_ids=(),
        arguments_sha256=canonical_sha256(arguments),
        issued_at_ms=13,
        expires_at_ms=100,
    )
    with pytest.raises(
        CapabilityCompositionActivationError, match="arguments_mismatch"
    ):
        authority.dispatch_via_existing_gateway(
            ExistingGatewayPortProbe(),
            authorization,
            arguments={"path": "workspace/other.txt"},
        )


def test_p7_module_contains_no_runtime_ticket_grant_or_completion_implementation() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "total_gateway"
        / "capability_composition_activation.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "class ExecutionTicket",
        "class CapabilityGrant",
        "class CompletionDecision",
        "class BodyRuntime",
        "subprocess",
        "sqlite3.connect",
        "omni_body.execute",
        "runtime.execute",
    )
    for token in forbidden:
        assert token not in source
    assert "requires_existing_policy_ticket_grant" in source
    assert "authorize_and_execute_composition_step" in source
