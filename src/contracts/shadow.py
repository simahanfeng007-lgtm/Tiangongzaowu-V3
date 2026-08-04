"""Observe-only migration contracts that cannot authorize business effects."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_sha256
from .models import ContractModel, InboundEnvelope, OpaqueId, SCHEMA_BASE, LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, Sha256


ShadowSide = Literal["candidate", "legacy"]
ShadowMismatchField = Literal[
    "attachment_count",
    "attachment_sha256",
    "classification",
    "should_forward",
]


def _envelope_sha256(envelope: InboundEnvelope) -> str:
    return canonical_sha256(envelope.model_dump(mode="json"))


def derive_shadow_id(envelope: InboundEnvelope, source_ingress_sha256: str) -> str:
    return "shd_" + canonical_sha256(
        {
            "domain": "tiangong.migration.shadow-ingress.v1",
            "idempotency_key": envelope.idempotency_key,
            "inbound_id": envelope.inbound_id,
            "envelope_sha256": _envelope_sha256(envelope),
            "source_ingress_sha256": source_ingress_sha256,
        }
    )


class ShadowIngressCopy(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ShadowIngressCopy",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    shadow_schema: Literal["tiangong.migration.shadow.v1"] = "tiangong.migration.shadow.v1"
    shadow_id: str = Field(pattern=r"^shd_[0-9a-f]{64}$")
    mode: Literal["OBSERVE_ONLY"] = "OBSERVE_ONLY"
    envelope: InboundEnvelope
    envelope_sha256: Sha256
    source_ingress_sha256: Sha256
    source_ack_permit_sha256: Sha256
    copied_at_ms: int = Field(ge=0)
    request_creation_permitted: Literal[False] = False
    effects_permitted: Literal[False] = False
    copy_sha256: Sha256

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if self.copied_at_ms < self.envelope.received_at_ms:
            raise ValueError("shadow copy predates the inbound envelope")
        if self.envelope_sha256 != _envelope_sha256(self.envelope):
            raise ValueError("shadow copy envelope digest is invalid")
        if self.shadow_id != derive_shadow_id(self.envelope, self.source_ingress_sha256):
            raise ValueError("shadow copy identity is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"copy_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.copy_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"copy_sha256": self.computed_sha256()})


def build_shadow_ingress_copy(
    envelope: InboundEnvelope,
    *,
    source_ingress_sha256: str,
    source_ack_permit_sha256: str,
    copied_at_ms: int,
) -> ShadowIngressCopy:
    return ShadowIngressCopy(
        shadow_id=derive_shadow_id(envelope, source_ingress_sha256),
        envelope=envelope,
        envelope_sha256=_envelope_sha256(envelope),
        source_ingress_sha256=source_ingress_sha256,
        source_ack_permit_sha256=source_ack_permit_sha256,
        copied_at_ms=copied_at_ms,
        copy_sha256="0" * 64,
    ).with_computed_sha256()


def _observation_id(
    *,
    shadow_id: str,
    side: ShadowSide,
    source_component_id: str,
    source_instance_id: str,
    source_decision_sha256: str,
) -> str:
    return "shobs_" + canonical_sha256(
        {
            "domain": "tiangong.migration.shadow-decision.v1",
            "shadow_id": shadow_id,
            "side": side,
            "source_component_id": source_component_id,
            "source_instance_id": source_instance_id,
            "source_decision_sha256": source_decision_sha256,
        }
    )


class ShadowDecisionObservation(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ShadowDecisionObservation",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    observation_id: str = Field(pattern=r"^shobs_[0-9a-f]{64}$")
    shadow_id: str = Field(pattern=r"^shd_[0-9a-f]{64}$")
    side: ShadowSide
    source_component_id: OpaqueId
    source_instance_id: OpaqueId
    source_decision_sha256: Sha256
    envelope_sha256: Sha256
    classification: OpaqueId
    should_forward: bool
    attachment_count: int = Field(ge=0, le=128)
    attachment_sha256: tuple[Sha256, ...] = Field(default=(), max_length=128)
    observed_at_ms: int = Field(ge=0)
    model_generated: Literal[False] = False
    request_creation_permitted: Literal[False] = False
    effects_permitted: Literal[False] = False
    observation_sha256: Sha256

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if self.attachment_count != len(self.attachment_sha256):
            raise ValueError("shadow decision attachment count is invalid")
        if self.observation_id != _observation_id(
            shadow_id=self.shadow_id,
            side=self.side,
            source_component_id=self.source_component_id,
            source_instance_id=self.source_instance_id,
            source_decision_sha256=self.source_decision_sha256,
        ):
            raise ValueError("shadow decision observation identity is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"observation_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.observation_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(
            update={"observation_sha256": self.computed_sha256()}
        )


def build_shadow_decision_observation(
    copy: ShadowIngressCopy,
    *,
    side: ShadowSide,
    source_component_id: str,
    source_instance_id: str,
    source_decision_sha256: str,
    classification: str,
    should_forward: bool,
    observed_at_ms: int,
) -> ShadowDecisionObservation:
    if not copy.has_valid_sha256():
        raise ValueError("shadow decision requires a valid ingress copy")
    attachment_sha256 = tuple(item.sha256 for item in copy.envelope.attachments)
    return ShadowDecisionObservation(
        observation_id=_observation_id(
            shadow_id=copy.shadow_id,
            side=side,
            source_component_id=source_component_id,
            source_instance_id=source_instance_id,
            source_decision_sha256=source_decision_sha256,
        ),
        shadow_id=copy.shadow_id,
        side=side,
        source_component_id=source_component_id,
        source_instance_id=source_instance_id,
        source_decision_sha256=source_decision_sha256,
        envelope_sha256=copy.envelope_sha256,
        classification=classification,
        should_forward=should_forward,
        attachment_count=len(attachment_sha256),
        attachment_sha256=attachment_sha256,
        observed_at_ms=observed_at_ms,
        observation_sha256="0" * 64,
    ).with_computed_sha256()


class ShadowObservationBatch(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ShadowObservationBatch",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    ingress_copy: ShadowIngressCopy
    observations: tuple[ShadowDecisionObservation, ...] = Field(min_length=1, max_length=2)
    batch_sha256: Sha256

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if not self.ingress_copy.has_valid_sha256():
            raise ValueError("shadow observation batch copy digest is invalid")
        sides = tuple(item.side for item in self.observations)
        if sides != tuple(sorted(set(sides))):
            raise ValueError("shadow observation batch sides must be sorted and unique")
        for observation in self.observations:
            if (
                not observation.has_valid_sha256()
                or observation.shadow_id != self.ingress_copy.shadow_id
                or observation.envelope_sha256 != self.ingress_copy.envelope_sha256
                or observation.observed_at_ms < self.ingress_copy.envelope.received_at_ms
            ):
                raise ValueError("shadow observation is not bound to its ingress copy")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"batch_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.batch_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"batch_sha256": self.computed_sha256()})


class ShadowComparison(ContractModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:ShadowComparison",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    comparison_id: str = Field(pattern=r"^shcmp_[0-9a-f]{64}$")
    shadow_id: str = Field(pattern=r"^shd_[0-9a-f]{64}$")
    status: Literal["MATCH", "MISMATCH", "WAITING_FOR_CANDIDATE", "WAITING_FOR_LEGACY"]
    legacy_observation_id: str | None = Field(
        default=None, pattern=r"^shobs_[0-9a-f]{64}$"
    )
    candidate_observation_id: str | None = Field(
        default=None, pattern=r"^shobs_[0-9a-f]{64}$"
    )
    mismatch_fields: tuple[ShadowMismatchField, ...] = Field(default=(), max_length=4)
    compared_at_ms: int = Field(ge=0)
    mode: Literal["OBSERVE_ONLY"] = "OBSERVE_ONLY"
    request_creation_permitted: Literal[False] = False
    effects_permitted: Literal[False] = False
    comparison_sha256: Sha256

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.mismatch_fields != tuple(sorted(set(self.mismatch_fields))):
            raise ValueError("shadow mismatch fields must be sorted and unique")
        both = self.legacy_observation_id is not None and self.candidate_observation_id is not None
        if self.status in {"MATCH", "MISMATCH"} and not both:
            raise ValueError("completed shadow comparison requires both observations")
        if self.status == "MATCH" and self.mismatch_fields:
            raise ValueError("matching shadow decisions cannot have mismatch fields")
        if self.status == "MISMATCH" and not self.mismatch_fields:
            raise ValueError("mismatching shadow decisions require mismatch fields")
        if self.status == "WAITING_FOR_LEGACY" and (
            self.legacy_observation_id is not None or self.candidate_observation_id is None
        ):
            raise ValueError("shadow legacy wait state is invalid")
        if self.status == "WAITING_FOR_CANDIDATE" and (
            self.candidate_observation_id is not None or self.legacy_observation_id is None
        ):
            raise ValueError("shadow candidate wait state is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"comparison_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.comparison_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"comparison_sha256": self.computed_sha256()})


def compare_shadow_observations(
    copy: ShadowIngressCopy,
    legacy: ShadowDecisionObservation | None,
    candidate: ShadowDecisionObservation | None,
    *,
    compared_at_ms: int,
) -> ShadowComparison:
    if not copy.has_valid_sha256() or compared_at_ms < copy.copied_at_ms:
        raise ValueError("shadow comparison input is invalid")
    for expected_side, observation in (("legacy", legacy), ("candidate", candidate)):
        if observation is None:
            continue
        if (
            observation.side != expected_side
            or observation.shadow_id != copy.shadow_id
            or observation.envelope_sha256 != copy.envelope_sha256
            or not observation.has_valid_sha256()
        ):
            raise ValueError("shadow comparison observation is invalid")
    if legacy is None and candidate is None:
        raise ValueError("shadow comparison requires at least one observation")
    mismatches: tuple[ShadowMismatchField, ...] = ()
    if legacy is None:
        status = "WAITING_FOR_LEGACY"
    elif candidate is None:
        status = "WAITING_FOR_CANDIDATE"
    else:
        values: list[ShadowMismatchField] = []
        for field in (
            "attachment_count",
            "attachment_sha256",
            "classification",
            "should_forward",
        ):
            if getattr(legacy, field) != getattr(candidate, field):
                values.append(field)  # type: ignore[arg-type]
        mismatches = tuple(values)
        status = "MATCH" if not mismatches else "MISMATCH"
    return ShadowComparison(
        comparison_id="shcmp_" + canonical_sha256(
            {"domain": "tiangong.migration.shadow-comparison.v1", "shadow_id": copy.shadow_id}
        ),
        shadow_id=copy.shadow_id,
        status=status,
        legacy_observation_id=None if legacy is None else legacy.observation_id,
        candidate_observation_id=None if candidate is None else candidate.observation_id,
        mismatch_fields=mismatches,
        compared_at_ms=compared_at_ms,
        comparison_sha256="0" * 64,
    ).with_computed_sha256()


__all__ = [
    "ShadowComparison",
    "ShadowDecisionObservation",
    "ShadowIngressCopy",
    "ShadowObservationBatch",
    "build_shadow_decision_observation",
    "build_shadow_ingress_copy",
    "compare_shadow_observations",
    "derive_shadow_id",
]
