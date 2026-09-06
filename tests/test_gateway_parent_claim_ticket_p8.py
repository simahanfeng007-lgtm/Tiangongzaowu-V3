"""Exercise the real worker's ticket producer against its persisted Claim.

No model is called: observation stops at Omni registration, before dispatch.
The fixture does not construct a ticket or replace Policy/Store authorities.
"""

from pathlib import Path
import time

import pytest

from runtime_security import verify_execution_ticket
from tests.test_gateway_worker_composition_resume_p7d2 import _envelope
from total_gateway.bootstrap import GatewayConfig
from total_gateway.runtime import GatewayRuntime


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("existing_claim", [False, True])
def test_parent_ticket_binds_the_persisted_effect_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_claim: bool
) -> None:
    for name, relative in {
        "APPDATA": "appdata",
        "TIANGONG_DOCUMENTS_PATH": "documents",
        "TIANGONG_LIFE_DATA_ROOT": "life-data",
        "TIANGONG_LIFE_RUNTIME_ROOT": "life-runtime",
    }.items():
        monkeypatch.setenv(name, str(tmp_path / relative))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = GatewayConfig(
        environment="test", deployment_mode="embedded", port=0,
        state_root=tmp_path / "gateway", min_free_bytes=1_048_576,
        backend_internal_token="parent-claim-test-" + "x" * 40,
        release_source_root=ROOT, workspace_root=workspace,
        skill_root=ROOT / "src/omni_body_skill",
    )
    runtime = GatewayRuntime.start(config)
    observed = []

    class BeforeDispatch(RuntimeError):
        pass

    try:
        worker = runtime.orchestration
        original_claim = runtime.store.claim_effect

        def claim_once(claim):
            if existing_claim:
                # The existing immutable Claim wins even when its original
                # timestamp differs from the retried proposal's timestamp.
                prior = claim.model_copy(update={
                    "claimed_at_ms": claim.claimed_at_ms - 1,
                }).with_computed_sha256()
                original_claim(prior)
            return original_claim(claim)

        monkeypatch.setattr(runtime.store, "claim_effect", claim_once)

        def observe_ticket(ticket, **kwargs):
            record = runtime.store.get_effect(ticket.payload.effect_id)
            verify_execution_ticket(
                ticket,
                worker._authority.execution_trust_bundle(
                    gateway_epoch=runtime.lease.gateway_epoch,
                    now_ms=kwargs["registered_at_ms"],
                ),
                now_ms=kwargs["registered_at_ms"],
            )
            observed.append((ticket, record))
            raise BeforeDispatch("observed signed parent before dispatch")

        monkeypatch.setattr(worker.omni_grant_authority, "register", observe_ticket)
        now = time.time_ns() // 1_000_000
        with runtime.store._lock:
            registered = runtime.store.register_request(
                _envelope("parent-claim"), ingress_sha256="a" * 64,
                created_at_ms=now,
            )
            activation = runtime.active_requests.claim(
                registered.entry.request_id, registered.entry.session_scope_hash,
                now_ms=now,
            )
        with pytest.raises(BeforeDispatch):
            worker.process(activation)
        assert len(observed) == 1
        ticket, record = observed[0]
        assert record.state == "CLAIMED"
        assert record.claim.has_valid_sha256()
        assert ticket.payload.claim_sha256 != "0" * 64
        assert ticket.payload.claim_sha256 == record.claim.claim_sha256
        assert ticket.payload.claim_revision == record.claim.claim_revision
        assert ticket.payload.claim_lease_epoch == record.claim.lease_epoch
        assert ticket.payload.effect_id == record.claim.effect_id
        assert ticket.payload.request_id == record.claim.request_id
        assert ticket.payload.run_id == record.claim.run_id
        assert ticket.payload.generation == record.claim.generation
    finally:
        runtime.close()
