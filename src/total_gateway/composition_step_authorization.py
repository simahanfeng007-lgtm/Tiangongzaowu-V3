"""Canonical P7C.1 composition-step authorization receipt codecs.

This module owns data validation and row projection only.  It does not decide
Policy, sign a Ticket or Grant, consume a nonce, claim an effect, dispatch a
handler, record verification, or make a completion decision.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import json
import re
from typing import Any, Mapping, Sequence, Self

from contracts import (
    ActionImpact,
    ActionIntent,
    ExecutionTicket,
    OmniCapabilityGrant,
    PolicyDecision,
    TrustBundle,
    canonical_json_bytes,
    canonical_sha256,
)


COMPOSITION_STEP_AUTHORIZATION_SCHEMA = (
    "tiangong.composition-step-authorization.v1"
)
COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2 = (
    "tiangong.composition-step-authorization.v2"
)
COMPOSITION_CONTINUATION_DELEGATION_SCHEMA = (
    "tiangong.composition-continuation-delegation.v1"
)
MAX_AUTHORIZATION_ARTIFACT_JSON_BYTES = 2 * 1024 * 1024
_ZERO_SHA256 = "0" * 64
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_A0_SIDE_EFFECTS = frozenset({"none", "read"})
_VALID_SIDE_EFFECTS = frozenset(
    {
        "none",
        "read",
        "local_write",
        "external_write",
        "external_send",
        "destructive",
    }
)
_CONTINUATION_ISSUANCE_CONTEXT_KEYS = frozenset(
    {
        "channel",
        "tenant_id",
        "link_account_id",
        "conversation_scope_hash",
        "life_id",
        "life_evidence_ref",
        "session_id",
        "life_snapshot_revision",
        "life_snapshot_hash",
        "output_root_id",
        "artifact_intent_id",
        "parent_ticket_payload_sha256",
        "max_output_bytes",
        "max_runtime_ms",
        "max_tool_calls",
        "resource_envelope_sha256",
        "allowed_side_effects",
        "side_effect_envelope_sha256",
    }
)


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = canonical_json_bytes(value)
    if len(encoded) > MAX_AUTHORIZATION_ARTIFACT_JSON_BYTES:
        raise ValueError("composition authorization JSON exceeds the byte limit")
    return json.loads(encoded)


def canonical_json_text(value: Any) -> str:
    """Return the canonical JSON text used by the immutable receipt row."""

    return canonical_json_bytes(_json_value(value)).decode("utf-8")


def _parse_canonical_json(
    value: str,
    *,
    label: str,
    expected_type: type | tuple[type, ...] | None = None,
) -> Any:
    if not isinstance(value, str) or len(value.encode("utf-8")) > (
        MAX_AUTHORIZATION_ARTIFACT_JSON_BYTES
    ):
        raise ValueError(f"{label} JSON is invalid")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} JSON is invalid") from exc
    if expected_type is not None and not isinstance(parsed, expected_type):
        raise ValueError(f"{label} JSON has the wrong top-level type")
    if canonical_json_text(parsed) != value:
        raise ValueError(f"{label} JSON is not canonical")
    return parsed


def _require_sha256(value: str, *, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_nonempty(value: str, *, label: str, maximum: int = 4096) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{label} is invalid")


def _composition_binding(
    document: Mapping[str, Any], *, label: str
) -> dict[str, Any]:
    binding = document.get("composition_execution_binding")
    if not isinstance(binding, Mapping):
        raise ValueError(f"{label} composition execution binding is missing")
    binding = dict(binding)
    value = binding.get("binding_sha256")
    _require_sha256(value, label=f"{label} composition binding")
    if value != canonical_sha256(
        {key: item for key, item in binding.items() if key != "binding_sha256"}
    ):
        raise ValueError(f"{label} composition binding digest is invalid")
    return binding


def _self_hash(document: Mapping[str, Any], field: str, *, label: str) -> str:
    value = document.get(field)
    _require_sha256(value, label=f"{label} digest")
    expected = canonical_sha256(
        {key: item for key, item in document.items() if key != field}
    )
    if value != expected:
        raise ValueError(f"{label} digest is invalid")
    return value


def _restore_contract(model_type: Any, payload: str, *, label: str) -> Any:
    """Restore a strict contract from its canonical JSON wire form.

    Pydantic's JSON representation uses arrays for frozen tuple fields.  The
    non-strict parse permits that one representation conversion; the exact
    canonical byte round-trip rejects every other coercion, inserted default,
    unknown field, or non-canonical encoding.
    """

    try:
        restored = model_type.model_validate_json(payload, strict=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} contract is invalid") from exc
    if canonical_json_bytes(
        restored.model_dump(mode="json")
    ) != payload.encode("utf-8"):
        raise ValueError(f"{label} contract is not an exact canonical value")
    return restored


def _validate_continuation_issuance_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _CONTINUATION_ISSUANCE_CONTEXT_KEYS:
        raise ValueError("composition continuation issuance context is invalid")
    for field in (
        "tenant_id",
        "link_account_id",
        "life_id",
        "session_id",
        "output_root_id",
    ):
        _require_nonempty(value[field], label=f"continuation {field}")
    if value["channel"] not in {"desktop", "wechat", "feishu", "system", "test"}:
        raise ValueError("composition continuation channel is invalid")
    if (
        not isinstance(value["life_evidence_ref"], str)
        or re.fullmatch(r"lev_[0-9a-f]{64}", value["life_evidence_ref"])
        is None
    ):
        raise ValueError("composition continuation life evidence is invalid")
    if value["artifact_intent_id"] is not None:
        _require_nonempty(
            value["artifact_intent_id"],
            label="continuation artifact intent id",
        )
    for field in (
        "conversation_scope_hash",
        "life_snapshot_hash",
        "parent_ticket_payload_sha256",
        "resource_envelope_sha256",
        "side_effect_envelope_sha256",
    ):
        _require_sha256(value[field], label=f"continuation {field}")
    for field, minimum, maximum in (
        ("life_snapshot_revision", 1, 2**63 - 1),
        ("max_output_bytes", 0, 2_147_483_648),
        ("max_runtime_ms", 1, 3_600_000),
        ("max_tool_calls", 1, 10_000),
    ):
        field_value = value[field]
        if (
            not isinstance(field_value, int)
            or isinstance(field_value, bool)
            or not minimum <= field_value <= maximum
        ):
            raise ValueError(
                f"composition continuation {field} is invalid"
            )
    resources = {
        "max_output_bytes": value["max_output_bytes"],
        "max_runtime_ms": value["max_runtime_ms"],
        "max_tool_calls": value["max_tool_calls"],
    }
    if value["resource_envelope_sha256"] != canonical_sha256(resources):
        raise ValueError("composition continuation resource envelope is invalid")
    side_effects = value["allowed_side_effects"]
    if (
        not isinstance(side_effects, list)
        or tuple(side_effects) != tuple(sorted(set(side_effects)))
        # This envelope describes the parent authority captured for later
        # re-issuance; it is inert and therefore may faithfully preserve a
        # non-A0 parent ticket.  Each materialized child remains independently
        # constrained to the A0-only envelope below.
        or not set(side_effects).issubset(_VALID_SIDE_EFFECTS)
        or value["side_effect_envelope_sha256"]
        != canonical_sha256({"allowed_side_effects": side_effects})
    ):
        raise ValueError("composition continuation side-effect envelope is invalid")
    return value


def build_composition_continuation_issuance_context(
    parent_ticket: ExecutionTicket,
    *,
    life_id: str,
    life_evidence_ref: str,
    session_id: str,
) -> dict[str, Any]:
    """Project only non-executable reissuance metadata from a live parent.

    The caller supplies the three ``ActiveExecutionAuthority`` values that are
    not part of the signed parent payload.  The returned canonical JSON value
    contains no signature or nonce and is revalidated again by the Store.
    """

    if not isinstance(parent_ticket, ExecutionTicket):
        raise TypeError("composition continuation parent ticket is invalid")
    payload = parent_ticket.payload
    return _validate_continuation_issuance_context(
        _json_value(
            {
                "channel": payload.channel,
                "tenant_id": payload.tenant_id,
                "link_account_id": payload.link_account_id,
                "conversation_scope_hash": payload.conversation_scope_hash,
                "life_id": life_id,
                "life_evidence_ref": life_evidence_ref,
                "session_id": session_id,
                "life_snapshot_revision": payload.life_snapshot_revision,
                "life_snapshot_hash": payload.life_snapshot_hash,
                "output_root_id": payload.output_root_id,
                "artifact_intent_id": payload.artifact_intent_id,
                "parent_ticket_payload_sha256": canonical_sha256(
                    payload.model_dump(mode="json")
                ),
                "max_output_bytes": payload.max_output_bytes,
                "max_runtime_ms": payload.max_runtime_ms,
                "max_tool_calls": payload.max_tool_calls,
                "resource_envelope_sha256": payload.resource_envelope_sha256,
                "allowed_side_effects": list(payload.allowed_side_effects),
                "side_effect_envelope_sha256": (
                    payload.side_effect_envelope_sha256
                ),
            }
        )
    )


@dataclass(frozen=True, slots=True)
class CompositionContinuationDelegation:
    """Durable, deliberately non-executable evidence for later issuance.

    The record carries no nonce, signature, Ticket, Grant, or dispatch right.
    Its only consumer is the existing policy issuer, which must re-read this
    evidence and issue a fresh current-epoch authorization.
    """

    delegation_id: str
    registration_id: str
    registration_sha256: str
    executable_plan_id: str
    executable_plan_sha256: str
    request_id: str
    run_id: str
    generation: int
    principal_scope_hash: str
    parent_ticket_id: str
    parent_ticket_sha256: str
    parent_ticket_expires_at_ms: int
    parent_effect_id: str
    parent_effect_claim_sha256: str
    source_manifest_sha256: str
    capability_manifest_sha256: str
    action_registry_sha256: str
    schema_catalog_sha256: str
    composition_execution_manifest_sha256: str
    component_manifest_sha256: str
    verification_plan_id: str
    verification_plan_sha256: str
    verification_plan_activation_id: str
    workspace_id: str
    workspace_scope_sha256: str
    object_grants_sha256: str
    issuance_context_json: str
    issuance_context_sha256: str
    allowed_action_versions_json: str
    allowed_action_versions_sha256: str
    issued_at_ms: int
    expires_at_ms: int
    delegation_sha256: str
    schema_version: str = COMPOSITION_CONTINUATION_DELEGATION_SCHEMA
    record_type: str = "NON_EXECUTABLE_CONTINUATION"
    executable: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != COMPOSITION_CONTINUATION_DELEGATION_SCHEMA:
            raise ValueError("composition continuation schema is invalid")
        if self.record_type != "NON_EXECUTABLE_CONTINUATION" or self.executable:
            raise ValueError("composition continuation must remain non-executable")
        for label, value in (
            ("delegation id", self.delegation_id),
            ("registration id", self.registration_id),
            ("executable plan id", self.executable_plan_id),
            ("request id", self.request_id),
            ("run id", self.run_id),
            ("parent ticket id", self.parent_ticket_id),
            ("verification plan id", self.verification_plan_id),
            ("verification plan activation id", self.verification_plan_activation_id),
            ("workspace id", self.workspace_id),
        ):
            _require_nonempty(value, label=label)
        if (
            not self.delegation_id.startswith("ccd_")
            or len(self.delegation_id) != 68
            or _SHA256.fullmatch(self.delegation_id[4:]) is None
        ):
            raise ValueError("composition continuation identity is invalid")
        if (
            not self.parent_effect_id.startswith("eff_")
            or len(self.parent_effect_id) != 68
            or _SHA256.fullmatch(self.parent_effect_id[4:]) is None
        ):
            raise ValueError("composition continuation parent Effect is invalid")
        for label, value in (
            ("registration", self.registration_sha256),
            ("executable plan", self.executable_plan_sha256),
            ("principal scope", self.principal_scope_hash),
            ("parent ticket", self.parent_ticket_sha256),
            ("parent Effect claim", self.parent_effect_claim_sha256),
            ("source manifest", self.source_manifest_sha256),
            ("capability manifest", self.capability_manifest_sha256),
            ("action registry", self.action_registry_sha256),
            ("schema catalog", self.schema_catalog_sha256),
            ("composition execution manifest", self.composition_execution_manifest_sha256),
            ("component manifest", self.component_manifest_sha256),
            ("verification plan", self.verification_plan_sha256),
            ("workspace scope", self.workspace_scope_sha256),
            ("object grants", self.object_grants_sha256),
            ("issuance context", self.issuance_context_sha256),
            ("allowed action versions", self.allowed_action_versions_sha256),
            ("delegation", self.delegation_sha256),
        ):
            _require_sha256(value, label=label)
        allowed = _parse_canonical_json(
            self.allowed_action_versions_json,
            label="allowed action versions",
            expected_type=list,
        )
        issuance_context = _validate_continuation_issuance_context(
            _parse_canonical_json(
                self.issuance_context_json,
                label="composition continuation issuance context",
                expected_type=dict,
            )
        )
        if self.issuance_context_sha256 != canonical_sha256(issuance_context):
            raise ValueError("composition continuation issuance context digest is invalid")
        if (
            any(not isinstance(item, dict) for item in allowed)
            or self.allowed_action_versions_sha256 != canonical_sha256(allowed)
        ):
            raise ValueError("composition continuation actions are invalid")
        if (
            not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 0
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in (
                    self.parent_ticket_expires_at_ms,
                    self.issued_at_ms,
                    self.expires_at_ms,
                )
            )
            or not self.issued_at_ms < self.expires_at_ms
        ):
            raise ValueError("composition continuation counters are invalid")
        if self.delegation_id != derive_composition_continuation_delegation_id(
            self.identity_payload()
        ):
            raise ValueError("composition continuation ID is not canonical")
        if self.delegation_sha256 not in {_ZERO_SHA256, self.computed_sha256()}:
            raise ValueError("composition continuation digest is invalid")

    @property
    def allowed_action_versions(self) -> list[dict[str, Any]]:
        return deepcopy(json.loads(self.allowed_action_versions_json))

    @property
    def issuance_context(self) -> dict[str, Any]:
        """Detached non-authorizing metadata used for fresh Policy issuance."""

        return deepcopy(json.loads(self.issuance_context_json))

    def identity_payload(self) -> dict[str, Any]:
        return {
            "registration_id": self.registration_id,
            "executable_plan_id": self.executable_plan_id,
            "executable_plan_sha256": self.executable_plan_sha256,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "parent_ticket_id": self.parent_ticket_id,
            "parent_ticket_sha256": self.parent_ticket_sha256,
            "parent_effect_id": self.parent_effect_id,
            "parent_effect_claim_sha256": self.parent_effect_claim_sha256,
        }

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type,
            "executable": self.executable,
            "delegation_id": self.delegation_id,
            "registration_id": self.registration_id,
            "registration_sha256": self.registration_sha256,
            "executable_plan_id": self.executable_plan_id,
            "executable_plan_sha256": self.executable_plan_sha256,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "principal_scope_hash": self.principal_scope_hash,
            "parent_ticket_id": self.parent_ticket_id,
            "parent_ticket_sha256": self.parent_ticket_sha256,
            "parent_ticket_expires_at_ms": self.parent_ticket_expires_at_ms,
            "parent_effect_id": self.parent_effect_id,
            "parent_effect_claim_sha256": self.parent_effect_claim_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "capability_manifest_sha256": self.capability_manifest_sha256,
            "action_registry_sha256": self.action_registry_sha256,
            "schema_catalog_sha256": self.schema_catalog_sha256,
            "composition_execution_manifest_sha256": self.composition_execution_manifest_sha256,
            "component_manifest_sha256": self.component_manifest_sha256,
            "verification_plan_id": self.verification_plan_id,
            "verification_plan_sha256": self.verification_plan_sha256,
            "verification_plan_activation_id": self.verification_plan_activation_id,
            "workspace_id": self.workspace_id,
            "workspace_scope_sha256": self.workspace_scope_sha256,
            "object_grants_sha256": self.object_grants_sha256,
            "issuance_context": self.issuance_context,
            "issuance_context_sha256": self.issuance_context_sha256,
            "allowed_action_versions": self.allowed_action_versions,
            "allowed_action_versions_sha256": self.allowed_action_versions_sha256,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.delegation_sha256 == self.computed_sha256()

    @property
    def canonical_json(self) -> str:
        return canonical_json_text(
            {**self.payload(), "delegation_sha256": self.delegation_sha256}
        )

    @classmethod
    def build(
        cls,
        *,
        registration_id: str,
        registration_sha256: str,
        executable_plan_id: str,
        executable_plan_sha256: str,
        request_id: str,
        run_id: str,
        generation: int,
        principal_scope_hash: str,
        parent_ticket_id: str,
        parent_ticket_sha256: str,
        parent_ticket_expires_at_ms: int,
        parent_effect_id: str,
        parent_effect_claim_sha256: str,
        source_manifest_sha256: str,
        capability_manifest_sha256: str,
        action_registry_sha256: str,
        schema_catalog_sha256: str,
        composition_execution_manifest_sha256: str,
        component_manifest_sha256: str,
        verification_plan_id: str,
        verification_plan_sha256: str,
        verification_plan_activation_id: str,
        workspace_id: str,
        workspace_scope_sha256: str,
        object_grants_sha256: str,
        issuance_context: Mapping[str, Any],
        allowed_action_versions: Sequence[Mapping[str, Any]],
        issued_at_ms: int,
        expires_at_ms: int,
    ) -> Self:
        allowed = _json_value(list(allowed_action_versions))
        context = _validate_continuation_issuance_context(
            _json_value(dict(issuance_context))
        )
        identity = {
            "registration_id": registration_id,
            "executable_plan_id": executable_plan_id,
            "executable_plan_sha256": executable_plan_sha256,
            "request_id": request_id,
            "run_id": run_id,
            "generation": generation,
            "parent_ticket_id": parent_ticket_id,
            "parent_ticket_sha256": parent_ticket_sha256,
            "parent_effect_id": parent_effect_id,
            "parent_effect_claim_sha256": parent_effect_claim_sha256,
        }
        draft = cls(
            delegation_id=derive_composition_continuation_delegation_id(identity),
            registration_id=registration_id,
            registration_sha256=registration_sha256,
            executable_plan_id=executable_plan_id,
            executable_plan_sha256=executable_plan_sha256,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            principal_scope_hash=principal_scope_hash,
            parent_ticket_id=parent_ticket_id,
            parent_ticket_sha256=parent_ticket_sha256,
            parent_ticket_expires_at_ms=parent_ticket_expires_at_ms,
            parent_effect_id=parent_effect_id,
            parent_effect_claim_sha256=parent_effect_claim_sha256,
            source_manifest_sha256=source_manifest_sha256,
            capability_manifest_sha256=capability_manifest_sha256,
            action_registry_sha256=action_registry_sha256,
            schema_catalog_sha256=schema_catalog_sha256,
            composition_execution_manifest_sha256=composition_execution_manifest_sha256,
            component_manifest_sha256=component_manifest_sha256,
            verification_plan_id=verification_plan_id,
            verification_plan_sha256=verification_plan_sha256,
            verification_plan_activation_id=verification_plan_activation_id,
            workspace_id=workspace_id,
            workspace_scope_sha256=workspace_scope_sha256,
            object_grants_sha256=object_grants_sha256,
            issuance_context_json=canonical_json_text(context),
            issuance_context_sha256=canonical_sha256(context),
            allowed_action_versions_json=canonical_json_text(allowed),
            allowed_action_versions_sha256=canonical_sha256(allowed),
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            delegation_sha256=_ZERO_SHA256,
        )
        return replace(draft, delegation_sha256=draft.computed_sha256())


def derive_composition_continuation_delegation_id(
    identity: Mapping[str, Any],
) -> str:
    return "ccd_" + canonical_sha256(
        {
            "domain": "tiangong.composition-continuation-delegation.v1.id",
            **dict(identity),
        }
    )


def continuation_delegation_from_row(row: Any) -> CompositionContinuationDelegation:
    payload = _parse_canonical_json(
        row["delegation_json"],
        label="composition continuation delegation",
        expected_type=dict,
    )
    try:
        delegation = CompositionContinuationDelegation(
            delegation_id=payload["delegation_id"],
            registration_id=payload["registration_id"],
            registration_sha256=payload["registration_sha256"],
            executable_plan_id=payload["executable_plan_id"],
            executable_plan_sha256=payload["executable_plan_sha256"],
            request_id=payload["request_id"],
            run_id=payload["run_id"],
            generation=payload["generation"],
            principal_scope_hash=payload["principal_scope_hash"],
            parent_ticket_id=payload["parent_ticket_id"],
            parent_ticket_sha256=payload["parent_ticket_sha256"],
            parent_ticket_expires_at_ms=payload["parent_ticket_expires_at_ms"],
            parent_effect_id=payload["parent_effect_id"],
            parent_effect_claim_sha256=payload["parent_effect_claim_sha256"],
            source_manifest_sha256=payload["source_manifest_sha256"],
            capability_manifest_sha256=payload["capability_manifest_sha256"],
            action_registry_sha256=payload["action_registry_sha256"],
            schema_catalog_sha256=payload["schema_catalog_sha256"],
            composition_execution_manifest_sha256=payload[
                "composition_execution_manifest_sha256"
            ],
            component_manifest_sha256=payload["component_manifest_sha256"],
            verification_plan_id=payload["verification_plan_id"],
            verification_plan_sha256=payload["verification_plan_sha256"],
            verification_plan_activation_id=payload[
                "verification_plan_activation_id"
            ],
            workspace_id=payload["workspace_id"],
            workspace_scope_sha256=payload["workspace_scope_sha256"],
            object_grants_sha256=payload["object_grants_sha256"],
            issuance_context_json=canonical_json_text(
                payload["issuance_context"]
            ),
            issuance_context_sha256=payload["issuance_context_sha256"],
            allowed_action_versions_json=canonical_json_text(
                payload["allowed_action_versions"]
            ),
            allowed_action_versions_sha256=payload[
                "allowed_action_versions_sha256"
            ],
            issued_at_ms=payload["issued_at_ms"],
            expires_at_ms=payload["expires_at_ms"],
            delegation_sha256=payload["delegation_sha256"],
            schema_version=payload["schema_version"],
            record_type=payload["record_type"],
            executable=payload["executable"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("stored composition continuation is invalid") from exc
    expected = {
        "delegation_id": delegation.delegation_id,
        "registration_id": delegation.registration_id,
        "executable_plan_id": delegation.executable_plan_id,
        "request_id": delegation.request_id,
        "run_id": delegation.run_id,
        "generation": delegation.generation,
        "parent_effect_id": delegation.parent_effect_id,
        "delegation_sha256": delegation.delegation_sha256,
        "issued_at_ms": delegation.issued_at_ms,
        "expires_at_ms": delegation.expires_at_ms,
    }
    if any(row[name] != value for name, value in expected.items()):
        raise ValueError("composition continuation columns disagree with payload")
    if delegation.canonical_json != row["delegation_json"]:
        raise ValueError("stored composition continuation JSON is not canonical")
    return delegation


@dataclass(frozen=True, slots=True)
class CompositionStepAuthorizationRequest:
    """Stable logical authorization request plus non-stable issuance ceiling.

    ``authorization_request_sha256`` deliberately excludes every clock value,
    nonce, signature, and signed artifact.  A retry for the same immutable
    plan step therefore reaches the same Store key even after a process restart.
    The clock values remain bound by the final immutable record digest.
    """

    registration_id: str
    registration_sha256: str
    executable_plan_id: str
    executable_plan_sha256: str
    composition_plan_id: str
    composition_plan_sha256: str
    request_id: str
    run_id: str
    generation: int
    principal_scope_hash: str
    parent_ticket_id: str
    parent_ticket_sha256: str
    parent_ticket_expires_at_ms: int
    step_id: str
    step_binding_sha256: str
    attempt: int
    action_id: str
    action_version: str
    source_revision_sha256: str
    action_registry_sha256: str
    action_permission_sha256: str
    argument_schema_sha256: str
    result_schema_sha256: str
    composition_binding_sha256: str
    materialized_arguments_json: str
    arguments_sha256: str
    target: str
    target_ref: str | None
    target_snapshot_json: str
    target_snapshot_sha256: str | None
    workspace_id: str
    workspace_scope_sha256: str
    object_grants_json: str
    object_grants_sha256: str
    prebound_effect_id: str
    prebound_effect_intent_sha256: str
    action_fence_epoch: int
    action_fence_sha256: str
    issued_at_ms: int
    expires_at_ms: int
    authorization_ceiling_ms: int
    authorization_request_sha256: str
    schema_version: str = COMPOSITION_STEP_AUTHORIZATION_SCHEMA
    continuation_delegation_id: str | None = None
    continuation_delegation_sha256: str | None = None
    dependency_evidence_json: str | None = None
    dependency_evidence_sha256: str | None = None
    supersedes_authorization_id: str | None = None
    supersedes_effect_id: str | None = None
    supersedes_claim_sha256: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("registration id", self.registration_id),
            ("executable plan id", self.executable_plan_id),
            ("composition plan id", self.composition_plan_id),
            ("request id", self.request_id),
            ("run id", self.run_id),
            ("principal action step id", self.step_id),
            ("parent ticket id", self.parent_ticket_id),
            ("action id", self.action_id),
            ("action version", self.action_version),
            ("workspace id", self.workspace_id),
        ):
            _require_nonempty(value, label=label)
        for label, value in (
            ("registration", self.registration_sha256),
            ("executable plan", self.executable_plan_sha256),
            ("composition plan", self.composition_plan_sha256),
            ("principal scope", self.principal_scope_hash),
            ("parent ticket", self.parent_ticket_sha256),
            ("step binding", self.step_binding_sha256),
            ("source revision", self.source_revision_sha256),
            ("action registry", self.action_registry_sha256),
            ("action permission", self.action_permission_sha256),
            ("argument schema", self.argument_schema_sha256),
            ("result schema", self.result_schema_sha256),
            ("composition binding", self.composition_binding_sha256),
            ("arguments", self.arguments_sha256),
            ("workspace scope", self.workspace_scope_sha256),
            ("object grants", self.object_grants_sha256),
            ("effect intent", self.prebound_effect_intent_sha256),
            ("action fence", self.action_fence_sha256),
            ("authorization request", self.authorization_request_sha256),
        ):
            _require_sha256(value, label=label)
        if self.target_snapshot_sha256 is not None:
            _require_sha256(
                self.target_snapshot_sha256, label="target snapshot"
            )
        if self.schema_version not in {
            COMPOSITION_STEP_AUTHORIZATION_SCHEMA,
            COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2,
        }:
            raise ValueError("composition authorization schema is invalid")
        if (
            not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 0
            or not isinstance(self.attempt, int)
            or isinstance(self.attempt, bool)
            or self.attempt < 1
            or not isinstance(self.action_fence_epoch, int)
            or isinstance(self.action_fence_epoch, bool)
            or self.action_fence_epoch < 0
        ):
            raise ValueError("composition authorization counters are invalid")
        if (
            not self.prebound_effect_id.startswith("eff_")
            or len(self.prebound_effect_id) != 68
            or _SHA256.fullmatch(self.prebound_effect_id[4:]) is None
        ):
            raise ValueError("prebound effect identity is invalid")
        if self.target_ref is not None:
            _require_nonempty(self.target_ref, label="target ref")
        args = _parse_canonical_json(
            self.materialized_arguments_json,
            label="materialized arguments",
            expected_type=dict,
        )
        target_snapshot = _parse_canonical_json(
            self.target_snapshot_json,
            label="target snapshot",
        )
        object_grants = _parse_canonical_json(
            self.object_grants_json,
            label="object grants",
            expected_type=list,
        )
        if any(not isinstance(item, dict) for item in object_grants):
            raise ValueError("object grants must contain only JSON objects")
        if self.arguments_sha256 != canonical_sha256(
            {"action": self.action_id, "args": args, "target": self.target}
        ):
            raise ValueError("materialized invocation digest is invalid")
        expected_target_snapshot_sha256 = (
            None
            if target_snapshot is None
            else canonical_sha256(target_snapshot)
        )
        if self.target_snapshot_sha256 != expected_target_snapshot_sha256:
            raise ValueError("target snapshot digest is invalid")
        if self.object_grants_sha256 != canonical_sha256(object_grants):
            raise ValueError("object grants digest is invalid")
        continuation = (
            self.continuation_delegation_id,
            self.continuation_delegation_sha256,
            self.dependency_evidence_json,
            self.dependency_evidence_sha256,
        )
        predecessor = (
            self.supersedes_authorization_id,
            self.supersedes_effect_id,
            self.supersedes_claim_sha256,
        )
        if self.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA:
            if self.attempt != 1 or any(
                value is not None for value in (*continuation, *predecessor)
            ):
                raise ValueError(
                    "v1 composition authorization cannot carry continuation fields"
                )
        else:
            if any(value is None for value in continuation):
                raise ValueError(
                    "v2 composition authorization requires continuation evidence"
                )
            _require_nonempty(
                self.continuation_delegation_id,
                label="continuation delegation id",
            )
            _require_sha256(
                self.continuation_delegation_sha256,
                label="continuation delegation",
            )
            dependency_evidence = _parse_canonical_json(
                self.dependency_evidence_json,
                label="composition dependency evidence",
                expected_type=list,
            )
            if any(not isinstance(item, dict) for item in dependency_evidence):
                raise ValueError(
                    "composition dependency evidence must contain JSON objects"
                )
            if self.dependency_evidence_sha256 != canonical_sha256(
                dependency_evidence
            ):
                raise ValueError("composition dependency evidence digest is invalid")
            if self.attempt == 1:
                if any(value is not None for value in predecessor):
                    raise ValueError(
                        "first composition attempt cannot supersede another authorization"
                    )
            else:
                if any(value is None for value in predecessor):
                    raise ValueError(
                        "later composition attempt requires a complete predecessor"
                    )
                _require_nonempty(
                    self.supersedes_authorization_id,
                    label="superseded authorization id",
                )
                if (
                    not self.supersedes_effect_id.startswith("eff_")
                    or len(self.supersedes_effect_id) != 68
                    or _SHA256.fullmatch(self.supersedes_effect_id[4:]) is None
                ):
                    raise ValueError("superseded Effect identity is invalid")
                _require_sha256(
                    self.supersedes_claim_sha256,
                    label="superseded Effect claim",
                )
        if self.action_fence_sha256 != canonical_sha256(
            {
                "domain": "tiangong.gateway.action-fence-binding.v1",
                "action_fence_epoch": self.action_fence_epoch,
            }
        ):
            raise ValueError("action fence digest is invalid")
        times = (
            self.issued_at_ms,
            self.expires_at_ms,
            self.authorization_ceiling_ms,
            self.parent_ticket_expires_at_ms,
        )
        lifetime_is_valid = not any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in times
        ) and (
            self.issued_at_ms
            < self.expires_at_ms
            <= self.authorization_ceiling_ms
        )
        if self.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA:
            lifetime_is_valid = (
                lifetime_is_valid
                and self.authorization_ceiling_ms
                <= self.parent_ticket_expires_at_ms
            )
        if not lifetime_is_valid:
            raise ValueError("composition authorization lifetime is invalid")

    @property
    def materialized_arguments(self) -> dict[str, Any]:
        return deepcopy(json.loads(self.materialized_arguments_json))

    @property
    def target_snapshot(self) -> Any:
        return deepcopy(json.loads(self.target_snapshot_json))

    @property
    def object_grants(self) -> list[dict[str, Any]]:
        return deepcopy(json.loads(self.object_grants_json))

    @property
    def dependency_evidence(self) -> list[dict[str, Any]] | None:
        if self.dependency_evidence_json is None:
            return None
        return deepcopy(json.loads(self.dependency_evidence_json))

    def stable_payload(self) -> dict[str, Any]:
        """Payload hashed for restart-stable logical request identity."""

        payload = {
            "schema_version": self.schema_version,
            "registration_id": self.registration_id,
            "registration_sha256": self.registration_sha256,
            "executable_plan_id": self.executable_plan_id,
            "executable_plan_sha256": self.executable_plan_sha256,
            "composition_plan_id": self.composition_plan_id,
            "composition_plan_sha256": self.composition_plan_sha256,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "principal_scope_hash": self.principal_scope_hash,
            "parent_ticket_id": self.parent_ticket_id,
            "parent_ticket_sha256": self.parent_ticket_sha256,
            "step_id": self.step_id,
            "step_binding_sha256": self.step_binding_sha256,
            "attempt": self.attempt,
            "action_id": self.action_id,
            "action_version": self.action_version,
            "source_revision_sha256": self.source_revision_sha256,
            "action_registry_sha256": self.action_registry_sha256,
            "action_permission_sha256": self.action_permission_sha256,
            "argument_schema_sha256": self.argument_schema_sha256,
            "result_schema_sha256": self.result_schema_sha256,
            "composition_binding_sha256": self.composition_binding_sha256,
            "materialized_arguments": self.materialized_arguments,
            "arguments_sha256": self.arguments_sha256,
            "target": self.target,
            "target_ref": self.target_ref,
            "target_snapshot": self.target_snapshot,
            "target_snapshot_sha256": self.target_snapshot_sha256,
            "workspace_id": self.workspace_id,
            "workspace_scope_sha256": self.workspace_scope_sha256,
            "object_grants": self.object_grants,
            "object_grants_sha256": self.object_grants_sha256,
            "prebound_effect_id": self.prebound_effect_id,
            "prebound_effect_intent_sha256": (
                self.prebound_effect_intent_sha256
            ),
            "action_fence_epoch": self.action_fence_epoch,
            "action_fence_sha256": self.action_fence_sha256,
        }
        if self.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2:
            payload.update(
                {
                    "continuation_delegation_id": self.continuation_delegation_id,
                    "continuation_delegation_sha256": self.continuation_delegation_sha256,
                    "dependency_evidence": self.dependency_evidence,
                    "dependency_evidence_sha256": self.dependency_evidence_sha256,
                    "supersedes_authorization_id": self.supersedes_authorization_id,
                    "supersedes_effect_id": self.supersedes_effect_id,
                    "supersedes_claim_sha256": self.supersedes_claim_sha256,
                }
            )
        return payload

    def computed_sha256(self) -> str:
        return canonical_sha256(self.stable_payload())

    def has_valid_sha256(self) -> bool:
        return self.authorization_request_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return replace(
            self, authorization_request_sha256=self.computed_sha256()
        )

    def stored_payload(self) -> dict[str, Any]:
        return {
            **self.stable_payload(),
            "parent_ticket_expires_at_ms": self.parent_ticket_expires_at_ms,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "authorization_ceiling_ms": self.authorization_ceiling_ms,
            "authorization_request_sha256": (
                self.authorization_request_sha256
            ),
        }

    @property
    def canonical_json(self) -> str:
        return canonical_json_text(self.stored_payload())

    @classmethod
    def build(
        cls,
        *,
        registration_id: str,
        registration_sha256: str,
        executable_plan_id: str,
        executable_plan_sha256: str,
        composition_plan_id: str,
        composition_plan_sha256: str,
        request_id: str,
        run_id: str,
        generation: int,
        principal_scope_hash: str,
        parent_ticket_id: str,
        parent_ticket_sha256: str,
        parent_ticket_expires_at_ms: int,
        step_id: str,
        step_binding_sha256: str,
        attempt: int,
        action_id: str,
        action_version: str,
        source_revision_sha256: str,
        action_registry_sha256: str,
        action_permission_sha256: str,
        argument_schema_sha256: str,
        result_schema_sha256: str,
        composition_binding_sha256: str,
        materialized_arguments: Mapping[str, Any],
        target: str,
        target_ref: str | None,
        target_snapshot: Any,
        workspace_id: str,
        workspace_scope_sha256: str,
        object_grants: Sequence[Mapping[str, Any]],
        prebound_effect_id: str,
        prebound_effect_intent_sha256: str,
        action_fence_epoch: int,
        issued_at_ms: int,
        expires_at_ms: int,
        authorization_ceiling_ms: int,
        schema_version: str = COMPOSITION_STEP_AUTHORIZATION_SCHEMA,
        continuation_delegation_id: str | None = None,
        continuation_delegation_sha256: str | None = None,
        dependency_evidence: Sequence[Mapping[str, Any]] | None = None,
        supersedes_authorization_id: str | None = None,
        supersedes_effect_id: str | None = None,
        supersedes_claim_sha256: str | None = None,
    ) -> Self:
        arguments_value = _json_value(dict(materialized_arguments))
        snapshot_value = _json_value(target_snapshot)
        grants_value = _json_value(list(object_grants))
        dependency_value = (
            None
            if dependency_evidence is None
            else _json_value(list(dependency_evidence))
        )
        arguments_sha256 = canonical_sha256(
            {"action": action_id, "args": arguments_value, "target": target}
        )
        fence_sha256 = canonical_sha256(
            {
                "domain": "tiangong.gateway.action-fence-binding.v1",
                "action_fence_epoch": action_fence_epoch,
            }
        )
        request = cls(
            registration_id=registration_id,
            registration_sha256=registration_sha256,
            executable_plan_id=executable_plan_id,
            executable_plan_sha256=executable_plan_sha256,
            composition_plan_id=composition_plan_id,
            composition_plan_sha256=composition_plan_sha256,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            principal_scope_hash=principal_scope_hash,
            parent_ticket_id=parent_ticket_id,
            parent_ticket_sha256=parent_ticket_sha256,
            parent_ticket_expires_at_ms=parent_ticket_expires_at_ms,
            step_id=step_id,
            step_binding_sha256=step_binding_sha256,
            attempt=attempt,
            action_id=action_id,
            action_version=action_version,
            source_revision_sha256=source_revision_sha256,
            action_registry_sha256=action_registry_sha256,
            action_permission_sha256=action_permission_sha256,
            argument_schema_sha256=argument_schema_sha256,
            result_schema_sha256=result_schema_sha256,
            composition_binding_sha256=composition_binding_sha256,
            materialized_arguments_json=canonical_json_text(arguments_value),
            arguments_sha256=arguments_sha256,
            target=target,
            target_ref=target_ref,
            target_snapshot_json=canonical_json_text(snapshot_value),
            target_snapshot_sha256=(
                None
                if snapshot_value is None
                else canonical_sha256(snapshot_value)
            ),
            workspace_id=workspace_id,
            workspace_scope_sha256=workspace_scope_sha256,
            object_grants_json=canonical_json_text(grants_value),
            object_grants_sha256=canonical_sha256(grants_value),
            prebound_effect_id=prebound_effect_id,
            prebound_effect_intent_sha256=prebound_effect_intent_sha256,
            action_fence_epoch=action_fence_epoch,
            action_fence_sha256=fence_sha256,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            authorization_ceiling_ms=authorization_ceiling_ms,
            authorization_request_sha256=_ZERO_SHA256,
            schema_version=schema_version,
            continuation_delegation_id=continuation_delegation_id,
            continuation_delegation_sha256=continuation_delegation_sha256,
            dependency_evidence_json=(
                None
                if dependency_value is None
                else canonical_json_text(dependency_value)
            ),
            dependency_evidence_sha256=(
                None
                if dependency_value is None
                else canonical_sha256(dependency_value)
            ),
            supersedes_authorization_id=supersedes_authorization_id,
            supersedes_effect_id=supersedes_effect_id,
            supersedes_claim_sha256=supersedes_claim_sha256,
        )
        return request.with_computed_sha256()


@dataclass(frozen=True, slots=True)
class CompositionStepAuthorizationArtifacts:
    intent_json: str
    impact_json: str
    decision_json: str
    signed_ticket_json: str
    signed_grant_json: str
    runtime_response_json: str

    def __post_init__(self) -> None:
        for label, value in (
            ("intent", self.intent_json),
            ("impact", self.impact_json),
            ("decision", self.decision_json),
            ("signed ticket", self.signed_ticket_json),
            ("signed grant", self.signed_grant_json),
            ("runtime response", self.runtime_response_json),
        ):
            _parse_canonical_json(
                value, label=label, expected_type=dict
            )

    @classmethod
    def build(
        cls,
        *,
        intent: Mapping[str, Any] | Any,
        impact: Mapping[str, Any] | Any,
        decision: Mapping[str, Any] | Any,
        signed_ticket: Mapping[str, Any] | Any,
        signed_grant: Mapping[str, Any] | Any,
        runtime_response: Mapping[str, Any],
    ) -> Self:
        return cls(
            intent_json=canonical_json_text(intent),
            impact_json=canonical_json_text(impact),
            decision_json=canonical_json_text(decision),
            signed_ticket_json=canonical_json_text(signed_ticket),
            signed_grant_json=canonical_json_text(signed_grant),
            runtime_response_json=canonical_json_text(runtime_response),
        )

    @property
    def intent(self) -> dict[str, Any]:
        return deepcopy(json.loads(self.intent_json))

    @property
    def impact(self) -> dict[str, Any]:
        return deepcopy(json.loads(self.impact_json))

    @property
    def decision(self) -> dict[str, Any]:
        return deepcopy(json.loads(self.decision_json))

    @property
    def signed_ticket(self) -> dict[str, Any]:
        return deepcopy(json.loads(self.signed_ticket_json))

    @property
    def signed_grant(self) -> dict[str, Any]:
        return deepcopy(json.loads(self.signed_grant_json))

    @property
    def runtime_response(self) -> dict[str, Any]:
        return deepcopy(json.loads(self.runtime_response_json))

    def restore_contracts(
        self,
    ) -> tuple[
        ActionIntent,
        ActionImpact,
        PolicyDecision,
        ExecutionTicket,
        OmniCapabilityGrant,
    ]:
        """Reconstruct every persisted signed-chain contract exactly."""

        return (
            _restore_contract(ActionIntent, self.intent_json, label="intent"),
            _restore_contract(ActionImpact, self.impact_json, label="impact"),
            _restore_contract(
                PolicyDecision, self.decision_json, label="decision"
            ),
            _restore_contract(
                ExecutionTicket, self.signed_ticket_json, label="signed ticket"
            ),
            _restore_contract(
                OmniCapabilityGrant,
                self.signed_grant_json,
                label="signed grant",
            ),
        )

    def validate_for_request(
        self, request: CompositionStepAuthorizationRequest
    ) -> dict[str, Any]:
        """Cross-check already-authorized artifacts without re-deciding Policy."""

        if not request.has_valid_sha256():
            raise ValueError("composition authorization request digest is invalid")
        restored = self.restore_contracts()
        intent, impact, decision, ticket, grant = (
            item.model_dump(mode="json") for item in restored
        )
        response = self.runtime_response
        ticket_payload = ticket.get("payload")
        grant_payload = grant.get("payload")
        if not isinstance(ticket_payload, dict) or not isinstance(
            grant_payload, dict
        ):
            raise ValueError("signed authorization payload is invalid")

        intent_sha256 = _self_hash(
            intent, "intent_sha256", label="intent"
        )
        impact_sha256 = _self_hash(
            impact, "impact_sha256", label="impact"
        )
        decision_sha256 = _self_hash(
            decision, "decision_sha256", label="decision"
        )
        ticket_payload_sha256 = canonical_sha256(ticket_payload)
        grant_payload_sha256 = canonical_sha256(grant_payload)
        intent_binding = _composition_binding(intent, label="intent")
        decision_binding = _composition_binding(decision, label="decision")
        ticket_binding = _composition_binding(ticket_payload, label="ticket")
        grant_binding = _composition_binding(grant_payload, label="grant")
        if not (
            intent_binding
            == decision_binding
            == ticket_binding
            == grant_binding
        ):
            raise ValueError("signed composition execution bindings diverged")
        expected_binding = {
            "schema_version": "tiangong.composition-execution-binding.v1",
            "executable_plan_id": request.executable_plan_id,
            "executable_plan_sha256": request.executable_plan_sha256,
            "step_id": request.step_id,
            "step_binding_sha256": request.step_binding_sha256,
            "request_id": request.request_id,
            "run_id": request.run_id,
            "generation": request.generation,
            "effect_id": request.prebound_effect_id,
            "action_id": request.action_id,
            "action_version": request.action_version,
            "materialized_arguments_sha256": canonical_sha256(
                request.materialized_arguments
            ),
            "target_sha256": canonical_sha256(request.target),
            "target_snapshot_sha256": request.target_snapshot_sha256,
            "workspace_id": request.workspace_id,
            "workspace_scope_hash": request.workspace_scope_sha256,
        }
        if request.schema_version == COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2:
            expected_binding.update(
                {
                    "attempt": request.attempt,
                    "continuation_delegation_id": (
                        request.continuation_delegation_id
                    ),
                    "continuation_delegation_sha256": (
                        request.continuation_delegation_sha256
                    ),
                    "dependency_evidence_sha256": (
                        request.dependency_evidence_sha256
                    ),
                    "supersedes_authorization_id": (
                        request.supersedes_authorization_id
                    ),
                    "supersedes_effect_id": request.supersedes_effect_id,
                    "supersedes_claim_sha256": request.supersedes_claim_sha256,
                }
            )
        if (
            intent_binding.get("binding_sha256")
            != request.composition_binding_sha256
            or any(
                intent_binding.get(field) != value
                for field, value in expected_binding.items()
            )
        ):
            raise ValueError("composition execution binding crossed the request")

        expected_scope = (
            request.request_id,
            request.run_id,
            request.generation,
            request.principal_scope_hash,
        )
        for label, document in (
            ("intent", intent),
            ("ticket", ticket_payload),
            ("grant", grant_payload),
        ):
            actual_scope = (
                document.get("request_id"),
                document.get("run_id"),
                document.get("generation"),
                document.get("principal_scope_hash"),
            )
            if actual_scope != expected_scope:
                raise ValueError(f"{label} crossed authorization scope")
        if (
            intent.get("action_id") != request.action_id
            or intent.get("action_version") != request.action_version
            or intent.get("arguments_sha256") != request.arguments_sha256
            or intent.get("payload_sha256")
            != canonical_sha256(request.materialized_arguments)
            or intent_binding.get("canonical_invocation_sha256")
            != intent.get("canonical_invocation_sha256")
            or intent.get("workspace_id") != request.workspace_id
            or intent.get("workspace_scope_hash")
            != request.workspace_scope_sha256
            or intent.get("target_ref") != request.target_ref
            or intent.get("target_snapshot_sha256")
            != request.target_snapshot_sha256
            or intent.get("created_at_ms") != request.issued_at_ms
            or intent.get("expires_at_ms") != request.expires_at_ms
        ):
            raise ValueError("intent crossed composition authorization")
        if (
            impact.get("intent_sha256") != intent_sha256
            or impact.get("action_id") != request.action_id
            or impact.get("target_snapshot_sha256")
            != request.target_snapshot_sha256
            or impact.get("dynamic_risk") not in {None, "A0"}
            or impact.get("computed_risk") not in {None, "A0"}
        ):
            raise ValueError("impact crossed composition authorization")
        if (
            decision.get("intent_sha256") != intent_sha256
            or decision.get("impact_id") != impact.get("impact_id")
            or decision.get("impact_sha256") != impact_sha256
            or decision.get("action_permission_sha256")
            != request.action_permission_sha256
            or decision.get("action_registry_sha256")
            != request.action_registry_sha256
            or decision.get("computed_risk") != "A0"
            or decision.get("outcome") != "ALLOW"
        ):
            raise ValueError("policy decision crossed composition authorization")

        if (
            ticket_payload.get("request_id") != request.request_id
            or ticket_payload.get("run_id") != request.run_id
            or ticket_payload.get("generation") != request.generation
            or ticket_payload.get("effect_id") != request.prebound_effect_id
            or ticket_payload.get("intent_id") != intent.get("intent_id")
            or ticket_payload.get("intent_sha256") != intent_sha256
            or ticket_payload.get("decision_id") != decision.get("decision_id")
            or ticket_payload.get("decision_sha256") != decision_sha256
            or ticket_payload.get("impact_id") != impact.get("impact_id")
            or ticket_payload.get("impact_sha256") != impact_sha256
            or ticket_payload.get("action_permission_sha256")
            != request.action_permission_sha256
            or ticket_payload.get("action_id") != request.action_id
            or ticket_payload.get("action_version") != request.action_version
            or ticket_payload.get("argument_schema_sha256")
            != request.argument_schema_sha256
            or ticket_payload.get("arguments_hash") != request.arguments_sha256
            or ticket_payload.get("workspace_id") != request.workspace_id
            or ticket_payload.get("object_grants_sha256")
            != request.object_grants_sha256
            or ticket_payload.get("risk_class") != "A0"
            or ticket_payload.get("issued_at_ms") != request.issued_at_ms
            or ticket_payload.get("expires_at_ms") != request.expires_at_ms
        ):
            raise ValueError("execution ticket crossed composition authorization")
        ticket_side_effects = ticket_payload.get("allowed_side_effects")
        if not isinstance(ticket_side_effects, list) or not set(
            ticket_side_effects
        ).issubset(_SAFE_A0_SIDE_EFFECTS):
            raise ValueError("execution ticket exceeds the A0 side-effect ceiling")

        if (
            grant_payload.get("ticket_id") != ticket_payload.get("ticket_id")
            or grant_payload.get("ticket_sha256") != ticket_payload_sha256
            or grant_payload.get("effect_id") != request.prebound_effect_id
            or grant_payload.get("decision_id") != decision.get("decision_id")
            or grant_payload.get("decision_sha256") != decision_sha256
            or grant_payload.get("impact_sha256") != impact_sha256
            or grant_payload.get("action_permission_sha256")
            != request.action_permission_sha256
            or grant_payload.get("action_registry_sha256")
            != request.action_registry_sha256
            or grant_payload.get("action_id") != request.action_id
            or grant_payload.get("action_version") != request.action_version
            or grant_payload.get("arguments_sha256") != request.arguments_sha256
            or grant_payload.get("workspace_id") != request.workspace_id
            or grant_payload.get("workspace_scope_hash")
            != request.workspace_scope_sha256
            or grant_payload.get("risk_class") != "A0"
            or grant_payload.get("issued_at_ms") != request.issued_at_ms
            or grant_payload.get("expires_at_ms") != request.expires_at_ms
        ):
            raise ValueError("capability grant crossed composition authorization")
        grant_side_effects = grant_payload.get("allowed_side_effects")
        if not isinstance(grant_side_effects, list) or not set(
            grant_side_effects
        ).issubset(_SAFE_A0_SIDE_EFFECTS):
            raise ValueError("capability grant exceeds the A0 side-effect ceiling")

        runtime = response.get("runtime")
        summary = response.get("decision")
        trust_bundle = None
        if isinstance(runtime, dict) and isinstance(
            runtime.get("trust_bundle"), dict
        ):
            trust_bundle = _restore_contract(
                TrustBundle,
                canonical_json_text(runtime["trust_bundle"]),
                label="runtime trust bundle",
            )
        expected_runtime_keys = {
            "execution_ticket_id",
            "request_id",
            "run_id",
            "generation",
            "effect_id",
            "step_id",
            "executable_plan_id",
            "composition_binding_sha256",
            "composition_execution_binding",
            "principal_scope_hash",
            "workspace_id",
            "action_id",
            "action_version",
            "decision_sha256",
            "impact_sha256",
            "action_permission_sha256",
            "action_registry_sha256",
            "capability_manifest_hash",
            "component_manifest_hash",
            "confirmation_sha256",
            "skill_id",
            "skill_version",
            "skill_sha256",
            "skill_activation_sha256",
            "gateway_url",
            "session_id",
            "fact_kernel_enabled",
            "gateway_epoch",
            "trust_bundle_sha256",
            "trust_bundle",
            "user_path_roots",
        }
        if (
            set(response) != {"status", "grant", "runtime", "decision"}
            or response.get("status") != "OK"
            or response.get("grant") != grant
            or not isinstance(runtime, dict)
            or not isinstance(summary, dict)
            or set(runtime) != expected_runtime_keys
            or set(summary)
            != {"decision_id", "decision_sha256", "risk_class", "reason_codes"}
            or runtime.get("execution_ticket_id")
            != ticket_payload.get("ticket_id")
            or runtime.get("request_id") != request.request_id
            or runtime.get("run_id") != request.run_id
            or runtime.get("generation") != request.generation
            or runtime.get("effect_id") != request.prebound_effect_id
            or runtime.get("step_id") != request.step_id
            or runtime.get("executable_plan_id")
            != request.executable_plan_id
            or runtime.get("principal_scope_hash")
            != request.principal_scope_hash
            or runtime.get("workspace_id") != request.workspace_id
            or runtime.get("action_id") != request.action_id
            or runtime.get("action_version") != request.action_version
            or runtime.get("decision_sha256") != decision_sha256
            or runtime.get("impact_sha256") != impact_sha256
            or runtime.get("action_permission_sha256")
            != request.action_permission_sha256
            or runtime.get("action_registry_sha256")
            != request.action_registry_sha256
            or runtime.get("composition_binding_sha256")
            != request.composition_binding_sha256
            or runtime.get("composition_execution_binding")
            != intent_binding
            or runtime.get("capability_manifest_hash")
            != ticket_payload.get("capability_manifest_hash")
            or runtime.get("component_manifest_hash")
            != ticket_payload.get("component_manifest_hash")
            or runtime.get("gateway_epoch")
            != ticket_payload.get("gateway_epoch")
            or trust_bundle is None
            or not trust_bundle.has_valid_sha256()
            or trust_bundle.production_ready is not True
            or runtime.get("trust_bundle_sha256")
            != trust_bundle.bundle_sha256
            or trust_bundle.gateway_epoch != runtime.get("gateway_epoch")
            or not isinstance(runtime.get("gateway_url"), str)
            or not runtime.get("gateway_url")
            or not isinstance(runtime.get("session_id"), str)
            or not runtime.get("session_id")
            or runtime.get("confirmation_sha256") is not None
            or any(
                runtime.get(field) is not None
                for field in (
                    "skill_id",
                    "skill_version",
                    "skill_sha256",
                    "skill_activation_sha256",
                )
            )
            or runtime.get("fact_kernel_enabled") is not True
            or runtime.get("user_path_roots") != []
            or summary.get("decision_id") != decision.get("decision_id")
            or summary.get("decision_sha256") != decision_sha256
            or summary.get("risk_class") != "A0"
            or summary.get("reason_codes") != decision.get("reason_codes")
        ):
            raise ValueError("runtime response crossed composition authorization")

        for label, value in (
            ("intent id", intent.get("intent_id")),
            ("impact id", impact.get("impact_id")),
            ("decision id", decision.get("decision_id")),
            ("ticket id", ticket_payload.get("ticket_id")),
            ("ticket nonce", ticket_payload.get("nonce")),
            ("grant id", grant_payload.get("grant_id")),
            ("grant nonce", grant_payload.get("nonce")),
        ):
            _require_nonempty(value, label=label)
        return {
            "intent_id": intent["intent_id"],
            "intent_sha256": intent_sha256,
            "intent_json_sha256": canonical_sha256(intent),
            "impact_id": impact["impact_id"],
            "impact_sha256": impact_sha256,
            "impact_json_sha256": canonical_sha256(impact),
            "decision_id": decision["decision_id"],
            "decision_sha256": decision_sha256,
            "decision_json_sha256": canonical_sha256(decision),
            "ticket_id": ticket_payload["ticket_id"],
            "ticket_nonce": ticket_payload["nonce"],
            "ticket_payload_sha256": ticket_payload_sha256,
            "signed_ticket_sha256": canonical_sha256(ticket),
            "grant_id": grant_payload["grant_id"],
            "grant_nonce": grant_payload["nonce"],
            "grant_payload_sha256": grant_payload_sha256,
            "signed_grant_sha256": canonical_sha256(grant),
            "runtime_response_sha256": canonical_sha256(response),
        }


def derive_composition_step_authorization_id(
    request: CompositionStepAuthorizationRequest,
) -> str:
    return "csa_" + canonical_sha256(
        {
            "domain": "tiangong.composition-step-authorization.v1.id",
            "executable_plan_id": request.executable_plan_id,
            "step_id": request.step_id,
            "attempt": request.attempt,
            "authorization_request_sha256": (
                request.authorization_request_sha256
            ),
        }
    )


@dataclass(frozen=True, slots=True)
class CompositionStepAuthorizationStoreRecord:
    authorization_id: str
    request: CompositionStepAuthorizationRequest
    artifacts: CompositionStepAuthorizationArtifacts
    committed_at_ms: int
    authorization_record_sha256: str
    created_by_this_call: bool = False
    duplicate: bool = False
    recovered_after_restart: bool = False

    def __post_init__(self) -> None:
        if self.authorization_id != derive_composition_step_authorization_id(
            self.request
        ):
            raise ValueError("composition authorization identity is invalid")
        if (
            not isinstance(self.committed_at_ms, int)
            or isinstance(self.committed_at_ms, bool)
            or not (
                self.request.issued_at_ms
                <= self.committed_at_ms
                < self.request.expires_at_ms
            )
        ):
            raise ValueError("composition authorization commit time is invalid")
        _require_sha256(
            self.authorization_record_sha256,
            label="composition authorization record",
        )
        if self.created_by_this_call and self.duplicate:
            raise ValueError("composition authorization creation flags disagree")
        if self.recovered_after_restart and (
            self.created_by_this_call or self.duplicate
        ):
            raise ValueError("recovered authorization cannot be newly created")

    @property
    def runtime_response(self) -> dict[str, Any]:
        return self.artifacts.runtime_response

    @property
    def step_id(self) -> str:
        return self.request.step_id

    @property
    def attempt(self) -> int:
        return self.request.attempt

    @property
    def prebound_effect_id(self) -> str:
        return self.request.prebound_effect_id

    @property
    def supersedes_authorization_id(self) -> str | None:
        return self.request.supersedes_authorization_id

    @property
    def supersedes_effect_id(self) -> str | None:
        return self.request.supersedes_effect_id

    @property
    def supersedes_claim_sha256(self) -> str | None:
        return self.request.supersedes_claim_sha256

    @property
    def dependency_evidence(self) -> list[dict[str, Any]] | None:
        return self.request.dependency_evidence

    def record_payload(self) -> dict[str, Any]:
        projections = self.artifacts.validate_for_request(self.request)
        return {
            "schema_version": self.request.schema_version,
            "authorization_id": self.authorization_id,
            "state": "ISSUED",
            "authorization_request": self.request.stored_payload(),
            "intent": self.artifacts.intent,
            "impact": self.artifacts.impact,
            "decision": self.artifacts.decision,
            "signed_ticket": self.artifacts.signed_ticket,
            "signed_grant": self.artifacts.signed_grant,
            "runtime_response": self.artifacts.runtime_response,
            "artifact_projections": projections,
            "committed_at_ms": self.committed_at_ms,
        }

    def computed_record_sha256(self) -> str:
        return canonical_sha256(self.record_payload())

    def has_valid_record_sha256(self) -> bool:
        try:
            return (
                self.request.has_valid_sha256()
                and self.authorization_record_sha256
                == self.computed_record_sha256()
            )
        except (TypeError, ValueError, RecursionError, OverflowError):
            return False

    def as_recovered(self) -> Self:
        return replace(
            self,
            created_by_this_call=False,
            duplicate=False,
            recovered_after_restart=True,
        )


def authorization_record_from_row(
    row: Any,
    *,
    created_by_this_call: bool = False,
    duplicate: bool = False,
    recovered_after_restart: bool = False,
) -> CompositionStepAuthorizationStoreRecord:
    """Strictly parse and cross-check every projection in one v32/v33 row."""

    request_payload = _parse_canonical_json(
        row["authorization_request_json"],
        label="authorization request",
        expected_type=dict,
    )
    try:
        request = CompositionStepAuthorizationRequest(
            registration_id=request_payload["registration_id"],
            registration_sha256=request_payload["registration_sha256"],
            executable_plan_id=request_payload["executable_plan_id"],
            executable_plan_sha256=request_payload["executable_plan_sha256"],
            composition_plan_id=request_payload["composition_plan_id"],
            composition_plan_sha256=request_payload["composition_plan_sha256"],
            request_id=request_payload["request_id"],
            run_id=request_payload["run_id"],
            generation=request_payload["generation"],
            principal_scope_hash=request_payload["principal_scope_hash"],
            parent_ticket_id=request_payload["parent_ticket_id"],
            parent_ticket_sha256=request_payload["parent_ticket_sha256"],
            parent_ticket_expires_at_ms=request_payload[
                "parent_ticket_expires_at_ms"
            ],
            step_id=request_payload["step_id"],
            step_binding_sha256=request_payload["step_binding_sha256"],
            attempt=request_payload["attempt"],
            action_id=request_payload["action_id"],
            action_version=request_payload["action_version"],
            source_revision_sha256=request_payload[
                "source_revision_sha256"
            ],
            action_registry_sha256=request_payload[
                "action_registry_sha256"
            ],
            action_permission_sha256=request_payload[
                "action_permission_sha256"
            ],
            argument_schema_sha256=request_payload[
                "argument_schema_sha256"
            ],
            result_schema_sha256=request_payload["result_schema_sha256"],
            composition_binding_sha256=request_payload[
                "composition_binding_sha256"
            ],
            materialized_arguments_json=canonical_json_text(
                request_payload["materialized_arguments"]
            ),
            arguments_sha256=request_payload["arguments_sha256"],
            target=request_payload["target"],
            target_ref=request_payload["target_ref"],
            target_snapshot_json=canonical_json_text(
                request_payload["target_snapshot"]
            ),
            target_snapshot_sha256=request_payload[
                "target_snapshot_sha256"
            ],
            workspace_id=request_payload["workspace_id"],
            workspace_scope_sha256=request_payload[
                "workspace_scope_sha256"
            ],
            object_grants_json=canonical_json_text(
                request_payload["object_grants"]
            ),
            object_grants_sha256=request_payload["object_grants_sha256"],
            prebound_effect_id=request_payload["prebound_effect_id"],
            prebound_effect_intent_sha256=request_payload[
                "prebound_effect_intent_sha256"
            ],
            action_fence_epoch=request_payload["action_fence_epoch"],
            action_fence_sha256=request_payload["action_fence_sha256"],
            issued_at_ms=request_payload["issued_at_ms"],
            expires_at_ms=request_payload["expires_at_ms"],
            authorization_ceiling_ms=request_payload[
                "authorization_ceiling_ms"
            ],
            authorization_request_sha256=request_payload[
                "authorization_request_sha256"
            ],
            schema_version=request_payload["schema_version"],
            continuation_delegation_id=request_payload.get(
                "continuation_delegation_id"
            ),
            continuation_delegation_sha256=request_payload.get(
                "continuation_delegation_sha256"
            ),
            dependency_evidence_json=(
                None
                if "dependency_evidence" not in request_payload
                else canonical_json_text(request_payload["dependency_evidence"])
            ),
            dependency_evidence_sha256=request_payload.get(
                "dependency_evidence_sha256"
            ),
            supersedes_authorization_id=request_payload.get(
                "supersedes_authorization_id"
            ),
            supersedes_effect_id=request_payload.get("supersedes_effect_id"),
            supersedes_claim_sha256=request_payload.get(
                "supersedes_claim_sha256"
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("stored authorization request is invalid") from exc
    if request.canonical_json != row["authorization_request_json"]:
        raise ValueError("stored authorization request JSON is not canonical")
    artifacts = CompositionStepAuthorizationArtifacts(
        intent_json=row["intent_json"],
        impact_json=row["impact_json"],
        decision_json=row["decision_json"],
        signed_ticket_json=row["signed_ticket_json"],
        signed_grant_json=row["signed_grant_json"],
        runtime_response_json=row["runtime_response_json"],
    )
    projections = artifacts.validate_for_request(request)
    expected = {
        "authorization_id": derive_composition_step_authorization_id(request),
        "state": "ISSUED",
        "executable_plan_id": request.executable_plan_id,
        "executable_plan_sha256": request.executable_plan_sha256,
        "registration_id": request.registration_id,
        "composition_plan_id": request.composition_plan_id,
        "request_id": request.request_id,
        "run_id": request.run_id,
        "generation": request.generation,
        "principal_scope_hash": request.principal_scope_hash,
        "parent_ticket_id": request.parent_ticket_id,
        "parent_ticket_sha256": request.parent_ticket_sha256,
        "step_id": request.step_id,
        "step_binding_sha256": request.step_binding_sha256,
        "attempt": request.attempt,
        "action_id": request.action_id,
        "action_version": request.action_version,
        "source_revision_sha256": request.source_revision_sha256,
        "action_registry_sha256": request.action_registry_sha256,
        "action_permission_sha256": request.action_permission_sha256,
        "argument_schema_sha256": request.argument_schema_sha256,
        "result_schema_sha256": request.result_schema_sha256,
        "composition_binding_sha256": request.composition_binding_sha256,
        "arguments_sha256": request.arguments_sha256,
        "target_snapshot_sha256": request.target_snapshot_sha256,
        "workspace_id": request.workspace_id,
        "workspace_scope_sha256": request.workspace_scope_sha256,
        "object_grants_sha256": request.object_grants_sha256,
        "prebound_effect_id": request.prebound_effect_id,
        "prebound_effect_intent_sha256": (
            request.prebound_effect_intent_sha256
        ),
        "action_fence_epoch": request.action_fence_epoch,
        "action_fence_sha256": request.action_fence_sha256,
        "continuation_delegation_id": request.continuation_delegation_id,
        "continuation_delegation_sha256": (
            request.continuation_delegation_sha256
        ),
        "dependency_evidence_sha256": request.dependency_evidence_sha256,
        "supersedes_authorization_id": request.supersedes_authorization_id,
        "supersedes_effect_id": request.supersedes_effect_id,
        "supersedes_claim_sha256": request.supersedes_claim_sha256,
        "authorization_request_sha256": (
            request.authorization_request_sha256
        ),
        "issued_at_ms": request.issued_at_ms,
        "expires_at_ms": request.expires_at_ms,
        "authorization_ceiling_ms": request.authorization_ceiling_ms,
        **projections,
    }
    for field, value in expected.items():
        if row[field] != value:
            raise ValueError(
                "stored composition authorization column disagrees with "
                f"canonical payload: {field}"
            )
    record = CompositionStepAuthorizationStoreRecord(
        authorization_id=expected["authorization_id"],
        request=request,
        artifacts=artifacts,
        committed_at_ms=row["committed_at_ms"],
        authorization_record_sha256=row["authorization_record_sha256"],
        created_by_this_call=created_by_this_call,
        duplicate=duplicate,
        recovered_after_restart=recovered_after_restart,
    )
    if not record.has_valid_record_sha256():
        raise ValueError("stored composition authorization digest is invalid")
    return record


__all__ = [
    "COMPOSITION_CONTINUATION_DELEGATION_SCHEMA",
    "COMPOSITION_STEP_AUTHORIZATION_SCHEMA",
    "COMPOSITION_STEP_AUTHORIZATION_SCHEMA_V2",
    "MAX_AUTHORIZATION_ARTIFACT_JSON_BYTES",
    "CompositionContinuationDelegation",
    "CompositionStepAuthorizationArtifacts",
    "CompositionStepAuthorizationRequest",
    "CompositionStepAuthorizationStoreRecord",
    "authorization_record_from_row",
    "build_composition_continuation_issuance_context",
    "canonical_json_text",
    "continuation_delegation_from_row",
    "derive_composition_continuation_delegation_id",
    "derive_composition_step_authorization_id",
]
