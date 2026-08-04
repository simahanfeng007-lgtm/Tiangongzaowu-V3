"""Durable, signature-verifying trust-bundle rotation and revocation authority."""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contracts import (
    EmergencyKeyRevocationManifest,
    KeyRotationManifest,
    PublicKeyDescriptor,
    TrustBundle,
    authorize_emergency_key_revocation_contract,
    authorize_key_rotation_contract,
    canonical_json_bytes,
    canonical_sha256,
)


_STATE_SCHEMA = "tiangong.gateway.operational-trust.v1"
_STATE_KEYS = {
    "schema",
    "current_bundle",
    "applied_manifests",
    "updated_at_ms",
    "state_sha256",
}


class OperationalTrustError(ValueError):
    pass


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OperationalTrustError("operational trust state has duplicate JSON keys")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise OperationalTrustError("operational trust state has non-finite JSON")


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))
    except (ValueError, TypeError) as exc:
        raise OperationalTrustError("trust manifest signature encoding is invalid") from exc


def trust_manifest_signing_input(
    manifest: KeyRotationManifest | EmergencyKeyRevocationManifest,
) -> bytes:
    signature_field = (
        "signature" if isinstance(manifest, KeyRotationManifest) else "recovery_signature"
    )
    return canonical_json_bytes(
        manifest.model_dump(mode="json", exclude={signature_field})
    )


def _verify_manifest_signature(
    manifest: KeyRotationManifest | EmergencyKeyRevocationManifest,
    key: PublicKeyDescriptor,
    *,
    now_ms: int,
) -> None:
    signature = (
        manifest.signature
        if isinstance(manifest, KeyRotationManifest)
        else manifest.recovery_signature
    )
    signer_kid = (
        manifest.signer_kid
        if isinstance(manifest, KeyRotationManifest)
        else manifest.recovery_signer_kid
    )
    if (
        key.kid != signer_kid
        or key.state not in {"ACTIVE", "PREVIOUS"}
        or not key.not_before_ms <= now_ms < key.not_after_ms
        or key.component_manifest_hash != manifest.component_manifest_hash
    ):
        raise OperationalTrustError("trust manifest signer is not currently authorized")
    try:
        public = Ed25519PublicKey.from_public_bytes(
            _b64url_decode(key.public_key_base64url)
        )
        public.verify(_b64url_decode(signature), trust_manifest_signing_input(manifest))
    except (ValueError, InvalidSignature) as exc:
        raise OperationalTrustError("trust manifest signature is invalid") from exc


def _state_digest(state: dict[str, Any]) -> str:
    return canonical_sha256({key: value for key, value in state.items() if key != "state_sha256"})


def _bundle_from_json(value: object) -> TrustBundle:
    """Validate persisted JSON in strict JSON mode (arrays map to contract tuples)."""

    return TrustBundle.model_validate_json(canonical_json_bytes(value), strict=True)


class OperationalTrustStore:
    """Single-writer trust deployment with exact lineage and replay protection."""

    def __init__(
        self,
        path: Path,
        *,
        recovery_key: PublicKeyDescriptor | None = None,
    ) -> None:
        if not path.is_absolute() or path == Path(path.anchor):
            raise OperationalTrustError("operational trust path must be a safe absolute file")
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.recovery_key = recovery_key

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        initial_bundle: TrustBundle | None,
        now_ms: int,
        recovery_key: PublicKeyDescriptor | None = None,
    ) -> "OperationalTrustStore":
        if now_ms < 0:
            raise OperationalTrustError("operational trust time is invalid")
        store = cls(path, recovery_key=recovery_key)
        store._ensure_parent()
        if path.exists():
            state = store._read_state()
            if initial_bundle is not None and (
                not initial_bundle.has_valid_sha256()
                or state["current_bundle"]["bundle_sha256"]
                != initial_bundle.bundle_sha256
            ):
                raise OperationalTrustError("initial trust bundle disagrees with durable state")
            return store
        if initial_bundle is None or not initial_bundle.has_valid_sha256() or not initial_bundle.production_ready:
            raise OperationalTrustError("a production-ready initial trust bundle is required")
        state = {
            "schema": _STATE_SCHEMA,
            "current_bundle": initial_bundle.model_dump(mode="json"),
            "applied_manifests": [],
            "updated_at_ms": now_ms,
            "state_sha256": "0" * 64,
        }
        state["state_sha256"] = _state_digest(state)
        store._write_state(state, create=True)
        return store

    def _ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cursor = self.path.parent
        while True:
            if cursor.is_symlink() or not cursor.is_dir():
                raise OperationalTrustError("operational trust parent is unsafe")
            if cursor == cursor.parent:
                break
            cursor = cursor.parent
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise OperationalTrustError("operational trust state path is unsafe")

    def _read_state(self) -> dict[str, Any]:
        self._ensure_parent()
        try:
            raw = self.path.read_bytes()
            state = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_pairs,
                parse_constant=_reject_constant,
            )
        except OperationalTrustError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationalTrustError("operational trust state is unreadable") from exc
        if (
            not isinstance(state, dict)
            or set(state) != _STATE_KEYS
            or state.get("schema") != _STATE_SCHEMA
            or state.get("state_sha256") != _state_digest(state)
            or isinstance(state.get("updated_at_ms"), bool)
            or not isinstance(state.get("updated_at_ms"), int)
            or state["updated_at_ms"] < 0
            or not isinstance(state.get("applied_manifests"), list)
            or len(state["applied_manifests"]) > 4096
        ):
            raise OperationalTrustError("operational trust state integrity is invalid")
        try:
            bundle = _bundle_from_json(state["current_bundle"])
        except ValueError as exc:
            raise OperationalTrustError("durable trust bundle is invalid") from exc
        if not bundle.has_valid_sha256() or not bundle.production_ready:
            raise OperationalTrustError("durable trust bundle is not production ready")
        for item in state["applied_manifests"]:
            if (
                not isinstance(item, dict)
                or set(item)
                != {"kind", "id", "manifest_sha256", "before_sha256", "after_sha256", "applied_at_ms"}
                or item["kind"] not in {"rotation", "emergency_revocation"}
                or not all(isinstance(item[key], str) and item[key] for key in ("id", "manifest_sha256", "before_sha256", "after_sha256"))
                or isinstance(item["applied_at_ms"], bool)
                or not isinstance(item["applied_at_ms"], int)
                or item["applied_at_ms"] < 0
            ):
                raise OperationalTrustError("operational trust audit chain is invalid")
        if state["applied_manifests"] and state["applied_manifests"][-1]["after_sha256"] != bundle.bundle_sha256:
            raise OperationalTrustError("operational trust audit head is detached")
        return state

    def current_bundle(self) -> TrustBundle:
        return _bundle_from_json(self._read_state()["current_bundle"])

    def _write_state(self, state: dict[str, Any], *, create: bool = False) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp-" + secrets.token_hex(8))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(canonical_json_bytes(state))
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            if create and self.path.exists():
                raise OperationalTrustError("operational trust state was initialized concurrently")
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _acquire_lock(self) -> int:
        try:
            return os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise OperationalTrustError("operational trust writer is already active") from exc

    def _release_lock(self, descriptor: int) -> None:
        try:
            os.close(descriptor)
        finally:
            self.lock_path.unlink(missing_ok=True)

    def _commit(
        self,
        state: dict[str, Any],
        after: TrustBundle,
        *,
        kind: str,
        manifest_id: str,
        manifest_sha256: str,
        now_ms: int,
    ) -> TrustBundle:
        if any(item["manifest_sha256"] == manifest_sha256 for item in state["applied_manifests"]):
            raise OperationalTrustError("trust manifest replay is forbidden")
        before_sha256 = state["current_bundle"]["bundle_sha256"]
        audit = [
            *state["applied_manifests"],
            {
                "kind": kind,
                "id": manifest_id,
                "manifest_sha256": manifest_sha256,
                "before_sha256": before_sha256,
                "after_sha256": after.bundle_sha256,
                "applied_at_ms": now_ms,
            },
        ]
        next_state = {
            "schema": _STATE_SCHEMA,
            "current_bundle": after.model_dump(mode="json"),
            "applied_manifests": audit,
            "updated_at_ms": now_ms,
            "state_sha256": "0" * 64,
        }
        next_state["state_sha256"] = _state_digest(next_state)
        self._write_state(next_state)
        return after

    def apply_rotation(
        self,
        manifest: KeyRotationManifest,
        after: TrustBundle,
        *,
        now_ms: int,
    ) -> TrustBundle:
        lock = self._acquire_lock()
        try:
            state = self._read_state()
            before = _bundle_from_json(state["current_bundle"])
            key = next((item for item in before.keys if item.kid == manifest.signer_kid), None)
            if key is None:
                raise OperationalTrustError("rotation signer is absent from current trust")
            _verify_manifest_signature(manifest, key, now_ms=now_ms)
            authorized = authorize_key_rotation_contract(
                manifest,
                before,
                after,
                signature_verified=True,
                now_ms=now_ms,
            )
            return self._commit(
                state,
                authorized,
                kind="rotation",
                manifest_id=manifest.rotation_id,
                manifest_sha256=manifest.manifest_sha256,
                now_ms=now_ms,
            )
        finally:
            self._release_lock(lock)

    def apply_emergency_revocation(
        self,
        manifest: EmergencyKeyRevocationManifest,
        after: TrustBundle,
        *,
        now_ms: int,
    ) -> TrustBundle:
        lock = self._acquire_lock()
        try:
            state = self._read_state()
            before = _bundle_from_json(state["current_bundle"])
            if self.recovery_key is None:
                raise OperationalTrustError("offline recovery key is not pinned")
            _verify_manifest_signature(manifest, self.recovery_key, now_ms=now_ms)
            authorized = authorize_emergency_key_revocation_contract(
                manifest,
                before,
                after,
                recovery_signature_verified=True,
                expected_recovery_signer_kid=self.recovery_key.kid,
                now_ms=now_ms,
            )
            return self._commit(
                state,
                authorized,
                kind="emergency_revocation",
                manifest_id=manifest.incident_id,
                manifest_sha256=manifest.manifest_sha256,
                now_ms=now_ms,
            )
        finally:
            self._release_lock(lock)


__all__ = [
    "OperationalTrustError",
    "OperationalTrustStore",
    "trust_manifest_signing_input",
]
