from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contracts import InboundEnvelope, InboundScope, canonical_sha256, derive_inbound_scope_keys, derive_run_identity
from total_gateway.continuity import persist_working_checkpoint
from total_gateway.regenerative_execution import ExecutionFrontier, ZERO_HASH
from total_gateway.store import GatewayStateStore, StoreCasConflict, StoreConflictError


HASH_A = "a" * 64


def inbound(tag: str = "m2") -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id=f"tenant_{tag}",
        link_account_id=f"desktop_{tag}",
        conversation_ref=f"conversation_{tag}",
        channel_message_ref=f"message_{tag}",
        sender_ref=f"sender_{tag}",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id=f"inbound_{tag}",
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
        text="execute a long deterministic task",
    )


class RegenerativeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        registration = self.store.register_request(inbound(), ingress_sha256=HASH_A, created_at_ms=1_100)
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.generation = 1
        self.life_id = "life_p18_m2"
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=self.generation,
            gateway_epoch=1,
            lease_id="lease_p18_m2",
            owner_instance_id="gateway_p18_m2",
            issued_at_ms=1_200,
            lease_duration_ms=500_000,
        )
        self.root_hash = canonical_sha256({"goal": "300 deterministic steps"})
        self.task_hash = canonical_sha256({"task": "immutable contract"})
        self.authority_hash = canonical_sha256({"authority": "gateway ticket"})
        self.store.bind_execution_task_contract(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            life_id=self.life_id,
            root_goal_hash=self.root_hash,
            task_contract_hash=self.task_hash,
            authority_hash=self.authority_hash,
            bound_at_ms=1_300,
        )
        record = persist_working_checkpoint(
            self.store,
            life_id=self.life_id,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            user_goal="finish 300 deterministic steps",
            hard_constraints=("do not duplicate effects",),
            active_plan=("continue from durable frontier",),
            latest_safe_step="request authority bound",
            next_step="execute next verified step",
            recovery_preconditions=("generation remains current",),
            created_at_ms=1_400,
        )
        self.capsule_id = record.capsule.capsule_id

    def tearDown(self) -> None:
        try:
            self.store.close()
        finally:
            self.temp.cleanup()

    def frontier(self, version: int, *, global_step: int, epoch_index: int = 0, epoch_step: int = 0) -> ExecutionFrontier:
        return ExecutionFrontier(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            life_id=self.life_id,
            root_goal_hash=self.root_hash,
            task_contract_hash=self.task_hash,
            authority_hash=self.authority_hash,
            global_step=global_step,
            epoch_index=epoch_index,
            epoch_step=epoch_step,
            completed_obligation_ids=tuple(sorted(f"ob_{idx}" for idx in range(min(global_step, 8)))),
            active_obligation_id=None,
            pending_obligation_ids=(),
            verified_fact_head=None,
            artifact_revision_head=None,
            pending_effect_ids=(),
            ambiguous_effect_ids=(),
            active_blockers=(),
            failed_strategy_ids=(),
            latest_safe_step=f"verified step {global_step}",
            next_action_hint=f"execute step {global_step + 1}",
            provider_turn_state_ref=None,
            frontier_version=version,
            frontier_hash=ZERO_HASH,
        ).with_computed_hash()

    def commit_checkpoint(self, frontier: ExecutionFrontier, *, now_ms: int):
        return self.store.commit_regenerative_checkpoint(
            frontier,
            continuity_capsule_id=self.capsule_id,
            recovery_preconditions=("generation remains current", "reconcile ambiguous effects first"),
            runtime_version="p18-m2-test",
            provider_version="deterministic",
            model_version="fixture",
            tool_contract_version="omni.v1",
            skill_contract_version="skill.v1",
            task_contract_version="task.v1",
            semantic_handoff="bounded handoff",
            created_at_ms=now_ms,
        )

    def test_task_contract_is_immutable_inside_generation(self) -> None:
        self.assertFalse(self.store.bind_execution_task_contract(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            life_id=self.life_id,
            root_goal_hash=self.root_hash,
            task_contract_hash=self.task_hash,
            authority_hash=self.authority_hash,
            bound_at_ms=1_500,
        ))
        with self.assertRaises(StoreConflictError):
            self.store.bind_execution_task_contract(
                request_id=self.request_id,
                run_id=self.run_id,
                generation=self.generation,
                life_id=self.life_id,
                root_goal_hash=self.root_hash,
                task_contract_hash="b" * 64,
                authority_hash=self.authority_hash,
                bound_at_ms=1_600,
            )

    def test_300_step_ledger_is_monotonic_hash_chained_and_multi_epoch(self) -> None:
        epoch_count = 6
        for index in range(300):
            epoch = index // 50
            event, created = self.store.append_execution_event(
                event_key=f"step-{index}",
                request_id=self.request_id,
                run_id=self.run_id,
                generation=self.generation,
                epoch_index=epoch,
                event_type="step.verified",
                payload={"step": index + 1, "epoch": epoch},
                created_at_ms=2_000 + index,
                step_id=None,
            )
            self.assertTrue(created)
            self.assertEqual(event.ledger_seq, index + 1)
            self.assertTrue(event.has_valid_hash())
        audit = self.store.audit_execution_ledger(
            self.request_id, run_id=self.run_id, generation=self.generation
        )
        self.assertTrue(audit["healthy"])
        self.assertEqual(audit["event_count"], 300)
        self.assertEqual(audit["last_valid_seq"], 300)
        events = self.store.list_execution_events(
            self.request_id, run_id=self.run_id, generation=self.generation
        )
        self.assertEqual(len({event.ledger_seq for event in events}), 300)
        self.assertGreaterEqual(len({event.epoch_index for event in events}), epoch_count)
        duplicate, created = self.store.append_execution_event(
            event_key="step-299",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            epoch_index=5,
            event_type="step.verified",
            payload={"step": 300, "epoch": 5},
            created_at_ms=9_999,
        )
        self.assertFalse(created)
        self.assertEqual(duplicate.ledger_seq, 300)

    def test_frontier_cas_rejects_silent_revision_overwrite(self) -> None:
        first = self.frontier(1, global_step=10)
        self.assertEqual(self.store.commit_execution_frontier(first, expected_revision=0, updated_at_ms=2_000), 1)
        second = self.frontier(2, global_step=11)
        self.assertEqual(self.store.commit_execution_frontier(second, expected_revision=1, updated_at_ms=2_100), 2)
        stale = self.frontier(2, global_step=12)
        with self.assertRaises(StoreCasConflict):
            self.store.commit_execution_frontier(stale, expected_revision=1, updated_at_ms=2_200)
        self.assertEqual(self.store.get_execution_frontier(
            self.request_id, run_id=self.run_id, generation=self.generation
        ), second)

    def test_checkpoint_has_checksum_and_falls_back_to_previous_known_good(self) -> None:
        first = self.frontier(1, global_step=20, epoch_index=1)
        self.store.commit_execution_frontier(first, expected_revision=0, updated_at_ms=2_000)
        self.store.append_execution_event(
            event_key="frontier-1",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            epoch_index=1,
            event_type="frontier.updated",
            payload={"frontier": first.model_dump(mode="json")},
            created_at_ms=2_010,
        )
        checkpoint1 = self.commit_checkpoint(first, now_ms=2_100)
        self.assertTrue(checkpoint1.has_valid_hashes())
        second = self.frontier(2, global_step=40, epoch_index=2)
        self.store.commit_execution_frontier(second, expected_revision=1, updated_at_ms=2_200)
        self.store.append_execution_event(
            event_key="frontier-2",
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            epoch_index=2,
            event_type="frontier.updated",
            payload={"frontier": second.model_dump(mode="json")},
            created_at_ms=2_210,
        )
        checkpoint2 = self.commit_checkpoint(second, now_ms=2_300)
        self.assertTrue(checkpoint2.has_valid_hashes())
        self.assertEqual(checkpoint2.previous_checkpoint_hash, checkpoint1.checkpoint_hash)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE regenerative_checkpoint SET checkpoint_json='{}' WHERE checkpoint_id=?",
                (checkpoint2.checkpoint_id,),
            )
            connection.commit()
        loaded, used_previous = self.store.load_regenerative_checkpoint(
            self.request_id, run_id=self.run_id, generation=self.generation
        )
        self.assertTrue(used_previous)
        self.assertEqual(loaded, checkpoint1)

    def test_torn_ledger_tail_is_detected_and_truncated_after_checkpoint(self) -> None:
        frontier = self.frontier(1, global_step=25, epoch_index=1)
        self.store.commit_execution_frontier(frontier, expected_revision=0, updated_at_ms=2_000)
        for index in range(3):
            self.store.append_execution_event(
                event_key=f"safe-{index}", request_id=self.request_id, run_id=self.run_id,
                generation=self.generation, epoch_index=1, event_type="step.verified",
                payload={"safe": index}, created_at_ms=2_010 + index,
            )
        checkpoint = self.commit_checkpoint(frontier, now_ms=2_100)
        bad, _ = self.store.append_execution_event(
            event_key="torn-tail", request_id=self.request_id, run_id=self.run_id,
            generation=self.generation, epoch_index=1, event_type="step.observed",
            payload={"tail": "candidate"}, created_at_ms=2_200,
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE execution_ledger SET event_json='{}' WHERE event_id=?",
                (bad.event_id,),
            )
            connection.commit()
        audit = self.store.audit_execution_ledger(
            self.request_id, run_id=self.run_id, generation=self.generation
        )
        self.assertFalse(audit["healthy"])
        recovered = self.store.recover_execution_ledger_tail(
            self.request_id, run_id=self.run_id, generation=self.generation,
            known_good_seq=checkpoint.ledger_head_seq, recovered_at_ms=2_300,
        )
        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["truncated"], 1)
        self.assertTrue(self.store.audit_execution_ledger(
            self.request_id, run_id=self.run_id, generation=self.generation
        )["healthy"])

    def test_restart_rehydrates_from_checkpoint_and_replays_frontier_tail(self) -> None:
        first = self.frontier(1, global_step=50, epoch_index=1)
        self.store.commit_execution_frontier(first, expected_revision=0, updated_at_ms=2_000)
        self.store.append_execution_event(
            event_key="frontier-checkpoint", request_id=self.request_id, run_id=self.run_id,
            generation=self.generation, epoch_index=1, event_type="frontier.updated",
            payload={"frontier": first.model_dump(mode="json")}, created_at_ms=2_010,
        )
        checkpoint = self.commit_checkpoint(first, now_ms=2_100)
        second = self.frontier(2, global_step=75, epoch_index=2)
        self.store.commit_execution_frontier(second, expected_revision=1, updated_at_ms=2_200)
        self.store.append_execution_event(
            event_key="frontier-after-checkpoint", request_id=self.request_id, run_id=self.run_id,
            generation=self.generation, epoch_index=2, event_type="frontier.updated",
            payload={"frontier": second.model_dump(mode="json")}, created_at_ms=2_210,
        )
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=2_300)
        recovered = self.store.recover_regenerative_execution(
            self.request_id, run_id=self.run_id, generation=self.generation,
            recovered_at_ms=2_400,
        )
        self.assertTrue(recovered["recoverable"])
        self.assertEqual(recovered["checkpoint"].checkpoint_id, checkpoint.checkpoint_id)
        self.assertEqual(recovered["frontier"], second)
        self.assertEqual(recovered["frontier"].request_id, self.request_id)
        self.assertEqual(recovered["frontier"].run_id, self.run_id)
        self.assertEqual(recovered["frontier"].generation, self.generation)

    def test_frontier_snapshot_is_bounded_independent_of_ledger_length(self) -> None:
        frontier = self.frontier(1, global_step=0)
        self.store.commit_execution_frontier(frontier, expected_revision=0, updated_at_ms=2_000)
        for index in range(300):
            self.store.append_execution_event(
                event_key=f"bounded-{index}", request_id=self.request_id, run_id=self.run_id,
                generation=self.generation, epoch_index=index // 50, event_type="step.observed",
                payload={"index": index, "blob": "x" * 128}, created_at_ms=2_100 + index,
            )
        encoded = json.dumps(
            self.store.get_execution_frontier(
                self.request_id, run_id=self.run_id, generation=self.generation
            ).model_dump(mode="json"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertLess(len(encoded), 32 * 1024)
        self.assertEqual(len(self.store.list_execution_events(
            self.request_id, run_id=self.run_id, generation=self.generation
        )), 300)

    def test_two_store_connections_allocate_unique_monotonic_sequences(self) -> None:
        other = GatewayStateStore.open(self.path, now_ms=1_500)
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def writer(store: GatewayStateStore, prefix: str) -> None:
            try:
                barrier.wait(timeout=5)
                for index in range(20):
                    store.append_execution_event(
                        event_key=f"{prefix}-{index}", request_id=self.request_id,
                        run_id=self.run_id, generation=self.generation,
                        epoch_index=0, event_type="step.observed",
                        payload={"writer": prefix, "index": index},
                        created_at_ms=3_000 + index,
                    )
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(self.store, "a")),
            threading.Thread(target=writer, args=(other, "b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        other.close()
        self.assertFalse(errors, errors)
        events = self.store.list_execution_events(
            self.request_id, run_id=self.run_id, generation=self.generation
        )
        self.assertEqual(tuple(event.ledger_seq for event in events), tuple(range(1, 41)))
        self.assertTrue(self.store.audit_execution_ledger(
            self.request_id, run_id=self.run_id, generation=self.generation
        )["healthy"])


if __name__ == "__main__":
    unittest.main()
