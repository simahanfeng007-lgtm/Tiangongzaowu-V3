from __future__ import annotations

from pathlib import Path

import pytest

from contracts.capability_composition import CompositionActivationContractV1
from contracts.verification import (
    AcceptancePredicate,
    VerificationPlan,
    VerificationPlanEntryV2,
)
from total_gateway.composition_activation_registration import (
    EXISTING_GATEWAY_STATE_STORE_AUTHORITY,
    LimitedActivationRegistrationError,
    LimitedCompositionActivationRegistrar,
    compile_limited_activation_registration,
)
from total_gateway.composition_activation_shadow import (
    ShadowActivationDifferentialTraceV1,
    ShadowCompositionActivationProposalV1,
    computed_activation_sha256,
)


REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
PLAN_ID = "plan.p7b.demo"
PLAN_SHA256 = "3" * 64
VALIDATION_SHA256 = "4" * 64
ACTION_REGISTRY_SHA256 = "5" * 64
VERIFICATION_REGISTRY_SHA256 = "6" * 64
WORLD_STATE_SHA256 = "7" * 64
SOURCE_MANIFEST_SHA256 = "8" * 64
CAPABILITY_MANIFEST_SHA256 = "9" * 64
PRINCIPAL_SCOPE_HASH = "a" * 64
ACTION_ID = "filesystem.read_file"
ACTION_VERSION = "1"


def _proposal(*, eligible: bool = True) -> ShadowCompositionActivationProposalV1:
    predicate = AcceptancePredicate.create(
        predicate_type="artifact.nonempty",
        subject_kind="artifact",
    )
    entry = VerificationPlanEntryV2(
        plan_entry_id="vpe_" + "0" * 64,
        verifier_id="verifier.artifact_content",
        verifier_version="2",
        predicate=predicate,
        subject_identity="artifact:p7b-demo",
        evaluation_phase="POST_EXECUTION",
        required=True,
        entry_sha256="0" * 64,
    ).with_computed_sha256()
    verification = VerificationPlan(
        verification_plan_id="vpl_" + "0" * 64,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        registry_snapshot_sha256=VERIFICATION_REGISTRY_SHA256,
        entries=(entry,),
        plan_sha256="0" * 64,
    ).with_computed_sha256()

    activation = CompositionActivationContractV1(
        composition_activation_id="activation.p7b.demo",
        composition_plan_id=PLAN_ID,
        composition_plan_sha256=PLAN_SHA256,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        principal_scope_hash=PRINCIPAL_SCOPE_HASH,
        world_state_sha256=WORLD_STATE_SHA256,
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        capability_manifest_sha256=CAPABILITY_MANIFEST_SHA256,
        allowed_action_ids=(ACTION_ID,),
        allowed_action_versions=(ACTION_VERSION,),
        verification_plan_ref=verification.verification_plan_id,
        issued_at_ms=1_000,
        expires_at_ms=2_000,
        activation_sha256="0" * 64,
    )
    activation = activation.model_copy(
        update={"activation_sha256": computed_activation_sha256(activation)}
    )

    rejection_codes = () if eligible else ("limited.risk_not_a0_a1",)
    trace = ShadowActivationDifferentialTraceV1(
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        composition_plan_id=PLAN_ID,
        composition_plan_sha256=PLAN_SHA256,
        validation_sha256=VALIDATION_SHA256,
        action_registry_sha256=ACTION_REGISTRY_SHA256,
        verification_registry_sha256=VERIFICATION_REGISTRY_SHA256,
        verification_plan_sha256=verification.plan_sha256,
        planned_action_ids=(ACTION_ID,),
        proposed_allowed_action_ids=(ACTION_ID,),
        legacy_allowed_action_ids=(),
        added_vs_legacy=(ACTION_ID,),
        removed_vs_legacy=(),
        exact_action_set=True,
        registry_subset=True,
        source_manifest_exact=True,
        action_versions_exact=True,
        verification_bindings_complete=True,
        limited_production_eligible=eligible,
        limited_rejection_codes=rejection_codes,
        trace_sha256="0" * 64,
    ).with_computed_sha256()
    return ShadowCompositionActivationProposalV1(
        activation_contract=activation,
        verification_plan=verification,
        validation_mode="PROVED_VALID",
        validation_sha256=VALIDATION_SHA256,
        action_registry_sha256=ACTION_REGISTRY_SHA256,
        verification_registry_sha256=VERIFICATION_REGISTRY_SHA256,
        differential_trace=trace,
        proposal_sha256="0" * 64,
    ).with_computed_sha256()


class _GatewayStorePort:
    authority_kind = EXISTING_GATEWAY_STATE_STORE_AUTHORITY

    def __init__(self) -> None:
        self.records = {}
        self.write_count = 0

    def get_limited_activation_registration(self, registration_id):
        return self.records.get(registration_id)

    def put_limited_activation_registration(
        self, registration, *, expected_absent, recorded_at_ms
    ):
        assert expected_absent is True
        assert recorded_at_ms == registration.registered_at_ms
        self.write_count += 1
        if registration.registration_id in self.records:
            return False
        self.records[registration.registration_id] = registration
        return True


class _WrongAuthorityPort(_GatewayStorePort):
    authority_kind = "ARBITRARY_WRITER"


def test_compiles_exact_non_authorizing_limited_registration() -> None:
    proposal = _proposal()
    registration = compile_limited_activation_registration(
        proposal, registered_at_ms=1_500
    )
    assert registration.has_valid_identity()
    assert registration.composition_activation_id == "activation.p7b.demo"
    assert registration.composition_plan_id == PLAN_ID
    assert registration.verification_plan_id == (
        proposal.verification_plan.verification_plan_id
    )
    assert registration.allowed_action_ids == (ACTION_ID,)
    assert registration.allowed_action_versions == (ACTION_VERSION,)
    assert registration.activation_mode == "LIMITED_PRODUCTION"
    assert registration.eligibility_only is True
    assert registration.authorizes is False
    assert registration.confirms is False
    assert registration.changes_risk is False
    assert registration.may_execute is False


def test_registration_is_single_write_and_idempotent_for_same_command() -> None:
    writer = _GatewayStorePort()
    registrar = LimitedCompositionActivationRegistrar(writer)
    first = registrar.register(_proposal(), recorded_at_ms=1_500)
    second = registrar.register(_proposal(), recorded_at_ms=1_500)
    assert first.has_valid_identity()
    assert second.has_valid_identity()
    assert first.registration_id == second.registration_id
    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert writer.write_count == 1
    assert len(writer.records) == 1


def test_rejects_noneligible_shadow_proposal() -> None:
    with pytest.raises(
        LimitedActivationRegistrationError,
        match="limited_registration.not_eligible",
    ):
        compile_limited_activation_registration(
            _proposal(eligible=False), registered_at_ms=1_500
        )


def test_rejects_expired_or_not_yet_valid_activation() -> None:
    for timestamp in (999, 2_000, 2_001):
        with pytest.raises(
            LimitedActivationRegistrationError,
            match="expired_or_not_yet_valid",
        ):
            compile_limited_activation_registration(
                _proposal(), registered_at_ms=timestamp
            )


def test_rejects_tampered_cross_scope_binding_even_with_rehashed_proposal() -> None:
    proposal = _proposal()
    tampered_activation = proposal.activation_contract.model_copy(
        update={"world_state_sha256": "b" * 64}
    )
    tampered_activation = tampered_activation.model_copy(
        update={
            "activation_sha256": computed_activation_sha256(
                tampered_activation
            )
        }
    )
    tampered = proposal.model_copy(
        update={"activation_contract": tampered_activation}
    ).with_computed_sha256()
    registration = compile_limited_activation_registration(
        tampered, registered_at_ms=1_500
    )
    assert registration.world_state_sha256 == "b" * 64
    assert registration.has_valid_identity()

    mismatched_plan = tampered.verification_plan.model_copy(
        update={"request_id": "req_" + "c" * 64}
    ).with_computed_sha256()
    broken = tampered.model_copy(
        update={"verification_plan": mismatched_plan}
    ).with_computed_sha256()
    with pytest.raises(
        LimitedActivationRegistrationError,
        match="limited_registration.binding_mismatch",
    ):
        compile_limited_activation_registration(
            broken, registered_at_ms=1_500
        )


def test_rejects_non_gateway_writer_authority() -> None:
    with pytest.raises(TypeError, match="Gateway State Store authority"):
        LimitedCompositionActivationRegistrar(_WrongAuthorityPort())


def test_p7b_registration_seam_cannot_mint_or_execute() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "total_gateway"
        / "composition_activation_registration.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "sqlite3.connect",
        "class GatewayStateStore",
        "ExecutionTicket(",
        "OmniCapabilityGrant(",
        "PolicyDecision(",
        "CompletionDecision(",
        ".execute(",
        ".dispatch(",
        "authorizes: Literal[True]",
        "may_execute: Literal[True]",
    ):
        assert forbidden not in source
