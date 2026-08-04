"""Service authentication, key lifecycle, DPAPI metadata, and log redaction."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .canonical import canonical_json_bytes, canonical_sha256
from .execution import Base64UrlEd25519Signature
from .models import (
    ContractModel,
    EffectId,
    OpaqueId,
    ReasonCode,
    RequestId,
    SCHEMA_BASE,
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION,
    Sha256,
    validate_safe_filename,
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


Base64UrlEd25519PublicKey = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$"),
]
KeyPurpose = Literal["execution_ticket", "delivery_ticket", "service_auth"]
KeyState = Literal["NEXT", "ACTIVE", "PREVIOUS", "REVOKED"]


class TrustScope(ContractModel):
    issuer: OpaqueId
    audience: OpaqueId
    purpose: KeyPurpose


class PublicKeyDescriptor(ContractModel):
    model_config = _schema_config("PublicKeyDescriptor")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    kid: OpaqueId
    issuer: OpaqueId
    audience: OpaqueId
    purpose: KeyPurpose
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_base64url: Base64UrlEd25519PublicKey
    public_key_sha256: Sha256
    state: KeyState
    not_before_ms: int = Field(ge=0)
    not_after_ms: int = Field(ge=0)
    component_manifest_hash: Sha256
    revoked_at_ms: int | None = Field(default=None, ge=0)
    revocation_reason: ReasonCode | None = None

    @model_validator(mode="after")
    def validate_key(self) -> Self:
        if self.not_after_ms <= self.not_before_ms:
            raise ValueError("public key validity window is empty")
        try:
            raw = base64.urlsafe_b64decode(self.public_key_base64url + "=")
        except ValueError as exc:
            raise ValueError("public key is not valid base64url") from exc
        if len(raw) != 32:
            raise ValueError("Ed25519 public key must contain 32 bytes")
        if hashlib.sha256(raw).hexdigest() != self.public_key_sha256:
            raise ValueError("public key fingerprint does not match key bytes")
        revoked = self.state == "REVOKED"
        if revoked != (self.revoked_at_ms is not None and self.revocation_reason is not None):
            raise ValueError("revoked state requires time and reason; live keys forbid both")
        if self.revoked_at_ms is not None and not self.not_before_ms <= self.revoked_at_ms <= self.not_after_ms:
            raise ValueError("revocation time is outside the key validity window")
        return self

    @property
    def scope(self) -> tuple[str, str, str]:
        return (self.issuer, self.audience, self.purpose)


class TrustBundle(ContractModel):
    model_config = _schema_config("TrustBundle")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    bundle_id: OpaqueId
    revision: int = Field(ge=1)
    gateway_epoch: int = Field(ge=1)
    generated_at_ms: int = Field(ge=0)
    required_scopes: tuple[TrustScope, ...] = Field(min_length=1, max_length=64)
    keys: tuple[PublicKeyDescriptor, ...] = Field(min_length=1, max_length=256)
    production_ready: bool
    bundle_sha256: Sha256

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        scope_keys = tuple((item.issuer, item.audience, item.purpose) for item in self.required_scopes)
        if scope_keys != tuple(sorted(set(scope_keys))):
            raise ValueError("required trust scopes must be sorted and unique")
        key_order = tuple((item.issuer, item.audience, item.purpose, item.kid) for item in self.keys)
        if key_order != tuple(sorted(key_order)):
            raise ValueError("trust keys must be sorted by scope and kid")
        kids = tuple(item.kid for item in self.keys)
        if len(set(kids)) != len(kids):
            raise ValueError("kid must be globally unique inside a trust bundle")
        declared_scopes = set(scope_keys)
        if any(item.scope not in declared_scopes for item in self.keys):
            raise ValueError("trust bundle contains a key outside its declared scopes")
        for scope in scope_keys:
            scoped = tuple(item for item in self.keys if item.scope == scope)
            for state in ("NEXT", "ACTIVE", "PREVIOUS"):
                if sum(item.state == state for item in scoped) > 1:
                    raise ValueError(f"trust scope has multiple {state} keys")
            active = tuple(item for item in scoped if item.state == "ACTIVE")
            if self.production_ready:
                if len(active) != 1:
                    raise ValueError("production trust scope requires exactly one active key")
                if not active[0].not_before_ms <= self.generated_at_ms < active[0].not_after_ms:
                    raise ValueError("active production key is outside its validity window")
            if any(
                item.state == "REVOKED" and (item.revoked_at_ms or 0) > self.generated_at_ms
                for item in scoped
            ):
                raise ValueError("trust bundle cannot claim a future key revocation")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"bundle_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.bundle_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"bundle_sha256": self.computed_sha256()})


class ServiceAuthHeader(ContractModel):
    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    alg: Literal["EdDSA"] = "EdDSA"
    typ: Literal["tiangong.service-auth+jws"] = "tiangong.service-auth+jws"
    kid: OpaqueId


class ServiceAuthClaims(ContractModel):
    issuer: OpaqueId
    audience: OpaqueId
    subject_instance_id: OpaqueId
    issued_at_ms: int = Field(ge=0)
    not_before_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    gateway_epoch: int = Field(ge=1)
    request_nonce: OpaqueId
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=512)
    body_sha256: Sha256
    component_manifest_hash: Sha256
    request_id: RequestId | None = None
    effect_id: EffectId | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or "?" in value
            or "#" in value
            or re.fullmatch(r"/[A-Za-z0-9_/-]+", value) is None
            or any(part in {"", ".", ".."} for part in value.split("/")[1:])
        ):
            raise ValueError("service auth path must be an absolute normalized API path")
        return value

    @model_validator(mode="after")
    def validate_time_window(self) -> Self:
        if not self.issued_at_ms <= self.not_before_ms <= self.expires_at_ms:
            raise ValueError("service auth time window is invalid")
        if self.expires_at_ms - self.issued_at_ms > 30_000:
            raise ValueError("service auth assertion exceeds 30 second lifetime")
        return self


class ServiceAuthAssertion(ContractModel):
    model_config = _schema_config("ServiceAuthAssertion")

    header: ServiceAuthHeader
    claims: ServiceAuthClaims
    signature: Base64UrlEd25519Signature


class ServiceAuthorizationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def authorize_service_request_contract(
    assertion: ServiceAuthAssertion,
    trust_bundle: TrustBundle,
    *,
    signature_verified: bool,
    nonce_registered: bool,
    now_ms: int,
    expected_gateway_epoch: int,
    expected_issuer: str,
    expected_audience: str,
    expected_method: str,
    expected_path: str,
    expected_body_sha256: str,
    expected_component_manifest_hash: str,
    clock_skew_ms: int = 5_000,
) -> ServiceAuthClaims:
    def reject(code: str) -> None:
        raise ServiceAuthorizationError(code)

    if not signature_verified:
        reject("service_auth.signature.unverified")
    if not nonce_registered:
        reject("service_auth.nonce.unregistered")
    if not 0 <= clock_skew_ms <= 5_000:
        reject("service_auth.clock_skew.invalid")
    if not trust_bundle.has_valid_sha256() or not trust_bundle.production_ready:
        reject("service_auth.trust_bundle.not_ready")
    claims = assertion.claims
    if trust_bundle.gateway_epoch != expected_gateway_epoch or claims.gateway_epoch != expected_gateway_epoch:
        reject("service_auth.gateway_epoch.mismatch")
    if claims.issuer != expected_issuer or claims.audience != expected_audience:
        reject("service_auth.principal.mismatch")
    exact = (
        (claims.method, expected_method, "method"),
        (claims.path, expected_path, "path"),
        (claims.body_sha256, expected_body_sha256, "body"),
        (claims.component_manifest_hash, expected_component_manifest_hash, "component_manifest"),
    )
    for actual, expected, name in exact:
        if actual != expected:
            reject(f"service_auth.{name}.mismatch")
    if now_ms + clock_skew_ms < claims.not_before_ms:
        reject("service_auth.not_yet_valid")
    if now_ms > claims.expires_at_ms + clock_skew_ms:
        reject("service_auth.expired")
    key = next((item for item in trust_bundle.keys if item.kid == assertion.header.kid), None)
    if key is None:
        reject("service_auth.kid.unknown")
    assert key is not None
    if key.scope != (claims.issuer, claims.audience, "service_auth"):
        reject("service_auth.key.scope_mismatch")
    if key.state not in {"ACTIVE", "PREVIOUS"}:
        reject("service_auth.key.state_rejected")
    if not key.not_before_ms <= now_ms < key.not_after_ms:
        reject("service_auth.key.expired")
    if key.component_manifest_hash != claims.component_manifest_hash:
        reject("service_auth.key.component_manifest_mismatch")
    return claims


class ProtectedPrivateKeyEnvelope(ContractModel):
    """Metadata for a DPAPI blob; this contract never contains plaintext key bytes."""

    model_config = _schema_config("ProtectedPrivateKeyEnvelope")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    envelope_id: OpaqueId
    kid: OpaqueId
    purpose: KeyPurpose
    audience: OpaqueId
    key_algorithm: Literal["Ed25519"] = "Ed25519"
    app_id: Literal["tiangong-v3-qiyuan"] = "tiangong-v3-qiyuan"
    provider: Literal["Windows-DPAPI"] = "Windows-DPAPI"
    protection_scope: Literal["CurrentUser"] = "CurrentUser"
    entropy_context: Literal["tiangong-v3-gateway-key-v1"] = "tiangong-v3-gateway-key-v1"
    additional_entropy_sha256: Sha256
    encrypted_blob_sha256: Sha256
    encrypted_blob_bytes: int = Field(ge=1, le=1_048_576)
    storage_relative_path: str = Field(min_length=1, max_length=260)
    owner_sid_sha256: Sha256
    acl_sha256: Sha256
    created_at_ms: int = Field(ge=0)
    plaintext_present: Literal[False] = False
    envelope_sha256: Sha256

    @field_validator("storage_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if (
            value != normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"", ".", ".."} for part in parts)
            or parts[0] != "keys"
            or not normalized.endswith(".dpapi")
        ):
            raise ValueError("DPAPI key path must be a normalized relative .dpapi path")
        for part in parts:
            validate_safe_filename(part)
        return value

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"envelope_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.envelope_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"envelope_sha256": self.computed_sha256()})


RotationPhase = Literal["PREPARE", "ACTIVATE", "RETIRE"]


class KeyRotationManifest(ContractModel):
    model_config = _schema_config("KeyRotationManifest")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    rotation_id: OpaqueId
    phase: RotationPhase
    issuer: OpaqueId
    audience: OpaqueId
    purpose: KeyPurpose
    gateway_epoch: int = Field(ge=1)
    old_kid: OpaqueId
    new_kid: OpaqueId
    signer_kid: OpaqueId
    prepared_at_ms: int = Field(ge=0)
    effective_at_ms: int = Field(ge=0)
    grace_until_ms: int = Field(ge=0)
    before_bundle_sha256: Sha256
    after_bundle_sha256: Sha256
    component_manifest_hash: Sha256
    signature: Base64UrlEd25519Signature
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_rotation(self) -> Self:
        if self.old_kid == self.new_kid or self.signer_kid != self.old_kid:
            raise ValueError("normal rotation must be authorized by the distinct old active key")
        if not self.prepared_at_ms <= self.effective_at_ms <= self.grace_until_ms:
            raise ValueError("rotation time sequence is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256", "signature"})
        )

    def has_valid_sha256(self) -> bool:
        return self.manifest_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"manifest_sha256": self.computed_sha256()})


class EmergencyKeyRevocationManifest(ContractModel):
    """Out-of-band recovery authorization for suspected private-key compromise."""

    model_config = _schema_config("EmergencyKeyRevocationManifest")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    incident_id: OpaqueId
    issuer: OpaqueId
    audience: OpaqueId
    purpose: KeyPurpose
    compromised_kid: OpaqueId
    replacement_kid: OpaqueId
    recovery_signer_kid: OpaqueId
    authorization_mode: Literal["offline_recovery_key"] = "offline_recovery_key"
    previous_gateway_epoch: int = Field(ge=1)
    new_gateway_epoch: int = Field(ge=2)
    detected_at_ms: int = Field(ge=0)
    effective_at_ms: int = Field(ge=0)
    before_bundle_sha256: Sha256
    after_bundle_sha256: Sha256
    component_manifest_hash: Sha256
    recovery_signature: Base64UrlEd25519Signature
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def validate_revocation(self) -> Self:
        if len({self.compromised_kid, self.replacement_kid, self.recovery_signer_kid}) != 3:
            raise ValueError("compromised, replacement, and recovery keys must be distinct")
        if self.new_gateway_epoch != self.previous_gateway_epoch + 1:
            raise ValueError("emergency revocation must advance gateway epoch exactly once")
        if self.effective_at_ms < self.detected_at_ms:
            raise ValueError("emergency revocation predates compromise detection")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256", "recovery_signature"})
        )

    def has_valid_sha256(self) -> bool:
        return self.manifest_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"manifest_sha256": self.computed_sha256()})


def _key_state(bundle: TrustBundle, kid: str) -> KeyState | None:
    item = next((key for key in bundle.keys if key.kid == kid), None)
    return None if item is None else item.state


def _key_by_id(bundle: TrustBundle, kid: str) -> PublicKeyDescriptor | None:
    return next((key for key in bundle.keys if key.kid == kid), None)


def _immutable_key_identity(key: PublicKeyDescriptor) -> tuple[object, ...]:
    return (
        key.kid,
        key.issuer,
        key.audience,
        key.purpose,
        key.algorithm,
        key.public_key_base64url,
        key.public_key_sha256,
        key.not_before_ms,
        key.not_after_ms,
        key.component_manifest_hash,
    )


def _unchanged_non_target_keys(
    before: TrustBundle,
    after: TrustBundle,
    target_kids: set[str],
) -> bool:
    before_keys = {
        key.kid: key for key in before.keys if key.kid not in target_kids
    }
    after_keys = {
        key.kid: key for key in after.keys if key.kid not in target_kids
    }
    return before_keys == after_keys


def authorize_key_rotation_contract(
    manifest: KeyRotationManifest,
    before: TrustBundle,
    after: TrustBundle,
    *,
    signature_verified: bool,
    now_ms: int,
) -> TrustBundle:
    if not signature_verified:
        raise ServiceAuthorizationError("key_rotation.signature.unverified")
    if not manifest.has_valid_sha256():
        raise ServiceAuthorizationError("key_rotation.manifest.digest_invalid")
    if not before.has_valid_sha256() or not after.has_valid_sha256():
        raise ServiceAuthorizationError("key_rotation.trust_bundle.digest_invalid")
    if (
        manifest.before_bundle_sha256 != before.bundle_sha256
        or manifest.after_bundle_sha256 != after.bundle_sha256
    ):
        raise ServiceAuthorizationError("key_rotation.trust_bundle.mismatch")
    if before.gateway_epoch != manifest.gateway_epoch or after.gateway_epoch != manifest.gateway_epoch:
        raise ServiceAuthorizationError("key_rotation.gateway_epoch.mismatch")
    if (
        before.bundle_id != after.bundle_id
        or after.revision != before.revision + 1
        or before.required_scopes != after.required_scopes
    ):
        raise ServiceAuthorizationError("key_rotation.bundle_lineage.invalid")
    expected_states = {
        "PREPARE": (("ACTIVE", None), ("ACTIVE", "NEXT")),
        "ACTIVATE": (("ACTIVE", "NEXT"), ("PREVIOUS", "ACTIVE")),
        "RETIRE": (("PREVIOUS", "ACTIVE"), ("REVOKED", "ACTIVE")),
    }[manifest.phase]
    before_states = (_key_state(before, manifest.old_kid), _key_state(before, manifest.new_kid))
    after_states = (_key_state(after, manifest.old_kid), _key_state(after, manifest.new_kid))
    if before_states != expected_states[0] or after_states != expected_states[1]:
        raise ServiceAuthorizationError("key_rotation.state_transition.invalid")
    if not _unchanged_non_target_keys(
        before,
        after,
        {manifest.old_kid, manifest.new_kid},
    ):
        raise ServiceAuthorizationError("key_rotation.unrelated_key.changed")
    relevant = tuple(
        item
        for item in (
            _key_by_id(before, manifest.old_kid),
            _key_by_id(after, manifest.old_kid),
            _key_by_id(before, manifest.new_kid),
            _key_by_id(after, manifest.new_kid),
        )
        if item is not None
    )
    expected_scope = (manifest.issuer, manifest.audience, manifest.purpose)
    if any(
        item.scope != expected_scope
        or item.component_manifest_hash != manifest.component_manifest_hash
        for item in relevant
    ):
        raise ServiceAuthorizationError("key_rotation.key_scope.invalid")
    for kid in (manifest.old_kid, manifest.new_kid):
        old = _key_by_id(before, kid)
        new = _key_by_id(after, kid)
        if (
            old is not None
            and new is not None
            and _immutable_key_identity(old) != _immutable_key_identity(new)
        ):
            raise ServiceAuthorizationError("key_rotation.key_identity.changed")
    old_after = _key_by_id(after, manifest.old_kid)
    if (
        manifest.phase == "RETIRE"
        and old_after is not None
        and old_after.revoked_at_ms != manifest.grace_until_ms
    ):
        raise ServiceAuthorizationError("key_rotation.revocation_time.mismatch")
    in_window = {
        "PREPARE": manifest.prepared_at_ms <= now_ms <= manifest.effective_at_ms,
        "ACTIVATE": manifest.effective_at_ms <= now_ms <= manifest.grace_until_ms,
        "RETIRE": now_ms >= manifest.grace_until_ms,
    }[manifest.phase]
    if not in_window:
        raise ServiceAuthorizationError("key_rotation.outside_phase_window")
    return after


def authorize_emergency_key_revocation_contract(
    manifest: EmergencyKeyRevocationManifest,
    before: TrustBundle,
    after: TrustBundle,
    *,
    recovery_signature_verified: bool,
    expected_recovery_signer_kid: str,
    now_ms: int,
) -> TrustBundle:
    if not recovery_signature_verified:
        raise ServiceAuthorizationError("emergency_revocation.recovery_signature.unverified")
    if manifest.recovery_signer_kid != expected_recovery_signer_kid:
        raise ServiceAuthorizationError("emergency_revocation.recovery_signer.mismatch")
    if not manifest.has_valid_sha256():
        raise ServiceAuthorizationError("emergency_revocation.manifest.digest_invalid")
    if (
        not before.has_valid_sha256()
        or not after.has_valid_sha256()
        or not before.production_ready
        or not after.production_ready
    ):
        raise ServiceAuthorizationError("emergency_revocation.trust_bundle.not_ready")
    if (
        manifest.before_bundle_sha256 != before.bundle_sha256
        or manifest.after_bundle_sha256 != after.bundle_sha256
    ):
        raise ServiceAuthorizationError("emergency_revocation.trust_bundle.mismatch")
    if (
        before.bundle_id != after.bundle_id
        or after.revision != before.revision + 1
        or before.required_scopes != after.required_scopes
    ):
        raise ServiceAuthorizationError("emergency_revocation.bundle_lineage.invalid")
    if (
        before.gateway_epoch != manifest.previous_gateway_epoch
        or after.gateway_epoch != manifest.new_gateway_epoch
    ):
        raise ServiceAuthorizationError("emergency_revocation.gateway_epoch.invalid")
    compromised_before = _key_by_id(before, manifest.compromised_kid)
    compromised_after = _key_by_id(after, manifest.compromised_kid)
    replacement_before = _key_by_id(before, manifest.replacement_kid)
    replacement_after = _key_by_id(after, manifest.replacement_kid)
    if (
        compromised_before is None
        or compromised_after is None
        or compromised_before.state not in {"ACTIVE", "PREVIOUS"}
        or compromised_after.state != "REVOKED"
        or replacement_after is None
        or replacement_after.state != "ACTIVE"
    ):
        raise ServiceAuthorizationError("emergency_revocation.key_state.invalid")
    if not _unchanged_non_target_keys(
        before,
        after,
        {manifest.compromised_kid, manifest.replacement_kid},
    ):
        raise ServiceAuthorizationError("emergency_revocation.unrelated_key.changed")
    expected_scope = (manifest.issuer, manifest.audience, manifest.purpose)
    relevant = (compromised_before, compromised_after, replacement_after)
    if any(
        item.scope != expected_scope
        or item.component_manifest_hash != manifest.component_manifest_hash
        for item in relevant
    ):
        raise ServiceAuthorizationError("emergency_revocation.key_scope.invalid")
    if _immutable_key_identity(compromised_before) != _immutable_key_identity(compromised_after):
        raise ServiceAuthorizationError("emergency_revocation.compromised_key.changed")
    if compromised_after.revoked_at_ms != manifest.effective_at_ms:
        raise ServiceAuthorizationError("emergency_revocation.revocation_time.mismatch")
    if (
        replacement_before is not None
        and _immutable_key_identity(replacement_before)
        != _immutable_key_identity(replacement_after)
    ):
        raise ServiceAuthorizationError("emergency_revocation.replacement_key.changed")
    if after.generated_at_ms < manifest.effective_at_ms:
        raise ServiceAuthorizationError("emergency_revocation.bundle_time.invalid")
    if now_ms < manifest.effective_at_ms:
        raise ServiceAuthorizationError("emergency_revocation.not_yet_effective")
    return after


class RedactionPolicy(ContractModel):
    model_config = _schema_config("RedactionPolicy")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    policy_id: OpaqueId
    revision: int = Field(ge=1)
    sensitive_keys: tuple[str, ...] = Field(min_length=1, max_length=256)
    sensitive_suffixes: tuple[str, ...] = Field(default=(), max_length=64)
    redact_bearer_tokens: bool = True
    redact_jwt_like_tokens: bool = True
    max_string_chars: int = Field(ge=32, le=100_000)
    policy_sha256: Sha256

    @field_validator("sensitive_keys", "sensitive_suffixes")
    @classmethod
    def validate_key_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item != item.lower() or "-" in item for item in value):
            raise ValueError("redaction keys must be normalized lowercase underscore names")
        if value != tuple(sorted(set(value))):
            raise ValueError("redaction key sets must be sorted and unique")
        return value

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_sha256()})


class RedactedLogPayload(ContractModel):
    model_config = _schema_config("RedactedLogPayload")

    schema_version: Literal[LEGACY_SCHEMA_VERSION, SCHEMA_VERSION] = SCHEMA_VERSION
    event_id: OpaqueId
    source_component_id: OpaqueId
    observed_at_ms: int = Field(ge=0)
    policy_sha256: Sha256
    original_payload_sha256: Sha256
    redacted_payload_json: str = Field(min_length=2, max_length=1_000_000)
    redacted_payload_sha256: Sha256
    redacted_paths: tuple[str, ...] = Field(default=(), max_length=10_000)

    @field_validator("redacted_paths")
    @classmethod
    def validate_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))) or any(not item.startswith("/") for item in value):
            raise ValueError("redacted JSON paths must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        try:
            payload = json.loads(self.redacted_payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("redacted payload is not JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("redacted structured log payload must be a JSON object")
        canonical = canonical_json_bytes(payload)
        if canonical.decode("utf-8") != self.redacted_payload_json:
            raise ValueError("redacted log payload must use canonical JSON")
        if hashlib.sha256(canonical).hexdigest() != self.redacted_payload_sha256:
            raise ValueError("redacted log payload digest is invalid")
        generic_sensitive_keys = {
            "access_token",
            "api_key",
            "authorization",
            "cookie",
            "encrypted_blob",
            "password",
            "private_key",
            "refresh_token",
            "secret",
            "set_cookie",
            "signature",
            "ticket",
        }
        generic_sensitive_suffixes = ("_api_key", "_password", "_secret", "_token")

        def scan(value: object) -> None:
            if isinstance(value, str):
                if _BEARER.search(value) or _JWT.search(value):
                    raise ValueError("redacted log still contains a credential-shaped string")
                return
            if isinstance(value, list):
                for item in value:
                    scan(item)
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = _normalized_key(key)
                    sensitive = normalized in generic_sensitive_keys or any(
                        normalized.endswith(suffix) for suffix in generic_sensitive_suffixes
                    )
                    if sensitive and not (
                        isinstance(item, str)
                        and re.fullmatch(r"\[REDACTED:sha256:[0-9a-f]{64}\]", item)
                    ):
                        raise ValueError("redacted log still contains a sensitive field value")
                    scan(item)

        scan(payload)
        return self


_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def _normalized_key(value: str) -> str:
    return value.lower().replace("-", "_")


def _redaction_marker(value: object) -> str:
    return f"[REDACTED:sha256:{canonical_sha256({'value': value})}]"


def default_redaction_policy() -> RedactionPolicy:
    return RedactionPolicy(
        policy_id="tiangong_structured_log_redaction_v1",
        revision=1,
        sensitive_keys=tuple(
            sorted(
                {
                    "access_token",
                    "api_key",
                    "authorization",
                    "cookie",
                    "encrypted_blob",
                    "local_path",
                    "password",
                    "private_key",
                    "refresh_token",
                    "secret",
                    "service_auth_assertion",
                    "set_cookie",
                    "signature",
                    "ticket",
                    "user_home",
                    "workspace_path",
                }
            )
        ),
        sensitive_suffixes=("_api_key", "_password", "_secret", "_token"),
        max_string_chars=4_096,
        policy_sha256="0" * 64,
    ).with_computed_sha256()


def redact_log_payload(
    payload: Mapping[str, object],
    policy: RedactionPolicy,
    *,
    event_id: str,
    source_component_id: str,
    observed_at_ms: int,
) -> RedactedLogPayload:
    if not policy.has_valid_sha256():
        raise ValueError("redaction policy digest is invalid")
    redacted_paths: list[str] = []

    def walk(value: object, path: str) -> object:
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float):
            raise TypeError("floating point values are forbidden in structured logs")
        if isinstance(value, str):
            secret = (
                (policy.redact_bearer_tokens and _BEARER.search(value))
                or (policy.redact_jwt_like_tokens and _JWT.search(value))
            )
            if secret:
                redacted_paths.append(path or "/")
                return _redaction_marker(value)
            if len(value) > policy.max_string_chars:
                return value[: policy.max_string_chars] + "...[TRUNCATED]"
            return value
        if isinstance(value, Mapping):
            result: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("structured log object keys must be strings")
                escaped = key.replace("~", "~0").replace("/", "~1")
                child_path = f"{path}/{escaped}"
                normalized = _normalized_key(key)
                sensitive = normalized in policy.sensitive_keys or any(
                    normalized.endswith(suffix) for suffix in policy.sensitive_suffixes
                )
                if sensitive:
                    redacted_paths.append(child_path)
                    result[key] = _redaction_marker(item)
                else:
                    result[key] = walk(item, child_path)
            return result
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return [walk(item, f"{path}/{index}") for index, item in enumerate(value)]
        raise TypeError(f"unsupported structured log value: {type(value).__name__}")

    original_sha256 = canonical_sha256(payload)
    redacted = walk(payload, "")
    encoded = canonical_json_bytes(redacted)
    return RedactedLogPayload(
        event_id=event_id,
        source_component_id=source_component_id,
        observed_at_ms=observed_at_ms,
        policy_sha256=policy.policy_sha256,
        original_payload_sha256=original_sha256,
        redacted_payload_json=encoded.decode("utf-8"),
        redacted_payload_sha256=hashlib.sha256(encoded).hexdigest(),
        redacted_paths=tuple(sorted(set(redacted_paths))),
    )


__all__ = [
    "EmergencyKeyRevocationManifest",
    "KeyRotationManifest",
    "ProtectedPrivateKeyEnvelope",
    "PublicKeyDescriptor",
    "RedactedLogPayload",
    "RedactionPolicy",
    "ServiceAuthAssertion",
    "ServiceAuthClaims",
    "ServiceAuthHeader",
    "ServiceAuthorizationError",
    "TrustBundle",
    "TrustScope",
    "authorize_key_rotation_contract",
    "authorize_emergency_key_revocation_contract",
    "authorize_service_request_contract",
    "default_redaction_policy",
    "redact_log_payload",
]
