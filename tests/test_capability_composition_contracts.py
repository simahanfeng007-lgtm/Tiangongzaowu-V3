from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.capability_composition import (
    AttributionIntegrityV1,
    CapabilityDescriptorObservationV1,
    CompositionActivationContractV1,
    CompositionProposalV1,
    CompositionValidationResultV1,
    ProposalStepV1,
    SourceRevisionRefV1,
)


H = "a" * 64
REQ = "req_" + "b" * 64
RUN = "run_" + "c" * 64


def source_revision(kind: str = "TOOL_ACTION") -> SourceRevisionRefV1:
    return SourceRevisionRefV1(
        source_kind=kind,
        semantic_id="file.read",
        version="1",
        source_files=("src/example.py",),
        source_sha256=H,
        descriptor_sha256=H,
        manifest_sha256=H if kind == "TOOL_ACTION" else None,
    )


def test_capability_descriptor_is_non_authorizing_and_non_executing() -> None:
    observation = CapabilityDescriptorObservationV1(
        observation_id="obs.tool.file-read",
        descriptor_kind="TOOL_ACTION",
        semantic_id="file.read",
        source_revision=source_revision(),
        observed_at_ms=1,
        descriptor_sha256=H,
    )
    assert observation.may_authorize is False
    assert observation.may_execute is False

    with pytest.raises(ValidationError):
        CapabilityDescriptorObservationV1(
            observation_id="obs.bad",
            descriptor_kind="TOOL_ACTION",
            semantic_id="file.read",
            source_revision=source_revision(),
            observed_at_ms=1,
            may_authorize=True,
            descriptor_sha256=H,
        )


def test_model_proposal_has_no_authority_fields() -> None:
    proposal = CompositionProposalV1(
        goal_ref="goal.demo",
        selected_action_candidate_ids=("candidate.A7",),
        steps=(ProposalStepV1(step_id="step.1", candidate_id="candidate.A7"),),
        proposal_sha256=H,
    )
    assert proposal.selected_action_candidate_ids == ("candidate.A7",)

    with pytest.raises(ValidationError):
        CompositionProposalV1(
            goal_ref="goal.demo",
            selected_action_candidate_ids=("candidate.A7",),
            steps=(ProposalStepV1(step_id="step.1", candidate_id="candidate.A7"),),
            proposal_sha256=H,
            allowed_action_ids=("file.read",),
        )


def test_unknown_validation_requires_explicit_disposition() -> None:
    with pytest.raises(ValidationError):
        CompositionValidationResultV1(
            plan_id="plan.demo",
            plan_sha256=H,
            result="UNKNOWN",
            validated_at_ms=1,
            validation_sha256=H,
        )

    provisional = CompositionValidationResultV1(
        plan_id="plan.demo",
        plan_sha256=H,
        result="UNKNOWN",
        unknown_disposition="PROVISIONAL_ALLOW",
        mandatory_verification=True,
        validated_at_ms=1,
        validation_sha256=H,
    )
    assert provisional.mandatory_verification is True


def test_provisional_allow_cannot_skip_verification() -> None:
    with pytest.raises(ValidationError):
        CompositionValidationResultV1(
            plan_id="plan.demo",
            plan_sha256=H,
            result="UNKNOWN",
            unknown_disposition="PROVISIONAL_ALLOW",
            mandatory_verification=False,
            validated_at_ms=1,
            validation_sha256=H,
        )


def test_activation_is_request_run_generation_scoped_and_expiring() -> None:
    activation = CompositionActivationContractV1(
        composition_activation_id="activation.demo",
        composition_plan_id="plan.demo",
        composition_plan_sha256=H,
        request_id=REQ,
        run_id=RUN,
        generation=1,
        principal_scope_hash=H,
        world_state_sha256=H,
        source_manifest_sha256=H,
        capability_manifest_sha256=H,
        allowed_action_ids=("file.read",),
        allowed_action_versions=("1",),
        issued_at_ms=10,
        expires_at_ms=20,
        activation_sha256=H,
    )
    assert activation.generation == 1

    with pytest.raises(ValidationError):
        CompositionActivationContractV1(
            composition_activation_id="activation.bad",
            composition_plan_id="plan.demo",
            composition_plan_sha256=H,
            request_id=REQ,
            run_id=RUN,
            generation=1,
            principal_scope_hash=H,
            world_state_sha256=H,
            source_manifest_sha256=H,
            capability_manifest_sha256=H,
            allowed_action_ids=("file.read",),
            allowed_action_versions=("1", "2"),
            issued_at_ms=10,
            expires_at_ms=20,
            activation_sha256=H,
        )


def test_attribution_integrity_is_bound_to_exact_plan_identity() -> None:
    integrity = AttributionIntegrityV1(
        request_id=REQ,
        run_id=RUN,
        generation=2,
        composition_plan_sha256=H,
        state="PASS",
        checked_lineage_sha256=H,
        checked_at_ms=20,
        attribution_sha256=H,
    )
    assert integrity.state == "PASS"
    assert integrity.composition_plan_sha256 == H
