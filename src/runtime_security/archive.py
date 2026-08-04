"""Shared ZIP/OOXML structural inspection with bomb and traversal defenses."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal


class ArchiveInspectionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 4_096
    max_expanded_bytes: int = 536_870_912
    max_entry_bytes: int = 268_435_456
    max_compression_ratio: int = 200
    max_xml_bytes: int = 16_777_216


@dataclass(frozen=True)
class ArchiveInspection:
    entry_names: tuple[str, ...]
    entry_count: int
    total_expanded_bytes: int
    profile: Literal["zip", "docx", "xlsx", "pptx"]
    required_parts_verified: bool


_OOXML = {
    "docx": (
        {"[Content_Types].xml", "_rels/.rels", "word/document.xml"},
        {"[Content_Types].xml": "Types", "_rels/.rels": "Relationships", "word/document.xml": "document"},
    ),
    "xlsx": (
        {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"},
        {"[Content_Types].xml": "Types", "_rels/.rels": "Relationships", "xl/workbook.xml": "workbook"},
    ),
    "pptx": (
        {"[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml"},
        {"[Content_Types].xml": "Types", "_rels/.rels": "Relationships", "ppt/presentation.xml": "presentation"},
    ),
}


def _open_archive(source: bytes | Path | BinaryIO) -> zipfile.ZipFile:
    if isinstance(source, bytes):
        if len(source) < 22 or not source.startswith(b"PK"):
            raise ArchiveInspectionError("archive.magic.invalid")
        return zipfile.ZipFile(io.BytesIO(source), "r")
    if isinstance(source, Path):
        if source.is_symlink() or not source.is_file():
            raise ArchiveInspectionError("archive.source.unsafe")
        with source.open("rb") as stream:
            prefix = stream.read(2)
        if source.stat().st_size < 22 or prefix != b"PK":
            raise ArchiveInspectionError("archive.magic.invalid")
        return zipfile.ZipFile(source, "r")
    return zipfile.ZipFile(source, "r")


def inspect_archive(
    source: bytes | Path | BinaryIO,
    *,
    profile: Literal["zip", "docx", "xlsx", "pptx"] = "zip",
    limits: ArchiveLimits = ArchiveLimits(),
) -> ArchiveInspection:
    try:
        with _open_archive(source) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > limits.max_entries:
                raise ArchiveInspectionError("archive.entry_count.invalid")
            seen: set[str] = set()
            exact_names: set[str] = set()
            total = 0
            for info in infos:
                name = info.filename
                normalized = name.replace("\\", "/")
                parts = PurePosixPath(normalized).parts
                if (
                    not name
                    or "\x00" in name
                    or name != normalized
                    or normalized.startswith("/")
                    or re.match(r"^[A-Za-z]:", normalized)
                    or any(part in {"", ".", ".."} for part in parts)
                ):
                    raise ArchiveInspectionError("archive.path.unsafe")
                folded = normalized.casefold()
                if folded in seen:
                    raise ArchiveInspectionError("archive.entry.duplicate")
                seen.add(folded)
                exact_names.add(normalized)
                if info.flag_bits & 0x1:
                    raise ArchiveInspectionError("archive.encrypted.forbidden")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ArchiveInspectionError("archive.symlink.forbidden")
                if info.file_size > limits.max_entry_bytes:
                    raise ArchiveInspectionError("archive.entry.expanded_too_large")
                total += info.file_size
                if total > limits.max_expanded_bytes:
                    raise ArchiveInspectionError("archive.expanded_too_large")
                if info.file_size and info.compress_size == 0:
                    raise ArchiveInspectionError("archive.compression_ratio.invalid")
                if (
                    info.compress_size > 0
                    and info.file_size > info.compress_size * limits.max_compression_ratio
                ):
                    raise ArchiveInspectionError("archive.compression_ratio.invalid")
            if archive.testzip() is not None:
                raise ArchiveInspectionError("archive.crc.invalid")

            required_verified = profile == "zip"
            if profile != "zip":
                required, roots = _OOXML[profile]
                if not required.issubset(exact_names):
                    raise ArchiveInspectionError("ooxml.required_part.missing")
                if any(
                    name.casefold().endswith(("vbaproject.bin", "vbaProjectSignature.bin".casefold()))
                    for name in exact_names
                ):
                    raise ArchiveInspectionError("ooxml.macro.forbidden")
                for name, expected_root in roots.items():
                    info = archive.getinfo(name)
                    if info.file_size > limits.max_xml_bytes:
                        raise ArchiveInspectionError("ooxml.xml.too_large")
                    xml = archive.read(name)
                    upper = xml.upper()
                    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
                        raise ArchiveInspectionError("ooxml.xml.unsafe")
                    try:
                        root = ElementTree.fromstring(xml)
                    except ElementTree.ParseError as exc:
                        raise ArchiveInspectionError("ooxml.xml.invalid") from exc
                    if not (root.tag == expected_root or root.tag.endswith("}" + expected_root)):
                        raise ArchiveInspectionError("ooxml.xml.root_invalid")
                required_verified = True
            return ArchiveInspection(
                entry_names=tuple(sorted(exact_names)),
                entry_count=len(infos),
                total_expanded_bytes=total,
                profile=profile,
                required_parts_verified=required_verified,
            )
    except ArchiveInspectionError:
        raise
    except (OSError, KeyError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ArchiveInspectionError("archive.structure.invalid") from exc


__all__ = [
    "ArchiveInspection",
    "ArchiveInspectionError",
    "ArchiveLimits",
    "inspect_archive",
]
