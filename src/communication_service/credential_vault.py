"""DPAPI-protected channel credentials with public, secret-free status evidence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from contracts import canonical_json_bytes, canonical_sha256
from runtime_security import DataProtector, WindowsDpapiProtector


_APPLICATION_ID = 0x54474356
_SCHEMA_VERSION = 1
_SCHEMA = (
    "CREATE TABLE credential_record ("
    "channel TEXT NOT NULL, tenant_id TEXT NOT NULL, link_account_id TEXT NOT NULL, "
    "revision INTEGER NOT NULL, protected_blob BLOB NOT NULL, protected_sha256 TEXT NOT NULL, "
    "plaintext_sha256 TEXT NOT NULL, public_metadata_json BLOB NOT NULL, "
    "public_metadata_sha256 TEXT NOT NULL, updated_at_ms INTEGER NOT NULL, "
    "PRIMARY KEY(channel,tenant_id,link_account_id)) STRICT",
)
_WECHAT_FIELDS = frozenset({"account_id", "bot_token", "cursor", "user_id"})
_FEISHU_FIELDS = frozenset(
    {"app_id", "app_secret", "bot_open_id", "encrypt_key", "platform_tenant_key", "verification_token"}
)


class CredentialVaultError(RuntimeError):
    pass


def _strict_json_object(raw: bytes, *, error: str) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise CredentialVaultError(error)
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except CredentialVaultError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CredentialVaultError(error) from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise CredentialVaultError(error)
    return value


@dataclass(frozen=True)
class CredentialStatus:
    channel: str
    tenant_id: str
    link_account_id: str
    revision: int
    configured: bool
    updated_at_ms: int
    public_metadata: dict[str, object]
    evidence_sha256: str


def _entropy(channel: str, tenant_id: str, link_account_id: str) -> bytes:
    return canonical_json_bytes(
        {
            "app_id": "tiangong-v3-qiyuan",
            "context": "communication-channel-credential-v1",
            "channel": channel,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
        }
    )


def _validate_scope(channel: str, tenant_id: str, link_account_id: str) -> None:
    if channel not in {"wechat", "feishu"}:
        raise ValueError("credential channel is invalid")
    for value in (tenant_id, link_account_id):
        if not value or value != value.strip() or len(value) > 160 or "\x00" in value:
            raise ValueError("credential scope is invalid")


def _validate_secret_payload(channel: str, payload: Mapping[str, str]) -> dict[str, str]:
    allowed = _WECHAT_FIELDS if channel == "wechat" else _FEISHU_FIELDS
    if set(payload) != allowed:
        raise ValueError("credential fields do not match the channel contract")
    clean: dict[str, str] = {}
    for key in sorted(allowed):
        value = payload.get(key)
        if not isinstance(value, str) or value != value.strip() or "\x00" in value:
            raise ValueError("credential value is malformed")
        if len(value.encode("utf-8")) > 16_384:
            raise ValueError("credential value is too large")
        clean[key] = value
    required = {"account_id", "bot_token", "user_id"} if channel == "wechat" else {
        "app_id",
        "app_secret",
        "bot_open_id",
        "platform_tenant_key",
    }
    if any(not clean[key] for key in required):
        raise ValueError("required channel credentials are missing")
    return clean


class ChannelCredentialVault:
    def __init__(self, path: Path, connection: sqlite3.Connection, protector: DataProtector) -> None:
        self.path = path
        self._connection = connection
        self._protector = protector
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        now_ms: int,
        protector: DataProtector | None = None,
    ) -> "ChannelCredentialVault":
        if not path.is_absolute() or path == Path(path.anchor) or now_ms < 0:
            raise ValueError("credential vault path or time is invalid")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise CredentialVaultError("credential vault path is unsafe")
        connection = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if application_id == 0 and version == 0:
                if connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]:
                    raise CredentialVaultError("unidentified credential database is not empty")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            elif application_id != _APPLICATION_ID or version != _SCHEMA_VERSION:
                raise CredentialVaultError("credential database identity or version is unsupported")
            os.chmod(path, 0o600)
            vault = cls(path, connection, protector or WindowsDpapiProtector())
            vault.health_check()
            return vault
        except Exception:
            connection.close()
            raise

    def put(
        self,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        payload: Mapping[str, str],
        *,
        updated_at_ms: int,
        source: str,
    ) -> CredentialStatus:
        _validate_scope(channel, tenant_id, link_account_id)
        if updated_at_ms < 0 or source not in {"control_plane", "legacy_migration"}:
            raise ValueError("credential update metadata is invalid")
        clean = _validate_secret_payload(channel, payload)
        plaintext = bytearray(canonical_json_bytes(clean))
        plaintext_sha = hashlib.sha256(plaintext).hexdigest()
        try:
            protected = self._protector.protect(plaintext, _entropy(channel, tenant_id, link_account_id))
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0
        public = {
            "source": source,
            "field_presence": {key: bool(clean[key]) for key in sorted(clean)},
            "identity_sha256": {
                key: hashlib.sha256(clean[key].encode("utf-8")).hexdigest()
                for key in sorted(clean)
                if key in {"account_id", "app_id", "bot_open_id", "platform_tenant_key", "user_id"}
                and clean[key]
            },
        }
        public_bytes = canonical_json_bytes(public)
        with self._lock:
            self._require_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT revision,plaintext_sha256 FROM credential_record "
                    "WHERE channel=? AND tenant_id=? AND link_account_id=?",
                    (channel, tenant_id, link_account_id),
                ).fetchone()
                if row is not None and row["plaintext_sha256"] == plaintext_sha:
                    self._connection.execute("ROLLBACK")
                    status = self.status(channel, tenant_id, link_account_id)
                    assert status is not None
                    return status
                revision = 1 if row is None else int(row["revision"]) + 1
                self._connection.execute(
                    "INSERT INTO credential_record(channel,tenant_id,link_account_id,revision,"
                    "protected_blob,protected_sha256,plaintext_sha256,public_metadata_json,"
                    "public_metadata_sha256,updated_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(channel,tenant_id,link_account_id) DO UPDATE SET "
                    "revision=excluded.revision,protected_blob=excluded.protected_blob,"
                    "protected_sha256=excluded.protected_sha256,plaintext_sha256=excluded.plaintext_sha256,"
                    "public_metadata_json=excluded.public_metadata_json,"
                    "public_metadata_sha256=excluded.public_metadata_sha256,updated_at_ms=excluded.updated_at_ms",
                    (
                        channel,
                        tenant_id,
                        link_account_id,
                        revision,
                        protected,
                        hashlib.sha256(protected).hexdigest(),
                        plaintext_sha,
                        public_bytes,
                        hashlib.sha256(public_bytes).hexdigest(),
                        updated_at_ms,
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        status = self.status(channel, tenant_id, link_account_id)
        assert status is not None
        return status

    def get(self, channel: str, tenant_id: str, link_account_id: str) -> dict[str, str] | None:
        _validate_scope(channel, tenant_id, link_account_id)
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT * FROM credential_record WHERE channel=? AND tenant_id=? AND link_account_id=?",
                (channel, tenant_id, link_account_id),
            ).fetchone()
        if row is None:
            return None
        protected = bytes(row["protected_blob"])
        if hashlib.sha256(protected).hexdigest() != row["protected_sha256"]:
            raise CredentialVaultError("protected credential digest is invalid")
        plaintext = self._protector.unprotect(protected, _entropy(channel, tenant_id, link_account_id))
        try:
            if hashlib.sha256(plaintext).hexdigest() != row["plaintext_sha256"]:
                raise CredentialVaultError("unprotected credential digest is invalid")
            value = _strict_json_object(
                bytes(plaintext),
                error="unprotected credential payload is invalid",
            )
            return _validate_secret_payload(channel, value)
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0

    def status(self, channel: str, tenant_id: str, link_account_id: str) -> CredentialStatus | None:
        _validate_scope(channel, tenant_id, link_account_id)
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT revision,public_metadata_json,public_metadata_sha256,updated_at_ms "
                "FROM credential_record WHERE channel=? AND tenant_id=? AND link_account_id=?",
                (channel, tenant_id, link_account_id),
            ).fetchone()
        if row is None:
            return None
        raw = bytes(row["public_metadata_json"])
        if hashlib.sha256(raw).hexdigest() != row["public_metadata_sha256"]:
            raise CredentialVaultError("credential public metadata digest is invalid")
        public = _strict_json_object(raw, error="credential public metadata is invalid")
        evidence = {
            "channel": channel,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "revision": int(row["revision"]),
            "configured": True,
            "updated_at_ms": int(row["updated_at_ms"]),
            "public_metadata": public,
        }
        return CredentialStatus(**evidence, evidence_sha256=canonical_sha256(evidence))

    def list_statuses(self) -> tuple[CredentialStatus, ...]:
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                "SELECT channel,tenant_id,link_account_id FROM credential_record "
                "ORDER BY channel,tenant_id,link_account_id"
            ).fetchall()
        return tuple(
            status
            for row in rows
            if (status := self.status(row["channel"], row["tenant_id"], row["link_account_id"]))
            is not None
        )

    def health_check(self) -> None:
        with self._lock:
            self._require_open()
            if self._connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise CredentialVaultError("credential vault integrity check failed")
            for row in self._connection.execute("SELECT * FROM credential_record").fetchall():
                protected = bytes(row["protected_blob"])
                public = bytes(row["public_metadata_json"])
                if (
                    hashlib.sha256(protected).hexdigest() != row["protected_sha256"]
                    or hashlib.sha256(public).hexdigest() != row["public_metadata_sha256"]
                ):
                    raise CredentialVaultError("credential vault semantic check failed")

    def _require_open(self) -> None:
        if self._closed:
            raise CredentialVaultError("credential vault is closed")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True


__all__ = ["ChannelCredentialVault", "CredentialStatus", "CredentialVaultError"]
