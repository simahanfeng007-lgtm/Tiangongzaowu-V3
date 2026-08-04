from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from life_service.complete_core import LifeCoreError
from life_service.embedded_runtime import EmbeddedLifeRuntime


def _runtime(root: Path) -> EmbeddedLifeRuntime:
    return EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "life-runtime",
        mode="embedded",
    )


def _request(life: EmbeddedLifeRuntime, method: str, path: str, payload=None, *, expected: int = 200):
    status, value, _ = life.request(method, path, payload)
    assert status == expected, (path, status, value)
    return value


def _life_id(life: EmbeddedLifeRuntime) -> str:
    return str(life._active()["life_id"])


def test_autonomy_batch_head_failure_is_fully_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    life = _runtime(tmp_path)
    try:
        _request(
            life,
            "POST",
            "/api/v1/v3/life/memory/assert",
            {
                "memory_id": "mem_low_confidence_cause",
                "content": {"text": "Because the input may be incomplete, the result is uncertain."},
                "epistemic_status": "hypothesis",
                "confidence_milli": 100,
            },
        )
        before_events = life.system.journal.events(_life_id(life))
        before_tasks = dict(life._autonomy_state()["tasks"])
        original = life.system.journal._write_signed_head
        monkeypatch.setattr(
            life.system.journal,
            "_write_signed_head",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("simulated head fsync failure")),
        )
        result = _request(
            life,
            "POST",
            "/api/v1/v3/life/autonomy/tick",
            {"reason": "atomicity-fault"},
            expected=500,
        )
        assert result["ok"] is False
        assert life.system.journal.events(_life_id(life)) == before_events
        assert life._autonomy_state()["tasks"] == before_tasks
        monkeypatch.setattr(life.system.journal, "_write_signed_head", original)
        assert life.system.journal.verify(_life_id(life))["valid"] is True
    finally:
        life.close()


def test_append_cache_detects_external_event_tamper(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    try:
        _request(life, "POST", "/api/v1/v3/life/memory/assert", {"memory_id": "mem_one", "content": {"text": "one"}})
        journal = life.system.journal
        life_id = _life_id(life)
        path = journal._path(life_id)
        events = journal.events(life_id)
        events[0]["actor"] = "external-tamper"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in events) + "\n", encoding="utf-8")
        with pytest.raises(LifeCoreError):
            journal.append(life_id, "test.after_tamper", {"value": 1})
    finally:
        life.close()


def test_thousand_memory_writes_restart_and_search_are_bounded(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    started = time.perf_counter()
    try:
        for index in range(1000):
            _request(
                life,
                "POST",
                "/api/v1/v3/life/memory/assert",
                {
                    "memory_id": f"mem_scale_{index:04d}",
                    "content": {"text": f"Scale memory number {index}; cause batch {index // 10}."},
                    "confidence_milli": 900,
                },
            )
        write_seconds = time.perf_counter() - started
        assert len(life._scope_state()["memories"]) == 1000
        # The bound is intentionally generous for slower CI disks, but still
        # catches the prior O(n^2) journal implementation.
        assert write_seconds < 45.0, write_seconds
    finally:
        life.close()

    restart_started = time.perf_counter()
    life = _runtime(tmp_path)
    try:
        restart_seconds = time.perf_counter() - restart_started
        search_started = time.perf_counter()
        result = _request(
            life,
            "POST",
            "/api/v1/v3/life/memory/search",
            {"query": "Scale memory number 999", "limit": 10},
        )
        search_seconds = time.perf_counter() - search_started
        assert any(item["memory_id"] == "mem_scale_0999" for item in result["results"])
        assert restart_seconds < 15.0, restart_seconds
        assert search_seconds < 3.0, search_seconds
        assert life.system.journal.verify(_life_id(life))["valid"] is True
    finally:
        life.close()


def test_append_batch_idempotency_conflict_writes_nothing(tmp_path: Path) -> None:
    life = _runtime(tmp_path)
    try:
        journal = life.system.journal
        life_id = _life_id(life)
        journal.append_batch(
            life_id,
            [
                {
                    "event_type": "test.first",
                    "payload": {"value": 1},
                    "idempotency_key": "same-key",
                }
            ],
        )
        before = journal.events(life_id)
        with pytest.raises(LifeCoreError) as caught:
            journal.append_batch(
                life_id,
                [
                    {
                        "event_type": "test.first",
                        "payload": {"value": 2},
                        "idempotency_key": "same-key",
                    },
                    {
                        "event_type": "test.second",
                        "payload": {"value": 3},
                        "idempotency_key": "second-key",
                    },
                ],
            )
        assert caught.value.code == "journal_idempotency_conflict"
        assert journal.events(life_id) == before
        assert journal.verify(life_id)["valid"] is True
    finally:
        life.close()


def test_signed_head_is_restored_when_post_replace_step_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    life = _runtime(tmp_path)
    try:
        journal = life.system.journal
        life_id = _life_id(life)
        journal.append(life_id, "test.seed", {"value": 1}, idempotency_key="seed")
        before_events = journal.events(life_id)
        before_head = journal._head_path(life_id).read_bytes()
        original = journal._write_signed_head

        def write_then_fail(target_life_id, chain):
            original(target_life_id, chain)
            raise OSError("simulated chmod failure after atomic head replace")

        monkeypatch.setattr(journal, "_write_signed_head", write_then_fail)
        with pytest.raises(OSError):
            journal.append_batch(
                life_id,
                [
                    {"event_type": "test.batch.a", "payload": {"value": 2}, "idempotency_key": "batch-a"},
                    {"event_type": "test.batch.b", "payload": {"value": 3}, "idempotency_key": "batch-b"},
                ],
            )
        monkeypatch.setattr(journal, "_write_signed_head", original)
        assert journal.events(life_id) == before_events
        assert journal._head_path(life_id).read_bytes() == before_head
        assert journal.verify(life_id)["valid"] is True
    finally:
        life.close()


def test_projection_persist_failure_immediately_closes_readiness_until_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import life_service.embedded_runtime as embedded_module

    life = _runtime(tmp_path)
    original = embedded_module.atomic_json
    try:
        def fail_state(path, value):
            if Path(path) == life.paths.state_file:
                raise OSError("simulated read-only state volume")
            return original(path, value)

        monkeypatch.setattr(embedded_module, "atomic_json", fail_state)
        _request(
            life,
            "POST",
            "/api/v1/v3/life/memory/assert",
            {"memory_id": "mem_projection_wal", "content": {"text": "recover from journal"}},
            expected=500,
        )
        ready = _request(life, "GET", "/ready", expected=503)
        assert "life.projection.persist_failed" in ready["reason_codes"]
        health = _request(life, "GET", "/health")
        assert health["life_ready"] is False
        blocked = _request(
            life,
            "POST",
            "/api/v1/v3/life/memory/search",
            {"query": "recover"},
            expected=503,
        )
        assert blocked["error_code"] == "life.projection.persist_failed"
    finally:
        monkeypatch.setattr(embedded_module, "atomic_json", original)
        life.close()

    life = _runtime(tmp_path)
    try:
        assert _request(life, "GET", "/ready")["status"] == "READY"
        result = _request(life, "POST", "/api/v1/v3/life/memory/search", {"query": "recover"})
        assert [item["memory_id"] for item in result["results"]] == ["mem_projection_wal"]
    finally:
        life.close()
