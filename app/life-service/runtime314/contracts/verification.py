"""P19-R2 M1 verification contracts.

Typed, versioned, hashed contracts for the minimal verification plane.
M1 scope (2026-08-29 review approval):

* ``VerifierDescriptor`` / ``RegistrySnapshot`` / ``VerificationRecord``;
* strict/frozen/extra-forbid models with canonical SHA-256 and
  deterministic IDs;
* enforcement is persistence-level RECORD only. ALERT/BLOCK carry no
  meaning in this milestone and are rejected by the recorder.

Deliberately NOT here (later milestones): VerificationPlan,
AcceptancePredicate, FailureEvidence, RepairDirective, plan compilers,
and any completion-gate binding. The obligation upstream is undecided
(see docs/p19-r2/BASELINE_AUDIT.txt finding 1) and M1 must not pick one.

Hash honesty: every ``*_sha256`` below is a plain unkeyed content hash.
It proves internal consistency and byte-identical recomputation, not
producer authenticity — trust rests on controlled call paths and module
boundaries, exactly like CompletionDecision today.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from .canonical import canonical_sha256
from .models import SCHEMA_BASE, ContractModel, OpaqueId, RequestId, RunId, Sha256


# ---------------------------------------------------------------------------
# Enumerations (Literal aliases — house style)
# ---------------------------------------------------------------------------

VerificationLayer = Literal["L0_DETERMINISTIC", "L1_GROUNDING", "L2_JUDGE", "L3_HUMAN"]
EnforcementMode = Literal["RECORD", "ALERT", "BLOCK"]

#: M1 only ever persists RECORD; the wider enum exists so the contract
#: does not need a breaking change when later milestones earn ALERT/BLOCK.
RecordedEnforcement = Literal["RECORD"]

#: NOT_APPLICABLE exists so "this predicate should not have been called
#: for this subject" is explicit instead of being smuggled in as
#: INCONCLUSIVE (2026-08-29 review rule).
VerificationStatus = Literal[
    "PASS", "FAIL", "INCONCLUSIVE", "ERROR", "NOT_APPLICABLE",
]

EvidenceAuthority = Literal[
    "OBJECT_STORE",
    "ARTIFACT_MANIFEST",
    "ARTIFACT_QC",
    "EFFECT_LEDGER",
    "FACT_LEDGER",
    "TOOL_RESULT_CONTRACT",
    "REPOSITORY_PROVIDER",
    "DELIVERY_RECEIPT",
    "KNOWLEDGE_CONTEXT",
    "HUMAN_LABEL",
]

#: Describes only *where in the lifecycle the evidence was produced*
#: (desktop gates pre-delivery, channels gate at delivery finalization —
#: BASELINE_AUDIT finding 2). It grants no blocking power.
EvaluationPhase = Literal[
    "POST_EXECUTION", "PRE_DELIVERY", "DELIVERY_FINALIZATION", "ASYNC_OBSERVATION",
]

SubjectKind = Literal["artifact", "effect", "repository", "delivery", "text", "handoff"]

_VERIFIER_DESCRIPTOR_SCHEMA_VERSION = "tiangong.verifier_descriptor.v1"
_REGISTRY_SNAPSHOT_SCHEMA_VERSION = "tiangong.verification_registry_snapshot.v1"
_VERIFICATION_RECORD_SCHEMA_VERSION = "tiangong.verification_record.v1"

PredicateType = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"),
]


# ---------------------------------------------------------------------------
# VerifierDescriptor
# ---------------------------------------------------------------------------

class VerifierDescriptor(ContractModel):
    """Static, versioned description of one deterministic verifier."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:VerifierDescriptor",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_id: Literal["VerifierDescriptor"] = "VerifierDescriptor"
    schema_version: Literal[_VERIFIER_DESCRIPTOR_SCHEMA_VERSION] = (
        _VERIFIER_DESCRIPTOR_SCHEMA_VERSION
    )
    verifier_id: OpaqueId
    verifier_version: str = Field(min_length=1, max_length=64)
    layer: VerificationLayer
    deterministic: bool
    supported_predicate_types: tuple[PredicateType, ...] = Field(min_length=1)
    accepted_authorities: tuple[EvidenceAuthority, ...] = Field(min_length=1)
    supported_subject_kinds: tuple[SubjectKind, ...] = Field(min_length=1)
    max_input_bytes: int = Field(gt=0)
    timeout_ms: int = Field(gt=0)
    default_enforcement: EnforcementMode
    block_capable: bool
    repair_feedback_capable: bool
    producer_component_id: OpaqueId
    config_sha256: Sha256
    implementation_ref: str = Field(min_length=1, max_length=400)
    descriptor_sha256: Sha256

    @model_validator(mode="after")
    def _validate_descriptor(self) -> VerifierDescriptor:
        if self.layer == "L2_JUDGE":
            if self.deterministic:
                raise ValueError("L2_JUDGE verifiers can never be deterministic")
            if self.default_enforcement == "BLOCK":
                raise ValueError("L2_JUDGE default enforcement may only be RECORD/ALERT")
        if self.default_enforcement == "BLOCK" and not self.block_capable:
            raise ValueError("BLOCK default enforcement requires block_capable=True")
        if self.block_capable and self.layer not in ("L0_DETERMINISTIC", "L3_HUMAN"):
            raise ValueError("only L0_DETERMINISTIC/L3_HUMAN verifiers may be block-capable")
        if len(set(self.supported_predicate_types)) != len(self.supported_predicate_types):
            raise ValueError("supported_predicate_types must be unique")
        if list(self.supported_predicate_types) != sorted(self.supported_predicate_types):
            raise ValueError("supported_predicate_types must be sorted")
        if len(set(self.accepted_authorities)) != len(self.accepted_authorities):
            raise ValueError("accepted_authorities must be unique")
        if len(set(self.supported_subject_kinds)) != len(self.supported_subject_kinds):
            raise ValueError("supported_subject_kinds must be unique")
        return self

    def computed_descriptor_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(
                mode="json", exclude={"descriptor_sha256"}
            )
        )

    def has_valid_descriptor_sha256(self) -> bool:
        return self.descriptor_sha256 == self.computed_descriptor_sha256()

    def with_computed_sha256(self) -> VerifierDescriptor:
        return self.model_copy(
            update={"descriptor_sha256": self.computed_descriptor_sha256()}
        )


def derive_verifier_descriptor_id(*, verifier_id: str, verifier_version: str) -> str:
    return "vfd_" + canonical_sha256(
        {
            "domain": _VERIFIER_DESCRIPTOR_SCHEMA_VERSION,
            "verifier_id": verifier_id,
            "verifier_version": verifier_version,
        }
    )


# ---------------------------------------------------------------------------
# RegistrySnapshot
# ---------------------------------------------------------------------------

class RegistrySnapshot(ContractModel):
    """Immutable, hashable snapshot of the verifier registry."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:RegistrySnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_id: Literal["RegistrySnapshot"] = "RegistrySnapshot"
    schema_version: Literal[_REGISTRY_SNAPSHOT_SCHEMA_VERSION] = (
        _REGISTRY_SNAPSHOT_SCHEMA_VERSION
    )
    registry_snapshot_id: str = Field(pattern=r"^vrg_[0-9a-f]{64}$")
    verifiers: tuple[VerifierDescriptor, ...] = Field(min_length=1)
    captured_at_ms: int = Field(ge=0)
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def _validate_snapshot(self) -> RegistrySnapshot:
        identifiers = [verifier.verifier_id for verifier in self.verifiers]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("registry snapshot contains duplicate verifier_id")
        if identifiers != sorted(identifiers):
            raise ValueError("registry snapshot verifiers must be sorted by verifier_id")
        return self

    def computed_snapshot_sha256(self) -> str:
        # The hash covers the descriptor set only. captured_at_ms is
        # metadata (same registry state at another time = same identity),
        # and registry_snapshot_id is derived *from* the hash so it can
        # never be part of its own preimage.
        return canonical_sha256(
            self.model_dump(
                mode="json",
                exclude={"snapshot_sha256", "registry_snapshot_id", "captured_at_ms"},
            )
        )

    def has_valid_snapshot_sha256(self) -> bool:
        return self.snapshot_sha256 == self.computed_snapshot_sha256()

    def has_valid_identity(self) -> bool:
        """Identity binding: derived id must match the (valid) hash.

        Pure method only — real trust boundaries (Recorder/Store) must
        re-verify, because ``model_copy(update=...)`` bypasses validation.
        """
        return self.has_valid_snapshot_sha256() and (
            self.registry_snapshot_id
            == derive_registry_snapshot_id(snapshot_sha256=self.snapshot_sha256)
        )

    def with_computed_sha256(self) -> RegistrySnapshot:
        return self.model_copy(
            update={"snapshot_sha256": self.computed_snapshot_sha256()}
        )

    def find(self, verifier_id: str) -> VerifierDescriptor | None:
        for verifier in self.verifiers:
            if verifier.verifier_id == verifier_id:
                return verifier
        return None


def derive_registry_snapshot_id(*, snapshot_sha256: str) -> str:
    return "vrg_" + canonical_sha256(
        {"domain": _REGISTRY_SNAPSHOT_SCHEMA_VERSION, "snapshot_sha256": snapshot_sha256}
    )


# ---------------------------------------------------------------------------
# VerificationRecord
# ---------------------------------------------------------------------------

class VerificationRecord(ContractModel):
    """One persisted verifier outcome. M1 stores RECORD-mode records only.

    ``status`` is the single-predicate verdict of an ideal verifier and is
    independent of delivery decisions: INCONCLUSIVE must never be folded
    into PASS, ERROR must never be silently dropped, and NOT_APPLICABLE
    marks "should not have been evaluated" instead of abusing INCONCLUSIVE.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:VerificationRecord",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_id: Literal["VerificationRecord"] = "VerificationRecord"
    schema_version: Literal[_VERIFICATION_RECORD_SCHEMA_VERSION] = (
        _VERIFICATION_RECORD_SCHEMA_VERSION
    )
    verification_record_id: str = Field(pattern=r"^vrs_[0-9a-f]{64}$")
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    verifier_id: OpaqueId
    verifier_version: str = Field(min_length=1, max_length=64)
    registry_snapshot_sha256: Sha256
    predicate_id: OpaqueId
    predicate_type: PredicateType
    subject_kind: SubjectKind
    subject_identity: str = Field(min_length=1, max_length=400)
    evaluation_phase: EvaluationPhase
    status: VerificationStatus
    enforcement: RecordedEnforcement
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    evidence_sha256: Sha256
    producer_component_id: OpaqueId
    model_generated: bool = False
    evaluated_at_ms: int = Field(ge=0)
    result_sha256: Sha256

    @model_validator(mode="after")
    def _validate_record(self) -> VerificationRecord:
        if self.status == "PASS" and self.reason_codes:
            raise ValueError("PASS records must not carry reason codes")
        if self.status == "NOT_APPLICABLE" and self.reason_codes:
            raise ValueError("NOT_APPLICABLE records must not carry blocking reason codes")
        if self.model_generated and self.enforcement != "RECORD":
            raise ValueError("model-generated records can never be recorded beyond RECORD")
        return self

    def computed_result_sha256(self) -> str:
        # verification_record_id is derived from result_sha256, so it is
        # excluded from its own preimage (same deadlock rule as snapshots).
        return canonical_sha256(
            self.model_dump(
                mode="json", exclude={"result_sha256", "verification_record_id"}
            )
        )

    def has_valid_result_sha256(self) -> bool:
        return self.result_sha256 == self.computed_result_sha256()

    def has_valid_identity(self) -> bool:
        """Identity binding: derived id must match the (valid) hash.

        Pure method only — real trust boundaries (Recorder/Store) must
        re-verify, because ``model_copy(update=...)`` bypasses validation.
        """
        return self.has_valid_result_sha256() and (
            self.verification_record_id
            == derive_verification_record_id(result_sha256=self.result_sha256)
        )

    def with_computed_sha256(self) -> VerificationRecord:
        return self.model_copy(update={"result_sha256": self.computed_result_sha256()})


def derive_verification_record_id(*, result_sha256: str) -> str:
    return "vrs_" + canonical_sha256(
        {"domain": _VERIFICATION_RECORD_SCHEMA_VERSION, "result_sha256": result_sha256}
    )


__all__ = [
    "EnforcementMode",
    "EvaluationPhase",
    "EvidenceAuthority",
    "PredicateType",
    "RecordedEnforcement",
    "RegistrySnapshot",
    "SubjectKind",
    "VerificationLayer",
    "VerificationRecord",
    "VerificationStatus",
    "VerifierDescriptor",
    "derive_registry_snapshot_id",
    "derive_verification_record_id",
    "derive_verifier_descriptor_id",
]
