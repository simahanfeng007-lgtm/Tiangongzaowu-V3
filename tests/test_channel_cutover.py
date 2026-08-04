import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from communication_service.adapters import AdapterRegistry
from communication_service.channel_authority import (
    ChannelAuthorityError,
    ChannelAuthorityGate,
)
from communication_service.delivery_ledger import DeliveryLedger
from communication_service.drain import ChannelDrainNotReady, CommunicationDrainInspector
from communication_service.inbox import CommunicationInbox
from communication_service.server import _strict_model_from_json_value
from contracts import (
    ChannelOwnershipLease,
    activate_candidate_owner,
    apply_channel_drain,
    begin_channel_cutover,
    build_channel_drain_evidence,
    renew_candidate_owner,
)
from total_gateway.store import (
    CHANNEL_LEASE_CLOCK_SKEW_MS,
    GatewayStateStore,
    STORE_SCHEMA_VERSION,
    StoreConflictError,
)
from total_gateway.bootstrap import GatewayConfig
from total_gateway.runtime import GatewayRuntime
from tests.test_communication_inbox import ingress as inbox_ingress
from tests.gateway_store_migration_support import downgrade_v12_to_v11
from tests.test_delivery_contracts import (
    consume_verified_delivery_for_test,
    delivery_ticket,
)


EPOCH = 17
MANIFEST_SHA256 = "d" * 64


def cutover_fixture(*, gateway_epoch=EPOCH, candidate="candidate-instance"):
    snapshot = begin_channel_cutover(
        channel="wechat",
        tenant_id="tenant-1",
        link_account_id="account-1",
        gateway_epoch=gateway_epoch,
        legacy_owner_component_id="legacy-communication",
        legacy_owner_instance_id="legacy-instance",
        candidate_owner_instance_id=candidate,
        started_at_ms=1_000,
    )
    evidence = build_channel_drain_evidence(
        channel="wechat",
        tenant_id="tenant-1",
        link_account_id="account-1",
        gateway_epoch=gateway_epoch,
        legacy_owner_component_id="legacy-communication",
        legacy_owner_instance_id="legacy-instance",
        inbox_ledger_sha256="a" * 64,
        delivery_ledger_sha256="b" * 64,
        last_cursor_sha256="c" * 64,
        observed_at_ms=1_100,
    )
    return snapshot, evidence


def active_fixture(*, gateway_epoch=EPOCH, candidate="candidate-instance"):
    snapshot, evidence = cutover_fixture(
        gateway_epoch=gateway_epoch,
        candidate=candidate,
    )
    drained = apply_channel_drain(snapshot, evidence)
    active, lease = activate_candidate_owner(
        drained,
        evidence,
        component_manifest_sha256=MANIFEST_SHA256,
        issued_at_ms=1_200,
        lease_ttl_ms=30_000,
    )
    return snapshot, evidence, drained, active, lease


class ChannelCutoverContractTests(unittest.TestCase):
    def test_http_json_round_trip_preserves_tuple_bound_lease(self):
        *_, lease = active_fixture()
        rebuilt = _strict_model_from_json_value(
            ChannelOwnershipLease,
            lease.model_dump(mode="json"),
        )
        self.assertEqual(rebuilt, lease)

    def test_drain_precedes_single_epoch_candidate_ownership_and_renews_by_digest(self):
        snapshot, evidence, drained, active, lease = active_fixture()

        self.assertEqual(snapshot.state, "DRAINING")
        self.assertEqual(drained.state, "DRAINED")
        self.assertEqual(active.state, "CANDIDATE_ACTIVE")
        self.assertEqual(active.gateway_epoch, active.migration_epoch)
        self.assertEqual(lease.allowed_operations, ("POLL", "SEND"))
        self.assertIsNone(lease.previous_lease_sha256)

        renewed_snapshot, renewed = renew_candidate_owner(
            active,
            evidence,
            lease,
            issued_at_ms=2_000,
            lease_ttl_ms=30_000,
        )
        self.assertEqual(renewed.previous_lease_sha256, lease.lease_sha256)
        self.assertEqual(renewed_snapshot.active_lease_id, renewed.lease_id)
        self.assertNotEqual(renewed.lease_id, lease.lease_id)

    def test_nonzero_or_unstopped_drain_evidence_cannot_activate(self):
        snapshot, evidence = cutover_fixture()
        for update in (
            {"poller_stopped": False},
            {"sender_stopped": False},
            {"inflight_poll_count": 1},
            {"inflight_send_count": 1},
            {"unacknowledged_inbox_count": 1},
            {"unresolved_delivery_count": 1},
        ):
            with self.subTest(update=update):
                bad = evidence.model_copy(
                    update={**update, "evidence_sha256": "0" * 64}
                ).with_computed_sha256()
                with self.assertRaises(ValueError):
                    apply_channel_drain(snapshot, bad)

    def test_activation_before_drain_old_epoch_and_late_renewal_fail_closed(self):
        snapshot, evidence = cutover_fixture()
        with self.assertRaises(ValueError):
            activate_candidate_owner(
                snapshot,
                evidence,
                component_manifest_sha256=MANIFEST_SHA256,
                issued_at_ms=1_200,
            )
        _, evidence, _, active, lease = active_fixture()
        with self.assertRaises(ValueError):
            renew_candidate_owner(
                active,
                evidence,
                lease,
                issued_at_ms=lease.expires_at_ms + 1,
            )


class ChannelCutoverStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=100)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def begin_and_drain(self):
        snapshot, evidence = cutover_fixture()
        self.assertTrue(
            self.store.begin_channel_cutover(
                snapshot,
                current_gateway_epoch=EPOCH,
            )
        )
        drained = self.store.record_channel_drain(
            evidence,
            current_gateway_epoch=EPOCH,
        )
        return snapshot, evidence, drained

    def test_persistent_activation_renewal_restart_and_old_epoch_fence(self):
        snapshot, _, _ = self.begin_and_drain()
        first = self.store.activate_channel_candidate(
            snapshot.cutover_id,
            current_gateway_epoch=EPOCH,
            component_manifest_sha256=MANIFEST_SHA256,
            issued_at_ms=1_200,
        )
        duplicate = self.store.activate_channel_candidate(
            snapshot.cutover_id,
            current_gateway_epoch=EPOCH,
            component_manifest_sha256=MANIFEST_SHA256,
            issued_at_ms=1_200,
        )
        self.assertTrue(first.created_by_this_call)
        self.assertTrue(duplicate.duplicate)
        renewed = self.store.renew_channel_candidate(
            snapshot.cutover_id,
            current_gateway_epoch=EPOCH,
            issued_at_ms=2_000,
        )
        self.assertEqual(renewed.lease.previous_lease_sha256, first.lease.lease_sha256)
        self.assertEqual(self.store.count_channel_cutover_records(), (1, 1, 2))
        self.assertIsNone(
            self.store.get_active_channel_lease(
                channel="wechat",
                tenant_id="tenant-1",
                link_account_id="account-1",
                current_gateway_epoch=EPOCH + 1,
                now_ms=2_001,
            )
        )
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=2_002)
        recovered = self.store.get_active_channel_lease(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
            current_gateway_epoch=EPOCH,
            now_ms=2_002,
        )
        self.assertEqual(recovered, renewed.lease)
        self.assertTrue(self.store.health_check(now_ms=2_002, full=True).healthy)

    def test_activation_insert_failure_rolls_back_lease_and_snapshot(self):
        snapshot, _, drained = self.begin_and_drain()
        self.store._connection.execute(  # noqa: SLF001 - deliberate fault injection
            """
            CREATE TRIGGER test_abort_channel_lease
            BEFORE INSERT ON channel_ownership_lease
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.activate_channel_candidate(
                    snapshot.cutover_id,
                    current_gateway_epoch=EPOCH,
                    component_manifest_sha256=MANIFEST_SHA256,
                    issued_at_ms=1_200,
                )
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_channel_lease")  # noqa: SLF001
        self.assertEqual(self.store.get_channel_cutover(snapshot.cutover_id), drained)
        self.assertEqual(self.store.count_channel_cutover_records(), (1, 1, 0))

    def test_two_connections_concurrently_create_only_one_active_lease(self):
        snapshot, _, _ = self.begin_and_drain()
        other = GatewayStateStore.open(self.path, now_ms=1_150)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def activate(store):
            try:
                barrier.wait()
                results.append(
                    store.activate_channel_candidate(
                        snapshot.cutover_id,
                        current_gateway_epoch=EPOCH,
                        component_manifest_sha256=MANIFEST_SHA256,
                        issued_at_ms=1_200,
                    )
                )
            except Exception as exc:  # assertions inspect both concurrent outcomes
                errors.append(exc)

        threads = [
            threading.Thread(target=activate, args=(self.store,)),
            threading.Thread(target=activate, args=(other,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        other.close()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(item.created_by_this_call for item in results), 1)
        self.assertEqual(sum(item.duplicate for item in results), 1)
        self.assertEqual(self.store.count_channel_cutover_records(), (1, 1, 1))

    def test_new_epoch_waits_for_old_lease_and_clock_skew_then_fences_old_epoch(self):
        old_snapshot, _, _ = self.begin_and_drain()
        old = self.store.activate_channel_candidate(
            old_snapshot.cutover_id,
            current_gateway_epoch=EPOCH,
            component_manifest_sha256=MANIFEST_SHA256,
            issued_at_ms=1_200,
        )
        unsafe, _ = cutover_fixture(gateway_epoch=EPOCH + 1)
        unsafe = unsafe.model_copy(
            update={
                "started_at_ms": old.lease.expires_at_ms
                + CHANNEL_LEASE_CLOCK_SKEW_MS
                - 1,
                "updated_at_ms": old.lease.expires_at_ms
                + CHANNEL_LEASE_CLOCK_SKEW_MS
                - 1,
                "snapshot_sha256": "0" * 64,
            }
        ).with_computed_sha256()
        with self.assertRaisesRegex(StoreConflictError, "not expired safely"):
            self.store.begin_channel_cutover(
                unsafe,
                current_gateway_epoch=EPOCH + 1,
            )

        safe_start = old.lease.expires_at_ms + CHANNEL_LEASE_CLOCK_SKEW_MS
        newer = begin_channel_cutover(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
            gateway_epoch=EPOCH + 1,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            candidate_owner_instance_id="candidate-instance-new",
            started_at_ms=safe_start,
        )
        self.assertTrue(
            self.store.begin_channel_cutover(
                newer,
                current_gateway_epoch=EPOCH + 1,
            )
        )
        with self.assertRaisesRegex(StoreConflictError, "superseded"):
            self.store.renew_channel_candidate(
                old_snapshot.cutover_id,
                current_gateway_epoch=EPOCH,
                issued_at_ms=2_000,
            )
        newer_evidence = build_channel_drain_evidence(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
            gateway_epoch=EPOCH + 1,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            inbox_ledger_sha256="e" * 64,
            delivery_ledger_sha256="f" * 64,
            last_cursor_sha256=None,
            observed_at_ms=safe_start + 100,
        )
        self.store.record_channel_drain(
            newer_evidence,
            current_gateway_epoch=EPOCH + 1,
        )
        current = self.store.activate_channel_candidate(
            newer.cutover_id,
            current_gateway_epoch=EPOCH + 1,
            component_manifest_sha256=MANIFEST_SHA256,
            issued_at_ms=safe_start + 200,
        )
        self.assertEqual(current.lease.gateway_epoch, EPOCH + 1)
        self.assertTrue(self.store.health_check(now_ms=safe_start + 201, full=True).healthy)

    def test_semantic_tamper_fails_health(self):
        snapshot, _, _ = self.begin_and_drain()
        self.store.activate_channel_candidate(
            snapshot.cutover_id,
            current_gateway_epoch=EPOCH,
            component_manifest_sha256=MANIFEST_SHA256,
            issued_at_ms=1_200,
        )
        self.store._connection.execute(  # noqa: SLF001 - deliberate semantic tamper
            "UPDATE channel_ownership_lease SET owner_instance_id = 'other-instance'"
        )
        self.assertFalse(self.store.health_check(now_ms=1_201, full=True).healthy)

    def test_v8_database_migrates_in_place_to_current_schema(self):
        self.store.close()
        connection = sqlite3.connect(self.path)
        downgrade_v12_to_v11(connection)
        connection.execute("DROP TABLE outbox_dispatch_boundary")
        connection.execute("DROP TABLE request_inbound_payload")
        connection.execute("DROP INDEX channel_one_active_lease")
        connection.execute("DROP INDEX channel_cutover_scope_epoch")
        connection.execute("DROP TABLE channel_ownership_lease")
        connection.execute("DROP TABLE channel_drain_evidence")
        connection.execute("DROP TABLE channel_cutover")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 9")
        connection.execute("PRAGMA user_version = 8")
        connection.commit()
        connection.close()
        self.store = GatewayStateStore.open(self.path, now_ms=200)
        health = self.store.health_check(now_ms=200, full=True)
        self.assertTrue(health.healthy)
        self.assertEqual(health.schema_version, STORE_SCHEMA_VERSION)
        self.assertEqual(self.store.count_channel_cutover_records(), (0, 0, 0))


class GatewayRuntimeCutoverTests(unittest.TestCase):
    def test_runtime_pins_every_cutover_write_and_read_to_its_instance_epoch(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = GatewayRuntime.start(
                GatewayConfig(
                    environment="test",
                    port=0,
                    state_root=Path(temporary) / "gateway",
                ),
                now_ms=1_000,
            )
            try:
                epoch = runtime.lease.gateway_epoch
                snapshot, evidence = cutover_fixture(gateway_epoch=epoch)
                runtime.begin_channel_cutover(snapshot)
                runtime.record_channel_drain(evidence)
                active = runtime.activate_channel_candidate(
                    snapshot.cutover_id,
                    component_manifest_sha256=MANIFEST_SHA256,
                    issued_at_ms=1_200,
                )
                self.assertEqual(active.lease.gateway_epoch, epoch)
                self.assertEqual(
                    runtime.get_active_channel_lease(
                        channel="wechat",
                        tenant_id="tenant-1",
                        link_account_id="account-1",
                        now_ms=1_201,
                    ),
                    active.lease,
                )
                old_snapshot, _ = cutover_fixture(gateway_epoch=epoch + 1)
                with self.assertRaises(ValueError):
                    runtime.begin_channel_cutover(old_snapshot)
            finally:
                runtime.close()


class ChannelAuthorityGateTests(unittest.TestCase):
    def test_only_exact_candidate_instance_can_poll_and_send(self):
        _, evidence, _, active, lease = active_fixture()
        candidate = ChannelAuthorityGate(
            owner_instance_id="candidate-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        legacy = ChannelAuthorityGate(
            owner_instance_id="legacy-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        self.assertTrue(candidate.install_lease(lease, now_ms=1_201))
        with self.assertRaises(ChannelAuthorityError):
            legacy.install_lease(lease, now_ms=1_201)
        for operation in ("POLL", "SEND"):
            authorized = candidate.authorize(
                channel="wechat",
                tenant_id="tenant-1",
                link_account_id="account-1",
                operation=operation,
                now_ms=1_201,
            )
            self.assertEqual(authorized.lease_id, active.active_lease_id)
            with self.assertRaises(ChannelAuthorityError):
                legacy.authorize(
                    channel="wechat",
                    tenant_id="tenant-1",
                    link_account_id="account-1",
                    operation=operation,
                    now_ms=1_201,
                )
        with self.assertRaises(ChannelAuthorityError):
            candidate.authorize(
                channel="wechat",
                tenant_id="tenant-1",
                link_account_id="other-account",
                operation="POLL",
                now_ms=1_201,
            )

    def test_restart_expiry_drain_and_discontinuous_renewal_fail_closed(self):
        _, evidence, _, active, lease = active_fixture()
        gate = ChannelAuthorityGate(
            owner_instance_id="candidate-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        gate.install_lease(lease, now_ms=1_201)
        _, renewed = renew_candidate_owner(
            active,
            evidence,
            lease,
            issued_at_ms=2_000,
        )
        self.assertTrue(gate.install_lease(renewed, now_ms=2_001))
        bad = renewed.model_copy(
            update={"previous_lease_sha256": "e" * 64, "lease_sha256": "0" * 64}
        ).with_computed_sha256()
        with self.assertRaises(ChannelAuthorityError):
            gate.install_lease(bad, now_ms=2_001)
        gate.begin_drain(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
        )
        with self.assertRaisesRegex(ChannelAuthorityError, "draining"):
            gate.authorize(
                channel="wechat",
                tenant_id="tenant-1",
                link_account_id="account-1",
                operation="SEND",
                now_ms=2_001,
            )
        restarted = ChannelAuthorityGate(
            owner_instance_id="candidate-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        with self.assertRaisesRegex(ChannelAuthorityError, "lease_missing"):
            restarted.authorize(
                channel="wechat",
                tenant_id="tenant-1",
                link_account_id="account-1",
                operation="POLL",
                now_ms=2_001,
            )
        with self.assertRaises(ChannelAuthorityError):
            restarted.install_lease(renewed, now_ms=renewed.expires_at_ms)

    def test_adapter_poll_boundary_is_unconfigured_by_default_and_exact_when_bound(self):
        registry = AdapterRegistry()
        with self.assertRaisesRegex(ChannelAuthorityError, "unconfigured"):
            registry.authorize_operation(
                channel="wechat",
                tenant_id="tenant-1",
                link_account_id="account-1",
                operation="POLL",
                now_ms=1_201,
            )
        _, _, _, _, lease = active_fixture()
        gate = ChannelAuthorityGate(
            owner_instance_id="candidate-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        registry.bind_channel_authority(gate)
        registry.install_channel_lease(lease, now_ms=1_201)
        registry.authorize_operation(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
            operation="POLL",
            now_ms=1_201,
        )


class CommunicationDrainInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.inbox = CommunicationInbox.open(root / "inbox.sqlite3", now_ms=100)
        self.deliveries = DeliveryLedger.open(root / "delivery.sqlite3", now_ms=100)

    def tearDown(self):
        self.deliveries.close()
        self.inbox.close()
        self.temporary.cleanup()

    def inspector(self, gate):
        return CommunicationDrainInspector(self.inbox, self.deliveries, gate)

    def test_empty_durable_ledgers_produce_machine_hashed_zero_drain(self):
        gate = ChannelAuthorityGate(
            owner_instance_id="legacy-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        evidence = self.inspector(gate).capture(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
            gateway_epoch=EPOCH,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            observed_at_ms=1_100,
        )
        self.assertTrue(evidence.has_valid_sha256())
        self.assertEqual(evidence.inflight_poll_count, 0)
        self.assertEqual(evidence.inflight_send_count, 0)
        self.assertEqual(evidence.unacknowledged_inbox_count, 0)
        self.assertEqual(evidence.unresolved_delivery_count, 0)
        self.assertNotEqual(evidence.inbox_ledger_sha256, "0" * 64)
        self.assertNotEqual(evidence.delivery_ledger_sha256, "0" * 64)

    def test_drain_blocks_new_operations_and_refuses_existing_inflight_poll(self):
        _, _, _, _, lease = active_fixture(candidate="legacy-instance")
        gate = ChannelAuthorityGate(
            owner_instance_id="legacy-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        gate.install_lease(lease, now_ms=1_201)
        inspector = self.inspector(gate)
        with gate.operation(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
            operation="POLL",
            now_ms=1_201,
        ):
            with self.assertRaisesRegex(ChannelDrainNotReady, "poll_inflight"):
                inspector.capture(
                    channel="wechat",
                    tenant_id="tenant-1",
                    link_account_id="account-1",
                    gateway_epoch=EPOCH,
                    legacy_owner_component_id="legacy-communication",
                    legacy_owner_instance_id="legacy-instance",
                    observed_at_ms=1_300,
                )
        with self.assertRaisesRegex(ChannelAuthorityError, "draining"):
            gate.authorize(
                channel="wechat",
                tenant_id="tenant-1",
                link_account_id="account-1",
                operation="POLL",
                now_ms=1_301,
            )
        evidence = inspector.capture(
            channel="wechat",
            tenant_id="tenant-1",
            link_account_id="account-1",
            gateway_epoch=EPOCH,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            observed_at_ms=1_302,
        )
        self.assertTrue(evidence.has_valid_sha256())

    def test_unacknowledged_inbox_and_claimed_delivery_block_drain(self):
        incoming = inbox_ingress()
        self.inbox.persist_and_advance_cursor(incoming, persisted_at_ms=1_200)
        inbox_gate = ChannelAuthorityGate(
            owner_instance_id="legacy-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        with self.assertRaisesRegex(ChannelDrainNotReady, "inbox_unacknowledged"):
            self.inspector(inbox_gate).capture(
                channel=incoming.envelope.channel,
                tenant_id=incoming.envelope.tenant_id,
                link_account_id=incoming.envelope.link_account_id,
                gateway_epoch=EPOCH,
                legacy_owner_component_id="legacy-communication",
                legacy_owner_instance_id="legacy-instance",
                observed_at_ms=1_300,
            )

        ticket = delivery_ticket()
        consume_verified_delivery_for_test(self.deliveries, ticket, at_ms=22_000)
        delivery_gate = ChannelAuthorityGate(
            owner_instance_id="legacy-instance",
            expected_gateway_epoch=EPOCH,
            expected_component_manifest_sha256=MANIFEST_SHA256,
        )
        with self.assertRaisesRegex(ChannelDrainNotReady, "send_inflight"):
            self.inspector(delivery_gate).capture(
                channel=ticket.payload.channel,
                tenant_id=ticket.payload.tenant_id,
                link_account_id=ticket.payload.link_account_id,
                gateway_epoch=EPOCH,
                legacy_owner_component_id="legacy-communication",
                legacy_owner_instance_id="legacy-instance",
                observed_at_ms=22_100,
            )


if __name__ == "__main__":
    unittest.main()
