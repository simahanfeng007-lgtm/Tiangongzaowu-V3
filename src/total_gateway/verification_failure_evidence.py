"""P19-R2 M5 §8: FailureEvidenceBuilder — derived failure facts.

The builder derives FailureEvidence from the active plan, current
readiness, and authoritative records. Callers cannot supply "why it
failed" — everything is re-derived from Store authority.
"""

from __future__ import annotations

from contracts.canonical import canonical_sha256
from contracts.verification import (
    FailureEvidence,
    VerificationPlan,
    VerificationReadiness,
    derive_failure_signature,
)


class FailureEvidenceBuilderError(RuntimeError):
    """Cannot derive FailureEvidence from the given inputs."""


_STATUS_TO_KIND = {
    "FAIL": "VERIFICATION_FAILED",
    "INCONCLUSIVE": "INCONCLUSIVE",
    "MISSING": "MISSING_EVIDENCE",
    "ERROR": "AUTHORITY_ERROR",
    "NOT_APPLICABLE": "PLAN_CONFIG_ERROR",
    "RECORD_MISMATCH": "RECORD_MISMATCH",
}


def build_failure_evidence(
    *,
    plan: VerificationPlan,
    readiness: VerificationReadiness,
    store,
    effective_subject_resolver=None,
    observed_at_ms: int,
) -> list[FailureEvidence]:
    """Derive FailureEvidence for every non-PASS required entry.

    ``effective_subject_resolver``: optional callable(plan_entry_id) →
    the current effective subject identity (after successor chain
    resolution). Falls back to the plan entry's original subject.
    """
    if not plan.has_valid_identity():
        raise FailureEvidenceBuilderError("plan identity is invalid")
    if not readiness.has_valid_identity():
        raise FailureEvidenceBuilderError("readiness identity is invalid")
    if readiness.verification_plan_id != plan.verification_plan_id:
        raise FailureEvidenceBuilderError(
            "readiness does not correspond to the given plan"
        )

    assessment_by_entry = {
        a.plan_entry_id: a for a in readiness.entry_assessments
    }
    all_records = store.list_verification_records(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
    )
    record_by_id = {r.verification_record_id: r for r in all_records}

    results: list[FailureEvidence] = []
    for entry in plan.entries:
        if not entry.required:
            continue
        assessment = assessment_by_entry.get(entry.plan_entry_id)
        if assessment is None:
            continue
        if assessment.status == "PASS":
            continue

        # Resolve effective subject (successor chain)
        effective_subject = entry.subject_identity
        if effective_subject_resolver is not None:
            resolved = effective_subject_resolver(entry.plan_entry_id)
            if resolved:
                effective_subject = resolved

        # Find the authoritative record (if any)
        record = None
        if assessment.verification_record_id:
            record = record_by_id.get(assessment.verification_record_id)

        # For MISSING: prove the store truly has no matching record
        if assessment.status == "MISSING" and record is not None:
            raise FailureEvidenceBuilderError(
                "readiness says MISSING but a record exists for this entry"
            )

        verification_evidence_sha = "0" * 64
        if record is not None:
            for ref in record.evidence_refs:
                if ref.startswith("predicate_sha256:"):
                    verification_evidence_sha = ref.split(":", 1)[1]
                    break
            if verification_evidence_sha == "0" * 64:
                verification_evidence_sha = record.evidence_sha256

        reason_codes = tuple(record.reason_codes) if record else ()
        failure_kind = _STATUS_TO_KIND.get(
            assessment.status, "AUTHORITY_ERROR"
        )
        failure_sig = derive_failure_signature(
            plan_entry_id=entry.plan_entry_id,
            effective_subject_identity=effective_subject,
            predicate_sha256=entry.predicate.predicate_sha256,
            verification_status=assessment.status,
            reason_codes=reason_codes,
            verification_evidence_sha256=verification_evidence_sha,
        )

        evidence = FailureEvidence(
            failure_evidence_id="vfe_" + "0" * 64,
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
            verification_plan_id=plan.verification_plan_id,
            verification_plan_sha256=plan.plan_sha256,
            registry_snapshot_sha256=plan.registry_snapshot_sha256,
            plan_entry_id=entry.plan_entry_id,
            plan_entry_sha256=entry.entry_sha256,
            verifier_id=entry.verifier_id,
            verifier_version=entry.verifier_version,
            predicate_id=entry.predicate.predicate_id,
            predicate_sha256=entry.predicate.predicate_sha256,
            predicate_type=entry.predicate.predicate_type,
            subject_kind=entry.predicate.subject_kind,
            original_subject_identity=entry.subject_identity,
            effective_subject_identity=effective_subject,
            verification_record_id=(
                record.verification_record_id if record else None
            ),
            verification_result_sha256=(
                record.result_sha256 if record else None
            ),
            verification_status=assessment.status,
            readiness_id=readiness.verification_readiness_id,
            readiness_sha256=readiness.readiness_sha256,
            failure_kind=failure_kind,
            reason_codes=reason_codes,
            evidence_refs=tuple(record.evidence_refs) if record else (),
            verification_evidence_sha256=verification_evidence_sha,
            failure_signature_sha256=failure_sig,
            observed_at_ms=observed_at_ms,
            failure_evidence_sha256="0" * 64,
        ).with_computed_sha256()
        results.append(evidence)

    return results


__all__ = [
    "FailureEvidenceBuilderError",
    "build_failure_evidence",
]
