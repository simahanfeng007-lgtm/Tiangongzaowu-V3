"""Fact-verified artifact byte egress from 7184 to the transport-only 7176."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from contracts import DeliveryPartGrant

from .artifact_content import ArtifactContentError, VerifiedArtifactContentSource
from .fact_ledger import FactLedger
from .object_store import ContentAddressedObjectStore


class ArtifactEgressError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArtifactEgressResult:
    data: bytes
    mime: str
    filename: str
    content_sha256: str
    artifact_manifest_sha256: str


class GatewayArtifactEgress:
    """Expose one ticket-bound artifact only after all QC facts are rechecked."""

    def __init__(
        self,
        objects: ContentAddressedObjectStore,
        facts: FactLedger,
    ) -> None:
        self._objects = objects
        self._facts = facts

    def fetch(
        self,
        grant_payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> ArtifactEgressResult:
        try:
            grant = DeliveryPartGrant.model_validate(grant_payload, strict=True)
        except ValueError as exc:
            raise ArtifactEgressError("artifact_egress.grant.invalid") from exc
        if grant.kind != "artifact" or grant.artifact_revision_id is None:
            raise ArtifactEgressError("artifact_egress.grant.not_artifact")
        manifest = self._facts.get_artifact_manifest(grant.artifact_revision_id)
        if manifest is None:
            raise ArtifactEgressError("artifact_egress.manifest.missing")
        try:
            source = VerifiedArtifactContentSource(
                self._objects,
                self._facts,
                (manifest,),
            )
            stream = source.open_artifact(grant, timeout_seconds=timeout_seconds)
            try:
                data = stream.read()
            finally:
                stream.close()
        except ArtifactContentError as exc:
            raise ArtifactEgressError(str(exc)) from exc
        assert grant.mime is not None
        assert grant.filename is not None
        assert grant.content_sha256 is not None
        assert grant.artifact_manifest_sha256 is not None
        return ArtifactEgressResult(
            data=data,
            mime=grant.mime,
            filename=grant.filename,
            content_sha256=grant.content_sha256,
            artifact_manifest_sha256=grant.artifact_manifest_sha256,
        )


__all__ = [
    "ArtifactEgressError",
    "ArtifactEgressResult",
    "GatewayArtifactEgress",
]
