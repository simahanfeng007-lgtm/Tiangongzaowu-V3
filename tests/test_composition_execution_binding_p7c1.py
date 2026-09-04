from __future__ import annotations

import unittest
import base64
import os
import tempfile
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from contracts import (
    ActionPermission,
    ActionRegistrySnapshot,
    CompositionExecutionBindingV1,
    ExecutionAuthorizationError,
    OmniCapabilityGrantPayload,
    UserConfirmationGrant,
    canonical_json_bytes,
    canonical_sha256,
    contract_schema_bundle,
    authorize_execution_contract,
)
from total_gateway.grant_signer import CapabilityGrantError, issue_omni_capability_grant
from total_gateway.policy_engine import PolicyEngine
from total_gateway.tickets import TicketSigner
from tests.test_execution_contracts import vnext_chain
from tests.test_omni_capability_guard import CapabilityFixture, load_module


ROOT = Path(__file__).resolve().parents[1]
SOURCE_OMNI_CAPABILITY = ROOT / "src" / "omni_body_skill" / "tools" / "omni_capability.py"


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def composition_binding(**overrides: object) -> CompositionExecutionBindingV1:
    values: dict[str, object] = {
        "executable_plan_id": "execution-plan-001",
        "executable_plan_sha256": HASH_A,
        "step_id": "step-001",
        "step_binding_sha256": HASH_B,
        "request_id": "req_" + "1" * 64,
        "run_id": "run_" + "2" * 64,
        "generation": 2,
        "effect_id": "eff_" + "3" * 64,
        "action_id": "file.read",
        "action_version": "omni-registry-v1",
        "materialized_arguments_sha256": HASH_C,
        "canonical_invocation_sha256": HASH_D,
        "target_sha256": canonical_sha256("notes.txt"),
        "target_snapshot_sha256": None,
        "workspace_id": "workspace-main",
        "workspace_scope_hash": HASH_A,
        "binding_sha256": "0" * 64,
    }
    values.update(overrides)
    return CompositionExecutionBindingV1(**values).with_computed_sha256()


class CompositionExecutionBindingContractTests(unittest.TestCase):
    def test_digest_covers_every_execution_coordinate(self) -> None:
        binding = composition_binding()
        self.assertTrue(binding.has_valid_sha256())
        for field, replacement in (
            ("executable_plan_sha256", HASH_B),
            ("step_binding_sha256", HASH_C),
            ("generation", 3),
            ("effect_id", "eff_" + "4" * 64),
            ("action_id", "file.stat"),
            ("materialized_arguments_sha256", HASH_D),
            ("target_sha256", HASH_D),
            ("workspace_id", "workspace-other"),
        ):
            with self.subTest(field=field):
                drifted = binding.model_copy(update={field: replacement})
                self.assertFalse(drifted.has_valid_sha256())

    def test_contract_is_strict_and_none_is_not_serialized(self) -> None:
        binding = composition_binding()
        self.assertNotIn("target_snapshot_sha256", binding.model_dump(mode="json"))
        with self.assertRaises(ValidationError):
            CompositionExecutionBindingV1(
                **{**binding.model_dump(mode="python"), "unknown_authority": True}
            )

    def test_legacy_hosts_do_not_serialize_a_null_composition_field(self) -> None:
        ticket, _, extras = vnext_chain()
        self.assertNotIn("composition_execution_binding", ticket.payload.model_dump(mode="json"))
        self.assertNotIn("composition_execution_binding", extras["intent"].model_dump(mode="json"))
        self.assertNotIn("composition_execution_binding", extras["decision"].model_dump(mode="json"))
        self.assertNotIn("composition_execution_binding", extras["grant"].payload.model_dump(mode="json"))

    def test_schema_bundle_exports_the_standalone_binding_contract(self) -> None:
        schema = contract_schema_bundle()["CompositionExecutionBindingV1"]
        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            "tiangong.composition-execution-binding.v1",
        )
        self.assertIn("step_binding_sha256", schema["required"])

    def test_intent_ticket_decision_and_grant_validate_their_nested_binding(self) -> None:
        ticket, _, extras = vnext_chain()
        binding = composition_binding(
            request_id=ticket.payload.request_id,
            run_id=ticket.payload.run_id,
            generation=ticket.payload.generation,
            effect_id=ticket.payload.effect_id,
            action_id=ticket.payload.action_id,
            action_version=ticket.payload.action_version,
            materialized_arguments_sha256=extras["intent"].payload_sha256,
            canonical_invocation_sha256=ticket.payload.canonical_invocation_sha256,
            target_snapshot_sha256=extras["intent"].target_snapshot_sha256,
            workspace_id=ticket.payload.workspace_id,
            workspace_scope_hash=extras["intent"].workspace_scope_hash,
        )
        extras["intent"].__class__(
            **{
                **extras["intent"].model_dump(mode="python"),
                "composition_execution_binding": binding,
            }
        )
        ticket.payload.__class__(
            **{
                **ticket.payload.model_dump(mode="python"),
                "composition_execution_binding": binding,
            }
        )
        extras["decision"].__class__(
            **{
                **extras["decision"].model_dump(mode="python"),
                "composition_execution_binding": binding,
            }
        )
        extras["grant"].payload.__class__(
            **{
                **extras["grant"].payload.model_dump(mode="python"),
                "composition_execution_binding": binding,
            }
        )
        drifted_action = composition_binding(
            **{
                **binding.model_dump(mode="python", exclude={"binding_sha256"}),
                "action_id": "file.stat",
            }
        )
        with self.assertRaises(ValidationError):
            extras["intent"].__class__(
                **{
                    **extras["intent"].model_dump(mode="python"),
                    "composition_execution_binding": drifted_action,
                }
            )
        with self.assertRaises(ValidationError):
            ticket.payload.__class__(
                **{
                    **ticket.payload.model_dump(mode="python"),
                    "composition_execution_binding": drifted_action,
                }
            )
        with self.assertRaises(ValidationError):
            extras["grant"].payload.__class__(
                **{
                    **extras["grant"].payload.model_dump(mode="python"),
                    "composition_execution_binding": drifted_action,
                }
            )
        invalid_digest = binding.model_copy(update={"binding_sha256": HASH_D})
        with self.assertRaises(ValidationError):
            extras["decision"].__class__(
                **{
                    **extras["decision"].model_dump(mode="python"),
                    "composition_execution_binding": invalid_digest,
                }
            )


class CompositionPolicyBindingTests(unittest.TestCase):
    def _fixture(self):
        _, manifest, extras = vnext_chain()
        permission = ActionPermission(
            action_id="docx.create",
            action_version="1.0.0",
            registry_risk="A0",
            effective_risk="A0",
            effect="read",
            handler="docx.create",
            allowed_side_effects=("read",),
            path_policy="workspace_only",
            allow_absolute_paths=False,
            allow_shell=False,
            allow_python=False,
            requires_confirmation=False,
            source_manifest_sha256=HASH_A,
            permission_sha256="0" * 64,
        ).with_computed_sha256()
        registry = ActionRegistrySnapshot(
            registry_id="registry-composition-test",
            revision=1,
            generated_at_ms=10_000,
            source_manifest_sha256=HASH_A,
            executable_count=1,
            permissions=(permission,),
            registry_sha256="0" * 64,
        ).with_computed_sha256()
        base = extras["intent"].model_copy(
            update={
                "requested_side_effects": ("read",),
                "intent_sha256": "0" * 64,
            }
        ).with_computed_sha256()
        binding = composition_binding(
            request_id=base.request_id,
            run_id=base.run_id,
            generation=base.generation,
            action_id=base.action_id,
            action_version=base.action_version,
            materialized_arguments_sha256=base.payload_sha256,
            canonical_invocation_sha256=base.canonical_invocation_sha256,
            workspace_id=base.workspace_id,
            workspace_scope_hash=base.workspace_scope_hash,
        )
        intent = base.model_copy(
            update={"composition_execution_binding": binding, "intent_sha256": "0" * 64}
        ).with_computed_sha256()
        impact = extras["impact"].model_copy(
            update={
                "intent_sha256": intent.intent_sha256,
                "dynamic_risk": "A0",
                "workspace_scope_milli": 0,
                "impact_sha256": "0" * 64,
            }
        ).with_computed_impact_sha256()
        engine = PolicyEngine(
            registry,
            policy_snapshot_sha256=HASH_B,
            skill_catalog_hash=HASH_B,
            capability_manifest_hash=manifest.sha256,
            component_manifest_hash=manifest.component_manifest_hash,
        )
        return engine, intent, impact, binding

    def test_expected_binding_is_required_and_propagated(self) -> None:
        engine, intent, impact, binding = self._fixture()
        missing = engine.evaluate(intent, impact, decided_at_ms=20_000)
        self.assertEqual(missing.outcome, "REJECT")
        self.assertIn("policy.composition_binding_untrusted", missing.reason_codes)
        exact = engine.evaluate(
            intent,
            impact,
            decided_at_ms=20_000,
            expected_composition_binding=binding,
        )
        self.assertEqual(exact.outcome, "ALLOW")
        self.assertEqual(exact.composition_execution_binding, binding)

    def test_rehashed_counterfeit_binding_is_rejected_against_trusted_expected(self) -> None:
        engine, intent, impact, binding = self._fixture()
        counterfeit = composition_binding(
            **{
                **binding.model_dump(mode="python", exclude={"binding_sha256"}),
                "step_id": "step-counterfeit",
            }
        )
        forged_intent = intent.model_copy(
            update={"composition_execution_binding": counterfeit, "intent_sha256": "0" * 64}
        ).with_computed_sha256()
        forged_impact = impact.model_copy(
            update={"intent_sha256": forged_intent.intent_sha256, "impact_sha256": "0" * 64}
        ).with_computed_impact_sha256()
        decision = engine.evaluate(
            forged_intent,
            forged_impact,
            decided_at_ms=20_000,
            expected_composition_binding=binding,
        )
        self.assertEqual(decision.outcome, "REJECT")
        self.assertIn("policy.composition_binding_mismatch", decision.reason_codes)

    def test_expected_composition_binding_is_capped_at_a0(self) -> None:
        engine, intent, impact, binding = self._fixture()
        raised_impact = impact.model_copy(
            update={
                "dynamic_risk": "A1",
                "uncertainty_milli": 100,
                "impact_sha256": "0" * 64,
            }
        ).with_computed_impact_sha256()
        decision = engine.evaluate(
            intent,
            raised_impact,
            decided_at_ms=20_000,
            expected_composition_binding=binding,
        )
        self.assertEqual((decision.computed_risk, decision.outcome), ("A1", "REJECT"))
        self.assertIn(
            "policy.composition_a0_ceiling_exceeded", decision.reason_codes
        )

        base_permission = engine.registry.permissions[0]
        unsafe_cases = (
            {"registry_risk": "A1", "effective_risk": "A1"},
            {"effect": "create"},
            {"allowed_side_effects": ("local_write", "read")},
            {"allow_shell": True},
            {"allow_python": True},
        )
        for permission_updates in unsafe_cases:
            with self.subTest(permission_updates=permission_updates):
                unsafe_permission = base_permission.model_copy(
                    update={**permission_updates, "permission_sha256": "0" * 64}
                ).with_computed_sha256()
                unsafe_registry = engine.registry.model_copy(
                    update={
                        "permissions": (unsafe_permission,),
                        "registry_sha256": "0" * 64,
                    }
                ).with_computed_sha256()
                unsafe_engine = PolicyEngine(
                    unsafe_registry,
                    policy_snapshot_sha256=engine.policy_snapshot_sha256,
                    skill_catalog_hash=engine.skill_catalog_hash,
                    capability_manifest_hash=engine.capability_manifest_hash,
                    component_manifest_hash=engine.component_manifest_hash,
                )
                rejected = unsafe_engine.evaluate(
                    intent,
                    impact,
                    decided_at_ms=20_000,
                    expected_composition_binding=binding,
                )
                self.assertEqual(rejected.outcome, "REJECT")
                self.assertIn(
                    "policy.composition_a0_ceiling_exceeded",
                    rejected.reason_codes,
                )

        confirmation = UserConfirmationGrant(
            confirmation_id="confirmation-composition-forbidden",
            decision_id="decision-composition-forbidden",
            impact_sha256=impact.impact_sha256,
            action_id=intent.action_id,
            arguments_sha256=intent.arguments_sha256,
            workspace_scope_hash=intent.workspace_scope_hash,
            principal_scope_hash=intent.principal_scope_hash,
            nonce="confirmation-composition-nonce",
            issued_at_ms=19_000,
            expires_at_ms=30_000,
            confirmation_sha256="0" * 64,
        ).with_computed_sha256()
        rejected_confirmation = engine.evaluate(
            intent,
            impact,
            decided_at_ms=20_000,
            expected_composition_binding=binding,
            confirmation=confirmation,
        )
        self.assertEqual(rejected_confirmation.outcome, "REJECT")
        self.assertIn(
            "policy.composition_a0_ceiling_exceeded",
            rejected_confirmation.reason_codes,
        )

        verify_permission = base_permission.model_copy(
            update={"effect": "verify", "permission_sha256": "0" * 64}
        ).with_computed_sha256()
        verify_registry = engine.registry.model_copy(
            update={
                "permissions": (verify_permission,),
                "registry_sha256": "0" * 64,
            }
        ).with_computed_sha256()
        verify_engine = PolicyEngine(
            verify_registry,
            policy_snapshot_sha256=engine.policy_snapshot_sha256,
            skill_catalog_hash=engine.skill_catalog_hash,
            capability_manifest_hash=engine.capability_manifest_hash,
            component_manifest_hash=engine.component_manifest_hash,
        )
        self.assertEqual(
            verify_engine.evaluate(
                intent,
                impact,
                decided_at_ms=20_000,
                expected_composition_binding=binding,
            ).outcome,
            "ALLOW",
        )


class CompositionAuthorizationBindingTests(unittest.TestCase):
    def _chain(self, **chain_overrides):
        ticket, manifest, extras = vnext_chain(**chain_overrides)
        binding = composition_binding(
            request_id=ticket.payload.request_id,
            run_id=ticket.payload.run_id,
            generation=ticket.payload.generation,
            effect_id=ticket.payload.effect_id,
            action_id=ticket.payload.action_id,
            action_version=ticket.payload.action_version,
            materialized_arguments_sha256=extras["intent"].payload_sha256,
            canonical_invocation_sha256=ticket.payload.canonical_invocation_sha256,
            target_sha256=canonical_sha256("notes.txt"),
            target_snapshot_sha256=extras["intent"].target_snapshot_sha256,
            workspace_id=ticket.payload.workspace_id,
            workspace_scope_hash=extras["intent"].workspace_scope_hash,
        )
        intent = extras["intent"].model_copy(
            update={"composition_execution_binding": binding, "intent_sha256": "0" * 64}
        ).with_computed_sha256()
        impact = extras["impact"].model_copy(
            update={"intent_sha256": intent.intent_sha256, "impact_sha256": "0" * 64}
        ).with_computed_impact_sha256()
        decision = extras["decision"].model_copy(
            update={
                "intent_sha256": intent.intent_sha256,
                "impact_sha256": impact.impact_sha256,
                "composition_execution_binding": binding,
                "decision_sha256": "0" * 64,
            }
        ).with_computed_sha256()
        ticket_payload = ticket.payload.model_copy(
            update={
                "intent_sha256": intent.intent_sha256,
                "decision_sha256": decision.decision_sha256,
                "impact_sha256": impact.impact_sha256,
                "composition_execution_binding": binding,
            }
        )
        ticket = ticket.model_copy(update={"payload": ticket_payload})
        grant_payload = extras["grant"].payload.model_copy(
            update={
                "ticket_sha256": canonical_sha256(ticket_payload.model_dump(mode="json")),
                "decision_sha256": decision.decision_sha256,
                "impact_sha256": impact.impact_sha256,
                "composition_execution_binding": binding,
            }
        )
        grant = extras["grant"].model_copy(update={"payload": grant_payload})
        extras.update(intent=intent, impact=impact, decision=decision, grant=grant)
        return ticket, manifest, extras, binding

    def _authorize(self, ticket, manifest, extras, binding, **overrides):
        values = {
            **extras,
            "expected_composition_binding": binding,
            "actual_materialized_arguments_sha256": binding.materialized_arguments_sha256,
            "actual_target_sha256": binding.target_sha256,
            "actual_target_snapshot_sha256": binding.target_snapshot_sha256,
        }
        values.pop("claim", None)
        values.pop("active_lease_epoch", None)
        values.update(overrides)
        return authorize_execution_contract(
            ticket,
            manifest,
            signature_verified=True,
            now_ms=20_000,
            expected_gateway_epoch=3,
            **values,
        )

    def test_exact_four_link_chain_authorizes(self) -> None:
        ticket, manifest, extras, binding = self._chain()
        action = self._authorize(ticket, manifest, extras, binding)
        self.assertEqual(action.action_id, binding.action_id)

    def test_plan_step_generation_effect_action_workspace_and_target_swaps_fail(self) -> None:
        ticket, manifest, extras, binding = self._chain()
        cases = (
            composition_binding(**{**binding.model_dump(mode="python", exclude={"binding_sha256"}), "executable_plan_id": "plan-swapped"}),
            composition_binding(**{**binding.model_dump(mode="python", exclude={"binding_sha256"}), "step_id": "step-swapped"}),
            composition_binding(**{**binding.model_dump(mode="python", exclude={"binding_sha256"}), "generation": 99}),
            composition_binding(**{**binding.model_dump(mode="python", exclude={"binding_sha256"}), "effect_id": "eff_" + "9" * 64}),
            composition_binding(**{**binding.model_dump(mode="python", exclude={"binding_sha256"}), "action_id": "file.stat"}),
            composition_binding(**{**binding.model_dump(mode="python", exclude={"binding_sha256"}), "workspace_id": "workspace-swapped"}),
        )
        for expected in cases:
            with self.subTest(field=expected.binding_sha256), self.assertRaises(ExecutionAuthorizationError) as caught:
                self._authorize(ticket, manifest, extras, expected)
            self.assertEqual(caught.exception.code, "ticket.composition_binding.mismatch")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            self._authorize(ticket, manifest, extras, binding, actual_target_sha256=HASH_D)
        self.assertEqual(caught.exception.code, "ticket.composition_target.mismatch")

    def test_partial_chain_and_missing_actual_inputs_fail_closed(self) -> None:
        ticket, manifest, extras, binding = self._chain()
        without = ticket.model_copy(
            update={"payload": ticket.payload.model_copy(update={"composition_execution_binding": None})}
        )
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            self._authorize(without, manifest, extras, binding)
        self.assertEqual(caught.exception.code, "ticket.composition_binding.incomplete")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            self._authorize(ticket, manifest, extras, binding, actual_target_sha256=None)
        self.assertEqual(caught.exception.code, "ticket.composition_target.missing")

    def test_actual_argument_and_target_snapshot_swaps_fail(self) -> None:
        ticket, manifest, extras, binding = self._chain()
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            self._authorize(
                ticket,
                manifest,
                extras,
                binding,
                actual_materialized_arguments_sha256=HASH_D,
            )
        self.assertEqual(caught.exception.code, "ticket.composition_arguments.mismatch")
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            self._authorize(
                ticket,
                manifest,
                extras,
                binding,
                actual_target_snapshot_sha256=HASH_D,
            )
        self.assertEqual(
            caught.exception.code, "ticket.composition_target_snapshot.mismatch"
        )
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            self._authorize(
                ticket,
                manifest,
                extras,
                binding,
                actual_arguments_sha256=HASH_D,
            )
        self.assertEqual(caught.exception.code, "ticket.arguments.mismatch")

    def test_non_null_target_probe_is_bound_without_the_legacy_probe_argument(self) -> None:
        ticket, manifest, extras, binding = self._chain(
            intent_overrides={
                "target_ref": "target-notes",
                "target_snapshot_sha256": HASH_B,
            }
        )
        action = self._authorize(ticket, manifest, extras, binding)
        self.assertEqual(action.action_id, binding.action_id)
        with self.assertRaises(ExecutionAuthorizationError) as caught:
            self._authorize(
                ticket,
                manifest,
                extras,
                binding,
                actual_target_snapshot_sha256=None,
            )
        self.assertEqual(
            caught.exception.code, "ticket.composition_target_snapshot.mismatch"
        )

    def test_grant_signer_propagates_exact_binding_and_rejects_partial_chain(self) -> None:
        ticket, _, extras, binding = self._chain()
        permission = ActionPermission(
            action_id=binding.action_id,
            action_version=binding.action_version,
            registry_risk="A2",
            effective_risk="A2",
            effect="create",
            handler="docx.create",
            allowed_side_effects=("local_write",),
            path_policy="workspace_only",
            allow_absolute_paths=False,
            allow_shell=False,
            allow_python=False,
            requires_confirmation=False,
            source_manifest_sha256=HASH_A,
            permission_sha256="0" * 64,
        ).with_computed_sha256()
        decision = extras["decision"].model_copy(
            update={
                "action_permission_sha256": permission.permission_sha256,
                "decision_sha256": "0" * 64,
            }
        ).with_computed_sha256()
        payload = ticket.payload.model_copy(
            update={
                "action_permission_sha256": permission.permission_sha256,
                "decision_sha256": decision.decision_sha256,
            }
        )
        ticket = ticket.model_copy(update={"payload": payload})
        signer = TicketSigner("composition-signing-key", Ed25519PrivateKey.generate())
        grant = issue_omni_capability_grant(
            signer=signer,
            ticket=ticket,
            intent=extras["intent"],
            permission=permission,
            decision=decision,
            nonce="composition-grant-nonce",
            issued_at_ms=20_000,
            expires_at_ms=30_000,
        )
        self.assertEqual(grant.payload.composition_execution_binding, binding)

        partial_decision = decision.model_copy(
            update={"composition_execution_binding": None, "decision_sha256": "0" * 64}
        ).with_computed_sha256()
        with self.assertRaisesRegex(CapabilityGrantError, "chain is incomplete"):
            issue_omni_capability_grant(
                signer=signer,
                ticket=ticket,
                intent=extras["intent"],
                permission=permission,
                decision=partial_decision,
                nonce="composition-grant-partial",
                issued_at_ms=20_000,
                expires_at_ms=30_000,
            )
        counterfeit = composition_binding(
            **{
                **binding.model_dump(mode="python", exclude={"binding_sha256"}),
                "step_id": "step-rehashed-counterfeit",
            }
        )
        counterfeit_decision = decision.model_copy(
            update={
                "composition_execution_binding": counterfeit,
                "decision_sha256": "0" * 64,
            }
        ).with_computed_sha256()
        with self.assertRaisesRegex(CapabilityGrantError, "chain is invalid"):
            issue_omni_capability_grant(
                signer=signer,
                ticket=ticket,
                intent=extras["intent"],
                permission=permission,
                decision=counterfeit_decision,
                nonce="composition-grant-counterfeit",
                issued_at_ms=20_000,
                expires_at_ms=30_000,
            )


class CompositionRawOmniConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CapabilityFixture(Path(self.temporary.name))
        self.fixture.module = load_module(
            "test_p7c1_source_omni_capability", SOURCE_OMNI_CAPABILITY
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _grant(self, nonce: str, **binding_overrides: object):
        legacy, action, target, args = self.fixture.grant(nonce)
        payload = legacy.payload
        binding_values = {
            "request_id": payload.request_id,
            "run_id": payload.run_id,
            "generation": payload.generation,
            "effect_id": payload.effect_id,
            "action_id": payload.action_id,
            "action_version": payload.action_version,
            "materialized_arguments_sha256": canonical_sha256(args),
            "target_sha256": canonical_sha256(target),
            "workspace_id": payload.workspace_id,
            "workspace_scope_hash": payload.workspace_scope_hash,
        }
        binding_values.update(binding_overrides)
        binding = composition_binding(**binding_values)
        bound_payload = OmniCapabilityGrantPayload(
            **{
                **payload.model_dump(mode="python"),
                "risk_class": "A0",
                "allowed_side_effects": ("read",),
                "allow_shell": False,
                "allow_python": False,
                "composition_execution_binding": binding,
            }
        )
        return (
            self.fixture.signer.sign_omni_capability(bound_payload),
            binding,
            action,
            target,
            args,
        )

    def _verify(self, grant, binding, action, target, args, *, runtime_binding=True):
        runtime = self.fixture.runtime_meta()
        if runtime_binding:
            runtime["composition_execution_binding"] = binding.model_dump(mode="json")
            runtime.update(
                {
                    "request_id": binding.request_id,
                    "run_id": binding.run_id,
                    "generation": binding.generation,
                    "effect_id": binding.effect_id,
                    "step_id": binding.step_id,
                    "executable_plan_id": binding.executable_plan_id,
                    "composition_binding_sha256": binding.binding_sha256,
                }
            )
        with mock.patch.dict(os.environ, self.fixture.env(), clear=False):
            return self.fixture.module.verify_capability_grant(
                grant.model_dump(mode="json") if hasattr(grant, "model_dump") else grant,
                action=action,
                target=target,
                args=args,
                workspace=str(self.fixture.workspace),
                runtime_meta=runtime,
            )

    def test_exact_nested_binding_is_closed_over_runtime_and_actual_call(self) -> None:
        grant, binding, action, target, args = self._grant("nonce_p7c1_exact")
        result = self._verify(grant, binding, action, target, args)
        self.assertEqual(result["grant_id"], grant.payload.grant_id)

    def test_signed_composition_grant_cannot_raise_a0_read_only_ceiling(self) -> None:
        cases = (
            ("risk", {"risk_class": "A1"}),
            ("write", {"allowed_side_effects": ["local_write", "read"]}),
            ("shell", {"allow_shell": True}),
            ("python", {"allow_python": True}),
        )
        for suffix, updates in cases:
            with self.subTest(suffix=suffix):
                grant, binding, action, target, args = self._grant(
                    f"nonce_p7c1_ceiling_{suffix}"
                )
                elevated = grant.model_dump(mode="json")
                elevated["payload"].update(updates)
                signing_input = (
                    base64.urlsafe_b64encode(
                        canonical_json_bytes(elevated["header"])
                    ).rstrip(b"=")
                    + b"."
                    + base64.urlsafe_b64encode(
                        canonical_json_bytes(elevated["payload"])
                    ).rstrip(b"=")
                )
                elevated["signature"] = base64.urlsafe_b64encode(
                    self.fixture.private.sign(signing_input)
                ).rstrip(b"=").decode("ascii")
                with self.assertRaisesRegex(
                    self.fixture.module.CapabilityGrantError,
                    "A0 read-only ceiling",
                ):
                    self._verify(elevated, binding, action, target, args)

    def test_missing_runtime_binding_and_rehashed_target_counterfeit_fail(self) -> None:
        grant, binding, action, target, args = self._grant("nonce_p7c1_partial")
        with self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError, "binding is incomplete"
        ):
            self._verify(
                grant, binding, action, target, args, runtime_binding=False
            )

        forged, forged_binding, action, target, args = self._grant(
            "nonce_p7c1_target", target_sha256=canonical_sha256("other.txt")
        )
        with self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError, "target binding is invalid"
        ):
            self._verify(forged, forged_binding, action, target, args)

        forged_args, forged_args_binding, action, target, args = self._grant(
            "nonce_p7c1_args", materialized_arguments_sha256=HASH_D
        )
        with self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError, "argument binding is invalid"
        ):
            self._verify(forged_args, forged_args_binding, action, target, args)

        grant, binding, action, target, args = self._grant("nonce_p7c1_runtime")
        runtime = self.fixture.runtime_meta()
        runtime.update(
            {
                "composition_execution_binding": binding.model_dump(mode="json"),
                "request_id": binding.request_id,
                "run_id": "run_" + "9" * 64,
                "generation": binding.generation,
                "effect_id": binding.effect_id,
                "step_id": binding.step_id,
                "executable_plan_id": binding.executable_plan_id,
                "composition_binding_sha256": binding.binding_sha256,
            }
        )
        with mock.patch.dict(os.environ, self.fixture.env(), clear=False), self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError,
            "runtime composition scope is invalid",
        ):
            self.fixture.module.verify_capability_grant(
                grant.model_dump(mode="json"),
                action=action,
                target=target,
                args=args,
                workspace=str(self.fixture.workspace),
                runtime_meta=runtime,
            )

    def test_nested_unknown_field_and_legacy_runtime_elevation_fail(self) -> None:
        grant, binding, action, target, args = self._grant("nonce_p7c1_unknown")
        raw = grant.model_dump(mode="json")
        raw["payload"]["composition_execution_binding"]["unknown_authority"] = True
        with self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError, "binding fields are invalid"
        ):
            self._verify(raw, binding, action, target, args)

        legacy, action, target, args = self.fixture.grant("nonce_p7c1_legacy")
        with self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError, "binding is incomplete"
        ):
            self._verify(legacy, binding, action, target, args)

        null_binding = legacy.model_dump(mode="json")
        null_binding["payload"]["composition_execution_binding"] = None
        with self.assertRaisesRegex(
            self.fixture.module.CapabilityGrantError, "binding is incomplete"
        ):
            self._verify(
                null_binding,
                binding,
                action,
                target,
                args,
                runtime_binding=False,
            )


if __name__ == "__main__":
    unittest.main()
