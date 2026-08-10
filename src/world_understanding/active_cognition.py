"""P13.2 bounded active cognition over the existing production chain.

This coordinator owns no listener, thread, scheduler, runtime, gateway, or
tool executor.  A committed WorldState may produce at most one admitted
inquiry.  The injected dispatcher is the existing Total Gateway worker; its
resulting native ToolResult returns through the one World Understanding
ingress and closes the persisted causal record here.
"""
from __future__ import annotations

from dataclasses import asdict
from threading import RLock
from typing import Callable, Mapping, Protocol

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.inquiry import InquiryOutcome, WorldInquiry

from .inquiry import CuriosityGenerator, InquiryAdmission, InquiryAdmissionSignals, KnowledgeGapGenerator
from .inquiry.inquiry_outcome import build_inquiry_outcome
from .inquiry.self_will_integration import AutonomousIntent
from .dynamics.inquiry_backoff import InquiryGainObservation, derive_inquiry_backoff
from .world_state.store import MaterializedWorldSnapshot, WorldStateStore


class ActiveInquiryDispatcher(Protocol):
    def __call__(
        self,
        inquiry: WorldInquiry,
        result_sink: Callable[[Mapping[str, object]], None],
    ) -> bool: ...


def _inquiry_from(record: Mapping[str, object]) -> WorldInquiry:
    return WorldInquiry.model_validate(record["inquiry"], strict=False)


def _autonomous_from(value: object) -> AutonomousIntent | None:
    if not isinstance(value, Mapping):
        return None
    data = dict(value)
    data["suggested_observation_modalities"] = tuple(data.get("suggested_observation_modalities") or ())
    data["authority_refs"] = tuple(data.get("authority_refs") or ())
    return AutonomousIntent(**data)


class ActiveWorldCognitionCoordinator:
    """Gap -> Inquiry admission -> existing Gateway -> Reality lineage."""

    def __init__(
        self,
        *,
        store: WorldStateStore,
        dispatcher: ActiveInquiryDispatcher,
        max_open_per_scope: int = 8,
    ) -> None:
        if not callable(dispatcher) or not 1 <= max_open_per_scope <= 64:
            raise ValueError("WORLD_ACTIVE_COGNITION_CONFIG_INVALID")
        self._store = store
        self._dispatcher = dispatcher
        self._max_open = max_open_per_scope
        self._gaps = KnowledgeGapGenerator()
        self._curiosity = CuriosityGenerator()
        self._lock = RLock()

    @staticmethod
    def _family(inquiry: WorldInquiry) -> str:
        subject = inquiry.subject_refs[0].record_type if inquiry.subject_refs else "world"
        missing = inquiry.missing_evidence_types[0] if inquiry.missing_evidence_types else "observation"
        return f"{subject}:{missing}"

    def _records(self, scope_hash: str) -> tuple[dict[str, object], ...]:
        return self._store.active_cognition_records(world_scope_hash=scope_hash)

    @staticmethod
    def _next(record: Mapping[str, object], **changes: object) -> dict[str, object]:
        value = dict(record)
        value.update(changes)
        value["revision"] = int(record.get("revision") or 0) + 1
        value.pop("record_sha256", None)
        return value

    def _dispatch_event(self, inquiry_id: str, event: Mapping[str, object]) -> None:
        with self._lock:
            current = self._store.active_cognition_record(inquiry_id)
            if current is None or str(current.get("status") or "") == "CLOSED":
                return
            phase = str(event.get("phase") or "").upper()
            changes: dict[str, object] = {"last_dispatch_event": dict(event)}
            if phase == "DECIDED":
                changes.update(
                    status="DECIDED",
                    self_will_decision=str(event.get("decision") or "DEFER").upper(),
                    decision=event.get("decision_record"),
                    autonomous_intent=event.get("autonomous_intent"),
                )
            elif phase == "STARTED":
                changes.update(
                    status="EXECUTING",
                    run_id=str(event.get("run_id") or ""),
                    execution_ticket_id=str(event.get("execution_ticket_id") or ""),
                )
            elif phase in {"DEFERRED", "DISMISSED", "EXPIRED", "FAILED"}:
                decision = str(current.get("self_will_decision") or event.get("decision") or "DEFER").upper()
                if decision not in {"DEFER", "DISMISS", "EXPIRE"}:
                    # An accepted execution failure remains an ACCEPT outcome;
                    # it simply has zero information gain and therefore backs off.
                    decision = "ACCEPT"
                self._close_without_reality(current, decision=decision, closed_at_ms=int(event.get("at_ms") or 0))
                return
            self._store.put_active_cognition_record(self._next(current, **changes))

    def _close_without_reality(self, record: Mapping[str, object], *, decision: str, closed_at_ms: int) -> None:
        inquiry = _inquiry_from(record)
        autonomous = _autonomous_from(record.get("autonomous_intent"))
        if decision != "ACCEPT":
            autonomous = None
        outcome = build_inquiry_outcome(
            inquiry,
            self_will_decision=decision,  # type: ignore[arg-type]
            autonomous_intent=autonomous,
            run_id=str(record.get("run_id") or "") or None,
            execution_ticket_id=str(record.get("execution_ticket_id") or "") or None,
            closed_at_ms=max(inquiry.created_at_ms, closed_at_ms),
            resolved=False,
            residual_gap_milli=1000,
            information_gain_milli=0,
        )
        self._store.put_active_cognition_record(self._next(
            record,
            status="CLOSED",
            outcome=outcome.model_dump(mode="json"),
            closed_at_ms=outcome.closed_at_ms,
            information_gain_milli=0,
        ))

    def _close_from_reality(
        self,
        envelope: WorldIngressEnvelope,
        snapshot: MaterializedWorldSnapshot,
        inquiry_id: str,
    ) -> None:
        record = self._store.active_cognition_record(inquiry_id)
        if record is None or str(record.get("status") or "") == "CLOSED":
            return
        inquiry = _inquiry_from(record)
        autonomous = _autonomous_from(record.get("autonomous_intent"))
        if autonomous is None:
            return
        payload = envelope.payload_inline or {}
        lineage = payload.get("world_inquiry_lineage") if isinstance(payload.get("world_inquiry_lineage"), Mapping) else {}
        terminal = str(payload.get("terminal_status") or lineage.get("terminal_status") or payload.get("status") or "").lower()
        success = terminal in {"success", "succeeded", "completed", "ok"} or payload.get("ok") is True
        unresolved = set(snapshot.state.stale_refs) | set(snapshot.state.unresolved_conflict_refs)
        if snapshot.uncertainty is not None:
            unresolved.update(snapshot.uncertainty.refs)
        resolved = success and not any(ref in unresolved for ref in inquiry.subject_refs)
        source_ref = WorldRecordRef(
            record_type="world_source_envelope",
            record_id=envelope.envelope_id,
            revision=1,
            sha256=envelope.dedup_key,
        )
        outcome = build_inquiry_outcome(
            inquiry,
            self_will_decision="ACCEPT",
            autonomous_intent=autonomous,
            run_id=str(record.get("run_id") or envelope.run_id or "") or None,
            execution_ticket_id=str(record.get("execution_ticket_id") or "") or None,
            resulting_source_envelope_refs=(source_ref,),
            changed_world_state_refs=(snapshot.state_ref,),
            closed_at_ms=max(inquiry.created_at_ms, envelope.source_time.recorded_at_ms),
            resolved=resolved,
            residual_gap_milli=0 if resolved else (500 if success else 1000),
            information_gain_milli=1000 if resolved else (250 if success else 0),
        )
        self._store.put_active_cognition_record(self._next(
            record,
            status="CLOSED",
            outcome=outcome.model_dump(mode="json"),
            closed_at_ms=outcome.closed_at_ms,
            information_gain_milli=outcome.information_gain_milli,
        ))

    def observe(
        self,
        envelope: WorldIngressEnvelope,
        snapshot: MaterializedWorldSnapshot,
    ) -> None:
        """Observe one committed state. Failures are isolated by the caller."""
        payload = envelope.payload_inline or {}
        lineage = payload.get("world_inquiry_lineage") if isinstance(payload.get("world_inquiry_lineage"), Mapping) else {}
        source_inquiry_id = str(payload.get("source_inquiry_id") or lineage.get("source_inquiry_id") or "")
        if source_inquiry_id:
            # Reality closes the originating cycle and is never allowed to
            # synchronously spawn its successor (hard anti-self-excitation).
            self._close_from_reality(envelope, snapshot, source_inquiry_id)
            return
        with self._lock:
            records = self._records(snapshot.state.scope.world_scope_hash)
            open_count = sum(str(row.get("status") or "") != "CLOSED" for row in records)
            if open_count >= self._max_open:
                return
            prior_dedup = {str(row.get("dedup_key") or "") for row in records}
            gaps = self._gaps.generate(snapshot)
            for gap in gaps[:1]:
                now_ms = envelope.source_time.recorded_at_ms
                curiosity = self._curiosity.build_curiosity(
                    gap,
                    frame_ref=snapshot.state.frame_ref,
                    created_at_ms=now_ms,
                    expires_at_ms=now_ms + 60_000,
                )
                inquiry = self._curiosity.build_inquiry(
                    gap,
                    curiosity,
                    correlation_id=envelope.correlation_id,
                    source_world_state_ref=snapshot.state_ref,
                    inquiry_budget_remaining=max(0, self._max_open - open_count),
                )
                if inquiry.dedup_key in prior_dedup:
                    return
                family_key = self._family(inquiry)
                gain_observations = []
                for row in records:
                    if str(row.get("family_key") or "") != family_key or not isinstance(row.get("outcome"), Mapping):
                        continue
                    try:
                        outcome = InquiryOutcome.model_validate(row["outcome"], strict=False)
                        gain_observations.append(InquiryGainObservation.from_outcome(outcome, family_key=family_key))
                    except ValueError:
                        continue
                backoff = derive_inquiry_backoff(
                    tuple(gain_observations),
                    family_key=family_key,
                    life_id=inquiry.scope.life_id,
                    world_scope_hash=inquiry.scope.world_scope_hash,
                    now_ms=now_ms,
                )
                signals = InquiryAdmissionSignals(
                    user_relevance_milli=gap.relevance_milli,
                    novelty_milli=900,
                    actionability_milli=900,
                    cost_milli=curiosity.expected_cost_milli,
                    risk_milli=0,
                    duplicate_milli=0,
                    privacy_cost_milli=0,
                    runtime_pressure_milli=0,
                    uncertainty_milli=gap.uncertainty_milli // 4,
                    time_remaining_ms=60_000,
                    inquiry_count_remaining=max(0, self._max_open - open_count),
                    privacy_allowed=snapshot.state.scope.privacy_scope == "system",
                    backoff_remaining_ms=backoff.backoff_remaining_ms,
                    prior_zero_gain_count=backoff.consecutive_zero_gain,
                )
                admission = InquiryAdmission().evaluate(inquiry, signals, charge=False)
                if admission.disposition != "ADMITTED":
                    return
                record = {
                    "record_id": inquiry.inquiry_id,
                    "revision": 1,
                    "world_scope_hash": inquiry.scope.world_scope_hash,
                    "dedup_key": inquiry.dedup_key,
                    "family_key": family_key,
                    "status": "ADMITTED",
                    "source_world_state_id": snapshot.state.world_state_id,
                    "admission": asdict(admission),
                    "inquiry": inquiry.model_dump(mode="json"),
                    "created_at_ms": now_ms,
                }
                self._store.put_active_cognition_record(record)
                accepted = self._dispatcher(
                    inquiry,
                    lambda event, inquiry_id=inquiry.inquiry_id: self._dispatch_event(inquiry_id, event),
                )
                if not accepted:
                    current = self._store.active_cognition_record(inquiry.inquiry_id)
                    if current is not None:
                        self._close_without_reality(
                            self._next(current, reason_code="GATEWAY_BUSY"),
                            decision="DEFER",
                            closed_at_ms=now_ms,
                        )
                return


__all__ = ["ActiveInquiryDispatcher", "ActiveWorldCognitionCoordinator"]
