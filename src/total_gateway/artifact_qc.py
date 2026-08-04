"""Generic deterministic artifact QC after ArtifactGate base admission."""

from __future__ import annotations

import hashlib
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


ARTIFACT_INTEGRITY_QC_CHECK_ID = "qc.artifact.delivery_integrity"
ARTIFACT_INTEGRITY_QC_CHECK_VERSION = "1.0.0"


class ArtifactIntegrityQcError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArtifactIntegrityQcOutcome:
    registration: ArtifactQcRegistration

    @property
    def passed(self) -> bool:
        return self.registration.record.result.status == "PASSED"


class ArtifactIntegrityQcService:
    """Attest immutable object/readback and gate structure evidence for non-DOCX artifacts."""

    def __init__(self, object_store: ContentAddressedObjectStore, fact_ledger: FactLedger) -> None:
        self._object_store = object_store
        self._fact_ledger = fact_ledger

    def evaluate(
        self,
        gate_result: ArtifactGateResult,
        *,
        run_sequence: int,
        checked_at_ms: int,
    ) -> ArtifactIntegrityQcOutcome:
        manifest = gate_result.manifest
        evidence = gate_result.evidence
        if (
            manifest.qc_state != "PENDING"
            or manifest.qc_evidence
            or manifest.format_id == "docx"
            or not manifest.has_valid_manifest_sha256()
            or not evidence.has_valid_sha256()
            or evidence.object_id != manifest.content_object_id
            or evidence.content_sha256 != manifest.sha256
            or evidence.size_bytes != manifest.size_bytes
            or evidence.mime != manifest.mime
            or evidence.filename != manifest.filename
            or evidence.format_id != manifest.format_id
            or not evidence.magic_verified
            or not evidence.structure_verified
        ):
            raise ArtifactIntegrityQcError("qc.artifact.gate_evidence_invalid")
        if run_sequence < 1 or checked_at_ms < manifest.created_at_ms:
            raise ArtifactIntegrityQcError("qc.artifact.policy_invalid")

        existing = self._fact_ledger.get_artifact_qc(
            manifest.artifact_revision_id,
            check_id=ARTIFACT_INTEGRITY_QC_CHECK_ID,
            check_version=ARTIFACT_INTEGRITY_QC_CHECK_VERSION,
        )
        if existing is not None:
            if existing.manifest.model_dump(
                mode="json", exclude={"qc_state", "qc_evidence", "manifest_sha256"}
            ) != manifest.model_dump(
                mode="json", exclude={"qc_state", "qc_evidence", "manifest_sha256"}
            ):
                raise ArtifactIntegrityQcError("qc.artifact.manifest_conflict")
            return ArtifactIntegrityQcOutcome(
                registration=ArtifactQcRegistration(existing, False)
            )

        data = self._object_store.read_bytes(manifest.content_object_id)
        if len(data) != manifest.size_bytes:
            raise ArtifactIntegrityQcError("qc.artifact.size_changed")
        if hashlib.sha256(data).hexdigest() != manifest.sha256:
            raise ArtifactIntegrityQcError("qc.artifact.digest_changed")

        metrics: dict[str, str | int | bool] = {
            "base_evidence_sha256": evidence.evidence_sha256,
            "content_readback_verified": True,
            "immutable_read_count": evidence.immutable_read_count,
            "magic_verified": evidence.magic_verified,
            "package_size_bytes": len(data),
            "structure_verified": evidence.structure_verified,
        }
        for name, value in evidence.structure_summary:
            metrics[f"base.{name}"] = value

        qc_result_id = derive_qc_result_id(
            artifact_revision_id=manifest.artifact_revision_id,
            check_id=ARTIFACT_INTEGRITY_QC_CHECK_ID,
            check_version=ARTIFACT_INTEGRITY_QC_CHECK_VERSION,
            content_sha256=manifest.sha256,
        )
        effect_id = derive_qc_effect_id(
            request_id=manifest.request_id,
            run_id=manifest.run_id,
            run_sequence=run_sequence,
            generation=manifest.generation,
            artifact_revision_id=manifest.artifact_revision_id,
            check_id=ARTIFACT_INTEGRITY_QC_CHECK_ID,
            check_version=ARTIFACT_INTEGRITY_QC_CHECK_VERSION,
            content_sha256=manifest.sha256,
        )
        result = ArtifactQcResult(
            qc_result_id=qc_result_id,
            check_id=ARTIFACT_INTEGRITY_QC_CHECK_ID,
            check_version=ARTIFACT_INTEGRITY_QC_CHECK_VERSION,
            status="PASSED",
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
            reason_codes=(),
            qc_result_sha256="0" * 64,
        ).with_computed_sha256()
        qc_evidence = QcEvidence(
            check_id=result.check_id,
            check_version=result.check_version,
            status=result.status,
            checked_at_ms=result.checked_at_ms,
            evidence_sha256=result.qc_result_sha256,
            tool_fact_id=derive_qc_fact_id(result),
        )
        final_manifest = manifest.model_copy(
            update={"qc_state": result.status, "qc_evidence": (qc_evidence,)}
        ).with_computed_manifest_sha256()
        registration = self._fact_ledger.record_artifact_qc(result, final_manifest)
        return ArtifactIntegrityQcOutcome(registration=registration)


__all__ = [
    "ARTIFACT_INTEGRITY_QC_CHECK_ID",
    "ARTIFACT_INTEGRITY_QC_CHECK_VERSION",
    "ArtifactIntegrityQcError",
    "ArtifactIntegrityQcOutcome",
    "ArtifactIntegrityQcService",
]
