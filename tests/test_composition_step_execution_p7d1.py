"""P7D.1 coordinator state-machine and restart-safety tests.

These tests deliberately reuse the production P7C plan/authorization fixture,
the real Gateway Store, and the real FactLedger.  Only the embedded backend
call is replaced with a deterministic in-process response so the assertions
can observe the exact durable boundary ordering.
"""

from __future__ import annotations

import concurrent.futures
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from contracts import (
    ExecutionResult,
    ObjectGrant,
    canonical_sha256,
    derive_effect_identity,
    derive_run_identity,
)
from tests import test_composition_grant_authority_p7c1 as p7c1
from tests.test_execution_contracts import capability_manifest
from total_gateway.backend_client import BackendClient, BackendClientError
from total_gateway.composition_step_execution import (
    CompositionStepExecutionCoordinator,
    CompositionStepExecutionError,
)
from total_gateway.composition_execution_binding import (
    COMPOSITION_STEP_PIPELINE_VERSION,
)
import total_gateway.composition_step_execution as execution_module
import total_gateway.orchestration as orchestration_module
from total_gateway.fact_ledger import FactLedger
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.embedded_backend import EmbeddedBackendRuntime
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.service_ports import CompatibilityJsonClient
from total_gateway.skill_selection import (
    compile_composition_execution_manifest,
    load_model_capability_manifest,
)
from total_gateway.store import GatewayStateStore, StoreConflictError


class _BackendProbe:
    def __init__(self, store, facts, effect_id: str) -> None:
        self._store = store
        self._facts = facts
        self._effect_id = effect_id
        self.calls = 0
        self.states_at_call: list[tuple[str, bool]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        timeout_seconds: float,
        backend_started: bool = False,
        before_request=None,
    ) -> tuple[int, dict[str, Any], str]:
        del method, path, payload, timeout_seconds, backend_started, before_request
        self.calls += 1
        effect = self._store.get_effect(self._effect_id)
        batch = self._facts.get_batch_for_effect(self._effect_id)
        self.states_at_call.append((effect.state, batch is not None))
        value = {"ok": True, "result": {"source": "p7d1-test"}}
        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return 200, value, hashlib.sha256(raw).hexdigest()


class _ParentTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def execute(self, body: bytes, *, timeout_seconds: float) -> dict[str, Any]:
        del body, timeout_seconds
        return self._payload


class _FactCrashProxy:
    """Optionally persist the Fact batch, then emulate process death."""

    def __init__(self, delegate: FactLedger, *, commit_first: bool) -> None:
        self._delegate = delegate
        self._commit_first = commit_first

    def record_execution(self, response, *, observed_at_ms: int):
        if self._commit_first:
            self._delegate.record_execution(response, observed_at_ms=observed_at_ms)
        raise OSError("injected crash at Fact commit boundary")

    def get_batch_for_effect(self, effect_id: str, *, verify_payload: bool = True):
        return self._delegate.get_batch_for_effect(
            effect_id, verify_payload=verify_payload
        )

    def get_batch_for_ticket(self, ticket_id: str, *, verify_payload: bool = True):
        return self._delegate.get_batch_for_ticket(
            ticket_id, verify_payload=verify_payload
        )


@dataclass
class _RuntimeFixture:
    p7c: Any
    manifest: Any
    record: Any
    facts: FactLedger
    backend: _BackendProbe
    coordinator: CompositionStepExecutionCoordinator
    events: list[tuple[str, str, bool]]
    baseline_nonce_count: int


def _seed_successful_parent(
    harness: Any,
    facts: FactLedger,
    *,
    parent_manifest: Any,
    parent_ticket: Any,
    parent_claim: EffectClaim,
) -> None:
    """Persist the real Fact batch and terminal parent Effect required by P7D."""

    parent_args = {"parent": "composition-authority"}
    result_payload = {"parent": "succeeded"}
    result = ExecutionResult(
        result_id="execution_result_parent_p7d1",
        ticket_id=parent_ticket.payload.ticket_id,
        request_id=parent_ticket.payload.request_id,
        run_id=parent_ticket.payload.run_id,
        generation=parent_ticket.payload.generation,
        effect_id=parent_ticket.payload.effect_id,
        action_id=parent_ticket.payload.action_id,
        action_version=parent_ticket.payload.action_version,
        status="SUCCEEDED",
        attempt=1,
        started_at_ms=1_640,
        finished_at_ms=1_660,
        side_effect_started=True,
        result_payload_sha256=canonical_sha256(result_payload),
        receipt_sha256="1" * 64,
        output_object_refs=(),
        fact_ids=("fact_parent_p7d1",),
    )
    transport = _ParentTransport(
        {
            "ok": True,
            "api_contract": "tiangong.desktop.backend.v3",
            "execution_result": result.model_dump(mode="json"),
            "result_payload": result_payload,
        }
    )
    harness.store.claim_effect(parent_claim)
    harness.store.mark_effect_started(
        parent_claim.effect_id, started_at_ms=result.started_at_ms
    )
    response = BackendClient(
        transport,
        harness.store,
        ticket_consumer_instance_id="gateway-parent-p7d1-test",
    ).execute(
        parent_ticket,
        parent_args,
        capability_manifest=parent_manifest,
        trust_bundle=harness.trust,
        now_ms=1_650,
        expected_gateway_epoch=1,
        minimum_generation=parent_ticket.payload.generation,
    )
    batch = facts.record_execution(response, observed_at_ms=1_660).record
    # Match GatewayOrchestrationWorker's production parent projection exactly:
    # parent Effect evidence is the verified backend response digest, while the
    # Fact batch independently binds the content-addressed result payload.
    effect_result = EffectResult(
        result_id="effect-result-" + response.result.result_id[:120],
        effect_id=response.result.effect_id,
        status="SUCCEEDED",
        fact_id=response.result.fact_ids[0],
        result_object_id=batch.result_payload_object_id,
        result_object_sha256=batch.result_payload_sha256,
        evidence_sha256=response.response_sha256,
        observed_at_ms=1_660,
        result_sha256="0" * 64,
    ).with_computed_sha256()
    harness.store.complete_effect(effect_result)


@contextmanager
def _runtime_fixture(root: Path, **harness_kwargs: Any) -> Iterator[_RuntimeFixture]:
    root.mkdir(parents=True, exist_ok=True)
    harness = p7c1._open_harness(root, **harness_kwargs)
    facts = FactLedger.open(root / "facts.sqlite3", harness.objects, now_ms=1_000)
    runtime: _RuntimeFixture | None = None
    try:
        model = load_model_capability_manifest(
            p7c1.CAPABILITY_MANIFEST,
            expected_sha256=hashlib.sha256(
                p7c1.CAPABILITY_MANIFEST.read_bytes()
            ).hexdigest(),
            component_manifest_hash=p7c1.COMPONENT_MANIFEST_SHA256,
            generated_at_ms=1_250,
        ).manifest
        manifest = compile_composition_execution_manifest(
            model,
            harness.loaded.registry,
            harness.loaded.schema_catalog,
            generated_at_ms=1_250,
        )
        # P7C's reusable test world intentionally uses a sentinel result-schema
        # hash instead of the model-manifest hash.  Align that one test action
        # so this fixture can exercise the runtime state machine; compiler
        # cross-authority drift remains covered by the dedicated P7D manifest
        # tests.
        sealed_result_schema = harness.plan.step_bindings[0].result_schema_sha256
        manifest = manifest.model_copy(
            update={
                "actions": tuple(
                    action.model_copy(
                        update={"result_schema_sha256": sealed_result_schema}
                    )
                    if action.action_id == harness.plan.step_bindings[0].action_id
                    else action
                    for action in manifest.actions
                ),
                "sha256": "0" * 64,
            }
        ).with_computed_sha256()

        parent_args = {"parent": "composition-authority"}
        parent_manifest = capability_manifest()
        parent_intent_sha256 = canonical_sha256(
            {
                "domain": "tiangong.test.composition-parent.v1",
                "request_id": harness.plan.request_id,
                "run_id": harness.plan.run_id,
                "generation": harness.plan.generation,
            }
        )
        parent_identity = derive_effect_identity(
            request_id=harness.plan.request_id,
            run_id=harness.plan.run_id,
            run_sequence=1,
            generation=harness.plan.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=parent_intent_sha256,
        )
        parent_claim = EffectClaim(
            effect_id=parent_identity.effect_id,
            request_id=harness.plan.request_id,
            run_id=harness.plan.run_id,
            run_sequence=1,
            generation=harness.plan.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=parent_intent_sha256,
            owner_component_id="tiangong-total-gateway",
            claimed_at_ms=1_600,
            claim_sha256="0" * 64,
        ).with_computed_sha256()
        outer = harness.outer.payload
        parent_ticket = p7c1.execution_ticket(
            manifest=parent_manifest,
            ticket_id="ticket_parent_p7d1",
            nonce="nonce_parent_p7d1",
            issued_at_ms=outer.issued_at_ms,
            not_before_ms=outer.not_before_ms,
            expires_at_ms=outer.expires_at_ms,
            gateway_epoch=outer.gateway_epoch,
            request_id=outer.request_id,
            run_id=outer.run_id,
            generation=outer.generation,
            effect_id=parent_identity.effect_id,
            channel=outer.channel,
            tenant_id=outer.tenant_id,
            link_account_id=outer.link_account_id,
            conversation_scope_hash=outer.conversation_scope_hash,
            principal_scope_hash=outer.principal_scope_hash,
            arguments_hash=canonical_sha256(parent_args),
            workspace_id=outer.workspace_id,
            input_objects=outer.input_objects,
            max_output_bytes=1_000_000,
            max_runtime_ms=30_000,
            max_tool_calls=1,
        )
        parent_ticket = harness.signer.sign_execution(parent_ticket.payload)

        # The shared P7C fixture predates the split between the outer
        # compatibility manifest and the child execution manifest.  Mutating
        # this test-only authority before issuance exercises the new split
        # without weakening any signed contract.
        authority = p7c1._authority(
            harness,
            capability_manifest_hash=parent_manifest.sha256,
            outer=parent_ticket,
        )
        authority.composition_capability_manifest_hash = manifest.sha256
        p7c1._authorize(
            harness,
            authority=authority,
            parent_ticket_id=parent_ticket.payload.ticket_id,
        )
        record = harness.store.get_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
            now_ms=1_700,
        )
        assert record is not None

        _seed_successful_parent(
            harness,
            facts,
            parent_manifest=parent_manifest,
            parent_ticket=parent_ticket,
            parent_claim=parent_claim,
        )
        baseline_nonce_count = _nonce_count(harness.store)

        backend = _BackendProbe(
            harness.store, facts, record.request.prebound_effect_id
        )
        events: list[tuple[str, str, bool]] = []

        def append_event(store, **kwargs) -> bool:
            effect = store.get_effect(kwargs["effect_id"])
            batch = facts.get_batch_for_effect(kwargs["effect_id"])
            events.append((kwargs["event_type"], effect.state, batch is not None))
            return True

        generation = harness.store.get_generation(record.request.request_id)
        assert generation is not None
        assert generation.owner_instance_id is not None
        coordinator = CompositionStepExecutionCoordinator(
            store=harness.store,
            objects=harness.objects,
            facts=facts,
            registry=harness.loaded.registry,
            schema_catalog=harness.loaded.schema_catalog,
            capability_manifest=manifest,
            trust_bundle_provider=lambda _now_ms: harness.trust,
            backend_compat_client=backend,
            workspace_root=root.resolve(strict=True),
            gateway_epoch=1,
            gateway_instance_id=generation.owner_instance_id,
            append_effect_event=append_event,
        )
        runtime = _RuntimeFixture(
            harness,
            manifest,
            record,
            facts,
            backend,
            coordinator,
            events,
            baseline_nonce_count,
        )
        yield runtime
    finally:
        if runtime is not None and runtime.facts is not facts:
            runtime.facts.close()
        facts.close()
        harness.close()


def _nonce_count(store) -> int:
    return int(
        store._connection.execute(  # noqa: SLF001 - boundary assertion
            "SELECT count(*) FROM security_nonce_ledger"
        ).fetchone()[0]
    )


def _exact_scope(fixture: _RuntimeFixture) -> dict[str, Any]:
    request = fixture.record.request
    return {
        "request_id": request.request_id,
        "run_id": request.run_id,
        "generation": request.generation,
    }


def _reopen_runtime(
    fixture: _RuntimeFixture,
    *,
    now_ms: int,
    gateway_epoch: int = 1,
    trust_bundle: Any | None = None,
) -> None:
    """Emulate a process restart with fresh SQLite/Object authority handles."""

    harness = fixture.p7c
    fixture.facts.close()
    harness.objects.close()
    harness.store.close()

    store = GatewayStateStore.open(harness.database_path, now_ms=now_ms)
    objects = ContentAddressedObjectStore.open(
        harness.root / "objects", now_ms=now_ms
    )
    facts = FactLedger.open(
        harness.root / "facts.sqlite3", objects, now_ms=now_ms
    )
    backend = _BackendProbe(
        store, facts, fixture.record.request.prebound_effect_id
    )
    events: list[tuple[str, str, bool]] = []

    def append_event(event_store, **kwargs) -> bool:
        effect = event_store.get_effect(kwargs["effect_id"])
        batch = facts.get_batch_for_effect(kwargs["effect_id"])
        events.append((kwargs["event_type"], effect.state, batch is not None))
        return True

    generation = store.get_generation(fixture.record.request.request_id)
    assert generation is not None
    coordinator = CompositionStepExecutionCoordinator(
        store=store,
        objects=objects,
        facts=facts,
        registry=harness.loaded.registry,
        schema_catalog=harness.loaded.schema_catalog,
        capability_manifest=fixture.manifest,
        trust_bundle_provider=lambda _now_ms: (
            harness.trust if trust_bundle is None else trust_bundle
        ),
        backend_compat_client=backend,
        workspace_root=harness.root.resolve(strict=True),
        gateway_epoch=gateway_epoch,
        gateway_instance_id=generation.owner_instance_id,
        append_effect_event=append_event,
    )
    harness.store = store
    harness.objects = objects
    fixture.facts = facts
    fixture.backend = backend
    fixture.coordinator = coordinator
    fixture.events = events


def test_dispatch_orders_started_handler_fact_effect_and_terminal_event(
    tmp_path: Path,
) -> None:
    # Unicode makes the test cover the Windows path representation used by the
    # real repository, while the plan compiler still supplies its canonical
    # resolved spelling.
    with _runtime_fixture(tmp_path / "天工造物-路径") as fixture:
        outcome = fixture.coordinator.dispatch_record(
            fixture.record, now_ms=1_700
        )

        assert outcome.status == "SUCCEEDED"
        assert fixture.backend.calls == 1
        assert fixture.backend.states_at_call == [("SIDE_EFFECT_STARTED", False)]
        assert fixture.events == [
            ("step.prepared", "CLAIMED", False),
            ("step.dispatched", "SIDE_EFFECT_STARTED", False),
            ("step.committed", "SUCCEEDED", True),
        ]
        assert (
            _nonce_count(fixture.p7c.store)
            == fixture.baseline_nonce_count + 1
        )
        attempt = fixture.p7c.store.get_effect_attempt(outcome.effect_id, 1)
        assert attempt["state"] == "SUCCEEDED"
        assert attempt["pipeline_version"] == COMPOSITION_STEP_PIPELINE_VERSION
        assert fixture.facts.get_batch_for_effect(outcome.effect_id) is not None


def test_timeout_retains_store_permit_until_running_handler_really_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="p7d1-store-permit-test",
    )
    slot = threading.BoundedSemaphore(value=1)
    monkeypatch.setattr(orchestration_module, "_EXECUTION_WATCHDOG_POOL", executor)
    monkeypatch.setattr(orchestration_module, "_COMPOSITION_WATCHDOG_SLOT", slot)
    started = threading.Event()
    release = threading.Event()
    exited = threading.Event()

    try:
        with _runtime_fixture(tmp_path / "timeout-store-permit") as fixture:
            class BlockingBackend(_BackendProbe):
                def request(self, *args, **kwargs):
                    started.set()
                    if not release.wait(timeout=2):  # pragma: no cover - cleanup guard
                        raise AssertionError("test did not release the handler")
                    try:
                        return super().request(*args, **kwargs)
                    finally:
                        exited.set()

            backend = BlockingBackend(
                fixture.p7c.store,
                fixture.facts,
                fixture.record.request.prebound_effect_id,
            )
            fixture.coordinator._backend = backend  # noqa: SLF001
            fixture.coordinator._transport_runner = (  # noqa: SLF001
                lambda call, _signed_timeout: (
                    orchestration_module._run_backend_transport_with_watchdog(
                        call,
                        timeout_seconds=0.05,
                    )
                )
            )

            outcome = fixture.coordinator.dispatch_record(
                fixture.record,
                now_ms=1_700,
            )

            assert started.is_set()
            assert outcome.status == "AMBIGUOUS"
            assert fixture.p7c.store.get_effect(outcome.effect_id).state == "AMBIGUOUS"
            assert fixture.p7c.store.action_fence_status()["inflight_count"] == 1
            assert fixture.facts.get_batch_for_effect(outcome.effect_id) is None

            release.set()
            assert exited.wait(timeout=1)
            deadline = time.monotonic() + 1
            while (
                fixture.p7c.store.action_fence_status()["inflight_count"] != 0
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert fixture.p7c.store.action_fence_status()["inflight_count"] == 0
            # Explicit release is idempotent after the Future callback.
            fixture.p7c.store.release_dispatch_permit(
                effect_id=outcome.effect_id,
                attempt=1,
                now_ms=2_000,
            )
            assert fixture.p7c.store.action_fence_status()["inflight_count"] == 0
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)


def test_restart_releases_terminal_permit_left_by_timed_out_process(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "timeout-terminal-restart") as fixture:
        callbacks: list[Any] = []

        class PendingTransport:
            def add_done_callback(self, callback) -> None:
                callbacks.append(callback)

        pending = PendingTransport()

        def timed_out_runner(_call, _timeout_seconds):
            raise BackendClientError(
                "backend.composition.execution_timeout",
                ambiguous=True,
                pending_transport_future=pending,
            )

        fixture.coordinator._transport_runner = timed_out_runner  # noqa: SLF001
        outcome = fixture.coordinator.dispatch_record(
            fixture.record,
            now_ms=1_700,
        )
        assert outcome.status == "AMBIGUOUS"
        assert len(callbacks) == 1
        assert fixture.p7c.store.action_fence_status()["inflight_count"] == 1

        # Emulate process loss before the pending Future invokes its callback.
        _reopen_runtime(fixture, now_ms=2_000)
        assert fixture.p7c.store.action_fence_status()["inflight_count"] == 1
        assert fixture.coordinator.recover_started(now_ms=2_000) == ()
        assert fixture.p7c.store.get_effect(outcome.effect_id).state == "AMBIGUOUS"
        assert fixture.p7c.store.action_fence_status()["inflight_count"] == 0


@pytest.mark.parametrize("status", ("SUCCEEDED", "FAILED_FINAL", "RECONCILED"))
def test_store_rejects_retaining_permit_for_nonambiguous_terminal(
    tmp_path: Path,
    status: str,
) -> None:
    with _runtime_fixture(tmp_path / f"retain-permit-{status.casefold()}") as fixture:
        prepared = fixture.coordinator._preflight(  # noqa: SLF001
            fixture.record,
            now_ms=1_700,
        )
        claim = prepared["claim"]
        fixture.p7c.store.claim_effect(claim)
        fixture.p7c.store.mark_effect_started(
            claim.effect_id,
            started_at_ms=1_700,
        )
        result = EffectResult(
            result_id="effect-result-invalid-retention-" + status.casefold(),
            effect_id=claim.effect_id,
            status=status,
            fact_id="fact-invalid-retention-" + status.casefold(),
            evidence_sha256=canonical_sha256(
                {"effect_id": claim.effect_id, "status": status}
            ),
            error_code=(
                "p7d1.test.invalid_retention" if status == "FAILED_FINAL" else None
            ),
            observed_at_ms=1_701,
            result_sha256="0" * 64,
        ).with_computed_sha256()

        with pytest.raises(
            ValueError,
            match="retaining an effect dispatch permit requires an ambiguous result",
        ):
            fixture.p7c.store.complete_effect(
                result,
                release_dispatch_permit=False,
            )

        assert fixture.p7c.store.get_effect(claim.effect_id).state == "SIDE_EFFECT_STARTED"


def test_private_handler_requires_dispatch_permit_and_consumes_grant_once(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "handler-permit") as fixture:
        prepared = fixture.coordinator._preflight(  # noqa: SLF001
            fixture.record,
            now_ms=1_700,
        )
        store = fixture.p7c.store
        claim = prepared["claim"]
        ticket = prepared["ticket"]
        grant = prepared["grant"]
        store.claim_effect(claim)
        ticket_sha256 = canonical_sha256(ticket.model_dump(mode="json"))
        grant_sha256 = canonical_sha256(grant.model_dump(mode="json"))
        generation = store.get_generation(claim.request_id)
        assert generation is not None
        assert generation.owner_instance_id is not None
        ticket_consumer = (
            "composition-inprocess-" + generation.owner_instance_id
        )
        handler_consumer = (
            "composition-runtime-" + generation.owner_instance_id
        )
        handler_kwargs = {
            "effect_id": claim.effect_id,
            "ticket_id": ticket.payload.ticket_id,
            "ticket_nonce": ticket.payload.nonce,
            "ticket_sha256": ticket_sha256,
            "grant_nonce": grant.payload.nonce,
            "grant_sha256": grant_sha256,
            "gateway_epoch": 1,
            "expected_ticket_consumer_instance_id": ticket_consumer,
            "handler_consumer_instance_id": handler_consumer,
            "now_ms": 1_701,
        }

        with pytest.raises(StoreConflictError, match="Effect permit"):
            store.consume_composition_handler_permit(**handler_kwargs)

        store.acquire_dispatch_permit(
            effect_id=claim.effect_id,
            attempt=claim.attempt,
            expected_fence_epoch=fixture.record.request.action_fence_epoch,
            nonce_sha256=canonical_sha256(
                {
                    "execution_ticket_nonce": ticket.payload.nonce,
                    "omni_grant_nonce": grant.payload.nonce,
                }
            ),
            ticket_id=ticket.payload.ticket_id,
            ticket_sha256=ticket_sha256,
            grant_sha256=grant_sha256,
            expected_request_id=claim.request_id,
            expected_run_id=claim.run_id,
            expected_generation=claim.generation,
            expected_gateway_epoch=1,
            expected_owner_instance_id=generation.owner_instance_id,
            required_parent_effect_id=prepared["parent_effect_id"],
            now_ms=1_702,
        )
        store.consume_security_nonce(
            issuer=ticket.payload.issuer,
            audience=ticket.payload.audience,
            purpose="execution_ticket",
            nonce=ticket.payload.nonce,
            payload_sha256=ticket_sha256,
            gateway_epoch=ticket.payload.gateway_epoch,
            consumer_instance_id=ticket_consumer,
            consumed_at_ms=1_703,
            expires_at_ms=ticket.payload.expires_at_ms,
        )
        handler_kwargs["now_ms"] = 1_704
        admitted = store.consume_composition_handler_permit(**handler_kwargs)
        assert admitted == {
            "effect_id": claim.effect_id,
            "attempt": 1,
            "handler_consumer_instance_id": handler_consumer,
        }
        assert _nonce_count(store) == fixture.baseline_nonce_count + 2

        with pytest.raises(StoreConflictError, match="already consumed"):
            store.consume_composition_handler_permit(**handler_kwargs)


def test_real_bound_embedded_route_runs_only_through_gateway_permit(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "real-embedded-route") as fixture:
        runner_calls: list[dict[str, Any]] = []

        def runner(payload: dict[str, Any]) -> dict[str, Any]:
            runner_calls.append(payload)
            return {"ok": True, "result": {"source": "real-embedded-route"}}

        backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
        backend._lock = threading.RLock()
        backend._closed = False
        backend._closing = False
        backend.qiaojie = SimpleNamespace(
            _core_execution_lock=threading.RLock()
        )
        backend.scheduler = SimpleNamespace()
        client = CompatibilityJsonClient(backend)
        worker = object.__new__(GatewayOrchestrationWorker)
        worker._store = fixture.p7c.store
        worker._epoch = 1
        generation = fixture.p7c.store.get_generation(
            fixture.record.request.request_id
        )
        assert generation is not None
        assert generation.owner_instance_id is not None
        worker._instance_id = generation.owner_instance_id
        worker._authority = SimpleNamespace(
            execution_trust_bundle=lambda **_kwargs: fixture.p7c.trust
        )
        client.set_composition_dispatch_authorizer(
            worker._authorize_composition_handler_entry
        )
        fixture.coordinator._backend = client  # noqa: SLF001

        def import_module(name: str):
            assert name == "v3.jineng.jirou_ceng"
            return SimpleNamespace(_run_omni_body_tool=runner)

        with mock.patch(
            "total_gateway.embedded_backend.time.time_ns",
            return_value=1_700_000_000,
        ), mock.patch(
            "total_gateway.embedded_backend.importlib.import_module",
            side_effect=import_module,
        ):
            outcome = fixture.coordinator.dispatch_record(
                fixture.record,
                now_ms=1_700,
            )

        assert outcome.status == "SUCCEEDED"
        assert len(runner_calls) == 1
        assert runner_calls[0]["action"] == fixture.record.request.action_id
        assert _nonce_count(fixture.p7c.store) == fixture.baseline_nonce_count + 2
        assert fixture.facts.get_batch_for_effect(outcome.effect_id) is not None


def test_valid_nonempty_opaque_target_reaches_handler(tmp_path: Path) -> None:
    with _runtime_fixture(
        tmp_path / "opaque-target",
        action_id="skill.get",
        target="word_delivery",
        arguments={},
    ) as fixture:
        outcome = fixture.coordinator.dispatch_record(
            fixture.record, now_ms=1_700
        )

        assert outcome.status == "SUCCEEDED"
        assert fixture.backend.calls == 1
        assert fixture.record.request.target == "word_delivery"


def test_handler_attempt_substitution_is_ambiguous_and_writes_no_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _runtime_fixture(tmp_path / "attempt-substitution") as fixture:
        canonical_transport = execution_module.CompositionBackendExecutionTransport

        class _AttemptSubstitutionTransport:
            def __init__(self, *args, **kwargs) -> None:
                self._delegate = canonical_transport(*args, **kwargs)

            def execute(
                self, body: bytes, *, timeout_seconds: float
            ) -> dict[str, Any]:
                response = self._delegate.execute(
                    body, timeout_seconds=timeout_seconds
                )
                execution_result = dict(response["execution_result"])
                execution_result["attempt"] = 2
                return {**response, "execution_result": execution_result}

        monkeypatch.setattr(
            execution_module,
            "CompositionBackendExecutionTransport",
            _AttemptSubstitutionTransport,
        )
        outcome = fixture.coordinator.dispatch_record(
            fixture.record, now_ms=1_700
        )

        assert outcome.status == "AMBIGUOUS"
        assert fixture.backend.calls == 1
        assert fixture.facts.get_batch_for_effect(outcome.effect_id) is None
        assert fixture.p7c.store.get_effect(outcome.effect_id).state == "AMBIGUOUS"
        assert fixture.events == [
            ("step.prepared", "CLAIMED", False),
            ("step.dispatched", "SIDE_EFFECT_STARTED", False),
            ("step.ambiguous", "AMBIGUOUS", False),
        ]


def test_exact_scope_plan_with_missing_or_expired_receipt_fails_closed(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "receipt-obligation") as fixture:
        store = fixture.p7c.store
        assert store.get_executable_composition_plan_for_request(
            **_exact_scope(fixture)
        ) is not None

        class _MissingReceiptStore:
            def __getattr__(self, name: str):
                return getattr(store, name)

            def get_composition_step_authorization(self, *args, **kwargs):
                del args, kwargs
                return None

        fixture.coordinator._store = _MissingReceiptStore()  # noqa: SLF001
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.authorization_missing",
        ):
            fixture.coordinator.dispatch_next(
                now_ms=1_700, **_exact_scope(fixture)
            )

        fixture.coordinator._store = store  # noqa: SLF001
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.authorization_not_live",
        ):
            fixture.coordinator.dispatch_next(
                now_ms=fixture.record.request.expires_at_ms + 1,
                **_exact_scope(fixture),
            )

        assert fixture.backend.calls == 0
        assert store.get_effect(fixture.record.request.prebound_effect_id) is None
        assert _nonce_count(store) == fixture.baseline_nonce_count


def test_exact_succeeded_effect_and_fact_survive_receipt_expiry(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "terminal-success") as fixture:
        first = fixture.coordinator.dispatch_record(
            fixture.record, now_ms=1_700
        )
        replay = fixture.coordinator.dispatch_next(
            now_ms=fixture.record.request.expires_at_ms + 1,
            **_exact_scope(fixture),
        )

        assert first.status == "SUCCEEDED"
        assert replay is not None
        assert replay.status == "SUCCEEDED"
        assert replay.effect_id == first.effect_id
        assert replay.fact_ids == first.fact_ids
        assert replay.recovered is True
        assert fixture.backend.calls == 1


@pytest.mark.parametrize("status", ("FAILED_FINAL", "AMBIGUOUS"))
def test_exact_terminal_failure_is_returned_and_never_hidden(
    tmp_path: Path, status: str
) -> None:
    with _runtime_fixture(tmp_path / f"terminal-{status}") as fixture:
        prepared = fixture.coordinator._preflight(  # noqa: SLF001
            fixture.record, now_ms=1_700
        )
        claim = prepared["claim"]
        fixture.p7c.store.claim_effect(claim)
        if status == "AMBIGUOUS":
            fixture.p7c.store.mark_effect_started(
                claim.effect_id, started_at_ms=1_700
            )
        result = EffectResult(
            result_id="effect-result-existing-" + status.casefold(),
            effect_id=claim.effect_id,
            status=status,
            fact_id="fact-existing-" + status.casefold(),
            evidence_sha256=canonical_sha256(
                {"effect_id": claim.effect_id, "status": status}
            ),
            error_code="p7d1.test.existing_terminal",
            observed_at_ms=1_701,
            result_sha256="0" * 64,
        ).with_computed_sha256()
        fixture.p7c.store.complete_effect(result)

        outcome = fixture.coordinator.dispatch_next(
            now_ms=fixture.record.request.expires_at_ms + 1,
            **_exact_scope(fixture),
        )

        assert outcome is not None
        assert outcome.status == status
        assert outcome.effect_id == claim.effect_id
        assert outcome.fact_ids == (result.fact_id,)
        assert outcome.recovered is True
        assert fixture.backend.calls == 0


def test_partial_dispatch_scope_is_rejected(tmp_path: Path) -> None:
    with _runtime_fixture(tmp_path / "partial-scope") as fixture:
        exact = _exact_scope(fixture)
        partial_scopes = (
            {"request_id": exact["request_id"]},
            {"run_id": exact["run_id"]},
            {"generation": exact["generation"]},
            {
                "request_id": exact["request_id"],
                "run_id": exact["run_id"],
            },
            {
                "request_id": exact["request_id"],
                "generation": exact["generation"],
            },
            {
                "run_id": exact["run_id"],
                "generation": exact["generation"],
            },
        )
        for scope in partial_scopes:
            with pytest.raises(
                ValueError, match="composition dispatch scope is incomplete"
            ):
                fixture.coordinator.dispatch_next(now_ms=1_700, **scope)

        assert fixture.backend.calls == 0


def test_current_trust_rejection_is_handler_zero_effect_zero_nonce_zero(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "trust") as fixture:
        fixture.coordinator._trust_bundle_provider = (  # noqa: SLF001
            lambda _now_ms: p7c1._trust_bundle(Ed25519PrivateKey.generate())
        )

        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.grant_invalid",
        ):
            fixture.coordinator.dispatch_record(fixture.record, now_ms=1_700)

        assert fixture.backend.calls == 0
        assert fixture.p7c.store.get_effect(
            fixture.record.request.prebound_effect_id
        ) is None
        assert _nonce_count(fixture.p7c.store) == fixture.baseline_nonce_count


def test_claimed_epoch_one_receipt_fails_closed_after_real_epoch_two_restart(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "gateway-epoch-restart") as fixture:
        prepared = fixture.coordinator._preflight(  # noqa: SLF001
            fixture.record, now_ms=1_700
        )
        claim = prepared["claim"]
        fixture.p7c.store.claim_effect(claim)
        pre_restart_backend = fixture.backend
        epoch_two_trust = fixture.p7c.trust.model_copy(
            update={"gateway_epoch": 2, "bundle_sha256": "0" * 64}
        ).with_computed_sha256()

        _reopen_runtime(
            fixture,
            now_ms=1_800,
            gateway_epoch=2,
            trust_bundle=epoch_two_trust,
        )
        outcome = fixture.coordinator.dispatch_next(
            now_ms=1_800, **_exact_scope(fixture)
        )

        assert outcome is not None
        assert outcome.status == "FAILED_FINAL"
        effect = fixture.p7c.store.get_effect(claim.effect_id)
        assert effect.state == "FAILED_FINAL"
        assert effect.result is not None
        assert (
            effect.result.error_code
            == "composition.execution.current_authority_mismatch"
        )
        assert pre_restart_backend.calls == 0
        assert fixture.backend.calls == 0
        assert _nonce_count(fixture.p7c.store) == fixture.baseline_nonce_count


def test_receipt_plan_registry_and_schema_drift_all_fail_before_claim(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "drift") as fixture:
        coordinator = fixture.coordinator
        original_store = coordinator._store  # noqa: SLF001
        original_registry = coordinator._registry  # noqa: SLF001
        original_schemas = coordinator._schemas  # noqa: SLF001

        invalid_record = replace(
            fixture.record, authorization_record_sha256="f" * 64
        )
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.authorization_invalid",
        ):
            coordinator.dispatch_record(invalid_record, now_ms=1_700)

        class _InactivePlanStore:
            def __getattr__(self, name: str):
                return getattr(original_store, name)

            def get_active_executable_composition_plan(
                self, registration_id: str, *, now_ms: int
            ):
                del registration_id, now_ms
                return None

        coordinator._store = _InactivePlanStore()  # noqa: SLF001
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.plan_inactive",
        ):
            coordinator.dispatch_record(fixture.record, now_ms=1_700)
        coordinator._store = original_store  # noqa: SLF001

        coordinator._registry = p7c1._registry_with_permission(  # noqa: SLF001
            fixture.p7c, handler="drifted.handler"
        )
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.plan_mismatch",
        ):
            coordinator.dispatch_record(fixture.record, now_ms=1_700)
        coordinator._registry = original_registry  # noqa: SLF001

        action_id = fixture.record.request.action_id
        entries = tuple(
            replace(item, argument_schema_sha256="e" * 64)
            if item.action_id == action_id
            else item
            for item in original_schemas.entries
        )
        coordinator._schemas = p7c1._catalog_with_entry(  # noqa: SLF001
            original_schemas,
            action_id,
            argument_schema_sha256="e" * 64,
        )
        assert coordinator._schemas.entries == entries  # noqa: SLF001
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.schema_rejected",
        ):
            coordinator.dispatch_record(fixture.record, now_ms=1_700)

        assert fixture.backend.calls == 0
        assert original_store.get_effect(
            fixture.record.request.prebound_effect_id
        ) is None
        assert _nonce_count(original_store) == fixture.baseline_nonce_count


def test_raw_windows_target_is_rejected_before_claim_even_if_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _runtime_fixture(tmp_path / "target") as fixture:
        materialized = execution_module.materialize_static_root_step(
            fixture.p7c.plan, step_id=fixture.record.request.step_id
        )
        monkeypatch.setattr(
            execution_module,
            "materialize_static_root_step",
            lambda _plan, *, step_id: replace(
                materialized,
                target=r"C:\\Users\\example\\raw-target.txt",
            ),
        )

        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.schema_rejected",
        ):
            fixture.coordinator.dispatch_record(fixture.record, now_ms=1_700)

        assert fixture.backend.calls == 0
        assert fixture.p7c.store.get_effect(
            fixture.record.request.prebound_effect_id
        ) is None
        assert _nonce_count(fixture.p7c.store) == fixture.baseline_nonce_count


def test_object_content_drift_is_checked_from_current_bytes() -> None:
    body = b"sealed-input"
    grant = ObjectGrant(
        object_id="object-p7d1",
        revision=1,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        mime="application/octet-stream",
        tenant_id="tenant-p7d1",
        link_account_id="account-p7d1",
        conversation_scope_hash="a" * 64,
    )
    request = SimpleNamespace(
        object_grants=[grant.model_dump(mode="json")]
    )
    ticket = SimpleNamespace(
        payload=SimpleNamespace(input_objects=(grant,))
    )
    reference = SimpleNamespace(
        object_id=grant.object_id,
        sha256=grant.sha256,
        size_bytes=grant.size_bytes,
        tenant_id=grant.tenant_id,
        link_account_id=grant.link_account_id,
        conversation_scope_hash=grant.conversation_scope_hash,
        has_valid_sha256=lambda: True,
    )
    coordinator = object.__new__(CompositionStepExecutionCoordinator)
    coordinator._objects = SimpleNamespace(  # noqa: SLF001
        get_reference=lambda _object_id: reference,
        read_bytes=lambda _object_id: b"changed-input",
    )

    with pytest.raises(
        CompositionStepExecutionError,
        match="composition.execution.object_changed",
    ):
        coordinator._verify_objects(request, ticket)  # noqa: SLF001


@pytest.mark.parametrize("commit_first", (False, True))
def test_restart_never_replays_started_effect_and_recovers_only_exact_fact(
    tmp_path: Path, commit_first: bool
) -> None:
    with _runtime_fixture(tmp_path / f"restart-{commit_first}") as fixture:
        pre_restart_backend = fixture.backend
        fixture.coordinator._facts = _FactCrashProxy(  # noqa: SLF001
            fixture.facts, commit_first=commit_first
        )
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.fact_commit_unknown",
        ):
            fixture.coordinator.dispatch_record(fixture.record, now_ms=1_700)

        effect_id = fixture.record.request.prebound_effect_id
        assert pre_restart_backend.calls == 1
        assert fixture.p7c.store.get_effect(effect_id).state == (
            "SIDE_EFFECT_STARTED"
        )
        assert (fixture.facts.get_batch_for_effect(effect_id) is not None) is (
            commit_first
        )

        # Reopen every durable authority and construct a fresh coordinator.
        # Its new backend probe makes any accidental post-restart replay
        # independently visible.
        _reopen_runtime(fixture, now_ms=2_000)
        outcomes = fixture.coordinator.recover_started(now_ms=2_000)

        assert len(outcomes) == 1
        assert outcomes[0].status == (
            "SUCCEEDED" if commit_first else "AMBIGUOUS"
        )
        assert outcomes[0].recovered is True
        assert pre_restart_backend.calls == 1
        assert fixture.backend.calls == 0
        assert fixture.p7c.store.get_effect(effect_id).state == outcomes[0].status
        assert fixture.coordinator.dispatch_next(now_ms=2_001) is None
        assert fixture.backend.calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("action_id", "skill.get"),
        ("action_version", "9.9.9"),
        ("attempt", 2),
    ),
)
def test_recovery_rejects_fact_identity_mismatch(
    tmp_path: Path, field: str, value: Any
) -> None:
    with _runtime_fixture(tmp_path / f"restart-mismatch-{field}") as fixture:
        fixture.coordinator._facts = _FactCrashProxy(  # noqa: SLF001
            fixture.facts, commit_first=True
        )
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.fact_commit_unknown",
        ):
            fixture.coordinator.dispatch_record(fixture.record, now_ms=1_700)

        effect_id = fixture.record.request.prebound_effect_id
        batch = fixture.facts.get_batch_for_effect(effect_id)
        assert batch is not None
        mismatched_result = batch.result.model_copy(update={field: value})
        mismatched_batch = replace(batch, result=mismatched_result)
        canonical_facts = fixture.facts
        fixture.coordinator._facts = SimpleNamespace(  # noqa: SLF001
            get_batch_for_effect=lambda *_args, **_kwargs: mismatched_batch,
            get_batch_for_ticket=canonical_facts.get_batch_for_ticket,
            record_execution=canonical_facts.record_execution,
        )

        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.recovery_fact_mismatch",
        ):
            fixture.coordinator.recover_started(now_ms=2_000)

        assert fixture.p7c.store.get_effect(effect_id).state == (
            "SIDE_EFFECT_STARTED"
        )
        assert fixture.backend.calls == 1


def test_fact_missing_recovery_survives_wall_clock_rollback(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "restart-clock-rollback") as fixture:
        pre_restart_backend = fixture.backend
        fixture.coordinator._facts = _FactCrashProxy(  # noqa: SLF001
            fixture.facts, commit_first=False
        )
        with pytest.raises(
            CompositionStepExecutionError,
            match="composition.execution.fact_commit_unknown",
        ):
            fixture.coordinator.dispatch_record(fixture.record, now_ms=1_700)

        effect_id = fixture.record.request.prebound_effect_id
        started = fixture.p7c.store.get_effect(effect_id)
        assert started.state == "SIDE_EFFECT_STARTED"
        assert started.side_effect_started_at_ms == 1_700

        _reopen_runtime(fixture, now_ms=1_600)
        outcomes = fixture.coordinator.recover_started(now_ms=1_600)

        assert len(outcomes) == 1
        assert outcomes[0].status == "AMBIGUOUS"
        assert outcomes[0].recovered is True
        terminal = fixture.p7c.store.get_effect(effect_id)
        assert terminal.state == "AMBIGUOUS"
        assert terminal.result.observed_at_ms == 1_700
        assert pre_restart_backend.calls == 1
        assert fixture.backend.calls == 0


def test_recovery_excludes_composition_pipeline_from_generic_recovery(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "generic-recovery") as fixture:
        fixture.coordinator._facts = _FactCrashProxy(  # noqa: SLF001
            fixture.facts, commit_first=False
        )
        with pytest.raises(CompositionStepExecutionError):
            fixture.coordinator.dispatch_record(fixture.record, now_ms=1_700)
        effect_id = fixture.record.request.prebound_effect_id

        recovered = fixture.p7c.store.recover_started_effects(
            now_ms=1_900,
            exclude_pipeline_versions=(COMPOSITION_STEP_PIPELINE_VERSION,),
        )

        assert recovered == ()
        assert fixture.p7c.store.get_effect(effect_id).state == (
            "SIDE_EFFECT_STARTED"
        )


def test_fence_advance_between_preflight_and_permit_keeps_handler_zero(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "fence") as fixture:
        original_claim = fixture.p7c.store.claim_effect

        def claim_then_fence(claim):
            result = original_claim(claim)
            fixture.p7c.store.increment_action_fence(
                reason="p7d1-test-race", now_ms=1_700
            )
            return result

        fixture.p7c.store.claim_effect = claim_then_fence  # type: ignore[method-assign]
        outcome = fixture.coordinator.dispatch_record(
            fixture.record, now_ms=1_700
        )

        assert outcome.status == "FAILED_FINAL"
        assert fixture.backend.calls == 0
        assert _nonce_count(fixture.p7c.store) == fixture.baseline_nonce_count
        effect = fixture.p7c.store.get_effect(outcome.effect_id)
        assert effect.state == "FAILED_FINAL"
        assert fixture.p7c.store.action_fence_status()["inflight_count"] == 0


def test_permit_rejects_closed_fence_even_when_caller_uses_new_epoch(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "fence-current-epoch") as fixture:
        prepared = fixture.coordinator._preflight(  # noqa: SLF001
            fixture.record, now_ms=1_700
        )
        claim = prepared["claim"]
        fixture.p7c.store.claim_effect(claim)
        closed_epoch = fixture.p7c.store.increment_action_fence(
            reason="p7d1-test-closed", now_ms=1_700
        )

        with pytest.raises(StoreConflictError, match="action fence is closed"):
            fixture.p7c.store.acquire_dispatch_permit(
                effect_id=claim.effect_id,
                attempt=claim.attempt,
                expected_fence_epoch=closed_epoch,
                nonce_sha256="a" * 64,
                now_ms=1_700,
            )

        assert fixture.p7c.store.get_effect(claim.effect_id).state == "CLAIMED"
        assert fixture.p7c.store.action_fence_status()["inflight_count"] == 0
        assert fixture.backend.calls == 0
        assert _nonce_count(fixture.p7c.store) == fixture.baseline_nonce_count


def test_legacy_started_boundary_rejects_claim_created_after_fence(
    tmp_path: Path,
) -> None:
    with _runtime_fixture(tmp_path / "legacy-fence-current-epoch") as fixture:
        prepared = fixture.coordinator._preflight(  # noqa: SLF001
            fixture.record, now_ms=1_700
        )
        claim = prepared["claim"]
        fixture.p7c.store.increment_action_fence(
            reason="p7d1-test-before-claim", now_ms=1_700
        )
        fixture.p7c.store.claim_effect(claim)

        with pytest.raises(StoreConflictError, match="action fence is closed"):
            fixture.p7c.store.mark_effect_started(
                claim.effect_id, started_at_ms=1_700
            )

        assert fixture.p7c.store.get_effect(claim.effect_id).state == "CLAIMED"
        assert fixture.p7c.store.action_fence_status()["inflight_count"] == 0
        assert fixture.backend.calls == 0
        assert _nonce_count(fixture.p7c.store) == fixture.baseline_nonce_count


@pytest.mark.parametrize("transition", ("cancel", "supersede"))
def test_generation_change_between_preflight_and_permit_keeps_handler_zero(
    tmp_path: Path, transition: str
) -> None:
    with _runtime_fixture(tmp_path / f"generation-{transition}") as fixture:
        store = fixture.p7c.store
        original_claim = store.claim_effect

        def claim_then_change_generation(claim):
            result = original_claim(claim)
            current = store.get_generation(claim.request_id)
            assert current is not None
            if transition == "cancel":
                store.cancel_generation(
                    claim.request_id,
                    reason_code="p7d1.test.cancelled",
                    cancelled_at_ms=1_700,
                )
            else:
                next_sequence = current.run_sequence + 1
                store.acquire_generation_lease(
                    request_id=claim.request_id,
                    run_id=derive_run_identity(
                        claim.request_id, next_sequence
                    ).run_id,
                    run_sequence=next_sequence,
                    generation=current.generation + 1,
                    gateway_epoch=current.gateway_epoch,
                    lease_id="lease_p7d1_successor",
                    owner_instance_id=current.owner_instance_id,
                    issued_at_ms=1_700,
                    lease_duration_ms=10_000,
                )
            return result

        store.claim_effect = claim_then_change_generation  # type: ignore[method-assign]
        outcome = fixture.coordinator.dispatch_record(
            fixture.record, now_ms=1_700
        )

        assert outcome.status == "FAILED_FINAL"
        assert fixture.backend.calls == 0
        assert _nonce_count(store) == fixture.baseline_nonce_count
        assert store.get_effect(outcome.effect_id).state == "FAILED_FINAL"
        assert store.action_fence_status()["inflight_count"] == 0
