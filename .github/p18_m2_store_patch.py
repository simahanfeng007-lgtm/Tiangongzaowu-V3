from __future__ import annotations

from pathlib import Path

STORE = Path("src/total_gateway/store.py")
DOMAIN = Path("src/total_gateway/regenerative_execution.py")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


# Keep the structured checkpoint self-contained enough to restart from its
# known-good Frontier, while canonical TaskContinuityCapsule remains the task SSoT.
domain = DOMAIN.read_text(encoding="utf-8")
domain = replace_once(
    domain,
    '    frontier_hash: str = Field(pattern=r"^[0-9a-f]{64}$")\n    continuity_capsule_id:',
    '    frontier_hash: str = Field(pattern=r"^[0-9a-f]{64}$")\n    frontier: ExecutionFrontier\n    continuity_capsule_id:',
    label="checkpoint frontier field",
)
domain = replace_once(
    domain,
    '        if self.pending_effect_ids != tuple(sorted(set(self.pending_effect_ids))):\n',
    '        if (\n            self.frontier.request_id != self.request_id\n            or self.frontier.run_id != self.run_id\n            or self.frontier.generation != self.generation\n            or self.frontier.life_id != self.life_id\n            or self.frontier.root_goal_hash != self.root_goal_hash\n            or self.frontier.task_contract_hash != self.task_contract_hash\n            or self.frontier.authority_hash != self.authority_hash\n            or self.frontier.frontier_version != self.frontier_version\n            or self.frontier.frontier_hash != self.frontier_hash\n            or not self.frontier.has_valid_hash()\n        ):\n            raise ValueError("checkpoint frontier binding is invalid")\n        if self.pending_effect_ids != tuple(sorted(set(self.pending_effect_ids))):\n',
    label="checkpoint frontier validator",
)
domain = replace_once(
    domain,
    '        frontier_hash=frontier.frontier_hash,\n        continuity_capsule_id=continuity_capsule_id,',
    '        frontier_hash=frontier.frontier_hash,\n        frontier=frontier,\n        continuity_capsule_id=continuity_capsule_id,',
    label="checkpoint frontier constructor",
)
DOMAIN.write_text(domain, encoding="utf-8", newline="\n")

store = STORE.read_text(encoding="utf-8")
store = replace_once(
    store,
    'from .effects import EffectClaim, EffectResult\n',
    'from .effects import EffectClaim, EffectResult\nfrom .regenerative_execution import (\n    ZERO_HASH,\n    ExecutionFrontier,\n    ExecutionLedgerEvent,\n    RegenerativeCheckpoint,\n    build_execution_ledger_event,\n    build_regenerative_checkpoint,\n)\n',
    label="store regenerative imports",
)
store = replace_once(
    store,
    'STORE_SCHEMA_VERSION = 20\n',
    'STORE_SCHEMA_VERSION = 21\n',
    label="store schema version",
)

migration = r'''
_MIGRATION_V21_ID = "gateway-regenerative-execution-v21"
_MIGRATION_V21_STATEMENTS = (
    """
    CREATE TABLE execution_task_contract (
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        life_id TEXT NOT NULL,
        root_goal_hash TEXT NOT NULL CHECK (length(root_goal_hash) = 64),
        task_contract_hash TEXT NOT NULL CHECK (length(task_contract_hash) = 64),
        authority_hash TEXT NOT NULL CHECK (length(authority_hash) = 64),
        bound_at_ms INTEGER NOT NULL CHECK (bound_at_ms >= 0),
        PRIMARY KEY (request_id, run_id, generation)
    ) STRICT
    """,
    """
    CREATE TABLE execution_ledger_head (
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        head_seq INTEGER NOT NULL CHECK (head_seq >= 0),
        head_hash TEXT NOT NULL CHECK (length(head_hash) = 64),
        revision INTEGER NOT NULL CHECK (revision >= 0),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
        PRIMARY KEY (request_id, run_id, generation)
    ) STRICT
    """,
    """
    CREATE TABLE execution_ledger (
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        ledger_seq INTEGER NOT NULL CHECK (ledger_seq >= 1),
        event_id TEXT NOT NULL UNIQUE,
        event_key TEXT NOT NULL,
        epoch_index INTEGER NOT NULL CHECK (epoch_index >= 0),
        event_type TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
        prev_event_hash TEXT NOT NULL CHECK (length(prev_event_hash) = 64),
        event_hash TEXT NOT NULL UNIQUE CHECK (length(event_hash) = 64),
        logical_effect_id TEXT,
        attempt_id TEXT,
        step_id TEXT,
        effect_id TEXT,
        causal_parent_event_id TEXT,
        event_json TEXT NOT NULL CHECK (json_valid(event_json)),
        PRIMARY KEY (request_id, run_id, generation, ledger_seq),
        UNIQUE (request_id, run_id, generation, event_key)
    ) STRICT
    """,
    """
    CREATE INDEX execution_ledger_effect
    ON execution_ledger(request_id, run_id, generation, logical_effect_id, ledger_seq)
    """,
    """
    CREATE TABLE execution_frontier (
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        frontier_hash TEXT NOT NULL CHECK (length(frontier_hash) = 64),
        frontier_json TEXT NOT NULL CHECK (json_valid(frontier_json)),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
        PRIMARY KEY (request_id, run_id, generation)
    ) STRICT
    """,
    """
    CREATE TABLE regenerative_checkpoint (
        checkpoint_id TEXT PRIMARY KEY,
        checkpoint_seq INTEGER NOT NULL CHECK (checkpoint_seq >= 1),
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        life_id TEXT NOT NULL,
        ledger_head_seq INTEGER NOT NULL CHECK (ledger_head_seq >= 0),
        ledger_head_hash TEXT NOT NULL CHECK (length(ledger_head_hash) = 64),
        frontier_hash TEXT NOT NULL CHECK (length(frontier_hash) = 64),
        continuity_capsule_id TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
        checksum_sha256 TEXT NOT NULL CHECK (length(checksum_sha256) = 64),
        checkpoint_hash TEXT NOT NULL UNIQUE CHECK (length(checkpoint_hash) = 64),
        checkpoint_json TEXT NOT NULL CHECK (json_valid(checkpoint_json)),
        UNIQUE (request_id, run_id, generation, checkpoint_seq)
    ) STRICT
    """,
    """
    CREATE INDEX regenerative_checkpoint_run
    ON regenerative_checkpoint(request_id, run_id, generation, checkpoint_seq)
    """,
    """
    CREATE TABLE regenerative_checkpoint_head (
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        current_checkpoint_id TEXT NOT NULL,
        previous_checkpoint_id TEXT,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
        PRIMARY KEY (request_id, run_id, generation),
        FOREIGN KEY (current_checkpoint_id) REFERENCES regenerative_checkpoint(checkpoint_id),
        FOREIGN KEY (previous_checkpoint_id) REFERENCES regenerative_checkpoint(checkpoint_id)
    ) STRICT
    """,
)

'''
store = replace_once(
    store,
    '\ndef _migration_sha256(version: int, migration_id: str, statements: tuple[str, ...]) -> str:\n',
    '\n' + migration + 'def _migration_sha256(version: int, migration_id: str, statements: tuple[str, ...]) -> str:\n',
    label="migration v21 insertion",
)
store = replace_once(
    store,
    '    (20, _MIGRATION_V20_ID, _MIGRATION_V20_STATEMENTS),\n)\n',
    '    (20, _MIGRATION_V20_ID, _MIGRATION_V20_STATEMENTS),\n    (21, _MIGRATION_V21_ID, _MIGRATION_V21_STATEMENTS),\n)\n',
    label="migration v21 registry",
)

methods = r'''
    # ------------------------------------------------------------------
    # P18-M2 regenerative execution kernel: same GatewayStateStore, no SSoT fork.
    # ------------------------------------------------------------------

    def bind_execution_task_contract(
        self,
        *,
        request_id: str,
        run_id: str,
        generation: int,
        life_id: str,
        root_goal_hash: str,
        task_contract_hash: str,
        authority_hash: str,
        bound_at_ms: int,
    ) -> bool:
        """Immutably bind the Run's root/task/authority hashes for this generation."""
        values = (root_goal_hash, task_contract_hash, authority_hash)
        if (
            not request_id or not run_id or generation < 0 or not life_id
            or bound_at_ms < 0
            or any(len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value) for value in values)
        ):
            raise ValueError("regenerative task-contract binding is invalid")
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=request_id,
                run_id=run_id,
                generation=generation,
                recorded_at_ms=bound_at_ms,
            )
            row = self._connection.execute(
                """SELECT * FROM execution_task_contract
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            desired = (
                life_id, root_goal_hash, task_contract_hash, authority_hash
            )
            if row is not None:
                existing = (
                    str(row["life_id"]), str(row["root_goal_hash"]),
                    str(row["task_contract_hash"]), str(row["authority_hash"]),
                )
                if existing != desired:
                    raise StoreConflictError("task contract or authority changed inside one generation")
                return False
            self._connection.execute(
                """INSERT INTO execution_task_contract(
                    request_id, run_id, generation, life_id, root_goal_hash,
                    task_contract_hash, authority_hash, bound_at_ms
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    request_id, run_id, generation, life_id, root_goal_hash,
                    task_contract_hash, authority_hash, bound_at_ms,
                ),
            )
            return True

    def get_execution_task_contract(
        self, request_id: str, *, run_id: str, generation: int
    ) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM execution_task_contract
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            return None if row is None else dict(row)

    def _execution_contract_locked(
        self, request_id: str, run_id: str, generation: int
    ) -> sqlite3.Row:
        row = self._connection.execute(
            """SELECT * FROM execution_task_contract
               WHERE request_id=? AND run_id=? AND generation=?""",
            (request_id, run_id, generation),
        ).fetchone()
        if row is None:
            raise StoreConflictError("regenerative execution requires an immutable task contract")
        return row

    def _execution_event_from_row_locked(self, row: sqlite3.Row) -> ExecutionLedgerEvent:
        try:
            event = ExecutionLedgerEvent.model_validate_json(str(row["event_json"]), strict=True)
        except ValueError as exc:
            raise StoreCorruptionError("execution ledger event JSON is invalid") from exc
        if (
            not event.has_valid_hash()
            or event.request_id != row["request_id"]
            or event.run_id != row["run_id"]
            or event.generation != row["generation"]
            or event.ledger_seq != row["ledger_seq"]
            or event.event_id != row["event_id"]
            or event.event_key != row["event_key"]
            or event.epoch_index != row["epoch_index"]
            or event.event_type != row["event_type"]
            or event.created_at_ms != row["created_at_ms"]
            or event.payload_hash != row["payload_hash"]
            or event.prev_event_hash != row["prev_event_hash"]
            or event.event_hash != row["event_hash"]
            or event.logical_effect_id != row["logical_effect_id"]
            or event.attempt_id != row["attempt_id"]
            or event.step_id != row["step_id"]
            or event.effect_id != row["effect_id"]
            or event.causal_parent_event_id != row["causal_parent_event_id"]
        ):
            raise StoreCorruptionError("execution ledger row disagrees with canonical event")
        return event

    def append_execution_event(
        self,
        *,
        event_key: str,
        request_id: str,
        run_id: str,
        generation: int,
        epoch_index: int,
        event_type: str,
        payload: dict,
        created_at_ms: int,
        logical_effect_id: str | None = None,
        attempt_id: str | None = None,
        step_id: str | None = None,
        effect_id: str | None = None,
        causal_parent_event_id: str | None = None,
    ) -> tuple[ExecutionLedgerEvent, bool]:
        """Append once with a monotonic per-Run seq and hash-chain CAS."""
        if not event_key or created_at_ms < 0:
            raise ValueError("execution event idempotency key or time is invalid")
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=request_id, run_id=run_id, generation=generation,
                recorded_at_ms=created_at_ms,
            )
            self._execution_contract_locked(request_id, run_id, generation)
            existing = self._connection.execute(
                """SELECT * FROM execution_ledger
                   WHERE request_id=? AND run_id=? AND generation=? AND event_key=?""",
                (request_id, run_id, generation, event_key),
            ).fetchone()
            if existing is not None:
                event = self._execution_event_from_row_locked(existing)
                probe = build_execution_ledger_event(
                    ledger_seq=event.ledger_seq,
                    event_key=event_key,
                    request_id=request_id,
                    run_id=run_id,
                    generation=generation,
                    epoch_index=epoch_index,
                    event_type=event_type,
                    created_at_ms=created_at_ms,
                    payload=dict(payload),
                    prev_event_hash=event.prev_event_hash,
                    logical_effect_id=logical_effect_id,
                    attempt_id=attempt_id,
                    step_id=step_id,
                    effect_id=effect_id,
                    causal_parent_event_id=causal_parent_event_id,
                )
                stable = {"created_at_ms", "event_hash"}
                if event.model_dump(exclude=stable) != probe.model_dump(exclude=stable):
                    raise StoreConflictError("execution ledger event key was reused with different content")
                return event, False
            head = self._connection.execute(
                """SELECT * FROM execution_ledger_head
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            head_seq = 0 if head is None else int(head["head_seq"])
            head_hash = ZERO_HASH if head is None else str(head["head_hash"])
            head_revision = 0 if head is None else int(head["revision"])
            event = build_execution_ledger_event(
                ledger_seq=head_seq + 1,
                event_key=event_key,
                request_id=request_id,
                run_id=run_id,
                generation=generation,
                epoch_index=epoch_index,
                event_type=event_type,
                created_at_ms=created_at_ms,
                payload=dict(payload),
                prev_event_hash=head_hash,
                logical_effect_id=logical_effect_id,
                attempt_id=attempt_id,
                step_id=step_id,
                effect_id=effect_id,
                causal_parent_event_id=causal_parent_event_id,
            )
            event_json = json.dumps(
                event.model_dump(mode="json"), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False,
            )
            self._connection.execute(
                """INSERT INTO execution_ledger(
                    request_id, run_id, generation, ledger_seq, event_id, event_key,
                    epoch_index, event_type, created_at_ms, payload_hash,
                    prev_event_hash, event_hash, logical_effect_id, attempt_id,
                    step_id, effect_id, causal_parent_event_id, event_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.request_id, event.run_id, event.generation, event.ledger_seq,
                    event.event_id, event.event_key, event.epoch_index, event.event_type,
                    event.created_at_ms, event.payload_hash, event.prev_event_hash,
                    event.event_hash, event.logical_effect_id, event.attempt_id,
                    event.step_id, event.effect_id, event.causal_parent_event_id, event_json,
                ),
            )
            if head is None:
                self._connection.execute(
                    """INSERT INTO execution_ledger_head(
                        request_id, run_id, generation, head_seq, head_hash, revision, updated_at_ms
                    ) VALUES (?,?,?,?,?,?,?)""",
                    (request_id, run_id, generation, event.ledger_seq, event.event_hash, 1, created_at_ms),
                )
            else:
                updated = self._connection.execute(
                    """UPDATE execution_ledger_head
                       SET head_seq=?, head_hash=?, revision=?, updated_at_ms=?
                       WHERE request_id=? AND run_id=? AND generation=?
                         AND revision=? AND head_seq=? AND head_hash=?""",
                    (
                        event.ledger_seq, event.event_hash, head_revision + 1, created_at_ms,
                        request_id, run_id, generation, head_revision, head_seq, head_hash,
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreCasConflict("execution ledger head changed before append")
            return event, True

    def list_execution_events(
        self, request_id: str, *, run_id: str, generation: int, after_seq: int = 0
    ) -> tuple[ExecutionLedgerEvent, ...]:
        if after_seq < 0:
            raise ValueError("execution ledger cursor is invalid")
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM execution_ledger
                   WHERE request_id=? AND run_id=? AND generation=? AND ledger_seq>?
                   ORDER BY ledger_seq""",
                (request_id, run_id, generation, after_seq),
            ).fetchall()
            return tuple(self._execution_event_from_row_locked(row) for row in rows)

    def get_execution_ledger_head(
        self, request_id: str, *, run_id: str, generation: int
    ) -> dict:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM execution_ledger_head
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            if row is None:
                return {"head_seq": 0, "head_hash": ZERO_HASH, "revision": 0}
            return dict(row)

    def audit_execution_ledger(
        self, request_id: str, *, run_id: str, generation: int
    ) -> dict:
        """Verify seq, prev hash, payload/event hashes and authoritative head."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM execution_ledger
                   WHERE request_id=? AND run_id=? AND generation=? ORDER BY ledger_seq""",
                (request_id, run_id, generation),
            ).fetchall()
            expected_seq = 1
            prev_hash = ZERO_HASH
            valid_events: list[ExecutionLedgerEvent] = []
            for row in rows:
                try:
                    event = self._execution_event_from_row_locked(row)
                except StoreCorruptionError as exc:
                    return {
                        "healthy": False, "first_invalid_seq": int(row["ledger_seq"]),
                        "last_valid_seq": expected_seq - 1, "last_valid_hash": prev_hash,
                        "reason": str(exc),
                    }
                if event.ledger_seq != expected_seq or event.prev_event_hash != prev_hash:
                    return {
                        "healthy": False, "first_invalid_seq": event.ledger_seq,
                        "last_valid_seq": expected_seq - 1, "last_valid_hash": prev_hash,
                        "reason": "execution ledger sequence/hash chain is discontinuous",
                    }
                valid_events.append(event)
                expected_seq += 1
                prev_hash = event.event_hash
            head = self._connection.execute(
                """SELECT * FROM execution_ledger_head
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            expected_head_seq = expected_seq - 1
            expected_head_hash = prev_hash
            if head is None:
                head_ok = expected_head_seq == 0
            else:
                head_ok = (
                    int(head["head_seq"]) == expected_head_seq
                    and str(head["head_hash"]) == expected_head_hash
                )
            if not head_ok:
                return {
                    "healthy": False, "first_invalid_seq": expected_head_seq + 1,
                    "last_valid_seq": expected_head_seq, "last_valid_hash": expected_head_hash,
                    "reason": "execution ledger head disagrees with durable chain",
                }
            return {
                "healthy": True, "first_invalid_seq": None,
                "last_valid_seq": expected_head_seq, "last_valid_hash": expected_head_hash,
                "event_count": len(valid_events),
            }

    def recover_execution_ledger_tail(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
        known_good_seq: int,
        recovered_at_ms: int,
    ) -> dict:
        """Truncate only a corrupt/torn tail strictly after a known-good checkpoint."""
        audit = self.audit_execution_ledger(request_id, run_id=run_id, generation=generation)
        if audit["healthy"]:
            return {**audit, "recovered": False, "truncated": 0}
        invalid_seq = int(audit["first_invalid_seq"])
        if invalid_seq <= known_good_seq:
            raise StoreCorruptionError("execution ledger corruption predates the known-good checkpoint")
        with self._lock, self._write_transaction():
            removed = int(self._connection.execute(
                """SELECT COUNT(*) FROM execution_ledger
                   WHERE request_id=? AND run_id=? AND generation=? AND ledger_seq>=?""",
                (request_id, run_id, generation, invalid_seq),
            ).fetchone()[0])
            self._connection.execute(
                """DELETE FROM execution_ledger
                   WHERE request_id=? AND run_id=? AND generation=? AND ledger_seq>=?""",
                (request_id, run_id, generation, invalid_seq),
            )
            last_seq = int(audit["last_valid_seq"])
            last_hash = str(audit["last_valid_hash"])
            row = self._connection.execute(
                """SELECT revision FROM execution_ledger_head
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    """INSERT INTO execution_ledger_head(
                        request_id, run_id, generation, head_seq, head_hash, revision, updated_at_ms
                    ) VALUES (?,?,?,?,?,1,?)""",
                    (request_id, run_id, generation, last_seq, last_hash, recovered_at_ms),
                )
            else:
                self._connection.execute(
                    """UPDATE execution_ledger_head
                       SET head_seq=?, head_hash=?, revision=?, updated_at_ms=?
                       WHERE request_id=? AND run_id=? AND generation=?""",
                    (
                        last_seq, last_hash, int(row["revision"]) + 1, recovered_at_ms,
                        request_id, run_id, generation,
                    ),
                )
        final = self.audit_execution_ledger(request_id, run_id=run_id, generation=generation)
        if not final["healthy"]:
            raise StoreCorruptionError("execution ledger tail recovery did not restore a valid chain")
        return {**final, "recovered": True, "truncated": removed, "first_invalid_seq": invalid_seq}

    def commit_execution_frontier(
        self,
        frontier: ExecutionFrontier,
        *,
        expected_revision: int,
        updated_at_ms: int,
    ) -> int:
        if expected_revision < 0 or updated_at_ms < 0 or not frontier.has_valid_hash():
            raise ValueError("execution frontier commit is invalid")
        if frontier.frontier_version != expected_revision + 1:
            raise StoreConflictError("execution frontier version is not the next CAS revision")
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=frontier.request_id, run_id=frontier.run_id,
                generation=frontier.generation, recorded_at_ms=updated_at_ms,
            )
            contract = self._execution_contract_locked(
                frontier.request_id, frontier.run_id, frontier.generation
            )
            if (
                str(contract["life_id"]) != frontier.life_id
                or str(contract["root_goal_hash"]) != frontier.root_goal_hash
                or str(contract["task_contract_hash"]) != frontier.task_contract_hash
                or str(contract["authority_hash"]) != frontier.authority_hash
            ):
                raise StoreConflictError("frontier crossed its immutable task/authority binding")
            payload = json.dumps(
                frontier.model_dump(mode="json"), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False,
            )
            row = self._connection.execute(
                """SELECT revision, frontier_hash FROM execution_frontier
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (frontier.request_id, frontier.run_id, frontier.generation),
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise StoreCasConflict("execution frontier genesis CAS is stale")
                self._connection.execute(
                    """INSERT INTO execution_frontier(
                        request_id, run_id, generation, revision, frontier_hash,
                        frontier_json, updated_at_ms
                    ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        frontier.request_id, frontier.run_id, frontier.generation,
                        frontier.frontier_version, frontier.frontier_hash, payload, updated_at_ms,
                    ),
                )
                return frontier.frontier_version
            if int(row["revision"]) != expected_revision:
                raise StoreCasConflict("execution frontier revision changed concurrently")
            updated = self._connection.execute(
                """UPDATE execution_frontier SET revision=?, frontier_hash=?, frontier_json=?, updated_at_ms=?
                   WHERE request_id=? AND run_id=? AND generation=? AND revision=? AND frontier_hash=?""",
                (
                    frontier.frontier_version, frontier.frontier_hash, payload, updated_at_ms,
                    frontier.request_id, frontier.run_id, frontier.generation,
                    expected_revision, str(row["frontier_hash"]),
                ),
            )
            if updated.rowcount != 1:
                raise StoreCasConflict("execution frontier changed before CAS commit")
            return frontier.frontier_version

    def get_execution_frontier(
        self, request_id: str, *, run_id: str, generation: int
    ) -> ExecutionFrontier | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM execution_frontier
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            if row is None:
                return None
            try:
                frontier = ExecutionFrontier.model_validate_json(str(row["frontier_json"]), strict=True)
            except ValueError as exc:
                raise StoreCorruptionError("execution frontier JSON is invalid") from exc
            if (
                not frontier.has_valid_hash()
                or frontier.frontier_hash != row["frontier_hash"]
                or frontier.frontier_version != row["revision"]
            ):
                raise StoreCorruptionError("execution frontier row is corrupted")
            return frontier

    def commit_regenerative_checkpoint(
        self,
        frontier: ExecutionFrontier,
        *,
        continuity_capsule_id: str,
        recovery_preconditions: tuple[str, ...],
        runtime_version: str,
        provider_version: str,
        model_version: str,
        tool_contract_version: str,
        skill_contract_version: str,
        task_contract_version: str,
        semantic_handoff: str,
        created_at_ms: int,
    ) -> RegenerativeCheckpoint:
        """Atomically commit current+previous known-good checkpoint head in this DB."""
        if created_at_ms < 0 or not continuity_capsule_id:
            raise ValueError("regenerative checkpoint identity is invalid")
        with self._lock, self._write_transaction():
            self._assert_request_binding_locked(
                request_id=frontier.request_id, run_id=frontier.run_id,
                generation=frontier.generation, recorded_at_ms=created_at_ms,
            )
            live_frontier = self._connection.execute(
                """SELECT revision, frontier_hash FROM execution_frontier
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (frontier.request_id, frontier.run_id, frontier.generation),
            ).fetchone()
            if (
                live_frontier is None
                or int(live_frontier["revision"]) != frontier.frontier_version
                or str(live_frontier["frontier_hash"]) != frontier.frontier_hash
                or not frontier.has_valid_hash()
            ):
                raise StoreConflictError("checkpoint frontier is not the committed frontier head")
            capsule = self._connection.execute(
                """SELECT request_id, run_id, generation, life_id, status
                   FROM request_capsules WHERE capsule_id=?""",
                (continuity_capsule_id,),
            ).fetchone()
            if (
                capsule is None
                or capsule["request_id"] != frontier.request_id
                or capsule["run_id"] != frontier.run_id
                or capsule["generation"] != frontier.generation
                or capsule["life_id"] != frontier.life_id
                or capsule["status"] != "ACTIVE"
            ):
                raise StoreConflictError("checkpoint is not bound to the active canonical continuity capsule")
            ledger = self._connection.execute(
                """SELECT head_seq, head_hash FROM execution_ledger_head
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (frontier.request_id, frontier.run_id, frontier.generation),
            ).fetchone()
            ledger_head_seq = 0 if ledger is None else int(ledger["head_seq"])
            ledger_head_hash = ZERO_HASH if ledger is None else str(ledger["head_hash"])
            head = self._connection.execute(
                """SELECT * FROM regenerative_checkpoint_head
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (frontier.request_id, frontier.run_id, frontier.generation),
            ).fetchone()
            previous_checkpoint_id = None if head is None else str(head["current_checkpoint_id"])
            previous_hash = ZERO_HASH
            checkpoint_seq = 1
            head_revision = 0
            if previous_checkpoint_id:
                prior = self._connection.execute(
                    "SELECT checkpoint_seq, checkpoint_hash FROM regenerative_checkpoint WHERE checkpoint_id=?",
                    (previous_checkpoint_id,),
                ).fetchone()
                if prior is None:
                    raise StoreCorruptionError("checkpoint head references a missing current checkpoint")
                checkpoint_seq = int(prior["checkpoint_seq"]) + 1
                previous_hash = str(prior["checkpoint_hash"])
                head_revision = int(head["revision"])
            checkpoint = build_regenerative_checkpoint(
                checkpoint_seq=checkpoint_seq,
                frontier=frontier,
                continuity_capsule_id=continuity_capsule_id,
                ledger_head_seq=ledger_head_seq,
                ledger_head_hash=ledger_head_hash,
                recovery_preconditions=recovery_preconditions,
                runtime_version=runtime_version,
                provider_version=provider_version,
                model_version=model_version,
                tool_contract_version=tool_contract_version,
                skill_contract_version=skill_contract_version,
                task_contract_version=task_contract_version,
                previous_checkpoint_hash=previous_hash,
                created_at_ms=created_at_ms,
                semantic_handoff=semantic_handoff,
            )
            checkpoint_json = json.dumps(
                checkpoint.model_dump(mode="json"), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False,
            )
            self._connection.execute(
                """INSERT INTO regenerative_checkpoint(
                    checkpoint_id, checkpoint_seq, request_id, run_id, generation, life_id,
                    ledger_head_seq, ledger_head_hash, frontier_hash, continuity_capsule_id,
                    created_at_ms, checksum_sha256, checkpoint_hash, checkpoint_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    checkpoint.checkpoint_id, checkpoint.checkpoint_seq,
                    checkpoint.request_id, checkpoint.run_id, checkpoint.generation,
                    checkpoint.life_id, checkpoint.ledger_head_seq, checkpoint.ledger_head_hash,
                    checkpoint.frontier_hash, checkpoint.continuity_capsule_id,
                    checkpoint.created_at_ms, checkpoint.checksum_sha256,
                    checkpoint.checkpoint_hash, checkpoint_json,
                ),
            )
            if head is None:
                self._connection.execute(
                    """INSERT INTO regenerative_checkpoint_head(
                        request_id, run_id, generation, current_checkpoint_id,
                        previous_checkpoint_id, revision, updated_at_ms
                    ) VALUES (?,?,?,?,NULL,1,?)""",
                    (
                        frontier.request_id, frontier.run_id, frontier.generation,
                        checkpoint.checkpoint_id, created_at_ms,
                    ),
                )
            else:
                updated = self._connection.execute(
                    """UPDATE regenerative_checkpoint_head
                       SET current_checkpoint_id=?, previous_checkpoint_id=?, revision=?, updated_at_ms=?
                       WHERE request_id=? AND run_id=? AND generation=? AND revision=?
                         AND current_checkpoint_id=?""",
                    (
                        checkpoint.checkpoint_id, previous_checkpoint_id, head_revision + 1,
                        created_at_ms, frontier.request_id, frontier.run_id, frontier.generation,
                        head_revision, previous_checkpoint_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise StoreCasConflict("regenerative checkpoint head changed concurrently")
            return checkpoint

    def _checkpoint_from_row_locked(self, row: sqlite3.Row) -> RegenerativeCheckpoint:
        try:
            checkpoint = RegenerativeCheckpoint.model_validate_json(
                str(row["checkpoint_json"]), strict=True
            )
        except ValueError as exc:
            raise StoreCorruptionError("regenerative checkpoint JSON is invalid") from exc
        if (
            not checkpoint.has_valid_hashes()
            or checkpoint.checkpoint_id != row["checkpoint_id"]
            or checkpoint.checkpoint_seq != row["checkpoint_seq"]
            or checkpoint.request_id != row["request_id"]
            or checkpoint.run_id != row["run_id"]
            or checkpoint.generation != row["generation"]
            or checkpoint.life_id != row["life_id"]
            or checkpoint.ledger_head_seq != row["ledger_head_seq"]
            or checkpoint.ledger_head_hash != row["ledger_head_hash"]
            or checkpoint.frontier_hash != row["frontier_hash"]
            or checkpoint.continuity_capsule_id != row["continuity_capsule_id"]
            or checkpoint.created_at_ms != row["created_at_ms"]
            or checkpoint.checksum_sha256 != row["checksum_sha256"]
            or checkpoint.checkpoint_hash != row["checkpoint_hash"]
        ):
            raise StoreCorruptionError("regenerative checkpoint row disagrees with its checksum")
        return checkpoint

    def load_regenerative_checkpoint(
        self, request_id: str, *, run_id: str, generation: int
    ) -> tuple[RegenerativeCheckpoint | None, bool]:
        """Return current checkpoint or previous known-good when current is corrupt."""
        with self._lock:
            head = self._connection.execute(
                """SELECT * FROM regenerative_checkpoint_head
                   WHERE request_id=? AND run_id=? AND generation=?""",
                (request_id, run_id, generation),
            ).fetchone()
            if head is None:
                return None, False
            ids = [str(head["current_checkpoint_id"])]
            if head["previous_checkpoint_id"]:
                ids.append(str(head["previous_checkpoint_id"]))
            for index, checkpoint_id in enumerate(ids):
                row = self._connection.execute(
                    "SELECT * FROM regenerative_checkpoint WHERE checkpoint_id=?",
                    (checkpoint_id,),
                ).fetchone()
                if row is None:
                    continue
                try:
                    checkpoint = self._checkpoint_from_row_locked(row)
                except StoreCorruptionError:
                    continue
                return checkpoint, index == 1
            raise StoreCorruptionError("current and previous regenerative checkpoints are invalid")

    def recover_regenerative_execution(
        self,
        request_id: str,
        *,
        run_id: str,
        generation: int,
        recovered_at_ms: int,
    ) -> dict:
        """Checkpoint -> ledger-tail audit/recovery -> bounded Frontier replay."""
        checkpoint, used_previous = self.load_regenerative_checkpoint(
            request_id, run_id=run_id, generation=generation
        )
        if checkpoint is None:
            return {"recoverable": False, "reason": "checkpoint_missing"}
        audit = self.audit_execution_ledger(request_id, run_id=run_id, generation=generation)
        if not audit["healthy"]:
            audit = self.recover_execution_ledger_tail(
                request_id, run_id=run_id, generation=generation,
                known_good_seq=checkpoint.ledger_head_seq,
                recovered_at_ms=recovered_at_ms,
            )
        if checkpoint.ledger_head_seq:
            row = self._connection.execute(
                """SELECT event_hash FROM execution_ledger
                   WHERE request_id=? AND run_id=? AND generation=? AND ledger_seq=?""",
                (request_id, run_id, generation, checkpoint.ledger_head_seq),
            ).fetchone()
            if row is None or str(row["event_hash"]) != checkpoint.ledger_head_hash:
                raise StoreCorruptionError("known-good checkpoint does not match its ledger anchor")
        elif checkpoint.ledger_head_hash != ZERO_HASH:
            raise StoreCorruptionError("genesis checkpoint has a non-genesis ledger hash")
        frontier = checkpoint.frontier
        tail = self.list_execution_events(
            request_id, run_id=run_id, generation=generation,
            after_seq=checkpoint.ledger_head_seq,
        )
        for event in tail:
            if event.event_type != "frontier.updated":
                continue
            raw = event.payload.get("frontier")
            if not isinstance(raw, dict):
                raise StoreCorruptionError("frontier.updated event has no frontier snapshot")
            candidate = ExecutionFrontier.model_validate(raw, strict=True)
            if not candidate.has_valid_hash():
                raise StoreCorruptionError("replayed frontier snapshot is invalid")
            if (
                candidate.request_id != request_id
                or candidate.run_id != run_id
                or candidate.generation != generation
                or candidate.frontier_version <= frontier.frontier_version
            ):
                raise StoreCorruptionError("replayed frontier crossed identity or revision order")
            frontier = candidate
        effects = self.list_effects_for_request(
            request_id, run_id=run_id, generation=generation
        )
        pending = tuple(sorted(
            record.claim.effect_id for record in effects
            if record.state in {"CLAIMED", "SIDE_EFFECT_STARTED"}
        ))
        ambiguous = tuple(sorted(
            record.claim.effect_id for record in effects if record.state == "AMBIGUOUS"
        ))
        return {
            "recoverable": True,
            "checkpoint": checkpoint,
            "used_previous_checkpoint": used_previous,
            "frontier": frontier,
            "ledger_tail": tail,
            "ledger_audit": audit,
            "pending_effect_ids": pending,
            "ambiguous_effect_ids": ambiguous,
        }

'''
store = replace_once(
    store,
    '    def count_unreconciled_attempts(self) -> int:\n',
    methods + '    def count_unreconciled_attempts(self) -> int:\n',
    label="regenerative store methods",
)
STORE.write_text(store, encoding="utf-8", newline="\n")
