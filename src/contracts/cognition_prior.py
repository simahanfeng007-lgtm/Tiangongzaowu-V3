"""World cognition prior contracts.

Cognitive priors shape interpretation and consolidation policy.  They are not
empirical evidence and therefore cannot, by themselves, establish a world fact.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_sha256
from .models import ContractModel, OpaqueId, SCHEMA_BASE, Sha256


COGNITION_CONTRACT_SCHEMA_VERSION = "tiangong.cognition.contracts.v1"

CognitionPriorId = Annotated[
    str, StringConstraints(pattern=r"^cpr_[0-9a-f]{64}$")
]
CognitionDomain = Literal[
    "software",
    "self",
    "user",
    "environment",
    "organization",
    "external",
]
CognitionPriorKind = Literal[
    "epistemic",
    "continuity",
    "identity",
    "consolidation",
    "revision",
]
CognitionPriorStatus = Literal["active", "retired"]


def _text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value or "\x00" in value:
        raise ValueError("cognition prior text must be NFC and contain no NUL")
    return value


def derive_cognition_prior_id(*, life_id: str, domain: str, prior_key: str) -> str:
    return "cpr_" + canonical_sha256(
        {
            "domain": "tiangong.cognition.prior-id.v1",
            "life_id": life_id,
            "cognition_domain": domain,
            "prior_key": prior_key,
        }
    )


class CognitionPrior(ContractModel):
    """One immutable cognitive prior.

    Priors may influence interpretation, but their empirical evidence weight is
    hard-zero so a prior cannot self-certify an external fact.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:CognitionPrior",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal["tiangong.cognition.contracts.v1"] = (
        COGNITION_CONTRACT_SCHEMA_VERSION
    )
    prior_id: CognitionPriorId
    life_id: OpaqueId
    domain: CognitionDomain
    prior_key: OpaqueId
    prior_kind: CognitionPriorKind
    principle: str = Field(min_length=1, max_length=20_000)

    interpretive_weight_milli: int = Field(ge=0, le=1000, strict=True)
    empirical_evidence_weight_milli: Literal[0] = 0

    projection_authority: Literal["interpretation_only"] = "interpretation_only"
    change_authority: Literal["explicit_system_migration"] = (
        "explicit_system_migration"
    )

    source_policy_ref: OpaqueId
    source_policy_sha256: Sha256

    revision: int = Field(ge=1, le=9_007_199_254_740_991)
    supersedes_prior_sha256: Sha256 | None = None
    status: CognitionPriorStatus
    created_at_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    prior_sha256: Sha256

    _validate_principle = field_validator("principle")(_text)

    @model_validator(mode="after")
    def validate_identity_and_revision(self) -> Self:
        if self.prior_id != derive_cognition_prior_id(
            life_id=self.life_id,
            domain=self.domain,
            prior_key=self.prior_key,
        ):
            raise ValueError("cognition prior ID does not match its stable identity")
        if (self.revision == 1) != (self.supersedes_prior_sha256 is None):
            raise ValueError("cognition prior revision chain is invalid")
        return self

    def computed_prior_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"prior_sha256"})
        )

    def has_valid_prior_sha256(self) -> bool:
        return self.prior_sha256 == self.computed_prior_sha256()

    def with_computed_prior_sha256(self) -> Self:
        return self.model_copy(update={"prior_sha256": self.computed_prior_sha256()})


__all__ = [
    "COGNITION_CONTRACT_SCHEMA_VERSION",
    "CognitionDomain",
    "CognitionPrior",
    "CognitionPriorId",
    "CognitionPriorKind",
    "CognitionPriorStatus",
    "derive_cognition_prior_id",
]
