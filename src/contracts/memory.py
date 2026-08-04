"""Causal-memory, bounded-context, retention, and deletion contracts."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .causal import CausalHypothesisId
from .life import (
    EvidenceClass,
    LIFE_CONTRACT_SCHEMA_VERSION,
    LifeEventId,
    Milli,
    TaskContinuityCapsule,
)
from .models import ContractModel, OpaqueId, SCHEMA_BASE, Sha256


MemoryId = Annotated[str, StringConstraints(pattern=r"^mem_[0-9a-f]{64}$")]
MemoryRelationId = Annotated[str, StringConstraints(pattern=r"^mrl_[0-9a-f]{64}$")]
CausalNodeId = Annotated[str, StringConstraints(pattern=r"^cnd_[0-9a-f]{64}$")]
CausalContextPackId = Annotated[str, StringConstraints(pattern=r"^ccp_[0-9a-f]{64}$")]
PrivacyTombstoneId = Annotated[str, StringConstraints(pattern=r"^ptm_[0-9a-f]{64}$")]

RetentionClass = Literal[
    "EPHEMERAL_TOOL",
    "ACTIVE_WORKING",
    "CHECKPOINT",
    "TERMINAL_RESULT",
    "LONG_TERM_MEMORY",
    "LEGAL_HOLD",
]
MemoryLifecycleStatus = Literal[
    "active",
    "corrected",
    "superseded",
    "recall_suppressed",
    "deleted",
]


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like memory fields must be sorted and unique")
    return value


def _text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError("memory text must be NFC and contain no NUL")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ValueError("memory text contains a control character")
    return value


def retention_priority(assertion: "MemoryAssertionV3") -> int:
    """Return an exact ordering score without pretending it is a probability."""

    recurrence = min(1000, assertion.recurrence_count * 100)
    return (
        assertion.causal_utility_milli
        + assertion.user_importance_milli
        + assertion.verification_strength_milli
        + recurrence
        + assertion.future_dependency_milli
        - assertion.privacy_cost_milli
        - assertion.contradiction_penalty_milli
        - assertion.staleness_milli
    )


class MemoryAssertionV3(ContractModel):
    """One versioned assertion whose plaintext lives only in protected payload storage."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:MemoryAssertionV3",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    memory_id: MemoryId
    life_id: OpaqueId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_assertion_sha256: Sha256 | None = None
    assertion_kind: Literal[
        "observation",
        "user_preference",
        "hard_constraint",
        "goal",
        "relationship",
        "skill",
        "causal_summary",
        "legacy",
    ]
    epistemic_status: Literal[
        "observed",
        "user_asserted",
        "hypothesis",
        "verified",
    ]
    lifecycle_status: MemoryLifecycleStatus
    protected_payload_id: OpaqueId | None = None
    protected_payload_sha256: Sha256 | None = None
    deletion_tombstone_id: PrivacyTombstoneId | None = None
    privacy_scope: OpaqueId
    retention_class: RetentionClass
    source_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)
    causal_hypothesis_ids: tuple[CausalHypothesisId, ...] = Field(
        default=(), max_length=1024
    )
    causal_utility_milli: Milli
    user_importance_milli: Milli
    verification_strength_milli: Milli
    recurrence_count: int = Field(ge=0, le=1_000_000)
    future_dependency_milli: Milli
    privacy_cost_milli: Milli
    contradiction_penalty_milli: Milli
    staleness_milli: Milli
    valid_from_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    expires_at_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    assertion_sha256: Sha256

    _validate_sets = field_validator(
        "source_event_ids", "causal_hypothesis_ids"
    )(_sorted_unique)

    @model_validator(mode="after")
    def validate_revision_and_payload(self) -> Self:
        if (self.revision == 1) != (self.supersedes_assertion_sha256 is None):
            raise ValueError("memory assertion revision chain is invalid")
        if self.expires_at_ms is not None and self.expires_at_ms < self.valid_from_ms:
            raise ValueError("memory assertion expiry predates validity")
        payload_bound = (
            self.protected_payload_id is not None
            and self.protected_payload_sha256 is not None
        )
        if (self.protected_payload_id is None) != (
            self.protected_payload_sha256 is None
        ):
            raise ValueError("memory protected payload binding is incomplete")
        if self.lifecycle_status == "deleted":
            if payload_bound or self.deletion_tombstone_id is None:
                raise ValueError("deleted memory must retain only a deletion tombstone")
        elif self.deletion_tombstone_id is not None or not payload_bound:
            raise ValueError("live memory must bind a protected payload without a tombstone")
        if self.retention_class == "EPHEMERAL_TOOL" and self.assertion_kind in {
            "hard_constraint",
            "goal",
        }:
            raise ValueError("goal or hard-constraint memory cannot be ephemeral tool data")
        if self.epistemic_status == "verified" and not self.source_event_ids:
            raise ValueError("verified memory lacks source events")
        return self

    def computed_assertion_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"assertion_sha256"})
        )

    def has_valid_assertion_sha256(self) -> bool:
        return self.assertion_sha256 == self.computed_assertion_sha256()

    def with_computed_assertion_sha256(self) -> Self:
        return self.model_copy(
            update={"assertion_sha256": self.computed_assertion_sha256()}
        )


class MemoryRelationV3(ContractModel):
    """A non-causal memory relation; weak relations never become `causes`."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:MemoryRelationV3",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    relation_id: MemoryRelationId
    life_id: OpaqueId
    source_memory_id: MemoryId
    relation_kind: Literal[
        "supports",
        "related_to",
        "contradicts",
        "refines",
        "derived_from",
        "temporal_before",
        "legacy_unclassified",
    ]
    original_relation_label: str | None = Field(default=None, max_length=256)
    target_ref: OpaqueId
    evidence_class: EvidenceClass
    supporting_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    relation_sha256: Sha256

    _validate_events = field_validator("supporting_event_ids")(_sorted_unique)
    _validate_label = field_validator("original_relation_label")(
        lambda value: None if value is None else _text(value)
    )

    @model_validator(mode="after")
    def validate_legacy_label(self) -> Self:
        if (self.relation_kind == "legacy_unclassified") != (
            self.original_relation_label is not None
        ):
            raise ValueError("legacy memory relation label is inconsistent")
        return self

    def computed_relation_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"relation_sha256"})
        )

    def has_valid_relation_sha256(self) -> bool:
        return self.relation_sha256 == self.computed_relation_sha256()

    def with_computed_relation_sha256(self) -> Self:
        return self.model_copy(update={"relation_sha256": self.computed_relation_sha256()})


class CausalNodeV3(ContractModel):
    """A graph node bound to protected content and explicit recall state."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CausalNodeV3",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    node_id: CausalNodeId
    life_id: OpaqueId
    node_kind: Literal[
        "event",
        "memory_assertion",
        "episode",
        "artifact",
        "goal",
        "constraint",
        "outcome",
        "legacy",
    ]
    source_ref: OpaqueId
    protected_payload_id: OpaqueId
    protected_payload_sha256: Sha256
    privacy_scope: OpaqueId
    retention_class: RetentionClass
    recall_status: MemoryLifecycleStatus
    source_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    node_sha256: Sha256

    _validate_events = field_validator("source_event_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_recall_status(self) -> Self:
        if self.recall_status == "deleted":
            raise ValueError("deleted content is represented only by a tombstone, not a node")
        return self

    def computed_node_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"node_sha256"}))

    def has_valid_node_sha256(self) -> bool:
        return self.node_sha256 == self.computed_node_sha256()

    def with_computed_node_sha256(self) -> Self:
        return self.model_copy(update={"node_sha256": self.computed_node_sha256()})


class ContextTokenBudget(ContractModel):
    """Exact integer context budget and 75/85/92 pressure state."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ContextTokenBudget",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    model_context_limit_tokens: int = Field(ge=1, le=10_000_000)
    product_limit_tokens: int = Field(default=120_000, ge=1, le=120_000)
    output_reserve_tokens: int = Field(ge=0, le=1_000_000)
    tool_schema_reserve_tokens: int = Field(ge=0, le=1_000_000)
    authority_reserve_tokens: int = Field(ge=0, le=1_000_000)
    protocol_reserve_tokens: int = Field(ge=0, le=1_000_000)
    usable_budget_tokens: int = Field(ge=1, le=120_000)
    current_context_tokens: int = Field(ge=0, le=10_000_000)
    utilization_milli: Milli
    watermark: Literal[
        "BELOW_75",
        "CANDIDATE_75",
        "MUST_PERSIST_85",
        "MUST_SWITCH_92",
    ]

    @model_validator(mode="after")
    def validate_budget_math(self) -> Self:
        reserve = (
            self.output_reserve_tokens
            + self.tool_schema_reserve_tokens
            + self.authority_reserve_tokens
            + self.protocol_reserve_tokens
        )
        expected_usable = min(
            self.product_limit_tokens,
            self.model_context_limit_tokens - reserve,
        )
        if expected_usable < 1 or self.usable_budget_tokens != expected_usable:
            raise ValueError("context usable budget arithmetic is invalid")
        expected_utilization = min(
            1000,
            (self.current_context_tokens * 1000) // expected_usable,
        )
        if self.utilization_milli != expected_utilization:
            raise ValueError("context utilization arithmetic is invalid")
        expected_watermark = (
            "BELOW_75"
            if expected_utilization < 750
            else "CANDIDATE_75"
            if expected_utilization < 850
            else "MUST_PERSIST_85"
            if expected_utilization < 920
            else "MUST_SWITCH_92"
        )
        if self.watermark != expected_watermark:
            raise ValueError("context watermark does not match utilization")
        return self


class CausalContextItem(ContractModel):
    item_ref: OpaqueId
    item_kind: Literal[
        "memory",
        "event",
        "episode",
        "artifact",
        "goal",
        "constraint",
        "outcome",
    ]
    source_revision: int = Field(ge=1, le=9_007_199_254_740_991)
    summary: str = Field(min_length=1, max_length=20_000)
    epistemic_status: Literal[
        "observed",
        "user_asserted",
        "hypothesis",
        "verified",
    ]
    confidence_milli: Milli
    priority: int = Field(ge=-3_000, le=5_000)
    privacy_scope: OpaqueId
    token_count: int = Field(ge=1, le=1_000_000)
    supporting_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)

    _validate_summary = field_validator("summary")(_text)
    _validate_events = field_validator("supporting_event_ids")(_sorted_unique)


class CausalContextEdge(ContractModel):
    hypothesis_id: CausalHypothesisId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    cause_ref: OpaqueId
    effect_ref: OpaqueId
    relation: Literal[
        "temporal_before",
        "correlated_with",
        "contributes_to",
        "enables",
        "inhibits",
        "prevents",
        "causes",
    ]
    causal_basis: Literal[
        "temporal",
        "correlation",
        "model_hypothesis",
        "mechanism_supported",
        "intervention_supported",
    ]
    status: Literal["candidate", "supported", "contradicted", "retired"]
    confidence_milli: Milli
    supporting_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)
    counterevidence_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)

    _validate_sets = field_validator(
        "supporting_event_ids", "counterevidence_event_ids"
    )(_sorted_unique)

    @model_validator(mode="after")
    def validate_epistemic_state(self) -> Self:
        if self.relation == "causes" and self.causal_basis not in {
            "mechanism_supported",
            "intervention_supported",
        }:
            raise ValueError("context cannot upgrade a weak edge to causes")
        if self.status == "contradicted" and not self.counterevidence_event_ids:
            raise ValueError("contradicted context edge lacks counterevidence")
        return self


class CausalContextPack(ContractModel):
    """A verified, bounded projection; raw tool process is structurally absent."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CausalContextPack",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    pack_id: CausalContextPackId
    life_id: OpaqueId
    continuity: TaskContinuityCapsule
    seed_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    items: tuple[CausalContextItem, ...] = Field(default=(), max_length=4096)
    edges: tuple[CausalContextEdge, ...] = Field(default=(), max_length=4096)
    token_budget: ContextTokenBudget
    selected_token_count: int = Field(ge=0, le=120_000)
    omitted_item_count: int = Field(ge=0, le=10_000_000)
    visible_raw_tool_process_count: Literal[0] = 0
    integrity_status: Literal["VERIFIED"] = "VERIFIED"
    model_input_switched: Literal[False] = False
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    pack_sha256: Sha256

    _validate_seeds = field_validator("seed_refs")(_sorted_unique)

    @model_validator(mode="after")
    def validate_binding_and_budget(self) -> Self:
        if self.life_id != self.continuity.life_id:
            raise ValueError("causal context pack crossed a life identity")
        if self.selected_token_count > self.token_budget.usable_budget_tokens:
            raise ValueError("causal context pack exceeds its usable budget")
        item_refs = tuple(item.item_ref for item in self.items)
        if item_refs != tuple(sorted(set(item_refs))):
            raise ValueError("causal context items must be sorted and unique")
        edge_keys = tuple((edge.hypothesis_id, edge.revision) for edge in self.edges)
        if edge_keys != tuple(sorted(set(edge_keys))):
            raise ValueError("causal context edges must be sorted and unique")
        return self

    def computed_pack_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"pack_sha256"}))

    def has_valid_pack_sha256(self) -> bool:
        return self.pack_sha256 == self.computed_pack_sha256()

    def with_computed_pack_sha256(self) -> Self:
        return self.model_copy(update={"pack_sha256": self.computed_pack_sha256()})


class PrivacyDeletionTombstone(ContractModel):
    """Minimal deletion proof; it contains hashes and identifiers, never plaintext."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:PrivacyDeletionTombstone",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    tombstone_id: PrivacyTombstoneId
    life_id: OpaqueId
    target_kind: Literal["memory", "causal_node", "context_pack", "privacy_scope"]
    target_ref_hash: Sha256
    privacy_scope: OpaqueId
    destroyed_payload_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=4096)
    removed_index_entry_count: int = Field(ge=0, le=10_000_000)
    affected_capsule_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=4096)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    deletion_proof_sha256: Sha256

    _validate_sets = field_validator(
        "destroyed_payload_ids", "affected_capsule_ids"
    )(_sorted_unique)

    def computed_deletion_proof_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"deletion_proof_sha256"})
        )

    def has_valid_deletion_proof_sha256(self) -> bool:
        return self.deletion_proof_sha256 == self.computed_deletion_proof_sha256()

    def with_computed_deletion_proof_sha256(self) -> Self:
        return self.model_copy(
            update={"deletion_proof_sha256": self.computed_deletion_proof_sha256()}
        )


__all__ = [
    "CausalContextEdge",
    "CausalContextItem",
    "CausalContextPack",
    "CausalContextPackId",
    "CausalNodeId",
    "CausalNodeV3",
    "ContextTokenBudget",
    "MemoryAssertionV3",
    "MemoryId",
    "MemoryLifecycleStatus",
    "MemoryRelationId",
    "MemoryRelationV3",
    "PrivacyDeletionTombstone",
    "PrivacyTombstoneId",
    "RetentionClass",
    "retention_priority",
]
