"""P19-R2 M5 §16-17: VerificationSubjectSuccessor + Store v28 APIs.

Subject supersession: repair produces a NEW authoritative reality object
(new artifact revision / new effect). The plan stays immutable — the
successor chain tracks which subject is currently effective for each
plan entry.
"""

from __future__ import annotations

from contracts.canonical import canonical_sha256
from contracts.verification import (
    FailureEvidence,
    RepairDirective,
    VerificationDisposition,
)
from contracts.verification import ContractModel, Field, Literal, Sha256, RequestId, RunId, SubjectKind, OpaqueId

_SUBJECT_SUCCESSOR_SCHEMA = "tiangong.verification_subject_successor.v1"


class VerificationSubjectSuccessor(ContractModel):
    """Append-only binding: old subject → new subject after repair.

    Only RepairDirective can create a successor (§17). The Store
    validates predecessor == current effective subject, directive
    belongs to the entry, successor exists in authoritative reality.
    """
    model_config = ContractModel.model_config.copy()
    model_config = type(model_config)(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": "urn:tiangong:gateway:contracts:v2:VerificationSubjectSuccessor",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["VerificationSubjectSuccessor"] = "VerificationSubjectSuccessor"
    schema_version: Literal[_SUBJECT_SUCCESSOR_SCHEMA] = _SUBJECT_SUCCESSOR_SCHEMA
    successor_binding_id: str = Field(pattern=r"^vss_[0-9a-f]{64}$")
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    verification_plan_id: str = Field(pattern=r"^vpl_[0-9a-f]{64}$")
    plan_entry_id: str = Field(pattern=r"^vpe_[0-9a-f]{64}$")
    subject_kind: SubjectKind
    predecessor_subject_identity: str = Field(min_length=1, max_length=400)
    successor_subject_identity: str = Field(min_length=1, max_length=400)
    repair_directive_id: str = Field(pattern=r"^vrd_[0-9a-f]{64}$")
    repair_directive_sha256: Sha256
    produced_by_effect_id: str = Field(min_length=1, max_length=400)
    repair_attempt_no: int = Field(ge=1)
    bound_at_ms: int = Field(ge=0)
    successor_binding_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(
                mode="json",
                exclude={"successor_binding_sha256", "successor_binding_id"},
            )
        )

    def has_valid_sha256(self) -> bool:
        return self.successor_binding_sha256 == self.computed_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_sha256():
            return False
        return self.successor_binding_id == "vss_" + canonical_sha256(
            {"domain": self.schema_version, "sha": self.successor_binding_sha256}
        )

    def with_computed_sha256(self) -> VerificationSubjectSuccessor:
        sha = self.computed_sha256()
        partial = self.model_copy(update={"successor_binding_sha256": sha})
        return partial.model_copy(update={
            "successor_binding_id": "vss_" + canonical_sha256(
                {"domain": self.schema_version, "sha": sha}
            )
        })


# ---------------------------------------------------------------------------
# RepairAttemptRecord (§20)
# ---------------------------------------------------------------------------

_REPAIR_ATTEMPT_SCHEMA = "tiangong.repair_attempt.v1"

ExecutionOutcome = Literal[
    "DISPATCHED", "EXECUTION_FAILED", "EXECUTION_AMBIGUOUS",
    "REVERIFY_PASS", "REVERIFY_FAIL", "REVERIFY_ERROR",
]


class RepairAttemptRecord(ContractModel):
    """Gateway audit binding for one repair attempt (§20).

    NOT a second ExecutionLedger — the real execution authority
    remains in the existing EffectLedger / execution system.
    """
    model_config = type(ContractModel.model_config)(
        extra="forbid", frozen=True, strict=True,
    )
    schema_id: Literal["RepairAttemptRecord"] = "RepairAttemptRecord"
    schema_version: Literal[_REPAIR_ATTEMPT_SCHEMA] = _REPAIR_ATTEMPT_SCHEMA
    repair_attempt_id: str = Field(pattern=r"^vra_[0-9a-f]{64}$")
    repair_directive_id: str = Field(pattern=r"^vrd_[0-9a-f]{64}$")
    repair_attempt_no: int = Field(ge=1)
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    plan_entry_id: str = Field(pattern=r"^vpe_[0-9a-f]{64}$")
    prior_subject_identity: str = Field(min_length=1, max_length=400)
    produced_subject_identity: str = Field(min_length=1, max_length=400)
    execution_effect_ids: tuple[str, ...] = Field(default=(), max_length=64)
    execution_outcome: ExecutionOutcome
    reverify_record_id: str | None = None
    started_at_ms: int = Field(ge=0)
    finished_at_ms: int = Field(ge=0)
    attempt_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"attempt_sha256", "repair_attempt_id"})
        )

    def has_valid_sha256(self) -> bool:
        return self.attempt_sha256 == self.computed_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_sha256():
            return False
        return self.repair_attempt_id == "vra_" + canonical_sha256(
            {"domain": self.schema_version, "sha": self.attempt_sha256}
        )

    def with_computed_sha256(self) -> RepairAttemptRecord:
        sha = self.computed_sha256()
        partial = self.model_copy(update={"attempt_sha256": sha})
        return partial.model_copy(update={
            "repair_attempt_id": "vra_" + canonical_sha256(
                {"domain": self.schema_version, "sha": sha}
            )
        })


__all__ = [
    "ExecutionOutcome",
    "RepairAttemptRecord",
    "VerificationSubjectSuccessor",
]
