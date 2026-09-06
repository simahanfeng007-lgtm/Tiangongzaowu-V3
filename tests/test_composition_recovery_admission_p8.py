"""A normal generation heartbeat must not exhaust sealed composition recovery."""
from __future__ import annotations

import pytest

from total_gateway.active_requests import ActiveRequestActivator
from total_gateway.orchestration import OrchestrationError
from test_gateway_worker_composition_resume_p7d2 import (
    _StoreHarness, _claim_parent, _close, _continuation, _record_parent_fact, _runtime,
)


def _heartbeat_and_recover(runtime):
    owner = ActiveRequestActivator(runtime.store, gateway_epoch=7,
        owner_instance_id="gateway-resume-test", lease_duration_ms=100_000)
    for stamp in (1_400, 1_500, 1_600, 1_700):
        owner.heartbeat(runtime.activation, now_ms=stamp)
    recovered = ActiveRequestActivator(runtime.store, gateway_epoch=8,
        owner_instance_id="gateway-after-crash", lease_duration_ms=100_000).recover_next(now_ms=102_000)
    assert recovered is not None
    assert recovered.generation.generation == runtime.activation.generation.generation
    assert recovered.generation.revision > 3
    return recovered


@pytest.mark.parametrize("sealed,enabled,expected", [(True, True, True), (False, True, False), (True, False, False)])
def test_recovered_parent_routes_to_existing_validator(tmp_path, monkeypatch, sealed, enabled, expected):
    runtime = _runtime(tmp_path, message_ref="heartbeat-recovery")
    try:
        claim = _claim_parent(runtime)
        response, batch = _record_parent_fact(runtime, claim)
        continuation = _continuation(runtime, claim, response.ticket.payload.ticket_id) if sealed else None
        runtime.worker._store = _StoreHarness(runtime.store, runtime.plan_record, continuation)
        recovered = _heartbeat_and_recover(runtime)
        monkeypatch.setenv("TIANGONG_REQUEST_REEXECUTION", "1" if enabled else "0")
        assert runtime.worker._has_sealed_composition_tail(recovered) is False
        assert runtime.worker._should_reexecute_without_outbox(recovered) is expected
        # Routing is read-only; it neither replays nor promotes the parent.
        assert runtime.store.get_effect(claim.effect_id).state == "CLAIMED"
        assert runtime.facts.get_batch_for_effect(claim.effect_id, verify_payload=True).batch_sha256 == batch.batch_sha256
    finally:
        _close(runtime)


def test_admission_does_not_replace_continuation_authority_validation(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, message_ref="invalid-continuation-recovery")
    try:
        claim = _claim_parent(runtime)
        response, batch = _record_parent_fact(runtime, claim)
        continuation = _continuation(runtime, claim, response.ticket.payload.ticket_id)
        continuation.principal_scope_hash = "0" * 64
        runtime.worker._store = _StoreHarness(runtime.store, runtime.plan_record, continuation)
        recovered = _heartbeat_and_recover(runtime)
        monkeypatch.setenv("TIANGONG_REQUEST_REEXECUTION", "1")
        assert runtime.worker._should_reexecute_without_outbox(recovered) is True
        with pytest.raises(OrchestrationError, match="resume_authority_mismatch"):
            runtime.worker._durable_composition_parent_resume(recovered, runtime.plan_record, now_ms=102_001)
        assert runtime.store.get_effect(claim.effect_id).state == "CLAIMED"
        assert runtime.facts.get_batch_for_effect(claim.effect_id, verify_payload=True).batch_sha256 == batch.batch_sha256
    finally:
        _close(runtime)


def test_noncomposition_request_keeps_existing_reexecution_cap(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, message_ref="ordinary-recovery")
    try:
        monkeypatch.setenv("TIANGONG_REQUEST_REEXECUTION", "1")
        assert runtime.worker._should_reexecute_without_outbox(runtime.activation) is True
        recovered = _heartbeat_and_recover(runtime)
        assert runtime.worker._should_reexecute_without_outbox(recovered) is False
    finally:
        _close(runtime)
