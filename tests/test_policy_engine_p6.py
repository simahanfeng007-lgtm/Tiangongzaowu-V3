from __future__ import annotations

import base64
import hashlib
import json
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import (
    ActionIntent,
    PublicKeyDescriptor,
    ResourceEnvelope,
    SkillActivationGrant,
    SourceRef,
    TrustBundle,
    TrustScope,
    UserConfirmationGrant,
    canonical_sha256,
)
from runtime_security import TicketVerificationError, verify_omni_capability_grant
from total_gateway.action_registry import (
    ActionRegistryError,
    compile_action_registry,
    load_action_registry,
)
from total_gateway.grant_signer import issue_omni_capability_grant
from total_gateway.impact_evaluator import compute_action_impact, risk_from_action_impact
from total_gateway.policy_engine import PolicyEngine, PolicyEngineError
from total_gateway.tickets import TicketSigner
from tests.test_execution_contracts import execution_ticket


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    ROOT
    / "readable-python-source"
    / "omni_body_skill"
    / "registry"
    / "capability_manifest.generated.json"
)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
EVENT_ID = "lev_" + "3" * 64


def registry():
    return load_action_registry(MANIFEST_PATH, generated_at_ms=10_000)


def permission_for(risk: str):
    return next(item for item in registry().permissions if item.effective_risk == risk)


def intent_for(permission, *, skilled: bool = False):
    intent = ActionIntent(
        intent_id="intent_" + permission.action_id.replace(".", "-"),
        source="chat",
        life_id="life_main",
        principal_scope_hash=HASH_A,
        conversation_scope_hash=HASH_B,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        action_id=permission.action_id,
        action_version=permission.action_version,
        arguments_sha256=HASH_C,
        workspace_id="workspace_main",
        workspace_scope_hash=HASH_D,
        input_object_refs=(),
        requested_side_effects=permission.allowed_side_effects,
        requested_resources=ResourceEnvelope(
            max_runtime_ms=30_000,
            max_output_bytes=1_000_000,
            max_tool_calls=3,
        ),
        skill_id="skill_main" if skilled else None,
        skill_version="1.0.0" if skilled else None,
        skill_sha256=HASH_A if skilled else None,
        source_refs=(
            SourceRef(
                source_type="CURRENT_USER_INSTRUCTION",
                object_id=EVENT_ID,
                object_revision=1,
                sha256=HASH_B,
            ),
        ),
        created_at_ms=10_000,
        expires_at_ms=60_000,
        intent_sha256="0" * 64,
    )
    return intent.with_computed_sha256()


def engine_for(snapshot):
    return PolicyEngine(
        snapshot,
        policy_snapshot_sha256=HASH_B,
        skill_catalog_hash=HASH_B,
        capability_manifest_hash=HASH_C,
        component_manifest_hash=HASH_D,
    )


class RegistryClosureTests(unittest.TestCase):
    def test_every_declared_executable_action_has_one_machine_permission(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        snapshot = registry()
        executable_ids = tuple(
            sorted(
                action_id
                for action_id, raw in manifest["capabilities"].items()
                if raw.get("executable") is True
            )
        )
        self.assertEqual(snapshot.executable_count, manifest["executable"])
        self.assertEqual(
            tuple(item.action_id for item in snapshot.permissions),
            executable_ids,
        )
        self.assertTrue(snapshot.has_valid_sha256())

    def test_write_execute_and_privileged_actions_cannot_lie_low(self) -> None:
        floors = {"read": 0, "verify": 0, "create": 2, "write": 2, "update": 3, "execute": 3}
        order = {f"A{index}": index for index in range(6)}
        for permission in registry().permissions:
            with self.subTest(action_id=permission.action_id):
                self.assertGreaterEqual(order[permission.effective_risk], floors[permission.effect])
                if permission.allow_shell or permission.allow_python:
                    self.assertEqual(permission.effective_risk, "A4")
                    self.assertFalse(permission.requires_confirmation)
                if permission.allow_absolute_paths:
                    self.assertFalse(permission.requires_confirmation)
                if "destructive" in permission.allowed_side_effects:
                    self.assertEqual(permission.effective_risk, "A4")

    def test_stale_count_and_unhealthy_manifest_fail_closed(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        stale = dict(manifest)
        stale["executable"] = manifest["executable"] - 1
        with self.assertRaisesRegex(ActionRegistryError, "counts are stale"):
            compile_action_registry(stale, generated_at_ms=10_000)
        unhealthy = dict(manifest)
        unhealthy["validation"] = dict(manifest["validation"], ok=False)
        with self.assertRaisesRegex(ActionRegistryError, "validation is not healthy"):
            compile_action_registry(unhealthy, generated_at_ms=10_000)


class PolicyEngineClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = registry()
        self.engine = engine_for(self.registry)

    def _impact(self, intent, permission, **overrides):
        return compute_action_impact(
            intent,
            permission,
            created_at_ms=20_000,
            **overrides,
        )

    def test_action_impact_is_deterministic_and_caller_cannot_lower_machine_floor(self) -> None:
        permission = permission_for("A3")
        intent = intent_for(permission)
        first = self._impact(intent, permission)
        second = self._impact(intent, permission)
        self.assertEqual(first, second)
        self.assertEqual(risk_from_action_impact(first), "A3")
        decision = self.engine.evaluate(intent, first, decided_at_ms=20_000)
        self.assertEqual(decision.computed_risk, "A3")
        self.assertEqual(decision.outcome, "ALLOW")
        self.assertEqual(decision.capability_manifest_hash, HASH_C)
        self.assertEqual(decision.component_manifest_hash, HASH_D)

    def test_core_identity_or_policy_impact_is_a5_and_never_executable(self) -> None:
        permission = permission_for("A2")
        intent = intent_for(permission)
        impact = self._impact(intent, permission, affected_internal_nodes=("core_code",))
        self.assertEqual(risk_from_action_impact(impact), "A5")
        decision = self.engine.evaluate(intent, impact, decided_at_ms=20_000)
        self.assertEqual((decision.computed_risk, decision.outcome), ("A5", "REJECT"))

    def test_a4_is_autonomous_and_legacy_confirmation_is_rejected(self) -> None:
        permission = permission_for("A4")
        intent = intent_for(permission)
        impact = self._impact(intent, permission)
        allowed = self.engine.evaluate(intent, impact, decided_at_ms=20_000)
        self.assertEqual((allowed.computed_risk, allowed.outcome), ("A4", "ALLOW"))
        legacy = UserConfirmationGrant(
            confirmation_id="confirmation_legacy",
            decision_id=allowed.decision_id,
            impact_sha256=impact.impact_sha256,
            action_id=intent.action_id,
            arguments_sha256=intent.arguments_sha256,
            workspace_scope_hash=intent.workspace_scope_hash,
            principal_scope_hash=intent.principal_scope_hash,
            nonce="confirmation_nonce_legacy",
            issued_at_ms=19_000,
            expires_at_ms=30_000,
            confirmation_sha256="0" * 64,
        ).with_computed_sha256()
        with self.assertRaisesRegex(PolicyEngineError, "does not consume"):
            self.engine.evaluate(intent, impact, decided_at_ms=20_000, confirmation=legacy)

    def test_skill_requires_exact_system_activation_channel(self) -> None:
        permission = permission_for("A2")
        intent = intent_for(permission, skilled=True)
        impact = self._impact(intent, permission)
        rejected = self.engine.evaluate(intent, impact, decided_at_ms=20_000)
        self.assertEqual(rejected.outcome, "REJECT")
        activation = SkillActivationGrant(
            activation_id="skill_activation_main",
            selection_id="skill_selection_main",
            request_id=intent.request_id,
            run_id=intent.run_id,
            generation=intent.generation,
            principal_scope_hash=intent.principal_scope_hash,
            skill_catalog_hash=HASH_B,
            capability_manifest_hash=HASH_C,
            skill_id=intent.skill_id,
            skill_version=intent.skill_version,
            skill_sha256=intent.skill_sha256,
            allowed_action_ids=(intent.action_id,),
            issued_at_ms=10_000,
            expires_at_ms=60_000,
            activation_sha256="0" * 64,
        ).with_computed_sha256()
        allowed = self.engine.evaluate(
            intent,
            impact,
            decided_at_ms=20_000,
            skill_activation=activation,
        )
        self.assertEqual(allowed.outcome, "ALLOW")
        self.assertEqual(allowed.skill_activation_sha256, activation.activation_sha256)
        drifted = activation.model_copy(
            update={"skill_catalog_hash": HASH_D, "activation_sha256": "0" * 64}
        ).with_computed_sha256()
        rejected_drift = self.engine.evaluate(
            intent,
            impact,
            decided_at_ms=20_000,
            skill_activation=drifted,
        )
        self.assertEqual(rejected_drift.outcome, "REJECT")
        self.assertIn(
            "policy.skill_activation_missing_or_invalid",
            rejected_drift.reason_codes,
        )

    def test_signed_capability_is_exact_and_tamper_evident(self) -> None:
        permission = permission_for("A3")
        intent = intent_for(permission)
        impact = self._impact(intent, permission)
        decision = self.engine.evaluate(intent, impact, decided_at_ms=20_000)
        ticket = execution_ticket(
            ticket_id="ticket_policy_p6",
            nonce="ticket_nonce_policy_p6",
            decision_id=decision.decision_id,
            decision_sha256=decision.decision_sha256,
            impact_id=impact.impact_id,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=permission.permission_sha256,
            capability_manifest_hash=decision.capability_manifest_hash,
            component_manifest_hash=decision.component_manifest_hash,
            policy_snapshot_hash=decision.policy_snapshot_sha256,
            risk_class=decision.computed_risk,
            action_id=intent.action_id,
            action_version=intent.action_version,
            arguments_hash=intent.arguments_sha256,
            workspace_id=intent.workspace_id,
            principal_scope_hash=intent.principal_scope_hash,
            allowed_side_effects=intent.requested_side_effects,
            max_output_bytes=intent.requested_resources.max_output_bytes,
            max_runtime_ms=intent.requested_resources.max_runtime_ms,
            max_tool_calls=intent.requested_resources.max_tool_calls,
            skill_id=None,
            skill_version=None,
            skill_sha256=None,
            skill_activation_id=None,
            skill_activation_sha256=None,
        )
        private = Ed25519PrivateKey.generate()
        signer = TicketSigner("execution_policy_p6", private)
        grant = issue_omni_capability_grant(
            signer=signer,
            ticket=ticket,
            intent=intent,
            permission=permission,
            decision=decision,
            nonce="grant_nonce_policy_p6",
            issued_at_ms=20_000,
            expires_at_ms=30_000,
        )
        raw_public = private.public_key().public_bytes_raw()
        descriptor = PublicKeyDescriptor(
            kid="execution_policy_p6",
            issuer="tiangong-total-gateway",
            audience="tiangong-backend",
            purpose="execution_ticket",
            public_key_base64url=base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode("ascii"),
            public_key_sha256=hashlib.sha256(raw_public).hexdigest(),
            state="ACTIVE",
            not_before_ms=0,
            not_after_ms=100_000,
            component_manifest_hash=HASH_D,
        )
        trust = TrustBundle(
            bundle_id="trust_policy_p6",
            revision=1,
            gateway_epoch=ticket.payload.gateway_epoch,
            generated_at_ms=20_000,
            required_scopes=(
                TrustScope(
                    issuer=descriptor.issuer,
                    audience=descriptor.audience,
                    purpose=descriptor.purpose,
                ),
            ),
            keys=(descriptor,),
            production_ready=True,
            bundle_sha256=HASH_A,
        ).with_computed_sha256()
        self.assertEqual(
            verify_omni_capability_grant(grant, trust, now_ms=25_000).kid,
            descriptor.kid,
        )
        swapped = grant.model_copy(
            update={"payload": grant.payload.model_copy(update={"arguments_sha256": HASH_A})}
        )
        with self.assertRaisesRegex(TicketVerificationError, "signature.invalid"):
            verify_omni_capability_grant(swapped, trust, now_ms=25_000)


if __name__ == "__main__":
    unittest.main()
