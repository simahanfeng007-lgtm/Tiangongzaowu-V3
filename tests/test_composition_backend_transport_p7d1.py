"""P7D.1 composition execution boundary and embedded transport tests.

These tests deliberately stop at the one-step execution boundary.  They do
not assume a composition scheduler/coordinator API: the already-persisted P7C
authorization artifacts are restored, rebound to a normal CapabilityManifest,
and presented directly to BackendClient and the private embedded route.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from typing import Any, Callable, Mapping
from unittest import mock

import pytest

import test_composition_grant_authority_p7c1 as p7c1
from contracts import (
    CapabilityAction,
    CapabilityManifest,
    CompositionExecutionBindingV1,
    ExecutionAuthorizationError,
    ExecutionTicket,
    OmniCapabilityGrant,
    canonical_json_bytes,
    canonical_sha256,
)
from tests.test_backend_client import backend_response
from total_gateway.action_registry import ActionSchemaCatalog
from total_gateway.backend_client import BackendClient, BackendClientError
from total_gateway.composition_backend_transport import (
    COMPOSITION_BACKEND_PATH,
    COMPOSITION_BACKEND_REQUEST_SCHEMA,
    COMPOSITION_RESULT_PAYLOAD_SCHEMA,
    CompositionBackendExecutionTransport,
)
from total_gateway.embedded_backend import EmbeddedBackendRuntime
from total_gateway.service_ports import CompatibilityJsonClient
from total_gateway.store import GatewayStateStore


ZERO_SHA256 = "0" * 64


@dataclass(frozen=True)
class AuthorizedComposition:
    invocation: dict[str, Any]
    ticket: ExecutionTicket
    manifest: CapabilityManifest
    trust: Any
    grant: OmniCapabilityGrant
    intent: Any
    impact: Any
    decision: Any
    binding: CompositionExecutionBindingV1
    runtime: dict[str, Any]
    signer: Any
    schema_catalog: ActionSchemaCatalog
    expected_result_schema_sha256: str


def _canonical_legacy_json(value: Any) -> bytes:
    """The strict JSON bytes used to content-address a raw Omni response."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _valid_skill_get_result(
    material: AuthorizedComposition,
    *,
    elapsed_seconds: float = 0.125,
) -> dict[str, Any]:
    activation: dict[str, Any] = {}
    return {
        "schema": "tiangong.v3.omni_body.v1",
        "ok": True,
        "zhuangtai": "wancheng",
        "gongju": "omni_body",
        "action": material.ticket.payload.action_id,
        "target": material.invocation["target"],
        "result": {
            "success": True,
            "op_id": "skill-get-p7d2",
            "action": material.ticket.payload.action_id,
            "risk_level": "A0",
            "elapsed_seconds": elapsed_seconds,
            "result": {
                "markdown": "# word_delivery",
                "selection": {},
                "activation": activation,
            },
            "activation": activation,
            "evidence": {},
        },
        "llm_brief": "skill loaded",
        "evidence": {},
    }


@pytest.fixture(scope="module")
def authorized_composition(tmp_path_factory: pytest.TempPathFactory) -> AuthorizedComposition:
    root = tmp_path_factory.mktemp("p7d1-authority")
    # Use a real, explicitly-schema'd A0 composition action with a non-empty
    # target.  This makes target and target-snapshot enforcement substantive;
    # the private route must not special-case only targetless actions.
    with p7c1._harness(
        root,
        action_id="skill.get",
        target="word_delivery",
        arguments={},
    ) as harness:
        response = p7c1._authorize(harness)
        persisted = harness.store.get_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
            now_ms=1_700,
        )
        assert persisted is not None
        intent, impact, decision, ticket, grant = (
            persisted.artifacts.restore_contracts()
        )
        original_binding = ticket.payload.composition_execution_binding
        assert original_binding is not None
        result_schema = harness.loaded.schema_catalog.resolve(
            ticket.payload.action_id,
            ticket.payload.action_version,
            require_result_explicit=True,
        )

        # The non-empty target is probed by the real P7C authority, so all four
        # runtime hash inputs include a genuine non-null optimistic snapshot.
        binding = original_binding
        assert binding.target_snapshot_sha256 is not None

        action = CapabilityAction(
            action_id=ticket.payload.action_id,
            version=ticket.payload.action_version,
            provider_component_id="tiangong-backend",
            argument_schema_sha256=ticket.payload.argument_schema_sha256,
            result_schema_sha256=result_schema.result_schema_sha256,
            risk_class=ticket.payload.risk_class,
            allowed_side_effects=ticket.payload.allowed_side_effects,
            idempotency_mode="pure",
            max_runtime_ms=ticket.payload.max_runtime_ms,
            max_output_bytes=ticket.payload.max_output_bytes,
            max_tool_calls=ticket.payload.max_tool_calls,
            available=True,
        )
        manifest = CapabilityManifest(
            manifest_id="composition_execution_manifest_p7d1",
            revision=1,
            generated_at_ms=1_700,
            component_manifest_hash=ticket.payload.component_manifest_hash,
            actions=(action,),
            sha256=ZERO_SHA256,
        ).with_computed_sha256()
        decision = decision.model_copy(
            update={
                "capability_manifest_hash": manifest.sha256,
                "decision_sha256": ZERO_SHA256,
            }
        ).with_computed_sha256()
        ticket_payload = ticket.payload.model_copy(
            update={
                "capability_manifest_hash": manifest.sha256,
                "decision_sha256": decision.decision_sha256,
            }
        )
        ticket = harness.signer.sign_execution(ticket_payload)
        grant_payload = grant.payload.model_copy(
            update={
                "ticket_sha256": canonical_sha256(
                    ticket.payload.model_dump(mode="json")
                ),
                "decision_sha256": decision.decision_sha256,
                "capability_manifest_hash": manifest.sha256,
            }
        )
        grant = harness.signer.sign_omni_capability(grant_payload)
        invocation = {
            "action": ticket.payload.action_id,
            "target": "word_delivery",
            "args": {},
        }
        assert canonical_sha256(invocation) == ticket.payload.arguments_hash

        # P7C persists the fact-kernel-enabled authorization response.  P7D
        # must dispatch a detached copy with the duplicate fact writer off.
        assert response["runtime"]["fact_kernel_enabled"] is True
        runtime = deepcopy(response["runtime"])
        runtime.update(
            {
                "composition_binding_sha256": binding.binding_sha256,
                "composition_execution_binding": binding.model_dump(mode="json"),
                "decision_sha256": decision.decision_sha256,
                "impact_sha256": impact.impact_sha256,
                "capability_manifest_hash": manifest.sha256,
                "trust_bundle": harness.trust.model_dump(mode="json"),
                "trust_bundle_sha256": harness.trust.bundle_sha256,
                "fact_kernel_enabled": False,
            }
        )
        material = AuthorizedComposition(
            invocation=invocation,
            ticket=ticket,
            manifest=manifest,
            trust=harness.trust,
            grant=grant,
            intent=intent,
            impact=impact,
            decision=decision,
            binding=binding,
            runtime=runtime,
            signer=harness.signer,
            schema_catalog=harness.loaded.schema_catalog,
            expected_result_schema_sha256=result_schema.result_schema_sha256,
        )
    return material


def _composition_transport(
    material: AuthorizedComposition,
    client: Any,
    *,
    signed_grant: Mapping[str, Any] | None = None,
    runtime_meta: Mapping[str, Any] | None = None,
    expected_result_schema_sha256: str | None = None,
) -> CompositionBackendExecutionTransport:
    return CompositionBackendExecutionTransport(
        client,
        signed_grant=(
            material.grant.model_dump(mode="json")
            if signed_grant is None
            else signed_grant
        ),
        runtime_meta=material.runtime if runtime_meta is None else runtime_meta,
        schema_catalog=material.schema_catalog,
        expected_result_schema_sha256=(
            material.expected_result_schema_sha256
            if expected_result_schema_sha256 is None
            else expected_result_schema_sha256
        ),
    )


class RecordingTransport:
    def __init__(
        self,
        response: dict[str, object] | None = None,
        *,
        error: Exception | None = None,
        on_execute: Callable[[], None] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.on_execute = on_execute
        self.calls: list[tuple[bytes, float]] = []

    def execute(self, body: bytes, *, timeout_seconds: float) -> dict[str, object]:
        self.calls.append((body, timeout_seconds))
        if self.on_execute is not None:
            self.on_execute()
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class FakeCompatibilityClient:
    def __init__(
        self,
        *,
        status: int,
        payload: dict[str, Any],
        digest: str | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.digest = digest or hashlib.sha256(
            _canonical_legacy_json(payload)
        ).hexdigest()
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        **kwargs: Any,
    ) -> tuple[int, dict[str, Any], str]:
        assert payload is not None
        self.calls.append((method, path, deepcopy(payload), dict(kwargs)))
        return self.status, deepcopy(self.payload), self.digest


def _wire(material: AuthorizedComposition, *, ticket: ExecutionTicket | None = None, invocation=None) -> bytes:
    selected_ticket = ticket or material.ticket
    return canonical_json_bytes(
        {
            "schema": "tiangong.backend.execute-ticket.v1",
            "ticket": selected_ticket.model_dump(mode="json"),
            "arguments": material.invocation if invocation is None else invocation,
        }
    )


def _private_request(material: AuthorizedComposition) -> dict[str, Any]:
    return {
        "schema": COMPOSITION_BACKEND_REQUEST_SCHEMA,
        "execute_ticket": json.loads(_wire(material)),
        "capability_grant": material.grant.model_dump(mode="json"),
        "runtime": deepcopy(material.runtime),
    }


def _nonce_count(store: GatewayStateStore) -> int:
    return int(
        store._connection.execute(
            "SELECT count(*) FROM security_nonce_ledger"
        ).fetchone()[0]
    )


def _client_execute(
    client: BackendClient,
    material: AuthorizedComposition,
    invocation: dict[str, Any],
    *,
    ticket: ExecutionTicket | None = None,
    grant: OmniCapabilityGrant | None = None,
    expected_binding: CompositionExecutionBindingV1 | None = None,
    actual_target_snapshot_sha256: str | None = None,
    before_dispatch: Callable[[int], None] | None = None,
    transport_runner: Any = None,
):
    return client.execute(
        ticket or material.ticket,
        invocation,
        capability_manifest=material.manifest,
        trust_bundle=material.trust,
        now_ms=1_800,
        expected_gateway_epoch=1,
        minimum_generation=1,
        grant=grant or material.grant,
        intent=material.intent,
        decision=material.decision,
        impact=material.impact,
        expected_composition_binding=expected_binding or material.binding,
        actual_target_snapshot_sha256=(
            material.binding.target_snapshot_sha256
            if actual_target_snapshot_sha256 is None
            else actual_target_snapshot_sha256
        ),
        before_dispatch=before_dispatch,
        transport_runner=transport_runner,
    )


def _ticket_and_grant_for_invocation(
    material: AuthorizedComposition,
    invocation: dict[str, Any],
) -> tuple[ExecutionTicket, OmniCapabilityGrant]:
    invocation_sha256 = canonical_sha256(invocation)
    payload = material.ticket.payload.model_copy(
        update={"arguments_hash": invocation_sha256}
    )
    ticket = material.signer.sign_execution(payload)
    grant_payload = material.grant.payload.model_copy(
        update={
            "arguments_sha256": invocation_sha256,
            "ticket_sha256": canonical_sha256(payload.model_dump(mode="json")),
        }
    )
    return ticket, material.signer.sign_omni_capability(grant_payload)


def _ticket_and_grant_for_output_limit(
    material: AuthorizedComposition,
    max_output_bytes: int,
) -> tuple[ExecutionTicket, OmniCapabilityGrant]:
    resource_envelope_sha256 = canonical_sha256(
        {
            "max_output_bytes": max_output_bytes,
            "max_runtime_ms": material.ticket.payload.max_runtime_ms,
            "max_tool_calls": material.ticket.payload.max_tool_calls,
        }
    )
    payload = material.ticket.payload.model_copy(
        update={
            "max_output_bytes": max_output_bytes,
            "resource_envelope_sha256": resource_envelope_sha256,
        }
    )
    ticket = material.signer.sign_execution(payload)
    grant_payload = material.grant.payload.model_copy(
        update={
            "ticket_sha256": canonical_sha256(payload.model_dump(mode="json")),
        }
    )
    return ticket, material.signer.sign_omni_capability(grant_payload)


def _alternate_signed_authority(
    material: AuthorizedComposition,
) -> tuple[ExecutionTicket, OmniCapabilityGrant, dict[str, Any]]:
    """Build a valid second ticket/grant/runtime binding for swap attacks."""

    effect_id = "eff_" + "7" * 64
    binding = material.binding.model_copy(
        update={
            "effect_id": effect_id,
            "binding_sha256": ZERO_SHA256,
        }
    ).with_computed_sha256()
    ticket_payload = material.ticket.payload.model_copy(
        update={
            "ticket_id": "execution-ticket-p7d1-alternate",
            "nonce": "execution-nonce-p7d1-alternate",
            "effect_id": effect_id,
            "composition_execution_binding": binding,
        }
    )
    ticket = material.signer.sign_execution(ticket_payload)
    grant_payload = material.grant.payload.model_copy(
        update={
            "grant_id": "omni-grant-p7d1-alternate",
            "ticket_id": ticket.payload.ticket_id,
            "ticket_sha256": canonical_sha256(
                ticket.payload.model_dump(mode="json")
            ),
            "effect_id": effect_id,
            "nonce": "omni-nonce-p7d1-alternate",
            "composition_execution_binding": binding,
        }
    )
    grant = material.signer.sign_omni_capability(grant_payload)
    runtime = deepcopy(material.runtime)
    runtime.update(
        {
            "execution_ticket_id": ticket.payload.ticket_id,
            "effect_id": effect_id,
            "composition_binding_sha256": binding.binding_sha256,
            "composition_execution_binding": binding.model_dump(mode="json"),
        }
    )
    return ticket, grant, runtime


def _continuation_signed_authority(
    material: AuthorizedComposition,
    *,
    attempt: int,
) -> tuple[ExecutionTicket, OmniCapabilityGrant, dict[str, Any]]:
    binding_updates: dict[str, Any] = {
        "attempt": attempt,
        "continuation_delegation_id": "ccd_" + "a" * 64,
        "continuation_delegation_sha256": "b" * 64,
        "dependency_evidence_sha256": "c" * 64,
        "binding_sha256": ZERO_SHA256,
    }
    if attempt > 1:
        binding_updates.update(
            {
                "supersedes_authorization_id": "authorization-p7d2-predecessor",
                "supersedes_effect_id": "eff_" + "d" * 64,
                "supersedes_claim_sha256": "e" * 64,
            }
        )
    binding = material.binding.model_copy(
        update=binding_updates
    ).with_computed_sha256()
    ticket_payload = material.ticket.payload.model_copy(
        update={"composition_execution_binding": binding}
    )
    ticket = material.signer.sign_execution(ticket_payload)
    grant_payload = material.grant.payload.model_copy(
        update={
            "ticket_sha256": canonical_sha256(
                ticket.payload.model_dump(mode="json")
            ),
            "composition_execution_binding": binding,
        }
    )
    grant = material.signer.sign_omni_capability(grant_payload)
    runtime = deepcopy(material.runtime)
    runtime.update(
        {
            "composition_binding_sha256": binding.binding_sha256,
            "composition_execution_binding": binding.model_dump(mode="json"),
        }
    )
    return ticket, grant, runtime


def test_backend_client_binds_complete_invocation_and_all_four_runtime_hashes(
    tmp_path: Path,
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    store = GatewayStateStore.open(tmp_path / "gateway.sqlite3", now_ms=1_000)
    callback_events: list[int] = []
    try:
        transport = RecordingTransport(
            backend_response(material.ticket, {"state": "observed"}),
            on_execute=lambda: (
                callback_events == [1_800]
                and _nonce_count(store) == 1
            )
            or pytest.fail("transport ran before callback and nonce consumption"),
        )
        client = BackendClient(
            transport,
            store,
            ticket_consumer_instance_id="p7d1-composition-consumer",
        )

        def before_dispatch(now_ms: int) -> None:
            # Full structural authorization has returned, while neither the
            # nonce nor the handler/transport boundary has been crossed.
            assert now_ms == 1_800
            assert _nonce_count(store) == 0
            assert transport.calls == []
            callback_events.append(now_ms)

        response = _client_execute(
            client,
            material,
            material.invocation,
            before_dispatch=before_dispatch,
        )

        assert response.result.status == "SUCCEEDED"
        assert callback_events == [1_800]
        assert len(transport.calls) == 1
        wire = json.loads(transport.calls[0][0])
        assert wire["arguments"] == material.invocation
        assert set(wire["arguments"]) == {"action", "target", "args"}

        # Four independent execution-time values are bound: the complete
        # invocation plus its args, target, and optimistic target snapshot.
        assert canonical_sha256(wire["arguments"]) == material.ticket.payload.arguments_hash
        assert canonical_sha256(wire["arguments"]["args"]) == material.binding.materialized_arguments_sha256
        assert canonical_sha256(wire["arguments"]["target"]) == material.binding.target_sha256
        assert material.binding.target_snapshot_sha256 is not None
        assert wire["arguments"]["action"] == material.binding.action_id
    finally:
        store.close()


@pytest.mark.parametrize(
    "drift",
    ("overall", "shape", "action", "arguments", "target", "target_snapshot", "binding"),
)
def test_composition_preflight_drift_has_zero_callback_nonce_and_transport(
    tmp_path: Path,
    authorized_composition: AuthorizedComposition,
    drift: str,
) -> None:
    material = authorized_composition
    invocation = deepcopy(material.invocation)
    ticket = material.ticket
    grant = material.grant
    expected_binding = material.binding
    actual_snapshot = material.binding.target_snapshot_sha256

    if drift == "overall":
        invocation["args"]["skill_id"] = "substituted-skill"
    elif drift == "shape":
        invocation["unexpected"] = "caller authority injection"
        ticket, grant = _ticket_and_grant_for_invocation(material, invocation)
    elif drift == "action":
        invocation["action"] = "skill.read"
        ticket, grant = _ticket_and_grant_for_invocation(material, invocation)
    elif drift == "arguments":
        invocation["args"] = {"skill_id": "substituted-skill"}
        ticket, grant = _ticket_and_grant_for_invocation(material, invocation)
    elif drift == "target":
        invocation["target"] = "substituted-target"
        ticket, grant = _ticket_and_grant_for_invocation(material, invocation)
    elif drift == "target_snapshot":
        actual_snapshot = "9" * 64
    else:
        expected_binding = material.binding.model_copy(
            update={
                "step_id": "step.substituted",
                "binding_sha256": ZERO_SHA256,
            }
        ).with_computed_sha256()

    store = GatewayStateStore.open(tmp_path / f"{drift}.sqlite3", now_ms=1_000)
    transport = RecordingTransport(backend_response(ticket, {"unexpected": True}))
    client = BackendClient(
        transport,
        store,
        ticket_consumer_instance_id=f"p7d1-drift-{drift}",
    )
    callback_calls: list[int] = []
    try:
        with pytest.raises((BackendClientError, ExecutionAuthorizationError)):
            _client_execute(
                client,
                material,
                invocation,
                ticket=ticket,
                grant=grant,
                expected_binding=expected_binding,
                actual_target_snapshot_sha256=actual_snapshot,
                before_dispatch=callback_calls.append,
            )
        assert callback_calls == []
        assert _nonce_count(store) == 0
        assert transport.calls == []
    finally:
        store.close()


def test_before_dispatch_failure_leaves_nonce_and_transport_untouched(
    tmp_path: Path,
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    store = GatewayStateStore.open(tmp_path / "callback.sqlite3", now_ms=1_000)
    transport = RecordingTransport(backend_response(material.ticket, {"unexpected": True}))
    client = BackendClient(
        transport,
        store,
        ticket_consumer_instance_id="p7d1-callback-failure",
    )
    try:
        def fail_dispatch(_now_ms: int) -> None:
            raise RuntimeError("durable dispatch permit failed")

        with pytest.raises(RuntimeError, match="durable dispatch permit failed"):
            _client_execute(
                client,
                material,
                material.invocation,
                before_dispatch=fail_dispatch,
            )
        assert _nonce_count(store) == 0
        assert transport.calls == []
    finally:
        store.close()


def test_transport_runner_receives_exact_timeout_and_invokes_transport_once(
    tmp_path: Path,
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    store = GatewayStateStore.open(tmp_path / "runner-success.sqlite3", now_ms=1_000)
    transport = RecordingTransport(
        backend_response(material.ticket, {"state": "runner-observed"})
    )
    client = BackendClient(
        transport,
        store,
        ticket_consumer_instance_id="p7d1-transport-runner-success",
    )
    runner_calls: list[float] = []
    callback_calls: list[int] = []

    def runner(run_transport: Callable[[], dict[str, Any]], timeout_seconds: float):
        assert callback_calls == [1_800]
        assert _nonce_count(store) == 1
        assert transport.calls == []
        runner_calls.append(timeout_seconds)
        return run_transport()

    try:
        response = _client_execute(
            client,
            material,
            material.invocation,
            before_dispatch=callback_calls.append,
            transport_runner=runner,
        )
        expected_timeout = material.ticket.payload.max_runtime_ms / 1_000
        assert response.result.status == "SUCCEEDED"
        assert runner_calls == [expected_timeout]
        assert callback_calls == [1_800]
        assert len(transport.calls) == 1
        assert transport.calls[0][1] == expected_timeout
    finally:
        store.close()


def test_invalid_transport_runner_fails_before_callback_nonce_or_transport(
    tmp_path: Path,
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    store = GatewayStateStore.open(tmp_path / "runner-invalid.sqlite3", now_ms=1_000)
    transport = RecordingTransport(
        backend_response(material.ticket, {"unexpected": True})
    )
    client = BackendClient(
        transport,
        store,
        ticket_consumer_instance_id="p7d1-transport-runner-invalid",
    )
    callback_calls: list[int] = []
    try:
        with pytest.raises(ValueError, match="transport runner is invalid"):
            _client_execute(
                client,
                material,
                material.invocation,
                before_dispatch=callback_calls.append,
                transport_runner=object(),
            )
        assert callback_calls == []
        assert _nonce_count(store) == 0
        assert transport.calls == []
    finally:
        store.close()


@pytest.mark.parametrize("runner_error", (TimeoutError("deadline"), RuntimeError("pool failed")))
def test_transport_runner_timeout_or_exception_after_dispatch_is_ambiguous_without_transport(
    tmp_path: Path,
    authorized_composition: AuthorizedComposition,
    runner_error: Exception,
) -> None:
    material = authorized_composition
    store = GatewayStateStore.open(
        tmp_path / f"runner-{type(runner_error).__name__}.sqlite3",
        now_ms=1_000,
    )
    transport = RecordingTransport(
        backend_response(material.ticket, {"unexpected": True})
    )
    client = BackendClient(
        transport,
        store,
        ticket_consumer_instance_id=(
            f"p7d1-transport-runner-{type(runner_error).__name__}"
        ),
    )
    callback_calls: list[int] = []
    runner_calls: list[float] = []

    def runner(
        _run_transport: Callable[[], dict[str, Any]], timeout_seconds: float
    ) -> dict[str, Any]:
        runner_calls.append(timeout_seconds)
        raise runner_error

    try:
        with pytest.raises(BackendClientError) as caught:
            _client_execute(
                client,
                material,
                material.invocation,
                before_dispatch=callback_calls.append,
                transport_runner=runner,
            )
        assert caught.value.code == "backend.transport.failed"
        assert caught.value.ambiguous is True
        assert callback_calls == [1_800]
        assert runner_calls == [material.ticket.payload.max_runtime_ms / 1_000]
        assert _nonce_count(store) == 1
        assert transport.calls == []
    finally:
        store.close()


@pytest.mark.parametrize("failure_kind", ("transport", "response"))
def test_every_failure_after_dispatch_callback_is_ambiguous(
    tmp_path: Path,
    authorized_composition: AuthorizedComposition,
    failure_kind: str,
) -> None:
    material = authorized_composition
    store = GatewayStateStore.open(tmp_path / f"{failure_kind}.sqlite3", now_ms=1_000)
    if failure_kind == "transport":
        transport = RecordingTransport(
            error=BackendClientError("backend.composition.route_failed", status=400)
        )
    else:
        transport = RecordingTransport({"ok": True})
    client = BackendClient(
        transport,
        store,
        ticket_consumer_instance_id=f"p7d1-post-dispatch-{failure_kind}",
    )
    callback_calls: list[int] = []
    try:
        with pytest.raises(BackendClientError) as caught:
            _client_execute(
                client,
                material,
                material.invocation,
                before_dispatch=callback_calls.append,
            )
        assert caught.value.ambiguous is True
        assert callback_calls == [1_800]
        assert _nonce_count(store) == 1
        assert len(transport.calls) == 1
    finally:
        store.close()


def test_composition_transport_uses_only_exact_private_route_and_body_and_wraps_float(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    raw_omni = _valid_skill_get_result(material)
    raw_omni["metrics"] = {"ratio": 0.75}
    client = FakeCompatibilityClient(status=200, payload=raw_omni)
    grant_value = material.grant.model_dump(mode="json")
    runtime_value = deepcopy(material.runtime)
    transport = _composition_transport(
        material,
        client,
        signed_grant=grant_value,
        runtime_meta=runtime_value,
    )

    # Constructor-bound authority is detached from mutable caller dictionaries.
    grant_value["payload"]["ticket_id"] = "ticket_substituted_after_bind"
    runtime_value["effect_id"] = "eff_" + "9" * 64
    envelope = transport.execute(_wire(material), timeout_seconds=30.0)

    assert len(client.calls) == 1
    method, path, request, kwargs = client.calls[0]
    assert (method, path) == ("POST", COMPOSITION_BACKEND_PATH)
    assert set(request) == {
        "schema",
        "execute_ticket",
        "capability_grant",
        "runtime",
    }
    assert request["schema"] == COMPOSITION_BACKEND_REQUEST_SCHEMA
    assert request["execute_ticket"] == json.loads(_wire(material))
    assert request["capability_grant"] == material.grant.model_dump(mode="json")
    assert request["runtime"] == material.runtime
    assert request["runtime"]["fact_kernel_enabled"] is False
    assert kwargs == {"timeout_seconds": 30.0, "backend_started": True}

    result = envelope["execution_result"]
    payload = envelope["result_payload"]
    raw_bytes = _canonical_legacy_json(raw_omni)
    assert result["status"] == "SUCCEEDED"
    assert result["attempt"] == 1
    assert result["output_object_refs"] == []
    assert payload == {
        "schema": COMPOSITION_RESULT_PAYLOAD_SCHEMA,
        "backend_http_status": 200,
        "backend_response_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "execution_boundary": "embedded-omni-body-composition-v1",
        "omni_ok": True,
        "omni_result_json": raw_bytes.decode("utf-8"),
        "omni_result_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "omni_result_size_bytes": len(raw_bytes),
    }
    assert json.loads(payload["omni_result_json"])["result"]["elapsed_seconds"] == 0.125
    assert canonical_sha256(payload) == result["result_payload_sha256"]


def test_composition_transport_uses_continuation_binding_attempt(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    ticket, grant, runtime = _continuation_signed_authority(material, attempt=2)
    client = FakeCompatibilityClient(
        status=200,
        payload=_valid_skill_get_result(material),
    )
    transport = _composition_transport(
        material,
        client,
        signed_grant=grant.model_dump(mode="json"),
        runtime_meta=runtime,
    )

    envelope = transport.execute(
        _wire(material, ticket=ticket),
        timeout_seconds=30.0,
    )

    assert envelope["execution_result"]["attempt"] == 2


def test_composition_transport_rejects_mismatched_expected_result_schema_before_route(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    client = FakeCompatibilityClient(
        status=200,
        payload=_valid_skill_get_result(material),
    )
    transport = _composition_transport(
        material,
        client,
        expected_result_schema_sha256="f" * 64,
    )

    with pytest.raises(BackendClientError) as caught:
        transport.execute(_wire(material), timeout_seconds=30.0)

    assert caught.value.code == "backend.composition.result_schema_authority_invalid"
    assert caught.value.ambiguous is False
    assert client.calls == []


def test_composition_transport_rejects_success_result_outside_explicit_schema(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    raw_omni = _valid_skill_get_result(material)
    raw_omni["result"]["action"] = "skill.read"
    client = FakeCompatibilityClient(status=200, payload=raw_omni)
    transport = _composition_transport(material, client)

    with pytest.raises(BackendClientError) as caught:
        transport.execute(_wire(material), timeout_seconds=30.0)

    assert caught.value.code == "backend.composition.result_schema_rejected"
    assert caught.value.ambiguous is True
    assert len(client.calls) == 1


def test_composition_transport_rejects_untrusted_backend_output_object_refs(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    raw_omni = _valid_skill_get_result(material)
    raw_omni["output_object_refs"] = ["obj_" + "a" * 64]
    client = FakeCompatibilityClient(status=200, payload=raw_omni)
    transport = _composition_transport(material, client)

    with pytest.raises(BackendClientError) as caught:
        transport.execute(_wire(material), timeout_seconds=30.0)

    assert caught.value.code == "backend.composition.output_object_refs_untrusted"
    assert caught.value.ambiguous is True
    assert len(client.calls) == 1


def test_composition_transport_returns_final_failure_for_non_5xx_omni_failure(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    raw_omni = {"ok": False, "cuowu": "action rejected", "progress": 0.5}
    client = FakeCompatibilityClient(status=400, payload=raw_omni)
    transport = _composition_transport(material, client)

    envelope = transport.execute(_wire(material), timeout_seconds=30.0)

    result = envelope["execution_result"]
    assert result["status"] == "FAILED_FINAL"
    assert result["error_code"] == "composition.runtime.action_failed"
    assert result["error_message"] == "action rejected"
    assert envelope["result_payload"]["omni_ok"] is False
    assert json.loads(envelope["result_payload"]["omni_result_json"])["progress"] == 0.5


def test_composition_transport_treats_5xx_as_unknown_after_started(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    client = FakeCompatibilityClient(
        status=503,
        payload={"ok": False, "error": "backend unavailable"},
    )
    transport = _composition_transport(material, client)

    with pytest.raises(BackendClientError) as caught:
        transport.execute(_wire(material), timeout_seconds=30.0)
    assert caught.value.code == "backend.composition.outcome_unknown"
    assert caught.value.status == 503
    assert caught.value.ambiguous is True
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "replacement",
    ("wire_authority", "bound_pair", "grant", "runtime", "fact_kernel"),
)
def test_composition_transport_rejects_authority_or_binding_replacement_before_route(
    authorized_composition: AuthorizedComposition,
    replacement: str,
) -> None:
    material = authorized_composition
    invocation = deepcopy(material.invocation)
    grant = material.grant.model_dump(mode="json")
    runtime = deepcopy(material.runtime)
    if replacement == "wire_authority":
        invocation["__runtime"] = runtime
        ticket, _ = _ticket_and_grant_for_invocation(material, invocation)
    elif replacement == "bound_pair":
        ticket = material.ticket
        _alternate_ticket, alternate_grant, alternate_runtime = (
            _alternate_signed_authority(material)
        )
        grant = alternate_grant.model_dump(mode="json")
        runtime = alternate_runtime
    else:
        ticket = material.ticket
        if replacement == "grant":
            grant["payload"]["effect_id"] = "eff_" + "8" * 64
        elif replacement == "runtime":
            runtime["composition_binding_sha256"] = "8" * 64
        else:
            runtime["fact_kernel_enabled"] = True

    client = FakeCompatibilityClient(status=200, payload={"ok": True})
    transport = _composition_transport(
        material,
        client,
        signed_grant=grant,
        runtime_meta=runtime,
    )
    with pytest.raises(BackendClientError):
        transport.execute(
            _wire(material, ticket=ticket, invocation=invocation),
            timeout_seconds=30.0,
        )
    assert client.calls == []


def test_composition_transport_bounds_oversized_output_and_rejects_impossible_envelope(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    raw_omni = {"ok": True, "content": "x" * 2_000}
    client = FakeCompatibilityClient(status=200, payload=raw_omni)

    bounded_ticket, bounded_grant = _ticket_and_grant_for_output_limit(
        material, 256
    )
    transport = _composition_transport(
        material,
        client,
        signed_grant=bounded_grant.model_dump(mode="json"),
        runtime_meta=material.runtime,
    )
    envelope = transport.execute(
        _wire(material, ticket=bounded_ticket), timeout_seconds=30.0
    )
    assert envelope["execution_result"]["status"] == "FAILED_FINAL"
    assert (
        envelope["execution_result"]["error_code"]
        == "composition.runtime.output_too_large"
    )
    assert envelope["result_payload"]["error_code"] == "composition.runtime.output_too_large"
    assert "omni_result_json" not in envelope["result_payload"]

    impossible_ticket, impossible_grant = _ticket_and_grant_for_output_limit(
        material, 8
    )
    impossible_transport = _composition_transport(
        material,
        client,
        signed_grant=impossible_grant.model_dump(mode="json"),
        runtime_meta=material.runtime,
    )
    with pytest.raises(BackendClientError) as caught:
        impossible_transport.execute(
            _wire(material, ticket=impossible_ticket), timeout_seconds=30.0
        )
    assert caught.value.code == "backend.composition.output_envelope_impossible"
    assert caught.value.ambiguous is True


def test_embedded_private_route_verifies_authority_and_runs_omni_exactly_once_without_model(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    runner_calls: list[dict[str, Any]] = []
    model_calls: list[tuple[Any, ...]] = []
    authorization_calls: list[dict[str, Any]] = []

    def runner(payload: dict[str, Any]) -> dict[str, Any]:
        runner_calls.append(deepcopy(payload))
        return {"ok": True, "elapsed_seconds": 0.25}

    backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
    backend._lock = threading.RLock()
    backend._closed = False
    backend._closing = False
    backend.qiaojie = SimpleNamespace(_core_execution_lock=threading.RLock())
    backend.scheduler = SimpleNamespace(
        _zhiming_llm=lambda *args: model_calls.append(args)
    )
    backend.set_composition_dispatch_authorizer(
        lambda **kwargs: authorization_calls.append(kwargs)
    )
    request = _private_request(material)

    def import_module(name: str):
        assert name == "v3.jineng.jirou_ceng"
        return SimpleNamespace(_run_omni_body_tool=runner)

    with mock.patch(
        "total_gateway.embedded_backend.time.time_ns",
        return_value=1_800_000_000,
    ), mock.patch(
        "total_gateway.embedded_backend.importlib.import_module",
        side_effect=import_module,
    ):
        status, payload, media_type = backend.request(
            "POST",
            COMPOSITION_BACKEND_PATH,
            request,
        )

    assert status == 200
    assert payload == {"ok": True, "elapsed_seconds": 0.25}
    assert media_type == "application/json; charset=utf-8"
    assert model_calls == []
    assert len(authorization_calls) == 1
    assert authorization_calls[0]["ticket"] == material.ticket
    assert authorization_calls[0]["grant"] == material.grant
    assert runner_calls == [
        {
            **material.invocation,
            "__capability_grant": material.grant.model_dump(mode="json"),
            "__runtime": material.runtime,
        }
    ]
    assert runner_calls[0]["__runtime"]["fact_kernel_enabled"] is False


def test_transport_and_real_embedded_request_contract_execute_end_to_end(
    authorized_composition: AuthorizedComposition,
) -> None:
    material = authorized_composition
    runner_calls: list[dict[str, Any]] = []

    def runner(payload: dict[str, Any]) -> dict[str, Any]:
        runner_calls.append(deepcopy(payload))
        return _valid_skill_get_result(material)

    backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
    backend._lock = threading.RLock()
    backend._closed = False
    backend._closing = False
    backend.qiaojie = SimpleNamespace(_core_execution_lock=threading.RLock())
    backend.scheduler = SimpleNamespace()
    authorization_calls: list[dict[str, Any]] = []
    client = CompatibilityJsonClient(backend)
    client.set_composition_dispatch_authorizer(
        lambda **kwargs: authorization_calls.append(kwargs)
    )
    transport = _composition_transport(material, client)

    def import_module(name: str):
        assert name == "v3.jineng.jirou_ceng"
        return SimpleNamespace(_run_omni_body_tool=runner)

    with mock.patch(
        "total_gateway.embedded_backend.time.time_ns",
        return_value=1_800_000_000,
    ), mock.patch(
        "total_gateway.embedded_backend.importlib.import_module",
        side_effect=import_module,
    ):
        envelope = transport.execute(_wire(material), timeout_seconds=30.0)

    assert envelope["execution_result"]["status"] == "SUCCEEDED"
    assert envelope["result_payload"]["omni_ok"] is True
    assert len(authorization_calls) == 1
    assert runner_calls == [
        {
            **material.invocation,
            "__capability_grant": material.grant.model_dump(mode="json"),
            "__runtime": material.runtime,
        }
    ]


def test_embedded_private_route_without_gateway_authorizer_is_handler_zero(
    authorized_composition: AuthorizedComposition,
) -> None:
    backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
    backend._lock = threading.RLock()
    backend._closed = False
    backend._closing = False
    backend.qiaojie = SimpleNamespace(_core_execution_lock=threading.RLock())
    backend.scheduler = SimpleNamespace()

    with mock.patch(
        "total_gateway.embedded_backend.time.time_ns",
        return_value=1_800_000_000,
    ), mock.patch(
        "total_gateway.embedded_backend.importlib.import_module"
    ) as import_module:
        status, payload, media_type = backend.request(
            "POST",
            COMPOSITION_BACKEND_PATH,
            _private_request(authorized_composition),
        )

    assert status == 500
    assert payload["ok"] is False
    assert payload["error_type"] == "PermissionError"
    assert media_type == "application/problem+json"
    import_module.assert_not_called()


def test_embedded_composition_route_is_private_and_has_no_legacy_alias(
    authorized_composition: AuthorizedComposition,
) -> None:
    backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
    backend._lock = threading.RLock()
    backend._closed = False
    backend._closing = False
    backend.qiaojie = SimpleNamespace(_core_execution_lock=threading.RLock())

    with mock.patch(
        "total_gateway.embedded_backend.importlib.import_module"
    ) as import_module:
        status, payload, media_type = backend.request(
            "POST",
            "/api/v1/internal/composition/execute",
            _private_request(authorized_composition),
        )
    assert status == 404
    assert payload == {"ok": False, "error": "not_found"}
    assert media_type == "application/problem+json"
    import_module.assert_not_called()
