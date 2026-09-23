"""Narrow system admission for operational uncertainty, never proof of success.

The original P4 validator and source descriptors remain unchanged. This adapter
retains every UNKNOWN finding, and binds an explicit fixed workspace profile to
one versioned finding. Intake recomputes it from original source inputs; shadow
checks the binding again before its existing permission and verification gates.
"""
from __future__ import annotations

from contracts import canonical_sha256
from contracts.capability_composition import CompositionValidationFindingV1
from contracts.composition_profile import (
    composition_permission_allowed, composition_profile_risk_ceiling,
    composition_profile_valid,
)
from world_understanding.capability_composition.validator import (
    computed_validation_sha256, validate_capability_composition_plan,
)

PROFILE_ADMISSION_CODE = "validator.profile.operational_unknown_admitted"
PROFILE_ADMISSION_VERSION = "composition.operational-unknown-admission.v1"
OPERATIONAL_UNKNOWN_CODES = frozenset({
    "validator.action.idempotency_unknown", "validator.action.determinism_unknown",
})
PLAN_BOUND_VERIFIER = "verification-intent:plan-bound-acceptance"


def _marker_hash(original, profile_id, profile_sha256):
    return canonical_sha256({"domain": PROFILE_ADMISSION_VERSION,
        "execution_profile_id": profile_id, "execution_profile_sha256": profile_sha256,
        "original_validation_sha256": original.validation_sha256})


def _operational_only(validation, plan):
    return bool(validation.findings) and all(
        finding.state == "UNKNOWN" and finding.code in OPERATIONAL_UNKNOWN_CODES
        and finding.subject_ref in plan.permission_requirements for finding in validation.findings)


def resolve_profile_validation(plan, proposal, candidates, context, registry, *,
        available_verifiers, validated_at_ms, execution_profile_id=None,
        execution_profile_sha256=None):
    """Recompute P4, then allow only fixed-profile operational UNKNOWNs.

    This function supplies no invocation permissions or execution authority. Its
    result remains UNKNOWN and must receive mandatory terminal-effect evidence.
    Unknown availability/verifiers, structural invalidity, and A5 stay refused.
    """
    if not composition_profile_valid(execution_profile_id, execution_profile_sha256):
        raise ValueError("composition.profile_admission.profile_invalid")
    original = validate_capability_composition_plan(plan, proposal, candidates, context, registry,
        available_verifiers=available_verifiers, validated_at_ms=validated_at_ms)
    if (execution_profile_id is None or original.result != "UNKNOWN"
            or original.unknown_disposition != "REJECT" or original.mandatory_verification
            or not _operational_only(original, plan)
            or plan.verification_intents != (PLAN_BOUND_VERIFIER,)
            or PLAN_BOUND_VERIFIER not in available_verifiers
            or plan.composition_risk > composition_profile_risk_ceiling(
                execution_profile_id, execution_profile_sha256)):
        return original
    permissions = {item.action_id: item for item in registry.permissions}
    if not plan.permission_requirements or any(
            action not in permissions or not composition_permission_allowed(permissions[action],
                profile_id=execution_profile_id, profile_sha256=execution_profile_sha256)
            for action in plan.permission_requirements):
        return original
    marker = CompositionValidationFindingV1(code=PROFILE_ADMISSION_CODE, state="UNKNOWN",
        subject_ref=execution_profile_id,
        detail_hash=_marker_hash(original, execution_profile_id, execution_profile_sha256))
    findings = tuple(sorted((*original.findings, marker),
        key=lambda item: (item.state, item.code, item.subject_ref, item.detail_hash or "")))
    admitted = original.model_copy(update={"findings": findings,
        "unknown_disposition": "PROVISIONAL_ALLOW", "mandatory_verification": True})
    return admitted.model_copy(update={"validation_sha256": computed_validation_sha256(admitted)})


def require_profile_validation_binding(plan, validation, *, execution_profile_id=None,
                                       execution_profile_sha256=None):
    """Shadow defense: recover original UNKNOWN and verify its sealed profile."""
    markers = tuple(item for item in validation.findings if item.code == PROFILE_ADMISSION_CODE)
    if not markers:
        if (validation.result == "UNKNOWN" and validation.unknown_disposition == "PROVISIONAL_ALLOW"
                and plan.composition_risk not in {"A0", "A1"}):
            raise ValueError("composition.profile_admission.binding_missing")
        return
    if (len(markers) != 1 or execution_profile_id is None
            or not composition_profile_valid(execution_profile_id, execution_profile_sha256)
            or validation.result != "UNKNOWN" or validation.unknown_disposition != "PROVISIONAL_ALLOW"
            or not validation.mandatory_verification
            or markers[0].state != "UNKNOWN" or markers[0].subject_ref != execution_profile_id
            or plan.composition_risk > composition_profile_risk_ceiling(execution_profile_id, execution_profile_sha256)
            or plan.verification_intents != (PLAN_BOUND_VERIFIER,)):
        raise ValueError("composition.profile_admission.binding_invalid")
    original = validation.model_copy(update={
        "findings": tuple(item for item in validation.findings if item.code != PROFILE_ADMISSION_CODE),
        "unknown_disposition": "REJECT", "mandatory_verification": False})
    original = original.model_copy(update={"validation_sha256": computed_validation_sha256(original)})
    if (not _operational_only(original, plan)
            or markers[0].detail_hash != _marker_hash(original, execution_profile_id, execution_profile_sha256)):
        raise ValueError("composition.profile_admission.binding_invalid")
