"""Crash-safe delivery Outbox assembly and dispatcher for the total gateway."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    DeliveryReceipt,
    DeliveryTicketPayload,
    OutboundPlan,
    canonical_json_bytes,
    canonical_sha256,
    grant_from_outbound_part,
)

from .communication_client import CommunicationClientError, CommunicationControlClient
from .completion_gate import CompletionGate, CompletionRequirements
from .continuity import persist_terminal_completion, persist_working_checkpoint
from .object_store import ContentAddressedObjectStore
from .store import GatewayStateStore, OutboxDispatchBoundary, OutboxRecord


class DeliveryOutboxError(RuntimeError):
    def __init__(self, code: str, *, ambiguous: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


class DeliveryOutboxPayload(BaseModel):
    """Immutable delivery plan plus the facts needed to finalize its request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.delivery-outbox.v2"] = (
        "tiangong.gateway.delivery-outbox.v2"
    )
    life_id: str = Field(min_length=1, max_length=160)
    session_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    plan: OutboundPlan
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if not self.plan.has_valid_plan_sha256():
            raise ValueError("delivery outbox plan digest is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"payload_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.payload_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"payload_sha256": self.computed_sha256()})


def build_delivery_outbox_payload(
    plan: OutboundPlan,
    *,
    life_id: str,
    session_scope_hash: str,
    execution_effect_id: str,
) -> DeliveryOutboxPayload:
    return DeliveryOutboxPayload(
        life_id=life_id,
        session_scope_hash=session_scope_hash,
        execution_effect_id=execution_effect_id,
        plan=plan,
        payload_sha256="0" * 64,
    ).with_computed_sha256()


class GatewayDeliveryOutboxWorker:
    """Dispatch one durable delivery at a time and recover finalization idempotently."""

    def __init__(
        self,
        *,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        facts,
        authority,
        component_manifest,
        communication: CommunicationControlClient,
        gateway_epoch: int,
        worker_id: str,
        advance: Callable[..., object],
        repair_dispatch: Callable[..., object] | None = None,
    ) -> None:
        self._store = store
        self._objects = objects
        self._facts = facts
        self._authority = authority
        self._components = component_manifest
        self._communication = communication
        self._epoch = gateway_epoch
        self._worker_id = worker_id
        self._advance = advance
        # P0-3: optional bridge to the EXISTING runtime for artifact-only
        # channel repairs. When absent NOTHING is auto-repaired here —
        # the delivery boundary is never blindly replayed.
        self._repair_dispatch = repair_dispatch

    def _run_channel_repair_loop(self, *, active_plan, readiness, artifacts):
        """P0-3: channel-side repair with delivery-boundary safety.

        Both finalization paths run AFTER the delivery side effect left
        the process (receipt = confirmed delivered; ambiguous = unknown).
        Therefore:
        - effect / repository subjects are NEVER auto-repaired here —
          replaying an external side effect is forbidden;
        - artifact subjects may only be repaired through an injected
          EXISTING-runtime bridge (artifact content repair does not
          touch the delivery side effect); without the bridge nothing
          executes and the REPAIR disposition keeps the request
          IN_PROGRESS at the Gate;
        - the delivery itself is never re-sent by this loop.
        Returns (final_readiness, disposition, failure_evidence).
        """
        from total_gateway.verification_plan_executor import (
            VerificationPlanExecutor,
        )
        from total_gateway.verification_repair_coordinator import (
            RepairDispatchResult,
            VerificationRepairCoordinator,
        )
        from total_gateway.orchestration import _verification_snapshot

        coordinator = VerificationRepairCoordinator(store=self._store)

        def _dispatch(directive):
            if self._repair_dispatch is None:
                # Unreachable when kinds=(): kept as a fail-closed guard.
                return RepairDispatchResult(
                    execution_outcome="EXECUTION_FAILED",
                    produced_subject_identity=(
                        directive.effective_subject_identity
                    ),
                    execution_effect_ids=(),
                )
            return self._repair_dispatch(directive)

        def _reverify():
            executor = VerificationPlanExecutor(
                snapshot=_verification_snapshot(
                    self._store, active_plan.registry_snapshot_sha256,
                ),
                store=self._store,
                object_store=self._objects,
                fact_ledger=self._facts,
                plan=active_plan,
            )
            return executor.execute(
                evaluated_at_ms=time.time_ns() // 1_000_000,
                artifact_manifests=tuple(artifacts),
            )

        dispatchable = (
            ("artifact",) if self._repair_dispatch is not None else ()
        )
        readiness, disposition = coordinator.execute_repair_loop(
            plan=active_plan,
            readiness=readiness,
            dispatch=_dispatch,
            reverify=_reverify,
            dispatchable_subject_kinds=dispatchable,
        )
        evidence = None
        if disposition is not None:
            evidence = self._store.get_verification_failure_evidence_by_id(
                disposition.failure_evidence_id
            )
        return readiness, disposition, evidence

    def _load_payload(self, record: OutboxRecord) -> DeliveryOutboxPayload:
        intent = record.intent
        if (
            intent.intent_kind != "DELIVERY"
            or intent.destination_component_id != "tiangong-communication-service"
        ):
            raise DeliveryOutboxError("delivery_outbox.intent.invalid")
        reference = self._objects.get_reference(intent.payload_object_id)
        if reference is None or reference.sha256 != intent.payload_sha256:
            raise DeliveryOutboxError("delivery_outbox.payload.reference_invalid")
        raw = self._objects.read_bytes(intent.payload_object_id)
        if hashlib.sha256(raw).hexdigest() != intent.payload_sha256:
            raise DeliveryOutboxError("delivery_outbox.payload.bytes_invalid")
        try:
            payload = DeliveryOutboxPayload.model_validate_json(raw, strict=True)
        except ValueError as exc:
            raise DeliveryOutboxError("delivery_outbox.payload.invalid") from exc
        if canonical_json_bytes(payload.model_dump(mode="json")) != raw or not payload.has_valid_sha256():
            raise DeliveryOutboxError("delivery_outbox.payload.noncanonical")
        plan = payload.plan
        if (
            plan.effect_id != intent.effect_id
            or plan.request_id != intent.request_id
            or plan.run_id != intent.run_id
            or plan.generation != intent.generation
        ):
            raise DeliveryOutboxError("delivery_outbox.payload.binding_mismatch")
        entry = self._store.get_request_entry(plan.request_id)
        if entry is None or entry.session_scope_hash != payload.session_scope_hash:
            raise DeliveryOutboxError("delivery_outbox.session.binding_mismatch")
        effect = self._store.get_effect(payload.execution_effect_id)
        generation = self._store.get_generation(plan.request_id)
        if (
            effect is None
            or effect.state != "SUCCEEDED"
            or effect.claim.request_id != plan.request_id
            or effect.claim.run_id != plan.run_id
            or effect.claim.generation != plan.generation
            or generation is None
            or generation.status != "ACTIVE"
            or generation.gateway_epoch != self._epoch
            or generation.owner_instance_id != self._worker_id
            or generation.run_id != plan.run_id
            or generation.generation != plan.generation
        ):
            raise DeliveryOutboxError("delivery_outbox.execution_fact.missing")
        return payload

    def _continuity_context(
        self,
        payload: DeliveryOutboxPayload,
        *,
        observed_at_ms: int,
    ) -> tuple[str, str]:
        plan = payload.plan
        envelope = self._store.get_request_envelope(plan.request_id)
        if envelope is None:
            raise DeliveryOutboxError("delivery_outbox.continuity.request_missing")
        active = self._store.get_active_request_capsule(
            plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
        )
        if active is None:
            active = persist_working_checkpoint(
                self._store,
                life_id=payload.life_id,
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
                user_goal=envelope.text,
                active_plan=("reconcile the durable channel delivery",),
                pending_effect_ids=(plan.effect_id,),
                latest_safe_step="delivery plan and dispatch result are durably recorded",
                next_step="evaluate the channel-independent completion gate",
                recovery_preconditions=("do not resend an ambiguous channel effect",),
                created_at_ms=observed_at_ms,
            )
        if active.capsule.life_id != payload.life_id:
            raise DeliveryOutboxError("delivery_outbox.continuity.life_mismatch")
        return payload.life_id, envelope.text

    def _issue_ticket(self, plan: OutboundPlan, *, issued_at_ms: int):
        text_count = sum(part.kind == "text" for part in plan.parts)
        file_count = sum(part.kind == "artifact" for part in plan.parts)
        payload = DeliveryTicketPayload(
            ticket_id="delivery-ticket-"
            + canonical_sha256(
                {
                    "effect_id": plan.effect_id,
                    "gateway_epoch": self._epoch,
                    "issued_at_ms": issued_at_ms,
                }
            ),
            issued_at_ms=issued_at_ms,
            not_before_ms=issued_at_ms,
            expires_at_ms=issued_at_ms + 60_000,
            gateway_epoch=self._epoch,
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
            delivery_id=plan.delivery_id,
            effect_id=plan.effect_id,
            channel=plan.channel,
            tenant_id=plan.tenant_id,
            link_account_id=plan.link_account_id,
            conversation_ref=plan.conversation_ref,
            conversation_scope_hash=plan.conversation_scope_hash,
            recipient_scope_hash=plan.recipient_scope_hash,
            reply_to_message_ref=plan.reply_to_message_ref,
            outbound_plan_id=plan.outbound_plan_id,
            outbound_plan_sha256=plan.plan_sha256,
            channel_policy_hash=plan.channel_policy_hash,
            component_manifest_hash=self._components.manifest_sha256,
            allow_text=bool(text_count),
            allow_files=bool(file_count),
            max_text_parts=text_count,
            max_file_parts=file_count,
            upload_timeout_ms=3_600_000,
            send_timeout_ms=60_000,
            parts=tuple(grant_from_outbound_part(part) for part in plan.parts),
        )
        return self._authority.delivery_signer.sign_delivery(payload)

    def _put_result(self, payload: DeliveryOutboxPayload, value: dict[str, object], *, at_ms: int):
        raw = canonical_json_bytes(value)
        return self._objects.put_bytes(
            raw,
            kind="payload",
            tenant_id=payload.plan.tenant_id,
            link_account_id=payload.plan.link_account_id,
            conversation_scope_hash=payload.plan.conversation_scope_hash,
            created_at_ms=at_ms,
        ).reference

    def _abandon_cancelled_outbox(
        self, record: OutboxRecord, *, reason: str, now_ms: int
    ) -> None:
        """Settle an outbox intent whose generation was cancelled as AMBIGUOUS.

        The payload failed validation only because its execution fact (the
        ACTIVE generation) is gone; the payload JSON itself parsed fine, so
        re-read it for the object-store scope binding and record a terminal
        ambiguity result instead of retrying the claim forever.
        """
        raw = self._objects.read_bytes(record.intent.payload_object_id)
        document = json.loads(raw.decode("utf-8"))
        plan = document.get("plan") if isinstance(document, dict) else None
        if not isinstance(plan, dict):
            raise DeliveryOutboxError("delivery_outbox.payload.unreadable")
        result_raw = canonical_json_bytes(
            {
                "kind": "error",
                "outbox_id": record.intent.outbox_id,
                "reason_code": "delivery_outbox.generation_cancelled",
                "detail": reason[:200],
            }
        )
        reference = self._objects.put_bytes(
            result_raw,
            kind="payload",
            tenant_id=str(plan.get("tenant_id") or ""),
            link_account_id=str(plan.get("link_account_id") or ""),
            conversation_scope_hash=str(plan.get("conversation_scope_hash") or ""),
            created_at_ms=now_ms,
        ).reference
        self._store.mark_expired_outbox_ambiguous(
            record.intent.outbox_id,
            observed_at_ms=now_ms,
            result_object_id=reference.object_id,
            result_sha256=reference.sha256,
        )

    def _record_error_result(
        self,
        record: OutboxRecord,
        payload: DeliveryOutboxPayload,
        *,
        code: str,
        ambiguous: bool,
        observed_at_ms: int,
        orphaned: bool = False,
    ) -> None:
        reference = self._put_result(
            payload,
            {
                "ambiguous": ambiguous,
                "kind": "error",
                "outbox_id": record.intent.outbox_id,
                "reason_code": code,
            },
            at_ms=observed_at_ms,
        )
        if orphaned:
            self._store.mark_expired_outbox_ambiguous(
                record.intent.outbox_id,
                observed_at_ms=observed_at_ms,
                result_object_id=reference.object_id,
                result_sha256=reference.sha256,
            )
        else:
            self._store.record_outbox_dispatch_result(
                record.intent.outbox_id,
                worker_id=self._worker_id,
                outcome="AMBIGUOUS" if ambiguous else "ACKED",
                result_object_id=reference.object_id,
                result_sha256=reference.sha256,
                completed_at_ms=observed_at_ms,
            )

    def dispatch_next(self, *, now_ms: int) -> bool:
        pending_finalization = self._store.list_unfinalized_outbox_results(limit=1)
        if pending_finalization:
            self._finalize(pending_finalization[0], now_ms=now_ms)
            return True
        candidates = self._store.list_dispatchable_outbox(now_ms=now_ms, limit=1)
        if not candidates:
            return False
        candidate = candidates[0]
        boundary = self._store.get_outbox_dispatch_boundary(candidate.intent.outbox_id)
        if candidate.state == "CLAIMED" and boundary is not None:
            payload = self._load_payload(candidate)
            observed_at_ms = max(now_ms, boundary.started_at_ms)
            self._record_error_result(
                candidate,
                payload,
                code="delivery_outbox.receipt_missing_after_restart",
                ambiguous=True,
                observed_at_ms=observed_at_ms,
                orphaned=True,
            )
            recovered = self._store.get_outbox_dispatch_boundary(candidate.intent.outbox_id)
            assert recovered is not None
            self._finalize(recovered, now_ms=observed_at_ms)
            return True

        claimed = self._store.claim_outbox(
            candidate.intent.outbox_id,
            worker_id=self._worker_id,
            now_ms=now_ms,
            lease_ms=120_000,
        )
        try:
            payload = self._load_payload(claimed)
        except DeliveryOutboxError as exc:
            # A cancelled/fenced generation can never become ACTIVE again,
            # so this intent is permanently undispatchable. Settle it as
            # AMBIGUOUS here; raising instead would loop the orchestration
            # worker on the same record every 120s lease expiry forever.
            generation = self._store.get_generation(claimed.intent.request_id)
            if generation is not None and generation.status == "ACTIVE":
                raise
            self._abandon_cancelled_outbox(claimed, reason=str(exc), now_ms=now_ms)
            return True
        ticket = self._issue_ticket(payload.plan, issued_at_ms=now_ms)
        ticket_raw = canonical_json_bytes(ticket.model_dump(mode="json"))
        ticket_reference = self._objects.put_bytes(
            ticket_raw,
            kind="payload",
            tenant_id=payload.plan.tenant_id,
            link_account_id=payload.plan.link_account_id,
            conversation_scope_hash=payload.plan.conversation_scope_hash,
            created_at_ms=now_ms,
        ).reference
        self._communication.install_delivery_authority(
            self._authority.delivery_trust_bundle(
                gateway_epoch=self._epoch,
                now_ms=now_ms,
            ),
            self._components,
        )
        delivery_entity = "delivery-" + payload.plan.run_id
        self._advance("delivery", delivery_entity, "TICKET_ISSUED", now_ms=now_ms)
        self._advance("delivery", delivery_entity, "SENDING", now_ms=now_ms)
        self._store.mark_outbox_dispatch_started(
            claimed.intent.outbox_id,
            worker_id=self._worker_id,
            gateway_epoch=self._epoch,
            ticket_object_id=ticket_reference.object_id,
            ticket_sha256=ticket_reference.sha256,
            started_at_ms=now_ms,
        )
        try:
            receipt = self._communication.dispatch_delivery(ticket, payload.plan)
            observed_at_ms = max(now_ms, receipt.observed_at_ms)
            result_reference = self._put_result(
                payload,
                {
                    "kind": "receipt",
                    "outbox_id": claimed.intent.outbox_id,
                    "receipt": receipt.model_dump(mode="json"),
                },
                at_ms=observed_at_ms,
            )
            self._store.record_outbox_dispatch_result(
                claimed.intent.outbox_id,
                worker_id=self._worker_id,
                outcome=(
                    "AMBIGUOUS"
                    if receipt.status in {"AMBIGUOUS", "RECONCILE_REQUIRED"}
                    else "ACKED"
                ),
                result_object_id=result_reference.object_id,
                result_sha256=result_reference.sha256,
                completed_at_ms=observed_at_ms,
            )
        except CommunicationClientError as exc:
            observed_at_ms = time.time_ns() // 1_000_000
            self._record_error_result(
                claimed,
                payload,
                code=exc.code,
                ambiguous=exc.ambiguous,
                observed_at_ms=observed_at_ms,
            )
        completed = self._store.get_outbox_dispatch_boundary(claimed.intent.outbox_id)
        assert completed is not None
        self._finalize(completed, now_ms=max(now_ms, completed.completed_at_ms or now_ms))
        return True

    def _advance_ambiguity(
        self,
        delivery_entity: str,
        *,
        observed_at_ms: int,
        fact_id: str,
        evidence_sha256: str,
    ) -> None:
        snapshot = self._store.get_snapshot("delivery", delivery_entity)
        if snapshot is None:
            raise DeliveryOutboxError("delivery_outbox.delivery_state.missing")
        if snapshot.state not in {"AMBIGUOUS", "RECONCILE_REQUIRED"}:
            self._advance(
                "delivery",
                delivery_entity,
                "AMBIGUOUS",
                now_ms=observed_at_ms,
                fact_id=fact_id,
                evidence_sha256=evidence_sha256,
            )
        self._advance(
            "delivery",
            delivery_entity,
            "RECONCILE_REQUIRED",
            now_ms=observed_at_ms,
            fact_id=fact_id,
            evidence_sha256=evidence_sha256,
        )

    def _finalize(self, boundary: OutboxDispatchBoundary, *, now_ms: int) -> None:
        record = self._store.get_outbox(boundary.outbox_id)
        if record is None or boundary.result_object_id is None or boundary.result_sha256 is None:
            raise DeliveryOutboxError("delivery_outbox.result.missing")
        payload = self._load_payload(record)
        raw = self._objects.read_bytes(boundary.result_object_id)
        if hashlib.sha256(raw).hexdigest() != boundary.result_sha256:
            raise DeliveryOutboxError("delivery_outbox.result.bytes_invalid")
        try:
            result = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise DeliveryOutboxError("delivery_outbox.result.invalid") from exc
        if not isinstance(result, dict) or canonical_json_bytes(result) != raw:
            raise DeliveryOutboxError("delivery_outbox.result.noncanonical")
        plan = payload.plan
        delivery_entity = "delivery-" + plan.run_id
        completed_at_ms = max(now_ms, boundary.completed_at_ms or now_ms)
        evidence_sha256 = boundary.result_sha256
        fact_id = "fact-delivery-" + evidence_sha256[:32]

        if result.get("kind") == "error":
            ambiguous = result.get("ambiguous") is True
            if ambiguous:
                self._advance_ambiguity(
                    delivery_entity,
                    observed_at_ms=completed_at_ms,
                    fact_id=fact_id,
                    evidence_sha256=evidence_sha256,
                )
            else:
                self._advance(
                    "delivery",
                    delivery_entity,
                    "FAILED_FINAL",
                    now_ms=completed_at_ms,
                    fact_id=fact_id,
                    evidence_sha256=evidence_sha256,
                )
            life_id, user_goal = self._continuity_context(
                payload, observed_at_ms=completed_at_ms
            )
            artifacts = tuple(
                part.artifact
                for part in plan.parts
                if part.kind == "artifact" and part.artifact is not None
            )
            text_parts = tuple(part.text for part in plan.parts if part.kind == "text")
            if len(text_parts) > 1:
                raise DeliveryOutboxError("delivery_outbox.text_parts.invalid")
            requirements = CompletionRequirements(
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
                text_required=bool(text_parts),
                required_execution_effect_ids=(payload.execution_effect_id,),
                required_artifact_revision_ids=tuple(
                    sorted(item.artifact_revision_id for item in artifacts)
                ),
                delivery_requirement="CHANNEL_ACCEPTED",
                verification_mode=(
                    "PLAN_BOUND"
                    if self._store.get_active_verification_plan(
                        request_id=plan.request_id,
                        run_id=plan.run_id,
                        generation=plan.generation,
                    ) is not None
                    else "NONE"
                ),
            )
            # M4.1 Final §10: production verification wiring — execute the
            # active plan BEFORE the gate reads readiness.
            active_plan = self._store.get_active_verification_plan(
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
            )
            verification_disposition = None
            verification_failure_evidence = None
            if active_plan is not None:
                from total_gateway.verification_plan_executor import (
                    VerificationPlanExecutor,
                )
                from total_gateway.orchestration import _verification_snapshot

                executor = VerificationPlanExecutor(
                    snapshot=_verification_snapshot(
                        self._store,
                        active_plan.registry_snapshot_sha256,
                    ),
                    store=self._store,
                    object_store=self._objects,
                    fact_ledger=self._facts,
                    plan=active_plan,
                )
                readiness = executor.execute(
                    evaluated_at_ms=completed_at_ms,
                    artifact_manifests=tuple(artifacts),
                )
                # P0-3: channel repair runs through the SAME loop with
                # delivery-boundary safety semantics (no replay).
                if not readiness.verification_ready:
                    (
                        readiness,
                        verification_disposition,
                        verification_failure_evidence,
                    ) = self._run_channel_repair_loop(
                        active_plan=active_plan,
                        readiness=readiness,
                        artifacts=artifacts,
                    )
            decision = CompletionGate(self._objects, self._facts, head_state_reader=self._store.get_effect_head_state).evaluate(
                requirements,
                candidate_text=text_parts[0] if text_parts else None,
                artifacts=artifacts,
                outbound_plan=plan,
                delivery_failure="AMBIGUOUS" if ambiguous else "FAILED_FINAL",
                active_plan=active_plan,
                verification_disposition=verification_disposition,
                verification_failure_evidence=verification_failure_evidence,
                disposition_authority_reader=(
                    self._store.get_verification_disposition_by_id
                ),
                verification_readiness=self._store.get_latest_verification_readiness(
                    request_id=plan.request_id,
                    run_id=plan.run_id,
                    generation=plan.generation,
                ),
            )
            if ambiguous:
                self._store.record_completion_decision(
                    decision, recorded_at_ms=completed_at_ms
                )
                checkpoint = persist_working_checkpoint(
                    self._store,
                    life_id=life_id,
                    request_id=plan.request_id,
                    run_id=plan.run_id,
                    generation=plan.generation,
                    user_goal=user_goal,
                    active_plan=("reconcile the unknown channel side effect",),
                    verified_fact_ids=(fact_id,),
                    artifact_refs=tuple(
                        sorted(item.artifact_revision_id for item in artifacts)
                    ),
                    pending_effect_ids=(plan.effect_id,),
                    latest_safe_step="channel side effect crossed its durable dispatch boundary",
                    next_step="query platform evidence without resending the effect",
                    recovery_preconditions=("the ambiguous effect must not be replayed",),
                    created_at_ms=completed_at_ms,
                )
                self._store.mark_outbox_finalized(
                    boundary.outbox_id,
                    finalized_at_ms=completed_at_ms,
                    finalization_sha256=canonical_sha256(
                        {
                            "completion_decision_sha256": decision.decision_sha256,
                            "continuity_capsule_sha256": checkpoint.capsule.capsule_sha256,
                            "outbox_id": boundary.outbox_id,
                            "result_sha256": evidence_sha256,
                        }
                    ),
                )
                return
            persist_terminal_completion(
                self._store,
                decision,
                life_id=life_id,
                user_goal=user_goal,
                final_result=f"任务未完成：{result.get('code') or 'delivery failed'}",
                created_at_ms=completed_at_ms,
                verified_fact_ids=(fact_id,),
                artifact_refs=tuple(
                    sorted(item.artifact_revision_id for item in artifacts)
                ),
            )
            request_snapshot = self._advance(
                "request",
                plan.request_id,
                "FAILED",
                now_ms=completed_at_ms,
                fact_id=fact_id,
                evidence_sha256=evidence_sha256,
            )
            self._store.complete_session_request(
                payload.session_scope_hash,
                plan.request_id,
                completed_at_ms=completed_at_ms,
            )
            finalization = canonical_sha256(
                {
                    "outbox_id": boundary.outbox_id,
                    "request_event_id": request_snapshot.last_event_id,
                    "result_sha256": evidence_sha256,
                }
            )
            self._store.mark_outbox_finalized(
                boundary.outbox_id,
                finalized_at_ms=completed_at_ms,
                finalization_sha256=finalization,
                release_generation=True,
            )
            return

        if result.get("kind") != "receipt" or result.get("outbox_id") != boundary.outbox_id:
            raise DeliveryOutboxError("delivery_outbox.result.kind_invalid")
        try:
            receipt = DeliveryReceipt.model_validate_json(
                canonical_json_bytes(result["receipt"]),
                strict=True,
            )
        except (KeyError, ValueError) as exc:
            raise DeliveryOutboxError("delivery_outbox.receipt.invalid") from exc
        if (
            not receipt.has_valid_receipt_sha256()
            or receipt.delivery_id != plan.delivery_id
            or receipt.effect_id != plan.effect_id
            or receipt.request_id != plan.request_id
            or receipt.run_id != plan.run_id
            or receipt.generation != plan.generation
        ):
            raise DeliveryOutboxError("delivery_outbox.receipt.binding_mismatch")

        if receipt.status in {"AMBIGUOUS", "RECONCILE_REQUIRED"}:
            self._advance_ambiguity(
                delivery_entity,
                observed_at_ms=completed_at_ms,
                fact_id=fact_id,
                evidence_sha256=receipt.receipt_sha256,
            )
        else:
            target = {
                "CHANNEL_ACCEPTED": "CHANNEL_ACCEPTED",
                "DELIVERED": "DELIVERED",
                "FAILED_RETRYABLE": "FAILED_RETRYABLE",
                "FAILED_FINAL": "FAILED_FINAL",
            }[receipt.status]
            self._advance(
                "delivery",
                delivery_entity,
                target,
                now_ms=completed_at_ms,
                fact_id=fact_id,
                evidence_sha256=receipt.receipt_sha256,
            )
        artifacts = tuple(
            part.artifact for part in plan.parts if part.kind == "artifact" and part.artifact is not None
        )
        text_parts = tuple(part.text for part in plan.parts if part.kind == "text")
        if len(text_parts) > 1:
            raise DeliveryOutboxError("delivery_outbox.text_parts.invalid")
        life_id, user_goal = self._continuity_context(
            payload, observed_at_ms=completed_at_ms
        )
        requirements = CompletionRequirements(
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
            text_required=bool(text_parts),
            required_execution_effect_ids=(payload.execution_effect_id,),
            required_artifact_revision_ids=tuple(
                sorted(item.artifact_revision_id for item in artifacts)
            ),
            delivery_requirement="CHANNEL_ACCEPTED",
            verification_mode=(
                "PLAN_BOUND"
                if self._store.get_active_verification_plan(
                    request_id=plan.request_id,
                    run_id=plan.run_id,
                    generation=plan.generation,
                ) is not None
                else "NONE"
            ),
        )
        # M4.1 Final §10: production verification wiring (receipt branch).
        active_plan = self._store.get_active_verification_plan(
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
        )
        verification_disposition = None
        verification_failure_evidence = None
        if active_plan is not None:
            from total_gateway.verification_plan_executor import (
                VerificationPlanExecutor,
            )
            from total_gateway.orchestration import _verification_snapshot

            executor = VerificationPlanExecutor(
                snapshot=_verification_snapshot(
                    self._store, active_plan.registry_snapshot_sha256,
                ),
                store=self._store,
                object_store=self._objects,
                fact_ledger=self._facts,
                plan=active_plan,
            )
            readiness = executor.execute(
                evaluated_at_ms=completed_at_ms,
                artifact_manifests=tuple(artifacts),
            )
            # P0-3: receipt branch — delivery side effect already
            # happened; the same no-replay repair loop applies.
            if not readiness.verification_ready:
                (
                    readiness,
                    verification_disposition,
                    verification_failure_evidence,
                ) = self._run_channel_repair_loop(
                    active_plan=active_plan,
                    readiness=readiness,
                    artifacts=artifacts,
                )
        decision = CompletionGate(self._objects, self._facts, head_state_reader=self._store.get_effect_head_state).evaluate(
            requirements,
            candidate_text=text_parts[0] if text_parts else None,
            artifacts=artifacts,
            outbound_plan=plan,
            delivery_receipt=receipt,
            active_plan=active_plan,
            verification_disposition=verification_disposition,
            verification_failure_evidence=verification_failure_evidence,
            disposition_authority_reader=(
                self._store.get_verification_disposition_by_id
            ),
            verification_readiness=self._store.get_latest_verification_readiness(
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
            ),
        )
        if decision.outcome in {"IN_PROGRESS", "RECONCILE_REQUIRED"}:
            self._store.record_completion_decision(
                decision, recorded_at_ms=completed_at_ms
            )
            checkpoint = persist_working_checkpoint(
                self._store,
                life_id=life_id,
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
                user_goal=user_goal,
                active_plan=("reconcile incomplete channel evidence",),
                verified_fact_ids=(fact_id,),
                artifact_refs=tuple(
                    sorted(item.artifact_revision_id for item in artifacts)
                ),
                pending_effect_ids=(plan.effect_id,),
                latest_safe_step="channel receipt is durably recorded but completion is unproven",
                next_step="obtain missing platform evidence without duplicating delivery",
                recovery_preconditions=("do not claim completion before the gate passes",),
                created_at_ms=completed_at_ms,
            )
            self._store.mark_outbox_finalized(
                boundary.outbox_id,
                finalized_at_ms=completed_at_ms,
                finalization_sha256=canonical_sha256(
                    {
                        "completion_decision_sha256": decision.decision_sha256,
                        "continuity_capsule_sha256": checkpoint.capsule.capsule_sha256,
                        "outbox_id": boundary.outbox_id,
                        "result_sha256": evidence_sha256,
                    }
                ),
            )
            return
        request_state = {
            "COMPLETED": "COMPLETED",
            "PARTIAL": "PARTIAL",
            "FAILED": "FAILED",
        }[decision.outcome]
        final_text = text_parts[0] if text_parts else (
            f"交付结果：{decision.outcome.lower()}，已验证 {len(artifacts)} 个文件。"
        )
        persist_terminal_completion(
            self._store,
            decision,
            life_id=life_id,
            user_goal=user_goal,
            final_result=final_text,
            created_at_ms=completed_at_ms,
            verified_fact_ids=(fact_id,),
            artifact_refs=tuple(
                sorted(item.artifact_revision_id for item in artifacts)
            ),
        )
        request_snapshot = self._advance(
            "request",
            plan.request_id,
            request_state,
            now_ms=completed_at_ms,
            fact_id="fact-completion-" + decision.decision_sha256[:32],
            evidence_sha256=decision.decision_sha256,
        )
        self._store.complete_session_request(
            payload.session_scope_hash,
            plan.request_id,
            completed_at_ms=completed_at_ms,
        )
        self._store.mark_outbox_finalized(
            boundary.outbox_id,
            finalized_at_ms=completed_at_ms,
            finalization_sha256=canonical_sha256(
                {
                    "completion_decision_sha256": decision.decision_sha256,
                    "outbox_id": boundary.outbox_id,
                    "request_event_id": request_snapshot.last_event_id,
                    "result_sha256": evidence_sha256,
                }
            ),
            release_generation=True,
        )


__all__ = [
    "DeliveryOutboxError",
    "DeliveryOutboxPayload",
    "GatewayDeliveryOutboxWorker",
    "build_delivery_outbox_payload",
]
