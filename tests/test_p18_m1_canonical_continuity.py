from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from contracts import InboundEnvelope, InboundScope, derive_inbound_scope_keys, derive_run_identity
from total_gateway.continuity import persist_interruption_checkpoint, persist_working_checkpoint
from total_gateway.runtime import _gateway_execution_epoch_checkpoint
from total_gateway.store import GatewayStateStore

HASH_A = "a" * 64
LIFE_ID = "life_p18_m1_continuity"


def inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_p18",
        link_account_id="desktop_p18",
        conversation_ref="conversation_p18",
        channel_message_ref="message_p18",
        sender_ref="sender_p18",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_p18_m1",
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
        text="run the 1000-step durable project",
    )


class CanonicalEpochContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = GatewayStateStore.open(Path(self.temp.name) / "gateway.sqlite3", now_ms=900)
        registration = self.store.register_request(inbound(), ingress_sha256=HASH_A, created_at_ms=1_100)
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=1,
            gateway_epoch=1,
            lease_id="lease_p18_m1",
            owner_instance_id="gateway_p18_m1",
            issued_at_ms=1_200,
            lease_duration_ms=60_000,
        )
        self.pending_effect = "eff_" + "6" * 64
        persist_working_checkpoint(
            self.store,
            life_id=LIFE_ID,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            user_goal="finish the 1000-step durable project",
            hard_constraints=("never change authority identity",),
            active_plan=("execute", "verify", "deliver"),
            pending_effect_ids=(self.pending_effect,),
            latest_safe_step="request authority bound",
            next_step="start execution epoch 0",
            recovery_preconditions=("generation fence remains current",),
            created_at_ms=1_300,
        )
        self.runtime = SimpleNamespace(store=self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def payload(self, *, epoch: int, tools: int, iterations: int | None = None) -> dict[str, object]:
        return {
            "schema": "tiangong.gateway.execution-epoch-checkpoint.v1",
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": 1,
            "life_id": LIFE_ID,
            "outer_execution_ticket_id": "ticket_p18",
            "epoch_index": epoch,
            "epoch_iteration_count": 75,
            "epoch_tool_rounds": 75,
            "global_iteration_count": tools if iterations is None else iterations,
            "global_tool_rounds": tools,
            "requested_tool_rounds": 1,
            "latest_safe_step": f"global_tool_round_{tools}",
            "next_step": f"epoch_{epoch + 1}_tool_round_1",
            "source": "single_tool",
        }

    def test_gateway_checkpoint_preserves_authority_and_existing_recovery_state(self) -> None:
        result = _gateway_execution_epoch_checkpoint(self.runtime, self.payload(epoch=0, tools=75))
        self.assertTrue(result["ok"])
        active = self.store.get_active_request_capsule(self.request_id, run_id=self.run_id, generation=1)
        self.assertIsNotNone(active)
        capsule = active.capsule
        self.assertEqual((capsule.request_id, capsule.run_id, capsule.generation, capsule.life_id),
                         (self.request_id, self.run_id, 1, LIFE_ID))
        self.assertEqual(capsule.pending_effect_ids, (self.pending_effect,))
        self.assertEqual(capsule.latest_safe_step, "global_tool_round_75")
        self.assertEqual(capsule.next_step, "epoch_1_tool_round_1")
        self.assertIn("request/run/generation/life authority identity remains unchanged", capsule.recovery_preconditions)

    def test_wrong_generation_and_life_are_fail_closed_without_superseding_active_capsule(self) -> None:
        before = self.store.get_active_request_capsule(self.request_id, run_id=self.run_id, generation=1).capsule
        bad_generation = self.payload(epoch=0, tools=75)
        bad_generation["generation"] = 2
        self.assertFalse(_gateway_execution_epoch_checkpoint(self.runtime, bad_generation)["ok"])
        bad_life = self.payload(epoch=0, tools=75)
        bad_life["life_id"] = "life_spoofed"
        self.assertFalse(_gateway_execution_epoch_checkpoint(self.runtime, bad_life)["ok"])
        after = self.store.get_active_request_capsule(self.request_id, run_id=self.run_id, generation=1).capsule
        self.assertEqual(after.capsule_id, before.capsule_id)

    def test_interruption_checkpoint_resumes_same_canonical_chain(self) -> None:
        first = _gateway_execution_epoch_checkpoint(self.runtime, self.payload(epoch=1, tools=150))
        self.assertTrue(first["ok"])
        interrupted = persist_interruption_checkpoint(
            self.store,
            request_id=self.request_id,
            run_id=self.run_id,
            generation=1,
            latest_safe_step="global_tool_round_150",
            next_step="epoch_2_tool_round_1",
            created_at_ms=__import__("time").time_ns() // 1_000_000,
            pending_effect_ids=(self.pending_effect,),
            recovery_preconditions=("reconcile pending effect before retry",),
        )
        self.assertIsNotNone(interrupted)
        resumed = _gateway_execution_epoch_checkpoint(self.runtime, self.payload(epoch=2, tools=225))
        self.assertTrue(resumed["ok"])
        active = self.store.get_active_request_capsule(self.request_id, run_id=self.run_id, generation=1).capsule
        self.assertEqual(active.life_id, LIFE_ID)
        self.assertEqual(active.latest_safe_step, "global_tool_round_225")
        self.assertEqual(active.pending_effect_ids, (self.pending_effect,))
        history = self.store.list_request_capsules(self.request_id, run_id=self.run_id, generation=1)
        self.assertGreaterEqual(len(history), 4)

    def test_1000_step_execution_crosses_epochs_without_changing_authority(self) -> None:
        from v3.runtime_turn_orchestration import TurnLoopState

        state = TurnLoopState()
        checkpoint_count = 0
        while state.action_rounds < 1000:
            decision = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
            if decision.should_checkpoint_continue:
                result = _gateway_execution_epoch_checkpoint(
                    self.runtime,
                    self.payload(
                        epoch=state.epoch_index,
                        tools=state.action_rounds,
                        iterations=state.iteration_count,
                    ),
                )
                self.assertTrue(result["ok"])
                checkpoint_count += 1
                state.begin_next_epoch()
                continue
            self.assertTrue(decision.can_schedule)
            state.reserve_one()
            state.bump_iteration()
        terminal = state.decide_schedule(1, max_epoch_rounds=75, max_global_rounds=1000)
        self.assertTrue(terminal.terminal)
        self.assertTrue(terminal.global_exhausted)
        self.assertEqual(state.action_rounds, 1000)
        self.assertEqual(checkpoint_count, 13)
        active = self.store.get_active_request_capsule(self.request_id, run_id=self.run_id, generation=1).capsule
        self.assertEqual((active.request_id, active.run_id, active.generation, active.life_id),
                         (self.request_id, self.run_id, 1, LIFE_ID))
        self.assertEqual(active.latest_safe_step, "global_tool_round_975")
        self.assertEqual(active.next_step, "epoch_13_tool_round_1")
        self.assertEqual(len(self.store.list_request_capsules(self.request_id, run_id=self.run_id, generation=1)), 14)


class BackendCanonicalProviderTests(unittest.TestCase):
    def test_gateway_authorized_epoch_requires_canonical_provider_and_binds_result(self) -> None:
        from v3.run_context import RunContext, bind_run_context
        from v3.runtime_turn_orchestration import TurnLoopState
        from v3.zongdiaodu import (
            _simple_chain_checkpoint_continue,
            set_simple_chain_continuity_checkpoint_provider,
            set_simple_chain_regenerative_execution_provider,
        )

        with tempfile.TemporaryDirectory() as temporary:
            previous = os.environ.get("TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT")
            os.environ["TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT"] = temporary
            seen: list[dict[str, object]] = []
            try:
                context = RunContext(
                    request_id="req_" + "1" * 64,
                    run_id="run_" + "2" * 64,
                    generation=3,
                    life_id="life_bound",
                    session_id="session_bound",
                    outer_execution_ticket_id="ticket_bound",
                )
                state = TurnLoopState(action_rounds=75, iteration_count=80, epoch_action_rounds=75, epoch_iteration_count=80)
                run_state: dict[str, object] = {
                    "run_id": context.run_id,
                    "request_id": context.request_id,
                    "session_id": context.session_id,
                    "version": 0,
                    "budget": {},
                }

                def provider(payload: dict[str, object]) -> dict[str, object]:
                    seen.append(dict(payload))
                    return {
                        "ok": True,
                        "request_id": context.request_id,
                        "run_id": context.run_id,
                        "generation": context.generation,
                        "life_id": context.life_id,
                        "capsule_id": "lcp_" + "9" * 64,
                        "duplicate": False,
                    }

                regenerative_seen: list[dict[str, object]] = []

                def regenerative_provider(payload: dict[str, object]) -> dict[str, object]:
                    regenerative_seen.append(dict(payload))
                    operation = str(payload.get("operation") or "")
                    response: dict[str, object] = {
                        "schema": "tiangong.gateway.regenerative-provider.v1",
                        "operation": operation,
                    }
                    if operation == "update_frontier":
                        frontier = payload.get("frontier") if isinstance(payload.get("frontier"), dict) else {}
                        response.update({
                            "committed": True,
                            "frontier_version": int(frontier.get("frontier_version") or 1),
                            "frontier_hash": str(frontier.get("frontier_hash") or ("8" * 64)),
                        })
                    elif operation == "commit_checkpoint":
                        frontier = payload.get("frontier") if isinstance(payload.get("frontier"), dict) else {}
                        response.update({
                            "committed": True,
                            "checkpoint_id": "rgc_" + "7" * 64,
                            "checkpoint_hash": "6" * 64,
                            "frontier_hash": str(frontier.get("frontier_hash") or ("8" * 64)),
                        })
                    else:
                        raise AssertionError(f"unexpected M2 operation: {operation}")
                    return response

                set_simple_chain_continuity_checkpoint_provider(provider)
                set_simple_chain_regenerative_execution_provider(regenerative_provider)
                with bind_run_context(context):
                    self.assertTrue(_simple_chain_checkpoint_continue(
                        run_state, state, requested=1, loop_started_at=1.0, source="single_tool"
                    ))
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0]["request_id"], context.request_id)
                self.assertEqual(seen[0]["generation"], 3)
                continuation = run_state["continuation"]
                self.assertEqual(continuation["canonical_capsule_id"], "lcp_" + "9" * 64)
                self.assertEqual(continuation["status"], "continued")
                self.assertEqual([item["operation"] for item in regenerative_seen], ["update_frontier", "commit_checkpoint"])
            finally:
                set_simple_chain_regenerative_execution_provider(None)
                set_simple_chain_continuity_checkpoint_provider(None)
                if previous is None:
                    os.environ.pop("TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT", None)
                else:
                    os.environ["TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT"] = previous

    def test_gateway_authorized_epoch_fails_closed_without_provider(self) -> None:
        from v3.run_context import RunContext, bind_run_context
        from v3.runtime_turn_orchestration import TurnLoopState
        from v3.zongdiaodu import _simple_chain_checkpoint_continue, set_simple_chain_continuity_checkpoint_provider

        with tempfile.TemporaryDirectory() as temporary:
            previous = os.environ.get("TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT")
            os.environ["TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT"] = temporary
            try:
                set_simple_chain_continuity_checkpoint_provider(None)
                context = RunContext(
                    request_id="req_" + "3" * 64,
                    run_id="run_" + "4" * 64,
                    generation=1,
                    life_id="life_bound",
                    outer_execution_ticket_id="ticket_bound",
                )
                state = TurnLoopState(action_rounds=75, epoch_action_rounds=75)
                run_state: dict[str, object] = {
                    "run_id": context.run_id,
                    "request_id": context.request_id,
                    "version": 0,
                    "budget": {},
                }
                with bind_run_context(context):
                    self.assertFalse(_simple_chain_checkpoint_continue(
                        run_state, state, requested=1, loop_started_at=1.0, source="single_tool"
                    ))
                self.assertEqual(run_state["continuation"]["status"], "canonical_checkpoint_unavailable")
            finally:
                set_simple_chain_continuity_checkpoint_provider(None)
                if previous is None:
                    os.environ.pop("TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT", None)
                else:
                    os.environ["TIANGONG_SIMPLE_CHAIN_RUN_STATE_ROOT"] = previous


if __name__ == "__main__":
    unittest.main()
