"""Ed25519 ticket signing, trust verification, and DPAPI private-key storage."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from runtime_security import (
    DataProtector,
    TicketVerificationError,
    WindowsDpapiProtector,
    b64url_encode as _b64url,
    ticket_signing_input as _signing_input,
    verify_delivery_ticket,
    verify_execution_ticket,
    verify_service_auth_signature,
)

from contracts import (
    DeliveryTicket,
    DeliveryTicketHeader,
    DeliveryTicketPayload,
    ExecutionTicket,
    ExecutionTicketHeader,
    ExecutionTicketPayload,
    OmniCapabilityGrant,
    OmniCapabilityGrantHeader,
    OmniCapabilityGrantPayload,
    ProtectedPrivateKeyEnvelope,
    PublicKeyDescriptor,
    ServiceAuthAssertion,
    ServiceAuthClaims,
    ServiceAuthHeader,
    canonical_json_bytes,
    canonical_sha256,
)


@dataclass(frozen=True)
class CreatedSigningKey:
    public_descriptor: PublicKeyDescriptor
    private_envelope: ProtectedPrivateKeyEnvelope


def _current_user_sid() -> str:
    completed = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    line = completed.stdout.strip()
    fields = next(iter(__import__("csv").reader([line])))
    if len(fields) < 2 or not fields[1].startswith("S-"):
        raise OSError("current Windows SID could not be determined")
    return fields[1]


def _restrict_file_to_sid(path: Path, sid: str) -> str:
    completed = subprocess.run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{sid}:(F)",
        ],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    os.chmod(path, 0o600)
    return canonical_sha256(
        {
            "owner_sid_sha256": hashlib.sha256(sid.encode("utf-8")).hexdigest(),
            "acl_command_result_sha256": hashlib.sha256(completed.stdout).hexdigest(),
            "inheritance_removed": True,
            "current_user_full_control": True,
        }
    )


def _protect_key_file(path: Path, *, allow_portable_test_acl: bool) -> tuple[str, str]:
    """Apply the platform authority to one encrypted key blob.

    Durable runtimes always use the Windows SID + ``icacls`` path.  The POSIX
    branch exists only when a caller explicitly injected a non-DPAPI test
    protector; it never becomes an automatic production fallback.
    """

    if os.name == "nt":
        owner = _current_user_sid()
        return owner, _restrict_file_to_sid(path, owner)
    if not allow_portable_test_acl:
        raise OSError("Windows CurrentUser ACL protection is unavailable")
    owner = f"portable-test-uid:{os.getuid()}"
    os.chmod(path, 0o600)
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        raise OSError("portable test key file permissions are unsafe")
    return owner, canonical_sha256(
        {
            "portable_test_only": True,
            "owner": owner,
            "mode": mode,
            "path_name_sha256": hashlib.sha256(path.name.encode("utf-8")).hexdigest(),
        }
    )


class ProtectedKeyStore:
    def __init__(self, root: Path, *, protector: DataProtector | None = None) -> None:
        if not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("key store root must be a safe absolute directory")
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise OSError("key store root is unsafe")
        self.root = root
        self.keys_root = root / "keys"
        self.keys_root.mkdir(parents=True, exist_ok=True)
        if self.keys_root.is_symlink() or not self.keys_root.is_dir():
            raise OSError("key store keys directory is unsafe")
        self._portable_test_acl = protector is not None and os.name != "nt"
        self.protector = protector or WindowsDpapiProtector()

    @staticmethod
    def _entropy(kid: str, purpose: str, audience: str) -> bytes:
        return canonical_json_bytes(
            {
                "app_id": "tiangong-v3-qiyuan",
                "context": "tiangong-v3-gateway-key-v1",
                "kid": kid,
                "purpose": purpose,
                "audience": audience,
            }
        )

    @staticmethod
    def _storage_stem(kid: str) -> str:
        """Return a fixed-size, filesystem-safe name for a logical key ID.

        Runtime authority roots can already be fairly deep on Windows.  Using
        the caller-controlled ``kid`` as the filename made the atomic ``.tmp``
        path cross the legacy MAX_PATH boundary even though the runtime root
        itself was writable.  A 160-bit content address keeps every key path
        bounded while the full logical ID remains bound by the DPAPI entropy
        and the signed envelope.
        """

        digest = hashlib.sha256(kid.encode("utf-8", errors="strict")).digest()[:20]
        return "k-" + _b64url(digest)

    def _storage_paths(self, kid: str) -> tuple[Path, Path, Path, Path]:
        stem = self._storage_stem(kid)
        blob_path = self.keys_root / f"{stem}.dpapi"
        metadata_path = self.keys_root / f"{stem}.json"
        return (
            blob_path,
            metadata_path,
            blob_path.with_suffix(".dtmp"),
            metadata_path.with_suffix(".jtmp"),
        )

    def create_key(
        self,
        *,
        kid: str,
        purpose: Literal["execution_ticket", "delivery_ticket", "service_auth"],
        audience: str,
        issuer: str,
        not_before_ms: int,
        not_after_ms: int,
        component_manifest_hash: str,
        created_at_ms: int,
    ) -> CreatedSigningKey:
        if not_after_ms <= not_before_ms or created_at_ms < 0:
            raise ValueError("signing key validity is invalid")
        blob_path, metadata_path, temporary_blob, temporary_metadata = self._storage_paths(kid)
        if blob_path.exists() or metadata_path.exists():
            raise FileExistsError("signing key already exists")
        private = Ed25519PrivateKey.generate()
        raw_private = bytearray(private.private_bytes_raw())
        public_bytes = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        entropy = self._entropy(kid, purpose, audience)
        try:
            encrypted = self.protector.protect(raw_private, entropy)
        finally:
            for index in range(len(raw_private)):
                raw_private[index] = 0
        try:
            with temporary_blob.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            owner_sid, acl_sha256 = _protect_key_file(
                temporary_blob,
                allow_portable_test_acl=self._portable_test_acl,
            )
            owner_sid_sha256 = hashlib.sha256(owner_sid.encode("utf-8")).hexdigest()
            os.replace(temporary_blob, blob_path)
            envelope = ProtectedPrivateKeyEnvelope(
                envelope_id="key_envelope_" + kid,
                kid=kid,
                purpose=purpose,
                audience=audience,
                additional_entropy_sha256=hashlib.sha256(entropy).hexdigest(),
                encrypted_blob_sha256=hashlib.sha256(encrypted).hexdigest(),
                encrypted_blob_bytes=len(encrypted),
                storage_relative_path=blob_path.relative_to(self.root).as_posix(),
                owner_sid_sha256=owner_sid_sha256,
                acl_sha256=acl_sha256,
                created_at_ms=created_at_ms,
                envelope_sha256="0" * 64,
            ).with_computed_sha256()
            metadata = canonical_json_bytes(envelope.model_dump(mode="json"))
            with temporary_metadata.open("xb") as stream:
                stream.write(metadata)
                stream.flush()
                os.fsync(stream.fileno())
            metadata_owner, _ = _protect_key_file(
                temporary_metadata,
                allow_portable_test_acl=self._portable_test_acl,
            )
            if metadata_owner != owner_sid:
                raise OSError("protected key metadata owner binding changed")
            os.replace(temporary_metadata, metadata_path)
            descriptor = PublicKeyDescriptor(
                kid=kid,
                issuer=issuer,
                audience=audience,
                purpose=purpose,
                public_key_base64url=_b64url(public_bytes),
                public_key_sha256=hashlib.sha256(public_bytes).hexdigest(),
                state="ACTIVE",
                not_before_ms=not_before_ms,
                not_after_ms=not_after_ms,
                component_manifest_hash=component_manifest_hash,
            )
            return CreatedSigningKey(descriptor, envelope)
        finally:
            for path in (temporary_blob, temporary_metadata):
                if path.exists():
                    path.unlink()

    def load_private_key(self, envelope: ProtectedPrivateKeyEnvelope) -> Ed25519PrivateKey:
        if not envelope.has_valid_sha256():
            raise ValueError("private key envelope digest is invalid")
        blob_path = self.root / envelope.storage_relative_path
        if (
            blob_path.is_symlink()
            or blob_path.parent.is_symlink()
            or not blob_path.is_file()
            or self.root.resolve(strict=True) not in blob_path.resolve(strict=True).parents
        ):
            raise OSError("protected key blob is missing or unsafe")
        encrypted = blob_path.read_bytes()
        if (
            len(encrypted) != envelope.encrypted_blob_bytes
            or hashlib.sha256(encrypted).hexdigest() != envelope.encrypted_blob_sha256
        ):
            raise OSError("protected key blob failed integrity verification")
        entropy = self._entropy(envelope.kid, envelope.purpose, envelope.audience)
        if hashlib.sha256(entropy).hexdigest() != envelope.additional_entropy_sha256:
            raise ValueError("private key entropy binding is invalid")
        raw = self.protector.unprotect(encrypted, entropy)
        try:
            if len(raw) != 32:
                raise OSError("unprotected Ed25519 private key has invalid size")
            return Ed25519PrivateKey.from_private_bytes(bytes(raw))
        finally:
            for index in range(len(raw)):
                raw[index] = 0


class TicketSigner:
    def __init__(self, kid: str, private_key: Ed25519PrivateKey) -> None:
        self.kid = kid
        self.private_key = private_key

    def sign_execution(self, payload: ExecutionTicketPayload) -> ExecutionTicket:
        header = ExecutionTicketHeader(kid=self.kid)
        signature = _b64url(self.private_key.sign(_signing_input(header, payload)))
        return ExecutionTicket(header=header, payload=payload, signature=signature)

    def sign_delivery(self, payload: DeliveryTicketPayload) -> DeliveryTicket:
        header = DeliveryTicketHeader(kid=self.kid)
        signature = _b64url(self.private_key.sign(_signing_input(header, payload)))
        return DeliveryTicket(header=header, payload=payload, signature=signature)

    def sign_omni_capability(
        self, payload: OmniCapabilityGrantPayload
    ) -> OmniCapabilityGrant:
        header = OmniCapabilityGrantHeader(kid=self.kid)
        signature = _b64url(self.private_key.sign(_signing_input(header, payload)))
        return OmniCapabilityGrant(header=header, payload=payload, signature=signature)

    def sign_service_auth(self, claims: ServiceAuthClaims) -> ServiceAuthAssertion:
        header = ServiceAuthHeader(kid=self.kid)
        signature = _b64url(self.private_key.sign(_signing_input(header, claims)))
        return ServiceAuthAssertion(header=header, claims=claims, signature=signature)


__all__ = [
    "CreatedSigningKey",
    "ProtectedKeyStore",
    "TicketSigner",
    "TicketVerificationError",
    "WindowsDpapiProtector",
    "verify_delivery_ticket",
    "verify_execution_ticket",
    "verify_service_auth_signature",
]
