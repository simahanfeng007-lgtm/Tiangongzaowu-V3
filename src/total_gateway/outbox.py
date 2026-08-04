"""Immutable transactional-outbox intents for gateway-owned side effects."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import CONTRACT_SCHEMA_VERSION, canonical_sha256


def derive_outbox_id(effect_id: str, destination_component_id: str, payload_sha256: str) -> str:
    return "obx_" + canonical_sha256(
        {
            "domain": "tiangong.gateway.outbox.v1",
            "effect_id": effect_id,
            "destination_component_id": destination_component_id,
            "payload_sha256": payload_sha256,
        }
    )


class OutboxIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    outbox_id: str = Field(pattern=r"^obx_[0-9a-f]{64}$")
    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    destination_component_id: Literal[
        "tiangong-backend",
        "tiangong-life-service",
        "tiangong-communication-service",
    ]
    intent_kind: Literal[
        "EXECUTION",
        "LIFE_READ",
        "LIFE_EVENT",
        "DELIVERY",
        "CONTROL",
    ]
    payload_object_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.outbox_id != derive_outbox_id(
            self.effect_id,
            self.destination_component_id,
            self.payload_sha256,
        ):
            raise ValueError("outbox identity is not bound to its effect and immutable payload")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"intent_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.intent_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"intent_sha256": self.computed_sha256()})


__all__ = ["OutboxIntent", "derive_outbox_id"]
