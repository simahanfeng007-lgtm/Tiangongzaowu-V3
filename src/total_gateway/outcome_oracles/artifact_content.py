"""P19-R2 M2 Gateway ArtifactContentOracle — RECORD ONLY.

Evaluates one explicit acceptance predicate against one bound
``ArtifactManifest`` by reading immutable object-store bytes through the
existing trusted chain (``VerifiedArtifactContentSource``: manifest
PASSED + QC facts + object reference binding + readback re-hash). The
oracle never opens host paths and never derives predicates from the raw
user message — that heuristic layer stays in the V3 local preflight
(``v3/simple_chain/content_preflight.py``) which has no final authority.

Status discipline (strict):
    PASS           deterministic evidence that the predicate holds
    FAIL           deterministic evidence that it does not
    INCONCLUSIVE   the inspector cannot reliably judge (unsupported
                   format, unparseable container, input beyond the size
                   limit, or a capability deliberately not implemented)
    ERROR          authority/readback/runtime failure
    NOT_APPLICABLE the predicate does not apply to this subject at all

"Cannot check" and "checked and failed" are different outcomes and are
never conflated: parse problems fall to INCONCLUSIVE, never to a
content-FAIL; authority problems fall to ERROR, never to PASS.

Honest capability surface: predicate types without a deterministic
inspector here return INCONCLUSIVE with
``predicate_not_implemented`` instead of a fake verdict.

This module only *produces* ``VerificationRecord``s; persisting them via
the M1 recorder/store changes no CompletionDecision, no request state
and no delivery behaviour (enforcement is always RECORD).
"""

from __future__ import annotations

import csv as _csv
import io
from dataclasses import dataclass, field
from typing import Any, Mapping

from contracts import ArtifactManifest, canonical_sha256
from contracts.verification import (
    VerificationRecord,
    derive_verification_record_id,
)
from total_gateway.artifact_content import (
    ArtifactContentError,
    VerifiedArtifactContentSource,
)
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.verification_predicate_types import PREDICATE_TYPE_ALLOWLIST


#: Predicate types with a deterministic inspector in this module. The M1
#: registry descriptor declares the wider planned surface; anything not
#: listed here yields an explicit INCONCLUSIVE (no fake verification).
_IMPLEMENTED_PREDICATE_TYPES: frozenset[str] = frozenset(
    {
        "artifact.nonempty",
        "artifact.min_visible_text_chars",
        "xlsx.required_columns",
        "xlsx.min_data_rows",
        "text.required_markers",
        "pptx.min_nonempty_slides",
    }
)

#: predicate prefix -> manifest format_id it applies to. ``artifact.*``
#: applies to every format that has an inspector at all.
_APPLICABLE_FORMATS: dict[str, frozenset[str]] = {
    "docx": frozenset({"docx"}),
    "xlsx": frozenset({"xlsx"}),
    "pptx": frozenset({"pptx"}),
    # text predicates cover txt/md files. NOTE: csv.* is deliberately
    # NOT implemented — the artifact gate has no csv format policy, so no
    # .csv manifest can ever reach this oracle with authority. It stays
    # INCONCLUSIVE (predicate_not_implemented) rather than fake-verifying.
    "text": frozenset({"text"}),
    "csv": frozenset({"text"}),
}

_INSPECTABLE_FORMATS: frozenset[str] = frozenset({"docx", "xlsx", "pptx", "text"})

DEFAULT_MAX_INPUT_BYTES = 64 * 1024 * 1024

_VERIFIER_ID = "verifier.artifact_content"
_VERIFIER_VERSION = "1"
# P19-R2 M2: LF line endings enforced by the cross-platform release gate.


class ArtifactPredicateSpecError(ValueError):
    """Raised when an explicit predicate spec is structurally invalid."""


class ContentUnparseable(Exception):
    """Deterministically detected "cannot reliably inspect" signal."""


@dataclass(frozen=True)
class ArtifactPredicate:
    """One explicit predicate with explicit parameters (no regex mining)."""

    predicate_id: str
    predicate_type: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.predicate_type not in PREDICATE_TYPE_ALLOWLIST:
            raise ArtifactPredicateSpecError(
                f"predicate type outside allowlist: {self.predicate_type}"
            )
        if not self.params or not isinstance(self.params, Mapping):
            raise ArtifactPredicateSpecError("predicate params must be a non-empty mapping")


def _positive_int(params: Mapping[str, Any], key: str, predicate_type: str) -> int:
    value = params.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ArtifactPredicateSpecError(
            f"{predicate_type}: param {key!r} must be a non-negative int"
        )
    return value


def _string_list(params: Mapping[str, Any], key: str, predicate_type: str) -> list[str]:
    value = params.get(key)
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ArtifactPredicateSpecError(
            f"{predicate_type}: param {key!r} must be a non-empty list of non-empty strings"
        )
    return [item for item in value]


# ---------------------------------------------------------------------------
# Inspectors (pure readers over verified bytes; no host paths)
# ---------------------------------------------------------------------------

def _inspect_docx(data: bytes) -> dict[str, Any]:
    try:
        from docx import Document
    except Exception as exc:  # pragma: no cover - dependency missing
        raise ContentUnparseable("docx inspector unavailable") from exc
    try:
        document = Document(io.BytesIO(data))
        paragraph_chars = sum(
            len(paragraph.text.strip()) for paragraph in document.paragraphs
        )
        table_chars = 0
        table_cells = 0
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text:
                        table_cells += 1
                        table_chars += len(text)
        return {
            "paragraph_chars": paragraph_chars,
            "table_cell_count": table_cells,
            "table_chars": table_chars,
            "visible_text_chars": paragraph_chars + table_chars,
        }
    except ContentUnparseable:
        raise
    except Exception as exc:
        raise ContentUnparseable(f"docx container unparseable: {exc}") from exc


def _inspect_xlsx(data: bytes) -> dict[str, Any]:
    try:
        import openpyxl
    except Exception as exc:  # pragma: no cover - dependency missing
        raise ContentUnparseable("xlsx inspector unavailable") from exc
    workbook = None
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheet = workbook.active
        if sheet is None:
            raise ContentUnparseable("xlsx workbook has no active sheet")
        header: list[str] = []
        data_row_count = 0
        nonempty_cell_count = 0
        for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
            cells = ["" if cell is None else str(cell).strip() for cell in row]
            nonempty = [cell for cell in cells if cell]
            nonempty_cell_count += len(nonempty)
            if row_index == 0:
                header = cells
                continue  # header is not a data row (M2-corrected semantics)
            if nonempty:
                data_row_count += 1
        sheet_names = list(workbook.sheetnames)
        return {
            "header": header,
            # data rows exclude the header row — unlike the legacy local
            # preflight which compares against max_row (kept unchanged).
            "data_row_count": data_row_count,
            "nonempty_cell_count": nonempty_cell_count,
            "sheet_names": sheet_names,
        }
    except ContentUnparseable:
        raise
    except Exception as exc:
        raise ContentUnparseable(f"xlsx container unparseable: {exc}") from exc
    finally:
        if workbook is not None:
            workbook.close()


def _inspect_pptx(data: bytes) -> dict[str, Any]:
    try:
        from pptx import Presentation
    except Exception as exc:  # pragma: no cover - dependency missing
        raise ContentUnparseable("pptx inspector unavailable") from exc
    try:
        presentation = Presentation(io.BytesIO(data))
        slide_count = 0
        text_bearing_slides = 0
        for slide in presentation.slides:
            slide_count += 1
            has_text = False
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    if shape.text_frame.text.strip():
                        has_text = True
                        break
            if has_text:
                text_bearing_slides += 1
        return {
            "slide_count": slide_count,
            "text_bearing_slide_count": text_bearing_slides,
        }
    except ContentUnparseable:
        raise
    except Exception as exc:
        raise ContentUnparseable(f"pptx container unparseable: {exc}") from exc


def _inspect_text(data: bytes, *, filename: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContentUnparseable(f"text not decodable as utf-8: {exc}") from exc
    lowered = filename.lower()
    metrics: dict[str, Any] = {
        "_text": text,
        "char_count": len(text),
        "non_whitespace_chars": sum(1 for ch in text if not ch.isspace()),
        "line_count": sum(1 for line in text.splitlines() if line.strip()),
    }
    if lowered.endswith(".csv"):
        rows = list(_csv.reader(io.StringIO(text)))
        header = [cell.strip() for cell in rows[0]] if rows else []
        metrics["header"] = header
        metrics["data_row_count"] = sum(
            1 for row in rows[1:] if any(cell.strip() for cell in row)
        )
    return metrics


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------

def _check_predicate(
    predicate: ArtifactPredicate,
    format_id: str,
    filename: str,
    metrics: dict[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """Return (holds, reason_codes). Only called with implemented types."""
    kind = predicate.predicate_type
    params = predicate.params
    if kind == "artifact.nonempty":
        if format_id == "xlsx":
            holds = metrics["nonempty_cell_count"] > 0
        elif format_id == "pptx":
            holds = metrics["slide_count"] > 0
        else:
            holds = metrics.get("non_whitespace_chars", 0) > 0 or (
                format_id == "docx" and metrics["visible_text_chars"] > 0
            )
        return holds, () if holds else ("artifact.content_empty",)
    if kind == "artifact.min_visible_text_chars":
        minimum = _positive_int(params, "min_chars", kind)
        if format_id == "docx":
            visible = metrics["visible_text_chars"]
        else:
            visible = metrics.get("non_whitespace_chars", 0)
        holds = visible >= minimum
        return holds, () if holds else ("artifact.visible_text_below_minimum",)
    if kind == "xlsx.required_columns":
        requested = _string_list(params, "columns", kind)
        header = [cell for cell in metrics["header"] if cell]
        missing = [
            column for column in requested
            if not any(column in cell for cell in header)
        ]
        return not missing, tuple(f"xlsx.column_missing:{c}" for c in missing)
    if kind == "xlsx.min_data_rows":
        minimum = _positive_int(params, "min_rows", kind)
        holds = metrics["data_row_count"] >= minimum
        return holds, () if holds else ("xlsx.data_rows_below_minimum",)
    if kind == "text.required_markers":
        markers = _string_list(params, "markers", kind)
        text_holds = metrics["non_whitespace_chars"] > 0
        missing = [
            marker
            for marker in markers
            if not _text_contains_marker(metrics, marker)
        ]
        holds = text_holds and not missing
        return holds, tuple(f"text.marker_missing:{m}" for m in missing)
    if kind == "pptx.min_nonempty_slides":
        minimum = _positive_int(params, "min_slides", kind)
        text_bearing = metrics["text_bearing_slide_count"]
        if metrics["slide_count"] > 0 and text_bearing == 0:
            # A deck whose slides carry no extractable text is either truly
            # empty or image-only; we cannot distinguish deterministically.
            raise ContentUnparseable("no text-bearing slide to measure")
        holds = text_bearing >= minimum
        return holds, () if holds else ("pptx.text_bearing_slides_below_minimum",)
    if kind == "csv.required_columns":
        requested = _string_list(params, "columns", kind)
        header = [cell for cell in metrics.get("header", []) if cell]
        missing = [
            column for column in requested
            if not any(column in cell for cell in header)
        ]
        return not missing, tuple(f"csv.column_missing:{c}" for c in missing)
    raise ArtifactPredicateSpecError(
        f"predicate type declared implemented but has no check: {kind}"
    )


def _text_contains_marker(metrics: dict[str, Any], marker: str) -> bool:
    # Markers are checked against the decoded text carried in the metrics
    # snapshot (bounded, no full-document persistence in records).
    return marker in metrics.get("_text", "")


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

class ArtifactContentOracle:
    """Deterministic content oracle over immutable, QC-passed artifacts."""

    def __init__(
        self,
        *,
        object_store: ContentAddressedObjectStore,
        fact_ledger: FactLedger,
        registry_snapshot_sha256: str,
        producer_component_id: str = "tiangong-gateway",
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    ) -> None:
        object.__setattr__(self, "_object_store", object_store)
        object.__setattr__(self, "_fact_ledger", fact_ledger)
        object.__setattr__(self, "_registry_snapshot_sha256", registry_snapshot_sha256)
        object.__setattr__(self, "_producer_component_id", producer_component_id)
        object.__setattr__(self, "_max_input_bytes", max_input_bytes)

    def evaluate(
        self,
        manifest: ArtifactManifest,
        predicate: ArtifactPredicate,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        evaluated_at_ms: int,
        evaluation_phase: str = "POST_EXECUTION",
        attempt: int = 1,
    ) -> VerificationRecord:
        status, reason_codes, metrics = self._evaluate_to_status(
            manifest, predicate
        )
        return self._build_record(
            manifest=manifest,
            predicate=predicate,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            evaluated_at_ms=evaluated_at_ms,
            evaluation_phase=evaluation_phase,
            attempt=attempt,
            status=status,
            reason_codes=reason_codes,
            metrics=metrics,
        )

    # -- internals ---------------------------------------------------------

    def _evaluate_to_status(
        self, manifest: ArtifactManifest, predicate: ArtifactPredicate
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        if predicate.predicate_type not in _IMPLEMENTED_PREDICATE_TYPES:
            return "INCONCLUSIVE", ("predicate_not_implemented",), {
                "predicate_type": predicate.predicate_type
            }
        format_id = manifest.format_id
        prefix = predicate.predicate_type.split(".", 1)[0]
        applicable = _APPLICABLE_FORMATS.get(prefix)
        if applicable is not None and format_id not in applicable:
            # M1 contract: NOT_APPLICABLE carries no reason codes (the
            # status itself plus subject binding is the entire statement).
            return "NOT_APPLICABLE", (), {
                "predicate_prefix": prefix,
                "format_id": format_id,
            }
        if (
            predicate.predicate_type.startswith("csv.")
            and not manifest.filename.lower().endswith(".csv")
        ):
            return "NOT_APPLICABLE", (), {"filename": manifest.filename}
        if format_id not in _INSPECTABLE_FORMATS:
            return "INCONCLUSIVE", ("format_not_inspectable",), {
                "format_id": format_id
            }

        # Authority chain: manifest PASSED + QC facts + object binding +
        # immutable readback with hash recomputation. Any failure here is
        # an ERROR — content is never judged on untrusted bytes. Broad
        # catch is deliberate: the object store itself raises
        # ObjectStoreCorruption on tampered blobs, and nothing from the
        # authority phase may escape as an exception or pass silently.
        try:
            source = VerifiedArtifactContentSource(
                self._object_store, self._fact_ledger, (manifest,)
            )
            data = source.read_verified_artifact(manifest.artifact_revision_id)
        except ArtifactContentError as exc:
            return "ERROR", (f"authority:{exc}",), {"authority_error": str(exc)}
        except Exception as exc:  # object-store corruption, IO, runtime
            return "ERROR", ("authority_failure",), {"authority_error": str(exc)[:200]}

        if len(data) > self._max_input_bytes:
            return "INCONCLUSIVE", ("input_too_large",), {
                "size_bytes": len(data),
                "max_input_bytes": self._max_input_bytes,
            }

        try:
            if format_id == "docx":
                metrics = _inspect_docx(data)
            elif format_id == "xlsx":
                metrics = _inspect_xlsx(data)
            elif format_id == "pptx":
                metrics = _inspect_pptx(data)
            else:
                metrics = _inspect_text(data, filename=manifest.filename)
        except ContentUnparseable as exc:
            return "INCONCLUSIVE", ("content_unparseable",), {
                "parse_error": str(exc)[:200]
            }
        except Exception as exc:  # unexpected runtime failure — never silent
            return "ERROR", ("inspector_failure",), {"error": str(exc)[:200]}

        try:
            holds, reason_codes = _check_predicate(
                predicate, format_id, manifest.filename, metrics
            )
        except ContentUnparseable as exc:
            return "INCONCLUSIVE", ("content_unparseable",), {
                "parse_error": str(exc)[:200]
            }
        except ArtifactPredicateSpecError:
            raise
        except Exception as exc:  # unexpected runtime failure — never silent
            return "ERROR", ("check_failure",), {"error": str(exc)[:200]}

        metrics = {key: value for key, value in metrics.items() if key != "_text"}
        return ("PASS" if holds else "FAIL"), reason_codes, metrics

    def _build_record(
        self,
        *,
        manifest: ArtifactManifest,
        predicate: ArtifactPredicate,
        request_id: str,
        run_id: str,
        generation: int,
        evaluated_at_ms: int,
        evaluation_phase: str,
        attempt: int,
        status: str,
        reason_codes: tuple[str, ...],
        metrics: dict[str, Any],
    ) -> VerificationRecord:
        evidence_refs = [
            manifest.artifact_revision_id,
            manifest.content_object_id,
            manifest.manifest_sha256,
        ]
        evidence_refs.extend(
            evidence.tool_fact_id for evidence in manifest.qc_evidence
        )
        evidence_refs_tuple = tuple(evidence_refs)
        payload = dict(
            verification_record_id="vrs_" + "0" * 64,
            request_id=request_id,
            run_id=run_id,
            generation=generation,
            verifier_id=_VERIFIER_ID,
            verifier_version=_VERIFIER_VERSION,
            registry_snapshot_sha256=self._registry_snapshot_sha256,
            predicate_id=predicate.predicate_id,
            predicate_type=predicate.predicate_type,
            subject_kind="artifact",
            subject_identity=manifest.artifact_revision_id,
            evaluation_phase=evaluation_phase,
            status=status,
            enforcement="RECORD",
            reason_codes=reason_codes,
            evidence_refs=evidence_refs_tuple,
            evidence_sha256=canonical_sha256(list(evidence_refs_tuple)),
            producer_component_id=self._producer_component_id,
            model_generated=False,
            evaluated_at_ms=evaluated_at_ms,
            result_sha256="0" * 64,
        )
        record = VerificationRecord(**payload).with_computed_sha256()
        return record.model_copy(
            update={
                "verification_record_id": derive_verification_record_id(
                    result_sha256=record.result_sha256
                )
            }
        )


__all__ = [
    "ArtifactContentOracle",
    "ArtifactPredicate",
    "ArtifactPredicateSpecError",
    "ContentUnparseable",
    "DEFAULT_MAX_INPUT_BYTES",
]
