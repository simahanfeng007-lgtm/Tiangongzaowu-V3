"""Deterministic safe/malicious file corpus shared by P6 channel matrices."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from html import escape

from contracts import canonical_sha256


@dataclass(frozen=True)
class SecurityFileCase:
    case_id: str
    filename: str
    declared_mime: str
    content: bytes
    expected_gate_accept: bool
    expected_reason_group: str
    declared_size_bytes: int | None = None

    @property
    def sha256(self) -> str:
        return __import__("hashlib").sha256(self.content).hexdigest()


def _build_zip(
    entries: tuple[tuple[str, bytes, int, bytes | None], ...],
) -> bytes:
    """Build a byte-stable ZIP without depending on ``zipfile`` internals.

    Python and zlib upgrades may legitimately change archive metadata or
    compressed byte streams.  Security test vectors must not drift with the
    developer machine, so the corpus writes the ZIP structures explicitly.
    """

    import binascii
    import struct

    local_parts: list[bytes] = []
    central_parts: list[bytes] = []
    offset = 0
    dos_time = 0
    dos_date = ((2026 - 1980) << 9) | (1 << 5) | 1
    for name, content, compression, compressed_override in entries:
        name_bytes = name.encode("utf-8")
        if compression == zipfile.ZIP_STORED:
            compressed = content
        elif compression == zipfile.ZIP_DEFLATED and compressed_override is not None:
            compressed = compressed_override
        else:
            raise ValueError("deterministic ZIP entries require fixed payload bytes")
        crc = binascii.crc32(content) & 0xFFFFFFFF
        local = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            0,
            compression,
            dos_time,
            dos_date,
            crc,
            len(compressed),
            len(content),
            len(name_bytes),
            0,
        ) + name_bytes + compressed
        central = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            (3 << 8) | 20,
            20,
            0,
            compression,
            dos_time,
            dos_date,
            crc,
            len(compressed),
            len(content),
            len(name_bytes),
            0,
            0,
            0,
            0,
            0o100600 << 16,
            offset,
        ) + name_bytes
        local_parts.append(local)
        central_parts.append(central)
        offset += len(local)
    central = b"".join(central_parts)
    end_record = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        len(entries),
        len(entries),
        len(central),
        offset,
        0,
    )
    return b"".join(local_parts) + central + end_record


def _deterministic_zip(entries: tuple[tuple[str, bytes], ...]) -> bytes:
    return _build_zip(
        tuple((name, content, zipfile.ZIP_STORED, None) for name, content in entries)
    )


_FIXED_RATIO_DEFLATE_B64 = (
    "7cGBAAAAAMMgpfnTHeRVAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAbwY="
)


def _deterministic_ratio_zip() -> bytes:
    import base64

    content = b"0" * 2_000_000
    return _build_zip(
        ((
            "huge.txt",
            content,
            zipfile.ZIP_DEFLATED,
            base64.b64decode(_FIXED_RATIO_DEFLATE_B64, validate=True),
        ),)
    )


def minimal_docx(text: str, *, macro: bool = False) -> bytes:
    content_types = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        b'</Types>'
    )
    relationships = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        b'</Relationships>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body>'
        '</w:document>'
    ).encode("utf-8")
    entries = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", relationships),
        ("word/document.xml", document),
    ]
    if macro:
        entries.append(("word/vbaProject.bin", b"synthetic-macro"))
    return _deterministic_zip(tuple(entries))


def security_file_corpus() -> tuple[SecurityFileCase, ...]:
    valid_docx = minimal_docx("字" * 1000)
    traversal_zip = _deterministic_zip((("../escape.txt", b"blocked"),))
    active_zip = _deterministic_zip((("payload.ps1", b"Write-Host blocked"),))
    ratio_zip = _deterministic_ratio_zip()
    cases = (
        SecurityFileCase(
            "valid.docx.1000-chars",
            "synthetic-report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            valid_docx,
            True,
            "accepted",
        ),
        SecurityFileCase(
            "invalid.docx.246-byte-placeholder",
            "placeholder.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"not-a-docx:" + b"x" * 235,
            False,
            "magic-or-structure",
        ),
        SecurityFileCase(
            "invalid.docx.1kb-placeholder",
            "one-kilobyte.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK" + b"0" * 1022,
            False,
            "magic-or-structure",
        ),
        SecurityFileCase(
            "invalid.docx.macro",
            "macro.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            minimal_docx("安全正文", macro=True),
            False,
            "macro",
        ),
        SecurityFileCase(
            "invalid.zip.path-traversal",
            "traversal.zip",
            "application/zip",
            traversal_zip,
            False,
            "path",
        ),
        SecurityFileCase(
            "invalid.zip.active-content",
            "active.zip",
            "application/zip",
            active_zip,
            False,
            "active-content",
        ),
        SecurityFileCase(
            "invalid.zip.compression-ratio",
            "ratio.zip",
            "application/zip",
            ratio_zip,
            False,
            "compression-ratio",
        ),
        SecurityFileCase(
            "valid.image.png",
            "synthetic.png",
            "image/png",
            b"\x89PNG\r\n\x1a\nsynthetic-pixelsIEND\xaeB`\x82",
            True,
            "accepted",
        ),
        SecurityFileCase(
            "invalid.image.pseudo-png",
            "pseudo.png",
            "image/png",
            b"this is not a png",
            False,
            "magic",
        ),
        SecurityFileCase(
            "valid.video.mp4",
            "synthetic.mp4",
            "video/mp4",
            b"\x00\x00\x00\x18ftypisomsynthetic-video",
            True,
            "accepted",
        ),
        SecurityFileCase(
            "valid.audio.silk",
            "synthetic.silk",
            "audio/silk",
            b"#!SILK_V3synthetic-audio",
            True,
            "accepted",
        ),
        SecurityFileCase(
            "valid.data.json",
            "synthetic.json",
            "application/json",
            json.dumps(
                {"kind": "synthetic", "production_secret": False},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            True,
            "accepted",
        ),
        SecurityFileCase(
            "invalid.data.bad-utf8",
            "bad.txt",
            "text/plain",
            b"\xff\xfe\x00",
            False,
            "utf8",
        ),
        SecurityFileCase(
            "invalid.declared.oversize",
            "oversize.txt",
            "text/plain",
            b"small-body",
            False,
            "declared-size",
            declared_size_bytes=536_870_913,
        ),
    )
    if len({case.case_id for case in cases}) != len(cases):
        raise AssertionError("security corpus case IDs are not unique")
    return cases


def security_corpus_sha256() -> str:
    return canonical_sha256(
        tuple(
            {
                "case_id": case.case_id,
                "filename": case.filename,
                "declared_mime": case.declared_mime,
                "content_sha256": case.sha256,
                "content_bytes": len(case.content),
                "expected_gate_accept": case.expected_gate_accept,
                "expected_reason_group": case.expected_reason_group,
                "declared_size_bytes": case.declared_size_bytes,
            }
            for case in security_file_corpus()
        )
    )


__all__ = [
    "SecurityFileCase",
    "minimal_docx",
    "security_corpus_sha256",
    "security_file_corpus",
]
