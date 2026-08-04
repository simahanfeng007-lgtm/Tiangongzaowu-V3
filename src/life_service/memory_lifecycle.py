"""Lifecycle metadata for the authoritative causal-memory projection.

The previous Life implementation had useful operational ideas (heat decay,
freezing, and cue-driven recall) but encoded them as fixed folders.  The new
Life owns one causal record model, so lifecycle is attached to each record
instead.  This keeps memory type, causal relations, and storage authority
independent from a particular number of ``levels``.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


MEMORY_LIFECYCLE_SCHEMA = "tiangong.life.memory-lifecycle.v1"
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{1,31}|[\u4e00-\u9fff]{2,8}")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    return str(value or "")


def _terms(content: Any) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in _TOKEN.findall(_text(content).casefold()):
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= 16:
            break
    return terms


def _policy(retention_class: str) -> tuple[int, int, int, int]:
    """Return initial heat, daily decay, freeze floor, and minimum age days."""

    if retention_class == "ACTIVE_WORKING":
        return (850, 180, 120, 3)
    if retention_class == "LONG_TERM_MEMORY":
        return (760, 12, 80, 30)
    return (700, 55, 100, 14)


def initial_lifecycle(
    *,
    classification: Mapping[str, Any],
    content: Any,
    priority: int,
    confidence_milli: int,
    at_ms: int | None = None,
) -> dict[str, Any]:
    created = int(at_ms if at_ms is not None else now_ms())
    retention_class = str(classification.get("retention_class") or "CHECKPOINT")
    base_heat, decay_per_day, freeze_floor, min_age_days = _policy(retention_class)
    heat = max(100, min(1000, base_heat + min(150, max(-100, priority // 25)) + (confidence_milli - 800) // 4))
    return {
        "schema": MEMORY_LIFECYCLE_SCHEMA,
        "state": "active",
        "heat_milli": heat,
        "decay_per_day_milli": decay_per_day,
        "freeze_floor_milli": freeze_floor,
        "minimum_active_age_days": min_age_days,
        "trigger_terms": _terms(content),
        "created_at_ms": created,
        "last_decay_at_ms": created,
        "last_recalled_at_ms": 0,
        "recall_count": 0,
        "frozen_at_ms": 0,
    }


def normalize_lifecycle(record: Mapping[str, Any], *, at_ms: int | None = None) -> dict[str, Any]:
    classification = record.get("classification") if isinstance(record.get("classification"), Mapping) else {}
    raw = record.get("lifecycle") if isinstance(record.get("lifecycle"), Mapping) else {}
    fallback = initial_lifecycle(
        classification=classification,
        content=record.get("content"),
        priority=int(record.get("priority") or 0),
        confidence_milli=int(record.get("confidence_milli") or 800),
        at_ms=at_ms,
    )
    value = {**fallback, **deepcopy(dict(raw))}
    value["schema"] = MEMORY_LIFECYCLE_SCHEMA
    value["state"] = "frozen" if str(value.get("state") or "") == "frozen" else "active"
    value["heat_milli"] = max(0, min(1000, int(value.get("heat_milli") or 0)))
    value["decay_per_day_milli"] = max(1, min(1000, int(value.get("decay_per_day_milli") or fallback["decay_per_day_milli"])))
    value["freeze_floor_milli"] = max(0, min(1000, int(value.get("freeze_floor_milli") or fallback["freeze_floor_milli"])))
    value["minimum_active_age_days"] = max(0, min(3650, int(value.get("minimum_active_age_days") or fallback["minimum_active_age_days"])))
    value["trigger_terms"] = _terms(value.get("trigger_terms") or record.get("content"))
    for key in ("created_at_ms", "last_decay_at_ms", "last_recalled_at_ms", "recall_count", "frozen_at_ms"):
        value[key] = max(0, int(value.get(key) or 0))
    return value


def advance_lifecycle(record: Mapping[str, Any], *, at_ms: int | None = None) -> tuple[dict[str, Any], bool]:
    now = int(at_ms if at_ms is not None else now_ms())
    value = normalize_lifecycle(record, at_ms=now)
    before = deepcopy(value)
    last_decay = min(now, value["last_decay_at_ms"] or now)
    elapsed_days = (now - last_decay) // 86_400_000
    if elapsed_days <= 0:
        return value, value != before
    # A flat subtraction makes frequently recalled memories decay too quickly
    # and old memories decay too slowly.  Preserve the useful legacy insight:
    # hot memories consolidate, while cold memories fade faster.  The rate is
    # still bounded by the retention class carried by the new classifier.
    base_rate = value["decay_per_day_milli"] / 1000.0
    heat = value["heat_milli"]
    rate_multiplier = 0.40 if heat >= 600 else 0.75 if heat >= 300 else 1.40
    value["heat_milli"] = max(0, min(1000, int(round(heat * math.exp(-base_rate * rate_multiplier * elapsed_days)))))
    value["last_decay_at_ms"] = last_decay + elapsed_days * 86_400_000
    age_days = (now - value["created_at_ms"]) // 86_400_000 if value["created_at_ms"] else 0
    if (
        value["state"] == "active"
        and age_days >= value["minimum_active_age_days"]
        and value["heat_milli"] <= value["freeze_floor_milli"]
    ):
        value["state"] = "frozen"
        value["frozen_at_ms"] = now
    return value, value != before


def recall_lifecycle(record: Mapping[str, Any], *, query: str, at_ms: int | None = None) -> tuple[dict[str, Any], bool, bool]:
    """Apply a cue-driven recall.  Returns lifecycle, changed, and thawed."""

    now = int(at_ms if at_ms is not None else now_ms())
    value, _ = advance_lifecycle(record, at_ms=now)
    before = deepcopy(value)
    cues = set(_terms(query))
    triggers = set(value.get("trigger_terms") or [])
    matched = bool(cues & triggers)
    thawed = value["state"] == "frozen" and matched
    prior_heat = value["heat_milli"]
    if thawed:
        value["state"] = "active"
        value["frozen_at_ms"] = 0
        value["heat_milli"] = max(value["heat_milli"], 250)
    if matched:
        # Re-encountering a cold cue has a larger consolidation effect than
        # repeatedly reading an already-hot record.
        boost = 160 if prior_heat < 200 else 104 if prior_heat < 500 else 80
        value["heat_milli"] = min(1000, value["heat_milli"] + boost)
        value["last_recalled_at_ms"] = now
        value["recall_count"] += 1
    return value, value != before, thawed


__all__ = [
    "MEMORY_LIFECYCLE_SCHEMA",
    "advance_lifecycle",
    "initial_lifecycle",
    "normalize_lifecycle",
    "recall_lifecycle",
]
