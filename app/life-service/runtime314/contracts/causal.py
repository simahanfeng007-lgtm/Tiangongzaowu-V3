"""Versioned causal episodes and hypotheses.

Facts remain immutable life events. Causal explanations are separate,
revisable hypotheses with explicit evidence ceilings.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .life import EvidenceClass, LIFE_CONTRACT_SCHEMA_VERSION, LifeEventId, Milli
from .models import ContractModel, OpaqueId, SCHEMA_BASE, Sha256


CausalEpisodeId = Annotated[str, StringConstraints(pattern=r"^cep_[0-9a-f]{64}$")]
CausalHypothesisId = Annotated[str, StringConstraints(pattern=r"^chy_[0-9a-f]{64}$")]


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like causal fields must be sorted and unique")
    return value


def _text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError("causal text must be NFC and contain no NUL")
    return value


class CausalEpisode(ContractModel):
    """One intention/prediction/action/outcome unit."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CausalEpisode",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    episode_id: CausalEpisodeId
    life_id: OpaqueId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_episode_sha256: Sha256 | None = None
    trigger_event_ids: tuple[LifeEventId, ...] = Field(min_length=1, max_length=256)
    context_state_hashes: tuple[Sha256, ...] = Field(min_length=1, max_length=64)
    intention: str = Field(min_length=1, max_length=20_000)
    prior_prediction: str = Field(min_length=1, max_length=20_000)
    candidate_action_ids: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    selected_action_id: OpaqueId | None = None
    authorization_ref: OpaqueId | None = None
    mediator_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=2048)
    outcome_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=2048)
    outcome_evaluation: str | None = Field(default=None, max_length=50_000)
    prediction_error_milli: Milli | None = None
    terminal_status: Literal["OPEN", "CLOSED", "ABORTED"]
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    closed_at_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    episode_sha256: Sha256

    _validate_sets = field_validator(
        "trigger_event_ids",
        "context_state_hashes",
        "candidate_action_ids",
        "mediator_event_ids",
        "outcome_event_ids",
    )(_sorted_unique)
    _validate_texts = field_validator(
        "intention",
        "prior_prediction",
        "outcome_evaluation",
    )(lambda value: None if value is None else _text(value))

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if (self.revision == 1) != (self.supersedes_episode_sha256 is None):
            raise ValueError("causal episode revision chain is invalid")
        if self.selected_action_id is not None and self.selected_action_id not in self.candidate_action_ids:
            raise ValueError("selected causal action is not in the candidate set")
        if self.terminal_status == "OPEN":
            if (
                self.closed_at_ms is not None
                or self.outcome_evaluation is not None
                or self.prediction_error_milli is not None
            ):
                raise ValueError("open causal episode carries terminal evidence")
        else:
            if (
                self.closed_at_ms is None
                or self.closed_at_ms < self.created_at_ms
                or not self.outcome_event_ids
                or self.outcome_evaluation is None
                or self.prediction_error_milli is None
            ):
                raise ValueError("terminal causal episode is incomplete")
        if self.authorization_ref is not None and self.selected_action_id is None:
            raise ValueError("authorization cannot exist without a selected action")
        return self

    def computed_episode_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"episode_sha256"})
        )

    def has_valid_episode_sha256(self) -> bool:
        return self.episode_sha256 == self.computed_episode_sha256()

    def with_computed_episode_sha256(self) -> Self:
        return self.model_copy(update={"episode_sha256": self.computed_episode_sha256()})


class CausalHypothesis(ContractModel):
    """A revisable causal edge with evidence, counterevidence, and alternatives."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CausalHypothesis",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    hypothesis_id: CausalHypothesisId
    life_id: OpaqueId
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
    mechanism_summary: str = Field(default="", max_length=20_000)
    confidence_milli: Milli
    evidence_class: EvidenceClass
    supporting_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)
    counterevidence_event_ids: tuple[LifeEventId, ...] = Field(default=(), max_length=4096)
    alternative_hypothesis_ids: tuple[CausalHypothesisId, ...] = Field(default=(), max_length=1024)
    confounder_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=1024)
    intervention_status: Literal[
        "none",
        "natural_experiment",
        "controlled_test",
        "repeated_intervention",
    ]
    valid_from_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    valid_until_ms: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    supersedes_id: CausalHypothesisId | None = None
    status: Literal["candidate", "supported", "contradicted", "retired"]
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    hypothesis_sha256: Sha256

    _validate_sets = field_validator(
        "supporting_event_ids",
        "counterevidence_event_ids",
        "alternative_hypothesis_ids",
        "confounder_refs",
    )(_sorted_unique)
    _validate_mechanism = field_validator("mechanism_summary")(_text)

    @model_validator(mode="after")
    def validate_epistemic_ceiling(self) -> Self:
        if self.cause_ref == self.effect_ref:
            raise ValueError("a causal hypothesis cannot directly cause itself")
        if self.valid_until_ms is not None and self.valid_until_ms < self.valid_from_ms:
            raise ValueError("causal validity interval is inverted")
        if set(self.supporting_event_ids) & set(self.counterevidence_event_ids):
            raise ValueError("one event cannot be support and counterevidence simultaneously")
        if self.hypothesis_id in self.alternative_hypothesis_ids:
            raise ValueError("a causal hypothesis cannot be its own alternative")
        expected_basis = {
            "temporal_before": {"temporal"},
            "correlated_with": {"correlation"},
        }
        if self.relation in expected_basis and self.causal_basis not in expected_basis[self.relation]:
            raise ValueError("weak causal relation uses an overstated evidence basis")
        if self.relation == "causes":
            if self.causal_basis not in {"mechanism_supported", "intervention_supported"}:
                raise ValueError("causes requires mechanism or intervention support")
            if not self.mechanism_summary.strip():
                raise ValueError("causes requires a mechanism summary")
            if not self.supporting_event_ids:
                raise ValueError("causes requires supporting events")
        if self.causal_basis == "intervention_supported" and self.intervention_status == "none":
            raise ValueError("intervention-supported causality lacks an intervention")
        ceiling = 1000
        if self.relation == "temporal_before":
            ceiling = min(ceiling, 600)
        if self.relation == "correlated_with":
            ceiling = min(ceiling, 700)
        if self.causal_basis == "model_hypothesis" or self.evidence_class in {
            "model_inference",
            "reflection",
        }:
            ceiling = min(ceiling, 750)
        if self.confidence_milli > ceiling:
            raise ValueError("causal confidence exceeds its evidence ceiling")
        if self.status == "supported" and not self.supporting_event_ids:
            raise ValueError("supported causal hypothesis lacks supporting evidence")
        if self.status == "contradicted" and not self.counterevidence_event_ids:
            raise ValueError("contradicted causal hypothesis lacks counterevidence")
        if self.revision == 1 and self.supersedes_id is not None:
            raise ValueError("causal hypothesis genesis cannot supersede a revision")
        if self.revision > 1 and self.supersedes_id != self.hypothesis_id:
            raise ValueError("causal hypothesis revision must supersede its stable identity")
        return self

    def computed_hypothesis_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"hypothesis_sha256"})
        )

    def has_valid_hypothesis_sha256(self) -> bool:
        return self.hypothesis_sha256 == self.computed_hypothesis_sha256()

    def with_computed_hypothesis_sha256(self) -> Self:
        return self.model_copy(
            update={"hypothesis_sha256": self.computed_hypothesis_sha256()}
        )


class CausalEpisodeVNext(ContractModel):
    """G1 ordered child episode; terminal rows are immutable evidence."""
    schema_id: Literal["CausalEpisodeVNext"] = "CausalEpisodeVNext"
    schema_version: Literal["tiangong.causal_episode.v4"] = "tiangong.causal_episode.v4"
    episode_id: CausalEpisodeId
    life_id: OpaqueId
    root_experience_id: OpaqueId
    sequence_no: int = Field(ge=1)
    predecessor_episode_id: CausalEpisodeId | None = None
    predecessor_episode_sha256: Sha256 | None = None
    episode_kind: Literal["conversation", "ask", "wait", "reflect", "external_action", "observation", "model_expression", "no_op"]
    run_life_binding_sha256: Sha256
    candidate_ids: tuple[OpaqueId, ...] = Field(default=())
    selected_candidate_id: OpaqueId | None = None
    terminal_status: Literal["OPEN", "CLOSED", "ABORTED"]
    terminal_reason_code: str | None = None
    created_at_ms: int = Field(ge=0)
    closed_at_ms: int | None = Field(default=None, ge=0)
    episode_sha256: Sha256
    _unique_candidates = field_validator("candidate_ids")(_sorted_unique)
    @model_validator(mode="after")
    def validate_sequence_and_terminal(self) -> Self:
        predecessors = (self.predecessor_episode_id, self.predecessor_episode_sha256)
        if self.sequence_no == 1 and not all(value is None for value in predecessors):
            raise ValueError("first child has no predecessor and later children require one")
        if self.sequence_no > 1 and not all(value is not None for value in predecessors):
            raise ValueError("first child has no predecessor and later children require one")
        if self.selected_candidate_id is not None and self.selected_candidate_id not in self.candidate_ids:
            raise ValueError("selected child candidate is not a candidate")
        if self.terminal_status == "CLOSED" and self.episode_kind != "observation" and self.selected_candidate_id is None:
            raise ValueError("closed non-observation child requires a selected candidate")
        if self.terminal_status == "OPEN":
            if self.closed_at_ms is not None or self.terminal_reason_code is not None:
                raise ValueError("open child carries terminal evidence")
        elif self.closed_at_ms is None or self.closed_at_ms < self.created_at_ms or self.terminal_reason_code is None:
            raise ValueError("terminal child evidence is incomplete")
        return self
    def computed_episode_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"episode_sha256"}))
    def with_computed_episode_sha256(self) -> Self:
        return self.model_copy(update={"episode_sha256": self.computed_episode_sha256()})


__all__ = [
    "CausalEpisode",
    "CausalEpisodeVNext",
    "CausalEpisodeId",
    "CausalHypothesis",
    "CausalHypothesisId",
]
