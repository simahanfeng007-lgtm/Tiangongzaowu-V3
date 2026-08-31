"""P19-R2 verification contracts.

M1 (2026-08-29): ``VerifierDescriptor`` / ``RegistrySnapshot`` /
``VerificationRecord`` — strict/frozen/extra-forbid models with
canonical SHA-256 and deterministic IDs; enforcement is persistence-level
RECORD only.

M2.1 (2026-08-30): ``AcceptancePredicate`` — one minimal, universal,
upstream-agnostic predicate identity shared by every verifier domain.
It says *what the predicate is*; it does not decide who compiles
predicates from user requests, who authorizes them, whether they may
BLOCK, or anything about CompletionDecision.

Deliberately NOT here (later milestones): VerificationPlan,
FailureEvidence, RepairDirective, plan compilers, and any
completion-gate binding. The obligation upstream is undecided
(see docs/p19-r2/BASELINE_AUDIT.txt finding 1).

Hash honesty: every ``*_sha256`` below is a plain unkeyed content hash.
It proves internal consistency and byte-identical recomputation, not
producer authenticity — trust rests on controlled call paths and module
boundaries, exactly like CompletionDecision today.
"""

from __future__ import annotations

import unicodedata
from types import MappingProxyType
from typing import Annotated, Any, Literal, Mapping, Union

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


# ---------------------------------------------------------------------------
# AcceptancePredicate (M2.1) — universal, upstream-agnostic predicate identity
# ---------------------------------------------------------------------------

_ACCEPTANCE_PREDICATE_SCHEMA_VERSION = "tiangong.acceptance_predicate.v1"

#: Frozen param value: scalar or an ordered tuple of strings (lists are
#: normalized: deduplicated + sorted, so input order never drifts identity).
PredicateParamScalar = Union[str, int, bool]
PredicateParamValue = Union[str, int, bool, tuple[str, ...]]


class AcceptancePredicateSpecError(ValueError):
    """Raised when predicate params violate the per-type parameter rules."""


#: Exact parameter rules for every predicate type that may be instantiated
#: today. Types outside this mapping have no rules yet and therefore cannot
#: be instantiated (fail-closed instead of guessed). Immutable mapping.
PREDICATE_PARAM_RULES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "artifact.nonempty": frozenset(),
        "artifact.min_visible_text_chars": frozenset({"min_chars"}),
        "xlsx.required_columns": frozenset({"columns"}),
        "xlsx.min_data_rows": frozenset({"min_rows"}),
        "text.required_markers": frozenset({"markers"}),
        "pptx.min_nonempty_slides": frozenset({"min_slides"}),
        # M3 effect predicates (first batch with authoritative evidence)
        "effect.terminal_succeeded": frozenset(),
        "effect.target_exists": frozenset({"target_path"}),
        "effect.target_sha256_matches": frozenset({"target_path", "sha256"}),
        "effect.required_change_observed": frozenset({"target_paths"}),
        # M3 repository predicates (first batch with authoritative evidence)
        "repository.required_paths_changed": frozenset({"paths"}),
        "repository.forbidden_paths_unchanged": frozenset({"paths"}),
        "repository.source_authority_valid": frozenset(),
        "repository.no_generated_mirror_direct_edit": frozenset(),
    }
)

#: predicate-type prefix -> the ONLY subject kind it may ever target.
#: A predicate whose subject_kind violates its domain is semantically
#: invalid no matter how correct its hashes are.
PREDICATE_SUBJECT_KINDS: Mapping[str, str] = MappingProxyType(
    {
        "artifact": "artifact",
        "docx": "artifact",
        "xlsx": "artifact",
        "pptx": "artifact",
        "csv": "artifact",
        "text": "artifact",
        "effect": "effect",
        "repository": "repository",
    }
)

#: Bounds applied to list-shaped params before hashing. They also bound the
#: number of per-item reason codes a single predicate can produce.
PREDICATE_MAX_LIST_ITEMS = 32
PREDICATE_MAX_ITEM_CHARS = 128


def normalize_predicate_text(value: str) -> str:
    """Canonical text form: NFKC + strip + casefold (exact-match basis)."""
    return unicodedata.normalize("NFKC", value).strip().casefold()


#: Path-shaped params keep case and unicode form (paths are case
#: sensitive); only strip/length bounds apply. Content-shaped lists
#: (columns/markers) use full normalize_predicate_text.
_PATH_PARAM_MAX_CHARS = 512
_SHA256_PATTERN = "0123456789abcdef"


def _normalize_path_param(value: Any, predicate_type: str, key: str) -> str:
    if not isinstance(value, str):
        raise AcceptancePredicateSpecError(
            f"{predicate_type}: param {key!r} must be a string path"
        )
    text = value.strip()
    if not text or len(text) > _PATH_PARAM_MAX_CHARS:
        raise AcceptancePredicateSpecError(
            f"{predicate_type}: param {key!r} must be 1..512 chars after strip"
        )
    return text


def _normalize_sha256_param(value: Any, predicate_type: str, key: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in _SHA256_PATTERN for char in value
    ):
        raise AcceptancePredicateSpecError(
            f"{predicate_type}: param {key!r} must be a lowercase 64-hex sha256"
        )
    return value


def normalize_predicate_params(
    predicate_type: str,
    params: Mapping[str, Any],
) -> tuple[tuple[str, PredicateParamValue], ...]:
    """Validate + normalize params into a deep-frozen, key-sorted tuple.

    Rules: exact key set per type (unknown keys rejected, missing keys
    rejected); list params are normalized per item (NFKC/strip/casefold),
    emptied of blanks, deduplicated and sorted — so both dict key order
    and list input order are identity-irrelevant.
    """
    allowed = PREDICATE_PARAM_RULES.get(predicate_type)
    if allowed is None:
        raise AcceptancePredicateSpecError(
            f"no parameter rules defined for predicate type: {predicate_type}"
        )
    unknown = set(params) - allowed
    if unknown:
        raise AcceptancePredicateSpecError(
            f"{predicate_type}: unknown params {sorted(unknown)};"
            f" allowed: {sorted(allowed)}"
        )
    missing = allowed - set(params)
    if missing:
        raise AcceptancePredicateSpecError(
            f"{predicate_type}: missing params {sorted(missing)}"
        )
    normalized: list[tuple[str, PredicateParamValue]] = []
    for key in sorted(params):
        value = params[key]
        if key in ("min_chars", "min_rows", "min_slides"):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AcceptancePredicateSpecError(
                    f"{predicate_type}: param {key!r} must be a non-negative int"
                )
            normalized.append((key, value))
        elif key in ("columns", "markers"):
            if not isinstance(value, (list, tuple)) or not value:
                raise AcceptancePredicateSpecError(
                    f"{predicate_type}: param {key!r} must be a non-empty list"
                )
            items: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise AcceptancePredicateSpecError(
                        f"{predicate_type}: param {key!r} items must be strings"
                    )
                text = normalize_predicate_text(item)
                if len(text) > PREDICATE_MAX_ITEM_CHARS:
                    raise AcceptancePredicateSpecError(
                        f"{predicate_type}: param {key!r} item exceeds"
                        f" {PREDICATE_MAX_ITEM_CHARS} chars after normalization"
                    )
                if text:
                    items.append(text)
            if not items:
                raise AcceptancePredicateSpecError(
                    f"{predicate_type}: param {key!r} has no usable items"
                )
            if len(items) > PREDICATE_MAX_LIST_ITEMS:
                raise AcceptancePredicateSpecError(
                    f"{predicate_type}: param {key!r} exceeds"
                    f" {PREDICATE_MAX_LIST_ITEMS} items"
                )
            # Deduplicate, then sort: input order must never drift identity.
            normalized.append((key, tuple(sorted(set(items)))))
        elif key in ("target_paths", "paths"):
            # Path lists: case-sensitive, no NFKC/casefold — identity must
            # preserve the exact path form.
            if not isinstance(value, (list, tuple)) or not value:
                raise AcceptancePredicateSpecError(
                    f"{predicate_type}: param {key!r} must be a non-empty list"
                )
            path_items = [
                _normalize_path_param(item, predicate_type, key) for item in value
            ]
            path_items = [item for item in path_items if item]
            if not path_items:
                raise AcceptancePredicateSpecError(
                    f"{predicate_type}: param {key!r} has no usable items"
                )
            if len(path_items) > PREDICATE_MAX_LIST_ITEMS:
                raise AcceptancePredicateSpecError(
                    f"{predicate_type}: param {key!r} exceeds"
                    f" {PREDICATE_MAX_LIST_ITEMS} items"
                )
            normalized.append((key, tuple(sorted(set(path_items)))))
        elif key == "target_path":
            normalized.append(
                (key, _normalize_path_param(value, predicate_type, key))
            )
        elif key == "sha256":
            normalized.append(
                (key, _normalize_sha256_param(value, predicate_type, key))
            )
        else:  # pragma: no cover - rules are exhaustive per allowed sets
            raise AcceptancePredicateSpecError(
                f"{predicate_type}: param {key!r} has no normalization rule"
            )
    return tuple(normalized)


class AcceptancePredicate(ContractModel):
    """Universal predicate identity — what the predicate IS, nothing more.

    ``predicate_id`` is deterministically derived from (schema version,
    predicate_type, subject_kind, normalized params); callers cannot name
    predicates freely. Params are stored as a key-sorted tuple of frozen
    values — no mutable dict survives inside the frozen model.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AcceptancePredicate",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_id: Literal["AcceptancePredicate"] = "AcceptancePredicate"
    schema_version: Literal[_ACCEPTANCE_PREDICATE_SCHEMA_VERSION] = (
        _ACCEPTANCE_PREDICATE_SCHEMA_VERSION
    )
    predicate_id: str = Field(pattern=r"^vpd_[0-9a-f]{64}$")
    predicate_type: PredicateType
    subject_kind: SubjectKind
    params: tuple[tuple[str, PredicateParamValue], ...] = Field(default=())
    predicate_sha256: Sha256

    @model_validator(mode="after")
    def _validate_predicate(self) -> AcceptancePredicate:
        keys = [key for key, _ in self.params]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError("predicate params must be a sorted, unique key tuple")
        for key, value in self.params:
            if isinstance(value, tuple):
                if (
                    not value
                    or list(value) != sorted(set(value))
                    or any(not isinstance(item, str) for item in value)
                ):
                    raise ValueError(
                        "predicate list params must be sorted, deduplicated"
                        " string tuples"
                    )
        # Trust-boundary seal: the stored params must be EXACTLY what
        # normalize_predicate_params produces for this type. This rejects
        # non-normalized payloads regardless of how they were built
        # (direct construction, model_validate_json, model_copy).
        expected = normalize_predicate_params(
            self.predicate_type, dict(self.params)
        )
        if self.params != expected:
            raise ValueError(
                "predicate params are not in canonical normalized form"
            )
        return self

    @classmethod
    def create(
        cls,
        *,
        predicate_type: str,
        subject_kind: str,
        params: Mapping[str, Any] | None = None,
    ) -> AcceptancePredicate:
        normalized = normalize_predicate_params(
            predicate_type, dict(params or {})
        )
        payload = {
            "schema_version": _ACCEPTANCE_PREDICATE_SCHEMA_VERSION,
            "predicate_type": predicate_type,
            "subject_kind": subject_kind,
            "params": [
                [key, list(value) if isinstance(value, tuple) else value]
                for key, value in normalized
            ],
        }
        predicate_sha256 = canonical_sha256(payload)
        predicate_id = "vpd_" + canonical_sha256(
            {
                "domain": _ACCEPTANCE_PREDICATE_SCHEMA_VERSION,
                "predicate_sha256": predicate_sha256,
            }
        )
        return cls(
            predicate_id=predicate_id,
            predicate_type=predicate_type,
            subject_kind=subject_kind,
            params=normalized,
            predicate_sha256=predicate_sha256,
        )

    def computed_predicate_sha256(self) -> str:
        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "predicate_type": self.predicate_type,
                "subject_kind": self.subject_kind,
                "params": [
                    [key, list(value) if isinstance(value, tuple) else value]
                    for key, value in self.params
                ],
            }
        )

    def has_valid_predicate_sha256(self) -> bool:
        return self.predicate_sha256 == self.computed_predicate_sha256()

    def has_valid_identity(self) -> bool:
        """Full semantic + cryptographic identity check (M2.2 seal).

        Verifies predicate_sha256 recomputation, derived predicate_id,
        canonical params form, AND the predicate_type/subject_kind domain
        correspondence. Trust boundaries call this — never a bare hash
        check — because model_copy(update=...) bypasses the validator.
        """
        expected_kind = PREDICATE_SUBJECT_KINDS.get(
            self.predicate_type.split(".", 1)[0]
        )
        if expected_kind is None or self.subject_kind != expected_kind:
            return False
        try:
            if self.params != normalize_predicate_params(
                self.predicate_type, dict(self.params)
            ):
                return False
        except AcceptancePredicateSpecError:
            return False
        if not self.has_valid_predicate_sha256():
            return False
        return self.predicate_id == "vpd_" + canonical_sha256(
            {
                "domain": self.schema_version,
                "predicate_sha256": self.predicate_sha256,
            }
        )

    def param_mapping(self) -> Mapping[str, PredicateParamValue]:
        """Read-only view over the frozen params."""
        return MappingProxyType(dict(self.params))


# ---------------------------------------------------------------------------
# VerificationPlanEntry / VerificationPlan / VerificationReadiness (M4)
# ---------------------------------------------------------------------------

_VERIFICATION_PLAN_ENTRY_SCHEMA_VERSION = "tiangong.verification_plan_entry.v1"
_VERIFICATION_PLAN_SCHEMA_VERSION = "tiangong.verification_plan.v1"
_VERIFICATION_PLAN_V2_SCHEMA_VERSION = "tiangong.verification_plan.v2"
_VERIFICATION_READINESS_SCHEMA_VERSION = "tiangong.verification_readiness.v1"

#: Entry assessment status: the six record statuses plus MISSING and
#: RECORD_MISMATCH (no authoritative record found / record did not match
#: the plan entry's exact binding requirements).
EntryAssessmentStatus = Literal[
    "PASS", "FAIL", "INCONCLUSIVE", "ERROR", "NOT_APPLICABLE",
    "MISSING", "RECORD_MISMATCH",
]


_VERIFICATION_PLAN_ENTRY_V2_SCHEMA_VERSION = "tiangong.verification_plan_entry.v2"


class VerificationPlanEntryV2(ContractModel):
    """M4.1 §3: plan entry with the COMPLETE AcceptancePredicate embedded.

    The predicate is a full nested model (not just id/sha/type refs), so
    a fabricated entry that combines predicate_id A + sha B + type C is
    impossible — the validator proves all identity fields derive from
    the same AcceptancePredicate.
    """
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:VerificationPlanEntryV2",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["VerificationPlanEntryV2"] = "VerificationPlanEntryV2"
    schema_version: Literal[_VERIFICATION_PLAN_ENTRY_V2_SCHEMA_VERSION] = (
        _VERIFICATION_PLAN_ENTRY_V2_SCHEMA_VERSION
    )
    plan_entry_id: str = Field(pattern=r"^vpe_[0-9a-f]{64}$")
    verifier_id: OpaqueId
    verifier_version: str = Field(min_length=1, max_length=64)
    predicate: AcceptancePredicate
    subject_identity: str = Field(min_length=1, max_length=400)
    evaluation_phase: EvaluationPhase
    required: bool = True
    entry_sha256: Sha256

    @property
    def predicate_id(self) -> str:
        return self.predicate.predicate_id

    @property
    def predicate_sha256(self) -> str:
        return self.predicate.predicate_sha256

    @property
    def predicate_type(self) -> str:
        return self.predicate.predicate_type

    @property
    def subject_kind(self) -> str:
        return self.predicate.subject_kind

    @model_validator(mode="after")
    def _validate_entry(self) -> VerificationPlanEntryV2:
        if not self.predicate.has_valid_identity():
            raise ValueError("entry predicate failed full identity validation")
        if self.predicate.subject_kind not in ("artifact", "effect", "repository"):
            raise ValueError("entry subject_kind must be a verifier domain")
        return self

    def computed_entry_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"entry_sha256", "plan_entry_id"})
        )

    def has_valid_entry_sha256(self) -> bool:
        return self.entry_sha256 == self.computed_entry_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_entry_sha256():
            return False
        if not self.predicate.has_valid_identity():
            return False
        return self.plan_entry_id == "vpe_" + canonical_sha256(
            {"domain": self.schema_version, "entry_sha256": self.entry_sha256}
        )

    def with_computed_sha256(self) -> VerificationPlanEntryV2:
        entry_sha256 = self.computed_entry_sha256()
        partial = self.model_copy(update={"entry_sha256": entry_sha256})
        return partial.model_copy(
            update={"plan_entry_id": "vpe_" + canonical_sha256(
                {"domain": self.schema_version, "entry_sha256": entry_sha256}
            )}
        )


class VerificationPlan(ContractModel):
    """A frozen verification plan (M4.1 v2): complete predicate bindings.

    Historical v1 plans (with id/sha/type entry references instead of
    nested predicates) remain readable through ``read_plan_any_version``
    but CANNOT be activated in current PLAN_BOUND mode.
    """
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:VerificationPlan",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["VerificationPlan"] = "VerificationPlan"
    schema_version: Literal[
        _VERIFICATION_PLAN_V2_SCHEMA_VERSION, _VERIFICATION_PLAN_SCHEMA_VERSION,
    ] = _VERIFICATION_PLAN_V2_SCHEMA_VERSION
    verification_plan_id: str = Field(pattern=r"^vpl_[0-9a-f]{64}$")
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    registry_snapshot_sha256: Sha256
    entries: tuple[VerificationPlanEntryV2, ...] = Field(min_length=1, max_length=64)
    plan_sha256: Sha256

    @model_validator(mode="after")
    def _validate_plan(self) -> VerificationPlan:
        entry_ids = [entry.plan_entry_id for entry in self.entries]
        if entry_ids != sorted(set(entry_ids)):
            raise ValueError("plan entries must have unique sorted ids")
        if not any(entry.required for entry in self.entries):
            raise ValueError("plan must have at least one required entry")
        return self

    def computed_plan_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(
                mode="json", exclude={"plan_sha256", "verification_plan_id"}
            )
        )

    def has_valid_plan_sha256(self) -> bool:
        return self.plan_sha256 == self.computed_plan_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_plan_sha256():
            return False
        if not all(entry.has_valid_identity() for entry in self.entries):
            return False
        return self.verification_plan_id == "vpl_" + canonical_sha256(
            {"domain": self.schema_version, "plan_sha256": self.plan_sha256}
        )

    def with_computed_sha256(self) -> VerificationPlan:
        plan_sha256 = self.computed_plan_sha256()
        partial = self.model_copy(update={"plan_sha256": plan_sha256})
        return partial.model_copy(
            update={"verification_plan_id": "vpl_" + canonical_sha256(
                {"domain": self.schema_version, "plan_sha256": plan_sha256}
            )}
        )


class EntryAssessment(ContractModel):
    """Per-entry assessment result for a VerificationReadiness."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    plan_entry_id: str = Field(pattern=r"^vpe_[0-9a-f]{64}$")
    status: EntryAssessmentStatus
    verification_record_id: str | None = None
    reason_code: str | None = Field(default=None, max_length=200)


class VerificationReadiness(ContractModel):
    """Machine assessment: does the plan have all its required PASS records?

    This is NOT a CompletionDecision. It cannot transition requests.
    It only reports whether the plan's verification requirements are met.
    """
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:VerificationReadiness",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["VerificationReadiness"] = "VerificationReadiness"
    schema_version: Literal[_VERIFICATION_READINESS_SCHEMA_VERSION] = (
        _VERIFICATION_READINESS_SCHEMA_VERSION
    )
    verification_readiness_id: str = Field(pattern=r"^vrd_[0-9a-f]{64}$")
    verification_plan_id: str = Field(pattern=r"^vpl_[0-9a-f]{64}$")
    verification_plan_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    registry_snapshot_sha256: Sha256
    required_entry_count: int = Field(ge=0)
    satisfied_entry_count: int = Field(ge=0)
    entry_assessments: tuple[EntryAssessment, ...] = Field(default=(), max_length=64)
    supporting_verification_record_ids: tuple[str, ...] = Field(
        default=(), max_length=64
    )
    verification_ready: bool
    failure_class: Literal[
        "NONE", "MISSING_EVIDENCE", "VERIFICATION_FAILED",
        "INCONCLUSIVE", "AUTHORITY_ERROR", "PLAN_CONFIG_ERROR",
    ] = "NONE"
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    evaluated_at_ms: int = Field(ge=0)
    readiness_sha256: Sha256

    @model_validator(mode="after")
    def _validate_readiness(self) -> VerificationReadiness:
        if self.verification_ready and self.failure_class != "NONE":
            raise ValueError("ready readiness cannot carry a failure class")
        if not self.verification_ready and self.failure_class == "NONE":
            raise ValueError("not-ready readiness must declare a failure class")
        entry_ids = [a.plan_entry_id for a in self.entry_assessments]
        if entry_ids != sorted(set(entry_ids)):
            raise ValueError("entry assessments must be unique sorted by entry id")
        return self

    def computed_readiness_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(
                mode="json",
                exclude={"readiness_sha256", "verification_readiness_id"},
            )
        )

    def has_valid_readiness_sha256(self) -> bool:
        return self.readiness_sha256 == self.computed_readiness_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_readiness_sha256():
            return False
        return self.verification_readiness_id == "vrd_" + canonical_sha256(
            {"domain": self.schema_version, "readiness_sha256": self.readiness_sha256}
        )

    def with_computed_sha256(self) -> VerificationReadiness:
        readiness_sha256 = self.computed_readiness_sha256()
        partial = self.model_copy(update={"readiness_sha256": readiness_sha256})
        return partial.model_copy(
            update={"verification_readiness_id": "vrd_" + canonical_sha256(
                {"domain": self.schema_version, "readiness_sha256": readiness_sha256}
            )}
        )


# ---------------------------------------------------------------------------
# RuntimeCloseoutEvidence (M4 §10)
# ---------------------------------------------------------------------------

_RUNTIME_CLOSEOUT_SCHEMA_VERSION = "tiangong.runtime_closeout_evidence.v1"


class RuntimeCloseoutEvidence(ContractModel):
    """P18 Runtime local closeout — NOT a final outcome proof.

    Replaces the bare booleans (life_gate_allowed,
    required_evidence_ready, runtime_blockers) consumed by
    regenerative_provider._verify_completion. Binds the runtime's local
    facts with identity + hash so the gateway can verify them.
    Architecture boundary: this only proves the V3 runtime's internal
    closeout state; it can never make VerificationReadiness PASS, never
    make CompletionGate COMPLETED, and never replace Artifact/Effect/
    Repository/Delivery gates.
    """
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:RuntimeCloseoutEvidence",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["RuntimeCloseoutEvidence"] = "RuntimeCloseoutEvidence"
    schema_version: Literal[_RUNTIME_CLOSEOUT_SCHEMA_VERSION] = (
        _RUNTIME_CLOSEOUT_SCHEMA_VERSION
    )
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    life_id: str = Field(min_length=1, max_length=160)
    execution_ticket_id: str | None = Field(default=None, max_length=160)
    root_goal_hash: Sha256
    task_contract_hash: Sha256
    runtime_blockers: tuple[str, ...] = Field(default=(), max_length=64)
    runtime_blockers_sha256: Sha256
    life_gate_allowed: bool
    required_evidence_ready: bool
    produced_at_ms: int = Field(ge=0)
    evidence_sha256: Sha256

    @model_validator(mode="after")
    def _validate_closeout(self) -> RuntimeCloseoutEvidence:
        if self.runtime_blockers_sha256 != canonical_sha256(
            list(self.runtime_blockers)
        ):
            raise ValueError("runtime_blockers_sha256 does not recompute")
        return self

    def computed_evidence_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"evidence_sha256"})
        )

    def has_valid_evidence_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_evidence_sha256()

    def has_valid_identity(self) -> bool:
        return self.has_valid_evidence_sha256()

    def with_computed_sha256(self) -> RuntimeCloseoutEvidence:
        return self.model_copy(
            update={"evidence_sha256": self.computed_evidence_sha256()}
        )


__all__ = [
    "AcceptancePredicate",
    "AcceptancePredicateSpecError",
    "EnforcementMode",
    "EntryAssessment",
    "EntryAssessmentStatus",
    "EvaluationPhase",
    "EvidenceAuthority",
    "PREDICATE_MAX_ITEM_CHARS",
    "PREDICATE_MAX_LIST_ITEMS",
    "PREDICATE_PARAM_RULES",
    "PredicateParamScalar",
    "PredicateParamValue",
    "PredicateType",
    "RecordedEnforcement",
    "RegistrySnapshot",
    "RuntimeCloseoutEvidence",
    "SubjectKind",
    "VerificationLayer",
    "VerificationPlan",
    "VerificationPlanEntry",
    "VerificationPlanEntryV2",
    "VerificationReadiness",
    "VerificationRecord",
    "VerificationStatus",
    "VerifierDescriptor",
    "derive_registry_snapshot_id",
    "derive_verification_record_id",
    "derive_verifier_descriptor_id",
    "normalize_predicate_params",
    "normalize_predicate_text",
    "read_plan_any_version",
]


# ---------------------------------------------------------------------------
# M5 §7-9: FailureEvidence / VerificationDisposition / RepairDirective
# ---------------------------------------------------------------------------

_FAILURE_EVIDENCE_SCHEMA = "tiangong.verification_failure_evidence.v1"
_DISPOSITION_SCHEMA = "tiangong.verification_disposition.v1"
_REPAIR_DIRECTIVE_SCHEMA = "tiangong.repair_directive.v1"

FailureKind = Literal[
    "VERIFICATION_FAILED", "INCONCLUSIVE", "MISSING_EVIDENCE",
    "AUTHORITY_ERROR", "PLAN_CONFIG_ERROR", "RECORD_MISMATCH",
]

DispositionAction = Literal[
    "REPAIR", "WAIT", "RECONCILE", "REVIEW", "BLOCK",
]


def derive_failure_signature(
    *,
    plan_entry_id: str,
    effective_subject_identity: str,
    predicate_sha256: str,
    verification_status: str,
    reason_codes: tuple[str, ...],
    verification_evidence_sha256: str,
) -> str:
    """M5 §9: deterministic failure signature for anti-loop detection."""
    return canonical_sha256({
        "plan_entry_id": plan_entry_id,
        "effective_subject_identity": effective_subject_identity,
        "predicate_sha256": predicate_sha256,
        "verification_status": verification_status,
        "reason_codes": list(reason_codes),
        "verification_evidence_sha256": verification_evidence_sha256,
    })


class FailureEvidence(ContractModel):
    """M5 §7: what exactly failed, against which obligation and evidence.

    DERIVED fact — callers cannot supply "why it failed"; the builder
    re-derives from plan + readiness + authoritative records.
    """
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:FailureEvidence",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["FailureEvidence"] = "FailureEvidence"
    schema_version: Literal[_FAILURE_EVIDENCE_SCHEMA] = _FAILURE_EVIDENCE_SCHEMA
    failure_evidence_id: str = Field(pattern=r"^vfe_[0-9a-f]{64}$")
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    verification_plan_id: str = Field(pattern=r"^vpl_[0-9a-f]{64}$")
    verification_plan_sha256: Sha256
    registry_snapshot_sha256: Sha256
    plan_entry_id: str = Field(pattern=r"^vpe_[0-9a-f]{64}$")
    plan_entry_sha256: Sha256
    verifier_id: OpaqueId
    verifier_version: str = Field(min_length=1, max_length=64)
    predicate_id: str = Field(pattern=r"^vpd_[0-9a-f]{64}$")
    predicate_sha256: Sha256
    predicate_type: PredicateType
    subject_kind: SubjectKind
    original_subject_identity: str = Field(min_length=1, max_length=400)
    effective_subject_identity: str = Field(min_length=1, max_length=400)
    verification_record_id: str | None = None
    verification_result_sha256: Sha256 | None = None
    verification_status: EntryAssessmentStatus
    readiness_id: str = Field(pattern=r"^vrd_[0-9a-f]{64}$")
    readiness_sha256: Sha256
    failure_kind: FailureKind
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    verification_evidence_sha256: Sha256
    failure_signature_sha256: Sha256
    observed_at_ms: int = Field(ge=0)
    failure_evidence_sha256: Sha256

    @model_validator(mode="after")
    def _validate(self) -> FailureEvidence:
        if self.verification_status == "PASS":
            raise ValueError("FailureEvidence cannot be derived from PASS")
        expected_sig = derive_failure_signature(
            plan_entry_id=self.plan_entry_id,
            effective_subject_identity=self.effective_subject_identity,
            predicate_sha256=self.predicate_sha256,
            verification_status=self.verification_status,
            reason_codes=self.reason_codes,
            verification_evidence_sha256=self.verification_evidence_sha256,
        )
        if self.failure_signature_sha256 != expected_sig:
            raise ValueError("failure_signature_sha256 does not recompute")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"failure_evidence_sha256", "failure_evidence_id"})
        )

    def has_valid_sha256(self) -> bool:
        return self.failure_evidence_sha256 == self.computed_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_sha256():
            return False
        return self.failure_evidence_id == "vfe_" + canonical_sha256(
            {"domain": self.schema_version, "sha": self.failure_evidence_sha256}
        )

    def with_computed_sha256(self) -> FailureEvidence:
        sha = self.computed_sha256()
        partial = self.model_copy(update={"failure_evidence_sha256": sha})
        return partial.model_copy(update={
            "failure_evidence_id": "vfe_" + canonical_sha256(
                {"domain": self.schema_version, "sha": sha}
            )
        })


class VerificationDisposition(ContractModel):
    """M5 §10: Gateway policy decision — what should the system do next.

    NOT a Verifier verdict. NOT a CompletionDecision. Deterministic
    policy v1 (no LLM involvement in the decision).
    """
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:VerificationDisposition",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["VerificationDisposition"] = "VerificationDisposition"
    schema_version: Literal[_DISPOSITION_SCHEMA] = _DISPOSITION_SCHEMA
    verification_disposition_id: str = Field(pattern=r"^vds_[0-9a-f]{64}$")
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    verification_plan_id: str = Field(pattern=r"^vpl_[0-9a-f]{64}$")
    plan_entry_id: str = Field(pattern=r"^vpe_[0-9a-f]{64}$")
    failure_evidence_id: str = Field(pattern=r"^vfe_[0-9a-f]{64}$")
    failure_evidence_sha256: Sha256
    action: DispositionAction
    policy_version: str = Field(min_length=1, max_length=64)
    policy_config_sha256: Sha256
    attempt_no: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=32)
    decided_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    disposition_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"disposition_sha256", "verification_disposition_id"})
        )

    def has_valid_sha256(self) -> bool:
        return self.disposition_sha256 == self.computed_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_sha256():
            return False
        return self.verification_disposition_id == "vds_" + canonical_sha256(
            {"domain": self.schema_version, "sha": self.disposition_sha256}
        )

    def with_computed_sha256(self) -> VerificationDisposition:
        sha = self.computed_sha256()
        partial = self.model_copy(update={"disposition_sha256": sha})
        return partial.model_copy(update={
            "verification_disposition_id": "vds_" + canonical_sha256(
                {"domain": self.schema_version, "sha": sha}
            )
        })


class RepairDirective(ContractModel):
    """M5 §14: bounded repair instruction — no completion authority.

    Only created when VerificationDisposition.action == REPAIR.
    Cannot change the acceptance predicate or plan.
    """
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:RepairDirective",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    schema_id: Literal["RepairDirective"] = "RepairDirective"
    schema_version: Literal[_REPAIR_DIRECTIVE_SCHEMA] = _REPAIR_DIRECTIVE_SCHEMA
    repair_directive_id: str = Field(pattern=r"^vrd_[0-9a-f]{64}$")
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    verification_plan_id: str = Field(pattern=r"^vpl_[0-9a-f]{64}$")
    verification_plan_sha256: Sha256
    plan_entry_id: str = Field(pattern=r"^vpe_[0-9a-f]{64}$")
    plan_entry_sha256: Sha256
    failure_evidence_id: str = Field(pattern=r"^vfe_[0-9a-f]{64}$")
    failure_evidence_sha256: Sha256
    disposition_id: str = Field(pattern=r"^vds_[0-9a-f]{64}$")
    disposition_sha256: Sha256
    predicate_id: str = Field(pattern=r"^vpd_[0-9a-f]{64}$")
    predicate_sha256: Sha256
    subject_kind: SubjectKind
    original_subject_identity: str = Field(min_length=1, max_length=400)
    effective_subject_identity: str = Field(min_length=1, max_length=400)
    repair_attempt_no: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    allowed_target_refs: tuple[str, ...] = Field(default=(), max_length=64)
    forbidden_target_refs: tuple[str, ...] = Field(default=(), max_length=64)
    repair_goal_kind: str = Field(min_length=1, max_length=160)
    repair_constraints: tuple[str, ...] = Field(default=(), max_length=32)
    execution_budget_ms: int = Field(gt=0)
    requires_reverification: Literal[True] = True
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    directive_sha256: Sha256

    @model_validator(mode="after")
    def _validate_directive(self) -> RepairDirective:
        if self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("directive expires_at must be after issued_at")
        if self.repair_attempt_no > self.max_attempts:
            raise ValueError("repair_attempt_no exceeds max_attempts")
        # M5 §15: predicate immutability is enforced at the store boundary
        # (the directive must carry the SAME predicate as the plan entry);
        # here we validate self-consistency of identity fields.
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"directive_sha256", "repair_directive_id"})
        )

    def has_valid_sha256(self) -> bool:
        return self.directive_sha256 == self.computed_sha256()

    def has_valid_identity(self) -> bool:
        if not self.has_valid_sha256():
            return False
        return self.repair_directive_id == "vrd_" + canonical_sha256(
            {"domain": self.schema_version, "sha": self.directive_sha256}
        )

    def with_computed_sha256(self) -> RepairDirective:
        sha = self.computed_sha256()
        partial = self.model_copy(update={"directive_sha256": sha})
        return partial.model_copy(update={
            "repair_directive_id": "vrd_" + canonical_sha256(
                {"domain": self.schema_version, "sha": sha}
            )
        })


# Export new M5 contracts
__all__.extend([
    "DispositionAction",
    "FailureEvidence",
    "FailureKind",
    "RepairDirective",
    "VerificationDisposition",
    "derive_failure_signature",
])
