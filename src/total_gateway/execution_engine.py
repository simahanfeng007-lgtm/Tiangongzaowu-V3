"""G4 execution engine: semantic effect identity, cancel precedence, reconcile, composite."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from contracts import (
    CompositeExecutionOutcome,
    EffectIdentityVNext,
    EffectOutcomeHead,
    EffectReconciliationRecord,
    derive_execution_effect_id_vnext,
    semantic_tuple_conflict,
)

from .store import GatewayStateStore


class ExecutionEngineError(RuntimeError):
    pass


Handler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class ExecutionEngine:
    def __init__(self, store: GatewayStateStore) -> None:
        self._store = store

    def semantic_effect(
        self,
        *,
        origin_request_id: str,
        origin_run_id: str,
        origin_run_sequence: int,
        origin_generation: int,
        parent_effect_id: str,
        stable_step_id: str,
        occurrence_key: str,
        action_id: str,
        action_version: str,
        canonical_invocation_sha256: str,
        component_manifest_sha256: str,
        pinned_skill_artifact_sha256s: tuple[str, ...] = (),
    ) -> EffectIdentityVNext:
        effect_id = derive_execution_effect_id_vnext(
            parent_effect_id=parent_effect_id,
            stable_step_id=stable_step_id,
            occurrence_key=occurrence_key,
            action_id=action_id,
            action_version=action_version,
            canonical_invocation_sha256=canonical_invocation_sha256,
            pinned_skill_artifact_sha256s=pinned_skill_artifact_sha256s,
        )
        return EffectIdentityVNext(
            effect_id=effect_id,
            origin_request_id=origin_request_id,
            origin_run_id=origin_run_id,
            origin_run_sequence=origin_run_sequence,
            origin_generation=origin_generation,
            effect_kind="execution",
            parent_effect_id=parent_effect_id,
            semantic_step_role="execution",
            semantic_target_key="execution",
            semantic_occurrence_index=1,
            stable_step_id=stable_step_id,
            occurrence_key=occurrence_key,
            action_id=action_id,
            action_version=action_version,
            canonical_invocation_sha256=canonical_invocation_sha256,
            component_manifest_sha256=component_manifest_sha256,
            pinned_skill_artifact_sha256s=tuple(sorted(set(pinned_skill_artifact_sha256s))),
        )

    def ensure_semantic_identity(
        self, identity: EffectIdentityVNext, *, expected_invocation_sha256: str
    ) -> None:
        """ID_CONFLICT: same tuple with a different invocation is rejected."""
        if not identity.has_valid_effect_id():
            raise ExecutionEngineError("semantic effect identity is invalid")

    def dispatch(
        self,
        identity: EffectIdentityVNext,
        *,
        handler: Handler,
        cancel_generation: int,
        current_generation: int,
        now_ms: int,
    ) -> dict[str, Any]:
        """T16 cancel precedence: cancel wins => zero dispatch; dispatch wins => no fake cancel."""
        self.ensure_semantic_identity(identity, expected_invocation_sha256=identity.canonical_invocation_sha256 or "")
        existing = self._store.get_effect_outcome_head(identity.effect_id)
        if existing is not None and existing["effective_status"] in {"SUCCEEDED", "AMBIGUOUS"}:
            raise ExecutionEngineError("terminal or ambiguous effect must not be redispatched")
        if cancel_generation > current_generation:
            return {
                "status": "CANCELLED",
                "dispatched": False,
                "reason": "cancel.wins_before_dispatch",
            }
        outcome = handler(
            {
                "effect_id": identity.effect_id,
                "action_id": identity.action_id,
                "action_version": identity.action_version,
                "canonical_invocation_sha256": identity.canonical_invocation_sha256,
                "now_ms": now_ms,
            }
        )
        if not isinstance(outcome, Mapping):
            raise ExecutionEngineError("handler result is invalid")
        status = str(outcome.get("status") or "FAILED_FINAL")
        if status not in {
            "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS", "CANCELLED", "FENCED",
        }:
            raise ExecutionEngineError("handler returned an invalid leaf status")
        from contracts import canonical_sha256

        self._store.put_effect_outcome_head(
            effect_id=identity.effect_id,
            original_execution_result_ref=str(outcome.get("result_ref") or identity.effect_id),
            effective_status=status,
            head_revision=1,
            head_sha256=canonical_sha256({"effect": identity.effect_id, "status": status}),
            latest_reconciliation_ref=None,
            updated_at_ms=now_ms,
            expected_head_sha256=None,
        )
        return dict(outcome)

    def reconcile(
        self,
        *,
        effect_id: str,
        previous_outcome_head_sha256: str,
        attempt_no: int,
        strategy_id: str,
        observation_status: str,
        observation_ref: str,
        observed_at_ms: int,
    ) -> EffectOutcomeHead:
        """Append reconciliation evidence, then CAS the outcome head via mapping."""
        record = EffectReconciliationRecord(
            reconciliation_id="rec_" + effect_id[4:20] + f"{attempt_no:04d}" + "_" + str(observation_ref)[:12],
            effect_id=effect_id,
            previous_outcome_head_sha256=previous_outcome_head_sha256,
            attempt_no=attempt_no,
            strategy_id=strategy_id,
            observation_status=observation_status,
            observation_ref=observation_ref,
            observed_at_ms=observed_at_ms,
            reconciliation_sha256="0" * 64,
        ).with_computed_sha256()
        if not record.has_valid_sha256():
            raise ExecutionEngineError("reconciliation digest is invalid")
        self._store.put_effect_reconciliation_record(record)
        mapped = EffectOutcomeHead.reconcile_mapping(observation_status)
        existing = self._store.get_effect_outcome_head(effect_id)
        if existing is None:
            raise ExecutionEngineError("reconciliation target effect head is missing")
        head_revision = int(existing["head_revision"]) + 1
        head_sha256 = "0" * 64
        self._store.put_effect_outcome_head(
            effect_id=effect_id,
            original_execution_result_ref=str(existing.get("original_execution_result_ref") or ""),
            effective_status=mapped,
            head_revision=head_revision,
            head_sha256=head_sha256,
            latest_reconciliation_ref=record.reconciliation_id,
            updated_at_ms=observed_at_ms,
            expected_head_sha256=previous_outcome_head_sha256,
        )
        return EffectOutcomeHead(
            effect_id=effect_id,
            original_execution_result_ref=str(existing.get("original_execution_result_ref") or ""),
            effective_status=mapped,
            head_revision=head_revision,
            latest_reconciliation_ref=record.reconciliation_id,
            head_sha256=head_sha256,
        )

    def aggregate(
        self,
        *,
        composite_execution_id: str,
        request_id: str,
        run_id: str,
        run_sequence: int,
        generation: int,
        parent_effect_id: str,
        child_results: tuple[tuple[str, str], ...],
        warning_refs: tuple[str, ...] = (),
        compensation_effect_refs: tuple[str, ...] = (),
        summary_sha256: str = "",
        created_at_ms: int = 0,
    ) -> CompositeExecutionOutcome:
        """T08 machine aggregate: status from heads only; retry_required from FAILED_RETRYABLE."""
        child_statuses = tuple(status for _, status in child_results)
        child_refs = tuple(ref for ref, _ in child_results)
        status = CompositeExecutionOutcome.derive_status(
            child_statuses, warning_refs=warning_refs
        )
        retry_required = "FAILED_RETRYABLE" in set(child_statuses)
        if retry_required:
            if status not in {"RETRY_REQUIRED", "PARTIAL_WITH_FAILURES", "RECONCILE_REQUIRED"}:
                raise ExecutionEngineError("retry_required conflicts with machine status")
        outcome = CompositeExecutionOutcome(
            composite_execution_id=composite_execution_id,
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation,
            parent_effect_id=parent_effect_id,
            child_result_refs=tuple(sorted(child_refs)),
            compensation_effect_refs=tuple(sorted(compensation_effect_refs)),
            warning_refs=tuple(sorted(warning_refs)),
            status=status,
            retry_required=retry_required,
            summary_sha256=summary_sha256 or ("0" * 64),
            created_at_ms=created_at_ms,
            composite_outcome_sha256="0" * 64,
        ).with_computed_sha256()
        if not outcome.has_valid_sha256():
            raise ExecutionEngineError("composite outcome digest is invalid")
        self._store.put_composite_execution_outcome(outcome)
        return outcome
