"""The sole authority that converts ActionIntent into an executable decision."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from contracts import (
    ActionImpact,
    ActionIntent,
    ActionRegistrySnapshot,
    CompositionExecutionBindingV1,
    PolicyDecision,
    SkillActivationGrant,
    SourceRef,
    UserConfirmationGrant,
    canonical_sha256,
)

from .impact_evaluator import risk_from_action_impact


_RISK_ORDER = ("A0", "A1", "A2", "A3", "A4", "A5")


class PolicyEngineError(ValueError):
    pass


# --- Provenance (draft section 2.3 / D-08) ------------------------------------
# Five-value source classification from the frozen contracts vNext SourceRef
# (g1-design/contracts-vnext-freeze.md section 0.4, contracts/models.py).  This
# is the production-side wiring: intent construction points bind their
# authorization provenance into PolicyEngine, and EXTERNAL_DATA / TOOL_DATA can
# never be promoted into an authorization source.  Models and routers may
# *propose* source refs; only the three authorizing classes below are accepted.
SOURCE_TYPE_VALUES = (
    "CURRENT_USER_INSTRUCTION",
    "PREAUTHORIZED_USER_FACT",
    "AUTHENTICATED_DIRECTORY",
    "EXTERNAL_DATA",
    "TOOL_DATA",
)
AUTHORIZATION_SOURCE_TYPES = frozenset(
    {
        "CURRENT_USER_INSTRUCTION",
        "PREAUTHORIZED_USER_FACT",
        "AUTHENTICATED_DIRECTORY",
    }
)
UNTRUSTED_SOURCE_TYPES = frozenset({"EXTERNAL_DATA", "TOOL_DATA"})


def normalize_source_refs(refs: Iterable[SourceRef | Mapping[str, Any]]) -> tuple[SourceRef, ...]:
    """Validate and canonically order a provenance set (sorted, deduplicated)."""
    normalized: list[SourceRef] = []
    for item in refs:
        if isinstance(item, SourceRef):
            ref = item
        elif isinstance(item, Mapping):
            try:
                ref = SourceRef.model_validate(dict(item))
            except ValueError as exc:
                raise PolicyEngineError("policy.source_ref_invalid") from exc
        else:
            raise PolicyEngineError("policy.source_ref_invalid")
        normalized.append(ref)
    unique = {ref.sort_key(): ref for ref in normalized}
    return tuple(unique[key] for key in sorted(unique))


def validate_authorization_source_refs(refs: Iterable[SourceRef | Mapping[str, Any]]) -> tuple[SourceRef, ...]:
    """Fail closed when an untrusted class is presented as an authorization source."""
    normalized = normalize_source_refs(refs)
    for ref in normalized:
        if ref.source_type in UNTRUSTED_SOURCE_TYPES:
            raise PolicyEngineError(f"policy.provenance_elevation:{ref.source_type}")
    return normalized


def derive_policy_decision_id(
    intent: ActionIntent,
    impact: ActionImpact,
    registry: ActionRegistrySnapshot,
    *,
    policy_snapshot_sha256: str,
) -> str:
    return "policy-decision-" + canonical_sha256(
        {
            "intent_sha256": intent.intent_sha256,
            "impact_sha256": impact.impact_sha256,
            "registry_sha256": registry.registry_sha256,
            "policy_snapshot_sha256": policy_snapshot_sha256,
        }
    )


class PolicyEngine:
    def __init__(
        self,
        registry: ActionRegistrySnapshot,
        *,
        policy_snapshot_sha256: str,
        skill_catalog_hash: str,
        capability_manifest_hash: str,
        component_manifest_hash: str,
    ) -> None:
        if (
            not registry.has_valid_sha256()
            or any(
                len(value) != 64
                for value in (
                    policy_snapshot_sha256,
                    skill_catalog_hash,
                    capability_manifest_hash,
                    component_manifest_hash,
                )
            )
        ):
            raise PolicyEngineError("policy authority inputs are invalid")
        self.registry = registry
        self.policy_snapshot_sha256 = policy_snapshot_sha256
        self.skill_catalog_hash = skill_catalog_hash
        self.capability_manifest_hash = capability_manifest_hash
        self.component_manifest_hash = component_manifest_hash

    def evaluate(
        self,
        intent: ActionIntent,
        impact: ActionImpact,
        *,
        decided_at_ms: int,
        confirmation: UserConfirmationGrant | None = None,
        skill_activation: SkillActivationGrant | None = None,
        authorization_source_refs: Iterable[SourceRef | Mapping[str, Any]] | None = None,
        expected_composition_binding: CompositionExecutionBindingV1 | None = None,
    ) -> PolicyDecision:
        if not intent.has_valid_sha256() or not impact.has_valid_impact_sha256():
            raise PolicyEngineError("policy evidence digest is invalid")
        if not intent.created_at_ms <= decided_at_ms <= intent.expires_at_ms:
            raise PolicyEngineError("policy decision is outside intent lifetime")
        if impact.life_id != intent.life_id or impact.action_id != intent.action_id:
            raise PolicyEngineError("policy impact binding is invalid")
        permission = next(
            (
                item
                for item in self.registry.permissions
                if item.action_id == intent.action_id
                and item.action_version == intent.action_version
            ),
            None,
        )
        if permission is None or not permission.has_valid_sha256():
            raise PolicyEngineError("policy action is not in the executable registry")
        if not set(intent.requested_side_effects).issubset(permission.allowed_side_effects):
            raise PolicyEngineError("policy requested side effects exceed the action permission")

        computed_risk = max(
            permission.effective_risk,
            risk_from_action_impact(impact),
            key=_RISK_ORDER.index,
        )
        decision_id = derive_policy_decision_id(
            intent,
            impact,
            self.registry,
            policy_snapshot_sha256=self.policy_snapshot_sha256,
        )
        reasons = {"policy.machine_risk_recomputed"}
        confirmation_id = None
        confirmation_sha256 = None
        outcome = None
        composition_binding = intent.composition_execution_binding
        if composition_binding is not None:
            # A binding carried by a caller is evidence only.  It becomes
            # authorization-relevant solely when the plan/store adapter passes
            # the exact independently re-read binding as a trusted argument.
            if expected_composition_binding is None:
                outcome = "REJECT"
                reasons.add("policy.composition_binding_untrusted")
            elif (
                not isinstance(expected_composition_binding, CompositionExecutionBindingV1)
                or not isinstance(composition_binding, CompositionExecutionBindingV1)
                or not composition_binding.has_valid_sha256()
                or not expected_composition_binding.has_valid_sha256()
                or composition_binding != expected_composition_binding
                or composition_binding.request_id != intent.request_id
                or composition_binding.run_id != intent.run_id
                or composition_binding.generation != intent.generation
                or composition_binding.action_id != intent.action_id
                or composition_binding.action_version != intent.action_version
                or composition_binding.materialized_arguments_sha256
                != intent.payload_sha256
                or composition_binding.canonical_invocation_sha256
                != intent.canonical_invocation_sha256
                or composition_binding.target_snapshot_sha256
                != intent.target_snapshot_sha256
                or composition_binding.workspace_id != intent.workspace_id
                or composition_binding.workspace_scope_hash
                != intent.workspace_scope_hash
            ):
                outcome = "REJECT"
                reasons.add("policy.composition_binding_mismatch")
            else:
                reasons.add("policy.composition_binding_bound")
        elif expected_composition_binding is not None:
            outcome = "REJECT"
            reasons.add("policy.composition_binding_missing")
        if expected_composition_binding is not None and (
            permission.registry_risk != "A0"
            or permission.effective_risk != "A0"
            or computed_risk != "A0"
            or permission.effect not in {"read", "verify"}
            or not set(permission.allowed_side_effects).issubset({"none", "read"})
            or not set(intent.requested_side_effects).issubset({"none", "read"})
            or permission.allow_shell
            or permission.allow_python
            or permission.requires_confirmation
            or confirmation is not None
        ):
            outcome = "REJECT"
            reasons.add("policy.composition_a0_ceiling_exceeded")
        if authorization_source_refs is not None:
            # D-08: the authorization provenance is bound into the decision.
            # EXTERNAL_DATA / TOOL_DATA may be carried as data, but presenting
            # them as an authorization source is a provenance elevation and the
            # action is refused outright; the model cannot talk its way into
            # user-grade authority.
            bound_refs = normalize_source_refs(authorization_source_refs)
            if any(ref.source_type in UNTRUSTED_SOURCE_TYPES for ref in bound_refs):
                outcome = "REJECT"
                reasons.add("policy.provenance_elevation")
            else:
                reasons.add("policy.provenance_sources_bound")
        if computed_risk == "A5":
            outcome = "REJECT"
            reasons.add("policy.a5_forbidden")
        elif outcome is None:
            # Personal-super-assistant policy: A0-A4 execute continuously.
            # A5 is the only sovereign hard gate.  Legacy confirmation grants
            # are rejected so model-supplied booleans can never become an
            # authorization fact and stale clients cannot revive the old A4
            # confirmation path.
            if confirmation is not None:
                raise PolicyEngineError("A0-A4 execution does not consume confirmation grants")
            outcome = "ALLOW"
            reasons.add("policy.within_autonomous_ceiling")

        activation_id = None
        activation_sha256 = None
        if intent.skill_id is not None:
            if (
                skill_activation is None
                or not skill_activation.has_valid_sha256()
                or skill_activation.request_id != intent.request_id
                or skill_activation.run_id != intent.run_id
                or skill_activation.generation != intent.generation
                or skill_activation.principal_scope_hash != intent.principal_scope_hash
                or skill_activation.skill_catalog_hash != self.skill_catalog_hash
                or skill_activation.capability_manifest_hash != self.capability_manifest_hash
                or skill_activation.skill_id != intent.skill_id
                or skill_activation.skill_version != intent.skill_version
                or skill_activation.skill_sha256 != intent.skill_sha256
                or intent.action_id not in skill_activation.allowed_action_ids
                or not skill_activation.issued_at_ms <= decided_at_ms <= skill_activation.expires_at_ms
            ):
                outcome = "REJECT"
                reasons.add("policy.skill_activation_missing_or_invalid")
            else:
                activation_id = skill_activation.activation_id
                activation_sha256 = skill_activation.activation_sha256
                reasons.add("policy.skill_activation_bound")
        elif skill_activation is not None:
            raise PolicyEngineError("unskilled action cannot consume a Skill activation")

        return PolicyDecision(
            decision_id=decision_id,
            intent_sha256=intent.intent_sha256,
            impact_id=impact.impact_id,
            impact_sha256=impact.impact_sha256,
            action_permission_sha256=permission.permission_sha256,
            action_registry_sha256=self.registry.registry_sha256,
            capability_manifest_hash=self.capability_manifest_hash,
            component_manifest_hash=self.component_manifest_hash,
            policy_snapshot_sha256=self.policy_snapshot_sha256,
            computed_risk=computed_risk,
            outcome=outcome,
            confirmation_id=confirmation_id,
            confirmation_sha256=confirmation_sha256,
            skill_activation_id=activation_id,
            skill_activation_sha256=activation_sha256,
            composition_execution_binding=composition_binding,
            reason_codes=tuple(sorted(reasons)),
            decided_at_ms=decided_at_ms,
            decision_sha256="0" * 64,
        ).with_computed_sha256()


__all__ = [
    "AUTHORIZATION_SOURCE_TYPES",
    "SOURCE_TYPE_VALUES",
    "UNTRUSTED_SOURCE_TYPES",
    "PolicyEngine",
    "PolicyEngineError",
    "SourceRef",
    "derive_policy_decision_id",
    "normalize_source_refs",
    "validate_authorization_source_refs",
]
