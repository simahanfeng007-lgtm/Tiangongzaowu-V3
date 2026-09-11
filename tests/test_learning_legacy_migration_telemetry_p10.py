"""P10 R3: retained legacy records migrate idempotently; real entry use is observed."""
from __future__ import annotations

from copy import deepcopy

import pytest

from contracts import canonical_sha256
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.learning_workflow import MIGRATION_REQUIRED, build_draft
from life_service.legacy_learning_migration import (
    LEGACY_MUTATION_ENTRYPOINTS, R3A_LEGACY_MUTATION_ENTRYPOINTS, UNINSTRUMENTED_COMPATIBILITY_SURFACES,
    classify_legacy_records, legacy_migration_summary,
)
from tests.test_learning_publication_freeze_p10 import decision, seed_history, life


def _stop(life):
    if life.scheduler is not None:
        life.scheduler.stop(timeout_seconds=2)


def _seed_approved(life):
    life_id = life._active()["life_id"]
    card = build_draft(life_id=life_id, scope={}, decision=decision("skill"), source="user_direct")
    card["learning_evidence"] = {"source_id": "legacy-proof", "error": "preserve-me"}
    scope = life._scope_state(life_id)
    scope["learning"][card["learning_id"]] = deepcopy(card)
    life.system.journal.append(life_id, "learning.draft_created", {"learning": card}, actor="fixture",
                               idempotency_key="p10-r3:legacy-approved")
    life._persist(life_id, force=True)
    return card


def test_restart_migrates_approved_card_and_retains_active_history(tmp_path):
    data, runtime = tmp_path/"data", tmp_path/"runtime"
    first = EmbeddedLifeRuntime(data_root=data, runtime_root=runtime, mode="embedded"); _stop(first)
    card = _seed_approved(first)
    artifact, pointer = seed_history(first)
    before_artifact = deepcopy(first._scope_state()["capabilities"][artifact["artifact_id"]])
    before_pointer = deepcopy(first._scope_state()["capability_pointers"][artifact["lineage_id"]])
    first.close()

    reopened = EmbeddedLifeRuntime(data_root=data, runtime_root=runtime, mode="embedded"); _stop(reopened)
    try:
        scope = reopened._scope_state()
        saved = scope["learning"][card["learning_id"]]
        assert saved["status"] == MIGRATION_REQUIRED and saved["publication_frozen"] is True
        assert saved["learning_evidence"] == card["learning_evidence"]
        assert scope["capabilities"][artifact["artifact_id"]] == before_artifact
        assert scope["capability_pointers"][artifact["lineage_id"]] == before_pointer
        rows = (scope["legacy_learning_migration"] or {})["records"]
        dispositions = {row["disposition"] for row in rows.values()}
        assert "FROZEN_SOURCE_EVOLUTION_PENDING" in dispositions
        assert "HISTORICAL_ACTIVE_POINTER_RETAINED" in dispositions
        assert "HISTORICAL_ACTIVE_ARTIFACT_RETAINED" in dispositions
        assert int(scope["legacy_learning_usage"]["observation_started_at_ms"]) > 0
        assert scope["legacy_learning_usage"]["total_calls"] == 0
        before_events = len(reopened.system.journal.events(card["life_id"]))
        reopened._migrate_legacy_learning_records(life_id=card["life_id"])
        assert len(reopened.system.journal.events(card["life_id"])) == before_events
    finally:
        reopened.close()


def test_pending_patch_and_unknown_ownership_are_retained_not_repaired(life):
    artifact, pointer = seed_history(life)
    scope = life._scope_state()
    health = deepcopy(pointer["health"])
    health["patch_pending"] = {"round": 1, "from_artifact_id": artifact["artifact_id"],
                               "to_artifact_id": "missing_patch", "to_artifact_sha256": "a"*64,
                               "proposed_at_ms": 2}
    pending = {**pointer, "health": health}
    pending["pointer_sha256"] = canonical_sha256({k:v for k,v in pending.items() if k != "pointer_sha256"})
    scope["capability_pointers"][artifact["lineage_id"]] = pending
    orphan = {**pending, "lineage_id": "unknown-lineage", "current_artifact_id": "missing-artifact"}
    orphan["pointer_sha256"] = canonical_sha256({k:v for k,v in orphan.items() if k != "pointer_sha256"})
    scope["capability_pointers"]["unknown-lineage"] = orphan
    before = deepcopy(scope["capability_pointers"])
    summary = life._migrate_legacy_learning_records(life_id=artifact["life_id"])
    assert scope["capability_pointers"] == before
    rows = scope["legacy_learning_migration"]["records"]
    assert rows["capability_pointer:"+artifact["lineage_id"]]["disposition"] == "PENDING_PATCH_RETAINED"
    assert rows["capability_pointer:unknown-lineage"]["disposition"] == "UNKNOWN_OWNERSHIP_RETAINED"
    assert summary["unknown_ownership_count"] >= 1
    assert summary["destructive_migration_performed"] is False


@pytest.mark.parametrize("alias", ["confirm", "process-approved", "request-activation", "activate", "release"])
def test_actual_learning_compatibility_entry_is_counted_once(life, alias):
    card = _seed_approved(life)
    before = legacy_migration_summary(life._scope_state(), now_ms=10**15)["legacy_mutation_call_count"]
    code, _result, _ = life.request("POST", "/api/v1/v3/learning/"+alias,
                                    {"learning_id": card["learning_id"]})
    assert code == 200
    summary = legacy_migration_summary(life._scope_state(), now_ms=10**15)
    assert summary["legacy_mutation_call_count"] == before + 1
    assert summary["by_entrypoint"]["/api/v1/v3/learning/"+alias] == 1
    assert summary["by_workload_class"]["learning_publication"] == 1


@pytest.mark.parametrize("path", [
    "/api/v1/v3/life/capability/propose",
    "/api/v1/v3/life/capability/build",
    "/api/v1/v3/life/capability/publish",
    "/api/v1/v3/life/capability/activate",
    "/api/v1/v3/life/capability/reactivate",
    "/api/v1/v3/life/capability/rollback",
    "/api/v1/v3/life/capability/patch/propose",
    "/api/v1/v3/life/capability/patch/verify",
])
def test_actual_capability_mutation_entry_is_counted_even_when_frozen(life, path):
    artifact, _pointer = seed_history(life)
    before = legacy_migration_summary(life._scope_state(), now_ms=10**15)["legacy_mutation_call_count"]
    code, result, _ = life.request("POST", path, {"artifact_id": artifact["artifact_id"]})
    assert code in {200, 409}
    assert result.get("ok") is False or path.endswith("/activate")
    summary = legacy_migration_summary(life._scope_state(), now_ms=10**15)
    assert summary["legacy_mutation_call_count"] == before + 1
    assert summary["by_entrypoint"][path] == 1


def test_panel_never_turns_missing_coverage_into_zero_use_claim(life):
    summary = life._panel()["learning"]["legacy_migration"]
    assert summary["zero_usage_proven"] is False
    assert set(summary["known_entrypoints"]) == set(LEGACY_MUTATION_ENTRYPOINTS)
    assert set(summary["instrumented_entrypoints"]) == set(R3A_LEGACY_MUTATION_ENTRYPOINTS)
    assert tuple(summary["uninstrumented_compatibility_surfaces"]) == UNINSTRUMENTED_COMPATIBILITY_SURFACES
    assert summary["observation_started_at_ms"] > 0
    assert summary["observation_window_ms"] >= 0


def test_classification_is_pure_and_deterministic(life):
    artifact, _pointer = seed_history(life)
    before = deepcopy(life._scope_state())
    one = classify_legacy_records(life._scope_state())
    two = classify_legacy_records(life._scope_state())
    assert one == two
    assert life._scope_state() == before
    assert any(row["record_id"] == artifact["artifact_id"] for row in one)
    assert all(row["destructive_change"] is False and row["source_pin_retained"] is True for row in one)

def test_existing_capability_health_supplies_historical_usage_without_new_counter(life):
    artifact, _pointer = seed_history(life)
    life.set_artifact_invoker(lambda action, args, ctx: {"ok": True})
    result = life._capability_invoke({"artifact_id": artifact["artifact_id"], "inputs": {}})
    assert result["ok"] is True
    summary = legacy_migration_summary(life._scope_state(), now_ms=10**15)
    assert summary["historical_active_capability_count"] >= 1
    assert summary["historical_capability_usage_count"] >= 1
    assert summary["historical_capability_last_outcome_at_ms"] > 0

def test_journal_replay_restores_usage_and_migration_projection_after_state_loss(tmp_path):
    import json
    data, runtime = tmp_path/"data", tmp_path/"runtime"
    first = EmbeddedLifeRuntime(data_root=data, runtime_root=runtime, mode="embedded"); _stop(first)
    card = _seed_approved(first)
    first._migrate_legacy_learning_records(life_id=card["life_id"])
    code, _reply, _ = first.request("POST", "/api/v1/v3/learning/confirm", {"learning_id": card["learning_id"]})
    assert code == 200
    expected = legacy_migration_summary(first._scope_state(), now_ms=10**15)
    state_file = first.paths.state_file
    first.close()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    scope = saved["identity_states"][card["life_id"]]
    scope.pop("legacy_learning_usage", None)
    scope.pop("legacy_learning_migration", None)
    state_file.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    reopened = EmbeddedLifeRuntime(data_root=data, runtime_root=runtime, mode="embedded"); _stop(reopened)
    try:
        actual = legacy_migration_summary(reopened._scope_state(), now_ms=10**15)
        assert actual["legacy_mutation_call_count"] == expected["legacy_mutation_call_count"] == 1
        assert actual["record_count"] == expected["record_count"]
        assert actual["by_entrypoint"] == expected["by_entrypoint"]
    finally:
        reopened.close()
