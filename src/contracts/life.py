"""Strict life-event, viability, appraisal, and continuity contracts."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .models import ContractModel, EffectId, OpaqueId, ReasonCode, RequestId, RunId, SCHEMA_BASE, Sha256


LIFE_CONTRACT_SCHEMA_VERSION = "tiangong.life.contracts.v4"
LEGACY_LIFE_CONTRACT_SCHEMA_VERSION = "tiangong.life.contracts.v3"
Milli = Annotated[int, Field(ge=0, le=1000, strict=True)]
SignedMilli = Annotated[int, Field(ge=-1000, le=1000, strict=True)]
LifeEventId = Annotated[str, StringConstraints(pattern=r"^lev_[0-9a-f]{64}$")]
LifeCapsuleId = Annotated[str, StringConstraints(pattern=r"^lcp_[0-9a-f]{64}$")]
LifeIngressId = Annotated[str, StringConstraints(pattern=r"^lin_[0-9a-f]{64}$")]
LifeIngressReceiptId = Annotated[str, StringConstraints(pattern=r"^lir_[0-9a-f]{64}$")]
LifeContextAuthorizationId = Annotated[
    str, StringConstraints(pattern=r"^lca_[0-9a-f]{64}$")
]
ViabilityObservationId = Annotated[
    str, StringConstraints(pattern=r"^vob_[0-9a-f]{64}$")
]
SignatureHex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]
EvidenceClass = Literal[
    "observed",
    "user_asserted",
    "execution_verified",
    "model_inference",
    "reflection",
    "prospective",
    "migration_verified",
]
LifeSourceKind = Literal[
    "user_message",
    "execution",
    "tool_receipt",
    "weather",
    "news",
    "system_health",
    "user_feedback",
    "migration",
    "reflection",
]
ViabilityDimensionName = Literal[
    "runtime_availability",
    "recoverability",
    "identity_continuity",
    "data_integrity",
    "memory_integrity",
    "context_continuity",
    "resource_headroom",
    "cognitive_certainty",
    "trust_and_authorization",
    "commitment_continuity",
    "security_margin",
]


def _human_text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("life contract text must use NFC normalization")
    if "\x00" in value or any(
        ord(character) < 32 and character not in "\t\n\r"
        for character in value
    ):
        raise ValueError("life contract text contains a control character")
    return value


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like life contract fields must be sorted and unique")
    return value


def derive_life_ingress_id(
    *,
    life_id: str,
    source_component_id: str,
    source_epoch: int,
    source_sequence: int,
    dedupe_key: str,
) -> str:
    return "lin_" + canonical_sha256(
        {
            "dedupe_key": dedupe_key,
            "domain": "tiangong.life.ingress-id.v1",
            "life_id": life_id,
            "source_component_id": source_component_id,
            "source_epoch": source_epoch,
            "source_sequence": source_sequence,
        }
    )


def derive_life_event_id(
    *,
    life_id: str,
    writer_epoch: int,
    sequence: int,
    ingress_id: str,
) -> str:
    return "lev_" + canonical_sha256(
        {
            "domain": "tiangong.life.event-id.v1",
            "ingress_id": ingress_id,
            "life_id": life_id,
            "sequence": sequence,
            "writer_epoch": writer_epoch,
        }
    )


def derive_life_ingress_receipt_id(
    *, ingress_id: str, source_sequence: int, event_hash: str
) -> str:
    return "lir_" + canonical_sha256(
        {
            "domain": "tiangong.life.ingress-receipt-id.v1",
            "event_hash": event_hash,
            "ingress_id": ingress_id,
            "source_sequence": source_sequence,
        }
    )


class LifeEventEnvelope(ContractModel):
    """An immutable observed event; untrusted content stays object-addressed."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:LifeEventEnvelope",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    event_id: LifeEventId
    life_id: OpaqueId
    sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    writer_epoch: int = Field(ge=1, le=9_007_199_254_740_991)
    source_service: OpaqueId
    source_kind: LifeSourceKind
    event_kind: OpaqueId
    occurred_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    observed_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    principal_ref: OpaqueId
    subject_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=64)
    evidence_class: EvidenceClass
    source_credibility_milli: Milli
    privacy_scope: Literal["public", "relationship", "private", "secret", "system"]
    content_object_id: OpaqueId
    content_sha256: Sha256
    dedupe_key: Sha256
    causation_id: OpaqueId | None = None
    correlation_id: OpaqueId | None = None
    previous_event_hash: Sha256 | None = None
    event_hash: Sha256
    signer_key_id: OpaqueId
    signature: SignatureHex

    _validate_subjects = field_validator("subject_refs")(_sorted_unique)

    @model_validator(mode="after")
    def validate_temporal_and_chain_shape(self) -> Self:
        if self.occurred_at_ms > self.observed_at_ms:
            raise ValueError("life event occurrence cannot be observed before it occurs")
        if (self.sequence == 1) != (self.previous_event_hash is None):
            raise ValueError("life event genesis and previous hash disagree")
        return self

    def computed_event_hash(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"event_hash", "signature"})
        )

    def has_valid_event_hash(self) -> bool:
        return self.event_hash == self.computed_event_hash()

    def with_computed_event_hash(self) -> Self:
        return self.model_copy(update={"event_hash": self.computed_event_hash()})


class LifeEventIngress(ContractModel):
    """One source-signed fact offered to the single life writer."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:LifeEventIngress",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    ingress_id: LifeIngressId
    life_id: OpaqueId
    source_component_id: OpaqueId
    source_epoch: int = Field(ge=1, le=9_007_199_254_740_991)
    source_sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    source_kind: LifeSourceKind
    event_kind: OpaqueId
    occurred_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    observed_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    principal_ref: OpaqueId
    subject_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=64)
    evidence_class: EvidenceClass
    source_credibility_milli: Milli
    privacy_scope: Literal["public", "relationship", "private", "secret", "system"]
    content_object_id: OpaqueId
    content_sha256: Sha256
    dedupe_key: Sha256
    request_id: RequestId | None = None
    run_id: RunId | None = None
    generation: int | None = Field(default=None, ge=0)
    causation_id: OpaqueId | None = None
    correlation_id: OpaqueId | None = None
    signer_key_id: OpaqueId
    ingress_sha256: Sha256
    signature: SignatureHex

    _validate_subjects = field_validator("subject_refs")(_sorted_unique)

    @model_validator(mode="after")
    def validate_identity_time_and_request_binding(self) -> Self:
        if self.occurred_at_ms > self.observed_at_ms:
            raise ValueError("life ingress occurrence cannot be observed before it occurs")
        bound = (self.request_id is not None, self.run_id is not None, self.generation is not None)
        if any(bound) and not all(bound):
            raise ValueError("life ingress request binding must be all-or-none")
        if self.ingress_id != self.computed_ingress_id():
            raise ValueError("life ingress ID is not source-sequence bound")
        return self

    def computed_ingress_id(self) -> str:
        return derive_life_ingress_id(
            life_id=self.life_id,
            source_component_id=self.source_component_id,
            source_epoch=self.source_epoch,
            source_sequence=self.source_sequence,
            dedupe_key=self.dedupe_key,
        )

    def computed_ingress_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"ingress_sha256", "signature"})
        )

    def has_valid_ingress_sha256(self) -> bool:
        return self.ingress_sha256 == self.computed_ingress_sha256()

    def with_computed_ingress_identity(self) -> Self:
        value = self.model_copy(update={"ingress_id": self.computed_ingress_id()})
        return value.model_copy(update={"ingress_sha256": value.computed_ingress_sha256()})


class LifeEventIngressReceipt(ContractModel):
    """Life-writer acknowledgement bound to one durable consumer offset."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:LifeEventIngressReceipt",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    receipt_id: LifeIngressReceiptId
    ingress_id: LifeIngressId
    life_id: OpaqueId
    source_component_id: OpaqueId
    source_epoch: int = Field(ge=1, le=9_007_199_254_740_991)
    source_sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    accepted: Literal[True] = True
    duplicate: bool
    event_id: LifeEventId
    event_hash: Sha256
    consumer_offset: int = Field(ge=1, le=9_007_199_254_740_991)
    received_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_receipt_identity_and_offset(self) -> Self:
        if self.consumer_offset != self.source_sequence:
            raise ValueError("life ingress receipt offset disagrees with source sequence")
        if self.receipt_id != self.computed_receipt_id():
            raise ValueError("life ingress receipt ID is invalid")
        return self

    def computed_receipt_id(self) -> str:
        return derive_life_ingress_receipt_id(
            ingress_id=self.ingress_id,
            source_sequence=self.source_sequence,
            event_hash=self.event_hash,
        )

    def computed_receipt_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )

    def has_valid_receipt_sha256(self) -> bool:
        return self.receipt_sha256 == self.computed_receipt_sha256()

    def with_computed_receipt_identity(self) -> Self:
        value = self.model_copy(update={"receipt_id": self.computed_receipt_id()})
        return value.model_copy(update={"receipt_sha256": value.computed_receipt_sha256()})


class ViabilityDimension(ContractModel):
    value_milli: Milli
    target_low_milli: Milli
    target_high_milli: Milli
    confidence_milli: Milli
    source_event_ids: tuple[LifeEventId, ...] = Field(min_length=1, max_length=256)
    measured_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    stale_after_ms: int = Field(ge=0, le=9_007_199_254_740_991)

    _validate_sources = field_validator("source_event_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_band_and_time(self) -> Self:
        if self.target_low_milli > self.target_high_milli:
            raise ValueError("viability target band is inverted")
        if self.stale_after_ms < self.measured_at_ms:
            raise ValueError("viability staleness precedes measurement")
        return self


class ViabilityObservation(ContractModel):
    """One source-bound measurement; aggregation remains machine-owned."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ViabilityObservation",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    observation_id: ViabilityObservationId
    life_id: OpaqueId
    dimension: ViabilityDimensionName
    value_milli: Milli
    declared_confidence_milli: Milli
    source_event_id: LifeEventId
    evidence_class: EvidenceClass
    source_kind: LifeSourceKind
    source_component_id: OpaqueId
    measured_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    stale_after_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    observation_sha256: Sha256

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        if self.stale_after_ms < self.measured_at_ms:
            raise ValueError("viability observation staleness precedes measurement")
        return self

    def computed_observation_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"observation_sha256"})
        )

    def has_valid_observation_sha256(self) -> bool:
        return self.observation_sha256 == self.computed_observation_sha256()

    def with_computed_observation_sha256(self) -> Self:
        return self.model_copy(
            update={"observation_sha256": self.computed_observation_sha256()}
        )


class ViabilityState(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ViabilityState",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    life_id: OpaqueId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    runtime_availability: ViabilityDimension
    recoverability: ViabilityDimension
    identity_continuity: ViabilityDimension
    data_integrity: ViabilityDimension
    memory_integrity: ViabilityDimension
    context_continuity: ViabilityDimension
    resource_headroom: ViabilityDimension
    cognitive_certainty: ViabilityDimension
    trust_and_authorization: ViabilityDimension
    commitment_continuity: ViabilityDimension
    security_margin: ViabilityDimension
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    state_sha256: Sha256

    def computed_state_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )

    def has_valid_state_sha256(self) -> bool:
        return self.state_sha256 == self.computed_state_sha256()

    def with_computed_state_sha256(self) -> Self:
        return self.model_copy(update={"state_sha256": self.computed_state_sha256()})

    def dimensions(self) -> dict[str, ViabilityDimension]:
        return {
            name: getattr(self, name)
            for name in (
                "runtime_availability",
                "recoverability",
                "identity_continuity",
                "data_integrity",
                "memory_integrity",
                "context_continuity",
                "resource_headroom",
                "cognitive_certainty",
                "trust_and_authorization",
                "commitment_continuity",
                "security_margin",
            )
        }


class AppraisalVectorV3(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AppraisalVectorV3",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    appraisal_id: OpaqueId
    life_id: OpaqueId
    source_event_ids: tuple[LifeEventId, ...] = Field(min_length=1, max_length=256)
    viability_revision: int = Field(ge=1)
    novelty_milli: Milli
    goal_congruence_milli: SignedMilli
    threat_milli: Milli
    loss_milli: Milli
    obstruction_milli: Milli
    certainty_milli: Milli
    controllability_milli: Milli
    social_warmth_milli: Milli
    social_trust_milli: Milli
    intensity_milli: Milli
    source_credibility_milli: Milli
    self_relevance_milli: Milli
    impact_on_others_milli: Milli
    norm_relevance_milli: Milli
    urgency_milli: Milli
    repetition_factor_milli: Milli
    appraised_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    appraisal_sha256: Sha256

    _validate_sources = field_validator("source_event_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_source_ceiling(self) -> Self:
        if self.source_credibility_milli == 0 and self.intensity_milli > 0:
            raise ValueError("zero-credibility input cannot produce appraisal intensity")
        return self

    def computed_appraisal_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"appraisal_sha256"})
        )

    def has_valid_appraisal_sha256(self) -> bool:
        return self.appraisal_sha256 == self.computed_appraisal_sha256()

    def with_computed_appraisal_sha256(self) -> Self:
        return self.model_copy(
            update={"appraisal_sha256": self.computed_appraisal_sha256()}
        )


class WorkspaceFileRef(ContractModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    sha256: Sha256
    size_bytes: int = Field(ge=0, le=4_398_046_511_104)
    revision: int = Field(ge=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if (
            "\\" in value
            or value.startswith("/")
            or value.endswith("/")
            or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("workspace file reference must be portable and relative")
        return value


class TaskContinuityCapsule(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:TaskContinuityCapsule",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    capsule_id: LifeCapsuleId
    life_id: OpaqueId
    capsule_kind: Literal[
        "WORKING_CHECKPOINT",
        "COMPRESSION_CHECKPOINT",
        "TERMINAL_RESULT",
    ]
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    episode_id: OpaqueId
    user_goal: str = Field(min_length=1, max_length=20_000)
    hard_constraints: tuple[str, ...] = Field(default=(), max_length=256)
    active_plan: tuple[str, ...] = Field(default=(), max_length=512)
    verified_fact_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=2048)
    causal_hypothesis_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=2048)
    workspace_manifest: tuple[WorkspaceFileRef, ...] = Field(default=(), max_length=4096)
    artifact_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    unresolved_questions: tuple[str, ...] = Field(default=(), max_length=256)
    pending_effect_ids: tuple[EffectId, ...] = Field(default=(), max_length=256)
    latest_safe_step: str | None = Field(default=None, max_length=20_000)
    next_step: str | None = Field(default=None, max_length=20_000)
    recovery_preconditions: tuple[str, ...] = Field(default=(), max_length=256)
    continuation_token_sha256: Sha256 | None = None
    final_result: str | None = Field(default=None, max_length=100_000)
    supersedes_capsule_id: LifeCapsuleId | None = None
    retention_class: Literal[
        "ACTIVE_WORKING",
        "CHECKPOINT",
        "TERMINAL_RESULT",
        "LONG_TERM_MEMORY",
        "LEGAL_HOLD",
    ]
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    capsule_sha256: Sha256

    _validate_fact_ids = field_validator(
        "verified_fact_ids",
        "causal_hypothesis_ids",
        "artifact_refs",
        "pending_effect_ids",
    )(_sorted_unique)

    @field_validator(
        "user_goal",
        "hard_constraints",
        "active_plan",
        "unresolved_questions",
        "latest_safe_step",
        "next_step",
        "recovery_preconditions",
        "final_result",
    )
    @classmethod
    def validate_text_fields(cls, value):
        if value is None:
            return value
        if isinstance(value, tuple):
            return tuple(_human_text(item) for item in value)
        return _human_text(value)

    @field_validator("workspace_manifest")
    @classmethod
    def validate_workspace_manifest(
        cls,
        value: tuple[WorkspaceFileRef, ...],
    ) -> tuple[WorkspaceFileRef, ...]:
        paths = tuple(item.relative_path for item in value)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("workspace manifest paths must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_capsule_lifecycle(self) -> Self:
        terminal = self.capsule_kind == "TERMINAL_RESULT"
        if terminal:
            if not self.final_result or self.pending_effect_ids:
                raise ValueError("terminal capsule requires a final result and no pending effects")
            if self.next_step is not None or self.continuation_token_sha256 is not None:
                raise ValueError("terminal capsule cannot carry continuation state")
            if self.retention_class not in {"TERMINAL_RESULT", "LEGAL_HOLD"}:
                raise ValueError("terminal capsule has an invalid retention class")
        else:
            if self.final_result is not None:
                raise ValueError("checkpoint capsule cannot claim a final result")
            if (
                not self.latest_safe_step
                or not self.next_step
                or self.continuation_token_sha256 is None
            ):
                raise ValueError("checkpoint capsule lacks recovery state")
            if self.retention_class not in {"ACTIVE_WORKING", "CHECKPOINT", "LEGAL_HOLD"}:
                raise ValueError("checkpoint capsule has an invalid retention class")
        return self

    def computed_capsule_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"capsule_sha256"})
        )

    def has_valid_capsule_sha256(self) -> bool:
        return self.capsule_sha256 == self.computed_capsule_sha256()

    def with_computed_capsule_sha256(self) -> Self:
        return self.model_copy(update={"capsule_sha256": self.computed_capsule_sha256()})


class LifeRevisionVector(ContractModel):
    """Exact source revisions consumed by one compiled context."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:LifeRevisionVector",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    life_id: OpaqueId
    writer_epoch: int = Field(ge=1, le=9_007_199_254_740_991)
    source_sequence: int = Field(ge=0, le=9_007_199_254_740_991)
    identity_revision: int = Field(ge=1, le=9_007_199_254_740_991)
    soul_revision: int = Field(ge=1, le=9_007_199_254_740_991)
    memory_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    affect_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    causal_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    viability_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    policy_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    reflection_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    capability_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    vector_sha256: Sha256

    def computed_vector_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"vector_sha256"})
        )

    def has_valid_vector_sha256(self) -> bool:
        return self.vector_sha256 == self.computed_vector_sha256()

    def with_computed_vector_sha256(self) -> Self:
        return self.model_copy(update={"vector_sha256": self.computed_vector_sha256()})


class LifeAuthorityHead(ContractModel):
    """Immutable Life authority revision used for CAS-bound subjective state."""
    schema_id: Literal["LifeAuthorityHead"] = "LifeAuthorityHead"
    schema_version: Literal["tiangong.life_authority_head.v1"] = "tiangong.life_authority_head.v1"
    life_id: OpaqueId
    writer_epoch: int = Field(ge=1)
    identity_revision: int = Field(ge=0)
    identity_sha256: Sha256
    soul_revision: int = Field(ge=0)
    soul_sha256: Sha256
    affect_revision: int = Field(ge=0)
    affect_sha256: Sha256
    deletion_epoch: int = Field(ge=0)
    head_sha256: Sha256
    def computed_head_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"head_sha256"}))
    def with_computed_head_sha256(self) -> Self:
        return self.model_copy(update={"head_sha256": self.computed_head_sha256()})


class RunLifeBinding(ContractModel):
    """Immutable request/internal-stimulus binding to the read authority head."""
    schema_id: Literal["RunLifeBinding"] = "RunLifeBinding"
    schema_version: Literal["tiangong.run_life_binding.v1"] = "tiangong.run_life_binding.v1"
    binding_id: OpaqueId
    life_id: OpaqueId
    binding_subject_kind: Literal["request", "internal_stimulus"]
    binding_subject_id: OpaqueId
    binding_subject_sha256: Sha256
    life_authority_head_sha256: Sha256
    writer_epoch: int = Field(ge=1)
    identity_revision: int = Field(ge=0)
    identity_sha256: Sha256
    soul_revision: int = Field(ge=0)
    soul_sha256: Sha256
    affect_revision: int = Field(ge=0)
    affect_sha256: Sha256
    deletion_epoch: int = Field(ge=0)
    bound_at_ms: int = Field(ge=0)
    binding_source: OpaqueId
    request_id: RequestId | None = None
    run_id: RunId | None = None
    run_sequence: int | None = Field(default=None, ge=0)
    generation: int | None = Field(default=None, ge=0)
    binding_sha256: Sha256
    @model_validator(mode="after")
    def validate_run_shape(self) -> Self:
        values = (self.request_id, self.run_id, self.run_sequence, self.generation)
        if any(value is None for value in values) != all(value is None for value in values):
            raise ValueError("run binding fields must be all-present or all-absent")
        if self.binding_subject_kind == "request" and self.binding_subject_id != self.run_id:
            raise ValueError("request binding subject must equal run_id")
        return self
    def computed_binding_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"binding_sha256"}))
    def with_computed_binding_sha256(self) -> Self:
        return self.model_copy(update={"binding_sha256": self.computed_binding_sha256()})


class RootExperienceHead(ContractModel):
    schema_id: Literal["RootExperienceHead"] = "RootExperienceHead"
    schema_version: Literal["tiangong.root_experience_head.v1"] = "tiangong.root_experience_head.v1"
    root_experience_id: OpaqueId
    life_id: OpaqueId
    initial_run_life_binding_sha256: Sha256
    active_run_life_binding_sha256: Sha256
    root_trigger_event_id: LifeEventId
    root_trigger_event_sha256: Sha256
    next_sequence_no: int = Field(ge=1)
    root_status: Literal["OPEN", "WAITING", "CLOSED", "ABORTED"]
    waiting_question_id: OpaqueId | None = None
    terminal_reason: str | None = None
    terminal_at_ms: int | None = Field(default=None, ge=0)
    terminal_basis_ref: OpaqueId | None = None
    terminal_completion_decision_ref: OpaqueId | None = None
    head_sha256: Sha256
    @model_validator(mode="after")
    def validate_state_shape(self) -> Self:
        terminal = self.root_status in {"CLOSED", "ABORTED"}
        if terminal != all(value is not None for value in (self.terminal_reason, self.terminal_at_ms, self.terminal_basis_ref)):
            raise ValueError("terminal root evidence must be all-present only for terminal roots")
        if self.root_status != "WAITING" and self.waiting_question_id is not None:
            raise ValueError("only waiting roots may carry a question")
        return self
    def computed_head_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"head_sha256"}))
    def with_computed_head_sha256(self) -> Self:
        return self.model_copy(update={"head_sha256": self.computed_head_sha256()})


class RootContinuationBinding(ContractModel):
    schema_id: Literal["RootContinuationBinding"] = "RootContinuationBinding"
    schema_version: Literal["tiangong.root_continuation_binding.v1"] = "tiangong.root_continuation_binding.v1"
    continuation_id: OpaqueId
    root_experience_id: OpaqueId
    reply_to_question_id: OpaqueId
    previous_binding_sha256: Sha256
    next_binding_sha256: Sha256
    answer_event_id: LifeEventId
    answer_event_sha256: Sha256
    previous_root_head_sha256: Sha256
    continuation_sha256: Sha256
    def computed_continuation_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"continuation_sha256"}))
    def with_computed_continuation_sha256(self) -> Self:
        return self.model_copy(update={"continuation_sha256": self.computed_continuation_sha256()})


def derive_turn_commit_id(
    *,
    life_id: str,
    root_experience_id: str,
    child_episode_id: str,
    stage: str,
    predecessor_commit_sha256: str | None = None,
) -> str:
    payload = {
        "domain": "tiangong.v21.life-turn-commit.v1",
        "life_id": life_id,
        "root_experience_id": root_experience_id,
        "child_episode_id": child_episode_id,
        "stage": stage,
    }
    if predecessor_commit_sha256 is not None:
        payload["predecessor_commit_sha256"] = predecessor_commit_sha256
    return canonical_sha256(payload)


class LifeTurnCommit(ContractModel):
    """Journal-authoritative response/terminal stage chain for one response episode."""

    schema_id: Literal["LifeTurnCommit"] = "LifeTurnCommit"
    schema_version: Literal["tiangong.life_turn_commit.v1"] = "tiangong.life_turn_commit.v1"
    turn_commit_id: OpaqueId
    stage: Literal[
        "OUTCOME_COMMITTED_RESPONSE_OPEN",
        "RESPONSE_COMMITTED",
        "DELIVERY_OBSERVED",
        "ROOT_TERMINAL",
    ]
    life_id: OpaqueId
    run_life_binding_sha256: Sha256
    root_experience_id: OpaqueId
    child_episode_id: OpaqueId
    response_episode_id: OpaqueId
    response_basis_kind: Literal["commitment", "conversation"]
    response_basis_sha256: Sha256
    predecessor_commit_sha256: Sha256 | None = None
    completion_delivery_mode: Literal["none", "response_delivery"] | None = None
    fact_refs: tuple[OpaqueId, ...] | None = None
    pre_delivery_completion_decision_ref: OpaqueId | None = None
    terminal_completion_decision_ref: OpaqueId | None = None
    model_attempt_plan_ref: OpaqueId | None = None
    model_attempt_refs: tuple[OpaqueId, ...] | None = None
    model_attempt_plan_outcome_ref: OpaqueId | None = None
    winner_attempt_ref: OpaqueId | None = None
    assistant_candidate_id: OpaqueId | None = None
    expression_status: Literal["model_available", "model_unavailable"] | None = None
    assistant_commit_ref: OpaqueId | None = None
    delivery_ref: OpaqueId | None = None
    terminal_basis_ref: OpaqueId | None = None
    root_terminal_status: str | None = Field(default=None, max_length=80)
    root_terminal_reason: ReasonCode | None = None
    root_terminal_at_ms: int | None = Field(default=None, ge=0)
    reflection_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    affect_revision: int | None = Field(default=None, ge=0)
    memory_candidate_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    capability_evidence_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    commit_sha256: Sha256

    @model_validator(mode="after")
    def validate_basis_shape(self) -> Self:
        if self.response_basis_kind == "conversation":
            if (
                self.completion_delivery_mode is not None
                or self.pre_delivery_completion_decision_ref is not None
                or self.terminal_completion_decision_ref is not None
            ):
                raise ValueError("conversation turn commit cannot carry completion decision fields")
        elif self.completion_delivery_mode is None:
            raise ValueError("commitment turn commit requires completion delivery mode")
        if self.expression_status == "model_available":
            if self.winner_attempt_ref is None or self.assistant_candidate_id is None:
                raise ValueError("model_available expression requires winner and assistant candidate")
        elif self.expression_status == "model_unavailable":
            if self.winner_attempt_ref is not None or self.assistant_candidate_id is not None:
                raise ValueError("model_unavailable expression cannot carry winner or assistant candidate")
        return self

    @model_validator(mode="after")
    def validate_stage_shape(self) -> Self:
        forbidden = (
            "model_attempt_plan_ref",
            "model_attempt_refs",
            "model_attempt_plan_outcome_ref",
            "winner_attempt_ref",
            "assistant_candidate_id",
            "expression_status",
            "assistant_commit_ref",
            "delivery_ref",
        )
        if self.stage == "OUTCOME_COMMITTED_RESPONSE_OPEN":
            if not self.fact_refs:
                raise ValueError("outcome stage requires fact refs")
            for field in forbidden:
                if getattr(self, field) is not None:
                    raise ValueError(f"outcome stage forbids {field}")
        elif self.stage == "RESPONSE_COMMITTED":
            if (
                self.model_attempt_plan_ref is None
                or not self.model_attempt_refs
                or self.model_attempt_plan_outcome_ref is None
                or self.expression_status is None
            ):
                raise ValueError("response committed stage requires model attempt evidence")
            if self.assistant_commit_ref is not None or self.delivery_ref is not None:
                raise ValueError("response committed stage forbids commit/delivery refs")
        elif self.stage == "DELIVERY_OBSERVED":
            if self.delivery_ref is None or self.terminal_basis_ref is None:
                raise ValueError("delivery observed stage requires delivery and terminal basis")
            if self.assistant_commit_ref is not None:
                raise ValueError("delivery observed stage forbids assistant commit ref")
        elif self.stage == "ROOT_TERMINAL":
            if (
                self.root_terminal_status is None
                or self.root_terminal_reason is None
                or self.root_terminal_at_ms is None
                or self.terminal_basis_ref is None
            ):
                raise ValueError("root terminal stage requires terminal evidence")
            if self.assistant_commit_ref is not None:
                raise ValueError("root terminal stage forbids assistant commit ref")
        return self

    def computed_commit_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"commit_sha256"}))

    def with_computed_commit_sha256(self) -> Self:
        return self.model_copy(update={"commit_sha256": self.computed_commit_sha256()})


class LifeContextAuthorization(ContractModel):
    """Atomic context compilation receipt; it is not an execution ticket."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:LifeContextAuthorization",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    authorization_id: LifeContextAuthorizationId
    life_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0, le=9_007_199_254_740_991)
    principal_scope_hash: Sha256
    current_request_sha256: Sha256
    continuity_capsule_sha256: Sha256
    context_pack_id: OpaqueId
    context_pack_sha256: Sha256
    revisions: LifeRevisionVector
    initial_context: bool
    issued_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    expires_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    authorization_sha256: Sha256

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.expires_at_ms <= self.issued_at_ms:
            raise ValueError("life context authorization expiry is invalid")
        if self.revisions.life_id != self.life_id:
            raise ValueError("life context authorization crossed life revisions")
        if self.authorization_id != self.computed_authorization_id():
            raise ValueError("life context authorization ID is invalid")
        return self

    def computed_authorization_id(self) -> str:
        return "lca_" + canonical_sha256(
            {
                "context_pack_sha256": self.context_pack_sha256,
                "domain": "tiangong.life.context-authorization.v1",
                "generation": self.generation,
                "principal_scope_hash": self.principal_scope_hash,
                "request_id": self.request_id,
                "revisions_sha256": self.revisions.vector_sha256,
                "run_id": self.run_id,
            }
        )

    def computed_authorization_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )

    def has_valid_authorization_sha256(self) -> bool:
        return self.authorization_sha256 == self.computed_authorization_sha256()

    def with_computed_authorization_identity(self) -> Self:
        value = self.model_copy(update={"authorization_id": self.computed_authorization_id()})
        return value.model_copy(
            update={"authorization_sha256": value.computed_authorization_sha256()}
        )


__all__ = [
    "AppraisalVectorV3",
    "EvidenceClass",
    "LIFE_CONTRACT_SCHEMA_VERSION",
    "LifeCapsuleId",
    "LifeAuthorityHead",
    "LifeContextAuthorization",
    "LifeContextAuthorizationId",
    "LifeEventEnvelope",
    "LifeEventId",
    "LifeEventIngress",
    "LifeEventIngressReceipt",
    "LifeIngressId",
    "LifeIngressReceiptId",
    "RunLifeBinding",
    "RootContinuationBinding",
    "RootExperienceHead",
    "LifeSourceKind",
    "LifeRevisionVector",
    "LifeTurnCommit",
    "Milli",
    "SignedMilli",
    "TaskContinuityCapsule",
    "ViabilityDimension",
    "ViabilityDimensionName",
    "ViabilityObservation",
    "ViabilityObservationId",
    "ViabilityState",
    "WorkspaceFileRef",
    "derive_turn_commit_id",
    "derive_life_ingress_id",
    "derive_life_ingress_receipt_id",
    "derive_life_event_id",
]
