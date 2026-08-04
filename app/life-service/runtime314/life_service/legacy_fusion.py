"""Lossless, side-effect-free projections for legacy life subsystems.

The former life runtime kept the daily plan, relationship hints and ``shenti``
input in independent JSON files.  The source-owned runtime must not revive
those writers.  This module instead defines their stable representation inside
the single-writer life state and projects it for the UI and agency pipeline.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def default_schedule() -> dict[str, Any]:
    return {"date": "", "mode": "embedded_autonomy", "summary": "", "tasks": {}}


def default_body() -> dict[str, Any]:
    return {
        "schema": "tiangong.life.body-state.v1",
        "available": True,
        "source": "life_state",
        "profile": {"body_preset": "standard"},
        "signals": {"energy_milli": 500, "load_milli": 0, "availability": "ready"},
        "updated_at": "",
    }


def normalize_schedule(value: Any, *, today: str, autonomy_tasks: list[Mapping[str, Any]]) -> dict[str, Any]:
    raw = deepcopy(value) if isinstance(value, Mapping) else default_schedule()
    stored_date = str(raw.get("date") or "")
    if stored_date and stored_date != today:
        # A daily projection must never present yesterday's plan as today's.
        # Pending autonomous tasks are re-added below from their authoritative
        # queue; stale plan-only rows and summaries do not cross the day
        # boundary.
        raw = default_schedule()
    raw["date"] = today
    raw["mode"] = str(raw.get("mode") or "embedded_autonomy")
    raw["summary"] = str(raw.get("summary") or "")
    stored = raw.get("tasks") if isinstance(raw.get("tasks"), Mapping) else {}
    active_autonomy_ids = {
        str(item.get("task_id") or item.get("id") or "")
        for item in autonomy_tasks
        if str(item.get("task_id") or item.get("id") or "")
    }
    rows: dict[str, dict[str, Any]] = {
        str(key): deepcopy(item) for key, item in stored.items()
        if isinstance(item, Mapping)
        # Generated Life tasks are a projection of the authoritative active
        # queue, not durable plan rows.  Remove completed/cancelled/unselected
        # projections while retaining explicitly authored legacy-plan rows.
        and (not str(key).startswith("lat_") or str(key) in active_autonomy_ids)
    }
    # Preserve explicit legacy-plan tasks first.  Then expose generated new
    # autonomy tasks in the same schedule without fabricating duplicates.
    for item in autonomy_tasks:
        task_id = str(item.get("task_id") or item.get("id") or "")
        if task_id and task_id not in rows:
            rows[task_id] = deepcopy(dict(item))
    raw["tasks"] = rows
    return raw


def normalize_body(value: Any, *, updated_at: str) -> dict[str, Any]:
    result = default_body()
    if isinstance(value, Mapping):
        for key in ("schema", "available", "source", "profile", "signals", "updated_at"):
            if key in value:
                result[key] = deepcopy(value[key])
    result["schema"] = "tiangong.life.body-state.v1"
    result["available"] = bool(result.get("available", True))
    result["source"] = str(result.get("source") or "life_state")
    result["profile"] = deepcopy(result["profile"]) if isinstance(result.get("profile"), Mapping) else {"body_preset": "standard"}
    signals = result.get("signals") if isinstance(result.get("signals"), Mapping) else {}
    normalized_signals: dict[str, Any] = {}
    for key in ("energy_milli", "load_milli"):
        try:
            normalized_signals[key] = min(1000, max(0, int(signals.get(key, 500 if key == "energy_milli" else 0))))
        except (TypeError, ValueError):
            normalized_signals[key] = 500 if key == "energy_milli" else 0
    normalized_signals["availability"] = str(signals.get("availability") or "ready")
    result["signals"] = normalized_signals
    result["updated_at"] = str(result.get("updated_at") or updated_at)
    return result


def relationship_projection(value: Any, memories: Mapping[str, Any], *, updated_at: str) -> dict[str, Any]:
    rows = deepcopy(value) if isinstance(value, Mapping) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for relationship_id, item in rows.items():
        if isinstance(item, Mapping) and str(relationship_id):
            normalized[str(relationship_id)] = deepcopy(dict(item))
    relational_memory_count = sum(
        1 for item in memories.values()
        if isinstance(item, Mapping) and str(item.get("memory_type") or "") == "relationship"
        and str(item.get("status") or "active") != "deleted"
    )
    return {
        "by_id": normalized,
        "count": len(normalized),
        "relational_memory_count": relational_memory_count,
        "updated_at": max((str(item.get("updated_at") or "") for item in normalized.values()), default=updated_at),
        "source": "life_state",
    }
