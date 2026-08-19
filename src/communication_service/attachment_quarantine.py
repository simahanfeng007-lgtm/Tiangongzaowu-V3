"""Durable attachment admission quota and TTL ledger for communication ingress."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from contracts import AttachmentRef, canonical_json_bytes, canonical_sha256


ATTACHMENT_LEDGER_APPLICATION_ID = 0x54474151
ATTACHMENT_LEDGER_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE accepted_attachment (
    attachment_key TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    link_account_id TEXT NOT NULL,
    conversation_scope_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_message_ref TEXT,
    accepted_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','EXPIRED')),
    attachment_json TEXT NOT NULL,
    attachment_sha256 TEXT NOT NULL,
    UNIQUE(object_id, revision)
) STRICT;
CREATE INDEX idx_attachment_account_active
ON accepted_attachment(state,tenant_id,link_account_id,expires_at_ms);
CREATE INDEX idx_attachment_conversation_active
ON accepted_attachment(state,conversation_scope_hash,expires_at_ms);
"""


@dataclass(frozen=True)
class AttachmentQuotaPolicy:
    max_active_files: int = 2_000
    max_total_active_bytes: int = 2_147_483_648
    max_account_active_bytes: int = 1_073_741_824
    max_conversation_active_bytes: int = 536_870_912
    ttl_ms: int = 604_800_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_active_files <= 1_000_000:
            raise ValueError("attachment file quota is invalid")
        if not 1 <= self.max_conversation_active_bytes <= self.max_account_active_bytes:
            raise ValueError("attachment conversation quota is invalid")
        if not self.max_account_active_bytes <= self.max_total_active_bytes:
            raise ValueError("attachment account quota is invalid")
        if not 60_000 <= self.ttl_ms <= 2_592_000_000:
            raise ValueError("attachment TTL is invalid")


@dataclass(frozen=True)
class AttachmentLedgerHealth:
    healthy: bool
    reason_code: str
    schema_sha256: str | None


class AttachmentQuotaError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def attachment_key(reference: AttachmentRef) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.communication.attachment-quarantine.v1",
            "object_id": reference.object_id,
            "revision": reference.revision,
            "sha256": reference.sha256,
        }
    )


def _schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return canonical_sha256(tuple(tuple(row) for row in rows))


@lru_cache(maxsize=1)
def expected_attachment_ledger_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA)
        return _schema_sha256(connection)
    finally:
        connection.close()


class AttachmentQuarantineLedger:
    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, now_ms: int) -> "AttachmentQuarantineLedger":
        if now_ms < 0 or not path.is_absolute():
            raise ValueError("attachment ledger path or time is invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise AttachmentQuotaError("attachment.ledger.path_unsafe")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            if str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() != "wal":
                raise AttachmentQuotaError("attachment.ledger.wal_required")
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            objects = connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if application_id not in {0, ATTACHMENT_LEDGER_APPLICATION_ID} or version > 1:
                raise AttachmentQuotaError("attachment.ledger.incompatible")
            if version == 0:
                if objects:
                    raise AttachmentQuotaError("attachment.ledger.unversioned_nonempty")
                connection.executescript(
                    "BEGIN EXCLUSIVE;\n"
                    + _SCHEMA
                    + f"\nPRAGMA application_id={ATTACHMENT_LEDGER_APPLICATION_ID};"
                    + f"\nPRAGMA user_version={ATTACHMENT_LEDGER_SCHEMA_VERSION};\nCOMMIT;"
                )
            ledger = cls(path, connection)
            if not ledger.health_check(now_ms=now_ms, full=True).healthy:
                raise AttachmentQuotaError("attachment.ledger.initial_check_failed")
            os.chmod(path, 0o600)
            return ledger
        except Exception:
            connection.close()
            raise

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise AttachmentQuotaError("attachment.ledger.closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def admit(
        self,
        reference: AttachmentRef,
        *,
        accepted_at_ms: int,
        policy: AttachmentQuotaPolicy,
    ) -> bool:
        if accepted_at_ms < reference.created_at_ms:
            raise ValueError("attachment acceptance predates object creation")
        key = attachment_key(reference)
        payload = canonical_json_bytes(reference.model_dump(mode="json")).decode("utf-8")
        digest = canonical_sha256(reference.model_dump(mode="json"))
        expires = accepted_at_ms + policy.ttl_ms
        with self._lock, self._transaction():
            self._connection.execute(
                "UPDATE accepted_attachment SET state='EXPIRED' "
                "WHERE state='ACTIVE' AND expires_at_ms <= ?",
                (accepted_at_ms,),
            )
            existing = self._connection.execute(
                "SELECT attachment_json,attachment_sha256 FROM accepted_attachment "
                "WHERE attachment_key=? OR (object_id=? AND revision=?)",
                (key, reference.object_id, reference.revision),
            ).fetchall()
            if existing:
                if len(existing) != 1 or existing[0]["attachment_json"] != payload or existing[0]["attachment_sha256"] != digest:
                    raise AttachmentQuotaError("attachment.identity_conflict")
                return False
            count, total = self._connection.execute(
                "SELECT count(*),coalesce(sum(size_bytes),0) FROM accepted_attachment WHERE state='ACTIVE'"
            ).fetchone()
            account_total = int(
                self._connection.execute(
                    "SELECT coalesce(sum(size_bytes),0) FROM accepted_attachment "
                    "WHERE state='ACTIVE' AND tenant_id=? AND link_account_id=?",
                    (reference.tenant_id, reference.link_account_id),
                ).fetchone()[0]
            )
            conversation_total = int(
                self._connection.execute(
                    "SELECT coalesce(sum(size_bytes),0) FROM accepted_attachment "
                    "WHERE state='ACTIVE' AND conversation_scope_hash=?",
                    (reference.conversation_scope_hash,),
                ).fetchone()[0]
            )
            if int(count) + 1 > policy.max_active_files:
                raise AttachmentQuotaError("attachment.quota.file_count")
            if int(total) + reference.size_bytes > policy.max_total_active_bytes:
                raise AttachmentQuotaError("attachment.quota.total_bytes")
            if account_total + reference.size_bytes > policy.max_account_active_bytes:
                raise AttachmentQuotaError("attachment.quota.account_bytes")
            if conversation_total + reference.size_bytes > policy.max_conversation_active_bytes:
                raise AttachmentQuotaError("attachment.quota.conversation_bytes")
            self._connection.execute(
                "INSERT INTO accepted_attachment VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    reference.object_id,
                    reference.revision,
                    reference.tenant_id,
                    reference.link_account_id,
                    reference.conversation_scope_hash,
                    reference.size_bytes,
                    reference.source_message_ref,
                    accepted_at_ms,
                    expires,
                    "ACTIVE",
                    payload,
                    digest,
                ),
            )
        return True

    def is_active(self, reference: AttachmentRef, *, now_ms: int) -> bool:
        row = self._connection.execute(
            "SELECT state,expires_at_ms,attachment_sha256 FROM accepted_attachment WHERE attachment_key=?",
            (attachment_key(reference),),
        ).fetchone()
        return bool(
            row is not None
            and row["state"] == "ACTIVE"
            and int(row["expires_at_ms"]) > now_ms
            and row["attachment_sha256"] == canonical_sha256(reference.model_dump(mode="json"))
        )

    def expire(self, *, now_ms: int) -> tuple[str, ...]:
        with self._lock, self._transaction():
            rows = self._connection.execute(
                "SELECT object_id FROM accepted_attachment WHERE state='ACTIVE' AND expires_at_ms <= ? "
                "ORDER BY object_id",
                (now_ms,),
            ).fetchall()
            self._connection.execute(
                "UPDATE accepted_attachment SET state='EXPIRED' WHERE state='ACTIVE' AND expires_at_ms <= ?",
                (now_ms,),
            )
        return tuple(str(row[0]) for row in rows)

    def _verify_rows(self) -> None:
        for row in self._connection.execute("SELECT * FROM accepted_attachment"):
            reference = AttachmentRef.model_validate_json(row["attachment_json"], strict=True)
            digest = canonical_sha256(reference.model_dump(mode="json"))
            if (
                digest != row["attachment_sha256"]
                or attachment_key(reference) != row["attachment_key"]
                or reference.object_id != row["object_id"]
                or reference.revision != row["revision"]
                or reference.size_bytes != row["size_bytes"]
                or reference.tenant_id != row["tenant_id"]
                or reference.link_account_id != row["link_account_id"]
                or reference.conversation_scope_hash != row["conversation_scope_hash"]
            ):
                raise AttachmentQuotaError("attachment.ledger.row_corrupt")

    def health_check(self, *, now_ms: int, full: bool = False) -> AttachmentLedgerHealth:
        if now_ms < 0 or self._closed:
            return AttachmentLedgerHealth(False, "attachment.ledger.closed", None)
        try:
            # Hold the instance lock: the sqlite connection is shared with
            # admit/expire on other threads (same pattern as the other ledgers).
            with self._lock:
                check = "integrity_check" if full else "quick_check"
                if self._connection.execute(f"PRAGMA {check}").fetchone()[0] != "ok":
                    raise AttachmentQuotaError("attachment.ledger.sqlite_corrupt")
                if _schema_sha256(self._connection) != expected_attachment_ledger_schema_sha256():
                    raise AttachmentQuotaError("attachment.ledger.schema_mismatch")
                self._verify_rows()
                with self._transaction():
                    self._connection.execute("SELECT 1")
                return AttachmentLedgerHealth(
                    True,
                    "attachment.ledger.ok",
                    expected_attachment_ledger_schema_sha256(),
                )
        except Exception:
            return AttachmentLedgerHealth(False, "attachment.ledger.check_failed", None)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True


__all__ = [
    "AttachmentLedgerHealth",
    "AttachmentQuarantineLedger",
    "AttachmentQuotaError",
    "AttachmentQuotaPolicy",
    "attachment_key",
    "expected_attachment_ledger_schema_sha256",
]
