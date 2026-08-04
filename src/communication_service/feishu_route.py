"""DPAPI-protected Feishu reply/download route state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from contracts import canonical_json_bytes, canonical_sha256
from runtime_security import DataProtector, WindowsDpapiProtector


FEISHU_ROUTE_APPLICATION_ID = 0x54474652
FEISHU_ROUTE_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE feishu_routes (
    route_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    link_account_id TEXT NOT NULL,
    conversation_scope_hash TEXT NOT NULL,
    route_cipher BLOB NOT NULL,
    route_plain_sha256 TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0),
    state_sha256 TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX feishu_route_scope
ON feishu_routes(tenant_id,link_account_id,conversation_scope_hash);
CREATE TABLE feishu_resources (
    resource_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    link_account_id TEXT NOT NULL,
    conversation_scope_hash TEXT NOT NULL,
    source_message_ref TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK(resource_type IN ('image','file')),
    resource_key_sha256 TEXT NOT NULL,
    resource_cipher BLOB NOT NULL,
    resource_plain_sha256 TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    state_sha256 TEXT NOT NULL,
    UNIQUE(tenant_id,link_account_id,conversation_scope_hash,source_message_ref,resource_type,resource_key_sha256)
) STRICT;
CREATE INDEX feishu_resource_scope
ON feishu_resources(tenant_id,link_account_id,conversation_scope_hash,created_at_ms);
"""


@dataclass(frozen=True)
class FeishuReplyRoute:
    route_key: str
    chat_id: str
    message_id: str
    root_id: str | None
    parent_id: str | None
    thread_id: str | None
    sender_open_id: str | None


@dataclass(frozen=True)
class FeishuProtectedResource:
    resource_id: str
    created_at_ms: int
    message_id: str
    resource_type: str
    resource_key: str
    filename: str | None
    source_message_ref: str


@dataclass(frozen=True)
class FeishuRouteHealth:
    healthy: bool
    reason_code: str
    schema_sha256: str | None
    writable: bool


class FeishuRouteError(RuntimeError):
    pass


class FeishuRouteConflict(FeishuRouteError):
    pass


def derive_feishu_route_key(
    tenant_id: str, link_account_id: str, conversation_scope_hash: str
) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.communication.feishu-route.v1",
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "conversation_scope_hash": conversation_scope_hash,
        }
    )


def derive_feishu_resource_id(
    tenant_id: str,
    link_account_id: str,
    conversation_scope_hash: str,
    source_message_ref: str,
    resource_type: str,
    resource_key: str,
) -> str:
    return "fsres_" + canonical_sha256(
        {
            "domain": "tiangong.communication.feishu-resource.v1",
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "conversation_scope_hash": conversation_scope_hash,
            "source_message_ref": source_message_ref,
            "resource_type": resource_type,
            "resource_key_sha256": hashlib.sha256(resource_key.encode("utf-8")).hexdigest(),
        }
    )


def _schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type,name"
    ).fetchall()
    return canonical_sha256(
        {"objects": [dict(row) for row in rows], "version": FEISHU_ROUTE_SCHEMA_VERSION}
    )


@lru_cache(maxsize=1)
def expected_feishu_route_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(_SCHEMA)
        return _schema_sha256(connection)
    finally:
        connection.close()


def _safe(value: str | None, name: str, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"Feishu {name} is required")
        return None
    if not isinstance(value, str) or value != value.strip() or "\x00" in value:
        raise ValueError(f"Feishu {name} is invalid")
    if len(value.encode("utf-8")) > 2_048:
        raise ValueError(f"Feishu {name} is too long")
    return value


class FeishuRouteLedger:
    def __init__(self, path, connection, protector):
        self.path = path
        self._connection = connection
        self._protector = protector
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, now_ms: int, protector: DataProtector | None = None):
        if not path.is_absolute() or now_ms < 0:
            raise ValueError("Feishu route path or time is invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise FeishuRouteError("Feishu route path is unsafe")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            if str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() != "wal":
                raise FeishuRouteError("Feishu route ledger could not enable WAL")
            application = int(connection.execute("PRAGMA application_id").fetchone()[0])
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            objects = connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if application not in {0, FEISHU_ROUTE_APPLICATION_ID} or version > 1:
                raise FeishuRouteError("Feishu route metadata is incompatible")
            if version == 0:
                if objects:
                    raise FeishuRouteError("unversioned Feishu route ledger is not empty")
                connection.executescript(
                    "BEGIN EXCLUSIVE;\n"
                    + _SCHEMA
                    + f"\nPRAGMA application_id={FEISHU_ROUTE_APPLICATION_ID};"
                    + f"\nPRAGMA user_version={FEISHU_ROUTE_SCHEMA_VERSION};\nCOMMIT;"
                )
            if _schema_sha256(connection) != expected_feishu_route_schema_sha256():
                raise FeishuRouteError("Feishu route schema is incompatible")
            ledger = cls(path, connection, protector or WindowsDpapiProtector())
            if not ledger.health_check(now_ms=now_ms, full=True).healthy:
                raise FeishuRouteError("Feishu route ledger failed initial health check")
            os.chmod(path, 0o600)
            return ledger
        except Exception:
            connection.close()
            raise

    @contextmanager
    def _transaction(self):
        if self._closed:
            raise FeishuRouteError("Feishu route ledger is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _entropy(route_key: str) -> bytes:
        return canonical_json_bytes(
            {
                "app_id": "tiangong-v3-qiyuan",
                "context": "feishu-reply-route-v1",
                "route_key": route_key,
            }
        )

    @staticmethod
    def _resource_entropy(resource_id: str) -> bytes:
        return canonical_json_bytes(
            {
                "app_id": "tiangong-v3-qiyuan",
                "context": "feishu-message-resource-v1",
                "resource_id": resource_id,
            }
        )

    def _protect(self, route_key: str, payload: dict) -> tuple[bytes, str]:
        raw = canonical_json_bytes(payload)
        plaintext = bytearray(raw)
        try:
            cipher = self._protector.protect(plaintext, self._entropy(route_key))
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0
        return cipher, hashlib.sha256(raw).hexdigest()

    def _unprotect(self, row: sqlite3.Row) -> FeishuReplyRoute:
        plaintext = self._protector.unprotect(
            bytes(row["route_cipher"]), self._entropy(str(row["route_key"]))
        )
        try:
            raw = bytes(plaintext)
            if hashlib.sha256(raw).hexdigest() != row["route_plain_sha256"]:
                raise FeishuRouteError("Feishu route plaintext digest is invalid")
            payload = json.loads(raw.decode("utf-8"))
            if canonical_json_bytes(payload) != raw:
                raise FeishuRouteError("Feishu route plaintext is not canonical")
            return FeishuReplyRoute(route_key=row["route_key"], **payload)
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0

    def _protect_resource(self, resource_id: str, payload: dict) -> tuple[bytes, str]:
        raw = canonical_json_bytes(payload)
        plaintext = bytearray(raw)
        try:
            cipher = self._protector.protect(
                plaintext, self._resource_entropy(resource_id)
            )
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0
        return cipher, hashlib.sha256(raw).hexdigest()

    def _unprotect_resource(self, row: sqlite3.Row) -> FeishuProtectedResource:
        plaintext = self._protector.unprotect(
            bytes(row["resource_cipher"]),
            self._resource_entropy(str(row["resource_id"])),
        )
        try:
            raw = bytes(plaintext)
            if hashlib.sha256(raw).hexdigest() != row["resource_plain_sha256"]:
                raise FeishuRouteError("Feishu resource plaintext digest is invalid")
            payload = json.loads(raw.decode("utf-8"))
            if canonical_json_bytes(payload) != raw:
                raise FeishuRouteError("Feishu resource plaintext is not canonical")
            return FeishuProtectedResource(
                resource_id=row["resource_id"],
                created_at_ms=int(row["created_at_ms"]),
                **payload,
            )
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0

    @staticmethod
    def _state_sha(row: dict) -> str:
        cipher_fields = [key for key in row if key.endswith("_cipher")]
        if len(cipher_fields) != 1:
            raise FeishuRouteError("Feishu protected state has invalid cipher fields")
        field = cipher_fields[0]
        cipher = row.pop(field)
        row[field + "_sha256"] = hashlib.sha256(bytes(cipher)).hexdigest()
        return canonical_sha256(row)

    def upsert(
        self,
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        chat_id: str,
        message_id: str,
        root_id: str | None,
        parent_id: str | None,
        thread_id: str | None,
        sender_open_id: str | None,
        observed_at_ms: int,
    ) -> str:
        route_key = derive_feishu_route_key(
            tenant_id, link_account_id, conversation_scope_hash
        )
        payload = {
            "chat_id": _safe(chat_id, "chat_id", required=True),
            "message_id": _safe(message_id, "message_id", required=True),
            "root_id": _safe(root_id, "root_id"),
            "parent_id": _safe(parent_id, "parent_id"),
            "thread_id": _safe(thread_id, "thread_id"),
            "sender_open_id": _safe(sender_open_id, "sender_open_id"),
        }
        cipher, plain_sha = self._protect(route_key, payload)
        values = {
            "route_key": route_key,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "conversation_scope_hash": conversation_scope_hash,
            "route_cipher": cipher,
            "route_plain_sha256": plain_sha,
            "updated_at_ms": observed_at_ms,
        }
        state_sha = self._state_sha(dict(values))
        with self._lock, self._transaction():
            previous = self._connection.execute(
                "SELECT * FROM feishu_routes WHERE route_key=?", (route_key,)
            ).fetchone()
            if previous is not None:
                old = self._unprotect(previous)
                if old.chat_id != payload["chat_id"]:
                    raise FeishuRouteConflict("Feishu route chat changed")
                if observed_at_ms < previous["updated_at_ms"]:
                    raise FeishuRouteConflict("Feishu route update is out of order")
            self._connection.execute(
                "INSERT INTO feishu_routes VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(route_key) DO UPDATE SET route_cipher=excluded.route_cipher,"
                "route_plain_sha256=excluded.route_plain_sha256,updated_at_ms=excluded.updated_at_ms,"
                "state_sha256=excluded.state_sha256",
                (*values.values(), state_sha),
            )
        return route_key

    def resolve(
        self, *, route_key: str, tenant_id: str, link_account_id: str, conversation_scope_hash: str
    ) -> FeishuReplyRoute:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM feishu_routes WHERE route_key=?", (route_key,)
            ).fetchone()
            if row is None:
                raise FeishuRouteConflict("Feishu route does not exist")
            values = dict(row)
            stored = values.pop("state_sha256")
            if stored != self._state_sha(values):
                raise FeishuRouteError("Feishu route state digest is invalid")
            if (
                row["tenant_id"] != tenant_id
                or row["link_account_id"] != link_account_id
                or row["conversation_scope_hash"] != conversation_scope_hash
            ):
                raise FeishuRouteConflict("Feishu route scope changed")
            return self._unprotect(row)

    def register_resource(
        self,
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        source_message_ref: str,
        message_id: str,
        resource_type: str,
        resource_key: str,
        filename: str | None,
        created_at_ms: int,
    ) -> str:
        if resource_type not in {"image", "file"} or created_at_ms < 0:
            raise ValueError("Feishu resource type or time is invalid")
        for value, name in (
            (tenant_id, "tenant_id"),
            (link_account_id, "link_account_id"),
            (conversation_scope_hash, "conversation_scope_hash"),
            (source_message_ref, "source_message_ref"),
            (message_id, "message_id"),
            (resource_key, "resource_key"),
        ):
            _safe(value, name, required=True)
        clean_filename = _safe(filename, "filename")
        resource_id = derive_feishu_resource_id(
            tenant_id,
            link_account_id,
            conversation_scope_hash,
            source_message_ref,
            resource_type,
            resource_key,
        )
        payload = {
            "message_id": message_id,
            "resource_type": resource_type,
            "resource_key": resource_key,
            "filename": clean_filename,
            "source_message_ref": source_message_ref,
        }
        cipher, plain_sha = self._protect_resource(resource_id, payload)
        values = {
            "resource_id": resource_id,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "conversation_scope_hash": conversation_scope_hash,
            "source_message_ref": source_message_ref,
            "resource_type": resource_type,
            "resource_key_sha256": hashlib.sha256(resource_key.encode("utf-8")).hexdigest(),
            "resource_cipher": cipher,
            "resource_plain_sha256": plain_sha,
            "created_at_ms": created_at_ms,
        }
        state_sha = self._state_sha(dict(values))
        with self._lock, self._transaction():
            previous = self._connection.execute(
                "SELECT * FROM feishu_resources WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if previous is not None:
                old = self._unprotect_resource(previous)
                if old != FeishuProtectedResource(
                    resource_id=resource_id,
                    created_at_ms=created_at_ms,
                    **payload,
                ):
                    raise FeishuRouteConflict("Feishu resource identity changed")
                return resource_id
            self._connection.execute(
                "INSERT INTO feishu_resources VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (*values.values(), state_sha),
            )
        return resource_id

    def resolve_resource(
        self,
        *,
        resource_id: str,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
    ) -> FeishuProtectedResource:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM feishu_resources WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if row is None:
                raise FeishuRouteConflict("Feishu resource does not exist")
            values = dict(row)
            stored = values.pop("state_sha256")
            if stored != self._state_sha(values):
                raise FeishuRouteError("Feishu resource state digest is invalid")
            if (
                row["tenant_id"] != tenant_id
                or row["link_account_id"] != link_account_id
                or row["conversation_scope_hash"] != conversation_scope_hash
            ):
                raise FeishuRouteConflict("Feishu resource scope changed")
            return self._unprotect_resource(row)

    def health_check(self, *, now_ms: int, full: bool = False) -> FeishuRouteHealth:
        if self._closed or now_ms < 0:
            return FeishuRouteHealth(False, "feishu_route.closed", None, False)
        try:
            check = "integrity_check" if full else "quick_check"
            if self._connection.execute(f"PRAGMA {check}").fetchone()[0] != "ok":
                raise FeishuRouteError("Feishu route SQLite check failed")
            if _schema_sha256(self._connection) != expected_feishu_route_schema_sha256():
                raise FeishuRouteError("Feishu route schema changed")
            for row in self._connection.execute("SELECT * FROM feishu_routes"):
                values = dict(row)
                stored = values.pop("state_sha256")
                if stored != self._state_sha(values):
                    raise FeishuRouteError("Feishu route state digest is invalid")
                if full:
                    self._unprotect(row)
            for row in self._connection.execute("SELECT * FROM feishu_resources"):
                values = dict(row)
                stored = values.pop("state_sha256")
                if stored != self._state_sha(values):
                    raise FeishuRouteError("Feishu resource state digest is invalid")
                if full:
                    self._unprotect_resource(row)
            with self._transaction():
                self._connection.execute("SELECT 1")
            return FeishuRouteHealth(
                True, "feishu_route.ok", expected_feishu_route_schema_sha256(), True
            )
        except Exception:
            return FeishuRouteHealth(False, "feishu_route.check.failed", None, False)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True


__all__ = [
    "FeishuProtectedResource",
    "FeishuReplyRoute",
    "FeishuRouteConflict",
    "FeishuRouteError",
    "FeishuRouteHealth",
    "FeishuRouteLedger",
    "derive_feishu_route_key",
    "derive_feishu_resource_id",
    "expected_feishu_route_schema_sha256",
]
