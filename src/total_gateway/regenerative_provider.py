"""Gateway-owned adapter for P18-M2 regenerative execution.

The embedded backend may *request* ledger/effect/checkpoint operations through
this adapter, but it cannot own or open persistence.  Every mutation lands in
the already-open GatewayStateStore and is fenced to the authoritative
Request/Run/Generation plus the immutable task/authority binding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from contracts.verification import RuntimeCloseoutEvidence


def _closeout_digest(value: object) -> str:
    """Coerce a payload hash field into a 64-hex digest (zero if absent).

    The runtime payload's root_goal_hash / task_contract_hash may be
    missing or malformed; the closeout evidence requires a valid Sha256
    so unknown values degrade to an explicit zero digest rather than
    fabricating a hash that was never observed.
    """
    text = str(value or "").strip().lower()
    if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
        return text
    return "0" * 64
from contracts import canonical_sha256, derive_effect_identity

from .diagnostics import diagnostic_log
from .effects import EffectClaim, EffectResult
from .regenerative_execution import (
    CHECKPOINT_SCHEMA_VERSION,
    ExecutionFrontier,
    derive_attempt_id,
    derive_logical_effect_id,
    derive_step_id,
)
from .regenerative_governance import (
    evaluate_checkpoint_version_compatibility,
    version_vector_from_mapping,
)
from .store import GatewayStateStore, StoreConflictError, StoreCorruptionError


_PROVIDER_SCHEMA = "tiangong.gateway.regenerative-provider.v1"
_TERMINAL_COMMITTED_EFFECT_STATES = frozenset({"SUCCEEDED", "RECONCILED"})
_RECONCILE_REQUIRED_EFFECT_STATES = frozenset({"SIDE_EFFECT_STARTED", "AMBIGUOUS"})
# Effects of this pipeline claim this version; the shared ledger also holds
# orchestrator run-boundary effects ("unspecified") and omni admission
# sub-effects ("tiangong.omni-grant-authority.v1"), which the execution
# frontier does not govern.
_REGENERATIVE_PIPELINE_VERSION = "p18-m2-regenerative-effect-v1"


def _text(value: Any, *, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{label} is required")
    return result


def _integer(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} is invalid")
    return value


def _hash(value: Any, *, label: str) -> str:
    result = _text(value, label=label).lower()
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{label} must be sha256")
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_frontier(value: Any) -> ExecutionFrontier:
    if not isinstance(value, Mapping):
        raise ValueError("frontier is required")
    raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    frontier = ExecutionFrontier.model_validate_json(raw, strict=True)
    if not frontier.has_valid_hash():
        raise ValueError("frontier checksum is invalid")
    return frontier


def authority_hash(execution_ticket_id: str) -> str:
    return canonical_sha256({
        "domain": "tiangong.gateway.regenerative-authority.v1",
        "execution_ticket_id": _text(execution_ticket_id, label="execution ticket"),
    })


@dataclass(frozen=True)


class _Identity:
    request_id: str
    run_id: str
    generation: int
    life_id: str
    execution_ticket_id: str

    @property
    def authority_hash(self) -> str:
        return authority_hash(self.execution_ticket_id)


class RegenerativeExecutionAuthority:
    """Thin request dispatcher backed by one existing GatewayStateStore."""

    def __init__(self, store: GatewayStateStore) -> None:
        self._store = store

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("regenerative execution payload must be a mapping")
        operation = _text(payload.get("operation"), label="operation")
        handlers = {
            "initialize": self._initialize,
            "append_event": self._append_event,
            "prepare_effect": self._prepare_effect,
            "start_effect": self._start_effect,
            "finish_effect": self._finish_effect,
            "reconcile_effect": self._reconcile_effect,
            "update_frontier": self._update_frontier,
            "commit_checkpoint": self._commit_checkpoint,
            "recover": self._recover,
            "verify_completion": self._verify_completion,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise ValueError(f"unsupported regenerative operation: {operation}")
        result = handler(payload)
        return {"schema": _PROVIDER_SCHEMA, "operation": operation, **result}

    def _identity(self, payload: Mapping[str, Any]) -> _Identity:
        identity = _Identity(
            request_id=_text(payload.get("request_id"), label="request_id"),
            run_id=_text(payload.get("run_id"), label="run_id"),
            generation=_integer(payload.get("generation"), label="generation"),
            life_id=_text(payload.get("life_id"), label="life_id"),
            execution_ticket_id=_text(
                payload.get("outer_execution_ticket_id"), label="outer_execution_ticket_id"
            ),
        )
        binding = self._store.get_request_generation_binding(identity.request_id)
        if binding is None:
            raise StoreConflictError("request has no active generation authority")
        if (
            str(binding["run_id"]) != identity.run_id
            or int(binding["current_generation"]) != identity.generation
            or str(binding["status"]) != "ACTIVE"
        ):
            raise StoreConflictError("request/run/generation authority is stale")
        return identity

    def _bound_identity(self, payload: Mapping[str, Any]) -> tuple[_Identity, dict[str, Any]]:
        identity = self._identity(payload)
        contract = self._store.get_execution_task_contract(
            identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
        )
        if contract is None:
            raise StoreConflictError("regenerative task contract has not been initialized")
        if (
            str(contract["life_id"]) != identity.life_id
            or str(contract["authority_hash"]) != identity.authority_hash
        ):
            raise StoreConflictError("regenerative authority binding changed")
        return identity, contract

    def _initialize(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity = self._identity(payload)
        root_goal_hash = _hash(payload.get("root_goal_hash"), label="root_goal_hash")
        task_contract_hash = _hash(payload.get("task_contract_hash"), label="task_contract_hash")
        now_ms = _integer(payload.get("now_ms"), label="now_ms")
        created = self._store.bind_execution_task_contract(
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            life_id=identity.life_id,
            root_goal_hash=root_goal_hash,
            task_contract_hash=task_contract_hash,
            authority_hash=identity.authority_hash,
            bound_at_ms=now_ms,
        )
        event, event_created = self._store.append_execution_event(
            event_key="chain.started",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=_integer(payload.get("epoch_index", 0), label="epoch_index"),
            event_type="chain.started",
            created_at_ms=now_ms,
            payload={
                "root_goal_hash": root_goal_hash,
                "task_contract_hash": task_contract_hash,
                "authority_hash": identity.authority_hash,
                "life_id": identity.life_id,
            },
        )
        return {
            "initialized": True,
            "contract_created": created,
            "event_created": event_created,
            "ledger_seq": event.ledger_seq,
            "root_goal_hash": root_goal_hash,
            "task_contract_hash": task_contract_hash,
            "authority_hash": identity.authority_hash,
        }

    def _append_event(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, _contract = self._bound_identity(payload)
        event, created = self._store.append_execution_event(
            event_key=_text(payload.get("event_key"), label="event_key"),
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=_integer(payload.get("epoch_index", 0), label="epoch_index"),
            event_type=_text(payload.get("event_type"), label="event_type"),
            created_at_ms=_integer(payload.get("now_ms"), label="now_ms"),
            payload=dict(_mapping(payload.get("payload"))),
            logical_effect_id=(str(payload.get("logical_effect_id") or "").strip() or None),
            attempt_id=(str(payload.get("attempt_id") or "").strip() or None),
            step_id=(str(payload.get("step_id") or "").strip() or None),
            effect_id=(str(payload.get("effect_id") or "").strip() or None),
            causal_parent_event_id=(str(payload.get("causal_parent_event_id") or "").strip() or None),
        )
        return {
            "created": created,
            "event_id": event.event_id,
            "ledger_seq": event.ledger_seq,
            "event_hash": event.event_hash,
        }

    def _effect_identity(self, payload: Mapping[str, Any]) -> tuple[_Identity, dict[str, Any], str, str, str, int, int, str, str]:
        identity, contract = self._bound_identity(payload)
        logical_effect_id = _text(payload.get("logical_effect_id"), label="logical_effect_id")
        expected_logical = derive_logical_effect_id(
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            obligation_key=_text(payload.get("obligation_key"), label="obligation_key"),
            effect_namespace=_text(payload.get("effect_namespace"), label="effect_namespace"),
            normalized_target=_text(payload.get("normalized_target"), label="normalized_target"),
            desired_postcondition_sha256=_hash(
                payload.get("desired_postcondition_sha256"), label="desired_postcondition_sha256"
            ),
        )
        if logical_effect_id != expected_logical:
            raise StoreConflictError("logical effect ID changed for the same immutable intent")
        attempt = _integer(payload.get("attempt", 1), label="attempt", minimum=1)
        global_step = _integer(payload.get("global_step"), label="global_step")
        attempt_id = derive_attempt_id(logical_effect_id=logical_effect_id, attempt=attempt)
        step_id = derive_step_id(
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            global_step=global_step,
            logical_effect_id=logical_effect_id,
        )
        generation_binding = self._store.get_request_generation_binding(identity.request_id)
        if generation_binding is None:
            raise StoreConflictError("request generation binding disappeared")
        run_sequence = int(generation_binding["run_sequence"])
        physical_key = canonical_sha256({
            "domain": "tiangong.gateway.physical-effect-attempt.v1",
            "logical_effect_id": logical_effect_id,
            "attempt_id": attempt_id,
            "step_id": step_id,
        })
        ordinal = int(physical_key[:8], 16) & 0x7fffffff
        intent_sha = canonical_sha256({
            "domain": "tiangong.gateway.physical-effect-intent.v1",
            "logical_effect_id": logical_effect_id,
            "attempt_id": attempt_id,
            "step_id": step_id,
        })
        effect_id = derive_effect_identity(
            request_id=identity.request_id,
            run_id=identity.run_id,
            run_sequence=run_sequence,
            generation=identity.generation,
            effect_kind="execution",
            ordinal=ordinal,
            intent_sha256=intent_sha,
        ).effect_id
        return (
            identity, contract, logical_effect_id, effect_id, intent_sha,
            run_sequence, ordinal, attempt_id, step_id,
        )

    def _logical_effect_disposition(
        self, identity: _Identity, logical_effect_id: str
    ) -> tuple[str | None, Any | None]:
        unresolved_started: dict[str, Any] = {}
        unresolved_ambiguous: dict[str, Any] = {}
        committed = None
        for event in self._store.list_execution_events(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        ):
            if event.logical_effect_id != logical_effect_id:
                continue
            effect_id = str(event.effect_id or "")
            if event.event_type == "step.dispatched" and effect_id:
                unresolved_started[effect_id] = event
            elif event.event_type == "step.committed":
                committed = event
                if effect_id:
                    unresolved_started.pop(effect_id, None)
                    unresolved_ambiguous.pop(effect_id, None)
            elif event.event_type == "step.failed" and effect_id:
                unresolved_started.pop(effect_id, None)
                unresolved_ambiguous.pop(effect_id, None)
            elif event.event_type == "step.ambiguous" and effect_id:
                unresolved_started.pop(effect_id, None)
                unresolved_ambiguous[effect_id] = event
            elif event.event_type == "step.reconciled" and effect_id:
                verdict = str(event.payload.get("verdict") or "").upper()
                if verdict == "APPLIED":
                    committed = event
                    unresolved_started.pop(effect_id, None)
                    unresolved_ambiguous.pop(effect_id, None)
                elif verdict == "PROVEN_NOT_APPLIED":
                    unresolved_started.pop(effect_id, None)
                    unresolved_ambiguous.pop(effect_id, None)
        if committed is not None:
            return "already_committed", committed
        if unresolved_ambiguous:
            return "reconcile_required", list(unresolved_ambiguous.values())[-1]
        if unresolved_started:
            return "in_flight", list(unresolved_started.values())[-1]
        return None, None

    def _prepare_effect(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        (
            identity, _contract, logical_effect_id, effect_id, intent_sha,
            run_sequence, ordinal, attempt_id, step_id,
        ) = self._effect_identity(payload)
        now_ms = _integer(payload.get("now_ms"), label="now_ms")
        attempt = _integer(payload.get("attempt", 1), label="attempt", minimum=1)
        prior_disposition, prior_event = self._logical_effect_disposition(identity, logical_effect_id)
        if prior_disposition is not None:
            prior_effect_id = str(getattr(prior_event, "effect_id", "") or "") or effect_id
            event, _ = self._store.append_execution_event(
                event_key=f"step.prepared:{step_id}:{attempt}",
                request_id=identity.request_id, run_id=identity.run_id,
                generation=identity.generation,
                epoch_index=_integer(payload.get("epoch_index", 0), label="epoch_index"),
                event_type="step.prepared", created_at_ms=now_ms,
                payload={
                    "disposition": prior_disposition,
                    "effect_state": ("LOGICAL_COMMITTED" if prior_disposition == "already_committed" else "SIDE_EFFECT_STARTED" if prior_disposition == "in_flight" else "AMBIGUOUS"),
                    "prior_event_id": getattr(prior_event, "event_id", None),
                    "obligation_key": payload.get("obligation_key"),
                    "effect_namespace": payload.get("effect_namespace"),
                    "normalized_target": payload.get("normalized_target"),
                    "desired_postcondition_sha256": payload.get("desired_postcondition_sha256"),
                },
                logical_effect_id=logical_effect_id, attempt_id=attempt_id,
                step_id=step_id, effect_id=prior_effect_id,
            )
            return {
                "disposition": prior_disposition, "effect_id": prior_effect_id,
                "logical_effect_id": logical_effect_id, "attempt_id": attempt_id,
                "step_id": step_id,
                "effect_state": ("LOGICAL_COMMITTED" if prior_disposition == "already_committed" else "SIDE_EFFECT_STARTED" if prior_disposition == "in_flight" else "AMBIGUOUS"),
                "prior_result_summary": (
                    dict(prior_event.payload.get("result_summary") or {})
                    if getattr(prior_event, "event_type", "") == "step.committed"
                    else dict(prior_event.payload.get("evidence") or {})
                ),
                "ledger_seq": event.ledger_seq,
            }
        claim = EffectClaim(
            effect_id=effect_id, request_id=identity.request_id, run_id=identity.run_id,
            run_sequence=run_sequence, generation=identity.generation,
            effect_kind="execution", ordinal=ordinal, intent_sha256=intent_sha,
            pipeline_version="p18-m2-regenerative-effect-v1", attempt=1,
            claim_revision=1, lease_epoch=1, supersedes_claim_sha256=None,
            owner_component_id="tiangong-total-gateway", claimed_at_ms=now_ms,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        record, claimed = self._store.claim_effect(claim)
        if record.state in _TERMINAL_COMMITTED_EFFECT_STATES:
            disposition = "already_committed"
        elif record.state in _RECONCILE_REQUIRED_EFFECT_STATES:
            disposition = "reconcile_required"
        elif record.state == "CLAIMED":
            disposition = "prepared"
        elif record.state == "FAILED_FINAL":
            disposition = "failed_final"
        else:
            disposition = "blocked"
        event, _ = self._store.append_execution_event(
            event_key=f"step.prepared:{step_id}:{attempt}", request_id=identity.request_id,
            run_id=identity.run_id, generation=identity.generation,
            epoch_index=_integer(payload.get("epoch_index", 0), label="epoch_index"),
            event_type="step.prepared", created_at_ms=now_ms,
            payload={
                "disposition": disposition, "effect_state": record.state,
                "obligation_key": payload.get("obligation_key"),
                "effect_namespace": payload.get("effect_namespace"),
                "normalized_target": payload.get("normalized_target"),
                "desired_postcondition_sha256": payload.get("desired_postcondition_sha256"),
            },
            logical_effect_id=logical_effect_id, attempt_id=attempt_id,
            step_id=step_id, effect_id=effect_id,
        )
        return {
            "disposition": disposition, "effect_id": effect_id,
            "logical_effect_id": logical_effect_id, "attempt_id": attempt_id,
            "step_id": step_id, "effect_state": record.state,
            "ledger_seq": event.ledger_seq,
        }

    def _start_effect(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, _contract = self._bound_identity(payload)
        effect_id = _text(payload.get("effect_id"), label="effect_id")
        logical_effect_id = _text(payload.get("logical_effect_id"), label="logical_effect_id")
        attempt_id = _text(payload.get("attempt_id"), label="attempt_id")
        step_id = _text(payload.get("step_id"), label="step_id")
        now_ms = _integer(payload.get("now_ms"), label="now_ms")
        record = self._store.get_effect(effect_id)
        if record is None or record.claim.request_id != identity.request_id or record.claim.run_id != identity.run_id or record.claim.generation != identity.generation:
            raise StoreConflictError("effect is not owned by this authoritative Run")
        if record.state in _TERMINAL_COMMITTED_EFFECT_STATES:
            return {"dispatch_permitted": False, "disposition": "already_committed", "effect_state": record.state}
        if record.state in _RECONCILE_REQUIRED_EFFECT_STATES:
            return {"dispatch_permitted": False, "disposition": "reconcile_required", "effect_state": record.state}
        fence = self._store.action_fence_status()
        dispatch_nonce = canonical_sha256({
            "domain": "tiangong.gateway.regenerative-dispatch-permit.v1",
            "request_id": identity.request_id,
            "run_id": identity.run_id,
            "generation": identity.generation,
            "effect_id": effect_id,
            "attempt_id": attempt_id,
            "step_id": step_id,
        })
        try:
            permit = self._store.acquire_dispatch_permit(
                effect_id=effect_id,
                attempt=1,
                expected_fence_epoch=int(fence["action_fence_epoch"]),
                nonce_sha256=dispatch_nonce,
                now_ms=now_ms,
            )
        except StoreConflictError:
            current = self._store.get_effect(effect_id)
            if current is None:
                raise
            if current.state in _TERMINAL_COMMITTED_EFFECT_STATES:
                return {
                    "dispatch_permitted": False,
                    "disposition": "already_committed",
                    "effect_state": current.state,
                }
            if current.state in _RECONCILE_REQUIRED_EFFECT_STATES:
                disposition = (
                    "in_flight" if current.state == "SIDE_EFFECT_STARTED" else "reconcile_required"
                )
                return {
                    "dispatch_permitted": False,
                    "disposition": disposition,
                    "effect_state": current.state,
                }
            raise
        started = self._store.get_effect(effect_id)
        if started is None or started.state != "SIDE_EFFECT_STARTED":
            raise StoreCorruptionError("dispatch permit did not advance canonical Effect head")
        event, _ = self._store.append_execution_event(
            event_key=f"step.dispatched:{step_id}:{attempt_id}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=_integer(payload.get("epoch_index", 0), label="epoch_index"),
            event_type="step.dispatched",
            created_at_ms=now_ms,
            payload={
                "effect_state": started.state,
                "dispatch_boundary": "action_fence_permit_before_handler",
                "fence_epoch": int(permit["fence_epoch"]),
            },
            logical_effect_id=logical_effect_id,
            attempt_id=attempt_id,
            step_id=step_id,
            effect_id=effect_id,
        )
        return {
            "dispatch_permitted": True,
            "disposition": "dispatched",
            "effect_state": started.state,
            "ledger_seq": event.ledger_seq,
        }

    def _finish_effect(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, _contract = self._bound_identity(payload)
        effect_id = _text(payload.get("effect_id"), label="effect_id")
        logical_effect_id = _text(payload.get("logical_effect_id"), label="logical_effect_id")
        attempt_id = _text(payload.get("attempt_id"), label="attempt_id")
        step_id = _text(payload.get("step_id"), label="step_id")
        now_ms = _integer(payload.get("now_ms"), label="now_ms")
        outcome = _text(payload.get("outcome"), label="outcome")
        if outcome not in {"succeeded", "failed_final", "ambiguous"}:
            raise ValueError("effect outcome is invalid")
        status = {"succeeded": "SUCCEEDED", "failed_final": "FAILED_FINAL", "ambiguous": "AMBIGUOUS"}[outcome]
        result_summary = dict(_mapping(payload.get("result_summary")))
        evidence_sha = canonical_sha256({
            "domain": "tiangong.gateway.regenerative-effect-evidence.v1",
            "effect_id": effect_id,
            "outcome": outcome,
            "result_summary": result_summary,
        })
        error_code = None if status == "SUCCEEDED" else _text(
            payload.get("error_code") or ("effect_ambiguous" if status == "AMBIGUOUS" else "effect_failed_final"),
            label="error_code",
        )
        result = EffectResult(
            result_id="rlt_" + canonical_sha256({
                "effect_id": effect_id, "status": status, "evidence_sha256": evidence_sha
            }),
            effect_id=effect_id,
            status=status,
            fact_id="fact_" + canonical_sha256({"effect_id": effect_id, "evidence": evidence_sha}),
            result_object_id=None,
            result_object_sha256=None,
            evidence_sha256=evidence_sha,
            error_code=error_code,
            observed_at_ms=now_ms,
            model_generated=False,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        record = self._store.complete_effect(result)
        event_type = {
            "SUCCEEDED": "step.committed",
            "FAILED_FINAL": "step.failed",
            "AMBIGUOUS": "step.ambiguous",
        }[status]
        event, _ = self._store.append_execution_event(
            event_key=f"{event_type}:{step_id}:{attempt_id}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=_integer(payload.get("epoch_index", 0), label="epoch_index"),
            event_type=event_type,
            created_at_ms=now_ms,
            payload={
                "effect_state": record.state,
                "result_sha256": result.result_sha256,
                "result_summary": result_summary,
            },
            logical_effect_id=logical_effect_id,
            attempt_id=attempt_id,
            step_id=step_id,
            effect_id=effect_id,
        )
        return {
            "effect_id": effect_id,
            "effect_state": record.state,
            "result_sha256": result.result_sha256,
            "ledger_seq": event.ledger_seq,
        }

    def _reconcile_effect(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, _contract = self._bound_identity(payload)
        effect_id = _text(payload.get("effect_id"), label="effect_id")
        logical_effect_id = _text(payload.get("logical_effect_id"), label="logical_effect_id")
        attempt_id = _text(payload.get("attempt_id"), label="attempt_id")
        step_id = _text(payload.get("step_id"), label="step_id")
        verdict = _text(payload.get("verdict"), label="verdict").upper()
        if verdict not in {"APPLIED", "PROVEN_NOT_APPLIED", "INCONCLUSIVE"}:
            raise ValueError("reconciliation verdict is invalid")
        now_ms = _integer(payload.get("now_ms"), label="now_ms")
        record = self._store.get_effect(effect_id)
        if record is None or record.claim.request_id != identity.request_id or record.claim.run_id != identity.run_id or record.claim.generation != identity.generation:
            raise StoreConflictError("reconciliation effect is not owned by this Run")
        result = self._store.record_effect_reconciliation(
            effect_id=effect_id, attempt=1, verdict=verdict,
            evidence=dict(_mapping(payload.get("evidence"))), observed_at_ms=now_ms,
        )
        event, _ = self._store.append_execution_event(
            event_key=f"step.reconciled:{step_id}:{attempt_id}:{verdict}",
            request_id=identity.request_id, run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=_integer(payload.get("epoch_index", 0), label="epoch_index"),
            event_type="step.reconciled", created_at_ms=now_ms,
            payload={
                "verdict": verdict, "contradiction": bool(result.get("contradiction")),
                "attempt_state": result.get("attempt_state"),
                "evidence": dict(_mapping(payload.get("evidence"))),
            },
            logical_effect_id=logical_effect_id, attempt_id=attempt_id,
            step_id=step_id, effect_id=effect_id,
        )
        return {
            "verdict": verdict, "contradiction": bool(result.get("contradiction")),
            "retry_allowed": verdict == "PROVEN_NOT_APPLIED" and not bool(result.get("contradiction")),
            "logical_committed": verdict == "APPLIED" and not bool(result.get("contradiction")),
            "ledger_seq": event.ledger_seq,
        }

    def _update_frontier(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, contract = self._bound_identity(payload)
        frontier = _json_frontier(payload.get("frontier"))
        if (
            frontier.request_id != identity.request_id
            or frontier.run_id != identity.run_id
            or frontier.generation != identity.generation
            or frontier.life_id != identity.life_id
            or frontier.root_goal_hash != str(contract["root_goal_hash"])
            or frontier.task_contract_hash != str(contract["task_contract_hash"])
            or frontier.authority_hash != str(contract["authority_hash"])
        ):
            raise StoreConflictError("frontier crossed immutable task/authority identity")
        effects = self._store.list_effects_for_request(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        )
        # Scope the projection check to effects this pipeline governs: the
        # ledger is shared with the orchestrator's run-boundary parent effect
        # and omni admission sub-effects, whose lifecycles bracket the whole
        # run and are invisible to the backend's local pending set. Including
        # them made every end-of-step frontier commit conflict (the parent
        # effect is open by definition while the run is executing).
        governed_effects = tuple(
            record
            for record in effects
            if record.claim.pipeline_version == _REGENERATIVE_PIPELINE_VERSION
        )
        actual_pending = tuple(sorted(
            record.claim.effect_id for record in governed_effects
            if record.state in {"CLAIMED", "SIDE_EFFECT_STARTED"}
        ))
        actual_ambiguous = tuple(sorted(
            record.claim.effect_id for record in governed_effects
            if record.state == "AMBIGUOUS"
            and self._store.latest_effect_verdict(record.claim.effect_id, 1) != "APPLIED"
        ))
        if actual_pending != frontier.pending_effect_ids or actual_ambiguous != frontier.ambiguous_effect_ids:
            # QA diagnosis: pinpoint which side of the projection diverged.
            ledger_states = {
                record.claim.effect_id: (record.state, record.claim.owner_component_id)
                for record in effects
                if record.state in {"CLAIMED", "SIDE_EFFECT_STARTED", "AMBIGUOUS"}
            }
            diagnostic_log(
                "[FRONTIER_CONFLICT] "
                + json.dumps(
                    {
                        "request_id": identity.request_id,
                        "frontier_pending": list(frontier.pending_effect_ids),
                        "ledger_pending": list(actual_pending),
                        "frontier_ambiguous": list(frontier.ambiguous_effect_ids),
                        "ledger_ambiguous": list(actual_ambiguous),
                        "ledger_open_states": ledger_states,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            raise StoreConflictError("frontier effect projection disagrees with canonical Effect Ledger")
        current = self._store.get_execution_frontier(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        )
        if current is not None and current.frontier_hash == frontier.frontier_hash:
            return {
                "committed": True, "duplicate": True,
                "frontier_version": current.frontier_version,
                "frontier_hash": current.frontier_hash,
            }
        expected_revision = 0 if current is None else current.frontier_version
        if frontier.frontier_version != expected_revision + 1:
            raise StoreConflictError("frontier revision is not the next authoritative CAS revision")
        self._store.commit_execution_frontier(
            frontier, expected_revision=expected_revision,
            updated_at_ms=_integer(payload.get("now_ms"), label="now_ms"),
        )
        event, _ = self._store.append_execution_event(
            event_key=f"frontier.updated:{frontier.frontier_version}",
            request_id=identity.request_id, run_id=identity.run_id,
            generation=identity.generation, epoch_index=frontier.epoch_index,
            event_type="frontier.updated",
            created_at_ms=_integer(payload.get("now_ms"), label="now_ms"),
            payload={"frontier": frontier.model_dump(mode="json")},
        )
        return {
            "committed": True, "duplicate": False,
            "frontier_version": frontier.frontier_version,
            "frontier_hash": frontier.frontier_hash,
            "ledger_seq": event.ledger_seq,
        }

    def _commit_checkpoint(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, contract = self._bound_identity(payload)
        frontier = _json_frontier(payload.get("frontier"))
        if (
            frontier.request_id != identity.request_id
            or frontier.run_id != identity.run_id
            or frontier.generation != identity.generation
            or frontier.life_id != identity.life_id
            or frontier.root_goal_hash != str(contract["root_goal_hash"])
            or frontier.task_contract_hash != str(contract["task_contract_hash"])
            or frontier.authority_hash != str(contract["authority_hash"])
        ):
            raise StoreConflictError("checkpoint frontier crossed immutable identity")
        now_ms = _integer(payload.get("now_ms"), label="now_ms")
        fact_status = str(payload.get("critical_fact_status") or "verified").strip().lower()
        if fact_status not in {"verified", "fresh"}:
            self._store.append_execution_event(
                event_key=f"checkpoint.audit.reject:{frontier.frontier_version}",
                request_id=identity.request_id,
                run_id=identity.run_id,
                generation=identity.generation,
                epoch_index=frontier.epoch_index,
                event_type="checkpoint.audited",
                created_at_ms=now_ms,
                payload={"accepted": False, "reason": f"critical_fact_status:{fact_status}"},
            )
            return {"committed": False, "reason": "checkpoint_reality_audit_failed"}
        effects = self._store.list_effects_for_request(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        )
        actual_pending = tuple(sorted(
            record.claim.effect_id for record in effects if record.state in {"CLAIMED", "SIDE_EFFECT_STARTED"}
        ))
        actual_ambiguous = tuple(sorted(
            record.claim.effect_id for record in effects
            if record.state == "AMBIGUOUS"
            and self._store.latest_effect_verdict(record.claim.effect_id, 1) != "APPLIED"
        ))
        if actual_pending != frontier.pending_effect_ids or actual_ambiguous != frontier.ambiguous_effect_ids:
            return {
                "committed": False,
                "reason": "checkpoint_effect_projection_mismatch",
                "pending_effect_ids": actual_pending,
                "ambiguous_effect_ids": actual_ambiguous,
            }
        current = self._store.get_execution_frontier(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        )
        expected_revision = 0 if current is None else current.frontier_version
        if current is not None and current.frontier_hash == frontier.frontier_hash:
            committed_frontier = current
        else:
            self._store.commit_execution_frontier(
                frontier, expected_revision=expected_revision, updated_at_ms=now_ms
            )
            committed_frontier = frontier
            self._store.append_execution_event(
                event_key=f"frontier.updated:{frontier.frontier_version}",
                request_id=identity.request_id,
                run_id=identity.run_id,
                generation=identity.generation,
                epoch_index=frontier.epoch_index,
                event_type="frontier.updated",
                created_at_ms=now_ms,
                payload={"frontier": frontier.model_dump(mode="json")},
            )
        self._store.append_execution_event(
            event_key=f"checkpoint.prepared:{committed_frontier.frontier_version}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=committed_frontier.epoch_index,
            event_type="checkpoint.prepared",
            created_at_ms=now_ms,
            payload={"frontier_hash": committed_frontier.frontier_hash},
        )
        self._store.append_execution_event(
            event_key=f"checkpoint.audited:{committed_frontier.frontier_version}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=committed_frontier.epoch_index,
            event_type="checkpoint.audited",
            created_at_ms=now_ms,
            payload={"accepted": True, "frontier_hash": committed_frontier.frontier_hash},
        )
        checkpoint = self._store.commit_regenerative_checkpoint(
            committed_frontier,
            continuity_capsule_id=_text(payload.get("continuity_capsule_id"), label="continuity_capsule_id"),
            recovery_preconditions=tuple(str(item) for item in payload.get("recovery_preconditions", ()) if str(item).strip()),
            runtime_version=_text(payload.get("runtime_version") or "tiangong-v3", label="runtime_version"),
            provider_version=_text(payload.get("provider_version") or "unknown", label="provider_version"),
            model_version=_text(payload.get("model_version") or "unknown", label="model_version"),
            tool_contract_version=_text(payload.get("tool_contract_version") or "omni_body.v1", label="tool_contract_version"),
            skill_contract_version=_text(payload.get("skill_contract_version") or "skill.v1", label="skill_contract_version"),
            task_contract_version=_text(payload.get("task_contract_version") or "task.v1", label="task_contract_version"),
            semantic_handoff=str(payload.get("semantic_handoff") or "")[:12_000],
            created_at_ms=now_ms,
        )
        event, _ = self._store.append_execution_event(
            event_key=f"checkpoint.committed:{checkpoint.checkpoint_id}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=committed_frontier.epoch_index,
            event_type="checkpoint.committed",
            created_at_ms=now_ms,
            payload={
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_hash": checkpoint.checkpoint_hash,
                "frontier_hash": checkpoint.frontier_hash,
                "ledger_head_seq": checkpoint.ledger_head_seq,
            },
        )
        return {
            "committed": True,
            "checkpoint_id": checkpoint.checkpoint_id,
            "checkpoint_hash": checkpoint.checkpoint_hash,
            "frontier_hash": checkpoint.frontier_hash,
            "ledger_seq": event.ledger_seq,
        }

    def _recover(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, _contract = self._bound_identity(payload)
        now_ms = _integer(payload.get("now_ms"), label="now_ms")

        def execution_events(effect_id: str) -> list[Any]:
            return [
                event for event in self._store.list_execution_events(
                    identity.request_id, run_id=identity.run_id, generation=identity.generation
                ) if event.effect_id == effect_id
            ]

        def result_for(effect_id: str, status: str, reason: str) -> EffectResult:
            evidence = canonical_sha256({
                "domain": "tiangong.gateway.crash-window-effect-recovery.v1",
                "effect_id": effect_id,
                "status": status,
                "reason": reason,
            })
            return EffectResult(
                result_id="rlt_" + canonical_sha256({
                    "effect_id": effect_id, "status": status, "recovery": reason
                }),
                effect_id=effect_id,
                status=status,
                fact_id="fact_" + canonical_sha256({"effect_id": effect_id, "evidence": evidence}),
                result_object_id=None,
                result_object_sha256=None,
                evidence_sha256=evidence,
                error_code=None if status == "SUCCEEDED" else reason,
                observed_at_ms=now_ms,
                model_generated=False,
                result_sha256="0" * 64,
            ).with_computed_sha256()

        # Repair cross-table crash windows from the canonical physical Effect
        # ledger back into the append-only execution ledger.  This does not
        # guess whether a STARTED action applied: it deliberately marks it
        # AMBIGUOUS.  A CLAIMED-only effect is proven not dispatched.
        for record in self._store.list_effects_for_request(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        ):
            if str(record.claim.owner_component_id) == "tiangong-backend":
                # Orchestration-owned effects are claimed/started/completed by
                # GatewayOrchestrationWorker while the chain is live; the
                # provider must never finalize them.  Their crash window is
                # owned by the gateway's own startup recovery
                # (recover_started_effects, owner-aware), not by chain recover.
                continue
            effect_id = record.claim.effect_id
            events = execution_events(effect_id)
            terminal = next((
                event for event in reversed(events)
                if event.event_type in {"step.committed", "step.failed", "step.ambiguous"}
            ), None)
            if terminal is not None:
                continue
            dispatched = next((
                event for event in reversed(events) if event.event_type == "step.dispatched"
            ), None)
            prepared = next((
                event for event in reversed(events) if event.event_type == "step.prepared"
            ), None)
            source = dispatched or prepared

            if record.state == "CLAIMED":
                # No STARTED fence exists, therefore this physical attempt was
                # durably prepared but never dispatched.  Finalize the stale
                # attempt as non-applied so it cannot poison Completion Proof.
                self._store.complete_effect(result_for(
                    effect_id, "FAILED_FINAL", "process_restart_before_dispatch"
                ))
                if source and source.logical_effect_id and source.attempt_id and source.step_id:
                    self._store.append_execution_event(
                        event_key=f"step.failed:{source.step_id}:{source.attempt_id}:restart-before-dispatch",
                        request_id=identity.request_id, run_id=identity.run_id,
                        generation=identity.generation, epoch_index=source.epoch_index,
                        event_type="step.failed", created_at_ms=now_ms,
                        payload={
                            "effect_state": "FAILED_FINAL",
                            "reason": "process_restart_before_dispatch",
                            "proven_not_applied": True,
                        },
                        logical_effect_id=source.logical_effect_id,
                        attempt_id=source.attempt_id,
                        step_id=source.step_id,
                        effect_id=effect_id,
                        causal_parent_event_id=source.event_id,
                    )
                continue

            if record.state == "SIDE_EFFECT_STARTED":
                if source is None or not source.logical_effect_id or not source.attempt_id or not source.step_id:
                    raise StoreCorruptionError(
                        "started effect has no prepared/dispatch event for crash recovery"
                    )
                if dispatched is None:
                    dispatched, _ = self._store.append_execution_event(
                        event_key=f"step.dispatched:{source.step_id}:{source.attempt_id}:recovered",
                        request_id=identity.request_id, run_id=identity.run_id,
                        generation=identity.generation, epoch_index=source.epoch_index,
                        event_type="step.dispatched", created_at_ms=now_ms,
                        payload={
                            "effect_state": "SIDE_EFFECT_STARTED",
                            "dispatch_boundary": "reconstructed_from_effect_started_fence",
                        },
                        logical_effect_id=source.logical_effect_id,
                        attempt_id=source.attempt_id,
                        step_id=source.step_id,
                        effect_id=effect_id,
                        causal_parent_event_id=source.event_id,
                    )
                    source = dispatched
                result = result_for(effect_id, "AMBIGUOUS", "process_restart_after_dispatch")
                self._store.complete_effect(result)
                self._store.append_execution_event(
                    event_key=f"step.ambiguous:{source.step_id}:{source.attempt_id}:restart",
                    request_id=identity.request_id, run_id=identity.run_id,
                    generation=identity.generation, epoch_index=source.epoch_index,
                    event_type="step.ambiguous", created_at_ms=now_ms,
                    payload={
                        "effect_state": "AMBIGUOUS",
                        "reason": "process_restart_after_dispatch",
                        "result_sha256": result.result_sha256,
                    },
                    logical_effect_id=source.logical_effect_id,
                    attempt_id=source.attempt_id,
                    step_id=source.step_id,
                    effect_id=effect_id,
                    causal_parent_event_id=source.event_id,
                )
                continue

            if record.state in {"SUCCEEDED", "AMBIGUOUS", "FAILED_FINAL"}:
                if source is None or not source.logical_effect_id or not source.attempt_id or not source.step_id:
                    raise StoreCorruptionError(
                        "terminal effect has no prepared/dispatch event for execution-ledger healing"
                    )
                event_type = {
                    "SUCCEEDED": "step.committed",
                    "AMBIGUOUS": "step.ambiguous",
                    "FAILED_FINAL": "step.failed",
                }[record.state]
                self._store.append_execution_event(
                    event_key=f"{event_type}:{source.step_id}:{source.attempt_id}:recovered",
                    request_id=identity.request_id, run_id=identity.run_id,
                    generation=identity.generation, epoch_index=source.epoch_index,
                    event_type=event_type, created_at_ms=now_ms,
                    payload={
                        "effect_state": record.state,
                        "reason": "healed_from_canonical_effect_ledger",
                        "recovered_terminal_event": True,
                    },
                    logical_effect_id=source.logical_effect_id,
                    attempt_id=source.attempt_id,
                    step_id=source.step_id,
                    effect_id=effect_id,
                    causal_parent_event_id=source.event_id,
                )

        recovered = self._store.recover_regenerative_execution(
            identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            recovered_at_ms=now_ms,
        )
        if not recovered.get("recoverable"):
            return {"recoverable": False, "resume_allowed": False, "reason": recovered.get("reason")}
        frontier: ExecutionFrontier = recovered["frontier"]
        checkpoint = recovered["checkpoint"]
        checkpoint_vector = version_vector_from_mapping(checkpoint.model_dump(mode="json"))
        current_vector = version_vector_from_mapping({
            "checkpoint_schema_version": str(payload.get("checkpoint_schema_version") or CHECKPOINT_SCHEMA_VERSION),
            "runtime_version": str(payload.get("runtime_version") or "tiangong-v3"),
            "provider_version": str(payload.get("provider_version") or "unknown"),
            "model_version": str(payload.get("model_version") or "unknown"),
            "tool_contract_version": str(payload.get("tool_contract_version") or "omni_body.v1"),
            "skill_contract_version": str(payload.get("skill_contract_version") or "skill.v1"),
            "task_contract_version": str(payload.get("task_contract_version") or "task.v1"),
        })
        raw_pairs = payload.get("migratable_schema_pairs") or ()
        migratable_pairs = tuple(
            (str(item[0]), str(item[1]))
            for item in raw_pairs
            if isinstance(item, (list, tuple)) and len(item) == 2
        )
        version_decision = evaluate_checkpoint_version_compatibility(
            checkpoint_vector,
            current_vector,
            compatible_mismatches=tuple(
                str(item)
                for item in (payload.get("compatible_version_mismatches") or ())
                if str(item).strip()
            ),
            migratable_schema_pairs=migratable_pairs,
            migration_completed=payload.get("version_migration_completed") is True,
            revalidated=payload.get("version_revalidated") is True,
        )
        if not version_decision.resume_allowed:
            return {
                "recoverable": True,
                "resume_allowed": False,
                "reconcile_required": bool(version_decision.reconcile_required),
                "migration_required": bool(version_decision.migration_required),
                "revalidation_required": bool(version_decision.revalidation_required),
                "reason": ("RECONCILE_REQUIRED" if version_decision.reconcile_required else "VERSION_REVALIDATION_REQUIRED"),
                "version_mismatches": list(version_decision.mismatches),
                "version_reasons": list(version_decision.reasons),
                "checkpoint": checkpoint.model_dump(mode="json"),
                "frontier": frontier.model_dump(mode="json"),
                "pending_effect_ids": list(recovered["pending_effect_ids"]),
                "ambiguous_effect_ids": list(recovered["ambiguous_effect_ids"]),
                "used_previous_checkpoint": bool(recovered["used_previous_checkpoint"]),
            }
        event, _ = self._store.append_execution_event(
            event_key=f"run.resumed:{frontier.frontier_version}:{frontier.global_step}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=frontier.epoch_index,
            event_type="run.resumed",
            created_at_ms=now_ms,
            payload={
                "checkpoint_id": recovered["checkpoint"].checkpoint_id,
                "used_previous_checkpoint": bool(recovered["used_previous_checkpoint"]),
                "frontier_hash": frontier.frontier_hash,
                "pending_effect_ids": list(recovered["pending_effect_ids"]),
                "ambiguous_effect_ids": list(recovered["ambiguous_effect_ids"]),
            },
        )
        return {
            "recoverable": True,
            "resume_allowed": True,
            "reconcile_required": False,
            "migration_required": bool(version_decision.migration_required),
            "revalidation_required": False,
            "version_mismatches": list(version_decision.mismatches),
            "checkpoint": checkpoint.model_dump(mode="json"),
            "frontier": frontier.model_dump(mode="json"),
            "pending_effect_ids": list(recovered["pending_effect_ids"]),
            "ambiguous_effect_ids": list(recovered["ambiguous_effect_ids"]),
            "used_previous_checkpoint": bool(recovered["used_previous_checkpoint"]),
            "ledger_seq": event.ledger_seq,
        }

    def _verify_completion(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity, _contract = self._bound_identity(payload)
        now_ms = _integer(payload.get("now_ms"), label="now_ms")
        epoch_index = _integer(payload.get("epoch_index", 0), label="epoch_index")
        proposal_key = _text(payload.get("proposal_key") or "final", label="proposal_key")
        self._store.append_execution_event(
            event_key=f"completion.proposed:{proposal_key}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=epoch_index,
            event_type="completion.proposed",
            created_at_ms=now_ms,
            payload={"proposal_key": proposal_key},
        )
        reasons: list[str] = []
        audit = self._store.audit_execution_ledger(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        )
        if not audit.get("healthy"):
            reasons.append("execution_ledger_invalid")
        frontier = self._store.get_execution_frontier(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        )
        if frontier is None:
            reasons.append("frontier_missing")
        else:
            if frontier.pending_obligation_ids or frontier.active_obligation_id:
                reasons.append("task_obligations_pending")
            if frontier.active_blockers:
                reasons.append("frontier_blocked")
            if frontier.pending_effect_ids:
                reasons.append("effects_pending")
            if frontier.ambiguous_effect_ids:
                reasons.append("effects_ambiguous")
        effects = self._store.list_effects_for_request(
            identity.request_id, run_id=identity.run_id, generation=identity.generation
        )
        # 完成提案发生在后端执行中途：本代的外层 execution effect 必然还
        # 处于 CLAIMED/SIDE_EFFECT_STARTED——它是这次提案自己的信封，不是
        # 未决债务。把它算作 pending 等于要求"提案在自己结束前证明自己已
        # 结束"，结构性不可满足（P18 后所有带工具请求因此全部 incomplete，
        # 真机 2026-08-29 复现）。只有其他 effect（交付等）挂起才是真阻断。
        def _is_own_inflight_envelope(record) -> bool:
            return (
                record.claim.effect_kind == "execution"
                and record.claim.generation == identity.generation
                and record.state in {"CLAIMED", "SIDE_EFFECT_STARTED"}
            )

        if any(
            record.state in {"CLAIMED", "SIDE_EFFECT_STARTED"}
            and not _is_own_inflight_envelope(record)
            for record in effects
        ):
            reasons.append("effect_ledger_pending")
        if any(
            record.state == "AMBIGUOUS"
            and self._store.latest_effect_verdict(record.claim.effect_id, 1) != "APPLIED"
            for record in effects
        ):
            reasons.append("effect_reconciliation_required")
        for reason in payload.get("runtime_blockers", ()):
            value = str(reason).strip()
            if value and value not in reasons:
                reasons.append(value)
        if payload.get("life_gate_allowed") is not True:
            reasons.append("life_completion_gate_rejected")
        if payload.get("required_evidence_ready") is not True:
            reasons.append("required_evidence_missing")
        reasons = list(dict.fromkeys(reasons))[:32]
        verified = not reasons
        event_type = "completion.verified" if verified else "completion.rejected"
        proof_payload = {
            "verified": verified,
            "reasons": reasons,
            "ledger_head": self._store.get_execution_ledger_head(
                identity.request_id, run_id=identity.run_id, generation=identity.generation
            ),
            "frontier_hash": None if frontier is None else frontier.frontier_hash,
            "effect_count": len(effects),
        }
        proof_hash = canonical_sha256({"domain": "tiangong.gateway.completion-proof.v1", **proof_payload})
        event, _ = self._store.append_execution_event(
            event_key=f"{event_type}:{proposal_key}",
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            epoch_index=epoch_index,
            event_type=event_type,
            created_at_ms=now_ms,
            payload={**proof_payload, "proof_hash": proof_hash},
        )
        terminal_seq = event.ledger_seq
        if verified:
            terminal, _ = self._store.append_execution_event(
                event_key=f"chain.completed:{proposal_key}",
                request_id=identity.request_id, run_id=identity.run_id,
                generation=identity.generation, epoch_index=epoch_index,
                event_type="chain.completed", created_at_ms=now_ms,
                payload={"completion_proof_hash": proof_hash, "frontier_hash": proof_payload["frontier_hash"]},
                causal_parent_event_id=event.event_id,
            )
            terminal_seq = terminal.ledger_seq
        # M4 §10: the closeout facts are also emitted as a typed,
        # hash-bound RuntimeCloseoutEvidence. Architecture boundary: this
        # evidence ONLY documents the V3 runtime's local closeout state —
        # it can never make VerificationReadiness PASS, never make
        # CompletionGate COMPLETED, and never replaces the artifact /
        # effect / repository / delivery gates.
        closeout_evidence = RuntimeCloseoutEvidence(
            request_id=identity.request_id,
            run_id=identity.run_id,
            generation=identity.generation,
            life_id=str(payload.get("life_id") or "unspecified")[:160],
            execution_ticket_id=(
                str(payload["execution_ticket_id"])[:160]
                if payload.get("execution_ticket_id") else None
            ),
            root_goal_hash=_closeout_digest(payload.get("root_goal_hash")),
            task_contract_hash=_closeout_digest(payload.get("task_contract_hash")),
            runtime_blockers=tuple(sorted(reasons)),
            runtime_blockers_sha256=canonical_sha256(sorted(reasons)),
            life_gate_allowed=payload.get("life_gate_allowed") is True,
            required_evidence_ready=payload.get("required_evidence_ready") is True,
            produced_at_ms=now_ms,
            evidence_sha256="0" * 64,
        ).with_computed_sha256()
        return {
            "verified_complete": verified,
            "reasons": reasons,
            "proof_hash": proof_hash,
            "ledger_seq": terminal_seq,
            "runtime_closeout_evidence": closeout_evidence.model_dump(mode="json"),
        }


__all__ = ["RegenerativeExecutionAuthority", "authority_hash"]
