"""Completion adapter for policy-authorized autonomous work."""

from __future__ import annotations

from contracts import ArtifactManifest

from .completion_gate import CompletionDecision, CompletionGate, CompletionRequirements
from .continuity import persist_terminal_completion, persist_working_checkpoint
from .fact_ledger import FactLedger
from .object_store import ContentAddressedObjectStore
from .store import GatewayStateStore


def evaluate_autonomous_completion(
    *,
    store: GatewayStateStore,
    objects: ContentAddressedObjectStore,
    facts: FactLedger,
    life_id: str,
    user_goal: str,
    request_id: str,
    run_id: str,
    generation: int,
    execution_effect_ids: tuple[str, ...],
    candidate_text: str | None,
    artifacts: tuple[ArtifactManifest, ...],
    evaluated_at_ms: int,
) -> CompletionDecision:
    requirements = CompletionRequirements(
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        text_required=candidate_text is not None,
        required_execution_effect_ids=tuple(sorted(set(execution_effect_ids))),
        required_artifact_revision_ids=tuple(
            sorted(item.artifact_revision_id for item in artifacts)
        ),
        delivery_requirement="NONE",
    )
    decision = CompletionGate(objects, facts, head_state_reader=store.get_effect_head_state).evaluate(
        requirements,
        candidate_text=candidate_text,
        artifacts=artifacts,
    )
    if decision.outcome in {"COMPLETED", "PARTIAL", "FAILED"}:
        persist_terminal_completion(
            store,
            decision,
            life_id=life_id,
            user_goal=user_goal,
            final_result=candidate_text or f"自主任务结果：{decision.outcome.lower()}",
            created_at_ms=evaluated_at_ms,
            artifact_refs=tuple(
                sorted(item.artifact_revision_id for item in artifacts)
            ),
        )
    else:
        store.record_completion_decision(decision, recorded_at_ms=evaluated_at_ms)
        persist_working_checkpoint(
            store,
            life_id=life_id,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            user_goal=user_goal,
            active_plan=("resolve missing autonomous completion evidence",),
            verified_fact_ids=decision.supporting_fact_ids,
            artifact_refs=tuple(
                sorted(item.artifact_revision_id for item in artifacts)
            ),
            pending_effect_ids=tuple(sorted(set(execution_effect_ids))),
            latest_safe_step="autonomous completion gate evaluated durable evidence",
            next_step="reconcile missing facts before any completion claim",
            recovery_preconditions=("preserve the active generation fence",),
            created_at_ms=evaluated_at_ms,
        )
    return decision


__all__ = ["evaluate_autonomous_completion"]
