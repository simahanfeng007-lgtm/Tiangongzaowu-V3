from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from contracts import canonical_sha256
from total_gateway.capability_composition_activation import (
    CapabilityCompositionActivationError,
)
from total_gateway.capability_composition_receipt import (
    CompositionStepExecutionReceiptV1,
    dispatch_and_verify_existing_gateway_step,
    validate_step_execution_receipt,
)

from tests.test_capability_composition_activation_p7 import (
    _proved_valid_activation_fixture,
)


class ReceiptPortProbe:
    def __init__(self, receipt: CompositionStepExecutionReceiptV1) -> None:
        self.receipt = receipt
        self.calls = 0

    def authorize_and_execute_composition_step(
        self, *, step_authorization, arguments
    ) -> CompositionStepExecutionReceiptV1:
        self.calls += 1
        return self.receipt


def _authorization_and_receipt():
    authority, registry, _candidates, _context, _proposal, plan, validation, activation = (
        _proved_valid_activation_fixture()
    )
    arguments = {"path": "workspace/p7.txt"}
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
    receipt = CompositionStepExecutionReceiptV1(
        request_id=authorization.request_id,
        run_id=authorization.run_id,
        generation=authorization.generation,
        principal_scope_hash=authorization.principal_scope_hash,
        activation_id=authorization.activation_id,
        activation_sha256=authorization.activation_sha256,
        plan_id=authorization.plan_id,
        plan_sha256=authorization.plan_sha256,
        validation_sha256=authorization.validation_sha256,
        step_id=authorization.step_id,
        step_authorization_sha256=authorization.authorization_sha256,
        action_id=authorization.action_id,
        action_version=authorization.action_version,
        action_registry_sha256=registry.registry_sha256,
        capability_manifest_sha256=authorization.capability_manifest_sha256,
        world_state_sha256=authorization.world_state_sha256,
        policy_decision_sha256="1" * 64,
        execution_ticket_sha256="2" * 64,
        capability_grant_sha256="3" * 64,
        effect_id="effect_p7_step_01",
        effect_sha256="4" * 64,
        fact_ids=("fact_p7_step_01",),
        fact_hashes=("5" * 64,),
        verification_plan_sha256="6" * 64,
        executed_at_ms=14,
        receipt_sha256="0" * 64,
    ).with_computed_sha256()
    return authority, authorization, arguments, receipt


def test_existing_route_receipt_requires_ticket_grant_effect_fact_and_p19() -> None:
    authority, authorization, arguments, receipt = _authorization_and_receipt()
    port = ReceiptPortProbe(receipt)
    result = dispatch_and_verify_existing_gateway_step(
        authority,
        port,
        authorization,
        arguments=arguments,
        checked_at_ms=15,
    )
    assert result == receipt
    assert result.has_valid_sha256()
    assert result.execution_route == (
        "TOTAL_GATEWAY_POLICY_TICKET_GRANT_OMNI_RUNTIME"
    )
    assert result.policy_decision_sha256
    assert result.execution_ticket_sha256
    assert result.capability_grant_sha256
    assert result.effect_id and result.effect_sha256
    assert result.fact_ids and result.fact_hashes
    assert result.verification_plan_sha256
    assert result.p19_ingress_required is True
    assert result.completion_claimed is False
    assert port.calls == 1


def test_receipt_cannot_cross_plan_activation_action_or_world_scope() -> None:
    _authority, authorization, _arguments, receipt = _authorization_and_receipt()
    for field, value in (
        ("run_id", "run_" + "7" * 64),
        ("generation", authorization.generation + 1),
        ("activation_sha256", "8" * 64),
        ("plan_sha256", "9" * 64),
        ("action_id", "invented.action"),
        ("world_state_sha256", "a" * 64),
    ):
        tampered = receipt.model_copy(
            update={field: value, "receipt_sha256": "0" * 64}
        ).with_computed_sha256()
        with pytest.raises(
            CapabilityCompositionActivationError,
            match="receipt.binding_mismatch",
        ):
            validate_step_execution_receipt(
                authorization, tampered, checked_at_ms=15
            )


def test_receipt_requires_hash_time_and_concrete_contract() -> None:
    authority, authorization, arguments, receipt = _authorization_and_receipt()
    invalid_hash = receipt.model_copy(
        update={"receipt_sha256": "f" * 64}
    )
    with pytest.raises(
        CapabilityCompositionActivationError, match="receipt.hash_invalid"
    ):
        validate_step_execution_receipt(
            authorization, invalid_hash, checked_at_ms=15
        )

    late = receipt.model_copy(
        update={"executed_at_ms": 99, "receipt_sha256": "0" * 64}
    ).with_computed_sha256()
    with pytest.raises(
        CapabilityCompositionActivationError,
        match="execution_time_invalid",
    ):
        validate_step_execution_receipt(
            authorization, late, checked_at_ms=15
        )

    class WrongPort:
        def authorize_and_execute_composition_step(self, **_kwargs):
            return {"not": "a receipt"}

    with pytest.raises(
        CapabilityCompositionActivationError,
        match="receipt.contract_required",
    ):
        dispatch_and_verify_existing_gateway_step(
            authority,
            WrongPort(),
            authorization,
            arguments=arguments,
            checked_at_ms=15,
        )


def test_receipt_contract_cannot_claim_completion_or_skip_p19() -> None:
    _authority, _authorization, _arguments, receipt = _authorization_and_receipt()
    with pytest.raises(Exception):
        receipt.model_copy(update={"completion_claimed": True})
    with pytest.raises(Exception):
        receipt.model_copy(update={"p19_ingress_required": False})


def test_p7_receipt_module_defines_no_ticket_grant_runtime_or_completion_authority() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "total_gateway"
        / "capability_composition_receipt.py"
    ).read_text(encoding="utf-8")
    for token in (
        "class ExecutionTicket",
        "class CapabilityGrant",
        "class CompletionDecision",
        "class BodyRuntime",
        "sqlite3.connect",
        "subprocess",
        "runtime.execute",
        "omni_body.execute",
    ):
        assert token not in source
    assert "completion_claimed: Literal[False]" in source
    assert "p19_ingress_required: Literal[True]" in source
