"""P18-M4 real-process certification worker.

TEST HARNESS ONLY.  It opens the canonical GatewayStateStore and drives the
existing RegenerativeExecutionAuthority from a separate OS process.  It is not
a production Runtime/Scheduler/Store and is never imported by production code.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app" / "backend" / "tiangong-backend"))

from contracts import (  # noqa: E402
    InboundEnvelope,
    InboundScope,
    canonical_sha256,
    derive_inbound_scope_keys,
    derive_run_identity,
)
from total_gateway.continuity import persist_working_checkpoint  # noqa: E402
from total_gateway.regenerative_execution import ExecutionFrontier, ZERO_HASH, derive_logical_effect_id  # noqa: E402
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority, authority_hash  # noqa: E402
from total_gateway.store import GatewayStateStore  # noqa: E402
from tiangong_kernel.l4_action_grounding.model_provider_adapter import ModelProviderErrorMapper  # noqa: E402


RUNTIME_VERSION = "tiangong-v3-p18-m4-real-process"
PROVIDER_VERSION = "deepseek-v4-adapter-v1"
MODEL_VERSION = "deepseek-v4"
TOOL_VERSION = "omni_body.v1"
SKILL_VERSION = "skill.v1"
TASK_VERSION = "task.v1"


def _inbound(scenario: str) -> InboundEnvelope:
    safe = canonical_sha256({"scenario": scenario})[:16]
    scope = InboundScope(
        channel="desktop",
        tenant_id=f"tenant_m4_{safe}",
        link_account_id=f"link_m4_{safe}",
        conversation_ref=f"conversation_m4_{safe}",
        channel_message_ref=f"message_m4_{safe}",
        sender_ref=f"sender_m4_{safe}",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id=f"inbound_m4_{safe}",
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
        text=f"P18-M4 real process {scenario}",
    )


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _setup(db: Path, scenario: str, *, now_ms: int) -> tuple[GatewayStateStore, RegenerativeExecutionAuthority, dict]:
    store = GatewayStateStore.open(db, now_ms=now_ms)
    registration = store.register_request(_inbound(scenario), ingress_sha256="a" * 64, created_at_ms=1_100)
    request_id = registration.entry.request_id
    run_id = derive_run_identity(request_id, 1).run_id
    generation = 1
    life_id = f"life_p18_m4_real_{canonical_sha256({'scenario': scenario})[:24]}"
    ticket = f"ticket_p18_m4_real_{canonical_sha256({'scenario': scenario})[:24]}"
    binding = store.get_request_generation_binding(request_id)
    if binding is None:
        store.acquire_generation_lease(
            request_id=request_id,
            run_id=run_id,
            run_sequence=1,
            generation=generation,
            gateway_epoch=1,
            lease_id=f"lease_{canonical_sha256({'scenario': scenario})[:32]}",
            owner_instance_id="p18-m4-real-process-worker",
            issued_at_ms=1_200,
            lease_duration_ms=86_400_000,
        )
    provider = RegenerativeExecutionAuthority(store)
    contract = store.get_execution_task_contract(request_id, run_id=run_id, generation=generation)
    root_hash = canonical_sha256({"goal": f"P18-M4 real process {scenario}"})
    task_hash = canonical_sha256({"task": f"complete {scenario} under process failure"})
    base = {
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "life_id": life_id,
        "outer_execution_ticket_id": ticket,
    }
    if contract is None:
        initialized = provider({
            "operation": "initialize",
            **base,
            "now_ms": now_ms,
            "root_goal_hash": root_hash,
            "task_contract_hash": task_hash,
            "epoch_index": 0,
        })
        if not initialized.get("initialized"):
            raise RuntimeError("real-process initialization failed")
    return store, provider, {
        **base,
        "root_goal_hash": root_hash,
        "task_contract_hash": task_hash,
        "authority_hash": authority_hash(ticket),
    }


def _frontier(identity: dict, *, version: int, step: int, scenario: str) -> ExecutionFrontier:
    return ExecutionFrontier(
        request_id=identity["request_id"],
        run_id=identity["run_id"],
        generation=identity["generation"],
        life_id=identity["life_id"],
        root_goal_hash=identity["root_goal_hash"],
        task_contract_hash=identity["task_contract_hash"],
        authority_hash=identity["authority_hash"],
        global_step=step,
        epoch_index=step // 75,
        epoch_step=step % 75,
        completed_obligation_ids=(),
        active_obligation_id=None,
        pending_obligation_ids=(),
        verified_fact_head=f"fact-head:{scenario}:{step}",
        artifact_revision_head=f"artifact-head:{scenario}:{step}",
        pending_effect_ids=(),
        ambiguous_effect_ids=(),
        active_blockers=(),
        failed_strategy_ids=(),
        latest_safe_step=f"real process step {step}",
        next_action_hint="continue same authoritative run",
        provider_turn_state_ref=None,
        frontier_version=version,
        frontier_hash=ZERO_HASH,
    ).with_computed_hash()


def _commit_checkpoint(store: GatewayStateStore, provider: RegenerativeExecutionAuthority, identity: dict, *, version: int, step: int, scenario: str, now_ms: int) -> dict:
    frontier = _frontier(identity, version=version, step=step, scenario=scenario)
    continuity = persist_working_checkpoint(
        store,
        life_id=identity["life_id"],
        request_id=identity["request_id"],
        run_id=identity["run_id"],
        generation=identity["generation"],
        user_goal=f"P18-M4 real process {scenario}",
        hard_constraints=("same request/run/generation",),
        active_plan=("resume from durable frontier",),
        latest_safe_step=f"real process step {step}",
        next_step="continue same authoritative run",
        recovery_preconditions=("ledger and checkpoint integrity valid",),
        created_at_ms=now_ms,
    )
    result = provider({
        "operation": "commit_checkpoint",
        **{key: identity[key] for key in ("request_id", "run_id", "generation", "life_id", "outer_execution_ticket_id")},
        "now_ms": now_ms + 1,
        "frontier": frontier.model_dump(mode="json"),
        "continuity_capsule_id": continuity.capsule.capsule_id,
        "recovery_preconditions": ["ledger and checkpoint integrity valid"],
        "critical_fact_status": "verified",
        "runtime_version": RUNTIME_VERSION,
        "provider_version": PROVIDER_VERSION,
        "model_version": MODEL_VERSION,
        "tool_contract_version": TOOL_VERSION,
        "skill_contract_version": SKILL_VERSION,
        "task_contract_version": TASK_VERSION,
        "semantic_handoff": f"structured real-process checkpoint at step {step}",
    })
    if not result.get("committed"):
        raise RuntimeError(f"checkpoint commit failed: {result}")
    return result


def _recover(provider: RegenerativeExecutionAuthority, identity: dict, *, now_ms: int) -> dict:
    result = provider({
        "operation": "recover",
        **{key: identity[key] for key in ("request_id", "run_id", "generation", "life_id", "outer_execution_ticket_id")},
        "now_ms": now_ms,
        "runtime_version": RUNTIME_VERSION,
        "provider_version": PROVIDER_VERSION,
        "model_version": MODEL_VERSION,
        "tool_contract_version": TOOL_VERSION,
        "skill_contract_version": SKILL_VERSION,
        "task_contract_version": TASK_VERSION,
    })
    return result


def _nonblocking_lock(path: Path) -> tuple[object | None, bool]:
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if handle.tell() == 0 and path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                return None, False
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return None, False
        return handle, True
    except Exception:
        handle.close()
        raise


def _unlock(handle: object) -> None:
    file_handle = handle
    try:
        if os.name == "nt":
            import msvcrt
            file_handle.seek(0)
            msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
    finally:
        file_handle.close()


def run_longrun(args: argparse.Namespace) -> int:
    db = Path(args.db)
    state_path = Path(args.state)
    artifact = Path(args.artifact)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    store, provider, identity = _setup(db, args.scenario, now_ms=int(time.time() * 1000))
    metrics = {
        "network_disconnects": 0,
        "api_timeouts": 0,
        "tool_timeouts": 0,
        "file_lock_blocks": 0,
        "sse_reconnects": 0,
        "provider_reconnects": 0,
        "checkpoint_count": 0,
        "recovery_count": 0,
        "test_failures_repaired": 0,
    }
    try:
        recovered = _recover(provider, identity, now_ms=int(time.time() * 1000) + 10)
        if recovered.get("recoverable") and recovered.get("resume_allowed"):
            start_step = int(recovered["frontier"]["global_step"]) + 1
            frontier_version = int(recovered["frontier"]["frontier_version"])
            metrics["recovery_count"] += 1
        else:
            current = store.get_execution_frontier(
                identity["request_id"], run_id=identity["run_id"], generation=identity["generation"]
            )
            start_step = 1 if current is None else current.global_step + 1
            frontier_version = 0 if current is None else current.frontier_version

        for step in range(start_step, args.total + 1):
            now_ms = int(time.time() * 1000) + step
            store.append_execution_event(
                event_key=f"real-process-step:{args.scenario}:{step}",
                request_id=identity["request_id"],
                run_id=identity["run_id"],
                generation=identity["generation"],
                epoch_index=step // 75,
                event_type="step.observed",
                payload={"scenario": args.scenario, "step": step, "process_id": os.getpid()},
                created_at_ms=now_ms,
            )

            if args.scenario in {"file_engineering", "code_edit_test_fix", "high_fault", "windows_longrun"}:
                with artifact.open("a", encoding="utf-8") as handle:
                    handle.write(f"{step}\n")

            if args.scenario == "code_edit_test_fix" and step == 333:
                code_path = artifact.with_suffix(".py")
                code_path.write_text("def broken(:\n", encoding="utf-8")
                try:
                    compile(code_path.read_text(encoding="utf-8"), str(code_path), "exec")
                except SyntaxError:
                    code_path.write_text("def repaired():\n    return 333\n", encoding="utf-8")
                    compile(code_path.read_text(encoding="utf-8"), str(code_path), "exec")
                    metrics["test_failures_repaired"] += 1

            if args.scenario == "high_fault":
                if step == 100:
                    left, right = socket.socketpair()
                    right.close()
                    try:
                        left.sendall(b"disconnect-probe")
                    except OSError:
                        metrics["network_disconnects"] += 1
                    finally:
                        left.close()
                    metrics["sse_reconnects"] += 1
                elif step == 120:
                    try:
                        raise TimeoutError("simulated API timeout at process boundary")
                    except TimeoutError:
                        metrics["api_timeouts"] += 1
                elif step == 130:
                    try:
                        raise TimeoutError("simulated tool timeout at process boundary")
                    except TimeoutError:
                        metrics["tool_timeouts"] += 1
                elif step == 150 and args.lock_probe:
                    lock_handle, acquired = _nonblocking_lock(Path(args.lock_probe))
                    if acquired:
                        assert lock_handle is not None
                        _unlock(lock_handle)
                    else:
                        metrics["file_lock_blocks"] += 1
                elif step == 170:
                    mapped = ModelProviderErrorMapper("deepseek_v4").jiexi(503, '{"error":{"message":"transient"}}')
                    if mapped.get("xuyao_zhongshi"):
                        metrics["provider_reconnects"] += 1

            if step % args.checkpoint_interval == 0 or step == args.total:
                frontier_version += 1
                _commit_checkpoint(
                    store,
                    provider,
                    identity,
                    version=frontier_version,
                    step=step,
                    scenario=args.scenario,
                    now_ms=now_ms + 1,
                )
                metrics["checkpoint_count"] += 1
                snapshot = {
                    "scenario": args.scenario,
                    "step": step,
                    "total": args.total,
                    "request_id": identity["request_id"],
                    "run_id": identity["run_id"],
                    "generation": identity["generation"],
                    "life_id": identity["life_id"],
                    "authority_hash": identity["authority_hash"],
                    "root_goal_hash": identity["root_goal_hash"],
                    "task_contract_hash": identity["task_contract_hash"],
                    "frontier_version": frontier_version,
                    "pid": os.getpid(),
                    "metrics": metrics,
                }
                _write_json(state_path, snapshot)
                if args.barrier_step and step == args.barrier_step:
                    Path(args.barrier).write_text("checkpoint-durable", encoding="utf-8")
                    while True:
                        time.sleep(1)
            if args.step_sleep > 0:
                time.sleep(args.step_sleep)

        audit = store.audit_execution_ledger(
            identity["request_id"], run_id=identity["run_id"], generation=identity["generation"]
        )
        final = json.loads(state_path.read_text(encoding="utf-8"))
        final["completed"] = True
        final["ledger_healthy"] = bool(audit.get("healthy"))
        final["ledger_event_count"] = int(audit.get("event_count") or 0)
        final["metrics"] = metrics
        _write_json(state_path, final)
        return 0
    finally:
        store.close()


def run_init(args: argparse.Namespace) -> int:
    store, _provider, identity = _setup(Path(args.db), args.scenario, now_ms=int(time.time() * 1000))
    try:
        _write_json(Path(args.state), identity)
        return 0
    finally:
        store.close()


def run_race(args: argparse.Namespace) -> int:
    store, provider, identity = _setup(Path(args.db), args.scenario, now_ms=int(time.time() * 1000))
    output = Path(args.state)
    ready = Path(args.ready)
    gate = Path(args.gate)
    artifact = Path(args.artifact)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    target = f"path:{artifact.resolve()}"
    postcondition = canonical_sha256({"target": target, "content": "race-winner"})
    logical = derive_logical_effect_id(
        request_id=identity["request_id"],
        run_id=identity["run_id"],
        generation=identity["generation"],
        obligation_key="m4-real-process-race",
        effect_namespace="filesystem.write",
        normalized_target=target,
        desired_postcondition_sha256=postcondition,
    )
    base = {key: identity[key] for key in ("request_id", "run_id", "generation", "life_id", "outer_execution_ticket_id")}
    try:
        prepared = provider({
            "operation": "prepare_effect",
            **base,
            "now_ms": int(time.time() * 1000),
            "epoch_index": 0,
            "global_step": 1,
            "attempt": 1,
            "logical_effect_id": logical,
            "obligation_key": "m4-real-process-race",
            "effect_namespace": "filesystem.write",
            "normalized_target": target,
            "desired_postcondition_sha256": postcondition,
        })
        ready.write_text("ready", encoding="utf-8")
        deadline = time.time() + 20
        while not gate.exists():
            if time.time() > deadline:
                raise TimeoutError("race gate timeout")
            time.sleep(0.01)
        started = provider({
            "operation": "start_effect",
            **base,
            "now_ms": int(time.time() * 1000) + 1,
            "epoch_index": 0,
            "effect_id": prepared["effect_id"],
            "logical_effect_id": prepared["logical_effect_id"],
            "attempt_id": prepared["attempt_id"],
            "step_id": prepared["step_id"],
        })
        wrote = False
        if started.get("dispatch_permitted"):
            artifact.write_text("race-winner", encoding="utf-8")
            provider({
                "operation": "finish_effect",
                **base,
                "now_ms": int(time.time() * 1000) + 2,
                "epoch_index": 0,
                "effect_id": prepared["effect_id"],
                "logical_effect_id": prepared["logical_effect_id"],
                "attempt_id": prepared["attempt_id"],
                "step_id": prepared["step_id"],
                "outcome": "succeeded",
                "result_summary": {"artifact": str(artifact), "sha256": postcondition},
            })
            wrote = True
        _write_json(output, {
            "prepared_disposition": prepared.get("disposition"),
            "dispatch_permitted": bool(started.get("dispatch_permitted")),
            "start_disposition": started.get("disposition"),
            "effect_id": prepared["effect_id"],
            "logical_effect_id": logical,
            "wrote": wrote,
        })
        return 0
    finally:
        store.close()


def run_hold_lock(args: argparse.Namespace) -> int:
    path = Path(args.artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, acquired = _nonblocking_lock(path)
    if not acquired or handle is None:
        return 2
    try:
        Path(args.ready).write_text("locked", encoding="utf-8")
        time.sleep(args.hold_seconds)
        return 0
    finally:
        _unlock(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("longrun", "init", "race", "hold-lock"), required=True)
    parser.add_argument("--db", default="")
    parser.add_argument("--state", default="")
    parser.add_argument("--artifact", default="")
    parser.add_argument("--scenario", default="real_process")
    parser.add_argument("--total", type=int, default=500)
    parser.add_argument("--checkpoint-interval", type=int, default=50)
    parser.add_argument("--barrier-step", type=int, default=0)
    parser.add_argument("--barrier", default="")
    parser.add_argument("--step-sleep", type=float, default=0.0)
    parser.add_argument("--lock-probe", default="")
    parser.add_argument("--ready", default="")
    parser.add_argument("--gate", default="")
    parser.add_argument("--hold-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.mode == "longrun":
        return run_longrun(args)
    if args.mode == "init":
        return run_init(args)
    if args.mode == "race":
        return run_race(args)
    return run_hold_lock(args)


if __name__ == "__main__":
    raise SystemExit(main())
