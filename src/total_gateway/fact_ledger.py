"""Durable machine-fact ledger; free-form model text has no write path here."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    ArtifactManifest,
    ExecutionResult,
    FactRecord,
    canonical_json_bytes,
    canonical_sha256,
    derive_effect_identity,
)

from .backend_client import BACKEND_API_CONTRACT, BackendClientError, BackendExecutionResponse
from .object_store import ContentAddressedObjectStore


FACT_LEDGER_APPLICATION_ID = 0x54474641
FACT_LEDGER_SCHEMA_VERSION = 1
_MIGRATION_ID = "gateway-machine-fact-ledger-v1"
_SOURCE_COMPONENT_ID = "tiangong-backend"

_STATEMENTS = (
    """
    CREATE TABLE fact_migrations (
        version INTEGER PRIMARY KEY CHECK (version >= 1),
        migration_id TEXT NOT NULL UNIQUE,
        migration_sha256 TEXT NOT NULL,
        applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
    ) STRICT
    """,
    """
    CREATE TABLE execution_fact_batches (
        result_id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL UNIQUE,
        effect_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        action_id TEXT NOT NULL,
        action_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'SUCCEEDED','FAILED_RETRYABLE','FAILED_FINAL','AMBIGUOUS','CANCELLED','FENCED'
        )),
        source_component_id TEXT NOT NULL CHECK (source_component_id = 'tiangong-backend'),
        observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
        tenant_id TEXT NOT NULL,
        link_account_id TEXT NOT NULL,
        conversation_scope_hash TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        max_output_bytes INTEGER NOT NULL CHECK (max_output_bytes >= 0),
        result_payload_object_id TEXT NOT NULL,
        result_payload_sha256 TEXT NOT NULL,
        response_sha256 TEXT NOT NULL,
        result_json TEXT NOT NULL CHECK (json_valid(result_json)),
        result_sha256 TEXT NOT NULL,
        batch_sha256 TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE fact_ledger (
        fact_id TEXT PRIMARY KEY,
        fact_type TEXT NOT NULL CHECK (fact_type IN (
            'execution.succeeded','execution.failed','execution.ambiguous',
            'execution.cancelled','execution.fenced','artifact.qc_passed','artifact.qc_failed'
        )),
        source_component_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        ticket_id TEXT NOT NULL,
        effect_id TEXT NOT NULL,
        action_id TEXT NOT NULL,
        action_version TEXT NOT NULL,
        observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
        payload_sha256 TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        verification_method TEXT NOT NULL CHECK (
            verification_method IN ('component_receipt','gateway_observation','qc_result')
        ),
        model_generated INTEGER NOT NULL CHECK (model_generated = 0),
        fact_json TEXT NOT NULL CHECK (json_valid(fact_json)),
        fact_sha256 TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE execution_batch_facts (
        result_id TEXT NOT NULL,
        fact_id TEXT NOT NULL UNIQUE,
        PRIMARY KEY (result_id, fact_id),
        FOREIGN KEY (result_id) REFERENCES execution_fact_batches(result_id),
        FOREIGN KEY (fact_id) REFERENCES fact_ledger(fact_id)
    ) STRICT
    """,
    """
    CREATE INDEX fact_request_lookup
    ON fact_ledger(request_id, run_id, generation, fact_type, fact_id)
    """,
    """
    CREATE TABLE artifact_qc_batches (
        qc_result_id TEXT PRIMARY KEY,
        artifact_revision_id TEXT NOT NULL,
        check_id TEXT NOT NULL,
        check_version TEXT NOT NULL,
        fact_id TEXT NOT NULL UNIQUE,
        producer_fact_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('PASSED','FAILED')),
        checked_at_ms INTEGER NOT NULL CHECK (checked_at_ms >= 0),
        qc_result_json TEXT NOT NULL CHECK (json_valid(qc_result_json)),
        qc_result_sha256 TEXT NOT NULL,
        manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
        manifest_sha256 TEXT NOT NULL,
        batch_sha256 TEXT NOT NULL,
        UNIQUE (artifact_revision_id, check_id, check_version),
        FOREIGN KEY (fact_id) REFERENCES fact_ledger(fact_id),
        FOREIGN KEY (producer_fact_id) REFERENCES fact_ledger(fact_id)
    ) STRICT
    """,
    """
    CREATE INDEX artifact_qc_lookup
    ON artifact_qc_batches(artifact_revision_id, checked_at_ms, check_id, check_version)
    """,
)
_MIGRATION_SHA256 = canonical_sha256(
    {
        "migration_id": _MIGRATION_ID,
        "statements": _STATEMENTS,
        "version": FACT_LEDGER_SCHEMA_VERSION,
    }
)


class FactLedgerError(RuntimeError):
    pass


class FactLedgerConflict(FactLedgerError):
    pass


class FactLedgerCorruption(FactLedgerError):
    pass


class QcMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    value: str | int | bool


def derive_qc_result_id(
    *,
    artifact_revision_id: str,
    check_id: str,
    check_version: str,
    content_sha256: str,
) -> str:
    return "qc_" + canonical_sha256(
        {
            "domain": "tiangong.gateway.artifact-qc-result.v1",
            "artifact_revision_id": artifact_revision_id,
            "check_id": check_id,
            "check_version": check_version,
            "content_sha256": content_sha256,
        }
    )


def derive_qc_effect_id(
    *,
    request_id: str,
    run_id: str,
    run_sequence: int,
    generation: int,
    artifact_revision_id: str,
    check_id: str,
    check_version: str,
    content_sha256: str,
) -> str:
    return derive_effect_identity(
        request_id=request_id,
        run_id=run_id,
        run_sequence=run_sequence,
        generation=generation,
        effect_kind="artifact",
        ordinal=0,
        intent_sha256=canonical_sha256(
            {
                "domain": "tiangong.gateway.artifact-qc-effect.v1",
                "artifact_revision_id": artifact_revision_id,
                "check_id": check_id,
                "check_version": check_version,
                "content_sha256": content_sha256,
            }
        ),
    ).effect_id


class ArtifactQcResult(BaseModel):
    """Generic machine QC result; it deliberately has no narrative completion field."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    qc_result_id: str = Field(min_length=1, max_length=160)
    check_id: str = Field(min_length=1, max_length=160)
    check_version: str = Field(min_length=1, max_length=160)
    status: Literal["PASSED", "FAILED"]
    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    run_sequence: int = Field(ge=1)
    generation: int = Field(ge=0)
    effect_id: str = Field(pattern=r"^eff_[0-9a-f]{64}$")
    artifact_revision_id: str = Field(pattern=r"^arv_[0-9a-f]{64}$")
    object_id: str = Field(pattern=r"^oref_[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_at_ms: int = Field(ge=0)
    metrics: tuple[QcMetric, ...] = Field(default=(), max_length=128)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=64)
    model_generated: Literal[False] = False
    qc_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        metric_names = tuple(metric.name for metric in self.metrics)
        if metric_names != tuple(sorted(set(metric_names))):
            raise ValueError("QC metrics must be sorted and unique")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("QC reason codes must be sorted and unique")
        if self.status == "PASSED" and self.reason_codes:
            raise ValueError("passed QC cannot contain failure reason codes")
        if self.status == "FAILED" and not self.reason_codes:
            raise ValueError("failed QC must contain a reason code")
        if self.qc_result_id != derive_qc_result_id(
            artifact_revision_id=self.artifact_revision_id,
            check_id=self.check_id,
            check_version=self.check_version,
            content_sha256=self.content_sha256,
        ):
            raise ValueError("QC result identity is invalid")
        expected_effect = derive_qc_effect_id(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=self.run_sequence,
            generation=self.generation,
            artifact_revision_id=self.artifact_revision_id,
            check_id=self.check_id,
            check_version=self.check_version,
            content_sha256=self.content_sha256,
        )
        if self.effect_id != expected_effect:
            raise ValueError("QC effect identity is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"qc_result_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.qc_result_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"qc_result_sha256": self.computed_sha256()})


def derive_qc_fact_id(result: ArtifactQcResult) -> str:
    if not result.has_valid_sha256():
        raise ValueError("QC result digest is invalid")
    return "fact_qc_" + result.qc_result_sha256


@dataclass(frozen=True)
class FactBatchRecord:
    result: ExecutionResult
    facts: tuple[FactRecord, ...]
    source_component_id: str
    observed_at_ms: int
    tenant_id: str
    link_account_id: str
    conversation_scope_hash: str
    workspace_id: str
    max_output_bytes: int
    result_payload_object_id: str
    result_payload_sha256: str
    response_sha256: str
    batch_sha256: str


@dataclass(frozen=True)
class FactBatchRegistration:
    record: FactBatchRecord
    created_by_this_call: bool


@dataclass(frozen=True)
class ArtifactQcBatchRecord:
    result: ArtifactQcResult
    fact: FactRecord
    manifest: ArtifactManifest
    producer_fact_id: str
    batch_sha256: str


@dataclass(frozen=True)
class ArtifactQcRegistration:
    record: ArtifactQcBatchRecord
    created_by_this_call: bool


@dataclass(frozen=True)
class FactLedgerHealth:
    healthy: bool
    reason_code: str
    checked_at_ms: int
    schema_sha256: str | None
    writable: bool


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
def expected_fact_ledger_schema_sha256() -> str:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for statement in _STATEMENTS:
            connection.execute(statement)
        return _schema_sha256(connection)
    finally:
        connection.close()


def _fact_type(status: str) -> str:
    return {
        "SUCCEEDED": "execution.succeeded",
        "FAILED_RETRYABLE": "execution.failed",
        "FAILED_FINAL": "execution.failed",
        "AMBIGUOUS": "execution.ambiguous",
        "CANCELLED": "execution.cancelled",
        "FENCED": "execution.fenced",
    }[status]


def _model_payload(value: ExecutionResult | FactRecord) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _contract_payload(
    value: ExecutionResult | FactRecord | ArtifactManifest | ArtifactQcResult,
) -> tuple[str, str]:
    data = value.model_dump(mode="json")
    return canonical_json_bytes(data).decode("utf-8"), canonical_sha256(data)


def _batch_digest(record: FactBatchRecord) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.gateway.execution-fact-batch.v1",
            "result_sha256": canonical_sha256(record.result.model_dump(mode="json")),
            "fact_sha256s": tuple(fact.fact_sha256 for fact in record.facts),
            "source_component_id": record.source_component_id,
            "observed_at_ms": record.observed_at_ms,
            "tenant_id": record.tenant_id,
            "link_account_id": record.link_account_id,
            "conversation_scope_hash": record.conversation_scope_hash,
            "workspace_id": record.workspace_id,
            "max_output_bytes": record.max_output_bytes,
            "result_payload_object_id": record.result_payload_object_id,
            "result_payload_sha256": record.result_payload_sha256,
            "response_sha256": record.response_sha256,
        }
    )


def _same_machine_evidence(first: FactBatchRecord, second: FactBatchRecord) -> bool:
    """Observation time is first-writer state; the underlying evidence must be identical."""

    return (
        first.result == second.result
        and first.source_component_id == second.source_component_id
        and first.tenant_id == second.tenant_id
        and first.link_account_id == second.link_account_id
        and first.conversation_scope_hash == second.conversation_scope_hash
        and first.workspace_id == second.workspace_id
        and first.max_output_bytes == second.max_output_bytes
        and first.result_payload_object_id == second.result_payload_object_id
        and first.result_payload_sha256 == second.result_payload_sha256
        and first.response_sha256 == second.response_sha256
        and tuple(fact.fact_id for fact in first.facts)
        == tuple(fact.fact_id for fact in second.facts)
    )


def _qc_batch_digest(record: ArtifactQcBatchRecord) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.gateway.artifact-qc-batch.v1",
            "qc_result_sha256": record.result.qc_result_sha256,
            "fact_sha256": record.fact.fact_sha256,
            "manifest_sha256": record.manifest.manifest_sha256,
            "producer_fact_id": record.producer_fact_id,
        }
    )


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise FactLedgerCorruption("fact payload contains a duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise FactLedgerCorruption("fact payload contains a non-finite number")


class FactLedger:
    def __init__(
        self,
        path: Path,
        connection: sqlite3.Connection,
        object_store: ContentAddressedObjectStore,
    ) -> None:
        self.path = path
        self._connection = connection
        self._object_store = object_store
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(
        cls,
        path: Path,
        object_store: ContentAddressedObjectStore,
        *,
        now_ms: int,
    ) -> "FactLedger":
        if now_ms < 0 or not path.is_absolute() or path == Path(path.anchor):
            raise ValueError("fact ledger path or time is invalid")
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise FactLedgerCorruption("fact ledger path is unsafe")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                path,
                isolation_level=None,
                timeout=5,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA synchronous = FULL")
            if str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() != "wal":
                raise FactLedgerCorruption("fact ledger could not enable WAL")
            cls._migrate(connection, now_ms=now_ms)
            ledger = cls(path, connection, object_store)
            if not ledger.health_check(now_ms=now_ms, full=True).healthy:
                raise FactLedgerCorruption("fact ledger failed initial health check")
            os.chmod(path, 0o600)
            return ledger
        except (sqlite3.DatabaseError, OSError, FactLedgerError) as exc:
            if "connection" in locals():
                connection.close()
            if isinstance(exc, FactLedgerError):
                raise
            raise FactLedgerCorruption("fact ledger could not be opened safely") from exc

    @staticmethod
    def _migrate(connection: sqlite3.Connection, *, now_ms: int) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if application_id not in {0, FACT_LEDGER_APPLICATION_ID} or version > FACT_LEDGER_SCHEMA_VERSION:
            raise FactLedgerCorruption("fact ledger metadata is incompatible")
        if version == 0:
            if objects:
                raise FactLedgerCorruption("unversioned fact ledger database is not empty")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                for statement in _STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO fact_migrations VALUES (?, ?, ?, ?)",
                    (FACT_LEDGER_SCHEMA_VERSION, _MIGRATION_ID, _MIGRATION_SHA256, now_ms),
                )
                connection.execute(f"PRAGMA application_id = {FACT_LEDGER_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {FACT_LEDGER_SCHEMA_VERSION}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        FactLedger._validate_schema(connection)

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM fact_migrations").fetchone()
        if (
            int(connection.execute("PRAGMA application_id").fetchone()[0])
            != FACT_LEDGER_APPLICATION_ID
            or int(connection.execute("PRAGMA user_version").fetchone()[0])
            != FACT_LEDGER_SCHEMA_VERSION
            or row is None
            or row["version"] != FACT_LEDGER_SCHEMA_VERSION
            or row["migration_id"] != _MIGRATION_ID
            or row["migration_sha256"] != _MIGRATION_SHA256
            or _schema_sha256(connection) != expected_fact_ledger_schema_sha256()
        ):
            raise FactLedgerCorruption("fact ledger schema metadata is invalid")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed:
            raise FactLedgerError("fact ledger is closed")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def _parse_fact(self, row: sqlite3.Row) -> FactRecord:
        try:
            fact = FactRecord.model_validate_json(row["fact_json"], strict=True)
        except ValueError as exc:
            raise FactLedgerCorruption("stored fact is invalid") from exc
        payload, digest = _model_payload(fact)
        expected = {
            "fact_id": fact.fact_id,
            "fact_type": fact.fact_type,
            "source_component_id": fact.source_component_id,
            "request_id": fact.request_id,
            "run_id": fact.run_id,
            "generation": fact.generation,
            "ticket_id": fact.ticket_id,
            "effect_id": fact.effect_id,
            "action_id": fact.action_id,
            "action_version": fact.action_version,
            "observed_at_ms": fact.observed_at_ms,
            "payload_sha256": fact.payload_sha256,
            "evidence_sha256": fact.evidence_sha256,
            "verification_method": fact.verification_method,
            "model_generated": 0,
        }
        if (
            payload != row["fact_json"]
            or digest != row["fact_sha256"]
            or not fact.has_valid_sha256()
            or any(row[name] != value for name, value in expected.items())
        ):
            raise FactLedgerCorruption("stored fact columns or digest are invalid")
        return fact

    def _parse_batch(self, row: sqlite3.Row, *, verify_payload: bool) -> FactBatchRecord:
        try:
            result = ExecutionResult.model_validate_json(row["result_json"], strict=True)
        except ValueError as exc:
            raise FactLedgerCorruption("stored execution result is invalid") from exc
        result_json, result_sha256 = _model_payload(result)
        fact_rows = self._connection.execute(
            """
            SELECT f.*
            FROM execution_batch_facts b JOIN fact_ledger f ON f.fact_id = b.fact_id
            WHERE b.result_id = ?
            ORDER BY f.fact_id
            """,
            (result.result_id,),
        ).fetchall()
        facts = tuple(self._parse_fact(item) for item in fact_rows)
        record = FactBatchRecord(
            result=result,
            facts=facts,
            source_component_id=row["source_component_id"],
            observed_at_ms=row["observed_at_ms"],
            tenant_id=row["tenant_id"],
            link_account_id=row["link_account_id"],
            conversation_scope_hash=row["conversation_scope_hash"],
            workspace_id=row["workspace_id"],
            max_output_bytes=row["max_output_bytes"],
            result_payload_object_id=row["result_payload_object_id"],
            result_payload_sha256=row["result_payload_sha256"],
            response_sha256=row["response_sha256"],
            batch_sha256=row["batch_sha256"],
        )
        expected = {
            "result_id": result.result_id,
            "ticket_id": result.ticket_id,
            "effect_id": result.effect_id,
            "request_id": result.request_id,
            "run_id": result.run_id,
            "generation": result.generation,
            "action_id": result.action_id,
            "action_version": result.action_version,
            "status": result.status,
        }
        if (
            result_json != row["result_json"]
            or result_sha256 != row["result_sha256"]
            or record.source_component_id != _SOURCE_COMPONENT_ID
            or record.observed_at_ms < result.finished_at_ms
            or record.result_payload_sha256 != result.result_payload_sha256
            or tuple(fact.fact_id for fact in facts) != result.fact_ids
            or any(row[name] != value for name, value in expected.items())
            or record.batch_sha256 != _batch_digest(record)
        ):
            raise FactLedgerCorruption("stored fact batch binding is invalid")
        for fact in facts:
            if (
                fact.fact_type != _fact_type(result.status)
                or fact.source_component_id != _SOURCE_COMPONENT_ID
                or fact.request_id != result.request_id
                or fact.run_id != result.run_id
                or fact.generation != result.generation
                or fact.ticket_id != result.ticket_id
                or fact.effect_id != result.effect_id
                or fact.action_id != result.action_id
                or fact.action_version != result.action_version
                or fact.observed_at_ms != record.observed_at_ms
                or fact.payload_sha256 != result.result_payload_sha256
                or fact.evidence_sha256 != record.response_sha256
            ):
                raise FactLedgerCorruption("stored fact exceeds its execution result authority")
        if verify_payload:
            payload_bytes = self._object_store.read_bytes(record.result_payload_object_id)
            if canonical_sha256_bytes(payload_bytes) != record.result_payload_sha256:
                raise FactLedgerCorruption("fact payload object digest is invalid")
            try:
                payload = json.loads(
                    payload_bytes.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=_reject_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FactLedgerCorruption("fact payload object is not canonical JSON") from exc
            if canonical_json_bytes(payload) != payload_bytes:
                raise FactLedgerCorruption("fact payload object is not canonical JSON")
            response = {
                "ok": True,
                "api_contract": BACKEND_API_CONTRACT,
                "execution_result": result.model_dump(mode="json"),
                "result_payload": payload,
            }
            if canonical_sha256(response) != record.response_sha256:
                raise FactLedgerCorruption("fact response evidence cannot be reconstructed")
        return record

    def _parse_qc_batch(self, row: sqlite3.Row, *, verify_payload: bool) -> ArtifactQcBatchRecord:
        try:
            result = ArtifactQcResult.model_validate_json(row["qc_result_json"], strict=True)
            manifest = ArtifactManifest.model_validate_json(row["manifest_json"], strict=True)
        except ValueError as exc:
            raise FactLedgerCorruption("stored artifact QC batch is invalid") from exc
        fact_row = self._connection.execute(
            "SELECT * FROM fact_ledger WHERE fact_id = ?", (row["fact_id"],)
        ).fetchone()
        producer_row = self._connection.execute(
            "SELECT * FROM fact_ledger WHERE fact_id = ?", (row["producer_fact_id"],)
        ).fetchone()
        if fact_row is None or producer_row is None:
            raise FactLedgerCorruption("artifact QC batch references a missing fact")
        fact = self._parse_fact(fact_row)
        producer = self._parse_fact(producer_row)
        result_json, result_sha256 = _contract_payload(result)
        manifest_json, manifest_sha256 = _contract_payload(manifest)
        record = ArtifactQcBatchRecord(
            result=result,
            fact=fact,
            manifest=manifest,
            producer_fact_id=producer.fact_id,
            batch_sha256=row["batch_sha256"],
        )
        expected = {
            "qc_result_id": result.qc_result_id,
            "artifact_revision_id": result.artifact_revision_id,
            "check_id": result.check_id,
            "check_version": result.check_version,
            "fact_id": fact.fact_id,
            "producer_fact_id": producer.fact_id,
            "object_id": result.object_id,
            "content_sha256": result.content_sha256,
            "status": result.status,
            "checked_at_ms": result.checked_at_ms,
        }
        evidence = tuple(
            item
            for item in manifest.qc_evidence
            if item.check_id == result.check_id and item.check_version == result.check_version
        )
        expected_fact_type = (
            "artifact.qc_passed" if result.status == "PASSED" else "artifact.qc_failed"
        )
        if (
            result_json != row["qc_result_json"]
            or result_sha256 != row["qc_result_sha256"]
            or not result.has_valid_sha256()
            or manifest_json != row["manifest_json"]
            or manifest_sha256 != row["manifest_sha256"]
            or not manifest.has_valid_manifest_sha256()
            or any(row[name] != value for name, value in expected.items())
            or len(evidence) != 1
            or evidence[0].status != result.status
            or evidence[0].checked_at_ms != result.checked_at_ms
            or evidence[0].evidence_sha256 != result.qc_result_sha256
            or evidence[0].tool_fact_id != fact.fact_id
            or manifest.qc_state != result.status
            or manifest.artifact_revision_id != result.artifact_revision_id
            or manifest.content_object_id != result.object_id
            or manifest.sha256 != result.content_sha256
            or result.request_id != manifest.request_id
            or result.run_id != manifest.run_id
            or result.generation != manifest.generation
            or fact.fact_id != derive_qc_fact_id(result)
            or fact.fact_type != expected_fact_type
            or fact.source_component_id != "tiangong-total-gateway"
            or fact.request_id != result.request_id
            or fact.run_id != result.run_id
            or fact.generation != result.generation
            or fact.ticket_id != producer.ticket_id
            or fact.effect_id != result.effect_id
            or fact.action_id != result.check_id
            or fact.action_version != result.check_version
            or fact.observed_at_ms != result.checked_at_ms
            or fact.payload_sha256 != result.content_sha256
            or fact.evidence_sha256 != result.qc_result_sha256
            or fact.verification_method != "qc_result"
            or producer.fact_type != "execution.succeeded"
            or producer.fact_id != manifest.producer_fact_id
            or producer.request_id != manifest.request_id
            or producer.run_id != manifest.run_id
            or producer.generation != manifest.generation
            or producer.effect_id != manifest.source_effect_id
            or record.batch_sha256 != _qc_batch_digest(record)
        ):
            raise FactLedgerCorruption("stored artifact QC evidence binding is invalid")
        if verify_payload:
            reference = self._object_store.get_reference(result.object_id)
            if (
                reference is None
                or reference.kind != "artifact"
                or reference.sha256 != result.content_sha256
                or reference.size_bytes != manifest.size_bytes
                or reference.tenant_id != manifest.tenant_id
                or reference.link_account_id != manifest.link_account_id
                or reference.conversation_scope_hash != manifest.conversation_scope_hash
            ):
                raise FactLedgerCorruption("artifact QC object binding is invalid")
            data = self._object_store.read_bytes(result.object_id)
            if canonical_sha256_bytes(data) != result.content_sha256:
                raise FactLedgerCorruption("artifact QC content changed after verification")
        return record

    def record_execution(
        self,
        response: BackendExecutionResponse,
        *,
        observed_at_ms: int,
    ) -> FactBatchRegistration:
        if not isinstance(response, BackendExecutionResponse):
            raise FactLedgerError("fact.input.unverified")
        try:
            response.assert_verified()
        except BackendClientError as exc:
            raise FactLedgerError("fact.input.unverified") from exc
        result = response.result
        if observed_at_ms < result.finished_at_ms:
            raise FactLedgerError("fact.observation.predates_result")
        payload_bytes = canonical_json_bytes(response.result_payload)
        if canonical_sha256_bytes(payload_bytes) != result.result_payload_sha256:
            raise FactLedgerError("fact.payload.digest_mismatch")
        reconstructed = {
            "ok": True,
            "api_contract": BACKEND_API_CONTRACT,
            "execution_result": result.model_dump(mode="json"),
            "result_payload": response.result_payload,
        }
        if canonical_sha256(reconstructed) != response.response_sha256:
            raise FactLedgerError("fact.response.digest_mismatch")
        ticket = response.ticket.payload
        payload_object = self._object_store.put_bytes(
            payload_bytes,
            kind="payload",
            tenant_id=ticket.tenant_id,
            link_account_id=ticket.link_account_id,
            conversation_scope_hash=ticket.conversation_scope_hash,
            created_at_ms=observed_at_ms,
        ).reference
        verification_method = (
            "component_receipt" if result.receipt_sha256 is not None else "gateway_observation"
        )
        facts = tuple(
            FactRecord(
                fact_id=fact_id,
                fact_type=_fact_type(result.status),
                source_component_id=_SOURCE_COMPONENT_ID,
                request_id=result.request_id,
                run_id=result.run_id,
                generation=result.generation,
                ticket_id=result.ticket_id,
                effect_id=result.effect_id,
                action_id=result.action_id,
                action_version=result.action_version,
                observed_at_ms=observed_at_ms,
                payload_sha256=result.result_payload_sha256,
                evidence_sha256=response.response_sha256,
                verification_method=verification_method,
                fact_sha256="0" * 64,
            ).with_computed_sha256()
            for fact_id in result.fact_ids
        )
        candidate = FactBatchRecord(
            result=result,
            facts=facts,
            source_component_id=_SOURCE_COMPONENT_ID,
            observed_at_ms=observed_at_ms,
            tenant_id=ticket.tenant_id,
            link_account_id=ticket.link_account_id,
            conversation_scope_hash=ticket.conversation_scope_hash,
            workspace_id=ticket.workspace_id,
            max_output_bytes=ticket.max_output_bytes,
            result_payload_object_id=payload_object.object_id,
            result_payload_sha256=payload_object.sha256,
            response_sha256=response.response_sha256,
            batch_sha256="0" * 64,
        )
        candidate = FactBatchRecord(
            **{**candidate.__dict__, "batch_sha256": _batch_digest(candidate)}
        )
        result_json, result_sha256 = _model_payload(result)
        with self._lock, self._transaction():
            rows = self._connection.execute(
                """
                SELECT * FROM execution_fact_batches
                WHERE result_id = ? OR ticket_id = ? OR effect_id = ?
                """,
                (result.result_id, result.ticket_id, result.effect_id),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise FactLedgerCorruption("fact batch identities disagree")
                stored = self._parse_batch(rows[0], verify_payload=True)
                if not _same_machine_evidence(stored, candidate):
                    raise FactLedgerConflict("execution fact identity was reused with different evidence")
                return FactBatchRegistration(stored, False)
            try:
                self._connection.execute(
                    """
                    INSERT INTO execution_fact_batches(
                        result_id, ticket_id, effect_id, request_id, run_id, generation,
                        action_id, action_version, status, source_component_id, observed_at_ms,
                        tenant_id, link_account_id, conversation_scope_hash, workspace_id,
                        max_output_bytes,
                        result_payload_object_id, result_payload_sha256, response_sha256,
                        result_json, result_sha256, batch_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.result_id, result.ticket_id, result.effect_id, result.request_id,
                        result.run_id, result.generation, result.action_id, result.action_version,
                        result.status, candidate.source_component_id, observed_at_ms,
                        candidate.tenant_id, candidate.link_account_id,
                        candidate.conversation_scope_hash, candidate.workspace_id,
                        candidate.max_output_bytes,
                        candidate.result_payload_object_id, candidate.result_payload_sha256,
                        candidate.response_sha256, result_json, result_sha256,
                        candidate.batch_sha256,
                    ),
                )
                for fact in facts:
                    fact_json, fact_sha256 = _model_payload(fact)
                    self._connection.execute(
                        """
                        INSERT INTO fact_ledger(
                            fact_id, fact_type, source_component_id, request_id,
                            run_id, generation, ticket_id, effect_id, action_id, action_version,
                            observed_at_ms, payload_sha256, evidence_sha256,
                            verification_method, model_generated, fact_json, fact_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (
                            fact.fact_id, fact.fact_type, fact.source_component_id,
                            fact.request_id, fact.run_id, fact.generation, fact.ticket_id,
                            fact.effect_id, fact.action_id, fact.action_version,
                            fact.observed_at_ms, fact.payload_sha256, fact.evidence_sha256,
                            fact.verification_method, fact_json, fact_sha256,
                        ),
                    )
                    self._connection.execute(
                        "INSERT INTO execution_batch_facts(result_id, fact_id) VALUES (?, ?)",
                        (result.result_id, fact.fact_id),
                    )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorname", "") in {
                    "SQLITE_CONSTRAINT_PRIMARYKEY",
                    "SQLITE_CONSTRAINT_UNIQUE",
                }:
                    raise FactLedgerConflict(
                        "execution fact conflicts with immutable ledger history"
                    ) from exc
                raise
        return FactBatchRegistration(candidate, True)

    def record_artifact_qc(
        self,
        result: ArtifactQcResult,
        manifest: ArtifactManifest,
    ) -> ArtifactQcRegistration:
        if not result.has_valid_sha256() or not manifest.has_valid_manifest_sha256():
            raise FactLedgerError("fact.qc.digest_invalid")
        if (
            result.artifact_revision_id != manifest.artifact_revision_id
            or result.object_id != manifest.content_object_id
            or result.content_sha256 != manifest.sha256
            or result.request_id != manifest.request_id
            or result.run_id != manifest.run_id
            or result.generation != manifest.generation
            or manifest.qc_state != result.status
        ):
            raise FactLedgerError("fact.qc.manifest_binding_mismatch")
        fact_id = derive_qc_fact_id(result)
        matching_evidence = tuple(
            item
            for item in manifest.qc_evidence
            if item.check_id == result.check_id and item.check_version == result.check_version
        )
        if (
            len(matching_evidence) != 1
            or matching_evidence[0].status != result.status
            or matching_evidence[0].checked_at_ms != result.checked_at_ms
            or matching_evidence[0].evidence_sha256 != result.qc_result_sha256
            or matching_evidence[0].tool_fact_id != fact_id
        ):
            raise FactLedgerError("fact.qc.evidence_binding_mismatch")
        producer = self.get_fact(manifest.producer_fact_id)
        if (
            producer is None
            or producer.fact_type != "execution.succeeded"
            or producer.request_id != manifest.request_id
            or producer.run_id != manifest.run_id
            or producer.generation != manifest.generation
            or producer.effect_id != manifest.source_effect_id
        ):
            raise FactLedgerError("fact.qc.producer_invalid")
        reference = self._object_store.get_reference(result.object_id)
        if (
            reference is None
            or reference.kind != "artifact"
            or reference.sha256 != result.content_sha256
            or reference.size_bytes != manifest.size_bytes
            or reference.tenant_id != manifest.tenant_id
            or reference.link_account_id != manifest.link_account_id
            or reference.conversation_scope_hash != manifest.conversation_scope_hash
        ):
            raise FactLedgerError("fact.qc.object_binding_mismatch")
        if canonical_sha256_bytes(self._object_store.read_bytes(result.object_id)) != result.content_sha256:
            raise FactLedgerError("fact.qc.object_readback_mismatch")
        fact_type = "artifact.qc_passed" if result.status == "PASSED" else "artifact.qc_failed"
        fact = FactRecord(
            fact_id=fact_id,
            fact_type=fact_type,
            source_component_id="tiangong-total-gateway",
            request_id=result.request_id,
            run_id=result.run_id,
            generation=result.generation,
            ticket_id=producer.ticket_id,
            effect_id=result.effect_id,
            action_id=result.check_id,
            action_version=result.check_version,
            observed_at_ms=result.checked_at_ms,
            payload_sha256=result.content_sha256,
            evidence_sha256=result.qc_result_sha256,
            verification_method="qc_result",
            fact_sha256="0" * 64,
        ).with_computed_sha256()
        candidate = ArtifactQcBatchRecord(
            result=result,
            fact=fact,
            manifest=manifest,
            producer_fact_id=producer.fact_id,
            batch_sha256="0" * 64,
        )
        candidate = ArtifactQcBatchRecord(
            **{**candidate.__dict__, "batch_sha256": _qc_batch_digest(candidate)}
        )
        result_json, result_sha256 = _contract_payload(result)
        manifest_json, manifest_sha256 = _contract_payload(manifest)
        fact_json, fact_sha256 = _contract_payload(fact)
        with self._lock, self._transaction():
            rows = self._connection.execute(
                """
                SELECT * FROM artifact_qc_batches
                WHERE qc_result_id = ? OR fact_id = ?
                   OR (artifact_revision_id = ? AND check_id = ? AND check_version = ?)
                """,
                (
                    result.qc_result_id,
                    fact.fact_id,
                    result.artifact_revision_id,
                    result.check_id,
                    result.check_version,
                ),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise FactLedgerCorruption("artifact QC identities disagree")
                stored = self._parse_qc_batch(rows[0], verify_payload=True)
                if stored != candidate:
                    raise FactLedgerConflict("artifact QC identity was reused with different evidence")
                return ArtifactQcRegistration(stored, False)
            try:
                self._connection.execute(
                    """
                    INSERT INTO fact_ledger(
                        fact_id, fact_type, source_component_id, request_id,
                        run_id, generation, ticket_id, effect_id, action_id, action_version,
                        observed_at_ms, payload_sha256, evidence_sha256,
                        verification_method, model_generated, fact_json, fact_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        fact.fact_id, fact.fact_type, fact.source_component_id,
                        fact.request_id, fact.run_id, fact.generation, fact.ticket_id,
                        fact.effect_id, fact.action_id, fact.action_version,
                        fact.observed_at_ms, fact.payload_sha256, fact.evidence_sha256,
                        fact.verification_method, fact_json, fact_sha256,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO artifact_qc_batches(
                        qc_result_id, artifact_revision_id, check_id, check_version,
                        fact_id, producer_fact_id, object_id, content_sha256, status,
                        checked_at_ms, qc_result_json, qc_result_sha256,
                        manifest_json, manifest_sha256, batch_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.qc_result_id, result.artifact_revision_id,
                        result.check_id, result.check_version, fact.fact_id,
                        producer.fact_id, result.object_id, result.content_sha256,
                        result.status, result.checked_at_ms, result_json, result_sha256,
                        manifest_json, manifest_sha256, candidate.batch_sha256,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorname", "") in {
                    "SQLITE_CONSTRAINT_PRIMARYKEY",
                    "SQLITE_CONSTRAINT_UNIQUE",
                }:
                    raise FactLedgerConflict(
                        "artifact QC conflicts with immutable ledger history"
                    ) from exc
                raise
        return ArtifactQcRegistration(candidate, True)

    def get_fact(self, fact_id: str) -> FactRecord | None:
        with self._lock:
            if self._closed:
                raise FactLedgerError("fact ledger is closed")
            row = self._connection.execute(
                "SELECT * FROM fact_ledger WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            return None if row is None else self._parse_fact(row)

    def get_batch(self, result_id: str, *, verify_payload: bool = True) -> FactBatchRecord | None:
        with self._lock:
            if self._closed:
                raise FactLedgerError("fact ledger is closed")
            row = self._connection.execute(
                "SELECT * FROM execution_fact_batches WHERE result_id = ?", (result_id,)
            ).fetchone()
            return None if row is None else self._parse_batch(row, verify_payload=verify_payload)

    def get_batch_for_fact(
        self,
        fact_id: str,
        *,
        verify_payload: bool = True,
    ) -> FactBatchRecord | None:
        with self._lock:
            if self._closed:
                raise FactLedgerError("fact ledger is closed")
            row = self._connection.execute(
                """
                SELECT b.*
                FROM execution_batch_facts f
                JOIN execution_fact_batches b ON b.result_id = f.result_id
                WHERE f.fact_id = ?
                """,
                (fact_id,),
            ).fetchone()
            return None if row is None else self._parse_batch(row, verify_payload=verify_payload)

    def get_artifact_qc(
        self,
        artifact_revision_id: str,
        *,
        check_id: str,
        check_version: str,
        verify_payload: bool = True,
    ) -> ArtifactQcBatchRecord | None:
        with self._lock:
            if self._closed:
                raise FactLedgerError("fact ledger is closed")
            row = self._connection.execute(
                """
                SELECT * FROM artifact_qc_batches
                WHERE artifact_revision_id = ? AND check_id = ? AND check_version = ?
                """,
                (artifact_revision_id, check_id, check_version),
            ).fetchone()
            return None if row is None else self._parse_qc_batch(row, verify_payload=verify_payload)

    def get_artifact_manifest(
        self,
        artifact_revision_id: str,
        *,
        verify_payload: bool = True,
    ) -> ArtifactManifest | None:
        if not artifact_revision_id:
            raise ValueError("artifact revision identity is invalid")
        with self._lock:
            if self._closed:
                raise FactLedgerError("fact ledger is closed")
            rows = self._connection.execute(
                """
                SELECT * FROM artifact_qc_batches
                WHERE artifact_revision_id = ?
                ORDER BY check_id, check_version
                """,
                (artifact_revision_id,),
            ).fetchall()
            if not rows:
                return None
            records = tuple(
                self._parse_qc_batch(row, verify_payload=verify_payload)
                for row in rows
            )
            manifest = records[0].manifest
            if any(record.manifest != manifest for record in records[1:]):
                raise FactLedgerCorruption("artifact revision has conflicting manifests")
            return manifest

    def list_request_artifact_manifests(
        self,
        request_id: str,
        *,
        run_id: str | None = None,
        generation: int | None = None,
        verify_payload: bool = True,
    ) -> tuple[ArtifactManifest, ...]:
        if not request_id or (generation is not None and generation < 0):
            raise ValueError("artifact request scope is invalid")
        with self._lock:
            if self._closed:
                raise FactLedgerError("fact ledger is closed")
            clauses = ["facts.request_id = ?"]
            parameters: list[object] = [request_id]
            if run_id is not None:
                clauses.append("facts.run_id = ?")
                parameters.append(run_id)
            if generation is not None:
                clauses.append("facts.generation = ?")
                parameters.append(generation)
            rows = self._connection.execute(
                f"""
                SELECT batches.*
                FROM artifact_qc_batches AS batches
                JOIN fact_ledger AS facts ON facts.fact_id = batches.fact_id
                WHERE {' AND '.join(clauses)}
                ORDER BY batches.checked_at_ms, batches.artifact_revision_id,
                         batches.check_id, batches.check_version
                """,
                tuple(parameters),
            ).fetchall()
            by_revision: dict[str, ArtifactManifest] = {}
            for row in rows:
                manifest = self._parse_qc_batch(
                    row,
                    verify_payload=verify_payload,
                ).manifest
                if manifest.request_id != request_id:
                    continue
                if run_id is not None and manifest.run_id != run_id:
                    continue
                if generation is not None and manifest.generation != generation:
                    continue
                previous = by_revision.setdefault(
                    manifest.artifact_revision_id,
                    manifest,
                )
                if previous != manifest:
                    raise FactLedgerCorruption("artifact revision has conflicting manifests")
            return tuple(
                sorted(
                    by_revision.values(),
                    key=lambda item: (
                        item.created_at_ms,
                        item.artifact_id,
                        item.revision,
                    ),
                )
            )

    def list_request_facts(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
    ) -> tuple[FactRecord, ...]:
        if generation < 0:
            raise ValueError("fact generation is invalid")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM fact_ledger
                WHERE request_id = ? AND run_id = ? AND generation = ?
                ORDER BY fact_id
                """,
                (request_id, run_id, generation),
            ).fetchall()
            return tuple(self._parse_fact(row) for row in rows)

    def count_facts(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT count(*) FROM fact_ledger").fetchone()[0])

    def health_check(self, *, now_ms: int, full: bool = False) -> FactLedgerHealth:
        if now_ms < 0:
            raise ValueError("fact ledger health time is invalid")
        with self._lock:
            if self._closed:
                return FactLedgerHealth(False, "fact_ledger.closed", now_ms, None, False)
            try:
                check = "integrity_check" if full else "quick_check"
                if [row[0] for row in self._connection.execute(f"PRAGMA {check}").fetchall()] != ["ok"]:
                    raise FactLedgerCorruption("fact ledger SQLite integrity failed")
                if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise FactLedgerCorruption("fact ledger foreign key check failed")
                self._validate_schema(self._connection)
                for row in self._connection.execute(
                    "SELECT * FROM execution_fact_batches ORDER BY result_id"
                ).fetchall():
                    self._parse_batch(row, verify_payload=full)
                for row in self._connection.execute(
                    "SELECT * FROM artifact_qc_batches ORDER BY qc_result_id"
                ).fetchall():
                    self._parse_qc_batch(row, verify_payload=full)
                linked = {
                    row[0]
                    for row in self._connection.execute(
                        "SELECT fact_id FROM execution_batch_facts"
                    ).fetchall()
                }
                linked.update(
                    row[0]
                    for row in self._connection.execute(
                        "SELECT fact_id FROM artifact_qc_batches"
                    ).fetchall()
                )
                stored = {
                    row[0]
                    for row in self._connection.execute("SELECT fact_id FROM fact_ledger").fetchall()
                }
                if linked != stored:
                    raise FactLedgerCorruption("fact ledger contains an unbound machine fact")
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._connection.execute(
                        "UPDATE fact_migrations SET applied_at_ms = applied_at_ms WHERE version = 1"
                    )
                finally:
                    self._connection.execute("ROLLBACK")
                return FactLedgerHealth(
                    True,
                    "fact_ledger.ok",
                    now_ms,
                    _schema_sha256(self._connection),
                    True,
                )
            except (sqlite3.DatabaseError, OSError, FactLedgerError):
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                return FactLedgerHealth(False, "fact_ledger.check.failed", now_ms, None, False)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "FactLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def canonical_sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "FACT_LEDGER_APPLICATION_ID",
    "FACT_LEDGER_SCHEMA_VERSION",
    "ArtifactQcBatchRecord",
    "ArtifactQcRegistration",
    "ArtifactQcResult",
    "FactBatchRecord",
    "FactBatchRegistration",
    "FactLedger",
    "FactLedgerConflict",
    "FactLedgerCorruption",
    "FactLedgerError",
    "FactLedgerHealth",
    "QcMetric",
    "derive_qc_effect_id",
    "derive_qc_fact_id",
    "derive_qc_result_id",
    "expected_fact_ledger_schema_sha256",
]
