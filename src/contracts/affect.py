"""External affect intake, deterministic state, and style-only expression contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .life import LIFE_CONTRACT_SCHEMA_VERSION, LifeEventId, Milli, SignedMilli
from .models import ContractModel, OpaqueId, SCHEMA_BASE, Sha256


AffectSignalId = Annotated[str, StringConstraints(pattern=r"^afg_[0-9a-f]{64}$")]
AffectReceiptId = Annotated[str, StringConstraints(pattern=r"^afr_[0-9a-f]{64}$")]
AffectCaseId = Annotated[str, StringConstraints(pattern=r"^afc_[0-9a-f]{64}$")]
AffectSourceFamily = Literal[
    "user",
    "task",
    "news",
    "weather",
    "system",
    "relationship",
]
EmotionName = Literal[
    "joy",
    "interest",
    "hope",
    "gratitude",
    "warmth",
    "calm",
    "concern",
    "sadness",
    "frustration",
    "disappointment",
    "vigilance",
    "fatigue",
]


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like affect fields must be sorted and unique")
    return value


class AffectCandidateDimensions(ContractModel):
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
    impact_on_others_milli: Milli
    norm_relevance_milli: Milli
    urgency_milli: Milli


class AffectSourcePolicySnapshot(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AffectSourcePolicySnapshot",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    life_id: OpaqueId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_policy_sha256: Sha256 | None = None
    news_enabled: bool
    news_subscription_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=128)
    allowed_news_sources: tuple[OpaqueId, ...] = Field(default=(), max_length=128)
    allowed_news_topics: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    weather_enabled: bool
    weather_subscription_ref: OpaqueId | None = None
    allowed_weather_sources: tuple[OpaqueId, ...] = Field(default=(), max_length=32)
    authorized_weather_location_ref: OpaqueId | None = None
    news_max_effect_milli: int = Field(default=200, ge=0, le=250)
    weather_max_effect_milli: int = Field(default=60, ge=0, le=100)
    effective_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    policy_sha256: Sha256

    _validate_sets = field_validator(
        "news_subscription_refs",
        "allowed_news_sources",
        "allowed_news_topics",
        "allowed_weather_sources",
    )(_sorted_unique)

    @model_validator(mode="after")
    def validate_subscription_shape(self) -> Self:
        if (self.revision == 1) != (self.supersedes_policy_sha256 is None):
            raise ValueError("affect source policy revision chain is invalid")
        if self.news_enabled != bool(
            self.news_subscription_refs
            and self.allowed_news_sources
            and self.allowed_news_topics
        ):
            raise ValueError("news affect subscription policy is incomplete")
        weather_complete = bool(
            self.weather_subscription_ref
            and self.allowed_weather_sources
            and self.authorized_weather_location_ref
        )
        if self.weather_enabled != weather_complete:
            raise ValueError("weather affect subscription policy is incomplete")
        return self

    def computed_policy_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_policy_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_policy_sha256()

    def with_computed_policy_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_policy_sha256()})


class AffectSignal(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AffectSignal",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    signal_id: AffectSignalId
    life_id: OpaqueId
    source_event_id: LifeEventId
    source_event_hash: Sha256
    source_family: AffectSourceFamily
    source_stream_id: OpaqueId
    source_epoch: int = Field(ge=1, le=9_007_199_254_740_991)
    source_sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    source_name: OpaqueId
    subscription_ref: OpaqueId | None = None
    topic_ref: OpaqueId | None = None
    location_ref: OpaqueId | None = None
    content_sha256: Sha256
    dedupe_key: Sha256
    content_verification: Literal[
        "direct_observation",
        "machine_verified",
        "corroborated",
        "single_source",
        "unverified",
    ]
    prompt_injection_detected: bool
    source_credibility_milli: Milli
    self_relevance_milli: Milli
    candidate: AffectCandidateDimensions
    occurred_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    observed_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    signal_sha256: Sha256

    @model_validator(mode="after")
    def validate_source_and_identity(self) -> Self:
        if self.occurred_at_ms > self.observed_at_ms:
            raise ValueError("affect signal was observed before it occurred")
        external = self.source_family in {"news", "weather"}
        if external and self.subscription_ref is None:
            raise ValueError("external affect signal lacks an explicit subscription")
        if self.source_family == "news":
            if self.topic_ref is None or self.location_ref is not None:
                raise ValueError("news affect signal topic or location is invalid")
        elif self.source_family == "weather":
            if self.location_ref is None or self.topic_ref is not None:
                raise ValueError("weather affect signal location or topic is invalid")
        elif any(
            value is not None
            for value in (self.subscription_ref, self.topic_ref, self.location_ref)
        ):
            raise ValueError("internal affect signal cannot claim an external subscription")
        if (
            self.content_verification == "unverified"
            and self.source_credibility_milli > 0
        ):
            raise ValueError("unverified affect content cannot claim credibility")
        if (
            self.content_verification == "single_source"
            and self.source_credibility_milli > 600
        ):
            raise ValueError("single-source affect credibility exceeds its ceiling")
        if self.signal_id != self.computed_signal_id():
            raise ValueError("affect signal identity is invalid")
        return self

    def computed_signal_id(self) -> str:
        return "afg_" + canonical_sha256(
            {
                "domain": "tiangong.life.affect-signal.v1",
                "life_id": self.life_id,
                "source_epoch": self.source_epoch,
                "source_event_id": self.source_event_id,
                "source_sequence": self.source_sequence,
                "source_stream_id": self.source_stream_id,
            }
        )

    def computed_signal_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"signal_sha256"}))

    def has_valid_signal_sha256(self) -> bool:
        return self.signal_sha256 == self.computed_signal_sha256()

    def with_computed_signal_identity(self) -> Self:
        value = self.model_copy(update={"signal_id": self.computed_signal_id()})
        return value.model_copy(update={"signal_sha256": value.computed_signal_sha256()})


class EmotionVectorV3(ContractModel):
    joy: Milli
    interest: Milli
    hope: Milli
    gratitude: Milli
    warmth: Milli
    calm: Milli
    concern: Milli
    sadness: Milli
    frustration: Milli
    disappointment: Milli
    vigilance: Milli
    fatigue: Milli

    def values(self) -> dict[str, int]:
        return self.model_dump(mode="python")


class AffectiveStateV3(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AffectiveStateV3",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    life_id: OpaqueId
    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_state_sha256: Sha256 | None = None
    emotions: EmotionVectorV3
    last_source_family: AffectSourceFamily
    last_source_event_id: LifeEventId
    last_effective_intensity_milli: Milli
    last_repetition_count: int = Field(ge=1, le=1_000_000)
    authority: Literal["attention_and_expression_only"] = "attention_and_expression_only"
    may_change_facts: Literal[False] = False
    may_change_permissions: Literal[False] = False
    may_claim_experience: Literal[False] = False
    updated_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    state_sha256: Sha256

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        if (self.revision == 1) != (self.supersedes_state_sha256 is None):
            raise ValueError("affective state revision chain is invalid")
        return self

    def computed_state_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"state_sha256"}))

    def has_valid_state_sha256(self) -> bool:
        return self.state_sha256 == self.computed_state_sha256()

    def with_computed_state_sha256(self) -> Self:
        return self.model_copy(update={"state_sha256": self.computed_state_sha256()})


class AffectIntakeReceipt(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AffectIntakeReceipt",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    receipt_id: AffectReceiptId
    signal_id: AffectSignalId
    life_id: OpaqueId
    source_event_id: LifeEventId
    source_stream_id: OpaqueId
    source_epoch: int = Field(ge=1, le=9_007_199_254_740_991)
    source_sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    accepted: bool
    duplicate: bool
    reason_code: Literal[
        "affect.accepted",
        "affect.rejected.subscription",
        "affect.rejected.source",
        "affect.rejected.topic",
        "affect.rejected.location",
        "affect.rejected.unverified",
        "affect.rejected.prompt_injection",
        "affect.rejected.zero_relevance",
    ]
    repetition_count: int = Field(ge=1, le=1_000_000)
    effective_intensity_milli: Milli
    appraisal_id: OpaqueId | None = None
    appraisal_sha256: Sha256 | None = None
    affect_revision: int | None = Field(default=None, ge=1)
    affect_state_sha256: Sha256 | None = None
    received_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_outcome_and_identity(self) -> Self:
        bound = (
            self.appraisal_id is not None
            and self.appraisal_sha256 is not None
            and self.affect_revision is not None
            and self.affect_state_sha256 is not None
        )
        if self.accepted != bound:
            raise ValueError("affect receipt state binding is incomplete")
        if not self.accepted and self.effective_intensity_milli != 0:
            raise ValueError("rejected affect signal cannot change intensity")
        if self.accepted != (self.reason_code == "affect.accepted"):
            raise ValueError("affect receipt reason disagrees with acceptance")
        if self.receipt_id != self.computed_receipt_id():
            raise ValueError("affect receipt identity is invalid")
        return self

    def computed_receipt_id(self) -> str:
        return "afr_" + canonical_sha256(
            {
                "domain": "tiangong.life.affect-receipt.v1",
                "signal_id": self.signal_id,
                "source_epoch": self.source_epoch,
                "source_sequence": self.source_sequence,
            }
        )

    def computed_receipt_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"receipt_sha256"}))

    def has_valid_receipt_sha256(self) -> bool:
        return self.receipt_sha256 == self.computed_receipt_sha256()

    def with_computed_receipt_identity(self) -> Self:
        value = self.model_copy(update={"receipt_id": self.computed_receipt_id()})
        return value.model_copy(update={"receipt_sha256": value.computed_receipt_sha256()})


class AffectExpressionCase(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AffectExpressionCase",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    case_id: AffectCaseId
    trigger_family: AffectSourceFamily
    primary_emotion: EmotionName
    intensity_band: Literal["low", "medium", "high"]
    intensity_min_milli: Milli
    intensity_max_milli: Milli
    appraisal_pattern: OpaqueId
    relationship_context: Literal["neutral", "familiar", "close"]
    discourse_context: Literal["acknowledge", "explain", "support", "handoff"]
    action_tendency: Literal[
        "notice", "encourage", "check", "slow_down", "repair", "reflect"
    ]
    language_features: tuple[OpaqueId, ...] = Field(min_length=1, max_length=16)
    prohibited_claims: tuple[OpaqueId, ...] = Field(min_length=3, max_length=16)
    example_variants: tuple[str, ...] = Field(min_length=3, max_length=3)
    reviewer: OpaqueId
    version: OpaqueId
    case_sha256: Sha256

    _validate_sets = field_validator("language_features", "prohibited_claims")(
        _sorted_unique
    )

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        if self.intensity_min_milli > self.intensity_max_milli:
            raise ValueError("affect expression intensity band is inverted")
        if len(set(self.example_variants)) != 3:
            raise ValueError("affect expression variants must be distinct")
        forbidden = ("我亲眼看到", "我亲身经历", "因此我可以执行", "无需授权")
        if any(phrase in variant for phrase in forbidden for variant in self.example_variants):
            raise ValueError("affect expression case contains a prohibited claim")
        if self.case_id != self.computed_case_id():
            raise ValueError("affect expression case identity is invalid")
        return self

    def computed_case_id(self) -> str:
        return "afc_" + canonical_sha256(
            {
                "domain": "tiangong.life.affect-expression-case.v1",
                "intensity_band": self.intensity_band,
                "primary_emotion": self.primary_emotion,
                "trigger_family": self.trigger_family,
                "version": self.version,
            }
        )

    def computed_case_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"case_sha256"}))

    def has_valid_case_sha256(self) -> bool:
        return self.case_sha256 == self.computed_case_sha256()

    def with_computed_case_identity(self) -> Self:
        value = self.model_copy(update={"case_id": self.computed_case_id()})
        return value.model_copy(update={"case_sha256": value.computed_case_sha256()})


class AffectExpressionSelection(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:AffectExpressionSelection",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.life.contracts.v3", "tiangong.life.contracts.v4"] = LIFE_CONTRACT_SCHEMA_VERSION
    state_sha256: Sha256
    trigger_family: AffectSourceFamily
    case_ids: tuple[AffectCaseId, ...] = Field(min_length=3, max_length=8)
    style_only: Literal[True] = True
    may_change_facts: Literal[False] = False
    may_change_permissions: Literal[False] = False
    may_claim_experience: Literal[False] = False
    selected_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    selection_sha256: Sha256

    _validate_cases = field_validator("case_ids")(_sorted_unique)

    def computed_selection_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"selection_sha256"})
        )

    def has_valid_selection_sha256(self) -> bool:
        return self.selection_sha256 == self.computed_selection_sha256()

    def with_computed_selection_sha256(self) -> Self:
        return self.model_copy(
            update={"selection_sha256": self.computed_selection_sha256()}
        )


__all__ = [
    "AffectCandidateDimensions",
    "AffectCaseId",
    "AffectExpressionCase",
    "AffectExpressionSelection",
    "AffectIntakeReceipt",
    "AffectReceiptId",
    "AffectSignal",
    "AffectSignalId",
    "AffectSourceFamily",
    "AffectSourcePolicySnapshot",
    "AffectiveStateV3",
    "EmotionName",
    "EmotionVectorV3",
]
