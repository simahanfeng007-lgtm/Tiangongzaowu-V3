"""P10 R3 restart-safe migration inventory and actual-entry telemetry.

This module is pure projection logic over the existing Life scope/journal. It
never publishes, activates, deletes, executes, or creates another store. Unknown
ownership is retained and reported. Runtime call telemetry covers only explicitly
instrumented legacy mutation entrypoints; absence outside that coverage is never
reported as zero usage.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from contracts import canonical_sha256
from .learning_workflow import legacy_publication_blocked

MIGRATION_SCHEMA = "tiangong.life.legacy-learning-migration.v1"
USAGE_SCHEMA = "tiangong.life.legacy-learning-usage.v1"
USAGE_WINDOW_SCHEMA = "tiangong.life.legacy-learning-usage-window.v1"

LEGACY_MUTATION_ENTRYPOINTS: dict[str, str] = {
    "/api/v1/v3/learning/confirm": "learning_publication",
    "/api/v1/v3/learning/process-approved": "learning_publication",
    "/api/v1/v3/learning/request-activation": "learning_publication",
    "/api/v1/v3/learning/activate": "learning_publication",
    "/api/v1/v3/learning/release": "learning_publication",
    "/api/v1/v3/life/capability/propose": "capability_publication",
    "/api/v1/v3/life/capability/approve": "capability_publication",
    "/api/v1/v3/life/capability/build": "capability_publication",
    "/api/v1/v3/life/capability/publish": "capability_publication",
    "/api/v1/v3/life/capability/activate": "capability_activation",
    "/api/v1/v3/life/capability/reactivate": "capability_activation",
    "/api/v1/v3/life/capability/rollback": "capability_activation",
    "/api/v1/v3/life/capability/patch/propose": "capability_patch",
    "/api/v1/v3/life/capability/patch/verify": "capability_patch",
}

# These are known compatibility surfaces from R0 but are outside the embedded
# Life journal boundary. P13 may remove them only after independent telemetry.
UNINSTRUMENTED_COMPATIBILITY_SURFACES = (
    "v3.duihua_qiaojie.legacy_learning_callbacks",
    "v3.jineng.jirou_ceng._xuexi_liucheng",
    "v3.zhili.nengli_zhuche.raw_registry_compatibility",
    "v3.l0_ability_projection.legacy_projection",
)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _digest(value: Mapping[str, Any]) -> str:
    return canonical_sha256(deepcopy(dict(value)))


def _migration_row(*, family: str, record_id: str, record: Mapping[str, Any], disposition: str,
                   unknown_ownership: bool = False) -> dict[str, Any]:
    return {
        "schema": MIGRATION_SCHEMA,
        "record_family": family,
        "record_id": record_id,
        "record_sha256": _digest(record),
        "disposition": disposition,
        "unknown_ownership": bool(unknown_ownership),
        "destructive_change": False,
        "source_pin_retained": True,
        "may_authorize": False,
        "may_execute": False,
    }


def classify_legacy_records(scope: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Classify current legacy records without mutating any authority state."""
    rows: list[dict[str, Any]] = []
    learning = _mapping(scope.get("learning")) or {}
    for learning_id, record in sorted(learning.items()):
        if not isinstance(record, Mapping) or not legacy_publication_blocked(record):
            continue
        status = str(record.get("status") or "")
        if status == "published":
            disposition = "HISTORICAL_PUBLISHED_RETAINED"
        elif status == "discarded":
            disposition = "HISTORICAL_DISCARDED_RETAINED"
        elif status == "migration_required" and record.get("publication_frozen") is True:
            disposition = "FROZEN_SOURCE_EVOLUTION_PENDING"
        else:
            disposition = "LEGACY_CARD_FREEZE_REQUIRED"
        rows.append(_migration_row(family="learning_card", record_id=str(learning_id),
                                   record=record, disposition=disposition))

    capabilities = _mapping(scope.get("capabilities")) or {}
    pointers = _mapping(scope.get("capability_pointers")) or {}
    pointed: set[str] = set()
    for lineage_id, pointer in sorted(pointers.items()):
        if not isinstance(pointer, Mapping):
            continue
        artifact_id = str(pointer.get("current_artifact_id") or "")
        if artifact_id:
            pointed.add(artifact_id)
        artifact = capabilities.get(artifact_id)
        unknown = bool(artifact_id) and not isinstance(artifact, Mapping)
        health = pointer.get("health") if isinstance(pointer.get("health"), Mapping) else {}
        if isinstance(health.get("patch_pending"), Mapping):
            disposition = "PENDING_PATCH_RETAINED"
        elif str(pointer.get("status") or "") == "active":
            disposition = "HISTORICAL_ACTIVE_POINTER_RETAINED"
        else:
            disposition = "HISTORICAL_POINTER_RETAINED"
        if unknown:
            disposition = "UNKNOWN_OWNERSHIP_RETAINED"
        rows.append(_migration_row(family="capability_pointer", record_id=str(lineage_id),
                                   record=pointer, disposition=disposition,
                                   unknown_ownership=unknown))

    for artifact_id, artifact in sorted(capabilities.items()):
        if not isinstance(artifact, Mapping) or str(artifact.get("kind") or "") not in {"skill", "tool"}:
            continue
        origin = str(artifact.get("origin") or "")
        unknown = origin not in {"life_learning", "life_patch"}
        if unknown:
            disposition = "UNKNOWN_OWNERSHIP_RETAINED"
        elif artifact_id in pointed:
            disposition = "HISTORICAL_ACTIVE_ARTIFACT_RETAINED"
        else:
            disposition = "HISTORICAL_ARTIFACT_RETAINED"
        rows.append(_migration_row(family="capability_artifact", record_id=str(artifact_id),
                                   record=artifact, disposition=disposition,
                                   unknown_ownership=unknown))
    return tuple(rows)


def apply_migration_record(scope: dict[str, Any], row: Mapping[str, Any]) -> bool:
    if not isinstance(row, Mapping) or row.get("schema") != MIGRATION_SCHEMA:
        raise ValueError("life.learning.legacy_migration_invalid")
    family, record_id = str(row.get("record_family") or ""), str(row.get("record_id") or "")
    digest = str(row.get("record_sha256") or "")
    if not family or not record_id or len(digest) != 64:
        raise ValueError("life.learning.legacy_migration_invalid")
    state = scope.setdefault("legacy_learning_migration", {"schema": MIGRATION_SCHEMA, "records": {}})
    records = state.setdefault("records", {})
    key = family + ":" + record_id
    existing = records.get(key)
    detached = deepcopy(dict(row))
    if existing == detached:
        return False
    records[key] = detached
    return True


def apply_usage_window(scope: dict[str, Any], payload: Mapping[str, Any]) -> bool:
    if (not isinstance(payload, Mapping) or payload.get("schema") != USAGE_WINDOW_SCHEMA
            or type(payload.get("started_at_ms")) is not int or payload["started_at_ms"] < 0):
        raise ValueError("life.learning.legacy_usage_window_invalid")
    state = scope.setdefault("legacy_learning_usage", {
        "schema": USAGE_SCHEMA, "observation_started_at_ms": payload["started_at_ms"],
        "last_observed_at_ms": 0, "total_calls": 0, "by_entrypoint": {}, "by_workload_class": {},
    })
    if state.get("schema") != USAGE_SCHEMA:
        raise ValueError("life.learning.legacy_usage_projection_invalid")
    current = int(state.get("observation_started_at_ms") or 0)
    if current and current != payload["started_at_ms"]:
        raise ValueError("life.learning.legacy_usage_window_conflict")
    if not current:
        state["observation_started_at_ms"] = payload["started_at_ms"]
        return True
    return False


def apply_usage_observation(scope: dict[str, Any], payload: Mapping[str, Any]) -> bool:
    if (not isinstance(payload, Mapping) or payload.get("schema") != USAGE_SCHEMA
            or type(payload.get("sequence")) is not int or payload["sequence"] < 1
            or type(payload.get("observed_at_ms")) is not int or payload["observed_at_ms"] < 0):
        raise ValueError("life.learning.legacy_usage_observation_invalid")
    entrypoint = str(payload.get("entrypoint") or "")
    workload = str(payload.get("workload_class") or "")
    if LEGACY_MUTATION_ENTRYPOINTS.get(entrypoint) != workload:
        raise ValueError("life.learning.legacy_usage_entrypoint_invalid")
    state = scope.setdefault("legacy_learning_usage", {
        "schema": USAGE_SCHEMA, "observation_started_at_ms": payload["observed_at_ms"],
        "last_observed_at_ms": 0, "total_calls": 0, "by_entrypoint": {}, "by_workload_class": {},
    })
    expected = int(state.get("total_calls") or 0) + 1
    if payload["sequence"] != expected:
        raise ValueError("life.learning.legacy_usage_sequence_invalid")
    state["total_calls"] = expected
    state["last_observed_at_ms"] = max(int(state.get("last_observed_at_ms") or 0), payload["observed_at_ms"])
    by_entry = state.setdefault("by_entrypoint", {})
    by_entry[entrypoint] = int(by_entry.get(entrypoint) or 0) + 1
    by_workload = state.setdefault("by_workload_class", {})
    by_workload[workload] = int(by_workload.get(workload) or 0) + 1
    return True


def legacy_migration_summary(scope: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
    migration = _mapping(scope.get("legacy_learning_migration")) or {}
    records = _mapping(migration.get("records")) or {}
    usage = _mapping(scope.get("legacy_learning_usage")) or {}
    started = int(usage.get("observation_started_at_ms") or 0)
    total = int(usage.get("total_calls") or 0)
    unknown = sum(1 for row in records.values() if isinstance(row, Mapping) and row.get("unknown_ownership") is True)
    pending = sum(1 for row in records.values() if isinstance(row, Mapping)
                  and row.get("disposition") in {"FROZEN_SOURCE_EVOLUTION_PENDING", "PENDING_PATCH_RETAINED", "LEGACY_CARD_FREEZE_REQUIRED"})
    pointers = _mapping(scope.get("capability_pointers")) or {}
    active_historical = 0
    historical_uses = 0
    historical_last_at_ms = 0
    for pointer in pointers.values():
        if not isinstance(pointer, Mapping):
            continue
        if str(pointer.get("status") or "") == "active":
            active_historical += 1
        health = pointer.get("health") if isinstance(pointer.get("health"), Mapping) else {}
        historical_uses += int(health.get("uses") or 0)
        historical_last_at_ms = max(historical_last_at_ms, int(health.get("last_outcome_at_ms") or 0))
    return {
        "schema": "tiangong.life.legacy-learning-migration-summary.v1",
        "record_count": len(records), "unknown_ownership_count": unknown,
        "pending_migration_count": pending,
        "observation_started_at_ms": started,
        "observation_window_ms": 0 if not started else max(0, int(now_ms) - started),
        "legacy_mutation_call_count": total,
        "historical_active_capability_count": active_historical,
        "historical_capability_usage_count": historical_uses,
        "historical_capability_last_outcome_at_ms": historical_last_at_ms,
        "by_entrypoint": deepcopy(dict(usage.get("by_entrypoint") or {})),
        "by_workload_class": deepcopy(dict(usage.get("by_workload_class") or {})),
        "instrumented_entrypoints": tuple(sorted(LEGACY_MUTATION_ENTRYPOINTS)),
        "uninstrumented_compatibility_surfaces": UNINSTRUMENTED_COMPATIBILITY_SURFACES,
        "zero_usage_proven": False,
        "destructive_migration_performed": False,
    }
