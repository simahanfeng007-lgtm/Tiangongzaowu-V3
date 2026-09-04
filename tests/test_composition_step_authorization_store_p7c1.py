"""P7C.1 durable composition-step authorization receipt tests.

The receipt is an ISSUED authorization boundary only.  It must not claim,
start, complete, or otherwise mutate an execution effect, and it must not
consume either signed nonce before the real runtime boundary.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import threading

import pytest

from contracts import (
    ActionImpact,
    ActionIntent,
    CompositionExecutionBindingV1,
    ExecutionTicket,
    ExecutionTicketHeader,
    ExecutionTicketPayload,
    ObjectGrant,
    OmniCapabilityGrant,
    OmniCapabilityGrantHeader,
    OmniCapabilityGrantPayload,
    PolicyDecision,
    PublicKeyDescriptor,
    ResourceEnvelope,
    SourceRef,
    TrustBundle,
    TrustScope,
    canonical_sha256,
)
from total_gateway.composition_step_authorization import (
    CompositionStepAuthorizationArtifacts,
    CompositionStepAuthorizationRequest,
    canonical_json_text,
)
from total_gateway.store import (
    STORE_SCHEMA_VERSION,
    GatewayStateStore,
    StoreConflictError,
)
from tests.gateway_store_migration_support import downgrade_v33_to_v32

from tests.test_composition_executable_plan_p7c0 import (
    _compile_material,
    _persist_executable,
)


ZERO = "0" * 64


def _extra_object_grant(*, sha256: str = "7" * 64) -> ObjectGrant:
    return ObjectGrant(
        object_id="object-parent-extra-p7c1",
        revision=1,
        sha256=sha256,
        size_bytes=17,
        mime="application/json",
        tenant_id="tenant-p7c1",
        link_account_id="account-p7c1",
        conversation_scope_hash="9" * 64,
    )


def _parent_ticket(plan) -> ExecutionTicket:
    input_objects = tuple(
        item.object_grant
        for item in plan.plan_inputs
        if item.object_grant is not None
    )
    resources = {
        "max_output_bytes": 1_000_000,
        "max_runtime_ms": 30_000,
        "max_tool_calls": 10,
    }
    allowed_side_effects = ("none", "read")
    payload = ExecutionTicketPayload(
        ticket_id="ticket-parent-p7c1",
        nonce="nonce-parent-p7c1",
        issued_at_ms=1_000,
        not_before_ms=1_000,
        expires_at_ms=2_400,
        gateway_epoch=1,
        fence_epoch=1,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        effect_id="eff_" + "a" * 64,
        channel="test",
        tenant_id="tenant-p7c1",
        link_account_id="account-p7c1",
        conversation_scope_hash="9" * 64,
        principal_scope_hash=plan.principal_scope_hash,
        capability_manifest_hash=plan.capability_manifest_sha256,
        policy_snapshot_hash="b" * 64,
        decision_id="decision-parent-p7c1",
        decision_sha256="c" * 64,
        impact_id="impact-parent-p7c1",
        impact_sha256="d" * 64,
        action_permission_sha256="e" * 64,
        component_manifest_hash="f" * 64,
        life_snapshot_revision=1,
        life_snapshot_hash="1" * 64,
        risk_class="A0",
        action_id="model.respond",
        action_version="1.0.0",
        argument_schema_sha256="2" * 64,
        arguments_hash="3" * 64,
        workspace_id=plan.workspace.workspace_id,
        input_objects=input_objects,
        object_grants_sha256=canonical_sha256(
            [item.model_dump(mode="json") for item in input_objects]
        ),
        output_root_id="workspace-output-p7c1",
        **resources,
        resource_envelope_sha256=canonical_sha256(resources),
        allowed_side_effects=allowed_side_effects,
        side_effect_envelope_sha256=canonical_sha256(
            {"allowed_side_effects": list(allowed_side_effects)}
        ),
    )
    return ExecutionTicket(
        header=ExecutionTicketHeader(kid="parent-ticket-key-p7c1"),
        payload=payload,
        signature="A" * 86,
    )


def _parent_with_objects(
    parent_ticket: ExecutionTicket, *objects: ObjectGrant
) -> ExecutionTicket:
    payload = ExecutionTicketPayload(
        **{
            **parent_ticket.payload.model_dump(mode="python"),
            "input_objects": objects,
            "object_grants_sha256": canonical_sha256(
                [item.model_dump(mode="json") for item in objects]
            ),
        }
    )
    return ExecutionTicket(
        header=parent_ticket.header,
        payload=payload,
        signature=parent_ticket.signature,
    )


def _receipt_fixture(store: GatewayStateStore, root: Path):
    material = _compile_material(store, root)
    bundle = _persist_executable(store, material)
    plan = bundle.record.executable_plan
    step = plan.step_bindings[0]
    target = step.target_skeleton
    assert target is not None
    args = {"artifact_id": "artifact-001", "mode": "metadata-only"}
    target_snapshot = {"exists": False, "target": target}
    object_grants = [
        item.object_grant.model_dump(mode="json")
        for item in plan.plan_inputs
        if item.object_grant is not None
    ]
    parent_ticket = _parent_ticket(plan)
    request = CompositionStepAuthorizationRequest.build(
        registration_id=plan.registration_id,
        registration_sha256=plan.registration_sha256,
        executable_plan_id=plan.executable_plan_id,
        executable_plan_sha256=plan.executable_plan_sha256,
        composition_plan_id=plan.composition_plan_id,
        composition_plan_sha256=plan.composition_plan_sha256,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        principal_scope_hash=plan.principal_scope_hash,
        parent_ticket_id=parent_ticket.payload.ticket_id,
        parent_ticket_sha256=canonical_sha256(
            parent_ticket.model_dump(mode="json")
        ),
        parent_ticket_expires_at_ms=parent_ticket.payload.expires_at_ms,
        step_id=step.step_id,
        step_binding_sha256=step.sha256,
        attempt=1,
        action_id=step.action_id,
        action_version=step.action_version,
        source_revision_sha256=canonical_sha256(
            step.source_revision.model_dump(mode="json")
        ),
        action_registry_sha256=plan.action_registry_sha256,
        action_permission_sha256=step.permission_sha256,
        argument_schema_sha256=step.argument_schema_sha256,
        result_schema_sha256=step.result_schema_sha256,
        composition_binding_sha256="2" * 64,
        materialized_arguments=args,
        target=target,
        target_ref="target-" + canonical_sha256(
            {"action": step.action_id, "target": target}
        ),
        target_snapshot=target_snapshot,
        workspace_id=plan.workspace.workspace_id,
        workspace_scope_sha256=plan.workspace.workspace_scope_sha256,
        object_grants=object_grants,
        prebound_effect_id="eff_" + "3" * 64,
        prebound_effect_intent_sha256="4" * 64,
        action_fence_epoch=0,
        issued_at_ms=1_700,
        expires_at_ms=2_300,
        authorization_ceiling_ms=2_400,
    )
    request = replace(
        request,
        composition_binding_sha256=_composition_binding(request)[
            "binding_sha256"
        ],
        authorization_request_sha256=ZERO,
    ).with_computed_sha256()
    artifacts = _artifacts(request)
    return plan, step, parent_ticket, request, artifacts


def _hashed_record(payload: dict, digest_field: str) -> dict:
    return {
        **payload,
        digest_field: canonical_sha256(payload),
    }


def _composition_binding(
    request: CompositionStepAuthorizationRequest,
) -> dict:
    materialized_arguments_sha256 = canonical_sha256(
        request.materialized_arguments
    )
    canonical_invocation_sha256 = canonical_sha256(
        {
            "action_id": request.action_id,
            "action_version": request.action_version,
            "payload_sha256": materialized_arguments_sha256,
            "target_ref": request.target_ref,
            "workspace_id": request.workspace_id,
        }
    )
    binding = {
        "schema_version": "tiangong.composition-execution-binding.v1",
        "binding_type": "COMPOSITION_STEP",
        "executable_plan_id": request.executable_plan_id,
        "executable_plan_sha256": request.executable_plan_sha256,
        "step_id": request.step_id,
        "step_binding_sha256": request.step_binding_sha256,
        "request_id": request.request_id,
        "run_id": request.run_id,
        "generation": request.generation,
        "effect_id": request.prebound_effect_id,
        "action_id": request.action_id,
        "action_version": request.action_version,
        "materialized_arguments_sha256": materialized_arguments_sha256,
        "canonical_invocation_sha256": canonical_invocation_sha256,
        "target_sha256": canonical_sha256(request.target),
        "workspace_id": request.workspace_id,
        "workspace_scope_hash": request.workspace_scope_sha256,
    }
    if request.target_snapshot_sha256 is not None:
        binding["target_snapshot_sha256"] = request.target_snapshot_sha256
    if request.schema_version == "tiangong.composition-step-authorization.v2":
        binding.update(
            {
                "attempt": request.attempt,
                "continuation_delegation_id": (
                    request.continuation_delegation_id
                ),
                "continuation_delegation_sha256": (
                    request.continuation_delegation_sha256
                ),
                "dependency_evidence_sha256": (
                    request.dependency_evidence_sha256
                ),
            }
        )
        if request.supersedes_authorization_id is not None:
            binding.update(
                {
                    "supersedes_authorization_id": (
                        request.supersedes_authorization_id
                    ),
                    "supersedes_effect_id": request.supersedes_effect_id,
                    "supersedes_claim_sha256": request.supersedes_claim_sha256,
                }
            )
    return _hashed_record(binding, "binding_sha256")


def _artifacts(
    request: CompositionStepAuthorizationRequest,
    *,
    nonce_suffix: str = "winner",
    allowed_side_effects: tuple[str, ...] = ("read",),
) -> CompositionStepAuthorizationArtifacts:
    materialized_arguments_sha256 = canonical_sha256(
        request.materialized_arguments
    )
    binding = CompositionExecutionBindingV1(**_composition_binding(request))
    assert binding.binding_sha256 == request.composition_binding_sha256
    resources = ResourceEnvelope(
        max_runtime_ms=30_000,
        max_output_bytes=1_000_000,
        max_tool_calls=1,
    )
    source_ref = SourceRef(
        source_type="CURRENT_USER_INSTRUCTION",
        object_id="source-p7c1",
        object_revision=1,
        sha256="5" * 64,
    )
    objects = tuple(ObjectGrant(**item) for item in request.object_grants)
    intent = ActionIntent(
        intent_id="intent-p7c1",
        source="chat",
        life_id="life-p7c1",
        principal_scope_hash=request.principal_scope_hash,
        conversation_scope_hash="9" * 64,
        request_id=request.request_id,
        run_id=request.run_id,
        generation=request.generation,
        action_id=request.action_id,
        action_version=request.action_version,
        arguments_sha256=request.arguments_sha256,
        workspace_id=request.workspace_id,
        workspace_scope_hash=request.workspace_scope_sha256,
        input_object_refs=tuple(item.object_id for item in objects),
        requested_side_effects=("read",),
        requested_resources=resources,
        source_refs=(source_ref,),
        payload_sha256=materialized_arguments_sha256,
        attachment_set_sha256=request.object_grants_sha256,
        target_ref=request.target_ref,
        target_snapshot_sha256=request.target_snapshot_sha256,
        composition_execution_binding=binding,
        created_at_ms=request.issued_at_ms,
        expires_at_ms=request.expires_at_ms,
        intent_sha256=ZERO,
    ).with_computed_sha256()
    impact = ActionImpact(
        impact_id="impact-p7c1",
        life_id=intent.life_id,
        action_id=request.action_id,
        intent_sha256=intent.intent_sha256,
        dynamic_risk="A0",
        target_snapshot_sha256=request.target_snapshot_sha256,
        touches_identity=False,
        touches_soul=False,
        touches_memory_keys=False,
        touches_policy=False,
        touches_core_code=False,
        workspace_scope_milli=0,
        external_recipient_count=0,
        credential_scope_milli=0,
        privacy_scope_milli=0,
        blast_radius_milli=0,
        irreversibility_milli=0,
        uncertainty_milli=0,
        estimated_resource_cost_milli=0,
        source_event_ids=("lev_" + "5" * 64,),
        created_at_ms=request.issued_at_ms,
        impact_sha256=ZERO,
    ).with_computed_impact_sha256()
    capability_manifest_hash = "6" * 64
    component_manifest_hash = "7" * 64
    policy_snapshot_sha256 = "8" * 64
    decision = PolicyDecision(
        decision_id="decision-p7c1",
        intent_sha256=intent.intent_sha256,
        impact_id=impact.impact_id,
        impact_sha256=impact.impact_sha256,
        action_permission_sha256=request.action_permission_sha256,
        action_registry_sha256=request.action_registry_sha256,
        capability_manifest_hash=capability_manifest_hash,
        component_manifest_hash=component_manifest_hash,
        policy_snapshot_sha256=policy_snapshot_sha256,
        policy_coverage_version="coverage-p7c1",
        policy_coverage_sha256="a" * 64,
        computed_risk="A0",
        outcome="ALLOW",
        composition_execution_binding=binding,
        reason_codes=("policy.composition_a0_allow",),
        decided_at_ms=request.issued_at_ms,
        decision_sha256=ZERO,
    ).with_computed_sha256()
    legal_side_effects = {
        "none",
        "read",
        "local_write",
        "external_write",
        "external_send",
        "destructive",
    }
    contract_side_effects = (
        allowed_side_effects
        if all(item in legal_side_effects for item in allowed_side_effects)
        else ("read",)
    )
    ticket_payload = ExecutionTicketPayload(
        ticket_id=f"execution-ticket-p7c1-{nonce_suffix}",
        nonce=f"execution-nonce-p7c1-{nonce_suffix}",
        issued_at_ms=request.issued_at_ms,
        not_before_ms=request.issued_at_ms,
        expires_at_ms=request.expires_at_ms,
        gateway_epoch=1,
        fence_epoch=max(1, request.action_fence_epoch),
        request_id=request.request_id,
        run_id=request.run_id,
        generation=request.generation,
        effect_id=request.prebound_effect_id,
        channel="test",
        tenant_id="tenant-p7c1",
        link_account_id="account-p7c1",
        conversation_scope_hash="9" * 64,
        principal_scope_hash=request.principal_scope_hash,
        capability_manifest_hash=capability_manifest_hash,
        policy_snapshot_hash=policy_snapshot_sha256,
        policy_coverage_sha256=decision.policy_coverage_sha256,
        intent_id=intent.intent_id,
        intent_sha256=intent.intent_sha256,
        canonical_invocation_sha256=intent.canonical_invocation_sha256,
        decision_id=decision.decision_id,
        decision_sha256=decision.decision_sha256,
        impact_id=impact.impact_id,
        impact_sha256=impact.impact_sha256,
        action_permission_sha256=request.action_permission_sha256,
        component_manifest_hash=component_manifest_hash,
        life_snapshot_revision=1,
        life_snapshot_hash="b" * 64,
        claim_sha256="c" * 64,
        claim_revision=1,
        claim_lease_epoch=1,
        risk_class="A0",
        action_id=request.action_id,
        action_version=request.action_version,
        argument_schema_sha256=request.argument_schema_sha256,
        arguments_hash=request.arguments_sha256,
        workspace_id=request.workspace_id,
        input_objects=objects,
        object_grants_sha256=request.object_grants_sha256,
        output_root_id="workspace-output-p7c1",
        max_output_bytes=resources.max_output_bytes,
        max_runtime_ms=resources.max_runtime_ms,
        max_tool_calls=resources.max_tool_calls,
        resource_envelope_sha256=resources.sha256(),
        allowed_side_effects=contract_side_effects,
        side_effect_envelope_sha256=canonical_sha256(
            {"allowed_side_effects": list(contract_side_effects)}
        ),
        composition_execution_binding=binding,
    )
    signed_ticket = ExecutionTicket(
        header=ExecutionTicketHeader(kid="test-p7c1"),
        payload=ticket_payload,
        signature="A" * 86,
    )
    grant_payload = OmniCapabilityGrantPayload(
        grant_id=f"grant-p7c1-{nonce_suffix}",
        ticket_id=ticket_payload.ticket_id,
        ticket_sha256=canonical_sha256(ticket_payload.model_dump(mode="json")),
        request_id=request.request_id,
        run_id=request.run_id,
        generation=request.generation,
        effect_id=request.prebound_effect_id,
        decision_id=decision.decision_id,
        decision_sha256=decision.decision_sha256,
        impact_sha256=impact.impact_sha256,
        action_permission_sha256=request.action_permission_sha256,
        action_registry_sha256=request.action_registry_sha256,
        capability_manifest_hash=capability_manifest_hash,
        component_manifest_hash=component_manifest_hash,
        action_id=request.action_id,
        action_version=request.action_version,
        arguments_sha256=request.arguments_sha256,
        workspace_id=request.workspace_id,
        workspace_scope_hash=request.workspace_scope_sha256,
        principal_scope_hash=request.principal_scope_hash,
        conversation_scope_hash="9" * 64,
        risk_class="A0",
        allowed_side_effects=contract_side_effects,
        path_policy="object_grant_only",
        allow_absolute_paths=False,
        allow_shell=False,
        allow_python=False,
        composition_execution_binding=binding,
        gateway_epoch=1,
        nonce=f"grant-nonce-p7c1-{nonce_suffix}",
        issued_at_ms=request.issued_at_ms,
        not_before_ms=request.issued_at_ms,
        expires_at_ms=request.expires_at_ms,
    )
    signed_grant = OmniCapabilityGrant(
        header=OmniCapabilityGrantHeader(kid="test-p7c1"),
        payload=grant_payload,
        signature="B" * 86,
    )
    public_key = b"\x01" * 32
    trust_scope = TrustScope(
        issuer="tiangong-total-gateway",
        audience="tiangong-backend",
        purpose="execution_ticket",
    )
    trust_key = PublicKeyDescriptor(
        kid="test-p7c1",
        issuer=trust_scope.issuer,
        audience=trust_scope.audience,
        purpose=trust_scope.purpose,
        public_key_base64url=base64.urlsafe_b64encode(public_key)
        .rstrip(b"=")
        .decode("ascii"),
        public_key_sha256=hashlib.sha256(public_key).hexdigest(),
        state="ACTIVE",
        not_before_ms=0,
        not_after_ms=100_000,
        component_manifest_hash=component_manifest_hash,
    )
    trust_bundle = TrustBundle(
        bundle_id="trust-bundle-p7c1",
        revision=1,
        gateway_epoch=1,
        generated_at_ms=request.issued_at_ms,
        required_scopes=(trust_scope,),
        keys=(trust_key,),
        production_ready=True,
        bundle_sha256=ZERO,
    ).with_computed_sha256()
    signed_ticket_value = signed_ticket.model_dump(mode="json")
    signed_grant_value = signed_grant.model_dump(mode="json")
    if contract_side_effects != allowed_side_effects:
        signed_ticket_value["payload"]["allowed_side_effects"] = list(
            allowed_side_effects
        )
        signed_ticket_value["payload"]["side_effect_envelope_sha256"] = (
            canonical_sha256(
                {"allowed_side_effects": list(allowed_side_effects)}
            )
        )
        signed_grant_value["payload"]["allowed_side_effects"] = list(
            allowed_side_effects
        )
    runtime_response = {
        "status": "OK",
        "grant": signed_grant_value,
        "runtime": {
            "execution_ticket_id": ticket_payload.ticket_id,
            "request_id": request.request_id,
            "run_id": request.run_id,
            "generation": request.generation,
            "effect_id": request.prebound_effect_id,
            "step_id": request.step_id,
            "executable_plan_id": request.executable_plan_id,
            "composition_binding_sha256": request.composition_binding_sha256,
            "composition_execution_binding": binding.model_dump(mode="json"),
            "principal_scope_hash": request.principal_scope_hash,
            "workspace_id": request.workspace_id,
            "action_id": request.action_id,
            "action_version": request.action_version,
            "decision_sha256": decision.decision_sha256,
            "impact_sha256": impact.impact_sha256,
            "action_permission_sha256": request.action_permission_sha256,
            "action_registry_sha256": request.action_registry_sha256,
            "capability_manifest_hash": capability_manifest_hash,
            "component_manifest_hash": component_manifest_hash,
            "confirmation_sha256": None,
            "skill_id": None,
            "skill_version": None,
            "skill_sha256": None,
            "skill_activation_sha256": None,
            "gateway_url": "http://127.0.0.1:8000",
            "session_id": "session-p7c1",
            "fact_kernel_enabled": True,
            "gateway_epoch": 1,
            "trust_bundle_sha256": trust_bundle.bundle_sha256,
            "trust_bundle": trust_bundle.model_dump(mode="json"),
            "user_path_roots": [],
        },
        "decision": {
            "decision_id": decision.decision_id,
            "decision_sha256": decision.decision_sha256,
            "risk_class": "A0",
            "reason_codes": list(decision.reason_codes),
        },
    }
    return CompositionStepAuthorizationArtifacts.build(
        intent=intent,
        impact=impact,
        decision=decision,
        signed_ticket=signed_ticket_value,
        signed_grant=signed_grant_value,
        runtime_response=runtime_response,
    )


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in (
            "effect_ledger",
            "effect_attempts",
            "effect_facts",
            "security_nonce_ledger",
        )
    }


def test_v32_commit_is_insert_only_replayable_and_does_not_write_effects_or_nonces(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        before = _table_counts(store._connection)
        first, created = store.commit_composition_step_authorization(
            request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_700
        )
        replay, replay_created = store.commit_composition_step_authorization(
            request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_701
        )

        assert created is True
        assert replay_created is False
        assert replay == first
        assert replay.runtime_response == first.runtime_response
        mutable = replay.runtime_response
        mutable["status"] = "MUTATED"
        assert first.runtime_response["status"] == "OK"
        assert _table_counts(store._connection) == before
        assert store.health_check(now_ms=1_702, full=True).healthy


def test_commit_and_replay_reject_parent_ticket_envelope_substitution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-parent-envelope.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        store.commit_composition_step_authorization(
            request,
            parent_ticket=parent_ticket,
            artifacts=artifacts,
            now_ms=1_700,
        )
        substituted = parent_ticket.model_copy(update={"signature": "B" * 86})
        with pytest.raises(StoreConflictError, match="parent ticket binding"):
            store.commit_composition_step_authorization(
                request,
                parent_ticket=substituted,
                artifacts=artifacts,
                now_ms=1_701,
            )
        assert store._connection.execute(
            "SELECT count(*) FROM composition_step_authorization"
        ).fetchone()[0] == 1


def test_commit_rejects_parent_ticket_scope_substitution(tmp_path: Path) -> None:
    path = tmp_path / "gateway-parent-scope.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        substituted = parent_ticket.model_copy(
            update={
                "payload": parent_ticket.payload.model_copy(
                    update={"workspace_id": "workspace-substituted-p7c1"}
                )
            }
        )
        substituted_request = replace(
            request,
            parent_ticket_sha256=canonical_sha256(
                substituted.model_dump(mode="json")
            ),
            authorization_request_sha256=ZERO,
        ).with_computed_sha256()
        with pytest.raises(StoreConflictError, match="parent ticket scope"):
            store.commit_composition_step_authorization(
                substituted_request,
                parent_ticket=substituted,
                artifacts=artifacts,
                now_ms=1_700,
            )
        assert store._connection.execute(
            "SELECT count(*) FROM composition_step_authorization"
        ).fetchone()[0] == 0


def test_parent_ticket_may_carry_grants_beyond_the_narrowed_plan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-parent-object-superset.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, _ = _receipt_fixture(store, tmp_path)
        parent_ticket = _parent_with_objects(
            parent_ticket, _extra_object_grant()
        )
        narrowed_request = replace(
            request,
            parent_ticket_sha256=canonical_sha256(
                parent_ticket.model_dump(mode="json")
            ),
            authorization_request_sha256=ZERO,
        ).with_computed_sha256()
        record, created = store.commit_composition_step_authorization(
            narrowed_request,
            parent_ticket=parent_ticket,
            artifacts=_artifacts(narrowed_request),
            now_ms=1_700,
        )
        assert created is True
        assert record.request.object_grants == []
        assert len(parent_ticket.payload.input_objects) == 1


def test_child_cannot_invent_a_grant_absent_from_parent(tmp_path: Path) -> None:
    path = tmp_path / "gateway-child-object-forgery.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, _ = _receipt_fixture(store, tmp_path)
        invented = [_extra_object_grant().model_dump(mode="json")]
        forged_request = replace(
            request,
            object_grants_json=canonical_json_text(invented),
            object_grants_sha256=canonical_sha256(invented),
            authorization_request_sha256=ZERO,
        ).with_computed_sha256()
        with pytest.raises(StoreConflictError, match="parent ticket objects"):
            store.commit_composition_step_authorization(
                forged_request,
                parent_ticket=parent_ticket,
                artifacts=_artifacts(forged_request),
                now_ms=1_700,
            )


def test_child_cannot_expand_to_an_extra_parent_grant_not_in_plan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-child-object-expansion.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, _ = _receipt_fixture(store, tmp_path)
        extra = _extra_object_grant()
        parent_ticket = _parent_with_objects(parent_ticket, extra)
        expanded = [extra.model_dump(mode="json")]
        expanded_request = replace(
            request,
            parent_ticket_sha256=canonical_sha256(
                parent_ticket.model_dump(mode="json")
            ),
            object_grants_json=canonical_json_text(expanded),
            object_grants_sha256=canonical_sha256(expanded),
            authorization_request_sha256=ZERO,
        ).with_computed_sha256()
        with pytest.raises(StoreConflictError, match="crossed its active plan"):
            store.commit_composition_step_authorization(
                expanded_request,
                parent_ticket=parent_ticket,
                artifacts=_artifacts(expanded_request),
                now_ms=1_700,
            )


@pytest.mark.parametrize("now_ms", (999, 2_400))
def test_commit_rejects_parent_ticket_outside_live_window(
    tmp_path: Path, now_ms: int
) -> None:
    path = tmp_path / f"gateway-parent-time-{now_ms}.sqlite3"
    with GatewayStateStore.open(path, now_ms=900) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        with pytest.raises(StoreConflictError, match="parent ticket is not live"):
            store.commit_composition_step_authorization(
                request,
                parent_ticket=parent_ticket,
                artifacts=artifacts,
                now_ms=now_ms,
            )
        assert store._connection.execute(
            "SELECT count(*) FROM composition_step_authorization"
        ).fetchone()[0] == 0


def test_same_stable_key_with_different_request_is_a_collision(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        store.commit_composition_step_authorization(
            request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_700
        )
        collision = replace(
            request,
            composition_binding_sha256="9" * 64,
            authorization_request_sha256=ZERO,
        ).with_computed_sha256()
        with pytest.raises(StoreConflictError, match="identity was reused"):
            store.commit_composition_step_authorization(
                collision,
                parent_ticket=parent_ticket,
                artifacts=artifacts,
                now_ms=1_701,
            )


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ("unknown_field", "intent contract is invalid"),
        ("normalized_type", "intent contract is not an exact canonical value"),
    ),
)
def test_persisted_contract_rejects_unknown_fields_and_type_normalization(
    tmp_path: Path, mutation: str, error: str
) -> None:
    path = tmp_path / f"gateway-contract-{mutation}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, _, request, artifacts = _receipt_fixture(store, tmp_path)
        intent = artifacts.intent
        if mutation == "unknown_field":
            intent["unknown_authority"] = True
        else:
            intent["generation"] = str(intent["generation"])
        tampered = replace(
            artifacts,
            intent_json=canonical_json_text(intent),
        )

        with pytest.raises(ValueError, match=error):
            tampered.validate_for_request(request)


@pytest.mark.parametrize("mutation", ("action_id", "composition_binding"))
def test_runtime_response_rejects_action_or_composition_binding_tampering(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / f"gateway-runtime-{mutation}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, _, request, artifacts = _receipt_fixture(store, tmp_path)
        response = artifacts.runtime_response
        if mutation == "action_id":
            response["runtime"]["action_id"] = "workspace.tampered"
        else:
            response["runtime"]["composition_execution_binding"]["step_id"] = (
                "step-tampered"
            )
        tampered = replace(
            artifacts,
            runtime_response_json=canonical_json_text(response),
        )

        with pytest.raises(
            ValueError, match="runtime response crossed composition authorization"
        ):
            tampered.validate_for_request(request)


@pytest.mark.parametrize("side_effect", ("none", "read"))
def test_ticket_and_grant_accept_only_a0_side_effect_classes(
    tmp_path: Path, side_effect: str
) -> None:
    path = tmp_path / f"gateway-{side_effect}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, _, request, _ = _receipt_fixture(store, tmp_path)
        artifacts = _artifacts(
            request, allowed_side_effects=(side_effect,)
        )
        assert artifacts.validate_for_request(request)["ticket_id"]


@pytest.mark.parametrize(
    ("side_effect", "error"),
    (
        ("verify", "signed ticket contract is invalid"),
        ("local_write", "side-effect ceiling"),
    ),
)
def test_ticket_and_grant_reject_non_side_effect_vocabulary_or_write(
    tmp_path: Path, side_effect: str, error: str
) -> None:
    path = tmp_path / f"gateway-reject-{side_effect}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, _, request, _ = _receipt_fixture(store, tmp_path)
        artifacts = _artifacts(
            request, allowed_side_effects=(side_effect,)
        )
        with pytest.raises(ValueError, match=error):
            artifacts.validate_for_request(request)


def test_null_target_snapshot_stays_null_across_request_and_signed_artifacts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-null-target-snapshot.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, _ = _receipt_fixture(store, tmp_path)
        request = replace(
            request,
            target_snapshot_json="null",
            target_snapshot_sha256=None,
            composition_binding_sha256=ZERO,
            authorization_request_sha256=ZERO,
        )
        request = replace(
            request,
            composition_binding_sha256=_composition_binding(request)[
                "binding_sha256"
            ],
        ).with_computed_sha256()
        artifacts = _artifacts(request)
        record, created = store.commit_composition_step_authorization(
            request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_700
        )
        assert created is True
        assert record.request.target_snapshot is None
        assert record.request.target_snapshot_sha256 is None


def test_logical_request_digest_excludes_issuance_clock_and_loser_artifacts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-stable-retry.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, first_artifacts = _receipt_fixture(store, tmp_path)
        winner, created = store.commit_composition_step_authorization(
            request,
            parent_ticket=parent_ticket,
            artifacts=first_artifacts,
            now_ms=1_700,
        )
        retry = replace(
            request,
            issued_at_ms=1_710,
            expires_at_ms=2_310,
            authorization_request_sha256=ZERO,
        ).with_computed_sha256()
        assert retry.authorization_request_sha256 == (
            request.authorization_request_sha256
        )
        replay, retry_created = store.commit_composition_step_authorization(
            retry,
            parent_ticket=parent_ticket,
            artifacts=_artifacts(retry, nonce_suffix="retry"),
            now_ms=1_710,
        )
        assert retry_created is False
        assert replay == winner


def test_replay_rechecks_expiry_generation_and_action_fence(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        store.commit_composition_step_authorization(
            request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_700
        )
        with pytest.raises(StoreConflictError, match="expired"):
            store.commit_composition_step_authorization(
                request,
                parent_ticket=parent_ticket,
                artifacts=artifacts,
                now_ms=request.expires_at_ms,
            )
        store.increment_action_fence(reason="test-stop", now_ms=1_800)
        with pytest.raises(StoreConflictError, match="action fence"):
            store.commit_composition_step_authorization(
                request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_801
            )
        assert store._connection.execute(
            "SELECT count(*) FROM composition_step_authorization"
        ).fetchone()[0] == 1


def test_replay_and_live_read_reject_clock_rollback_before_issuance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-clock-rollback.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        store.commit_composition_step_authorization(
            request,
            parent_ticket=parent_ticket,
            artifacts=artifacts,
            now_ms=request.issued_at_ms,
        )
        rollback_ms = request.issued_at_ms - 1
        with pytest.raises(StoreConflictError, match="not yet valid"):
            store.commit_composition_step_authorization(
                request,
                parent_ticket=parent_ticket,
                artifacts=artifacts,
                now_ms=rollback_ms,
            )
        with pytest.raises(StoreConflictError, match="not yet valid"):
            store.get_composition_step_authorization(
                request.executable_plan_id,
                request.step_id,
                now_ms=rollback_ms,
            )


def test_two_connections_return_one_durable_winner(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as setup:
        _, _, parent_ticket, request, _ = _receipt_fixture(setup, tmp_path)
    first_store = GatewayStateStore.open(path, now_ms=1_650)
    second_store = GatewayStateStore.open(path, now_ms=1_650)
    try:
        barrier = threading.Barrier(2)

        def commit(store: GatewayStateStore, suffix: str):
            barrier.wait()
            return store.commit_composition_step_authorization(
                request,
                parent_ticket=parent_ticket,
                artifacts=_artifacts(request, nonce_suffix=suffix),
                now_ms=1_700,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                pool.map(
                    lambda pair: commit(*pair),
                    ((first_store, "a"), (second_store, "b")),
                )
            )
        assert sorted(created for _, created in results) == [False, True]
        assert results[0][0] == results[1][0]
        assert results[0][0].runtime_response == results[1][0].runtime_response
        assert first_store._connection.execute(
            "SELECT count(*) FROM composition_step_authorization"
        ).fetchone()[0] == 1
    finally:
        first_store.close()
        second_store.close()


def test_fault_after_insert_rolls_back_without_effect_or_nonce_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        before = _table_counts(store._connection)
        store._connection.execute(
            """
            CREATE TEMP TRIGGER fail_p7c1_authorization_insert
            AFTER INSERT ON composition_step_authorization
            BEGIN
                SELECT RAISE(ABORT, 'injected p7c1 failure');
            END
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected p7c1 failure"):
            store.commit_composition_step_authorization(
                request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_700
            )
        store._connection.execute("DROP TRIGGER fail_p7c1_authorization_insert")
        assert store._connection.execute(
            "SELECT count(*) FROM composition_step_authorization"
        ).fetchone()[0] == 0
        assert _table_counts(store._connection) == before


def test_sql_update_delete_replace_and_upsert_are_denied(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        store.commit_composition_step_authorization(
            request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_700
        )
        row = store._connection.execute(
            "SELECT * FROM composition_step_authorization"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._connection.execute(
                "UPDATE composition_step_authorization SET committed_at_ms=1701"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._connection.execute("DELETE FROM composition_step_authorization")
        columns = tuple(row.keys())
        placeholders = ",".join("?" for _ in columns)
        values = tuple(row[column] for column in columns)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._connection.execute(
                f"INSERT OR REPLACE INTO composition_step_authorization "
                f"({','.join(columns)}) VALUES ({placeholders})",
                values,
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._connection.execute(
                f"INSERT INTO composition_step_authorization "
                f"({','.join(columns)}) VALUES ({placeholders}) "
                "ON CONFLICT(executable_plan_id,step_id,attempt) "
                "DO UPDATE SET committed_at_ms=excluded.committed_at_ms",
                values,
            )


def test_health_and_recovery_detect_payload_tampering(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        _, _, parent_ticket, request, artifacts = _receipt_fixture(store, tmp_path)
        expected, _ = store.commit_composition_step_authorization(
            request, parent_ticket=parent_ticket, artifacts=artifacts, now_ms=1_700
        )
        recovered = store.recover_live_composition_step_authorizations(now_ms=1_701)
        assert recovered == (expected.as_recovered(),)

        trigger_name = "composition_step_authorization_immutable_update_guard"
        trigger_sql = store._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
            (trigger_name,),
        ).fetchone()[0]
        store._connection.execute(f"DROP TRIGGER {trigger_name}")
        store._connection.execute(
            "UPDATE composition_step_authorization "
            "SET runtime_response_json=?",
            (canonical_json_text({"status": "TAMPERED"}),),
        )
        store._connection.execute(trigger_sql)
        assert not store.health_check(now_ms=1_702, full=True).healthy


def test_v31_to_current_is_additive_and_fresh_health_is_current(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as fresh:
        assert STORE_SCHEMA_VERSION == 33
        assert fresh.health_check(now_ms=1_001, full=True).healthy
        before = {
            row[0]
            for row in fresh._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        downgrade_v33_to_v32(connection)
        for trigger in (
            "composition_step_authorization_identity_insert_guard",
            "composition_step_authorization_immutable_update_guard",
            "composition_step_authorization_immutable_delete_guard",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute(
            "DROP INDEX IF EXISTS composition_step_authorization_request_idx"
        )
        connection.execute("DROP TABLE composition_step_authorization")
        connection.execute("DELETE FROM schema_migrations WHERE version=32")
        connection.execute("PRAGMA user_version=31")
    finally:
        connection.close()
    with GatewayStateStore.open(path, now_ms=1_100) as upgraded:
        after = {
            row[0]
            for row in upgraded._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert before - {"composition_step_authorization"} <= after
        assert "composition_step_authorization" in after
        assert upgraded.health_check(now_ms=1_101, full=True).healthy
