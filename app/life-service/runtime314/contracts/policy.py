"""Deterministic action-policy, confirmation, and Omni capability contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_sha256
from .execution import (
    Base64UrlEd25519Signature,
    CompositionExecutionBindingV1,
    RiskClass,
    SideEffectClass,
)
from .models import (
    ActionId,
    ContractModel,
    EffectId,
    OpaqueId,
    RequestId,
    RunId,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
    SourceRef,
)


ActionEffect = Literal["read", "verify", "create", "write", "update", "execute"]
PathPolicy = Literal["no_path", "workspace_only", "object_grant_only"]
PolicyOutcome = Literal["ALLOW", "REQUIRE_CONFIRMATION", "REJECT"]

_EMPTY_SET_SHA256 = canonical_sha256([])
_UNSET_SHA256 = "0" * 64
_UNSET_REQUEST_ID = "req_" + "0" * 64
_UNSET_RUN_ID = "run_" + "0" * 64
_UNSET_EFFECT_ID = "eff_" + "0" * 64


def _schema(name: str) -> ConfigDict:
    return ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        json_schema_extra={
            "$id": f"{SCHEMA_BASE}:{name}",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )


def _sorted_unique(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value))):
        raise ValueError("set-like policy fields must be sorted and unique")
    return value


class ResourceEnvelope(ContractModel):
    max_runtime_ms: int = Field(ge=1, le=3_600_000)
    max_output_bytes: int = Field(ge=0, le=2_147_483_648)
    max_tool_calls: int = Field(ge=1, le=10_000)

    def sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class ActionIntent(ContractModel):
    """A model/life process may propose this object, but cannot authorize it."""

    model_config = _schema("ActionIntent")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    intent_id: OpaqueId
    source: Literal["chat", "life_scheduler", "system"]
    life_id: OpaqueId
    principal_scope_hash: Sha256
    conversation_scope_hash: Sha256
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    action_id: ActionId
    action_version: OpaqueId
    arguments_sha256: Sha256
    workspace_id: OpaqueId
    workspace_scope_hash: Sha256
    input_object_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    requested_side_effects: tuple[SideEffectClass, ...] = Field(max_length=6)
    requested_resources: ResourceEnvelope
    skill_id: OpaqueId | None = None
    skill_version: OpaqueId | None = None
    skill_sha256: Sha256 | None = None
    source_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=1024)
    source_set_sha256: Sha256
    canonical_invocation_sha256: Sha256
    target_ref: OpaqueId | None = None
    target_snapshot_sha256: Sha256 | None = None
    life_snapshot_revision: int | None = Field(default=None, ge=1)
    life_snapshot_sha256: Sha256 | None = None
    payload_sha256: Sha256 = _UNSET_SHA256
    attachment_set_sha256: Sha256 = _EMPTY_SET_SHA256
    composition_execution_binding: CompositionExecutionBindingV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    intent_sha256: Sha256

    _sets = field_validator("input_object_refs", "requested_side_effects")(_sorted_unique)

    @field_validator("source_refs")
    @classmethod
    def validate_sorted_unique_source_refs(
        cls, value: tuple[SourceRef, ...]
    ) -> tuple[SourceRef, ...]:
        keys = tuple(item.sort_key() for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("set-like policy fields must be sorted and unique")
        return value

    @model_validator(mode="before")
    @classmethod
    def fill_computed_digests(cls, data: object) -> object:
        if isinstance(data, dict):
            refs = data.get("source_refs")
            if refs is not None and data.get("source_set_sha256") is None:
                normalized = [
                    item if isinstance(item, SourceRef) else SourceRef.model_validate(item)
                    for item in refs
                ]
                data = {
                    **data,
                    "source_set_sha256": canonical_sha256(
                        [item.model_dump(mode="json") for item in normalized]
                    ),
                }
            if data.get("canonical_invocation_sha256") is None and all(
                key in data for key in ("action_id", "action_version", "workspace_id")
            ):
                data = {
                    **data,
                    "canonical_invocation_sha256": canonical_sha256(
                        {
                            "action_id": data["action_id"],
                            "action_version": data["action_version"],
                            "payload_sha256": data.get("payload_sha256") or _UNSET_SHA256,
                            "target_ref": data.get("target_ref"),
                            "workspace_id": data["workspace_id"],
                        }
                    ),
                }
        return data

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if not self.created_at_ms <= self.expires_at_ms <= self.created_at_ms + 60_000:
            raise ValueError("action intent lifetime is invalid")
        skill = (self.skill_id, self.skill_version, self.skill_sha256)
        if sum(value is not None for value in skill) not in {0, 3}:
            raise ValueError("action intent Skill binding is incomplete")
        life_snapshot = (self.life_snapshot_revision, self.life_snapshot_sha256)
        if sum(value is not None for value in life_snapshot) not in {0, 2}:
            raise ValueError("action intent life snapshot binding is incomplete")
        if self.source == "life_scheduler" and self.life_snapshot_revision is None:
            raise ValueError("life scheduler intents must bind a life snapshot")
        if self.source_set_sha256 != canonical_sha256(
            [item.model_dump(mode="json") for item in self.source_refs]
        ):
            raise ValueError("action intent source set digest is invalid")
        if self.canonical_invocation_sha256 != canonical_sha256(
            {
                "action_id": self.action_id,
                "action_version": self.action_version,
                "payload_sha256": self.payload_sha256,
                "target_ref": self.target_ref,
                "workspace_id": self.workspace_id,
            }
        ):
            raise ValueError("action intent canonical invocation digest is invalid")
        binding = self.composition_execution_binding
        if binding is not None:
            if not binding.has_valid_sha256():
                raise ValueError("action intent composition binding digest is invalid")
            if (
                binding.request_id != self.request_id
                or binding.run_id != self.run_id
                or binding.generation != self.generation
                or binding.action_id != self.action_id
                or binding.action_version != self.action_version
                or binding.materialized_arguments_sha256 != self.payload_sha256
                or binding.canonical_invocation_sha256
                != self.canonical_invocation_sha256
                or binding.target_snapshot_sha256 != self.target_snapshot_sha256
                or binding.workspace_id != self.workspace_id
                or binding.workspace_scope_hash != self.workspace_scope_hash
            ):
                raise ValueError(
                    "action intent composition binding does not match intent scope"
                )
        return self

    @property
    def source_evidence_refs(self) -> tuple[str, ...]:
        """vOld evidence-id view of the vNext provenance set (one object_id per ref)."""

        return tuple(item.object_id for item in self.source_refs)

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"intent_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.intent_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"intent_sha256": self.computed_sha256()})


class ActionPermission(ContractModel):
    """Machine-generated permission for one executable registry action."""

    model_config = _schema("ActionPermission")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    action_id: ActionId
    action_version: OpaqueId
    registry_risk: RiskClass
    effective_risk: RiskClass
    effect: ActionEffect
    handler: str = Field(max_length=256)
    executable: Literal[True] = True
    allowed_side_effects: tuple[SideEffectClass, ...] = Field(max_length=6)
    path_policy: PathPolicy
    allow_absolute_paths: bool
    allow_shell: bool
    allow_python: bool
    requires_confirmation: bool
    source_manifest_sha256: Sha256
    permission_sha256: Sha256

    _effects = field_validator("allowed_side_effects")(_sorted_unique)

    @model_validator(mode="after")
    def validate_floor(self) -> Self:
        order = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4, "A5": 5}
        effect_floor = {
            "read": "A0",
            "verify": "A0",
            "create": "A2",
            "write": "A2",
            "update": "A3",
            "execute": "A3",
        }[self.effect]
        if order[self.effective_risk] < max(order[self.registry_risk], order[effect_floor]):
            raise ValueError("effective action risk is below a machine floor")
        if self.effective_risk == "A5":
            raise ValueError("A5 action cannot be executable")
        if self.requires_confirmation:
            raise ValueError("A0-A4 permissions must not require confirmation")
        # Absolute paths are a location capability, not an impact class.  The
        # gateway still binds every path to the signed action grant and A5 is
        # still non-executable, but an ordinary A1 read must not be promoted to
        # A4 merely because the user selected a file outside the workspace.
        # Arbitrary shell/Python execution remains privileged and therefore A4.
        if (self.allow_shell or self.allow_python) and self.effective_risk != "A4":
            raise ValueError("privileged execution must remain classified as A4")
        if "destructive" in self.allowed_side_effects and self.effective_risk != "A4":
            raise ValueError("destructive permission must be A4")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"permission_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.permission_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"permission_sha256": self.computed_sha256()})


class ActionRegistrySnapshot(ContractModel):
    model_config = _schema("ActionRegistrySnapshot")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    registry_id: OpaqueId
    revision: int = Field(ge=1)
    generated_at_ms: int = Field(ge=0)
    source_manifest_sha256: Sha256
    executable_count: int = Field(ge=1, le=10_000)
    permissions: tuple[ActionPermission, ...] = Field(min_length=1, max_length=10_000)
    registry_sha256: Sha256

    @model_validator(mode="after")
    def validate_permissions(self) -> Self:
        keys = tuple(item.action_id for item in self.permissions)
        if keys != tuple(sorted(set(keys))) or self.executable_count != len(keys):
            raise ValueError("action registry permissions are incomplete or unordered")
        if any(
            not item.has_valid_sha256()
            or item.source_manifest_sha256 != self.source_manifest_sha256
            for item in self.permissions
        ):
            raise ValueError("action registry contains an invalid permission")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.registry_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"registry_sha256": self.computed_sha256()})


class UserConfirmationGrant(ContractModel):
    model_config = _schema("UserConfirmationGrant")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    confirmation_id: OpaqueId
    decision_id: OpaqueId
    impact_sha256: Sha256
    action_id: ActionId
    arguments_sha256: Sha256
    workspace_scope_hash: Sha256
    principal_scope_hash: Sha256
    risk_class: Literal["A4"] = "A4"
    confirmer: Literal["user"] = "user"
    nonce: OpaqueId
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    confirmation_sha256: Sha256

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        if not self.issued_at_ms <= self.expires_at_ms <= self.issued_at_ms + 600_000:
            raise ValueError("confirmation grant lifetime is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"confirmation_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.confirmation_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"confirmation_sha256": self.computed_sha256()})


class SkillActivationGrant(ContractModel):
    model_config = _schema("SkillActivationGrant")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    activation_id: OpaqueId
    selection_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    principal_scope_hash: Sha256
    skill_catalog_hash: Sha256
    capability_manifest_hash: Sha256
    skill_id: OpaqueId
    skill_version: OpaqueId
    skill_sha256: Sha256
    allowed_action_ids: tuple[ActionId, ...] = Field(min_length=1, max_length=4096)
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    activation_sha256: Sha256

    _actions = field_validator("allowed_action_ids")(_sorted_unique)

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        if not self.issued_at_ms <= self.expires_at_ms <= self.issued_at_ms + 3_600_000:
            raise ValueError("Skill activation lifetime is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"activation_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.activation_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"activation_sha256": self.computed_sha256()})


class PolicyDecision(ContractModel):
    model_config = _schema("PolicyDecision")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    decision_id: OpaqueId
    intent_sha256: Sha256
    impact_id: OpaqueId
    impact_sha256: Sha256
    action_permission_sha256: Sha256
    action_registry_sha256: Sha256
    capability_manifest_hash: Sha256
    component_manifest_hash: Sha256
    policy_snapshot_sha256: Sha256
    policy_coverage_version: OpaqueId = "unspecified"
    policy_coverage_sha256: Sha256 = _UNSET_SHA256
    computed_risk: RiskClass
    outcome: PolicyOutcome
    confirmation_id: OpaqueId | None = None
    confirmation_sha256: Sha256 | None = None
    skill_activation_id: OpaqueId | None = None
    skill_activation_sha256: Sha256 | None = None
    composition_execution_binding: CompositionExecutionBindingV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=64)
    decided_at_ms: int = Field(ge=0)
    decision_sha256: Sha256

    _reasons = field_validator("reason_codes")(_sorted_unique)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        confirmation = (self.confirmation_id, self.confirmation_sha256)
        skill = (self.skill_activation_id, self.skill_activation_sha256)
        if sum(value is not None for value in confirmation) not in {0, 2}:
            raise ValueError("policy confirmation binding is incomplete")
        if sum(value is not None for value in skill) not in {0, 2}:
            raise ValueError("policy Skill binding is incomplete")
        if self.computed_risk == "A5":
            if self.outcome != "REJECT":
                raise ValueError("A5 policy decision must reject")
        elif self.outcome == "REQUIRE_CONFIRMATION":
            raise ValueError("A0-A4 policy decisions cannot require confirmation")
        if self.confirmation_id is not None:
            raise ValueError("A0-A5 policy decisions do not consume confirmation grants")
        if (
            self.composition_execution_binding is not None
            and not self.composition_execution_binding.has_valid_sha256()
        ):
            raise ValueError("policy composition binding digest is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"decision_sha256": self.computed_sha256()})


class OmniCapabilityGrantHeader(ContractModel):
    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    alg: Literal["EdDSA"] = "EdDSA"
    typ: Literal["tiangong.omni-capability-grant+jws"] = (
        "tiangong.omni-capability-grant+jws"
    )
    kid: OpaqueId


class OmniCapabilityGrantPayload(ContractModel):
    grant_type: Literal["OmniCapabilityGrant"] = "OmniCapabilityGrant"
    grant_id: OpaqueId
    issuer: Literal["tiangong-total-gateway"] = "tiangong-total-gateway"
    audience: Literal["tiangong-backend"] = "tiangong-backend"
    ticket_id: OpaqueId
    ticket_sha256: Sha256 = _UNSET_SHA256
    request_id: RequestId = _UNSET_REQUEST_ID
    run_id: RunId = _UNSET_RUN_ID
    generation: int = Field(default=0, ge=0)
    effect_id: EffectId = _UNSET_EFFECT_ID
    decision_id: OpaqueId
    decision_sha256: Sha256
    impact_sha256: Sha256
    action_permission_sha256: Sha256
    action_registry_sha256: Sha256
    capability_manifest_hash: Sha256
    component_manifest_hash: Sha256
    action_id: ActionId
    action_version: OpaqueId
    arguments_sha256: Sha256
    workspace_id: OpaqueId
    workspace_scope_hash: Sha256
    principal_scope_hash: Sha256
    conversation_scope_hash: Sha256 = _UNSET_SHA256
    risk_class: RiskClass
    allowed_side_effects: tuple[SideEffectClass, ...] = Field(max_length=6)
    path_policy: PathPolicy
    allow_absolute_paths: bool
    allow_shell: bool
    allow_python: bool
    confirmation_sha256: Sha256 | None = None
    skill_id: OpaqueId | None = None
    skill_version: OpaqueId | None = None
    skill_sha256: Sha256 | None = None
    skill_activation_sha256: Sha256 | None = None
    composition_execution_binding: CompositionExecutionBindingV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    gateway_epoch: int = Field(ge=1)
    nonce: OpaqueId
    issued_at_ms: int = Field(ge=0)
    not_before_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)

    _side_effects = field_validator("allowed_side_effects")(_sorted_unique)

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if not self.issued_at_ms <= self.not_before_ms <= self.expires_at_ms:
            raise ValueError("capability grant time window is invalid")
        if self.expires_at_ms - self.issued_at_ms > 60_000:
            raise ValueError("capability grant lifetime exceeds 60 seconds")
        if self.risk_class == "A5":
            raise ValueError("A5 capability grant is forbidden")
        if self.confirmation_sha256 is not None:
            raise ValueError("A0-A4 capability grants must not carry confirmation")
        skill = (self.skill_id, self.skill_version, self.skill_sha256)
        if sum(value is not None for value in skill) not in {0, 3}:
            raise ValueError("capability grant Skill binding is incomplete")
        if (self.skill_id is None) != (self.skill_activation_sha256 is None):
            raise ValueError("capability grant Skill activation binding is incomplete")
        binding = self.composition_execution_binding
        if binding is not None:
            if not binding.has_valid_sha256():
                raise ValueError("capability composition binding digest is invalid")
            if (
                binding.request_id != self.request_id
                or binding.run_id != self.run_id
                or binding.generation != self.generation
                or binding.effect_id != self.effect_id
                or binding.action_id != self.action_id
                or binding.action_version != self.action_version
                or binding.workspace_id != self.workspace_id
                or binding.workspace_scope_hash != self.workspace_scope_hash
            ):
                raise ValueError(
                    "capability composition binding does not match grant scope"
                )
        # Keep the signed path-location capability orthogonal to impact risk;
        # shell and Python elevation still require A4.  This mirrors
        # ActionPermission and permits user-selected files outside the active
        # workspace without weakening the A5 boundary.
        if (self.allow_shell or self.allow_python) and self.risk_class != "A4":
            raise ValueError("capability elevation must remain classified as A4")
        return self


class OmniCapabilityGrant(ContractModel):
    model_config = _schema("OmniCapabilityGrant")

    header: OmniCapabilityGrantHeader
    payload: OmniCapabilityGrantPayload
    signature: Base64UrlEd25519Signature


def derive_stable_step_id(*, anchor_id: str, semantic_step_role: str) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.v21.stable-step.v1",
            "anchor_id": anchor_id,
            "semantic_step_role": semantic_step_role,
        }
    )


def derive_occurrence_key(
    *, stable_step_id: str, semantic_target_key: str, semantic_occurrence_index: int
) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.v21.occurrence.v1",
            "stable_step_id": stable_step_id,
            "semantic_target_key": semantic_target_key,
            "semantic_occurrence_index": semantic_occurrence_index,
        }
    )


def derive_life_proposal_id(
    *,
    life_id: str,
    root_experience_id: str,
    episode_id: str,
    stable_step_id: str,
    occurrence_key: str,
) -> str:
    return "lpr_" + canonical_sha256(
        {
            "domain": "tiangong.v21.life-proposal.v1",
            "life_id": life_id,
            "root_experience_id": root_experience_id,
            "episode_id": episode_id,
            "stable_step_id": stable_step_id,
            "occurrence_key": occurrence_key,
        }
    )


def derive_gateway_registration_id(*, proposal_id: str) -> str:
    return "reg_" + canonical_sha256(
        {
            "domain": "tiangong.v21.gateway-registration.v1",
            "proposal_id": proposal_id,
        }
    )


class ActionIntentVNext(ContractModel):
    """vNext ActionIntent: binds episode, commitment, route and semantic identity."""

    schema_id: Literal["ActionIntentVNext"] = "ActionIntentVNext"
    schema_version: Literal["tiangong.action_intent.v3"] = "tiangong.action_intent.v3"
    intent_id: OpaqueId
    source: Literal["chat", "life_scheduler", "system"]
    life_id: OpaqueId
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    action_id: ActionId
    action_version: OpaqueId
    arguments_sha256: Sha256
    workspace_id: OpaqueId
    workspace_scope_hash: Sha256
    run_life_binding_sha256: Sha256
    root_experience_id: OpaqueId
    episode_id: OpaqueId
    episode_sha256: Sha256
    commitment_kind: Literal["work", "none"]
    intent_anchor_sha256: Sha256
    route_kind: Literal["ad_hoc", "release_skill", "life_skill", "composite_tool"]
    semantic_step_role: str = Field(min_length=1, max_length=160)
    semantic_target_key: str = Field(min_length=1, max_length=160)
    semantic_occurrence_index: int = Field(ge=1)
    stable_step_id: Sha256
    occurrence_key: Sha256
    canonical_invocation_sha256: Sha256
    absolute_deadline_ms: int = Field(ge=0)
    cancel_generation: int = Field(ge=0)
    agency_decision_id: OpaqueId | None = None
    agency_decision_sha256: Sha256 | None = None
    commitment_id: OpaqueId | None = None
    commitment_sha256: Sha256 | None = None
    obligation_id: OpaqueId | None = None
    obligation_set_sha256: Sha256 | None = None
    skill_id: OpaqueId | None = None
    skill_version: OpaqueId | None = None
    skill_sha256: Sha256 | None = None
    activation_hash: Sha256 | None = None
    pinned_skill_artifact_sha256s: tuple[Sha256, ...] = Field(default=(), max_length=64)
    life_artifact_sha256: Sha256 | None = None
    life_overlay_sha256: Sha256 | None = None
    parent_effect_id: EffectId | None = None
    composite_execution_id: OpaqueId | None = None
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    intent_sha256: Sha256

    @model_validator(mode="after")
    def validate_commitment_and_route(self) -> Self:
        if self.commitment_kind == "work":
            if (
                self.commitment_id is None
                or self.commitment_sha256 is None
                or self.obligation_id is None
                or self.obligation_set_sha256 is None
            ):
                raise ValueError("work intent requires commitment and obligation binding")
            if self.intent_anchor_sha256 != self.commitment_sha256:
                raise ValueError("work intent anchor must equal commitment sha256")
        else:
            if (
                self.agency_decision_id is None
                or self.agency_decision_sha256 is None
                or self.commitment_id is not None
                or self.commitment_sha256 is not None
                or self.obligation_id is not None
                or self.obligation_set_sha256 is not None
            ):
                raise ValueError("none intent requires agency anchor and forbids commitment fields")
            if self.intent_anchor_sha256 != self.agency_decision_sha256:
                raise ValueError("none intent anchor must equal agency decision sha256")
        if self.route_kind == "ad_hoc" and any(
            value is not None
            for value in (self.skill_id, self.skill_version, self.skill_sha256, self.activation_hash)
        ):
            raise ValueError("ad_hoc route cannot carry skill binding")
        skill = (self.skill_id, self.skill_version, self.skill_sha256)
        if sum(value is not None for value in skill) not in {0, 3}:
            raise ValueError("vNext Skill binding is incomplete")
        if self.expires_at_ms < self.created_at_ms:
            raise ValueError("vNext intent lifetime is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"intent_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.intent_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"intent_sha256": self.computed_sha256()})


class LifeExecutionProposal(ContractModel):
    """Life -> Gateway external action proposal; Gateway recomputes identity."""

    schema_id: Literal["LifeExecutionProposal"] = "LifeExecutionProposal"
    schema_version: Literal["tiangong.life_execution_proposal.v1"] = "tiangong.life_execution_proposal.v1"
    proposal_id: OpaqueId
    life_id: OpaqueId
    run_life_binding_sha256: Sha256
    root_experience_id: OpaqueId
    episode_id: OpaqueId
    episode_sha256: Sha256
    agency_decision_id: OpaqueId
    agency_decision_sha256: Sha256
    action_candidate_object_ref: OpaqueId
    action_candidate_sha256: Sha256
    commitment_kind: Literal["work", "none"]
    intent_anchor_sha256: Sha256
    action_id: ActionId
    action_version: OpaqueId
    args_sha256: Sha256
    workspace_id: OpaqueId
    scope_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    resource_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    semantic_step_role: str = Field(min_length=1, max_length=160)
    semantic_target_key: str = Field(min_length=1, max_length=160)
    semantic_occurrence_index: int = Field(ge=1)
    stable_step_id: Sha256
    occurrence_key: Sha256
    commitment_id: OpaqueId | None = None
    commitment_sha256: Sha256 | None = None
    obligation_id: OpaqueId | None = None
    obligation_set_sha256: Sha256 | None = None
    skill_id: OpaqueId | None = None
    skill_version: OpaqueId | None = None
    skill_sha256: Sha256 | None = None
    activation_hash: Sha256 | None = None
    pinned_skill_artifact_sha256s: tuple[Sha256, ...] = Field(default=(), max_length=64)
    target_ref: OpaqueId | None = None
    target_revision: int | None = Field(default=None, ge=0)
    recipient_ref: OpaqueId | None = None
    input_object_refs: tuple[OpaqueId, ...] = Field(default=(), max_length=256)
    proposal_sha256: Sha256

    @model_validator(mode="after")
    def validate_commitment_shape(self) -> Self:
        if self.commitment_kind == "work":
            if (
                self.commitment_id is None
                or self.commitment_sha256 is None
                or self.obligation_id is None
                or self.obligation_set_sha256 is None
            ):
                raise ValueError("work proposal requires commitment binding")
            if self.intent_anchor_sha256 != self.commitment_sha256:
                raise ValueError("work proposal anchor must equal commitment sha256")
        else:
            if self.commitment_id is not None or self.commitment_sha256 is not None:
                raise ValueError("none proposal forbids commitment fields")
            if self.intent_anchor_sha256 != self.agency_decision_sha256:
                raise ValueError("none proposal anchor must equal agency decision sha256")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"proposal_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.proposal_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"proposal_sha256": self.computed_sha256()})


class GatewayRegistrationReceipt(ContractModel):
    """Gateway receipt for one durable LifeExecutionProposal registration."""

    schema_id: Literal["GatewayRegistrationReceipt"] = "GatewayRegistrationReceipt"
    schema_version: Literal["tiangong.gateway_registration_receipt.v1"] = "tiangong.gateway_registration_receipt.v1"
    registration_id: OpaqueId
    proposal_id: OpaqueId
    proposal_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    run_sequence: int = Field(ge=0)
    generation: int = Field(ge=0)
    run_life_binding_sha256: Sha256
    action_intent_id: OpaqueId
    action_intent_sha256: Sha256
    registered_at_ms: int = Field(ge=0)
    registration_sha256: Sha256

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"registration_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.registration_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"registration_sha256": self.computed_sha256()})


__all__ = [
    "ActionEffect",
    "ActionIntent",
    "ActionIntentVNext",
    "ActionPermission",
    "ActionRegistrySnapshot",
    "GatewayRegistrationReceipt",
    "LifeExecutionProposal",
    "OmniCapabilityGrant",
    "OmniCapabilityGrantHeader",
    "OmniCapabilityGrantPayload",
    "PathPolicy",
    "PolicyDecision",
    "PolicyOutcome",
    "ResourceEnvelope",
    "SkillActivationGrant",
    "UserConfirmationGrant",
    "derive_gateway_registration_id",
    "derive_life_proposal_id",
    "derive_occurrence_key",
    "derive_stable_step_id",
]
