"""P19-R2 M2.1 Gateway ArtifactContentOracle — RECORD ONLY.

Evaluates one explicit ``AcceptancePredicate`` against one bound
``ArtifactManifest`` by reading immutable object-store bytes through the
existing trusted chain (``VerifiedArtifactContentSource``). The oracle
never opens host paths and never derives predicates from the raw user
message — that heuristic layer stays in the V3 local preflight
(``v3/simple_chain/content_preflight.py``) with no final authority.

M2.1 review hardening:
* constructed from a full validated ``RegistrySnapshot`` — the verifier
  version, producer id, input limit and supported predicate set are read
  from the pinned v2 descriptor (single source:
  ``verification_oracle_config``), never hardcoded here;
* record lineage is taken from the manifest itself
  (request_id/run_id/generation) — there is nothing to rebind;
* applicability is decided BEFORE implementation, so an unimplemented
  xlsx predicate on a docx subject is NOT_APPLICABLE, not INCONCLUSIVE;
* deterministic metrics exist per format (xlsx counts formula text via a
  data_only=False pass; pptx counts meaningful slides: text, pictures,
  tables, charts — not mere slide presence);
* evidence refs are a small typed set including qc-evidence-set,
  predicate and observation digests (never 64 raw QC fact ids);
* resource discipline: manifest.size_bytes is checked against the
  descriptor limit BEFORE any blob read; ImportError/dependency failures
  are ERROR, known-unparseable containers are INCONCLUSIVE;
* reason codes are stable machine codes — no raw exception text.

Status discipline (strict):
    PASS / FAIL        deterministic evidence either way
    INCONCLUSIVE       cannot reliably judge (unsupported format,
                       unparseable container, size beyond limit, missing
                       per-format metric, capability not implemented)
    ERROR              authority/readback/dependency/runtime failure
    NOT_APPLICABLE     predicate does not apply to this subject at all

RECORD ONLY: this module produces ``VerificationRecord``s; persisting
them changes no CompletionDecision, no request state, no delivery.
"""

from __future__ import annotations

import csv as _csv
import io
from typing import Any

from contracts import ArtifactManifest, canonical_sha256
from contracts.verification import (
    AcceptancePredicate,
    RegistrySnapshot,
    VerificationRecord,
    derive_verification_record_id,
    normalize_predicate_text,
)
from total_gateway.artifact_content import (
    ArtifactContentError,
    VerifiedArtifactContentSource,
)
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.verification_oracle_config import (
    ARTIFACT_APPLICABLE_FORMATS,
    ARTIFACT_INSPECTABLE_FORMATS,
    ARTIFACT_INSPECTOR_SEMANTIC_VERSION,
    VERIFIER_ID,
)
from total_gateway.verification_registry import (
    UnknownVerifierError,
    VerifierRegistry,
)


class OracleSnapshotInvalid(ValueError):
    """Raised when the oracle cannot be bound to the given snapshot."""


class ContentUnparseable(Exception):
    """Deterministically detected "cannot reliably inspect" signal."""


class _InspectorDependencyMissing(Exception):
    """ImportError from an inspector dependency (ERROR, not INCONCLUSIVE)."""


# ---------------------------------------------------------------------------
# Inspectors (pure readers over verified bytes; no host paths)
# ---------------------------------------------------------------------------

def _inspect_docx(data: bytes) -> dict[str, Any]:
    try:
        from docx import Document
    except ImportError as exc:
        raise _InspectorDependencyMissing("docx") from exc
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
            "measured_format": "docx",
            "visible_text_chars": paragraph_chars + table_chars,
        }
    except _InspectorDependencyMissing:
        raise
    except Exception as exc:
        raise ContentUnparseable(f"docx container unparseable: {type(exc).__name__}") from exc


def _inspect_xlsx(data: bytes) -> dict[str, Any]:
    try:
        import openpyxl
    except ImportError as exc:
        raise _InspectorDependencyMissing("openpyxl") from exc
    values_book = None
    formulas_book = None
    try:
        # Pass 1 (data_only=True): cached values for header/data-row shape.
        values_book = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True
        )
        sheet = values_book.active
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
                continue  # header is not a data row (oracle semantics)
            if nonempty:
                data_row_count += 1
        sheet_names = list(values_book.sheetnames)
        # Pass 2 (data_only=False): formula text is content too — a cell
        # holding "=SUM(A1:A9)" is not empty just because its cached
        # value is missing.
        formulas_book = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=False
        )
        formula_sheet = formulas_book.active
        visible_text_chars = 0
        if formula_sheet is not None:
            for row in formula_sheet.iter_rows(values_only=True):
                for cell in row:
                    if cell is None:
                        continue
                    visible_text_chars += len(str(cell).strip())
        return {
            "measured_format": "xlsx",
            "header": header,
            "data_row_count": data_row_count,
            "nonempty_cell_count": nonempty_cell_count,
            "sheet_names": sheet_names,
            "visible_text_chars": visible_text_chars,
        }
    except _InspectorDependencyMissing:
        raise
    except ContentUnparseable:
        raise
    except Exception as exc:
        raise ContentUnparseable(f"xlsx container unparseable: {type(exc).__name__}") from exc
    finally:
        if values_book is not None:
            values_book.close()
        if formulas_book is not None:
            formulas_book.close()


def _inspect_pptx(data: bytes) -> dict[str, Any]:
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise _InspectorDependencyMissing("pptx") from exc
    try:
        presentation = Presentation(io.BytesIO(data))
        slide_count = 0
        meaningful_slide_count = 0
        text_bearing_slide_count = 0
        slide_text_chars = 0
        for slide in presentation.slides:
            slide_count += 1
            has_text = False
            has_other_content = False
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    text = shape.text_frame.text.strip()
                    if text:
                        has_text = True
                        slide_text_chars += len(text)
                if getattr(shape, "has_table", False):
                    has_other_content = True
                if getattr(shape, "has_chart", False):
                    has_other_content = True
                if shape.shape_type is not None and str(shape.shape_type).startswith(
                    "PICTURE"
                ):
                    has_other_content = True
            if has_text:
                text_bearing_slide_count += 1
            if has_text or has_other_content:
                meaningful_slide_count += 1
        return {
            "measured_format": "pptx",
            "slide_count": slide_count,
            "text_bearing_slide_count": text_bearing_slide_count,
            "meaningful_slide_count": meaningful_slide_count,
            "slide_text_chars": slide_text_chars,
        }
    except _InspectorDependencyMissing:
        raise
    except Exception as exc:
        raise ContentUnparseable(f"pptx container unparseable: {type(exc).__name__}") from exc


def _inspect_text(data: bytes, *, filename: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContentUnparseable("text not decodable as utf-8") from exc
    metrics: dict[str, Any] = {
        "_text": text,
        "measured_format": "text",
        "char_count": len(text),
        "non_whitespace_chars": sum(1 for ch in text if not ch.isspace()),
        "line_count": sum(1 for line in text.splitlines() if line.strip()),
    }
    if filename.lower().endswith(".csv"):
        rows = list(_csv.reader(io.StringIO(text)))
        metrics["header"] = [cell.strip() for cell in rows[0]] if rows else []
        metrics["data_row_count"] = sum(
            1 for row in rows[1:] if any(cell.strip() for cell in row)
        )
    return metrics


_INSPECTORS = {
    "docx": _inspect_docx,
    "xlsx": _inspect_xlsx,
    "pptx": _inspect_pptx,
}


def _inspect(format_id: str, data: bytes, *, filename: str) -> dict[str, Any]:
    if format_id == "text":
        return _inspect_text(data, filename=filename)
    inspector = _INSPECTORS.get(format_id)
    if inspector is None:  # defensive: guarded by _INSPECTABLE_FORMATS
        raise ContentUnparseable(f"no inspector for format: {format_id}")
    return inspector(data)


# ---------------------------------------------------------------------------
# Deterministic checks (stable machine reason codes only)
# ---------------------------------------------------------------------------

#: Per-format metric key used by artifact.min_visible_text_chars. A format
#: without a reliable metric key in the observation raises
#: ContentUnparseable (INCONCLUSIVE) — never a fake 0.
_VISIBLE_TEXT_METRIC_KEYS: dict[str, str] = {
    "docx": "visible_text_chars",
    "xlsx": "visible_text_chars",
    "pptx": "slide_text_chars",
    "text": "non_whitespace_chars",
}


def _visible_text_metric(format_id: str, metrics: dict[str, Any]) -> int:
    key = _VISIBLE_TEXT_METRIC_KEYS.get(format_id)
    if key is None or key not in metrics:
        raise ContentUnparseable(
            f"no reliable visible-text metric for format: {format_id}"
        )
    value = metrics[key]
    if not isinstance(value, int) or value < 0:
        raise ContentUnparseable(f"unusable visible-text metric for format: {format_id}")
    return value


def _check_predicate(
    predicate: AcceptancePredicate,
    format_id: str,
    metrics: dict[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """Return (holds, reason_codes). Only called with implemented types."""
    kind = predicate.predicate_type
    params = predicate.param_mapping()
    if kind == "artifact.nonempty":
        if format_id == "xlsx":
            holds = metrics["nonempty_cell_count"] > 0
        elif format_id == "pptx":
            holds = metrics["meaningful_slide_count"] > 0
        else:
            holds = _visible_text_metric(format_id, metrics) > 0
        return holds, () if holds else ("artifact.content_empty",)
    if kind == "artifact.min_visible_text_chars":
        minimum = params["min_chars"]
        assert isinstance(minimum, int)
        visible = _visible_text_metric(format_id, metrics)
        holds = visible >= minimum
        return holds, () if holds else ("artifact.visible_text_below_minimum",)
    if kind == "xlsx.required_columns":
        requested = params["columns"]
        assert isinstance(requested, tuple)
        header = {
            normalize_predicate_text(cell)
            for cell in metrics["header"]
            if isinstance(cell, str) and cell.strip()
        }
        missing = [column for column in requested if column not in header]
        return not missing, tuple(f"xlsx.column_missing:{c}" for c in missing)
    if kind == "xlsx.min_data_rows":
        minimum = params["min_rows"]
        assert isinstance(minimum, int)
        holds = metrics["data_row_count"] >= minimum
        return holds, () if holds else ("xlsx.data_rows_below_minimum",)
    if kind == "text.required_markers":
        markers = params["markers"]
        assert isinstance(markers, tuple)
        missing = tuple(
            f"text.marker_missing:{m}" for m in markers if m not in metrics["_text"]
        )
        return not missing, missing
    if kind == "pptx.min_nonempty_slides":
        minimum = params["min_slides"]
        assert isinstance(minimum, int)
        holds = metrics["meaningful_slide_count"] >= minimum
        return holds, () if holds else ("pptx.meaningful_slides_below_minimum",)
    raise AssertionError(
        f"predicate type declared implemented but has no check: {kind}"
    )


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

class ArtifactContentOracle:
    """Deterministic content oracle over immutable, QC-passed artifacts.

    Bound to one validated ``RegistrySnapshot``; the v2 descriptor inside
    that snapshot supplies the verifier version, producer component,
    input limit and supported predicate set (no hardcoded drift).
    """

    def __init__(
        self,
        *,
        snapshot: RegistrySnapshot,
        object_store: ContentAddressedObjectStore,
        fact_ledger: FactLedger,
    ) -> None:
        if not snapshot.has_valid_identity():
            raise OracleSnapshotInvalid("registry snapshot identity binding is invalid")
        try:
            VerifierRegistry(snapshot.verifiers)
        except ValueError as exc:
            raise OracleSnapshotInvalid(
                "registry snapshot contains invalid verifier descriptors"
            ) from exc
        try:
            descriptor = VerifierRegistry(snapshot.verifiers).find(
                VERIFIER_ID, ARTIFACT_INSPECTOR_SEMANTIC_VERSION
            )
        except UnknownVerifierError as exc:
            raise OracleSnapshotInvalid(
                f"snapshot does not carry {VERIFIER_ID}@"
                f"{ARTIFACT_INSPECTOR_SEMANTIC_VERSION}"
            ) from exc
        object.__setattr__(self, "_snapshot", snapshot)
        object.__setattr__(self, "_descriptor", descriptor)
        object.__setattr__(self, "_object_store", object_store)
        object.__setattr__(self, "_fact_ledger", fact_ledger)

    @property
    def descriptor(self):
        return self._descriptor  # type: ignore[attr-defined]

    def evaluate(
        self,
        manifest: ArtifactManifest,
        predicate: AcceptancePredicate,
        *,
        evaluated_at_ms: int,
        evaluation_phase: str = "POST_EXECUTION",
    ) -> VerificationRecord:
        if predicate.subject_kind != "artifact":
            raise ValueError(
                "artifact oracle only evaluates artifact-subject predicates:"
                f" {predicate.subject_kind}"
            )
        if not predicate.has_valid_identity():
            raise ValueError("predicate identity binding is invalid")
        status, reason_codes, observation = self._evaluate_to_status(
            manifest, predicate
        )
        return self._build_record(
            manifest=manifest,
            predicate=predicate,
            evaluated_at_ms=evaluated_at_ms,
            evaluation_phase=evaluation_phase,
            status=status,
            reason_codes=reason_codes,
            observation=observation,
        )

    # -- internals ---------------------------------------------------------

    def _evaluate_to_status(
        self, manifest: ArtifactManifest, predicate: AcceptancePredicate
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        format_id = manifest.format_id

        # 1. Applicability FIRST: a predicate that can never apply to this
        # subject/format is NOT_APPLICABLE regardless of implementation.
        prefix = predicate.predicate_type.split(".", 1)[0]
        applicable = ARTIFACT_APPLICABLE_FORMATS.get(prefix)
        if applicable is not None and format_id not in applicable:
            return "NOT_APPLICABLE", (), {
                "predicate_prefix": prefix,
                "format_id": format_id,
            }

        # 2. Implementation: declared capability comes from the pinned
        # descriptor (single source), not from a parallel list here.
        if predicate.predicate_type not in self._descriptor.supported_predicate_types:  # type: ignore[attr-defined]
            return "INCONCLUSIVE", ("predicate_not_implemented",), {
                "predicate_type": predicate.predicate_type
            }
        if format_id not in ARTIFACT_INSPECTABLE_FORMATS:
            return "INCONCLUSIVE", ("format_not_inspectable",), {
                "format_id": format_id
            }

        # 3. Resource pre-check against the descriptor limit BEFORE any
        # blob read: oversize manifests never touch object-store bytes.
        max_input_bytes = self._descriptor.max_input_bytes  # type: ignore[attr-defined]
        if manifest.size_bytes > max_input_bytes:
            return "INCONCLUSIVE", ("input_too_large",), {
                "size_bytes": manifest.size_bytes,
                "max_input_bytes": max_input_bytes,
            }

        # 4. Authority chain: manifest PASSED + QC facts + object binding +
        # immutable readback with hash recomputation. Any failure here is
        # ERROR; nothing may escape or pass silently. ArtifactContentError
        # messages ARE stable machine codes (artifact.content.*).
        try:
            source = VerifiedArtifactContentSource(
                self._object_store, self._fact_ledger, (manifest,)  # type: ignore[attr-defined]
            )
            data = source.read_verified_artifact(manifest.artifact_revision_id)
        except ArtifactContentError as exc:
            return "ERROR", (f"authority:{exc}",), {"authority_error": str(exc)}
        except Exception as exc:  # object-store corruption, IO, runtime
            return "ERROR", ("authority_failure",), {
                "authority_error": type(exc).__name__
            }

        # 5. Inspection: dependency failures are ERROR; known-unparseable
        # containers are INCONCLUSIVE.
        try:
            metrics = _inspect(format_id, data, filename=manifest.filename)
        except _InspectorDependencyMissing:
            return "ERROR", ("inspector_dependency_missing",), {}
        except ContentUnparseable:
            return "INCONCLUSIVE", ("content_unparseable",), {}
        except Exception:  # unexpected runtime bug — never silent
            return "ERROR", ("inspector_failure",), {}

        # 6. Deterministic check (stable machine codes only).
        try:
            holds, reason_codes = _check_predicate(predicate, format_id, metrics)
        except ContentUnparseable:
            return "INCONCLUSIVE", ("content_unparseable",), {}
        except Exception:  # unexpected runtime bug — never silent
            return "ERROR", ("check_failure",), {}

        observation = {
            key: value for key, value in metrics.items() if key != "_text"
        }
        observation["inspector_version"] = self._descriptor.verifier_version  # type: ignore[attr-defined]
        return ("PASS" if holds else "FAIL"), reason_codes, observation

    def _build_record(
        self,
        *,
        manifest: ArtifactManifest,
        predicate: AcceptancePredicate,
        evaluated_at_ms: int,
        evaluation_phase: str,
        status: str,
        reason_codes: tuple[str, ...],
        observation: dict[str, Any],
    ) -> VerificationRecord:
        descriptor = self._descriptor  # type: ignore[attr-defined]
        qc_evidence_set = sorted(
            (
                evidence.check_id,
                evidence.check_version,
                evidence.tool_fact_id,
                evidence.evidence_sha256,
            )
            for evidence in manifest.qc_evidence
        )
        observation_payload = {
            "measured_format": observation.get("measured_format"),
            "inspector_version": observation.get("inspector_version"),
            "metrics": {
                key: (list(value) if isinstance(value, (list, tuple)) else value)
                for key, value in sorted(observation.items())
                if key not in ("measured_format", "inspector_version")
            },
        }
        evidence_refs = (
            f"artifact_revision:{manifest.artifact_revision_id}",
            f"manifest_sha256:{manifest.manifest_sha256}",
            f"content_object:{manifest.content_object_id}",
            f"content_sha256:{manifest.sha256}",
            f"qc_evidence_set_sha256:{canonical_sha256(qc_evidence_set)}",
            f"predicate_sha256:{predicate.predicate_sha256}",
            f"observation_sha256:{canonical_sha256(observation_payload)}",
        )
        payload = dict(
            verification_record_id="vrs_" + "0" * 64,
            # Lineage is taken from the manifest itself — there is no
            # caller-supplied request/run/generation to rebind.
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            generation=manifest.generation,
            verifier_id=descriptor.verifier_id,
            verifier_version=descriptor.verifier_version,
            registry_snapshot_sha256=self._snapshot.snapshot_sha256,  # type: ignore[attr-defined]
            predicate_id=predicate.predicate_id,
            predicate_type=predicate.predicate_type,
            subject_kind="artifact",
            subject_identity=manifest.artifact_revision_id,
            evaluation_phase=evaluation_phase,
            status=status,
            enforcement="RECORD",
            reason_codes=reason_codes,
            evidence_refs=evidence_refs,
            evidence_sha256=canonical_sha256(list(evidence_refs)),
            producer_component_id=descriptor.producer_component_id,
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
    "ContentUnparseable",
    "OracleSnapshotInvalid",
]
