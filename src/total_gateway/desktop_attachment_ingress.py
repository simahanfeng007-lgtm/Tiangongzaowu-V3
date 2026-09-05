"""Trusted Electron-main attachment ingress for desktop conversations.

The renderer never grants host paths to the Python runtime. Electron's trusted
main process streams selected bytes here; the gateway validates content using
the same policy as WeChat/Feishu and returns a content-addressed AttachmentRef.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import BinaryIO

from runtime_security.path_identity import resolve_existing_path

from contracts import AttachmentRef, InboundScope, canonical_json_bytes, derive_inbound_scope_keys
from contracts.models import validate_safe_filename

from .object_store import ContentAddressedObjectStore


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_MIME = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)


def _opaque_attachment_mime(value: object) -> str:
    """Preserve a syntactically safe hint without treating it as admission."""

    candidate = str(value or "").split(";", 1)[0].strip().casefold()
    return candidate if _MIME.fullmatch(candidate) else "application/octet-stream"


class DesktopAttachmentIngressError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def encode_desktop_attachment_metadata(metadata: Mapping[str, object]) -> str:
    raw = canonical_json_bytes(dict(metadata))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_metadata(value: str) -> dict[str, object]:
    if not value or len(value) > 16_384 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise DesktopAttachmentIngressError("desktop_attachment.metadata.invalid")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode((value + padding).encode("ascii"))

        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in items:
                if key in result:
                    raise DesktopAttachmentIngressError("desktop_attachment.metadata.duplicate_key")
                result[key] = item
            return result

        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except DesktopAttachmentIngressError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise DesktopAttachmentIngressError("desktop_attachment.metadata.invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "content_sha256",
        "created_at_ms",
        "filename",
        "mime",
        "session_id",
        "size_bytes",
    }:
        raise DesktopAttachmentIngressError("desktop_attachment.metadata.fields_invalid")
    if canonical_json_bytes(payload) != raw:
        raise DesktopAttachmentIngressError("desktop_attachment.metadata.noncanonical")
    return payload


class DesktopAttachmentIngress:
    def __init__(
        self,
        objects: ContentAddressedObjectStore,
        staging_root: Path,
        *,
        max_attachment_bytes: int = 536_870_912,
    ) -> None:
        if not staging_root.is_absolute() or staging_root == Path(staging_root.anchor):
            raise ValueError("desktop attachment staging root is unsafe")
        if not 1 <= max_attachment_bytes <= 536_870_912:
            raise ValueError("desktop attachment size limit is invalid")
        staging_root.mkdir(parents=True, exist_ok=True)
        if staging_root.is_symlink() or not staging_root.is_dir():
            raise ValueError("desktop attachment staging root is unsafe")
        self._objects = objects
        self._staging_root = resolve_existing_path(staging_root)
        self._max_bytes = max_attachment_bytes

    @staticmethod
    def _chunks(path: Path) -> Iterator[bytes]:
        with path.open("rb") as stream:
            while chunk := stream.read(262_144):
                yield chunk

    def accept(
        self,
        metadata_header: str,
        stream: BinaryIO,
        *,
        content_length: int,
    ) -> AttachmentRef:
        payload = _decode_metadata(metadata_header)
        session_id = payload["session_id"]
        filename = payload["filename"]
        mime = payload["mime"]
        digest = payload["content_sha256"]
        size_bytes = payload["size_bytes"]
        created_at_ms = payload["created_at_ms"]
        if (
            not isinstance(session_id, str)
            or _OPAQUE.fullmatch(session_id) is None
            or not isinstance(filename, str)
            or not 1 <= len(filename) <= 255
            or not isinstance(mime, str)
            or not 3 <= len(mime) <= 255
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or type(size_bytes) is not int
            or not 1 <= size_bytes <= self._max_bytes
            or type(created_at_ms) is not int
            or created_at_ms < 0
            or content_length != size_bytes
        ):
            raise DesktopAttachmentIngressError("desktop_attachment.metadata.values_invalid")

        scope = InboundScope(
            channel="desktop",
            tenant_id="desktop",
            link_account_id="desktop-local",
            conversation_ref=session_id,
            channel_message_ref="attachment-upload",
            sender_ref="desktop-user",
        )
        keys = derive_inbound_scope_keys(scope)
        temporary = self._staging_root / ("desktop-" + secrets.token_hex(16) + ".plain.part")
        actual_digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("xb") as target:
                while written < content_length:
                    chunk = stream.read(min(262_144, content_length - written))
                    if not chunk:
                        raise DesktopAttachmentIngressError("desktop_attachment.body.truncated")
                    written += len(chunk)
                    actual_digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if written != size_bytes or actual_digest.hexdigest() != digest:
                raise DesktopAttachmentIngressError("desktop_attachment.body.digest_mismatch")
            try:
                safe_filename = validate_safe_filename(filename)
            except ValueError as exc:
                raise DesktopAttachmentIngressError(
                    "desktop_attachment.filename_unsafe"
                ) from exc
            admitted_mime = _opaque_attachment_mime(mime)
            stored = self._objects.put_stream(
                self._chunks(temporary),
                kind="attachment",
                tenant_id="desktop",
                link_account_id="desktop-local",
                conversation_scope_hash=keys.conversation_scope_hash,
                created_at_ms=created_at_ms,
                max_bytes=size_bytes,
            ).reference
            if stored.sha256 != digest or stored.size_bytes != size_bytes:
                raise DesktopAttachmentIngressError("desktop_attachment.object_binding_mismatch")
            return AttachmentRef(
                object_id=stored.object_id,
                revision=1,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                mime=admitted_mime,
                filename=safe_filename,
                tenant_id="desktop",
                link_account_id="desktop-local",
                conversation_scope_hash=keys.conversation_scope_hash,
                source_message_ref=None,
                created_at_ms=created_at_ms,
                acceptance="accepted",
                # Legacy schema name: this now records verified byte length and
                # digest binding.  Desktop upload deliberately does not inspect
                # or reject the user's file format.
                magic_verified=True,
            )
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "DesktopAttachmentIngress",
    "DesktopAttachmentIngressError",
    "encode_desktop_attachment_metadata",
]
