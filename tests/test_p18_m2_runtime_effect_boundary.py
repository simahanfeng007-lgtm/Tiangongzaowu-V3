from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from contracts import (
    InboundEnvelope,
    InboundScope,
    canonical_sha256,
    derive_inbound_scope_keys,
    derive_run_identity,
)
from total_gateway.regenerative_execution import derive_logical_effect_id
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority
from total_gateway.store import GatewayStateStore


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from v3.run_context import RunContext, bind_run_context  # noqa: E402
from v3.runtime_turn_orchestration import TurnLoopState  # noqa: E402
from v3.zongdiaodu import (  # noqa: E402
    _simple_chain_regenerative_execute_tool,
    set_simple_chain_regenerative_execution_provider,
)


class _Owner:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.calls = 0

    def _jineng_zhixing(self, tool_name, tool_args, user_message, *, call_id):
        self.calls += 1
        self.trace.append("handler")
        return {"ok": True, "status": "success", "tool": tool_name, "call_id": call_id}


def _runtime_state(context: RunContext) -> dict:
    return {
        "request_id": context.request_id,
        "run_id": context.run_id,
        "obligations": [],
        "regenerative": {
            "root_goal_hash": "a" * 64,
            "task_contract_hash": "b" * 64,
            "authority_hash": "c" * 64,
            "frontier_version": 0,
            "frontier_hash": "",
            "pending_effect_ids": [],
            "ambiguous_effect_ids": [],
            "active_effects": {},
        },
    }


class RuntimeEffectBoundaryTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_simple_chain_regenerative_execution_provider(None)

    def context(self) -> RunContext:
        return RunContext(
            request_id="req_" + "1" * 64,
            run_id="run_" + "2" * 64,
            generation=3,
            life_id="life_runtime_effect_boundary",
            session_id="session_runtime_effect_boundary",
            outer_execution_ticket_id="ticket_runtime_effect_boundary",
        )

    def test_real_wrapper_prepares_and_starts_before_physical_handler(self) -> None:
        trace: list[str] = []
        owner = _Owner(trace)
        context = self.context()
        state = _runtime_state(context)
        loop = TurnLoopState(action_rounds=1, epoch_action_rounds=1)

        def provider(payload: dict) -> dict:
            operation = str(payload["operation"])
            trace.append(operation)
            response = {"schema": "tiangong.gateway.regenerative-provider.v1", "operation": operation}
            if operation == "prepare_effect":
                response.update({
                    "disposition": "prepared",
                    "effect_id": "eff_" + "3" * 64,
                    "logical_effect_id": str(payload["logical_effect_id"]),
                    "attempt_id": "att_" + "4" * 64,
                    "step_id": "stp_" + "5" * 64,
                    "effect_state": "CLAIMED",
                })
            elif operation == "start_effect":
                response.update({"dispatch_permitted": True, "disposition": "dispatched", "effect_state": "SIDE_EFFECT_STARTED"})
            elif operation == "finish_effect":
                response.update({"effect_state": "SUCCEEDED", "result_sha256": "6" * 64})
            elif operation == "update_frontier":
                frontier = payload["frontier"]
                response.update({
                    "committed": True,
                    "frontier_version": frontier["frontier_version"],
                    "frontier_hash": frontier["frontier_hash"],
                })
            return response

        set_simple_chain_regenerative_execution_provider(provider)
        with bind_run_context(context):
            result = _simple_chain_regenerative_execute_tool(
                owner,
                state,
                loop,
                tool_name="omni_body",
                tool_args={"action": "file.read", "path": "C:/tmp/input.txt"},
                user_message="read the file",
                call_id="call_runtime_order",
                global_step=2,
                attempted_action="file.read",
                update_frontier=True,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(owner.calls, 1)
        self.assertLess(trace.index("prepare_effect"), trace.index("start_effect"))
        self.assertLess(trace.index("start_effect"), trace.index("handler"))
        self.assertLess(trace.index("handler"), trace.index("finish_effect"))
        self.assertEqual(state["regenerative"]["pending_effect_ids"], [])
        self.assertEqual(state["regenerative"]["ambiguous_effect_ids"], [])

    def test_real_wrapper_never_dispatches_already_committed_logical_effect(self) -> None:
        trace: list[str] = []
        owner = _Owner(trace)
        context = self.context()
        state = _runtime_state(context)
        loop = TurnLoopState(action_rounds=5, epoch_action_rounds=5)

        def provider(payload: dict) -> dict:
            operation = str(payload["operation"])
            trace.append(operation)
            response = {"schema": "tiangong.gateway.regenerative-provider.v1", "operation": operation}
            if operation == "prepare_effect":
                response.update({
                    "disposition": "already_committed",
                    "effect_id": "eff_" + "3" * 64,
                    "logical_effect_id": str(payload["logical_effect_id"]),
                    "attempt_id": "att_" + "4" * 64,
                    "step_id": "stp_" + "5" * 64,
                    "effect_state": "LOGICAL_COMMITTED",
                    "prior_result_summary": {"ok": True},
                })
            elif operation == "update_frontier":
                frontier = payload["frontier"]
                response.update({
                    "committed": True,
                    "frontier_version": frontier["frontier_version"],
                    "frontier_hash": frontier["frontier_hash"],
                })
            return response

        set_simple_chain_regenerative_execution_provider(provider)
        with bind_run_context(context):
            result = _simple_chain_regenerative_execute_tool(
                owner,
                state,
                loop,
                tool_name="omni_body",
                tool_args={"action": "file.write", "path": "C:/tmp/output.txt", "content": "x"},
                user_message="write once",
                call_id="call_dedup",
                global_step=6,
                attempted_action="file.write",
                update_frontier=True,
            )
        self.assertTrue(result["deduplicated"])
        self.assertEqual(owner.calls, 0)
        self.assertNotIn("start_effect", trace)
        self.assertNotIn("finish_effect", trace)

    def test_real_wrapper_blocks_in_flight_effect_without_marking_it_ambiguous(self) -> None:
        trace: list[str] = []
        owner = _Owner(trace)
        context = self.context()
        state = _runtime_state(context)
        loop = TurnLoopState(action_rounds=9, epoch_action_rounds=9)
        effect_id = "eff_" + "7" * 64

        def provider(payload: dict) -> dict:
            operation = str(payload["operation"])
            trace.append(operation)
            response = {"schema": "tiangong.gateway.regenerative-provider.v1", "operation": operation}
            if operation == "prepare_effect":
                response.update({
                    "disposition": "in_flight",
                    "effect_id": effect_id,
                    "logical_effect_id": str(payload["logical_effect_id"]),
                    "attempt_id": "att_" + "8" * 64,
                    "step_id": "stp_" + "9" * 64,
                    "effect_state": "SIDE_EFFECT_STARTED",
                })
            elif operation == "update_frontier":
                frontier = payload["frontier"]
                self.assertIn(effect_id, frontier["pending_effect_ids"])
                self.assertNotIn(effect_id, frontier["ambiguous_effect_ids"])
                response.update({
                    "committed": True,
                    "frontier_version": frontier["frontier_version"],
                    "frontier_hash": frontier["frontier_hash"],
                })
            return response

        set_simple_chain_regenerative_execution_provider(provider)
        with bind_run_context(context):
            result = _simple_chain_regenerative_execute_tool(
                owner,
                state,
                loop,
                tool_name="omni_body",
                tool_args={"action": "file.write", "path": "C:/tmp/output.txt", "content": "x"},
                user_message="do not duplicate",
                call_id="call_in_flight",
                global_step=10,
                attempted_action="file.write",
                update_frontier=True,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "in_flight")
        self.assertEqual(owner.calls, 0)
        self.assertNotIn("start_effect", trace)
        self.assertIn(effect_id, state["regenerative"]["pending_effect_ids"])
        self.assertNotIn(effect_id, state["regenerative"]["ambiguous_effect_ids"])


def _inbound() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_timeout",
        link_account_id="link_timeout",
        conversation_ref="conversation_timeout",
        channel_message_ref="message_timeout",
        sender_ref="sender_timeout",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_timeout",
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
        channel_metadata_hash="a" * 64,
        text="timeout hardening",
    )


class ProviderInFlightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = GatewayStateStore.open(Path(self.temp.name) / "gateway.sqlite3", now_ms=900)
        registration = self.store.register_request(_inbound(), ingress_sha256="a" * 64, created_at_ms=1_100)
        self.request_id = registration.entry.request_id
        self.run_id = derive_run_identity(self.request_id, 1).run_id
        self.generation = 1
        self.life_id = "life_timeout"
        self.ticket = "ticket_timeout"
        self.store.acquire_generation_lease(
            request_id=self.request_id,
            run_id=self.run_id,
            run_sequence=1,
            generation=1,
            gateway_epoch=1,
            lease_id="lease_timeout",
            owner_instance_id="gateway_timeout",
            issued_at_ms=1_200,
            lease_duration_ms=500_000,
        )
        self.provider = RegenerativeExecutionAuthority(self.store)
        self.provider(self.base(
            "initialize",
            now_ms=1_300,
            root_goal_hash=canonical_sha256({"goal": "timeout"}),
            task_contract_hash=canonical_sha256({"task": "timeout"}),
            epoch_index=0,
        ))

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def base(self, operation: str, **extra) -> dict:
        return {
            "operation": operation,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "life_id": self.life_id,
            "outer_execution_ticket_id": self.ticket,
            **extra,
        }

    def effect(self, operation: str, *, global_step: int, now_ms: int) -> dict:
        postcondition = canonical_sha256({"post": "same"})
        logical = derive_logical_effect_id(
            request_id=self.request_id,
            run_id=self.run_id,
            generation=self.generation,
            obligation_key="write-once",
            effect_namespace="omni_body:file.write",
            normalized_target="path:C:/tmp/once.txt",
            desired_postcondition_sha256=postcondition,
        )
        return self.base(
            operation,
            now_ms=now_ms,
            epoch_index=0,
            global_step=global_step,
            logical_effect_id=logical,
            obligation_key="write-once",
            effect_namespace="omni_body:file.write",
            normalized_target="path:C:/tmp/once.txt",
            desired_postcondition_sha256=postcondition,
            attempt=global_step,
        )

    def test_started_but_unresolved_logical_effect_blocks_new_physical_attempt(self) -> None:
        first = self.provider(self.effect("prepare_effect", global_step=1, now_ms=2_000))
        started = self.provider(self.base(
            "start_effect",
            now_ms=2_100,
            epoch_index=0,
            effect_id=first["effect_id"],
            logical_effect_id=first["logical_effect_id"],
            attempt_id=first["attempt_id"],
            step_id=first["step_id"],
        ))
        self.assertTrue(started["dispatch_permitted"])
        second = self.provider(self.effect("prepare_effect", global_step=2, now_ms=2_200))
        self.assertEqual(second["disposition"], "in_flight")
        self.assertEqual(second["effect_id"], first["effect_id"])
        effects = self.store.list_effects_for_request(
            self.request_id,
            run_id=self.run_id,
            generation=self.generation,
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].state, "SIDE_EFFECT_STARTED")


if __name__ == "__main__":
    unittest.main()
