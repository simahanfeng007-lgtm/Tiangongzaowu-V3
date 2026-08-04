"""Multi-tenant content-addressed immutable object and revision store."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import CONTRACT_SCHEMA_VERSION, canonical_json_bytes, canonical_sha256


OBJECT_STORE_APPLICATION_ID = 0x54474F42
OBJECT_STORE_SCHEMA_VERSION = 1
_MIGRATION_ID = "gateway-object-store-v1"


def _content_object_id(content_sha256: str) -> str:
    return "obj_" + content_sha256


def derive_object_reference_id(
    *,
    kind: str,
    content_sha256: str,
    tenant_id: str,
    link_account_id: str,
    conversation_scope_hash: str,
) -> str:
    return "oref_" + canonical_sha256(
        {
            "domain": "tiangong.gateway.object-reference.v1",
            "kind": kind,
            "content_sha256": content_sha256,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "conversation_scope_hash": conversation_scope_hash,
        }
    )


class ObjectReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    object_id: str = Field(pattern=r"^oref_[0-9a-f]{64}$")
    content_object_id: str = Field(pattern=r"^obj_[0-9a-f]{64}$")
    kind: Literal["attachment", "artifact", "delivery_package", "payload"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1, le=2_147_483_648)
    tenant_id: str = Field(min_length=1, max_length=160)
    link_account_id: str = Field(min_length=1, max_length=160)
    conversation_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.content_object_id != _content_object_id(self.sha256):
            raise ValueError("content object ID does not match content digest")
        if self.object_id != derive_object_reference_id(
            kind=self.kind,
            content_sha256=self.sha256,
            tenant_id=self.tenant_id,
            link_account_id=self.link_account_id,
            conversation_scope_hash=self.conversation_scope_hash,
        ):
            raise ValueError("object reference ID is not scope and content bound")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"reference_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.reference_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"reference_sha256": self.computed_sha256()})


class ObjectRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    revision_id: str = Field(pattern=r"^orv_[0-9a-f]{64}$")
    logical_object_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    object_id: str = Field(pattern=r"^oref_[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at_ms: int = Field(ge=0)
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = "orv_" + canonical_sha256(
            {
                "domain": "tiangong.gateway.object-revision.v1",
                "logical_object_id": self.logical_object_id,
                "revision": self.revision,
                "object_id": self.object_id,
                "content_sha256": self.content_sha256,
                "manifest_sha256": self.manifest_sha256,
            }
        )
        if self.revision_id != expected:
            raise ValueError("object revision ID is not content and manifest bound")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"revision_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.revision_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"revision_sha256": self.computed_sha256()})


@dataclass(frozen=True)
class ObjectPutResult:
    reference: ObjectReference
    created_by_this_call: bool
    content_created_by_this_call: bool


@dataclass(frozen=True)
class ObjectStoreHealth:
    healthy: bool
    reason_code: str
    checked_at_ms: int
    schema_sha256: str | None
    writable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "reason_code": self.reason_code,
            "checked_at_ms": self.checked_at_ms,
            "schema_sha256": self.schema_sha256,
            "writable": self.writable,
        }


class ObjectStoreError(RuntimeError):
    pass


class ObjectStoreConflict(ObjectStoreError):
    pass


class ObjectStoreCorruption(ObjectStoreError):
    pass


_STATEMENTS = (
    """
    CREATE TABLE object_migrations (
        version INTEGER PRIMARY KEY,
        migration_id TEXT NOT NULL UNIQUE,
        migration_sha256 TEXT NOT NULL,
        applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE content_objects (
        content_sha256 TEXT PRIMARY KEY,
        content_object_id TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 1),
        relative_path TEXT NOT NULL UNIQUE,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE object_references (
        object_id TEXT PRIMARY KEY,
        content_sha256 TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('attachment','artifact','delivery_package','payload')),
        tenant_id TEXT NOT NULL,
        link_account_id TEXT NOT NULL,
        conversation_scope_hash TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        reference_json TEXT NOT NULL CHECK (json_valid(reference_json)),
        reference_sha256 TEXT NOT NULL,
        FOREIGN KEY (content_sha256) REFERENCES content_objects(content_sha256)
    ) STRICT
    """,
    """
    CREATE TABLE object_revisions (
        revision_id TEXT PRIMARY KEY,
        logical_object_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        object_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        revision_json TEXT NOT NULL CHECK (json_valid(revision_json)),
        revision_sha256 TEXT NOT NULL,
        UNIQUE (logical_object_id, revision),
        FOREIGN KEY (object_id) REFERENCES object_references(object_id)
    ) STRICT
    """,
    """
    CREATE INDEX object_scope_lookup
    ON object_references(tenant_id, link_account_id, conversation_scope_hash, kind)
    """,
)
_MIGRATION_SHA256 = canonical_sha256(
    {"version": OBJECT_STORE_SCHEMA_VERSION, "migration_id": _MIGRATION_ID, "statements": _STATEMENTS}
)


def _schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return canonical_sha256(
        tuple({"type": row[0], "name": row[1], "table": row[2], "sql": row[3]} for row in rows)
    )


@lru_cache(maxsize=1)
def expected_object_store_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for statement in _STATEMENTS:
            connection.execute(statement)
        return _schema_sha256(connection)
    finally:
        connection.close()


def _model_payload(value: BaseModel) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


class ContentAddressedObjectStore:
    def __init__(self, root: Path, connection: sqlite3.Connection) -> None:
        self.root = root
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, root: Path, *, now_ms: int) -> "ContentAddressedObjectStore":
        if now_ms < 0 or not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("object store root or time is invalid")
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise ObjectStoreCorruption("object store root is unsafe")
        root.mkdir(parents=True, exist_ok=True)
        managed_directories = (root / "blobs", root / "blobs" / "sha256", root / "tmp")
        for directory in managed_directories:
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise ObjectStoreCorruption("object store managed directory is unsafe")
        database = root / "object-store.sqlite3"
        if database.exists() and (database.is_symlink() or not database.is_file()):
            raise ObjectStoreCorruption("object store database path is unsafe")
        try:
            connection = sqlite3.connect(database, isolation_level=None, timeout=5, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = FULL")
            if str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() != "wal":
                raise ObjectStoreCorruption("object store could not enable WAL")
            cls._migrate(connection, now_ms)
            store = cls(root, connection)
            if not store.health_check(now_ms=now_ms, full=True).healthy:
                raise ObjectStoreCorruption("object store failed initial health check")
            os.chmod(database, 0o600)
            return store
        except (sqlite3.DatabaseError, OSError, ObjectStoreError) as exc:
            if "connection" in locals():
                connection.close()
            if isinstance(exc, ObjectStoreError):
                raise
            raise ObjectStoreCorruption("object store could not be opened safely") from exc

    @staticmethod
    def _migrate(connection: sqlite3.Connection, now_ms: int) -> None:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if application_id not in {0, OBJECT_STORE_APPLICATION_ID} or version > OBJECT_STORE_SCHEMA_VERSION:
            raise ObjectStoreCorruption("object store metadata is incompatible")
        if version == 0:
            if objects:
                raise ObjectStoreCorruption("unversioned object store database is not empty")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                for statement in _STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO object_migrations VALUES (?, ?, ?, ?)",
                    (OBJECT_STORE_SCHEMA_VERSION, _MIGRATION_ID, _MIGRATION_SHA256, now_ms),
                )
                connection.execute(f"PRAGMA application_id = {OBJECT_STORE_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {OBJECT_STORE_SCHEMA_VERSION}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        ContentAddressedObjectStore._validate_schema(connection)

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM object_migrations WHERE version = 1").fetchone()
        if (
            connection.execute("PRAGMA application_id").fetchone()[0] != OBJECT_STORE_APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != OBJECT_STORE_SCHEMA_VERSION
            or row is None
            or row["migration_id"] != _MIGRATION_ID
            or row["migration_sha256"] != _MIGRATION_SHA256
            or _schema_sha256(connection) != expected_object_store_schema_sha256()
        ):
            raise ObjectStoreCorruption("object store schema metadata is invalid")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise ObjectStoreError("object store is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def _blob_relative_path(self, digest: str) -> str:
        return f"blobs/sha256/{digest[:2]}/{digest}"

    def _blob_path(self, digest: str) -> Path:
        return self.root / self._blob_relative_path(digest)

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def put_bytes(
        self,
        data: bytes,
        *,
        kind: Literal["attachment", "artifact", "delivery_package", "payload"],
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        created_at_ms: int,
    ) -> ObjectPutResult:
        return self.put_stream(
            (data,),
            kind=kind,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            conversation_scope_hash=conversation_scope_hash,
            created_at_ms=created_at_ms,
            max_bytes=max(1, len(data)),
        )

    def put_stream(
        self,
        chunks: Iterable[bytes],
        *,
        kind: Literal["attachment", "artifact", "delivery_package", "payload"],
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        created_at_ms: int,
        max_bytes: int,
    ) -> ObjectPutResult:
        if (
            created_at_ms < 0
            or not tenant_id
            or not link_account_id
            or len(conversation_scope_hash) != 64
            or any(char not in "0123456789abcdef" for char in conversation_scope_hash)
            or not 1 <= max_bytes <= 2_147_483_648
        ):
            raise ValueError("object reference scope or limit is invalid")
        temporary = self.root / "tmp" / ("put-" + secrets.token_hex(16) + ".tmp")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as stream:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise TypeError("object stream chunks must be bytes")
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("object stream exceeds declared byte limit")
                    digest.update(chunk)
                    stream.write(chunk)
                if size == 0:
                    raise ValueError("empty content object is forbidden")
                stream.flush()
                os.fsync(stream.fileno())
            content_sha256 = digest.hexdigest()
            target = self._blob_path(content_sha256)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.parent.is_symlink() or not target.parent.is_dir():
                raise ObjectStoreCorruption("content-address bucket is unsafe")
            content_created = False
            with self._lock:
                if target.exists():
                    if target.is_symlink() or not target.is_file() or self._hash_file(target) != (content_sha256, size):
                        raise ObjectStoreCorruption("content-address collision or blob tamper detected")
                else:
                    os.replace(temporary, target)
                    content_created = True
                    if self._hash_file(target) != (content_sha256, size):
                        raise ObjectStoreCorruption("atomic content write failed readback verification")
                reference = ObjectReference(
                    object_id=derive_object_reference_id(
                        kind=kind,
                        content_sha256=content_sha256,
                        tenant_id=tenant_id,
                        link_account_id=link_account_id,
                        conversation_scope_hash=conversation_scope_hash,
                    ),
                    content_object_id=_content_object_id(content_sha256),
                    kind=kind,
                    sha256=content_sha256,
                    size_bytes=size,
                    tenant_id=tenant_id,
                    link_account_id=link_account_id,
                    conversation_scope_hash=conversation_scope_hash,
                    created_at_ms=created_at_ms,
                    reference_sha256="0" * 64,
                ).with_computed_sha256()
                reference_json, reference_digest = _model_payload(reference)
                with self._transaction():
                    content = self._connection.execute(
                        "SELECT * FROM content_objects WHERE content_sha256 = ?", (content_sha256,)
                    ).fetchone()
                    if content is None:
                        self._connection.execute(
                            "INSERT INTO content_objects VALUES (?, ?, ?, ?, ?)",
                            (
                                content_sha256,
                                reference.content_object_id,
                                size,
                                self._blob_relative_path(content_sha256),
                                created_at_ms,
                            ),
                        )
                    elif content["size_bytes"] != size or content["relative_path"] != self._blob_relative_path(content_sha256):
                        raise ObjectStoreCorruption("content index disagrees with immutable blob")
                    row = self._connection.execute(
                        "SELECT * FROM object_references WHERE object_id = ?", (reference.object_id,)
                    ).fetchone()
                    if row is not None:
                        stored = self._parse_reference(row)
                        if stored.model_dump(exclude={"created_at_ms", "reference_sha256"}) != reference.model_dump(
                            exclude={"created_at_ms", "reference_sha256"}
                        ):
                            raise ObjectStoreConflict("object reference identity was reused")
                        return ObjectPutResult(stored, False, content_created)
                    self._connection.execute(
                        """
                        INSERT INTO object_references(
                            object_id, content_sha256, kind, tenant_id, link_account_id,
                            conversation_scope_hash, created_at_ms, reference_json, reference_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            reference.object_id, reference.sha256, reference.kind, reference.tenant_id,
                            reference.link_account_id, reference.conversation_scope_hash,
                            reference.created_at_ms, reference_json, reference_digest,
                        ),
                    )
                return ObjectPutResult(reference, True, content_created)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _parse_reference(row: sqlite3.Row) -> ObjectReference:
        try:
            reference = ObjectReference.model_validate_json(row["reference_json"], strict=True)
        except ValueError as exc:
            raise ObjectStoreCorruption("stored object reference is invalid") from exc
        payload, digest = _model_payload(reference)
        if payload != row["reference_json"] or digest != row["reference_sha256"] or not reference.has_valid_sha256():
            raise ObjectStoreCorruption("stored object reference digest is invalid")
        return reference

    @staticmethod
    def _parse_revision(row: sqlite3.Row) -> ObjectRevision:
        try:
            revision = ObjectRevision.model_validate_json(row["revision_json"], strict=True)
        except ValueError as exc:
            raise ObjectStoreCorruption("stored object revision is invalid") from exc
        payload, digest = _model_payload(revision)
        if payload != row["revision_json"] or digest != row["revision_sha256"] or not revision.has_valid_sha256():
            raise ObjectStoreCorruption("stored object revision digest is invalid")
        return revision

    def register_revision(
        self,
        logical_object_id: str,
        revision: int,
        object_id: str,
        *,
        manifest_sha256: str,
        created_at_ms: int,
    ) -> tuple[ObjectRevision, bool]:
        with self._lock, self._transaction():
            reference_row = self._connection.execute(
                "SELECT * FROM object_references WHERE object_id = ?", (object_id,)
            ).fetchone()
            if reference_row is None:
                raise ObjectStoreConflict("revision references an unknown content object")
            reference = self._parse_reference(reference_row)
            revision_id = "orv_" + canonical_sha256(
                {
                    "domain": "tiangong.gateway.object-revision.v1",
                    "logical_object_id": logical_object_id,
                    "revision": revision,
                    "object_id": object_id,
                    "content_sha256": reference.sha256,
                    "manifest_sha256": manifest_sha256,
                }
            )
            candidate = ObjectRevision(
                revision_id=revision_id,
                logical_object_id=logical_object_id,
                revision=revision,
                object_id=object_id,
                content_sha256=reference.sha256,
                manifest_sha256=manifest_sha256,
                created_at_ms=created_at_ms,
                revision_sha256="0" * 64,
            ).with_computed_sha256()
            existing = self._connection.execute(
                "SELECT * FROM object_revisions WHERE logical_object_id = ? AND revision = ?",
                (logical_object_id, revision),
            ).fetchone()
            if existing is not None:
                stored = self._parse_revision(existing)
                if stored != candidate:
                    raise ObjectStoreConflict("logical revision cannot change content or manifest")
                return stored, False
            current = self._connection.execute(
                "SELECT coalesce(max(revision), 0) FROM object_revisions WHERE logical_object_id = ?",
                (logical_object_id,),
            ).fetchone()[0]
            if revision != current + 1:
                raise ObjectStoreConflict("logical object revisions must be contiguous")
            payload, digest = _model_payload(candidate)
            self._connection.execute(
                "INSERT INTO object_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.revision_id, candidate.logical_object_id, candidate.revision,
                    candidate.object_id, candidate.content_sha256, candidate.manifest_sha256,
                    candidate.created_at_ms, payload, digest,
                ),
            )
            return candidate, True

    def read_bytes(self, object_id: str) -> bytes:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT r.*, c.relative_path, c.size_bytes AS indexed_size
                FROM object_references r JOIN content_objects c ON c.content_sha256 = r.content_sha256
                WHERE r.object_id = ?
                """,
                (object_id,),
            ).fetchone()
            if row is None:
                raise ObjectStoreConflict("object reference does not exist")
            reference = self._parse_reference(row)
            path = self.root / row["relative_path"]
            if (
                path.is_symlink()
                or path.parent.is_symlink()
                or not path.is_file()
                or self.root.resolve(strict=True) not in path.resolve(strict=True).parents
            ):
                raise ObjectStoreCorruption("content blob is missing or unsafe")
            data = path.read_bytes()
            if len(data) != reference.size_bytes or hashlib.sha256(data).hexdigest() != reference.sha256:
                raise ObjectStoreCorruption("content blob failed read verification")
            return data

    def get_reference(self, object_id: str) -> ObjectReference | None:
        with self._lock:
            if self._closed:
                raise ObjectStoreError("object store is closed")
            row = self._connection.execute(
                "SELECT * FROM object_references WHERE object_id = ?", (object_id,)
            ).fetchone()
            return None if row is None else self._parse_reference(row)

    def list_references(self) -> tuple[ObjectReference, ...]:
        """Return verified immutable references for read-only retention analysis."""

        with self._lock:
            if self._closed:
                raise ObjectStoreError("object store is closed")
            rows = self._connection.execute(
                "SELECT * FROM object_references ORDER BY object_id"
            ).fetchall()
            return tuple(self._parse_reference(row) for row in rows)

    def list_revisions(self) -> tuple[ObjectRevision, ...]:
        """Return verified logical revisions; every revision is an active reference."""

        with self._lock:
            if self._closed:
                raise ObjectStoreError("object store is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM object_revisions
                ORDER BY logical_object_id, revision
                """
            ).fetchall()
            return tuple(self._parse_revision(row) for row in rows)

    def _verify_rows(self, *, full: bool) -> None:
        references = {}
        for row in self._connection.execute("SELECT * FROM object_references").fetchall():
            reference = self._parse_reference(row)
            expected = {
                "object_id": reference.object_id,
                "content_sha256": reference.sha256,
                "kind": reference.kind,
                "tenant_id": reference.tenant_id,
                "link_account_id": reference.link_account_id,
                "conversation_scope_hash": reference.conversation_scope_hash,
                "created_at_ms": reference.created_at_ms,
            }
            if any(row[name] != value for name, value in expected.items()):
                raise ObjectStoreCorruption("object reference columns disagree with canonical payload")
            references[reference.object_id] = reference
        for row in self._connection.execute("SELECT * FROM content_objects").fetchall():
            if row["content_object_id"] != _content_object_id(row["content_sha256"]):
                raise ObjectStoreCorruption("content object identity is invalid")
            expected_relative = self._blob_relative_path(row["content_sha256"])
            if row["relative_path"] != expected_relative:
                raise ObjectStoreCorruption("content object path is not canonical")
            path = self.root / expected_relative
            if (
                path.is_symlink()
                or path.parent.is_symlink()
                or not path.is_file()
                or self.root.resolve(strict=True) not in path.resolve(strict=True).parents
            ):
                raise ObjectStoreCorruption("indexed content blob is missing or unsafe")
            if full and self._hash_file(path) != (row["content_sha256"], row["size_bytes"]):
                raise ObjectStoreCorruption("indexed content blob digest is invalid")
        grouped: dict[str, list[ObjectRevision]] = {}
        for row in self._connection.execute("SELECT * FROM object_revisions").fetchall():
            revision = self._parse_revision(row)
            reference = references.get(revision.object_id)
            if reference is None or reference.sha256 != revision.content_sha256:
                raise ObjectStoreCorruption("object revision is not bound to its content reference")
            grouped.setdefault(revision.logical_object_id, []).append(revision)
        for revisions in grouped.values():
            if sorted(item.revision for item in revisions) != list(range(1, len(revisions) + 1)):
                raise ObjectStoreCorruption("logical object revisions are not contiguous")

    def health_check(self, *, now_ms: int, full: bool = False) -> ObjectStoreHealth:
        with self._lock:
            if self._closed:
                return ObjectStoreHealth(False, "object_store.closed", now_ms, None, False)
            try:
                check = "integrity_check" if full else "quick_check"
                if [row[0] for row in self._connection.execute(f"PRAGMA {check}").fetchall()] != ["ok"]:
                    raise ObjectStoreCorruption("SQLite integrity failed")
                if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ObjectStoreCorruption("object store foreign key failed")
                self._validate_schema(self._connection)
                self._verify_rows(full=full)
                probe = self.root / "tmp" / ("health-" + secrets.token_hex(8) + ".tmp")
                try:
                    with probe.open("xb") as stream:
                        stream.write(b"object-store-health")
                        stream.flush()
                        os.fsync(stream.fileno())
                    if probe.read_bytes() != b"object-store-health":
                        raise ObjectStoreCorruption("object store write probe failed")
                finally:
                    if probe.exists():
                        probe.unlink()
                return ObjectStoreHealth(True, "object_store.ok", now_ms, _schema_sha256(self._connection), True)
            except (sqlite3.DatabaseError, OSError, ObjectStoreError):
                return ObjectStoreHealth(False, "object_store.check.failed", now_ms, None, False)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._connection.close()
                self._closed = True


__all__ = [
    "ContentAddressedObjectStore",
    "ObjectPutResult",
    "ObjectReference",
    "ObjectRevision",
    "ObjectStoreConflict",
    "ObjectStoreCorruption",
    "ObjectStoreError",
    "ObjectStoreHealth",
    "derive_object_reference_id",
    "expected_object_store_schema_sha256",
]
