"""Durable 7176 delivery-effect ledger with fail-closed ambiguity recovery."""

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
    DeliveryReceipt,
    DeliveryTicketPayload,
    canonical_json_bytes,
    canonical_sha256,
)


DELIVERY_LEDGER_APPLICATION_ID = 0x5447444C
DELIVERY_LEDGER_SCHEMA_VERSION = 1
_MIGRATION_ID = "communication-delivery-ledger-v1"


def derive_channel_client_message_id(effect_id: str) -> str:
    return "tgv3_" + canonical_sha256(
        {"domain": "tiangong.communication.client-message.v1", "effect_id": effect_id}
    )[:48]


class DeliveryEffectClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    ticket_id: str = Field(min_length=1, max_length=160)
    delivery_id: str = Field(pattern=r"^del_[0-9a-f]{64}$")
    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    gateway_epoch: int = Field(ge=1)
    channel: Literal["desktop", "wechat", "feishu", "test"]
    tenant_id: str = Field(min_length=1, max_length=160)
    link_account_id: str = Field(min_length=1, max_length=160)
    conversation_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipient_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outbound_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ticket_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_client_message_id: str = Field(min_length=1, max_length=160)
    claimed_at_ms: int = Field(ge=0)
    claim_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_client_identity(self) -> Self:
        if self.channel_client_message_id != derive_channel_client_message_id(self.effect_id):
            raise ValueError("channel client message ID is not derived from effect ID")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"claim_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.claim_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"claim_sha256": self.computed_sha256()})


class DeliveryPartStageFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    part_id: str = Field(min_length=1, max_length=160)
    part_index: int = Field(ge=0, le=999)
    kind: Literal["text", "artifact"]
    stage: Literal[
        "FETCHED",
        "READY_TO_UPLOAD",
        "ENCRYPTED",
        "UPLOAD_URL_GRANTED",
        "UPLOADED",
        "SEND_STARTED",
        "CHANNEL_ACCEPTED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "AMBIGUOUS",
    ]
    attempt: int = Field(ge=1, le=1_000)
    occurred_at_ms: int = Field(ge=0)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage_fact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"stage_fact_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.stage_fact_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"stage_fact_sha256": self.computed_sha256()})


class DeliveryTransferProgressFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    part_id: str = Field(min_length=1, max_length=160)
    part_index: int = Field(ge=0, le=999)
    phase: Literal["FETCH", "ENCRYPT", "UPLOAD"]
    bytes_completed: int = Field(ge=1, le=2_147_483_648)
    total_bytes: int = Field(ge=1, le=2_147_483_648)
    observed_at_ms: int = Field(ge=0)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    progress_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if self.bytes_completed > self.total_bytes:
            raise ValueError("delivery transfer progress exceeds total bytes")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"progress_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.progress_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"progress_sha256": self.computed_sha256()})


class VerifiedDeliveryTicketFact(BaseModel):
    """Machine fact proving that 7176 verified one complete DeliveryTicket."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["tiangong.gateway.contracts.v1", "tiangong.gateway.contracts.v2"] = CONTRACT_SCHEMA_VERSION
    ticket_id: str = Field(min_length=1, max_length=160)
    kid: str = Field(min_length=1, max_length=160)
    issuer: Literal["tiangong-total-gateway"] = "tiangong-total-gateway"
    audience: Literal["tiangong-communication-service"] = "tiangong-communication-service"
    gateway_epoch: int = Field(ge=1)
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    delivery_id: str = Field(pattern=r"^del_[0-9a-f]{64}$")
    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    outbound_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trust_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    component_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_verification_time(self) -> Self:
        if self.verified_at_ms > self.expires_at_ms:
            raise ValueError("delivery ticket verification occurred after expiry")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"verification_sha256"})
        )

    def has_valid_sha256(self) -> bool:
        return self.verification_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(
            update={"verification_sha256": self.computed_sha256()}
        )


@dataclass(frozen=True)
class DeliveryLedgerRecord:
    claim: DeliveryEffectClaim
    state: str
    side_effect_started_at_ms: int | None
    receipt: DeliveryReceipt | None
    reconcile_reason_code: str | None
    updated_at_ms: int


@dataclass(frozen=True)
class DeliveryLedgerHealth:
    healthy: bool
    reason_code: str
    checked_at_ms: int
    schema_sha256: str | None
    writable: bool


@dataclass(frozen=True)
class DeliveryDrainFacts:
    channel: str
    tenant_id: str
    link_account_id: str
    inflight_send_count: int
    unresolved_delivery_count: int
    ledger_sha256: str


@dataclass(frozen=True)
class VerifiedDeliveryConsumption:
    verification: VerifiedDeliveryTicketFact
    delivery: DeliveryLedgerRecord
    created: bool


class DeliveryLedgerError(RuntimeError):
    pass


class DeliveryLedgerConflict(DeliveryLedgerError):
    pass


class DeliveryLedgerCorruption(DeliveryLedgerError):
    pass


class DeliveryLedgerMigrationError(DeliveryLedgerError):
    pass


_STATEMENTS = (
    """
    CREATE TABLE delivery_migrations (
        version INTEGER PRIMARY KEY,
        migration_id TEXT NOT NULL UNIQUE,
        migration_sha256 TEXT NOT NULL,
        applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE verified_delivery_tickets (
        ticket_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL UNIQUE,
        delivery_id TEXT NOT NULL UNIQUE,
        kid TEXT NOT NULL,
        issuer TEXT NOT NULL,
        audience TEXT NOT NULL,
        gateway_epoch INTEGER NOT NULL CHECK (gateway_epoch >= 1),
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        outbound_plan_sha256 TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        signature_sha256 TEXT NOT NULL,
        trust_bundle_sha256 TEXT NOT NULL,
        component_manifest_sha256 TEXT NOT NULL,
        verified_at_ms INTEGER NOT NULL CHECK (verified_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms >= verified_at_ms),
        verification_json TEXT NOT NULL CHECK (json_valid(verification_json)),
        verification_sha256 TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE INDEX verified_delivery_generation_fence
    ON verified_delivery_tickets(request_id, run_id, generation)
    """,
    """
    CREATE TABLE delivery_effects (
        effect_id TEXT PRIMARY KEY,
        delivery_id TEXT NOT NULL UNIQUE,
        ticket_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        channel TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        link_account_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN (
            'CLAIMED','SIDE_EFFECT_STARTED','CHANNEL_ACCEPTED','DELIVERED',
            'FAILED_RETRYABLE','FAILED_FINAL','RECONCILE_REQUIRED','RECONCILED'
        )),
        claimed_at_ms INTEGER NOT NULL CHECK (claimed_at_ms >= 0),
        side_effect_started_at_ms INTEGER,
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= claimed_at_ms),
        claim_json TEXT NOT NULL CHECK (json_valid(claim_json)),
        claim_sha256 TEXT NOT NULL,
        receipt_json TEXT CHECK (receipt_json IS NULL OR json_valid(receipt_json)),
        receipt_sha256 TEXT,
        reconcile_reason_code TEXT,
        CHECK ((receipt_json IS NULL) = (receipt_sha256 IS NULL)),
        CHECK (
            (state = 'CLAIMED' AND side_effect_started_at_ms IS NULL AND receipt_json IS NULL AND reconcile_reason_code IS NULL)
            OR (state = 'SIDE_EFFECT_STARTED' AND side_effect_started_at_ms IS NOT NULL AND receipt_json IS NULL AND reconcile_reason_code IS NULL)
            OR (state = 'RECONCILE_REQUIRED' AND side_effect_started_at_ms IS NOT NULL AND reconcile_reason_code IS NOT NULL)
            OR (state IN ('CHANNEL_ACCEPTED','DELIVERED','FAILED_RETRYABLE','FAILED_FINAL','RECONCILED') AND receipt_json IS NOT NULL)
        )
    ) STRICT
    """,
    """
    CREATE TABLE delivery_stage_events (
        sequence INTEGER PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        effect_id TEXT NOT NULL,
        from_state TEXT,
        to_state TEXT NOT NULL,
        occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
        evidence_sha256 TEXT NOT NULL,
        FOREIGN KEY (effect_id) REFERENCES delivery_effects(effect_id)
    ) STRICT
    """,
    """
    CREATE TABLE delivery_part_stage_facts (
        sequence INTEGER PRIMARY KEY,
        stage_fact_id TEXT NOT NULL UNIQUE,
        effect_id TEXT NOT NULL,
        part_id TEXT NOT NULL,
        part_index INTEGER NOT NULL CHECK (part_index >= 0),
        kind TEXT NOT NULL CHECK (kind IN ('text','artifact')),
        stage TEXT NOT NULL CHECK (stage IN (
            'FETCHED','READY_TO_UPLOAD','ENCRYPTED','UPLOAD_URL_GRANTED','UPLOADED','SEND_STARTED','CHANNEL_ACCEPTED',
            'FAILED_RETRYABLE','FAILED_FINAL','AMBIGUOUS'
        )),
        attempt INTEGER NOT NULL CHECK (attempt >= 1),
        occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
        evidence_sha256 TEXT NOT NULL,
        stage_fact_json TEXT NOT NULL CHECK (json_valid(stage_fact_json)),
        stage_fact_sha256 TEXT NOT NULL,
        UNIQUE(effect_id, part_id, stage, attempt),
        FOREIGN KEY (effect_id) REFERENCES delivery_effects(effect_id)
    ) STRICT
    """,
    """
    CREATE INDEX delivery_part_stage_order
    ON delivery_part_stage_facts(effect_id, part_index, sequence)
    """,
    """
    CREATE TABLE delivery_transfer_progress_facts (
        sequence INTEGER PRIMARY KEY,
        progress_id TEXT NOT NULL UNIQUE,
        effect_id TEXT NOT NULL,
        part_id TEXT NOT NULL,
        part_index INTEGER NOT NULL CHECK (part_index >= 0),
        phase TEXT NOT NULL CHECK (phase IN ('FETCH','ENCRYPT','UPLOAD')),
        bytes_completed INTEGER NOT NULL CHECK (bytes_completed >= 1),
        total_bytes INTEGER NOT NULL CHECK (total_bytes >= bytes_completed),
        observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
        evidence_sha256 TEXT NOT NULL,
        progress_json TEXT NOT NULL CHECK (json_valid(progress_json)),
        progress_sha256 TEXT NOT NULL,
        UNIQUE(effect_id, part_id, phase, bytes_completed),
        FOREIGN KEY (effect_id) REFERENCES delivery_effects(effect_id)
    ) STRICT
    """,
    """
    CREATE INDEX delivery_transfer_progress_order
    ON delivery_transfer_progress_facts(effect_id, part_index, phase, bytes_completed)
    """,
    """
    CREATE INDEX delivery_reconcile_queue
    ON delivery_effects(state, updated_at_ms, effect_id)
    """,
)
_MIGRATION_SHA256 = canonical_sha256(
    {"version": DELIVERY_LEDGER_SCHEMA_VERSION, "migration_id": _MIGRATION_ID, "statements": _STATEMENTS}
)


def _schema_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    return canonical_sha256(
        tuple({"type": row[0], "name": row[1], "table": row[2], "sql": row[3]} for row in rows)
    )


@lru_cache(maxsize=1)
def expected_delivery_ledger_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for statement in _STATEMENTS:
            connection.execute(statement)
        return _schema_sha256(connection)
    finally:
        connection.close()


def _canonical_model(value: BaseModel) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _event_id(effect_id: str, to_state: str, evidence_sha256: str) -> str:
    return "dle_" + canonical_sha256(
        {
            "domain": "tiangong.communication.delivery-stage-event.v1",
            "effect_id": effect_id,
            "to_state": to_state,
            "evidence_sha256": evidence_sha256,
        }
    )


def _part_stage_fact_id(fact: DeliveryPartStageFact) -> str:
    return "dpf_" + canonical_sha256(
        {
            "domain": "tiangong.communication.delivery-part-stage.v1",
            "effect_id": fact.effect_id,
            "part_id": fact.part_id,
            "stage": fact.stage,
            "attempt": fact.attempt,
            "stage_fact_sha256": fact.stage_fact_sha256,
        }
    )


def _progress_fact_id(fact: DeliveryTransferProgressFact) -> str:
    return "dpg_" + canonical_sha256(
        {
            "domain": "tiangong.communication.delivery-progress.v1",
            "effect_id": fact.effect_id,
            "part_id": fact.part_id,
            "phase": fact.phase,
            "bytes_completed": fact.bytes_completed,
            "progress_sha256": fact.progress_sha256,
        }
    )


_PART_STAGE_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"FETCHED", "SEND_STARTED", "FAILED_RETRYABLE", "FAILED_FINAL"}),
    "FETCHED": frozenset(
        {"READY_TO_UPLOAD", "ENCRYPTED", "FAILED_RETRYABLE", "FAILED_FINAL"}
    ),
    "READY_TO_UPLOAD": frozenset(
        {"UPLOADED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS"}
    ),
    "ENCRYPTED": frozenset({"UPLOAD_URL_GRANTED", "FAILED_RETRYABLE", "FAILED_FINAL"}),
    "UPLOAD_URL_GRANTED": frozenset({"UPLOADED", "FAILED_RETRYABLE", "FAILED_FINAL"}),
    "UPLOADED": frozenset({"SEND_STARTED", "FAILED_RETRYABLE", "FAILED_FINAL"}),
    "SEND_STARTED": frozenset(
        {"CHANNEL_ACCEPTED", "FAILED_RETRYABLE", "FAILED_FINAL", "AMBIGUOUS"}
    ),
}


class DeliveryLedger:
    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, now_ms: int) -> "DeliveryLedger":
        if now_ms < 0 or not path.is_absolute():
            raise ValueError("delivery ledger path or time is invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise DeliveryLedgerCorruption("delivery ledger path is not a regular file")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(path, isolation_level=None, timeout=5, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = FULL")
            if str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() != "wal":
                raise DeliveryLedgerMigrationError("delivery ledger could not enable WAL")
            cls._migrate(connection, now_ms)
            ledger = cls(path, connection)
            if not ledger.health_check(now_ms=now_ms, full=True).healthy:
                raise DeliveryLedgerCorruption("delivery ledger failed initial health check")
            os.chmod(path, 0o600)
            return ledger
        except (sqlite3.DatabaseError, OSError, DeliveryLedgerError) as exc:
            if "connection" in locals():
                connection.close()
            if isinstance(exc, DeliveryLedgerError):
                raise
            raise DeliveryLedgerCorruption("delivery ledger could not be opened safely") from exc

    @staticmethod
    def _migrate(connection: sqlite3.Connection, now_ms: int) -> None:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if application_id not in {0, DELIVERY_LEDGER_APPLICATION_ID} or version > DELIVERY_LEDGER_SCHEMA_VERSION:
            raise DeliveryLedgerMigrationError("delivery ledger metadata is incompatible")
        if version == 0:
            if objects:
                raise DeliveryLedgerMigrationError("unversioned delivery ledger is not empty")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                for statement in _STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO delivery_migrations VALUES (?, ?, ?, ?)",
                    (DELIVERY_LEDGER_SCHEMA_VERSION, _MIGRATION_ID, _MIGRATION_SHA256, now_ms),
                )
                connection.execute(f"PRAGMA application_id = {DELIVERY_LEDGER_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {DELIVERY_LEDGER_SCHEMA_VERSION}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        DeliveryLedger._validate_schema(connection)

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA application_id").fetchone()[0] != DELIVERY_LEDGER_APPLICATION_ID:
            raise DeliveryLedgerMigrationError("delivery ledger application ID is invalid")
        if connection.execute("PRAGMA user_version").fetchone()[0] != DELIVERY_LEDGER_SCHEMA_VERSION:
            raise DeliveryLedgerMigrationError("delivery ledger schema version is invalid")
        row = connection.execute("SELECT * FROM delivery_migrations WHERE version = 1").fetchone()
        if row is None or row["migration_id"] != _MIGRATION_ID or row["migration_sha256"] != _MIGRATION_SHA256:
            raise DeliveryLedgerMigrationError("delivery ledger migration record is invalid")
        if _schema_sha256(connection) != expected_delivery_ledger_schema_sha256():
            raise DeliveryLedgerMigrationError("delivery ledger schema fingerprint is invalid")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise DeliveryLedgerError("delivery ledger is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    @staticmethod
    def claim_from_payload(payload: DeliveryTicketPayload, *, claimed_at_ms: int) -> DeliveryEffectClaim:
        if claimed_at_ms < payload.not_before_ms:
            raise ValueError("delivery claim predates ticket validity")
        data = payload.model_dump(mode="json")
        return DeliveryEffectClaim(
            ticket_id=payload.ticket_id,
            delivery_id=payload.delivery_id,
            effect_id=payload.effect_id,
            request_id=payload.request_id,
            run_id=payload.run_id,
            generation=payload.generation,
            gateway_epoch=payload.gateway_epoch,
            channel=payload.channel,
            tenant_id=payload.tenant_id,
            link_account_id=payload.link_account_id,
            conversation_scope_hash=payload.conversation_scope_hash,
            recipient_scope_hash=payload.recipient_scope_hash,
            outbound_plan_sha256=payload.outbound_plan_sha256,
            ticket_payload_sha256=canonical_sha256(data),
            channel_client_message_id=derive_channel_client_message_id(payload.effect_id),
            claimed_at_ms=claimed_at_ms,
            claim_sha256="0" * 64,
        ).with_computed_sha256()

    def _claim_locked(
        self,
        claim: DeliveryEffectClaim,
        claim_json: str,
        claim_digest: str,
    ) -> tuple[DeliveryLedgerRecord, bool]:
        rows = self._connection.execute(
            "SELECT * FROM delivery_effects WHERE effect_id = ? OR delivery_id = ? OR ticket_id = ?",
            (claim.effect_id, claim.delivery_id, claim.ticket_id),
        ).fetchall()
        if rows:
            if len(rows) != 1:
                raise DeliveryLedgerCorruption("delivery identities point to different effects")
            record = self._record(rows[0])
            stable_excludes = {"claimed_at_ms", "claim_sha256"}
            if record.claim.model_dump(exclude=stable_excludes) != claim.model_dump(
                exclude=stable_excludes
            ):
                raise DeliveryLedgerConflict(
                    "delivery identity was reused with different content"
                )
            return record, False
        self._connection.execute(
            """
            INSERT INTO delivery_effects(
                effect_id, delivery_id, ticket_id, request_id, run_id, generation,
                channel, tenant_id, link_account_id, state, claimed_at_ms,
                side_effect_started_at_ms, updated_at_ms, claim_json, claim_sha256,
                receipt_json, receipt_sha256, reconcile_reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CLAIMED', ?, NULL, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                claim.effect_id, claim.delivery_id, claim.ticket_id, claim.request_id,
                claim.run_id, claim.generation, claim.channel, claim.tenant_id,
                claim.link_account_id, claim.claimed_at_ms, claim.claimed_at_ms,
                claim_json, claim_digest,
            ),
        )
        self._append_event(
            claim.effect_id,
            None,
            "CLAIMED",
            claim.claim_sha256,
            claim.claimed_at_ms,
        )
        row = self._connection.execute(
            "SELECT * FROM delivery_effects WHERE effect_id = ?", (claim.effect_id,)
        ).fetchone()
        return self._record(row), True

    @staticmethod
    def _verification(row: sqlite3.Row) -> VerifiedDeliveryTicketFact:
        try:
            fact = VerifiedDeliveryTicketFact.model_validate_json(
                row["verification_json"], strict=True
            )
        except ValueError as exc:
            raise DeliveryLedgerCorruption(
                "verified delivery ticket payload is invalid"
            ) from exc
        fact_json, fact_digest = _canonical_model(fact)
        columns = {
            "ticket_id": fact.ticket_id,
            "effect_id": fact.effect_id,
            "delivery_id": fact.delivery_id,
            "kid": fact.kid,
            "issuer": fact.issuer,
            "audience": fact.audience,
            "gateway_epoch": fact.gateway_epoch,
            "request_id": fact.request_id,
            "run_id": fact.run_id,
            "generation": fact.generation,
            "outbound_plan_sha256": fact.outbound_plan_sha256,
            "payload_sha256": fact.payload_sha256,
            "signature_sha256": fact.signature_sha256,
            "trust_bundle_sha256": fact.trust_bundle_sha256,
            "component_manifest_sha256": fact.component_manifest_sha256,
            "verified_at_ms": fact.verified_at_ms,
            "expires_at_ms": fact.expires_at_ms,
        }
        if (
            not fact.has_valid_sha256()
            or fact_json != row["verification_json"]
            or fact_digest != row["verification_sha256"]
            or any(row[name] != value for name, value in columns.items())
        ):
            raise DeliveryLedgerCorruption(
                "verified delivery ticket columns are invalid"
            )
        return fact

    def consume_verified_ticket(
        self,
        verification: VerifiedDeliveryTicketFact,
        claim: DeliveryEffectClaim,
    ) -> VerifiedDeliveryConsumption:
        """Persist verification and CLAIMED atomically before any channel side effect."""

        if not verification.has_valid_sha256():
            raise ValueError("delivery ticket verification digest is invalid")
        if not claim.has_valid_sha256():
            raise ValueError("delivery claim digest is invalid")
        exact = (
            verification.ticket_id == claim.ticket_id
            and verification.delivery_id == claim.delivery_id
            and verification.effect_id == claim.effect_id
            and verification.request_id == claim.request_id
            and verification.run_id == claim.run_id
            and verification.generation == claim.generation
            and verification.gateway_epoch == claim.gateway_epoch
            and verification.outbound_plan_sha256 == claim.outbound_plan_sha256
            and verification.payload_sha256 == claim.ticket_payload_sha256
            and verification.verified_at_ms == claim.claimed_at_ms
        )
        if not exact:
            raise DeliveryLedgerConflict(
                "verified delivery ticket does not match its effect claim"
            )
        verification_json, verification_digest = _canonical_model(verification)
        claim_json, claim_digest = _canonical_model(claim)
        with self._lock, self._transaction():
            maximum = self._connection.execute(
                "SELECT MAX(generation) FROM verified_delivery_tickets "
                "WHERE request_id=? AND run_id=?",
                (verification.request_id, verification.run_id),
            ).fetchone()[0]
            if maximum is not None and verification.generation < int(maximum):
                raise DeliveryLedgerConflict(
                    "delivery ticket generation is below the persisted fence"
                )
            rows = self._connection.execute(
                "SELECT * FROM verified_delivery_tickets "
                "WHERE ticket_id=? OR effect_id=? OR delivery_id=?",
                (
                    verification.ticket_id,
                    verification.effect_id,
                    verification.delivery_id,
                ),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise DeliveryLedgerCorruption(
                        "verified delivery identities point to different tickets"
                    )
                stored = self._verification(rows[0])
                stable_excludes = {"verified_at_ms", "verification_sha256"}
                if stored.model_dump(exclude=stable_excludes) != verification.model_dump(
                    exclude=stable_excludes
                ):
                    raise DeliveryLedgerConflict(
                        "delivery ticket identity was reused with different verification"
                    )
                record, _ = self._claim_locked(claim, claim_json, claim_digest)
                return VerifiedDeliveryConsumption(stored, record, False)
            self._connection.execute(
                """
                INSERT INTO verified_delivery_tickets(
                    ticket_id,effect_id,delivery_id,kid,issuer,audience,gateway_epoch,
                    request_id,run_id,generation,outbound_plan_sha256,payload_sha256,
                    signature_sha256,trust_bundle_sha256,component_manifest_sha256,
                    verified_at_ms,expires_at_ms,verification_json,verification_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    verification.ticket_id,
                    verification.effect_id,
                    verification.delivery_id,
                    verification.kid,
                    verification.issuer,
                    verification.audience,
                    verification.gateway_epoch,
                    verification.request_id,
                    verification.run_id,
                    verification.generation,
                    verification.outbound_plan_sha256,
                    verification.payload_sha256,
                    verification.signature_sha256,
                    verification.trust_bundle_sha256,
                    verification.component_manifest_sha256,
                    verification.verified_at_ms,
                    verification.expires_at_ms,
                    verification_json,
                    verification_digest,
                ),
            )
            record, _ = self._claim_locked(claim, claim_json, claim_digest)
            return VerifiedDeliveryConsumption(verification, record, True)

    def get_verified_ticket(
        self, ticket_id: str
    ) -> VerifiedDeliveryTicketFact | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM verified_delivery_tickets WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
            return None if row is None else self._verification(row)

    def require_verified_delivery(
        self, payload: DeliveryTicketPayload
    ) -> DeliveryLedgerRecord:
        """Resolve an already verified/claimed payload; never creates authority."""

        payload_sha256 = canonical_sha256(payload.model_dump(mode="json"))
        with self._lock:
            verification_row = self._connection.execute(
                "SELECT * FROM verified_delivery_tickets "
                "WHERE ticket_id=? OR effect_id=? OR delivery_id=?",
                (payload.ticket_id, payload.effect_id, payload.delivery_id),
            ).fetchall()
            if len(verification_row) != 1:
                raise DeliveryLedgerConflict(
                    "delivery payload has no unique verified ticket"
                )
            verification = self._verification(verification_row[0])
            row = self._connection.execute(
                "SELECT * FROM delivery_effects WHERE effect_id=?",
                (payload.effect_id,),
            ).fetchone()
            if row is None:
                raise DeliveryLedgerCorruption(
                    "verified delivery ticket has no effect claim"
                )
            record = self._record(row)
            claim = record.claim
            exact = (
                verification.ticket_id == payload.ticket_id == claim.ticket_id
                and verification.delivery_id == payload.delivery_id == claim.delivery_id
                and verification.effect_id == payload.effect_id == claim.effect_id
                and verification.request_id == payload.request_id == claim.request_id
                and verification.run_id == payload.run_id == claim.run_id
                and verification.generation == payload.generation == claim.generation
                and verification.gateway_epoch
                == payload.gateway_epoch
                == claim.gateway_epoch
                and verification.outbound_plan_sha256
                == payload.outbound_plan_sha256
                == claim.outbound_plan_sha256
                and verification.payload_sha256
                == payload_sha256
                == claim.ticket_payload_sha256
                and payload.channel == claim.channel
                and payload.tenant_id == claim.tenant_id
                and payload.link_account_id == claim.link_account_id
                and payload.conversation_scope_hash == claim.conversation_scope_hash
                and payload.recipient_scope_hash == claim.recipient_scope_hash
            )
            if not exact:
                raise DeliveryLedgerConflict(
                    "delivery payload does not match verified ticket authority"
                )
            return record

    def _append_event(
        self, effect_id: str, from_state: str | None, to_state: str, evidence_sha256: str, at_ms: int
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO delivery_stage_events(event_id, effect_id, from_state, to_state, occurred_at_ms, evidence_sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_event_id(effect_id, to_state, evidence_sha256), effect_id, from_state, to_state, at_ms, evidence_sha256),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> DeliveryLedgerRecord:
        try:
            claim = DeliveryEffectClaim.model_validate_json(row["claim_json"], strict=True)
            receipt = (
                None
                if row["receipt_json"] is None
                else DeliveryReceipt.model_validate_json(row["receipt_json"], strict=True)
            )
        except ValueError as exc:
            raise DeliveryLedgerCorruption("delivery ledger payload is invalid") from exc
        claim_json, claim_digest = _canonical_model(claim)
        if claim_json != row["claim_json"] or claim_digest != row["claim_sha256"] or not claim.has_valid_sha256():
            raise DeliveryLedgerCorruption("delivery claim digest is invalid")
        if receipt is not None:
            receipt_json, receipt_digest = _canonical_model(receipt)
            if (
                receipt_json != row["receipt_json"]
                or receipt_digest != row["receipt_sha256"]
                or not receipt.has_valid_receipt_sha256()
            ):
                raise DeliveryLedgerCorruption("delivery receipt digest is invalid")
        return DeliveryLedgerRecord(
            claim, row["state"], row["side_effect_started_at_ms"], receipt,
            row["reconcile_reason_code"], row["updated_at_ms"],
        )

    def get(self, effect_id: str) -> DeliveryLedgerRecord | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM delivery_effects WHERE effect_id = ?", (effect_id,)).fetchone()
            return None if row is None else self._record(row)

    def mark_side_effect_started(self, effect_id: str, *, started_at_ms: int) -> DeliveryLedgerRecord:
        with self._lock, self._transaction():
            row = self._connection.execute("SELECT * FROM delivery_effects WHERE effect_id = ?", (effect_id,)).fetchone()
            if row is None:
                raise DeliveryLedgerConflict("delivery effect is not claimed")
            if row["state"] == "SIDE_EFFECT_STARTED":
                if row["side_effect_started_at_ms"] != started_at_ms:
                    raise DeliveryLedgerConflict("side-effect start fact changed")
                return self._record(row)
            if row["state"] != "CLAIMED" or started_at_ms < row["claimed_at_ms"]:
                raise DeliveryLedgerConflict("delivery effect cannot enter side-effect boundary")
            evidence = canonical_sha256({"effect_id": effect_id, "started_at_ms": started_at_ms})
            self._connection.execute(
                """
                UPDATE delivery_effects SET state = 'SIDE_EFFECT_STARTED', side_effect_started_at_ms = ?, updated_at_ms = ?
                WHERE effect_id = ? AND state = 'CLAIMED'
                """,
                (started_at_ms, started_at_ms, effect_id),
            )
            self._append_event(effect_id, "CLAIMED", "SIDE_EFFECT_STARTED", evidence, started_at_ms)
            return self._record(self._connection.execute("SELECT * FROM delivery_effects WHERE effect_id = ?", (effect_id,)).fetchone())

    def record_receipt(
        self,
        receipt: DeliveryReceipt,
        *,
        side_effect_absent_verified: bool = False,
        reconciliation: bool = False,
    ) -> DeliveryLedgerRecord:
        if not receipt.has_valid_receipt_sha256():
            raise ValueError("delivery receipt digest is invalid")
        receipt_json, receipt_digest = _canonical_model(receipt)
        with self._lock, self._transaction():
            row = self._connection.execute("SELECT * FROM delivery_effects WHERE effect_id = ?", (receipt.effect_id,)).fetchone()
            if row is None:
                raise DeliveryLedgerConflict("delivery receipt has no claimed effect")
            record = self._record(row)
            exact = (
                receipt.delivery_id == record.claim.delivery_id
                and receipt.request_id == record.claim.request_id
                and receipt.run_id == record.claim.run_id
                and receipt.generation == record.claim.generation
                and receipt.channel == record.claim.channel
                and receipt.ticket_id == record.claim.ticket_id
            )
            if not exact:
                raise DeliveryLedgerConflict("delivery receipt context does not match claimed effect")
            if record.receipt is not None:
                if record.receipt == receipt:
                    return record
                if record.state != "RECONCILE_REQUIRED" or not reconciliation:
                    raise DeliveryLedgerConflict("delivery effect already has a different receipt")
            if record.state == "RECONCILE_REQUIRED" and not reconciliation:
                raise DeliveryLedgerConflict("ambiguous delivery requires explicit reconciliation")
            if receipt.status in {"CHANNEL_ACCEPTED", "DELIVERED", "AMBIGUOUS", "RECONCILE_REQUIRED"} and record.side_effect_started_at_ms is None:
                raise DeliveryLedgerConflict("platform result requires a persisted side-effect start")
            if record.state == "SIDE_EFFECT_STARTED" and receipt.status == "FAILED_RETRYABLE" and not side_effect_absent_verified:
                raise DeliveryLedgerConflict("started side effect cannot be retried without verified absence")
            if record.state not in {"CLAIMED", "SIDE_EFFECT_STARTED", "RECONCILE_REQUIRED"}:
                raise DeliveryLedgerConflict("delivery effect is not awaiting a receipt")
            target = "RECONCILED" if record.state == "RECONCILE_REQUIRED" else receipt.status
            if target in {"AMBIGUOUS", "RECONCILE_REQUIRED"}:
                target = "RECONCILE_REQUIRED"
            self._connection.execute(
                """
                UPDATE delivery_effects
                SET state = ?, updated_at_ms = ?, receipt_json = ?, receipt_sha256 = ?,
                    reconcile_reason_code = CASE WHEN ? = 'RECONCILE_REQUIRED' THEN ? ELSE NULL END
                WHERE effect_id = ?
                """,
                (
                    target, receipt.observed_at_ms, receipt_json, receipt_digest,
                    target, receipt.error_code, receipt.effect_id,
                ),
            )
            self._append_event(
                receipt.effect_id, record.state, target, receipt.receipt_sha256, receipt.observed_at_ms
            )
            return self._record(self._connection.execute("SELECT * FROM delivery_effects WHERE effect_id = ?", (receipt.effect_id,)).fetchone())

    def recover_ambiguous(self, *, now_ms: int) -> tuple[DeliveryLedgerRecord, ...]:
        with self._lock, self._transaction():
            rows = self._connection.execute(
                "SELECT * FROM delivery_effects WHERE state = 'SIDE_EFFECT_STARTED' ORDER BY effect_id"
            ).fetchall()
            recovered = []
            for row in rows:
                if now_ms < row["side_effect_started_at_ms"]:
                    raise ValueError("ambiguity recovery time predates side-effect start")
                evidence = canonical_sha256(
                    {"effect_id": row["effect_id"], "reason": "receipt_missing_after_restart"}
                )
                self._connection.execute(
                    """
                    UPDATE delivery_effects
                    SET state = 'RECONCILE_REQUIRED', reconcile_reason_code = ?, updated_at_ms = ?
                    WHERE effect_id = ? AND state = 'SIDE_EFFECT_STARTED'
                    """,
                    ("delivery.receipt_missing_after_restart", now_ms, row["effect_id"]),
                )
                self._append_event(
                    row["effect_id"], "SIDE_EFFECT_STARTED", "RECONCILE_REQUIRED", evidence, now_ms
                )
                recovered.append(
                    self._record(
                        self._connection.execute(
                            "SELECT * FROM delivery_effects WHERE effect_id = ?", (row["effect_id"],)
                        ).fetchone()
                    )
                )
            return tuple(recovered)

    def list_reconcile_required(self) -> tuple[DeliveryLedgerRecord, ...]:
        with self._lock:
            return tuple(
                self._record(row)
                for row in self._connection.execute(
                    "SELECT * FROM delivery_effects WHERE state = 'RECONCILE_REQUIRED' ORDER BY updated_at_ms, effect_id"
                ).fetchall()
            )

    def record_part_stage(self, fact: DeliveryPartStageFact) -> DeliveryPartStageFact:
        if not fact.has_valid_sha256():
            raise ValueError("delivery part-stage fact digest is invalid")
        fact_json, fact_digest = _canonical_model(fact)
        fact_id = _part_stage_fact_id(fact)
        with self._lock, self._transaction():
            effect = self._connection.execute(
                "SELECT claimed_at_ms FROM delivery_effects WHERE effect_id = ?",
                (fact.effect_id,),
            ).fetchone()
            if effect is None:
                raise DeliveryLedgerConflict("delivery part stage has no claimed effect")
            if fact.occurred_at_ms < effect["claimed_at_ms"]:
                raise DeliveryLedgerConflict("delivery part stage predates its effect claim")
            duplicate = self._connection.execute(
                "SELECT * FROM delivery_part_stage_facts WHERE effect_id=? AND part_id=? "
                "AND stage=? AND attempt=?",
                (fact.effect_id, fact.part_id, fact.stage, fact.attempt),
            ).fetchone()
            if duplicate is not None:
                stored = DeliveryPartStageFact.model_validate_json(
                    duplicate["stage_fact_json"], strict=True
                )
                if stored != fact or duplicate["stage_fact_id"] != fact_id:
                    raise DeliveryLedgerConflict("delivery part stage was rebound")
                return stored
            previous = self._connection.execute(
                "SELECT * FROM delivery_part_stage_facts WHERE effect_id=? AND part_id=? "
                "ORDER BY sequence DESC LIMIT 1",
                (fact.effect_id, fact.part_id),
            ).fetchone()
            previous_stage = None if previous is None else str(previous["stage"])
            if previous is not None and (
                int(previous["part_index"]) != fact.part_index
                or previous["kind"] != fact.kind
                or fact.occurred_at_ms < int(previous["occurred_at_ms"])
            ):
                raise DeliveryLedgerConflict("delivery part stage context changed")
            if fact.stage not in _PART_STAGE_TRANSITIONS.get(previous_stage, frozenset()):
                raise DeliveryLedgerConflict("delivery part stage transition is invalid")
            self._connection.execute(
                "INSERT INTO delivery_part_stage_facts("
                "stage_fact_id,effect_id,part_id,part_index,kind,stage,attempt,occurred_at_ms,"
                "evidence_sha256,stage_fact_json,stage_fact_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fact_id,
                    fact.effect_id,
                    fact.part_id,
                    fact.part_index,
                    fact.kind,
                    fact.stage,
                    fact.attempt,
                    fact.occurred_at_ms,
                    fact.evidence_sha256,
                    fact_json,
                    fact_digest,
                ),
            )
            return fact

    def list_part_stages(self, effect_id: str) -> tuple[DeliveryPartStageFact, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM delivery_part_stage_facts WHERE effect_id=? "
                "ORDER BY part_index,sequence",
                (effect_id,),
            ).fetchall()
            result = []
            for row in rows:
                fact = DeliveryPartStageFact.model_validate_json(
                    row["stage_fact_json"], strict=True
                )
                fact_json, fact_digest = _canonical_model(fact)
                if (
                    not fact.has_valid_sha256()
                    or fact_json != row["stage_fact_json"]
                    or fact_digest != row["stage_fact_sha256"]
                    or row["stage_fact_id"] != _part_stage_fact_id(fact)
                ):
                    raise DeliveryLedgerCorruption("delivery part-stage fact is invalid")
                result.append(fact)
            return tuple(result)

    def record_transfer_progress(
        self, fact: DeliveryTransferProgressFact
    ) -> DeliveryTransferProgressFact:
        if not fact.has_valid_sha256():
            raise ValueError("delivery transfer progress digest is invalid")
        fact_json, fact_digest = _canonical_model(fact)
        progress_id = _progress_fact_id(fact)
        with self._lock, self._transaction():
            effect = self._connection.execute(
                "SELECT claimed_at_ms FROM delivery_effects WHERE effect_id=?",
                (fact.effect_id,),
            ).fetchone()
            if effect is None or fact.observed_at_ms < effect["claimed_at_ms"]:
                raise DeliveryLedgerConflict("delivery progress has no valid claimed effect")
            duplicate = self._connection.execute(
                "SELECT * FROM delivery_transfer_progress_facts WHERE effect_id=? "
                "AND part_id=? AND phase=? AND bytes_completed=?",
                (fact.effect_id, fact.part_id, fact.phase, fact.bytes_completed),
            ).fetchone()
            if duplicate is not None:
                stored = DeliveryTransferProgressFact.model_validate_json(
                    duplicate["progress_json"], strict=True
                )
                if stored != fact or duplicate["progress_id"] != progress_id:
                    raise DeliveryLedgerConflict("delivery transfer progress was rebound")
                return stored
            previous = self._connection.execute(
                "SELECT * FROM delivery_transfer_progress_facts WHERE effect_id=? "
                "AND part_id=? AND phase=? ORDER BY bytes_completed DESC LIMIT 1",
                (fact.effect_id, fact.part_id, fact.phase),
            ).fetchone()
            if previous is not None and (
                previous["part_index"] != fact.part_index
                or previous["total_bytes"] != fact.total_bytes
                or previous["bytes_completed"] >= fact.bytes_completed
                or previous["observed_at_ms"] > fact.observed_at_ms
            ):
                raise DeliveryLedgerConflict("delivery transfer progress is not monotonic")
            self._connection.execute(
                "INSERT INTO delivery_transfer_progress_facts("
                "progress_id,effect_id,part_id,part_index,phase,bytes_completed,total_bytes,"
                "observed_at_ms,evidence_sha256,progress_json,progress_sha256) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    progress_id,
                    fact.effect_id,
                    fact.part_id,
                    fact.part_index,
                    fact.phase,
                    fact.bytes_completed,
                    fact.total_bytes,
                    fact.observed_at_ms,
                    fact.evidence_sha256,
                    fact_json,
                    fact_digest,
                ),
            )
            return fact

    def list_transfer_progress(
        self, effect_id: str
    ) -> tuple[DeliveryTransferProgressFact, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM delivery_transfer_progress_facts WHERE effect_id=? "
                "ORDER BY part_index,sequence",
                (effect_id,),
            ).fetchall()
            result = []
            for row in rows:
                fact = DeliveryTransferProgressFact.model_validate_json(
                    row["progress_json"], strict=True
                )
                fact_json, fact_digest = _canonical_model(fact)
                if (
                    not fact.has_valid_sha256()
                    or fact_json != row["progress_json"]
                    or fact_digest != row["progress_sha256"]
                    or row["progress_id"] != _progress_fact_id(fact)
                ):
                    raise DeliveryLedgerCorruption("delivery transfer progress is invalid")
                result.append(fact)
            return tuple(result)

    def _verify_rows(self) -> None:
        verifications = {}
        for row in self._connection.execute(
            "SELECT * FROM verified_delivery_tickets"
        ).fetchall():
            fact = self._verification(row)
            verifications[fact.ticket_id] = fact
        effects = {}
        for row in self._connection.execute("SELECT * FROM delivery_effects").fetchall():
            record = self._record(row)
            claim = record.claim
            expected = {
                "effect_id": claim.effect_id,
                "delivery_id": claim.delivery_id,
                "ticket_id": claim.ticket_id,
                "request_id": claim.request_id,
                "run_id": claim.run_id,
                "generation": claim.generation,
                "channel": claim.channel,
                "tenant_id": claim.tenant_id,
                "link_account_id": claim.link_account_id,
                "claimed_at_ms": claim.claimed_at_ms,
            }
            if any(row[name] != value for name, value in expected.items()):
                raise DeliveryLedgerCorruption("delivery columns disagree with canonical claim")
            effects[claim.effect_id] = record
        for fact in verifications.values():
            record = effects.get(fact.effect_id)
            if record is None:
                raise DeliveryLedgerCorruption(
                    "verified delivery ticket has no atomic effect claim"
                )
            claim = record.claim
            if (
                fact.ticket_id != claim.ticket_id
                or fact.delivery_id != claim.delivery_id
                or fact.request_id != claim.request_id
                or fact.run_id != claim.run_id
                or fact.generation != claim.generation
                or fact.gateway_epoch != claim.gateway_epoch
                or fact.outbound_plan_sha256 != claim.outbound_plan_sha256
                or fact.payload_sha256 != claim.ticket_payload_sha256
            ):
                raise DeliveryLedgerCorruption(
                    "verified delivery ticket disagrees with its effect claim"
                )
        if any(
            record.claim.ticket_id not in verifications
            for record in effects.values()
        ):
            raise DeliveryLedgerCorruption(
                "delivery effect exists without verified ticket authority"
            )
        events: dict[str, list[sqlite3.Row]] = {}
        for row in self._connection.execute("SELECT * FROM delivery_stage_events ORDER BY sequence").fetchall():
            if row["effect_id"] not in effects:
                raise DeliveryLedgerCorruption("delivery stage event references missing effect")
            if row["event_id"] != _event_id(row["effect_id"], row["to_state"], row["evidence_sha256"]):
                raise DeliveryLedgerCorruption("delivery stage event identity is invalid")
            events.setdefault(row["effect_id"], []).append(row)
        for effect_id, record in effects.items():
            chain = events.get(effect_id, [])
            if not chain or chain[0]["from_state"] is not None or chain[0]["to_state"] != "CLAIMED":
                raise DeliveryLedgerCorruption("delivery effect has no valid claim event")
            current = "CLAIMED"
            for event in chain[1:]:
                if event["from_state"] != current:
                    raise DeliveryLedgerCorruption("delivery stage event chain is discontinuous")
                current = event["to_state"]
            if current != record.state:
                raise DeliveryLedgerCorruption("delivery stage event chain disagrees with current state")
        part_chains: dict[tuple[str, str], list[DeliveryPartStageFact]] = {}
        for row in self._connection.execute(
            "SELECT * FROM delivery_part_stage_facts ORDER BY sequence"
        ).fetchall():
            if row["effect_id"] not in effects:
                raise DeliveryLedgerCorruption("delivery part stage references missing effect")
            fact = DeliveryPartStageFact.model_validate_json(row["stage_fact_json"], strict=True)
            fact_json, fact_digest = _canonical_model(fact)
            if (
                not fact.has_valid_sha256()
                or fact_json != row["stage_fact_json"]
                or fact_digest != row["stage_fact_sha256"]
                or row["stage_fact_id"] != _part_stage_fact_id(fact)
                or row["effect_id"] != fact.effect_id
                or row["part_id"] != fact.part_id
                or row["part_index"] != fact.part_index
                or row["kind"] != fact.kind
                or row["stage"] != fact.stage
                or row["attempt"] != fact.attempt
                or row["occurred_at_ms"] != fact.occurred_at_ms
                or row["evidence_sha256"] != fact.evidence_sha256
            ):
                raise DeliveryLedgerCorruption("delivery part-stage columns are invalid")
            part_chains.setdefault((fact.effect_id, fact.part_id), []).append(fact)
        for chain in part_chains.values():
            previous = None
            part_index = chain[0].part_index
            kind = chain[0].kind
            occurred_at_ms = effects[chain[0].effect_id].claim.claimed_at_ms
            for fact in chain:
                if (
                    fact.part_index != part_index
                    or fact.kind != kind
                    or fact.occurred_at_ms < occurred_at_ms
                    or fact.stage not in _PART_STAGE_TRANSITIONS.get(previous, frozenset())
                ):
                    raise DeliveryLedgerCorruption("delivery part-stage chain is invalid")
                previous = fact.stage
                occurred_at_ms = fact.occurred_at_ms
        progress_chains: dict[tuple[str, str, str], list[DeliveryTransferProgressFact]] = {}
        for row in self._connection.execute(
            "SELECT * FROM delivery_transfer_progress_facts ORDER BY sequence"
        ).fetchall():
            if row["effect_id"] not in effects:
                raise DeliveryLedgerCorruption("delivery progress references missing effect")
            fact = DeliveryTransferProgressFact.model_validate_json(
                row["progress_json"], strict=True
            )
            fact_json, fact_digest = _canonical_model(fact)
            if (
                not fact.has_valid_sha256()
                or fact_json != row["progress_json"]
                or fact_digest != row["progress_sha256"]
                or row["progress_id"] != _progress_fact_id(fact)
                or row["effect_id"] != fact.effect_id
                or row["part_id"] != fact.part_id
                or row["part_index"] != fact.part_index
                or row["phase"] != fact.phase
                or row["bytes_completed"] != fact.bytes_completed
                or row["total_bytes"] != fact.total_bytes
                or row["observed_at_ms"] != fact.observed_at_ms
                or row["evidence_sha256"] != fact.evidence_sha256
            ):
                raise DeliveryLedgerCorruption("delivery progress columns are invalid")
            progress_chains.setdefault((fact.effect_id, fact.part_id, fact.phase), []).append(fact)
        for chain in progress_chains.values():
            total = chain[0].total_bytes
            completed = 0
            observed = effects[chain[0].effect_id].claim.claimed_at_ms
            for fact in chain:
                if (
                    fact.total_bytes != total
                    or fact.bytes_completed <= completed
                    or fact.observed_at_ms < observed
                ):
                    raise DeliveryLedgerCorruption("delivery progress chain is invalid")
                completed = fact.bytes_completed
                observed = fact.observed_at_ms

    def channel_drain_facts(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
    ) -> DeliveryDrainFacts:
        if channel not in {"wechat", "feishu"} or not tenant_id or not link_account_id:
            raise ValueError("delivery drain scope is invalid")
        with self._lock:
            if self._closed:
                raise DeliveryLedgerError("delivery ledger is closed")
            self._verify_rows()
            effects = self._connection.execute(
                """
                SELECT effect_id, delivery_id, ticket_id, state, claim_sha256,
                       receipt_sha256, reconcile_reason_code, updated_at_ms
                FROM delivery_effects
                WHERE channel = ? AND tenant_id = ? AND link_account_id = ?
                ORDER BY effect_id
                """,
                (channel, tenant_id, link_account_id),
            ).fetchall()
            effect_ids = tuple(row["effect_id"] for row in effects)
            stages = ()
            progress = ()
            verifications = ()
            if effect_ids:
                placeholders = ",".join("?" for _ in effect_ids)
                stages = tuple(
                    dict(row)
                    for row in self._connection.execute(
                        f"""
                        SELECT effect_id, stage_fact_id, stage_fact_sha256
                        FROM delivery_part_stage_facts
                        WHERE effect_id IN ({placeholders})
                        ORDER BY effect_id, sequence
                        """,
                        effect_ids,
                    ).fetchall()
                )
                progress = tuple(
                    dict(row)
                    for row in self._connection.execute(
                        f"""
                        SELECT effect_id, progress_id, progress_sha256
                        FROM delivery_transfer_progress_facts
                        WHERE effect_id IN ({placeholders})
                        ORDER BY effect_id, sequence
                        """,
                        effect_ids,
                    ).fetchall()
                )
                verifications = tuple(
                    dict(row)
                    for row in self._connection.execute(
                        f"""
                        SELECT effect_id, ticket_id, verification_sha256
                        FROM verified_delivery_tickets
                        WHERE effect_id IN ({placeholders})
                        ORDER BY effect_id, ticket_id
                        """,
                        effect_ids,
                    ).fetchall()
                )
            effect_facts = tuple(dict(row) for row in effects)
            ledger_sha256 = canonical_sha256(
                {
                    "domain": "tiangong.communication.delivery-drain.v1",
                    "channel": channel,
                    "tenant_id": tenant_id,
                    "link_account_id": link_account_id,
                    "effects": effect_facts,
                    "verifications": verifications,
                    "stages": stages,
                    "progress": progress,
                }
            )
            inflight_states = {"CLAIMED", "SIDE_EFFECT_STARTED"}
            unresolved_states = inflight_states | {"FAILED_RETRYABLE", "RECONCILE_REQUIRED"}
            return DeliveryDrainFacts(
                channel=channel,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
                inflight_send_count=sum(row["state"] in inflight_states for row in effects),
                unresolved_delivery_count=sum(
                    row["state"] in unresolved_states for row in effects
                ),
                ledger_sha256=ledger_sha256,
            )

    def health_check(self, *, now_ms: int, full: bool = False) -> DeliveryLedgerHealth:
        with self._lock:
            if self._closed:
                return DeliveryLedgerHealth(False, "delivery_ledger.closed", now_ms, None, False)
            try:
                check = "integrity_check" if full else "quick_check"
                if [row[0] for row in self._connection.execute(f"PRAGMA {check}").fetchall()] != ["ok"]:
                    raise DeliveryLedgerCorruption("SQLite integrity check failed")
                if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise DeliveryLedgerCorruption("delivery ledger foreign key check failed")
                self._validate_schema(self._connection)
                self._verify_rows()
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._connection.execute("UPDATE delivery_migrations SET applied_at_ms = applied_at_ms")
                finally:
                    self._connection.execute("ROLLBACK")
                return DeliveryLedgerHealth(
                    True, "delivery_ledger.ok", now_ms, _schema_sha256(self._connection), True
                )
            except (sqlite3.DatabaseError, OSError, DeliveryLedgerError):
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                return DeliveryLedgerHealth(False, "delivery_ledger.check.failed", now_ms, None, False)

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
    "DELIVERY_LEDGER_APPLICATION_ID",
    "DELIVERY_LEDGER_SCHEMA_VERSION",
    "DeliveryEffectClaim",
    "DeliveryLedger",
    "DeliveryDrainFacts",
    "DeliveryLedgerConflict",
    "DeliveryLedgerCorruption",
    "DeliveryLedgerError",
    "DeliveryLedgerHealth",
    "DeliveryLedgerMigrationError",
    "DeliveryLedgerRecord",
    "DeliveryPartStageFact",
    "DeliveryTransferProgressFact",
    "VerifiedDeliveryConsumption",
    "VerifiedDeliveryTicketFact",
    "derive_channel_client_message_id",
    "expected_delivery_ledger_schema_sha256",
]
