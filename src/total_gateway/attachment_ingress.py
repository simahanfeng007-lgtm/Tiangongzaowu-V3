"""Authenticated streaming attachment ingress from transport-only 7176."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import BinaryIO

from contracts import canonical_json_bytes

from .object_store import ContentAddressedObjectStore


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")


class AttachmentIngressError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AttachmentIngressMetadata:
    tenant_id: str
    link_account_id: str
    conversation_scope_hash: str
    content_sha256: str
    size_bytes: int
    mime: str
    filename: str
    created_at_ms: int

    @classmethod
    def from_header(cls, value: str) -> "AttachmentIngressMetadata":
        if not value or len(value) > 16_384 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise AttachmentIngressError("attachment_ingress.metadata.invalid")
        try:
            padding = "=" * (-len(value) % 4)
            raw = base64.urlsafe_b64decode((value + padding).encode("ascii"))

            def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, item in items:
                    if key in result:
                        raise AttachmentIngressError("attachment_ingress.metadata.duplicate_key")
                    result[key] = item
                return result

            payload = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except AttachmentIngressError:
            raise
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise AttachmentIngressError("attachment_ingress.metadata.invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "content_sha256",
            "conversation_scope_hash",
            "created_at_ms",
            "filename",
            "link_account_id",
            "mime",
            "size_bytes",
            "tenant_id",
        }:
            raise AttachmentIngressError("attachment_ingress.metadata.fields_invalid")
        if canonical_json_bytes(payload) != raw:
            raise AttachmentIngressError("attachment_ingress.metadata.noncanonical")
        try:
            metadata = cls(
                tenant_id=payload["tenant_id"],
                link_account_id=payload["link_account_id"],
                conversation_scope_hash=payload["conversation_scope_hash"],
                content_sha256=payload["content_sha256"],
                size_bytes=payload["size_bytes"],
                mime=payload["mime"],
                filename=payload["filename"],
                created_at_ms=payload["created_at_ms"],
            )
        except TypeError as exc:
            raise AttachmentIngressError("attachment_ingress.metadata.types_invalid") from exc
        metadata.validate()
        return metadata

    def validate(self) -> None:
        if (
            not isinstance(self.tenant_id, str)
            or not _OPAQUE.fullmatch(self.tenant_id)
            or not isinstance(self.link_account_id, str)
            or not _OPAQUE.fullmatch(self.link_account_id)
            or not isinstance(self.conversation_scope_hash, str)
            or not _SHA256.fullmatch(self.conversation_scope_hash)
            or not isinstance(self.content_sha256, str)
            or not _SHA256.fullmatch(self.content_sha256)
            or isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or not 1 <= self.size_bytes <= 536_870_912
            or isinstance(self.created_at_ms, bool)
            or not isinstance(self.created_at_ms, int)
            or self.created_at_ms < 0
            or not isinstance(self.mime, str)
            or not 1 <= len(self.mime) <= 255
            or not isinstance(self.filename, str)
            or not 1 <= len(self.filename) <= 255
            or "\x00" in self.filename
        ):
            raise AttachmentIngressError("attachment_ingress.metadata.values_invalid")


def encode_attachment_metadata(metadata: Mapping[str, object]) -> str:
    raw = canonical_json_bytes(dict(metadata))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class GatewayAttachmentIngress:
    def __init__(self, objects: ContentAddressedObjectStore) -> None:
        self._objects = objects

    @staticmethod
    def _chunks(stream: BinaryIO, remaining: int) -> Iterator[bytes]:
        total = 0
        while total < remaining:
            chunk = stream.read(min(262_144, remaining - total))
            if not chunk:
                raise AttachmentIngressError("attachment_ingress.body.truncated")
            total += len(chunk)
            yield chunk
        if total != remaining:
            raise AttachmentIngressError("attachment_ingress.body.size_mismatch")

    def accept(
        self,
        metadata_header: str,
        stream: BinaryIO,
        *,
        content_length: int,
    ) -> dict[str, object]:
        metadata = AttachmentIngressMetadata.from_header(metadata_header)
        if content_length != metadata.size_bytes:
            raise AttachmentIngressError("attachment_ingress.body.declared_size_mismatch")
        stored = self._objects.put_stream(
            self._chunks(stream, content_length),
            kind="attachment",
            tenant_id=metadata.tenant_id,
            link_account_id=metadata.link_account_id,
            conversation_scope_hash=metadata.conversation_scope_hash,
            created_at_ms=metadata.created_at_ms,
            max_bytes=metadata.size_bytes,
        ).reference
        if stored.sha256 != metadata.content_sha256 or stored.size_bytes != metadata.size_bytes:
            raise AttachmentIngressError("attachment_ingress.body.digest_mismatch")
        return {
            "object_id": stored.object_id,
            "revision": 1,
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "evidence_sha256": hashlib.sha256(
                canonical_json_bytes(
                    {
                        "metadata": metadata.__dict__,
                        "object_reference_sha256": stored.reference_sha256,
                    }
                )
            ).hexdigest(),
        }


__all__ = [
    "AttachmentIngressError",
    "AttachmentIngressMetadata",
    "GatewayAttachmentIngress",
    "encode_attachment_metadata",
]
