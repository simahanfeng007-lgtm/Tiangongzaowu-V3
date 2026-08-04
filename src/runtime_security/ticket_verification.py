"""Shared Ed25519 ticket signing input and trust-bundle verification."""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contracts import (
    DeliveryTicket,
    ExecutionTicket,
    OmniCapabilityGrant,
    PublicKeyDescriptor,
    ServiceAuthAssertion,
    TrustBundle,
    canonical_json_bytes,
)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def ticket_signing_input(header: object, payload: object) -> bytes:
    header_data = header.model_dump(mode="json")  # type: ignore[attr-defined]
    payload_data = payload.model_dump(mode="json")  # type: ignore[attr-defined]
    return (
        b64url_encode(canonical_json_bytes(header_data))
        + "."
        + b64url_encode(canonical_json_bytes(payload_data))
    ).encode("ascii")


class TicketVerificationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _trusted_key(
    trust_bundle: TrustBundle,
    *,
    kid: str,
    issuer: str,
    audience: str,
    purpose: str,
    now_ms: int,
) -> PublicKeyDescriptor:
    if not trust_bundle.has_valid_sha256() or not trust_bundle.production_ready:
        raise TicketVerificationError("ticket.trust_bundle.not_ready")
    key = next((item for item in trust_bundle.keys if item.kid == kid), None)
    if key is None:
        raise TicketVerificationError("ticket.kid.unknown")
    if key.scope != (issuer, audience, purpose):
        raise TicketVerificationError("ticket.key.scope_mismatch")
    if key.state not in {"ACTIVE", "PREVIOUS"}:
        raise TicketVerificationError("ticket.key.state_rejected")
    if not key.not_before_ms <= now_ms < key.not_after_ms:
        raise TicketVerificationError("ticket.key.expired")
    return key


def _verify_signature(
    key: PublicKeyDescriptor,
    header: object,
    payload: object,
    signature: str,
) -> None:
    try:
        public = Ed25519PublicKey.from_public_bytes(
            b64url_decode(key.public_key_base64url)
        )
        public.verify(
            b64url_decode(signature), ticket_signing_input(header, payload)
        )
    except (ValueError, InvalidSignature) as exc:
        raise TicketVerificationError("ticket.signature.invalid") from exc


def verify_execution_ticket(
    ticket: ExecutionTicket,
    trust_bundle: TrustBundle,
    *,
    now_ms: int,
) -> PublicKeyDescriptor:
    payload = ticket.payload
    if trust_bundle.gateway_epoch != payload.gateway_epoch:
        raise TicketVerificationError("ticket.gateway_epoch.mismatch")
    key = _trusted_key(
        trust_bundle,
        kid=ticket.header.kid,
        issuer=payload.issuer,
        audience=payload.audience,
        purpose="execution_ticket",
        now_ms=now_ms,
    )
    if key.component_manifest_hash != payload.component_manifest_hash:
        raise TicketVerificationError("ticket.component_manifest.mismatch")
    if now_ms < payload.not_before_ms or now_ms > payload.expires_at_ms:
        raise TicketVerificationError("ticket.time_window.invalid")
    _verify_signature(key, ticket.header, payload, ticket.signature)
    return key


def verify_omni_capability_grant(
    grant: OmniCapabilityGrant,
    trust_bundle: TrustBundle,
    *,
    now_ms: int,
) -> PublicKeyDescriptor:
    payload = grant.payload
    if trust_bundle.gateway_epoch != payload.gateway_epoch:
        raise TicketVerificationError("grant.gateway_epoch.mismatch")
    key = _trusted_key(
        trust_bundle,
        kid=grant.header.kid,
        issuer=payload.issuer,
        audience=payload.audience,
        purpose="execution_ticket",
        now_ms=now_ms,
    )
    if key.component_manifest_hash != payload.component_manifest_hash:
        raise TicketVerificationError("grant.component_manifest.mismatch")
    if now_ms < payload.not_before_ms or now_ms > payload.expires_at_ms:
        raise TicketVerificationError("grant.time_window.invalid")
    _verify_signature(key, grant.header, payload, grant.signature)
    return key


def verify_delivery_ticket(
    ticket: DeliveryTicket,
    trust_bundle: TrustBundle,
    *,
    now_ms: int,
) -> PublicKeyDescriptor:
    payload = ticket.payload
    if trust_bundle.gateway_epoch != payload.gateway_epoch:
        raise TicketVerificationError("ticket.gateway_epoch.mismatch")
    key = _trusted_key(
        trust_bundle,
        kid=ticket.header.kid,
        issuer=payload.issuer,
        audience=payload.audience,
        purpose="delivery_ticket",
        now_ms=now_ms,
    )
    if key.component_manifest_hash != payload.component_manifest_hash:
        raise TicketVerificationError("ticket.component_manifest.mismatch")
    if now_ms < payload.not_before_ms or now_ms > payload.expires_at_ms:
        raise TicketVerificationError("ticket.time_window.invalid")
    _verify_signature(key, ticket.header, payload, ticket.signature)
    return key


def verify_service_auth_signature(
    assertion: ServiceAuthAssertion,
    trust_bundle: TrustBundle,
    *,
    now_ms: int,
) -> PublicKeyDescriptor:
    claims = assertion.claims
    if trust_bundle.gateway_epoch != claims.gateway_epoch:
        raise TicketVerificationError("ticket.gateway_epoch.mismatch")
    key = _trusted_key(
        trust_bundle,
        kid=assertion.header.kid,
        issuer=claims.issuer,
        audience=claims.audience,
        purpose="service_auth",
        now_ms=now_ms,
    )
    if key.component_manifest_hash != claims.component_manifest_hash:
        raise TicketVerificationError("ticket.component_manifest.mismatch")
    _verify_signature(key, assertion.header, claims, assertion.signature)
    return key


__all__ = [
    "TicketVerificationError",
    "b64url_decode",
    "b64url_encode",
    "ticket_signing_input",
    "verify_delivery_ticket",
    "verify_execution_ticket",
    "verify_omni_capability_grant",
    "verify_service_auth_signature",
]
