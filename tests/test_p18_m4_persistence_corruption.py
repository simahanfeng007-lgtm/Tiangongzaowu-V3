from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest

from contracts import (
    InboundEnvelope,
    InboundScope,
    canonical_sha256,
    derive_inbound_scope_keys,
    derive_run_identity,
)
from total_gateway.continuity import persist_working_checkpoint
from total_gateway.regenerative_execution import ExecutionFrontier, ZERO_HASH
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority, authority_hash
from total_gateway.store import GatewayStateStore, StoreCorruptionError


HASH_A = "a" * 64


def _inbound(tag: str) -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id=f"tenant_{tag}",
        link_account_id=f"link_{tag}",
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
        received_at_ms=1000,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_A,
        text="certify corruption recovery",
    )


class CorruptionRig:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=900)
        registration = self.store.register_request(
            _inbound("m4_corruption"), ingress_sha256=HASH_A, created_at_ms=1100
        )
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.generation = 1
        self.life_id = "life_p18_m4_corruption"
        self.ticket = "ticket_p18_m4_corruption"
        self.root_hash = canonical_sha256({"goal": "corruption certification"})
        self.task_hash = canonical_sha256({"task": "preserve verified reality"})
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=self.generation,
            gateway_epoch=1,
            lease_id="lease_p18_m4_corruption",
            owner_instance_id="gateway_p18_m4_corruption",
            issued_at_ms=1200,
            lease_duration_ms=500_000,
        )
        self.provider = RegenerativeExecutionAuthority(self.store)
        initialized = self.provider(
            self.payload(
                "initialize",
                now_ms=1300,
                root_goal_hash=self.root_hash,
                task_contract_hash=self.task_hash,
                epoch_index=0,
            )
        )
        assert initialized["initialized"] is True

    def payload(self, operation: str, **extra: object) -> dict[str, object]:
        return {
            "operation": operation,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "life_id": self.life_id,
            "outer_execution_ticket_id": self.ticket,
            **extra,
        }

    def frontier(self, *, version: int, global_step: int) -> ExecutionFrontier:
        return ExecutionFrontier(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            life_id=self.life_id,
            root_goal_hash=self.root_hash,
            task_contract_hash=self.task_hash,
            authority_hash=authority_hash(self.ticket),
            global_step=global_step,
            epoch_index=global_step // 75,
            epoch_step=global_step % 75,
            completed_obligation_ids=(),
            active_obligation_id=None,
            pending_obligation_ids=(),
            verified_fact_head=None,
            artifact_revision_head=None,
            pending_effect_ids=(),
            ambiguous_effect_ids=(),
            active_blockers=(),
            failed_strategy_ids=(),
            latest_safe_step=f"step {global_step}",
            next_action_hint="continue",
            provider_turn_state_ref=None,
            frontier_version=version,
            frontier_hash=ZERO_HASH,
        ).with_computed_hash()

    def append_event(self, key: str, *, now_ms: int) -> int:
        event, created = self.store.append_execution_event(
            event_key=key,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            epoch_index=0,
            event_type="m4.certification",
            payload={"key": key},
            created_at_ms=now_ms,
        )
        assert created is True
        return event.ledger_seq

    def commit_checkpoint(self, *, version: int, global_step: int, now_ms: int):
        frontier = self.frontier(version=version, global_step=global_step)
        self.store.commit_execution_frontier(
            frontier,
            expected_revision=version - 1,
            updated_at_ms=now_ms,
        )
        continuity = persist_working_checkpoint(
            self.store,
            life_id=self.life_id,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            user_goal="corruption certification",
            hard_constraints=(),
            active_plan=(),
            latest_safe_step=f"step {global_step}",
            next_step="continue after validated recovery",
            recovery_preconditions=("ledger and checkpoint integrity valid",),
            created_at_ms=now_ms + 1,
        )
        result = self.provider(
            self.payload(
                "commit_checkpoint",
                now_ms=now_ms + 2,
                frontier=frontier.model_dump(mode="json"),
                continuity_capsule_id=continuity.capsule.capsule_id,
                recovery_preconditions=["ledger and checkpoint integrity valid"],
                critical_fact_status="verified",
                runtime_version="tiangong-v3-p18-m4",
                provider_version="gateway-regenerative-provider-v1",
                model_version="deterministic-cert-model",
                tool_contract_version="omni_body.v1",
                skill_contract_version="skill.v1",
                task_contract_version="task.v1",
            )
        )
        assert result["committed"] is True
        checkpoint, _ = self.store.load_regenerative_checkpoint(
            self.request_id, run_id=self.run_id, generation=self.generation
        )
        assert checkpoint is not None
        return checkpoint

    def close(self) -> None:
        try:
            self.store.close()
        finally:
            self.temp.cleanup()

    def close_store_only(self) -> None:
        self.store.close()

    def reopen(self, *, now_ms: int) -> None:
        self.store = GatewayStateStore.open(self.path, now_ms=now_ms)
        self.provider = RegenerativeExecutionAuthority(self.store)

    def corrupt(self, sql: str, params: tuple[object, ...] = ()) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()


def test_m4_torn_ledger_tail_is_detected_and_truncated_only_after_known_good_checkpoint() -> None:
    rig = CorruptionRig()
    try:
        rig.append_event("m4.before-checkpoint", now_ms=1400)
        checkpoint = rig.commit_checkpoint(version=1, global_step=5, now_ms=1500)
        tail_seq = rig.append_event("m4.torn-tail", now_ms=1600)
        assert tail_seq > checkpoint.ledger_head_seq
        rig.close_store_only()

        rig.corrupt(
            "UPDATE execution_ledger SET event_json=? WHERE request_id=? AND run_id=? AND generation=? AND ledger_seq=?",
            ("{\"broken\":true}", rig.request_id, rig.run_id, rig.generation, tail_seq),
        )
        rig.reopen(now_ms=1700)
        audit = rig.store.audit_execution_ledger(
            rig.request_id, run_id=rig.run_id, generation=rig.generation
        )
        assert audit["healthy"] is False
        assert audit["first_invalid_seq"] == tail_seq

        recovered = rig.store.recover_execution_ledger_tail(
            rig.request_id,
            run_id=rig.run_id,
            generation=rig.generation,
            known_good_seq=checkpoint.ledger_head_seq,
            recovered_at_ms=1800,
        )
        assert recovered["healthy"] is True
        assert recovered["recovered"] is True
        assert recovered["truncated"] == 1
        assert recovered["first_invalid_seq"] == tail_seq
    finally:
        rig.close()


def test_m4_corruption_at_or_before_checkpoint_anchor_is_fatal_not_silently_truncated() -> None:
    rig = CorruptionRig()
    try:
        rig.append_event("m4.anchor-event", now_ms=1400)
        checkpoint = rig.commit_checkpoint(version=1, global_step=5, now_ms=1500)
        assert checkpoint.ledger_head_seq >= 1
        rig.close_store_only()
        rig.corrupt(
            "UPDATE execution_ledger SET event_json=? WHERE request_id=? AND run_id=? AND generation=? AND ledger_seq=1",
            ("{\"broken\":true}", rig.request_id, rig.run_id, rig.generation),
        )
        rig.reopen(now_ms=1700)
        with pytest.raises(StoreCorruptionError, match="predates the known-good checkpoint"):
            rig.store.recover_execution_ledger_tail(
                rig.request_id,
                run_id=rig.run_id,
                generation=rig.generation,
                known_good_seq=checkpoint.ledger_head_seq,
                recovered_at_ms=1800,
            )
    finally:
        rig.close()


def test_m4_corrupt_current_checkpoint_falls_back_to_previous_known_good() -> None:
    rig = CorruptionRig()
    try:
        rig.append_event("m4.cp1-event", now_ms=1400)
        first = rig.commit_checkpoint(version=1, global_step=5, now_ms=1500)
        rig.append_event("m4.cp2-event", now_ms=1600)
        second = rig.commit_checkpoint(version=2, global_step=10, now_ms=1700)
        assert second.checkpoint_seq == first.checkpoint_seq + 1
        rig.close_store_only()

        rig.corrupt(
            "UPDATE regenerative_checkpoint SET checkpoint_json=? WHERE checkpoint_id=?",
            ("{\"broken\":true}", second.checkpoint_id),
        )
        rig.reopen(now_ms=1800)
        loaded, used_previous = rig.store.load_regenerative_checkpoint(
            rig.request_id, run_id=rig.run_id, generation=rig.generation
        )
        assert used_previous is True
        assert loaded is not None
        assert loaded.checkpoint_id == first.checkpoint_id
        assert loaded.has_valid_hashes()
    finally:
        rig.close()


def test_m4_both_current_and_previous_checkpoint_corruption_fail_closed() -> None:
    rig = CorruptionRig()
    try:
        rig.append_event("m4.cp1-event", now_ms=1400)
        first = rig.commit_checkpoint(version=1, global_step=5, now_ms=1500)
        rig.append_event("m4.cp2-event", now_ms=1600)
        second = rig.commit_checkpoint(version=2, global_step=10, now_ms=1700)
        rig.close_store_only()

        rig.corrupt(
            "UPDATE regenerative_checkpoint SET checkpoint_json=? WHERE checkpoint_id IN (?,?)",
            ("{\"broken\":true}", first.checkpoint_id, second.checkpoint_id),
        )
        rig.reopen(now_ms=1800)
        with pytest.raises(
            StoreCorruptionError,
            match="current and previous regenerative checkpoints are invalid",
        ):
            rig.store.load_regenerative_checkpoint(
                rig.request_id, run_id=rig.run_id, generation=rig.generation
            )
    finally:
        rig.close()


def test_m4_concurrent_ledger_writers_preserve_unique_monotonic_sequence() -> None:
    rig = CorruptionRig()
    workers: list[GatewayStateStore] = []
    try:
        baseline = rig.store.audit_execution_ledger(
            rig.request_id, run_id=rig.run_id, generation=rig.generation
        )
        assert baseline["healthy"] is True
        baseline_count = baseline["event_count"]
        rig.close_store_only()

        barrier = threading.Barrier(2)
        failures: list[BaseException] = []
        sequences: list[int] = []
        lock = threading.Lock()

        def append_from_writer(writer_no: int) -> None:
            store: GatewayStateStore | None = None
            try:
                store = GatewayStateStore.open(rig.path, now_ms=2000 + writer_no)
                with lock:
                    workers.append(store)
                barrier.wait(timeout=10)
                event, created = store.append_execution_event(
                    event_key=f"m4.concurrent-writer-{writer_no}",
                    request_id=rig.request_id,
                    run_id=rig.run_id,
                    generation=rig.generation,
                    epoch_index=0,
                    event_type="m4.concurrent-certification",
                    payload={"writer": writer_no},
                    created_at_ms=2100 + writer_no,
                )
                assert created is True
                with lock:
                    sequences.append(event.ledger_seq)
            except BaseException as exc:  # make thread failures visible to the test
                with lock:
                    failures.append(exc)
            finally:
                if store is not None:
                    store.close()

        threads = [
            threading.Thread(target=append_from_writer, args=(1,)),
            threading.Thread(target=append_from_writer, args=(2,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert all(not thread.is_alive() for thread in threads)
        assert failures == []
        assert len(sequences) == 2
        assert len(set(sequences)) == 2

        rig.reopen(now_ms=2300)
        audit = rig.store.audit_execution_ledger(
            rig.request_id, run_id=rig.run_id, generation=rig.generation
        )
        assert audit["healthy"] is True
        assert audit["event_count"] == baseline_count + 2
        events = rig.store.list_execution_events(
            rig.request_id, run_id=rig.run_id, generation=rig.generation
        )
        assert [event.ledger_seq for event in events] == list(range(1, len(events) + 1))
    finally:
        # worker stores are normally closed in their own threads; this protects failure paths.
        for store in workers:
            try:
                store.close()
            except Exception:
                pass
        rig.close()
