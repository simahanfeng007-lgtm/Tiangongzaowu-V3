"""Shared Life store contract validation and result types."""
from __future__ import annotations

from dataclasses import dataclass
from contracts import MemoryAssertionV3, PrivacyDeletionTombstone, canonical_json_bytes


class LifeShadowStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProtectedPayloadRecord:
    payload_id: str
    life_id: str
    privacy_scope: str
    ciphertext_sha256: str
    created_at_ms: int
    key_available: bool
    key_destroyed_at_ms: int | None


@dataclass(frozen=True, slots=True)
class MemoryDeletionResult:
    tombstone: PrivacyDeletionTombstone
    deleted_assertion: MemoryAssertionV3
    destroyed_payload_ids: tuple[str, ...]


def _revalidate_contract(value, model_type, identity: str):
    try:
        payload = canonical_json_bytes(value)
        validated = model_type.model_validate_json(payload)
    except Exception as exc:
        raise LifeShadowStoreError(f"{identity} contract is invalid") from exc
    if canonical_json_bytes(validated) != payload:
        raise LifeShadowStoreError(f"{identity} contract is not canonical")
    return validated, payload


def _parse_stored_contract(payload: bytes, model_type, identity: str):
    try:
        value = model_type.model_validate_json(payload, strict=True)
    except Exception as exc:
        raise LifeShadowStoreError(f"stored {identity} contract is invalid") from exc
    if canonical_json_bytes(value) != payload:
        raise LifeShadowStoreError(f"stored {identity} contract is not canonical")
    return value
