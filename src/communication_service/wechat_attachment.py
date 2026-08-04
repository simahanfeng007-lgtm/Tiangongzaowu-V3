"""Security admission from decrypted WeChat media to immutable AttachmentRef."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from contracts import AttachmentRef
from contracts.models import validate_safe_filename
from runtime_security import ArchiveInspectionError, ArchiveLimits, inspect_archive

from .attachment_quarantine import (
    AttachmentQuarantineLedger,
    AttachmentQuotaPolicy,
)
from .wechat_media import (
    DownloadedWechatMedia,
    WechatMediaDownloader,
    WechatMediaReference,
)


class WechatAttachmentError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StoredAttachmentObject:
    object_id: str
    revision: int
    sha256: str
    size_bytes: int


class AttachmentObjectSink(Protocol):
    def put_attachment(
        self,
        source: Path,
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        content_sha256: str,
        size_bytes: int,
        mime: str,
        filename: str,
        created_at_ms: int,
    ) -> StoredAttachmentObject: ...


@dataclass(frozen=True)
class WechatAttachmentCandidate:
    media: DownloadedWechatMedia
    filename: str
    declared_mime: str
    tenant_id: str
    link_account_id: str
    conversation_scope_hash: str
    source_message_ref: str
    created_at_ms: int


@dataclass(frozen=True)
class ValidatedAttachmentSource:
    """Immutable evidence produced by the shared attachment admission gate."""

    filename: str
    mime: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class _Format:
    mime: str
    kind: str
    profile: str


_FORMATS = {
    ".docx": _Format(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "document",
        "docx",
    ),
    ".xlsx": _Format(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "document",
        "xlsx",
    ),
    ".pptx": _Format(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "document",
        "pptx",
    ),
    ".pdf": _Format("application/pdf", "document", "pdf"),
    ".zip": _Format("application/zip", "archive", "zip"),
    ".png": _Format("image/png", "image", "png"),
    ".jpg": _Format("image/jpeg", "image", "jpeg"),
    ".jpeg": _Format("image/jpeg", "image", "jpeg"),
    ".gif": _Format("image/gif", "image", "gif"),
    ".mp4": _Format("video/mp4", "video", "mp4"),
    ".silk": _Format("audio/silk", "audio", "silk"),
    ".txt": _Format("text/plain", "data", "text"),
    ".md": _Format("text/markdown", "data", "text"),
    ".csv": _Format("text/csv", "data", "text"),
    ".json": _Format("application/json", "data", "json"),
    ".markdown": _Format("text/markdown", "data", "text"),
    ".jsonl": _Format("text/plain", "data", "text"),
    ".html": _Format("text/plain", "data", "text"),
    ".htm": _Format("text/plain", "data", "text"),
    ".xml": _Format("text/plain", "data", "text"),
    ".yaml": _Format("text/plain", "data", "text"),
    ".yml": _Format("text/plain", "data", "text"),
    ".toml": _Format("text/plain", "data", "text"),
    ".py": _Format("text/plain", "data", "text"),
    ".pyi": _Format("text/plain", "data", "text"),
    ".js": _Format("text/plain", "data", "text"),
    ".mjs": _Format("text/plain", "data", "text"),
    ".cjs": _Format("text/plain", "data", "text"),
    ".ts": _Format("text/plain", "data", "text"),
    ".tsx": _Format("text/plain", "data", "text"),
    ".jsx": _Format("text/plain", "data", "text"),
    ".css": _Format("text/plain", "data", "text"),
    ".scss": _Format("text/plain", "data", "text"),
    ".less": _Format("text/plain", "data", "text"),
    ".vue": _Format("text/plain", "data", "text"),
    ".svelte": _Format("text/plain", "data", "text"),
    ".java": _Format("text/plain", "data", "text"),
    ".c": _Format("text/plain", "data", "text"),
    ".cc": _Format("text/plain", "data", "text"),
    ".cpp": _Format("text/plain", "data", "text"),
    ".h": _Format("text/plain", "data", "text"),
    ".hpp": _Format("text/plain", "data", "text"),
    ".cs": _Format("text/plain", "data", "text"),
    ".go": _Format("text/plain", "data", "text"),
    ".rs": _Format("text/plain", "data", "text"),
    ".php": _Format("text/plain", "data", "text"),
    ".rb": _Format("text/plain", "data", "text"),
    ".swift": _Format("text/plain", "data", "text"),
    ".kt": _Format("text/plain", "data", "text"),
    ".kts": _Format("text/plain", "data", "text"),
    ".sql": _Format("text/plain", "data", "text"),
    ".ini": _Format("text/plain", "data", "text"),
    ".conf": _Format("text/plain", "data", "text"),
    ".cfg": _Format("text/plain", "data", "text"),
    ".log": _Format("text/plain", "data", "text"),
    ".bat": _Format("text/plain", "data", "text"),
    ".cmd": _Format("text/plain", "data", "text"),
    ".ps1": _Format("text/plain", "data", "text"),
    ".sh": _Format("text/plain", "data", "text"),
}
_ACTIVE_ARCHIVE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".ps1",
    ".scr",
    ".vbs",
    ".vbe",
}


def expected_attachment_mime(filename: str) -> str:
    try:
        validate_safe_filename(filename)
    except ValueError as exc:
        raise WechatAttachmentError("wechat.attachment.filename_unsafe") from exc
    policy = _FORMATS.get(Path(filename).suffix.casefold())
    if policy is None:
        raise WechatAttachmentError("wechat.attachment.extension_forbidden")
    return policy.mime


def _read_prefix_tail(path: Path, *, prefix_bytes: int = 64, tail_bytes: int = 2_048) -> tuple[bytes, bytes]:
    with path.open("rb") as stream:
        prefix = stream.read(prefix_bytes)
        size = path.stat().st_size
        stream.seek(max(0, size - tail_bytes))
        tail = stream.read(tail_bytes)
    return prefix, tail


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(262_144)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _validate_nonarchive(path: Path, profile: str) -> None:
    prefix, tail = _read_prefix_tail(path)
    if profile == "pdf":
        if not prefix.startswith(b"%PDF-") or b"%%EOF" not in tail:
            raise WechatAttachmentError("wechat.attachment.magic.pdf_invalid")
    elif profile == "png":
        if not prefix.startswith(b"\x89PNG\r\n\x1a\n") or not tail.endswith(b"IEND\xaeB`\x82"):
            raise WechatAttachmentError("wechat.attachment.magic.png_invalid")
    elif profile == "jpeg":
        if not prefix.startswith(b"\xff\xd8") or not tail.endswith(b"\xff\xd9"):
            raise WechatAttachmentError("wechat.attachment.magic.jpeg_invalid")
    elif profile == "gif":
        if not prefix.startswith((b"GIF87a", b"GIF89a")) or not tail.endswith(b";"):
            raise WechatAttachmentError("wechat.attachment.magic.gif_invalid")
    elif profile == "mp4":
        if len(prefix) < 12 or prefix[4:8] != b"ftyp":
            raise WechatAttachmentError("wechat.attachment.magic.mp4_invalid")
    elif profile == "silk":
        if not prefix.startswith((b"#!SILK_V3", b"\x02#!SILK_V3")):
            raise WechatAttachmentError("wechat.attachment.magic.silk_invalid")
    elif profile in {"text", "json"}:
        if path.stat().st_size > 16_777_216:
            raise WechatAttachmentError("wechat.attachment.text_too_large")
        prefix, _ = _read_prefix_tail(path, prefix_bytes=4, tail_bytes=1)
        if prefix.startswith(b"\xef\xbb\xbf"):
            raise WechatAttachmentError("wechat.attachment.text_utf8_bom_forbidden")
        if prefix.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")):
            raise WechatAttachmentError("wechat.attachment.text_encoding_forbidden")
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WechatAttachmentError("wechat.attachment.text_utf8_invalid") from exc
        if "\x00" in text:
            raise WechatAttachmentError("wechat.attachment.text_nul_forbidden")
        if profile == "json":
            def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in items:
                    if key in result:
                        raise WechatAttachmentError("wechat.attachment.json_duplicate_key")
                    result[key] = value
                return result

            try:
                json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except WechatAttachmentError:
                raise
            except (json.JSONDecodeError, ValueError) as exc:
                raise WechatAttachmentError("wechat.attachment.json_invalid") from exc


def validate_attachment_source(
    source: Path,
    *,
    filename: str,
    declared_mime: str,
    max_attachment_bytes: int = 536_870_912,
    archive_limits: ArchiveLimits = ArchiveLimits(),
) -> ValidatedAttachmentSource:
    """Validate one stable quarantined file for every local/channel ingress.

    The caller owns path containment and quarantine naming.  This function is
    the single content-policy authority used by desktop, WeChat and Feishu so
    no transport can receive a weaker file-admission policy.
    """

    if not 1 <= max_attachment_bytes <= 2_147_483_648:
        raise ValueError("attachment size limit is invalid")
    if source.is_symlink() or not source.is_file():
        raise WechatAttachmentError("wechat.attachment.source_unsafe")
    try:
        validate_safe_filename(filename)
    except ValueError as exc:
        raise WechatAttachmentError("wechat.attachment.filename_unsafe") from exc
    extension = Path(filename).suffix.casefold()
    policy = _FORMATS.get(extension)
    if policy is None:
        raise WechatAttachmentError("wechat.attachment.extension_forbidden")
    if not isinstance(declared_mime, str) or declared_mime.casefold() != policy.mime:
        raise WechatAttachmentError("wechat.attachment.mime_mismatch")
    first_size, first_sha = _hash_file(source)
    if first_size < 1 or first_size > max_attachment_bytes:
        raise WechatAttachmentError("wechat.attachment.download_evidence_mismatch")

    if policy.profile in {"zip", "docx", "xlsx", "pptx"}:
        try:
            inspection = inspect_archive(
                source,
                profile=policy.profile,  # type: ignore[arg-type]
                limits=archive_limits,
            )
        except ArchiveInspectionError as exc:
            raise WechatAttachmentError("wechat.attachment." + exc.code) from exc
        if policy.profile == "zip" and any(
            Path(name).suffix.casefold() in _ACTIVE_ARCHIVE_SUFFIXES
            for name in inspection.entry_names
        ):
            raise WechatAttachmentError("wechat.attachment.archive_active_content")
    else:
        _validate_nonarchive(source, policy.profile)

    second_size, second_sha = _hash_file(source)
    if (second_size, second_sha) != (first_size, first_sha):
        raise WechatAttachmentError("wechat.attachment.changed_during_validation")
    return ValidatedAttachmentSource(
        filename=filename,
        mime=policy.mime,
        size_bytes=first_size,
        sha256=first_sha,
    )


class WechatAttachmentGate:
    def __init__(
        self,
        staging_root: Path,
        sink: AttachmentObjectSink,
        ledger: AttachmentQuarantineLedger,
        *,
        quota_policy: AttachmentQuotaPolicy = AttachmentQuotaPolicy(),
        archive_limits: ArchiveLimits = ArchiveLimits(),
        max_attachment_bytes: int = 536_870_912,
    ) -> None:
        if not staging_root.is_absolute() or staging_root == Path(staging_root.anchor):
            raise ValueError("attachment staging root is unsafe")
        if not 1 <= max_attachment_bytes <= 2_147_483_648:
            raise ValueError("attachment size limit is invalid")
        self._staging_root = staging_root.resolve(strict=False)
        self._sink = sink
        self._ledger = ledger
        self._quota = quota_policy
        self._archive_limits = archive_limits
        self._max_bytes = max_attachment_bytes

    def accept(self, candidate: WechatAttachmentCandidate) -> AttachmentRef:
        path = candidate.media.plaintext_path
        try:
            if path.is_symlink() or not path.is_file():
                raise WechatAttachmentError("wechat.attachment.source_unsafe")
            resolved = path.resolve(strict=True)
            try:
                resolved.relative_to(self._staging_root)
            except ValueError as exc:
                raise WechatAttachmentError("wechat.attachment.source_outside_staging") from exc
            if not resolved.name.endswith(".plain.part"):
                raise WechatAttachmentError("wechat.attachment.source_not_quarantined")
            validated = validate_attachment_source(
                resolved,
                filename=candidate.filename,
                declared_mime=candidate.declared_mime,
                max_attachment_bytes=self._max_bytes,
                archive_limits=self._archive_limits,
            )
            if (
                validated.size_bytes != candidate.media.plaintext_size_bytes
                or validated.sha256 != candidate.media.plaintext_sha256
            ):
                raise WechatAttachmentError("wechat.attachment.download_evidence_mismatch")
            stored = self._sink.put_attachment(
                resolved,
                tenant_id=candidate.tenant_id,
                link_account_id=candidate.link_account_id,
                conversation_scope_hash=candidate.conversation_scope_hash,
                content_sha256=validated.sha256,
                size_bytes=validated.size_bytes,
                mime=validated.mime,
                filename=validated.filename,
                created_at_ms=candidate.created_at_ms,
            )
            if (
                stored.sha256 != validated.sha256
                or stored.size_bytes != validated.size_bytes
                or stored.revision < 1
            ):
                raise WechatAttachmentError("wechat.attachment.object_sink_mismatch")
            reference = AttachmentRef(
                object_id=stored.object_id,
                revision=stored.revision,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                mime=validated.mime,
                filename=validated.filename,
                tenant_id=candidate.tenant_id,
                link_account_id=candidate.link_account_id,
                conversation_scope_hash=candidate.conversation_scope_hash,
                source_message_ref=candidate.source_message_ref,
                created_at_ms=candidate.created_at_ms,
                acceptance="accepted",
                magic_verified=True,
            )
            self._ledger.admit(
                reference,
                accepted_at_ms=candidate.created_at_ms,
                policy=self._quota,
            )
            return reference
        finally:
            candidate.media.cleanup()


class WechatInboundAttachmentIngestor:
    """Parse one exact iLink media item, decrypt it, and pass the shared gate."""

    _KINDS = frozenset({"image", "voice", "video", "file"})

    def __init__(
        self,
        downloader: WechatMediaDownloader,
        gate: WechatAttachmentGate,
    ) -> None:
        self._downloader = downloader
        self._gate = gate

    @staticmethod
    def _string(
        source: Mapping[str, Any],
        *names: str,
        required: bool = False,
        limit: int = 16_384,
    ) -> str | None:
        values = [source.get(name) for name in names if source.get(name) is not None]
        if len(values) > 1 and any(value != values[0] for value in values[1:]):
            raise WechatAttachmentError("wechat.attachment.media_field_conflict")
        if not values:
            if required:
                raise WechatAttachmentError("wechat.attachment.media_field_missing")
            return None
        value = values[0]
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or "\x00" in value
            or len(value.encode("utf-8")) > limit
        ):
            raise WechatAttachmentError("wechat.attachment.media_field_invalid")
        return value

    @staticmethod
    def _size(source: Mapping[str, Any], *names: str) -> int | None:
        values = [source.get(name) for name in names if source.get(name) is not None]
        if len(values) > 1 and any(value != values[0] for value in values[1:]):
            raise WechatAttachmentError("wechat.attachment.media_size_conflict")
        if not values:
            return None
        value = values[0]
        if isinstance(value, str) and value.isdecimal():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2_147_483_648:
            raise WechatAttachmentError("wechat.attachment.media_size_invalid")
        return value

    @staticmethod
    def _image_filename(media: DownloadedWechatMedia) -> str:
        with media.plaintext_path.open("rb") as stream:
            prefix = stream.read(16)
        if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif prefix.startswith(b"\xff\xd8"):
            extension = ".jpg"
        elif prefix.startswith((b"GIF87a", b"GIF89a")):
            extension = ".gif"
        else:
            raise WechatAttachmentError("wechat.attachment.image_magic_unknown")
        return "wechat-image-" + media.plaintext_sha256[:16] + extension

    def ingest_item(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        source_message_ref: str,
        created_at_ms: int,
    ) -> AttachmentRef:
        if kind not in self._KINDS or not isinstance(payload, Mapping):
            raise WechatAttachmentError("wechat.attachment.media_kind_invalid")
        nested = payload.get("media")
        if nested is not None:
            if not isinstance(nested, Mapping):
                raise WechatAttachmentError("wechat.attachment.media_invalid")
            media_fields = nested
        else:
            media_fields = payload
        reference = WechatMediaReference(
            encrypted_query_param=self._string(
                media_fields,
                "encrypted_query_param",
                "download_param",
            ),
            full_url=self._string(media_fields, "full_url", "download_url"),
            aes_key=self._string(
                media_fields,
                "aes_key",
                required=True,
                limit=128,
            ),
            declared_cipher_bytes=self._size(
                media_fields,
                "cipher_size",
                "encrypted_size",
            ),
            declared_plain_bytes=self._size(
                media_fields,
                "plain_size",
                "size",
                "file_size",
            ),
        )
        downloaded = self._downloader.download(reference)
        try:
            if kind == "image":
                filename = self._image_filename(downloaded)
            elif kind == "video":
                filename = "wechat-video-" + downloaded.plaintext_sha256[:16] + ".mp4"
            elif kind == "voice":
                filename = "wechat-voice-" + downloaded.plaintext_sha256[:16] + ".silk"
            else:
                filename = self._string(
                    payload,
                    "filename",
                    "file_name",
                    required=True,
                    limit=255,
                )
                assert filename is not None
            mime = expected_attachment_mime(filename)
            declared_mime = self._string(payload, "mime", "content_type", limit=255)
            if declared_mime is not None and declared_mime.casefold() != mime:
                raise WechatAttachmentError("wechat.attachment.mime_mismatch")
            return self._gate.accept(
                WechatAttachmentCandidate(
                    media=downloaded,
                    filename=filename,
                    declared_mime=mime,
                    tenant_id=tenant_id,
                    link_account_id=link_account_id,
                    conversation_scope_hash=conversation_scope_hash,
                    source_message_ref=source_message_ref,
                    created_at_ms=created_at_ms,
                )
            )
        except Exception:
            downloaded.cleanup()
            raise


__all__ = [
    "AttachmentObjectSink",
    "StoredAttachmentObject",
    "WechatAttachmentCandidate",
    "WechatAttachmentError",
    "WechatAttachmentGate",
    "WechatInboundAttachmentIngestor",
    "ValidatedAttachmentSource",
    "expected_attachment_mime",
    "validate_attachment_source",
]
