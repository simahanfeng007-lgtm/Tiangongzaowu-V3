"""Fact-bound access to immutable artifacts for channel delivery."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterable
from typing import BinaryIO

from contracts import ArtifactManifest, DeliveryPartGrant

from .fact_ledger import FactLedger
from .object_store import ContentAddressedObjectStore


class ArtifactContentError(RuntimeError):
    """Raised before artifact bytes are exposed when their authority is invalid."""


class VerifiedArtifactContentSource:
    """Open only artifacts backed by immutable QC facts and object-store bytes.

    Instances are intentionally scoped to the manifests in one authorized delivery
    operation.  The communication service receives only this narrow byte-source
    interface; it does not import or query total-gateway state directly.
    """

    def __init__(
        self,
        object_store: ContentAddressedObjectStore,
        fact_ledger: FactLedger,
        manifests: Iterable[ArtifactManifest],
    ) -> None:
        manifest_by_revision: dict[str, ArtifactManifest] = {}
        for manifest in manifests:
            if (
                manifest.qc_state != "PASSED"
                or not manifest.qc_evidence
                or not manifest.has_valid_manifest_sha256()
            ):
                raise ArtifactContentError("artifact.content.manifest_not_passed")
            previous = manifest_by_revision.setdefault(
                manifest.artifact_revision_id,
                manifest,
            )
            if previous != manifest:
                raise ArtifactContentError("artifact.content.revision_conflict")
        if not manifest_by_revision:
            raise ArtifactContentError("artifact.content.manifest_set_empty")
        self._object_store = object_store
        self._fact_ledger = fact_ledger
        self._manifest_by_revision = manifest_by_revision

    def open_artifact(
        self,
        grant: DeliveryPartGrant,
        *,
        timeout_seconds: int,
    ) -> BinaryIO:
        if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 3_600:
            raise ArtifactContentError("artifact.content.timeout_invalid")
        if grant.kind != "artifact" or grant.artifact_revision_id is None:
            raise ArtifactContentError("artifact.content.grant_invalid")
        manifest = self._manifest_by_revision.get(grant.artifact_revision_id)
        if manifest is None:
            raise ArtifactContentError("artifact.content.revision_not_authorized")
        if not self._grant_matches_manifest(grant, manifest):
            raise ArtifactContentError("artifact.content.grant_manifest_mismatch")

        self.verify_artifact_revision(manifest.artifact_revision_id)
        data = self._object_store.read_bytes(manifest.content_object_id)
        if (
            len(data) != manifest.size_bytes
            or hashlib.sha256(data).hexdigest() != manifest.sha256
        ):
            raise ArtifactContentError("artifact.content.object_readback_invalid")
        return io.BytesIO(data)

    def verify_artifact_revision(self, artifact_revision_id: str) -> ArtifactManifest:
        manifest = self._manifest_by_revision.get(artifact_revision_id)
        if manifest is None:
            raise ArtifactContentError("artifact.content.revision_not_authorized")
        self._verify_qc_facts(manifest)
        reference = self._object_store.get_reference(manifest.content_object_id)
        if (
            reference is None
            or reference.kind != "artifact"
            or reference.sha256 != manifest.sha256
            or reference.size_bytes != manifest.size_bytes
            or reference.tenant_id != manifest.tenant_id
            or reference.link_account_id != manifest.link_account_id
            or reference.conversation_scope_hash != manifest.conversation_scope_hash
        ):
            raise ArtifactContentError("artifact.content.object_binding_invalid")
        return manifest

    @staticmethod
    def _grant_matches_manifest(
        grant: DeliveryPartGrant,
        manifest: ArtifactManifest,
    ) -> bool:
        return (
            grant.artifact_id == manifest.artifact_id
            and grant.artifact_revision_id == manifest.artifact_revision_id
            and grant.artifact_revision == manifest.revision
            and grant.artifact_manifest_sha256 == manifest.manifest_sha256
            and grant.content_object_id == manifest.content_object_id
            and grant.content_sha256 == manifest.sha256
            and grant.size_bytes == manifest.size_bytes
            and grant.mime == manifest.mime
            and grant.filename == manifest.filename
        )

    def _verify_qc_facts(self, manifest: ArtifactManifest) -> None:
        for evidence in manifest.qc_evidence:
            record = self._fact_ledger.get_artifact_qc(
                manifest.artifact_revision_id,
                check_id=evidence.check_id,
                check_version=evidence.check_version,
            )
            if (
                record is None
                or record.manifest != manifest
                or record.result.status != "PASSED"
                or record.result.object_id != manifest.content_object_id
                or record.result.content_sha256 != manifest.sha256
                or record.fact.fact_type != "artifact.qc_passed"
                or record.fact.fact_id != evidence.tool_fact_id
                or record.fact.evidence_sha256 != evidence.evidence_sha256
                or record.fact.model_generated
            ):
                raise ArtifactContentError("artifact.content.qc_fact_invalid")


__all__ = ["ArtifactContentError", "VerifiedArtifactContentSource"]
