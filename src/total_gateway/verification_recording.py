"""P19-R2 M1 RECORD-only verification recorder.

Validates ``VerificationRecord`` payloads against an immutable registry
snapshot before handing them to ``GatewayStateStore``. This module never
runs verifiers, never touches request state, and rejects anything that
tries to be more than RECORD.

Rejected at this layer:
* enforcement != RECORD (ALERT/BLOCK have no meaning in M1);
* verifier/version not present in the pinned registry snapshot;
* registry_snapshot_sha256 != the pinned snapshot hash (stale snapshot);
* predicate_type not declared by the descriptor;
* subject_kind not supported by the descriptor;
* tampered result hashes (store re-checks as well).
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.verification import RegistrySnapshot, VerificationRecord
from total_gateway.verification_registry import (
    UnknownVerifierError,
    VerifierRegistry,
)


class VerificationRecordRejected(ValueError):
    """Raised when a record fails RECORD-mode validation."""


@dataclass(frozen=True)
class RecordOutcome:
    created_by_this_call: bool
    duplicate: bool
    recorded_at_ms: int


class VerificationRecorder:
    """Binding between a pinned registry snapshot and the store."""

    def __init__(self, *, snapshot: RegistrySnapshot, store) -> None:
        # Trust boundary: model_copy(update=...) bypasses pydantic
        # validation, so identity binding and nested descriptor integrity
        # are re-verified here instead of trusting the constructor path.
        if not snapshot.has_valid_identity():
            raise VerificationRecordRejected("registry snapshot identity binding is invalid")
        try:
            # Reuse the registry's fail-closed rules (descriptor hashes,
            # duplicates, predicate allowlist) instead of duplicating them.
            VerifierRegistry(snapshot.verifiers)
        except ValueError as exc:
            raise VerificationRecordRejected(
                "registry snapshot contains invalid verifier descriptors"
            ) from exc
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_store", store)

    @property
    def snapshot(self) -> RegistrySnapshot:
        return self._snapshot  # type: ignore[attr-defined]

    def record(self, record: VerificationRecord, *, recorded_at_ms: int) -> RecordOutcome:
        self._validate(record)
        persisted = self._store.put_verification_record(  # type: ignore[attr-defined]
            record, recorded_at_ms=recorded_at_ms
        )
        return RecordOutcome(
            created_by_this_call=persisted.created_by_this_call,
            duplicate=persisted.duplicate,
            recorded_at_ms=persisted.recorded_at_ms,
        )

    def _validate(self, record: VerificationRecord) -> None:
        if record.enforcement != "RECORD":
            raise VerificationRecordRejected(
                f"M1 records must be enforcement=RECORD, got {record.enforcement}"
            )
        # Trust boundary re-verification: a model_copy(update=...) payload
        # with a valid-looking result hash but a mismatched derived id must
        # fail closed here, not depend on the caller's constructor discipline.
        if not record.has_valid_identity():
            raise VerificationRecordRejected(
                "record identity binding is invalid (derived id does not"
                " match its result hash)"
            )
        if record.registry_snapshot_sha256 != self._snapshot.snapshot_sha256:
            raise VerificationRecordRejected(
                "record was produced against a different registry snapshot"
            )
        descriptor = self._snapshot.find(record.verifier_id)
        if descriptor is None or descriptor.verifier_version != record.verifier_version:
            raise UnknownVerifierError(
                f"verifier not in pinned snapshot: {record.verifier_id}"
                f"@{record.verifier_version}"
            )
        if record.predicate_type not in descriptor.supported_predicate_types:
            raise VerificationRecordRejected(
                f"predicate type not declared by verifier: {record.predicate_type}"
            )
        if record.subject_kind not in descriptor.supported_subject_kinds:
            raise VerificationRecordRejected(
                f"subject kind not supported by verifier: {record.subject_kind}"
            )


__all__ = [
    "RecordOutcome",
    "VerificationRecordRejected",
    "VerificationRecorder",
]
