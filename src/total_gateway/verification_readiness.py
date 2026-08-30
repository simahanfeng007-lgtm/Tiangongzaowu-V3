"""P19-R2 M4.1 VerificationReadinessBuilder — the sole trusted readiness source.

M4.1 §5-§7: VerificationReadiness is NEVER hand-written. It is
materialized from the active VerificationPlan + real VerificationRecords
in the GatewayStateStore. The builder does exact per-entry matching and
the store write re-verifies the same computation before persisting.

Architecture boundary: this module has NO completion authority. It
computes readiness; CompletionGate consumes it.
"""

from __future__ import annotations

from typing import Any

from contracts.canonical import canonical_sha256
from contracts.verification import (
    AcceptancePredicate,
    EntryAssessment,
    RegistrySnapshot,
    VerificationPlan,
    VerificationReadiness,
)
from total_gateway.verification_registry import VerifierRegistry


class ReadinessBuilderError(RuntimeError):
    """Raised when readiness cannot be built from authoritative inputs."""


def _record_matches_entry(
    record,
    entry,
    *,
    plan_registry_sha256: str,
    request_id: str,
    run_id: str,
    generation: int,
) -> bool:
    """M4.1 §5: exact one-to-one matching — any difference rejects."""
    if record.request_id != request_id:
        return False
    if record.run_id != run_id:
        return False
    if record.generation != generation:
        return False
    if record.registry_snapshot_sha256 != plan_registry_sha256:
        return False
    if record.verifier_id != entry.verifier_id:
        return False
    if record.verifier_version != entry.verifier_version:
        return False
    if record.predicate_id != entry.predicate.predicate_id:
        return False
    if record.predicate_type != entry.predicate.predicate_type:
        return False
    if record.subject_kind != entry.predicate.subject_kind:
        return False
    if record.subject_identity != entry.subject_identity:
        return False
    if record.evaluation_phase != entry.evaluation_phase:
        return False
    if record.enforcement != "RECORD":
        return False
    if not record.has_valid_identity():
        return False
    # §5: the record must carry the exact predicate_sha256 in its
    # evidence_refs — different params of the same predicate_type
    # produce different sha256 and cannot masquerade.
    expected_ref = f"predicate_sha256:{entry.predicate.predicate_sha256}"
    if expected_ref not in record.evidence_refs:
        return False
    return True


def build_readiness(
    *,
    plan: VerificationPlan,
    snapshot: RegistrySnapshot,
    store,
    evaluated_at_ms: int,
) -> VerificationReadiness:
    """Materialize a VerificationReadiness from plan + authoritative records.

    M4.1 §6: the readiness is COMPUTED, never self-signed. Every field
    derives from the plan and the store's actual records.

    M4.1 Final §8 supersession: the LATEST authoritative record (by
    evaluated_at_ms) for an entry is the current verdict. If multiple
    records share the same max evaluated_at_ms with conflicting verdicts,
    the entry becomes RECORD_MISMATCH (never an arbitrary pick).
    """
    if not plan.has_valid_identity():
        raise ReadinessBuilderError("plan identity is invalid")
    if not snapshot.has_valid_identity():
        raise ReadinessBuilderError("registry snapshot identity is invalid")
    if plan.registry_snapshot_sha256 != snapshot.snapshot_sha256:
        raise ReadinessBuilderError(
            "plan registry snapshot does not match the authoritative snapshot"
        )
    try:
        registry = VerifierRegistry(snapshot.verifiers)
    except ValueError as exc:
        raise ReadinessBuilderError(
            "registry snapshot contains invalid descriptors"
        ) from exc
    for entry in plan.entries:
        try:
            descriptor = registry.find(entry.verifier_id, entry.verifier_version)
        except Exception:
            raise ReadinessBuilderError(
                f"plan entry verifier not in registry: {entry.verifier_id}"
                f"@{entry.verifier_version}"
            )
        if entry.predicate.predicate_type not in descriptor.supported_predicate_types:
            raise ReadinessBuilderError(
                f"plan entry predicate not supported: {entry.predicate.predicate_type}"
            )

    all_records = store.list_verification_records(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
    )
    assessments: list[EntryAssessment] = []
    supporting_record_ids: list[str] = []
    required_count = 0
    satisfied_count = 0

    for entry in sorted(plan.entries, key=lambda e: e.plan_entry_id):
        matching = [
            r for r in all_records
            if _record_matches_entry(
                r, entry,
                plan_registry_sha256=plan.registry_snapshot_sha256,
                request_id=plan.request_id,
                run_id=plan.run_id,
                generation=plan.generation,
            )
        ]
        if not matching:
            if entry.required:
                required_count += 1
            assessments.append(
                EntryAssessment(
                    plan_entry_id=entry.plan_entry_id,
                    status="MISSING",
                )
            )
            continue

        # M4.1 Final §8 supersession: latest evaluated_at_ms wins; ties
        # with conflicting verdicts are RECORD_MISMATCH.
        matching = sorted(
            matching, key=lambda r: (r.evaluated_at_ms, r.verification_record_id),
        )
        latest_ts = matching[-1].evaluated_at_ms
        latest = [r for r in matching if r.evaluated_at_ms == latest_ts]
        verdicts = {r.status for r in latest}
        if len(verdicts) > 1:
            if entry.required:
                required_count += 1
            assessments.append(
                EntryAssessment(
                    plan_entry_id=entry.plan_entry_id,
                    status="RECORD_MISMATCH",
                )
            )
            continue
        current = latest[-1]
        assessments.append(
            EntryAssessment(
                plan_entry_id=entry.plan_entry_id,
                status=current.status,
                verification_record_id=current.verification_record_id,
            )
        )
        supporting_record_ids.append(current.verification_record_id)
        if entry.required:
            required_count += 1
            if current.status == "PASS":
                satisfied_count += 1

    all_required_pass = (
        required_count > 0 and satisfied_count == required_count
    )
    required_assessments = [
        a for a in assessments
        if any(
            e.plan_entry_id == a.plan_entry_id and e.required
            for e in plan.entries
        )
    ]
    has_mandatory_na = any(
        a.status == "NOT_APPLICABLE" for a in required_assessments
    )
    has_mismatch = any(a.status == "RECORD_MISMATCH" for a in required_assessments)
    has_error = any(a.status == "ERROR" for a in required_assessments)
    has_fail = any(a.status == "FAIL" for a in required_assessments)
    has_missing_or_inconclusive = any(
        a.status in ("MISSING", "INCONCLUSIVE") for a in required_assessments
    )

    if all_required_pass and not has_mandatory_na:
        verification_ready = True
        failure_class = "NONE"
    elif has_mandatory_na or has_mismatch:
        verification_ready = False
        failure_class = "PLAN_CONFIG_ERROR"
    elif has_error:
        verification_ready = False
        failure_class = "AUTHORITY_ERROR"
    elif has_fail:
        verification_ready = False
        failure_class = "VERIFICATION_FAILED"
    else:
        verification_ready = False
        failure_class = "MISSING_EVIDENCE"

    return VerificationReadiness(
        verification_readiness_id="vrd_" + "0" * 64,
        verification_plan_id=plan.verification_plan_id,
        verification_plan_sha256=plan.plan_sha256,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        registry_snapshot_sha256=plan.registry_snapshot_sha256,
        required_entry_count=required_entry_count,
        satisfied_entry_count=satisfied_count,
        entry_assessments=tuple(
            sorted(assessments, key=lambda a: a.plan_entry_id)
        ),
        supporting_verification_record_ids=tuple(
            sorted(set(supporting_record_ids))
        ),
        verification_ready=verification_ready,
        failure_class=failure_class,
        evaluated_at_ms=evaluated_at_ms,
        readiness_sha256="0" * 64,
    ).with_computed_sha256()


__all__ = [
    "ReadinessBuilderError",
    "build_readiness",
]
