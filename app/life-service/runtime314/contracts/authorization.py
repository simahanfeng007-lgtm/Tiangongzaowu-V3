"""Pure structural authorization checks for an already verified ticket signature.

vNext consumer order (24 fail-closed steps, fixed, no downgrades): contract
epoch -> purpose -> signature -> manifest digest -> grant/ticket binding ->
run quadruple -> scope -> hash chain -> invocation -> registry bindings ->
gateway epoch -> fence epoch -> generation -> time window -> nonce replay ->
claim binding -> target version -> action -> argument schema -> risk floor ->
decision risk -> A5 recheck -> side-effect/resource limits -> arguments.
"""

from __future__ import annotations

from typing import Protocol

from .agency import ActionImpact
from .canonical import canonical_sha256
from .execution import (
    CapabilityAction,
    CapabilityManifest,
    CompositionExecutionBindingV1,
    ExecutionTicket,
)
from .life import LIFE_CONTRACT_SCHEMA_VERSION
from .models import SCHEMA_VERSION
from .policy import ActionIntent, OmniCapabilityGrant, PolicyDecision


_RISK_ORDER = ("A0", "A1", "A2", "A3", "A4", "A5")


class ExecutionAuthorizationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EffectClaimLike(Protocol):
    """Structural view of an effect-ledger claim (claim-before-ticket)."""

    schema_version: str
    intent_sha256: str
    effect_id: str
    claim_sha256: str
    claim_revision: int
    lease_epoch: int

    def has_valid_sha256(self) -> bool: ...


def authorize_execution_contract(
    ticket: ExecutionTicket,
    manifest: CapabilityManifest,
    *,
    signature_verified: bool,
    now_ms: int,
    expected_gateway_epoch: int,
    minimum_generation: int = 0,
    grant: OmniCapabilityGrant | None = None,
    intent: ActionIntent | None = None,
    decision: PolicyDecision | None = None,
    impact: ActionImpact | None = None,
    claim: EffectClaimLike | None = None,
    expected_fence_epoch: int | None = None,
    active_lease_epoch: int | None = None,
    nonce_already_consumed: bool = False,
    expected_target_snapshot_sha256: str | None = None,
    actual_arguments_sha256: str | None = None,
    expected_composition_binding: CompositionExecutionBindingV1 | None = None,
    actual_materialized_arguments_sha256: str | None = None,
    actual_target_sha256: str | None = None,
    actual_target_snapshot_sha256: str | None = None,
) -> CapabilityAction:
    """Return the exact authorized action or fail closed with a stable reason code.

    过渡语义（ExecutionContractCutover 前）：grant/intent/decision/impact/claim
    与 fence/lease epoch 全部提供时执行完整 24 步 vNext 链；缺省的相关检查跳过，
    其余检查（purpose/signature/manifest/registry/epoch/generation/time/action/
    schema/risk/side-effect/resource/arguments）与旧消费端等价，保证混合版本期
    vOld 票据行为不回归。切换完成后这些参数将成为必填。
    """

    payload = ticket.payload

    # 1. single contract epoch: vNext ticket plus vNext linked contracts only.
    epoch_mismatch = payload.contract_version != 3 or ticket.header.schema_version != SCHEMA_VERSION
    if grant is not None and grant.header.schema_version != SCHEMA_VERSION:
        epoch_mismatch = True
    if intent is not None and intent.schema_version != SCHEMA_VERSION:
        epoch_mismatch = True
    if decision is not None and decision.schema_version != SCHEMA_VERSION:
        epoch_mismatch = True
    if claim is not None and claim.schema_version != SCHEMA_VERSION:
        epoch_mismatch = True
    if impact is not None and impact.schema_version != LIFE_CONTRACT_SCHEMA_VERSION:
        epoch_mismatch = True
    if epoch_mismatch:
        raise ExecutionAuthorizationError("ticket.contract_version.unsupported")
    # 2. purpose literals (pydantic already enforces them; consumer rechecks).
    if (
        ticket.header.typ != "tiangong.execution-ticket+jws"
        or ticket.header.alg != "EdDSA"
        or payload.ticket_type != "ExecutionTicket"
        or payload.issuer != "tiangong-total-gateway"
        or payload.audience != "tiangong-backend"
    ):
        raise ExecutionAuthorizationError("ticket.purpose.mismatch")
    # 3. Ed25519 signature (verified upstream against the TrustBundle kid).
    if not signature_verified:
        raise ExecutionAuthorizationError("ticket.signature.unverified")
    # 4. capability manifest digest.
    if not manifest.has_valid_sha256():
        raise ExecutionAuthorizationError("capability_manifest.digest.invalid")
    # 4a. A composition binding is never trusted merely because it is signed.
    # The active-plan adapter must supply the independently re-read expected
    # value, and every signed host must carry that exact value all-or-none.
    grant_binding = (
        grant.payload.composition_execution_binding if grant is not None else None
    )
    intent_binding = intent.composition_execution_binding if intent is not None else None
    decision_binding = (
        decision.composition_execution_binding if decision is not None else None
    )
    ticket_binding = payload.composition_execution_binding
    composition_present = expected_composition_binding is not None or any(
        item is not None
        for item in (ticket_binding, grant_binding, intent_binding, decision_binding)
    )
    if composition_present:
        if expected_composition_binding is None:
            raise ExecutionAuthorizationError("ticket.composition_binding.untrusted")
        if grant is None or intent is None or decision is None or any(
            item is None
            for item in (ticket_binding, grant_binding, intent_binding, decision_binding)
        ):
            raise ExecutionAuthorizationError("ticket.composition_binding.incomplete")
        binding = expected_composition_binding
        if (
            not isinstance(binding, CompositionExecutionBindingV1)
            or not binding.has_valid_sha256()
            or ticket_binding != binding
            or grant_binding != binding
            or intent_binding != binding
            or decision_binding != binding
        ):
            raise ExecutionAuthorizationError("ticket.composition_binding.mismatch")
        if (
            binding.request_id != payload.request_id
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
            or grant.payload.request_id != binding.request_id
            or grant.payload.run_id != binding.run_id
            or grant.payload.generation != binding.generation
            or grant.payload.effect_id != binding.effect_id
            or grant.payload.action_id != binding.action_id
            or grant.payload.action_version != binding.action_version
            or grant.payload.arguments_sha256 != payload.arguments_hash
            or grant.payload.workspace_id != binding.workspace_id
            or grant.payload.workspace_scope_hash != binding.workspace_scope_hash
        ):
            raise ExecutionAuthorizationError("ticket.composition_binding.mismatch")
        if actual_arguments_sha256 is None or actual_materialized_arguments_sha256 is None:
            raise ExecutionAuthorizationError("ticket.composition_arguments.missing")
        if actual_materialized_arguments_sha256 != binding.materialized_arguments_sha256:
            raise ExecutionAuthorizationError("ticket.composition_arguments.mismatch")
        if actual_target_sha256 is None:
            raise ExecutionAuthorizationError("ticket.composition_target.missing")
        if actual_target_sha256 != binding.target_sha256:
            raise ExecutionAuthorizationError("ticket.composition_target.mismatch")
        if actual_target_snapshot_sha256 != binding.target_snapshot_sha256:
            raise ExecutionAuthorizationError("ticket.composition_target_snapshot.mismatch")
    # 5. exact grant -> ticket binding.
    if grant is not None:
        grant_payload = grant.payload
        if (
            grant_payload.ticket_sha256 != canonical_sha256(payload.model_dump(mode="json"))
            or grant_payload.ticket_id != payload.ticket_id
        ):
            raise ExecutionAuthorizationError("grant.ticket.mismatch")
        # 6. same request/run/generation/effect quadruple.
        if (
            grant_payload.request_id != payload.request_id
            or grant_payload.run_id != payload.run_id
            or grant_payload.generation != payload.generation
            or grant_payload.effect_id != payload.effect_id
        ):
            raise ExecutionAuthorizationError("grant.scope.mismatch")
        # 7. same conversation/principal/workspace scope.
        if (
            grant_payload.conversation_scope_hash != payload.conversation_scope_hash
            or grant_payload.principal_scope_hash != payload.principal_scope_hash
            or (intent is not None and grant_payload.workspace_scope_hash != intent.workspace_scope_hash)
            or grant_payload.workspace_id != payload.workspace_id
        ):
            raise ExecutionAuthorizationError("grant.scope.mismatch")
    # 8. intent/decision/impact hash chain.
    if intent is not None and (
        not intent.has_valid_sha256()
        or payload.intent_id != intent.intent_id
        or payload.intent_sha256 != intent.intent_sha256
        or (decision is not None and decision.intent_sha256 != intent.intent_sha256)
        or (impact is not None and impact.intent_sha256 != intent.intent_sha256)
        or (impact is not None and impact.target_snapshot_sha256 != intent.target_snapshot_sha256)
    ):
        raise ExecutionAuthorizationError("ticket.hash_chain.mismatch")
    if decision is not None and (
        not decision.has_valid_sha256()
        or payload.decision_id != decision.decision_id
        or payload.decision_sha256 != decision.decision_sha256
    ):
        raise ExecutionAuthorizationError("ticket.hash_chain.mismatch")
    if impact is not None and (
        not impact.has_valid_impact_sha256()
        or payload.impact_id != impact.impact_id
        or payload.impact_sha256 != impact.impact_sha256
    ):
        raise ExecutionAuthorizationError("ticket.hash_chain.mismatch")
    # 9. exact invocation anchor.
    if intent is not None and payload.canonical_invocation_sha256 != intent.canonical_invocation_sha256:
        raise ExecutionAuthorizationError("ticket.invocation.mismatch")
    # 10. registry/manifest/policy/coverage bindings.
    if payload.capability_manifest_hash != manifest.sha256:
        raise ExecutionAuthorizationError("ticket.capability_manifest.mismatch")
    if payload.component_manifest_hash != manifest.component_manifest_hash:
        raise ExecutionAuthorizationError("ticket.component_manifest.mismatch")
    if decision is not None:
        if payload.policy_snapshot_hash != decision.policy_snapshot_sha256:
            raise ExecutionAuthorizationError("ticket.policy_snapshot.mismatch")
        if payload.policy_coverage_sha256 != decision.policy_coverage_sha256:
            raise ExecutionAuthorizationError("ticket.policy_coverage.mismatch")
    # 11. gateway epoch.
    if payload.gateway_epoch != expected_gateway_epoch:
        raise ExecutionAuthorizationError("ticket.gateway_epoch.mismatch")
    # 12. active effect fence epoch (exact match).
    if expected_fence_epoch is not None and payload.fence_epoch != expected_fence_epoch:
        raise ExecutionAuthorizationError("ticket.fence_epoch.stale")
    # 13. generation fence.
    if payload.generation < minimum_generation:
        raise ExecutionAuthorizationError("ticket.generation.fenced")
    # 14. time window.
    if now_ms < payload.not_before_ms:
        raise ExecutionAuthorizationError("ticket.not_yet_valid")
    if now_ms > payload.expires_at_ms:
        raise ExecutionAuthorizationError("ticket.expired")
    # 15. nonce replay ledger (consumed atomically with execution upstream).
    if nonce_already_consumed:
        raise ExecutionAuthorizationError("ticket.nonce.replay")
    # 16. exact claim binding and live lease epoch.
    if claim is not None:
        if (
            not claim.has_valid_sha256()
            or claim.claim_sha256 != payload.claim_sha256
            or claim.claim_revision != payload.claim_revision
            or claim.intent_sha256 != payload.intent_sha256
            or claim.effect_id != payload.effect_id
        ):
            raise ExecutionAuthorizationError("ticket.claim.mismatch")
        if claim.lease_epoch != payload.claim_lease_epoch or (
            active_lease_epoch is not None and claim.lease_epoch != active_lease_epoch
        ):
            raise ExecutionAuthorizationError("ticket.claim.lease_epoch.stale")
    # 17. optimistic target concurrency (create-type actions carry no target).
    if (
        not composition_present
        and intent is not None
        and intent.target_ref is not None
        and (
            intent.target_snapshot_sha256 is None
            or expected_target_snapshot_sha256 != intent.target_snapshot_sha256
        )
    ):
        raise ExecutionAuthorizationError("ticket.target.version_mismatch")

    # 18. action must exist and be available.
    action = next(
        (
            item
            for item in manifest.actions
            if item.action_id == payload.action_id and item.version == payload.action_version
        ),
        None,
    )
    if action is None:
        raise ExecutionAuthorizationError("ticket.action.unknown")
    if not action.available:
        raise ExecutionAuthorizationError("ticket.action.unavailable")
    # 19. argument schema.
    if payload.argument_schema_sha256 != action.argument_schema_sha256:
        raise ExecutionAuthorizationError("ticket.argument_schema.mismatch")
    # 20. static manifest risk is a floor, not an equality.
    if _RISK_ORDER.index(payload.risk_class) < _RISK_ORDER.index(action.risk_class):
        raise ExecutionAuthorizationError("ticket.risk_class.below_floor")
    # 21. decision risk is the final value and never below dynamic risk.
    if decision is not None and payload.risk_class != decision.computed_risk:
        raise ExecutionAuthorizationError("ticket.risk_class.decision_mismatch")
    if impact is not None and _RISK_ORDER.index(payload.risk_class) < _RISK_ORDER.index(
        impact.dynamic_risk
    ):
        raise ExecutionAuthorizationError("ticket.risk_class.decision_mismatch")
    if decision is None and impact is None and payload.risk_class != action.risk_class:
        # vOld 过渡语义：无 decision/impact 上下文时保持旧消费端的 risk 相等规则
        raise ExecutionAuthorizationError("ticket.risk_class.mismatch")
    # 22. A5 never reaches execution (structural recheck).
    if payload.risk_class == "A5":
        raise ExecutionAuthorizationError("ticket.risk_class.mismatch")
    # 23. side-effect scope and resource limits.
    if not set(payload.allowed_side_effects).issubset(action.allowed_side_effects):
        raise ExecutionAuthorizationError("ticket.side_effect_scope.exceeded")
    if payload.max_runtime_ms > action.max_runtime_ms:
        raise ExecutionAuthorizationError("ticket.runtime_limit.exceeded")
    if payload.max_output_bytes > action.max_output_bytes:
        raise ExecutionAuthorizationError("ticket.output_limit.exceeded")
    if payload.max_tool_calls > action.max_tool_calls:
        raise ExecutionAuthorizationError("ticket.tool_call_limit.exceeded")
    # 24. executed arguments hash binds the ticket payload hash.
    if actual_arguments_sha256 is not None and actual_arguments_sha256 != payload.arguments_hash:
        raise ExecutionAuthorizationError("ticket.arguments.mismatch")
    return action


__all__ = [
    "EffectClaimLike",
    "ExecutionAuthorizationError",
    "authorize_execution_contract",
]
