"""Transactional persistence for World Cognition Core.

Design invariants:
- no directory/database is created by construction or read-only access;
- evidence, priors, statements and revision decisions are immutable rows;
- only the cognition head pointer is mutable;
- head updates use compare-and-swap inside BEGIN IMMEDIATE transactions;
- canonical contract hashes are verified before persistence;
- no last-write-wins semantics are permitted.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, Sequence

from contracts.cognition_evidence import CognitionEvidence
from contracts.cognition_prior import CognitionPrior
from contracts.cognition_revision import CognitionRevision
from contracts.cognition_statement import CognitionStatement


SCHEMA_VERSION = "tiangong.world_cognition.store.v1"


class CognitionStoreError(RuntimeError):
    pass


class CognitionIntegrityError(CognitionStoreError):
    pass


class CognitionConflictError(CognitionStoreError):
    pass


def _payload_json(model: object) -> str:
    if not hasattr(model, "model_dump"):
        raise TypeError("world cognition store accepts Pydantic contract models only")
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class WorldCognitionStore:
    """SQLite-backed immutable cognition ledger with a CAS head table."""

    def __init__(self, root: str | os.PathLike[str], *, timeout_seconds: float = 5.0) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.db_path = self.root / "cognition.sqlite3"
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._init_lock = threading.RLock()
        self._initialized = False

    @property
    def exists(self) -> bool:
        return self.db_path.is_file()

    def _connect_existing(self) -> sqlite3.Connection | None:
        if not self.db_path.is_file():
            return None
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.timeout_seconds,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=%d" % int(self.timeout_seconds * 1000))
        return connection

    def _ensure_initialized(self) -> None:
        if self._initialized and self.db_path.is_file():
            return
        with self._init_lock:
            if self._initialized and self.db_path.is_file():
                return
            self.root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.db_path,
                timeout=self.timeout_seconds,
                isolation_level=None,
                check_same_thread=False,
            )
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA busy_timeout=%d" % int(self.timeout_seconds * 1000))
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS priors (
                        prior_sha256 TEXT PRIMARY KEY,
                        prior_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        life_id TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at_ms INTEGER NOT NULL,
                        payload_json TEXT NOT NULL,
                        UNIQUE(prior_id, revision)
                    );
                    CREATE INDEX IF NOT EXISTS idx_priors_scope
                        ON priors(life_id, domain, status, prior_id, revision);

                    CREATE TABLE IF NOT EXISTS evidence (
                        evidence_id TEXT PRIMARY KEY,
                        evidence_sha256 TEXT NOT NULL UNIQUE,
                        life_id TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        world_scope_hash TEXT NOT NULL,
                        principal_scope_hash TEXT NOT NULL,
                        independence_group_hash TEXT NOT NULL,
                        evidence_class TEXT NOT NULL,
                        source_kind TEXT NOT NULL,
                        observed_at_ms INTEGER NOT NULL,
                        valid_until_ms INTEGER,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_evidence_scope
                        ON evidence(life_id, domain, world_scope_hash, principal_scope_hash, observed_at_ms);
                    CREATE INDEX IF NOT EXISTS idx_evidence_group
                        ON evidence(independence_group_hash);

                    CREATE TABLE IF NOT EXISTS statements (
                        statement_sha256 TEXT PRIMARY KEY,
                        cognition_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        life_id TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        world_scope_hash TEXT NOT NULL,
                        principal_scope_hash TEXT NOT NULL,
                        privacy_scope TEXT NOT NULL,
                        status TEXT NOT NULL,
                        stability_level TEXT NOT NULL,
                        confidence_milli INTEGER NOT NULL,
                        valid_until_ms INTEGER,
                        payload_json TEXT NOT NULL,
                        UNIQUE(cognition_id, revision)
                    );
                    CREATE INDEX IF NOT EXISTS idx_statements_scope
                        ON statements(life_id, domain, world_scope_hash, principal_scope_hash, status, stability_level);

                    CREATE TABLE IF NOT EXISTS revisions (
                        revision_sha256 TEXT PRIMARY KEY,
                        cognition_revision_id TEXT NOT NULL UNIQUE,
                        cognition_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        created_at_ms INTEGER NOT NULL,
                        payload_json TEXT NOT NULL,
                        UNIQUE(cognition_id, sequence)
                    );
                    CREATE INDEX IF NOT EXISTS idx_revisions_cognition
                        ON revisions(cognition_id, sequence);

                    CREATE TABLE IF NOT EXISTS cognition_heads (
                        cognition_id TEXT PRIMARY KEY,
                        statement_sha256 TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL,
                        FOREIGN KEY(statement_sha256) REFERENCES statements(statement_sha256)
                    );
                    """
                )
                current = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
                if current is None:
                    connection.execute(
                        "INSERT INTO metadata(key, value) VALUES('schema_version', ?)",
                        (SCHEMA_VERSION,),
                    )
                elif str(current[0]) != SCHEMA_VERSION:
                    raise CognitionIntegrityError(
                        f"unsupported world cognition store schema: {current[0]!r}"
                    )
            finally:
                connection.close()
            self._initialized = True

    @staticmethod
    def _validate_hash(model: object, method_name: str, kind: str) -> None:
        method = getattr(model, method_name, None)
        if not callable(method) or not bool(method()):
            raise CognitionIntegrityError(f"invalid canonical hash for {kind}")

    @staticmethod
    def _insert_immutable(
        connection: sqlite3.Connection,
        *,
        table: str,
        key_column: str,
        key_value: str,
        columns: Sequence[str],
        values: Sequence[object],
        payload_json: str,
    ) -> bool:
        existing = connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column}=?",
            (key_value,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload_json:
                raise CognitionIntegrityError(
                    f"immutable {table} identity collision for {key_value}"
                )
            return False
        placeholders = ",".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
            tuple(values),
        )
        return True

    def put_prior(self, prior: CognitionPrior) -> bool:
        self._validate_hash(prior, "has_valid_prior_sha256", "cognition prior")
        payload = _payload_json(prior)
        self._ensure_initialized()
        connection = self._connect_existing()
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            inserted = self._insert_immutable(
                connection,
                table="priors",
                key_column="prior_sha256",
                key_value=prior.prior_sha256,
                columns=("prior_sha256", "prior_id", "revision", "life_id", "domain", "status", "created_at_ms", "payload_json"),
                values=(prior.prior_sha256, prior.prior_id, prior.revision, prior.life_id, prior.domain, prior.status, prior.created_at_ms, payload),
                payload_json=payload,
            )
            connection.commit()
            return inserted
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise CognitionIntegrityError(f"prior uniqueness violation: {exc}") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def put_evidence(self, evidence: CognitionEvidence) -> bool:
        self._validate_hash(evidence, "has_valid_evidence_sha256", "cognition evidence")
        payload = _payload_json(evidence)
        self._ensure_initialized()
        connection = self._connect_existing()
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            inserted = self._insert_immutable(
                connection,
                table="evidence",
                key_column="evidence_id",
                key_value=evidence.evidence_id,
                columns=(
                    "evidence_id", "evidence_sha256", "life_id", "domain",
                    "world_scope_hash", "principal_scope_hash", "independence_group_hash",
                    "evidence_class", "source_kind", "observed_at_ms", "valid_until_ms", "payload_json",
                ),
                values=(
                    evidence.evidence_id, evidence.evidence_sha256, evidence.life_id, evidence.domain,
                    evidence.world_scope_hash, evidence.principal_scope_hash, evidence.independence_group_hash,
                    evidence.evidence_class, evidence.source_ref.source_kind, evidence.observed_at_ms,
                    evidence.valid_until_ms, payload,
                ),
                payload_json=payload,
            )
            connection.commit()
            return inserted
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise CognitionIntegrityError(f"evidence uniqueness violation: {exc}") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _last_revision_row(self, connection: sqlite3.Connection, cognition_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT revision_sha256, sequence FROM revisions WHERE cognition_id=? ORDER BY sequence DESC LIMIT 1",
            (cognition_id,),
        ).fetchone()

    def commit_transition(
        self,
        statement: CognitionStatement,
        decision: CognitionRevision,
        *,
        expected_head_sha256: str | None,
    ) -> None:
        """Atomically append statement+decision and CAS the active cognition head."""
        self._validate_hash(statement, "has_valid_statement_sha256", "cognition statement")
        self._validate_hash(decision, "has_valid_revision_sha256", "cognition revision")
        if statement.cognition_id != decision.cognition_id:
            raise CognitionIntegrityError("statement and revision cognition IDs differ")
        if statement.life_id != decision.life_id:
            raise CognitionIntegrityError("statement and revision life IDs differ")
        if statement.statement_sha256 != decision.to_statement_sha256:
            raise CognitionIntegrityError("revision does not point to the supplied statement")
        if statement.revision != decision.sequence:
            raise CognitionIntegrityError("statement revision and decision sequence differ")
        if statement.supersedes_statement_sha256 != expected_head_sha256:
            raise CognitionIntegrityError("statement predecessor does not match expected head")
        if decision.from_statement_sha256 != expected_head_sha256:
            raise CognitionIntegrityError("decision predecessor does not match expected head")

        statement_payload = _payload_json(statement)
        decision_payload = _payload_json(decision)
        self._ensure_initialized()
        connection = self._connect_existing()
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            head = connection.execute(
                "SELECT statement_sha256, revision FROM cognition_heads WHERE cognition_id=?",
                (statement.cognition_id,),
            ).fetchone()
            actual_head = str(head[0]) if head is not None else None
            actual_revision = int(head[1]) if head is not None else 0
            if actual_head != expected_head_sha256:
                raise CognitionConflictError(
                    f"stale cognition head: expected {expected_head_sha256!r}, actual {actual_head!r}"
                )
            if statement.revision != actual_revision + 1:
                raise CognitionIntegrityError("cognition statement revision is not contiguous")

            previous_decision = self._last_revision_row(connection, statement.cognition_id)
            expected_previous_decision = str(previous_decision[0]) if previous_decision is not None else None
            expected_sequence = int(previous_decision[1]) + 1 if previous_decision is not None else 1
            if decision.previous_revision_sha256 != expected_previous_decision:
                raise CognitionIntegrityError("revision decision chain predecessor is invalid")
            if decision.sequence != expected_sequence:
                raise CognitionIntegrityError("revision decision sequence is not contiguous")

            self._insert_immutable(
                connection,
                table="statements",
                key_column="statement_sha256",
                key_value=statement.statement_sha256,
                columns=(
                    "statement_sha256", "cognition_id", "revision", "life_id", "domain",
                    "world_scope_hash", "principal_scope_hash", "privacy_scope", "status",
                    "stability_level", "confidence_milli", "valid_until_ms", "payload_json",
                ),
                values=(
                    statement.statement_sha256, statement.cognition_id, statement.revision,
                    statement.life_id, statement.domain, statement.world_scope_hash,
                    statement.principal_scope_hash, statement.privacy_scope, statement.status,
                    statement.stability_level, statement.confidence_milli,
                    statement.valid_until_ms, statement_payload,
                ),
                payload_json=statement_payload,
            )
            self._insert_immutable(
                connection,
                table="revisions",
                key_column="revision_sha256",
                key_value=decision.revision_sha256,
                columns=("revision_sha256", "cognition_revision_id", "cognition_id", "sequence", "created_at_ms", "payload_json"),
                values=(decision.revision_sha256, decision.cognition_revision_id, decision.cognition_id, decision.sequence, decision.created_at_ms, decision_payload),
                payload_json=decision_payload,
            )
            now_ms = int(time.time() * 1000)
            if head is None:
                connection.execute(
                    "INSERT INTO cognition_heads(cognition_id, statement_sha256, revision, updated_at_ms) VALUES(?,?,?,?)",
                    (statement.cognition_id, statement.statement_sha256, statement.revision, now_ms),
                )
            else:
                cursor = connection.execute(
                    "UPDATE cognition_heads SET statement_sha256=?, revision=?, updated_at_ms=? WHERE cognition_id=? AND statement_sha256=? AND revision=?",
                    (
                        statement.statement_sha256, statement.revision, now_ms,
                        statement.cognition_id, actual_head, actual_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CognitionConflictError("cognition head CAS failed")
            connection.commit()
        except (CognitionConflictError, CognitionIntegrityError):
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise CognitionIntegrityError(f"cognition transition uniqueness violation: {exc}") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _model_from_row(model_type: type, row: sqlite3.Row | None) -> object | None:
        if row is None:
            return None
        return model_type.model_validate_json(str(row[0]))

    def get_prior_by_sha(self, sha256: str) -> CognitionPrior | None:
        connection = self._connect_existing()
        if connection is None:
            return None
        try:
            row = connection.execute("SELECT payload_json FROM priors WHERE prior_sha256=?", (sha256,)).fetchone()
            return self._model_from_row(CognitionPrior, row)  # type: ignore[return-value]
        finally:
            connection.close()

    def get_evidence(self, evidence_id: str) -> CognitionEvidence | None:
        connection = self._connect_existing()
        if connection is None:
            return None
        try:
            row = connection.execute("SELECT payload_json FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
            return self._model_from_row(CognitionEvidence, row)  # type: ignore[return-value]
        finally:
            connection.close()

    def get_evidence_many(self, evidence_ids: Iterable[str]) -> list[CognitionEvidence]:
        ids = tuple(dict.fromkeys(str(item) for item in evidence_ids if str(item)))
        if not ids:
            return []
        connection = self._connect_existing()
        if connection is None:
            return []
        try:
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT evidence_id, payload_json FROM evidence WHERE evidence_id IN ({placeholders})",
                ids,
            ).fetchall()
            by_id = {
                str(row[0]): CognitionEvidence.model_validate_json(str(row[1]))
                for row in rows
            }
            return [by_id[item] for item in ids if item in by_id]
        finally:
            connection.close()

    def find_evidence_by_lineage_root(
        self, lineage_root_hash: str
    ) -> tuple[CognitionEvidence, ...]:
        """Return every evidence row carrying one lineage root hash."""

        connection = self._connect_existing()
        if connection is None:
            return ()
        try:
            rows = connection.execute(
                "SELECT payload_json FROM evidence"
            ).fetchall()
            # Parse each row once; corrupt rows still raise exactly as before.
            evidence_rows = [
                CognitionEvidence.model_validate_json(str(row[0])) for row in rows
            ]
            return tuple(
                evidence
                for evidence in evidence_rows
                if lineage_root_hash in {
                    str(item) for item in evidence.lineage_root_hashes
                }
            )
        finally:
            connection.close()

    def get_statement_by_sha(self, sha256: str) -> CognitionStatement | None:
        connection = self._connect_existing()
        if connection is None:
            return None
        try:
            row = connection.execute("SELECT payload_json FROM statements WHERE statement_sha256=?", (sha256,)).fetchone()
            return self._model_from_row(CognitionStatement, row)  # type: ignore[return-value]
        finally:
            connection.close()

    def get_head(self, cognition_id: str) -> CognitionStatement | None:
        connection = self._connect_existing()
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT s.payload_json FROM cognition_heads h JOIN statements s ON s.statement_sha256=h.statement_sha256 WHERE h.cognition_id=?",
                (cognition_id,),
            ).fetchone()
            return self._model_from_row(CognitionStatement, row)  # type: ignore[return-value]
        finally:
            connection.close()

    def get_latest_revision(self, cognition_id: str) -> CognitionRevision | None:
        connection = self._connect_existing()
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT payload_json FROM revisions WHERE cognition_id=? ORDER BY sequence DESC LIMIT 1",
                (cognition_id,),
            ).fetchone()
            return self._model_from_row(CognitionRevision, row)  # type: ignore[return-value]
        finally:
            connection.close()

    def list_active_heads(
        self,
        *,
        life_id: str,
        domain: str,
        world_scope_hash: str,
        principal_scope_hash: str,
        statuses: Sequence[str] = ("STABLE", "CORE"),
        limit: int = 1000,
    ) -> list[CognitionStatement]:
        if not statuses or limit <= 0:
            return []
        connection = self._connect_existing()
        if connection is None:
            return []
        try:
            placeholders = ",".join("?" for _ in statuses)
            rows = connection.execute(
                f"""
                SELECT s.payload_json
                FROM cognition_heads h
                JOIN statements s ON s.statement_sha256=h.statement_sha256
                WHERE s.life_id=? AND s.domain=? AND s.world_scope_hash=?
                  AND s.principal_scope_hash=? AND s.status IN ({placeholders})
                ORDER BY s.stability_level DESC, s.confidence_milli DESC, s.cognition_id ASC
                LIMIT ?
                """,
                (life_id, domain, world_scope_hash, principal_scope_hash, *statuses, int(limit)),
            ).fetchall()
            return [CognitionStatement.model_validate_json(str(row[0])) for row in rows]
        finally:
            connection.close()

    def list_priors(self, *, life_id: str, domain: str, status: str = "active") -> list[CognitionPrior]:
        connection = self._connect_existing()
        if connection is None:
            return []
        try:
            rows = connection.execute(
                """
                SELECT p.payload_json FROM priors p
                JOIN (
                    SELECT prior_id, MAX(revision) AS max_revision
                    FROM priors WHERE life_id=? AND domain=? GROUP BY prior_id
                ) latest ON latest.prior_id=p.prior_id AND latest.max_revision=p.revision
                WHERE p.life_id=? AND p.domain=? AND p.status=?
                ORDER BY p.prior_id ASC
                """,
                (life_id, domain, life_id, domain, status),
            ).fetchall()
            return [CognitionPrior.model_validate_json(str(row[0])) for row in rows]
        finally:
            connection.close()

    def counts(self) -> dict[str, int]:
        connection = self._connect_existing()
        if connection is None:
            return {"priors": 0, "evidence": 0, "statements": 0, "revisions": 0, "heads": 0}
        try:
            return {
                name: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for name, table in (
                    ("priors", "priors"),
                    ("evidence", "evidence"),
                    ("statements", "statements"),
                    ("revisions", "revisions"),
                    ("heads", "cognition_heads"),
                )
            }
        finally:
            connection.close()


__all__ = [
    "CognitionConflictError",
    "CognitionIntegrityError",
    "CognitionStoreError",
    "SCHEMA_VERSION",
    "WorldCognitionStore",
]
