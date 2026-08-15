from __future__ import annotations

from pathlib import Path

ROOT = Path('.')
ZONG = ROOT / 'app/backend/tiangong-backend/v3/zongdiaodu.py'
EMBEDDED = ROOT / 'src/total_gateway/embedded_backend.py'
RUNTIME = ROOT / 'src/total_gateway/runtime.py'
TEST = ROOT / 'tests/test_p18_m1_canonical_continuity.py'


def require_once(text: str, needle: str, label: str) -> None:
    count = text.count(needle)
    if count != 1:
        raise SystemExit(f'{label}: expected one anchor, got {count}')


def patch_zong() -> None:
    text = ZONG.read_text(encoding='utf-8')
    global_anchor = '''def _simple_chain_authority_identity(run_state: dict[str, Any] | None) -> dict[str, Any]:\n'''
    require_once(text, global_anchor, 'zong authority helper')
    provider_code = '''_SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER: Callable[[dict[str, Any]], Any] | None = None\n\n\ndef set_simple_chain_continuity_checkpoint_provider(\n    provider: Callable[[dict[str, Any]], Any] | None,\n) -> None:\n    \"\"\"Bind the one Total-Gateway continuity authority into the embedded backend.\"\"\"\n    if provider is not None and not callable(provider):\n        raise TypeError(\"continuity checkpoint provider must be callable\")\n    global _SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER\n    _SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER = provider\n\n\n'''
    text = text.replace(global_anchor, provider_code + global_anchor, 1)

    start = text.index('def _simple_chain_checkpoint_continue(')
    end = text.index('\n\ndef _simple_chain_prepare_tool_budget(', start)
    helper = text[start:end]
    commit_anchor = '''    _simple_chain_save_run_state(run_state)\n    if run_state.get("persistence_degraded"):\n        return False\n\n    run_state["continuation"]["status"] = "checkpoint_committed"\n'''
    require_once(helper, commit_anchor, 'zong checkpoint commit boundary')
    canonical = '''    _simple_chain_save_run_state(run_state)\n    if run_state.get("persistence_degraded"):\n        return False\n\n    # A Gateway-authorized production run must commit the Epoch boundary into\n    # the existing canonical TaskContinuityCapsule chain before it can be\n    # reported as committed locally. The provider is an injected pointer to\n    # Total Gateway's already-open GatewayStateStore; it never owns state.\n    context = current_run_context()\n    provider = _SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER\n    canonical_required = bool(context.outer_execution_ticket_id)\n    if callable(provider):\n        canonical_payload = {\n            "schema": "tiangong.gateway.execution-epoch-checkpoint.v1",\n            **identity,\n            "outer_execution_ticket_id": str(context.outer_execution_ticket_id or ""),\n            "epoch_index": epoch_index,\n            "epoch_iteration_count": int(turn_loop.epoch_iteration_count),\n            "epoch_tool_rounds": int(turn_loop.epoch_action_rounds),\n            "global_iteration_count": int(turn_loop.iteration_count),\n            "global_tool_rounds": int(turn_loop.action_rounds),\n            "requested_tool_rounds": requested_count,\n            "latest_safe_step": str(run_state["continuation"].get("latest_safe_step") or ""),\n            "next_step": str(run_state["continuation"].get("next_step") or ""),\n            "source": str(source or "execution_epoch"),\n        }\n        try:\n            canonical_result = provider(canonical_payload)\n        except Exception:\n            canonical_result = None\n        binding_ok = (\n            isinstance(canonical_result, dict)\n            and canonical_result.get("ok") is True\n            and str(canonical_result.get("request_id") or "") == identity["request_id"]\n            and str(canonical_result.get("run_id") or "") == identity["run_id"]\n            and int(canonical_result.get("generation") if type(canonical_result.get("generation")) is int else -1)\n            == int(identity["generation"])\n            and str(canonical_result.get("life_id") or "") == identity["life_id"]\n            and bool(str(canonical_result.get("capsule_id") or ""))\n        )\n        if not binding_ok:\n            run_state["continuation"]["status"] = "canonical_checkpoint_failed"\n            _simple_chain_save_run_state(run_state)\n            return False\n        run_state["continuation"]["canonical_capsule_id"] = str(canonical_result["capsule_id"])\n        run_state["continuation"]["canonical_duplicate"] = bool(canonical_result.get("duplicate"))\n    elif canonical_required:\n        run_state["continuation"]["status"] = "canonical_checkpoint_unavailable"\n        _simple_chain_save_run_state(run_state)\n        return False\n\n    run_state["continuation"]["status"] = "checkpoint_committed"\n'''
    helper = helper.replace(commit_anchor, canonical, 1)
    text = text[:start] + helper + text[end:]
    ZONG.write_text(text, encoding='utf-8')


def patch_embedded() -> None:
    text = EMBEDDED.read_text(encoding='utf-8')
    init_anchor = '''        self.scheduler = scheduler_module.Zongdiaodu()\n        self.scheduler.life_orchestrator = None\n'''
    require_once(text, init_anchor, 'embedded scheduler init')
    text = text.replace(
        init_anchor,
        '''        self.scheduler = scheduler_module.Zongdiaodu()\n        # Reset the process-global dependency pointer before this GatewayRuntime\n        # instance wires its own canonical store provider. This prevents test or\n        # restart leakage while keeping one shared provider for concurrent runs.\n        continuity_setter = getattr(scheduler_module, "set_simple_chain_continuity_checkpoint_provider", None)\n        if callable(continuity_setter):\n            continuity_setter(None)\n        self.scheduler.life_orchestrator = None\n''',
        1,
    )
    method_anchor = '''    def set_learning_ingest_provider(self, provider: Any) -> None:\n'''
    require_once(text, method_anchor, 'embedded provider method anchor')
    method = '''    def set_continuity_checkpoint_provider(self, provider: Any) -> None:\n        \"\"\"Bind Epoch checkpoints to Total Gateway's one canonical store.\"\"\"\n        if provider is not None and not callable(provider):\n            raise TypeError("continuity checkpoint provider must be callable")\n        module = importlib.import_module("v3.zongdiaodu")\n        setter = getattr(module, "set_simple_chain_continuity_checkpoint_provider", None)\n        if not callable(setter):\n            raise EmbeddedBackendError("continuity.checkpoint_provider_unsupported")\n        setter(provider)\n        self._continuity_checkpoint_provider = provider\n\n'''
    text = text.replace(method_anchor, method + method_anchor, 1)
    EMBEDDED.write_text(text, encoding='utf-8')


def patch_runtime() -> None:
    text = RUNTIME.read_text(encoding='utf-8')
    import_anchor = 'from .cutover_coordinator import ChannelCutoverCoordinator\n'
    require_once(text, import_anchor, 'runtime import anchor')
    text = text.replace(import_anchor, import_anchor + 'from .continuity import persist_working_checkpoint\n', 1)

    helper_anchor = 'def _gateway_p15_memory_remember(runtime: object, user_text: object) -> dict[str, object]:\n'
    require_once(text, helper_anchor, 'runtime helper anchor')
    helper = '''def _gateway_execution_epoch_checkpoint(\n    runtime: object,\n    payload: object,\n) -> dict[str, object]:\n    \"\"\"Commit one backend Epoch boundary through the existing Gateway continuity SSoT.\"\"\"\n    if not isinstance(payload, Mapping):\n        return {"ok": False, "error": "continuity.payload_invalid"}\n    if payload.get("schema") != "tiangong.gateway.execution-epoch-checkpoint.v1":\n        return {"ok": False, "error": "continuity.schema_invalid"}\n    request_id = str(payload.get("request_id") or "").strip()\n    run_id = str(payload.get("run_id") or "").strip()\n    life_id = str(payload.get("life_id") or "").strip()\n    generation = payload.get("generation")\n    epoch_index = payload.get("epoch_index")\n    global_tool_rounds = payload.get("global_tool_rounds")\n    global_iteration_count = payload.get("global_iteration_count")\n    latest_safe_step = str(payload.get("latest_safe_step") or "").strip()[:500]\n    next_step = str(payload.get("next_step") or "").strip()[:500]\n    if (\n        not request_id\n        or not run_id\n        or not life_id\n        or type(generation) is not int\n        or generation < 0\n        or type(epoch_index) is not int\n        or epoch_index < 0\n        or type(global_tool_rounds) is not int\n        or global_tool_rounds < 0\n        or type(global_iteration_count) is not int\n        or global_iteration_count < 0\n        or not latest_safe_step\n        or not next_step\n    ):\n        return {"ok": False, "error": "continuity.identity_or_progress_invalid"}\n    store = getattr(runtime, "store", None)\n    if store is None:\n        return {"ok": False, "error": "continuity.store_unavailable"}\n    try:\n        active = store.get_active_request_capsule(\n            request_id, run_id=run_id, generation=generation\n        )\n    except Exception:\n        return {"ok": False, "error": "continuity.authority_lookup_failed"}\n    if active is None:\n        return {"ok": False, "error": "continuity.authority_not_found"}\n    current = active.capsule\n    if (\n        current.request_id != request_id\n        or current.run_id != run_id\n        or current.generation != generation\n        or current.life_id != life_id\n    ):\n        return {"ok": False, "error": "continuity.authority_binding_mismatch"}\n    recovery = tuple(dict.fromkeys((\n        *current.recovery_preconditions,\n        f"execution epoch {epoch_index} is canonically committed",\n        f"global tool round {global_tool_rounds} is the latest safe tool boundary",\n        f"global iteration {global_iteration_count} remains in the same run",\n        "request/run/generation/life authority identity remains unchanged",\n    )))\n    try:\n        record = persist_working_checkpoint(\n            store,\n            life_id=current.life_id,\n            request_id=current.request_id,\n            run_id=current.run_id,\n            generation=current.generation,\n            user_goal=current.user_goal,\n            hard_constraints=current.hard_constraints,\n            active_plan=current.active_plan,\n            verified_fact_ids=current.verified_fact_ids,\n            artifact_refs=current.artifact_refs,\n            pending_effect_ids=current.pending_effect_ids,\n            latest_safe_step=latest_safe_step,\n            next_step=next_step,\n            recovery_preconditions=recovery,\n            created_at_ms=time.time_ns() // 1_000_000,\n        )\n    except Exception:\n        return {"ok": False, "error": "continuity.checkpoint_commit_failed"}\n    capsule = record.capsule\n    return {\n        "ok": True,\n        "request_id": capsule.request_id,\n        "run_id": capsule.run_id,\n        "generation": capsule.generation,\n        "life_id": capsule.life_id,\n        "capsule_id": capsule.capsule_id,\n        "duplicate": bool(record.duplicate),\n        "epoch_index": epoch_index,\n        "global_tool_rounds": global_tool_rounds,\n        "global_iteration_count": global_iteration_count,\n    }\n\n\n'''
    text = text.replace(helper_anchor, helper + helper_anchor, 1)

    wiring_anchor = '''                runtime.backend_service.set_p15_memory_provider(\n                    remember_provider=p15_memory_remember,\n                    recall_provider=p15_memory_recall,\n                )\n'''
    require_once(text, wiring_anchor, 'runtime embedded provider wiring')
    text = text.replace(
        wiring_anchor,
        wiring_anchor + '''\n                def execution_epoch_checkpoint(payload: object) -> dict[str, object]:\n                    return _gateway_execution_epoch_checkpoint(runtime, payload)\n\n                runtime.backend_service.set_continuity_checkpoint_provider(\n                    execution_epoch_checkpoint\n                )\n''',
        1,
    )
    RUNTIME.write_text(text, encoding='utf-8')


def write_tests() -> None:
    content = r'''from __future__ import annotations

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
            created_at_ms=2_000,
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

                set_simple_chain_continuity_checkpoint_provider(provider)
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
            finally:
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
'''
    TEST.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    patch_zong()
    patch_embedded()
    patch_runtime()
    write_tests()
