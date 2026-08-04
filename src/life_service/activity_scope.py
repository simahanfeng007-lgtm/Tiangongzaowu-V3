"""Deterministic activity-range projection for the embedded Life kernel.

The activity range is a *snapshot of evidence available to a learning
decision*, not a second memory database and never a copy of credentials or
runtime settings.  A model may choose a learning path from this projection,
but the kernel remains the authority that validates and publishes the result.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from contracts import canonical_sha256
from .panel_projection import (
    boundary_projection,
    long_term_goals,
    preference_projection,
)


ACTIVITY_SCOPE_SCHEMA = "tiangong.life.activity-scope.v1"
_TERM = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")
_SECRET_KEYS = {"api_key", "apikey", "token", "password", "secret", "credential"}


def _terms(value: Any, *, limit: int = 24) -> list[str]:
    if not isinstance(value, str):
        return []
    found: list[str] = []
    for item in _TERM.findall(value):
        token = item.casefold()
        if token not in found:
            found.append(token)
        if len(found) >= limit:
            break
    return found


def _safe_soul(soul: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(soul or {})
    return {
        key: deepcopy(source.get(key))
        for key in ("prompt", "values", "boundaries", "revision", "revision_id")
        if key in source
    }


def _canonical_safe(value: Any) -> Any:
    """Convert UI ratios to deterministic decimal strings for signed scope."""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, Mapping):
        return {str(key): _canonical_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_safe(item) for item in value]
    return deepcopy(value)


def _memory_refs(scope: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    terms: list[str] = []
    memories = scope.get("memories") if isinstance(scope.get("memories"), Mapping) else {}
    for memory_id, raw in list(memories.items())[-64:]:
        if not isinstance(raw, Mapping) or str(raw.get("status") or "active") != "active":
            continue
        classification = raw.get("classification") if isinstance(raw.get("classification"), Mapping) else {}
        lifecycle = raw.get("lifecycle") if isinstance(raw.get("lifecycle"), Mapping) else {}
        # Frozen records remain durable, but are intentionally not part of an
        # unprompted learning decision.  A query/association must recall them
        # first, preventing autonomous learning from mining cold history.
        if str(lifecycle.get("state") or "active") == "frozen":
            continue
        row = {
            "memory_id": str(raw.get("memory_id") or memory_id),
            "memory_type": str(classification.get("memory_type") or raw.get("memory_type") or "semantic"),
            "causal_role": str(classification.get("causal_role") or "context"),
            "content": deepcopy(raw.get("content")),
            "provenance": deepcopy(raw.get("provenance") or {}),
            "priority": int(raw.get("priority") or 0),
            "confidence_milli": int(raw.get("confidence_milli") or 0),
            "causal_refs": list(classification.get("causal_refs") or [])[:12],
            "lifecycle": {
                "state": str(lifecycle.get("state") or "active"),
                "heat_milli": int(lifecycle.get("heat_milli") or 0),
                "recall_count": int(lifecycle.get("recall_count") or 0),
                "trigger_terms": list(lifecycle.get("trigger_terms") or [])[:12],
            },
        }
        rows.append(row)
        terms.extend(_terms(str(raw.get("content") or "")))
        terms.extend(str(item) for item in lifecycle.get("trigger_terms") or [])
    return rows[-24:], list(dict.fromkeys(terms))[:48]


def build_activity_scope(*, life_id: str, soul: Mapping[str, Any] | None, scope: Mapping[str, Any]) -> dict[str, Any]:
    """Build a bounded, canonical evidence projection for one learning turn."""
    memory_rows, memory_terms = _memory_refs(scope)
    autonomy = scope.get("autonomy") if isinstance(scope.get("autonomy"), Mapping) else {}
    active_tasks = [
        {
            "task_id": str(row.get("task_id") or task_id),
            "task_kind": str(row.get("task_kind") or ""),
            "objective": str(row.get("objective") or ""),
            "risk_class": str(row.get("risk_class") or "A0"),
            "fingerprint": str(row.get("fingerprint") or ""),
        }
        for task_id, row in (autonomy.get("tasks") or {}).items()
        if isinstance(row, Mapping) and str(row.get("status") or "") in {"pending", "running", "blocked", "awaiting_user"}
    ][-24:]
    capabilities = [
        {
            "artifact_id": str(row.get("artifact_id") or artifact_id),
            "kind": str(row.get("kind") or row.get("target") or "skill"),
            "title": str(row.get("title") or ""),
            "status": str(row.get("status") or ""),
            "version": str(row.get("version") or ""),
        }
        for artifact_id, row in (scope.get("capabilities") or {}).items()
        if isinstance(row, Mapping) and str(row.get("status") or "") not in {"discarded", "rolled_back"}
    ][-48:]
    rejected = [
        str(row.get("fingerprint") or "")
        for row in (scope.get("learning") or {}).values()
        if isinstance(row, Mapping) and str(row.get("status") or "") == "discarded" and row.get("fingerprint")
    ][-128:]
    soul_view = _safe_soul(soul)
    settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
    goals = _canonical_safe(long_term_goals())
    preferences = _canonical_safe(preference_projection(
        list(settings.get("autonomy_activity_types") or [])
    ))
    declared_boundaries = [
        str(value)
        for value in soul_view.get("boundaries", [])
        if str(value).strip()
    ]
    boundaries = _canonical_safe(boundary_projection(settings, declared_boundaries))
    terms = memory_terms + _terms(str(soul_view.get("prompt") or ""))
    for task in active_tasks:
        terms.extend(_terms(task["objective"]))
    for capability in capabilities:
        terms.extend(_terms(capability["title"]))
    for goal in goals:
        terms.extend(_terms(str(goal.get("title") or "")))
        terms.extend(_terms(str(goal.get("description") or "")))
    result = {
        "schema": ACTIVITY_SCOPE_SCHEMA,
        "life_id": str(life_id),
        "soul": soul_view,
        "recent_memories": memory_rows,
        "active_tasks": active_tasks,
        "capabilities": capabilities,
        "long_term_goals": goals,
        "preferences": preferences,
        "boundaries": boundaries,
        "rejected_learning_fingerprints": sorted(set(item for item in rejected if item)),
        "topics": list(dict.fromkeys(terms))[:64],
        "source_refs": {
            "memory_ids": [row["memory_id"] for row in memory_rows],
            "task_ids": [row["task_id"] for row in active_tasks],
            "capability_ids": [row["artifact_id"] for row in capabilities],
        },
    }
    # Sanity check prevents future additions from accidentally putting runtime
    # credentials into a model-facing projection.
    serialized_keys = {str(key).casefold() for key in result.keys()}
    if serialized_keys & _SECRET_KEYS:
        raise ValueError("activity scope contains a credential-like key")
    result["scope_sha256"] = canonical_sha256({"domain": ACTIVITY_SCOPE_SCHEMA, "scope": result})
    return result


__all__ = ["ACTIVITY_SCOPE_SCHEMA", "build_activity_scope"]
