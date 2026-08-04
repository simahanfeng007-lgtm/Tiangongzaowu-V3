"""Lossless P11 copy-on-write import, writer handoff, and rollback authority."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .legacy_adapter import (
    LegacySnapshotError,
    LegacySnapshotReader,
    SNAPSHOT_MANIFEST_SCHEMA,
    snapshot_tree_sha256,
)
from .memory_migration import LegacyMemoryRecord, migrate_legacy_memory_records
from .store import LifeShadowStore, LifeShadowStoreError


COW_IMPORT_SCHEMA = "tiangong.life.cow-import.v1"
DRAIN_EVIDENCE_SCHEMA = "tiangong.life.cutover-drain.v1"
HANDOFF_PERMIT_SCHEMA = "tiangong.life.writer-handoff.v1"
CUTOVER_COMPARISON_SCHEMA = "tiangong.life.cutover-comparison.v1"
CUTOVER_BUNDLE_SCHEMA = "tiangong.life.cutover-state-bundle.v1"
ACTIVE_RELEASE_SCHEMA = "tiangong.life.cutover-active-release.v1"
CUTOVER_TRUST_SCHEMA = "tiangong.life.cutover-root-trust.v1"
PRODUCTION_PORT = 7175
ZERO_SHA256 = "0" * 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LifeCutoverError(RuntimeError):
    """A fail-closed cutover validation error with a stable code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _reject_constant(value: str) -> Any:
    raise LifeCutoverError("cutover.json_non_finite", f"non-finite JSON: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LifeCutoverError("cutover.json_duplicate_key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LifeCutoverError("cutover.json_invalid") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _load_object(path: Path, *, max_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LifeCutoverError("cutover.file_missing", str(path)) from exc
    if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > max_bytes:
        raise LifeCutoverError("cutover.file_unsafe", str(path))
    try:
        value = json.loads(
            resolved.read_bytes().decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except LifeCutoverError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifeCutoverError("cutover.file_json_invalid", str(path)) from exc
    if not isinstance(value, dict):
        raise LifeCutoverError("cutover.file_not_object", str(path))
    return value


def _atomic_write(path: Path, payload: bytes) -> None:
    parent = path.parent.resolve(strict=True)
    candidate = parent / path.name
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise LifeCutoverError("cutover.write_target_unsafe", str(candidate))
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.exists():
        raise LifeCutoverError("cutover.temporary_exists", str(temporary))
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, candidate)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_copy(source: Path, target: Path) -> None:
    source = source.resolve(strict=True)
    parent = target.parent.resolve(strict=True)
    temporary = parent / f".{target.name}.{os.getpid()}.copy"
    if source.is_symlink() or not source.is_file() or temporary.exists():
        raise LifeCutoverError("cutover.copy_source_unsafe")
    try:
        shutil.copy2(source, temporary)
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _path_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _trust_document(
    *,
    life_id: str,
    public_key_sha256: str,
    initial_manifest_sha256: str,
) -> dict[str, Any]:
    value = {
        "schema": CUTOVER_TRUST_SCHEMA,
        "life_id": life_id,
        "public_key_sha256": public_key_sha256,
        "initial_manifest_sha256": initial_manifest_sha256,
    }
    value["trust_sha256"] = _sha256(value)
    return value


def _load_trust(path: Path) -> dict[str, Any]:
    value = _load_object(path)
    digest = str(value.pop("trust_sha256", ""))
    if (
        set(value)
        != {
            "schema",
            "life_id",
            "public_key_sha256",
            "initial_manifest_sha256",
        }
        or value.get("schema") != CUTOVER_TRUST_SCHEMA
        or not isinstance(value.get("life_id"), str)
        or not _SHA256.fullmatch(str(value.get("public_key_sha256") or ""))
        or not _SHA256.fullmatch(str(value.get("initial_manifest_sha256") or ""))
        or digest != _sha256(value)
    ):
        raise LifeCutoverError("cutover.root_trust_invalid")
    return {**value, "trust_sha256": digest}


def _overlay_identity(store: LifeShadowStore) -> str:
    health = store.health()
    return _sha256(
        {
            "application_id": health["application_id"],
            "purpose": health["purpose"],
            "schema_sha256": health["schema_sha256"],
            "schema_version": health["schema_version"],
            "strict_table_count": health["strict_table_count"],
        }
    )


def _migrated_memory_id(life_id: str, legacy_memory_id: str) -> str:
    return "mem_" + _sha256(
        {
            "domain": "tiangong.life.legacy-memory.v1",
            "legacy_memory_id": legacy_memory_id,
            "life_id": life_id,
        }
    )


def _legacy_records(reader: LegacySnapshotReader, life_id: str) -> tuple[LegacyMemoryRecord, ...]:
    records: list[LegacyMemoryRecord] = []
    for metadata in reader.memory_records(life_id):
        legacy_id = str(metadata["memory_id"])
        terms = tuple(
            sorted(
                {
                    term
                    for term in str(metadata.get("search_text") or "").split()
                    if term and len(term) <= 256
                }
            )
        )[:128]
        records.append(
            LegacyMemoryRecord(
                legacy_memory_id=legacy_id,
                memory_type=str(metadata.get("memory_type") or "legacy"),
                status=str(metadata.get("status") or "active"),
                content=reader.redacted_memory_content(legacy_id, life_id),
                search_terms=terms,
            )
        )
    return tuple(records)


def _sync_legacy_memory_overlay(
    reader: LegacySnapshotReader,
    store: LifeShadowStore,
    *,
    now_ms: int,
) -> int:
    """Copy legacy memories once; retries compare plaintext and never rewrite history."""

    life_id = reader.active_life_id()
    records = _legacy_records(reader, life_id)
    new_records: list[LegacyMemoryRecord] = []
    for record in records:
        memory_id = _migrated_memory_id(life_id, record.legacy_memory_id)
        existing = store.get_latest_memory_assertion(memory_id)
        if existing is None:
            new_records.append(record)
            continue
        if (
            existing.protected_payload_id is None
            or store.read_protected_payload(existing.protected_payload_id)
            != _canonical_bytes(dict(record.content))
        ):
            raise LifeCutoverError("cutover.legacy_memory_rebound")
    if new_records:
        migrate_legacy_memory_records(
            store,
            life_id=life_id,
            records=tuple(new_records),
            migrated_at_ms=now_ms,
            privacy_scope="private",
        )
    migrated = store.list_latest_memory_assertions(life_id, recallable_only=False)
    if len(migrated) != len(records):
        raise LifeCutoverError("cutover.legacy_memory_import_incomplete")
    return len(migrated)


def _journal_hash_at(reader: LegacySnapshotReader, sequence: int) -> str:
    verified = reader.verify_journal()
    if sequence == 0:
        return ""
    if sequence < 0 or sequence > int(verified["sequence"]):
        raise LifeCutoverError("cutover.journal_sequence_invalid")
    path = reader.life_root() / "journal" / "current" / "life_events.jsonl"
    seen = 0
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line in handle:
                if not line.strip():
                    continue
                seen += 1
                if seen != sequence:
                    continue
                value = json.loads(
                    line,
                    object_pairs_hook=_reject_duplicate_pairs,
                    parse_constant=_reject_constant,
                )
                digest = str(value.get("event_hash") or "") if isinstance(value, dict) else ""
                if not _SHA256.fullmatch(digest):
                    raise LifeCutoverError("cutover.journal_prefix_hash_invalid")
                return digest
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifeCutoverError("cutover.journal_prefix_unreadable") from exc
    raise LifeCutoverError("cutover.journal_prefix_missing")


@dataclass(frozen=True, slots=True)
class LifeCowImportManifest:
    schema: str
    life_id: str
    source_snapshot: str
    source_tree_sha256: str
    base_tree_sha256: str
    identity_sha256: str
    soul_sha256: str
    writer_epoch: int
    event_sequence: int
    event_hash: str
    memory_total: int
    memory_content_sha256: str
    context_hash: str
    context_content_sha256: str
    projection_sha256: str
    affect_sha256: str
    capabilities_sha256: str
    overlay_file: str
    overlay_identity_sha256: str
    previous_manifest_sha256: str | None
    delta_from_sequence: int
    imported_at_ms: int
    manifest_sha256: str

    def unsigned_dict(self) -> dict[str, Any]:
        result = self.to_dict()
        result.pop("manifest_sha256")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "affect_sha256": self.affect_sha256,
            "base_tree_sha256": self.base_tree_sha256,
            "capabilities_sha256": self.capabilities_sha256,
            "context_content_sha256": self.context_content_sha256,
            "context_hash": self.context_hash,
            "delta_from_sequence": self.delta_from_sequence,
            "event_hash": self.event_hash,
            "event_sequence": self.event_sequence,
            "identity_sha256": self.identity_sha256,
            "imported_at_ms": self.imported_at_ms,
            "life_id": self.life_id,
            "manifest_sha256": self.manifest_sha256,
            "memory_content_sha256": self.memory_content_sha256,
            "memory_total": self.memory_total,
            "overlay_file": self.overlay_file,
            "overlay_identity_sha256": self.overlay_identity_sha256,
            "previous_manifest_sha256": self.previous_manifest_sha256,
            "projection_sha256": self.projection_sha256,
            "schema": self.schema,
            "soul_sha256": self.soul_sha256,
            "source_snapshot": self.source_snapshot,
            "source_tree_sha256": self.source_tree_sha256,
            "writer_epoch": self.writer_epoch,
        }

    def with_digest(self) -> "LifeCowImportManifest":
        return replace(self, manifest_sha256=_sha256(self.unsigned_dict()))

    def validate(self) -> None:
        digests = (
            self.source_tree_sha256,
            self.base_tree_sha256,
            self.identity_sha256,
            self.soul_sha256,
            self.memory_content_sha256,
            self.context_content_sha256,
            self.projection_sha256,
            self.affect_sha256,
            self.capabilities_sha256,
            self.overlay_identity_sha256,
            self.manifest_sha256,
        )
        integers = (
            self.writer_epoch,
            self.event_sequence,
            self.memory_total,
            self.delta_from_sequence,
            self.imported_at_ms,
        )
        if (
            self.schema != COW_IMPORT_SCHEMA
            or not isinstance(self.life_id, str)
            or not self.life_id
            or not isinstance(self.source_snapshot, str)
            or not self.source_snapshot
            or not isinstance(self.overlay_file, str)
            or self.overlay_file != "life-overlay.shadow.sqlite3"
            or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in digests)
            or (
                self.previous_manifest_sha256 is not None
                and (
                    not isinstance(self.previous_manifest_sha256, str)
                    or not _SHA256.fullmatch(self.previous_manifest_sha256)
                )
            )
            or any(isinstance(value, bool) or not isinstance(value, int) for value in integers)
            or self.writer_epoch < 1
            or self.event_sequence < 0
            or self.memory_total < 0
            or self.delta_from_sequence < 0
            or self.delta_from_sequence > self.event_sequence
            or self.imported_at_ms < 0
            or not isinstance(self.event_hash, str)
            or not isinstance(self.context_hash, str)
            or (self.context_hash != "" and not _SHA256.fullmatch(self.context_hash))
            or (self.event_sequence == 0 and self.event_hash != "")
            or (self.event_sequence > 0 and not _SHA256.fullmatch(self.event_hash))
            or self.manifest_sha256 != _sha256(self.unsigned_dict())
        ):
            raise LifeCutoverError("cutover.import_manifest_invalid")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LifeCowImportManifest":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise LifeCutoverError("cutover.import_manifest_shape_invalid")
        try:
            result = cls(**dict(value))
        except TypeError as exc:
            raise LifeCutoverError("cutover.import_manifest_invalid") from exc
        result.validate()
        return result


def load_cow_manifest(path: Path) -> LifeCowImportManifest:
    return LifeCowImportManifest.from_dict(_load_object(path))


def _build_manifest(
    reader: LegacySnapshotReader,
    *,
    overlay_file: str,
    overlay_identity_sha256: str,
    base_tree_sha256: str,
    previous_manifest_sha256: str | None,
    delta_from_sequence: int,
    imported_at_ms: int,
) -> LifeCowImportManifest:
    anchor = reader.anchor()
    journal = reader.verify_journal()
    if (
        anchor.projection_source_sequence != anchor.event_sequence
        or anchor.projection_source_hash != anchor.event_hash
    ):
        raise LifeCutoverError("cutover.projection_not_at_journal_head")
    memory_hashes: list[dict[str, str]] = []
    for memory_id in reader.memory_ids(anchor.life_id):
        content = reader.decrypt_memory_content(memory_id, anchor.life_id)
        memory_hashes.append({"memory_id": memory_id, "content_sha256": _sha256(content)})
    latest = reader.latest_context(anchor.life_id)
    context_hash = ""
    context_content_sha256 = ZERO_SHA256
    if latest.get("available") is True:
        context_hash = str(latest["meta"].get("context_hash") or "")
        context_content_sha256 = _sha256(
            {"envelope": latest["envelope"], "meta": latest["meta"]}
        )
    projection = reader.projection(anchor.life_id)
    identity = reader.identity(anchor.life_id)
    soul = reader.soul(anchor.life_id)
    tree = snapshot_tree_sha256(reader.root)
    manifest = LifeCowImportManifest(
        schema=COW_IMPORT_SCHEMA,
        life_id=anchor.life_id,
        source_snapshot=str(reader.root),
        source_tree_sha256=tree,
        base_tree_sha256=base_tree_sha256,
        identity_sha256=_sha256(identity),
        soul_sha256=_sha256(soul),
        writer_epoch=anchor.writer_epoch,
        event_sequence=int(journal["sequence"]),
        event_hash=str(journal["last_hash"]),
        memory_total=len(memory_hashes),
        memory_content_sha256=_sha256(memory_hashes),
        context_hash=context_hash,
        context_content_sha256=context_content_sha256,
        projection_sha256=_sha256(projection),
        affect_sha256=_sha256(projection.get("affect", {})),
        capabilities_sha256=_sha256(projection.get("capabilities", {})),
        overlay_file=overlay_file,
        overlay_identity_sha256=overlay_identity_sha256,
        previous_manifest_sha256=previous_manifest_sha256,
        delta_from_sequence=delta_from_sequence,
        imported_at_ms=imported_at_ms,
        manifest_sha256=ZERO_SHA256,
    ).with_digest()
    manifest.validate()
    if manifest.memory_total != anchor.memory_total or tree != anchor_to_tree(reader):
        raise LifeCutoverError("cutover.import_changed_during_validation")
    return manifest


def anchor_to_tree(reader: LegacySnapshotReader) -> str:
    """Re-read the tree at the end of an import to close the validation race."""

    return snapshot_tree_sha256(reader.root)


def capture_stopped_legacy_snapshot(
    source_root: Path,
    snapshot_root: Path,
    *,
    writer_stopped: bool,
    now_ms: int,
) -> dict[str, Any]:
    """Capture and fully verify one stopped legacy life-data tree."""

    if writer_stopped is not True:
        raise LifeCutoverError("cutover.snapshot_writer_not_stopped")
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise LifeCutoverError("cutover.snapshot_time_invalid")
    try:
        source = source_root.expanduser().resolve(strict=True)
        parent = snapshot_root.expanduser().parent.resolve(strict=True)
    except OSError as exc:
        raise LifeCutoverError("cutover.snapshot_path_invalid") from exc
    target = parent / snapshot_root.name
    if not source.is_dir() or source.is_symlink() or _path_overlap(source, target):
        raise LifeCutoverError("cutover.snapshot_path_unsafe")
    if target.exists():
        raise LifeCutoverError("cutover.snapshot_target_exists")

    paths = sorted(source.rglob("*"), key=lambda item: item.as_posix())
    if any(path.is_symlink() for path in paths):
        raise LifeCutoverError("cutover.snapshot_symlink_forbidden")
    if any(
        path.is_file() and path.name.endswith((".sqlite3-wal", ".sqlite3-shm"))
        for path in paths
    ):
        raise LifeCutoverError("cutover.snapshot_sqlite_not_checkpointed")

    lives_root = source / "lives"
    if not lives_root.is_dir() or lives_root.is_symlink():
        raise LifeCutoverError("cutover.snapshot_lives_missing")
    life_roots = {
        path.name: f"lives/{path.name}"
        for path in sorted(lives_root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and not path.is_symlink() and _SAFE_ID.fullmatch(path.name)
    }
    if not life_roots:
        raise LifeCutoverError("cutover.snapshot_life_roots_empty")

    source_before = snapshot_tree_sha256(source)
    try:
        shutil.copytree(source, target, symlinks=False)
        source_after = snapshot_tree_sha256(source)
        copied_tree = snapshot_tree_sha256(target)
        if source_before != source_after or copied_tree != source_before:
            raise LifeCutoverError("cutover.snapshot_source_changed")
        captured_at = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        manifest = {
            "schema": SNAPSHOT_MANIFEST_SCHEMA,
            "source_kind": "snapshot_copy",
            "immutable": True,
            "capture_consistency": "atomic",
            "capture_method": "stopped_process_copy",
            "captured_at": captured_at,
            "life_roots": life_roots,
            "tree_sha256": copied_tree,
        }
        _atomic_write(
            target / "life_snapshot_manifest.json",
            _canonical_bytes(manifest),
        )
        reader = LegacySnapshotReader(target)
        reader.anchor()
        return manifest
    except Exception:
        if target.exists() and target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        raise


def prepare_cow_import(
    snapshot_root: Path,
    stage_root: Path,
    *,
    now_ms: int,
) -> LifeCowImportManifest:
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise LifeCutoverError("cutover.timestamp_invalid")
    reader = LegacySnapshotReader(snapshot_root)
    source = reader.root
    parent = stage_root.expanduser().parent.resolve(strict=True)
    stage = parent / stage_root.name
    if _path_overlap(source, stage):
        raise LifeCutoverError("cutover.stage_overlaps_source")
    if stage.exists():
        raise LifeCutoverError("cutover.stage_already_exists")
    stage.mkdir()
    try:
        overlay = stage / "life-overlay.shadow.sqlite3"
        store = LifeShadowStore.open(overlay, create=True, now_ms=now_ms)
        try:
            _sync_legacy_memory_overlay(reader, store, now_ms=now_ms)
            overlay_identity = _overlay_identity(store)
        finally:
            store.close()
        base_tree = snapshot_tree_sha256(source)
        manifest = _build_manifest(
            reader,
            overlay_file=overlay.name,
            overlay_identity_sha256=overlay_identity,
            base_tree_sha256=base_tree,
            previous_manifest_sha256=None,
            delta_from_sequence=0,
            imported_at_ms=now_ms,
        )
        _atomic_write(stage / "cow_import.json", _canonical_bytes(manifest.to_dict()))
        return manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def capture_final_delta(
    final_snapshot_root: Path,
    stage_root: Path,
    *,
    previous_manifest_path: Path | None = None,
    now_ms: int,
) -> LifeCowImportManifest:
    stage = stage_root.expanduser().resolve(strict=True)
    previous_path = previous_manifest_path or (stage / "cow_import.json")
    previous = load_cow_manifest(previous_path)
    if (
        isinstance(now_ms, bool)
        or not isinstance(now_ms, int)
        or now_ms < previous.imported_at_ms
    ):
        raise LifeCutoverError("cutover.final_delta_time_invalid")
    reader = LegacySnapshotReader(final_snapshot_root)
    if _path_overlap(reader.root, stage):
        raise LifeCutoverError("cutover.stage_overlaps_source")
    overlay = stage / previous.overlay_file
    store = LifeShadowStore.open(overlay, create=False, now_ms=now_ms)
    try:
        overlay_identity = _overlay_identity(store)
    finally:
        store.close()
    if overlay_identity != previous.overlay_identity_sha256:
        raise LifeCutoverError("cutover.overlay_identity_changed")
    anchor = reader.anchor()
    if (
        anchor.life_id != previous.life_id
        or anchor.identity_sha256 != previous.identity_sha256
        or anchor.soul_sha256 != previous.soul_sha256
        or anchor.writer_epoch != previous.writer_epoch
        or anchor.event_sequence < previous.event_sequence
        or _journal_hash_at(reader, previous.event_sequence) != previous.event_hash
    ):
        raise LifeCutoverError("cutover.final_delta_not_prefix_compatible")
    candidate = _build_manifest(
        reader,
        overlay_file=previous.overlay_file,
        overlay_identity_sha256=overlay_identity,
        base_tree_sha256=previous.base_tree_sha256,
        previous_manifest_sha256=previous.manifest_sha256,
        delta_from_sequence=previous.event_sequence,
        imported_at_ms=now_ms,
    )
    if candidate.event_sequence == previous.event_sequence and any(
        getattr(candidate, field) != getattr(previous, field)
        for field in (
            "memory_total",
            "memory_content_sha256",
            "projection_sha256",
            "affect_sha256",
            "capabilities_sha256",
        )
    ):
        raise LifeCutoverError("cutover.final_delta_missing_causal_event")
    store = LifeShadowStore.open(overlay, create=False, now_ms=now_ms)
    try:
        _sync_legacy_memory_overlay(reader, store, now_ms=now_ms)
        if _overlay_identity(store) != overlay_identity:
            raise LifeCutoverError("cutover.overlay_identity_changed")
    finally:
        store.close()
    manifest = _build_manifest(
        reader,
        overlay_file=previous.overlay_file,
        overlay_identity_sha256=overlay_identity,
        base_tree_sha256=previous.base_tree_sha256,
        previous_manifest_sha256=previous.manifest_sha256,
        delta_from_sequence=previous.event_sequence,
        imported_at_ms=now_ms,
    )
    _atomic_write(stage / "cow_final.json", _canonical_bytes(manifest.to_dict()))
    return manifest


@dataclass(frozen=True, slots=True)
class LifeDrainEvidence:
    schema: str
    scheduler_pending: int
    inflight_requests: int
    old_writer_stopped: bool
    final_manifest_sha256: str
    observed_at_ms: int
    evidence_sha256: str

    def unsigned_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("evidence_sha256")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_sha256": self.evidence_sha256,
            "final_manifest_sha256": self.final_manifest_sha256,
            "inflight_requests": self.inflight_requests,
            "observed_at_ms": self.observed_at_ms,
            "old_writer_stopped": self.old_writer_stopped,
            "scheduler_pending": self.scheduler_pending,
            "schema": self.schema,
        }

    def validate(self) -> None:
        if (
            self.schema != DRAIN_EVIDENCE_SCHEMA
            or isinstance(self.scheduler_pending, bool)
            or not isinstance(self.scheduler_pending, int)
            or isinstance(self.inflight_requests, bool)
            or not isinstance(self.inflight_requests, int)
            or isinstance(self.observed_at_ms, bool)
            or not isinstance(self.observed_at_ms, int)
            or self.scheduler_pending != 0
            or self.inflight_requests != 0
            or self.old_writer_stopped is not True
            or not isinstance(self.final_manifest_sha256, str)
            or not _SHA256.fullmatch(self.final_manifest_sha256)
            or self.observed_at_ms < 0
            or not isinstance(self.evidence_sha256, str)
            or self.evidence_sha256 != _sha256(self.unsigned_dict())
        ):
            raise LifeCutoverError("cutover.drain_evidence_invalid")


def create_drain_evidence(
    *,
    scheduler_pending: int,
    inflight_requests: int,
    old_writer_stopped: bool,
    final_manifest_sha256: str,
    observed_at_ms: int,
) -> LifeDrainEvidence:
    if any(isinstance(value, bool) for value in (scheduler_pending, inflight_requests, observed_at_ms)):
        raise LifeCutoverError("cutover.drain_evidence_invalid")
    evidence = LifeDrainEvidence(
        schema=DRAIN_EVIDENCE_SCHEMA,
        scheduler_pending=scheduler_pending,
        inflight_requests=inflight_requests,
        old_writer_stopped=old_writer_stopped,
        final_manifest_sha256=final_manifest_sha256,
        observed_at_ms=observed_at_ms,
        evidence_sha256=ZERO_SHA256,
    )
    evidence = replace(evidence, evidence_sha256=_sha256(evidence.unsigned_dict()))
    evidence.validate()
    return evidence


@dataclass(frozen=True, slots=True)
class LifeHandoffPermit:
    schema: str
    life_id: str
    owner: str
    port: int
    writer_epoch: int
    final_manifest_sha256: str
    overlay_identity_sha256: str
    drain_evidence_sha256: str
    previous_permit_sha256: str | None
    compatibility_replay_sha256: str
    compatibility_event_count: int
    issued_at_ms: int
    expires_at_ms: int
    permit_sha256: str
    signature: str

    def unsigned_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("permit_sha256")
        value.pop("signature")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LifeHandoffPermit":
        if set(value) != set(cls.__dataclass_fields__):
            raise LifeCutoverError("cutover.handoff_shape_invalid")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise LifeCutoverError("cutover.handoff_invalid") from exc


class LifeCutoverAuthority:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key

    @classmethod
    def generate(cls) -> "LifeCutoverAuthority":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "LifeCutoverAuthority":
        try:
            return cls(Ed25519PrivateKey.from_private_bytes(raw))
        except (TypeError, ValueError) as exc:
            raise LifeCutoverError("cutover.private_key_invalid") from exc

    def private_bytes(self) -> bytes:
        return self._private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )

    def public_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def sign(self, permit: LifeHandoffPermit) -> LifeHandoffPermit:
        digest = _sha256(permit.unsigned_dict())
        signature = base64.b64encode(self._private_key.sign(digest.encode("ascii"))).decode("ascii")
        return replace(permit, permit_sha256=digest, signature=signature)


def verify_handoff_permit(
    permit: LifeHandoffPermit,
    public_key_bytes: bytes,
    *,
    now_ms: int | None = None,
) -> None:
    if now_ms is not None and (
        isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0
    ):
        raise LifeCutoverError("cutover.handoff_time_invalid")
    integers = (
        permit.port,
        permit.writer_epoch,
        permit.compatibility_event_count,
        permit.issued_at_ms,
        permit.expires_at_ms,
    )
    digests = (
        permit.final_manifest_sha256,
        permit.overlay_identity_sha256,
        permit.drain_evidence_sha256,
        permit.compatibility_replay_sha256,
        permit.permit_sha256,
    )
    if (
        permit.schema != HANDOFF_PERMIT_SCHEMA
        or not isinstance(permit.life_id, str)
        or not permit.life_id
        or not isinstance(permit.owner, str)
        or permit.owner not in {"source_life_service", "legacy_compatibility_replay"}
        or any(isinstance(value, bool) or not isinstance(value, int) for value in integers)
        or permit.port != PRODUCTION_PORT
        or permit.writer_epoch < 2
        or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in digests)
        or permit.compatibility_event_count < 0
        or permit.issued_at_ms < 0
        or permit.expires_at_ms <= permit.issued_at_ms
        or permit.permit_sha256 != _sha256(permit.unsigned_dict())
        or (
            permit.previous_permit_sha256 is not None
            and (
                not isinstance(permit.previous_permit_sha256, str)
                or not _SHA256.fullmatch(permit.previous_permit_sha256)
            )
        )
        or not isinstance(permit.signature, str)
    ):
        raise LifeCutoverError("cutover.handoff_invalid")
    if now_ms is not None and not permit.issued_at_ms <= now_ms < permit.expires_at_ms:
        raise LifeCutoverError("cutover.handoff_expired")
    try:
        signature = base64.b64decode(permit.signature, validate=True)
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature, permit.permit_sha256.encode("ascii")
        )
    except Exception as exc:
        raise LifeCutoverError("cutover.handoff_signature_invalid") from exc


def activate_handoff(
    manifest: LifeCowImportManifest,
    drain: LifeDrainEvidence,
    authority: LifeCutoverAuthority,
    *,
    issued_at_ms: int,
    expires_at_ms: int,
) -> LifeHandoffPermit:
    manifest.validate()
    drain.validate()
    if (
        drain.final_manifest_sha256 != manifest.manifest_sha256
        or isinstance(issued_at_ms, bool)
        or not isinstance(issued_at_ms, int)
        or issued_at_ms < max(manifest.imported_at_ms, drain.observed_at_ms)
        or isinstance(expires_at_ms, bool)
        or not isinstance(expires_at_ms, int)
        or expires_at_ms <= issued_at_ms
    ):
        raise LifeCutoverError("cutover.drain_manifest_mismatch")
    permit = LifeHandoffPermit(
        schema=HANDOFF_PERMIT_SCHEMA,
        life_id=manifest.life_id,
        owner="source_life_service",
        port=PRODUCTION_PORT,
        writer_epoch=manifest.writer_epoch + 1,
        final_manifest_sha256=manifest.manifest_sha256,
        overlay_identity_sha256=manifest.overlay_identity_sha256,
        drain_evidence_sha256=drain.evidence_sha256,
        previous_permit_sha256=None,
        compatibility_replay_sha256=ZERO_SHA256,
        compatibility_event_count=0,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        permit_sha256=ZERO_SHA256,
        signature="",
    )
    signed = authority.sign(permit)
    verify_handoff_permit(signed, authority.public_bytes(), now_ms=issued_at_ms)
    return signed


def build_rollback_permit(
    active: LifeHandoffPermit,
    authority: LifeCutoverAuthority,
    *,
    new_writer_stopped: bool,
    post_cutover_event_hashes: Iterable[str],
    compatible_replay_event_hashes: Iterable[str],
    issued_at_ms: int,
    expires_at_ms: int,
) -> LifeHandoffPermit:
    verify_handoff_permit(active, authority.public_bytes())
    source = tuple(post_cutover_event_hashes)
    replay = tuple(compatible_replay_event_hashes)
    if (
        new_writer_stopped is not True
        or source != replay
        or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in source)
        or isinstance(issued_at_ms, bool)
        or not isinstance(issued_at_ms, int)
        or issued_at_ms <= active.issued_at_ms
        or isinstance(expires_at_ms, bool)
        or not isinstance(expires_at_ms, int)
        or expires_at_ms <= issued_at_ms
    ):
        raise LifeCutoverError("cutover.rollback_replay_incomplete")
    replay_sha = _sha256(
        {"domain": "tiangong.life.compatibility-replay.v1", "event_hashes": source}
    )
    permit = LifeHandoffPermit(
        schema=HANDOFF_PERMIT_SCHEMA,
        life_id=active.life_id,
        owner="legacy_compatibility_replay",
        port=PRODUCTION_PORT,
        writer_epoch=active.writer_epoch + 1,
        final_manifest_sha256=active.final_manifest_sha256,
        overlay_identity_sha256=active.overlay_identity_sha256,
        drain_evidence_sha256=active.drain_evidence_sha256,
        previous_permit_sha256=active.permit_sha256,
        compatibility_replay_sha256=replay_sha,
        compatibility_event_count=len(source),
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        permit_sha256=ZERO_SHA256,
        signature="",
    )
    signed = authority.sign(permit)
    verify_handoff_permit(signed, authority.public_bytes(), now_ms=issued_at_ms)
    return signed


def renew_handoff_permit(
    active: LifeHandoffPermit,
    authority: LifeCutoverAuthority,
    *,
    issued_at_ms: int,
    expires_at_ms: int,
) -> LifeHandoffPermit:
    """Renew one active writer lease without changing owner or epoch."""

    verify_handoff_permit(active, authority.public_bytes())
    if (
        active.owner != "source_life_service"
        or isinstance(issued_at_ms, bool)
        or not isinstance(issued_at_ms, int)
        or issued_at_ms < active.issued_at_ms
        or issued_at_ms >= active.expires_at_ms
        or isinstance(expires_at_ms, bool)
        or not isinstance(expires_at_ms, int)
        or expires_at_ms <= issued_at_ms
    ):
        raise LifeCutoverError("cutover.handoff_renewal_invalid")
    renewed = LifeHandoffPermit(
        schema=active.schema,
        life_id=active.life_id,
        owner=active.owner,
        port=active.port,
        writer_epoch=active.writer_epoch,
        final_manifest_sha256=active.final_manifest_sha256,
        overlay_identity_sha256=active.overlay_identity_sha256,
        drain_evidence_sha256=active.drain_evidence_sha256,
        previous_permit_sha256=active.permit_sha256,
        compatibility_replay_sha256=active.compatibility_replay_sha256,
        compatibility_event_count=active.compatibility_event_count,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        permit_sha256=ZERO_SHA256,
        signature="",
    )
    signed = authority.sign(renewed)
    verify_handoff_permit(signed, authority.public_bytes(), now_ms=issued_at_ms)
    return signed


def write_handoff_artifacts(
    stage_root: Path,
    permit: LifeHandoffPermit,
    authority: LifeCutoverAuthority,
) -> tuple[Path, Path]:
    stage = stage_root.resolve(strict=True)
    permit_path = stage / "writer_handoff.json"
    key_path = stage / "cutover_authority.pub"
    _atomic_write(permit_path, _canonical_bytes(permit.to_dict()))
    _atomic_write(key_path, authority.public_bytes())
    return permit_path, key_path


def load_and_verify_handoff(
    permit_path: Path,
    public_key_path: Path,
    *,
    now_ms: int | None = None,
) -> LifeHandoffPermit:
    permit = load_handoff_permit(permit_path)
    public_key = public_key_path.resolve(strict=True).read_bytes()
    verify_handoff_permit(permit, public_key, now_ms=now_ms)
    return permit


def load_handoff_permit(permit_path: Path) -> LifeHandoffPermit:
    return LifeHandoffPermit.from_dict(_load_object(permit_path))


def build_cutover_comparison(
    reader: LegacySnapshotReader,
    manifest: LifeCowImportManifest,
    overlay_path: Path,
) -> dict[str, Any]:
    """Compare every user-visible authority domain before writer activation."""

    manifest.validate()
    anchor = reader.anchor()
    projection = reader.projection()
    latest = reader.latest_context()
    memory_hashes = [
        {"memory_id": memory_id, "content_sha256": _sha256(reader.decrypt_memory_content(memory_id))}
        for memory_id in reader.memory_ids()
    ]
    store = LifeShadowStore.open(overlay_path, create=False, now_ms=manifest.imported_at_ms)
    try:
        overlay_identity = _overlay_identity(store)
        overlay_health = store.health()
        overlay_memories = store.list_latest_memory_assertions(
            manifest.life_id, recallable_only=False
        )
        by_id = {item.memory_id: item for item in overlay_memories}
        observed_overlay_memory: list[dict[str, str]] = []
        for legacy_id in reader.memory_ids():
            migrated_id = _migrated_memory_id(manifest.life_id, legacy_id)
            assertion = by_id.get(migrated_id)
            if assertion is None or assertion.protected_payload_id is None:
                observed_overlay_memory.append(
                    {
                        "legacy_memory_id": legacy_id,
                        "migrated_memory_id": migrated_id,
                        "content_sha256": ZERO_SHA256,
                    }
                )
                continue
            observed_overlay_memory.append(
                {
                    "legacy_memory_id": legacy_id,
                    "migrated_memory_id": migrated_id,
                    "content_sha256": hashlib.sha256(
                        store.read_protected_payload(assertion.protected_payload_id)
                    ).hexdigest(),
                }
            )
        overlay_memory_sha256 = _sha256(observed_overlay_memory)
    finally:
        store.close()
    observed = {
        "anchor": anchor.sha256,
        "projection": _sha256(projection),
        "affect": _sha256(projection.get("affect", {})),
        "recall": _sha256(memory_hashes),
        "context": (
            _sha256({"envelope": latest["envelope"], "meta": latest["meta"]})
            if latest.get("available") is True
            else ZERO_SHA256
        ),
        "decision": _sha256(
            {
                "capabilities": projection.get("capabilities", {}),
                "free_will": projection.get("free_will", {}),
                "scheduler": projection.get("scheduler", {}),
            }
        ),
        "overlay": overlay_identity,
        "overlay_memory": overlay_memory_sha256,
        "overlay_memory_count": len(overlay_memories),
    }
    expected = {
        "anchor": anchor.sha256,
        "projection": manifest.projection_sha256,
        "affect": manifest.affect_sha256,
        "recall": manifest.memory_content_sha256,
        "context": manifest.context_content_sha256,
        "decision": _sha256(
            {
                "capabilities": projection.get("capabilities", {}),
                "free_will": projection.get("free_will", {}),
                "scheduler": projection.get("scheduler", {}),
            }
        ),
        "overlay": manifest.overlay_identity_sha256,
        "overlay_memory": _sha256(
            [
                {
                    "legacy_memory_id": legacy_id,
                    "migrated_memory_id": _migrated_memory_id(manifest.life_id, legacy_id),
                    "content_sha256": _sha256(reader.redacted_memory_content(legacy_id)),
                }
                for legacy_id in reader.memory_ids()
            ]
        ),
        "overlay_memory_count": manifest.memory_total,
    }
    differences = [
        {"domain": domain, "expected": expected[domain], "observed": observed[domain]}
        for domain in expected
        if expected[domain] != observed[domain]
    ]
    return {
        "schema": CUTOVER_COMPARISON_SCHEMA,
        "compatible": not differences,
        "domains": {domain: {"compatible": expected[domain] == observed[domain]} for domain in expected},
        "differences": differences,
        "performance": {"method": "bounded-local-validation", "network_calls": 0},
        "overlay_health": overlay_health,
        "overlay_memory_count": len(overlay_memories),
    }


def install_cutover_state_bundle(
    stage_root: Path,
    install_root: Path,
    *,
    release_id: str,
    mode: str,
    writer_stopped: bool = False,
) -> dict[str, Any]:
    """Install cutover state with an atomic pointer; never delete prior releases."""

    if not _SAFE_ID.fullmatch(release_id) or mode not in {"fresh", "overwrite", "upgrade", "recovery"}:
        raise LifeCutoverError("cutover.bundle_request_invalid")
    stage = stage_root.resolve(strict=True)
    final_manifest = load_cow_manifest(stage / "cow_final.json")
    required = {
        "cow_final.json": hashlib.sha256((stage / "cow_final.json").read_bytes()).hexdigest(),
        "writer_handoff.json": hashlib.sha256((stage / "writer_handoff.json").read_bytes()).hexdigest(),
        "cutover_authority.pub": hashlib.sha256((stage / "cutover_authority.pub").read_bytes()).hexdigest(),
    }
    root = install_root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    releases = root / "releases"
    releases.mkdir(exist_ok=True)
    base_root = root / "base"
    base_root.mkdir(exist_ok=True)
    base_snapshot = base_root / final_manifest.source_tree_sha256
    source_snapshot = Path(final_manifest.source_snapshot).resolve(strict=True)
    if _path_overlap(source_snapshot, root):
        raise LifeCutoverError("cutover.bundle_install_overlaps_snapshot")
    if snapshot_tree_sha256(source_snapshot) != final_manifest.source_tree_sha256:
        raise LifeCutoverError("cutover.bundle_source_snapshot_changed")
    if not base_snapshot.exists():
        incoming_base = base_root / f".{final_manifest.source_tree_sha256}.incoming"
        if incoming_base.exists():
            raise LifeCutoverError("cutover.bundle_base_incoming_exists")
        try:
            shutil.copytree(source_snapshot, incoming_base)
            copied_reader = LegacySnapshotReader(incoming_base)
            if snapshot_tree_sha256(copied_reader.root) != final_manifest.source_tree_sha256:
                raise LifeCutoverError("cutover.bundle_base_copy_mismatch")
            incoming_base.rename(base_snapshot)
        except Exception:
            shutil.rmtree(incoming_base, ignore_errors=True)
            raise
    elif snapshot_tree_sha256(base_snapshot) != final_manifest.source_tree_sha256:
        raise LifeCutoverError("cutover.bundle_base_copy_mismatch")
    base_relative = base_snapshot.relative_to(root).as_posix()
    target = releases / release_id
    incoming = releases / f".{release_id}.incoming"
    active_path = root / "active.json"
    old_active_bytes = active_path.read_bytes() if active_path.is_file() else None
    previous: str | None = None
    overlay_source = stage / final_manifest.overlay_file
    stage_permit = load_handoff_permit(stage / "writer_handoff.json")
    stage_public_key = (stage / "cutover_authority.pub").read_bytes()
    verify_handoff_permit(stage_permit, stage_public_key)
    if (
        stage_permit.life_id != final_manifest.life_id
        or stage_permit.final_manifest_sha256 != final_manifest.manifest_sha256
        or stage_permit.overlay_identity_sha256
        != final_manifest.overlay_identity_sha256
    ):
        raise LifeCutoverError("cutover.bundle_stage_handoff_invalid")
    trust_path = root / "trust.json"
    expected_trust = _trust_document(
        life_id=final_manifest.life_id,
        public_key_sha256=hashlib.sha256(stage_public_key).hexdigest(),
        initial_manifest_sha256=final_manifest.manifest_sha256,
    )
    if trust_path.exists():
        trust = _load_trust(trust_path)
        if (
            trust["life_id"] != expected_trust["life_id"]
            or trust["public_key_sha256"]
            != expected_trust["public_key_sha256"]
        ):
            raise LifeCutoverError("cutover.root_trust_changed")
    elif active_path.exists():
        raise LifeCutoverError("cutover.root_trust_missing")
    else:
        _atomic_write(trust_path, _canonical_bytes(expected_trust))
    if active_path.exists():
        active = _load_object(active_path)
        previous = str(active.get("release_id") or "") or None
        if mode == "fresh":
            raise LifeCutoverError("cutover.bundle_already_installed")
        if writer_stopped is not True:
            raise LifeCutoverError("cutover.bundle_writer_not_stopped")
        if previous is None or not _SAFE_ID.fullmatch(previous):
            raise LifeCutoverError("cutover.active_pointer_invalid")
        active_release = releases / previous
        active_verified = _verify_release_path(
            active_release,
            expected_bundle_sha256=str(active.get("bundle_sha256") or ""),
        )
        active_manifest = active_verified["manifest"]
        active_permit = load_handoff_permit(active_release / "writer_handoff.json")
        if (
            (active_release / "cutover_authority.pub").read_bytes()
            != stage_public_key
            or
            active_manifest.life_id != final_manifest.life_id
            or active_manifest.overlay_identity_sha256
            != final_manifest.overlay_identity_sha256
            or stage_permit.writer_epoch < active_permit.writer_epoch
            or (
                stage_permit.writer_epoch == active_permit.writer_epoch
                and stage_permit.permit_sha256 != active_permit.permit_sha256
                and stage_permit.previous_permit_sha256
                != active_permit.permit_sha256
            )
        ):
            raise LifeCutoverError("cutover.bundle_epoch_regression")
        overlay_source = active_release / active_manifest.overlay_file
        if any(
            path.exists()
            for path in (
                Path(str(overlay_source) + "-wal"),
                Path(str(overlay_source) + "-shm"),
            )
        ):
            raise LifeCutoverError("cutover.bundle_overlay_not_drained")
    elif mode in {"overwrite", "upgrade"}:
        raise LifeCutoverError("cutover.bundle_missing_install")
    if target.exists() and mode != "overwrite":
        raise LifeCutoverError("cutover.bundle_release_exists")
    if incoming.exists():
        raise LifeCutoverError("cutover.bundle_incoming_exists")
    incoming.mkdir()
    backup: Path | None = None
    try:
        for name in required:
            shutil.copy2(stage / name, incoming / name)
        shutil.copy2(overlay_source, incoming / final_manifest.overlay_file)
        copied = {name: hashlib.sha256((incoming / name).read_bytes()).hexdigest() for name in required}
        if copied != required:
            raise LifeCutoverError("cutover.bundle_copy_mismatch")
        bundle = {
            "schema": CUTOVER_BUNDLE_SCHEMA,
            "release_id": release_id,
            "previous_release_id": previous,
            "files": copied,
            "manifest_sha256": final_manifest.manifest_sha256,
            "base_snapshot_relative_path": base_relative,
            "mutable_overlay_file": final_manifest.overlay_file,
            "mutable_overlay_identity_sha256": final_manifest.overlay_identity_sha256,
        }
        bundle["bundle_sha256"] = _sha256(bundle)
        _atomic_write(incoming / "bundle.json", _canonical_bytes(bundle))
        _verify_release_path(incoming, expected_bundle_sha256=bundle["bundle_sha256"])
        if target.exists():
            old_bundle = _load_object(target / "bundle.json")
            old_digest = str(old_bundle.get("bundle_sha256") or "")
            if not _SHA256.fullmatch(old_digest):
                raise LifeCutoverError("cutover.bundle_manifest_invalid")
            backup_id = f"{release_id}.previous-{old_digest[:12]}"
            backup = releases / backup_id
            if backup.exists():
                raise LifeCutoverError("cutover.bundle_backup_exists")
            target.rename(backup)
            if previous == release_id:
                previous = backup_id
                bundle["previous_release_id"] = previous
                bundle.pop("bundle_sha256")
                bundle["bundle_sha256"] = _sha256(bundle)
                _atomic_write(incoming / "bundle.json", _canonical_bytes(bundle))
                _verify_release_path(incoming, expected_bundle_sha256=bundle["bundle_sha256"])
        incoming.rename(target)
        pointer = {
            "schema": ACTIVE_RELEASE_SCHEMA,
            "release_id": release_id,
            "previous_release_id": previous,
            "bundle_sha256": bundle["bundle_sha256"],
        }
        _atomic_write(active_path, _canonical_bytes(pointer))
        verify_cutover_state_bundle(root)
        return pointer
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        if backup is not None and backup.exists():
            shutil.rmtree(target, ignore_errors=True)
            backup.rename(target)
        if old_active_bytes is not None:
            _atomic_write(active_path, old_active_bytes)
        elif active_path.exists():
            active_path.unlink()
        raise


def _verify_release_path(
    release: Path,
    *,
    expected_bundle_sha256: str | None = None,
) -> dict[str, Any]:
    bundle = _load_object(release / "bundle.json")
    digest = str(bundle.pop("bundle_sha256", ""))
    if (
        bundle.get("schema") != CUTOVER_BUNDLE_SCHEMA
        or digest != _sha256(bundle)
        or (expected_bundle_sha256 is not None and digest != expected_bundle_sha256)
    ):
        raise LifeCutoverError("cutover.bundle_manifest_invalid")
    files = bundle.get("files")
    if not isinstance(files, Mapping):
        raise LifeCutoverError("cutover.bundle_files_invalid")
    for name, expected in files.items():
        path = release / str(name)
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise LifeCutoverError("cutover.bundle_file_mismatch")
    manifest = load_cow_manifest(release / "cow_final.json")
    base_relative = bundle.get("base_snapshot_relative_path")
    if not isinstance(base_relative, str) or not base_relative:
        raise LifeCutoverError("cutover.bundle_base_binding_invalid")
    pure_base = Path(base_relative)
    if pure_base.is_absolute() or ".." in pure_base.parts:
        raise LifeCutoverError("cutover.bundle_base_binding_invalid")
    install_root = release.parent.parent.resolve(strict=True)
    base_snapshot = (install_root / pure_base).resolve(strict=True)
    if (
        not base_snapshot.is_relative_to(install_root)
        or snapshot_tree_sha256(base_snapshot) != manifest.source_tree_sha256
    ):
        raise LifeCutoverError("cutover.bundle_base_binding_invalid")
    LegacySnapshotReader(base_snapshot).anchor()
    if (
        bundle.get("mutable_overlay_file") != manifest.overlay_file
        or bundle.get("mutable_overlay_identity_sha256")
        != manifest.overlay_identity_sha256
    ):
        raise LifeCutoverError("cutover.bundle_overlay_binding_invalid")
    if bundle.get("manifest_sha256") != manifest.manifest_sha256:
        raise LifeCutoverError("cutover.bundle_manifest_binding_invalid")
    store = LifeShadowStore.open(release / manifest.overlay_file, create=False, now_ms=manifest.imported_at_ms)
    try:
        if _overlay_identity(store) != manifest.overlay_identity_sha256:
            raise LifeCutoverError("cutover.bundle_overlay_invalid")
    finally:
        store.close()
    permit = load_handoff_permit(release / "writer_handoff.json")
    public_key = (release / "cutover_authority.pub").read_bytes()
    verify_handoff_permit(permit, public_key)
    if (
        permit.life_id != manifest.life_id
        or permit.final_manifest_sha256 != manifest.manifest_sha256
        or permit.overlay_identity_sha256 != manifest.overlay_identity_sha256
    ):
        raise LifeCutoverError("cutover.bundle_handoff_binding_invalid")
    return {"bundle_sha256": digest, "manifest": manifest}


def verify_cutover_state_bundle(install_root: Path) -> dict[str, Any]:
    root = install_root.resolve(strict=True)
    trust = _load_trust(root / "trust.json")
    active = _load_object(root / "active.json")
    if active.get("schema") != ACTIVE_RELEASE_SCHEMA or not _SAFE_ID.fullmatch(str(active.get("release_id") or "")):
        raise LifeCutoverError("cutover.active_pointer_invalid")
    release = root / "releases" / str(active["release_id"])
    if hashlib.sha256((release / "cutover_authority.pub").read_bytes()).hexdigest() != trust["public_key_sha256"]:
        raise LifeCutoverError("cutover.root_trust_mismatch")
    verified = _verify_release_path(
        release, expected_bundle_sha256=str(active.get("bundle_sha256") or "")
    )
    digest = str(verified["bundle_sha256"])
    return {"ok": True, "release_id": active["release_id"], "bundle_sha256": digest}


def rollback_cutover_state_bundle(
    install_root: Path,
    *,
    writer_stopped: bool,
    rollback_permit_path: Path,
) -> dict[str, Any]:
    if writer_stopped is not True:
        raise LifeCutoverError("cutover.bundle_writer_not_stopped")
    root = install_root.resolve(strict=True)
    active = _load_object(root / "active.json")
    current_id = str(active.get("release_id") or "")
    previous = str(active.get("previous_release_id") or "")
    if not _SAFE_ID.fullmatch(current_id) or not _SAFE_ID.fullmatch(previous):
        raise LifeCutoverError("cutover.bundle_no_rollback")
    current = root / "releases" / current_id
    target = root / "releases" / previous
    trust = _load_trust(root / "trust.json")
    current_verified = _verify_release_path(
        current, expected_bundle_sha256=str(active.get("bundle_sha256") or "")
    )
    target_verified = _verify_release_path(target)
    current_manifest = current_verified["manifest"]
    target_manifest = target_verified["manifest"]
    current_permit = load_handoff_permit(current / "writer_handoff.json")
    rollback = load_handoff_permit(rollback_permit_path)
    public_key = (target / "cutover_authority.pub").read_bytes()
    if hashlib.sha256(public_key).hexdigest() != trust["public_key_sha256"]:
        raise LifeCutoverError("cutover.root_trust_mismatch")
    verify_handoff_permit(rollback, public_key)
    if (
        rollback.owner != "legacy_compatibility_replay"
        or rollback.previous_permit_sha256 != current_permit.permit_sha256
        or rollback.writer_epoch != current_permit.writer_epoch + 1
        or rollback.life_id != current_manifest.life_id
        or rollback.final_manifest_sha256 != target_manifest.manifest_sha256
        or rollback.overlay_identity_sha256 != target_manifest.overlay_identity_sha256
    ):
        raise LifeCutoverError("cutover.bundle_rollback_permit_invalid")
    current_overlay = current / current_manifest.overlay_file
    target_overlay = target / target_manifest.overlay_file
    if any(
        path.exists()
        for path in (
            Path(str(current_overlay) + "-wal"),
            Path(str(current_overlay) + "-shm"),
        )
    ):
        raise LifeCutoverError("cutover.bundle_overlay_not_drained")
    backup_root = target / ".rollback-backup"
    if backup_root.exists():
        raise LifeCutoverError("cutover.bundle_rollback_backup_exists")
    backup_root.mkdir()
    active_bytes = (root / "active.json").read_bytes()
    protected_names = (
        target_manifest.overlay_file,
        "writer_handoff.json",
        "bundle.json",
    )
    try:
        for name in protected_names:
            shutil.copy2(target / name, backup_root / name)
        _atomic_copy(current_overlay, target_overlay)
        _atomic_copy(rollback_permit_path, target / "writer_handoff.json")
        previous_bundle = _load_object(target / "bundle.json")
        previous_bundle.pop("bundle_sha256", None)
        files = dict(previous_bundle.get("files") or {})
        files["writer_handoff.json"] = hashlib.sha256(
            (target / "writer_handoff.json").read_bytes()
        ).hexdigest()
        previous_bundle["files"] = files
        previous_bundle["previous_release_id"] = current_id
        previous_bundle["bundle_sha256"] = _sha256(previous_bundle)
        _atomic_write(target / "bundle.json", _canonical_bytes(previous_bundle))
        _verify_release_path(
            target,
            expected_bundle_sha256=str(previous_bundle["bundle_sha256"]),
        )
        pointer = {
            "schema": ACTIVE_RELEASE_SCHEMA,
            "release_id": previous,
            "previous_release_id": current_id,
            "bundle_sha256": previous_bundle["bundle_sha256"],
        }
        _atomic_write(root / "active.json", _canonical_bytes(pointer))
        verify_cutover_state_bundle(root)
        shutil.rmtree(backup_root)
        return pointer
    except Exception:
        for name in protected_names:
            backup = backup_root / name
            if backup.is_file():
                _atomic_copy(backup, target / name)
        _atomic_write(root / "active.json", active_bytes)
        shutil.rmtree(backup_root, ignore_errors=True)
        raise


def recover_cutover_state_bundle(
    install_root: Path,
    *,
    release_id: str,
    previous_release_id: str | None = None,
    expected_overlay_sha256: str,
) -> dict[str, Any]:
    """Recover only from a fully hash-verified retained release."""

    if not _SAFE_ID.fullmatch(release_id) or (
        previous_release_id is not None and not _SAFE_ID.fullmatch(previous_release_id)
    ) or not isinstance(expected_overlay_sha256, str) or not _SHA256.fullmatch(expected_overlay_sha256):
        raise LifeCutoverError("cutover.recovery_release_invalid")
    root = install_root.resolve(strict=True)
    trust = _load_trust(root / "trust.json")
    releases = root / "releases"
    verified_epochs: list[int] = []
    public_key_hashes: set[str] = set()
    for candidate in releases.iterdir():
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        _verify_release_path(candidate)
        verified_epochs.append(
            load_handoff_permit(candidate / "writer_handoff.json").writer_epoch
        )
        public_key_hashes.add(
            hashlib.sha256((candidate / "cutover_authority.pub").read_bytes()).hexdigest()
        )
    bundle_path = releases / release_id / "bundle.json"
    bundle = _load_object(bundle_path)
    digest = str(bundle.get("bundle_sha256") or "")
    manifest = load_cow_manifest(releases / release_id / "cow_final.json")
    files = bundle.get("files")
    permit = load_handoff_permit(releases / release_id / "writer_handoff.json")
    if (
        not verified_epochs
        or len(public_key_hashes) != 1
        or next(iter(public_key_hashes)) != trust["public_key_sha256"]
        or permit.writer_epoch != max(verified_epochs)
        or not isinstance(files, Mapping)
        or hashlib.sha256(
            (releases / release_id / manifest.overlay_file).read_bytes()
        ).hexdigest()
        != expected_overlay_sha256
    ):
        raise LifeCutoverError("cutover.recovery_authority_ambiguous")
    pointer = {
        "schema": ACTIVE_RELEASE_SCHEMA,
        "release_id": release_id,
        "previous_release_id": previous_release_id,
        "bundle_sha256": digest,
    }
    _atomic_write(root / "active.json", _canonical_bytes(pointer))
    verify_cutover_state_bundle(root)
    return pointer


__all__ = [
    "ACTIVE_RELEASE_SCHEMA",
    "COW_IMPORT_SCHEMA",
    "CUTOVER_BUNDLE_SCHEMA",
    "CUTOVER_COMPARISON_SCHEMA",
    "DRAIN_EVIDENCE_SCHEMA",
    "HANDOFF_PERMIT_SCHEMA",
    "LifeCowImportManifest",
    "LifeCutoverAuthority",
    "LifeCutoverError",
    "LifeDrainEvidence",
    "LifeHandoffPermit",
    "activate_handoff",
    "build_cutover_comparison",
    "build_rollback_permit",
    "capture_final_delta",
    "capture_stopped_legacy_snapshot",
    "create_drain_evidence",
    "install_cutover_state_bundle",
    "load_and_verify_handoff",
    "load_handoff_permit",
    "load_cow_manifest",
    "prepare_cow_import",
    "recover_cutover_state_bundle",
    "renew_handoff_permit",
    "rollback_cutover_state_bundle",
    "verify_cutover_state_bundle",
    "verify_handoff_permit",
    "write_handoff_artifacts",
]
