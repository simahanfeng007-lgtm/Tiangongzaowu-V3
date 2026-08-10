"""P11 InquiryOutcome construction from actual post-execution reality references."""
from __future__ import annotations

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.inquiry import InquiryOutcome, SelfWillDecision, WorldInquiry, derive_inquiry_outcome_id
from world_understanding.inquiry.self_will_integration import AutonomousIntent


def build_inquiry_outcome(
    inquiry: WorldInquiry,
    *,
    self_will_decision: SelfWillDecision,
    closed_at_ms: int,
    autonomous_intent: AutonomousIntent | None = None,
    run_id: str | None = None,
    execution_ticket_id: str | None = None,
    resulting_source_envelope_refs: tuple[WorldRecordRef, ...] = (),
    observation_refs: tuple[WorldRecordRef, ...] = (),
    evidence_refs: tuple[WorldRecordRef, ...] = (),
    changed_cognition_refs: tuple[WorldRecordRef, ...] = (),
    changed_world_state_refs: tuple[WorldRecordRef, ...] = (),
    resolved: bool = False,
    residual_gap_milli: int = 1000,
    information_gain_milli: int = 0,
) -> InquiryOutcome:
    if not inquiry.has_valid_hash():
        raise ValueError("WORLD_INQUIRY_HASH_INVALID")
    if self_will_decision == "ACCEPT" and autonomous_intent is None:
        raise ValueError("INQUIRY_OUTCOME_ACCEPT_REQUIRES_AUTONOMOUS_INTENT")
    if self_will_decision != "ACCEPT" and autonomous_intent is not None:
        raise ValueError("INQUIRY_OUTCOME_NON_ACCEPT_CANNOT_HAVE_AUTONOMOUS_INTENT")
    if self_will_decision != "ACCEPT" and resolved:
        raise ValueError("INQUIRY_OUTCOME_NON_ACCEPT_CANNOT_RESOLVE_WORLD_GAP")
    if autonomous_intent is not None:
        if (
            not autonomous_intent.has_valid_hash()
            or autonomous_intent.source_inquiry_id != inquiry.inquiry_id
            or autonomous_intent.life_id != inquiry.scope.life_id
        ):
            raise ValueError("INQUIRY_OUTCOME_AUTONOMOUS_INTENT_MISMATCH")
        autonomous_intent_id = autonomous_intent.autonomous_intent_id
    else:
        autonomous_intent_id = None
    outcome_id = derive_inquiry_outcome_id(
        world_scope_hash=inquiry.scope.world_scope_hash,
        inquiry_id=inquiry.inquiry_id,
        self_will_decision=self_will_decision,
        closed_at_ms=closed_at_ms,
        resulting_source_envelope_refs=resulting_source_envelope_refs,
    )
    return InquiryOutcome(
        outcome_id=outcome_id,
        scope=inquiry.scope,
        inquiry_id=inquiry.inquiry_id,
        self_will_decision=self_will_decision,
        autonomous_intent_id=autonomous_intent_id,
        run_id=run_id,
        execution_ticket_id=execution_ticket_id,
        resulting_source_envelope_refs=resulting_source_envelope_refs,
        observation_refs=observation_refs,
        evidence_refs=evidence_refs,
        resolved=resolved,
        residual_gap_milli=residual_gap_milli,
        information_gain_milli=information_gain_milli,
        changed_cognition_refs=changed_cognition_refs,
        changed_world_state_refs=changed_world_state_refs,
        closed_at_ms=closed_at_ms,
        outcome_sha256="0" * 64,
    ).with_computed_hash()


__all__ = ["build_inquiry_outcome"]
