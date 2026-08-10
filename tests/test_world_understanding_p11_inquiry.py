from __future__ import annotations

from types import SimpleNamespace

import pytest

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from world_understanding.common.budgets import BudgetConfig, BudgetLedger, WorkCost
from world_understanding.inquiry import (
    CuriosityGenerator,
    ExistingSelfWillAdapter,
    ExistingSelfWillInquiryPort,
    InquiryAdmission,
    InquiryAdmissionConfig,
    InquiryAdmissionSignals,
    KnowledgeGapGenerator,
)

PRINCIPAL = "a" * 64


def _scope(life_id: str = "life.A") -> WorldScope:
    bindings = (ScopeBinding(key="repository", value="repo.main"),)
    world_id = derive_world_id(life_id=life_id, namespace_anchor="primary")
    return WorldScope(
        life_id=life_id,
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id=life_id,
            world_id=world_id,
            domain_id="software",
            scope_bindings=bindings,
        ),
        principal_scope_hash=PRINCIPAL,
        privacy_scope="system",
    )


def _snapshot():
    scope = _scope()
    conflict = WorldRecordRef(record_type="world_conflict", record_id="conf.1", revision=None, sha256="1" * 64)
    stale = WorldRecordRef(record_type="world_entity", record_id="ent.1", revision=1, sha256="2" * 64)
    uncertainty = WorldRecordRef(record_type="world_uncertainty", record_id="unc.1", revision=None, sha256="3" * 64)
    frame = WorldRecordRef(record_type="world_frame", record_id="frame.1", revision=1, sha256="4" * 64)
    state = SimpleNamespace(
        world_state_id="wst.fixture",
        world_sequence=0,
        state_sha256="5" * 64,
        scope=scope,
        frame_ref=frame,
        unresolved_conflict_refs=(conflict,),
        stale_refs=(stale,),
        has_valid_hash=lambda: True,
    )
    return SimpleNamespace(state=state, uncertainty=SimpleNamespace(refs=(uncertainty,)))


def _inquiry():
    snapshot = _snapshot()
    gap = KnowledgeGapGenerator().generate(snapshot)[0]
    generator = CuriosityGenerator()
    curiosity = generator.build_curiosity(
        gap,
        frame_ref=snapshot.state.frame_ref,
        created_at_ms=1_000,
        expires_at_ms=61_000,
    )
    state_ref = WorldRecordRef(
        record_type="world_state",
        record_id=snapshot.state.world_state_id,
        revision=1,
        sha256=snapshot.state.state_sha256,
    )
    return generator.build_inquiry(
        gap,
        curiosity,
        correlation_id="corr.p11",
        source_world_state_ref=state_ref,
        inquiry_budget_remaining=8,
    )


def _signals(**changes) -> InquiryAdmissionSignals:
    values = dict(
        user_relevance_milli=900,
        novelty_milli=900,
        actionability_milli=900,
        cost_milli=100,
        risk_milli=0,
        duplicate_milli=0,
        privacy_cost_milli=0,
        runtime_pressure_milli=0,
        uncertainty_milli=100,
        time_remaining_ms=5_000,
        inquiry_count_remaining=3,
        privacy_allowed=True,
        user_present=False,
        active_user_task=False,
    )
    values.update(changes)
    return InquiryAdmissionSignals(**values)


def _budget(available: bool = True) -> BudgetLedger:
    config = BudgetConfig(
        token_budget=10_000 if available else 100,
        compute_budget_ms=10_000 if available else 100,
        io_budget_bytes=1_000_000,
        latency_budget_ms=10_000,
        interactive_token_reserve=100 if available else 100,
        interactive_compute_reserve_ms=100 if available else 100,
    )
    return BudgetLedger(config)


def test_gap_to_inquiry_remains_non_authorizing_and_modality_only():
    gaps = KnowledgeGapGenerator().generate(_snapshot())
    assert len(gaps) == 3
    assert all(gap.empirical_evidence_weight_milli == 0 and not gap.may_execute for gap in gaps)
    inquiry = _inquiry()
    assert inquiry.has_valid_hash()
    assert inquiry.authorization == "NONE"
    assert inquiry.may_execute is False and inquiry.may_call_tools is False and inquiry.may_authorize is False
    assert inquiry.empirical_evidence_weight_milli == 0
    assert inquiry.suggested_observation_modalities
    assert all(" " not in value and "/" not in value and "\\" not in value for value in inquiry.suggested_observation_modalities)


def test_executable_modality_text_is_rejected():
    from world_understanding.inquiry.curiosity import validate_observation_modalities
    with pytest.raises(ValueError, match="WORLD_INQUIRY_EXECUTABLE_MODALITY_FORBIDDEN"):
        validate_observation_modalities(("shell.run rm -rf /",))


def test_lambda_admission_enforces_duplicate_privacy_time_budget_and_interactive_priority():
    inquiry = _inquiry()
    admission = InquiryAdmission(
        config=InquiryAdmissionConfig(admit_threshold=800, defer_threshold=100),
        budget=_budget(),
    )
    first = admission.evaluate(inquiry, _signals(), work_cost=WorkCost(token_cost=100, compute_ms=50))
    assert first.disposition == "ADMITTED" and first.charged
    assert admission.evaluate(inquiry, _signals(), charge=False).reason_code == "INQUIRY_DUPLICATE"

    assert InquiryAdmission().evaluate(inquiry, _signals(privacy_allowed=False), charge=False).reason_code == "INQUIRY_PRIVACY_FORBIDDEN"
    assert InquiryAdmission().evaluate(inquiry, _signals(time_remaining_ms=1), charge=False).reason_code == "INQUIRY_TIME_BUDGET"
    foreground = InquiryAdmission().evaluate(
        inquiry,
        _signals(user_present=True, active_user_task=True, runtime_pressure_milli=800),
        charge=False,
    )
    assert foreground.disposition == "DEFERRED" and foreground.reason_code == "INQUIRY_INTERACTIVE_PRIORITY"


def test_lambda_respects_p5_interactive_resource_reserve():
    inquiry = _inquiry()
    admission = InquiryAdmission(budget=_budget(available=False))
    decision = admission.evaluate(inquiry, _signals(), work_cost=WorkCost(token_cost=1, compute_ms=1))
    assert decision.disposition == "DEFERRED"
    assert decision.reason_code == "INQUIRY_RESOURCE_RESERVE"


def test_self_will_accept_creates_zero_authority_autonomous_intent_only():
    inquiry = _inquiry()
    adapter = ExistingSelfWillAdapter(
        lambda _: {
            "decision": "ACCEPT",
            "goal": "Verify the unresolved world gap with an independent bounded observation",
            "reason_codes": ["self_will.information_gain"],
        }
    )
    decision, intent = adapter.decide(inquiry, decided_at_ms=2_000)
    assert decision.decision == "ACCEPT"
    assert decision.empirical_evidence_weight_milli == 0 and not decision.may_authorize and not decision.may_execute
    assert intent is not None and intent.has_valid_hash()
    assert intent.origin == "SELF_WILL"
    assert intent.principal == "life:self"
    assert intent.source_inquiry_id == inquiry.inquiry_id
    assert intent.authority_refs == ()
    assert intent.authorization == "NONE"
    assert intent.may_execute_directly is False and intent.requires_gateway_evaluation is True
    assert intent.empirical_evidence_weight_milli == 0


@pytest.mark.parametrize("decision_name", ["DEFER", "DISMISS", "EXPIRE"])
def test_non_accept_self_will_decisions_never_create_autonomous_intent(decision_name: str):
    inquiry = _inquiry()
    decision, intent = ExistingSelfWillAdapter(
        lambda _: {"decision": decision_name, "reason_codes": ["self_will.no_action"]}
    ).decide(inquiry, decided_at_ms=2_000)
    assert decision.decision == decision_name
    assert intent is None


def test_concrete_inquiry_output_port_calls_existing_self_will_without_creating_a_scheduler():
    inquiry = _inquiry()
    seen = []
    port = ExistingSelfWillInquiryPort(
        self_will=ExistingSelfWillAdapter(lambda _:{"decision":"DEFER","reason_codes":["self_will.busy"]}),
        now_ms=lambda: 2_500,
        result_sink=seen.append,
    )
    result = port.dispatch(inquiry)
    assert result.decision.decision == "DEFER"
    assert result.autonomous_intent is None and result.gateway_receipt is None
    assert seen == [result]
