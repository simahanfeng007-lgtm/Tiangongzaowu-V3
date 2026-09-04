"""Issue exact, short-lived, signed Omni capability grants."""

from __future__ import annotations

from contracts import (
    ActionIntent,
    ActionPermission,
    ExecutionTicket,
    OmniCapabilityGrant,
    OmniCapabilityGrantPayload,
    PolicyDecision,
    SkillActivationGrant,
    UserConfirmationGrant,
    canonical_sha256,
)

from .tickets import TicketSigner


class CapabilityGrantError(ValueError):
    pass


def issue_omni_capability_grant(
    *,
    signer: TicketSigner,
    ticket: ExecutionTicket,
    intent: ActionIntent,
    permission: ActionPermission,
    decision: PolicyDecision,
    nonce: str,
    issued_at_ms: int,
    expires_at_ms: int,
    confirmation: UserConfirmationGrant | None = None,
    skill_activation: SkillActivationGrant | None = None,
) -> OmniCapabilityGrant:
    if decision.outcome != "ALLOW" or not decision.has_valid_sha256():
        raise CapabilityGrantError("capability grant requires an allowed policy decision")
    if not intent.has_valid_sha256() or not permission.has_valid_sha256():
        raise CapabilityGrantError("capability grant evidence digest is invalid")
    payload = ticket.payload
    composition_chain = (
        intent.composition_execution_binding,
        decision.composition_execution_binding,
        payload.composition_execution_binding,
    )
    if sum(item is not None for item in composition_chain) not in {0, 3}:
        raise CapabilityGrantError("composition binding chain is incomplete")
    if composition_chain[0] is not None:
        binding = composition_chain[0]
        assert binding is not None
        if (
            any(item != binding for item in composition_chain[1:])
            or not binding.has_valid_sha256()
            or binding.request_id != payload.request_id
            or binding.run_id != payload.run_id
            or binding.generation != payload.generation
            or binding.effect_id != payload.effect_id
            or binding.action_id != payload.action_id
            or binding.action_version != payload.action_version
            or binding.canonical_invocation_sha256
            != payload.canonical_invocation_sha256
            or binding.workspace_id != payload.workspace_id
            or binding.request_id != intent.request_id
            or binding.run_id != intent.run_id
            or binding.generation != intent.generation
            or binding.action_id != intent.action_id
            or binding.action_version != intent.action_version
            or binding.materialized_arguments_sha256 != intent.payload_sha256
            or binding.canonical_invocation_sha256
            != intent.canonical_invocation_sha256
            or binding.target_snapshot_sha256 != intent.target_snapshot_sha256
            or binding.workspace_id != intent.workspace_id
            or binding.workspace_scope_hash != intent.workspace_scope_hash
        ):
            raise CapabilityGrantError("composition binding chain is invalid")
    if (
        payload.decision_id != decision.decision_id
        or payload.decision_sha256 != decision.decision_sha256
        or payload.impact_id != decision.impact_id
        or payload.impact_sha256 != decision.impact_sha256
        or payload.action_permission_sha256 != permission.permission_sha256
        or payload.capability_manifest_hash != decision.capability_manifest_hash
        or payload.component_manifest_hash != decision.component_manifest_hash
        or payload.action_id != intent.action_id
        or payload.action_version != intent.action_version
        or payload.arguments_hash != intent.arguments_sha256
        or payload.workspace_id != intent.workspace_id
        or payload.principal_scope_hash != intent.principal_scope_hash
        or payload.policy_snapshot_hash != decision.policy_snapshot_sha256
        or payload.risk_class != decision.computed_risk
    ):
        raise CapabilityGrantError("execution ticket is not bound to the policy decision")
    if not set(intent.requested_side_effects).issubset(permission.allowed_side_effects):
        raise CapabilityGrantError("capability side effects exceed permission")
    if confirmation is not None:
        raise CapabilityGrantError("A0-A4 capability grants do not consume confirmation")
    if decision.confirmation_sha256 is not None or payload.confirmation_sha256 is not None:
        raise CapabilityGrantError("legacy confirmation binding is forbidden")
    if intent.skill_id is not None:
        if (
            skill_activation is None
            or not skill_activation.has_valid_sha256()
            or decision.skill_activation_sha256 != skill_activation.activation_sha256
            or payload.skill_activation_sha256 != skill_activation.activation_sha256
        ):
            raise CapabilityGrantError("capability is missing its Skill activation")
    elif skill_activation is not None:
        raise CapabilityGrantError("unskilled capability cannot consume activation")
    grant_payload = OmniCapabilityGrantPayload(
        grant_id="omni-grant-" + canonical_sha256(
            {
                "ticket_id": payload.ticket_id,
                "decision_sha256": decision.decision_sha256,
                "nonce": nonce,
            }
        ),
        ticket_id=payload.ticket_id,
        # vNext 绑定（grant vNext ↔ 子票四元组/scope/票面哈希）：签发一次成形，
        # 不做二次补齐重签（同一逻辑 grant 只能有一次 capability 签名）。
        ticket_sha256=canonical_sha256(payload.model_dump(mode="json")),
        request_id=payload.request_id,
        run_id=payload.run_id,
        generation=payload.generation,
        effect_id=payload.effect_id,
        conversation_scope_hash=payload.conversation_scope_hash,
        decision_id=decision.decision_id,
        decision_sha256=decision.decision_sha256,
        impact_sha256=decision.impact_sha256,
        action_permission_sha256=permission.permission_sha256,
        action_registry_sha256=decision.action_registry_sha256,
        capability_manifest_hash=payload.capability_manifest_hash,
        component_manifest_hash=payload.component_manifest_hash,
        action_id=intent.action_id,
        action_version=intent.action_version,
        arguments_sha256=intent.arguments_sha256,
        workspace_id=intent.workspace_id,
        workspace_scope_hash=intent.workspace_scope_hash,
        principal_scope_hash=intent.principal_scope_hash,
        risk_class=decision.computed_risk,
        allowed_side_effects=intent.requested_side_effects,
        path_policy=permission.path_policy,
        allow_absolute_paths=permission.allow_absolute_paths,
        allow_shell=permission.allow_shell,
        allow_python=permission.allow_python,
        confirmation_sha256=decision.confirmation_sha256,
        skill_id=intent.skill_id,
        skill_version=intent.skill_version,
        skill_sha256=intent.skill_sha256,
        skill_activation_sha256=decision.skill_activation_sha256,
        composition_execution_binding=intent.composition_execution_binding,
        gateway_epoch=payload.gateway_epoch,
        nonce=nonce,
        issued_at_ms=issued_at_ms,
        not_before_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
    )
    return signer.sign_omni_capability(grant_payload)


__all__ = ["CapabilityGrantError", "issue_omni_capability_grant"]
