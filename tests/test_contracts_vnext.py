"""Contracts vNext freeze coverage: provenance, risk floor chain, consumer order, cutover."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

import contracts
from contracts import (
    ActionImpact,
    ActionIntent,
    ExecutionAuthorizationError,
    ExecutionTicketPayload,
    OmniCapabilityGrantPayload,
    PolicyDecision,
    ResourceEnvelope,
    SourceRef,
    authorize_execution_contract,
    canonical_sha256,
)
from contracts.cutover import (
    ExecutionContractCutoverSnapshot,
    ExecutionContractDrainEvidence,
    activate_execution_contract_epoch,
    apply_execution_contract_drain,
    apply_terminal_fence,
    begin_execution_contract_cutover,
    build_execution_contract_drain_evidence,
    derive_execution_contract_cutover_id,
    pin_old_head,
)
from contracts.life import LIFE_CONTRACT_SCHEMA_VERSION
from contracts.models import SCHEMA_VERSION
from tests.test_execution_contracts import (
    HASH_A,
    HASH_B,
    HASH_C,
    HASH_D,
    capability_manifest,
    execution_ticket,
    regrant,
    vnext_chain,
)
from total_gateway.effects import EffectClaim


HASH_E = "e" * 64
HASH_F = "f" * 64
SOURCE_TYPES = (
    "CURRENT_USER_INSTRUCTION",
    "PREAUTHORIZED_USER_FACT",
    "AUTHENTICATED_DIRECTORY",
    "EXTERNAL_DATA",
    "TOOL_DATA",
)


def source_ref(source_type="CURRENT_USER_INSTRUCTION", **overrides):
    values = {
        "source_type": source_type,
        "object_id": "lev_" + "3" * 64,
        "object_revision": 1,
        "sha256": HASH_A,
    }
    values.update(overrides)
    return SourceRef(**values)


def intent_for(**overrides):
    values = {
        "intent_id": "intent_001",
        "source": "chat",
        "life_id": "life_main",
        "principal_scope_hash": HASH_A,
        "conversation_scope_hash": HASH_B,
        "request_id": "req_" + "1" * 64,
        "run_id": "run_" + "2" * 64,
        "generation": 0,
        "action_id": "docx.create",
        "action_version": "1.0.0",
        "arguments_sha256": HASH_C,
        "workspace_id": "workspace_001",
        "workspace_scope_hash": HASH_D,
        "requested_side_effects": ("local_write",),
        "requested_resources": ResourceEnvelope(
            max_runtime_ms=30_000,
            max_output_bytes=1_000_000,
            max_tool_calls=3,
        ),
        "source_refs": (source_ref(),),
        "created_at_ms": 10_000,
        "expires_at_ms": 60_000,
        "intent_sha256": "0" * 64,
    }
    values.update(overrides)
    return ActionIntent(**values)


def authorize(ticket, manifest, extras, **overrides):
    kwargs = {
        "signature_verified": True,
        "now_ms": 20_000,
        "expected_gateway_epoch": 3,
        "minimum_generation": 2,
        **extras,
    }
    kwargs.update(overrides)
    return authorize_execution_contract(ticket, manifest, **kwargs)


class SourceRefTests(unittest.TestCase):
    def test_all_five_provenance_source_types_are_accepted(self) -> None:
        for source_type in SOURCE_TYPES:
            with self.subTest(source_type=source_type):
                self.assertEqual(source_ref(source_type).source_type, source_type)

    def test_unknown_source_type_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            source_ref("MODEL_CLAIM")

    def test_span_is_all_or_none_and_ordered(self) -> None:
        with self.assertRaises(ValidationError):
            source_ref(span_start=4)
        with self.assertRaises(ValidationError):
            source_ref(span_end=4)
        with self.assertRaises(ValidationError):
            source_ref(span_start=9, span_end=4)
        anchored = source_ref(span_start=4, span_end=9)
        self.assertEqual(anchored.sort_key(), ("CURRENT_USER_INSTRUCTION", "lev_" + "3" * 64, 1, HASH_A, 4, 9))
        self.assertEqual(source_ref().sort_key()[-2:], (-1, -1))


class ActionIntentVNextTests(unittest.TestCase):
    def test_schema_version_is_v2_and_digests_autofill(self) -> None:
        intent = intent_for().with_computed_sha256()
        self.assertEqual(intent.schema_version, "tiangong.gateway.contracts.v2")
        self.assertEqual(
            intent.source_set_sha256,
            canonical_sha256([item.model_dump(mode="json") for item in intent.source_refs]),
        )
        self.assertEqual(
            intent.canonical_invocation_sha256,
            canonical_sha256(
                {
                    "action_id": "docx.create",
                    "action_version": "1.0.0",
                    "payload_sha256": "0" * 64,
                    "target_ref": None,
                    "workspace_id": "workspace_001",
                }
            ),
        )
        self.assertTrue(intent.has_valid_sha256())

    def test_invocation_digest_binds_payload_and_target(self) -> None:
        intent = intent_for(payload_sha256=HASH_E, target_ref="doc_001").with_computed_sha256()
        self.assertEqual(
            intent.canonical_invocation_sha256,
            canonical_sha256(
                {
                    "action_id": "docx.create",
                    "action_version": "1.0.0",
                    "payload_sha256": HASH_E,
                    "target_ref": "doc_001",
                    "workspace_id": "workspace_001",
                }
            ),
        )

    def test_tampered_recomputed_digests_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            intent_for(source_set_sha256=HASH_E)
        with self.assertRaises(ValidationError):
            intent_for(canonical_invocation_sha256=HASH_E)

    def test_source_refs_are_sorted_and_unique_by_frozen_key(self) -> None:
        first = source_ref(object_revision=1)
        second = source_ref(object_revision=2)
        intent = intent_for(source_refs=(first, second))
        self.assertEqual(len(intent.source_refs), 2)
        with self.assertRaises(ValidationError):
            intent_for(source_refs=(second, first))
        with self.assertRaises(ValidationError):
            intent_for(source_refs=(first, first))

    def test_life_snapshot_binding_is_all_or_none(self) -> None:
        with self.assertRaises(ValidationError):
            intent_for(life_snapshot_revision=4)
        with self.assertRaises(ValidationError):
            intent_for(life_snapshot_sha256=HASH_E)
        bound = intent_for(life_snapshot_revision=4, life_snapshot_sha256=HASH_E)
        self.assertEqual(bound.life_snapshot_revision, 4)

    def test_life_scheduler_intents_must_bind_a_life_snapshot(self) -> None:
        with self.assertRaises(ValidationError):
            intent_for(source="life_scheduler")
        scheduled = intent_for(
            source="life_scheduler",
            life_snapshot_revision=4,
            life_snapshot_sha256=HASH_E,
        )
        self.assertEqual(scheduled.source, "life_scheduler")

    def test_vold_evidence_view_and_self_hash_coverage(self) -> None:
        intent = intent_for().with_computed_sha256()
        self.assertEqual(intent.source_evidence_refs, ("lev_" + "3" * 64,))
        drifted = intent.model_copy(update={"payload_sha256": HASH_E})
        self.assertFalse(drifted.has_valid_sha256())


class ActionImpactVNextTests(unittest.TestCase):
    def _impact(self, **overrides):
        values = {
            "impact_id": "impact_001",
            "life_id": "life_main",
            "action_id": "docx.create",
            "intent_sha256": HASH_E,
            "dynamic_risk": "A2",
            "target_snapshot_sha256": HASH_F,
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
        values.update(overrides)
        return ActionImpact(**values)

    def test_life_schema_v4_and_new_fields_enter_self_hash(self) -> None:
        impact = self._impact().with_computed_impact_sha256()
        self.assertEqual(impact.schema_version, "tiangong.life.contracts.v4")
        self.assertTrue(impact.has_valid_impact_sha256())
        drifted = impact.model_copy(update={"dynamic_risk": "A5"})
        self.assertFalse(drifted.has_valid_impact_sha256())

    def test_new_fields_default_for_vold_producers(self) -> None:
        values = self._impact().model_dump(mode="python")
        for removed in ("intent_sha256", "dynamic_risk", "target_snapshot_sha256"):
            values.pop(removed)
        impact = ActionImpact(**values)
        self.assertEqual(
            (impact.intent_sha256, impact.dynamic_risk, impact.target_snapshot_sha256),
            ("0" * 64, "A0", None),
        )

    def test_rollback_claim_validator_is_unchanged(self) -> None:
        with self.assertRaises(ValidationError):
            self._impact(irreversibility_milli=800, rollback_proof_ref="proof_001")


class PolicyDecisionVNextTests(unittest.TestCase):
    def test_coverage_binding_and_unchanged_outcome_rules(self) -> None:
        manifest = capability_manifest()
        _, _, extras = vnext_chain(manifest)
        decision = extras["decision"]
        self.assertEqual(decision.policy_coverage_version, "coverage_001")
        self.assertEqual(decision.policy_coverage_sha256, HASH_A)
        self.assertTrue(decision.has_valid_sha256())
        with self.assertRaises(ValidationError):
            PolicyDecision(
                **{
                    **decision.model_dump(mode="python"),
                    "computed_risk": "A5",
                    "outcome": "ALLOW",
                }
            )


class ExecutionTicketVNextTests(unittest.TestCase):
    def test_contract_version_3_and_new_field_defaults(self) -> None:
        ticket = execution_ticket()
        payload = ticket.payload
        self.assertEqual(payload.contract_version, 3)
        self.assertEqual(payload.intent_id, "unspecified")
        self.assertEqual(payload.intent_sha256, "0" * 64)
        self.assertEqual(payload.canonical_invocation_sha256, "0" * 64)
        self.assertEqual(payload.policy_coverage_sha256, "0" * 64)
        self.assertEqual(payload.claim_sha256, "0" * 64)
        self.assertEqual(payload.claim_revision, 1)
        self.assertEqual(payload.claim_lease_epoch, 1)
        self.assertEqual(payload.fence_epoch, 1)
        self.assertEqual(ticket.header.schema_version, "tiangong.gateway.contracts.v2")

    def test_explicit_claim_and_fence_fields(self) -> None:
        payload = ExecutionTicketPayload(
            **{
                **execution_ticket().payload.model_dump(mode="python"),
                "claim_revision": 4,
                "claim_lease_epoch": 9,
                "fence_epoch": 12,
            }
        )
        self.assertEqual(
            (payload.claim_revision, payload.claim_lease_epoch, payload.fence_epoch),
            (4, 9, 12),
        )


class OmniCapabilityGrantVNextTests(unittest.TestCase):
    def test_new_fields_default_and_authority_rules_unchanged(self) -> None:
        _, _, extras = vnext_chain()
        payload = extras["grant"].payload
        self.assertEqual(payload.request_id, extras["intent"].request_id)
        self.assertEqual(payload.effect_id, extras["claim"].effect_id)
        self.assertEqual(payload.conversation_scope_hash, HASH_B)
        with self.assertRaises(ValidationError):
            OmniCapabilityGrantPayload(
                **{**payload.model_dump(mode="python"), "risk_class": "A5"}
            )
        with self.assertRaises(ValidationError):
            OmniCapabilityGrantPayload(
                **{**payload.model_dump(mode="python"), "expires_at_ms": 70_001}
            )


class EffectClaimVNextTests(unittest.TestCase):
    def test_revision_chain_matches_supersedes_link(self) -> None:
        _, _, extras = vnext_chain()
        claim = extras["claim"]
        self.assertEqual(
            (claim.pipeline_version, claim.attempt, claim.claim_revision, claim.lease_epoch),
            ("pipeline_001", 1, 1, 7),
        )
        self.assertIsNone(claim.supersedes_claim_sha256)
        base = claim.model_dump(mode="python")
        with self.assertRaises(ValidationError):
            EffectClaim(**{**base, "supersedes_claim_sha256": HASH_A})
        with self.assertRaises(ValidationError):
            EffectClaim(**{**base, "claim_revision": 2})
        renewed = EffectClaim(
            **{
                **base,
                "claim_revision": 2,
                "supersedes_claim_sha256": claim.claim_sha256,
                "claim_sha256": "0" * 64,
            }
        ).with_computed_sha256()
        self.assertTrue(renewed.has_valid_sha256())

    def test_effect_identity_derivation_is_unchanged(self) -> None:
        _, _, extras = vnext_chain()
        base = extras["claim"].model_dump(mode="python")
        with self.assertRaises(ValidationError):
            EffectClaim(**{**base, "effect_id": "eff_" + "9" * 64})


class ConsumerAuthorizationVNextTests(unittest.TestCase):
    def test_happy_path_authorizes_the_exact_action(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        action = authorize(ticket, manifest, extras)
        self.assertEqual(action.action_id, "docx.create")

    def test_step1_rejects_mixed_contract_epochs(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        vold_intent = extras["intent"].model_copy(
            update={"schema_version": "tiangong.gateway.contracts.v1", "intent_sha256": "0" * 64}
        ).with_computed_sha256()
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, intent=vold_intent)
        self.assertEqual(caught.exception.code, "ticket.contract_version.unsupported")

    def test_step5_rejects_grant_ticket_drift(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        forged = extras["grant"].model_copy(
            update={
                "payload": extras["grant"].payload.model_copy(
                    update={"ticket_sha256": "0" * 64}
                )
            }
        )
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, grant=forged)
        self.assertEqual(caught.exception.code, "grant.ticket.mismatch")

    def test_step6_and_step7_reject_grant_scope_drift(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        for field, value in (("generation", 99), ("workspace_scope_hash", HASH_A)):
            with self.subTest(field=field):
                forged = extras["grant"].model_copy(
                    update={
                        "payload": extras["grant"].payload.model_copy(update={field: value})
                    }
                )
                with self.assertRaises(ExecutionAuthorizationError) as caught:
                    authorize(ticket, manifest, extras, grant=forged)
                self.assertEqual(caught.exception.code, "grant.scope.mismatch")

    def test_step8_rejects_a_broken_hash_chain(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        drifted_decision = extras["decision"].model_copy(
            update={"reason_codes": ("policy.drifted",)}
        )
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, decision=drifted_decision)
        self.assertEqual(caught.exception.code, "ticket.hash_chain.mismatch")

        ticket2, manifest2, extras2 = vnext_chain(intent_sha256=HASH_F)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket2, manifest2, extras2)
        self.assertEqual(caught.exception.code, "ticket.hash_chain.mismatch")

    def test_step9_rejects_invocation_drift(self) -> None:
        ticket, manifest, extras = vnext_chain(canonical_invocation_sha256=HASH_F)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras)
        self.assertEqual(caught.exception.code, "ticket.invocation.mismatch")

    def test_step10_rejects_policy_and_coverage_drift(self) -> None:
        for field, expected in (
            ("policy_snapshot_hash", "ticket.policy_snapshot.mismatch"),
            ("policy_coverage_sha256", "ticket.policy_coverage.mismatch"),
        ):
            with self.subTest(field=field):
                ticket, manifest, extras = vnext_chain(**{field: HASH_F})
                with self.assertRaises(ExecutionAuthorizationError) as caught:
                    authorize(ticket, manifest, extras)
                self.assertEqual(caught.exception.code, expected)

    def test_step12_rejects_a_stale_fence_epoch(self) -> None:
        ticket, manifest, extras = vnext_chain()
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, expected_fence_epoch=6)
        self.assertEqual(caught.exception.code, "ticket.fence_epoch.stale")

    def test_step15_rejects_replayed_nonce(self) -> None:
        ticket, manifest, extras = vnext_chain()
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, nonce_already_consumed=True)
        self.assertEqual(caught.exception.code, "ticket.nonce.replay")

    def test_step16_rejects_claim_drift_and_stale_lease(self) -> None:
        ticket, manifest, extras = vnext_chain()
        drifted_claim = extras["claim"].model_copy(update={"claimed_at_ms": 21_000})
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, claim=drifted_claim)
        self.assertEqual(caught.exception.code, "ticket.claim.mismatch")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, active_lease_epoch=8)
        self.assertEqual(caught.exception.code, "ticket.claim.lease_epoch.stale")

    def test_step17_anchors_the_target_snapshot(self) -> None:
        ticket, manifest, extras = vnext_chain(
            intent_overrides={
                "target_ref": "doc_001",
                "target_snapshot_sha256": HASH_F,
            }
        )
        action = authorize(
            ticket, manifest, extras, expected_target_snapshot_sha256=HASH_F
        )
        self.assertEqual(action.action_id, "docx.create")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, expected_target_snapshot_sha256=HASH_E)
        self.assertEqual(caught.exception.code, "ticket.target.version_mismatch")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras)
        self.assertEqual(caught.exception.code, "ticket.target.version_mismatch")

    def test_step20_enforces_the_manifest_risk_floor(self) -> None:
        ticket, manifest, extras = vnext_chain(risk_class="A1")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras)
        self.assertEqual(caught.exception.code, "ticket.risk_class.below_floor")

    def test_step21_enforces_the_final_decision_risk(self) -> None:
        ticket, manifest, extras = vnext_chain(risk_class="A3")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras)
        self.assertEqual(caught.exception.code, "ticket.risk_class.decision_mismatch")

        ticket2, manifest2, extras2 = vnext_chain(
            impact_overrides={"dynamic_risk": "A3"},
        )
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket2, manifest2, extras2)
        self.assertEqual(caught.exception.code, "ticket.risk_class.decision_mismatch")

    def test_step24_binds_the_executed_arguments(self) -> None:
        ticket, manifest, extras = vnext_chain()
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, actual_arguments_sha256=HASH_F)
        self.assertEqual(caught.exception.code, "ticket.arguments.mismatch")

    def test_consumer_order_is_fixed_and_fail_closed(self) -> None:
        manifest = capability_manifest()
        ticket, _, extras = vnext_chain(manifest)
        # step 3 (signature) precedes step 5 (grant/ticket binding)
        forged = regrant(ticket, extras, ticket_sha256="0" * 64)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(
                ticket, manifest, extras, grant=forged, signature_verified=False
            )
        self.assertEqual(caught.exception.code, "ticket.signature.unverified")
        # step 11 (gateway epoch) precedes step 13 (generation fence)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(
                ticket,
                manifest,
                extras,
                expected_gateway_epoch=4,
                minimum_generation=99,
            )
        self.assertEqual(caught.exception.code, "ticket.gateway_epoch.mismatch")
        # step 12 (fence epoch) precedes step 14 (time window)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(ticket, manifest, extras, expected_fence_epoch=6, now_ms=70_001)
        self.assertEqual(caught.exception.code, "ticket.fence_epoch.stale")
        # step 20 (floor) precedes step 21 (decision final risk): an A1 ticket is
        # below the A2 manifest floor and also below the A2 decision; the floor
        # code must win.
        low_ticket, low_manifest, low_extras = vnext_chain(risk_class="A1")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            authorize(low_ticket, low_manifest, low_extras)
        self.assertEqual(caught.exception.code, "ticket.risk_class.below_floor")


class ExecutionContractCutoverTests(unittest.TestCase):
    def _evidence(self, gateway_epoch=3, observed_at_ms=2_000, **overrides):
        values = {
            "gateway_epoch": gateway_epoch,
            "effect_ledger_sha256": HASH_A,
            "state_ledger_sha256": HASH_B,
            "ticket_ledger_sha256": HASH_C,
            "observed_at_ms": observed_at_ms,
        }
        values.update(overrides)
        return build_execution_contract_drain_evidence(**values)

    def test_five_state_sequence_pins_old_head_and_activates(self) -> None:
        snapshot = begin_execution_contract_cutover(gateway_epoch=3, started_at_ms=1_000)
        self.assertEqual((snapshot.state, snapshot.revision), ("FENCING", 1))
        self.assertIsNone(snapshot.drain_evidence_id)
        self.assertTrue(snapshot.has_valid_sha256())

        evidence = self._evidence()
        drained = apply_execution_contract_drain(snapshot, evidence)
        self.assertEqual((drained.state, drained.revision), ("DRAINED", 2))
        self.assertEqual(drained.drain_evidence_sha256, evidence.evidence_sha256)

        pinned = pin_old_head(drained, evidence)
        self.assertEqual((pinned.state, pinned.revision), ("HEAD_PINNED", 3))
        self.assertEqual(pinned.old_head_sha256, HASH_B)

        fenced = apply_terminal_fence(pinned, ("evt_002", "evt_001", "evt_001"))
        self.assertEqual((fenced.state, fenced.revision), ("TERMINAL_FENCED", 4))
        self.assertEqual(
            fenced.terminal_fence_event_ids_sha256,
            canonical_sha256(["evt_001", "evt_002"]),
        )

        active = activate_execution_contract_epoch(fenced, activated_at_ms=3_000)
        self.assertEqual((active.state, active.revision), ("ACTIVE", 5))
        self.assertEqual(active.activated_at_ms, 3_000)
        self.assertEqual(active.old_head_sha256, HASH_B)
        self.assertTrue(active.has_valid_sha256())

    def test_drain_evidence_identity_and_counts_are_fail_closed(self) -> None:
        evidence = self._evidence()
        with self.assertRaises(ValidationError):
            ExecutionContractDrainEvidence(
                **{**evidence.model_dump(mode="python"), "inflight_execution_count": 1}
            )
        self.assertTrue(evidence.has_valid_sha256())
        drifted = evidence.model_copy(update={"observed_at_ms": 9_999})
        self.assertFalse(drifted.has_valid_sha256())
        snapshot = begin_execution_contract_cutover(gateway_epoch=3, started_at_ms=1_000)
        with self.assertRaises(ValueError):
            apply_execution_contract_drain(snapshot, drifted)
        with self.assertRaises(ValueError):
            apply_execution_contract_drain(snapshot, self._evidence(gateway_epoch=4))
        early = self._evidence(observed_at_ms=500)
        with self.assertRaises(ValueError):
            apply_execution_contract_drain(snapshot, early)

    def test_cutover_steps_reject_out_of_order_transitions(self) -> None:
        snapshot = begin_execution_contract_cutover(gateway_epoch=3, started_at_ms=1_000)
        evidence = self._evidence()
        with self.assertRaises(ValueError):
            pin_old_head(snapshot, evidence)
        with self.assertRaises(ValueError):
            apply_terminal_fence(snapshot, ("evt_001",))
        with self.assertRaises(ValueError):
            activate_execution_contract_epoch(snapshot, activated_at_ms=2_000)
        drained = apply_execution_contract_drain(snapshot, evidence)
        with self.assertRaises(ValueError):
            apply_execution_contract_drain(drained, evidence)
        with self.assertRaises(ValueError):
            activate_execution_contract_epoch(drained, activated_at_ms=2_000)
        pinned = pin_old_head(drained, evidence)
        with self.assertRaises(ValueError):
            activate_execution_contract_epoch(pinned, activated_at_ms=2_000)
        fenced = apply_terminal_fence(pinned, ())
        self.assertEqual(fenced.terminal_fence_event_ids_sha256, canonical_sha256([]))
        with self.assertRaises(ValueError):
            activate_execution_contract_epoch(fenced, activated_at_ms=1_000)

    def test_snapshot_revision_and_version_literals_are_pinned(self) -> None:
        snapshot = begin_execution_contract_cutover(gateway_epoch=3, started_at_ms=1_000)
        base = snapshot.model_dump(mode="python")
        with self.assertRaises(ValidationError):
            ExecutionContractCutoverSnapshot(**{**base, "revision": 2})
        with self.assertRaises(ValidationError):
            ExecutionContractCutoverSnapshot(
                **{**base, "from_schema_version": "tiangong.gateway.contracts.v2"}
            )
        evidence = self._evidence().model_dump(mode="python")
        with self.assertRaises(ValidationError):
            ExecutionContractDrainEvidence(
                **{**evidence, "to_schema_version": "tiangong.gateway.contracts.v3"}
            )

    def test_cutover_id_is_domain_separated_and_epoch_bound(self) -> None:
        first = derive_execution_contract_cutover_id(
            "tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2", 3
        )
        self.assertTrue(first.startswith("cut_"))
        other_epoch = derive_execution_contract_cutover_id(
            "tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2", 4
        )
        self.assertNotEqual(first, other_epoch)


class ContractEpochTests(unittest.TestCase):
    def test_single_shared_version_atom(self) -> None:
        self.assertEqual(contracts.CONTRACT_SCHEMA_VERSION, "tiangong.gateway.contracts.v2")
        self.assertEqual(SCHEMA_VERSION, contracts.CONTRACT_SCHEMA_VERSION)
        self.assertEqual(LIFE_CONTRACT_SCHEMA_VERSION, "tiangong.life.contracts.v4")


if __name__ == "__main__":
    unittest.main()
