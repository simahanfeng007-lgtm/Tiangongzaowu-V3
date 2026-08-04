"""Stable gateway identities and generation-fencing contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .canonical import canonical_sha256
from .models import (
    ActionId,
    ArtifactId,
    ArtifactRevisionId,
    ContractModel,
    DeliveryId,
    EffectId,
    GenerationFenceId,
    OpaqueId,
    ReasonCode,
    RequestId,
    RunId,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
)


class EffectIdentityVNext(ContractModel):
    """vNext semantic effect identity: execution and model_inference kinds."""

    schema_id: Literal["EffectIdentityVNext"] = "EffectIdentityVNext"
    schema_version: Literal["tiangong.effect_identity.v2"] = "tiangong.effect_identity.v2"
    effect_id: EffectId
    origin_request_id: RequestId
    origin_run_id: RunId
    origin_run_sequence: int = Field(ge=0)
    origin_generation: int = Field(ge=0)
    effect_kind: Literal["execution", "artifact", "delivery", "control", "model_inference"]
    parent_effect_id: EffectId | None = None
    semantic_step_role: str | None = None
    semantic_target_key: str | None = None
    semantic_occurrence_index: int | None = Field(default=None, ge=1)
    stable_step_id: str | None = None
    occurrence_key: str | None = None
    action_id: ActionId | None = None
    action_version: OpaqueId | None = None
    canonical_invocation_sha256: Sha256 | None = None
    component_manifest_sha256: Sha256 | None = None
    pinned_skill_artifact_sha256s: tuple[Sha256, ...] = Field(default=(), max_length=64)
    root_experience_id: OpaqueId | None = None
    response_episode_id: OpaqueId | None = None
    request_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_kind_shape(self) -> Self:
        execution_fields = (
            "parent_effect_id", "semantic_step_role", "semantic_target_key",
            "semantic_occurrence_index", "stable_step_id", "occurrence_key",
            "action_id", "action_version", "canonical_invocation_sha256",
            "component_manifest_sha256",
        )
        model_fields = ("root_experience_id", "response_episode_id", "request_sha256")
        if self.effect_kind == "execution":
            if any(getattr(self, field) is None for field in execution_fields):
                raise ValueError("execution effect identity is incomplete")
            if any(getattr(self, field) is not None for field in model_fields):
                raise ValueError("execution effect identity cannot carry model fields")
            if self.pinned_skill_artifact_sha256s != tuple(sorted(set(self.pinned_skill_artifact_sha256s))):
                raise ValueError("pinned skill artifacts must be sorted and unique")
        elif self.effect_kind == "model_inference":
            if any(getattr(self, field) is None for field in model_fields):
                raise ValueError("model inference effect identity is incomplete")
            if any(getattr(self, field) is not None for field in execution_fields):
                raise ValueError("model inference effect identity cannot carry execution fields")
        else:
            if any(getattr(self, field) is not None for field in (*execution_fields, *model_fields)):
                raise ValueError("legacy effect kinds keep current-source identity fields only")
        return self

    def computed_effect_id(self) -> str:
        if self.effect_kind == "execution":
            return derive_execution_effect_id_vnext(
                parent_effect_id=self.parent_effect_id,
                stable_step_id=self.stable_step_id,
                occurrence_key=self.occurrence_key,
                action_id=self.action_id,
                action_version=self.action_version,
                canonical_invocation_sha256=self.canonical_invocation_sha256,
                pinned_skill_artifact_sha256s=self.pinned_skill_artifact_sha256s,
            )
        if self.effect_kind == "model_inference":
            from .models import derive_model_inference_effect_id

            return derive_model_inference_effect_id(
                origin_request_id=self.origin_request_id,
                origin_run_id=self.origin_run_id,
                root_experience_id=self.root_experience_id,
                response_episode_id=self.response_episode_id,
                request_sha256=self.request_sha256,
            )
        return self.effect_id

    def has_valid_effect_id(self) -> bool:
        return self.effect_id == self.computed_effect_id()


def derive_execution_effect_id_vnext(
    *,
    parent_effect_id: str,
    stable_step_id: str,
    occurrence_key: str,
    action_id: str,
    action_version: str,
    canonical_invocation_sha256: str,
    pinned_skill_artifact_sha256s: tuple[str, ...] = (),
) -> str:
    return _stable_id(
        "eff",
        "tiangong.v21.effect-child.v1",
        {
            "parent_effect_id": parent_effect_id,
            "stable_step_id": stable_step_id,
            "occurrence_key": occurrence_key,
            "action_id": action_id,
            "action_version": action_version,
            "canonical_invocation_sha256": canonical_invocation_sha256,
            "pinned_skill_artifact_sha256s": tuple(sorted(set(pinned_skill_artifact_sha256s))),
        },
    )


def semantic_tuple_conflict(left: EffectIdentityVNext, right: EffectIdentityVNext) -> bool:
    """Same semantic tuple with a different invocation is an ID_CONFLICT."""
    if left.effect_kind != "execution" or right.effect_kind != "execution":
        return False
    same_tuple = (
        left.parent_effect_id == right.parent_effect_id
        and left.stable_step_id == right.stable_step_id
        and left.occurrence_key == right.occurrence_key
    )
    if not same_tuple:
        return False
    return (
        left.canonical_invocation_sha256 != right.canonical_invocation_sha256
        or left.action_id != right.action_id
        or left.action_version != right.action_version
    )


def _schema_config(name: str) -> ConfigDict:
    return ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:{name}",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )


def _stable_id(prefix: str, domain: str, fields: dict[str, object]) -> str:
    return f"{prefix}_{canonical_sha256({'domain': domain, **fields})}"


def _request_id(idempotency_key: str) -> str:
    return _stable_id(
        "req",
        "tiangong.identity.request.v1",
        {"inbound_idempotency_key": idempotency_key},
    )


def _run_id(request_id: str, run_sequence: int) -> str:
    return _stable_id(
        "run",
        "tiangong.identity.run.v1",
        {"request_id": request_id, "run_sequence": run_sequence},
    )


def _effect_id(
    *,
    request_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    effect_kind: str,
    ordinal: int,
    intent_sha256: str,
) -> str:
    return _stable_id(
        "eff",
        "tiangong.identity.effect.v1",
        {
            "request_id": request_id,
            "run_id": run_id,
            "run_sequence": run_sequence,
            "generation": generation,
            "effect_kind": effect_kind,
            "ordinal": ordinal,
            "intent_sha256": intent_sha256,
        },
    )


def _artifact_id(request_id: str, artifact_intent_id: str) -> str:
    return _stable_id(
        "art",
        "tiangong.identity.artifact.v1",
        {"request_id": request_id, "artifact_intent_id": artifact_intent_id},
    )


def _artifact_revision_id(
    *,
    artifact_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    revision: int,
    content_sha256: str,
) -> str:
    return _stable_id(
        "arv",
        "tiangong.identity.artifact-revision.v1",
        {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "run_sequence": run_sequence,
            "generation": generation,
            "revision": revision,
            "content_sha256": content_sha256,
        },
    )


def _delivery_id(
    *,
    request_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    recipient_scope_hash: str,
    reply_to_message_ref: str | None,
    payload_manifest_sha256: str,
) -> str:
    return _stable_id(
        "del",
        "tiangong.identity.delivery.v1",
        {
            "request_id": request_id,
            "run_id": run_id,
            "run_sequence": run_sequence,
            "generation": generation,
            "recipient_scope_hash": recipient_scope_hash,
            "reply_to_message_ref": reply_to_message_ref,
            "payload_manifest_sha256": payload_manifest_sha256,
        },
    )


class RequestIdentity(ContractModel):
    model_config = _schema_config("RequestIdentity")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    inbound_idempotency_key: Sha256
    request_id: RequestId

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.request_id != _request_id(self.inbound_idempotency_key):
            raise ValueError("request_id is not derived from the inbound idempotency key")
        return self


class RunIdentity(ContractModel):
    model_config = _schema_config("RunIdentity")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    request_id: RequestId
    run_sequence: int = Field(ge=1, le=2_147_483_647)
    run_id: RunId

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.run_id != _run_id(self.request_id, self.run_sequence):
            raise ValueError("run_id is not derived from request_id and run_sequence")
        return self


EffectKind = Literal["execution", "artifact", "delivery", "control"]


class EffectIdentity(ContractModel):
    model_config = _schema_config("EffectIdentity")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=1, le=2_147_483_647)
    generation: int = Field(ge=0, le=2_147_483_647)
    effect_kind: EffectKind
    ordinal: int = Field(ge=0, le=2_147_483_647)
    intent_sha256: Sha256
    effect_id: EffectId

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.run_id != _run_id(self.request_id, self.run_sequence):
            raise ValueError("effect run_id does not belong to request_id and run_sequence")
        expected = _effect_id(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=self.run_sequence,
            generation=self.generation,
            effect_kind=self.effect_kind,
            ordinal=self.ordinal,
            intent_sha256=self.intent_sha256,
        )
        if self.effect_id != expected:
            raise ValueError("effect_id is not derived from the complete effect intent")
        return self


class ArtifactRevisionIdentity(ContractModel):
    model_config = _schema_config("ArtifactRevisionIdentity")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=1, le=2_147_483_647)
    generation: int = Field(ge=0, le=2_147_483_647)
    artifact_intent_id: OpaqueId
    artifact_id: ArtifactId
    revision: int = Field(ge=1, le=2_147_483_647)
    content_sha256: Sha256
    artifact_revision_id: ArtifactRevisionId

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.run_id != _run_id(self.request_id, self.run_sequence):
            raise ValueError("artifact run_id does not belong to request_id and run_sequence")
        if self.artifact_id != _artifact_id(self.request_id, self.artifact_intent_id):
            raise ValueError("artifact_id is not derived from request_id and artifact intent")
        expected_revision = _artifact_revision_id(
            artifact_id=self.artifact_id,
            run_id=self.run_id,
            run_sequence=self.run_sequence,
            generation=self.generation,
            revision=self.revision,
            content_sha256=self.content_sha256,
        )
        if self.artifact_revision_id != expected_revision:
            raise ValueError("artifact revision identity does not bind its exact content and generation")
        return self


class DeliveryIdentity(ContractModel):
    model_config = _schema_config("DeliveryIdentity")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=1, le=2_147_483_647)
    generation: int = Field(ge=0, le=2_147_483_647)
    recipient_scope_hash: Sha256
    reply_to_message_ref: OpaqueId | None = None
    payload_manifest_sha256: Sha256
    delivery_id: DeliveryId

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.run_id != _run_id(self.request_id, self.run_sequence):
            raise ValueError("delivery run_id does not belong to request_id and run_sequence")
        expected = _delivery_id(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=self.run_sequence,
            generation=self.generation,
            recipient_scope_hash=self.recipient_scope_hash,
            reply_to_message_ref=self.reply_to_message_ref,
            payload_manifest_sha256=self.payload_manifest_sha256,
        )
        if self.delivery_id != expected:
            raise ValueError("delivery_id is not derived from recipient and immutable payload")
        return self


def _fence_id(fields: dict[str, object]) -> str:
    return _stable_id("fnc", "tiangong.identity.generation-fence.v1", fields)


class GenerationFence(ContractModel):
    """A digest-bound lease assertion; cryptographic authorization remains Ticket-owned."""

    model_config = _schema_config("GenerationFence")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    fence_id: GenerationFenceId
    gateway_epoch: int = Field(ge=1, le=9_223_372_036_854_775_807)
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=1, le=2_147_483_647)
    generation: int = Field(ge=0, le=2_147_483_647)
    lease_id: OpaqueId
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    supersedes_fence_id: GenerationFenceId | None = None
    fence_sha256: Sha256

    def identity_fields(self) -> dict[str, object]:
        return {
            "gateway_epoch": self.gateway_epoch,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "run_sequence": self.run_sequence,
            "generation": self.generation,
            "lease_id": self.lease_id,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "supersedes_fence_id": self.supersedes_fence_id,
        }

    def computed_fence_id(self) -> str:
        return _fence_id(self.identity_fields())

    def computed_fence_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"fence_sha256"}))

    def has_valid_fence(self) -> bool:
        return self.fence_id == self.computed_fence_id() and self.fence_sha256 == self.computed_fence_sha256()

    @model_validator(mode="after")
    def validate_fence(self) -> Self:
        if self.run_id != _run_id(self.request_id, self.run_sequence):
            raise ValueError("fence run_id does not belong to request_id and run_sequence")
        if self.expires_at_ms < self.issued_at_ms:
            raise ValueError("generation fence expires before it is issued")
        if self.expires_at_ms - self.issued_at_ms > 3_600_000:
            raise ValueError("generation fence lifetime exceeds one hour")
        if not self.has_valid_fence():
            raise ValueError("generation fence identity or digest is invalid")
        return self


FenceDisposition = Literal[
    "CURRENT",
    "DIGEST_INVALID",
    "CONTEXT_MISMATCH",
    "EPOCH_MISMATCH",
    "LATE_GENERATION",
    "FUTURE_GENERATION",
    "LEASE_MISMATCH",
    "NOT_YET_VALID",
    "EXPIRED",
]


class FenceDecision(ContractModel):
    model_config = _schema_config("FenceDecision")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    accepted: bool
    disposition: FenceDisposition
    reason_code: ReasonCode
    current_gateway_epoch: int = Field(ge=1)
    current_generation: int = Field(ge=0)
    observed_fence_id: GenerationFenceId

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.accepted != (self.disposition == "CURRENT"):
            raise ValueError("only CURRENT generation fence may be accepted")
        return self


def derive_request_identity(inbound_idempotency_key: str) -> RequestIdentity:
    return RequestIdentity(
        inbound_idempotency_key=inbound_idempotency_key,
        request_id=_request_id(inbound_idempotency_key),
    )


def derive_run_identity(request_id: str, run_sequence: int) -> RunIdentity:
    return RunIdentity(
        request_id=request_id,
        run_sequence=run_sequence,
        run_id=_run_id(request_id, run_sequence),
    )


def derive_effect_identity(
    *,
    request_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    effect_kind: EffectKind,
    ordinal: int,
    intent_sha256: str,
) -> EffectIdentity:
    return EffectIdentity(
        request_id=request_id,
        run_id=run_id,
        run_sequence=run_sequence,
        generation=generation,
        effect_kind=effect_kind,
        ordinal=ordinal,
        intent_sha256=intent_sha256,
        effect_id=_effect_id(
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation,
            effect_kind=effect_kind,
            ordinal=ordinal,
            intent_sha256=intent_sha256,
        ),
    )


def derive_artifact_revision_identity(
    *,
    request_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    artifact_intent_id: str,
    revision: int,
    content_sha256: str,
) -> ArtifactRevisionIdentity:
    artifact_id = _artifact_id(request_id, artifact_intent_id)
    return ArtifactRevisionIdentity(
        request_id=request_id,
        run_id=run_id,
        run_sequence=run_sequence,
        generation=generation,
        artifact_intent_id=artifact_intent_id,
        artifact_id=artifact_id,
        revision=revision,
        content_sha256=content_sha256,
        artifact_revision_id=_artifact_revision_id(
            artifact_id=artifact_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation,
            revision=revision,
            content_sha256=content_sha256,
        ),
    )


def derive_delivery_identity(
    *,
    request_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    recipient_scope_hash: str,
    reply_to_message_ref: str | None,
    payload_manifest_sha256: str,
) -> DeliveryIdentity:
    return DeliveryIdentity(
        request_id=request_id,
        run_id=run_id,
        run_sequence=run_sequence,
        generation=generation,
        recipient_scope_hash=recipient_scope_hash,
        reply_to_message_ref=reply_to_message_ref,
        payload_manifest_sha256=payload_manifest_sha256,
        delivery_id=_delivery_id(
            request_id=request_id,
            run_id=run_id,
            run_sequence=run_sequence,
            generation=generation,
            recipient_scope_hash=recipient_scope_hash,
            reply_to_message_ref=reply_to_message_ref,
            payload_manifest_sha256=payload_manifest_sha256,
        ),
    )


def derive_generation_fence(
    *,
    gateway_epoch: int,
    request_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    lease_id: str,
    issued_at_ms: int,
    expires_at_ms: int,
    supersedes_fence_id: str | None = None,
) -> GenerationFence:
    fields: dict[str, object] = {
        "gateway_epoch": gateway_epoch,
        "request_id": request_id,
        "run_id": run_id,
        "run_sequence": run_sequence,
        "generation": generation,
        "lease_id": lease_id,
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
        "supersedes_fence_id": supersedes_fence_id,
    }
    fence_id = _fence_id(fields)
    body = {"schema_version": SCHEMA_VERSION, "fence_id": fence_id, **fields}
    return GenerationFence(**body, fence_sha256=canonical_sha256(body))


def _decision(
    fence: GenerationFence,
    disposition: FenceDisposition,
    reason_code: str,
    *,
    current_gateway_epoch: int,
    current_generation: int,
) -> FenceDecision:
    return FenceDecision(
        accepted=disposition == "CURRENT",
        disposition=disposition,
        reason_code=reason_code,
        current_gateway_epoch=current_gateway_epoch,
        current_generation=current_generation,
        observed_fence_id=fence.fence_id,
    )


def evaluate_generation_fence(
    fence: GenerationFence,
    *,
    current_gateway_epoch: int,
    current_request_id: str,
    current_run_id: str,
    current_generation: int,
    active_lease_id: str,
    now_ms: int,
    clock_skew_ms: int = 5_000,
) -> FenceDecision:
    if current_gateway_epoch < 1 or current_generation < 0 or now_ms < 0 or clock_skew_ms < 0:
        raise ValueError("invalid current fence comparison values")
    common = {
        "current_gateway_epoch": current_gateway_epoch,
        "current_generation": current_generation,
    }
    if not fence.has_valid_fence():
        return _decision(fence, "DIGEST_INVALID", "fence.digest_invalid", **common)
    if fence.request_id != current_request_id or fence.run_id != current_run_id:
        return _decision(fence, "CONTEXT_MISMATCH", "fence.context_mismatch", **common)
    if fence.gateway_epoch != current_gateway_epoch:
        return _decision(fence, "EPOCH_MISMATCH", "fence.epoch_mismatch", **common)
    if fence.generation < current_generation:
        return _decision(fence, "LATE_GENERATION", "fence.late_generation", **common)
    if fence.generation > current_generation:
        return _decision(fence, "FUTURE_GENERATION", "fence.future_generation", **common)
    if fence.lease_id != active_lease_id:
        return _decision(fence, "LEASE_MISMATCH", "fence.lease_mismatch", **common)
    if now_ms + clock_skew_ms < fence.issued_at_ms:
        return _decision(fence, "NOT_YET_VALID", "fence.not_yet_valid", **common)
    if now_ms > fence.expires_at_ms + clock_skew_ms:
        return _decision(fence, "EXPIRED", "fence.expired", **common)
    return _decision(fence, "CURRENT", "fence.current", **common)


__all__ = [
    "ArtifactRevisionIdentity",
    "DeliveryIdentity",
    "EffectIdentity",
    "EffectIdentityVNext",
    "EffectKind",
    "FenceDecision",
    "FenceDisposition",
    "GenerationFence",
    "RequestIdentity",
    "RunIdentity",
    "derive_artifact_revision_identity",
    "derive_delivery_identity",
    "derive_execution_effect_id_vnext",
    "derive_effect_identity",
    "derive_generation_fence",
    "derive_request_identity",
    "derive_run_identity",
    "evaluate_generation_fence",
    "semantic_tuple_conflict",
]
