"""P11 bridge from WorldInquiry to existing Self-Will and existing Gateway intake."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable, Literal, Mapping, Protocol

from contracts import ActionIntent, SourceRef, canonical_sha256
from contracts.world_understanding.inquiry import SelfWillDecision, WorldInquiry
from life_service.action_intents import ActionIntentReceipt, LifeActionIntentEmitter


@dataclass(frozen=True, slots=True)
class SelfWillDecisionRecord:
    decision_id: str
    inquiry_id: str
    decision: SelfWillDecision
    reason_codes: tuple[str, ...]
    goal: str | None
    decided_at_ms: int
    empirical_evidence_weight_milli: Literal[0] = 0
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False
    decision_sha256: str = ""

    def payload_for_hash(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("decision_sha256", None)
        return value

    def with_hash(self) -> "SelfWillDecisionRecord":
        digest = canonical_sha256(self.payload_for_hash())
        return replace(self, decision_sha256=digest)

    def has_valid_hash(self) -> bool:
        return self.decision_sha256 == canonical_sha256(self.payload_for_hash())


@dataclass(frozen=True, slots=True)
class AutonomousIntent:
    autonomous_intent_id: str
    origin: Literal["SELF_WILL"]
    principal: Literal["life:self"]
    life_id: str
    source_inquiry_id: str
    source_inquiry_sha256: str
    goal: str
    suggested_observation_modalities: tuple[str, ...]
    authority_refs: tuple[str, ...] = ()
    authorization: Literal["NONE"] = "NONE"
    may_execute_directly: Literal[False] = False
    requires_gateway_evaluation: Literal[True] = True
    empirical_evidence_weight_milli: Literal[0] = 0
    created_at_ms: int = 0
    expires_at_ms: int = 0
    autonomous_intent_sha256: str = ""

    def __post_init__(self) -> None:
        if self.origin != "SELF_WILL" or self.principal != "life:self" or self.authority_refs:
            raise ValueError("AUTONOMOUS_INTENT_AUTHORITY_INVALID")
        if not self.goal.strip() or self.expires_at_ms <= self.created_at_ms:
            raise ValueError("AUTONOMOUS_INTENT_LIFETIME_INVALID")

    def payload_for_hash(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("autonomous_intent_sha256", None)
        return value

    def with_hash(self) -> "AutonomousIntent":
        identity = "waut_" + canonical_sha256({
            "domain": "tiangong.world.autonomous-intent-id.v1",
            "life_id": self.life_id,
            "source_inquiry_id": self.source_inquiry_id,
            "created_at_ms": self.created_at_ms,
            "goal": self.goal,
        })
        identified = replace(self, autonomous_intent_id=identity, autonomous_intent_sha256="")
        digest = canonical_sha256(identified.payload_for_hash())
        return replace(identified, autonomous_intent_sha256=digest)

    def has_valid_hash(self) -> bool:
        return self.autonomous_intent_sha256 == canonical_sha256(self.payload_for_hash())


class SelfWillDecider(Protocol):
    def __call__(self, inquiry: WorldInquiry) -> Mapping[str, object] | SelfWillDecisionRecord: ...


class ExistingSelfWillAdapter:
    """No scheduler here: only adapts one inquiry into one existing Self-Will decision."""

    def __init__(self, decider: SelfWillDecider) -> None:
        if not callable(decider):
            raise TypeError("self-will decider must be callable")
        self._decider = decider

    def decide(self, inquiry: WorldInquiry, *, decided_at_ms: int) -> tuple[SelfWillDecisionRecord, AutonomousIntent | None]:
        if not inquiry.has_valid_hash() or inquiry.authorization != "NONE" or inquiry.may_execute or inquiry.may_call_tools:
            raise ValueError("WORLD_INQUIRY_INVALID_FOR_SELF_WILL")
        raw = self._decider(inquiry)
        if isinstance(raw, SelfWillDecisionRecord):
            record = raw
        elif isinstance(raw, Mapping):
            decision = str(raw.get("decision") or "").upper()
            if decision not in {"ACCEPT", "DEFER", "DISMISS", "EXPIRE"}:
                raise ValueError("SELF_WILL_DECISION_INVALID")
            reasons = tuple(sorted(set(str(v) for v in (raw.get("reason_codes") or ()) if str(v))))
            goal = str(raw.get("goal") or "").strip() or None
            decision_id = "swdec_" + canonical_sha256({
                "domain": "tiangong.world.self-will-decision-id.v1",
                "inquiry_id": inquiry.inquiry_id,
                "decision": decision,
                "reasons": reasons,
                "goal": goal,
                "decided_at_ms": decided_at_ms,
            })
            record = SelfWillDecisionRecord(
                decision_id=decision_id,
                inquiry_id=inquiry.inquiry_id,
                decision=decision,
                reason_codes=reasons or ("self_will.decision",),
                goal=goal,
                decided_at_ms=decided_at_ms,
            ).with_hash()
        else:
            raise ValueError("SELF_WILL_DECISION_INVALID")
        if (
            record.inquiry_id != inquiry.inquiry_id
            or record.decided_at_ms != decided_at_ms
            or record.decision not in {"ACCEPT", "DEFER", "DISMISS", "EXPIRE"}
            or record.empirical_evidence_weight_milli != 0
            or record.may_authorize
            or record.may_execute
            or not record.has_valid_hash()
        ):
            raise ValueError("SELF_WILL_DECISION_BINDING_INVALID")
        if record.decision != "ACCEPT":
            return record, None
        if not record.goal:
            raise ValueError("SELF_WILL_ACCEPT_REQUIRES_GOAL")
        intent = AutonomousIntent(
            autonomous_intent_id="waut_pending",
            origin="SELF_WILL",
            principal="life:self",
            life_id=inquiry.scope.life_id,
            source_inquiry_id=inquiry.inquiry_id,
            source_inquiry_sha256=inquiry.inquiry_sha256,
            goal=record.goal,
            suggested_observation_modalities=inquiry.suggested_observation_modalities,
            authority_refs=(),
            created_at_ms=decided_at_ms,
            expires_at_ms=decided_at_ms + 60_000,
        ).with_hash()
        return record, intent


def inquiry_source_ref(inquiry: WorldInquiry) -> SourceRef:
    if not inquiry.has_valid_hash():
        raise ValueError("WORLD_INQUIRY_HASH_INVALID")
    return SourceRef(
        source_type="EXTERNAL_DATA",
        object_id=inquiry.inquiry_id,
        object_revision=inquiry.revision,
        sha256=inquiry.inquiry_sha256,
    )


class SelfWillGatewayBridge:
    """Maps accepted Self-Will goals through the existing Life emitter into Gateway."""

    def __init__(
        self,
        *,
        emitter: LifeActionIntentEmitter,
        action_intent_factory: Callable[[AutonomousIntent, WorldInquiry], ActionIntent],
    ) -> None:
        self._emitter = emitter
        self._factory = action_intent_factory

    def submit(self, autonomous_intent: AutonomousIntent, inquiry: WorldInquiry) -> ActionIntentReceipt:
        if (
            not inquiry.has_valid_hash()
            or inquiry.authorization != "NONE"
            or inquiry.may_execute
            or inquiry.may_call_tools
            or inquiry.empirical_evidence_weight_milli != 0
        ):
            raise ValueError("WORLD_INQUIRY_INVALID_FOR_GATEWAY_BRIDGE")
        if not autonomous_intent.has_valid_hash():
            raise ValueError("AUTONOMOUS_INTENT_HASH_INVALID")
        if (
            autonomous_intent.origin != "SELF_WILL"
            or autonomous_intent.principal != "life:self"
            or autonomous_intent.authority_refs
            or autonomous_intent.source_inquiry_id != inquiry.inquiry_id
            or autonomous_intent.source_inquiry_sha256 != inquiry.inquiry_sha256
            or autonomous_intent.life_id != inquiry.scope.life_id
        ):
            raise ValueError("AUTONOMOUS_INTENT_BINDING_INVALID")
        intent = self._factory(autonomous_intent, inquiry)
        if (
            intent.life_id != inquiry.scope.life_id
            or getattr(intent, "principal_scope_hash", inquiry.scope.principal_scope_hash) != inquiry.scope.principal_scope_hash
        ):
            raise ValueError("SELF_WILL_GATEWAY_INTENT_SCOPE_MISMATCH")
        return self._emitter.submit_self_will(
            intent,
            source_inquiry_id=inquiry.inquiry_id,
            source_inquiry_sha256=inquiry.inquiry_sha256,
        )


@dataclass(frozen=True, slots=True)
class InquiryDispatchResult:
    decision: SelfWillDecisionRecord
    autonomous_intent: AutonomousIntent | None
    gateway_receipt: ActionIntentReceipt | None


class ExistingSelfWillInquiryPort:
    """Concrete WorldInquiryOutputPort over the existing Self-Will/Gateway path.

    It owns no queue or scheduler. ``emit`` synchronously hands one semantic
    inquiry to the injected existing Self-Will adapter; only ACCEPT can continue
    through the injected existing Gateway bridge.
    """

    def __init__(
        self,
        *,
        self_will: ExistingSelfWillAdapter,
        now_ms: Callable[[], int],
        gateway_bridge: SelfWillGatewayBridge | None = None,
        result_sink: Callable[[InquiryDispatchResult], None] | None = None,
    ) -> None:
        self._self_will = self_will
        self._now_ms = now_ms
        self._gateway_bridge = gateway_bridge
        self._result_sink = result_sink

    def dispatch(self, inquiry: WorldInquiry) -> InquiryDispatchResult:
        decided_at_ms = int(self._now_ms())
        decision, autonomous_intent = self._self_will.decide(inquiry, decided_at_ms=decided_at_ms)
        receipt = None
        if autonomous_intent is not None and self._gateway_bridge is not None:
            receipt = self._gateway_bridge.submit(autonomous_intent, inquiry)
        result = InquiryDispatchResult(decision, autonomous_intent, receipt)
        if self._result_sink is not None:
            self._result_sink(result)
        return result

    def emit(self, inquiry: WorldInquiry) -> None:
        self.dispatch(inquiry)


__all__ = [
    "AutonomousIntent", "ExistingSelfWillAdapter", "SelfWillDecisionRecord",
    "SelfWillGatewayBridge", "ExistingSelfWillInquiryPort", "InquiryDispatchResult",
    "inquiry_source_ref",
]
