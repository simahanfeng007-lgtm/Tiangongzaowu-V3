import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from contracts import InboundEnvelope, InboundScope, derive_inbound_scope_keys, derive_run_identity
from total_gateway.active_requests import ActiveRequestActivator
from total_gateway.life_client import LifeProfileBindings
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.store import GatewayStateStore, StoreConflictError
from tests.gateway_store_migration_support import downgrade_v12_to_v11


HASH_A = "a" * 64


def envelope(
    message_ref: str,
    *,
    conversation_ref: str = "conversation_001",
) -> InboundEnvelope:
    scope = InboundScope(
        channel="wechat",
        tenant_id="tenant_001",
        link_account_id="wechat_001",
        conversation_ref=conversation_ref,
        channel_message_ref=message_ref,
        sender_ref="sender_001",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id=f"inbound_{message_ref}",
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
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A,
        text="hello",
    )


class ActiveRequestActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        self.activator = ActiveRequestActivator(
            self.store,
            gateway_epoch=7,
            owner_instance_id="gateway-instance-001",
            lease_duration_ms=10_000,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def register(
        self,
        message_ref: str,
        *,
        conversation_ref: str = "conversation_001",
        created_at_ms: int = 1_100,
    ):
        return self.store.register_request(
            envelope(message_ref, conversation_ref=conversation_ref),
            ingress_sha256=HASH_A,
            created_at_ms=created_at_ms,
        )

    def test_orchestration_binds_life_token_as_keyword_only_transport_argument(self) -> None:
        orchestrator = object.__new__(GatewayOrchestrationWorker)
        orchestrator._life_token = "life-token-value"
        orchestrator._objects = object()
        inbound = envelope("message_001")
        profile = LifeProfileBindings(user_callsign="用户")
        expected = object()
        with (
            patch("total_gateway.orchestration.LoopbackLifeJsonTransport") as transport_type,
            patch("total_gateway.orchestration.LifeClient") as client_type,
        ):
            client_type.return_value.acquire_snapshot.return_value = expected
            actual = orchestrator._acquire_life_snapshot(inbound, profile)
        transport_type.assert_called_once_with(
            "http://127.0.0.1:7175",
            desktop_token="life-token-value",
        )
        client_type.assert_called_once_with(transport_type.return_value, orchestrator._objects)
        client_type.return_value.acquire_snapshot.assert_called_once_with(
            tenant_id=inbound.tenant_id,
            link_account_id=inbound.link_account_id,
            conversation_scope_hash=inbound.conversation_scope_hash,
            profile=profile,
        )
        self.assertIs(actual, expected)

    def test_active_generation_uses_one_atomic_life_call(self) -> None:
        orchestrator = object.__new__(GatewayOrchestrationWorker)
        orchestrator._life_token = "life-token-value"
        orchestrator._objects = object()
        inbound = envelope("message_atomic")
        profile = LifeProfileBindings(user_callsign="用户")
        activation = SimpleNamespace(
            entry=SimpleNamespace(request_id="req_" + "1" * 64),
            generation=SimpleNamespace(run_id="run_" + "2" * 64, generation=3),
        )
        expected = object()
        with (
            patch("total_gateway.orchestration.LoopbackLifeJsonTransport") as transport_type,
            patch("total_gateway.orchestration.LifeClient") as client_type,
        ):
            client_type.return_value.compile_and_authorize_snapshot.return_value = expected
            actual = orchestrator._acquire_life_snapshot(
                inbound, profile, activation, 4_000
            )
        client_type.return_value.compile_and_authorize_snapshot.assert_called_once_with(
            request_id=activation.entry.request_id,
            run_id=activation.generation.run_id,
            generation=3,
            current_request=inbound.text,
            tenant_id=inbound.tenant_id,
            link_account_id=inbound.link_account_id,
            conversation_scope_hash=inbound.conversation_scope_hash,
            profile=profile,
            observed_at_ms=4_000,
            current_context_tokens=None,
        )
        client_type.return_value.acquire_snapshot.assert_not_called()
        self.assertIs(actual, expected)

    def test_claim_next_initializes_only_request_authority_and_generation(self) -> None:
        first = self.register("message_001")
        queued = self.register("message_002", created_at_ms=1_200)
        other = self.register(
            "message_101",
            conversation_ref="conversation_002",
            created_at_ms=1_300,
        )
        candidates = self.store.list_unclaimed_active_requests()
        self.assertEqual(
            [candidate.entry.request_id for candidate in candidates],
            [first.entry.request_id, other.entry.request_id],
        )

        activation = self.activator.claim_next(now_ms=1_400)
        assert activation is not None
        self.assertTrue(activation.created_by_this_call)
        self.assertFalse(activation.duplicate)
        self.assertEqual(activation.entry.request_id, first.entry.request_id)
        self.assertEqual(activation.envelope, self.store.get_request_envelope(first.entry.request_id))
        self.assertEqual(activation.generation.run_sequence, 1)
        self.assertEqual(activation.generation.generation, 1)
        self.assertEqual(activation.generation.gateway_epoch, 7)
        self.assertEqual(
            activation.generation.run_id,
            derive_run_identity(first.entry.request_id, 1).run_id,
        )
        self.assertEqual(activation.request_snapshot.machine, "request")
        self.assertEqual(activation.request_snapshot.entity_id, first.entry.request_id)
        self.assertEqual(activation.request_snapshot.state, "RECEIVED")
        self.assertEqual(self.store.list_request_snapshots(first.entry.request_id), (activation.request_snapshot,))
        self.assertEqual(
            [candidate.entry.request_id for candidate in self.store.list_unclaimed_active_requests()],
            [other.entry.request_id],
        )
        self.assertEqual(queued.queue_state, "QUEUED")
        self.assertEqual(self.store.get_generation(queued.entry.request_id), None)
        self.assertEqual(
            self.store._connection.execute("SELECT count(*) FROM outbox").fetchone()[0],  # noqa: SLF001
            0,
        )
        self.assertEqual(
            self.store._connection.execute("SELECT count(*) FROM effect_ledger").fetchone()[0],  # noqa: SLF001
            0,
        )

    def test_cancelled_active_session_head_can_be_reconciled_and_promotes_next(self) -> None:
        first = self.register("message_cancelled")
        second = self.register("message_after_cancel", created_at_ms=1_200)
        activation = self.activator.claim(
            first.entry.request_id,
            first.entry.session_scope_hash,
            now_ms=1_300,
        )
        self.store.cancel_generation(
            first.entry.request_id,
            reason_code="desktop.user_cancelled",
            cancelled_at_ms=1_400,
        )
        self.assertEqual(
            self.store.list_cancelled_active_session_request_ids(),
            (first.entry.request_id,),
        )
        promoted = self.store.complete_session_request(
            first.entry.session_scope_hash,
            first.entry.request_id,
            completed_at_ms=1_400,
            release_generation=False,
        )
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.request_id, second.entry.request_id)
        self.assertEqual(promoted.state, "ACTIVE")
        self.assertEqual(
            self.store.get_generation(activation.entry.request_id).status,
            "CANCELLED",
        )
        self.assertEqual(self.store.list_cancelled_active_session_request_ids(), ())
        self.assertEqual(
            [item.entry.request_id for item in self.store.list_unclaimed_active_requests()],
            [second.entry.request_id],
        )

    def test_late_finalization_after_cancel_is_idempotent(self) -> None:
        """A late release after a user cancel must not wedge the request.

        Regression: the watchdog can stabilize a stuck effect as AMBIGUOUS
        after the user already cancelled the generation.  The late
        finalization calls complete_session_request(release_generation=True),
        and _release_generation_locked must treat CANCELLED as terminal and
        idempotent instead of raising StoreConflictError (which left the UI
        stuck in "thinking" forever).
        """
        registered = self.register("message_late_finalize")
        activation = self.activator.claim(
            registered.entry.request_id,
            registered.entry.session_scope_hash,
            now_ms=1_200,
        )
        self.store.cancel_generation(
            registered.entry.request_id,
            reason_code="desktop.user_cancelled",
            cancelled_at_ms=1_400,
        )
        self.assertEqual(
            self.store.get_generation(registered.entry.request_id).status,
            "CANCELLED",
        )
        # This previously raised StoreConflictError("generation lease cannot
        # be released") and left the request non-terminal.
        completed = self.store.complete_session_request(
            registered.entry.session_scope_hash,
            registered.entry.request_id,
            completed_at_ms=1_500,
            release_generation=True,
        )
        self.assertIsNone(completed)
        self.assertEqual(
            self.store.get_generation(registered.entry.request_id).status,
            "CANCELLED",
        )

    def test_unhandled_pre_effect_request_releases_generation_and_session_atomically(self) -> None:
        registered = self.register("message_001")
        activation = self.activator.claim(
            registered.entry.request_id,
            registered.entry.session_scope_hash,
            now_ms=1_200,
        )
        orchestrator = object.__new__(GatewayOrchestrationWorker)
        orchestrator._store = self.store
        orchestrator._finalize_unhandled(activation, RuntimeError("planning failed"))
        self.assertEqual(
            self.store.get_snapshot("request", registered.entry.request_id).state,
            "FAILED",
        )
        self.assertEqual(
            self.store.get_generation(registered.entry.request_id).status,
            "RELEASED",
        )
        queue = self.store._connection.execute(  # noqa: SLF001
            "SELECT state FROM session_queue WHERE request_id = ?",
            (registered.entry.request_id,),
        ).fetchone()
        self.assertEqual(queue["state"], "COMPLETED")
        self.assertTrue(self.store.health_check(now_ms=2_000, full=True).healthy)
        self.assertEqual(
            self.store._connection.execute("SELECT count(*) FROM effect_ledger").fetchone()[0],  # noqa: SLF001
            0,
        )

    def test_duplicate_claim_and_reopen_return_first_durable_activation(self) -> None:
        registered = self.register("message_001")
        first = self.activator.claim(
            registered.entry.request_id,
            registered.entry.session_scope_hash,
            now_ms=1_200,
        )
        duplicate = self.activator.claim(
            registered.entry.request_id,
            registered.entry.session_scope_hash,
            now_ms=1_300,
        )
        self.assertFalse(duplicate.created_by_this_call)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.generation, first.generation)
        self.assertEqual(duplicate.request_snapshot, first.request_snapshot)

        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=1_400)
        self.activator = ActiveRequestActivator(
            self.store,
            gateway_epoch=7,
            owner_instance_id="gateway-instance-001",
            lease_duration_ms=10_000,
        )
        reopened = self.activator.claim(
            registered.entry.request_id,
            registered.entry.session_scope_hash,
            now_ms=1_500,
        )
        self.assertTrue(reopened.duplicate)
        self.assertEqual(reopened.generation, first.generation)
        self.assertEqual(self.store.health_check(now_ms=1_600, full=True).healthy, True)

    def test_queued_cross_scope_and_different_owner_are_rejected(self) -> None:
        first = self.register("message_001")
        queued = self.register("message_002", created_at_ms=1_200)
        other_session = self.register(
            "message_101",
            conversation_ref="conversation_002",
            created_at_ms=1_250,
        )
        with self.assertRaises(StoreConflictError):
            self.activator.claim(
                queued.entry.request_id,
                queued.entry.session_scope_hash,
                now_ms=1_300,
            )
        with self.assertRaises(StoreConflictError):
            self.activator.claim(
                first.entry.request_id,
                other_session.entry.session_scope_hash,
                now_ms=1_300,
            )
        first_activation = self.activator.claim(
            first.entry.request_id,
            first.entry.session_scope_hash,
            now_ms=1_300,
        )
        different_owner = ActiveRequestActivator(
            self.store,
            gateway_epoch=8,
            owner_instance_id="gateway-instance-002",
        )
        with self.assertRaises(StoreConflictError):
            different_owner.claim(
                first.entry.request_id,
                first.entry.session_scope_hash,
                now_ms=1_400,
            )
        self.assertEqual(self.store.get_generation(first.entry.request_id), first_activation.generation)
        self.assertIsNone(self.store.get_generation(queued.entry.request_id))

    def test_snapshot_insert_fault_rolls_back_generation_and_fence(self) -> None:
        registered = self.register("message_001")
        self.store._connection.execute(  # noqa: SLF001 - deliberate transaction fault
            """
            CREATE TRIGGER test_abort_request_authority
            BEFORE INSERT ON aggregate_state
            BEGIN
                SELECT RAISE(ABORT, 'fault injection');
            END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.activator.claim(
                    registered.entry.request_id,
                    registered.entry.session_scope_hash,
                    now_ms=1_200,
                )
        finally:
            self.store._connection.execute("DROP TRIGGER test_abort_request_authority")  # noqa: SLF001
        self.assertIsNone(self.store.get_generation(registered.entry.request_id))
        self.assertEqual(self.store.list_request_snapshots(registered.entry.request_id), ())
        self.assertEqual(
            self.store._connection.execute("SELECT count(*) FROM generation_fences").fetchone()[0],  # noqa: SLF001
            0,
        )
        self.assertEqual(len(self.store.list_unclaimed_active_requests()), 1)

    def test_two_connections_converge_on_one_activation(self) -> None:
        registered = self.register("message_001")
        other_store = GatewayStateStore.open(self.path, now_ms=950)
        other_activator = ActiveRequestActivator(
            other_store,
            gateway_epoch=7,
            owner_instance_id="gateway-instance-001",
            lease_duration_ms=10_000,
        )
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def claim(activator: ActiveRequestActivator) -> None:
            try:
                barrier.wait(timeout=5)
                results.append(
                    activator.claim(
                        registered.entry.request_id,
                        registered.entry.session_scope_hash,
                        now_ms=1_200,
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = (
            threading.Thread(target=claim, args=(self.activator,)),
            threading.Thread(target=claim, args=(other_activator,)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        other_store.close()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(item.created_by_this_call for item in results), 1)
        self.assertEqual(sum(item.duplicate for item in results), 1)
        self.assertEqual(results[0].generation, results[1].generation)
        self.assertEqual(self.store.count_journal_entries(), 1)
        self.assertEqual(len(self.store.list_request_snapshots(registered.entry.request_id)), 1)

    def test_heartbeat_extends_the_same_generation_without_effects(self) -> None:
        registered = self.register("message_001")
        activation = self.activator.claim(
            registered.entry.request_id,
            registered.entry.session_scope_hash,
            now_ms=1_200,
        )
        heartbeat = self.activator.heartbeat(activation, now_ms=2_000)
        self.assertEqual(heartbeat.request_id, activation.generation.request_id)
        self.assertEqual(heartbeat.lease_id, activation.generation.lease_id)
        self.assertGreater(heartbeat.fence.expires_at_ms, activation.generation.fence.expires_at_ms)
        self.assertEqual(
            self.store._connection.execute("SELECT count(*) FROM outbox").fetchone()[0],  # noqa: SLF001
            0,
        )

    def test_new_gateway_epoch_takes_over_only_an_expired_active_generation(self) -> None:
        registered = self.register("message_001")
        original = self.activator.claim_next(now_ms=1_400)
        replacement = ActiveRequestActivator(
            self.store,
            gateway_epoch=8,
            owner_instance_id="gateway-instance-002",
            lease_duration_ms=10_000,
        )
        self.assertIsNone(replacement.recover_next(now_ms=11_399))
        recovered = replacement.recover_next(now_ms=11_400)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.entry.request_id, registered.entry.request_id)
        self.assertEqual(recovered.generation.generation, original.generation.generation)
        self.assertEqual(recovered.generation.run_id, original.generation.run_id)
        self.assertEqual(recovered.generation.gateway_epoch, 8)
        self.assertEqual(recovered.generation.owner_instance_id, "gateway-instance-002")
        self.assertEqual(
            recovered.generation.fence.supersedes_fence_id,
            original.generation.fence.fence_id,
        )
        self.assertIsNone(replacement.recover_next(now_ms=11_401))
        self.assertTrue(self.store.health_check(now_ms=11_402, full=True).healthy)

    def test_v9_legacy_request_waits_for_exact_duplicate_to_restore_envelope(self) -> None:
        incoming = envelope("message_001")
        registered = self.store.register_request(
            incoming,
            ingress_sha256=HASH_A,
            created_at_ms=1_100,
        )
        self.store.close()
        connection = sqlite3.connect(self.path)
        downgrade_v12_to_v11(connection)
        connection.execute("DROP TABLE outbox_dispatch_boundary")
        connection.execute("DROP TABLE request_inbound_payload")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 10")
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
        connection.close()

        self.store = GatewayStateStore.open(self.path, now_ms=1_200)
        self.activator = ActiveRequestActivator(
            self.store,
            gateway_epoch=7,
            owner_instance_id="gateway-instance-001",
            lease_duration_ms=10_000,
        )
        self.assertIsNone(self.store.get_request_envelope(registered.entry.request_id))
        self.assertEqual(self.store.list_unclaimed_active_requests(), ())
        with self.assertRaises(StoreConflictError):
            self.activator.claim(
                registered.entry.request_id,
                registered.entry.session_scope_hash,
                now_ms=1_300,
            )

        duplicate = self.store.register_request(
            incoming,
            ingress_sha256=HASH_A,
            created_at_ms=1_400,
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(self.store.get_request_envelope(registered.entry.request_id), incoming)
        activation = self.activator.claim(
            registered.entry.request_id,
            registered.entry.session_scope_hash,
            now_ms=1_500,
        )
        self.assertTrue(activation.created_by_this_call)
        self.assertEqual(activation.envelope, incoming)
        self.assertTrue(self.store.health_check(now_ms=1_600, full=True).healthy)


if __name__ == "__main__":
    unittest.main()
