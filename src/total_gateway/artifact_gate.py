"""Path-free artifact admission gate over immutable content-addressed objects."""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Callable

from contracts import ArtifactManifest, canonical_sha256, derive_artifact_revision_identity
from contracts.models import validate_safe_filename
from runtime_security import ArchiveInspectionError, inspect_archive

from .fact_ledger import FactBatchRecord, FactLedger
from .object_store import ContentAddressedObjectStore, ObjectReference


class ArtifactGateError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArtifactCandidate:
    producer_fact_id: str
    object_id: str
    expected_sha256: str
    expected_size_bytes: int
    run_sequence: int
    artifact_intent_id: str
    revision: int
    workspace_id: str
    filename: str
    declared_mime: str
    format_id: str
    created_at_ms: int


@dataclass(frozen=True)
class ArtifactBaseEvidence:
    check_id: str
    check_version: str
    object_id: str
    content_sha256: str
    size_bytes: int
    mime: str
    filename: str
    format_id: str
    magic_verified: bool
    structure_verified: bool
    immutable_read_count: int
    structure_summary: tuple[tuple[str, str | int | bool], ...]
    evidence_sha256: str

    def computed_sha256(self) -> str:
        return canonical_sha256(
            {
                "check_id": self.check_id,
                "check_version": self.check_version,
                "object_id": self.object_id,
                "content_sha256": self.content_sha256,
                "size_bytes": self.size_bytes,
                "mime": self.mime,
                "filename": self.filename,
                "format_id": self.format_id,
                "magic_verified": self.magic_verified,
                "structure_verified": self.structure_verified,
                "immutable_read_count": self.immutable_read_count,
                "structure_summary": self.structure_summary,
            }
        )

    def has_valid_sha256(self) -> bool:
        return self.evidence_sha256 == self.computed_sha256()


@dataclass(frozen=True)
class ArtifactGateResult:
    manifest: ArtifactManifest
    evidence: ArtifactBaseEvidence


@dataclass(frozen=True)
class _FormatPolicy:
    mime: str | None  # None = 兜底格式（other），不锁定 mime 等值
    extensions: tuple[str, ...]  # 空 = 不校验扩展名（仅兜底格式）
    artifact_kind: str
    validator: Callable[[bytes], dict[str, str | int | bool]]


def _reject_constant(_: str) -> None:
    raise ArtifactGateError("artifact.json.non_finite")


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactGateError("artifact.json.duplicate_key")
        result[key] = value
    return result


def _validate_zip(data: bytes, *, profile: str) -> dict[str, str | int | bool]:
    try:
        inspection = inspect_archive(data, profile=profile)
    except ArchiveInspectionError as exc:
        raise ArtifactGateError("artifact.structure.zip." + exc.code) from exc
    return {
        "entry_count": inspection.entry_count,
        "total_uncompressed_bytes": inspection.total_expanded_bytes,
        "ooxml_required_parts_verified": profile != "zip" and inspection.required_parts_verified,
        "archive_profile": profile,
    }


def _validate_docx(data: bytes) -> dict[str, str | int | bool]:
    return _validate_zip(data, profile="docx")


def _validate_xlsx(data: bytes) -> dict[str, str | int | bool]:
    return _validate_zip(data, profile="xlsx")


def _validate_pptx(data: bytes) -> dict[str, str | int | bool]:
    return _validate_zip(data, profile="pptx")


def _validate_plain_zip(data: bytes) -> dict[str, str | int | bool]:
    return _validate_zip(data, profile="zip")


def _validate_binary(data: bytes) -> dict[str, str | int | bool]:
    if not data:
        raise ArtifactGateError("artifact.structure.binary_empty")
    executable_magics = (
        b"MZ",
        b"\x7fELF",
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"#!",
    )
    if any(data.startswith(prefix) for prefix in executable_magics):
        raise ArtifactGateError("artifact.binary.executable_forbidden")
    return {"binary_bytes": len(data), "executable_magic_absent": True}


def _validate_mp4(data: bytes) -> dict[str, str | int | bool]:
    _validate_binary(data)
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise ArtifactGateError("artifact.magic.mp4_invalid")
    return {"mp4_ftyp": data[8:12].decode("ascii", errors="ignore"), "binary_bytes": len(data)}


def _validate_gif(data: bytes) -> dict[str, str | int | bool]:
    _validate_binary(data)
    if not (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
        raise ArtifactGateError("artifact.magic.gif_invalid")
    return {"gif_header": data[:6].decode("ascii"), "binary_bytes": len(data)}


def _validate_webp(data: bytes) -> dict[str, str | int | bool]:
    _validate_binary(data)
    if len(data) < 12 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
        raise ArtifactGateError("artifact.magic.webp_invalid")
    return {"webp_riff": True, "binary_bytes": len(data)}


def _validate_mp3(data: bytes) -> dict[str, str | int | bool]:
    _validate_binary(data)
    if not (data.startswith(b"ID3") or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)):
        raise ArtifactGateError("artifact.magic.mp3_invalid")
    return {"mp3_frame_or_id3": True, "binary_bytes": len(data)}


def _validate_wav(data: bytes) -> dict[str, str | int | bool]:
    _validate_binary(data)
    if len(data) < 12 or not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        raise ArtifactGateError("artifact.magic.wav_invalid")
    return {"wav_riff": True, "binary_bytes": len(data)}


def _validate_pdf(data: bytes) -> dict[str, str | int | bool]:
    if not data.startswith(b"%PDF-"):
        raise ArtifactGateError("artifact.magic.pdf_invalid")
    if b"%%EOF" not in data[-2048:]:
        raise ArtifactGateError("artifact.structure.pdf_eof_missing")
    version = data[5:8].decode("ascii", errors="ignore")
    if not re.fullmatch(r"[1-2]\.[0-9]", version):
        raise ArtifactGateError("artifact.structure.pdf_version_invalid")
    return {"pdf_version": version, "eof_verified": True}


def _validate_png(data: bytes) -> dict[str, str | int | bool]:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ArtifactGateError("artifact.magic.png_invalid")
    offset = len(signature)
    chunks = 0
    width = height = 0
    saw_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ArtifactGateError("artifact.structure.png_truncated")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ArtifactGateError("artifact.structure.png_truncated")
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            raise ArtifactGateError("artifact.structure.png_crc_invalid")
        chunks += 1
        if chunks == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise ArtifactGateError("artifact.structure.png_ihdr_invalid")
            width, height = struct.unpack(">II", chunk_data[:8])
            if width == 0 or height == 0:
                raise ArtifactGateError("artifact.structure.png_dimensions_invalid")
        if chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise ArtifactGateError("artifact.structure.png_iend_invalid")
            saw_iend = True
            break
        offset = end
        if chunks > 100_000:
            raise ArtifactGateError("artifact.structure.png_chunk_count")
    if not saw_iend:
        raise ArtifactGateError("artifact.structure.png_iend_missing")
    return {"chunk_count": chunks, "width": width, "height": height}


def _validate_jpeg(data: bytes) -> dict[str, str | int | bool]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        raise ArtifactGateError("artifact.magic.jpeg_invalid")
    if not data.endswith(b"\xff\xd9") or b"\xff\xda" not in data:
        raise ArtifactGateError("artifact.structure.jpeg_invalid")
    return {"start_of_scan": True, "end_of_image": True}


def _validate_json(data: bytes) -> dict[str, str | int | bool]:
    try:
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except ArtifactGateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactGateError("artifact.structure.json_invalid") from exc
    return {"json_root_type": type(value).__name__}


def _validate_text(data: bytes) -> dict[str, str | int | bool]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ArtifactGateError("artifact.structure.text_utf8_invalid") from exc
    if "\x00" in text:
        raise ArtifactGateError("artifact.structure.text_nul_forbidden")
    return {"character_count": len(text), "utf8_verified": True}


_FORMAT_POLICIES = {
    "docx": _FormatPolicy(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        (".docx",),
        "document",
        _validate_docx,
    ),
    "xlsx": _FormatPolicy(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        (".xlsx",),
        "data",
        _validate_xlsx,
    ),
    "pptx": _FormatPolicy(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        (".pptx",),
        "document",
        _validate_pptx,
    ),
    "pdf": _FormatPolicy("application/pdf", (".pdf",), "document", _validate_pdf),
    "zip": _FormatPolicy("application/zip", (".zip",), "archive", _validate_plain_zip),
    "png": _FormatPolicy("image/png", (".png",), "image", _validate_png),
    "jpeg": _FormatPolicy("image/jpeg", (".jpg", ".jpeg"), "image", _validate_jpeg),
    "json": _FormatPolicy("application/json", (".json",), "data", _validate_json),
    "text": _FormatPolicy(
        "text/plain",
        (".txt", ".md", ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".xml", ".opml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".ps1", ".bat", ".cmd", ".sh", ".sql"),
        "data",
        _validate_text,
    ),
    "csv": _FormatPolicy("text/csv", (".csv",), "data", _validate_text),
    "binary": _FormatPolicy("application/octet-stream", (".bin", ".dat"), "other", _validate_binary),
    # D-22 格式全覆盖：产品可产出的媒体类型 + 通用 other 兜底（mime/扩展不锁定，
    # 仍执行禁可执行魔数与读回一致性校验），杜绝"格式不支持"误拒。
    "mp4": _FormatPolicy("video/mp4", (".mp4",), "video", _validate_mp4),
    "gif": _FormatPolicy("image/gif", (".gif",), "image", _validate_gif),
    "webp": _FormatPolicy("image/webp", (".webp",), "image", _validate_webp),
    "mp3": _FormatPolicy("audio/mpeg", (".mp3",), "audio", _validate_mp3),
    "wav": _FormatPolicy("audio/wav", (".wav",), "audio", _validate_wav),
    "other": _FormatPolicy(None, (), "other", _validate_binary),
}


class ArtifactGate:
    def __init__(
        self,
        object_store: ContentAddressedObjectStore,
        fact_ledger: FactLedger,
        *,
        max_artifact_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if not 1 <= max_artifact_bytes <= 2_147_483_648:
            raise ValueError("artifact size limit is invalid")
        self._object_store = object_store
        self._fact_ledger = fact_ledger
        self._max_artifact_bytes = max_artifact_bytes

    def accept(self, candidate: ArtifactCandidate) -> ArtifactGateResult:
        if (
            candidate.expected_size_bytes < 1
            or candidate.expected_size_bytes > self._max_artifact_bytes
            or not re.fullmatch(r"[0-9a-f]{64}", candidate.expected_sha256)
            or candidate.run_sequence < 1
            or candidate.revision < 1
            or candidate.created_at_ms < 0
        ):
            raise ArtifactGateError("artifact.candidate.invalid")
        policy = _FORMAT_POLICIES.get(candidate.format_id)
        if policy is None:
            raise ArtifactGateError("artifact.format.unsupported")
        if policy.mime is not None and candidate.declared_mime != policy.mime:
            raise ArtifactGateError("artifact.mime.mismatch")
        try:
            validate_safe_filename(candidate.filename)
        except ValueError as exc:
            raise ArtifactGateError("artifact.filename.unsafe") from exc
        folded_name = candidate.filename.casefold()
        if policy.extensions and not any(folded_name.endswith(extension) for extension in policy.extensions):
            raise ArtifactGateError("artifact.extension.mismatch")

        producer = self._fact_ledger.get_fact(candidate.producer_fact_id)
        batch = self._fact_ledger.get_batch_for_fact(candidate.producer_fact_id)
        if producer is None or batch is None:
            raise ArtifactGateError("artifact.producer_fact.missing")
        if (
            producer.fact_type != "execution.succeeded"
            or producer.source_component_id != "tiangong-backend"
            or producer.effect_id != batch.result.effect_id
            or candidate.object_id not in batch.result.output_object_refs
        ):
            raise ArtifactGateError("artifact.producer_fact.invalid")
        reference = self._object_store.get_reference(candidate.object_id)
        if reference is None:
            raise ArtifactGateError("artifact.object.missing")
        self._verify_reference(candidate, reference, batch)

        first = self._object_store.read_bytes(candidate.object_id)
        if len(first) != reference.size_bytes or hashlib.sha256(first).hexdigest() != reference.sha256:
            raise ArtifactGateError("artifact.object.readback_mismatch")
        summary = policy.validator(first)
        second = self._object_store.read_bytes(candidate.object_id)
        if first != second or hashlib.sha256(second).hexdigest() != reference.sha256:
            raise ArtifactGateError("artifact.object.changed_after_validation")

        identity = derive_artifact_revision_identity(
            request_id=producer.request_id,
            run_id=producer.run_id,
            run_sequence=candidate.run_sequence,
            generation=producer.generation,
            artifact_intent_id=candidate.artifact_intent_id,
            revision=candidate.revision,
            content_sha256=reference.sha256,
        )
        manifest = ArtifactManifest(
            artifact_id=identity.artifact_id,
            artifact_revision_id=identity.artifact_revision_id,
            revision=candidate.revision,
            request_id=producer.request_id,
            run_id=producer.run_id,
            generation=producer.generation,
            source_effect_id=producer.effect_id,
            producer_fact_id=producer.fact_id,
            tenant_id=reference.tenant_id,
            link_account_id=reference.link_account_id,
            conversation_scope_hash=reference.conversation_scope_hash,
            workspace_id=candidate.workspace_id,
            content_object_id=reference.object_id,
            sha256=reference.sha256,
            size_bytes=reference.size_bytes,
            mime=policy.mime or candidate.declared_mime,
            filename=candidate.filename,
            artifact_kind=policy.artifact_kind,
            format_id=candidate.format_id,
            created_at_ms=candidate.created_at_ms,
            qc_state="PENDING",
            qc_evidence=(),
            manifest_sha256="0" * 64,
        ).with_computed_manifest_sha256()
        self._object_store.register_revision(
            manifest.artifact_id,
            manifest.revision,
            reference.object_id,
            manifest_sha256=manifest.manifest_sha256,
            created_at_ms=manifest.created_at_ms,
        )
        ordered_summary = tuple(sorted(summary.items()))
        evidence = ArtifactBaseEvidence(
            check_id="artifact.base_integrity",
            check_version="1.0.0",
            object_id=reference.object_id,
            content_sha256=reference.sha256,
            size_bytes=reference.size_bytes,
            mime=policy.mime or candidate.declared_mime,
            filename=candidate.filename,
            format_id=candidate.format_id,
            magic_verified=True,
            structure_verified=True,
            immutable_read_count=2,
            structure_summary=ordered_summary,
            evidence_sha256="0" * 64,
        )
        evidence = ArtifactBaseEvidence(
            **{**evidence.__dict__, "evidence_sha256": evidence.computed_sha256()}
        )
        return ArtifactGateResult(manifest=manifest, evidence=evidence)

    @staticmethod
    def _verify_reference(
        candidate: ArtifactCandidate,
        reference: ObjectReference,
        batch: FactBatchRecord,
    ) -> None:
        if reference.kind != "artifact":
            raise ArtifactGateError("artifact.object.kind_mismatch")
        if reference.sha256 != candidate.expected_sha256:
            raise ArtifactGateError("artifact.object.digest_mismatch")
        if reference.size_bytes != candidate.expected_size_bytes:
            raise ArtifactGateError("artifact.object.size_mismatch")
        if reference.size_bytes > batch.max_output_bytes:
            raise ArtifactGateError("artifact.object.ticket_limit_exceeded")
        if (
            reference.tenant_id != batch.tenant_id
            or reference.link_account_id != batch.link_account_id
            or reference.conversation_scope_hash != batch.conversation_scope_hash
            or candidate.workspace_id != batch.workspace_id
        ):
            raise ArtifactGateError("artifact.object.scope_mismatch")


__all__ = [
    "ArtifactBaseEvidence",
    "ArtifactCandidate",
    "ArtifactGate",
    "ArtifactGateError",
    "ArtifactGateResult",
]
