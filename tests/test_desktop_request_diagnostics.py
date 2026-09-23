from types import SimpleNamespace

import pytest

from total_gateway.desktop_api import DesktopApiRouter
from total_gateway.desktop_diagnostics import persist_desktop_request_error
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore, StoreCorruptionError


def test_later_execution_failure_is_not_hidden_by_a_successful_first_step():
    request_id, run_id = "req_" + "a" * 64, "run_" + "b" * 64
    calls = []
    def effect(kind, code, at):
        return SimpleNamespace(claim=SimpleNamespace(effect_kind=kind),
                               result=SimpleNamespace(error_code=code, observed_at_ms=at))
    def scoped_effects(request, **scope):
        calls.append((request, scope))
        return [effect("execution", None, 10),
                effect("execution", "composition.execution.earlier_failure", 20),
                effect("execution", "composition.runtime.action_failed", 30),
                effect("execution", None, 40),
                effect("artifact", "artifact.optional_failure", 50)]
    router = object.__new__(DesktopApiRouter)
    router._runtime = SimpleNamespace(store=SimpleNamespace(
        list_effects_for_request=scoped_effects, list_system_statuses=lambda *_a, **_k: ()))
    detail = router._desktop_error_detail(request_id,
        (SimpleNamespace(machine="request", run_id=run_id, generation=2),))
    assert calls == [(request_id, {"run_id": run_id, "generation": 2})]
    assert detail["code"] == "composition.runtime.action_failed"
    assert detail["service"] == "execution"
    assert "字典动作执行失败" in detail["message"]


def test_ambiguous_execution_is_not_presented_as_definite_failure():
    effect = SimpleNamespace(claim=SimpleNamespace(effect_kind="execution"),
        result=SimpleNamespace(error_code="composition.execution.result_unknown",
                               status="AMBIGUOUS", observed_at_ms=20))
    router = object.__new__(DesktopApiRouter)
    router._runtime = SimpleNamespace(store=SimpleNamespace(
        list_effects_for_request=lambda *_a, **_k: (effect,),
        list_system_statuses=lambda *_a, **_k: ()))
    detail = router._desktop_error_detail("req_" + "c" * 64,
        (SimpleNamespace(machine="request", run_id="run_" + "d" * 64, generation=1),))
    assert "结果尚未确认" in detail["message"]
    assert "先核对" in detail["action"]


@pytest.mark.parametrize("raw,expected", [
    ("COMPOSITION_SOURCE_CONTEXT_UNAVAILABLE", "desktop_composition.source_context_unavailable"),
    ("INSTALLED_SOURCE_GENESIS_UNAVAILABLE", "desktop_composition.source_initialization_failed"),
    ("COMPOSITION_SOURCE_CONTEXT_UNAVAILABLE private exception suffix", "valueerror"),
    ("provider credential must remain private", "valueerror"),
])
def test_only_known_source_failure_codes_reach_desktop(tmp_path, raw, expected):
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=1)
    objects = ContentAddressedObjectStore.open(tmp_path / "objects", now_ms=1)
    request_id, run_id = "req_" + "7" * 64, "run_" + "8" * 64
    activation = SimpleNamespace(entry=SimpleNamespace(request_id=request_id),
        generation=SimpleNamespace(run_id=run_id, generation=1, run_sequence=1),
        envelope=SimpleNamespace(tenant_id="tenant", link_account_id="account", conversation_scope_hash="9" * 64))
    try:
        persist_desktop_request_error(store=store, objects=objects, activation=activation,
                                      error=ValueError(raw), at_ms=10)
        router = object.__new__(DesktopApiRouter)
        router._runtime = SimpleNamespace(store=store, objects=objects)
        detail = router._desktop_error_detail(request_id,
            (SimpleNamespace(machine="request", run_id=run_id, generation=1),))
        assert detail["code"] == expected
        assert raw not in str(detail)
        if expected.startswith("desktop_composition."):
            assert "尚未" in detail["message"]
            assert detail["service"] == "planner"
    finally:
        store.close()
        objects.close()


def test_planning_failure_survives_restart_without_execution_effect(tmp_path):
    db = tmp_path / "gateway.sqlite3"
    store = GatewayStateStore.open(db, now_ms=1)
    objects = ContentAddressedObjectStore.open(tmp_path / "objects", now_ms=1)
    request_id, run_id = "req_" + "1" * 64, "run_" + "2" * 64
    activation = SimpleNamespace(entry=SimpleNamespace(request_id=request_id),
        generation=SimpleNamespace(run_id=run_id, generation=1, run_sequence=1),
        envelope=SimpleNamespace(tenant_id="tenant", link_account_id="account", conversation_scope_hash="3" * 64))
    error = ValueError("ignored raw exception")
    error.code = "desktop_composition.unsupported_task"
    error.detail = "当前范围不支持运行 Python。"
    persist_desktop_request_error(store=store, objects=objects, activation=activation, error=error, at_ms=10)
    persist_desktop_request_error(store=store, objects=objects, activation=activation, error=error, at_ms=11)
    store.close()
    store = GatewayStateStore.open(db, now_ms=12)
    try:
        statuses = store.list_system_statuses(request_id, run_id=run_id, generation=1)
        assert len(statuses) == 1
        assert not store.list_system_statuses(request_id, run_id=run_id, generation=2)
        router = object.__new__(DesktopApiRouter)
        router._runtime = SimpleNamespace(store=store, objects=objects)
        snapshot = SimpleNamespace(machine="request", run_id=run_id, generation=1)
        detail = router._desktop_error_detail(request_id, (snapshot,))
        assert detail["code"] == error.code
        assert error.detail in detail["message"]
        assert detail["service"] == "planner"
        with store._lock:
            store._connection.execute("UPDATE system_status SET payload_sha256=?", ("0" * 64,))
        with pytest.raises(StoreCorruptionError):
            store.list_system_statuses(request_id, run_id=run_id, generation=1)
    finally:
        store.close()
        objects.close()


def test_concurrent_identical_error_keeps_first_durable_diagnostic(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=1)
    objects = ContentAddressedObjectStore.open(tmp_path / "objects", now_ms=1)
    request_id, run_id = "req_" + "4" * 64, "run_" + "5" * 64
    activation = SimpleNamespace(entry=SimpleNamespace(request_id=request_id),
        generation=SimpleNamespace(run_id=run_id, generation=1, run_sequence=1),
        envelope=SimpleNamespace(tenant_id="tenant", link_account_id="account", conversation_scope_hash="6" * 64))
    error = ValueError("raw exception must not be persisted")
    original = store.list_system_statuses
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    calls = 0
    def race(*args, **kwargs):
        nonlocal calls
        values = original(*args, **kwargs)
        with counter_lock:
            calls += 1
            first_reads = calls <= 2
        if first_reads:
            barrier.wait(timeout=5)
        return values
    monkeypatch.setattr(store, "list_system_statuses", race)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(persist_desktop_request_error, store=store, objects=objects,
                                  activation=activation, error=error, at_ms=at) for at in (10, 11)]
            for future in futures:
                future.result(timeout=10)
        assert len(original(request_id, run_id=run_id, generation=1)) == 1
    finally:
        store.close()
        objects.close()
