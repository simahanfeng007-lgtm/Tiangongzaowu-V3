from __future__ import annotations

import inspect
import threading
from pathlib import Path

from contracts.canonical import canonical_sha256
from contracts.world_understanding import (
    ScopeBinding,
    WorldIngressEnvelope,
    WorldScope,
    WorldTime,
    derive_ingress_dedup_key,
    derive_ingress_envelope_id,
    derive_world_id,
    derive_world_scope_hash,
)
from world_understanding import WorldUnderstandingFacade
from world_understanding.ingress.compiler_registry import CompilerRegistry

A = "a" * 64


def scope() -> WorldScope:
    bindings = (
        ScopeBinding(key="branch", value="main"),
        ScopeBinding(key="repository", value="repo.main"),
    )
    world_id = derive_world_id(life_id="life.main", namespace_anchor="primary")
    world_scope_hash = derive_world_scope_hash(
        life_id="life.main",
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
    )
    return WorldScope(
        life_id="life.main",
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=world_scope_hash,
        principal_scope_hash=A,
        privacy_scope="system",
    )


def source_time() -> WorldTime:
    return WorldTime(valid_from_ms=1, observed_at_ms=2, recorded_at_ms=3)


def envelope(
    *,
    source_kind: str = "FACT_EXECUTION",
    envelope_kind: str = "SOURCE_RECORD",
    source_native_id: str = "native.1",
    correlation_id: str = "corr.1",
) -> WorldIngressEnvelope:
    payload = {"kind": envelope_kind, "value": 1}
    payload_sha256 = canonical_sha256(payload)
    world_scope = scope()
    dedup_key = derive_ingress_dedup_key(
        envelope_kind=envelope_kind,
        source_kind=source_kind,
        source_native_id=source_native_id,
        payload_sha256=payload_sha256,
        world_scope_hash=world_scope.world_scope_hash,
    )
    return WorldIngressEnvelope(
        envelope_id=derive_ingress_envelope_id(dedup_key=dedup_key),
        envelope_kind=envelope_kind,
        source_kind=source_kind,
        source_native_id=source_native_id,
        producer_ref="producer.p2.test",
        payload_inline=payload,
        payload_sha256=payload_sha256,
        source_time=source_time(),
        life_id=world_scope.life_id,
        principal_scope_hash=world_scope.principal_scope_hash,
        scope_hint=world_scope,
        correlation_id=correlation_id,
        dedup_key=dedup_key,
    )


def test_facade_has_one_public_physical_ingress_method() -> None:
    methods = {
        name
        for name, value in inspect.getmembers(WorldUnderstandingFacade, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {"accept"}


def test_duplicate_source_is_processed_once_and_returns_cached_receipt() -> None:
    calls: list[str] = []
    registry = CompilerRegistry({"FACT_EXECUTION": lambda item: calls.append(item.envelope_id)})
    facade = WorldUnderstandingFacade(enabled=True, compiler_registry=registry)
    first = facade.accept(envelope())
    second = facade.accept(envelope())
    assert calls == [envelope().envelope_id]
    assert first is second
    assert first.disposition == "ACCEPTED"
    assert first.processed is True
    assert first.has_valid_receipt_sha256()


def test_unclassified_source_is_quarantined_without_compiler_execution() -> None:
    calls: list[int] = []
    registry = CompilerRegistry({"UNCLASSIFIED_SOURCE": lambda _: calls.append(1)})
    receipt = WorldUnderstandingFacade(enabled=True, compiler_registry=registry).accept(
        envelope(source_kind="UNCLASSIFIED_SOURCE")
    )
    assert receipt.disposition == "QUARANTINED"
    assert receipt.reason_code == "UNCLASSIFIED_SOURCE"
    assert calls == []


def test_known_source_without_compiler_is_quarantined() -> None:
    receipt = WorldUnderstandingFacade(enabled=True, compiler_registry=CompilerRegistry()).accept(envelope(source_kind="TOOL_RESULT"))
    assert receipt.disposition == "QUARANTINED"
    assert receipt.reason_code == "NO_COMPILER_REGISTERED"
    assert receipt.processed is False


def test_context_request_is_control_only_and_never_source_compiled() -> None:
    calls: list[int] = []
    registry = CompilerRegistry({"CONTEXT_REQUEST": lambda _: calls.append(1)})
    receipt = WorldUnderstandingFacade(enabled=True, compiler_registry=registry).accept(
        envelope(source_kind="CONTEXT_REQUEST", envelope_kind="CONTEXT_REQUEST")
    )
    assert receipt.disposition == "ACCEPTED"
    assert receipt.reason_code == "CONTEXT_REQUEST_ACCEPTED"
    assert calls == []
    assert receipt.ack_only is True
    assert receipt.semantic_output is False
    assert receipt.may_authorize is False
    assert receipt.may_execute is False
    assert receipt.empirical_evidence_weight_milli == 0


def test_tampered_envelope_is_revalidated_and_rejected_fail_closed() -> None:
    valid = envelope()
    tampered = valid.model_copy(update={"payload_inline": {"tampered": True}})
    receipt = WorldUnderstandingFacade(enabled=True).accept(tampered)
    assert receipt.disposition == "REJECTED"
    assert receipt.reason_code == "MALFORMED_ENVELOPE"
    assert receipt.processed is False


def test_off_mode_is_lazy_noop_without_files_threads_or_compiler(monkeypatch, tmp_path: Path) -> None:
    calls: list[int] = []
    registry = CompilerRegistry({"FACT_EXECUTION": lambda _: calls.append(1)})
    monkeypatch.chdir(tmp_path)
    before_threads = {thread.ident for thread in threading.enumerate()}
    before_files = tuple(tmp_path.rglob("*"))

    facade = WorldUnderstandingFacade(enabled=False, compiler_registry=registry)
    receipt = facade.accept(envelope())

    after_threads = {thread.ident for thread in threading.enumerate()}
    assert facade._ingress is None
    assert tuple(tmp_path.rglob("*")) == before_files
    assert after_threads == before_threads
    assert calls == []
    assert receipt.disposition == "OFF_NOOP"
    assert receipt.processed is False


def test_compiler_failure_is_fail_closed_and_reservation_is_retryable() -> None:
    attempts = {"count": 0}

    def compiler(_: WorldIngressEnvelope) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient compiler failure")

    facade = WorldUnderstandingFacade(
        enabled=True,
        compiler_registry=CompilerRegistry({"FACT_EXECUTION": compiler}),
    )
    first = facade.accept(envelope())
    second = facade.accept(envelope())
    assert first.disposition == "REJECTED"
    assert first.reason_code == "COMPILER_FAILURE"
    assert second.disposition == "ACCEPTED"
    assert attempts["count"] == 2


def test_correlation_id_is_preserved_in_receipt() -> None:
    registry = CompilerRegistry({"FACT_EXECUTION": lambda _: None})
    receipt = WorldUnderstandingFacade(enabled=True, compiler_registry=registry).accept(
        envelope(correlation_id="corr.special")
    )
    assert receipt.correlation_id == "corr.special"


def test_concurrent_duplicate_sources_compile_once() -> None:
    calls: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def compiler(_: WorldIngressEnvelope) -> None:
        with lock:
            calls.append(1)

    facade = WorldUnderstandingFacade(
        enabled=True,
        compiler_registry=CompilerRegistry({"FACT_EXECUTION": compiler}),
    )
    receipts = []

    def run() -> None:
        barrier.wait()
        receipts.append(facade.accept(envelope()))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert len(receipts) == 8
    assert len({id(receipt) for receipt in receipts}) == 1


def test_p2_package_has_no_runtime_tool_network_or_llm_imports() -> None:
    package_root = Path(__file__).resolve().parents[1] / "src" / "world_understanding"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_root.rglob("*.py"))
    forbidden = (
        "subprocess",
        "requests",
        "total_gateway",
        "zongdiaodu",
        "tool_result_contract",
        "openai",
        "anthropic",
    )
    for module in forbidden:
        assert f"import {module}" not in source
        assert f"from {module}" not in source
