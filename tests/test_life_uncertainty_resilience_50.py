from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

import life_service.embedded_runtime as embedded_module
from contracts import canonical_sha256
from life_service.autonomous_tasks import materialize_tasks, update_task_status
from life_service.embedded_runtime import EmbeddedLifeError, EmbeddedLifeRuntime
from life_service.memory_classification import classify_memory


def _runtime(root: Path) -> EmbeddedLifeRuntime:
    return EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "life-runtime",
        mode="embedded",
    )


def _request(
    life: EmbeddedLifeRuntime,
    method: str,
    path: str,
    payload=None,
    *,
    expected: int = 200,
):
    status, value, _ = life.request(method, path, payload)
    assert status == expected, (path, status, value)
    return value


def _active_id(life: EmbeddedLifeRuntime) -> str:
    return str(life._active()["life_id"])


def _journal_path(life: EmbeddedLifeRuntime, life_id: str | None = None) -> Path:
    return life.system.journal._path(life_id or _active_id(life))


def _head_path(life: EmbeddedLifeRuntime, life_id: str | None = None) -> Path:
    return life.system.journal._head_path(life_id or _active_id(life))


def _private_key_path(life: EmbeddedLifeRuntime, life_id: str | None = None) -> Path:
    root = life.system.identities.root_for(life_id or _active_id(life))
    return root / "identity" / "private_key.pem"


SCENARIOS = [
    "root_path_rejected",
    "data_root_symlink_rejected",
    "writer_lock_symlink_rejected",
    "duplicate_writer_rejected",
    "corrupt_registry_fail_closed",
    "corrupt_state_fail_closed",
    "unknown_state_schema_fail_closed",
    "journal_truncation_fail_closed",
    "signed_head_missing_fail_closed",
    "private_key_missing_fail_closed",
    "memory_append_failure_rolls_back_projection",
    "state_persist_failure_recovers_from_journal",
    "relation_append_failure_rolls_back_projection",
    "status_append_failure_rolls_back_projection",
    "delete_append_failure_rolls_back_projection",
    "correction_append_failure_rolls_back_projection",
    "autonomy_append_failure_rolls_back_tasks",
    "scheduler_stop_timeout_retains_lease",
    "authority_store_close_failure_retains_lease",
    "closed_runtime_rejects_calls",
    "pending_limit_zero_blocks_generation",
    "pending_limit_negative_rejected",
    "pending_limit_huge_rejected",
    "task_clock_rollback_is_monotonic",
    "tick_clock_rollback_is_monotonic",
    "duplicate_active_fingerprint_unhealthy",
    "task_sequence_rollback_unhealthy",
    "autonomy_counter_mismatch_unhealthy",
    "scheduler_exception_marks_not_ready",
    "terminal_replay_conflict_preserves_result",
    "nan_memory_rejected",
    "infinity_memory_rejected",
    "oversized_memory_rejected",
    "oversized_search_query_rejected",
    "excessive_search_filters_rejected",
    "duplicate_relations_are_deduplicated",
    "self_causal_relation_rejected",
    "relation_to_deleted_target_rejected",
    "recall_suppressed_not_searchable",
    "corrected_memory_not_searchable_by_default",
    "contradictory_explicit_causal_role_rejected",
    "deleted_target_makes_cause_incomplete",
    "concurrent_100_writes_no_loss",
    "concurrent_tick_and_search_no_deadlock",
    "identity_switch_with_pinned_writes_isolated",
    "v1_state_migration_preserves_autonomy",
    "malformed_autonomy_state_fail_closed",
    "oversized_task_result_rejected",
    "clean_close_reacquire_repeatedly",
    "twenty_restart_cycles_remain_ready",
]

assert len(SCENARIOS) == 50


@pytest.mark.parametrize("case", SCENARIOS, ids=SCENARIOS)
def test_life_uncertainty_resilience_50(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str):
    if case == "root_path_rejected":
        with pytest.raises(EmbeddedLifeError):
            EmbeddedLifeRuntime(data_root=Path(Path.cwd().anchor), runtime_root=tmp_path / "rt")
        return

    if case == "data_root_symlink_rejected":
        target = tmp_path / "real-data"
        target.mkdir()
        link = tmp_path / "linked-data"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        with pytest.raises(EmbeddedLifeError, match="life.writer.root_unsafe"):
            EmbeddedLifeRuntime(data_root=link, runtime_root=tmp_path / "rt")
        return

    if case == "writer_lock_symlink_rejected":
        data = tmp_path / "life-data"
        data.mkdir()
        target = tmp_path / "outside.lock"
        target.write_text("x", encoding="utf-8")
        try:
            (data / "life.writer.lock").symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        with pytest.raises(EmbeddedLifeError, match="life.writer.lock_unsafe"):
            _runtime(tmp_path)
        return

    if case == "duplicate_writer_rejected":
        life = _runtime(tmp_path)
        try:
            with pytest.raises(EmbeddedLifeError, match="life.writer.already_owned"):
                _runtime(tmp_path)
        finally:
            life.close()
        return

    if case in {
        "corrupt_registry_fail_closed",
        "corrupt_state_fail_closed",
        "unknown_state_schema_fail_closed",
        "journal_truncation_fail_closed",
        "signed_head_missing_fail_closed",
        "private_key_missing_fail_closed",
    }:
        life = _runtime(tmp_path)
        _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_seed", "content": {"text": "seed"}})
        registry = life.system.identities.registry_path
        state_file = life.paths.state_file
        journal = _journal_path(life)
        head = _head_path(life)
        private_key = _private_key_path(life)
        life.close()
        if case == "corrupt_registry_fail_closed":
            registry.write_text("{", encoding="utf-8")
        elif case == "corrupt_state_fail_closed":
            state_file.write_text("{", encoding="utf-8")
        elif case == "unknown_state_schema_fail_closed":
            value = json.loads(state_file.read_text(encoding="utf-8"))
            value["schema"] = "tiangong.life.embedded-state.v999"
            state_file.write_text(json.dumps(value), encoding="utf-8")
        elif case == "journal_truncation_fail_closed":
            raw = journal.read_bytes()
            journal.write_bytes(raw[:-1])
        elif case == "signed_head_missing_fail_closed":
            head.unlink()
        elif case == "private_key_missing_fail_closed":
            private_key.unlink()
        with pytest.raises(Exception):
            _runtime(tmp_path)
        return

    life = _runtime(tmp_path)
    try:
        if case == "memory_append_failure_rolls_back_projection":
            original = life.system.journal._write_signed_head
            monkeypatch.setattr(life.system.journal, "_write_signed_head", lambda *a, **k: (_ for _ in ()).throw(OSError("head failure")))
            value = _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_fail", "content": {"text": "fail"}}, expected=500)
            assert value["ok"] is False
            assert "mem_fail" not in life._scope_state()["memories"]
            monkeypatch.setattr(life.system.journal, "_write_signed_head", original)

        elif case == "state_persist_failure_recovers_from_journal":
            original = embedded_module.atomic_json
            def fail_state(path, value):
                if Path(path) == life.paths.state_file:
                    raise OSError("disk full")
                return original(path, value)
            monkeypatch.setattr(embedded_module, "atomic_json", fail_state)
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_wal", "content": {"text": "recover"}}, expected=500)
            assert "mem_wal" not in life._scope_state()["memories"]
            monkeypatch.setattr(embedded_module, "atomic_json", original)
            life.close()
            life = _runtime(tmp_path)
            assert life._scope_state()["memories"]["mem_wal"]["content"]["text"] == "recover"

        elif case in {
            "relation_append_failure_rolls_back_projection",
            "status_append_failure_rolls_back_projection",
            "delete_append_failure_rolls_back_projection",
            "correction_append_failure_rolls_back_projection",
        }:
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_a", "content": {"text": "A"}})
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_b", "content": {"text": "B"}})
            original_append = life.system.journal.append
            def fail_event(life_id, event_type, *args, **kwargs):
                wanted = {
                    "relation_append_failure_rolls_back_projection": "memory.relation_added",
                    "status_append_failure_rolls_back_projection": "memory.status_changed",
                    "delete_append_failure_rolls_back_projection": "memory.deleted",
                    "correction_append_failure_rolls_back_projection": "memory.corrected",
                }[case]
                if event_type == wanted:
                    raise OSError("journal unavailable")
                return original_append(life_id, event_type, *args, **kwargs)
            monkeypatch.setattr(life.system.journal, "append", fail_event)
            if case == "relation_append_failure_rolls_back_projection":
                _request(life, "POST", "/api/v1/v3/life/memory/relation", {"source_memory_id": "mem_a", "target_memory_id": "mem_b", "kind": "causes"}, expected=500)
                assert life._scope_state()["memory_relations"] == []
            elif case == "status_append_failure_rolls_back_projection":
                _request(life, "POST", "/api/v1/v3/life/memory/status", {"memory_id": "mem_a", "status": "recall_suppressed"}, expected=500)
                assert life._scope_state()["memories"]["mem_a"]["status"] == "active"
            elif case == "delete_append_failure_rolls_back_projection":
                _request(life, "POST", "/api/v1/v3/life/memory/delete", {"memory_id": "mem_a"}, expected=500)
                assert life._scope_state()["memories"]["mem_a"]["status"] == "active"
                assert life._scope_state()["memories"]["mem_a"]["content"] == {"text": "A"}
            else:
                _request(life, "POST", "/api/v1/v3/life/memory/correct", {"target_memory_id": "mem_a", "memory_id": "mem_c", "content": {"text": "C"}}, expected=500)
                assert life._scope_state()["memories"]["mem_a"]["status"] == "active"
                assert "mem_c" not in life._scope_state()["memories"]

        elif case == "autonomy_append_failure_rolls_back_tasks":
            original_append_batch = life.system.journal.append_batch
            def fail_task_batch(life_id, entries):
                if any(str(item.get("event_type") or "") == "autonomy.task_generated" for item in entries):
                    raise OSError("journal unavailable")
                return original_append_batch(life_id, entries)
            monkeypatch.setattr(life.system.journal, "append_batch", fail_task_batch)
            _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "failure"}, expected=500)
            assert life._autonomy_state()["tasks"] == {}

        elif case == "scheduler_stop_timeout_retains_lease":
            original_stop = life.scheduler.stop
            monkeypatch.setattr(life.scheduler, "stop", lambda **kwargs: (_ for _ in ()).throw(TimeoutError("blocked")))
            with pytest.raises(TimeoutError):
                life.close()
            assert life._lease.active is True and life._closed is False
            monkeypatch.setattr(life.scheduler, "stop", original_stop)

        elif case == "authority_store_close_failure_retains_lease":
            original_close = life.authority_store.close
            monkeypatch.setattr(life.authority_store, "close", lambda: (_ for _ in ()).throw(OSError("busy")))
            with pytest.raises(RuntimeError, match="authority store"):
                life.close()
            assert life._lease.active is True and life._closed is False
            monkeypatch.setattr(life.authority_store, "close", original_close)

        elif case == "closed_runtime_rejects_calls":
            life.close()
            _request(life, "GET", "/health", expected=503)

        elif case == "pending_limit_zero_blocks_generation":
            autonomy = life._autonomy_state()
            autonomy["pending_limit"] = 0
            life._persist()
            result = _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "zero"})
            assert result["generated"] == [] and result["autonomy"]["active_task_count"] == 0

        elif case in {"pending_limit_negative_rejected", "pending_limit_huge_rejected", "malformed_autonomy_state_fail_closed"}:
            state_file = life.paths.state_file
            life.close()
            persisted = json.loads(state_file.read_text(encoding="utf-8"))
            active_id = next(iter(persisted["identity_states"]))
            autonomy = persisted["identity_states"][active_id]["autonomy"]
            if case == "pending_limit_negative_rejected":
                autonomy["pending_limit"] = -1
            elif case == "pending_limit_huge_rejected":
                autonomy["pending_limit"] = 1_000_000
            else:
                autonomy["tasks"] = []
            state_file.write_text(json.dumps(persisted), encoding="utf-8")
            with pytest.raises(EmbeddedLifeError, match="life.state.autonomy_invalid"):
                _runtime(tmp_path)
            life = None

        elif case == "task_clock_rollback_is_monotonic":
            state = {"tasks": {}}
            candidate = {
                "task_kind": "x", "objective": "x", "proposed_action": "x",
                "subject_refs": ["m"], "causal_basis": ["b"], "priority": 1,
                "risk_class": "A0", "requires_user": False, "fingerprint": "f" * 64,
            }
            task = materialize_tasks(state, [candidate], life_id="org_x", reason="first", now_ms=1000)[0]
            updated = update_task_status(state, task_id=task["task_id"], status="running", now_ms=900)
            assert updated["updated_at_ms"] >= 1000

        elif case == "tick_clock_rollback_is_monotonic":
            state = {"tasks": {}}
            candidate = {
                "task_kind": "x", "objective": "x", "proposed_action": "x",
                "subject_refs": ["m"], "causal_basis": ["b"], "priority": 1,
                "risk_class": "A0", "requires_user": False, "fingerprint": "f" * 64,
            }
            materialize_tasks(state, [candidate], life_id="org_x", reason="first", now_ms=1000)
            materialize_tasks(state, [candidate], life_id="org_x", reason="second", now_ms=900)
            assert state["last_tick_at_ms"] >= 1000

        elif case in {"duplicate_active_fingerprint_unhealthy", "task_sequence_rollback_unhealthy", "autonomy_counter_mismatch_unhealthy"}:
            result = _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "health"})
            assert result["tasks"]
            autonomy = life._autonomy_state()
            task = deepcopy(next(iter(autonomy["tasks"].values())))
            if case == "duplicate_active_fingerprint_unhealthy":
                task["task_id"] = "lat_" + "a" * 64
                task["sequence"] = int(task["sequence"]) + 1
                task["task_sha256"] = canonical_sha256({k: v for k, v in task.items() if k != "task_sha256"})
                autonomy["tasks"][task["task_id"]] = task
            elif case == "task_sequence_rollback_unhealthy":
                autonomy["task_sequence"] = 0
            else:
                autonomy["generated_total"] = 0
            assert life._autonomy_health_payload()["healthy"] is False

        elif case == "scheduler_exception_marks_not_ready":
            life.close()
            monkeypatch.setenv("TIANGONG_LIFE_HEARTBEAT_SECONDS", "1")
            life = _runtime(tmp_path)
            monkeypatch.setattr(life.scheduler, "_tick", lambda reason: (_ for _ in ()).throw(RuntimeError("tick")))
            deadline = time.time() + 2.5
            while time.time() < deadline and not life.scheduler.status()["last_error_type"]:
                time.sleep(0.05)
            assert life.scheduler.status()["last_error_type"] == "RuntimeError"
            status, ready = life.ready_payload()
            assert status == 503 and "life.scheduler.tick_failed" in ready["reason_codes"]

        elif case == "terminal_replay_conflict_preserves_result":
            result = _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "terminal"})
            task_id = result["tasks"][0]["task_id"]
            _request(life, "POST", "/api/v1/v3/life/autonomy/task/status", {"task_id": task_id, "status": "completed", "result": {"value": "A"}})
            _request(life, "POST", "/api/v1/v3/life/autonomy/task/status", {"task_id": task_id, "status": "completed", "result": {"value": "B"}}, expected=409)
            assert life._autonomy_state()["tasks"][task_id]["result"] == {"value": "A"}

        elif case in {"nan_memory_rejected", "infinity_memory_rejected"}:
            value = math.nan if case == "nan_memory_rejected" else math.inf
            response = _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_nonfinite", "content": {"value": value}}, expected=400)
            assert response["ok"] is False

        elif case == "oversized_memory_rejected":
            response = _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_large", "content": {"text": "x" * (1024 * 1024 + 1)}}, expected=400)
            assert response["ok"] is False

        elif case == "oversized_search_query_rejected":
            _request(life, "POST", "/api/v1/v3/life/memory/search", {"query": "x" * 65537}, expected=400)

        elif case == "excessive_search_filters_rejected":
            _request(life, "POST", "/api/v1/v3/life/memory/search", {"memory_types": ["semantic"] * 257}, expected=400)

        elif case == "duplicate_relations_are_deduplicated":
            value = classify_memory(
                content={"text": "cause"}, provenance={},
                relations=[{"kind": "causes", "target_memory_id": "mem_b"}, {"kind": "causes", "target_memory_id": "mem_b"}],
            )
            assert len(value["relations"]) == 1

        elif case == "self_causal_relation_rejected":
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_self", "content": {"cause": "loop"}})
            _request(life, "POST", "/api/v1/v3/life/memory/relation", {"source_memory_id": "mem_self", "target_memory_id": "mem_self", "kind": "causes"}, expected=400)

        elif case == "relation_to_deleted_target_rejected":
            for memory_id in ("mem_src", "mem_dst"):
                _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": memory_id, "content": {"text": memory_id}})
            _request(life, "POST", "/api/v1/v3/life/memory/delete", {"memory_id": "mem_dst"})
            _request(life, "POST", "/api/v1/v3/life/memory/relation", {"source_memory_id": "mem_src", "target_memory_id": "mem_dst", "kind": "causes"}, expected=409)

        elif case == "recall_suppressed_not_searchable":
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_hidden", "content": {"text": "hidden needle"}})
            _request(life, "POST", "/api/v1/v3/life/memory/status", {"memory_id": "mem_hidden", "status": "recall_suppressed"})
            result = _request(life, "POST", "/api/v1/v3/life/memory/search", {"query": "hidden needle"})
            assert result["results"] == []

        elif case == "corrected_memory_not_searchable_by_default":
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_old", "content": {"text": "obsolete needle"}})
            _request(life, "POST", "/api/v1/v3/life/memory/correct", {"target_memory_id": "mem_old", "memory_id": "mem_new", "content": {"text": "current needle"}})
            old = _request(life, "POST", "/api/v1/v3/life/memory/search", {"query": "obsolete needle"})
            new = _request(life, "POST", "/api/v1/v3/life/memory/search", {"query": "current needle"})
            assert old["results"] == [] and [row["memory_id"] for row in new["results"]] == ["mem_new"]

        elif case == "contradictory_explicit_causal_role_rejected":
            with pytest.raises(ValueError):
                classify_memory(content={"cause": "heat"}, provenance={}, relations=[], requested_causal_role="effect")

        elif case == "deleted_target_makes_cause_incomplete":
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_cause", "content": {"cause": "heat"}})
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_effect", "content": {"effect": "shutdown"}})
            _request(life, "POST", "/api/v1/v3/life/memory/relation", {"source_memory_id": "mem_cause", "target_memory_id": "mem_effect", "kind": "causes"})
            _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "linked"})
            _request(life, "POST", "/api/v1/v3/life/memory/delete", {"memory_id": "mem_effect"})
            result = _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "deleted-target"})
            assert any(task["task_kind"] == "complete_causal_link" and "mem_cause" in task["subject_refs"] for task in result["tasks"] if task["status"] in {"pending", "running", "blocked", "awaiting_user"})

        elif case == "concurrent_100_writes_no_loss":
            def write(index: int):
                status, value, _ = life.request("POST", "/api/v1/v3/life/memory/assert", {"memory_id": f"mem_{index:03d}", "content": {"text": f"row {index}"}})
                return status, value
            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(write, range(100)))
            assert all(status == 200 for status, _ in results)
            assert sum(1 for key in life._scope_state()["memories"] if key.startswith("mem_")) == 100
            assert life._journal_verify()["valid"] is True

        elif case == "concurrent_tick_and_search_no_deadlock":
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_mix", "content": {"cause": "load"}})
            def action(index: int):
                if index % 2:
                    return life.request("POST", "/api/v1/v3/life/autonomy/tick", {"reason": f"tick-{index}"})[0]
                return life.request("POST", "/api/v1/v3/life/memory/search", {"query": "load"})[0]
            with ThreadPoolExecutor(max_workers=12) as pool:
                statuses = list(pool.map(action, range(40)))
            assert statuses == [200] * 40 and life.ready_payload()[0] == 200

        elif case == "identity_switch_with_pinned_writes_isolated":
            first = _active_id(life)
            second = _request(life, "POST", "/api/v1/v3/life/identity/create", {"name": "second"})["identity"]["life_id"]
            def pinned(index: int):
                life_id = first if index % 2 == 0 else second
                return life.request("POST", "/api/v1/v3/life/memory/assert", {"life_id": life_id, "memory_id": f"mem_pin_{index}", "content": {"text": str(index)}})[0]
            def switcher():
                for _ in range(10):
                    life.request("POST", "/api/v1/v3/life/identity/activate", {"life_id": second})
                    life.request("POST", "/api/v1/v3/life/identity/activate", {"life_id": first})
            thread = threading.Thread(target=switcher)
            thread.start()
            with ThreadPoolExecutor(max_workers=10) as pool:
                statuses = list(pool.map(pinned, range(40)))
            thread.join(timeout=5)
            assert not thread.is_alive() and statuses == [200] * 40
            assert all(f"mem_pin_{i}" in life._scope_state(first)["memories"] for i in range(0, 40, 2))
            assert all(f"mem_pin_{i}" in life._scope_state(second)["memories"] for i in range(1, 40, 2))

        elif case == "v1_state_migration_preserves_autonomy":
            active = _active_id(life)
            state_file = life.paths.state_file
            life.close()
            v1 = life._default_identity_state()
            v1["schema"] = "tiangong.life.embedded-state.v1"
            v1["autonomy"]["pending_limit"] = 7
            v1["memories"]["mem_v1"] = {
                "memory_id": "mem_v1", "life_id": active, "memory_type": "semantic",
                "content": {"text": "v1"}, "provenance": {}, "relations": [],
                "epistemic_status": "user_asserted", "confidence_milli": 800,
                "priority": 900, "status": "active", "revision": 1,
                "created_at": "2026-01-01T00:00:00.000Z", "updated_at": "2026-01-01T00:00:00.000Z",
            }
            state_file.write_text(json.dumps(v1), encoding="utf-8")
            life = _runtime(tmp_path)
            assert life._autonomy_state()["pending_limit"] == 7
            assert "mem_v1" in life._scope_state()["memories"]

        elif case == "oversized_task_result_rejected":
            result = _request(life, "POST", "/api/v1/v3/life/autonomy/tick", {"reason": "large-result"})
            task_id = result["tasks"][0]["task_id"]
            _request(life, "POST", "/api/v1/v3/life/autonomy/task/status", {"task_id": task_id, "status": "completed", "result": {"blob": "x" * (1024 * 1024 + 1)}}, expected=400)

        elif case == "clean_close_reacquire_repeatedly":
            life.close()
            for _ in range(10):
                life = _runtime(tmp_path)
                assert life.ready_payload()[0] == 200
                life.close()
            life = None

        elif case == "twenty_restart_cycles_remain_ready":
            _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_restart", "content": {"text": "survive"}})
            for _ in range(20):
                life.close()
                life = _runtime(tmp_path)
                assert life.ready_payload()[0] == 200
                result = _request(life, "POST", "/api/v1/v3/life/memory/search", {"query": "survive"})
                assert [row["memory_id"] for row in result["results"]] == ["mem_restart"]

        else:  # pragma: no cover
            raise AssertionError(case)
    finally:
        if life is not None and not getattr(life, "_closed", True):
            try:
                life.close()
            except Exception:
                pass
