"""Shared infrastructure for P19-R2 outcome oracles (M3).

Extracted so the effect/repository oracles do not copy the artifact
oracle's descriptor validation and record assembly (M2.2 review rule:
shared base, no second runtime, no mega manager). The artifact oracle
keeps its approved M2.2 implementation untouched.
"""

from __future__ import annotations

from typing import Any, Mapping

from contracts import canonical_sha256
from contracts.verification import (
    AcceptancePredicate,
    RegistrySnapshot,
    VerificationRecord,
    derive_verification_record_id,
)
from total_gateway.verification_registry import (
    UnknownVerifierError,
    VerifierRegistry,
)


class OracleSnapshotInvalid(ValueError):
    """Raised when an oracle cannot be bound to the given snapshot."""


def bind_snapshot_and_descriptor(
    snapshot: RegistrySnapshot,
    *,
    verifier_id: str,
    verifier_version: str,
    config_sha256: str,
    supported_predicate_types: frozenset[str],
    expectations: Mapping[str, Any],
    implementation_ref: str,
    timeout_ms: int | None = None,
):
    """Validate snapshot identity, registry integrity and the EXACT
    descriptor binding (M2.2 §2 discipline, shared across oracles)."""
    if not snapshot.has_valid_identity():
        raise OracleSnapshotInvalid("registry snapshot identity binding is invalid")
    try:
        registry = VerifierRegistry(snapshot.verifiers)
    except ValueError as exc:
        raise OracleSnapshotInvalid(
            "registry snapshot contains invalid verifier descriptors"
        ) from exc
    try:
        descriptor = registry.find(verifier_id, verifier_version)
    except UnknownVerifierError as exc:
        raise OracleSnapshotInvalid(
            f"snapshot does not carry {verifier_id}@{verifier_version}"
        ) from exc
    problems: list[str] = []
    if descriptor.config_sha256 != config_sha256:
        problems.append("config_sha256")
    if list(descriptor.supported_predicate_types) != sorted(
        supported_predicate_types
    ):
        problems.append("supported_predicate_types")
    if tuple(descriptor.supported_subject_kinds) != expectations[
        "supported_subject_kinds"
    ]:
        problems.append("supported_subject_kinds")
    if set(descriptor.accepted_authorities) != set(
        expectations["accepted_authorities"]
    ):
        problems.append("accepted_authorities")
    if descriptor.implementation_ref != implementation_ref:
        problems.append("implementation_ref")
    if descriptor.producer_component_id != expectations["producer_component_id"]:
        problems.append("producer_component_id")
    if descriptor.layer != expectations["layer"]:
        problems.append("layer")
    if descriptor.deterministic is not expectations["deterministic"]:
        problems.append("deterministic")
    if descriptor.default_enforcement != expectations["default_enforcement"]:
        problems.append("default_enforcement")
    if descriptor.block_capable is not expectations["block_capable"]:
        problems.append("block_capable")
    if descriptor.repair_feedback_capable is not expectations[
        "repair_feedback_capable"
    ]:
        problems.append("repair_feedback_capable")
    if timeout_ms is not None and descriptor.timeout_ms != timeout_ms:
        problems.append("timeout_ms")
    if problems:
        raise OracleSnapshotInvalid(
            f"{verifier_id} descriptor does not match the oracle config:"
            f" {', '.join(problems)}"
        )
    return snapshot, descriptor


def assemble_record(
    *,
    descriptor,
    snapshot: RegistrySnapshot,
    predicate: AcceptancePredicate,
    subject_kind: str,
    subject_identity: str,
    request_id: str,
    run_id: str,
    generation: int,
    status: str,
    reason_codes: tuple[str, ...],
    evidence_refs: tuple[str, ...],
    observation: dict[str, Any],
    evaluated_at_ms: int,
    evaluation_phase: str,
) -> VerificationRecord:
    """Assemble a RECORD-mode VerificationRecord with derived identity."""
    observation_payload = {
        key: (list(value) if isinstance(value, (list, tuple)) else value)
        for key, value in sorted(observation.items())
    }
    refs = evidence_refs + (
        f"predicate_sha256:{predicate.predicate_sha256}",
        f"observation_sha256:{canonical_sha256(observation_payload)}",
    )
    payload = dict(
        verification_record_id="vrs_" + "0" * 64,
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        verifier_id=descriptor.verifier_id,
        verifier_version=descriptor.verifier_version,
        registry_snapshot_sha256=snapshot.snapshot_sha256,
        predicate_id=predicate.predicate_id,
        predicate_type=predicate.predicate_type,
        subject_kind=subject_kind,
        subject_identity=subject_identity,
        evaluation_phase=evaluation_phase,
        status=status,
        enforcement="RECORD",
        reason_codes=reason_codes,
        evidence_refs=refs,
        evidence_sha256=canonical_sha256(list(refs)),
        producer_component_id=descriptor.producer_component_id,
        model_generated=False,
        evaluated_at_ms=evaluated_at_ms,
        result_sha256="0" * 64,
    )
    record = VerificationRecord(**payload).with_computed_sha256()
    return record.model_copy(
        update={
            "verification_record_id": derive_verification_record_id(
                result_sha256=record.result_sha256
            )
        }
    )


__all__ = [
    "OracleSnapshotInvalid",
    "assemble_record",
    "bind_snapshot_and_descriptor",
]
