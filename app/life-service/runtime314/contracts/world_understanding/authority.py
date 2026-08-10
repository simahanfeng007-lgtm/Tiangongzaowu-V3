"""Authority-domain vocabulary.

Authority is never a single trust score attached to a source.  A binding is
specific to proposition type, logical scope, and a validity interval.
"""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, model_validator
from ._base import MAX_SAFE_INTEGER, WorldContractModel
from ..models import OpaqueId, Sha256

AuthorityDomain = Literal[
    "IDENTITY_RUN_CONTEXT",
    "USER_HUMAN_INPUT",
    "SYSTEM_GOVERNANCE",
    "RUNTIME_ENVIRONMENT",
    "AUTHORIZATION",
    "EXECUTION_ACTION",
    "FILESYSTEM_ARTIFACT",
    "GIT_CODE",
    "WEB_EXTERNAL",
    "DESKTOP_UI_PROCESS",
    "MEMORY_EXPERIENCE",
    "KNOWLEDGE_DOCUMENT",
    "CONTEXT_CONTINUITY",
    "AUTONOMY_SELF_WILL",
    "TASK_RUN_LIFECYCLE",
    "EXECUTION_INTEGRITY",
    "OBSERVABILITY_METRICS",
    "MIGRATION_AUDIT",
    "INTERNAL_MODEL_OUTPUT",
]

AuthorizationClass = Literal["NONE", "NATIVE_SOURCE_ONLY"]


class AuthorityBinding(WorldContractModel):
    domain: AuthorityDomain
    proposition_type: OpaqueId
    world_scope_hash: Sha256
    valid_from_ms: int = Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)
    valid_until_ms: int | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER, strict=True)
    authority_ceiling_milli: int = Field(ge=0, le=1000, strict=True)
    authorization_class: AuthorizationClass = "NONE"
    may_authorize: bool = False
    empirical_evidence_weight_milli: int = Field(default=0, ge=0, le=1000, strict=True)

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if self.valid_until_ms is not None and self.valid_until_ms < self.valid_from_ms:
            raise ValueError("authority validity interval is inverted")
        if self.empirical_evidence_weight_milli > self.authority_ceiling_milli:
            raise ValueError("empirical weight cannot exceed authority ceiling")
        if self.may_authorize and self.authorization_class != "NATIVE_SOURCE_ONLY":
            raise ValueError("only native authorization-source bindings may authorize")
        return self
