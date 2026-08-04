"""Persistent asynchronous planning events for Skill and user confirmation waits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import CONTRACT_SCHEMA_VERSION, canonical_sha256


CoordinationKind = Literal["NEED_SKILL", "NEED_CONFIRMATION"]
CoordinationOutcome = Literal[
    "SKILL_SELECTED",
    "NO_SKILL",
    "SKILL_REJECTED",
    "CONFIRMED",
    "DENIED",
    "EXPIRED",
]


def derive_coordination_event_id(
    *,
    request_id: str,
    run_id: str,
    generation: int,
    kind: CoordinationKind,
    ordinal: int,
    payload_sha256: str,
) -> str:
    return "cev_" + canonical_sha256(
        {
            "domain": "tiangong.gateway.coordination-event.v1",
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation,
            "kind": kind,
            "ordinal": ordinal,
            "payload_sha256": payload_sha256,
        }
    )


class CoordinationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    event_id: str = Field(pattern=r"^cev_[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    kind: CoordinationKind
    consumer: Literal["skill_resolver", "user_confirmation"]
    ordinal: int = Field(ge=1, le=10_000)
    payload_object_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected_consumer = {
            "NEED_SKILL": "skill_resolver",
            "NEED_CONFIRMATION": "user_confirmation",
        }[self.kind]
        if self.consumer != expected_consumer:
            raise ValueError("coordination event kind and consumer disagree")
        if self.expires_at_ms <= self.created_at_ms:
            raise ValueError("coordination event must expire after creation")
        if self.event_id != derive_coordination_event_id(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            kind=self.kind,
            ordinal=self.ordinal,
            payload_sha256=self.payload_sha256,
        ):
            raise ValueError("coordination event identity is not bound to immutable intent")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"event_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.event_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"event_sha256": self.computed_sha256()})


class CoordinationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    event_id: str = Field(pattern=r"^cev_[0-9a-f]{64}$")
    outcome: CoordinationOutcome
    resolver_component_id: Literal["tiangong-total-gateway", "tiangong-desktop"]
    result_object_id: str | None = Field(
        default=None,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    resolved_at_ms: int = Field(ge=0)
    resolution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_result_binding(self) -> Self:
        if (self.result_object_id is None) != (self.result_sha256 is None):
            raise ValueError("coordination result object and digest must be bound together")
        if self.outcome in {"SKILL_SELECTED", "CONFIRMED"} and self.result_object_id is None:
            raise ValueError("positive coordination result requires an immutable result object")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"resolution_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.resolution_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"resolution_sha256": self.computed_sha256()})


@dataclass(frozen=True)
class CoordinationRecord:
    event: CoordinationEvent
    state: Literal["PENDING", "CLAIMED", "RESOLVED", "CANCELLED"]
    attempt_count: int
    claimed_by: str | None
    claim_expires_at_ms: int | None
    resolution: CoordinationResolution | None
    cancelled_at_ms: int | None
    cancel_reason_code: str | None


def create_coordination_event(
    *,
    request_id: str,
    run_id: str,
    generation: int,
    kind: CoordinationKind,
    ordinal: int,
    payload_object_id: str,
    payload_sha256: str,
    created_at_ms: int,
    expires_at_ms: int,
) -> CoordinationEvent:
    consumer: Literal["skill_resolver", "user_confirmation"] = (
        "skill_resolver" if kind == "NEED_SKILL" else "user_confirmation"
    )
    return CoordinationEvent(
        event_id=derive_coordination_event_id(
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            kind=kind,
            ordinal=ordinal,
            payload_sha256=payload_sha256,
        ),
        request_id=request_id,
        run_id=run_id,
        generation=generation,
        kind=kind,
        consumer=consumer,
        ordinal=ordinal,
        payload_object_id=payload_object_id,
        payload_sha256=payload_sha256,
        created_at_ms=created_at_ms,
        expires_at_ms=expires_at_ms,
        event_sha256="0" * 64,
    ).with_computed_sha256()


__all__ = [
    "CoordinationEvent",
    "CoordinationKind",
    "CoordinationOutcome",
    "CoordinationRecord",
    "CoordinationResolution",
    "create_coordination_event",
    "derive_coordination_event_id",
]
