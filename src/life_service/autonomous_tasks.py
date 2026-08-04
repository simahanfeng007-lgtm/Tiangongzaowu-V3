"""Deterministic autonomous task proposal engine for the LifeKernel.

This module creates *task proposals*, not execution authority.  Every proposed
external action still has to pass the Runtime Policy/Ticket/Grant chain.  The
engine is local, replayable, bounded and safe to host in either embedded or
standalone mode.
"""
from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Mapping

from contracts import canonical_sha256

AUTONOMY_ENGINE_VERSION = "tiangong.life.autonomy-task-engine.v1"
TERMINAL_TASK_STATES = {"completed", "failed", "cancelled", "rejected"}
ACTIVE_TASK_STATES = {"pending", "running", "blocked", "awaiting_user"}
ALLOWED_TASK_STATES = TERMINAL_TASK_STATES | ACTIVE_TASK_STATES

# This is a catalog of bounded internal activities, not a second scheduler and
# not an execution-tool registry.  The Life scheduler selects from it; any
# external side effect still has to cross the Gateway policy/ticket/grant chain.
_ACTIVITY_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "activity_id": "daily_planning",
        "label": "今日规划",
        "description": "根据当前状态整理今天值得推进的事项与先后顺序。",
        "objective": "形成一份简短、可执行且不打扰用户的今日计划。",
        "proposed_action": "梳理今日优先事项",
        "window": "上午",
        "priority": 820,
        "risk_class": "A0",
    },
    {
        "activity_id": "self_reflection",
        "label": "行动反思",
        "description": "复盘最近行动的结果、偏差与可以改进的地方。",
        "objective": "从最近的真实行动中提炼下一次可以做得更好的具体改进。",
        "proposed_action": "复盘最近行动",
        "window": "傍晚",
        "priority": 760,
        "risk_class": "A0",
    },
    {
        "activity_id": "goal_progress",
        "label": "目标推进",
        "description": "检查长期目标是否有可推进的一小步，避免无目的随机行动。",
        "objective": "找出一个与长期方向一致、当天可以完成的小步骤。",
        "proposed_action": "检查目标进度并提出下一步",
        "window": "白天",
        "priority": 740,
        "risk_class": "A0",
    },
    {
        "activity_id": "relationship_care",
        "label": "关系关怀",
        "description": "理解用户近期关注点，准备克制且有价值的关怀建议。",
        "objective": "在不打扰、不替用户做决定的前提下改善沟通质量。",
        "proposed_action": "整理关系线索与关怀建议",
        "window": "白天",
        "priority": 700,
        "risk_class": "A1",
    },
    {
        "activity_id": "knowledge_organization",
        "label": "知识整理",
        "description": "整理已有知识、主题与待核实问题，不局限于记忆数量。",
        "objective": "把分散信息整理为更清楚的主题、问题和后续查证方向。",
        "proposed_action": "整理知识主题与待核实项",
        "window": "下午",
        "priority": 680,
        "risk_class": "A0",
    },
    {
        "activity_id": "learning_review",
        "label": "学习复盘",
        "description": "检查近期学习候选和能力缺口，提出值得学习的方向。",
        "objective": "识别一个有证据、有用途且不会重复建设的学习方向。",
        "proposed_action": "复核学习候选与能力缺口",
        "window": "下午",
        "priority": 660,
        "risk_class": "A0",
    },
    {
        "activity_id": "capability_inventory",
        "label": "能力盘点",
        "description": "盘点已具备、待激活和缺失的能力，避免能力映射失真。",
        "objective": "形成当前能力边界和缺口的真实清单。",
        "proposed_action": "盘点当前能力边界",
        "window": "白天",
        "priority": 640,
        "risk_class": "A0",
    },
    {
        "activity_id": "system_health",
        "label": "生命自检",
        "description": "检查生命心跳、任务队列、能力映射与执行记录是否健康。",
        "objective": "发现可验证的运行异常并给出安全的修复建议。",
        "proposed_action": "检查生命系统健康状态",
        "window": "白天",
        "priority": 720,
        "risk_class": "A0",
    },
    {
        "activity_id": "workspace_hygiene",
        "label": "工作区整理建议",
        "description": "只诊断重复、过期或散乱内容并提出整理建议，不自动删除文件。",
        "objective": "给出可回退的工作区整理建议，保持用户数据不受触碰。",
        "proposed_action": "诊断工作区整理机会",
        "window": "下午",
        "priority": 560,
        "risk_class": "A1",
    },
    {
        "activity_id": "creative_exploration",
        "label": "好奇探索",
        "description": "围绕当前主题提出新问题、新联系或可验证的小实验。",
        "objective": "产生一个有意义、可验证且不越权的探索方向。",
        "proposed_action": "探索一个值得验证的新方向",
        "window": "空闲时",
        "priority": 520,
        "risk_class": "A0",
    },
    {
        "activity_id": "end_of_day_summary",
        "label": "今日小结",
        "description": "在一天结束时总结完成事项、未决问题与明日线索。",
        "objective": "形成简洁的今日小结和明日接续点。",
        "proposed_action": "总结今天并准备明日接续",
        "window": "晚间",
        "priority": 500,
        "risk_class": "A0",
    },
)
DEFAULT_ACTIVITY_TYPES = tuple(str(item["activity_id"]) for item in _ACTIVITY_CATALOG)
ACTIVITY_TYPE_IDS = frozenset(DEFAULT_ACTIVITY_TYPES)


def autonomy_activity_catalog() -> list[dict[str, Any]]:
    """Return the UI-safe, immutable-definition projection of the catalog."""
    return [deepcopy(item) for item in _ACTIVITY_CATALOG]


def normalize_activity_types(value: Any) -> list[str]:
    """Normalize a per-life multi-selection without accepting unknown actions."""
    if value is None:
        return list(DEFAULT_ACTIVITY_TYPES)
    if not isinstance(value, list):
        raise ValueError("autonomy activity types are invalid")
    selected: list[str] = []
    for item in value:
        activity_id = str(item or "").strip()
        if activity_id not in ACTIVITY_TYPE_IDS:
            raise ValueError("autonomy activity type is unknown")
        if activity_id not in selected:
            selected.append(activity_id)
    return selected


def default_autonomy_state() -> dict[str, Any]:
    return {
        "schema": AUTONOMY_ENGINE_VERSION,
        "enabled": True,
        "task_generation_enabled": True,
        "generation_count": 0,
        "task_sequence": 0,
        "last_tick_at_ms": 0,
        "last_tick_reason": "",
        "last_error_code": "",
        "tasks": {},
        "pending_limit": 64,
        "generated_total": 0,
        "completed_total": 0,
        "failed_total": 0,
    }


def normalize_autonomy_state(value: Any) -> dict[str, Any]:
    defaults = default_autonomy_state()
    if not isinstance(value, dict):
        return defaults
    for key, fallback in defaults.items():
        value.setdefault(key, deepcopy(fallback))
    if not isinstance(value.get("tasks"), dict):
        raise ValueError("autonomy task state is invalid")
    pending_limit = value.get("pending_limit")
    if isinstance(pending_limit, bool) or not isinstance(pending_limit, int):
        raise ValueError("autonomy pending limit is invalid")
    if pending_limit < 0 or pending_limit > 1024:
        raise ValueError("autonomy pending limit is out of bounds")
    for key in ("generation_count", "task_sequence", "generated_total", "completed_total", "failed_total", "last_tick_at_ms"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ValueError(f"autonomy counter {key} is invalid")
    return value


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _task_fingerprint(kind: str, subject_refs: list[str], causal_basis: list[str]) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.life.autonomy-task-fingerprint.v1",
            "task_kind": kind,
            "subject_refs": sorted(set(subject_refs)),
            "causal_basis": sorted(set(causal_basis)),
        }
    )


def _candidate(
    *,
    kind: str,
    objective: str,
    proposed_action: str,
    subject_refs: list[str],
    causal_basis: list[str],
    priority: int,
    risk_class: str = "A0",
    requires_user: bool = False,
) -> dict[str, Any]:
    fingerprint = _task_fingerprint(kind, subject_refs, causal_basis)
    return {
        "task_kind": kind,
        "objective": objective,
        "proposed_action": proposed_action,
        "subject_refs": sorted(set(subject_refs)),
        "causal_basis": sorted(set(causal_basis)),
        "priority": max(-10_000, min(10_000, int(priority))),
        "risk_class": risk_class,
        "requires_user": bool(requires_user),
        "fingerprint": fingerprint,
    }


def derive_task_candidates(
    scope: Mapping[str, Any],
    *,
    life_id: str,
    day_key: str = "",
) -> list[dict[str, Any]]:
    memories = scope.get("memories") if isinstance(scope.get("memories"), Mapping) else {}
    relations = list(scope.get("memory_relations")) if isinstance(scope.get("memory_relations"), list) else []
    # Assertions may carry relations inside the immutable journal row.  Merge
    # them into the task-generation view so a complete cause/effect link does
    # not generate a false follow-up merely because no separate relation API
    # call was made.
    for memory_id, row in memories.items():
        if not isinstance(row, Mapping):
            continue
        embedded = row.get("relations")
        if not isinstance(embedded, list):
            continue
        for relation in embedded:
            if not isinstance(relation, Mapping):
                continue
            relations.append(
                {
                    **dict(relation),
                    "source_memory_id": str(relation.get("source_memory_id") or memory_id),
                    "target_ref": str(relation.get("target_ref") or relation.get("target_memory_id") or ""),
                }
            )
    active_memory_ids = {
        str(memory_id)
        for memory_id, row in memories.items()
        if isinstance(row, Mapping) and str(row.get("status") or "active") == "active"
    }
    filtered_relations: list[dict[str, Any]] = []
    for relation in relations:
        if not isinstance(relation, Mapping):
            continue
        source = str(relation.get("source_memory_id") or "")
        target = str(relation.get("target_ref") or relation.get("target_memory_id") or "")
        if source and source not in active_memory_ids:
            continue
        if target.startswith("mem_") and target not in active_memory_ids:
            continue
        filtered_relations.append(dict(relation))
    relations = filtered_relations
    learning = scope.get("learning") if isinstance(scope.get("learning"), Mapping) else {}
    capabilities = scope.get("capabilities") if isinstance(scope.get("capabilities"), Mapping) else {}
    candidates: list[dict[str, Any]] = []

    active_memories = [
        row for row in memories.values()
        if isinstance(row, Mapping) and str(row.get("status") or "active") == "active"
    ]
    if not active_memories:
        candidates.append(
            _candidate(
                kind="establish_memory_baseline",
                objective="Establish a minimal verified memory baseline for the active life identity.",
                proposed_action="inspect_life_context",
                subject_refs=[life_id],
                causal_basis=["memory.total=0"],
                priority=1200,
                risk_class="A0",
            )
        )
    else:
        for row in active_memories:
            memory_id = str(row.get("memory_id") or "")
            classification = row.get("classification") if isinstance(row.get("classification"), Mapping) else {}
            causal_role = str(classification.get("causal_role") or "context")
            epistemic = str(row.get("epistemic_status") or "user_asserted")
            confidence = int(row.get("confidence_milli") or 0)
            priority = int(row.get("priority") or 0)
            if epistemic == "hypothesis" or confidence < 500:
                candidates.append(
                    _candidate(
                        kind="verify_memory_hypothesis",
                        objective="Collect evidence before promoting a low-confidence memory.",
                        proposed_action="review_memory_evidence",
                        subject_refs=[memory_id],
                        causal_basis=[f"epistemic={epistemic}", f"confidence={confidence}"],
                        priority=max(1600, priority),
                        risk_class="A0",
                    )
                )
            if causal_role == "cause":
                has_effect = any(
                    isinstance(rel, Mapping)
                    and str(rel.get("source_memory_id") or "") == memory_id
                    and str(rel.get("kind") or "") in {"causes", "leads_to", "triggers", "enables", "prevents"}
                    for rel in relations
                )
                if not has_effect:
                    candidates.append(
                        _candidate(
                            kind="complete_causal_link",
                            objective="Find or verify the effect associated with a recorded cause.",
                            proposed_action="inspect_related_memories",
                            subject_refs=[memory_id],
                            causal_basis=["causal_role=cause", "outgoing_effect_missing"],
                            priority=max(1800, priority),
                            risk_class="A0",
                        )
                    )
            if causal_role in {"effect", "outcome"}:
                has_cause = any(
                    isinstance(rel, Mapping)
                    and str(rel.get("target_ref") or rel.get("target_memory_id") or "") == memory_id
                    and str(rel.get("kind") or "") in {"causes", "leads_to", "triggers", "enables", "prevents"}
                    for rel in relations
                )
                if not has_cause:
                    candidates.append(
                        _candidate(
                            kind="identify_root_cause",
                            objective="Identify evidence-backed causes for an observed outcome.",
                            proposed_action="inspect_prior_events",
                            subject_refs=[memory_id],
                            causal_basis=[f"causal_role={causal_role}", "incoming_cause_missing"],
                            priority=max(1900, priority),
                            risk_class="A0",
                        )
                    )
            if row.get("status") == "corrected":
                candidates.append(
                    _candidate(
                        kind="reconcile_corrected_memory",
                        objective="Ensure downstream context no longer relies on a corrected assertion.",
                        proposed_action="rebuild_memory_context",
                        subject_refs=[memory_id],
                        causal_basis=["memory.status=corrected"],
                        priority=max(1700, priority),
                        risk_class="A0",
                    )
                )

    for relation in relations:
        if not isinstance(relation, Mapping) or str(relation.get("kind") or "") != "contradicts":
            continue
        source = str(relation.get("source_memory_id") or "")
        target = str(relation.get("target_ref") or relation.get("target_memory_id") or "")
        candidates.append(
            _candidate(
                kind="resolve_memory_contradiction",
                objective="Resolve contradictory assertions before they influence planning.",
                proposed_action="compare_memory_evidence",
                subject_refs=[item for item in (source, target) if item],
                causal_basis=["relation=contradicts"],
                priority=2400,
                risk_class="A1",
                # A0--A2 are automatic.  Learning publication confirmation is
                # handled separately at A3--A5.
                requires_user=False,
            )
        )

    for learning_id, row in learning.items():
        if isinstance(row, Mapping) and str(row.get("status") or "") in {"candidate", "pending", "proposed"}:
            candidates.append(
                _candidate(
                    kind="review_learning_candidate",
                    objective="Evaluate pending learning evidence before activation.",
                    proposed_action="review_learning_evidence",
                    subject_refs=[str(learning_id)],
                    causal_basis=["learning.status=pending"],
                    priority=1300,
                    risk_class="A0",
                )
            )
    for capability_id, row in capabilities.items():
        if isinstance(row, Mapping) and str(row.get("status") or "") in {"candidate", "build", "proposed"}:
            candidates.append(
                _candidate(
                    kind="review_capability_candidate",
                    objective="Verify capability evidence before publication.",
                    proposed_action="review_capability_evidence",
                    subject_refs=[str(capability_id)],
                    causal_basis=["capability.status=pending"],
                    priority=1300,
                    risk_class="A0",
                )
            )

    # Built-in activities are daily, bounded proposals from the same scheduler.
    # A day key belongs in the causal basis so yesterday's unfinished proposal
    # is cancelled at the next tick and today's proposal has a new fingerprint.
    # For a brand-new life, first establish the memory/context baseline instead
    # of flooding it with a full plan on its first heartbeat.
    settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
    selected_activity_types = normalize_activity_types(
        settings.get("autonomy_activity_types")
    )
    if active_memories and day_key and selected_activity_types:
        catalog_by_id = {
            str(item["activity_id"]): item
            for item in _ACTIVITY_CATALOG
        }
        for activity_id in selected_activity_types:
            definition = catalog_by_id[activity_id]
            candidates.append(
                {
                    **_candidate(
                        kind=f"life_activity.{activity_id}",
                        objective=str(definition["objective"]),
                        proposed_action=str(definition["proposed_action"]),
                        subject_refs=[life_id],
                        causal_basis=[
                            f"activity={activity_id}",
                            f"day={day_key}",
                            "source=life_activity_catalog",
                        ],
                        priority=int(definition["priority"]),
                        risk_class=str(definition["risk_class"]),
                    ),
                    "source": "life_activity_catalog",
                    "activity_id": activity_id,
                    "title": str(definition["label"]),
                    "summary": str(definition["description"]),
                    "time_window": str(definition["window"]),
                    "execution_mode": "model_internal",
                }
            )

    # Stable order is part of replay determinism.
    unique: dict[str, dict[str, Any]] = {}
    for item in candidates:
        unique.setdefault(str(item["fingerprint"]), item)
    return sorted(
        unique.values(),
        key=lambda item: (-int(item["priority"]), str(item["task_kind"]), str(item["fingerprint"])),
    )


def materialize_tasks(
    state: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    life_id: str,
    reason: str,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    state = normalize_autonomy_state(state)
    if not state.get("enabled") or not state.get("task_generation_enabled"):
        return []
    now_ms = _now_ms() if now_ms is None else int(now_ms)
    now_ms = max(int(state.get("last_tick_at_ms") or 0), now_ms)
    tasks = state["tasks"]
    existing_fingerprints = {
        str(task.get("fingerprint") or "")
        for task in tasks.values()
        if isinstance(task, Mapping)
    }
    active_fingerprints = {
        str(task.get("fingerprint") or "")
        for task in tasks.values()
        if isinstance(task, Mapping) and str(task.get("status") or "") in ACTIVE_TASK_STATES
    }
    pending_count = len(active_fingerprints)
    available = max(0, int(state.get("pending_limit")) - pending_count)
    created: list[dict[str, Any]] = []
    for candidate in candidates:
        if available <= 0:
            break
        fingerprint = str(candidate["fingerprint"])
        # The causal fingerprint is a durable idempotency key.  Recurring
        # catalog activities receive a new day in their causal basis; all
        # other resolved work must not be recreated after reaching terminal.
        if fingerprint in existing_fingerprints:
            continue
        sequence = int(state.get("task_sequence") or 0) + 1
        task_id = "lat_" + canonical_sha256(
            {
                "domain": "tiangong.life.autonomy-task-id.v1",
                "life_id": life_id,
                "fingerprint": fingerprint,
                "sequence": sequence,
            }
        )
        task = {
            "schema": AUTONOMY_ENGINE_VERSION,
            "task_id": task_id,
            "life_id": life_id,
            **candidate,
            "status": "awaiting_user" if candidate.get("requires_user") else "pending",
            "generation_reason": str(reason or "scheduled")[:80],
            "sequence": sequence,
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "attempt_count": 0,
        }
        task["task_sha256"] = canonical_sha256(task)
        tasks[task_id] = task
        state["task_sequence"] = sequence
        state["generated_total"] = int(state.get("generated_total") or 0) + 1
        active_fingerprints.add(fingerprint)
        existing_fingerprints.add(fingerprint)
        created.append(deepcopy(task))
        available -= 1
    state["generation_count"] = int(state.get("generation_count") or 0) + 1
    state["last_tick_at_ms"] = now_ms
    state["last_tick_reason"] = str(reason or "scheduled")[:80]
    state["last_error_code"] = ""
    return created


def update_task_status(
    state: dict[str, Any],
    *,
    task_id: str,
    status: str,
    now_ms: int | None = None,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = normalize_autonomy_state(state)
    if status not in ALLOWED_TASK_STATES:
        raise ValueError("autonomy task status is invalid")
    task = state["tasks"].get(task_id)
    if not isinstance(task, dict):
        raise KeyError(task_id)
    previous = str(task.get("status") or "pending")
    if previous in TERMINAL_TASK_STATES and status != previous:
        raise ValueError("terminal autonomy task cannot transition")
    allowed = {
        "pending": {"running", "blocked", "awaiting_user", "completed", "cancelled", "failed", "rejected"},
        "running": {"blocked", "awaiting_user", "completed", "cancelled", "failed", "rejected"},
        "blocked": {"pending", "running", "awaiting_user", "cancelled", "failed", "rejected"},
        "awaiting_user": {"pending", "running", "cancelled", "rejected"},
    }
    if previous != status and status not in allowed.get(previous, set()):
        raise ValueError("autonomy task transition is invalid")
    now_ms = _now_ms() if now_ms is None else int(now_ms)
    now_ms = max(int(task.get("updated_at_ms") or task.get("created_at_ms") or 0), now_ms)
    task["status"] = status
    task["updated_at_ms"] = now_ms
    if status == "running" and previous != "running":
        task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
    if result is not None:
        task["result"] = deepcopy(dict(result))
    task["task_sha256"] = canonical_sha256({key: value for key, value in task.items() if key != "task_sha256"})
    if previous not in TERMINAL_TASK_STATES and status == "completed":
        state["completed_total"] = int(state.get("completed_total") or 0) + 1
    if previous not in TERMINAL_TASK_STATES and status == "failed":
        state["failed_total"] = int(state.get("failed_total") or 0) + 1
    return deepcopy(task)


__all__ = [
    "ACTIVE_TASK_STATES",
    "ALLOWED_TASK_STATES",
    "AUTONOMY_ENGINE_VERSION",
    "ACTIVITY_TYPE_IDS",
    "DEFAULT_ACTIVITY_TYPES",
    "TERMINAL_TASK_STATES",
    "autonomy_activity_catalog",
    "default_autonomy_state",
    "derive_task_candidates",
    "materialize_tasks",
    "normalize_activity_types",
    "normalize_autonomy_state",
    "update_task_status",
]
