"""Pure P18-M2 runtime boundary helpers.

No persistence, scheduler, authority, or second Runtime lives here.  This file
only canonicalizes stable logical-effect identity and bounded Frontier payloads
before the existing zongdiaodu production path calls the Gateway-owned provider.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_logical_effect_id(
    *,
    request_id: str,
    run_id: str,
    generation: int,
    obligation_key: str,
    effect_namespace: str,
    normalized_target: str,
    desired_postcondition_sha256: str,
) -> str:
    return "lef_" + canonical_sha256({
        "domain": "tiangong.gateway.logical-effect-id.v1",
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "obligation_key": str(obligation_key or "").strip(),
        "effect_namespace": str(effect_namespace or "").strip(),
        "normalized_target": str(normalized_target or "").strip(),
        "desired_postcondition_sha256": str(desired_postcondition_sha256 or "").strip().lower(),
    })


def task_hashes(user_goal: str, task_contract: Mapping[str, Any]) -> tuple[str, str]:
    return (
        canonical_sha256({"domain": "tiangong.gateway.root-goal.v1", "user_goal": str(user_goal or "").strip()}),
        canonical_sha256({"domain": "tiangong.gateway.task-contract.v1", "task_contract": dict(task_contract)}),
    )


def tool_effect_descriptor(
    *,
    request_id: str,
    run_id: str,
    generation: int,
    tool_name: str,
    tool_args: Mapping[str, Any],
    attempted_action: str,
) -> dict[str, Any]:
    args = dict(tool_args or {})
    action = str(args.get("action") or args.get("operation") or "execute").strip() or "execute"
    target = ""
    for key in (
        "target", "path", "file_path", "filepath", "destination", "dest",
        "directory", "workspace", "url", "query", "command", "name",
    ):
        value = args.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            target = f"{key}:{str(value).strip()}"
            break
    if not target:
        target = "args:" + canonical_sha256(args)
    obligation_key = str(attempted_action or tool_name or "tool-step").strip()[:500]
    effect_namespace = f"{str(tool_name or 'tool').strip()}:{action}"[:500]
    desired_postcondition_sha256 = canonical_sha256({
        "domain": "tiangong.gateway.desired-tool-postcondition.v1",
        "tool_name": str(tool_name or "").strip(),
        "action": action,
        "target": target,
        "arguments": args,
        "attempted_action": str(attempted_action or "").strip(),
    })
    logical_effect_id = derive_logical_effect_id(
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        obligation_key=obligation_key,
        effect_namespace=effect_namespace,
        normalized_target=target,
        desired_postcondition_sha256=desired_postcondition_sha256,
    )
    return {
        "logical_effect_id": logical_effect_id,
        "obligation_key": obligation_key,
        "effect_namespace": effect_namespace,
        "normalized_target": target,
        "desired_postcondition_sha256": desired_postcondition_sha256,
    }


def _sorted_unique(values: Any, *, limit: int) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in values if str(item).strip()})[:limit]


def build_frontier_payload(
    *,
    request_id: str,
    run_id: str,
    generation: int,
    life_id: str,
    root_goal_hash: str,
    task_contract_hash: str,
    authority_hash: str,
    global_step: int,
    epoch_index: int,
    epoch_step: int,
    frontier_version: int,
    completed_obligation_ids: Any = (),
    active_obligation_id: str | None = None,
    pending_obligation_ids: Any = (),
    pending_effect_ids: Any = (),
    ambiguous_effect_ids: Any = (),
    active_blockers: Any = (),
    failed_strategy_ids: Any = (),
    verified_fact_head: str | None = None,
    artifact_revision_head: str | None = None,
    latest_safe_step: str = "execution state is durably observed",
    next_action_hint: str = "continue authoritative execution",
    provider_turn_state_ref: str | None = None,
) -> dict[str, Any]:
    frontier = {
        "schema_version": "tiangong.gateway.execution-frontier.v1",
        "request_id": request_id,
        "run_id": run_id,
        "generation": int(generation),
        "life_id": life_id,
        "root_goal_hash": root_goal_hash,
        "task_contract_hash": task_contract_hash,
        "authority_hash": authority_hash,
        "global_step": max(0, int(global_step)),
        "epoch_index": max(0, int(epoch_index)),
        "epoch_step": max(0, int(epoch_step)),
        "completed_obligation_ids": _sorted_unique(completed_obligation_ids, limit=512),
        "active_obligation_id": str(active_obligation_id).strip()[:200] if active_obligation_id else None,
        "pending_obligation_ids": _sorted_unique(pending_obligation_ids, limit=512),
        "verified_fact_head": str(verified_fact_head).strip()[:200] if verified_fact_head else None,
        "artifact_revision_head": str(artifact_revision_head).strip()[:200] if artifact_revision_head else None,
        "pending_effect_ids": _sorted_unique(pending_effect_ids, limit=512),
        "ambiguous_effect_ids": _sorted_unique(ambiguous_effect_ids, limit=512),
        "active_blockers": _sorted_unique(active_blockers, limit=128),
        "failed_strategy_ids": _sorted_unique(failed_strategy_ids, limit=256),
        "latest_safe_step": str(latest_safe_step or "execution state is durably observed")[:1000],
        "next_action_hint": str(next_action_hint or "continue authoritative execution")[:1000],
        "provider_turn_state_ref": str(provider_turn_state_ref).strip()[:500] if provider_turn_state_ref else None,
        "frontier_version": max(1, int(frontier_version)),
        "frontier_hash": "0" * 64,
    }
    frontier["frontier_hash"] = canonical_sha256({key: value for key, value in frontier.items() if key != "frontier_hash"})
    return frontier


def bounded_history(values: list[Any], *, limit: int = 24) -> list[Any]:
    if limit < 1:
        raise ValueError("history limit must be positive")
    if len(values) <= limit:
        return values
    del values[:-limit]
    return values


__all__ = [
    "bounded_history",
    "build_frontier_payload",
    "canonical_sha256",
    "derive_logical_effect_id",
    "task_hashes",
    "tool_effect_descriptor",
]
