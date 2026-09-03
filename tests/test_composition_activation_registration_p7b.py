from __future__ import annotations

from pathlib import Path

import pytest

from contracts.verification import AcceptancePredicate
from total_gateway.composition_activation_registration import (
    EXISTING_GATEWAY_STATE_STORE_AUTHORITY,
    LimitedActivationRegistrationError,
    LimitedCompositionActivationRegistrar,
    compile_limited_activation_registration,
)
from total_gateway.composition_activation_shadow import (
    build_system_verification_binding,
    computed_activation_sha256,
    propose_shadow_composition_activation,
)
from total_gateway.verification_registry import VerifierRegistry
from world_understanding.capability_composition import (
    compile_capability_composition_plan,
    parse_composition_proposal,
    validate_capability_composition_plan,
)

from tests.test_capability_composition_p4 import _single_read_fixture


def _fixture(*, risk: str = "A0", effect: str = "read"):
    action_registry, candidates, context, document = _single_read_fixture(
        risk=risk,
        effect=effect,
    )
    parsed = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        parsed,
        candidates,
        context,
        action_registry,
    )
    validation = validate_capability_composition_plan(
        plan,
        parsed,
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
        subject_identity="object:p7b-registration-output",
        evaluation_phase="POST_EXECUTION",
        registry_snapshot=verification_registry,
    )
    bindings = (binding,)
    shadow = propose_shadow_composition_activation(
        plan,
        validation,
        action_registry,
        verification_registry,
        bindings,
        current_world_state_sha256=plan.world_state_sha256,
        expected_principal_scope_hash=plan.principal_scope_hash,
        issued_at_ms=20,
        expires_at_ms=60,
    )
    return {
        "proposal": shadow,
        "plan": plan,
        "validation": validation,
        "action_registry": action_registry,
        "verification_registry": verification_registry,
        "verification_bindings": bindings,
        "current_world_state_sha256": plan.world_state_sha256,
        "expected_principal_scope_hash": plan.principal_scope_hash,
    }


def _compile(fixture, *, registered_at_ms: int = 30):
    return compile_limited_activation_registration(
        fixture["proposal"],
        plan=fixture["plan"],
        validation=fixture["validation"],
        action_registry=fixture["action_registry"],
        verification_registry=fixture["verification_registry"],
        verification_bindings=fixture["verification_bindings"],
        current_world_state_sha256=fixture["current_world_state_sha256"],
        expected_principal_scope_hash=fixture[
            "expected_principal_scope_hash"
        ],
        registered_at_ms=registered_at_ms,
    )


def _register(registrar, fixture, *, recorded_at_ms: int):
    return registrar.register(
        fixture["proposal"],
        plan=fixture["plan"],
        validation=fixture["validation"],
        action_registry=fixture["action_registry"],
        verification_registry=fixture["verification_registry"],
        verification_bindings=fixture["verification_bindings"],
        current_world_state_sha256=fixture["current_world_state_sha256"],
        expected_principal_scope_hash=fixture[
            "expected_principal_scope_hash"
        ],
        recorded_at_ms=recorded_at_ms,
    )


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


class _RaceWinnerPort(_GatewayStorePort):
    def put_limited_activation_registration(
        self, registration, *, expected_absent, recorded_at_ms
    ):
        assert expected_absent is True
        self.write_count += 1
        winner = registration.model_copy(
            update={"registered_at_ms": recorded_at_ms - 1}
        ).with_computed_identity()
        self.records[winner.registration_id] = winner
        return False


def test_compiles_exact_non_authorizing_limited_registration() -> None:
    fixture = _fixture()
    registration = _compile(fixture)
    proposal = fixture["proposal"]
    assert registration.has_valid_identity()
    assert registration.shadow_proposal_sha256 == proposal.proposal_sha256
    assert (
        registration.differential_trace_sha256
        == proposal.differential_trace.trace_sha256
    )
    assert registration.composition_activation_id == (
        proposal.activation_contract.composition_activation_id
    )
    assert registration.composition_plan_id == fixture["plan"].plan_id
    assert registration.verification_plan_id == (
        proposal.verification_plan.verification_plan_id
    )
    assert registration.allowed_action_ids == (
        proposal.activation_contract.allowed_action_ids
    )
    assert registration.allowed_action_versions == (
        proposal.activation_contract.allowed_action_versions
    )
    assert registration.activation_mode == "LIMITED_PRODUCTION"
    assert registration.eligibility_only is True
    assert registration.authorizes is False
    assert registration.confirms is False
    assert registration.changes_risk is False
    assert registration.may_execute is False


def test_replay_at_a_later_time_keeps_one_logical_registration() -> None:
    fixture = _fixture()
    at_30 = _compile(fixture, registered_at_ms=30)
    at_31 = _compile(fixture, registered_at_ms=31)
    assert at_30.registration_id == at_31.registration_id
    assert at_30.registration_sha256 != at_31.registration_sha256
    assert at_30.has_same_authority(at_31)

    writer = _GatewayStorePort()
    registrar = LimitedCompositionActivationRegistrar(writer)
    first = _register(registrar, fixture, recorded_at_ms=30)
    second = _register(registrar, fixture, recorded_at_ms=31)
    assert first.has_valid_identity()
    assert second.has_valid_identity()
    assert first.registration_id == second.registration_id
    assert first.registration_sha256 == second.registration_sha256
    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert writer.write_count == 1
    assert len(writer.records) == 1


def test_write_race_reconciles_the_first_gateway_store_record() -> None:
    fixture = _fixture()
    writer = _RaceWinnerPort()
    receipt = _register(
        LimitedCompositionActivationRegistrar(writer),
        fixture,
        recorded_at_ms=31,
    )
    persisted = next(iter(writer.records.values()))
    assert receipt.idempotent_replay is True
    assert receipt.registration_id == persisted.registration_id
    assert receipt.registration_sha256 == persisted.registration_sha256
    assert persisted.registered_at_ms == 30
    assert writer.write_count == 1


def test_p7a_future_a1_telemetry_cannot_enter_first_batch() -> None:
    fixture = _fixture(risk="A1", effect="read")
    # P7A remains shadow-only and may report the future second-batch envelope.
    assert fixture["proposal"].differential_trace.limited_production_eligible is True
    with pytest.raises(
        LimitedActivationRegistrationError,
        match="limited_registration.first_batch_a0_only",
    ):
        _compile(fixture)


def test_rejects_real_p7a_proposal_outside_limited_batch() -> None:
    fixture = _fixture(risk="A2", effect="write")
    assert fixture["proposal"].differential_trace.limited_production_eligible is False
    with pytest.raises(
        LimitedActivationRegistrationError,
        match="limited_registration.not_eligible",
    ):
        _compile(fixture)


def test_rejects_expired_or_not_yet_valid_activation() -> None:
    fixture = _fixture()
    for timestamp in (19, 60, 61):
        with pytest.raises(
            LimitedActivationRegistrationError,
            match="expired_or_not_yet_valid",
        ):
            _compile(fixture, registered_at_ms=timestamp)


def test_rehashed_world_state_forgery_fails_authoritative_rebuild() -> None:
    fixture = _fixture()
    proposal = fixture["proposal"]
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
    fixture["proposal"] = proposal.model_copy(
        update={"activation_contract": tampered_activation}
    ).with_computed_sha256()
    with pytest.raises(
        LimitedActivationRegistrationError,
        match="limited_registration.shadow_rebuild_mismatch",
    ):
        _compile(fixture)


def test_wrong_current_world_state_fails_before_registration() -> None:
    fixture = _fixture()
    fixture["current_world_state_sha256"] = "f" * 64
    with pytest.raises(
        LimitedActivationRegistrationError,
        match="authoritative_rebuild_failed.*shadow.world_state.mismatch",
    ):
        _compile(fixture)


def test_same_registration_key_with_different_authority_is_a_collision() -> None:
    fixture = _fixture()
    valid = _compile(fixture, registered_at_ms=30)
    forged = valid.model_copy(
        update={"world_state_sha256": "e" * 64}
    ).with_computed_identity()
    assert forged.registration_id == valid.registration_id
    assert forged.has_valid_identity()
    assert not forged.has_same_authority(valid)

    writer = _GatewayStorePort()
    writer.records[forged.registration_id] = forged
    with pytest.raises(
        LimitedActivationRegistrationError,
        match="limited_registration.identity_collision",
    ):
        _register(
            LimitedCompositionActivationRegistrar(writer),
            fixture,
            recorded_at_ms=31,
        )
    assert writer.write_count == 0


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
