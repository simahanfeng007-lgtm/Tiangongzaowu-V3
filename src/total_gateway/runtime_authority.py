"""Persistent DPAPI-backed signing authority for gateway-issued Tickets."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    ComponentManifest,
    ProtectedPrivateKeyEnvelope,
    PublicKeyDescriptor,
    TrustBundle,
    TrustScope,
    canonical_json_bytes,
    canonical_sha256,
)

from runtime_security import DataProtector

from .tickets import ProtectedKeyStore, TicketSigner


class RuntimeAuthorityError(RuntimeError):
    pass


class _AuthorityKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    purpose: Literal["execution_ticket", "delivery_ticket"]
    descriptor: PublicKeyDescriptor
    envelope: ProtectedPrivateKeyEnvelope

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if (
            self.descriptor.kid != self.envelope.kid
            or self.descriptor.purpose != self.purpose
            or self.envelope.purpose != self.purpose
            or self.descriptor.audience != self.envelope.audience
        ):
            raise ValueError("authority key descriptor and envelope disagree")
        if not self.envelope.has_valid_sha256():
            raise ValueError("authority key envelope digest is invalid")
        return self


class _AuthorityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_type: Literal["tiangong.gateway.runtime-authority.v1"] = (
        "tiangong.gateway.runtime-authority.v1"
    )
    revision: int = Field(ge=1)
    component_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)
    keys: tuple[_AuthorityKey, _AuthorityKey]
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_keys(self) -> Self:
        if tuple(item.purpose for item in self.keys) != (
            "delivery_ticket",
            "execution_ticket",
        ):
            raise ValueError("authority keys must be complete, sorted and audience-separated")
        if len({item.descriptor.kid for item in self.keys}) != 2:
            raise ValueError("authority key IDs must be unique")
        if any(
            item.descriptor.component_manifest_hash != self.component_manifest_sha256
            for item in self.keys
        ):
            raise ValueError("authority key is bound to another component manifest")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"record_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.record_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"record_sha256": self.computed_sha256()})


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeAuthorityError("authority record contains duplicate JSON keys")
        result[key] = value
    return result


def _write_record(path: Path, record: _AuthorityRecord) -> None:
    payload = canonical_json_bytes(record.model_dump(mode="json")) + b"\n"
    temporary = path.with_suffix(".json.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RuntimeTicketAuthority:
    def __init__(
        self,
        record: _AuthorityRecord,
        execution_signer: TicketSigner,
        delivery_signer: TicketSigner,
    ) -> None:
        self._record = record
        self.execution_signer = execution_signer
        self.delivery_signer = delivery_signer

    @property
    def component_manifest_sha256(self) -> str:
        return self._record.component_manifest_sha256

    @classmethod
    def open(
        cls,
        root: Path,
        component_manifest: ComponentManifest,
        *,
        now_ms: int,
        protector: DataProtector | None = None,
    ) -> "RuntimeTicketAuthority":
        if not component_manifest.has_valid_manifest_sha256() or now_ms < 0:
            raise ValueError("runtime authority component manifest or time is invalid")
        if not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("runtime authority root is unsafe")
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise RuntimeAuthorityError("runtime authority root is unsafe")
        path = root / "authority.json"
        key_store = ProtectedKeyStore(root, protector=protector)
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 262_144:
                raise RuntimeAuthorityError("runtime authority record is unsafe")
            raw = path.read_bytes()
            try:
                json.loads(
                    raw.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                record = _AuthorityRecord.model_validate_json(raw, strict=True)
            except RuntimeAuthorityError:
                raise
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise RuntimeAuthorityError("runtime authority record is invalid") from exc
            if raw != canonical_json_bytes(record.model_dump(mode="json")) + b"\n":
                raise RuntimeAuthorityError("runtime authority record is not canonical")
            if not record.has_valid_sha256():
                raise RuntimeAuthorityError("runtime authority record digest is invalid")
        else:
            if any(root.iterdir()):
                # The keys directory is created by ProtectedKeyStore and is the
                # only permitted pre-record entry on first initialization.
                entries = tuple(root.iterdir())
                if any(item.name != "keys" or any(item.iterdir()) for item in entries):
                    raise RuntimeAuthorityError("unidentified runtime authority state is not empty")
            component_sha = component_manifest.manifest_sha256
            valid_until = now_ms + 5 * 366 * 24 * 60 * 60 * 1_000
            created = []
            for purpose, audience, prefix in (
                ("delivery_ticket", "tiangong-communication-service", "delivery"),
                ("execution_ticket", "tiangong-backend", "execution"),
            ):
                created_key = key_store.create_key(
                    kid=f"{prefix}-{secrets.token_hex(16)}",
                    purpose=purpose,  # type: ignore[arg-type]
                    audience=audience,
                    issuer="tiangong-total-gateway",
                    not_before_ms=max(0, now_ms - 5_000),
                    not_after_ms=valid_until,
                    component_manifest_hash=component_sha,
                    created_at_ms=now_ms,
                )
                created.append(
                    _AuthorityKey(
                        purpose=purpose,  # type: ignore[arg-type]
                        descriptor=created_key.public_descriptor,
                        envelope=created_key.private_envelope,
                    )
                )
            record = _AuthorityRecord(
                revision=1,
                component_manifest_sha256=component_sha,
                created_at_ms=now_ms,
                keys=tuple(created),  # type: ignore[arg-type]
                record_sha256="0" * 64,
            ).with_computed_sha256()
            _write_record(path, record)
        if record.component_manifest_sha256 != component_manifest.manifest_sha256:
            raise RuntimeAuthorityError("runtime authority is bound to another component manifest")
        by_purpose = {item.purpose: item for item in record.keys}
        execution = by_purpose["execution_ticket"]
        delivery = by_purpose["delivery_ticket"]
        return cls(
            record,
            TicketSigner(
                execution.descriptor.kid,
                key_store.load_private_key(execution.envelope),
            ),
            TicketSigner(
                delivery.descriptor.kid,
                key_store.load_private_key(delivery.envelope),
            ),
        )

    def execution_trust_bundle(self, *, gateway_epoch: int, now_ms: int) -> TrustBundle:
        return self._trust_bundle("execution_ticket", gateway_epoch=gateway_epoch, now_ms=now_ms)

    def delivery_trust_bundle(self, *, gateway_epoch: int, now_ms: int) -> TrustBundle:
        return self._trust_bundle("delivery_ticket", gateway_epoch=gateway_epoch, now_ms=now_ms)

    def _trust_bundle(
        self,
        purpose: Literal["execution_ticket", "delivery_ticket"],
        *,
        gateway_epoch: int,
        now_ms: int,
    ) -> TrustBundle:
        key = next(item.descriptor for item in self._record.keys if item.purpose == purpose)
        return TrustBundle(
            bundle_id=f"trust-{purpose}-{gateway_epoch}",
            revision=self._record.revision,
            gateway_epoch=gateway_epoch,
            generated_at_ms=now_ms,
            required_scopes=(
                TrustScope(
                    issuer=key.issuer,
                    audience=key.audience,
                    purpose=key.purpose,
                ),
            ),
            keys=(key,),
            production_ready=True,
            bundle_sha256="0" * 64,
        ).with_computed_sha256()


__all__ = ["RuntimeAuthorityError", "RuntimeTicketAuthority"]
