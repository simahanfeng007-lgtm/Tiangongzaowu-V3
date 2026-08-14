"""Memory SSoT persistence on the Life store's single SQLite connection."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import unicodedata
from typing import Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from contracts import (
    CausalNodeV3, MemoryAssertionV3, MemoryDerivationV1, MemoryInvalidationRecord,
    MemoryParentRef, MemoryRelationV3, PrivacyDeletionTombstone,
    canonical_json_bytes, canonical_sha256, derive_promotion_key, retention_priority,
)
from contracts.world_understanding.memory_candidate import MemoryWorldCandidate
from .store_contract_support import (
    LifeShadowStoreError, MemoryDeletionResult, ProtectedPayloadRecord,
    _parse_stored_contract, _revalidate_contract,
)


def _normalize_search_term(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("memory search term must be text")
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    if (
        not normalized
        or len(normalized) > 256
        or "\x00" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError("memory search term is invalid")
    return normalized


def _protected_payload_aad(
    *, payload_id: str, life_id: str, privacy_scope: str
) -> bytes:
    return canonical_json_bytes(
        {
            "domain": "tiangong.life.protected-payload.v1",
            "life_id": life_id,
            "payload_id": payload_id,
            "privacy_scope": privacy_scope,
        }
    )


class LifeMemoryRepository:
    """Memory repository; connection lifecycle and schema stay outside."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @staticmethod
    def _protected_payload_record_from_row(
            row: sqlite3.Row,
            *,
            key_available: bool,
        ) -> ProtectedPayloadRecord:
            ciphertext = bytes(row["ciphertext"])
            if hashlib.sha256(ciphertext).hexdigest() != str(row["ciphertext_sha256"]):
                raise LifeShadowStoreError("protected payload ciphertext digest is invalid")
            return ProtectedPayloadRecord(
                payload_id=str(row["payload_id"]),
                life_id=str(row["life_id"]),
                privacy_scope=str(row["privacy_scope"]),
                ciphertext_sha256=str(row["ciphertext_sha256"]),
                created_at_ms=int(row["created_at_ms"]),
                key_available=key_available,
                key_destroyed_at_ms=(
                    None
                    if row["key_destroyed_at_ms"] is None
                    else int(row["key_destroyed_at_ms"])
                ),
            )

    def _put_protected_payload_locked(
            self,
            plaintext: bytes,
            *,
            life_id: str,
            privacy_scope: str,
            created_at_ms: int,
        ) -> ProtectedPayloadRecord:
            if (
                not isinstance(plaintext, bytes)
                or not plaintext
                or len(plaintext) > 16 * 1024 * 1024
                or not life_id
                or not privacy_scope
                or isinstance(created_at_ms, bool)
                or not isinstance(created_at_ms, int)
                or created_at_ms < 0
            ):
                raise ValueError("protected payload input is invalid")
            payload_id = "ppd_" + secrets.token_hex(32)
            if self._connection.execute(
                "SELECT 1 FROM protected_payloads WHERE payload_id = ?", (payload_id,)
            ).fetchone() is not None:
                raise LifeShadowStoreError("protected payload random identity collided")
            key = AESGCM.generate_key(bit_length=256)
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(key).encrypt(
                nonce,
                plaintext,
                _protected_payload_aad(
                    payload_id=payload_id,
                    life_id=life_id,
                    privacy_scope=privacy_scope,
                ),
            )
            digest = hashlib.sha256(ciphertext).hexdigest()
            self._connection.execute(
                """
                INSERT INTO protected_payloads(
                    payload_id, life_id, privacy_scope, nonce, ciphertext,
                    ciphertext_sha256, created_at_ms, key_destroyed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    payload_id,
                    life_id,
                    privacy_scope,
                    nonce,
                    ciphertext,
                    digest,
                    created_at_ms,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO protected_payload_keys(payload_id, key_material, created_at_ms)
                VALUES (?, ?, ?)
                """,
                (payload_id, key, created_at_ms),
            )
            row = self._connection.execute(
                "SELECT * FROM protected_payloads WHERE payload_id = ?", (payload_id,)
            ).fetchone()
            assert row is not None
            return self._protected_payload_record_from_row(row, key_available=True)

    def put_protected_payload(
            self,
            plaintext: bytes,
            *,
            life_id: str,
            privacy_scope: str,
            created_at_ms: int,
        ) -> ProtectedPayloadRecord:
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                record = self._put_protected_payload_locked(
                    plaintext,
                    life_id=life_id,
                    privacy_scope=privacy_scope,
                    created_at_ms=created_at_ms,
                )
                connection.execute("COMMIT")
                return record
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def get_protected_payload(self, payload_id: str) -> ProtectedPayloadRecord | None:
            row = self._connection.execute(
                "SELECT * FROM protected_payloads WHERE payload_id = ?", (payload_id,)
            ).fetchone()
            if row is None:
                return None
            key = self._connection.execute(
                "SELECT 1 FROM protected_payload_keys WHERE payload_id = ?", (payload_id,)
            ).fetchone()
            return self._protected_payload_record_from_row(
                row, key_available=key is not None
            )

    def read_protected_payload(self, payload_id: str) -> bytes:
            row = self._connection.execute(
                """
                SELECT p.*, k.key_material
                FROM protected_payloads AS p
                LEFT JOIN protected_payload_keys AS k ON k.payload_id = p.payload_id
                WHERE p.payload_id = ?
                """,
                (payload_id,),
            ).fetchone()
            if row is None:
                raise LifeShadowStoreError("protected payload does not exist")
            self._protected_payload_record_from_row(
                row, key_available=row["key_material"] is not None
            )
            if row["key_material"] is None or row["key_destroyed_at_ms"] is not None:
                raise LifeShadowStoreError("protected payload key is unavailable")
            try:
                return AESGCM(bytes(row["key_material"])).decrypt(
                    bytes(row["nonce"]),
                    bytes(row["ciphertext"]),
                    _protected_payload_aad(
                        payload_id=str(row["payload_id"]),
                        life_id=str(row["life_id"]),
                        privacy_scope=str(row["privacy_scope"]),
                    ),
                )
            except Exception as exc:
                raise LifeShadowStoreError("protected payload cannot be decrypted") from exc

    def _get_or_create_index_key_locked(
            self, life_id: str, *, created_at_ms: int
        ) -> bytes:
            row = self._connection.execute(
                "SELECT key_material FROM life_index_keys WHERE life_id = ?", (life_id,)
            ).fetchone()
            if row is not None:
                return bytes(row["key_material"])
            key = secrets.token_bytes(32)
            self._connection.execute(
                "INSERT INTO life_index_keys(life_id, key_material, created_at_ms) VALUES (?, ?, ?)",
                (life_id, key, created_at_ms),
            )
            return key

    @staticmethod
    def _term_digests(key: bytes, terms: tuple[str, ...]) -> tuple[str, ...]:
            normalized = tuple(sorted({_normalize_search_term(term) for term in terms}))
            return tuple(
                sorted(
                    hmac.new(key, term.encode("utf-8"), hashlib.sha256).hexdigest()
                    for term in normalized
                )
            )

    def _assert_protected_payload_binding_locked(
            self,
            *,
            payload_id: str,
            payload_sha256: str,
            life_id: str,
            privacy_scope: str,
        ) -> None:
            row = self._connection.execute(
                """
                SELECT p.*, k.payload_id AS key_id
                FROM protected_payloads AS p
                LEFT JOIN protected_payload_keys AS k ON k.payload_id = p.payload_id
                WHERE p.payload_id = ?
                """,
                (payload_id,),
            ).fetchone()
            if (
                row is None
                or row["key_id"] is None
                or row["key_destroyed_at_ms"] is not None
                or str(row["ciphertext_sha256"]) != payload_sha256
                or str(row["life_id"]) != life_id
                or str(row["privacy_scope"]) != privacy_scope
            ):
                raise LifeShadowStoreError("protected payload binding is invalid")

    def _record_memory_change_locked(
            self,
            *,
            life_id: str,
            memory_id: str,
            revision: int,
            change_kind: str,
            assertion_sha256: str,
            created_at_ms: int,
        ) -> int:
            """Append one globally monotonic memory change plus its outbox row.

            Must be called inside an open transaction so the change and the
            assertion/tombstone it describes commit or roll back together.
            """

            if change_kind not in {"assert", "revise", "tombstone"}:
                raise LifeShadowStoreError("memory change kind is invalid")
            connection = self._connection
            row = connection.execute(
                "SELECT COALESCE(MAX(change_seq), 0) + 1 AS next_seq FROM memory_change_log"
            ).fetchone()
            change_seq = int(row["next_seq"])
            connection.execute(
                """
                INSERT INTO memory_change_log(
                    change_seq, life_id, memory_id, revision, change_kind,
                    assertion_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change_seq,
                    life_id,
                    memory_id,
                    revision,
                    change_kind,
                    assertion_sha256,
                    created_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_outbox(
                    change_seq, life_id, memory_id, change_kind, created_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (change_seq, life_id, memory_id, change_kind, created_at_ms),
            )
            return change_seq

    def _put_memory_assertion_locked(
            self,
            assertion: MemoryAssertionV3,
            payload: bytes,
            *,
            search_terms: tuple[str, ...] = (),
        ) -> tuple[bool, int | None]:
            """Transaction-scoped body of ``put_memory_assertion``.

            Returns ``(created, change_seq)``; the change seq is committed in the
            same transaction as the assertion revision it numbers.
            """

            connection = self._connection
            existing = connection.execute(
                """
                SELECT a.*, c.payload, c.assertion_sha256
                FROM memory_assertions AS a
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.memory_id = ? AND a.revision = ?
                """,
                (assertion.memory_id, assertion.revision),
            ).fetchone()
            if existing is not None:
                if (
                    bytes(existing["payload"]) != payload
                    or str(existing["assertion_sha256"])
                    != assertion.assertion_sha256
                ):
                    raise LifeShadowStoreError("memory assertion identity was rebound")
                return False, None
            previous = connection.execute(
                """
                SELECT a.revision, a.status, c.assertion_sha256
                FROM memory_assertions AS a
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
                """,
                (assertion.memory_id,),
            ).fetchone()
            if assertion.revision == 1:
                if previous is not None:
                    raise LifeShadowStoreError("memory assertion genesis already exists")
            elif (
                previous is None
                or int(previous["revision"]) + 1 != assertion.revision
                or str(previous["assertion_sha256"])
                != assertion.supersedes_assertion_sha256
                or str(previous["status"]) == "deleted"
            ):
                raise LifeShadowStoreError("memory assertion revision is discontinuous")
            assert assertion.protected_payload_id is not None
            assert assertion.protected_payload_sha256 is not None
            self._assert_protected_payload_binding_locked(
                payload_id=assertion.protected_payload_id,
                payload_sha256=assertion.protected_payload_sha256,
                life_id=assertion.life_id,
                privacy_scope=assertion.privacy_scope,
            )
            connection.execute(
                "DELETE FROM memory_search_terms WHERE memory_id = ?",
                (assertion.memory_id,),
            )
            connection.execute(
                """
                INSERT INTO memory_assertions(
                    memory_id, revision, life_id, status, privacy_scope,
                    payload_object_id, payload_sha256, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assertion.memory_id,
                    assertion.revision,
                    assertion.life_id,
                    assertion.lifecycle_status,
                    assertion.privacy_scope,
                    assertion.protected_payload_id,
                    assertion.protected_payload_sha256,
                    assertion.created_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_assertion_contracts(
                    memory_id, revision, payload, assertion_sha256
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    assertion.memory_id,
                    assertion.revision,
                    payload,
                    assertion.assertion_sha256,
                ),
            )
            if assertion.lifecycle_status == "active" and search_terms:
                key = self._get_or_create_index_key_locked(
                    assertion.life_id, created_at_ms=assertion.created_at_ms
                )
                for digest in self._term_digests(key, search_terms):
                    connection.execute(
                        """
                        INSERT INTO memory_search_terms(
                            memory_id, revision, term_hmac_sha256,
                            privacy_scope, created_at_ms
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            assertion.memory_id,
                            assertion.revision,
                            digest,
                            assertion.privacy_scope,
                            assertion.created_at_ms,
                        ),
                    )
            change_seq = self._record_memory_change_locked(
                life_id=assertion.life_id,
                memory_id=assertion.memory_id,
                revision=assertion.revision,
                change_kind="assert" if assertion.revision == 1 else "revise",
                assertion_sha256=assertion.assertion_sha256,
                created_at_ms=assertion.created_at_ms,
            )
            return True, change_seq

    def put_memory_assertion(
            self,
            assertion: MemoryAssertionV3,
            *,
            search_terms: tuple[str, ...] = (),
        ) -> bool:
            assertion, payload = _revalidate_contract(
                assertion, MemoryAssertionV3, "memory assertion"
            )
            if not assertion.has_valid_assertion_sha256():
                raise LifeShadowStoreError("memory assertion digest is invalid")
            if assertion.lifecycle_status == "deleted":
                raise LifeShadowStoreError(
                    "deleted memory must be committed by the privacy-deletion boundary"
                )
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                created, _ = self._put_memory_assertion_locked(
                    assertion, payload, search_terms=search_terms
                )
                connection.execute("COMMIT")
                return created
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def put_live_memory_assertion(
            self,
            plaintext: bytes,
            *,
            memory_id: str,
            life_id: str,
            assertion_kind: str,
            epistemic_status: str,
            lifecycle_status: str,
            privacy_scope: str,
            retention_class: str,
            source_event_ids: tuple[str, ...] = (),
            causal_utility_milli: int = 0,
            user_importance_milli: int = 0,
            verification_strength_milli: int = 0,
            future_dependency_milli: int = 0,
            valid_from_ms: int,
            created_at_ms: int,
            search_terms: tuple[str, ...] = (),
            expires_at_ms: int | None = None,
            derivation: MemoryDerivationV1 | None = None,
            activate_head: bool = False,
        ) -> tuple[MemoryAssertionV3, int, bool]:
            """Commit one live user-fact assertion with its payload atomically.

            The protected payload, the assertion revision, the global
            ``memory_change_seq`` row and the outbox row commit in one
            transaction.  Repeating the same call (same memory, same latest
            revision, same plaintext, same lifecycle status) is an idempotent
            no-op that returns the existing assertion and its original change
            seq instead of opening a new revision.  When ``derivation`` is
            supplied, the P15 derivation row and optional active head commit in
            the same transaction as the assertion it describes.  The derivation's
            ``memory_assertion_sha256`` is rebound to the actual committed
            assertion digest and its derivation digest recomputed inside the
            transaction, so the caller may pass a placeholder assertion digest.
            """

            if lifecycle_status == "deleted":
                raise LifeShadowStoreError(
                    "deleted memory must be committed by the privacy-deletion boundary"
                )
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                latest_row = connection.execute(
                    """
                    SELECT a.*, c.payload
                    FROM memory_assertions AS a
                    JOIN memory_assertion_contracts AS c
                      ON c.memory_id = a.memory_id AND c.revision = a.revision
                    WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
                    """,
                    (memory_id,),
                ).fetchone()
                if latest_row is not None:
                    latest = _parse_stored_contract(
                        bytes(latest_row["payload"]), MemoryAssertionV3, "memory assertion"
                    )
                    if latest.lifecycle_status == "deleted":
                        raise LifeShadowStoreError("live memory assertion was deleted")
                    assert latest.protected_payload_id is not None
                    try:
                        previous_plaintext = self.read_protected_payload(
                            latest.protected_payload_id
                        )
                    except LifeShadowStoreError as exc:
                        raise LifeShadowStoreError(
                            "live memory plaintext cannot be verified"
                        ) from exc
                    if previous_plaintext != plaintext:
                        raise LifeShadowStoreError("live memory content drifted")
                    if latest.lifecycle_status == lifecycle_status:
                        change_row = connection.execute(
                            """
                            SELECT change_seq FROM memory_change_log
                            WHERE memory_id = ? AND revision = ?
                            """,
                            (latest.memory_id, latest.revision),
                        ).fetchone()
                        if change_row is None:
                            # Assertions written before the change ledger existed
                            # (legacy migration data) are enrolled on first touch
                            # so every live assertion is seq-addressable.
                            backfilled_seq = self._record_memory_change_locked(
                                life_id=latest.life_id,
                                memory_id=latest.memory_id,
                                revision=latest.revision,
                                change_kind=(
                                    "assert" if latest.revision == 1 else "revise"
                                ),
                                assertion_sha256=latest.assertion_sha256,
                                created_at_ms=latest.created_at_ms,
                            )
                            connection.execute("COMMIT")
                            return latest, backfilled_seq, False
                        connection.execute("COMMIT")
                        return latest, int(change_row["change_seq"]), False
                    revision = latest.revision + 1
                    supersedes = latest.assertion_sha256
                    protected_payload_id = latest.protected_payload_id
                    protected_payload_sha256 = latest.protected_payload_sha256
                else:
                    latest = None
                    revision = 1
                    supersedes = None
                    protected = self._put_protected_payload_locked(
                        plaintext,
                        life_id=life_id,
                        privacy_scope=privacy_scope,
                        created_at_ms=created_at_ms,
                    )
                    protected_payload_id = protected.payload_id
                    protected_payload_sha256 = protected.ciphertext_sha256
                try:
                    assertion = MemoryAssertionV3(
                        memory_id=memory_id,
                        life_id=life_id,
                        revision=revision,
                        supersedes_assertion_sha256=supersedes,
                        assertion_kind=assertion_kind,
                        epistemic_status=epistemic_status,
                        lifecycle_status=lifecycle_status,
                        protected_payload_id=protected_payload_id,
                        protected_payload_sha256=protected_payload_sha256,
                        deletion_tombstone_id=None,
                        privacy_scope=privacy_scope,
                        retention_class=retention_class,
                        source_event_ids=tuple(sorted(set(source_event_ids))),
                        causal_hypothesis_ids=(),
                        causal_utility_milli=causal_utility_milli,
                        user_importance_milli=user_importance_milli,
                        verification_strength_milli=verification_strength_milli,
                        recurrence_count=0,
                        future_dependency_milli=future_dependency_milli,
                        privacy_cost_milli=500,
                        contradiction_penalty_milli=0,
                        staleness_milli=0,
                        valid_from_ms=valid_from_ms,
                        expires_at_ms=expires_at_ms,
                        created_at_ms=created_at_ms,
                        assertion_sha256="0" * 64,
                    ).with_computed_assertion_sha256()
                except Exception as exc:
                    raise LifeShadowStoreError("live memory assertion contract is invalid") from exc
                payload = canonical_json_bytes(assertion)
                created, change_seq = self._put_memory_assertion_locked(
                    assertion, payload, search_terms=search_terms
                )
                assert created and change_seq is not None
                if derivation is not None:
                    derivation, _payload = _revalidate_contract(
                        derivation, MemoryDerivationV1, "memory derivation"
                    )
                    derivation = derivation.model_copy(
                        update={
                            "memory_assertion_sha256": assertion.assertion_sha256,
                            "memory_revision": assertion.revision,
                        }
                    ).with_computed_derivation_sha256()
                    self._put_memory_derivation_locked(
                        derivation, activate_head=activate_head
                    )
                connection.execute("COMMIT")
                return assertion, change_seq, True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def memory_change_head(self, life_id: str | None = None) -> int:
            """Return the greatest committed memory change seq (0 when empty)."""

            if life_id is None:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(change_seq), 0) AS head FROM memory_change_log"
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(change_seq), 0) AS head FROM memory_change_log WHERE life_id = ?",
                    (life_id,),
                ).fetchone()
            return int(row["head"])

    def memory_change_seq_for(self, memory_id: str, revision: int) -> int | None:
            row = self._connection.execute(
                """
                SELECT change_seq FROM memory_change_log
                WHERE memory_id = ? AND revision = ?
                """,
                (memory_id, revision),
            ).fetchone()
            return None if row is None else int(row["change_seq"])

    def list_memory_outbox(
            self,
            *,
            life_id: str | None = None,
            pending_only: bool = True,
            limit: int = 256,
        ) -> tuple[dict[str, Any], ...]:
            if not 1 <= limit <= 4096:
                raise ValueError("memory outbox limit is invalid")
            clauses = []
            values: list[object] = []
            if life_id is not None:
                clauses.append("o.life_id = ?")
                values.append(life_id)
            if pending_only:
                clauses.append("o.receipt_id IS NULL")
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = self._connection.execute(
                f"""
                SELECT o.change_seq, o.life_id, o.memory_id, o.change_kind,
                       o.receipt_id, o.receipt_sha256, o.delivered_at_ms,
                       o.created_at_ms, l.revision, l.assertion_sha256
                FROM memory_outbox AS o
                JOIN memory_change_log AS l ON l.change_seq = o.change_seq
                {where}
                ORDER BY o.change_seq
                LIMIT ?
                """,
                (*values, limit),
            ).fetchall()
            return tuple(
                {
                    "change_seq": int(row["change_seq"]),
                    "life_id": str(row["life_id"]),
                    "memory_id": str(row["memory_id"]),
                    "revision": int(row["revision"]),
                    "change_kind": str(row["change_kind"]),
                    "assertion_sha256": str(row["assertion_sha256"]),
                    "receipt_id": (
                        None if row["receipt_id"] is None else str(row["receipt_id"])
                    ),
                    "receipt_sha256": (
                        None
                        if row["receipt_sha256"] is None
                        else str(row["receipt_sha256"])
                    ),
                    "delivered_at_ms": (
                        None
                        if row["delivered_at_ms"] is None
                        else int(row["delivered_at_ms"])
                    ),
                    "created_at_ms": int(row["created_at_ms"]),
                }
                for row in rows
            )

    def count_pending_memory_outbox(self, life_id: str | None = None) -> int:
            if life_id is None:
                row = self._connection.execute(
                    "SELECT count(*) AS pending FROM memory_outbox WHERE receipt_id IS NULL"
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT count(*) AS pending FROM memory_outbox WHERE receipt_id IS NULL AND life_id = ?",
                    (life_id,),
                ).fetchone()
            return int(row["pending"])

    def _derivation_from_row(self, row: sqlite3.Row) -> MemoryDerivationV1:
            return _parse_stored_contract(
                bytes(row["payload"]), MemoryDerivationV1, "memory derivation"
            )

    def put_memory_derivation(
            self,
            derivation: MemoryDerivationV1,
            *,
            activate_head: bool = False,
        ) -> bool:
            """Append one memory derivation and its parent edges atomically.

            Repeating the exact same derivation is an idempotent no-op returning
            False.  Promotion and learning origins require at least one parent;
            every parent must already exist, must share life/principal/privacy
            scope, must predate the child, and every parent lineage root must be
            inherited by the child.  Parent edges come from the derivation
            contract's ``parent_memory_refs`` field.
            """

            derivation, _payload = _revalidate_contract(
                derivation, MemoryDerivationV1, "memory derivation"
            )
            if not derivation.has_valid_derivation_sha256():
                raise LifeShadowStoreError("memory derivation digest is invalid")
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                created = self._put_memory_derivation_locked(
                    derivation,
                    activate_head=activate_head,
                )
                connection.execute("COMMIT")
                return created
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _put_memory_derivation_locked(
            self,
            derivation: MemoryDerivationV1,
            *,
            activate_head: bool,
        ) -> bool:
            connection = self._connection
            parents = derivation.parent_memory_refs
            existing = connection.execute(
                "SELECT derivation_id FROM memory_derivations WHERE derivation_id = ?",
                (derivation.derivation_id,),
            ).fetchone()
            if existing is not None:
                return False
            duplicate = connection.execute(
                "SELECT derivation_id FROM memory_derivations WHERE derivation_sha256 = ?",
                (derivation.derivation_sha256,),
            ).fetchone()
            if duplicate is not None:
                raise LifeShadowStoreError(
                    "memory derivation digest collides with another id"
                )
            same_slot = connection.execute(
                """
                SELECT derivation_id FROM memory_derivations
                WHERE life_id = ? AND memory_id = ? AND memory_revision = ? AND layer = ?
                """,
                (
                    derivation.life_id,
                    derivation.memory_id,
                    derivation.memory_revision,
                    derivation.layer,
                ),
            ).fetchone()
            if same_slot is not None:
                raise LifeShadowStoreError(
                    "memory derivation slot is already occupied"
                )
            assertion = connection.execute(
                """
                SELECT a.life_id, a.privacy_scope, c.assertion_sha256
                FROM memory_assertions AS a
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.memory_id = ? AND a.revision = ?
                """,
                (derivation.memory_id, derivation.memory_revision),
            ).fetchone()
            if assertion is None:
                raise LifeShadowStoreError(
                    "memory derivation references a missing assertion"
                )
            if (
                str(assertion["life_id"]) != derivation.life_id
                or str(assertion["privacy_scope"]) != derivation.privacy_scope
                or str(assertion["assertion_sha256"])
                != derivation.memory_assertion_sha256
            ):
                raise LifeShadowStoreError(
                    "memory derivation assertion binding is inconsistent"
                )
            if derivation.origin in {"PROMOTION", "LEARNING_RESULT"} and not parents:
                raise LifeShadowStoreError(
                    "promotion derivation requires at least one parent"
                )
            child_roots = set(derivation.lineage_root_event_ids)
            parent_rows: list[tuple[object, ...]] = []
            for parent in parents:
                if parent.parent_derivation_id is None:
                    raise LifeShadowStoreError(
                        "memory parent ref must name a derivation"
                    )
                parent_row = connection.execute(
                    "SELECT * FROM memory_derivations WHERE derivation_id = ?",
                    (parent.parent_derivation_id,),
                ).fetchone()
                if parent_row is None:
                    raise LifeShadowStoreError(
                        "memory parent derivation does not exist"
                    )
                parent_contract = self._derivation_from_row(parent_row)
                if (
                    parent_contract.life_id != derivation.life_id
                    or parent_contract.principal_ref != derivation.principal_ref
                    or parent_contract.privacy_scope != derivation.privacy_scope
                ):
                    raise LifeShadowStoreError(
                        "memory derivation parent scope mismatch"
                    )
                if parent_contract.created_at_ms >= derivation.created_at_ms:
                    raise LifeShadowStoreError(
                        "memory derivation parent must predate the child"
                    )
                if (
                    parent_contract.memory_id != parent.memory_id
                    or parent_contract.memory_revision != parent.memory_revision
                    or parent_contract.memory_assertion_sha256
                    != parent.assertion_sha256
                ):
                    raise LifeShadowStoreError(
                        "memory parent ref does not match the parent derivation"
                    )
                if not parent.has_valid_parent_ref_sha256():
                    raise LifeShadowStoreError(
                        "memory parent ref digest is invalid"
                    )
                if not set(parent_contract.lineage_root_event_ids).issubset(
                    child_roots
                ):
                    raise LifeShadowStoreError(
                        "memory derivation drops a parent lineage root"
                    )
                parent_rows.append(
                    (
                        derivation.derivation_id,
                        parent.parent_derivation_id,
                        parent.memory_id,
                        parent.memory_revision,
                        parent.assertion_sha256,
                        parent.parent_ref_sha256,
                        derivation.created_at_ms,
                    )
                )
            payload = canonical_json_bytes(derivation)
            promotion_key = derive_promotion_key(
                policy_version=derivation.promotion_policy_version,
                life_id=derivation.life_id,
                target_layer=derivation.layer,
                parent_assertion_sha256=tuple(
                    parent.assertion_sha256 for parent in parents
                ),
                semantic_domain=derivation.semantic_domain,
                claim_key=derivation.claim_key,
                lineage_root_event_ids=derivation.lineage_root_event_ids,
            )
            key_row = connection.execute(
                "SELECT derivation_id FROM memory_derivations WHERE promotion_key = ?",
                (promotion_key,),
            ).fetchone()
            if key_row is not None:
                raise LifeShadowStoreError(
                    "memory derivation promotion key is already committed"
                )
            connection.execute(
                """
                INSERT INTO memory_derivations(
                    derivation_id, life_id, memory_id, memory_revision,
                    memory_assertion_sha256, layer, semantic_domain, origin,
                    principal_ref, workspace_ref, privacy_scope, claim_key,
                    source_event_ids_json, lineage_root_event_ids_json,
                    external_evidence_refs_json, promotion_policy_version,
                    promotion_reason_codes_json, valid_from_ms, expires_at_ms,
                    context_eligible, learning_eligible, temperament_eligible,
                    self_cognition_eligible, world_candidate_eligible,
                    created_at_ms, promotion_key, derivation_sha256, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    derivation.derivation_id,
                    derivation.life_id,
                    derivation.memory_id,
                    derivation.memory_revision,
                    derivation.memory_assertion_sha256,
                    derivation.layer,
                    derivation.semantic_domain,
                    derivation.origin,
                    derivation.principal_ref,
                    derivation.workspace_ref,
                    derivation.privacy_scope,
                    derivation.claim_key,
                    canonical_json_bytes(
                        derivation.source_event_ids
                    ).decode("utf-8"),
                    canonical_json_bytes(
                        derivation.lineage_root_event_ids
                    ).decode("utf-8"),
                    canonical_json_bytes(
                        derivation.external_evidence_refs
                    ).decode("utf-8"),
                    derivation.promotion_policy_version,
                    canonical_json_bytes(
                        derivation.promotion_reason_codes
                    ).decode("utf-8"),
                    derivation.valid_from_ms,
                    derivation.expires_at_ms,
                    int(derivation.context_eligible),
                    int(derivation.learning_eligible),
                    int(derivation.temperament_eligible),
                    int(derivation.self_cognition_eligible),
                    int(derivation.world_candidate_eligible),
                    derivation.created_at_ms,
                    promotion_key,
                    derivation.derivation_sha256,
                    payload,
                ),
            )
            for parent_row in parent_rows:
                connection.execute(
                    """
                    INSERT INTO memory_derivation_parents(
                        derivation_id, parent_derivation_id, parent_memory_id,
                        parent_revision, parent_assertion_sha256,
                        parent_ref_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    parent_row,
                )
            if activate_head:
                connection.execute(
                    """
                    INSERT INTO memory_active_heads(
                        life_id, principal_ref, claim_key, layer,
                        derivation_id, memory_id, memory_revision,
                        assertion_sha256, activated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(life_id, principal_ref, claim_key, layer)
                    DO UPDATE SET
                        derivation_id = excluded.derivation_id,
                        memory_id = excluded.memory_id,
                        memory_revision = excluded.memory_revision,
                        assertion_sha256 = excluded.assertion_sha256,
                        activated_at_ms = excluded.activated_at_ms
                    """,
                    (
                        derivation.life_id,
                        derivation.principal_ref,
                        derivation.claim_key,
                        derivation.layer,
                        derivation.derivation_id,
                        derivation.memory_id,
                        derivation.memory_revision,
                        derivation.memory_assertion_sha256,
                        derivation.created_at_ms,
                    ),
                )
            return True

    def get_memory_derivation(
            self, derivation_id: str
        ) -> MemoryDerivationV1 | None:
            row = self._connection.execute(
                "SELECT payload FROM memory_derivations WHERE derivation_id = ?",
                (derivation_id,),
            ).fetchone()
            return None if row is None else self._derivation_from_row(row)

    def find_derivation(
            self, *, memory_id: str, memory_revision: int, layer: str
        ) -> MemoryDerivationV1 | None:
            """Return the derivation occupying one assertion layer slot, if any."""

            row = self._connection.execute(
                """
                SELECT payload FROM memory_derivations
                WHERE memory_id = ? AND memory_revision = ? AND layer = ?
                LIMIT 1
                """,
                (memory_id, memory_revision, layer),
            ).fetchone()
            return None if row is None else self._derivation_from_row(row)

    def has_derivation_for_assertion(
            self, memory_id: str, memory_revision: int
        ) -> bool:
            row = self._connection.execute(
                """
                SELECT 1 FROM memory_derivations
                WHERE memory_id = ? AND memory_revision = ?
                LIMIT 1
                """,
                (memory_id, memory_revision),
            ).fetchone()
            return row is not None

    def list_derivations_for_memory(
            self, memory_id: str
        ) -> tuple[MemoryDerivationV1, ...]:
            rows = self._connection.execute(
                """
                SELECT payload FROM memory_derivations
                WHERE memory_id = ?
                ORDER BY created_at_ms, derivation_id
                """,
                (memory_id,),
            ).fetchall()
            return tuple(self._derivation_from_row(row) for row in rows)

    def get_derivation_promotion_key(self, derivation_id: str) -> str | None:
            row = self._connection.execute(
                "SELECT promotion_key FROM memory_derivations WHERE derivation_id = ?",
                (derivation_id,),
            ).fetchone()
            return None if row is None else str(row["promotion_key"])

    def list_memory_derivations(
            self,
            *,
            life_id: str | None = None,
            principal_ref: str | None = None,
            layer: str | None = None,
            active_only: bool = False,
            limit: int = 1024,
        ) -> tuple[MemoryDerivationV1, ...]:
            if not 1 <= limit <= 65536:
                raise ValueError("memory derivation list limit is invalid")
            clauses: list[str] = []
            values: list[object] = []
            if life_id is not None:
                clauses.append("life_id = ?")
                values.append(life_id)
            if principal_ref is not None:
                clauses.append("principal_ref = ?")
                values.append(principal_ref)
            if layer is not None:
                clauses.append("layer = ?")
                values.append(layer)
            if active_only:
                clauses.append(
                    "NOT EXISTS (SELECT 1 FROM memory_derivation_invalidations AS i "
                    "WHERE i.derivation_id = memory_derivations.derivation_id)"
                )
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = self._connection.execute(
                f"""
                SELECT payload FROM memory_derivations
                {where}
                ORDER BY created_at_ms, derivation_id
                LIMIT ?
                """,
                (*values, limit),
            ).fetchall()
            return tuple(self._derivation_from_row(row) for row in rows)

    def list_derivation_parents(
            self, derivation_id: str
        ) -> tuple[MemoryParentRef, ...]:
            rows = self._connection.execute(
                """
                SELECT parent_derivation_id, parent_memory_id, parent_revision,
                       parent_assertion_sha256, parent_ref_sha256
                FROM memory_derivation_parents
                WHERE derivation_id = ?
                ORDER BY parent_memory_id, parent_revision
                """,
                (derivation_id,),
            ).fetchall()
            refs: list[MemoryParentRef] = []
            for row in rows:
                ref = MemoryParentRef(
                    parent_derivation_id=str(row["parent_derivation_id"]),
                    memory_id=str(row["parent_memory_id"]),
                    memory_revision=int(row["parent_revision"]),
                    assertion_sha256=str(row["parent_assertion_sha256"]),
                    parent_ref_sha256=str(row["parent_ref_sha256"]),
                )
                if not ref.has_valid_parent_ref_sha256():
                    raise LifeShadowStoreError(
                        "stored memory parent ref digest is invalid"
                    )
                refs.append(ref)
            return tuple(refs)

    def list_derivation_children(
            self, derivation_id: str
        ) -> tuple[MemoryDerivationV1, ...]:
            rows = self._connection.execute(
                """
                SELECT d.payload
                FROM memory_derivations AS d
                JOIN memory_derivation_parents AS p
                  ON p.derivation_id = d.derivation_id
                WHERE p.parent_derivation_id = ?
                ORDER BY d.created_at_ms, d.derivation_id
                """,
                (derivation_id,),
            ).fetchall()
            return tuple(self._derivation_from_row(row) for row in rows)

    def get_active_memory_head(
            self,
            *,
            life_id: str,
            principal_ref: str,
            claim_key: str,
            layer: str,
        ) -> MemoryDerivationV1 | None:
            row = self._connection.execute(
                """
                SELECT d.payload
                FROM memory_active_heads AS h
                JOIN memory_derivations AS d
                  ON d.derivation_id = h.derivation_id
                WHERE h.life_id = ? AND h.principal_ref = ?
                  AND h.claim_key = ? AND h.layer = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_derivation_invalidations AS i
                      WHERE i.derivation_id = h.derivation_id
                  )
                """,
                (life_id, principal_ref, claim_key, layer),
            ).fetchone()
            return None if row is None else self._derivation_from_row(row)

    def list_active_memory_heads(
            self,
            *,
            life_id: str | None = None,
            principal_ref: str | None = None,
        ) -> tuple[MemoryDerivationV1, ...]:
            clauses: list[str] = []
            values: list[object] = []
            if life_id is not None:
                clauses.append("h.life_id = ?")
                values.append(life_id)
            if principal_ref is not None:
                clauses.append("h.principal_ref = ?")
                values.append(principal_ref)
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM memory_derivation_invalidations AS i "
                "WHERE i.derivation_id = h.derivation_id)"
            )
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = self._connection.execute(
                f"""
                SELECT d.payload
                FROM memory_active_heads AS h
                JOIN memory_derivations AS d
                  ON d.derivation_id = h.derivation_id
                {where}
                ORDER BY h.life_id, h.principal_ref, h.claim_key, h.layer
                """,
                values,
            ).fetchall()
            return tuple(self._derivation_from_row(row) for row in rows)

    def get_memory_consumer_offset(self, consumer_id: str, life_id: str) -> int:
            if not consumer_id or len(consumer_id) > 160:
                raise ValueError("memory consumer id is invalid")
            row = self._connection.execute(
                """
                SELECT last_change_seq FROM memory_consumer_offsets
                WHERE consumer_id = ? AND life_id = ?
                """,
                (consumer_id, life_id),
            ).fetchone()
            return 0 if row is None else int(row["last_change_seq"])

    def advance_memory_consumer_offset(
            self,
            consumer_id: str,
            life_id: str,
            last_change_seq: int,
            *,
            updated_at_ms: int,
        ) -> bool:
            """Advance one consumer watermark idempotently; never backwards."""

            if not consumer_id or len(consumer_id) > 160:
                raise ValueError("memory consumer id is invalid")
            if (
                isinstance(last_change_seq, bool)
                or not isinstance(last_change_seq, int)
                or last_change_seq < 0
            ):
                raise ValueError("memory consumer offset is invalid")
            if (
                isinstance(updated_at_ms, bool)
                or not isinstance(updated_at_ms, int)
                or updated_at_ms < 0
            ):
                raise ValueError("memory consumer timestamp is invalid")
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    """
                    SELECT last_change_seq FROM memory_consumer_offsets
                    WHERE consumer_id = ? AND life_id = ?
                    """,
                    (consumer_id, life_id),
                ).fetchone()
                if (
                    current is not None
                    and int(current["last_change_seq"]) > last_change_seq
                ):
                    raise LifeShadowStoreError(
                        "memory consumer offset cannot move backwards"
                    )
                connection.execute(
                    """
                    INSERT INTO memory_consumer_offsets(
                        consumer_id, life_id, last_change_seq, updated_at_ms
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(consumer_id, life_id) DO UPDATE SET
                        last_change_seq = excluded.last_change_seq,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (consumer_id, life_id, last_change_seq, updated_at_ms),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def is_derivation_active(self, derivation_id: str) -> bool:
            if not isinstance(derivation_id, str) or not derivation_id.startswith(
                "mdr_"
            ):
                raise ValueError("memory derivation id is invalid")
            row = self._connection.execute(
                """
                SELECT 1 FROM memory_derivations AS d
                WHERE d.derivation_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_derivation_invalidations AS i
                      WHERE i.derivation_id = d.derivation_id
                  )
                """,
                (derivation_id,),
            ).fetchone()
            return row is not None

    def put_memory_invalidation(
            self, record: MemoryInvalidationRecord
        ) -> bool:
            """Append one derivation-invalidation audit record atomically."""

            record, payload = _revalidate_contract(
                record, MemoryInvalidationRecord, "memory invalidation"
            )
            if not record.has_valid_invalidation_sha256():
                raise LifeShadowStoreError("memory invalidation digest is invalid")
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT invalidation_id FROM memory_derivation_invalidations
                    WHERE invalidation_id = ?
                    """,
                    (record.invalidation_id,),
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    return False
                duplicate = connection.execute(
                    """
                    SELECT invalidation_id FROM memory_derivation_invalidations
                    WHERE invalidation_sha256 = ?
                    """,
                    (record.invalidation_sha256,),
                ).fetchone()
                if duplicate is not None:
                    raise LifeShadowStoreError(
                        "memory invalidation digest collides with another id"
                    )
                derivation = connection.execute(
                    "SELECT * FROM memory_derivations WHERE derivation_id = ?",
                    (record.derivation_id,),
                ).fetchone()
                if derivation is None:
                    raise LifeShadowStoreError(
                        "memory invalidation targets a missing derivation"
                    )
                parsed = self._derivation_from_row(derivation)
                if (
                    parsed.life_id != record.life_id
                    or parsed.principal_ref != record.principal_ref
                    or parsed.memory_id != record.memory_id
                    or parsed.memory_revision != record.memory_revision
                    or parsed.memory_assertion_sha256 != record.assertion_sha256
                ):
                    raise LifeShadowStoreError(
                        "memory invalidation binding is inconsistent"
                    )
                connection.execute(
                    """
                    INSERT INTO memory_derivation_invalidations(
                        invalidation_id, life_id, principal_ref, derivation_id,
                        memory_id, memory_revision, assertion_sha256, reason,
                        source_trigger_ref, invalidated_at_ms,
                        descendant_derivation_ids_json, invalidation_sha256, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.invalidation_id,
                        record.life_id,
                        record.principal_ref,
                        record.derivation_id,
                        record.memory_id,
                        record.memory_revision,
                        record.assertion_sha256,
                        record.reason,
                        record.source_trigger_ref,
                        record.invalidated_at_ms,
                        canonical_json_bytes(
                            record.descendant_derivation_ids
                        ).decode("utf-8"),
                        record.invalidation_sha256,
                        payload,
                    ),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def list_memory_invalidations(
            self,
            *,
            derivation_id: str | None = None,
            life_id: str | None = None,
        ) -> tuple[MemoryInvalidationRecord, ...]:
            clauses: list[str] = []
            values: list[object] = []
            if derivation_id is not None:
                clauses.append("derivation_id = ?")
                values.append(derivation_id)
            if life_id is not None:
                clauses.append("life_id = ?")
                values.append(life_id)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = self._connection.execute(
                f"""
                SELECT payload FROM memory_derivation_invalidations
                {where}
                ORDER BY invalidated_at_ms, invalidation_id
                """,
                values,
            ).fetchall()
            records: list[MemoryInvalidationRecord] = []
            for row in rows:
                records.append(
                    _parse_stored_contract(
                        bytes(row["payload"]),
                        MemoryInvalidationRecord,
                        "memory invalidation",
                    )
                )
            return tuple(records)

    def clear_active_head(
            self,
            *,
            life_id: str,
            principal_ref: str,
            claim_key: str,
            layer: str,
            derivation_id: str | None = None,
        ) -> bool:
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                if derivation_id is None:
                    cursor = connection.execute(
                        """
                        DELETE FROM memory_active_heads
                        WHERE life_id = ? AND principal_ref = ? AND claim_key = ? AND layer = ?
                        """,
                        (life_id, principal_ref, claim_key, layer),
                    )
                else:
                    cursor = connection.execute(
                        """
                        DELETE FROM memory_active_heads
                        WHERE life_id = ? AND principal_ref = ? AND claim_key = ? AND layer = ?
                          AND derivation_id = ?
                        """,
                        (
                            life_id,
                            principal_ref,
                            claim_key,
                            layer,
                            derivation_id,
                        ),
                    )
                connection.execute("COMMIT")
                return cursor.rowcount > 0
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def put_temperament_receipt(
            self,
            *,
            life_id: str,
            derivation_id: str,
            trait_delta_sha256: str,
            adapted_at_ms: int,
            receipt_payload: Mapping[str, object],
        ) -> bool:
            """Persist one exactly-once core-memory temperament adaptation."""

            if (
                isinstance(adapted_at_ms, bool)
                or not isinstance(adapted_at_ms, int)
                or adapted_at_ms < 0
            ):
                raise ValueError("temperament receipt timestamp is invalid")
            payload = canonical_json_bytes(receipt_payload)
            receipt_sha256 = canonical_sha256(
                {
                    "domain": "tiangong.life.temperament-receipt.v1",
                    "life_id": life_id,
                    "derivation_id": derivation_id,
                    "trait_delta_sha256": trait_delta_sha256,
                    "adapted_at_ms": adapted_at_ms,
                }
            )
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT receipt_sha256 FROM temperament_adaptation_receipts
                    WHERE life_id = ? AND derivation_id = ?
                    """,
                    (life_id, derivation_id),
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    return False
                derivation = connection.execute(
                    """
                    SELECT derivation_id FROM memory_derivations
                    WHERE derivation_id = ?
                    """,
                    (derivation_id,),
                ).fetchone()
                if derivation is None:
                    raise LifeShadowStoreError(
                        "temperament receipt targets a missing derivation"
                    )
                connection.execute(
                    """
                    INSERT INTO temperament_adaptation_receipts(
                        life_id, derivation_id, trait_delta_sha256,
                        adapted_at_ms, receipt_sha256, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        life_id,
                        derivation_id,
                        trait_delta_sha256,
                        adapted_at_ms,
                        receipt_sha256,
                        payload,
                    ),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def has_temperament_receipt(
            self, life_id: str, derivation_id: str
        ) -> bool:
            row = self._connection.execute(
                """
                SELECT 1 FROM temperament_adaptation_receipts
                WHERE life_id = ? AND derivation_id = ?
                """,
                (life_id, derivation_id),
            ).fetchone()
            return row is not None

    def list_temperament_receipts(
            self, life_id: str | None = None
        ) -> tuple[dict[str, Any], ...]:
            if life_id is None:
                rows = self._connection.execute(
                    """
                    SELECT life_id, derivation_id, trait_delta_sha256,
                           adapted_at_ms, receipt_sha256
                    FROM temperament_adaptation_receipts
                    ORDER BY adapted_at_ms, derivation_id
                    """
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT life_id, derivation_id, trait_delta_sha256,
                           adapted_at_ms, receipt_sha256
                    FROM temperament_adaptation_receipts
                    WHERE life_id = ?
                    ORDER BY adapted_at_ms, derivation_id
                    """,
                    (life_id,),
                ).fetchall()
            return tuple(dict(row) for row in rows)

    def put_world_candidate_outbox(
            self,
            candidate: MemoryWorldCandidate,
            *,
            derivation_id: str,
            enqueued_at_ms: int,
        ) -> bool:
            """Enqueue one memory world candidate durably and idempotently."""

            candidate, payload = _revalidate_contract(
                candidate,
                MemoryWorldCandidate,
                "memory world candidate",
            )
            if not candidate.has_valid_candidate_sha256():
                raise LifeShadowStoreError(
                    "memory world candidate digest is invalid"
                )
            if (
                isinstance(enqueued_at_ms, bool)
                or not isinstance(enqueued_at_ms, int)
                or enqueued_at_ms < 0
            ):
                raise ValueError("world candidate enqueue timestamp is invalid")
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT candidate_id FROM memory_world_candidate_outbox
                    WHERE candidate_id = ?
                    """,
                    (candidate.candidate_id,),
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    return False
                derivation = connection.execute(
                    """
                    SELECT life_id, principal_ref, privacy_scope
                    FROM memory_derivations WHERE derivation_id = ?
                    """,
                    (derivation_id,),
                ).fetchone()
                if derivation is None:
                    raise LifeShadowStoreError(
                        "world candidate references a missing derivation"
                    )
                if str(derivation["life_id"]) != candidate.life_id:
                    raise LifeShadowStoreError(
                        "world candidate derivation binding is inconsistent"
                    )
                connection.execute(
                    """
                    INSERT INTO memory_world_candidate_outbox(
                        candidate_id, life_id, derivation_id, candidate_sha256,
                        status, enqueued_at_ms, payload
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        candidate.candidate_id,
                        candidate.life_id,
                        derivation_id,
                        candidate.candidate_sha256,
                        enqueued_at_ms,
                        payload,
                    ),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def list_world_candidate_outbox(
            self,
            *,
            status: str = "pending",
            life_id: str | None = None,
            limit: int = 256,
        ) -> tuple[tuple[MemoryWorldCandidate, str], ...]:
            if status not in {"pending", "delivered", "failed"}:
                raise ValueError("world candidate outbox status is invalid")
            if not 1 <= limit <= 4096:
                raise ValueError("world candidate outbox limit is invalid")
            clauses = ["status = ?"]
            values: list[object] = [status]
            if life_id is not None:
                clauses.append("life_id = ?")
                values.append(life_id)
            where = " AND ".join(clauses)
            rows = self._connection.execute(
                f"""
                SELECT payload, derivation_id FROM memory_world_candidate_outbox
                WHERE {where}
                ORDER BY enqueued_at_ms, candidate_id
                LIMIT ?
                """,
                (*values, limit),
            ).fetchall()
            return tuple(
                (
                    _parse_stored_contract(
                        bytes(row["payload"]),
                        MemoryWorldCandidate,
                        "memory world candidate",
                    ),
                    str(row["derivation_id"]),
                )
                for row in rows
            )

    def ack_world_candidate_outbox(
            self,
            candidate_id: str,
            *,
            receipt_id: str,
            delivered_at_ms: int,
        ) -> bool:
            """Record an idempotent delivery receipt for one candidate."""

            if (
                not candidate_id.startswith("wmc_")
                or not receipt_id
                or len(receipt_id) > 160
                or isinstance(delivered_at_ms, bool)
                or not isinstance(delivered_at_ms, int)
                or delivered_at_ms < 0
            ):
                raise ValueError("world candidate outbox receipt is invalid")
            receipt_sha256 = canonical_sha256(
                {
                    "domain": "tiangong.world.memory-candidate-receipt.v1",
                    "candidate_id": candidate_id,
                    "receipt_id": receipt_id,
                }
            )
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT status, receipt_id FROM memory_world_candidate_outbox
                    WHERE candidate_id = ?
                    """,
                    (candidate_id,),
                ).fetchone()
                if row is None:
                    raise LifeShadowStoreError(
                        "world candidate outbox row does not exist"
                    )
                if row["receipt_id"] is not None:
                    if str(row["receipt_id"]) != receipt_id:
                        raise LifeShadowStoreError(
                            "world candidate outbox receipt conflict"
                        )
                    connection.execute("COMMIT")
                    return False
                connection.execute(
                    """
                    UPDATE memory_world_candidate_outbox
                    SET status = 'delivered', receipt_id = ?, receipt_sha256 = ?,
                        delivered_at_ms = ?
                    WHERE candidate_id = ?
                    """,
                    (receipt_id, receipt_sha256, delivered_at_ms, candidate_id),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def count_pending_world_candidates(
            self, life_id: str | None = None
        ) -> int:
            if life_id is None:
                row = self._connection.execute(
                    """
                    SELECT count(*) AS n FROM memory_world_candidate_outbox
                    WHERE status = 'pending'
                    """
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT count(*) AS n FROM memory_world_candidate_outbox
                    WHERE status = 'pending' AND life_id = ?
                    """,
                    (life_id,),
                ).fetchone()
            return int(row["n"])

    def ack_memory_outbox(
            self,
            change_seq: int,
            *,
            receipt_id: str,
            delivered_at_ms: int,
        ) -> bool:
            """Record an idempotent delivery receipt for one outbox change.

            Repeating the same receipt for the same change is a no-op; a
            different receipt identity for an already-delivered change fails
            closed so delivery ambiguity never goes unnoticed.
            """

            if (
                isinstance(change_seq, bool)
                or not isinstance(change_seq, int)
                or change_seq < 1
                or not receipt_id
                or len(receipt_id) > 160
                or isinstance(delivered_at_ms, bool)
                or not isinstance(delivered_at_ms, int)
                or delivered_at_ms < 0
            ):
                raise ValueError("memory outbox receipt is invalid")
            receipt_sha256 = canonical_sha256(
                {
                    "domain": "tiangong.life.memory-outbox-receipt.v1",
                    "change_seq": change_seq,
                    "receipt_id": receipt_id,
                }
            )
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM memory_outbox WHERE change_seq = ?",
                    (change_seq,),
                ).fetchone()
                if row is None:
                    raise LifeShadowStoreError("memory outbox change does not exist")
                if row["receipt_id"] is not None:
                    if (
                        str(row["receipt_id"]) != receipt_id
                        or str(row["receipt_sha256"]) != receipt_sha256
                    ):
                        raise LifeShadowStoreError("memory outbox receipt conflicts")
                    connection.execute("COMMIT")
                    return False
                if delivered_at_ms < int(row["created_at_ms"]):
                    raise LifeShadowStoreError("memory outbox receipt predates the change")
                connection.execute(
                    """
                    UPDATE memory_outbox
                    SET receipt_id = ?, receipt_sha256 = ?, delivered_at_ms = ?
                    WHERE change_seq = ?
                    """,
                    (receipt_id, receipt_sha256, delivered_at_ms, change_seq),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def get_latest_memory_assertion(
            self, memory_id: str
        ) -> MemoryAssertionV3 | None:
            row = self._connection.execute(
                """
                SELECT a.memory_id, a.revision, c.payload
                FROM memory_assertions AS a
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
                """,
                (memory_id,),
            ).fetchone()
            if row is None:
                return None
            return _parse_stored_contract(
                bytes(row["payload"]),
                MemoryAssertionV3,
                "memory assertion",
            )

    def get_memory_assertion(
            self, memory_id: str, revision: int
        ) -> MemoryAssertionV3 | None:
            row = self._connection.execute(
                """
                SELECT a.memory_id, a.revision, c.payload
                FROM memory_assertions AS a
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.memory_id = ? AND a.revision = ?
                """,
                (memory_id, revision),
            ).fetchone()
            if row is None:
                return None
            return _parse_stored_contract(
                bytes(row["payload"]),
                MemoryAssertionV3,
                "memory assertion",
            )

    def _memory_assertion_payload(self, memory_id: str, revision: int) -> bytes:
            row = self._connection.execute(
                """
                SELECT payload FROM memory_assertion_contracts
                WHERE memory_id = ? AND revision = ?
                """,
                (memory_id, revision),
            ).fetchone()
            if row is None:
                raise LifeShadowStoreError("memory assertion payload is missing")
            return bytes(row["payload"])

    def list_latest_memory_assertions(
            self,
            life_id: str,
            *,
            recallable_only: bool = True,
        ) -> tuple[MemoryAssertionV3, ...]:
            rows = self._connection.execute(
                """
                SELECT a.memory_id, a.revision, c.payload
                FROM memory_assertions AS a
                JOIN (
                    SELECT memory_id, max(revision) AS revision
                    FROM memory_assertions GROUP BY memory_id
                ) AS latest
                  ON latest.memory_id = a.memory_id AND latest.revision = a.revision
                JOIN memory_assertion_contracts AS c
                  ON c.memory_id = a.memory_id AND c.revision = a.revision
                WHERE a.life_id = ?
                ORDER BY a.memory_id
                """,
                (life_id,),
            ).fetchall()
            values = tuple(
                _parse_stored_contract(
                    bytes(row["payload"]), MemoryAssertionV3, "memory assertion"
                )
                for row in rows
            )
            if not recallable_only:
                return values
            return tuple(
                item
                for item in values
                if item.lifecycle_status == "active"
                and item.protected_payload_id is not None
                and (
                    (record := self.get_protected_payload(item.protected_payload_id))
                    is not None
                    and record.key_available
                    and record.key_destroyed_at_ms is None
                )
            )

    def search_memory_assertions(
            self,
            life_id: str,
            terms: tuple[str, ...],
            *,
            limit: int = 128,
        ) -> tuple[MemoryAssertionV3, ...]:
            if not 1 <= limit <= 4096:
                raise ValueError("memory search limit is invalid")
            key_row = self._connection.execute(
                "SELECT key_material FROM life_index_keys WHERE life_id = ?", (life_id,)
            ).fetchone()
            if key_row is None or not terms:
                return ()
            digests = self._term_digests(bytes(key_row["key_material"]), terms)
            placeholders = ",".join("?" for _ in digests)
            rows = self._connection.execute(
                f"""
                SELECT memory_id
                FROM memory_search_terms
                WHERE term_hmac_sha256 IN ({placeholders})
                GROUP BY memory_id
                HAVING count(DISTINCT term_hmac_sha256) = ?
                ORDER BY memory_id
                LIMIT ?
                """,
                (*digests, len(digests), limit),
            ).fetchall()
            values = tuple(
                item
                for row in rows
                if (item := self.get_latest_memory_assertion(str(row["memory_id"])))
                is not None
                and item.life_id == life_id
                and item.lifecycle_status == "active"
                and item.protected_payload_id is not None
                and (
                    (record := self.get_protected_payload(item.protected_payload_id))
                    is not None
                    and record.key_available
                    and record.key_destroyed_at_ms is None
                )
            )
            return tuple(
                sorted(values, key=lambda item: (-retention_priority(item), item.memory_id))
            )

    def put_memory_relation(self, relation: MemoryRelationV3) -> bool:
            relation, payload = _revalidate_contract(
                relation, MemoryRelationV3, "memory relation"
            )
            if not relation.has_valid_relation_sha256():
                raise LifeShadowStoreError("memory relation digest is invalid")
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT payload FROM memory_relations WHERE relation_id = ?",
                    (relation.relation_id,),
                ).fetchone()
                if existing is not None:
                    if bytes(existing["payload"]) != payload:
                        raise LifeShadowStoreError("memory relation identity was rebound")
                    connection.execute("COMMIT")
                    return False
                source = connection.execute(
                    """
                    SELECT life_id, status FROM memory_assertions
                    WHERE memory_id = ? ORDER BY revision DESC LIMIT 1
                    """,
                    (relation.source_memory_id,),
                ).fetchone()
                if (
                    source is None
                    or str(source["life_id"]) != relation.life_id
                    or str(source["status"]) == "deleted"
                ):
                    raise LifeShadowStoreError("memory relation source does not exist")
                connection.execute(
                    """
                    INSERT INTO memory_relations(
                        relation_id, life_id, source_memory_id, relation_kind,
                        target_ref, payload, payload_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation.relation_id,
                        relation.life_id,
                        relation.source_memory_id,
                        relation.relation_kind,
                        relation.target_ref,
                        payload,
                        relation.relation_sha256,
                        relation.created_at_ms,
                    ),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def list_memory_relations(
            self, life_id: str, *, recallable_only: bool = True
        ) -> tuple[MemoryRelationV3, ...]:
            rows = self._connection.execute(
                "SELECT payload FROM memory_relations WHERE life_id = ? ORDER BY relation_id",
                (life_id,),
            ).fetchall()
            relations = tuple(
                _parse_stored_contract(
                    bytes(row["payload"]), MemoryRelationV3, "memory relation"
                )
                for row in rows
            )
            if not recallable_only:
                return relations
            return tuple(
                relation
                for relation in relations
                if (
                    (source := self.get_latest_memory_assertion(
                        relation.source_memory_id
                    ))
                    is not None
                    and source.lifecycle_status == "active"
                    and source.protected_payload_id is not None
                    and (
                        (record := self.get_protected_payload(
                            source.protected_payload_id
                        ))
                        is not None
                        and record.key_available
                        and record.key_destroyed_at_ms is None
                    )
                )
            )

    def delete_memory(
            self,
            memory_id: str,
            *,
            expected_revision: int,
            deleted_at_ms: int,
        ) -> MemoryDeletionResult:
            if not memory_id or expected_revision < 1 or deleted_at_ms < 0:
                raise ValueError("memory deletion request is invalid")
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                latest_row = connection.execute(
                    """
                    SELECT a.*, c.payload
                    FROM memory_assertions AS a
                    JOIN memory_assertion_contracts AS c
                      ON c.memory_id = a.memory_id AND c.revision = a.revision
                    WHERE a.memory_id = ? ORDER BY a.revision DESC LIMIT 1
                    """,
                    (memory_id,),
                ).fetchone()
                if latest_row is None:
                    raise LifeShadowStoreError("memory deletion target does not exist")
                latest = _parse_stored_contract(
                    bytes(latest_row["payload"]), MemoryAssertionV3, "memory assertion"
                )
                if latest.lifecycle_status == "deleted":
                    if expected_revision not in {latest.revision, latest.revision - 1}:
                        raise LifeShadowStoreError("memory deletion revision is stale")
                    assert latest.deletion_tombstone_id is not None
                    tombstone_row = connection.execute(
                        "SELECT payload FROM privacy_deletion_tombstones WHERE tombstone_id = ?",
                        (latest.deletion_tombstone_id,),
                    ).fetchone()
                    if tombstone_row is None:
                        raise LifeShadowStoreError("memory deletion tombstone is missing")
                    tombstone = _parse_stored_contract(
                        bytes(tombstone_row["payload"]),
                        PrivacyDeletionTombstone,
                        "privacy deletion tombstone",
                    )
                    connection.execute("COMMIT")
                    return MemoryDeletionResult(
                        tombstone, latest, tombstone.destroyed_payload_ids
                    )
                if latest.revision != expected_revision:
                    raise LifeShadowStoreError("memory deletion revision is stale")
                if latest.retention_class == "LEGAL_HOLD":
                    raise LifeShadowStoreError("legal-hold memory cannot be deleted")

                history_rows = connection.execute(
                    """
                    SELECT payload_object_id FROM memory_assertions
                    WHERE memory_id = ? AND payload_object_id IS NOT NULL
                    """,
                    (memory_id,),
                ).fetchall()
                node_rows = connection.execute(
                    "SELECT node_id, payload FROM causal_nodes WHERE life_id = ?",
                    (latest.life_id,),
                ).fetchall()
                affected_nodes: list[CausalNodeV3] = []
                for row in node_rows:
                    node = _parse_stored_contract(
                        bytes(row["payload"]), CausalNodeV3, "causal node"
                    )
                    if node.source_ref == memory_id:
                        affected_nodes.append(node)
                pack_rows = connection.execute(
                    """
                    SELECT p.pack_id, p.source_capsule_id, p.protected_payload_id
                    FROM causal_context_packs AS p
                    JOIN causal_context_pack_members AS m ON m.pack_id = p.pack_id
                    WHERE m.item_ref = ? ORDER BY p.pack_id
                    """,
                    (memory_id,),
                ).fetchall()
                payload_ids = tuple(
                    sorted(
                        {
                            *(str(row["payload_object_id"]) for row in history_rows),
                            *(node.protected_payload_id for node in affected_nodes),
                            *(str(row["protected_payload_id"]) for row in pack_rows),
                        }
                    )
                )
                if len(payload_ids) > 4096:
                    raise LifeShadowStoreError("memory deletion fanout exceeds safe bound")
                removed_index_count = int(
                    connection.execute(
                        "SELECT count(*) FROM memory_search_terms WHERE memory_id = ?",
                        (memory_id,),
                    ).fetchone()[0]
                )
                if affected_nodes:
                    placeholders = ",".join("?" for _ in affected_nodes)
                    node_ids = tuple(node.node_id for node in affected_nodes)
                    removed_index_count += int(
                        connection.execute(
                            f"SELECT count(*) FROM causal_node_terms WHERE node_id IN ({placeholders})",
                            node_ids,
                        ).fetchone()[0]
                    )
                    connection.execute(
                        f"DELETE FROM causal_node_terms WHERE node_id IN ({placeholders})",
                        node_ids,
                    )
                connection.execute(
                    "DELETE FROM memory_search_terms WHERE memory_id = ?", (memory_id,)
                )
                for payload_id in payload_ids:
                    payload_row = connection.execute(
                        "SELECT created_at_ms FROM protected_payloads WHERE payload_id = ?",
                        (payload_id,),
                    ).fetchone()
                    if payload_row is None:
                        raise LifeShadowStoreError("memory deletion payload is missing")
                    if deleted_at_ms < int(payload_row["created_at_ms"]):
                        raise LifeShadowStoreError("memory deletion predates protected payload")
                    connection.execute(
                        "DELETE FROM protected_payload_keys WHERE payload_id = ?",
                        (payload_id,),
                    )
                    connection.execute(
                        "UPDATE protected_payloads SET key_destroyed_at_ms = ? WHERE payload_id = ?",
                        (deleted_at_ms, payload_id),
                    )

                target_ref_hash = hashlib.sha256(memory_id.encode("utf-8")).hexdigest()
                tombstone_id = "ptm_" + canonical_sha256(
                    {
                        "domain": "tiangong.life.memory-deletion.v1",
                        "life_id": latest.life_id,
                        "memory_id_hash": target_ref_hash,
                        "superseded_assertion_sha256": latest.assertion_sha256,
                    }
                )
                affected_capsules = tuple(
                    sorted({str(row["source_capsule_id"]) for row in pack_rows})
                )
                tombstone = PrivacyDeletionTombstone(
                    tombstone_id=tombstone_id,
                    life_id=latest.life_id,
                    target_kind="memory",
                    target_ref_hash=target_ref_hash,
                    privacy_scope=latest.privacy_scope,
                    destroyed_payload_ids=payload_ids,
                    removed_index_entry_count=removed_index_count,
                    affected_capsule_ids=affected_capsules,
                    created_at_ms=deleted_at_ms,
                    deletion_proof_sha256="0" * 64,
                ).with_computed_deletion_proof_sha256()
                tombstone_payload = canonical_json_bytes(tombstone)
                connection.execute(
                    """
                    INSERT INTO privacy_deletion_tombstones(
                        tombstone_id, life_id, target_kind, target_ref_hash,
                        privacy_scope, payload, deletion_proof_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tombstone.tombstone_id,
                        tombstone.life_id,
                        tombstone.target_kind,
                        tombstone.target_ref_hash,
                        tombstone.privacy_scope,
                        tombstone_payload,
                        tombstone.deletion_proof_sha256,
                        tombstone.created_at_ms,
                    ),
                )
                connection.execute(
                    "INSERT INTO tombstones VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        tombstone.tombstone_id,
                        tombstone.life_id,
                        tombstone.target_kind,
                        tombstone.target_ref_hash,
                        tombstone.deletion_proof_sha256,
                        tombstone.created_at_ms,
                    ),
                )
                suppression_targets = [("memory", target_ref_hash)]
                suppression_targets.extend(
                    ("causal_node", hashlib.sha256(node.node_id.encode("utf-8")).hexdigest())
                    for node in affected_nodes
                )
                suppression_targets.extend(
                    (
                        "context_pack",
                        hashlib.sha256(str(row["pack_id"]).encode("utf-8")).hexdigest(),
                    )
                    for row in pack_rows
                )
                for target_kind, target_hash in suppression_targets:
                    connection.execute(
                        """
                        INSERT INTO privacy_suppressions(
                            target_kind, target_ref_hash, privacy_scope,
                            tombstone_id, created_at_ms
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            target_kind,
                            target_hash,
                            latest.privacy_scope,
                            tombstone.tombstone_id,
                            deleted_at_ms,
                        ),
                    )
                deleted = latest.model_copy(
                    update={
                        "revision": latest.revision + 1,
                        "supersedes_assertion_sha256": latest.assertion_sha256,
                        "lifecycle_status": "deleted",
                        "protected_payload_id": None,
                        "protected_payload_sha256": None,
                        "deletion_tombstone_id": tombstone.tombstone_id,
                        "created_at_ms": deleted_at_ms,
                        "assertion_sha256": "0" * 64,
                    }
                ).with_computed_assertion_sha256()
                deleted_payload = canonical_json_bytes(deleted)
                connection.execute(
                    """
                    INSERT INTO memory_assertions(
                        memory_id, revision, life_id, status, privacy_scope,
                        payload_object_id, payload_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        deleted.memory_id,
                        deleted.revision,
                        deleted.life_id,
                        deleted.lifecycle_status,
                        deleted.privacy_scope,
                        deleted.created_at_ms,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO memory_assertion_contracts(
                        memory_id, revision, payload, assertion_sha256
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        deleted.memory_id,
                        deleted.revision,
                        deleted_payload,
                        deleted.assertion_sha256,
                    ),
                )
                self._record_memory_change_locked(
                    life_id=deleted.life_id,
                    memory_id=deleted.memory_id,
                    revision=deleted.revision,
                    change_kind="tombstone",
                    assertion_sha256=deleted.assertion_sha256,
                    created_at_ms=deleted.created_at_ms,
                )
                connection.execute("COMMIT")
                return MemoryDeletionResult(tombstone, deleted, payload_ids)
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
