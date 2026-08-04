"""Fact-bound desktop Artifact Cards and safe local materialization."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from contracts import ArtifactManifest, DeliveryPartGrant, canonical_sha256
from contracts.models import ContractModel

from .artifact_content import ArtifactContentError, VerifiedArtifactContentSource
from .fact_ledger import FactLedger
from .object_store import ContentAddressedObjectStore


_OPEN_FORMATS = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", (".docx",)),
    "pdf": ("application/pdf", (".pdf",)),
    "zip": ("application/zip", (".zip",)),
    "png": ("image/png", (".png",)),
    "jpeg": ("image/jpeg", (".jpg", ".jpeg")),
    "json": ("application/json", (".json",)),
    "text": ("text/plain", (".txt",)),
    "csv": ("text/csv", (".csv",)),
}
_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_UNSAFE_FILENAME = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WINDOWS_SAFE_PATH_LENGTH = 240
_SAFE_COMPONENT_BYTES = 240


class ArtifactOpenError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class DesktopArtifactCard(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_schema: Literal["tiangong.gateway.artifact-card.v1"] = (
        "tiangong.gateway.artifact-card.v1"
    )
    gateway_request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    artifact_id: str = Field(pattern=r"^art_[0-9a-f]{64}$")
    artifact_revision_id: str = Field(pattern=r"^arv_[0-9a-f]{64}$")
    revision: int = Field(ge=1)
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, le=2_147_483_648)
    mime: str = Field(min_length=1, max_length=255)
    artifact_kind: Literal["document", "image", "audio", "video", "archive", "data", "other"]
    format_id: str = Field(min_length=1, max_length=160)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    qc_state: Literal["PASSED"] = "PASSED"
    qc_checks: tuple[str, ...] = Field(min_length=1, max_length=64)
    created_at_ms: int = Field(ge=0)
    open_capability: Literal["gateway_artifact_revision"] = "gateway_artifact_revision"
    card_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_checks(self) -> Self:
        if self.qc_checks != tuple(sorted(set(self.qc_checks))):
            raise ValueError("artifact card QC checks are not sorted and unique")
        policy = _OPEN_FORMATS.get(self.format_id)
        if (
            policy is None
            or self.mime != policy[0]
            or Path(self.filename).suffix.lower() not in policy[1]
        ):
            raise ValueError("artifact card format is not safe to open")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"card_sha256"}))

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"card_sha256": self.computed_sha256()})


class ArtifactOpenService:
    def __init__(
        self,
        fact_ledger: FactLedger,
        object_store: ContentAddressedObjectStore,
        cache_root: Path,
    ) -> None:
        if not cache_root.is_absolute() or cache_root == Path(cache_root.anchor):
            raise ValueError("artifact open cache root is invalid")
        self._facts = fact_ledger
        self._objects = object_store
        self._cache_root = cache_root

    def list_cards(
        self,
        gateway_request_id: str,
        *,
        run_id: str | None = None,
        generation: int | None = None,
    ) -> tuple[DesktopArtifactCard, ...]:
        manifests = self._facts.list_request_artifact_manifests(
            gateway_request_id,
            run_id=run_id,
            generation=generation,
        )
        cards: list[DesktopArtifactCard] = []
        for manifest in manifests:
            if manifest.qc_state != "PASSED":
                continue
            source = VerifiedArtifactContentSource(
                self._objects,
                self._facts,
                (manifest,),
            )
            try:
                source.verify_artifact_revision(manifest.artifact_revision_id)
            except ArtifactContentError as exc:
                raise ArtifactOpenError("artifact.open.authority_invalid") from exc
            cards.append(self._card(manifest))
        return tuple(cards)

    def materialize(
        self,
        *,
        gateway_request_id: str,
        run_id: str,
        generation: int,
        artifact_revision_id: str,
        manifest_sha256: str,
        card_sha256: str,
    ) -> tuple[DesktopArtifactCard, Path]:
        manifest = self._facts.get_artifact_manifest(artifact_revision_id)
        if manifest is None:
            raise ArtifactOpenError("artifact.open.revision_not_found")
        if (
            manifest.request_id != gateway_request_id
            or manifest.run_id != run_id
            or manifest.generation != generation
            or manifest.manifest_sha256 != manifest_sha256
            or manifest.qc_state != "PASSED"
        ):
            raise ArtifactOpenError("artifact.open.manifest_binding_invalid")
        card = self._card(manifest)
        if card.card_sha256 != card_sha256:
            raise ArtifactOpenError("artifact.open.card_binding_invalid")
        source = VerifiedArtifactContentSource(
            self._objects,
            self._facts,
            (manifest,),
        )
        grant = self._grant(manifest)
        try:
            stream = source.open_artifact(grant, timeout_seconds=120)
            try:
                data = stream.read(manifest.size_bytes + 1)
            finally:
                stream.close()
        except ArtifactContentError as exc:
            raise ArtifactOpenError("artifact.open.authority_invalid") from exc
        if (
            len(data) != manifest.size_bytes
            or hashlib.sha256(data).hexdigest() != manifest.sha256
        ):
            raise ArtifactOpenError("artifact.open.readback_invalid")
        try:
            target = self._materialize_bytes(manifest, data)
        except ArtifactOpenError:
            raise
        except OSError as exc:
            raise ArtifactOpenError("artifact.open.cache_io_failed") from exc
        return card, target

    @staticmethod
    def _grant(manifest: ArtifactManifest) -> DeliveryPartGrant:
        return DeliveryPartGrant(
            part_id="desktop_artifact_open_" + manifest.artifact_revision_id[4:36],
            index=0,
            kind="artifact",
            artifact_id=manifest.artifact_id,
            artifact_revision_id=manifest.artifact_revision_id,
            artifact_revision=manifest.revision,
            artifact_manifest_sha256=manifest.manifest_sha256,
            content_object_id=manifest.content_object_id,
            content_sha256=manifest.sha256,
            size_bytes=manifest.size_bytes,
            mime=manifest.mime,
            filename=manifest.filename,
        )

    @staticmethod
    def _card(manifest: ArtifactManifest) -> DesktopArtifactCard:
        checks = tuple(
            sorted(
                f"{item.check_id}@{item.check_version}"
                for item in manifest.qc_evidence
                if item.status == "PASSED"
            )
        )
        return DesktopArtifactCard(
            gateway_request_id=manifest.request_id,
            run_id=manifest.run_id,
            generation=manifest.generation,
            artifact_id=manifest.artifact_id,
            artifact_revision_id=manifest.artifact_revision_id,
            revision=manifest.revision,
            filename=manifest.filename,
            size_bytes=manifest.size_bytes,
            mime=manifest.mime,
            artifact_kind=manifest.artifact_kind,
            format_id=manifest.format_id,
            content_sha256=manifest.sha256,
            manifest_sha256=manifest.manifest_sha256,
            qc_checks=checks,
            created_at_ms=manifest.created_at_ms,
            card_sha256="0" * 64,
        ).with_computed_sha256()

    def _materialize_bytes(self, manifest: ArtifactManifest, data: bytes) -> Path:
        root = self._cache_root
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ArtifactOpenError("artifact.open.cache_root_unsafe")
        os.chmod(root, 0o700)
        resolved_root = root.resolve(strict=True)
        cache_key = hashlib.sha256(
            (
                manifest.artifact_revision_id
                + "\0"
                + manifest.manifest_sha256
            ).encode("ascii")
        ).hexdigest()
        parent = root / cache_key
        parent.mkdir(exist_ok=True)
        if (
            parent.is_symlink()
            or parent.resolve(strict=True).parent != resolved_root
        ):
            raise ArtifactOpenError("artifact.open.cache_path_unsafe")
        target = parent / self._local_filename(manifest, parent)
        if target.exists():
            if (
                target.is_symlink()
                or not target.is_file()
                or target.stat().st_size != manifest.size_bytes
                or hashlib.sha256(target.read_bytes()).hexdigest() != manifest.sha256
            ):
                raise ArtifactOpenError("artifact.open.cached_copy_invalid")
            return target
        temporary = root / (".materialize-" + secrets.token_hex(12) + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            if (
                target.is_symlink()
                or not target.is_file()
                or target.stat().st_size != manifest.size_bytes
                or hashlib.sha256(target.read_bytes()).hexdigest() != manifest.sha256
            ):
                raise ArtifactOpenError("artifact.open.materialization_invalid")
            os.chmod(target, 0o600)
            return target
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _local_filename(manifest: ArtifactManifest, parent: Path) -> str:
        original = str(manifest.filename)
        suffix = Path(original.replace("\\", "/")).suffix.lower()
        stem = original[: -len(suffix)] if suffix else original
        safe_stem = _WINDOWS_UNSAFE_FILENAME.sub("_", stem).rstrip(" .")
        if not safe_stem or safe_stem in {".", ".."}:
            safe_stem = "artifact"
        if safe_stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED_FILENAMES:
            safe_stem = "_" + safe_stem

        tag = "~" + manifest.sha256[:12]
        changed = safe_stem != stem
        candidate = safe_stem + (tag if changed else "") + suffix
        char_budget = 255
        if os.name == "nt":
            char_budget = _WINDOWS_SAFE_PATH_LENGTH - len(str(parent)) - 1
        byte_budget = _SAFE_COMPONENT_BYTES
        if (
            char_budget < len(tag) + len(suffix) + 1
            or byte_budget < len((tag + suffix).encode("utf-8")) + 1
        ):
            raise ArtifactOpenError("artifact.open.cache_path_too_long")

        if len(candidate) > char_budget or len(candidate.encode("utf-8")) > byte_budget:
            changed = True
        if changed:
            while safe_stem and (
                len(safe_stem + tag + suffix) > char_budget
                or len((safe_stem + tag + suffix).encode("utf-8")) > byte_budget
            ):
                safe_stem = safe_stem[:-1]
            if not safe_stem:
                safe_stem = "a"
            candidate = safe_stem + tag + suffix
        return candidate


__all__ = [
    "ArtifactOpenError",
    "ArtifactOpenService",
    "DesktopArtifactCard",
]
