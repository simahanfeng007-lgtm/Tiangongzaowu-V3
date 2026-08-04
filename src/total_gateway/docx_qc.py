"""Deterministic DOCX delivery QC with durable machine-fact recording."""

from __future__ import annotations

import io
import hashlib
import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass

from contracts import QcEvidence

from .artifact_gate import ArtifactGateResult
from .fact_ledger import (
    ArtifactQcRegistration,
    ArtifactQcResult,
    FactLedger,
    QcMetric,
    derive_qc_effect_id,
    derive_qc_fact_id,
    derive_qc_result_id,
)
from .object_store import ContentAddressedObjectStore


DOCX_QC_CHECK_ID = "qc.docx.delivery_check"
DOCX_QC_CHECK_VERSION = "1.0.0"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MAIN_DOCUMENT_REL = _OFFICE_REL_NS + "/officeDocument"
_MAIN_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


class DocxQcError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DocxQcPolicy:
    minimum_word_count: int = 1
    maximum_word_count: int = 10_000_000
    allow_external_hyperlinks: bool = True


@dataclass(frozen=True)
class DocxInspection:
    package_part_count: int
    xml_part_count: int
    relationship_count: int
    external_relationship_count: int
    paragraph_count: int
    character_count: int
    word_count: int


@dataclass(frozen=True)
class DocxQcOutcome:
    registration: ArtifactQcRegistration

    @property
    def passed(self) -> bool:
        return self.registration.record.result.status == "PASSED"


def _safe_xml(data: bytes, *, reason_prefix: str) -> ElementTree.Element:
    if len(data) > 32 * 1024 * 1024:
        raise DocxQcError(reason_prefix + ".too_large")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise DocxQcError(reason_prefix + ".unsafe_declaration")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise DocxQcError(reason_prefix + ".invalid_xml") from exc


def _relationship_source(path: str) -> tuple[str, str]:
    if path == "_rels/.rels":
        return "", ""
    marker = "/_rels/"
    if marker not in path or not path.endswith(".rels"):
        raise DocxQcError("qc.docx.relationship_path_invalid")
    directory, filename = path.split(marker, 1)
    source = posixpath.join(directory, filename[:-5])
    return source, posixpath.dirname(source)


def _internal_target(base: str, target: str) -> str:
    if not target or "\\" in target or "\x00" in target:
        raise DocxQcError("qc.docx.relationship_target_unsafe")
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise DocxQcError("qc.docx.relationship_target_unsafe")
    decoded = urllib.parse.unquote(parsed.path)
    if decoded.startswith("/") or re.match(r"^[A-Za-z]:", decoded):
        raise DocxQcError("qc.docx.relationship_target_unsafe")
    normalized = posixpath.normpath(posixpath.join(base, decoded))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise DocxQcError("qc.docx.relationship_target_unsafe")
    return normalized


def _external_target(target: str, *, allowed: bool) -> None:
    if not allowed:
        raise DocxQcError("qc.docx.external_relationship_forbidden")
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme not in {"http", "https", "mailto"}:
        raise DocxQcError("qc.docx.external_relationship_scheme_forbidden")
    if parsed.username is not None or parsed.password is not None or len(target) > 4096:
        raise DocxQcError("qc.docx.external_relationship_unsafe")


def _visible_paragraph_text(paragraph: ElementTree.Element) -> str:
    pieces: list[str] = []

    def visit(node: ElementTree.Element, deleted: bool = False) -> None:
        local = node.tag.rsplit("}", 1)[-1]
        deleted = deleted or local == "del"
        if local == "t" and not deleted and node.text:
            pieces.append(node.text)
        elif local in {"tab", "br", "cr"} and not deleted:
            pieces.append(" ")
        for child in node:
            visit(child, deleted)

    visit(paragraph)
    return "".join(pieces)


def _real_word_count(text: str) -> int:
    count = 0
    in_alphanumeric = False
    for char in text:
        code = ord(char)
        is_cjk = (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0x20000 <= code <= 0x2FA1F
        )
        if is_cjk:
            count += 1
            in_alphanumeric = False
        elif char.isalnum():
            if not in_alphanumeric:
                count += 1
            in_alphanumeric = True
        else:
            in_alphanumeric = False
    return count


def inspect_docx(data: bytes, *, allow_external_hyperlinks: bool) -> DocxInspection:
    try:
        archive_context = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise DocxQcError("qc.docx.zip_invalid") from exc
    with archive_context as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos if not info.is_dir()}
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        if not required.issubset(names):
            raise DocxQcError("qc.docx.required_part_missing")
        xml_names = sorted(
            name for name in names if name.endswith(".xml") or name.endswith(".rels")
        )
        xml_roots = {
            name: _safe_xml(archive.read(name), reason_prefix="qc.docx.xml")
            for name in xml_names
        }

        content_types = xml_roots["[Content_Types].xml"]
        if content_types.tag != f"{{{_CONTENT_TYPES_NS}}}Types":
            raise DocxQcError("qc.docx.content_types_root_invalid")
        overrides: dict[str, str] = {}
        for item in content_types.findall(f"{{{_CONTENT_TYPES_NS}}}Override"):
            part_name = item.attrib.get("PartName", "")
            content_type = item.attrib.get("ContentType", "")
            if not part_name or part_name in overrides:
                raise DocxQcError("qc.docx.content_types_duplicate_override")
            overrides[part_name] = content_type
        if overrides.get("/word/document.xml") != _MAIN_DOCUMENT_CONTENT_TYPE:
            raise DocxQcError("qc.docx.main_content_type_invalid")

        relationship_count = 0
        external_count = 0
        relationship_maps: dict[str, dict[str, tuple[str, str, bool]]] = {}
        for name in sorted(item for item in names if item.endswith(".rels")):
            root = xml_roots[name]
            if root.tag != f"{{{_PACKAGE_REL_NS}}}Relationships":
                raise DocxQcError("qc.docx.relationship_root_invalid")
            source, base = _relationship_source(name)
            mapping: dict[str, tuple[str, str, bool]] = {}
            for item in root.findall(f"{{{_PACKAGE_REL_NS}}}Relationship"):
                relationship_id = item.attrib.get("Id", "")
                relationship_type = item.attrib.get("Type", "")
                target = item.attrib.get("Target", "")
                external = item.attrib.get("TargetMode") == "External"
                if not relationship_id or relationship_id in mapping or not relationship_type:
                    raise DocxQcError("qc.docx.relationship_identity_invalid")
                if external:
                    _external_target(target, allowed=allow_external_hyperlinks)
                    resolved = target
                    external_count += 1
                else:
                    resolved = _internal_target(base, target)
                    if resolved not in names:
                        raise DocxQcError("qc.docx.relationship_target_missing")
                mapping[relationship_id] = (relationship_type, resolved, external)
                relationship_count += 1
            relationship_maps[source] = mapping

        root_relationships = relationship_maps.get("", {})
        office_targets = tuple(
            target
            for relationship_type, target, external in root_relationships.values()
            if relationship_type == _MAIN_DOCUMENT_REL and not external
        )
        if office_targets != ("word/document.xml",):
            raise DocxQcError("qc.docx.office_document_relationship_invalid")

        document = xml_roots["word/document.xml"]
        if document.tag != f"{{{_WORD_NS}}}document":
            raise DocxQcError("qc.docx.document_root_invalid")
        body = document.find(f"{{{_WORD_NS}}}body")
        if body is None:
            raise DocxQcError("qc.docx.body_missing")
        document_relationships = relationship_maps.get("word/document.xml", {})
        for node in document.iter():
            for attribute, value in node.attrib.items():
                if attribute.startswith(f"{{{_OFFICE_REL_NS}}}") and attribute.rsplit("}", 1)[-1] in {
                    "id",
                    "embed",
                    "link",
                }:
                    if value not in document_relationships:
                        raise DocxQcError("qc.docx.document_relationship_missing")

        paragraphs = [
            _visible_paragraph_text(node)
            for node in body.iter(f"{{{_WORD_NS}}}p")
        ]
        visible = "\n".join(text for text in paragraphs if text.strip())
        paragraph_count = sum(bool(text.strip()) for text in paragraphs)
        character_count = sum(not char.isspace() for char in visible)
        word_count = _real_word_count(visible)
        if paragraph_count == 0 or character_count == 0 or word_count == 0:
            raise DocxQcError("qc.docx.visible_content_empty")
        return DocxInspection(
            package_part_count=len(names),
            xml_part_count=len(xml_names),
            relationship_count=relationship_count,
            external_relationship_count=external_count,
            paragraph_count=paragraph_count,
            character_count=character_count,
            word_count=word_count,
        )


class DocxQcService:
    def __init__(
        self,
        object_store: ContentAddressedObjectStore,
        fact_ledger: FactLedger,
    ) -> None:
        self._object_store = object_store
        self._fact_ledger = fact_ledger

    def evaluate(
        self,
        gate_result: ArtifactGateResult,
        *,
        run_sequence: int,
        policy: DocxQcPolicy,
        checked_at_ms: int,
    ) -> DocxQcOutcome:
        manifest = gate_result.manifest
        if (
            manifest.qc_state != "PENDING"
            or manifest.qc_evidence
            or manifest.format_id != "docx"
            or not manifest.has_valid_manifest_sha256()
            or not gate_result.evidence.has_valid_sha256()
            or gate_result.evidence.object_id != manifest.content_object_id
            or gate_result.evidence.content_sha256 != manifest.sha256
        ):
            raise DocxQcError("qc.docx.gate_evidence_invalid")
        if (
            run_sequence < 1
            or checked_at_ms < manifest.created_at_ms
            or policy.minimum_word_count < 1
            or policy.maximum_word_count < policy.minimum_word_count
            or policy.maximum_word_count > 10_000_000
        ):
            raise DocxQcError("qc.docx.policy_invalid")
        existing = self._fact_ledger.get_artifact_qc(
            manifest.artifact_revision_id,
            check_id=DOCX_QC_CHECK_ID,
            check_version=DOCX_QC_CHECK_VERSION,
        )
        if existing is not None:
            existing_metrics = {item.name: item.value for item in existing.result.metrics}
            if (
                existing_metrics.get("minimum_word_count") != policy.minimum_word_count
                or existing_metrics.get("maximum_word_count") != policy.maximum_word_count
                or existing_metrics.get("allow_external_hyperlinks")
                != policy.allow_external_hyperlinks
                or existing.manifest.model_dump(
                    mode="json",
                    exclude={"qc_state", "qc_evidence", "manifest_sha256"},
                )
                != manifest.model_dump(
                    mode="json",
                    exclude={"qc_state", "qc_evidence", "manifest_sha256"},
                )
            ):
                raise DocxQcError("qc.docx.policy_or_manifest_conflict")
            return DocxQcOutcome(
                registration=ArtifactQcRegistration(existing, False)
            )
        data = self._object_store.read_bytes(manifest.content_object_id)
        if len(data) != manifest.size_bytes:
            raise DocxQcError("qc.docx.size_changed")
        if hashlib.sha256(data).hexdigest() != manifest.sha256:
            raise DocxQcError("qc.docx.digest_changed")

        reason_codes: tuple[str, ...] = ()
        inspection: DocxInspection | None = None
        try:
            inspection = inspect_docx(
                data,
                allow_external_hyperlinks=policy.allow_external_hyperlinks,
            )
            failures: list[str] = []
            if inspection.word_count < policy.minimum_word_count:
                failures.append("qc.docx.word_count_below_minimum")
            if inspection.word_count > policy.maximum_word_count:
                failures.append("qc.docx.word_count_above_maximum")
            reason_codes = tuple(sorted(failures))
        except DocxQcError as exc:
            reason_codes = (exc.code,)

        status = "FAILED" if reason_codes else "PASSED"
        metrics: dict[str, str | int | bool] = {
            "allow_external_hyperlinks": policy.allow_external_hyperlinks,
            "base_evidence_sha256": gate_result.evidence.evidence_sha256,
            "content_readback_verified": True,
            "minimum_word_count": policy.minimum_word_count,
            "maximum_word_count": policy.maximum_word_count,
            "package_size_bytes": len(data),
        }
        if inspection is not None:
            metrics.update(
                {
                    "character_count": inspection.character_count,
                    "external_relationship_count": inspection.external_relationship_count,
                    "package_part_count": inspection.package_part_count,
                    "paragraph_count": inspection.paragraph_count,
                    "relationship_count": inspection.relationship_count,
                    "word_count": inspection.word_count,
                    "xml_part_count": inspection.xml_part_count,
                }
            )
        qc_result_id = derive_qc_result_id(
            artifact_revision_id=manifest.artifact_revision_id,
            check_id=DOCX_QC_CHECK_ID,
            check_version=DOCX_QC_CHECK_VERSION,
            content_sha256=manifest.sha256,
        )
        effect_id = derive_qc_effect_id(
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            run_sequence=run_sequence,
            generation=manifest.generation,
            artifact_revision_id=manifest.artifact_revision_id,
            check_id=DOCX_QC_CHECK_ID,
            check_version=DOCX_QC_CHECK_VERSION,
            content_sha256=manifest.sha256,
        )
        result = ArtifactQcResult(
            qc_result_id=qc_result_id,
            check_id=DOCX_QC_CHECK_ID,
            check_version=DOCX_QC_CHECK_VERSION,
            status=status,
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            run_sequence=run_sequence,
            generation=manifest.generation,
            effect_id=effect_id,
            artifact_revision_id=manifest.artifact_revision_id,
            object_id=manifest.content_object_id,
            content_sha256=manifest.sha256,
            checked_at_ms=checked_at_ms,
            metrics=tuple(QcMetric(name=name, value=value) for name, value in sorted(metrics.items())),
            reason_codes=reason_codes,
            qc_result_sha256="0" * 64,
        ).with_computed_sha256()
        evidence = QcEvidence(
            check_id=result.check_id,
            check_version=result.check_version,
            status=result.status,
            checked_at_ms=result.checked_at_ms,
            evidence_sha256=result.qc_result_sha256,
            tool_fact_id=derive_qc_fact_id(result),
        )
        final_manifest = manifest.model_copy(
            update={"qc_state": result.status, "qc_evidence": (evidence,)}
        ).with_computed_manifest_sha256()
        registration = self._fact_ledger.record_artifact_qc(result, final_manifest)
        return DocxQcOutcome(registration=registration)


__all__ = [
    "DOCX_QC_CHECK_ID",
    "DOCX_QC_CHECK_VERSION",
    "DocxInspection",
    "DocxQcError",
    "DocxQcOutcome",
    "DocxQcPolicy",
    "DocxQcService",
    "inspect_docx",
]
