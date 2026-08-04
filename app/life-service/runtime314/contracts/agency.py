"""Strict impact, agency, reflection, and capability-learning contracts."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .causal import CausalEpisodeId, CausalHypothesisId
from .life import (
    LIFE_CONTRACT_SCHEMA_VERSION,
    LifeEventId,
    Milli,
    SignedMilli,
    ViabilityDimensionName,
)
from .models import ActionId, ContractModel, OpaqueId, ReasonCode, SCHEMA_BASE, Sha256


AgencyDecisionId = Annotated[str, StringConstraints(pattern=r"^agd_[0-9a-f]{64}$")]
ActionCandidateId = Annotated[str, StringConstraints(pattern=r"^acd_[0-9a-f]{64}$")]
AutonomyPolicyId = Annotated[str, StringConstraints(pattern=r"^aup_[0-9a-f]{64}$")]
ReflectionId = Annotated[str, StringConstraints(pattern=r"^rfc_[0-9a-f]{64}$")]
OutcomeEvidenceId = Annotated[str, StringConstraints(pattern=r"^oev_[0-9a-f]{64}$")]
ReflectionQuestionDecisionId = Annotated[
    str, StringConstraints(pattern=r"^rqd_[0-9a-f]{64}$")
]
CapabilityEvidenceId = Annotated[str, StringConstraints(pattern=r"^cpe_[0-9a-f]{64}$")]
CapabilityLearningDecisionId = Annotated[
    str, StringConstraints(pattern=r"^cld_[0-9a-f]{64}$")
]
CapabilityRollbackId = Annotated[str, StringConstraints(pattern=r"^crb_[0-9a-f]{64}$")]
RiskClass = Literal["A0", "A1", "A2", "A3", "A4", "A5"]
AutonomyLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like agency fields must be sorted and unique")
    return value


def _text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError("agency text must be NFC and contain no NUL")
    return value


class ViabilityDelta(ContractModel):
    dimension: ViabilityDimensionName
    delta_milli: SignedMilli
    confidence_milli: Milli
    causal_hypothesis_ids: tuple[CausalHypothesisId, ...] = Field(default=(), max_length=256)

    _validate_hypotheses = field_validator("causal_hypothesis_ids")(_sorted_unique)


class ActionCandidate(ContractModel):
    """A model may propose this shape, but cannot provide authority or risk."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ActionCandidate",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    candidate_id: ActionCandidateId
    life_id: OpaqueId
    episode_id: CausalEpisodeId
    action_id: ActionId
    args_sha256: Sha256
    workspace_id: OpaqueId
    candidate_kind: Literal["action", "minimal_probe", "observation", "reflection"]
    objective: str = Field(min_length=1, max_length=20_000)
    expected_outcome: str = Field(min_length=1, max_length=20_000)
    goal_gain_milli: Milli
    information_gain_milli: Milli
    relationship_value_milli: Milli
    benefit_confidence_milli: Milli
    requires_user_preference: bool
    required_skill_id: OpaqueId | None = None
    evidence_refs: tuple[OpaqueId, ...] = Field(min_length=1, max_length=1024)
    causal_hypothesis_ids: tuple[CausalHypothesisId, ...] = Field(
        default=(), max_length=256
    )
    proposed_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    expires_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    candidate_sha256: Sha256

    _validate_sets = field_validator(
        "evidence_refs",
        "causal_hypothesis_ids",
    )(_sorted_unique)
    _validate_text = field_validator("objective", "expected_outcome")(_text)

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        if self.expires_at_ms <= self.proposed_at_ms:
            raise ValueError("action candidate must have a positive lifetime")
        return self

    def computed_candidate_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"candidate_sha256"})
        )

    def has_valid_candidate_sha256(self) -> bool:
        return self.candidate_sha256 == self.computed_candidate_sha256()

    def with_computed_candidate_sha256(self) -> Self:
        return self.model_copy(
            update={"candidate_sha256": self.computed_candidate_sha256()}
        )


class ActionCandidateVNext(ContractModel):
    """A typed Life choice; non-actions cannot smuggle execution fields."""
    schema_id: Literal["ActionCandidateVNext"] = "ActionCandidateVNext"
    schema_version: Literal["tiangong.action_candidate.v4"] = "tiangong.action_candidate.v4"
    candidate_id: ActionCandidateId
    life_id: OpaqueId
    episode_id: CausalEpisodeId
    candidate_kind: Literal["action", "minimal_probe", "observation", "reflection", "ask_user", "wait", "reject", "respond", "no_op"]
    action_id: ActionId | None = None
    args_sha256: Sha256 | None = None
    workspace_id: OpaqueId | None = None
    question_or_expression_ref: OpaqueId | None = None
    objective: str = Field(min_length=1, max_length=20_000)
    expected_outcome: str = Field(min_length=1, max_length=20_000)
    evidence_refs: tuple[OpaqueId, ...] = Field(min_length=1, max_length=1024)
    proposed_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    candidate_sha256: Sha256
    _vnext_evidence = field_validator("evidence_refs")(_sorted_unique)
    _vnext_text = field_validator("objective", "expected_outcome")(_text)
    @model_validator(mode="after")
    def validate_candidate_shape(self) -> Self:
        action_fields = (self.action_id, self.args_sha256, self.workspace_id)
        executable = self.candidate_kind in {"action", "minimal_probe"}
        if executable != all(value is not None for value in action_fields):
            raise ValueError("only action candidates carry a complete invocation")
        if self.candidate_kind in {"ask_user", "respond"} and self.question_or_expression_ref is None:
            raise ValueError("ask/respond candidate requires expression reference")
        if self.candidate_kind not in {"ask_user", "respond"} and self.question_or_expression_ref is not None:
            raise ValueError("only ask/respond carries expression reference")
        if self.expires_at_ms <= self.proposed_at_ms:
            raise ValueError("candidate lifetime is invalid")
        return self
    def computed_candidate_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"candidate_sha256"}))
    def with_computed_candidate_sha256(self) -> Self:
        return self.model_copy(update={"candidate_sha256": self.computed_candidate_sha256()})


class AutonomyActionUsage(ContractModel):
    action_id: ActionId
    execution_count: int = Field(ge=0, le=1_000_000)
    last_executed_at_ms: int | None = Field(
        default=None, ge=0, le=9_007_199_254_740_991
    )

    @model_validator(mode="after")
    def validate_last_execution(self) -> Self:
        if (self.execution_count == 0) != (self.last_executed_at_ms is None):
            raise ValueError("autonomy usage count and last execution disagree")
        return self


class AutonomyUsageSnapshot(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AutonomyUsageSnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    life_id: OpaqueId
    policy_snapshot_hash: Sha256
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_usage_sha256: Sha256 | None = None
    day_start_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    day_end_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    execution_count: int = Field(ge=0, le=1_000_000)
    resource_cost_milli: int = Field(ge=0, le=1_000_000_000, strict=True)
    action_usage: tuple[AutonomyActionUsage, ...] = Field(default=(), max_length=4096)
    source_decision_hashes: tuple[Sha256, ...] = Field(default=(), max_length=100_000)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    usage_sha256: Sha256

    _validate_sources = field_validator("source_decision_hashes")(_sorted_unique)

    @field_validator("action_usage")
    @classmethod
    def validate_action_usage(
        cls, value: tuple[AutonomyActionUsage, ...]
    ) -> tuple[AutonomyActionUsage, ...]:
        names = tuple(item.action_id for item in value)
        if names != tuple(sorted(set(names))):
            raise ValueError("autonomy action usage must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_window_and_totals(self) -> Self:
        if self.day_end_ms <= self.day_start_ms:
            raise ValueError("autonomy usage day window is invalid")
        if sum(item.execution_count for item in self.action_usage) != self.execution_count:
            raise ValueError("autonomy usage total disagrees with per-action usage")
        if len(self.source_decision_hashes) != self.execution_count:
            raise ValueError("autonomy usage lacks one decision fact per execution")
        if (self.revision == 1) != (self.supersedes_usage_sha256 is None):
            raise ValueError("autonomy usage revision chain is invalid")
        return self

    def computed_usage_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"usage_sha256"}))

    def has_valid_usage_sha256(self) -> bool:
        return self.usage_sha256 == self.computed_usage_sha256()

    def with_computed_usage_sha256(self) -> Self:
        return self.model_copy(update={"usage_sha256": self.computed_usage_sha256()})


class AutonomyPolicySnapshot(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AutonomyPolicySnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    policy_id: AutonomyPolicyId
    life_id: OpaqueId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_policy_sha256: Sha256 | None = None
    autonomy_level: AutonomyLevel
    user_paused: bool
    shutdown_requested: bool
    privacy_lockdown: bool
    allowed_action_ids: tuple[ActionId, ...] = Field(default=(), max_length=4096)
    allowed_workspace_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=4096)
    active_window_start_minute_utc: int = Field(ge=0, le=1439, strict=True)
    active_window_end_minute_utc: int = Field(ge=0, le=1439, strict=True)
    daily_execution_budget: int = Field(ge=0, le=1_000_000, strict=True)
    daily_resource_budget_milli: int = Field(ge=0, le=1_000_000_000, strict=True)
    per_action_daily_limit: int = Field(ge=0, le=1_000_000, strict=True)
    minimum_interval_ms: int = Field(ge=0, le=86_400_000, strict=True)
    risk_ceiling: RiskClass
    allow_minimal_probes: bool
    minimum_execute_confidence_milli: Milli
    effective_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    expires_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    policy_sha256: Sha256

    _validate_sets = field_validator(
        "allowed_action_ids",
        "allowed_workspace_ids",
    )(_sorted_unique)

    @model_validator(mode="after")
    def validate_policy_lifecycle(self) -> Self:
        if self.expires_at_ms <= self.effective_at_ms:
            raise ValueError("autonomy policy lifetime is invalid")
        if (self.revision == 1) != (self.supersedes_policy_sha256 is None):
            raise ValueError("autonomy policy revision chain is invalid")
        return self

    def computed_policy_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_policy_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_policy_sha256()

    def with_computed_policy_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_policy_sha256()})


class ActionImpact(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ActionImpact",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    impact_id: OpaqueId
    life_id: OpaqueId
    action_id: OpaqueId
    intent_sha256: Sha256 = "0" * 64
    dynamic_risk: RiskClass = "A0"
    target_snapshot_sha256: Sha256 | None = None
    affected_internal_nodes: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    touches_identity: bool
    touches_soul: bool
    touches_memory_keys: bool
    touches_policy: bool
    touches_core_code: bool
    workspace_scope_milli: Milli
    external_recipient_count: int = Field(ge=0, le=1_000_000)
    credential_scope_milli: Milli
    privacy_scope_milli: Milli
    blast_radius_milli: Milli
    irreversibility_milli: Milli
    uncertainty_milli: Milli
    rollback_proof_ref: OpaqueId | None = None
    estimated_resource_cost_milli: Milli
    predicted_viability_deltas: tuple[ViabilityDelta, ...] = Field(default=(), max_length=64)
    source_event_ids: tuple[LifeEventId, ...] = Field(min_length=1, max_length=1024)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    impact_sha256: Sha256

    _validate_sets = field_validator(
        "affected_internal_nodes",
        "source_event_ids",
    )(_sorted_unique)

    @field_validator("predicted_viability_deltas")
    @classmethod
    def validate_viability_deltas(
        cls,
        value: tuple[ViabilityDelta, ...],
    ) -> tuple[ViabilityDelta, ...]:
        names = tuple(item.dimension for item in value)
        if names != tuple(sorted(set(names))):
            raise ValueError("predicted viability dimensions must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_rollback_claim(self) -> Self:
        if self.irreversibility_milli >= 800 and self.rollback_proof_ref is not None:
            raise ValueError("highly irreversible action cannot claim a rollback proof")
        return self

    def computed_impact_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"impact_sha256"})
        )

    def has_valid_impact_sha256(self) -> bool:
        return self.impact_sha256 == self.computed_impact_sha256()

    def with_computed_impact_sha256(self) -> Self:
        return self.model_copy(update={"impact_sha256": self.computed_impact_sha256()})


class AgencyScoreBreakdown(ContractModel):
    goal_gain_milli: Milli
    viability_gain_milli: Milli
    information_gain_milli: Milli
    relationship_value_milli: Milli
    resource_cost_milli: Milli
    expected_harm_milli: Milli
    uncertainty_penalty_milli: Milli
    irreversibility_penalty_milli: Milli
    expected_utility_milli: int = Field(ge=-4000, le=4000, strict=True)
    utility_lcb_milli: int = Field(ge=-5000, le=4000, strict=True)

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        expected = (
            self.goal_gain_milli
            + self.viability_gain_milli
            + self.information_gain_milli
            + self.relationship_value_milli
            - self.resource_cost_milli
            - self.expected_harm_milli
            - self.irreversibility_penalty_milli
        )
        if self.expected_utility_milli != expected:
            raise ValueError("agency expected utility disagrees with its score components")
        if self.utility_lcb_milli != expected - self.uncertainty_penalty_milli:
            raise ValueError("agency lower confidence bound is invalid")
        return self


class AgencyDecision(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AgencyDecision",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    decision_id: AgencyDecisionId
    life_id: OpaqueId
    episode_id: CausalEpisodeId
    candidate_set_sha256: Sha256
    selected_candidate_id: OpaqueId | None = None
    action_impact_sha256: Sha256 | None = None
    score_breakdown: AgencyScoreBreakdown | None = None
    computed_risk: RiskClass
    policy_ceiling: RiskClass
    required_confirmation: bool
    confirmation_grant_ref: OpaqueId | None = None
    required_skill_activation: bool
    skill_activation_ref: OpaqueId | None = None
    outcome: Literal["observe", "reflect", "ask_user", "wait", "execute", "reject"]
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=64)
    state_revision_hashes: tuple[Sha256, ...] = Field(min_length=1, max_length=64)
    policy_snapshot_hash: Sha256
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    decision_sha256: Sha256

    _validate_sets = field_validator(
        "reason_codes",
        "state_revision_hashes",
    )(_sorted_unique)

    @model_validator(mode="after")
    def validate_authorization_shape(self) -> Self:
        order = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4, "A5": 5}
        if self.outcome == "execute":
            if (
                self.selected_candidate_id is None
                or self.action_impact_sha256 is None
                or self.score_breakdown is None
            ):
                raise ValueError("executed agency decision lacks action evidence")
            if order[self.computed_risk] > order[self.policy_ceiling]:
                raise ValueError("agency execution exceeds the policy ceiling")
            if self.computed_risk == "A5":
                raise ValueError("A5 cannot be autonomously executed")
            if self.required_confirmation or self.confirmation_grant_ref is not None:
                raise ValueError("A0-A4 agency execution must not consume confirmation grants")
            if self.required_skill_activation and self.skill_activation_ref is None:
                raise ValueError("agency execution lacks its Skill activation")
        else:
            if self.confirmation_grant_ref is not None or self.skill_activation_ref is not None:
                raise ValueError("non-executing agency decision cannot consume grants")
        return self

    def computed_decision_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )

    def has_valid_decision_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_decision_sha256()

    def with_computed_decision_sha256(self) -> Self:
        return self.model_copy(update={"decision_sha256": self.computed_decision_sha256()})


class ReflectionCard(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ReflectionCard",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    reflection_id: ReflectionId
    life_id: OpaqueId
    episode_id: CausalEpisodeId
    expected_outcome: str = Field(min_length=1, max_length=20_000)
    observed_outcome: str = Field(min_length=1, max_length=50_000)
    prediction_error_milli: Milli
    success_dimensions: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    failure_dimensions: tuple[Literal[
        "input_error",
        "model_reasoning_error",
        "tool_error",
        "environment_error",
        "policy_block",
        "insufficient_permission",
        "stale_context",
        "user_preference_mismatch",
        "unknown",
    ], ...] = Field(default=(), max_length=32)
    candidate_cause_ids: tuple[CausalHypothesisId, ...] = Field(default=(), max_length=1024)
    counterevidence_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    alternative_explanations: tuple[str, ...] = Field(default=(), max_length=256)
    counterfactual_actions: tuple[str, ...] = Field(default=(), max_length=256)
    next_minimal_experiment: str | None = Field(default=None, max_length=20_000)
    lessons: tuple[str, ...] = Field(default=(), max_length=256)
    memory_candidate_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    capability_evidence_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    user_question: str | None = Field(default=None, max_length=20_000)
    user_question_value_of_information_milli: Milli
    confidence_milli: Milli
    reviewer: Literal["deterministic", "model_assisted", "user_confirmed"]
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    reflection_sha256: Sha256

    _validate_sets = field_validator(
        "success_dimensions",
        "failure_dimensions",
        "candidate_cause_ids",
        "counterevidence_refs",
        "memory_candidate_refs",
        "capability_evidence_refs",
    )(_sorted_unique)
    _validate_texts = field_validator(
        "expected_outcome",
        "observed_outcome",
        "alternative_explanations",
        "counterfactual_actions",
        "next_minimal_experiment",
        "lessons",
        "user_question",
    )(
        lambda value: (
            None
            if value is None
            else tuple(_text(item) for item in value)
            if isinstance(value, tuple)
            else _text(value)
        )
    )

    @model_validator(mode="after")
    def validate_feedback_question(self) -> Self:
        if (self.user_question is None) != (
            self.user_question_value_of_information_milli == 0
        ):
            raise ValueError("reflection user question and information value disagree")
        if not self.success_dimensions and not self.failure_dimensions:
            raise ValueError("reflection must classify at least one outcome dimension")
        return self

    def computed_reflection_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"reflection_sha256"})
        )

    def has_valid_reflection_sha256(self) -> bool:
        return self.reflection_sha256 == self.computed_reflection_sha256()

    def with_computed_reflection_sha256(self) -> Self:
        return self.model_copy(
            update={"reflection_sha256": self.computed_reflection_sha256()}
        )


class EpisodeOutcomeEvidence(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:EpisodeOutcomeEvidence",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    outcome_evidence_id: OutcomeEvidenceId
    life_id: OpaqueId
    episode_id: CausalEpisodeId
    outcome_status: Literal["success", "failure", "partial", "aborted"]
    observed_outcome: str = Field(min_length=1, max_length=50_000)
    observed_quality_milli: Milli
    predicted_success_milli: Milli
    prediction_snapshot_hash: Sha256
    completion_decision_sha256: Sha256
    terminal_fact_hashes: tuple[Sha256, ...] = Field(min_length=1, max_length=2048)
    outcome_event_ids: tuple[LifeEventId, ...] = Field(min_length=1, max_length=2048)
    failure_category: Literal[
        "input_error",
        "model_reasoning_error",
        "tool_error",
        "environment_error",
        "policy_block",
        "insufficient_permission",
        "stale_context",
        "user_preference_mismatch",
        "unknown",
    ] | None = None
    method_attribution: Literal[
        "capability", "input", "environment", "policy", "user_preference", "unknown"
    ]
    supported_cause_ids: tuple[CausalHypothesisId, ...] = Field(default=(), max_length=1024)
    counterevidence_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    alternative_explanation_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    context_fingerprint_sha256: Sha256
    preference_domain: OpaqueId | None = None
    user_preference_uncertainty_milli: Milli
    action_risk: RiskClass
    counterfactual_actions: tuple[str, ...] = Field(default=(), max_length=256)
    next_minimal_experiment: str | None = Field(default=None, max_length=20_000)
    candidate_user_question: str | None = Field(default=None, max_length=20_000)
    occurred_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    evidence_sha256: Sha256

    _validate_sets = field_validator(
        "terminal_fact_hashes",
        "outcome_event_ids",
        "supported_cause_ids",
        "counterevidence_refs",
        "alternative_explanation_refs",
    )(_sorted_unique)
    _validate_texts = field_validator(
        "observed_outcome",
        "counterfactual_actions",
        "next_minimal_experiment",
        "candidate_user_question",
    )(
        lambda value: (
            None
            if value is None
            else tuple(_text(item) for item in value)
            if isinstance(value, tuple)
            else _text(value)
        )
    )

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> Self:
        if (self.outcome_status == "success") != (self.failure_category is None):
            raise ValueError("episode outcome failure classification is inconsistent")
        if self.preference_domain is None and self.user_preference_uncertainty_milli:
            raise ValueError("preference uncertainty lacks its domain")
        if self.next_minimal_experiment is not None and not self.counterfactual_actions:
            raise ValueError("minimal experiment lacks a counterfactual action")
        return self

    def computed_evidence_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"evidence_sha256"}))

    def has_valid_evidence_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_evidence_sha256()

    def with_computed_evidence_sha256(self) -> Self:
        return self.model_copy(update={"evidence_sha256": self.computed_evidence_sha256()})


class ReflectionQuestionDecision(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ReflectionQuestionDecision",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    question_decision_id: ReflectionQuestionDecisionId
    life_id: OpaqueId
    reflection_id: ReflectionId
    preference_domain: OpaqueId
    outcome: Literal["ask_user", "suppress"]
    question: str | None = Field(default=None, max_length=20_000)
    value_of_information_milli: Milli
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=32)
    last_asked_at_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    cooldown_until_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    policy_sha256: Sha256
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    decision_sha256: Sha256

    _validate_reasons = field_validator("reason_codes")(_sorted_unique)
    _validate_question = field_validator("question")(
        lambda value: None if value is None else _text(value)
    )

    @model_validator(mode="after")
    def validate_question_shape(self) -> Self:
        if (self.outcome == "ask_user") != (self.question is not None):
            raise ValueError("reflection question outcome and text disagree")
        if self.cooldown_until_ms < self.created_at_ms:
            raise ValueError("reflection question cooldown precedes decision")
        return self

    def computed_decision_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"decision_sha256"}))

    def has_valid_decision_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_decision_sha256()

    def with_computed_decision_sha256(self) -> Self:
        return self.model_copy(update={"decision_sha256": self.computed_decision_sha256()})


class CapabilityEvidence(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CapabilityEvidence",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    evidence_id: CapabilityEvidenceId
    capability_id: OpaqueId
    capability_version: OpaqueId
    life_id: OpaqueId
    episode_id: CausalEpisodeId
    reflection_id: ReflectionId
    context_fingerprint_sha256: Sha256
    outcome: Literal["success", "failure", "partial", "aborted", "rollback"]
    attribution: Literal[
        "capability", "input", "environment", "policy", "user_preference", "unknown"
    ]
    causal_support: Literal["supported", "plausible", "correlation_only", "refuted"]
    verified: bool
    quality_milli: Milli
    prediction_error_milli: Milli
    terminal_fact_hashes: tuple[Sha256, ...] = Field(min_length=1, max_length=2048)
    action_impact_sha256: Sha256
    impact_floor: RiskClass
    touches_core_code: bool
    eligible_success: bool
    eligible_failure: bool
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    evidence_sha256: Sha256

    _validate_facts = field_validator("terminal_fact_hashes")(_sorted_unique)

    @model_validator(mode="after")
    def validate_eligibility(self) -> Self:
        success = (
            self.verified
            and self.outcome == "success"
            and self.attribution == "capability"
            and self.causal_support == "supported"
            and self.quality_milli >= 800
        )
        failure = (
            self.verified
            and self.outcome in {"failure", "rollback"}
            and self.attribution == "capability"
        )
        if self.eligible_success != success or self.eligible_failure != failure:
            raise ValueError("capability evidence eligibility is not machine-consistent")
        if self.eligible_success and self.eligible_failure:
            raise ValueError("capability evidence cannot be both success and failure")
        return self

    def computed_evidence_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"evidence_sha256"}))

    def has_valid_evidence_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_evidence_sha256()

    def with_computed_evidence_sha256(self) -> Self:
        return self.model_copy(update={"evidence_sha256": self.computed_evidence_sha256()})


class CapabilityLearningDecision(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CapabilityLearningDecision",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    learning_decision_id: CapabilityLearningDecisionId
    capability_id: OpaqueId
    capability_version: OpaqueId
    life_id: OpaqueId
    previous_profile_sha256: Sha256 | None = None
    evidence_set_sha256: Sha256
    eligible_successes: int = Field(ge=0, le=1_000_000)
    eligible_failures: int = Field(ge=0, le=1_000_000)
    independent_context_count: int = Field(ge=0, le=1_000_000)
    minimum_successes: int = Field(ge=1, le=1_000_000)
    minimum_independent_contexts: int = Field(ge=1, le=1_000_000)
    proficiency_mean_milli: Milli
    proficiency_lower_bound_milli: Milli
    outcome: Literal[
        "hold", "sandbox_candidate", "human_review", "core_review", "rollback"
    ]
    review_level: Literal["OBSERVE", "SANDBOX", "HUMAN_REVIEW", "CORE_REVIEW"]
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=64)
    cooldown_until_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    resulting_profile_sha256: Sha256
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    decision_sha256: Sha256

    _validate_reasons = field_validator("reason_codes")(_sorted_unique)

    @model_validator(mode="after")
    def validate_learning_thresholds(self) -> Self:
        if self.proficiency_lower_bound_milli > self.proficiency_mean_milli:
            raise ValueError("learning lower confidence bound exceeds its mean")
        publishable = self.outcome != "hold"
        if publishable and self.outcome != "rollback" and (
            self.eligible_successes < self.minimum_successes
            or self.independent_context_count < self.minimum_independent_contexts
        ):
            raise ValueError("capability learning decision bypasses evidence thresholds")
        return self

    def computed_decision_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"decision_sha256"}))

    def has_valid_decision_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_decision_sha256()

    def with_computed_decision_sha256(self) -> Self:
        return self.model_copy(update={"decision_sha256": self.computed_decision_sha256()})


class CapabilityRollbackRecord(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CapabilityRollbackRecord",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    rollback_id: CapabilityRollbackId
    capability_id: OpaqueId
    capability_version: OpaqueId
    life_id: OpaqueId
    rolled_back_profile_sha256: Sha256
    resulting_profile_sha256: Sha256
    trigger_evidence_ids: tuple[CapabilityEvidenceId, ...] = Field(min_length=1, max_length=1024)
    invalidated_context_pack_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=4096)
    invalidated_skill_activation_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=4096)
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=64)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    rollback_sha256: Sha256

    _validate_sets = field_validator(
        "trigger_evidence_ids",
        "invalidated_context_pack_ids",
        "invalidated_skill_activation_ids",
        "reason_codes",
    )(_sorted_unique)

    def computed_rollback_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"rollback_sha256"}))

    def has_valid_rollback_sha256(self) -> bool:
        return self.rollback_sha256 == self.computed_rollback_sha256()

    def with_computed_rollback_sha256(self) -> Self:
        return self.model_copy(update={"rollback_sha256": self.computed_rollback_sha256()})


class CapabilityProfile(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CapabilityProfile",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    capability_id: OpaqueId
    life_id: OpaqueId
    version: OpaqueId
    profile_revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_profile_sha256: Sha256 | None = None
    scope: str = Field(min_length=1, max_length=20_000)
    verified_successes: int = Field(ge=0, le=1_000_000)
    verified_failures: int = Field(ge=0, le=1_000_000)
    independent_context_count: int = Field(ge=0, le=1_000_000)
    calibration_error_milli: Milli
    rollback_count: int = Field(ge=0, le=1_000_000)
    last_regression_at_ms: int | None = Field(
        default=None,
        ge=0,
        le=9_007_199_254_740_991,
    )
    proficiency_mean_milli: Milli
    proficiency_lower_bound_milli: Milli
    evidence_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=4096)
    impact_floor: RiskClass
    review_level: Literal["OBSERVE", "SANDBOX", "HUMAN_REVIEW", "CORE_REVIEW"]
    updated_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    profile_sha256: Sha256

    _validate_evidence = field_validator("evidence_refs")(_sorted_unique)
    _validate_scope = field_validator("scope")(_text)

    @model_validator(mode="after")
    def validate_calibration(self) -> Self:
        if (self.profile_revision == 1) != (self.supersedes_profile_sha256 is None):
            raise ValueError("capability profile revision chain is invalid")
        total = self.verified_successes + self.verified_failures
        if self.proficiency_lower_bound_milli > self.proficiency_mean_milli:
            raise ValueError("capability lower bound exceeds its mean")
        if total == 0 and (
            self.proficiency_mean_milli != 0
            or self.proficiency_lower_bound_milli != 0
        ):
            raise ValueError("capability without evidence cannot claim proficiency")
        if self.independent_context_count > total:
            raise ValueError("capability context count exceeds verified outcomes")
        if bool(self.evidence_refs) != (total > 0):
            raise ValueError("capability evidence references disagree with outcome counts")
        return self

    def computed_profile_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )

    def has_valid_profile_sha256(self) -> bool:
        return self.profile_sha256 == self.computed_profile_sha256()

    def with_computed_profile_sha256(self) -> Self:
        return self.model_copy(update={"profile_sha256": self.computed_profile_sha256()})


__all__ = [
    "ActionCandidate",
    "ActionCandidateVNext",
    "ActionCandidateId",
    "ActionImpact",
    "AgencyDecision",
    "AgencyDecisionId",
    "AgencyScoreBreakdown",
    "AutonomyActionUsage",
    "AutonomyLevel",
    "AutonomyPolicyId",
    "AutonomyPolicySnapshot",
    "AutonomyUsageSnapshot",
    "CapabilityProfile",
    "CapabilityEvidence",
    "CapabilityEvidenceId",
    "CapabilityLearningDecision",
    "CapabilityLearningDecisionId",
    "CapabilityRollbackId",
    "CapabilityRollbackRecord",
    "EpisodeOutcomeEvidence",
    "OutcomeEvidenceId",
    "ReflectionCard",
    "ReflectionId",
    "ReflectionQuestionDecision",
    "ReflectionQuestionDecisionId",
    "RiskClass",
    "ViabilityDelta",
    "ViabilityDimensionName",
]
