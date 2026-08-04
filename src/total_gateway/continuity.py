"""Canonical working, compression, and terminal task-continuity boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts import TaskContinuityCapsule, WorkspaceFileRef, canonical_sha256

from .store import GatewayStateStore, RequestCapsuleRecord, StoreConflictError

if TYPE_CHECKING:
    from .completion_gate import CompletionDecision


def _capsule_id(payload: dict[str, object]) -> str:
    return "lcp_" + canonical_sha256(
        {"domain": "tiangong.gateway.continuity-capsule-id.v1", **payload}
    )


def build_task_continuity_capsule(
    *,
    life_id: str,
    capsule_kind: str,
    request_id: str,
    run_id: str,
    generation: int,
    user_goal: str,
    created_at_ms: int,
    hard_constraints: tuple[str, ...] = (),
    active_plan: tuple[str, ...] = (),
    verified_fact_ids: tuple[str, ...] = (),
    causal_hypothesis_ids: tuple[str, ...] = (),
    workspace_manifest: tuple[WorkspaceFileRef, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    unresolved_questions: tuple[str, ...] = (),
    pending_effect_ids: tuple[str, ...] = (),
    latest_safe_step: str | None = None,
    next_step: str | None = None,
    recovery_preconditions: tuple[str, ...] = (),
    continuation_token_sha256: str | None = None,
    final_result: str | None = None,
    supersedes_capsule_id: str | None = None,
) -> TaskContinuityCapsule:
    terminal = capsule_kind == "TERMINAL_RESULT"
    payload: dict[str, object] = {
        "life_id": life_id,
        "capsule_kind": capsule_kind,
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "episode_id": "cep_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.request-episode.v1",
                "request_id": request_id,
                "run_id": run_id,
                "generation": generation,
            }
        ),
        "user_goal": user_goal,
        "hard_constraints": tuple(hard_constraints),
        "active_plan": () if terminal else tuple(active_plan),
        "verified_fact_ids": tuple(sorted(set(verified_fact_ids))),
        "causal_hypothesis_ids": tuple(sorted(set(causal_hypothesis_ids))),
        "workspace_manifest": tuple(sorted(
            workspace_manifest, key=lambda item: item.relative_path
        )),
        "artifact_refs": tuple(sorted(set(artifact_refs))),
        "unresolved_questions": () if terminal else tuple(unresolved_questions),
        "pending_effect_ids": () if terminal else tuple(sorted(set(pending_effect_ids))),
        "latest_safe_step": None if terminal else latest_safe_step,
        "next_step": None if terminal else next_step,
        "recovery_preconditions": () if terminal else tuple(recovery_preconditions),
        "continuation_token_sha256": None if terminal else continuation_token_sha256,
        "final_result": final_result if terminal else None,
        "supersedes_capsule_id": supersedes_capsule_id,
        "retention_class": "TERMINAL_RESULT" if terminal else (
            "CHECKPOINT" if capsule_kind == "COMPRESSION_CHECKPOINT" else "ACTIVE_WORKING"
        ),
        "created_at_ms": created_at_ms,
    }
    capsule = TaskContinuityCapsule(
        capsule_id=_capsule_id(payload),
        **payload,
        capsule_sha256="0" * 64,
    )
    return capsule.with_computed_capsule_sha256()


def persist_working_checkpoint(
    store: GatewayStateStore,
    *,
    life_id: str,
    request_id: str,
    run_id: str,
    generation: int,
    user_goal: str,
    latest_safe_step: str,
    next_step: str,
    created_at_ms: int,
    hard_constraints: tuple[str, ...] = (),
    active_plan: tuple[str, ...] = (),
    verified_fact_ids: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    pending_effect_ids: tuple[str, ...] = (),
    recovery_preconditions: tuple[str, ...] = (),
) -> RequestCapsuleRecord:
    active = store.get_active_request_capsule(
        request_id, run_id=run_id, generation=generation
    )
    if active is not None:
        current = active.capsule
        if (
            current.capsule_kind == "WORKING_CHECKPOINT"
            and current.life_id == life_id
            and current.user_goal == user_goal
            and current.latest_safe_step == latest_safe_step
            and current.next_step == next_step
            and current.pending_effect_ids == tuple(sorted(set(pending_effect_ids)))
            and current.artifact_refs == tuple(sorted(set(artifact_refs)))
        ):
            return active
    continuation = canonical_sha256(
        {
            "artifact_refs": sorted(set(artifact_refs)),
            "latest_safe_step": latest_safe_step,
            "pending_effect_ids": sorted(set(pending_effect_ids)),
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation,
        }
    )
    capsule = build_task_continuity_capsule(
        life_id=life_id,
        capsule_kind="WORKING_CHECKPOINT",
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        user_goal=user_goal,
        hard_constraints=hard_constraints,
        active_plan=active_plan,
        verified_fact_ids=verified_fact_ids,
        artifact_refs=artifact_refs,
        pending_effect_ids=pending_effect_ids,
        latest_safe_step=latest_safe_step,
        next_step=next_step,
        recovery_preconditions=recovery_preconditions,
        continuation_token_sha256=continuation,
        supersedes_capsule_id=None if active is None else active.capsule.capsule_id,
        created_at_ms=created_at_ms,
    )
    return store.put_request_capsule(capsule)


def persist_compression_checkpoint(
    store: GatewayStateStore,
    *,
    life_id: str,
    request_id: str,
    run_id: str,
    generation: int,
    user_goal: str,
    latest_safe_step: str,
    next_step: str,
    created_at_ms: int,
    hard_constraints: tuple[str, ...] = (),
    active_plan: tuple[str, ...] = (),
    verified_fact_ids: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    pending_effect_ids: tuple[str, ...] = (),
    recovery_preconditions: tuple[str, ...] = (),
) -> RequestCapsuleRecord:
    active = store.get_active_request_capsule(
        request_id, run_id=run_id, generation=generation
    )
    if active is not None:
        current = active.capsule
        if (
            current.capsule_kind == "COMPRESSION_CHECKPOINT"
            and current.life_id == life_id
            and current.user_goal == user_goal
            and current.latest_safe_step == latest_safe_step
            and current.next_step == next_step
            and current.pending_effect_ids == tuple(sorted(set(pending_effect_ids)))
            and current.artifact_refs == tuple(sorted(set(artifact_refs)))
        ):
            return active
    continuation = canonical_sha256(
        {
            "artifact_refs": sorted(set(artifact_refs)),
            "latest_safe_step": latest_safe_step,
            "pending_effect_ids": sorted(set(pending_effect_ids)),
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation,
        }
    )
    capsule = build_task_continuity_capsule(
        life_id=life_id,
        capsule_kind="COMPRESSION_CHECKPOINT",
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        user_goal=user_goal,
        hard_constraints=hard_constraints,
        active_plan=active_plan,
        verified_fact_ids=verified_fact_ids,
        artifact_refs=artifact_refs,
        pending_effect_ids=pending_effect_ids,
        latest_safe_step=latest_safe_step,
        next_step=next_step,
        recovery_preconditions=recovery_preconditions,
        continuation_token_sha256=continuation,
        supersedes_capsule_id=None if active is None else active.capsule.capsule_id,
        created_at_ms=created_at_ms,
    )
    return store.put_request_capsule(capsule)


def persist_terminal_result(
    store: GatewayStateStore,
    *,
    life_id: str,
    request_id: str,
    run_id: str,
    generation: int,
    user_goal: str,
    final_result: str,
    created_at_ms: int,
    verified_fact_ids: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
) -> RequestCapsuleRecord:
    expected_fact_ids = tuple(sorted(set(verified_fact_ids)))
    expected_artifact_refs = tuple(sorted(set(artifact_refs)))
    terminal = store.get_terminal_request_capsule(
        request_id, run_id=run_id, generation=generation
    )
    if terminal is not None:
        current = terminal.capsule
        if (
            current.life_id == life_id
            and current.user_goal == user_goal
            and current.final_result == final_result
            and current.verified_fact_ids == expected_fact_ids
            and current.artifact_refs == expected_artifact_refs
        ):
            return RequestCapsuleRecord(
                capsule=current,
                status=terminal.status,
                created_by_this_call=False,
                duplicate=True,
            )
        raise StoreConflictError(
            "terminal continuity result conflicts with the durable terminal result"
        )
    active = store.get_active_request_capsule(
        request_id, run_id=run_id, generation=generation
    )
    capsule = build_task_continuity_capsule(
        life_id=life_id,
        capsule_kind="TERMINAL_RESULT",
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        user_goal=user_goal,
        verified_fact_ids=expected_fact_ids,
        artifact_refs=expected_artifact_refs,
        final_result=final_result,
        supersedes_capsule_id=None if active is None else active.capsule.capsule_id,
        created_at_ms=created_at_ms,
    )
    return store.put_request_capsule(capsule)


def persist_interruption_checkpoint(
    store: GatewayStateStore,
    *,
    request_id: str,
    run_id: str,
    generation: int,
    latest_safe_step: str,
    next_step: str,
    created_at_ms: int,
    verified_fact_ids: tuple[str, ...] = (),
    pending_effect_ids: tuple[str, ...] = (),
    recovery_preconditions: tuple[str, ...] = (),
) -> RequestCapsuleRecord | None:
    active = store.get_active_request_capsule(
        request_id, run_id=run_id, generation=generation
    )
    if active is None:
        return None
    current = active.capsule
    capsule = build_task_continuity_capsule(
        life_id=current.life_id,
        capsule_kind="WORKING_CHECKPOINT",
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        user_goal=current.user_goal,
        hard_constraints=current.hard_constraints,
        active_plan=current.active_plan,
        verified_fact_ids=tuple(sorted(set(
            (*current.verified_fact_ids, *verified_fact_ids)
        ))),
        causal_hypothesis_ids=current.causal_hypothesis_ids,
        workspace_manifest=current.workspace_manifest,
        artifact_refs=current.artifact_refs,
        unresolved_questions=current.unresolved_questions,
        pending_effect_ids=tuple(sorted(set(
            (*current.pending_effect_ids, *pending_effect_ids)
        ))),
        latest_safe_step=latest_safe_step,
        next_step=next_step,
        recovery_preconditions=recovery_preconditions,
        continuation_token_sha256=canonical_sha256(
            {
                "latest_safe_step": latest_safe_step,
                "next_step": next_step,
                "pending_effect_ids": sorted(set(
                    (*current.pending_effect_ids, *pending_effect_ids)
                )),
                "supersedes_capsule_id": current.capsule_id,
            }
        ),
        supersedes_capsule_id=current.capsule_id,
        created_at_ms=created_at_ms,
    )
    return store.put_request_capsule(capsule)


def persist_terminal_completion(
    store: GatewayStateStore,
    decision: CompletionDecision,
    *,
    life_id: str,
    user_goal: str,
    final_result: str,
    created_at_ms: int,
    verified_fact_ids: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
) -> RequestCapsuleRecord:
    if decision.outcome not in {"COMPLETED", "PARTIAL", "FAILED"}:
        raise ValueError("nonterminal completion decision requires a checkpoint")
    store.record_completion_decision(decision, recorded_at_ms=created_at_ms)
    return persist_terminal_result(
        store,
        life_id=life_id,
        request_id=decision.request_id,
        run_id=decision.run_id,
        generation=decision.generation,
        user_goal=user_goal,
        final_result=final_result,
        created_at_ms=created_at_ms,
        verified_fact_ids=tuple(sorted(set(
            (*verified_fact_ids, *decision.supporting_fact_ids)
        ))),
        artifact_refs=artifact_refs,
    )


__all__ = [
    "build_task_continuity_capsule",
    "persist_compression_checkpoint",
    "persist_interruption_checkpoint",
    "persist_terminal_result",
    "persist_terminal_completion",
    "persist_working_checkpoint",
]
