"""Fail-closed, read-only adapter for immutable legacy 7175 snapshots.

The adapter intentionally cannot discover the live user-data directory.  It
accepts only an explicitly captured snapshot whose manifest binds every file
by one tree digest.  This keeps P2 useful for compatibility comparison without
creating a second reader/writer authority over production state.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


LEGACY_API_CONTRACT = "tiangong.life.api.v2"
SNAPSHOT_MANIFEST_SCHEMA = "tiangong.life.legacy-snapshot.v1"
CONTEXT_STORE_SCHEMA = "tiangong.life.context-store.v1"
MEMORY_CONTENT_SCHEMA = "tiangong.life.encrypted-memory-content.v1"
REGISTRY_SCHEMAS = frozenset({"tiangong.life.registry.v2"})
IDENTITY_SCHEMAS = frozenset({"tiangong.life.identity.v2"})
SOUL_SCHEMAS = frozenset({"tiangong.life.soul.v1"})
HEAD_SCHEMAS = frozenset({"tiangong.life.semantic-head.v1", "tiangong.life.semantic-head.v2"})
EVENT_SCHEMAS = frozenset({"tiangong.life.semantic-event.v1", "tiangong.life.semantic-event.v2"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LIFE_ID = re.compile(r"org_[A-Za-z0-9_-]{8,128}")
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_JOURNAL_BYTES = 512 * 1024 * 1024
_CAPTURE_METHODS = frozenset({"stopped_process_copy", "volume_shadow_copy", "sqlite_backup"})
_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "cookie",
        "session",
    }
)


class LegacySnapshotError(RuntimeError):
    """A stable error code for fail-closed snapshot inspection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _strict_json_loads(data: str | bytes, *, code: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LegacySnapshotError(code, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise LegacySnapshotError(code, f"non-finite JSON constant: {value}")

    try:
        return json.loads(data, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except LegacySnapshotError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegacySnapshotError(code, "JSON is invalid") from exc


def _canonical_bytes(value: Any) -> bytes:
    _assert_finite_json(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LegacySnapshotError("legacy.invalid_json", "legacy value is not canonical JSON") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _assert_finite_json(value: Any) -> None:
    pending = [value]
    visited = 0
    while pending:
        visited += 1
        if visited > 500_000:
            raise LegacySnapshotError("legacy.json_too_complex", "legacy JSON exceeds validation budget")
        item = pending.pop()
        if item is None or isinstance(item, (str, bool, int)):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise LegacySnapshotError("legacy.non_finite_number", "legacy JSON contains a non-finite number")
            continue
        if isinstance(item, list):
            pending.extend(item)
            continue
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise LegacySnapshotError("legacy.non_string_key", "legacy JSON contains a non-string key")
            pending.extend(item.values())
            continue
        raise LegacySnapshotError("legacy.unsupported_json", "legacy JSON contains an unsupported value")


def _redact_memory_value(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, Mapping):
        return {
            key: (
                "[REDACTED]"
                if str(key).strip().lower().replace("-", "_") in _SECRET_FIELDS
                else _redact_memory_value(item, depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_memory_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-[REDACTED]", value)
    return value


def snapshot_tree_sha256(root: Path) -> str:
    """Hash all regular snapshot files except the self-referential manifest."""

    root = root.resolve(strict=True)
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise LegacySnapshotError("snapshot.symlink_forbidden", f"snapshot contains a symlink: {path}")
        if not path.is_file() or path.name == "life_snapshot_manifest.json":
            continue
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise LegacySnapshotError("snapshot.path_escape", f"snapshot file escapes root: {path}")
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise LegacySnapshotError(
                "snapshot.changed_during_hash", f"snapshot changed while hashing: {path}"
            )
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": digest.hexdigest(),
                "size_bytes": before.st_size,
            }
        )
    return _canonical_sha256(records)


@dataclass(frozen=True, slots=True)
class LegacyProjectionAnchor:
    life_id: str
    identity_sha256: str
    soul_sha256: str
    writer_epoch: int
    event_sequence: int
    event_hash: str
    memory_total: int
    context_hash: str
    projection_source_sequence: int
    projection_source_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_hash": self.context_hash,
            "event_hash": self.event_hash,
            "event_sequence": self.event_sequence,
            "identity_sha256": self.identity_sha256,
            "life_id": self.life_id,
            "memory_total": self.memory_total,
            "projection_source_hash": self.projection_source_hash,
            "projection_source_sequence": self.projection_source_sequence,
            "soul_sha256": self.soul_sha256,
            "writer_epoch": self.writer_epoch,
        }

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


@dataclass(frozen=True, slots=True)
class ProjectionDifference:
    field: str
    legacy_value: Any
    candidate_value: Any
    classification: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_value": self.candidate_value,
            "classification": self.classification,
            "field": self.field,
            "legacy_value": self.legacy_value,
        }


def compare_projection_anchor(
    legacy: LegacyProjectionAnchor,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare only reconstructable authority fields and name every loss."""

    expected = legacy.to_dict()
    differences: list[ProjectionDifference] = []
    for field, legacy_value in expected.items():
        if field not in candidate:
            differences.append(
                ProjectionDifference(field, legacy_value, None, "missing_in_candidate")
            )
        elif candidate[field] != legacy_value:
            differences.append(
                ProjectionDifference(field, legacy_value, candidate[field], "value_mismatch")
            )
    unknown = sorted(str(field) for field in candidate if field not in expected)
    return {
        "schema": "tiangong.life.shadow-comparison.v1",
        "compatible": not differences,
        "legacy_anchor_sha256": legacy.sha256,
        "candidate_anchor_sha256": _canonical_sha256(dict(candidate)),
        "differences": [item.to_dict() for item in differences],
        "candidate_only_fields": unknown,
        "unrecoverable_information": [
            item.field for item in differences if item.classification == "missing_in_candidate"
        ],
    }


class LegacySnapshotReader:
    """Read and verify one manifest-bound copy of legacy life state."""

    def __init__(self, snapshot_root: Path) -> None:
        root = snapshot_root.expanduser().resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise LegacySnapshotError("snapshot.root_invalid", "snapshot root must be a regular directory")
        self.root = root
        manifest_path = root / "life_snapshot_manifest.json"
        self._manifest = self._read_json_path(manifest_path)
        if self._manifest.get("schema") != SNAPSHOT_MANIFEST_SCHEMA:
            raise LegacySnapshotError("snapshot.manifest_schema", "snapshot manifest schema is unsupported")
        if self._manifest.get("source_kind") != "snapshot_copy":
            raise LegacySnapshotError(
                "snapshot.source_kind_forbidden",
                "P2 accepts only an offline snapshot_copy, never a live data root",
            )
        if self._manifest.get("immutable") is not True:
            raise LegacySnapshotError("snapshot.not_immutable", "snapshot manifest must attest immutability")
        if self._manifest.get("capture_consistency") != "atomic":
            raise LegacySnapshotError(
                "snapshot.capture_not_atomic", "snapshot capture must be atomically consistent"
            )
        if self._manifest.get("capture_method") not in _CAPTURE_METHODS:
            raise LegacySnapshotError(
                "snapshot.capture_method_invalid", "snapshot capture method is unsupported"
            )
        captured_at = str(self._manifest.get("captured_at") or "")
        try:
            parsed_capture = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LegacySnapshotError("snapshot.captured_at_invalid", "captured_at is invalid") from exc
        if not captured_at.endswith("Z") or parsed_capture.utcoffset() is None:
            raise LegacySnapshotError("snapshot.captured_at_invalid", "captured_at must be UTC")
        sidecars = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.name.endswith((".sqlite3-wal", ".sqlite3-shm"))
        ]
        if sidecars:
            raise LegacySnapshotError(
                "snapshot.sqlite_not_checkpointed",
                "snapshot contains SQLite WAL/SHM sidecars and is not checkpointed",
            )
        expected_tree = str(self._manifest.get("tree_sha256") or "")
        if not _SHA256.fullmatch(expected_tree) or snapshot_tree_sha256(root) != expected_tree:
            raise LegacySnapshotError("snapshot.tree_mismatch", "snapshot tree does not match its manifest")
        life_roots = self._manifest.get("life_roots")
        if not isinstance(life_roots, Mapping) or not life_roots:
            raise LegacySnapshotError("snapshot.life_roots_invalid", "snapshot life_roots is required")
        self._life_roots: dict[str, Path] = {}
        for life_id, relative in life_roots.items():
            if not isinstance(life_id, str) or not _LIFE_ID.fullmatch(life_id):
                raise LegacySnapshotError("snapshot.life_id_invalid", "snapshot contains an invalid life id")
            self._life_roots[life_id] = self._resolve_relative(relative)

    def _resolve_relative(self, relative: Any) -> Path:
        if not isinstance(relative, str) or not relative:
            raise LegacySnapshotError("snapshot.relative_path_invalid", "snapshot path must be relative")
        pure = PurePosixPath(relative.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            raise LegacySnapshotError("snapshot.path_escape", "snapshot path escapes the snapshot root")
        path = (self.root / Path(*pure.parts)).resolve(strict=True)
        if not path.is_relative_to(self.root) or path.is_symlink():
            raise LegacySnapshotError("snapshot.path_escape", "snapshot path escapes the snapshot root")
        return path

    def _read_bytes(self, path: Path, *, max_bytes: int) -> bytes:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or path.is_symlink() or not path.is_file():
            raise LegacySnapshotError("snapshot.file_invalid", f"snapshot file is invalid: {path.name}")
        before = path.stat()
        if before.st_size > max_bytes:
            raise LegacySnapshotError("snapshot.file_too_large", f"snapshot file is too large: {path.name}")
        payload = path.read_bytes()
        after = path.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(payload) != before.st_size
        ):
            raise LegacySnapshotError("snapshot.changed_during_read", "snapshot changed while being read")
        return payload

    def _read_json_path(self, path: Path) -> dict[str, Any]:
        value = _strict_json_loads(
            self._read_bytes(path, max_bytes=_MAX_JSON_BYTES),
            code="snapshot.json_invalid",
        )
        if not isinstance(value, dict):
            raise LegacySnapshotError("snapshot.json_not_object", f"JSON must be an object: {path.name}")
        _assert_finite_json(value)
        return value

    def registry(self) -> dict[str, Any]:
        registry = self._read_json_path(self.root / "life_registry.json")
        if registry.get("schema") not in REGISTRY_SCHEMAS:
            raise LegacySnapshotError("legacy.registry_schema", "legacy registry schema is unsupported")
        bindings = registry.get("bindings")
        if not isinstance(bindings, Mapping):
            raise LegacySnapshotError("legacy.registry_bindings", "legacy registry bindings are invalid")
        return registry

    def active_life_id(self) -> str:
        life_id = str(self.registry().get("active_id") or "")
        if not _LIFE_ID.fullmatch(life_id) or life_id not in self._life_roots:
            raise LegacySnapshotError("legacy.active_life_invalid", "legacy active life is unavailable")
        return life_id

    def life_root(self, life_id: str | None = None) -> Path:
        value = life_id or self.active_life_id()
        try:
            return self._life_roots[value]
        except KeyError as exc:
            raise LegacySnapshotError("legacy.life_not_captured", "life root is not captured") from exc

    @staticmethod
    def _public_key(identity: Mapping[str, Any]) -> Ed25519PublicKey:
        try:
            raw = base64.b64decode(str(identity.get("public_key") or ""), validate=True)
            return Ed25519PublicKey.from_public_bytes(raw)
        except Exception as exc:
            raise LegacySnapshotError("legacy.public_key_invalid", "legacy public key is invalid") from exc

    def _verify_signature(
        self,
        document: Mapping[str, Any],
        signature_path: Path,
        public: Ed25519PublicKey,
        code: str,
    ) -> None:
        try:
            signature = base64.b64decode(
                self._read_bytes(signature_path, max_bytes=1024).decode("ascii").strip(),
                validate=True,
            )
            public.verify(signature, _canonical_bytes(document))
        except Exception as exc:
            raise LegacySnapshotError(code, f"legacy signature is invalid: {signature_path.name}") from exc

    def identity(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        root = self.life_root(value)
        document = self._read_json_path(root / "identity" / "life_identity.json")
        if document.get("schema") not in IDENTITY_SCHEMAS or document.get("organism_id") != value:
            raise LegacySnapshotError("legacy.identity_invalid", "legacy identity does not match the life id")
        public = self._public_key(document)
        self._verify_signature(document, root / "identity" / "life_identity.sig", public, "legacy.identity_signature")
        return document

    def soul(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        root = self.life_root(value)
        identity = self.identity(value)
        document = self._read_json_path(root / "identity" / "soul.json")
        if document.get("schema") not in SOUL_SCHEMAS or document.get("life_id") != value:
            raise LegacySnapshotError("legacy.soul_invalid", "legacy Soul does not match the life id")
        self._verify_signature(
            document,
            root / "identity" / "soul.sig",
            self._public_key(identity),
            "legacy.soul_signature",
        )
        return document

    def writer_lease(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        lease = self._read_json_path(self.life_root(value) / "identity" / "writer_lease.json")
        epoch = lease.get("epoch")
        if (
            lease.get("schema") != "tiangong.life.writer-lease.v1"
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 1
        ):
            raise LegacySnapshotError("legacy.writer_lease_invalid", "legacy writer lease is invalid")
        return lease

    def head(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        head = self._read_json_path(
            self.life_root(value) / "journal" / "current" / "life_head.json"
        )
        if head.get("schema") not in HEAD_SCHEMAS or head.get("life_id") != value:
            raise LegacySnapshotError("legacy.head_invalid", "legacy event head is invalid")
        if head.get("writer_epoch") != self.writer_lease(value).get("epoch"):
            raise LegacySnapshotError("legacy.writer_epoch_mismatch", "head and writer lease epochs differ")
        sequence = head.get("last_sequence")
        event_hash = str(head.get("last_hash") or "")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise LegacySnapshotError("legacy.head_sequence", "legacy event sequence is invalid")
        if sequence and not _SHA256.fullmatch(event_hash):
            raise LegacySnapshotError("legacy.head_hash", "legacy event head hash is invalid")
        if not sequence and event_hash:
            raise LegacySnapshotError("legacy.head_hash", "empty journal cannot have a head hash")
        return head

    def verify_journal(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        public = self._public_key(self.identity(value))
        path = self.life_root(value) / "journal" / "current" / "life_events.jsonl"
        previous = ""
        sequence = 0
        if path.exists():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root) or path.is_symlink() or not path.is_file():
                raise LegacySnapshotError("snapshot.file_invalid", "legacy journal path is invalid")
            before = path.stat()
            if before.st_size > _MAX_JOURNAL_BYTES:
                raise LegacySnapshotError("snapshot.file_too_large", "legacy journal exceeds verification limit")
            try:
                with path.open("r", encoding="utf-8", newline="") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        try:
                            stored = _strict_json_loads(line, code="legacy.journal_json")
                        except LegacySnapshotError as exc:
                            raise LegacySnapshotError(
                                "legacy.journal_json", f"invalid event at line {line_number}"
                            ) from exc
                        if not isinstance(stored, dict):
                            raise LegacySnapshotError(
                                "legacy.journal_event", f"event is not an object at line {line_number}"
                            )
                        event = deepcopy(stored)
                        claimed_hash = str(event.pop("event_hash", ""))
                        try:
                            signature = base64.b64decode(
                                str(event.pop("signature", "")), validate=True
                            )
                        except Exception as exc:
                            raise LegacySnapshotError(
                                "legacy.journal_signature",
                                f"invalid signature at line {line_number}",
                            ) from exc
                        actual_hash = _canonical_sha256(event)
                        if (
                            event.get("schema") not in EVENT_SCHEMAS
                            or event.get("life_id") != value
                            or event.get("previous_hash") != previous
                            or event.get("sequence") != sequence + 1
                            or claimed_hash != actual_hash
                        ):
                            raise LegacySnapshotError(
                                "legacy.journal_chain", f"journal chain failed at line {line_number}"
                            )
                        try:
                            public.verify(signature, actual_hash.encode("ascii"))
                        except Exception as exc:
                            raise LegacySnapshotError(
                                "legacy.journal_signature",
                                f"signature failed at line {line_number}",
                            ) from exc
                        previous = actual_hash
                        sequence += 1
            except UnicodeDecodeError as exc:
                raise LegacySnapshotError("legacy.journal_utf8", "legacy journal is not UTF-8") from exc
            after = path.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise LegacySnapshotError(
                    "snapshot.changed_during_read", "legacy journal changed during verification"
                )
        head = self.head(value)
        if head.get("last_sequence") != sequence or str(head.get("last_hash") or "") != previous:
            raise LegacySnapshotError("legacy.journal_head_mismatch", "journal head does not match event stream")
        return {"ok": True, "life_id": value, "sequence": sequence, "last_hash": previous}

    def projection(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        projection = self._read_json_path(self.life_root(value) / "projections" / "life.json")
        if projection.get("life_id") not in (None, value):
            raise LegacySnapshotError("legacy.projection_life", "legacy projection belongs to another life")
        return projection

    def _sqlite(self, life_id: str | None = None) -> sqlite3.Connection:
        path = self.life_root(life_id) / "memory" / "memory_index.sqlite3"
        if not path.is_file() or path.is_symlink():
            raise LegacySnapshotError("legacy.memory_index_missing", "legacy memory index is unavailable")
        uri = "file:" + quote(path.resolve(strict=True).as_posix(), safe="/:?") + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"memories", "meta"}.issubset(tables):
            connection.close()
            raise LegacySnapshotError("legacy.memory_schema", "legacy memory index schema is unsupported")
        return connection

    def memory_stats(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        connection = self._sqlite(value)
        try:
            total = int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
            by_type = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type"
                )
            }
            by_status = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT status, COUNT(*) FROM memories GROUP BY status"
                )
            }
            meta = {
                str(row[0]): str(row[1])
                for row in connection.execute("SELECT key, value FROM meta")
            }
        finally:
            connection.close()
        return {
            "ok": True,
            "life_id": value,
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
            "index": meta,
        }

    def memory_ids(self, life_id: str | None = None) -> tuple[str, ...]:
        """Return the complete immutable memory identity set for COW import."""

        value = life_id or self.active_life_id()
        connection = self._sqlite(value)
        try:
            rows = connection.execute(
                "SELECT memory_id FROM memories ORDER BY memory_id"
            ).fetchall()
        finally:
            connection.close()
        result = tuple(str(row[0]) for row in rows)
        if len(result) != len(set(result)) or any(not item for item in result):
            raise LegacySnapshotError(
                "legacy.memory_identity_invalid",
                "legacy memory identities are invalid",
            )
        return result

    def memory_records(self, life_id: str | None = None) -> tuple[dict[str, Any], ...]:
        """Return bounded legacy metadata for deterministic protected migration."""

        value = life_id or self.active_life_id()
        connection = self._sqlite(value)
        try:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memories)")}
            required = {"memory_id", "memory_type", "status", "search_text"}
            if not required.issubset(columns):
                raise LegacySnapshotError(
                    "legacy.memory_schema", "legacy memory columns are unsupported"
                )
            rows = connection.execute(
                "SELECT memory_id, memory_type, status, search_text FROM memories ORDER BY memory_id"
            ).fetchall()
        finally:
            connection.close()
        records = tuple(
            {
                "memory_id": str(row[0]),
                "memory_type": str(row[1] or "legacy"),
                "status": str(row[2] or "active"),
                "search_text": str(row[3] or ""),
            }
            for row in rows
        )
        if tuple(item["memory_id"] for item in records) != self.memory_ids(value):
            raise LegacySnapshotError(
                "legacy.memory_identity_changed", "legacy memory identities changed during read"
            )
        return records

    def _memory_row(self, life_id: str, memory_id: str) -> dict[str, Any]:
        connection = self._sqlite(life_id)
        try:
            row = connection.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise LegacySnapshotError("legacy.memory_not_found", "legacy memory was not found")
        return dict(row)

    def decrypt_memory_content(
        self, memory_id: str, life_id: str | None = None
    ) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        row = self._memory_row(value, memory_id)
        try:
            descriptor = _strict_json_loads(
                str(row.get("content_json") or "{}"), code="legacy.memory_descriptor"
            )
        except LegacySnapshotError as exc:
            raise LegacySnapshotError("legacy.memory_descriptor", "memory descriptor is invalid") from exc
        if not isinstance(descriptor, dict):
            raise LegacySnapshotError("legacy.memory_descriptor", "memory descriptor must be an object")
        if descriptor.get("storage") != "encrypted_blob":
            _assert_finite_json(descriptor)
            return deepcopy(descriptor)
        blob_id = str(descriptor.get("blob_id") or "")
        if blob_id != memory_id:
            raise LegacySnapshotError("legacy.memory_blob_id", "memory blob id does not match")
        root = self.life_root(value) / "memory"
        cipher = self._read_bytes(root / "blobs" / f"{memory_id}.blob", max_bytes=_MAX_JSON_BYTES)
        key = self._read_bytes(root / "keys" / f"{memory_id}.key", max_bytes=64)
        if len(key) != 32 or len(cipher) < 28:
            raise LegacySnapshotError("legacy.memory_cipher", "memory cipher material is invalid")
        if hashlib.sha256(cipher).hexdigest() != str(descriptor.get("cipher_sha256") or ""):
            raise LegacySnapshotError("legacy.memory_cipher_hash", "memory cipher hash does not match")
        aad = f"{value}:{memory_id}:{MEMORY_CONTENT_SCHEMA}".encode("utf-8")
        try:
            plaintext = AESGCM(key).decrypt(cipher[:12], cipher[12:], aad)
        except Exception as exc:
            raise LegacySnapshotError("legacy.memory_decrypt", "memory content cannot be decrypted") from exc
        if hashlib.sha256(plaintext).hexdigest() != str(descriptor.get("content_sha256") or ""):
            raise LegacySnapshotError("legacy.memory_plaintext_hash", "memory plaintext hash does not match")
        try:
            content = _strict_json_loads(plaintext, code="legacy.memory_plaintext")
        except LegacySnapshotError as exc:
            raise LegacySnapshotError("legacy.memory_plaintext", "memory plaintext is invalid") from exc
        if not isinstance(content, dict):
            raise LegacySnapshotError("legacy.memory_plaintext", "memory plaintext must be an object")
        _assert_finite_json(content)
        return content

    def redacted_memory_content(
        self, memory_id: str, life_id: str | None = None
    ) -> dict[str, Any]:
        """Return the recall-safe projection while the immutable base stays intact."""

        content = _redact_memory_value(
            self.decrypt_memory_content(memory_id, life_id)
        )
        if not isinstance(content, dict):
            raise LegacySnapshotError(
                "legacy.memory_redaction_invalid", "memory redaction is invalid"
            )
        _assert_finite_json(content)
        return content

    def search_memory(
        self,
        query: str,
        *,
        limit: int = 20,
        memory_types: list[str] | None = None,
        include_content: bool = False,
        life_id: str | None = None,
    ) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise LegacySnapshotError("legacy.memory_limit", "memory search limit is invalid")
        connection = self._sqlite(value)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)")}
            required = {"memory_id", "memory_type", "status", "search_text", "content_json"}
            if not required.issubset(columns):
                raise LegacySnapshotError("legacy.memory_schema", "legacy memory columns are unsupported")
            sql = "SELECT * FROM memories WHERE search_text LIKE ? ESCAPE '\\'"
            escaped = str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters: list[Any] = [f"%{escaped}%"]
            if memory_types:
                normalized = sorted({str(item) for item in memory_types if str(item)})
                sql += " AND memory_type IN (" + ",".join("?" for _ in normalized) + ")"
                parameters.extend(normalized)
            sql += " ORDER BY memory_id LIMIT ?"
            parameters.append(limit)
            rows = [dict(row) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()
        results: list[dict[str, Any]] = []
        for row in rows:
            projected = {key: item for key, item in row.items() if key != "content_json"}
            if include_content:
                projected["content"] = _redact_memory_value(
                    self.decrypt_memory_content(str(row["memory_id"]), value)
                )
            results.append(projected)
        return {"ok": True, "life_id": value, "query": str(query), "results": results}

    def context(self, context_hash: str, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        normalized = str(context_hash or "").lower()
        if not _SHA256.fullmatch(normalized):
            raise LegacySnapshotError("legacy.context_hash", "context hash is invalid")
        root = self.life_root(value) / "context"
        meta = self._read_json_path(root / "envelopes" / f"{normalized}.meta.json")
        cipher = self._read_bytes(
            root / "envelopes" / f"{normalized}.ctx", max_bytes=_MAX_JSON_BYTES
        )
        key = self._read_bytes(root / "context.key", max_bytes=64)
        if len(key) != 32 or len(cipher) < 28:
            raise LegacySnapshotError("legacy.context_cipher", "context cipher material is invalid")
        if meta.get("schema") != CONTEXT_STORE_SCHEMA or meta.get("life_id") != value:
            raise LegacySnapshotError("legacy.context_meta", "context metadata is invalid")
        if hashlib.sha256(cipher).hexdigest() != str(meta.get("cipher_sha256") or ""):
            raise LegacySnapshotError("legacy.context_cipher_hash", "context cipher hash does not match")
        aad = f"{value}:{normalized}:{CONTEXT_STORE_SCHEMA}".encode("utf-8")
        try:
            plaintext = AESGCM(key).decrypt(cipher[:12], cipher[12:], aad)
            envelope = _strict_json_loads(plaintext, code="legacy.context_envelope")
        except Exception as exc:
            raise LegacySnapshotError("legacy.context_decrypt", "context cannot be decrypted") from exc
        if not isinstance(envelope, dict):
            raise LegacySnapshotError("legacy.context_envelope", "context envelope must be an object")
        _assert_finite_json(envelope)
        if envelope.get("life_id") != value or envelope.get("context_hash") != normalized:
            raise LegacySnapshotError("legacy.context_binding", "context envelope binding is invalid")
        return {"meta": meta, "envelope": envelope}

    def latest_context(self, life_id: str | None = None) -> dict[str, Any]:
        value = life_id or self.active_life_id()
        path = self.life_root(value) / "context" / "latest.json"
        if not path.is_file():
            return {"available": False, "reason_code": "NO_CONTEXT_COMPILED"}
        latest = self._read_json_path(path)
        context_hash = str(latest.get("context_hash") or "")
        result = self.context(context_hash, value)
        return {"available": True, **result}

    def active_binding(self) -> dict[str, Any]:
        registry = self.registry()
        life_id = self.active_life_id()
        binding = registry["bindings"].get(life_id)
        if not isinstance(binding, Mapping):
            raise LegacySnapshotError("legacy.binding_invalid", "legacy active binding is invalid")
        identity = self.identity(life_id)
        soul = self.soul(life_id)
        result = deepcopy(dict(binding))
        result.update(
            {
                "life_id": life_id,
                "active": True,
                "integrity": "valid",
                "soul_integrity": "valid",
                "registry_name": result.get("name") or identity.get("name") or "起源",
                "name": soul.get("name") or result.get("name") or identity.get("name") or "起源",
                "soul_revision_id": soul.get("revision_id"),
                "writer_epoch": self.writer_lease(life_id)["epoch"],
            }
        )
        return result

    def identities(self) -> list[dict[str, Any]]:
        registry = self.registry()
        active = self.active_life_id()
        result: list[dict[str, Any]] = []
        for life_id in sorted(registry["bindings"]):
            if life_id not in self._life_roots:
                continue
            binding = deepcopy(dict(registry["bindings"][life_id]))
            binding.update({"life_id": life_id, "active": life_id == active})
            try:
                self.identity(life_id)
                binding["integrity"] = "valid"
            except LegacySnapshotError as exc:
                binding.update({"integrity": "invalid", "integrity_error": exc.code})
            result.append(binding)
        return result

    def anchor(self) -> LegacyProjectionAnchor:
        life_id = self.active_life_id()
        identity = self.identity(life_id)
        soul = self.soul(life_id)
        head = self.head(life_id)
        stats = self.memory_stats(life_id)
        latest = self.latest_context(life_id)
        projection = self.projection(life_id)
        context_hash = ""
        if latest.get("available") is True:
            context_hash = str(latest["meta"].get("context_hash") or "")
        source_sequence = projection.get("source_sequence", head["last_sequence"])
        if isinstance(source_sequence, bool) or not isinstance(source_sequence, int):
            raise LegacySnapshotError("legacy.projection_sequence", "projection source sequence is invalid")
        return LegacyProjectionAnchor(
            life_id=life_id,
            identity_sha256=_canonical_sha256(identity),
            soul_sha256=_canonical_sha256(soul),
            writer_epoch=int(head["writer_epoch"]),
            event_sequence=int(head["last_sequence"]),
            event_hash=str(head.get("last_hash") or ""),
            memory_total=int(stats["total"]),
            context_hash=context_hash,
            projection_source_sequence=source_sequence,
            projection_source_hash=str(projection.get("source_hash") or head.get("last_hash") or ""),
        )


__all__ = [
    "CONTEXT_STORE_SCHEMA",
    "LEGACY_API_CONTRACT",
    "MEMORY_CONTENT_SCHEMA",
    "SNAPSHOT_MANIFEST_SCHEMA",
    "LegacyProjectionAnchor",
    "LegacySnapshotError",
    "LegacySnapshotReader",
    "ProjectionDifference",
    "compare_projection_anchor",
    "snapshot_tree_sha256",
]
