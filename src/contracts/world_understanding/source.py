"""Reality-source references and source-kind vocabulary."""
from __future__ import annotations
from typing import Literal, Self
from pydantic import Field, model_validator, field_validator
from ._base import MAX_SAFE_INTEGER, WorldContractModel, normalized_text
from .authority import AuthorityDomain
from ..models import OpaqueId, Sha256

SourceKind = Literal[
    "RUN_CONTEXT",
    "USER_CONVERSATION",
    "SYSTEM_GOVERNANCE",
    "RUNTIME_ENVIRONMENT",
    "AUTHORIZATION",
    "FACT_EXECUTION",
    "TOOL_RESULT",
    "FILESYSTEM",
    "GIT_CODE",
    "WEB_EXTERNAL",
    "DESKTOP_UI",
    "MEMORY",
    "KNOWLEDGE",
    "CONTEXT_CONTINUITY",
    "AUTONOMY",
    "CHAIN_EVENT",
    "EXECUTION_INTEGRITY",
    "METRICS",
    "MIGRATION_AUDIT",
    "MODEL_OUTPUT",
    "CONTEXT_REQUEST",
    "UNCLASSIFIED_SOURCE",
]


class WorldSourceRef(WorldContractModel):
    source_kind: SourceKind
    object_id: OpaqueId
    object_revision: int | None = Field(default=None, ge=1, le=MAX_SAFE_INTEGER, strict=True)
    sha256: Sha256
    locator: str | None = Field(default=None, max_length=4096)
    span_start: int | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER, strict=True)
    span_end: int | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER, strict=True)
    authority_domain: AuthorityDomain | None = None
    authority_ceiling_milli: int = Field(default=0, ge=0, le=1000, strict=True)
    provenance_integrity_milli: int = Field(default=0, ge=0, le=1000, strict=True)

    _validate_locator = field_validator("locator")(
        lambda value: None if value is None else normalized_text(value)
    )

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if (self.span_start is None) != (self.span_end is None):
            raise ValueError("source span must be all-or-none")
        if self.span_start is not None and self.span_start > self.span_end:
            raise ValueError("source span is inverted")
        if self.source_kind in {"CONTEXT_REQUEST", "UNCLASSIFIED_SOURCE"}:
            if self.authority_domain is not None or self.authority_ceiling_milli != 0:
                raise ValueError("control/unclassified source refs cannot claim reality authority")
        return self

    def sort_key(self) -> tuple[str, str, int, str, int, int]:
        return (
            self.source_kind,
            self.object_id,
            self.object_revision or 0,
            self.sha256,
            self.span_start if self.span_start is not None else -1,
            self.span_end if self.span_end is not None else -1,
        )
