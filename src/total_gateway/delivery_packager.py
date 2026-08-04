"""Atomic object-based delivery ZIP packaging with no host-path API."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import zipfile
from dataclasses import dataclass
from pathlib import Path

from contracts import ArtifactManifest, canonical_json_bytes, canonical_sha256
from contracts.models import validate_safe_filename

from .fact_ledger import FactLedger
from .object_store import ContentAddressedObjectStore, ObjectReference


class DeliveryPackagingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DeliveryPackageItem:
    artifact_id: str
    artifact_revision_id: str
    revision: int
    object_id: str
    content_sha256: str
    size_bytes: int
    mime: str
    filename: str
    artifact_manifest_sha256: str

    @classmethod
    def from_manifest(cls, manifest: ArtifactManifest) -> "DeliveryPackageItem":
        return cls(
            artifact_id=manifest.artifact_id,
            artifact_revision_id=manifest.artifact_revision_id,
            revision=manifest.revision,
            object_id=manifest.content_object_id,
            content_sha256=manifest.sha256,
            size_bytes=manifest.size_bytes,
            mime=manifest.mime,
            filename=manifest.filename,
            artifact_manifest_sha256=manifest.manifest_sha256,
        )


@dataclass(frozen=True)
class DeliveryPackageManifest:
    schema: str
    package_id: str
    request_id: str
    run_id: str
    generation: int
    tenant_id: str
    link_account_id: str
    conversation_scope_hash: str
    filename: str
    created_at_ms: int
    items: tuple[DeliveryPackageItem, ...]
    notes: str
    manifest_sha256: str

    def payload(self, *, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": self.schema,
            "package_id": self.package_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "tenant_id": self.tenant_id,
            "link_account_id": self.link_account_id,
            "conversation_scope_hash": self.conversation_scope_hash,
            "filename": self.filename,
            "created_at_ms": self.created_at_ms,
            "items": tuple(item.__dict__ for item in self.items),
            "notes": self.notes,
        }
        if include_digest:
            payload["manifest_sha256"] = self.manifest_sha256
        return payload

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload(include_digest=False))

    def has_valid_sha256(self) -> bool:
        return self.manifest_sha256 == self.computed_sha256()


@dataclass(frozen=True)
class DeliveryPackageResult:
    manifest: DeliveryPackageManifest
    object_reference: ObjectReference
    package_sha256: str
    package_size_bytes: int
    created_by_this_call: bool


class DeliveryPackager:
    def __init__(
        self,
        object_store: ContentAddressedObjectStore,
        fact_ledger: FactLedger,
        staging_root: Path,
        *,
        max_package_bytes: int = 1_073_741_824,
    ) -> None:
        if (
            not staging_root.is_absolute()
            or staging_root == Path(staging_root.anchor)
            or not 1 <= max_package_bytes <= 2_147_483_648
        ):
            raise ValueError("delivery package staging root or size limit is invalid")
        staging_root.mkdir(parents=True, exist_ok=True)
        if staging_root.is_symlink() or not staging_root.is_dir():
            raise ValueError("delivery package staging root is unsafe")
        self._object_store = object_store
        self._fact_ledger = fact_ledger
        self._staging_root = staging_root
        self._max_package_bytes = max_package_bytes

    def package(
        self,
        artifacts: tuple[ArtifactManifest, ...],
        *,
        filename: str,
        created_at_ms: int,
        notes: str = "",
    ) -> DeliveryPackageResult:
        if not artifacts:
            raise DeliveryPackagingError("package.items.empty")
        if created_at_ms < 0 or len(notes) > 10_000 or "\x00" in notes:
            raise DeliveryPackagingError("package.metadata.invalid")
        try:
            validate_safe_filename(filename)
        except ValueError as exc:
            raise DeliveryPackagingError("package.filename.unsafe") from exc
        if not filename.casefold().endswith(".zip"):
            raise DeliveryPackagingError("package.filename.not_zip")
        ordered = tuple(sorted(artifacts, key=lambda item: (item.filename.casefold(), item.artifact_id)))
        if len({item.artifact_revision_id for item in ordered}) != len(ordered):
            raise DeliveryPackagingError("package.items.duplicate_revision")
        folded_names = tuple(item.filename.casefold() for item in ordered)
        if len(set(folded_names)) != len(folded_names) or "delivery_manifest.json" in folded_names:
            raise DeliveryPackagingError("package.items.duplicate_filename")
        first = ordered[0]
        item_records: list[DeliveryPackageItem] = []
        item_bytes: list[tuple[str, bytes]] = []
        total_input_bytes = 0
        for artifact in ordered:
            self._validate_artifact(artifact, first)
            data = self._object_store.read_bytes(artifact.content_object_id)
            if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
                raise DeliveryPackagingError("package.item.readback_mismatch")
            total_input_bytes += len(data)
            if total_input_bytes > self._max_package_bytes:
                raise DeliveryPackagingError("package.items.total_too_large")
            item_records.append(DeliveryPackageItem.from_manifest(artifact))
            item_bytes.append((artifact.filename, data))
        items = tuple(item_records)
        package_id = "pkg_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.delivery-package.v1",
                "request_id": first.request_id,
                "run_id": first.run_id,
                "generation": first.generation,
                "filename": filename,
                "items": tuple(item.__dict__ for item in items),
            }
        )
        manifest = DeliveryPackageManifest(
            schema="tiangong.gateway.delivery-package.v1",
            package_id=package_id,
            request_id=first.request_id,
            run_id=first.run_id,
            generation=first.generation,
            tenant_id=first.tenant_id,
            link_account_id=first.link_account_id,
            conversation_scope_hash=first.conversation_scope_hash,
            filename=filename,
            created_at_ms=created_at_ms,
            items=items,
            notes=notes,
            manifest_sha256="0" * 64,
        )
        manifest = DeliveryPackageManifest(
            **{**manifest.__dict__, "manifest_sha256": manifest.computed_sha256()}
        )
        manifest_bytes = canonical_json_bytes(manifest.payload())
        temporary = self._staging_root / ("package-" + secrets.token_hex(16) + ".tmp")
        try:
            self._write_verified_zip(temporary, item_bytes, manifest_bytes)
            package_size = temporary.stat().st_size
            if not 1 <= package_size <= self._max_package_bytes:
                raise DeliveryPackagingError("package.output.size_invalid")
            package_sha256 = self._hash_file(temporary)
            with temporary.open("rb") as stream:
                put = self._object_store.put_stream(
                    iter(lambda: stream.read(1024 * 1024), b""),
                    kind="delivery_package",
                    tenant_id=first.tenant_id,
                    link_account_id=first.link_account_id,
                    conversation_scope_hash=first.conversation_scope_hash,
                    created_at_ms=created_at_ms,
                    max_bytes=self._max_package_bytes,
                )
            reference = put.reference
            if reference.sha256 != package_sha256 or reference.size_bytes != package_size:
                raise DeliveryPackagingError("package.object.commit_mismatch")
            committed = self._object_store.read_bytes(reference.object_id)
            if len(committed) != package_size or hashlib.sha256(committed).hexdigest() != package_sha256:
                raise DeliveryPackagingError("package.object.readback_mismatch")
            if reference.object_id in {item.object_id for item in items}:
                raise DeliveryPackagingError("package.input_output_identity_conflict")
            return DeliveryPackageResult(
                manifest=manifest,
                object_reference=reference,
                package_sha256=package_sha256,
                package_size_bytes=package_size,
                created_by_this_call=put.created_by_this_call,
            )
        finally:
            if temporary.exists():
                temporary.unlink()

    def _validate_artifact(self, artifact: ArtifactManifest, first: ArtifactManifest) -> None:
        if artifact.qc_state != "PASSED" or not artifact.has_valid_manifest_sha256():
            raise DeliveryPackagingError("package.item.qc_not_passed")
        if (
            artifact.request_id != first.request_id
            or artifact.run_id != first.run_id
            or artifact.generation != first.generation
            or artifact.tenant_id != first.tenant_id
            or artifact.link_account_id != first.link_account_id
            or artifact.conversation_scope_hash != first.conversation_scope_hash
        ):
            raise DeliveryPackagingError("package.item.scope_mismatch")
        reference = self._object_store.get_reference(artifact.content_object_id)
        if (
            reference is None
            or reference.kind != "artifact"
            or reference.sha256 != artifact.sha256
            or reference.size_bytes != artifact.size_bytes
            or reference.tenant_id != artifact.tenant_id
            or reference.link_account_id != artifact.link_account_id
            or reference.conversation_scope_hash != artifact.conversation_scope_hash
        ):
            raise DeliveryPackagingError("package.item.object_binding_mismatch")
        for evidence in artifact.qc_evidence:
            qc = self._fact_ledger.get_artifact_qc(
                artifact.artifact_revision_id,
                check_id=evidence.check_id,
                check_version=evidence.check_version,
            )
            if (
                qc is None
                or qc.result.status != "PASSED"
                or qc.result.content_sha256 != artifact.sha256
                or qc.fact.fact_id != evidence.tool_fact_id
                or qc.result.qc_result_sha256 != evidence.evidence_sha256
                or qc.manifest != artifact
            ):
                raise DeliveryPackagingError("package.item.qc_fact_missing")

    @staticmethod
    def _zip_info(filename: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(filename=filename, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        return info

    def _write_verified_zip(
        self,
        path: Path,
        items: list[tuple[str, bytes]],
        manifest_bytes: bytes,
    ) -> None:
        with path.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", allowZip64=True) as archive:
                for filename, data in items:
                    archive.writestr(self._zip_info(filename), data)
                archive.writestr(self._zip_info("DELIVERY_MANIFEST.json"), manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        with zipfile.ZipFile(path, "r") as archive:
            if archive.testzip() is not None:
                raise DeliveryPackagingError("package.verify.crc_failed")
            expected = {filename for filename, _ in items} | {"DELIVERY_MANIFEST.json"}
            if set(archive.namelist()) != expected or len(archive.infolist()) != len(expected):
                raise DeliveryPackagingError("package.verify.membership_mismatch")
            if archive.read("DELIVERY_MANIFEST.json") != manifest_bytes:
                raise DeliveryPackagingError("package.verify.manifest_mismatch")
            try:
                stored = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DeliveryPackagingError("package.verify.manifest_invalid") from exc
            if canonical_json_bytes(stored) != manifest_bytes:
                raise DeliveryPackagingError("package.verify.manifest_not_canonical")

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = [
    "DeliveryPackageItem",
    "DeliveryPackageManifest",
    "DeliveryPackageResult",
    "DeliveryPackager",
    "DeliveryPackagingError",
]
