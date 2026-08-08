"""Revision-decision contracts for the world cognition system.

Every promotion, refresh, challenge, confirmation, supersession, protection, or
retirement is recorded as an immutable deterministic decision. LLM output may
propose a candidate statement but is never a revision authority.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .models import ContractModel, OpaqueId, ReasonCode, SCHEMA_BASE, Sha256
from .cognition_prior import COGNITION_CONTRACT_SCHEMA_VERSION
from .cognition_evidence import CognitionEvidenceId
from .cognition_statement import CognitionId, CognitionStabilityLevel, CognitionStatus

CognitionRevisionId = Annotated[str, StringConstraints(pattern=r"^crv_[0-9a-f]{64}$")]
CognitionTransition = Literal[
    "GENESIS",
    "REFRESH",
    "REPLACE_CANDIDATE",
    "PROMOTE",
    "CHALLENGE",
    "BEGIN_REVERIFY",
    "CONFIRM",
    "SUPERSEDE",
    "PROTECT",
    "RETIRE",
]
CognitionDecisionAuthority = Literal["deterministic_policy", "explicit_system_authority", "migration"]


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like cognition revision fields must be sorted and unique")
    return value


def derive_cognition_revision_id(*, cognition_id: str, sequence: int, from_statement_sha256: str | None, to_statement_sha256: str) -> str:
    return "crv_" + canonical_sha256({"domain": "tiangong.cognition.revision-id.v1", "cognition_id": cognition_id, "sequence": sequence, "from_statement_sha256": from_statement_sha256, "to_statement_sha256": to_statement_sha256})


class CognitionRevision(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, json_schema_extra={"$id": f"{SCHEMA_BASE}:CognitionRevision", "$schema": "https://json-schema.org/draft/2020-12/schema"})
    schema_version: Literal["tiangong.cognition.contracts.v1"] = COGNITION_CONTRACT_SCHEMA_VERSION
    cognition_revision_id: CognitionRevisionId
    life_id: OpaqueId
    cognition_id: CognitionId
    sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    previous_revision_sha256: Sha256 | None = None
    from_statement_sha256: Sha256 | None = None
    to_statement_sha256: Sha256
    from_status: CognitionStatus | None = None
    to_status: CognitionStatus
    from_stability_level: CognitionStabilityLevel | None = None
    to_stability_level: CognitionStabilityLevel
    transition: CognitionTransition
    trigger_evidence_ids: tuple[CognitionEvidenceId, ...] = Field(default=(), max_length=4096)
    support_independence_groups: tuple[Sha256, ...] = Field(default=(), max_length=4096)
    counter_independence_groups: tuple[Sha256, ...] = Field(default=(), max_length=4096)
    support_milli: int = Field(ge=0, le=1000, strict=True)
    counter_milli: int = Field(ge=0, le=1000, strict=True)
    correlation_discount_milli: int = Field(ge=0, le=1000, strict=True)
    staleness_penalty_milli: int = Field(ge=0, le=1000, strict=True)
    decision_authority: CognitionDecisionAuthority
    policy_ref: OpaqueId
    policy_sha256: Sha256
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=128)
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    revision_sha256: Sha256

    _validate_sets = field_validator("trigger_evidence_ids", "support_independence_groups", "counter_independence_groups", "reason_codes")(_sorted_unique)

    @model_validator(mode="after")
    def validate_chain_and_transition(self) -> Self:
        genesis = self.sequence == 1
        if genesis:
            if self.previous_revision_sha256 is not None or self.from_statement_sha256 is not None or self.from_status is not None or self.from_stability_level is not None:
                raise ValueError("genesis cognition revision cannot carry predecessor state")
            if self.transition != "GENESIS":
                raise ValueError("first cognition revision must be GENESIS")
            if self.decision_authority == "deterministic_policy" and (self.to_status, self.to_stability_level) != ("CANDIDATE", "C0"):
                raise ValueError("ordinary deterministic genesis must start as CANDIDATE/C0")
        else:
            if self.previous_revision_sha256 is None or self.from_statement_sha256 is None or self.from_status is None or self.from_stability_level is None:
                raise ValueError("non-genesis cognition revision requires complete predecessor state")
            if self.transition == "GENESIS":
                raise ValueError("GENESIS transition is only valid at sequence one")

        if set(self.support_independence_groups) & set(self.counter_independence_groups):
            raise ValueError("one independence group cannot support and contradict the same decision")

        if self.transition == "REFRESH":
            if self.from_status not in {"CANDIDATE", "PROVISIONAL", "STABLE", "CORE"}:
                raise ValueError("only active cognition may be refreshed")
            if self.to_status != self.from_status or self.to_stability_level != self.from_stability_level:
                raise ValueError("refresh must preserve cognition status and stability level")

        if self.transition == "REPLACE_CANDIDATE":
            if (self.from_status, self.from_stability_level, self.to_status, self.to_stability_level) != ("CANDIDATE", "C0", "CANDIDATE", "C0"):
                raise ValueError("candidate replacement is only valid inside CANDIDATE/C0")

        if self.transition == "PROMOTE":
            allowed = {
                ("CANDIDATE", "C0", "PROVISIONAL", "C1"),
                ("PROVISIONAL", "C1", "STABLE", "C2"),
                ("STABLE", "C2", "CORE", "C3"),
            }
            if (self.from_status, self.from_stability_level, self.to_status, self.to_stability_level) not in allowed:
                raise ValueError("invalid cognition promotion transition")

        if self.transition == "PROTECT":
            if (self.from_status, self.from_stability_level, self.to_status, self.to_stability_level) != ("CORE", "C3", "CORE", "C4"):
                raise ValueError("PROTECT must transition CORE/C3 to CORE/C4")
            if self.decision_authority not in {"explicit_system_authority", "migration"}:
                raise ValueError("protected C4 cognition requires explicit authority")

        if self.transition == "CHALLENGE":
            if self.from_status not in {"PROVISIONAL", "STABLE", "CORE", "REVERIFYING"} or self.to_status != "CHALLENGED":
                raise ValueError("invalid cognition challenge transition")
            if not self.trigger_evidence_ids or not self.counter_independence_groups:
                raise ValueError("cognition challenge requires triggering counterevidence")
            if self.from_stability_level != self.to_stability_level:
                raise ValueError("challenge must preserve the prior cognition stability level")

        if self.transition == "BEGIN_REVERIFY":
            if self.from_status != "CHALLENGED" or self.to_status != "REVERIFYING" or self.from_stability_level != self.to_stability_level:
                raise ValueError("invalid cognition reverification transition")

        if self.transition == "CONFIRM":
            allowed = {("C1", "PROVISIONAL"), ("C2", "STABLE"), ("C3", "CORE"), ("C4", "CORE")}
            if self.from_status != "REVERIFYING" or (self.to_stability_level, self.to_status) not in allowed or self.from_stability_level != self.to_stability_level:
                raise ValueError("invalid cognition confirmation transition")

        if self.transition == "SUPERSEDE":
            if self.to_status not in {"PROVISIONAL", "STABLE", "CORE"}:
                raise ValueError("superseded cognition must resolve to an active supported state")
            if self.decision_authority == "deterministic_policy":
                if self.from_status != "REVERIFYING":
                    raise ValueError("deterministic supersession requires prior reverification")
                expected = {"C1": ("PROVISIONAL", "C1"), "C2": ("STABLE", "C2"), "C3": ("CORE", "C3"), "C4": ("CORE", "C4")}.get(self.from_stability_level)
                if expected != (self.to_status, self.to_stability_level):
                    raise ValueError("deterministic supersession must preserve the established stability class")

        if self.transition == "RETIRE":
            if self.to_status != "RETIRED" or self.from_status == "RETIRED":
                raise ValueError("invalid cognition retirement transition")

        if self.to_stability_level == "C4" and self.from_stability_level != "C4" and self.decision_authority not in {"explicit_system_authority", "migration"}:
            raise ValueError("entering protected C4 requires explicit system authority or migration")
        if self.from_stability_level == "C4" and self.transition in {"SUPERSEDE", "RETIRE"} and self.decision_authority not in {"explicit_system_authority", "migration"}:
            raise ValueError("protected C4 cognition requires explicit authority to supersede or retire")

        if self.to_status in {"STABLE", "CORE"}:
            required_groups = 2 if self.to_status == "STABLE" else 3
            if len(self.support_independence_groups) < required_groups:
                raise ValueError("stable cognition decision lacks independent support quorum")

        expected_id = derive_cognition_revision_id(cognition_id=self.cognition_id, sequence=self.sequence, from_statement_sha256=self.from_statement_sha256, to_statement_sha256=self.to_statement_sha256)
        if self.cognition_revision_id != expected_id:
            raise ValueError("cognition revision ID does not match its transition identity")
        return self

    def computed_revision_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"revision_sha256"}))

    def has_valid_revision_sha256(self) -> bool:
        return self.revision_sha256 == self.computed_revision_sha256()

    def with_computed_revision_sha256(self) -> Self:
        return self.model_copy(update={"revision_sha256": self.computed_revision_sha256()})


__all__ = ["CognitionDecisionAuthority", "CognitionRevision", "CognitionRevisionId", "CognitionTransition", "derive_cognition_revision_id"]
