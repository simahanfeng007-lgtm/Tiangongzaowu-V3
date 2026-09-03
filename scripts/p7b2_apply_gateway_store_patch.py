from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


store_path = "src/total_gateway/store.py"
store = read(store_path)
store = replace_once(
    store,
    "STORE_SCHEMA_VERSION = 29",
    "STORE_SCHEMA_VERSION = 30",
    "store schema version",
)

import_anchor = '''from .regenerative_execution import (
    ZERO_HASH,
    ExecutionFrontier,
    ExecutionLedgerEvent,
    RegenerativeCheckpoint,
    build_execution_ledger_event,
    build_regenerative_checkpoint,
)

if TYPE_CHECKING:
'''
import_replacement = '''from .regenerative_execution import (
    ZERO_HASH,
    ExecutionFrontier,
    ExecutionLedgerEvent,
    RegenerativeCheckpoint,
    build_execution_ledger_event,
    build_regenerative_checkpoint,
)
from .composition_activation_store import (
    LimitedActivationBundleRegistration,
    LimitedActivationStoreRecord,
    canonical_registration_json,
    canonical_string_tuple_json,
    computed_limited_activation_lifecycle_sha256,
    limited_activation_record_from_row,
)

if TYPE_CHECKING:
'''
store = replace_once(store, import_anchor, import_replacement, "store import")

migration = r'''
_MIGRATION_V30_ID = "gateway-composition-activation-registration-v30"
_MIGRATION_V30_STATEMENTS = (
    """
    CREATE TABLE composition_activation_registration (
        registration_id TEXT NOT NULL PRIMARY KEY
            CHECK (registration_id GLOB 'car_[0-9a-f]*'),
        composition_activation_id TEXT NOT NULL UNIQUE,
        composition_activation_sha256 TEXT NOT NULL
            CHECK (length(composition_activation_sha256) = 64
                   AND composition_activation_sha256 NOT GLOB '*[^0-9a-f]*'),
        shadow_proposal_sha256 TEXT NOT NULL
            CHECK (length(shadow_proposal_sha256) = 64
                   AND shadow_proposal_sha256 NOT GLOB '*[^0-9a-f]*'),
        differential_trace_sha256 TEXT NOT NULL
            CHECK (length(differential_trace_sha256) = 64
                   AND differential_trace_sha256 NOT GLOB '*[^0-9a-f]*'),
        composition_plan_id TEXT NOT NULL,
        composition_plan_sha256 TEXT NOT NULL
            CHECK (length(composition_plan_sha256) = 64
                   AND composition_plan_sha256 NOT GLOB '*[^0-9a-f]*'),
        verification_plan_id TEXT NOT NULL,
        verification_plan_sha256 TEXT NOT NULL
            CHECK (length(verification_plan_sha256) = 64
                   AND verification_plan_sha256 NOT GLOB '*[^0-9a-f]*'),
        verification_plan_activation_id TEXT NOT NULL,
        validation_mode TEXT NOT NULL
            CHECK (validation_mode IN ('PROVED_VALID','PROVISIONAL_UNKNOWN')),
        validation_sha256 TEXT NOT NULL
            CHECK (length(validation_sha256) = 64
                   AND validation_sha256 NOT GLOB '*[^0-9a-f]*'),
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        principal_scope_hash TEXT NOT NULL
            CHECK (length(principal_scope_hash) = 64
                   AND principal_scope_hash NOT GLOB '*[^0-9a-f]*'),
        world_state_sha256 TEXT NOT NULL
            CHECK (length(world_state_sha256) = 64
                   AND world_state_sha256 NOT GLOB '*[^0-9a-f]*'),
        source_manifest_sha256 TEXT NOT NULL
            CHECK (length(source_manifest_sha256) = 64
                   AND source_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
        capability_manifest_sha256 TEXT NOT NULL
            CHECK (length(capability_manifest_sha256) = 64
                   AND capability_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
        action_registry_sha256 TEXT NOT NULL
            CHECK (length(action_registry_sha256) = 64
                   AND action_registry_sha256 NOT GLOB '*[^0-9a-f]*'),
        verification_registry_sha256 TEXT NOT NULL
            CHECK (length(verification_registry_sha256) = 64
                   AND verification_registry_sha256 NOT GLOB '*[^0-9a-f]*'),
        allowed_action_ids_json TEXT NOT NULL CHECK (json_valid(allowed_action_ids_json)),
        allowed_action_versions_json TEXT NOT NULL CHECK (json_valid(allowed_action_versions_json)),
        issued_at_ms INTEGER NOT NULL CHECK (issued_at_ms >= 0),
        expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms > issued_at_ms),
        registered_at_ms INTEGER NOT NULL
            CHECK (registered_at_ms >= issued_at_ms AND registered_at_ms < expires_at_ms),
        provisional_verification_required INTEGER NOT NULL
            CHECK (provisional_verification_required IN (0,1)),
        state TEXT NOT NULL CHECK (state IN ('ACTIVE','EXPIRED')),
        expired_at_ms INTEGER CHECK (expired_at_ms IS NULL OR expired_at_ms >= expires_at_ms),
        registration_json TEXT NOT NULL CHECK (json_valid(registration_json)),
        registration_sha256 TEXT NOT NULL
            CHECK (length(registration_sha256) = 64
                   AND registration_sha256 NOT GLOB '*[^0-9a-f]*'),
        lifecycle_sha256 TEXT NOT NULL
            CHECK (length(lifecycle_sha256) = 64
                   AND lifecycle_sha256 NOT GLOB '*[^0-9a-f]*'),
        CHECK (
            (validation_mode = 'PROVISIONAL_UNKNOWN' AND provisional_verification_required = 1)
            OR (validation_mode = 'PROVED_VALID' AND provisional_verification_required = 0)
        ),
        CHECK (
            (state = 'ACTIVE' AND expired_at_ms IS NULL)
            OR (state = 'EXPIRED' AND expired_at_ms IS NOT NULL)
        ),
        FOREIGN KEY (verification_plan_id)
            REFERENCES verification_plan(verification_plan_id),
        FOREIGN KEY (verification_plan_activation_id)
            REFERENCES verification_plan_activation(activation_id)
    ) STRICT
    """,
    """
    CREATE UNIQUE INDEX composition_activation_registration_lineage_idx
        ON composition_activation_registration (request_id, run_id, generation)
    """,
    """
    CREATE INDEX composition_activation_registration_expiry_idx
        ON composition_activation_registration (state, expires_at_ms, registration_id)
    """,
)

'''
store = replace_once(
    store,
    "_MIGRATIONS = (\n",
    migration + "_MIGRATIONS = (\n",
    "v30 migration insert",
)
store = replace_once(
    store,
    "    (29, _MIGRATION_V29_ID, _MIGRATION_V29_STATEMENTS),\n)",
    "    (29, _MIGRATION_V29_ID, _MIGRATION_V29_STATEMENTS),\n"
    "    (30, _MIGRATION_V30_ID, _MIGRATION_V30_STATEMENTS),\n)",
    "v30 migration tuple",
)

verification_helper = r'''
def _verify_limited_activation_registration_rows(
    connection: sqlite3.Connection,
) -> None:
    """Verify v30 composition registrations against canonical P19 authorities."""

    from contracts.verification import VerificationPlan

    rows = connection.execute(
        "SELECT * FROM composition_activation_registration ORDER BY registration_id"
    ).fetchall()
    for row in rows:
        try:
            record = limited_activation_record_from_row(row)
        except ValueError as exc:
            raise StoreCorruptionError(
                "stored limited activation registration is invalid"
            ) from exc
        registration = record.registration

        registry_row = connection.execute(
            "SELECT * FROM verification_registry_snapshot WHERE snapshot_sha256 = ?",
            (registration.verification_registry_sha256,),
        ).fetchone()
        if registry_row is None:
            raise StoreCorruptionError(
                "limited activation references a missing verification registry"
            )
        try:
            snapshot = RegistrySnapshot.model_validate_json(
                registry_row["snapshot_json"], strict=True
            )
        except ValueError as exc:
            raise StoreCorruptionError(
                "limited activation verification registry is invalid"
            ) from exc
        if (
            not snapshot.has_valid_identity()
            or snapshot.snapshot_sha256
            != registration.verification_registry_sha256
            or registry_row["registry_snapshot_id"]
            != snapshot.registry_snapshot_id
            or registry_row["snapshot_sha256"] != snapshot.snapshot_sha256
        ):
            raise StoreCorruptionError(
                "limited activation verification registry binding is invalid"
            )

        plan_row = connection.execute(
            "SELECT * FROM verification_plan WHERE verification_plan_id = ?",
            (registration.verification_plan_id,),
        ).fetchone()
        if plan_row is None:
            raise StoreCorruptionError(
                "limited activation references a missing verification plan"
            )
        try:
            plan = VerificationPlan.model_validate_json(
                plan_row["plan_json"], strict=True
            )
        except ValueError as exc:
            raise StoreCorruptionError(
                "limited activation verification plan is invalid"
            ) from exc
        if (
            not plan.has_valid_identity()
            or plan.verification_plan_id != registration.verification_plan_id
            or plan.plan_sha256 != registration.verification_plan_sha256
            or plan.request_id != registration.request_id
            or plan.run_id != registration.run_id
            or plan.generation != registration.generation
            or plan.registry_snapshot_sha256
            != registration.verification_registry_sha256
            or plan_row["plan_sha256"] != plan.plan_sha256
        ):
            raise StoreCorruptionError(
                "limited activation verification plan binding is invalid"
            )

        activation_row = connection.execute(
            "SELECT * FROM verification_plan_activation WHERE activation_id = ?",
            (record.verification_plan_activation_id,),
        ).fetchone()
        if activation_row is None:
            raise StoreCorruptionError(
                "limited activation references a missing plan activation"
            )
        expected_activation_sha256 = canonical_sha256(
            {
                "domain": "tiangong.verification-plan-activation.v1",
                "request_id": activation_row["request_id"],
                "run_id": activation_row["run_id"],
                "generation": activation_row["generation"],
                "verification_plan_id": activation_row["verification_plan_id"],
                "verification_plan_sha256": activation_row[
                    "verification_plan_sha256"
                ],
                "registry_snapshot_sha256": activation_row[
                    "registry_snapshot_sha256"
                ],
            }
        )
        if (
            activation_row["request_id"] != registration.request_id
            or activation_row["run_id"] != registration.run_id
            or activation_row["generation"] != registration.generation
            or activation_row["verification_plan_id"]
            != registration.verification_plan_id
            or activation_row["verification_plan_sha256"]
            != registration.verification_plan_sha256
            or activation_row["registry_snapshot_sha256"]
            != registration.verification_registry_sha256
            or activation_row["activation_sha256"]
            != expected_activation_sha256
        ):
            raise StoreCorruptionError(
                "limited activation P19 activation binding is invalid"
            )


'''
store = replace_once(
    store,
    "def _verify_full_event_chain(connection: sqlite3.Connection) -> None:\n",
    verification_helper
    + "def _verify_full_event_chain(connection: sqlite3.Connection) -> None:\n",
    "registration verifier insert",
)
store = replace_once(
    store,
    "            store = cls(path, connection)\n"
    "            health = store.health_check(now_ms=now_ms, full=True)\n",
    "            store = cls(path, connection)\n"
    "            store.expire_limited_activation_registrations(now_ms=now_ms)\n"
    "            health = store.health_check(now_ms=now_ms, full=True)\n",
    "open expiry recovery",
)
store = replace_once(
    store,
    "                _verify_channel_cutover_rows(self._connection)\n"
    "                if full:\n",
    "                _verify_channel_cutover_rows(self._connection)\n"
    "                _verify_limited_activation_registration_rows(self._connection)\n"
    "                if full:\n",
    "health registration verification",
)

methods = r'''
    @property
    def authority_kind(self) -> str:
        from .composition_activation_registration import (
            EXISTING_GATEWAY_STATE_STORE_AUTHORITY,
        )

        return EXISTING_GATEWAY_STATE_STORE_AUTHORITY

    def get_limited_activation_registration_record(
        self, registration_id: str
    ) -> LimitedActivationStoreRecord | None:
        if not registration_id:
            raise ValueError("limited activation registration identity is invalid")
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            row = self._connection.execute(
                "SELECT * FROM composition_activation_registration "
                "WHERE registration_id = ?",
                (registration_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                return limited_activation_record_from_row(row)
            except ValueError as exc:
                raise StoreCorruptionError(
                    "stored limited activation registration is invalid"
                ) from exc

    def get_limited_activation_registration(self, registration_id: str):
        record = self.get_limited_activation_registration_record(registration_id)
        return None if record is None else record.registration

    def put_limited_activation_registration(
        self,
        registration,
        *,
        expected_absent: bool,
        recorded_at_ms: int,
    ) -> bool:
        """Persist one P7B.1 eligibility row under existing Store authority."""

        from contracts.verification import VerificationPlan
        from .composition_activation_registration import (
            LimitedCompositionActivationRegistrationV1,
        )

        if not isinstance(
            registration, LimitedCompositionActivationRegistrationV1
        ):
            raise ValueError("limited activation registration has the wrong type")
        if not registration.has_valid_identity():
            raise ValueError("limited activation registration identity is invalid")
        if expected_absent is not True:
            raise ValueError("limited activation registration requires expected_absent")
        if recorded_at_ms != registration.registered_at_ms:
            raise ValueError(
                "limited activation registration time differs from its row"
            )
        if not (
            registration.issued_at_ms
            <= recorded_at_ms
            < registration.expires_at_ms
        ):
            raise ValueError("limited activation registration is not live")

        with self._lock, self._write_transaction():
            current = self._assert_request_binding_locked(
                request_id=registration.request_id,
                run_id=registration.run_id,
                generation=registration.generation,
                recorded_at_ms=recorded_at_ms,
            )
            if current["status"] != "ACTIVE":
                raise StoreConflictError(
                    "limited activation registration generation is not active"
                )

            registry_row = self._connection.execute(
                "SELECT * FROM verification_registry_snapshot "
                "WHERE snapshot_sha256 = ?",
                (registration.verification_registry_sha256,),
            ).fetchone()
            if registry_row is None:
                raise StoreNotFoundError(
                    "limited activation verification registry does not exist"
                )
            snapshot = RegistrySnapshot.model_validate_json(
                registry_row["snapshot_json"], strict=True
            )
            if (
                not snapshot.has_valid_identity()
                or snapshot.snapshot_sha256
                != registration.verification_registry_sha256
            ):
                raise StoreConflictError(
                    "limited activation verification registry changed"
                )

            plan_row = self._connection.execute(
                "SELECT * FROM verification_plan WHERE verification_plan_id = ?",
                (registration.verification_plan_id,),
            ).fetchone()
            if plan_row is None:
                raise StoreNotFoundError(
                    "limited activation verification plan does not exist"
                )
            plan = VerificationPlan.model_validate_json(
                plan_row["plan_json"], strict=True
            )
            if (
                not plan.has_valid_identity()
                or plan.plan_sha256 != registration.verification_plan_sha256
                or plan.request_id != registration.request_id
                or plan.run_id != registration.run_id
                or plan.generation != registration.generation
                or plan.registry_snapshot_sha256
                != registration.verification_registry_sha256
            ):
                raise StoreConflictError(
                    "limited activation verification plan changed"
                )

            activation_row = self._connection.execute(
                "SELECT * FROM verification_plan_activation "
                "WHERE request_id = ? AND run_id = ? AND generation = ?",
                (
                    registration.request_id,
                    registration.run_id,
                    registration.generation,
                ),
            ).fetchone()
            if activation_row is None:
                raise StoreNotFoundError(
                    "limited activation P19 plan is not active"
                )
            if (
                activation_row["verification_plan_id"]
                != registration.verification_plan_id
                or activation_row["verification_plan_sha256"]
                != registration.verification_plan_sha256
                or activation_row["registry_snapshot_sha256"]
                != registration.verification_registry_sha256
            ):
                raise StoreConflictError(
                    "limited activation crossed its active P19 plan"
                )

            rows = self._connection.execute(
                "SELECT * FROM composition_activation_registration "
                "WHERE registration_id = ? OR composition_activation_id = ? "
                "OR (request_id = ? AND run_id = ? AND generation = ?)",
                (
                    registration.registration_id,
                    registration.composition_activation_id,
                    registration.request_id,
                    registration.run_id,
                    registration.generation,
                ),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise StoreCorruptionError(
                        "limited activation identities diverged"
                    )
                try:
                    existing = limited_activation_record_from_row(rows[0])
                except ValueError as exc:
                    raise StoreCorruptionError(
                        "stored limited activation registration is invalid"
                    ) from exc
                if not existing.registration.has_same_authority(registration):
                    raise StoreConflictError(
                        "limited activation identity was reused for different authority"
                    )
                return False

            registration_json = canonical_registration_json(registration)
            action_ids_json = canonical_string_tuple_json(
                registration.allowed_action_ids
            )
            action_versions_json = canonical_string_tuple_json(
                registration.allowed_action_versions
            )
            lifecycle_sha256 = computed_limited_activation_lifecycle_sha256(
                registration_id=registration.registration_id,
                registration_sha256=registration.registration_sha256,
                state="ACTIVE",
                expires_at_ms=registration.expires_at_ms,
                expired_at_ms=None,
            )
            self._connection.execute(
                """
                INSERT INTO composition_activation_registration(
                    registration_id, composition_activation_id,
                    composition_activation_sha256, shadow_proposal_sha256,
                    differential_trace_sha256, composition_plan_id,
                    composition_plan_sha256, verification_plan_id,
                    verification_plan_sha256,
                    verification_plan_activation_id, validation_mode,
                    validation_sha256, request_id, run_id, generation,
                    principal_scope_hash, world_state_sha256,
                    source_manifest_sha256, capability_manifest_sha256,
                    action_registry_sha256, verification_registry_sha256,
                    allowed_action_ids_json, allowed_action_versions_json,
                    issued_at_ms, expires_at_ms, registered_at_ms,
                    provisional_verification_required, state, expired_at_ms,
                    registration_json, registration_sha256, lifecycle_sha256
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    'ACTIVE',NULL,?,?,?
                )
                """,
                (
                    registration.registration_id,
                    registration.composition_activation_id,
                    registration.composition_activation_sha256,
                    registration.shadow_proposal_sha256,
                    registration.differential_trace_sha256,
                    registration.composition_plan_id,
                    registration.composition_plan_sha256,
                    registration.verification_plan_id,
                    registration.verification_plan_sha256,
                    activation_row["activation_id"],
                    registration.validation_mode,
                    registration.validation_sha256,
                    registration.request_id,
                    registration.run_id,
                    registration.generation,
                    registration.principal_scope_hash,
                    registration.world_state_sha256,
                    registration.source_manifest_sha256,
                    registration.capability_manifest_sha256,
                    registration.action_registry_sha256,
                    registration.verification_registry_sha256,
                    action_ids_json,
                    action_versions_json,
                    registration.issued_at_ms,
                    registration.expires_at_ms,
                    registration.registered_at_ms,
                    int(registration.provisional_verification_required),
                    registration_json,
                    registration.registration_sha256,
                    lifecycle_sha256,
                ),
            )
            return True

    def expire_limited_activation_registrations(
        self, *, now_ms: int
    ) -> tuple[str, ...]:
        if now_ms < 0:
            raise ValueError("limited activation expiry time is invalid")
        expired: list[str] = []
        with self._lock, self._write_transaction():
            rows = self._connection.execute(
                "SELECT * FROM composition_activation_registration "
                "WHERE state = 'ACTIVE' AND expires_at_ms <= ? "
                "ORDER BY registration_id",
                (now_ms,),
            ).fetchall()
            for row in rows:
                try:
                    record = limited_activation_record_from_row(row)
                except ValueError as exc:
                    raise StoreCorruptionError(
                        "stored limited activation registration is invalid"
                    ) from exc
                registration = record.registration
                lifecycle_sha256 = (
                    computed_limited_activation_lifecycle_sha256(
                        registration_id=registration.registration_id,
                        registration_sha256=registration.registration_sha256,
                        state="EXPIRED",
                        expires_at_ms=registration.expires_at_ms,
                        expired_at_ms=now_ms,
                    )
                )
                update = self._connection.execute(
                    "UPDATE composition_activation_registration "
                    "SET state = 'EXPIRED', expired_at_ms = ?, "
                    "lifecycle_sha256 = ? "
                    "WHERE registration_id = ? AND state = 'ACTIVE' "
                    "AND lifecycle_sha256 = ?",
                    (
                        now_ms,
                        lifecycle_sha256,
                        registration.registration_id,
                        record.lifecycle_sha256,
                    ),
                )
                if update.rowcount != 1:
                    raise StoreCasConflict(
                        "limited activation changed during expiry"
                    )
                expired.append(registration.registration_id)
        return tuple(expired)

    def recover_limited_activation_registrations(
        self, *, now_ms: int
    ) -> tuple[LimitedActivationStoreRecord, ...]:
        """Recover only live registrations still on the current generation."""

        self.expire_limited_activation_registrations(now_ms=now_ms)
        with self._lock:
            if self._closed:
                raise StoreError("gateway store is closed")
            rows = self._connection.execute(
                """
                SELECT c.*
                FROM composition_activation_registration AS c
                JOIN request_generation AS g ON g.request_id = c.request_id
                WHERE c.state = 'ACTIVE'
                  AND c.registered_at_ms <= ? AND c.expires_at_ms > ?
                  AND g.run_id = c.run_id
                  AND g.current_generation = c.generation
                  AND g.status = 'ACTIVE'
                ORDER BY c.registered_at_ms, c.registration_id
                """,
                (now_ms, now_ms),
            ).fetchall()
            recovered: list[LimitedActivationStoreRecord] = []
            for row in rows:
                try:
                    recovered.append(
                        limited_activation_record_from_row(
                            row, recovered_after_restart=True
                        )
                    )
                except ValueError as exc:
                    raise StoreCorruptionError(
                        "stored limited activation registration is invalid"
                    ) from exc
            return tuple(recovered)

    def get_active_limited_activation_registration(
        self, registration_id: str, *, now_ms: int
    ) -> LimitedActivationStoreRecord | None:
        self.expire_limited_activation_registrations(now_ms=now_ms)
        record = self.get_limited_activation_registration_record(registration_id)
        if record is None or record.state == "EXPIRED":
            return None
        if not record.active_at(now_ms):
            return None
        registration = record.registration
        with self._lock:
            current = self._connection.execute(
                "SELECT run_id, current_generation, status "
                "FROM request_generation WHERE request_id = ?",
                (registration.request_id,),
            ).fetchone()
            if (
                current is None
                or current["run_id"] != registration.run_id
                or current["current_generation"] != registration.generation
                or current["status"] != "ACTIVE"
            ):
                raise StoreConflictError(
                    "limited activation registration is not bound to current generation"
                )
        return record

    def register_limited_composition_activation_bundle(
        self,
        proposal,
        *,
        plan,
        validation,
        action_registry,
        verification_registry,
        verification_bindings,
        current_world_state_sha256: str,
        expected_principal_scope_hash: str,
        recorded_at_ms: int,
    ) -> LimitedActivationBundleRegistration:
        """Atomically persist P19 authorities and one P7B.1 registration."""

        from .composition_activation_registration import (
            LimitedCompositionActivationRegistrar,
        )

        with self._lock, self._write_transaction():
            registry_created = self.put_registry_snapshot(
                verification_registry, recorded_at_ms=recorded_at_ms
            )
            verification_plan_created = self.put_verification_plan(
                proposal.verification_plan, recorded_at_ms=recorded_at_ms
            )
            verification_plan_activation_id = self.activate_verification_plan(
                request_id=proposal.verification_plan.request_id,
                run_id=proposal.verification_plan.run_id,
                generation=proposal.verification_plan.generation,
                verification_plan_id=(
                    proposal.verification_plan.verification_plan_id
                ),
                verification_plan_sha256=proposal.verification_plan.plan_sha256,
                registry_snapshot_sha256=(
                    proposal.verification_plan.registry_snapshot_sha256
                ),
                activated_at_ms=recorded_at_ms,
            )
            receipt = LimitedCompositionActivationRegistrar(self).register(
                proposal,
                plan=plan,
                validation=validation,
                action_registry=action_registry,
                verification_registry=verification_registry,
                verification_bindings=verification_bindings,
                current_world_state_sha256=current_world_state_sha256,
                expected_principal_scope_hash=expected_principal_scope_hash,
                recorded_at_ms=recorded_at_ms,
            )
            row = self._connection.execute(
                "SELECT * FROM composition_activation_registration "
                "WHERE registration_id = ?",
                (receipt.registration_id,),
            ).fetchone()
            if row is None:
                raise StoreCorruptionError(
                    "limited activation registration write disappeared"
                )
            created = not receipt.idempotent_replay
            try:
                record = limited_activation_record_from_row(
                    row,
                    created_by_this_call=created,
                    duplicate=not created,
                )
            except ValueError as exc:
                raise StoreCorruptionError(
                    "stored limited activation registration is invalid"
                ) from exc
            if (
                record.verification_plan_activation_id
                != verification_plan_activation_id
            ):
                raise StoreCorruptionError(
                    "limited activation registration crossed P19 activation"
                )
            return LimitedActivationBundleRegistration(
                record=record,
                receipt=receipt,
                registry_created=registry_created,
                verification_plan_created=verification_plan_created,
                verification_plan_activation_id=(
                    verification_plan_activation_id
                ),
                created_by_this_call=created,
                duplicate=not created,
            )

'''
store = replace_once(
    store,
    "    def count_journal_entries(self) -> int:\n",
    methods + "    def count_journal_entries(self) -> int:\n",
    "store methods insert",
)
store = replace_once(
    store,
    '    "JournalRegistration",\n',
    '    "JournalRegistration",\n'
    '    "LimitedActivationBundleRegistration",\n'
    '    "LimitedActivationStoreRecord",\n',
    "store exports",
)
write(store_path, store)

# Fix the focused test to use the existing release API.
test_path = "tests/test_composition_activation_store_p7b2.py"
test_text = read(test_path)
test_text = replace_once(
    test_text,
    '''            store.release_generation_lease(
                request_id=fixture["plan"].request_id,
                lease_id="lease_p7b2",
                released_at_ms=1_700,
            )
''',
    '''            store.release_generation(
                fixture["plan"].request_id,
                released_at_ms=1_700,
            )
''',
    "release generation test",
)
write(test_path, test_text)

# Explicit P19 compatibility revision.
plane_path = "src/total_gateway/verification_plane.py"
plane = read(plane_path)
plane = replace_once(
    plane,
    'VERIFICATION_PLANE_VERSION = "1.0"',
    'VERIFICATION_PLANE_VERSION = "1.1"',
    "verification plane version",
)
write(plane_path, plane)

freeze_test_path = "tests/golden/p19_r2/test_freeze_and_guards.py"
freeze_test = read(freeze_test_path)
freeze_test = replace_once(
    freeze_test,
    'the single Verification Plane version source exists and is "1.0"',
    'the single Verification Plane version source exists and is "1.1"',
    "freeze doc version",
)
freeze_test = replace_once(
    freeze_test,
    'self.assertEqual(VERIFICATION_PLANE_VERSION, "1.0")',
    'self.assertEqual(VERIFICATION_PLANE_VERSION, "1.1")',
    "freeze assertion version",
)
freeze_test = replace_once(
    freeze_test,
    "if '\"1.0\"' in (",
    "if '\"1.1\"' in (",
    "freeze holder version",
)
freeze_test = replace_once(
    freeze_test,
    '"baseline_sha": "a94035f221d8769dcf4d5bcd1e7fa2827aa9623a",',
    '"baseline_sha": "14f9ef400786d01b65fd1cb495ea42aaa55473e1",',
    "freeze baseline",
)
freeze_test = replace_once(
    freeze_test,
    '''        "src/total_gateway/store.py",
        "src/total_gateway/store_unit_of_work.py",
''',
    '''        "src/total_gateway/store.py",
        "src/total_gateway/store_unit_of_work.py",
        "src/total_gateway/composition_activation_shadow.py",
        "src/total_gateway/composition_activation_registration.py",
        "src/total_gateway/composition_activation_store.py",
''',
    "freeze authority additions",
)
write(freeze_test_path, freeze_test)

# Extend the human authority map and update its frozen digest.
authority_path = "docs/p19-r2/AUTHORITY_MAP.txt"
authority = read(authority_path)
section = '''

P7B.2｜Limited Composition Activation Persistence（Verification Plane 1.1）
----------------------------------------------------------------------

src/total_gateway/composition_activation_shadow.py
src/total_gateway/composition_activation_registration.py
src/total_gateway/composition_activation_store.py
src/total_gateway/store.py migration v30

上述新增面只负责把 P7A/P7B.1 的 A0-only eligibility registration 与既有
RegistrySnapshot、VerificationPlan、VerificationPlanActivation 在同一个
GatewayStateStore UoW 中原子持久化，并负责重启恢复、过期回收和完整性扫描。

它不改变 verifier PASS 语义、VerificationReadiness、Repair Policy 或
CompletionGate；不签发 PolicyDecision、ExecutionTicket、CapabilityGrant；
不调用 Runtime。
'''
if "P7B.2｜Limited Composition Activation Persistence" not in authority:
    authority = authority.rstrip() + section + "\n"
write(authority_path, authority)

# Refresh the explicit freeze manifest without importing the application.
manifest_path = ROOT / "docs/p19-r2/m6/VERIFICATION_PLANE_FREEZE.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["verification_plane_version"] = "1.1"
manifest["baseline_sha"] = "14f9ef400786d01b65fd1cb495ea42aaa55473e1"
manifest["store_schema_version"] = 30
manifest["authority_map_sha256"] = hashlib.sha256(
    (ROOT / authority_path).read_bytes()
).hexdigest()
authority_files = (
    "src/total_gateway/store.py",
    "src/total_gateway/store_unit_of_work.py",
    "src/total_gateway/verification_repair_coordinator.py",
    "src/total_gateway/verification_repair_policy.py",
    "src/total_gateway/verification_plan_executor.py",
    "src/total_gateway/verification_readiness.py",
    "src/total_gateway/verification_failure_evidence.py",
    "src/total_gateway/verification_registry.py",
    "src/total_gateway/outcome_oracles/artifact_content.py",
    "src/total_gateway/outcome_oracles/effect_state.py",
    "src/total_gateway/outcome_oracles/repository_state.py",
    "src/total_gateway/completion_gate.py",
    "src/contracts/verification.py",
    "src/contracts/verification_repair.py",
    "src/total_gateway/effects.py",
    "src/total_gateway/composition_activation_shadow.py",
    "src/total_gateway/composition_activation_registration.py",
    "src/total_gateway/composition_activation_store.py",
)
manifest["authority_surface_sha256"] = {
    rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
    for rel in authority_files
}
manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
    encoding="utf-8",
    newline="\n",
)

print(json.dumps({"ok": True, "patched": "P7B.2"}, sort_keys=True))
