"""Slow planning must not consume or extend an expired Life authorization."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from life_service.context_api import LifeContextCompileAuthorizeApi, LifeProjectionInputs
from life_service.store import LifeShadowStore
from total_gateway.frozen_backend_compat import (
    FrozenBackendCompatibilityError, FrozenBackendCompatibilityTransport,
)
from total_gateway.life_client import LifeClient, LifeClientError, LifeProfileBindings
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError


@pytest.fixture
def case(tmp_path, monkeypatch):
    clock = SimpleNamespace(ms=1000)
    monkeypatch.setattr("total_gateway.orchestration.time.time_ns", lambda: clock.ms * 1_000_000)
    shadow = LifeShadowStore.open(tmp_path / "life.shadow.sqlite3", create=True, now_ms=100)
    objects = ContentAddressedObjectStore.open(tmp_path / "objects", now_ms=100)
    api = LifeContextCompileAuthorizeApi(shadow)
    authority = SimpleNamespace(life_id="life_refresh", epoch=2, identity_revision=2, requests=[])

    def get_json(path):
        assert path == "/api/v1/v3/state"
        revisions = shadow.build_revision_vector(
            authority.life_id, writer_epoch=authority.epoch,
            identity_revision=authority.identity_revision, soul_revision=1,
        )
        return {"ok": True, "api_contract": "tiangong.life.api.v2", "life_ready": True,
                "identity": {"life_id": authority.life_id, "writer_epoch": authority.epoch,
                             "identity_revision": authority.identity_revision},
                "projection_authority": {"revisions": revisions.model_dump(mode="json"),
                                         "vector_sha256": revisions.vector_sha256}}

    def post_json(path, payload):
        assert path == "/api/v1/v3/life/context/compile-and-authorize"
        authority.requests.append(dict(payload))
        return api.compile_and_authorize(payload, LifeProjectionInputs(
            life_id=authority.life_id, writer_epoch=authority.epoch,
            identity_revision=authority.identity_revision,
            soul={"life_id": authority.life_id, "revision": 1, "name": "起源", "prompt": "fixture soul"},
            capabilities={},
        ))

    activation = SimpleNamespace(
        entry=SimpleNamespace(request_id="req_" + "1" * 64),
        generation=SimpleNamespace(run_id="run_" + "2" * 64, generation=1),
        envelope=SimpleNamespace(text="读取文件", tenant_id="desktop", link_account_id="local-user",
                                 conversation_scope_hash="3" * 64, principal_scope_hash="4" * 64),
    )
    plan = SimpleNamespace(
        has_valid_identity=lambda: True, request_id=activation.entry.request_id,
        run_id=activation.generation.run_id, generation=1, principal_scope_hash="4" * 64,
        registration_id="registration_refresh", executable_plan_id="ecp_" + "5" * 64,
        executable_plan_sha256="5" * 64,
    )
    record = SimpleNamespace(executable_plan=plan)
    observed = []

    def read_active(registration_id, *, now_ms):
        observed.append((registration_id, now_ms))
        return record

    worker = object.__new__(GatewayOrchestrationWorker)
    worker._objects = objects
    worker._life_transport_for_execution = lambda: SimpleNamespace(post_json=post_json, get_json=get_json)
    worker._store = SimpleNamespace(get_active_executable_composition_plan=read_active)
    profile = LifeProfileBindings(user_callsign="用户")
    initial = LifeClient(worker._life_transport_for_execution(), objects).acquire_planning_identity()
    clock.ms += 132_000
    try:
        yield SimpleNamespace(worker=worker, activation=activation, record=record, initial=initial,
                              profile=profile, clock=clock, authority=authority, observed=observed,
                              objects=objects, shadow=shadow)
    finally:
        shadow.close()
        objects.close()


def _refresh(c):
    return c.worker._authorize_life_after_planning(
        c.activation, c.record, c.initial, c.profile, current_context_tokens=20,
    )

def _prepare(transport, c, life):
    snapshot = life.snapshot
    ticket = SimpleNamespace(payload=SimpleNamespace(
        life_snapshot_hash=snapshot.sha256, life_snapshot_revision=snapshot.revision,
        request_id=c.activation.entry.request_id, run_id=c.activation.generation.run_id,
        generation=1, channel="desktop",
    ))
    return transport._prepare_life(ticket, {"life_snapshot": snapshot.model_dump(mode="json"),
                                           "text": c.activation.envelope.text, "recent_messages": []})


def test_slow_planning_only_observes_identity_then_issues_one_real_atomic_authorization(case):
    c = case
    transport = object.__new__(FrozenBackendCompatibilityTransport)
    transport._objects = c.objects
    transport._on_context_compaction = None
    assert c.authority.requests == []
    assert c.shadow.get_context_authorization(c.activation.entry.request_id,
        run_id=c.activation.generation.run_id, generation=1) is None
    refreshed = _refresh(c)
    assert refreshed.snapshot.identity_ref == c.initial.identity_ref
    assert len(c.authority.requests) == 1
    assert c.authority.requests[0]["issued_at_ms"] == 133000
    assert c.authority.requests[0]["request_id"] == c.activation.entry.request_id
    assert c.authority.requests[0]["run_id"] == c.activation.generation.run_id
    assert c.authority.requests[0]["generation"] == 1
    assert c.authority.requests[0]["current_request"] == c.activation.envelope.text
    assert c.observed == [(c.record.executable_plan.registration_id, c.clock.ms)]
    assert _prepare(transport, c, refreshed)["binding_status"] == "authorized"
    # Delayed issuance retains the normal lifetime; it never disables expiry.
    c.clock.ms += 60_000
    with pytest.raises(FrozenBackendCompatibilityError, match="context_authorization_binding_mismatch"):
        _prepare(transport, c, refreshed)


@pytest.mark.parametrize("field,value", [("life_id", "life_other"), ("epoch", 3), ("identity_revision", 3)])
def test_refresh_rejects_identity_or_writer_epoch_switch(case, field, value):
    setattr(case.authority, field, value)
    with pytest.raises(OrchestrationError, match="life_refresh_identity_mismatch"):
        _refresh(case)
    assert case.observed == []


@pytest.mark.parametrize("field,value", [
    ("request_id", "req_" + "9" * 64), ("run_id", "run_" + "9" * 64),
    ("generation", 2), ("principal_scope_hash", "9" * 64),
])
def test_refresh_rejects_registered_plan_scope_substitution(case, field, value):
    setattr(case.record.executable_plan, field, value)
    with pytest.raises(OrchestrationError, match="life_refresh_plan_scope_invalid"):
        _refresh(case)
    assert case.authority.requests == []


@pytest.mark.parametrize("changed", [False, True])
def test_refresh_revalidates_plan_still_active_and_unchanged(case, changed):
    replacement = None if not changed else SimpleNamespace(executable_plan=SimpleNamespace(
        executable_plan_id="different-plan", executable_plan_sha256="9" * 64,
    ))
    case.worker._store.get_active_executable_composition_plan = lambda *_args, **_kwargs: replacement
    with pytest.raises(OrchestrationError, match="life_refresh_plan_inactive"):
        _refresh(case)


def test_refresh_precedes_signed_parent_arguments_effect_and_ticket():
    source = Path(__file__).resolve().parents[1] / "src/total_gateway/orchestration.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GatewayOrchestrationWorker")
    process = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "_process")
    def line(name):
        return min(n.lineno for n in ast.walk(process) if isinstance(n, ast.Call) and ast.unparse(n.func) == name)
    refresh = line("self._authorize_life_after_planning")
    assert line("self._prepare_composition_plan") < refresh < line("self._store.claim_effect")
    assert refresh < line("self._authority.execution_signer.sign_execution")
    assert refresh < line("self._omni_grants.seal_composition_continuation")
    args = next(n for n in ast.walk(process) if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name) and n.target.id == "arguments")
    assert refresh < args.lineno


def test_planner_bypass_still_issues_first_atomic_authorization(case):
    life = case.worker._authorize_life_after_planning(
        case.activation, None, case.initial, case.profile, current_context_tokens=20,
    )
    assert life.snapshot.context_authorization_id
    assert len(case.authority.requests) == 1
    assert case.observed == []


@pytest.mark.parametrize("tamper", ["hash", "identity", "epoch", "not_ready"])
def test_planning_observation_rejects_untrusted_or_inconsistent_projection(case, tamper):
    transport = case.worker._life_transport_for_execution()
    state = transport.get_json("/api/v1/v3/state")
    if tamper == "hash":
        state["projection_authority"]["vector_sha256"] = "9" * 64
    elif tamper == "identity":
        state["identity"]["life_id"] = "life_other"
    elif tamper == "epoch":
        state["identity"]["writer_epoch"] += 1
    else:
        state["life_ready"] = False
    client = LifeClient(SimpleNamespace(get_json=lambda _: state), case.objects)
    with pytest.raises(LifeClientError, match="planning_identity_binding_mismatch"):
        client.acquire_planning_identity()
    assert case.authority.requests == []


def test_actual_embedded_life_read_identity_then_first_authorization(tmp_path, monkeypatch):
    """Use the exact service endpoint shape consumed by the installed desktop."""
    import time
    from life_service.embedded_runtime import EmbeddedLifeRuntime
    from total_gateway.life_client import InProcessLifeJsonTransport

    service = EmbeddedLifeRuntime(data_root=tmp_path / "life-data",
                                  runtime_root=tmp_path / "life-runtime", mode="embedded")
    objects = ContentAddressedObjectStore.open(tmp_path / "gateway-objects", now_ms=100)
    try:
        client = LifeClient(InProcessLifeJsonTransport(service), objects)
        identity = client.acquire_planning_identity()
        service.scheduler.stop(timeout_seconds=2)
        request_id, run_id = "req_" + "a" * 64, "run_" + "b" * 64
        assert service.authority_store.get_context_authorization(
            request_id, run_id=run_id, generation=1) is None
        now_ms = time.time_ns() // 1_000_000 + 132_000
        monkeypatch.setattr("total_gateway.orchestration.time.time_ns", lambda: now_ms * 1_000_000)
        snapshot = client.compile_and_authorize_snapshot(
            request_id=request_id, run_id=run_id, generation=1, current_request="读取文件",
            tenant_id="desktop", link_account_id="local-user", conversation_scope_hash="c" * 64,
            profile=LifeProfileBindings(user_callsign="用户"), observed_at_ms=now_ms,
        )
        assert snapshot.snapshot.identity_ref == identity.identity_ref
        assert snapshot.snapshot.identity_revision == identity.identity_revision
        assert snapshot.writer_epoch == identity.writer_epoch
        stored = service.authority_store.get_context_authorization(request_id, run_id=run_id, generation=1)
        assert stored.authorization_id == snapshot.context_authorization_id
        assert stored.issued_at_ms == now_ms and stored.expires_at_ms == now_ms + 60_000
        transport = object.__new__(FrozenBackendCompatibilityTransport)
        transport._objects, transport._on_context_compaction = objects, None
        context = SimpleNamespace(activation=SimpleNamespace(
            entry=SimpleNamespace(request_id=request_id), generation=SimpleNamespace(run_id=run_id),
            envelope=SimpleNamespace(text="读取文件")))
        assert _prepare(transport, context, snapshot)["binding_status"] == "authorized"
    finally:
        objects.close()
        service.close()
