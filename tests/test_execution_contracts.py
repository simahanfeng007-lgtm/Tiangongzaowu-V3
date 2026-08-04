import unittest

from pydantic import ValidationError

from contracts import (
    ActionImpact,
    ActionIntent,
    CapabilityAction,
    CapabilityManifest,
    ExecutionAuthorizationError,
    ExecutionResult,
    ExecutionTicket,
    ExecutionTicketHeader,
    ExecutionTicketPayload,
    FactRecord,
    ObjectGrant,
    OmniCapabilityGrant,
    OmniCapabilityGrantHeader,
    OmniCapabilityGrantPayload,
    PolicyDecision,
    ResourceEnvelope,
    SourceRef,
    authorize_execution_contract,
    canonical_json_bytes,
    canonical_sha256,
    derive_effect_identity,
    derive_run_identity,
)
from total_gateway.effects import EffectClaim


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
EFFECT_ID = "eff_" + "3" * 64


def capability_action(**overrides):
    values = {
        "action_id": "docx.create",
        "version": "1.0.0",
        "provider_component_id": "tiangong-backend",
        "argument_schema_sha256": HASH_A,
        "result_schema_sha256": HASH_B,
        "risk_class": "A2",
        "allowed_side_effects": ("local_write",),
        "idempotency_mode": "effect_id_required",
        "max_runtime_ms": 60_000,
        "max_output_bytes": 10_000_000,
        "max_tool_calls": 1,
        "available": True,
    }
    values.update(overrides)
    return CapabilityAction(**values)


def capability_manifest(**overrides):
    values = {
        "manifest_id": "capability_manifest_001",
        "revision": 1,
        "generated_at_ms": 1_784_010_685_000,
        "component_manifest_hash": HASH_C,
        "actions": (capability_action(),),
        "sha256": HASH_D,
    }
    values.update(overrides)
    return CapabilityManifest(**values).with_computed_sha256()


def object_grant(**overrides):
    values = {
        "object_id": "attachment_001",
        "revision": 1,
        "sha256": HASH_A,
        "size_bytes": 1024,
        "mime": "application/pdf",
        "tenant_id": "tenant_001",
        "link_account_id": "wechat_001",
        "conversation_scope_hash": HASH_B,
    }
    values.update(overrides)
    return ObjectGrant(**values)


def execution_ticket(manifest=None, **payload_overrides):
    manifest = manifest or capability_manifest()
    values = {
        "ticket_id": "ticket_001",
        "nonce": "nonce_001",
        "issued_at_ms": 10_000,
        "not_before_ms": 10_000,
        "expires_at_ms": 70_000,
        "gateway_epoch": 3,
        "request_id": REQUEST_ID,
        "run_id": RUN_ID,
        "generation": 2,
        "effect_id": EFFECT_ID,
        "channel": "wechat",
        "tenant_id": "tenant_001",
        "link_account_id": "wechat_001",
        "conversation_scope_hash": HASH_B,
        "principal_scope_hash": HASH_C,
        "capability_manifest_hash": manifest.sha256,
        "policy_snapshot_hash": HASH_D,
        "decision_id": "policy_decision_001",
        "decision_sha256": HASH_A,
        "impact_id": "impact_001",
        "impact_sha256": HASH_B,
        "action_permission_sha256": HASH_C,
        "component_manifest_hash": manifest.component_manifest_hash,
        "life_snapshot_revision": 4,
        "life_snapshot_hash": HASH_A,
        "risk_class": "A2",
        "action_id": "docx.create",
        "action_version": "1.0.0",
        "argument_schema_sha256": HASH_A,
        "arguments_hash": HASH_B,
        "workspace_id": "workspace_001",
        "input_objects": (object_grant(),),
        "object_grants_sha256": HASH_D,
        "output_root_id": "workspace_output_001",
        "artifact_intent_id": "artifact_intent_001",
        "max_output_bytes": 5_000_000,
        "max_runtime_ms": 30_000,
        "max_tool_calls": 1,
        "resource_envelope_sha256": HASH_D,
        "allowed_side_effects": ("local_write",),
        "side_effect_envelope_sha256": HASH_D,
        "skill_id": "word_delivery",
        "skill_version": "3.0.0",
        "skill_sha256": HASH_C,
        "skill_activation_id": "skill_activation_001",
        "skill_activation_sha256": HASH_D,
    }
    values.update(payload_overrides)
    if "object_grants_sha256" not in payload_overrides:
        values["object_grants_sha256"] = canonical_sha256(
            [item.model_dump(mode="json") for item in values["input_objects"]]
        )
    if "resource_envelope_sha256" not in payload_overrides:
        values["resource_envelope_sha256"] = canonical_sha256(
            {
                "max_output_bytes": values["max_output_bytes"],
                "max_runtime_ms": values["max_runtime_ms"],
                "max_tool_calls": values["max_tool_calls"],
            }
        )
    if "side_effect_envelope_sha256" not in payload_overrides:
        values["side_effect_envelope_sha256"] = canonical_sha256(
            {"allowed_side_effects": list(values["allowed_side_effects"])}
        )
    return ExecutionTicket(
        header=ExecutionTicketHeader(kid="execution_key_001"),
        payload=ExecutionTicketPayload(**values),
        signature="A" * 86,
    )


def vnext_chain(
    manifest=None,
    *,
    intent_overrides=None,
    impact_overrides=None,
    decision_overrides=None,
    claim_overrides=None,
    **ticket_overrides,
):
    """Build a coherent vNext intent->impact->decision->claim->ticket->grant chain."""

    manifest = manifest or capability_manifest()
    run_id = derive_run_identity(REQUEST_ID, 1).run_id
    intent_values = {
        "intent_id": "intent_001",
        "source": "chat",
        "life_id": "life_main",
        "principal_scope_hash": HASH_C,
        "conversation_scope_hash": HASH_B,
        "request_id": REQUEST_ID,
        "run_id": run_id,
        "generation": 2,
        "action_id": "docx.create",
        "action_version": "1.0.0",
        "arguments_sha256": HASH_B,
        "workspace_id": "workspace_001",
        "workspace_scope_hash": HASH_D,
        "requested_side_effects": ("local_write",),
        "requested_resources": ResourceEnvelope(
            max_runtime_ms=30_000,
            max_output_bytes=5_000_000,
            max_tool_calls=1,
        ),
        "source_refs": (
            SourceRef(
                source_type="CURRENT_USER_INSTRUCTION",
                object_id="lev_" + "3" * 64,
                object_revision=1,
                sha256=HASH_A,
            ),
        ),
        "created_at_ms": 10_000,
        "expires_at_ms": 60_000,
        "intent_sha256": "0" * 64,
    }
    intent_values.update(intent_overrides or {})
    intent = ActionIntent(**intent_values).with_computed_sha256()
    impact_values = {
        "impact_id": "impact_001",
        "life_id": "life_main",
        "action_id": "docx.create",
        "intent_sha256": intent.intent_sha256,
        "dynamic_risk": "A1",
        "target_snapshot_sha256": intent.target_snapshot_sha256,
        "touches_identity": False,
        "touches_soul": False,
        "touches_memory_keys": False,
        "touches_policy": False,
        "touches_core_code": False,
        "workspace_scope_milli": 300,
        "external_recipient_count": 0,
        "credential_scope_milli": 0,
        "privacy_scope_milli": 0,
        "blast_radius_milli": 0,
        "irreversibility_milli": 0,
        "uncertainty_milli": 0,
        "estimated_resource_cost_milli": 100,
        "source_event_ids": ("lev_" + "3" * 64,),
        "created_at_ms": 20_000,
        "impact_sha256": "0" * 64,
    }
    impact_values.update(impact_overrides or {})
    impact = ActionImpact(**impact_values).with_computed_impact_sha256()
    decision_values = {
        "decision_id": "policy_decision_001",
        "intent_sha256": intent.intent_sha256,
        "impact_id": impact.impact_id,
        "impact_sha256": impact.impact_sha256,
        "action_permission_sha256": HASH_C,
        "action_registry_sha256": HASH_D,
        "capability_manifest_hash": manifest.sha256,
        "component_manifest_hash": manifest.component_manifest_hash,
        "policy_snapshot_sha256": HASH_D,
        "policy_coverage_version": "coverage_001",
        "policy_coverage_sha256": HASH_A,
        "computed_risk": "A2",
        "outcome": "ALLOW",
        "reason_codes": ("policy.machine_risk_recomputed",),
        "decided_at_ms": 20_000,
        "decision_sha256": "0" * 64,
    }
    decision_values.update(decision_overrides or {})
    decision = PolicyDecision(**decision_values).with_computed_sha256()
    effect_id = derive_effect_identity(
        request_id=REQUEST_ID,
        run_id=run_id,
        run_sequence=1,
        generation=2,
        effect_kind="execution",
        ordinal=0,
        intent_sha256=intent.intent_sha256,
    ).effect_id
    claim_values = {
        "effect_id": effect_id,
        "request_id": REQUEST_ID,
        "run_id": run_id,
        "run_sequence": 1,
        "generation": 2,
        "effect_kind": "execution",
        "ordinal": 0,
        "intent_sha256": intent.intent_sha256,
        "pipeline_version": "pipeline_001",
        "attempt": 1,
        "claim_revision": 1,
        "lease_epoch": 7,
        "owner_component_id": "tiangong-backend",
        "claimed_at_ms": 20_000,
        "claim_sha256": "0" * 64,
    }
    claim_values.update(claim_overrides or {})
    claim = EffectClaim(**claim_values).with_computed_sha256()
    ticket_values = {
        "effect_id": effect_id,
        "run_id": run_id,
        "intent_id": intent.intent_id,
        "intent_sha256": intent.intent_sha256,
        "canonical_invocation_sha256": intent.canonical_invocation_sha256,
        "policy_snapshot_hash": decision.policy_snapshot_sha256,
        "policy_coverage_sha256": decision.policy_coverage_sha256,
        "decision_id": decision.decision_id,
        "decision_sha256": decision.decision_sha256,
        "impact_id": impact.impact_id,
        "impact_sha256": impact.impact_sha256,
        "claim_sha256": claim.claim_sha256,
        "claim_revision": claim.claim_revision,
        "claim_lease_epoch": claim.lease_epoch,
        "fence_epoch": 5,
        "risk_class": decision.computed_risk,
    }
    ticket_values.update(ticket_overrides)
    ticket = execution_ticket(manifest, **ticket_values)
    grant = OmniCapabilityGrant(
        header=OmniCapabilityGrantHeader(kid="execution_key_001"),
        payload=OmniCapabilityGrantPayload(
            grant_id="omni_grant_001",
            ticket_id=ticket.payload.ticket_id,
            ticket_sha256=canonical_sha256(ticket.payload.model_dump(mode="json")),
            request_id=ticket.payload.request_id,
            run_id=ticket.payload.run_id,
            generation=ticket.payload.generation,
            effect_id=ticket.payload.effect_id,
            decision_id=decision.decision_id,
            decision_sha256=decision.decision_sha256,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=HASH_C,
            action_registry_sha256=HASH_D,
            capability_manifest_hash=manifest.sha256,
            component_manifest_hash=manifest.component_manifest_hash,
            action_id="docx.create",
            action_version="1.0.0",
            arguments_sha256=intent.arguments_sha256,
            workspace_id="workspace_001",
            workspace_scope_hash=intent.workspace_scope_hash,
            principal_scope_hash=HASH_C,
            conversation_scope_hash=HASH_B,
            risk_class=decision.computed_risk,
            allowed_side_effects=("local_write",),
            path_policy="workspace_only",
            allow_absolute_paths=False,
            allow_shell=False,
            allow_python=False,
            gateway_epoch=3,
            nonce="grant_nonce_001",
            issued_at_ms=10_000,
            not_before_ms=10_000,
            expires_at_ms=70_000,
        ),
        signature="A" * 86,
    )
    extras = {
        "grant": grant,
        "intent": intent,
        "decision": decision,
        "impact": impact,
        "claim": claim,
        "expected_fence_epoch": 5,
        "active_lease_epoch": 7,
        "actual_arguments_sha256": ticket.payload.arguments_hash,
    }
    return ticket, manifest, extras


def regrant(ticket, extras, **grant_overrides):
    """Rebind the chain grant to a (possibly rebuilt) ticket payload."""

    grant_values = {
        **extras["grant"].payload.model_dump(mode="python"),
        "ticket_sha256": canonical_sha256(ticket.payload.model_dump(mode="json")),
    }
    grant_values.update(grant_overrides)
    return OmniCapabilityGrant(
        header=extras["grant"].header,
        payload=OmniCapabilityGrantPayload(**grant_values),
        signature=extras["grant"].signature,
    )


class CanonicalJsonTests(unittest.TestCase):
    def test_orders_keys_and_rejects_floats(self) -> None:
        self.assertEqual(canonical_json_bytes({"b": 1, "a": 2}), b'{"a":2,"b":1}')
        with self.assertRaises(TypeError):
            canonical_json_bytes({"score": 0.5})


class CapabilityManifestTests(unittest.TestCase):
    def test_manifest_digest_covers_all_actions(self) -> None:
        manifest = capability_manifest()
        self.assertTrue(manifest.has_valid_sha256())
        changed = manifest.model_copy(
            update={"actions": (capability_action(max_runtime_ms=90_000),)}
        )
        self.assertFalse(changed.has_valid_sha256())

    def test_manifest_actions_must_be_sorted(self) -> None:
        with self.assertRaises(ValidationError):
            capability_manifest(
                actions=(
                    capability_action(action_id="zip.create"),
                    capability_action(action_id="docx.create"),
                )
            )


class ExecutionTicketTests(unittest.TestCase):
    def test_ticket_lifetime_is_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            execution_ticket(expires_at_ms=70_001)

    def test_a4_is_autonomous_legacy_confirmation_is_forbidden_and_a5_is_blocked(self) -> None:
        autonomous = execution_ticket(risk_class="A4")
        self.assertEqual(autonomous.payload.risk_class, "A4")
        with self.assertRaises(ValidationError):
            execution_ticket(
                risk_class="A4",
                confirmation_id="confirmation_001",
                confirmation_sha256=HASH_A,
            )
        with self.assertRaises(ValidationError):
            execution_ticket(risk_class="A5")

    def test_input_object_must_share_ticket_scope(self) -> None:
        with self.assertRaises(ValidationError):
            execution_ticket(input_objects=(object_grant(tenant_id="tenant_002"),))

    def test_skill_binding_is_all_or_none(self) -> None:
        with self.assertRaises(ValidationError):
            execution_ticket(skill_version=None)
        with self.assertRaises(ValidationError):
            execution_ticket(skill_activation_sha256=None)


class StructuralAuthorizationTests(unittest.TestCase):
    def test_authorizes_exact_action_with_verified_signature(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        action = authorize_execution_contract(
            ticket,
            manifest,
            signature_verified=True,
            now_ms=20_000,
            expected_gateway_epoch=3,
            minimum_generation=2,
            **extras,
        )
        self.assertEqual(action.action_id, "docx.create")

    def test_refuses_unverified_signature(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize_execution_contract(
                ticket,
                manifest,
                signature_verified=False,
                now_ms=20_000,
                expected_gateway_epoch=3,
                **extras,
            )
        self.assertEqual(caught.exception.code, "ticket.signature.unverified")

    def test_refuses_expired_or_fenced_ticket(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        for now_ms, epoch, minimum_generation, expected_code in (
            (70_001, 3, 2, "ticket.expired"),
            (20_000, 4, 2, "ticket.gateway_epoch.mismatch"),
            (20_000, 3, 3, "ticket.generation.fenced"),
        ):
            with self.subTest(expected_code=expected_code), self.assertRaises(
                ExecutionAuthorizationError
            ) as caught:
                authorize_execution_contract(
                    ticket,
                    manifest,
                    signature_verified=True,
                    now_ms=now_ms,
                    expected_gateway_epoch=epoch,
                    minimum_generation=minimum_generation,
                    **extras,
                )
            self.assertEqual(caught.exception.code, expected_code)

    def test_refuses_tampered_manifest_or_expanded_limit(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        tampered = manifest.model_copy(update={"revision": 2})
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize_execution_contract(
                ticket,
                tampered,
                signature_verified=True,
                now_ms=20_000,
                expected_gateway_epoch=3,
                **extras,
            )
        self.assertEqual(caught.exception.code, "capability_manifest.digest.invalid")

        expanded, _, expanded_extras = vnext_chain(manifest, max_runtime_ms=60_001)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize_execution_contract(
                expanded,
                manifest,
                signature_verified=True,
                now_ms=20_000,
                expected_gateway_epoch=3,
                **expanded_extras,
            )
        self.assertEqual(caught.exception.code, "ticket.runtime_limit.exceeded")


class ResultAndFactTests(unittest.TestCase):
    def test_valid_success_records_fact_and_receipt(self) -> None:
        result = ExecutionResult(
            result_id="result_001",
            ticket_id="ticket_001",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=2,
            effect_id=EFFECT_ID,
            action_id="docx.create",
            action_version="1.0.0",
            status="SUCCEEDED",
            attempt=1,
            started_at_ms=20_000,
            finished_at_ms=21_000,
            side_effect_started=True,
            result_payload_sha256=HASH_A,
            receipt_sha256=HASH_B,
            output_object_refs=("artifact_001",),
            fact_ids=("fact_001",),
        )
        self.assertEqual(result.status, "SUCCEEDED")

    def test_successful_side_effect_requires_receipt(self) -> None:
        with self.assertRaises(ValidationError):
            ExecutionResult(
                result_id="result_001",
                ticket_id="ticket_001",
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=2,
                effect_id=EFFECT_ID,
                action_id="docx.create",
                action_version="1.0.0",
                status="SUCCEEDED",
                attempt=1,
                started_at_ms=20_000,
                finished_at_ms=21_000,
                side_effect_started=True,
                result_payload_sha256=HASH_A,
                output_object_refs=("artifact_001",),
                fact_ids=("fact_001",),
            )

    def test_retryable_result_cannot_follow_started_side_effect(self) -> None:
        with self.assertRaises(ValidationError):
            ExecutionResult(
                result_id="result_001",
                ticket_id="ticket_001",
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=2,
                effect_id=EFFECT_ID,
                action_id="docx.create",
                action_version="1.0.0",
                status="FAILED_RETRYABLE",
                attempt=1,
                started_at_ms=20_000,
                finished_at_ms=21_000,
                side_effect_started=True,
                result_payload_sha256=HASH_A,
                fact_ids=("fact_001",),
                error_code="backend.timeout",
            )

    def test_fact_is_machine_evidence_and_self_hashes(self) -> None:
        fact = FactRecord(
            fact_id="fact_001",
            fact_type="execution.succeeded",
            source_component_id="tiangong-backend",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=2,
            ticket_id="ticket_001",
            effect_id=EFFECT_ID,
            action_id="docx.create",
            action_version="1.0.0",
            observed_at_ms=21_000,
            payload_sha256=HASH_A,
            evidence_sha256=HASH_B,
            verification_method="component_receipt",
            fact_sha256=HASH_C,
        ).with_computed_sha256()
        self.assertTrue(fact.has_valid_sha256())
        with self.assertRaises(ValidationError):
            FactRecord(**{**fact.model_dump(), "model_generated": True})


if __name__ == "__main__":
    unittest.main()
