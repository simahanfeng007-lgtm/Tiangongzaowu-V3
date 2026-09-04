from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from contracts import (
    InboundEnvelope,
    InboundScope,
    canonical_sha256,
    derive_inbound_scope_keys,
    derive_request_identity,
    derive_run_identity,
)
from contracts.verification import AcceptancePredicate
from total_gateway.composition_activation_registration import (
    ExistingGatewayActivationRegistrationPort,
    compile_limited_activation_registration,
)
from total_gateway.composition_activation_shadow import (
    build_system_verification_binding,
    propose_shadow_composition_activation,
)
from total_gateway.store import (
    APPLICATION_ID,
    STORE_SCHEMA_VERSION,
    GatewayStateStore,
    StoreConflictError,
)
from total_gateway.verification_plane import VERIFICATION_PLANE_VERSION
from total_gateway.verification_registry import VerifierRegistry
from world_understanding.capability_composition import (
    compile_capability_composition_plan,
    parse_composition_proposal,
    validate_capability_composition_plan,
)

from tests.test_capability_composition_p4 import _single_read_fixture


HASH_B = "b" * 64


def _register_request_lineage(store: GatewayStateStore):
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_p7b2",
        link_account_id="desktop_p7b2",
        conversation_ref="conversation_p7b2",
        channel_message_ref="message_p7b2",
        sender_ref="sender_p7b2",
    )
    keys = derive_inbound_scope_keys(scope)
    idempotency_key = "7" * 64
    request = derive_request_identity(idempotency_key)
    run = derive_run_identity(request.request_id, 1)
    envelope = InboundEnvelope(
        inbound_id="inbound_p7b2",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=1_000,
        idempotency_key=idempotency_key,
        channel_metadata_hash=HASH_B,
        text="register the P7B.2 limited composition",
    )
    registration = store.register_request(
        envelope, ingress_sha256=HASH_B, created_at_ms=1_100
    )
    assert registration.entry.request_id == request.request_id
    store.acquire_generation_lease(
        request_id=request.request_id,
        run_id=run.run_id,
        run_sequence=1,
        generation=1,
        gateway_epoch=1,
        lease_id="lease_p7b2",
        owner_instance_id="gateway_p7b2",
        issued_at_ms=1_200,
        lease_duration_ms=10_000,
    )
    return envelope, request, run


def _bundle_fixture(store: GatewayStateStore):
    envelope, request, run = _register_request_lineage(store)
    action_registry, candidates, context, document = _single_read_fixture()
    context = replace(
        context,
        request_id=request.request_id,
        run_id=run.run_id,
        generation=1,
        principal_scope_hash=envelope.principal_scope_hash,
        created_at_ms=1_250,
        context_sha256="0" * 64,
    ).with_computed_sha256()
    proposal = parse_composition_proposal(document, candidates)
    plan = compile_capability_composition_plan(
        proposal, candidates, context, action_registry
    )
    validation = validate_capability_composition_plan(
        plan,
        proposal,
        candidates,
        context,
        action_registry,
        available_verifiers=frozenset(plan.verification_intents),
        validated_at_ms=1_300,
    )
    assert validation.result == "PROVED_VALID"
    verification_registry = VerifierRegistry.with_defaults().snapshot(
        captured_at_ms=1_350
    )
    predicate = AcceptancePredicate.create(
        predicate_type="artifact.nonempty",
        subject_kind="artifact",
        params={},
    )
    binding = build_system_verification_binding(
        intent_ref=plan.verification_intents[0],
        predicate=predicate,
        subject_identity="object:p7b2-read-output",
        evaluation_phase="POST_EXECUTION",
        registry_snapshot=verification_registry,
    )
    bindings = (binding,)
    shadow = propose_shadow_composition_activation(
        plan,
        validation,
        action_registry,
        verification_registry,
        bindings,
        current_world_state_sha256=plan.world_state_sha256,
        expected_principal_scope_hash=plan.principal_scope_hash,
        issued_at_ms=1_500,
        expires_at_ms=2_500,
    )
    return {
        "proposal": shadow,
        "plan": plan,
        "validation": validation,
        "action_registry": action_registry,
        "verification_registry": verification_registry,
        "verification_bindings": bindings,
        "current_world_state_sha256": plan.world_state_sha256,
        "expected_principal_scope_hash": plan.principal_scope_hash,
    }


def _persist(store: GatewayStateStore, fixture: dict, *, recorded_at_ms: int):
    return store.register_limited_composition_activation_bundle(
        fixture["proposal"],
        plan=fixture["plan"],
        validation=fixture["validation"],
        action_registry=fixture["action_registry"],
        verification_registry=fixture["verification_registry"],
        verification_bindings=fixture["verification_bindings"],
        current_world_state_sha256=fixture[
            "current_world_state_sha256"
        ],
        expected_principal_scope_hash=fixture[
            "expected_principal_scope_hash"
        ],
        recorded_at_ms=recorded_at_ms,
    )


def test_p7b2_explicitly_advances_store_and_p19_compatibility() -> None:
    # P7B.2 remains v30, P7C.0 adds the v31 executable-Plan companion, and
    # P7C.1 adds the current v32 authorization receipt.  Neither later layer
    # changes P7B eligibility semantics.
    assert STORE_SCHEMA_VERSION == 32
    assert VERIFICATION_PLANE_VERSION == "1.4"


def test_v29_store_migrates_additively_through_v30_to_current() -> None:
    import total_gateway.store as store_module

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway-v29.sqlite3"
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            for version, migration_id, statements in store_module._MIGRATIONS[:29]:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations("
                    "version, migration_id, migration_sha256, applied_at_ms"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        version,
                        migration_id,
                        store_module._MIGRATION_DIGESTS[version],
                        1_000,
                    ),
                )
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute("PRAGMA user_version = 29")
        finally:
            connection.close()

        with GatewayStateStore.open(path, now_ms=1_500) as store:
            assert store.health_check(now_ms=1_500, full=True).healthy
            assert store._connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0] == 32
            assert tuple(
                row[0]
                for row in store._connection.execute(
                    "SELECT version FROM schema_migrations "
                    "WHERE version BETWEEN 30 AND 32 ORDER BY version"
                ).fetchall()
            ) == (30, 31, 32)
            assert store._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='composition_activation_registration'"
            ).fetchone() is not None
            assert store._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='composition_executable_plan'"
            ).fetchone() is not None
            assert store._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='composition_step_authorization'"
            ).fetchone() is not None


def test_bundle_is_atomic_and_roundtrips_from_the_existing_store() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            result = _persist(store, fixture, recorded_at_ms=1_600)
            assert result.created_by_this_call is True
            assert result.duplicate is False
            assert result.record.state == "ACTIVE"
            assert result.record.active_at(1_600)
            assert result.record.registration.authorizes is False
            assert result.record.registration.may_execute is False
            assert result.receipt.persisted is True
            assert result.verification_plan_activation_id.startswith("vpa_")
            stored = store.get_limited_activation_registration_record(
                result.record.registration.registration_id
            )
            assert stored is not None
            assert stored.registration == result.record.registration
            assert stored.verification_plan_activation_id == (
                result.verification_plan_activation_id
            )
            assert store.get_registry_snapshot(
                fixture["verification_registry"].registry_snapshot_id
            ) == fixture["verification_registry"]
            assert store.get_verification_plan(
                fixture["proposal"].verification_plan.verification_plan_id
            ) == fixture["proposal"].verification_plan
            assert store.get_active_verification_plan(
                request_id=fixture["plan"].request_id,
                run_id=fixture["plan"].run_id,
                generation=fixture["plan"].generation,
            ) == fixture["proposal"].verification_plan
            assert store.health_check(now_ms=1_700, full=True).healthy


def test_bundle_replay_preserves_first_writer_timestamp_and_row() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            first = _persist(store, fixture, recorded_at_ms=1_600)
            second = _persist(store, fixture, recorded_at_ms=1_700)
            assert first.created_by_this_call is True
            assert second.created_by_this_call is False
            assert second.duplicate is True
            assert first.record.registration.registration_id == (
                second.record.registration.registration_id
            )
            assert first.record.registration.registration_sha256 == (
                second.record.registration.registration_sha256
            )
            assert second.record.registration.registered_at_ms == 1_600
            assert second.receipt.idempotent_replay is True
            count = store._connection.execute(
                "SELECT count(*) FROM composition_activation_registration"
            ).fetchone()[0]
            assert count == 1


def test_p19_and_registration_writes_roll_back_as_one_uow() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            with mock.patch.object(
                GatewayStateStore,
                "_put_limited_activation_registration_from_bundle",
                side_effect=RuntimeError("forced registration failure"),
            ):
                with pytest.raises(RuntimeError, match="forced registration"):
                    _persist(store, fixture, recorded_at_ms=1_600)
            assert store.get_registry_snapshot(
                fixture["verification_registry"].registry_snapshot_id
            ) is None
            assert store.get_verification_plan(
                fixture["proposal"].verification_plan.verification_plan_id
            ) is None
            assert store.get_active_verification_plan(
                request_id=fixture["plan"].request_id,
                run_id=fixture["plan"].run_id,
                generation=fixture["plan"].generation,
            ) is None
            assert store._connection.execute(
                "SELECT count(*) FROM composition_activation_registration"
            ).fetchone()[0] == 0


def test_restart_recovers_active_then_expires_without_deleting_evidence() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            created = _persist(store, fixture, recorded_at_ms=1_600)
            registration_id = created.record.registration.registration_id

        with GatewayStateStore.open(path, now_ms=2_000) as reopened:
            recovered = reopened.recover_limited_activation_registrations(
                now_ms=2_000
            )
            assert len(recovered) == 1
            assert recovered[0].registration.registration_id == registration_id
            assert recovered[0].recovered_after_restart is True
            assert reopened.get_active_limited_activation_registration(
                registration_id, now_ms=2_000
            ) is not None

        with GatewayStateStore.open(path, now_ms=2_600) as expired:
            assert expired.get_active_limited_activation_registration(
                registration_id, now_ms=2_600
            ) is None
            record = expired.get_limited_activation_registration_record(
                registration_id
            )
            assert record is not None
            assert record.state == "EXPIRED"
            assert record.expired_at_ms == 2_600
            assert record.has_valid_lifecycle()
            assert expired._connection.execute(
                "SELECT count(*) FROM composition_activation_registration"
            ).fetchone()[0] == 1


def test_expired_registration_cannot_be_reactivated_by_replay() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            created = _persist(store, fixture, recorded_at_ms=1_600)
            registration_id = created.record.registration.registration_id
            assert store.expire_limited_activation_registrations(
                now_ms=2_600
            ) == (registration_id,)
            replay = _persist(store, fixture, recorded_at_ms=1_700)
            assert replay.duplicate is True
            assert replay.record.state == "EXPIRED"
            assert store.get_active_limited_activation_registration(
                registration_id, now_ms=2_600
            ) is None


def test_two_store_connections_converge_on_one_registration() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        bootstrap = GatewayStateStore.open(path, now_ms=1_000)
        fixture = _bundle_fixture(bootstrap)
        bootstrap.close()
        first = GatewayStateStore.open(path, now_ms=1_400)
        second = GatewayStateStore.open(path, now_ms=1_400)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = tuple(
                    pool.map(
                        lambda store: _persist(
                            store, fixture, recorded_at_ms=1_600
                        ),
                        (first, second),
                    )
                )
            assert sum(item.created_by_this_call for item in outcomes) == 1
            assert sum(item.duplicate for item in outcomes) == 1
            assert {
                item.record.registration.registration_id for item in outcomes
            }.__len__() == 1
            assert first._connection.execute(
                "SELECT count(*) FROM composition_activation_registration"
            ).fetchone()[0] == 1
        finally:
            first.close()
            second.close()


def test_active_read_fails_closed_after_generation_scope_changes() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            result = _persist(store, fixture, recorded_at_ms=1_600)
            store.release_generation(
                fixture["plan"].request_id,
                released_at_ms=1_700,
            )
            with pytest.raises(StoreConflictError, match="current generation"):
                store.get_active_limited_activation_registration(
                    result.record.registration.registration_id,
                    now_ms=1_800,
                )


def test_integrity_scan_detects_column_payload_tampering() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            result = _persist(store, fixture, recorded_at_ms=1_600)
            store._connection.execute(
                "UPDATE composition_activation_registration "
                "SET world_state_sha256 = ? WHERE registration_id = ?",
                (
                    "f" * 64,
                    result.record.registration.registration_id,
                ),
            )
            health = store.health_check(now_ms=1_700, full=True)
            assert health.healthy is False
            assert health.reason_code == "store.check.failed"



def test_direct_store_registration_port_is_closed_and_private_sink_is_guarded() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        with GatewayStateStore.open(path, now_ms=1_000) as store:
            fixture = _bundle_fixture(store)
            registration = compile_limited_activation_registration(
                fixture["proposal"],
                plan=fixture["plan"],
                validation=fixture["validation"],
                action_registry=fixture["action_registry"],
                verification_registry=fixture["verification_registry"],
                verification_bindings=fixture["verification_bindings"],
                current_world_state_sha256=fixture[
                    "current_world_state_sha256"
                ],
                expected_principal_scope_hash=fixture[
                    "expected_principal_scope_hash"
                ],
                registered_at_ms=1_600,
            )
            assert not isinstance(
                store, ExistingGatewayActivationRegistrationPort
            )
            assert not hasattr(store, "put_limited_activation_registration")
            with pytest.raises(
                StoreConflictError,
                match="authoritative bundle path",
            ):
                store._put_limited_activation_registration_from_bundle(
                    registration,
                    expected_absent=True,
                    recorded_at_ms=1_600,
                    _bundle_write_token=object(),
                )
            assert store._connection.execute(
                "SELECT count(*) FROM composition_activation_registration"
            ).fetchone()[0] == 0


def test_p7b2_has_no_second_store_or_execution_authority() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "total_gateway"
    helper = (root / "composition_activation_store.py").read_text(
        encoding="utf-8"
    )
    store = (root / "store.py").read_text(encoding="utf-8")
    assert store.count("class GatewayStateStore:") == 1
    for forbidden in (
        "class CompositionActivationStore",
        "sqlite3.connect",
        "ExecutionTicket(",
        "OmniCapabilityGrant(",
        "PolicyDecision(",
        "CompletionDecision(",
        ".execute(",
        ".dispatch(",
    ):
        assert forbidden not in helper
