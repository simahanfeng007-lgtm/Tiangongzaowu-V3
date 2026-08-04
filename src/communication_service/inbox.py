"""Durable channel inbox: commit message and cursor before any external ACK."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    CONTRACT_SCHEMA_VERSION,
    ChannelAckPermit,
    InboundEnvelope,
    InboundScope,
    bind_inbound_scope,
    canonical_json_bytes,
    canonical_sha256,
)


INBOX_APPLICATION_ID = 0x5447494E
INBOX_SCHEMA_VERSION = 1
_MIGRATION_ID = "communication-inbox-v1"


class InboxIngress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    ingress_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$")
    envelope: InboundEnvelope
    raw_payload_object_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_payload_size_bytes: int = Field(ge=1, le=2_147_483_648)
    cursor_stream_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_cursor_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    next_cursor_token: str = Field(max_length=4_096)
    next_cursor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at_ms: int = Field(ge=0)
    ingress_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_ingress(self) -> Self:
        if self.ingress_id != self.envelope.inbound_id:
            raise ValueError("inbox ingress ID must equal envelope inbound ID")
        scope = InboundScope(
            channel=self.envelope.channel,
            tenant_id=self.envelope.tenant_id,
            link_account_id=self.envelope.link_account_id,
            conversation_ref=self.envelope.conversation_ref,
            channel_message_ref=self.envelope.channel_message_ref,
            sender_ref=self.envelope.sender_ref,
        )
        bind_inbound_scope(self.envelope, scope)
        expected_stream = derive_cursor_stream_key(
            self.envelope.channel,
            self.envelope.tenant_id,
            self.envelope.link_account_id,
        )
        if self.cursor_stream_key != expected_stream:
            raise ValueError("cursor stream is not bound to channel tenant and account")
        if self.next_cursor_sha256 != cursor_token_sha256(self.next_cursor_token):
            raise ValueError("next cursor digest does not match cursor token")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"ingress_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.ingress_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"ingress_sha256": self.computed_sha256()})


@dataclass(frozen=True)
class InboxPersistResult:
    permit: ChannelAckPermit
    persisted_by_this_call: bool
    duplicate: bool


@dataclass(frozen=True)
class PendingInboxDelivery:
    ingress: InboxIngress
    permit: ChannelAckPermit


@dataclass(frozen=True)
class CursorSnapshot:
    cursor_stream_key: str
    cursor_token: str
    cursor_sha256: str
    revision: int
    last_ingress_id: str


@dataclass(frozen=True)
class InboxHealthEvidence:
    healthy: bool
    reason_code: str
    checked_at_ms: int
    schema_sha256: str | None
    writable: bool


@dataclass(frozen=True)
class InboxDrainFacts:
    channel: str
    tenant_id: str
    link_account_id: str
    unacknowledged_count: int
    last_cursor_sha256: str | None
    ledger_sha256: str


class InboxError(RuntimeError):
    pass


class InboxMigrationError(InboxError):
    pass


class InboxCorruptionError(InboxError):
    pass


class InboxConflictError(InboxError):
    pass


class CursorConflictError(InboxConflictError):
    pass


class AckConflictError(InboxConflictError):
    pass


def derive_cursor_stream_key(channel: str, tenant_id: str, link_account_id: str) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.communication.cursor-stream.v1",
            "channel": channel,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
        }
    )


def cursor_token_sha256(token: str) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.communication.cursor-token.v1",
            "token": token,
        }
    )


_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE inbox_migrations (
        version INTEGER PRIMARY KEY CHECK (version >= 1),
        migration_id TEXT NOT NULL UNIQUE,
        migration_sha256 TEXT NOT NULL CHECK (
            length(migration_sha256) = 64
            AND migration_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE inbox_records (
        ingress_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        channel TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        link_account_id TEXT NOT NULL,
        conversation_ref TEXT NOT NULL,
        channel_message_ref TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('ACK_ELIGIBLE','ACKED')),
        persisted_at_ms INTEGER NOT NULL CHECK (persisted_at_ms >= 0),
        ingress_json TEXT NOT NULL CHECK (json_valid(ingress_json)),
        ingress_sha256 TEXT NOT NULL CHECK (
            length(ingress_sha256) = 64
            AND ingress_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        envelope_json TEXT NOT NULL CHECK (json_valid(envelope_json)),
        envelope_sha256 TEXT NOT NULL CHECK (
            length(envelope_sha256) = 64
            AND envelope_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        raw_payload_object_id TEXT NOT NULL,
        raw_payload_sha256 TEXT NOT NULL,
        raw_payload_size_bytes INTEGER NOT NULL CHECK (raw_payload_size_bytes >= 1),
        cursor_stream_key TEXT NOT NULL,
        previous_cursor_sha256 TEXT,
        next_cursor_sha256 TEXT NOT NULL,
        UNIQUE (channel, tenant_id, link_account_id, channel_message_ref)
    ) STRICT
    """,
    """
    CREATE TABLE cursor_state (
        cursor_stream_key TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        link_account_id TEXT NOT NULL,
        cursor_token TEXT NOT NULL,
        cursor_sha256 TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
        last_ingress_id TEXT NOT NULL UNIQUE,
        FOREIGN KEY (last_ingress_id) REFERENCES inbox_records(ingress_id)
    ) STRICT
    """,
    """
    CREATE TABLE ack_permits (
        permit_id TEXT PRIMARY KEY,
        ingress_id TEXT NOT NULL UNIQUE,
        cursor_stream_key TEXT NOT NULL,
        cursor_revision INTEGER NOT NULL CHECK (cursor_revision >= 1),
        next_cursor_sha256 TEXT NOT NULL,
        issued_at_ms INTEGER NOT NULL CHECK (issued_at_ms >= 0),
        permit_json TEXT NOT NULL CHECK (json_valid(permit_json)),
        permit_sha256 TEXT NOT NULL CHECK (
            length(permit_sha256) = 64
            AND permit_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
        platform_receipt_sha256 TEXT,
        acknowledged_at_ms INTEGER,
        CHECK (
            (platform_receipt_sha256 IS NULL AND acknowledged_at_ms IS NULL)
            OR (platform_receipt_sha256 IS NOT NULL AND acknowledged_at_ms IS NOT NULL)
        ),
        FOREIGN KEY (ingress_id) REFERENCES inbox_records(ingress_id)
    ) STRICT
    """,
    """
    CREATE INDEX inbox_dispatch_order
    ON inbox_records(state, persisted_at_ms, ingress_id)
    """,
)
_MIGRATION_SHA256 = canonical_sha256(
    {
        "migration_id": _MIGRATION_ID,
        "statements": _MIGRATION_STATEMENTS,
        "version": INBOX_SCHEMA_VERSION,
    }
)


def _schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return canonical_sha256(
        tuple(
            {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
            for row in rows
        )
    )


@lru_cache(maxsize=1)
def expected_inbox_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for statement in _MIGRATION_STATEMENTS:
            connection.execute(statement)
        return _schema_sha256(connection)
    finally:
        connection.close()


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA synchronous = FULL")
    if str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() != "wal":
        raise InboxMigrationError("communication inbox could not enable WAL")


def _validate_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA application_id").fetchone()[0] != INBOX_APPLICATION_ID:
        raise InboxMigrationError("communication inbox application ID is invalid")
    if connection.execute("PRAGMA user_version").fetchone()[0] != INBOX_SCHEMA_VERSION:
        raise InboxMigrationError("communication inbox schema version is unsupported")
    row = connection.execute(
        "SELECT migration_id, migration_sha256 FROM inbox_migrations WHERE version = 1"
    ).fetchone()
    if row is None or row["migration_id"] != _MIGRATION_ID or row["migration_sha256"] != _MIGRATION_SHA256:
        raise InboxMigrationError("communication inbox migration record is invalid")
    if _schema_sha256(connection) != expected_inbox_schema_sha256():
        raise InboxMigrationError("communication inbox schema fingerprint is invalid")


def _migrate(connection: sqlite3.Connection, now_ms: int) -> None:
    application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    objects = connection.execute(
        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if application_id not in {0, INBOX_APPLICATION_ID}:
        raise InboxMigrationError("database belongs to another application")
    if version > INBOX_SCHEMA_VERSION:
        raise InboxMigrationError("communication inbox is newer than this binary")
    if version == 0:
        if objects:
            raise InboxMigrationError("unversioned communication inbox is not empty")
        connection.execute("BEGIN EXCLUSIVE")
        try:
            for statement in _MIGRATION_STATEMENTS:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO inbox_migrations VALUES (?, ?, ?, ?)",
                (INBOX_SCHEMA_VERSION, _MIGRATION_ID, _MIGRATION_SHA256, now_ms),
            )
            connection.execute(f"PRAGMA application_id = {INBOX_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {INBOX_SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    _validate_schema(connection)


def _json_and_sha(value: BaseModel) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _parse_ingress(payload: str, digest: str) -> InboxIngress:
    try:
        ingress = InboxIngress.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise InboxCorruptionError("stored inbox ingress is invalid") from exc
    canonical, actual = _json_and_sha(ingress)
    if canonical != payload or actual != digest or not ingress.has_valid_sha256():
        raise InboxCorruptionError("stored inbox ingress digest is invalid")
    return ingress


def _parse_permit(payload: str, digest: str) -> ChannelAckPermit:
    try:
        permit = ChannelAckPermit.model_validate_json(payload, strict=True)
    except ValueError as exc:
        raise InboxCorruptionError("stored ACK permit is invalid") from exc
    canonical, actual = _json_and_sha(permit)
    if canonical != payload or actual != digest or not permit.has_valid_sha256():
        raise InboxCorruptionError("stored ACK permit digest is invalid")
    return permit


class CommunicationInbox:
    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, now_ms: int) -> "CommunicationInbox":
        if now_ms < 0 or not path.is_absolute():
            raise ValueError("communication inbox path or time is invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise InboxCorruptionError("communication inbox path is not a regular file")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                path,
                timeout=5,
                isolation_level=None,
                check_same_thread=False,
            )
            _configure(connection)
            _migrate(connection, now_ms)
            inbox = cls(path, connection)
            health = inbox.health_check(now_ms=now_ms, full=True)
            if not health.healthy:
                raise InboxCorruptionError(health.reason_code)
            os.chmod(path, 0o600)
            return inbox
        except (sqlite3.DatabaseError, InboxError, OSError) as exc:
            if "connection" in locals():
                connection.close()
            if isinstance(exc, InboxError):
                raise
            raise InboxCorruptionError("communication inbox could not be opened safely") from exc

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise InboxError("communication inbox is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def _permit_for_ingress(self, ingress_id: str) -> ChannelAckPermit:
        row = self._connection.execute(
            "SELECT permit_json, permit_sha256 FROM ack_permits WHERE ingress_id = ?",
            (ingress_id,),
        ).fetchone()
        if row is None:
            raise InboxCorruptionError("durable inbox record has no ACK permit")
        return _parse_permit(row["permit_json"], row["permit_sha256"])

    def persist_and_advance_cursor(
        self,
        ingress: InboxIngress,
        *,
        persisted_at_ms: int,
    ) -> InboxPersistResult:
        if not ingress.has_valid_sha256() or persisted_at_ms < ingress.captured_at_ms:
            raise ValueError("inbox ingress digest or persistence time is invalid")
        ingress_json, ingress_digest = _json_and_sha(ingress)
        envelope_json, envelope_digest = _json_and_sha(ingress.envelope)
        with self._lock, self._transaction():
            existing = self._connection.execute(
                """
                SELECT ingress_id, ingress_json, ingress_sha256
                FROM inbox_records
                WHERE ingress_id = ? OR idempotency_key = ? OR (
                    channel = ? AND tenant_id = ? AND link_account_id = ?
                    AND channel_message_ref = ?
                )
                """,
                (
                    ingress.ingress_id,
                    ingress.envelope.idempotency_key,
                    ingress.envelope.channel,
                    ingress.envelope.tenant_id,
                    ingress.envelope.link_account_id,
                    ingress.envelope.channel_message_ref,
                ),
            ).fetchall()
            if existing:
                if len(existing) != 1:
                    raise InboxCorruptionError("inbox uniqueness keys point to different records")
                row = existing[0]
                if row["ingress_json"] != ingress_json or row["ingress_sha256"] != ingress_digest:
                    raise InboxConflictError("inbound identity was reused with different content")
                return InboxPersistResult(self._permit_for_ingress(row["ingress_id"]), False, True)

            cursor = self._connection.execute(
                "SELECT cursor_sha256, revision FROM cursor_state WHERE cursor_stream_key = ?",
                (ingress.cursor_stream_key,),
            ).fetchone()
            if cursor is None:
                if ingress.previous_cursor_sha256 is not None:
                    raise CursorConflictError("cursor stream is missing its claimed previous cursor")
                cursor_revision = 1
            else:
                if ingress.previous_cursor_sha256 != cursor["cursor_sha256"]:
                    raise CursorConflictError("cursor compare-and-set failed")
                cursor_revision = int(cursor["revision"]) + 1

            permit_id = "ack_" + canonical_sha256(
                {
                    "domain": "tiangong.communication.ack-permit.v1",
                    "ingress_sha256": ingress.ingress_sha256,
                    "cursor_revision": cursor_revision,
                    "next_cursor_sha256": ingress.next_cursor_sha256,
                }
            )
            permit = ChannelAckPermit(
                permit_id=permit_id,
                ingress_id=ingress.ingress_id,
                idempotency_key=ingress.envelope.idempotency_key,
                channel=ingress.envelope.channel,
                tenant_id=ingress.envelope.tenant_id,
                link_account_id=ingress.envelope.link_account_id,
                channel_message_ref=ingress.envelope.channel_message_ref,
                cursor_stream_key=ingress.cursor_stream_key,
                cursor_revision=cursor_revision,
                next_cursor_sha256=ingress.next_cursor_sha256,
                inbox_record_sha256=ingress.ingress_sha256,
                persisted_at_ms=persisted_at_ms,
                issued_at_ms=persisted_at_ms,
                permit_sha256="0" * 64,
            ).with_computed_sha256()
            permit_json, permit_digest = _json_and_sha(permit)
            self._connection.execute(
                """
                INSERT INTO inbox_records(
                    ingress_id, idempotency_key, channel, tenant_id, link_account_id,
                    conversation_ref, channel_message_ref, state, persisted_at_ms,
                    ingress_json, ingress_sha256, envelope_json, envelope_sha256,
                    raw_payload_object_id, raw_payload_sha256, raw_payload_size_bytes,
                    cursor_stream_key, previous_cursor_sha256, next_cursor_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACK_ELIGIBLE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ingress.ingress_id,
                    ingress.envelope.idempotency_key,
                    ingress.envelope.channel,
                    ingress.envelope.tenant_id,
                    ingress.envelope.link_account_id,
                    ingress.envelope.conversation_ref,
                    ingress.envelope.channel_message_ref,
                    persisted_at_ms,
                    ingress_json,
                    ingress_digest,
                    envelope_json,
                    envelope_digest,
                    ingress.raw_payload_object_id,
                    ingress.raw_payload_sha256,
                    ingress.raw_payload_size_bytes,
                    ingress.cursor_stream_key,
                    ingress.previous_cursor_sha256,
                    ingress.next_cursor_sha256,
                ),
            )
            if cursor is None:
                self._connection.execute(
                    """
                    INSERT INTO cursor_state(
                        cursor_stream_key, channel, tenant_id, link_account_id,
                        cursor_token, cursor_sha256, revision, updated_at_ms,
                        last_ingress_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ingress.cursor_stream_key,
                        ingress.envelope.channel,
                        ingress.envelope.tenant_id,
                        ingress.envelope.link_account_id,
                        ingress.next_cursor_token,
                        ingress.next_cursor_sha256,
                        cursor_revision,
                        persisted_at_ms,
                        ingress.ingress_id,
                    ),
                )
            else:
                updated = self._connection.execute(
                    """
                    UPDATE cursor_state
                    SET cursor_token = ?, cursor_sha256 = ?, revision = ?,
                        updated_at_ms = ?, last_ingress_id = ?
                    WHERE cursor_stream_key = ? AND revision = ? AND cursor_sha256 = ?
                    """,
                    (
                        ingress.next_cursor_token,
                        ingress.next_cursor_sha256,
                        cursor_revision,
                        persisted_at_ms,
                        ingress.ingress_id,
                        ingress.cursor_stream_key,
                        cursor["revision"],
                        cursor["cursor_sha256"],
                    ),
                )
                if updated.rowcount != 1:
                    raise CursorConflictError("cursor changed before durable CAS commit")
            self._connection.execute(
                """
                INSERT INTO ack_permits(
                    permit_id, ingress_id, cursor_stream_key, cursor_revision,
                    next_cursor_sha256, issued_at_ms, permit_json, permit_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    permit.permit_id,
                    permit.ingress_id,
                    permit.cursor_stream_key,
                    permit.cursor_revision,
                    permit.next_cursor_sha256,
                    permit.issued_at_ms,
                    permit_json,
                    permit_digest,
                ),
            )
        return InboxPersistResult(permit, True, False)

    def mark_acknowledged(
        self,
        permit_id: str,
        *,
        platform_receipt_sha256: str,
        acknowledged_at_ms: int,
    ) -> bool:
        if len(platform_receipt_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in platform_receipt_sha256
        ):
            raise ValueError("platform receipt digest is invalid")
        with self._lock, self._transaction():
            row = self._connection.execute(
                """
                SELECT ingress_id, issued_at_ms, platform_receipt_sha256,
                       acknowledged_at_ms
                FROM ack_permits WHERE permit_id = ?
                """,
                (permit_id,),
            ).fetchone()
            if row is None:
                raise InboxConflictError("ACK permit does not exist")
            if acknowledged_at_ms < row["issued_at_ms"]:
                raise ValueError("platform ACK predates permit issuance")
            if row["platform_receipt_sha256"] is not None:
                if row["platform_receipt_sha256"] != platform_receipt_sha256:
                    raise AckConflictError("ACK permit was reused with a different receipt")
                return False
            self._connection.execute(
                """
                UPDATE ack_permits
                SET platform_receipt_sha256 = ?, acknowledged_at_ms = ?
                WHERE permit_id = ? AND platform_receipt_sha256 IS NULL
                """,
                (platform_receipt_sha256, acknowledged_at_ms, permit_id),
            )
            updated = self._connection.execute(
                """
                UPDATE inbox_records SET state = 'ACKED'
                WHERE ingress_id = ? AND state = 'ACK_ELIGIBLE'
                """,
                (row["ingress_id"],),
            )
            if updated.rowcount != 1:
                raise InboxCorruptionError("ACK permit and inbox state disagree")
        return True

    def get_cursor(self, cursor_stream_key: str) -> CursorSnapshot | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM cursor_state WHERE cursor_stream_key = ?",
                (cursor_stream_key,),
            ).fetchone()
            if row is None:
                return None
            return CursorSnapshot(
                cursor_stream_key=row["cursor_stream_key"],
                cursor_token=row["cursor_token"],
                cursor_sha256=row["cursor_sha256"],
                revision=row["revision"],
                last_ingress_id=row["last_ingress_id"],
            )

    def list_unacknowledged(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        limit: int = 1_000,
    ) -> tuple[PendingInboxDelivery, ...]:
        if channel not in {"wechat", "feishu"} or not 1 <= limit <= 10_000:
            raise ValueError("pending inbox query is invalid")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT r.ingress_json,r.ingress_sha256,p.permit_json,p.permit_sha256
                FROM inbox_records AS r
                JOIN ack_permits AS p ON p.ingress_id=r.ingress_id
                WHERE r.channel=? AND r.tenant_id=? AND r.link_account_id=?
                  AND r.state='ACK_ELIGIBLE' AND p.platform_receipt_sha256 IS NULL
                ORDER BY r.persisted_at_ms,r.ingress_id LIMIT ?
                """,
                (channel, tenant_id, link_account_id, limit),
            ).fetchall()
        return tuple(
            PendingInboxDelivery(
                _parse_ingress(row["ingress_json"], row["ingress_sha256"]),
                _parse_permit(row["permit_json"], row["permit_sha256"]),
            )
            for row in rows
        )

    def get_ingress(self, ingress_id: str) -> InboxIngress | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT ingress_json,ingress_sha256 FROM inbox_records WHERE ingress_id=?",
                (ingress_id,),
            ).fetchone()
            if row is None:
                return None
            return _parse_ingress(row["ingress_json"], row["ingress_sha256"])

    def count_records(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT count(*) FROM inbox_records").fetchone()[0])

    def channel_drain_facts(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
    ) -> InboxDrainFacts:
        if channel not in {"wechat", "feishu"} or not tenant_id or not link_account_id:
            raise ValueError("inbox drain scope is invalid")
        with self._lock:
            if self._closed:
                raise InboxError("inbox is closed")
            self._verify_application_rows()
            rows = self._connection.execute(
                """
                SELECT i.ingress_id, i.state, i.ingress_sha256,
                       a.permit_sha256, a.platform_receipt_sha256,
                       a.acknowledged_at_ms
                FROM inbox_records AS i
                JOIN ack_permits AS a ON a.ingress_id = i.ingress_id
                WHERE i.channel = ? AND i.tenant_id = ? AND i.link_account_id = ?
                ORDER BY i.ingress_id
                """,
                (channel, tenant_id, link_account_id),
            ).fetchall()
            cursor = self._connection.execute(
                """
                SELECT cursor_sha256, revision, last_ingress_id
                FROM cursor_state
                WHERE channel = ? AND tenant_id = ? AND link_account_id = ?
                """,
                (channel, tenant_id, link_account_id),
            ).fetchone()
            records = tuple(
                {
                    "ingress_id": row["ingress_id"],
                    "state": row["state"],
                    "ingress_sha256": row["ingress_sha256"],
                    "permit_sha256": row["permit_sha256"],
                    "platform_receipt_sha256": row["platform_receipt_sha256"],
                    "acknowledged_at_ms": row["acknowledged_at_ms"],
                }
                for row in rows
            )
            cursor_fact = None if cursor is None else {
                "cursor_sha256": cursor["cursor_sha256"],
                "revision": cursor["revision"],
                "last_ingress_id": cursor["last_ingress_id"],
            }
            ledger_sha256 = canonical_sha256(
                {
                    "domain": "tiangong.communication.inbox-drain.v1",
                    "channel": channel,
                    "tenant_id": tenant_id,
                    "link_account_id": link_account_id,
                    "records": records,
                    "cursor": cursor_fact,
                }
            )
            return InboxDrainFacts(
                channel=channel,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
                unacknowledged_count=sum(row["state"] != "ACKED" for row in rows),
                last_cursor_sha256=None if cursor is None else cursor["cursor_sha256"],
                ledger_sha256=ledger_sha256,
            )

    def _verify_application_rows(self) -> None:
        inbox_by_id: dict[str, InboxIngress] = {}
        for row in self._connection.execute("SELECT * FROM inbox_records").fetchall():
            ingress = _parse_ingress(row["ingress_json"], row["ingress_sha256"])
            envelope_json, envelope_sha256 = _json_and_sha(ingress.envelope)
            expected = {
                "ingress_id": ingress.ingress_id,
                "idempotency_key": ingress.envelope.idempotency_key,
                "channel": ingress.envelope.channel,
                "tenant_id": ingress.envelope.tenant_id,
                "link_account_id": ingress.envelope.link_account_id,
                "conversation_ref": ingress.envelope.conversation_ref,
                "channel_message_ref": ingress.envelope.channel_message_ref,
                "envelope_json": envelope_json,
                "envelope_sha256": envelope_sha256,
                "raw_payload_object_id": ingress.raw_payload_object_id,
                "raw_payload_sha256": ingress.raw_payload_sha256,
                "raw_payload_size_bytes": ingress.raw_payload_size_bytes,
                "cursor_stream_key": ingress.cursor_stream_key,
                "previous_cursor_sha256": ingress.previous_cursor_sha256,
                "next_cursor_sha256": ingress.next_cursor_sha256,
            }
            if any(row[name] != value for name, value in expected.items()):
                raise InboxCorruptionError("inbox columns disagree with canonical ingress")
            inbox_by_id[ingress.ingress_id] = ingress

        permits: dict[str, ChannelAckPermit] = {}
        for row in self._connection.execute("SELECT * FROM ack_permits").fetchall():
            permit = _parse_permit(row["permit_json"], row["permit_sha256"])
            if (
                permit.permit_id != row["permit_id"]
                or permit.ingress_id != row["ingress_id"]
                or permit.cursor_stream_key != row["cursor_stream_key"]
                or permit.cursor_revision != row["cursor_revision"]
                or permit.next_cursor_sha256 != row["next_cursor_sha256"]
                or permit.issued_at_ms != row["issued_at_ms"]
            ):
                raise InboxCorruptionError("ACK permit columns disagree with canonical permit")
            ingress = inbox_by_id.get(permit.ingress_id)
            if ingress is None or permit.inbox_record_sha256 != ingress.ingress_sha256:
                raise InboxCorruptionError("ACK permit is not bound to its inbox record")
            permits[permit.ingress_id] = permit

        for row in self._connection.execute("SELECT * FROM inbox_records").fetchall():
            if row["ingress_id"] not in permits:
                raise InboxCorruptionError("inbox record is missing an ACK permit")
            ack = self._connection.execute(
                """
                SELECT platform_receipt_sha256, acknowledged_at_ms
                FROM ack_permits WHERE ingress_id = ?
                """,
                (row["ingress_id"],),
            ).fetchone()
            acknowledged = ack["platform_receipt_sha256"] is not None
            if (row["state"] == "ACKED") != acknowledged:
                raise InboxCorruptionError("inbox ACK state disagrees with platform receipt")

        for row in self._connection.execute("SELECT * FROM cursor_state").fetchall():
            ingress = inbox_by_id.get(row["last_ingress_id"])
            if ingress is None:
                raise InboxCorruptionError("cursor references a missing inbox record")
            if (
                row["cursor_stream_key"] != ingress.cursor_stream_key
                or row["cursor_token"] != ingress.next_cursor_token
                or row["cursor_sha256"] != ingress.next_cursor_sha256
                or cursor_token_sha256(row["cursor_token"]) != row["cursor_sha256"]
                or row["channel"] != ingress.envelope.channel
                or row["tenant_id"] != ingress.envelope.tenant_id
                or row["link_account_id"] != ingress.envelope.link_account_id
            ):
                raise InboxCorruptionError("cursor state disagrees with its durable ingress")

    def health_check(self, *, now_ms: int, full: bool = False) -> InboxHealthEvidence:
        with self._lock:
            if self._closed:
                return InboxHealthEvidence(False, "inbox.closed", now_ms, None, False)
            try:
                check = "integrity_check" if full else "quick_check"
                if [row[0] for row in self._connection.execute(f"PRAGMA {check}").fetchall()] != ["ok"]:
                    raise InboxCorruptionError("SQLite integrity check failed")
                if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise InboxCorruptionError("inbox foreign key check failed")
                _validate_schema(self._connection)
                self._verify_application_rows()
                journal = str(self._connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if (
                    journal != "wal"
                    or self._connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
                    or self._connection.execute("PRAGMA synchronous").fetchone()[0] < 2
                    or self._connection.execute("PRAGMA trusted_schema").fetchone()[0] != 0
                ):
                    raise InboxCorruptionError("inbox safety PRAGMA mismatch")
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._connection.execute(
                        "UPDATE inbox_migrations SET applied_at_ms = applied_at_ms WHERE version = 1"
                    )
                finally:
                    self._connection.execute("ROLLBACK")
                return InboxHealthEvidence(
                    True,
                    "inbox.ok",
                    now_ms,
                    _schema_sha256(self._connection),
                    True,
                )
            except (sqlite3.DatabaseError, InboxError, OSError):
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                return InboxHealthEvidence(False, "inbox.check.failed", now_ms, None, False)

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
    "AckConflictError",
    "ChannelAckPermit",
    "CommunicationInbox",
    "CursorConflictError",
    "CursorSnapshot",
    "INBOX_APPLICATION_ID",
    "INBOX_SCHEMA_VERSION",
    "InboxConflictError",
    "InboxCorruptionError",
    "InboxError",
    "InboxHealthEvidence",
    "InboxDrainFacts",
    "InboxIngress",
    "InboxMigrationError",
    "InboxPersistResult",
    "PendingInboxDelivery",
    "cursor_token_sha256",
    "derive_cursor_stream_key",
    "expected_inbox_schema_sha256",
]
