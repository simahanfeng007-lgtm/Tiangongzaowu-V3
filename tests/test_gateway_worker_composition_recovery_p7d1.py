"""Focused P7D.1 recovery and watchdog regressions for orchestration.

The recovery cases use the real Effect store but a minimal immutable Fact
source.  No backend transport is installed: recovery must decide only from
durable state and must never replay a handler.
"""

from __future__ import annotations

import ast
import concurrent.futures
import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from contracts import (
    ExecutionResult,
    FactRecord,
    canonical_sha256,
    derive_effect_identity,
    derive_run_identity,
)
from total_gateway.backend_client import BackendClientError
from total_gateway.effects import EffectClaim
from total_gateway.fact_ledger import FactBatchRecord
from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError
import total_gateway.orchestration as orchestration_module
from total_gateway.store import GatewayStateStore


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION_SOURCE = ROOT / "src" / "total_gateway" / "orchestration.py"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = derive_run_identity(REQUEST_ID, 1).run_id


class _FactSource:
    def __init__(self, batch: FactBatchRecord | None) -> None:
        self.batch = batch
        self.lookups: list[tuple[str, bool]] = []

    def get_batch_for_effect(
        self,
        effect_id: str,
        *,
        verify_payload: bool = True,
    ) -> FactBatchRecord | None:
        self.lookups.append((effect_id, verify_payload))
        return self.batch


class _ForbiddenExecutionPool:
    """Recovery has no authority to schedule an execution handler."""

    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, *args, **kwargs):  # pragma: no cover - forbidden path
        del args, kwargs
        self.submit_calls += 1
        raise AssertionError("recovery replayed the backend handler")


def _started_parent(
    store: GatewayStateStore,
    *,
    pipeline_version: str = "unspecified",
) -> EffectClaim:
    intent_sha256 = canonical_sha256(
        {"case": "p7d1-parent-recovery", "pipeline": pipeline_version}
    )
    identity = derive_effect_identity(
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        run_sequence=1,
        generation=1,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=intent_sha256,
    )
    claim = EffectClaim(
        effect_id=identity.effect_id,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        run_sequence=1,
        generation=1,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=intent_sha256,
        pipeline_version=pipeline_version,
        attempt=1,
        owner_component_id="tiangong-backend",
        claimed_at_ms=1_000,
        claim_sha256="0" * 64,
    ).with_computed_sha256()
    store.claim_effect(claim)
    store.mark_effect_started(claim.effect_id, started_at_ms=1_100)
    return claim


def _exact_parent_batch(claim: EffectClaim) -> FactBatchRecord:
    result = ExecutionResult(
        result_id="execution_result_parent_recovery_p7d1",
        ticket_id="ticket_parent_recovery_p7d1",
        request_id=claim.request_id,
        run_id=claim.run_id,
        generation=claim.generation,
        effect_id=claim.effect_id,
        action_id="gateway.model.run",
        action_version="1.0.0",
        status="SUCCEEDED",
        attempt=claim.attempt,
        started_at_ms=1_100,
        finished_at_ms=1_200,
        side_effect_started=True,
        result_payload_sha256=HASH_A,
        receipt_sha256=HASH_B,
        output_object_refs=(),
        fact_ids=("fact_parent_recovery_p7d1",),
    )
    fact = FactRecord(
        fact_id=result.fact_ids[0],
        fact_type="execution.succeeded",
        source_component_id="tiangong-backend",
        request_id=result.request_id,
        run_id=result.run_id,
        generation=result.generation,
        ticket_id=result.ticket_id,
        effect_id=result.effect_id,
        action_id=result.action_id,
        action_version=result.action_version,
        observed_at_ms=1_200,
        payload_sha256=HASH_A,
        evidence_sha256=HASH_B,
        verification_method="component_receipt",
        fact_sha256="0" * 64,
    ).with_computed_sha256()
    return FactBatchRecord(
        result=result,
        facts=(fact,),
        source_component_id="tiangong-backend",
        observed_at_ms=1_200,
        tenant_id="tenant_p7d1",
        link_account_id="account_p7d1",
        conversation_scope_hash=HASH_C,
        workspace_id="workspace_p7d1",
        max_output_bytes=1_000_000,
        result_payload_object_id="object_parent_recovery_p7d1",
        result_payload_sha256=HASH_A,
        response_sha256=HASH_B,
        batch_sha256=HASH_C,
    )


def _recovery_worker(
    store: GatewayStateStore,
    facts: _FactSource,
) -> GatewayOrchestrationWorker:
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._store = store
    worker._facts = facts
    return worker


def test_startup_discovery_leaves_noncomposition_parent_to_generic_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        claim = _started_parent(store)
        facts = _FactSource(_exact_parent_batch(claim))
        pool = _ForbiddenExecutionPool()
        monkeypatch.setattr(orchestration_module, "_EXECUTION_WATCHDOG_POOL", pool)

        protected = (
            _recovery_worker(store, facts)
            ._composition_parent_started_effect_ids()
        )

        head = store.get_effect(claim.effect_id)
        assert protected == ()
        assert head is not None
        assert head.state == "SIDE_EFFECT_STARTED"
        assert head.result is None
        assert facts.lookups == []
        assert pool.submit_calls == 0

        recovered = store.recover_started_effects(
            now_ms=1_300,
            exclude_effect_ids=protected,
        )
        assert len(recovered) == 1
        head = store.get_effect(claim.effect_id)
        assert head is not None and head.state == "FAILED_FINAL"
        assert head.result is not None
        assert head.result.error_code == "effect.execution_interrupted_by_restart"
    finally:
        store.close()


def test_started_noncomposition_parent_without_fact_keeps_generic_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        claim = _started_parent(store)
        facts = _FactSource(None)
        pool = _ForbiddenExecutionPool()
        monkeypatch.setattr(orchestration_module, "_EXECUTION_WATCHDOG_POOL", pool)

        worker = _recovery_worker(store, facts)
        protected = worker._composition_parent_started_effect_ids()
        recovered = store.recover_started_effects(
            now_ms=1_050,
            exclude_effect_ids=protected,
        )

        head = store.get_effect(claim.effect_id)
        assert len(recovered) == 1
        assert head is not None
        assert head.state == "FAILED_FINAL"
        assert head.result is not None
        assert head.result.error_code == "effect.execution_interrupted_by_restart"
        assert head.result.observed_at_ms == 1_100
        assert facts.lookups == []
        assert pool.submit_calls == 0
    finally:
        store.close()


def test_parent_fact_must_match_full_execution_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        claim = _started_parent(store)
        exact = _exact_parent_batch(claim)
        facts = _FactSource(exact)
        pool = _ForbiddenExecutionPool()
        monkeypatch.setattr(orchestration_module, "_EXECUTION_WATCHDOG_POOL", pool)
        worker = _recovery_worker(store, facts)
        mismatches = {
            "fact identity": {"fact_ids": ("fact_other_parent",)},
            "effect identity": {"effect_id": "eff_" + "9" * 64},
            "request identity": {"request_id": "req_" + "9" * 64},
            "run identity": {"run_id": "run_" + "9" * 64},
            "generation identity": {"generation": 2},
            "action": {"action_id": "gateway.other.run"},
            "action version": {"action_version": "2.0.0"},
            "attempt": {"attempt": 2},
            "dispatch boundary": {"side_effect_started": False},
        }

        for label, updates in mismatches.items():
            facts.batch = replace(
                exact,
                result=exact.result.model_copy(update=updates),
            )
            with pytest.raises(OrchestrationError) as caught:
                worker._parent_effect_result_from_batch(
                    store.get_effect(claim.effect_id),
                    facts.batch,
                )
            assert caught.value.code == "orchestration.parent.recovery_fact_mismatch", label
            assert caught.value.ambiguous is True, label
            assert store.get_effect(claim.effect_id).state == "SIDE_EFFECT_STARTED", label

        assert pool.submit_calls == 0
    finally:
        store.close()


def test_composition_parent_discovery_is_limited_to_unspecified_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=900)
    try:
        claim = _started_parent(store, pipeline_version="other-parent-pipeline.v1")
        facts = _FactSource(_exact_parent_batch(claim))
        pool = _ForbiddenExecutionPool()
        monkeypatch.setattr(orchestration_module, "_EXECUTION_WATCHDOG_POOL", pool)

        protected = (
            _recovery_worker(store, facts)
            ._composition_parent_started_effect_ids()
        )

        assert protected == ()
        assert store.get_effect(claim.effect_id).state == "SIDE_EFFECT_STARTED"
        assert facts.lookups == []
        assert pool.submit_calls == 0
    finally:
        store.close()


def test_composition_watchdog_slot_stays_owned_until_timed_out_call_really_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="p7d1-watchdog-test",
    )
    slot = threading.BoundedSemaphore(value=1)
    monkeypatch.setattr(orchestration_module, "_EXECUTION_WATCHDOG_POOL", executor)
    monkeypatch.setattr(orchestration_module, "_COMPOSITION_WATCHDOG_SLOT", slot)
    started = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    caller_done = threading.Event()
    first_errors: list[BaseException] = []
    calls = {"first": 0, "second": 0, "third": 0}

    def hanging_handler() -> dict[str, bool]:
        calls["first"] += 1
        started.set()
        if not release.wait(timeout=2):  # pragma: no cover - test cleanup guard
            raise AssertionError("test did not release the hanging handler")
        exited.set()
        return {"first": True}

    def invoke_first() -> None:
        try:
            orchestration_module._run_backend_transport_with_watchdog(
                hanging_handler,
                timeout_seconds=0.05,
            )
        except BaseException as exc:  # captured for assertions in the test thread
            first_errors.append(exc)
        finally:
            caller_done.set()

    caller = threading.Thread(target=invoke_first, name="p7d1-watchdog-caller")
    caller.start()
    try:
        assert started.wait(timeout=1)
        assert caller_done.wait(timeout=1)
        caller.join(timeout=1)
        assert not caller.is_alive()
        assert len(first_errors) == 1
        first_error = first_errors[0]
        assert isinstance(first_error, BackendClientError)
        assert first_error.code == "backend.composition.execution_timeout"
        assert first_error.ambiguous is True

        def forbidden_second_handler() -> dict[str, bool]:
            calls["second"] += 1
            return {"second": True}

        with pytest.raises(BackendClientError) as caught:
            orchestration_module._run_backend_transport_with_watchdog(
                forbidden_second_handler,
                timeout_seconds=0.2,
            )
        assert caught.value.code == "backend.composition.watchdog_capacity_exhausted"
        assert caught.value.ambiguous is True
        assert calls["second"] == 0
        assert slot.acquire(blocking=False) is False

        release.set()
        assert exited.wait(timeout=1)
        # The Future done callback, rather than the caller timeout, returns the
        # slot.  Acquire with a bound to avoid racing that callback.
        assert slot.acquire(timeout=1)
        slot.release()

        def third_handler() -> dict[str, bool]:
            calls["third"] += 1
            return {"third": True}

        assert orchestration_module._run_backend_transport_with_watchdog(
            third_handler,
            timeout_seconds=0.2,
        ) == {"third": True}
        assert calls == {"first": 1, "second": 0, "third": 1}
    finally:
        release.set()
        caller.join(timeout=1)
        executor.shutdown(wait=True, cancel_futures=True)


def _parent_execution_timeout_try() -> ast.Try:
    """Return the exact parent execution-future wait from process()."""

    tree = ast.parse(
        ORCHESTRATION_SOURCE.read_text(encoding="utf-8"),
        filename=str(ORCHESTRATION_SOURCE),
    )
    worker = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GatewayOrchestrationWorker"
    )
    process = next(
        node
        for node in worker.body
        if isinstance(node, ast.FunctionDef) and node.name == "process"
    )
    candidates = [
        node
        for node in ast.walk(process)
        if isinstance(node, ast.Try)
        and any(
            handler.type is not None
            and ast.unparse(handler.type) == "concurrent.futures.TimeoutError"
            for handler in node.handlers
        )
        and any(
            isinstance(item, ast.Call)
            and ast.unparse(item.func) == "execution_future.result"
            for item in ast.walk(node)
        )
    ]
    assert len(candidates) == 1
    return candidates[0]


def test_parent_execution_future_timeout_calls_cancel_on_the_actual_branch() -> None:
    # Execute the production try/except statements with a controlled Future.
    # This reaches the parent path without constructing the unrelated policy,
    # Life, object-store, and delivery authorities required by process().
    wrapper = ast.parse(
        "def exercise(self, effect, execution_future, watchdog_ms):\n"
        "    pass\n"
    ).body[0]
    assert isinstance(wrapper, ast.FunctionDef)
    wrapper.body = [copy.deepcopy(_parent_execution_timeout_try())]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace = {
        "BackendClientError": BackendClientError,
        "concurrent": concurrent,
    }
    exec(compile(module, str(ORCHESTRATION_SOURCE), "exec"), namespace)

    class _TimeoutFuture:
        def __init__(self) -> None:
            self.cancel_calls = 0
            self.timeouts: list[float] = []

        def result(self, *, timeout: float):
            self.timeouts.append(timeout)
            raise concurrent.futures.TimeoutError

        def cancel(self) -> bool:
            self.cancel_calls += 1
            return True

    future = _TimeoutFuture()
    effect = SimpleNamespace(effect_id="eff_" + "7" * 64)
    owner = SimpleNamespace(
        _store=SimpleNamespace(
            get_effect=lambda effect_id: SimpleNamespace(
                state="SIDE_EFFECT_STARTED"
            )
        )
    )

    with pytest.raises(BackendClientError) as caught:
        namespace["exercise"](owner, effect, future, 250)

    assert future.timeouts == [0.25]
    assert future.cancel_calls == 1
    assert caught.value.code == "effect_execution_timeout"
    assert caught.value.ambiguous is True
