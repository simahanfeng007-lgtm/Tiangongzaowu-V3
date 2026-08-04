"""Durable WeChat ordering and DPAPI-protected iLink context-token state."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from contracts import canonical_json_bytes, canonical_sha256
from runtime_security import DataProtector, WindowsDpapiProtector


WECHAT_SESSION_APPLICATION_ID = 0x54475753
WECHAT_SESSION_SCHEMA_VERSION = 1
_MIGRATION_ID = "communication-wechat-session-v1"
_MIGRATION_SQL = """
CREATE TABLE session_state (
    session_key TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    sender_ref TEXT NOT NULL,
    conversation_scope_hash TEXT NOT NULL,
    recipient_cipher BLOB NOT NULL,
    recipient_sha256 TEXT NOT NULL,
    last_sequence INTEGER,
    last_received_at_ms INTEGER NOT NULL,
    last_message_ref TEXT NOT NULL,
    context_cipher BLOB,
    context_token_sha256 TEXT,
    context_updated_at_ms INTEGER,
    state_sha256 TEXT NOT NULL
) STRICT;
CREATE TABLE message_decision (
    message_ref TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    message_fingerprint TEXT NOT NULL,
    envelope_sha256 TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    decision_sha256 TEXT NOT NULL,
    context_cipher BLOB,
    context_token_sha256 TEXT,
    created_at_ms INTEGER NOT NULL
) STRICT;
CREATE INDEX idx_wechat_decision_session ON message_decision(session_key, created_at_ms);
"""


class WechatSessionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    message_ref: str = Field(min_length=1, max_length=160)
    session_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification: Literal[
        "ACCEPTED",
        "SELF_MESSAGE",
        "UNEXPECTED_SENDER",
        "GROUP_DISABLED",
        "GROUP_MENTION_REQUIRED",
        "EMPTY_TEXT",
        "ATTACHMENT_HANDLER_UNAVAILABLE",
        "ATTACHMENT_REJECTED",
        "UNSUPPORTED_MESSAGE_TYPE",
        "OUT_OF_ORDER",
        "SEQUENCE_CONFLICT",
    ]
    should_forward: bool
    duplicate: bool
    sequence: int | None = Field(default=None, ge=0)
    context_token_source: Literal["incoming", "cache", "missing", "not_applicable"]
    context_token_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    decided_at_ms: int = Field(ge=0)
    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"decision_sha256", "duplicate"})
        )

    def has_valid_sha256(self) -> bool:
        return self.decision_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"decision_sha256": self.computed_sha256()})


@dataclass(frozen=True)
class WechatSessionResult:
    decision: WechatSessionDecision
    context_token: str | None


@dataclass(frozen=True)
class WechatSessionHealth:
    healthy: bool
    reason_code: str
    schema_sha256: str | None
    writable: bool


class WechatSessionError(RuntimeError):
    pass


class WechatSessionConflict(WechatSessionError):
    pass


def derive_wechat_session_key(account_id: str, sender_ref: str, conversation_scope_hash: str) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.communication.wechat-session.v1",
            "account_id": account_id,
            "sender_ref": sender_ref,
            "conversation_scope_hash": conversation_scope_hash,
        }
    )


def _schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return canonical_sha256(
        {"objects": [dict(row) for row in rows], "user_version": WECHAT_SESSION_SCHEMA_VERSION}
    )


@lru_cache(maxsize=1)
def expected_wechat_session_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(_MIGRATION_SQL)
        return _schema_sha256(connection)
    finally:
        connection.close()


def _state_sha256(row: dict[str, object]) -> str:
    recipient_cipher = row.pop("recipient_cipher", None)
    row["recipient_cipher_sha256"] = (
        None
        if recipient_cipher is None
        else hashlib.sha256(bytes(recipient_cipher)).hexdigest()
    )
    cipher = row.pop("context_cipher", None)
    row["context_cipher_sha256"] = (
        None if cipher is None else hashlib.sha256(bytes(cipher)).hexdigest()
    )
    return canonical_sha256(row)


class WechatSessionLedger:
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
    ) -> "WechatSessionLedger":
        if now_ms < 0 or not path.is_absolute():
            raise ValueError("WeChat session path or time is invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise WechatSessionError("WeChat session path is unsafe")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = FULL")
            if str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() != "wal":
                raise WechatSessionError("WeChat session ledger could not enable WAL")
            cls._migrate(connection, now_ms)
            ledger = cls(path, connection, protector or WindowsDpapiProtector())
            if not ledger.health_check(now_ms=now_ms, full=True).healthy:
                raise WechatSessionError("WeChat session ledger failed initial health check")
            os.chmod(path, 0o600)
            return ledger
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _migrate(connection: sqlite3.Connection, now_ms: int) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if application_id not in {0, WECHAT_SESSION_APPLICATION_ID} or version > 1:
            raise WechatSessionError("WeChat session ledger metadata is incompatible")
        if version == 0:
            if objects:
                raise WechatSessionError("unversioned WeChat session ledger is not empty")
            try:
                connection.executescript(
                    "BEGIN EXCLUSIVE;\n"
                    + _MIGRATION_SQL
                    + f"\nPRAGMA application_id = {WECHAT_SESSION_APPLICATION_ID};"
                    + f"\nPRAGMA user_version = {WECHAT_SESSION_SCHEMA_VERSION};"
                    + "\nCOMMIT;"
                )
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        _ = now_ms
        if _schema_sha256(connection) != expected_wechat_session_schema_sha256():
            raise WechatSessionError("WeChat session ledger schema is incompatible")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise WechatSessionError("WeChat session ledger is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _entropy(*, session_key: str, message_ref: str | None) -> bytes:
        return canonical_json_bytes(
            {
                "app_id": "tiangong-v3-qiyuan",
                "context": "wechat-ilink-context-token-v1",
                "session_key": session_key,
                "message_ref": message_ref,
            }
        )

    @staticmethod
    def _recipient_entropy(*, session_key: str) -> bytes:
        return canonical_json_bytes(
            {
                "app_id": "tiangong-v3-qiyuan",
                "context": "wechat-ilink-recipient-v1",
                "session_key": session_key,
            }
        )

    def _protect(self, token: str, *, session_key: str, message_ref: str | None) -> tuple[bytes, str]:
        if not token or "\x00" in token or len(token.encode("utf-8")) > 8_192:
            raise ValueError("WeChat context token is invalid")
        plaintext = bytearray(token.encode("utf-8"))
        try:
            ciphertext = self._protector.protect(
                plaintext,
                self._entropy(session_key=session_key, message_ref=message_ref),
            )
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0
        return ciphertext, hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _unprotect(
        self,
        ciphertext: bytes,
        expected_sha256: str,
        *,
        session_key: str,
        message_ref: str | None,
    ) -> str:
        plaintext = self._protector.unprotect(
            ciphertext,
            self._entropy(session_key=session_key, message_ref=message_ref),
        )
        try:
            token = bytes(plaintext).decode("utf-8", errors="strict")
            if hashlib.sha256(token.encode("utf-8")).hexdigest() != expected_sha256:
                raise WechatSessionError("protected WeChat context token digest is invalid")
            return token
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0

    def _protect_recipient(self, recipient_user_id: str, *, session_key: str) -> tuple[bytes, str]:
        if (
            not recipient_user_id
            or recipient_user_id != recipient_user_id.strip()
            or "\x00" in recipient_user_id
            or len(recipient_user_id.encode("utf-8")) > 1_024
        ):
            raise ValueError("WeChat recipient user ID is invalid")
        plaintext = bytearray(recipient_user_id.encode("utf-8"))
        try:
            ciphertext = self._protector.protect(
                plaintext,
                self._recipient_entropy(session_key=session_key),
            )
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0
        return ciphertext, hashlib.sha256(recipient_user_id.encode("utf-8")).hexdigest()

    def _unprotect_recipient(
        self,
        ciphertext: bytes,
        expected_sha256: str,
        *,
        session_key: str,
    ) -> str:
        plaintext = self._protector.unprotect(
            ciphertext,
            self._recipient_entropy(session_key=session_key),
        )
        try:
            recipient = bytes(plaintext).decode("utf-8", errors="strict")
            if hashlib.sha256(recipient.encode("utf-8")).hexdigest() != expected_sha256:
                raise WechatSessionError("protected WeChat recipient digest is invalid")
            return recipient
        finally:
            for index in range(len(plaintext)):
                plaintext[index] = 0

    def decide(
        self,
        *,
        account_id: str,
        sender_ref: str,
        conversation_scope_hash: str,
        message_ref: str,
        message_fingerprint: str,
        envelope_sha256: str,
        preliminary_classification: str,
        recipient_user_id: str,
        sequence: int | None,
        received_at_ms: int,
        incoming_context_token: str | None,
        max_cached_token_age_ms: int = 604_800_000,
    ) -> WechatSessionResult:
        if received_at_ms < 0 or max_cached_token_age_ms < 1:
            raise ValueError("WeChat session decision timing is invalid")
        session_key = derive_wechat_session_key(account_id, sender_ref, conversation_scope_hash)
        with self._lock, self._transaction():
            duplicate = self._connection.execute(
                "SELECT * FROM message_decision WHERE message_ref = ?", (message_ref,)
            ).fetchone()
            if duplicate is not None:
                if (
                    duplicate["message_fingerprint"] != message_fingerprint
                    or duplicate["envelope_sha256"] != envelope_sha256
                    or duplicate["session_key"] != session_key
                ):
                    raise WechatSessionConflict("WeChat message identity was rebound")
                decision = WechatSessionDecision.model_validate_json(duplicate["decision_json"])
                if not decision.has_valid_sha256() or decision.decision_sha256 != duplicate["decision_sha256"]:
                    raise WechatSessionError("stored WeChat decision is corrupt")
                token = None
                if duplicate["context_cipher"] is not None:
                    token = self._unprotect(
                        bytes(duplicate["context_cipher"]),
                        str(duplicate["context_token_sha256"]),
                        session_key=session_key,
                        message_ref=message_ref,
                    )
                return WechatSessionResult(decision.model_copy(update={"duplicate": True}), token)

            state = self._connection.execute(
                "SELECT * FROM session_state WHERE session_key = ?", (session_key,)
            ).fetchone()
            classification = preliminary_classification
            if classification == "ACCEPTED" and state is not None and sequence is not None:
                last_sequence = state["last_sequence"]
                if last_sequence is not None and sequence < int(last_sequence):
                    classification = "OUT_OF_ORDER"
                elif (
                    last_sequence is not None
                    and sequence == int(last_sequence)
                    and message_ref != state["last_message_ref"]
                ):
                    classification = "SEQUENCE_CONFLICT"

            context_token: str | None = None
            context_source = "not_applicable"
            if classification == "ACCEPTED":
                if incoming_context_token:
                    context_token = incoming_context_token
                    context_source = "incoming"
                elif (
                    state is not None
                    and state["context_cipher"] is not None
                    and state["context_updated_at_ms"] is not None
                    and received_at_ms - int(state["context_updated_at_ms"]) <= max_cached_token_age_ms
                ):
                    context_token = self._unprotect(
                        bytes(state["context_cipher"]),
                        str(state["context_token_sha256"]),
                        session_key=session_key,
                        message_ref=None,
                    )
                    context_source = "cache"
                else:
                    context_source = "missing"

            decision_cipher = None
            token_sha256 = None
            if context_token is not None:
                decision_cipher, token_sha256 = self._protect(
                    context_token,
                    session_key=session_key,
                    message_ref=message_ref,
                )
            decision = WechatSessionDecision(
                message_ref=message_ref,
                session_key=session_key,
                classification=classification,
                should_forward=classification == "ACCEPTED",
                duplicate=False,
                sequence=sequence,
                context_token_source=context_source,
                context_token_sha256=token_sha256,
                decided_at_ms=received_at_ms,
                decision_sha256="0" * 64,
            ).with_computed_sha256()
            decision_json = canonical_json_bytes(decision.model_dump(mode="json")).decode("utf-8")
            self._connection.execute(
                "INSERT INTO message_decision(message_ref,session_key,message_fingerprint,"
                "envelope_sha256,decision_json,decision_sha256,context_cipher,"
                "context_token_sha256,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    message_ref,
                    session_key,
                    message_fingerprint,
                    envelope_sha256,
                    decision_json,
                    decision.decision_sha256,
                    decision_cipher,
                    token_sha256,
                    received_at_ms,
                ),
            )
            if classification == "ACCEPTED":
                recipient_cipher, recipient_sha256 = self._protect_recipient(
                    recipient_user_id,
                    session_key=session_key,
                )
                if state is not None and (
                    state["recipient_sha256"] != recipient_sha256
                    or self._unprotect_recipient(
                        bytes(state["recipient_cipher"]),
                        str(state["recipient_sha256"]),
                        session_key=session_key,
                    )
                    != recipient_user_id
                ):
                    raise WechatSessionConflict("WeChat session recipient changed")
                state_cipher = state["context_cipher"] if state is not None else None
                state_token_sha256 = state["context_token_sha256"] if state is not None else None
                state_token_time = state["context_updated_at_ms"] if state is not None else None
                if incoming_context_token:
                    state_cipher, state_token_sha256 = self._protect(
                        incoming_context_token,
                        session_key=session_key,
                        message_ref=None,
                    )
                    state_token_time = received_at_ms
                state_values: dict[str, object] = {
                    "session_key": session_key,
                    "account_id": account_id,
                    "sender_ref": sender_ref,
                    "conversation_scope_hash": conversation_scope_hash,
                    "recipient_cipher": recipient_cipher,
                    "recipient_sha256": recipient_sha256,
                    "last_sequence": sequence,
                    "last_received_at_ms": received_at_ms,
                    "last_message_ref": message_ref,
                    "context_cipher": state_cipher,
                    "context_token_sha256": state_token_sha256,
                    "context_updated_at_ms": state_token_time,
                }
                state_sha256 = _state_sha256(dict(state_values))
                self._connection.execute(
                    "INSERT INTO session_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(session_key) DO UPDATE SET account_id=excluded.account_id,"
                    "sender_ref=excluded.sender_ref,conversation_scope_hash=excluded.conversation_scope_hash,"
                    "recipient_cipher=excluded.recipient_cipher,recipient_sha256=excluded.recipient_sha256,"
                    "last_sequence=excluded.last_sequence,last_received_at_ms=excluded.last_received_at_ms,"
                    "last_message_ref=excluded.last_message_ref,context_cipher=excluded.context_cipher,"
                    "context_token_sha256=excluded.context_token_sha256,"
                    "context_updated_at_ms=excluded.context_updated_at_ms,state_sha256=excluded.state_sha256",
                    (*state_values.values(), state_sha256),
                )
            return WechatSessionResult(decision, context_token)

    def resolve_reply_target(
        self,
        *,
        session_key: str,
        account_id: str,
        conversation_scope_hash: str,
    ) -> str:
        if (
            len(session_key) != 64
            or any(char not in "0123456789abcdef" for char in session_key)
            or not account_id
            or len(conversation_scope_hash) != 64
        ):
            raise ValueError("WeChat reply route binding is invalid")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM session_state WHERE session_key = ?", (session_key,)
            ).fetchone()
            if row is None:
                raise WechatSessionConflict("WeChat reply session does not exist")
            values = dict(row)
            stored = str(values.pop("state_sha256"))
            if stored != _state_sha256(values):
                raise WechatSessionError("WeChat session state digest is invalid")
            if (
                row["account_id"] != account_id
                or row["conversation_scope_hash"] != conversation_scope_hash
            ):
                raise WechatSessionConflict("WeChat reply route scope changed")
            return self._unprotect_recipient(
                bytes(row["recipient_cipher"]),
                str(row["recipient_sha256"]),
                session_key=session_key,
            )

    def get_decision(self, message_ref: str) -> WechatSessionDecision | None:
        if not message_ref or len(message_ref) > 160:
            raise ValueError("WeChat message reference is invalid")
        with self._lock:
            row = self._connection.execute(
                "SELECT decision_json,decision_sha256 FROM message_decision WHERE message_ref=?",
                (message_ref,),
            ).fetchone()
        if row is None:
            return None
        decision = WechatSessionDecision.model_validate_json(row["decision_json"], strict=True)
        if not decision.has_valid_sha256() or decision.decision_sha256 != row["decision_sha256"]:
            raise WechatSessionError("WeChat message decision digest is invalid")
        return decision

    def clear_context_token(self, *, session_key: str) -> bool:
        if len(session_key) != 64 or any(
            char not in "0123456789abcdef" for char in session_key
        ):
            raise ValueError("WeChat session key is invalid")
        with self._lock, self._transaction():
            row = self._connection.execute(
                "SELECT * FROM session_state WHERE session_key = ?", (session_key,)
            ).fetchone()
            if row is None or row["context_cipher"] is None:
                return False
            values = dict(row)
            values.pop("state_sha256")
            values["context_cipher"] = None
            values["context_token_sha256"] = None
            values["context_updated_at_ms"] = None
            state_sha256 = _state_sha256(dict(values))
            self._connection.execute(
                "UPDATE session_state SET context_cipher=NULL,context_token_sha256=NULL,"
                "context_updated_at_ms=NULL,state_sha256=? WHERE session_key=?",
                (state_sha256, session_key),
            )
            return True

    def resolve_context_token(
        self,
        *,
        session_key: str,
        account_id: str,
        conversation_scope_hash: str,
    ) -> str | None:
        # Resolve the protected route first so account/session scope cannot be bypassed.
        self.resolve_reply_target(
            session_key=session_key,
            account_id=account_id,
            conversation_scope_hash=conversation_scope_hash,
        )
        with self._lock:
            row = self._connection.execute(
                "SELECT context_cipher,context_token_sha256 FROM session_state "
                "WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            if row is None:
                raise WechatSessionConflict("WeChat reply session does not exist")
            if row["context_cipher"] is None:
                return None
            return self._unprotect(
                bytes(row["context_cipher"]),
                str(row["context_token_sha256"]),
                session_key=session_key,
                message_ref=None,
            )

    def _verify_rows(self, *, verify_tokens: bool) -> None:
        for row in self._connection.execute("SELECT * FROM session_state ORDER BY session_key"):
            values = dict(row)
            stored = str(values.pop("state_sha256"))
            if stored != _state_sha256(values):
                raise WechatSessionError("WeChat session state digest is invalid")
            if verify_tokens and row["context_cipher"] is not None:
                self._unprotect(
                    bytes(row["context_cipher"]),
                    str(row["context_token_sha256"]),
                    session_key=str(row["session_key"]),
                    message_ref=None,
                )
            if verify_tokens:
                self._unprotect_recipient(
                    bytes(row["recipient_cipher"]),
                    str(row["recipient_sha256"]),
                    session_key=str(row["session_key"]),
                )
        for row in self._connection.execute("SELECT * FROM message_decision ORDER BY message_ref"):
            decision = WechatSessionDecision.model_validate_json(row["decision_json"])
            if not decision.has_valid_sha256() or decision.decision_sha256 != row["decision_sha256"]:
                raise WechatSessionError("WeChat message decision digest is invalid")
            if verify_tokens and row["context_cipher"] is not None:
                self._unprotect(
                    bytes(row["context_cipher"]),
                    str(row["context_token_sha256"]),
                    session_key=str(row["session_key"]),
                    message_ref=str(row["message_ref"]),
                )

    def health_check(self, *, now_ms: int, full: bool = False) -> WechatSessionHealth:
        if now_ms < 0 or self._closed:
            return WechatSessionHealth(False, "wechat_session.closed", None, False)
        try:
            if self._connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise WechatSessionError("SQLite integrity check failed")
            if _schema_sha256(self._connection) != expected_wechat_session_schema_sha256():
                raise WechatSessionError("schema mismatch")
            self._verify_rows(verify_tokens=full)
            with self._transaction():
                self._connection.execute("SELECT 1")
            return WechatSessionHealth(
                True,
                "wechat_session.ok",
                expected_wechat_session_schema_sha256(),
                True,
            )
        except Exception:
            return WechatSessionHealth(False, "wechat_session.check.failed", None, False)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True


__all__ = [
    "WechatSessionConflict",
    "WechatSessionDecision",
    "WechatSessionError",
    "WechatSessionHealth",
    "WechatSessionLedger",
    "WechatSessionResult",
    "derive_wechat_session_key",
    "expected_wechat_session_schema_sha256",
]
