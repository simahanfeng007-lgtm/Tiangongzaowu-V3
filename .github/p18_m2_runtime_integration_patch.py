from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def insert_before(text: str, anchor: str, payload: str, label: str) -> str:
    count = text.count(anchor)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(anchor, payload + anchor, 1)


# ---------------------------------------------------------------------------
# Source authority: register the new V3 implementation file in the closed world.
# ---------------------------------------------------------------------------
ownership_path = Path("source-ownership.json")
ownership = ownership_path.read_text(encoding="utf-8")
ownership = replace_once(
    ownership,
    '"runtime_lifecycle.py","runtime_turn_orchestration.py","runtime_tool_result_boundary.py"',
    '"runtime_lifecycle.py","runtime_regenerative_boundary.py","runtime_turn_orchestration.py","runtime_tool_result_boundary.py"',
    "source ownership regenerative boundary",
)
ownership_path.write_text(ownership, encoding="utf-8", newline="\n")

# ---------------------------------------------------------------------------
# Gateway authority provider: add bounded live-Frontier commits and terminal chain event.
# ---------------------------------------------------------------------------
provider_path = Path("src/total_gateway/regenerative_provider.py")
provider = provider_path.read_text(encoding="utf-8")
provider = replace_once(
    provider,
    '            "reconcile_effect": self._reconcile_effect,\n            "commit_checkpoint": self._commit_checkpoint,\n',
    '            "reconcile_effect": self._reconcile_effect,\n            "update_frontier": self._update_frontier,\n            "commit_checkpoint": self._commit_checkpoint,\n',
    "provider update frontier handler",
)
provider = replace_once(
    provider,
    '                "effect_state": "LOGICAL_COMMITTED" if prior_disposition == "already_committed" else "AMBIGUOUS",\n                "ledger_seq": event.ledger_seq,\n            }\n',
    '                "effect_state": "LOGICAL_COMMITTED" if prior_disposition == "already_committed" else "AMBIGUOUS",\n                "prior_result_summary": (\n                    dict(prior_event.payload.get("result_summary") or {})\n                    if getattr(prior_event, "event_type", "") == "step.committed"\n                    else dict(prior_event.payload.get("evidence") or {})\n                ),\n                "ledger_seq": event.ledger_seq,\n            }\n',
    "provider prior result summary",
)
update_frontier_method = '''    def _update_frontier(self, payload: Mapping[str, Any]) -> dict[str, Any]:\n        identity, contract = self._bound_identity(payload)\n        frontier = _json_frontier(payload.get("frontier"))\n        if (\n            frontier.request_id != identity.request_id\n            or frontier.run_id != identity.run_id\n            or frontier.generation != identity.generation\n            or frontier.life_id != identity.life_id\n            or frontier.root_goal_hash != str(contract["root_goal_hash"])\n            or frontier.task_contract_hash != str(contract["task_contract_hash"])\n            or frontier.authority_hash != str(contract["authority_hash"])\n        ):\n            raise StoreConflictError("frontier crossed immutable task/authority identity")\n        effects = self._store.list_effects_for_request(\n            identity.request_id, run_id=identity.run_id, generation=identity.generation\n        )\n        actual_pending = tuple(sorted(\n            record.claim.effect_id for record in effects\n            if record.state in {"CLAIMED", "SIDE_EFFECT_STARTED"}\n        ))\n        actual_ambiguous = tuple(sorted(\n            record.claim.effect_id for record in effects if record.state == "AMBIGUOUS"\n        ))\n        if actual_pending != frontier.pending_effect_ids or actual_ambiguous != frontier.ambiguous_effect_ids:\n            raise StoreConflictError("frontier effect projection disagrees with canonical Effect Ledger")\n        current = self._store.get_execution_frontier(\n            identity.request_id, run_id=identity.run_id, generation=identity.generation\n        )\n        if current is not None and current.frontier_hash == frontier.frontier_hash:\n            return {\n                "committed": True, "duplicate": True,\n                "frontier_version": current.frontier_version,\n                "frontier_hash": current.frontier_hash,\n            }\n        expected_revision = 0 if current is None else current.frontier_version\n        if frontier.frontier_version != expected_revision + 1:\n            raise StoreConflictError("frontier revision is not the next authoritative CAS revision")\n        self._store.commit_execution_frontier(\n            frontier, expected_revision=expected_revision,\n            updated_at_ms=_integer(payload.get("now_ms"), label="now_ms"),\n        )\n        event, _ = self._store.append_execution_event(\n            event_key=f"frontier.updated:{frontier.frontier_version}",\n            request_id=identity.request_id, run_id=identity.run_id,\n            generation=identity.generation, epoch_index=frontier.epoch_index,\n            event_type="frontier.updated",\n            created_at_ms=_integer(payload.get("now_ms"), label="now_ms"),\n            payload={"frontier": frontier.model_dump(mode="json")},\n        )\n        return {\n            "committed": True, "duplicate": False,\n            "frontier_version": frontier.frontier_version,\n            "frontier_hash": frontier.frontier_hash,\n            "ledger_seq": event.ledger_seq,\n        }\n\n'''
provider = insert_before(
    provider,
    '    def _commit_checkpoint(self, payload: Mapping[str, Any]) -> dict[str, Any]:\n',
    update_frontier_method,
    "provider update frontier method",
)
provider = replace_once(
    provider,
    '        return {\n            "verified_complete": verified,\n            "reasons": reasons,\n            "proof_hash": proof_hash,\n            "ledger_seq": event.ledger_seq,\n        }\n',
    '        terminal_seq = event.ledger_seq\n        if verified:\n            terminal, _ = self._store.append_execution_event(\n                event_key=f"chain.completed:{proposal_key}",\n                request_id=identity.request_id, run_id=identity.run_id,\n                generation=identity.generation, epoch_index=epoch_index,\n                event_type="chain.completed", created_at_ms=now_ms,\n                payload={"completion_proof_hash": proof_hash, "frontier_hash": proof_payload["frontier_hash"]},\n                causal_parent_event_id=event.event_id,\n            )\n            terminal_seq = terminal.ledger_seq\n        return {\n            "verified_complete": verified,\n            "reasons": reasons,\n            "proof_hash": proof_hash,\n            "ledger_seq": terminal_seq,\n        }\n',
    "provider completion chain event",
)
provider_path.write_text(provider, encoding="utf-8", newline="\n")

# ---------------------------------------------------------------------------
# Embedded backend: thin provider injection only; no persistence ownership.
# ---------------------------------------------------------------------------
embedded_path = Path("src/total_gateway/embedded_backend.py")
embedded = embedded_path.read_text(encoding="utf-8")
embedded = replace_once(
    embedded,
    '        continuity_setter = getattr(scheduler_module, "set_simple_chain_continuity_checkpoint_provider", None)\n        if callable(continuity_setter):\n            continuity_setter(None)\n',
    '        continuity_setter = getattr(scheduler_module, "set_simple_chain_continuity_checkpoint_provider", None)\n        if callable(continuity_setter):\n            continuity_setter(None)\n        regenerative_setter = getattr(scheduler_module, "set_simple_chain_regenerative_execution_provider", None)\n        if callable(regenerative_setter):\n            regenerative_setter(None)\n',
    "embedded provider reset",
)
embedded_method = '''    def set_regenerative_execution_provider(self, provider: Any) -> None:\n        \"\"\"Bind P18-M2 execution requests to Total Gateway's existing canonical store.\"\"\"\n        if provider is not None and not callable(provider):\n            raise TypeError("regenerative execution provider must be callable")\n        module = importlib.import_module("v3.zongdiaodu")\n        setter = getattr(module, "set_simple_chain_regenerative_execution_provider", None)\n        if not callable(setter):\n            raise EmbeddedBackendError("embedded_backend.regenerative_provider_unsupported")\n        setter(provider)\n        self._regenerative_execution_provider = provider\n\n'''
embedded = insert_before(
    embedded,
    '    def set_continuity_checkpoint_provider(self, provider: Any) -> None:\n',
    embedded_method,
    "embedded regenerative setter",
)
embedded_path.write_text(embedded, encoding="utf-8", newline="\n")

# ---------------------------------------------------------------------------
# Gateway runtime: instantiate authority adapter over the already-open one store.
# Also project M2 pending/ambiguous effects into canonical TaskContinuityCapsule.
# ---------------------------------------------------------------------------
runtime_path = Path("src/total_gateway/runtime.py")
runtime = runtime_path.read_text(encoding="utf-8")
runtime = replace_once(
    runtime,
    'from .readiness_collector import ProductionReadinessCollector\n',
    'from .readiness_collector import ProductionReadinessCollector\nfrom .regenerative_provider import RegenerativeExecutionAuthority\n',
    "runtime regenerative import",
)
runtime = replace_once(
    runtime,
    '            pending_effect_ids=current.pending_effect_ids,\n            latest_safe_step=latest_safe_step,\n',
    '            pending_effect_ids=tuple(dict.fromkeys((\n                *current.pending_effect_ids,\n                *(str(item).strip() for item in payload.get("pending_effect_ids", ()) if str(item).strip()),\n            ))),\n            latest_safe_step=latest_safe_step,\n',
    "continuity pending effects projection",
)
runtime = replace_once(
    runtime,
    '                runtime.backend_service.set_continuity_checkpoint_provider(\n                    execution_epoch_checkpoint\n                )\n\n                def pending_learning_ingest(arguments: object) -> dict[str, object]:\n',
    '                runtime.backend_service.set_continuity_checkpoint_provider(\n                    execution_epoch_checkpoint\n                )\n                runtime.backend_service.set_regenerative_execution_provider(\n                    RegenerativeExecutionAuthority(runtime.store)\n                )\n\n                def pending_learning_ingest(arguments: object) -> dict[str, object]:\n',
    "runtime provider injection",
)
runtime_path.write_text(runtime, encoding="utf-8", newline="\n")

# ---------------------------------------------------------------------------
# V3 production chain: provider bridge, single+parallel effects, resume,
# structured checkpoint-before-rollover, bounded context, Completion Proof.
# ---------------------------------------------------------------------------
zong_path = Path("app/backend/tiangong-backend/v3/zongdiaodu.py")
zong = zong_path.read_text(encoding="utf-8")

# Import pure deterministic/bounded helper functions.
zong = replace_once(
    zong,
    'from .runtime_turn_orchestration import (\n',
    'from .runtime_regenerative_boundary import (\n    bounded_history as _simple_chain_bound_history,\n    build_frontier_payload as _simple_chain_build_frontier_payload,\n    canonical_sha256 as _simple_chain_regenerative_sha256,\n    task_hashes as _simple_chain_task_hashes,\n    tool_effect_descriptor as _simple_chain_tool_effect_descriptor,\n)\nfrom .runtime_turn_orchestration import (\n',
    "zong regenerative helper imports",
)

# Provider pointer + setter, positioned next to the existing Continuity pointer.
zong = replace_once(
    zong,
    '_SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER: Callable[[dict[str, Any]], Any] | None = None\n',
    '_SIMPLE_CHAIN_CONTINUITY_CHECKPOINT_PROVIDER: Callable[[dict[str, Any]], Any] | None = None\n_SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER: Callable[[dict[str, Any]], Any] | None = None\n_SIMPLE_CHAIN_REGENERATIVE_STATE_LOCK = threading.RLock()\n',
    "zong regenerative provider global",
)
regenerative_setter = '''\n\ndef set_simple_chain_regenerative_execution_provider(\n    provider: Callable[[dict[str, Any]], Any] | None,\n) -> None:\n    \"\"\"Inject Total Gateway's one P18-M2 execution authority adapter.\"\"\"\n    if provider is not None and not callable(provider):\n        raise TypeError("regenerative execution provider must be callable")\n    global _SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER\n    _SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER = provider\n\n'''
zong = insert_before(
    zong,
    'def _simple_chain_authority_identity(run_state: dict[str, Any] | None) -> dict[str, Any]:\n',
    regenerative_setter,
    "zong regenerative setter",
)

# Core thin bridge helpers. Total Gateway still owns every authoritative mutation.
regenerative_helpers = r'''

def _simple_chain_regenerative_call(
    run_state: dict[str, Any] | None,
    operation: str,
    **payload: Any,
) -> dict[str, Any] | None:
    context = current_run_context()
    ticket_id = str(getattr(context, "outer_execution_ticket_id", "") or "").strip()
    if not ticket_id:
        return None
    provider = _SIMPLE_CHAIN_REGENERATIVE_EXECUTION_PROVIDER
    if not callable(provider):
        raise RuntimeError("regenerative_execution_provider_unavailable")
    identity = _simple_chain_authority_identity(run_state)
    if (
        not identity.get("request_id")
        or not identity.get("run_id")
        or not identity.get("life_id")
        or type(identity.get("generation")) is not int
        or int(identity.get("generation")) < 0
    ):
        raise RuntimeError("regenerative_execution_identity_unavailable")
    request = {
        "operation": str(operation or "").strip(),
        "request_id": identity["request_id"],
        "run_id": identity["run_id"],
        "generation": int(identity["generation"]),
        "life_id": identity["life_id"],
        "outer_execution_ticket_id": ticket_id,
        "now_ms": time.time_ns() // 1_000_000,
        **payload,
    }
    result = provider(request)
    if not isinstance(result, dict):
        raise RuntimeError("regenerative_execution_provider_invalid_result")
    if result.get("schema") != "tiangong.gateway.regenerative-provider.v1":
        raise RuntimeError("regenerative_execution_provider_schema_mismatch")
    if str(result.get("operation") or "") != request["operation"]:
        raise RuntimeError("regenerative_execution_provider_operation_mismatch")
    return dict(result)


def _simple_chain_regenerative_state(run_state: dict[str, Any]) -> dict[str, Any]:
    state = run_state.get("regenerative")
    if not isinstance(state, dict):
        state = {}
        run_state["regenerative"] = state
    state.setdefault("frontier_version", 0)
    state.setdefault("frontier_hash", "")
    state.setdefault("pending_effect_ids", [])
    state.setdefault("ambiguous_effect_ids", [])
    state.setdefault("active_effects", {})
    state.setdefault("critical_fact_status", "verified")
    return state


def _simple_chain_regenerative_initialize(
    run_state: dict[str, Any],
    user_goal: str,
) -> dict[str, Any] | None:
    if str(run_state.get("mode") or "") != "work":
        return None
    task_contract = run_state.get("task_contract") if isinstance(run_state.get("task_contract"), dict) else {}
    root_goal_hash, task_contract_hash = _simple_chain_task_hashes(user_goal, task_contract)
    initialized = _simple_chain_regenerative_call(
        run_state,
        "initialize",
        root_goal_hash=root_goal_hash,
        task_contract_hash=task_contract_hash,
        epoch_index=0,
    )
    if initialized is None:
        return None
    state = _simple_chain_regenerative_state(run_state)
    state.update({
        "root_goal_hash": str(initialized["root_goal_hash"]),
        "task_contract_hash": str(initialized["task_contract_hash"]),
        "authority_hash": str(initialized["authority_hash"]),
    })
    recovered = _simple_chain_regenerative_call(run_state, "recover")
    if isinstance(recovered, dict) and recovered.get("recoverable") is True:
        frontier = recovered.get("frontier") if isinstance(recovered.get("frontier"), dict) else {}
        state["recovery_frontier"] = frontier
        state["frontier_version"] = int(frontier.get("frontier_version") or 0)
        state["frontier_hash"] = str(frontier.get("frontier_hash") or "")
        state["pending_effect_ids"] = sorted({
            str(item) for item in recovered.get("pending_effect_ids", ()) if str(item).strip()
        })
        state["ambiguous_effect_ids"] = sorted({
            str(item) for item in recovered.get("ambiguous_effect_ids", ()) if str(item).strip()
        })
        checkpoint = recovered.get("checkpoint") if isinstance(recovered.get("checkpoint"), dict) else {}
        state["recovered_checkpoint_id"] = str(checkpoint.get("checkpoint_id") or "")
        state["used_previous_checkpoint"] = bool(recovered.get("used_previous_checkpoint"))
    return recovered


def _simple_chain_regenerative_restore_turn_loop(
    run_state: dict[str, Any],
    turn_loop: TurnLoopState,
) -> None:
    state = _simple_chain_regenerative_state(run_state)
    frontier = state.pop("recovery_frontier", None)
    if not isinstance(frontier, dict):
        return
    turn_loop.action_rounds = max(0, int(frontier.get("global_step") or 0))
    turn_loop.epoch_index = max(0, int(frontier.get("epoch_index") or 0))
    turn_loop.epoch_action_rounds = max(0, int(frontier.get("epoch_step") or 0))
    provider_ref = str(frontier.get("provider_turn_state_ref") or "")
    if provider_ref.startswith("iterations:"):
        try:
            turn_loop.iteration_count = max(0, int(provider_ref.split(":", 1)[1]))
        except Exception:
            pass
    turn_loop.epoch_iteration_count = 0


def _simple_chain_regenerative_obligations(run_state: dict[str, Any]) -> tuple[list[str], str | None, list[str]]:
    completed: list[str] = []
    pending: list[str] = []
    active: str | None = None
    raw = run_state.get("obligations")
    values = raw if isinstance(raw, list) else list(raw.values()) if isinstance(raw, dict) else []
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            continue
        obligation_id = str(item.get("id") or item.get("obligation_id") or f"ob_{index}").strip()[:200]
        if not obligation_id:
            continue
        status = str(item.get("status") or "pending").strip().lower()
        if status in {"satisfied", "complete", "completed", "done", "verified"}:
            completed.append(obligation_id)
        else:
            pending.append(obligation_id)
            if active is None and status in {"active", "running", "in_progress", "executing"}:
                active = obligation_id
    return sorted(set(completed))[:512], active, sorted(set(pending))[:512]


def _simple_chain_regenerative_frontier(
    run_state: dict[str, Any],
    turn_loop: TurnLoopState,
    *,
    global_step: int | None = None,
    epoch_step: int | None = None,
    latest_safe_step: str = "execution state is durably observed",
    next_action_hint: str = "continue authoritative execution",
) -> dict[str, Any]:
    state = _simple_chain_regenerative_state(run_state)
    identity = _simple_chain_authority_identity(run_state)
    completed, active, pending_obligations = _simple_chain_regenerative_obligations(run_state)
    return _simple_chain_build_frontier_payload(
        request_id=str(identity.get("request_id") or ""),
        run_id=str(identity.get("run_id") or ""),
        generation=int(identity.get("generation") or 0),
        life_id=str(identity.get("life_id") or ""),
        root_goal_hash=str(state.get("root_goal_hash") or ""),
        task_contract_hash=str(state.get("task_contract_hash") or ""),
        authority_hash=str(state.get("authority_hash") or ""),
        global_step=max(0, int(turn_loop.action_rounds if global_step is None else global_step)),
        epoch_index=max(0, int(turn_loop.epoch_index)),
        epoch_step=max(0, int(turn_loop.epoch_action_rounds if epoch_step is None else epoch_step)),
        frontier_version=max(1, int(state.get("frontier_version") or 0) + 1),
        completed_obligation_ids=completed,
        active_obligation_id=active,
        pending_obligation_ids=pending_obligations,
        pending_effect_ids=state.get("pending_effect_ids") or [],
        ambiguous_effect_ids=state.get("ambiguous_effect_ids") or [],
        active_blockers=run_state.get("final_reasons") or [],
        failed_strategy_ids=state.get("failed_strategy_ids") or [],
        latest_safe_step=latest_safe_step,
        next_action_hint=next_action_hint,
        provider_turn_state_ref=f"iterations:{int(turn_loop.iteration_count)}",
    )


def _simple_chain_regenerative_update_frontier(
    run_state: dict[str, Any],
    turn_loop: TurnLoopState,
    *,
    global_step: int | None = None,
    epoch_step: int | None = None,
    latest_safe_step: str = "execution state is durably observed",
    next_action_hint: str = "continue authoritative execution",
) -> dict[str, Any] | None:
    context = current_run_context()
    if not str(getattr(context, "outer_execution_ticket_id", "") or "").strip():
        return None
    frontier = _simple_chain_regenerative_frontier(
        run_state,
        turn_loop,
        global_step=global_step,
        epoch_step=epoch_step,
        latest_safe_step=latest_safe_step,
        next_action_hint=next_action_hint,
    )
    committed = _simple_chain_regenerative_call(run_state, "update_frontier", frontier=frontier)
    if not isinstance(committed, dict) or committed.get("committed") is not True:
        raise RuntimeError("regenerative_frontier_commit_failed")
    state = _simple_chain_regenerative_state(run_state)
    state["frontier_version"] = int(committed.get("frontier_version") or frontier["frontier_version"])
    state["frontier_hash"] = str(committed.get("frontier_hash") or frontier["frontier_hash"])
    state["latest_frontier"] = frontier
    return frontier


def _simple_chain_regenerative_effect_state(
    run_state: dict[str, Any],
    effect_id: str,
    *,
    state: str,
    call_id: str = "",
    logical_effect_id: str = "",
    attempt_id: str = "",
    step_id: str = "",
) -> None:
    with _SIMPLE_CHAIN_REGENERATIVE_STATE_LOCK:
        regenerative = _simple_chain_regenerative_state(run_state)
        pending = set(str(item) for item in regenerative.get("pending_effect_ids") or [] if str(item).strip())
        ambiguous = set(str(item) for item in regenerative.get("ambiguous_effect_ids") or [] if str(item).strip())
        active = regenerative.get("active_effects") if isinstance(regenerative.get("active_effects"), dict) else {}
        if state in {"prepared", "started"}:
            pending.add(effect_id)
            ambiguous.discard(effect_id)
        elif state == "ambiguous":
            pending.discard(effect_id)
            ambiguous.add(effect_id)
        else:
            pending.discard(effect_id)
            ambiguous.discard(effect_id)
        if state == "started" and call_id:
            active[call_id] = {
                "effect_id": effect_id,
                "logical_effect_id": logical_effect_id,
                "attempt_id": attempt_id,
                "step_id": step_id,
            }
        elif call_id:
            active.pop(call_id, None)
        regenerative["pending_effect_ids"] = sorted(pending)[:512]
        regenerative["ambiguous_effect_ids"] = sorted(ambiguous)[:512]
        regenerative["active_effects"] = dict(list(active.items())[-64:])


def _simple_chain_regenerative_execute_tool(
    owner: Any,
    run_state: dict[str, Any],
    turn_loop: TurnLoopState,
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    user_message: str,
    call_id: str,
    global_step: int,
    attempted_action: str,
    update_frontier: bool = True,
) -> Any:
    context = current_run_context()
    if not str(getattr(context, "outer_execution_ticket_id", "") or "").strip():
        return owner._jineng_zhixing(tool_name, tool_args, user_message, call_id=call_id)
    descriptor = _simple_chain_tool_effect_descriptor(
        request_id=str(getattr(context, "request_id", "") or ""),
        run_id=str(getattr(context, "run_id", "") or ""),
        generation=int(getattr(context, "generation", 0) or 0),
        tool_name=tool_name,
        tool_args=tool_args,
        attempted_action=attempted_action,
    )
    _simple_chain_regenerative_call(
        run_state,
        "append_event",
        event_key=f"step.planned:{call_id}:{global_step}",
        epoch_index=int(turn_loop.epoch_index),
        event_type="step.planned",
        payload={
            "call_id": call_id,
            "global_step": int(global_step),
            "tool_name": tool_name,
            "attempted_action": attempted_action,
            **descriptor,
        },
        logical_effect_id=descriptor["logical_effect_id"],
    )
    prepared = _simple_chain_regenerative_call(
        run_state,
        "prepare_effect",
        epoch_index=int(turn_loop.epoch_index),
        global_step=int(global_step),
        attempt=max(1, int(global_step)),
        **descriptor,
    )
    if not isinstance(prepared, dict):
        raise RuntimeError("regenerative_effect_prepare_missing")
    disposition = str(prepared.get("disposition") or "")
    effect_id = str(prepared.get("effect_id") or "")
    logical_effect_id = str(prepared.get("logical_effect_id") or descriptor["logical_effect_id"])
    attempt_id = str(prepared.get("attempt_id") or "")
    step_id = str(prepared.get("step_id") or "")
    if disposition == "already_committed":
        raw = {
            "ok": True,
            "status": "already_committed",
            "deduplicated": True,
            "effect_id": effect_id,
            "logical_effect_id": logical_effect_id,
            "prior_result_summary": prepared.get("prior_result_summary") or {},
        }
        if update_frontier:
            _simple_chain_regenerative_update_frontier(
                run_state, turn_loop, global_step=global_step,
                latest_safe_step=f"logical effect {logical_effect_id} was already committed",
            )
        return raw
    if disposition == "reconcile_required":
        _simple_chain_regenerative_effect_state(run_state, effect_id, state="ambiguous")
        if update_frontier:
            _simple_chain_regenerative_update_frontier(
                run_state, turn_loop, global_step=global_step,
                latest_safe_step=f"logical effect {logical_effect_id} requires reconciliation",
                next_action_hint="reconcile ambiguous effect before retry",
            )
        return {
            "ok": False,
            "status": "ambiguous",
            "ambiguous_effect": True,
            "error": "[EFFECT_RECONCILIATION_REQUIRED] logical action outcome is unknown; retry blocked",
            "effect_id": effect_id,
            "logical_effect_id": logical_effect_id,
        }
    if disposition != "prepared":
        return {
            "ok": False,
            "status": disposition or "blocked",
            "error": f"[EFFECT_PREPARE_BLOCKED] {disposition or 'unknown'}",
            "effect_id": effect_id,
            "logical_effect_id": logical_effect_id,
        }
    _simple_chain_regenerative_effect_state(
        run_state, effect_id, state="prepared", call_id=call_id,
        logical_effect_id=logical_effect_id, attempt_id=attempt_id, step_id=step_id,
    )
    started = _simple_chain_regenerative_call(
        run_state,
        "start_effect",
        epoch_index=int(turn_loop.epoch_index),
        effect_id=effect_id,
        logical_effect_id=logical_effect_id,
        attempt_id=attempt_id,
        step_id=step_id,
    )
    if not isinstance(started, dict) or started.get("dispatch_permitted") is not True:
        start_disposition = str((started or {}).get("disposition") or "blocked")
        if start_disposition == "reconcile_required":
            _simple_chain_regenerative_effect_state(run_state, effect_id, state="ambiguous", call_id=call_id)
        else:
            _simple_chain_regenerative_effect_state(run_state, effect_id, state="blocked", call_id=call_id)
        return {
            "ok": False,
            "status": start_disposition,
            "ambiguous_effect": start_disposition == "reconcile_required",
            "error": f"[EFFECT_DISPATCH_BLOCKED] {start_disposition}",
            "effect_id": effect_id,
            "logical_effect_id": logical_effect_id,
        }
    _simple_chain_regenerative_effect_state(
        run_state, effect_id, state="started", call_id=call_id,
        logical_effect_id=logical_effect_id, attempt_id=attempt_id, step_id=step_id,
    )
    try:
        raw = owner._jineng_zhixing(tool_name, tool_args, user_message, call_id=call_id)
    except Exception as exc:
        raw = {"ok": False, "error": str(exc), "error_code": type(exc).__name__}
    status = str(raw.get("status") or raw.get("zhuangtai") or "").strip().lower() if isinstance(raw, dict) else ""
    ambiguous = bool(isinstance(raw, dict) and raw.get("ambiguous_effect")) or status in {
        "ambiguous", "unknown", "deadline", "timeout", "timed_out"
    }
    outcome = "ambiguous" if ambiguous else "succeeded" if tool_result_ok(raw) else "failed_final"
    _simple_chain_regenerative_call(
        run_state,
        "append_event",
        event_key=f"step.observed:{step_id}:{attempt_id}",
        epoch_index=int(turn_loop.epoch_index),
        event_type="step.observed",
        payload={
            "status": status,
            "ok": bool(tool_result_ok(raw)),
            "result_digest": _simple_chain_regenerative_sha256({"result": str(raw)[:4000]}),
        },
        logical_effect_id=logical_effect_id,
        attempt_id=attempt_id,
        step_id=step_id,
        effect_id=effect_id,
    )
    finished = _simple_chain_regenerative_call(
        run_state,
        "finish_effect",
        epoch_index=int(turn_loop.epoch_index),
        effect_id=effect_id,
        logical_effect_id=logical_effect_id,
        attempt_id=attempt_id,
        step_id=step_id,
        outcome=outcome,
        error_code=(str(raw.get("error_code") or raw.get("error") or "")[:160] if isinstance(raw, dict) else ""),
        result_summary={
            "ok": bool(tool_result_ok(raw)),
            "status": status,
            "tool_name": tool_name,
            "call_id": call_id,
        },
    )
    final_effect_state = str((finished or {}).get("effect_state") or "")
    if outcome == "ambiguous" or final_effect_state == "AMBIGUOUS":
        _simple_chain_regenerative_effect_state(run_state, effect_id, state="ambiguous", call_id=call_id)
    else:
        _simple_chain_regenerative_effect_state(run_state, effect_id, state="terminal", call_id=call_id)
    if update_frontier:
        _simple_chain_regenerative_update_frontier(
            run_state,
            turn_loop,
            global_step=global_step,
            latest_safe_step=f"tool step {global_step} durably observed as {outcome}",
        )
    return raw


def _simple_chain_regenerative_checkpoint(
    run_state: dict[str, Any],
    turn_loop: TurnLoopState,
    *,
    source: str,
) -> bool:
    context = current_run_context()
    if not str(getattr(context, "outer_execution_ticket_id", "") or "").strip():
        return True
    state = _simple_chain_regenerative_state(run_state)
    canonical_capsule_id = str((run_state.get("continuation") or {}).get("canonical_capsule_id") or "").strip()
    if not canonical_capsule_id:
        return False
    try:
        frontier = _simple_chain_regenerative_update_frontier(
            run_state,
            turn_loop,
            latest_safe_step=f"epoch {turn_loop.epoch_index} is ready for regenerative checkpoint",
            next_action_hint="commit checkpoint then continue same Run in next Epoch",
        )
        result = _simple_chain_regenerative_call(
            run_state,
            "commit_checkpoint",
            frontier=frontier,
            continuity_capsule_id=canonical_capsule_id,
            recovery_preconditions=[
                "request/run/generation/life authority identity remains unchanged",
                "reconcile ambiguous effects before retry",
                "resume from committed Frontier and ledger head",
            ],
            critical_fact_status=str(state.get("critical_fact_status") or "verified"),
            runtime_version="tiangong-v3-p18-m2",
            provider_version="gateway-regenerative-provider-v1",
            model_version=str(MOREN_PROVIDER or "configured-model"),
            tool_contract_version="omni_body.v1",
            skill_contract_version="skill.v1",
            task_contract_version=str((run_state.get("task_contract") or {}).get("schema") or "task.v1"),
            semantic_handoff=json.dumps(_simple_chain_run_state_view(run_state), ensure_ascii=False, default=str)[:12000],
        )
    except Exception as exc:
        state["checkpoint_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return False
    if not isinstance(result, dict) or result.get("committed") is not True:
        state["checkpoint_error"] = str((result or {}).get("reason") or "regenerative_checkpoint_rejected")
        return False
    state["checkpoint_id"] = str(result.get("checkpoint_id") or "")
    state["checkpoint_hash"] = str(result.get("checkpoint_hash") or "")
    state["frontier_hash"] = str(result.get("frontier_hash") or state.get("frontier_hash") or "")
    state["checkpoint_source"] = source
    return True


def _simple_chain_regenerative_verify_completion(
    run_state: dict[str, Any],
    turn_loop: TurnLoopState,
    *,
    life_gate_allowed: bool,
    reasons: list[str],
    proposal_key: str,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    context = current_run_context()
    if not str(getattr(context, "outer_execution_ticket_id", "") or "").strip():
        return bool(life_gate_allowed), list(reasons), None
    try:
        _simple_chain_regenerative_update_frontier(
            run_state,
            turn_loop,
            latest_safe_step="completion proposal is bound to the latest durable execution frontier",
            next_action_hint="accept only if Runtime completion proof verifies every obligation",
        )
        result = _simple_chain_regenerative_call(
            run_state,
            "verify_completion",
            epoch_index=int(turn_loop.epoch_index),
            proposal_key=proposal_key,
            runtime_blockers=list(reasons),
            life_gate_allowed=bool(life_gate_allowed),
            required_evidence_ready=bool(life_gate_allowed and not reasons),
        )
    except Exception as exc:
        return False, list(dict.fromkeys([*reasons, f"completion_proof_failed:{type(exc).__name__}"])), None
    if not isinstance(result, dict):
        return False, list(dict.fromkeys([*reasons, "completion_proof_missing"])), None
    merged = list(dict.fromkeys([
        *[str(item) for item in reasons if str(item).strip()],
        *[str(item) for item in result.get("reasons", ()) if str(item).strip()],
    ]))[:32]
    return bool(life_gate_allowed and result.get("verified_complete") is True), merged, result

'''
# Place bridge helpers directly before Epoch checkpoint helper so it can call them.
zong = insert_before(
    zong,
    'def _simple_chain_checkpoint_continue(\n',
    regenerative_helpers,
    "zong regenerative bridge helpers",
)

# Canonical Continuity payload carries unresolved physical effects as well.
zong = replace_once(
    zong,
    '            "next_step": "resume the same authoritative Run in the next execution Epoch",\n',
    '            "next_step": "resume the same authoritative Run in the next execution Epoch",\n            "pending_effect_ids": list(dict.fromkeys([\n                *(_simple_chain_regenerative_state(run_state).get("pending_effect_ids") or []),\n                *(_simple_chain_regenerative_state(run_state).get("ambiguous_effect_ids") or []),\n            ])),\n',
    "zong continuity pending effect payload",
)

# No Epoch rollover until structured M2 checkpoint is committed.
zong = replace_once(
    zong,
    '    if run_state.get("persistence_degraded"):\n        return False\n    _simple_chain_emit_event(run_state, "epoch.checkpoint_committed", "epoch checkpoint persisted", source, extra=meta)\n',
    '    if run_state.get("persistence_degraded"):\n        return False\n    if not _simple_chain_regenerative_checkpoint(run_state, turn_loop, source=source):\n        run_state.setdefault("continuation", {})["status"] = "regenerative_checkpoint_failed"\n        _simple_chain_save_run_state(run_state)\n        return False\n    _simple_chain_emit_event(run_state, "epoch.checkpoint_committed", "epoch checkpoint persisted", source, extra=meta)\n',
    "zong checkpoint before rollover",
)

# Initialize immutable task/authority binding and recover if a known-good checkpoint exists.
zong = replace_once(
    zong,
    '        run_state["plan_version"] = run_state["task_contract"].get("plan_version")\n        try:\n',
    '        run_state["plan_version"] = run_state["task_contract"].get("plan_version")\n        _simple_chain_regenerative_initialize(run_state, xiaoxi)\n        try:\n',
    "zong regenerative initialize",
)
zong = replace_once(
    zong,
    '        turn_loop = TurnLoopState()\n        gongju_cishu = turn_loop.action_rounds\n',
    '        turn_loop = TurnLoopState()\n        _simple_chain_regenerative_restore_turn_loop(run_state, turn_loop)\n        gongju_cishu = turn_loop.action_rounds\n',
    "zong turn loop recovery",
)

# Single real dispatch goes through PREPARED -> STARTED -> actual handler -> terminal observation.
zong = replace_once(
    zong,
    '                    lambda: self._jineng_zhixing(tool_name, tool_args, xiaoxi, call_id=tool_call_id),\n',
    '                    lambda: _simple_chain_regenerative_execute_tool(\n                        self, run_state, turn_loop, tool_name=tool_name, tool_args=tool_args,\n                        user_message=xiaoxi, call_id=tool_call_id, global_step=gongju_cishu,\n                        attempted_action=attempted_action, update_frontier=True,\n                    ),\n',
    "single regenerative dispatch",
)

# Parallel read-only batch: Effect ledgers remain concurrent-safe, Frontier commits once after ordered collection.
zong = replace_once(
    zong,
    '                        raw = self._jineng_zhixing(tn, ta, xiaoxi, call_id=call_id)\n',
    '                        raw = _simple_chain_regenerative_execute_tool(\n                            self, run_state, turn_loop, tool_name=tn, tool_args=ta,\n                            user_message=xiaoxi, call_id=call_id, global_step=call_index,\n                            attempted_action=_simple_chain_tool_action(tn, ta), update_frontier=False,\n                        )\n',
    "parallel regenerative dispatch",
)
zong = replace_once(
    zong,
    '                combined = {\n                    "schema": "tiangong.v3.parallel_tool_results.v1",\n',
    '                _simple_chain_regenerative_update_frontier(\n                    run_state, turn_loop, global_step=gongju_cishu,\n                    latest_safe_step=f"parallel batch through global step {gongju_cishu} durably observed",\n                )\n                combined = {\n                    "schema": "tiangong.v3.parallel_tool_results.v1",\n',
    "parallel frontier commit",
)

# Bound live model history independently of the durable ledger.
zong = zong.replace(
    '                    quality_history.append(qp)\n',
    '                    quality_history.append(qp)\n                    _simple_chain_bound_history(quality_history, limit=24)\n',
)
zong = zong.replace(
    '            quality_history.append(quality_payload)\n',
    '            quality_history.append(quality_payload)\n            _simple_chain_bound_history(quality_history, limit=24)\n',
)
zong = zong.replace(
    '                quality_history.append(native_payload)\n',
    '                quality_history.append(native_payload)\n                _simple_chain_bound_history(quality_history, limit=24)\n',
)

# Mid-loop model completion proposal is only advisory until machine proof verifies it.
zong = replace_once(
    zong,
    '                    if final_allowed_now:\n                        final_chain_status = final_status_now\n                        break\n\n                    correction_state = _simple_chain_completion_correction_state(run_state)\n',
    '                    proof_allowed_now, proof_reasons_now, proof_now = _simple_chain_regenerative_verify_completion(\n                        run_state, turn_loop, life_gate_allowed=final_allowed_now,\n                        reasons=list(final_reasons_now or []),\n                        proposal_key=f"loop-{iteration_count}",\n                    )\n                    if isinstance(run_state, dict):\n                        run_state["completion_proof"] = proof_now or {}\n                        _simple_chain_save_run_state(run_state)\n                    if proof_allowed_now:\n                        final_chain_status = final_status_now\n                        break\n                    final_reasons_now = proof_reasons_now\n\n                    correction_state = _simple_chain_completion_correction_state(run_state)\n',
    "mid-loop completion proof",
)

# Final completion pass also requires the same proof.
zong = replace_once(
    zong,
    '            elif not final_allowed:\n                final_guard_exhausted = True\n',
    '            else:\n                proof_allowed, proof_reasons, proof_result = _simple_chain_regenerative_verify_completion(\n                    run_state, turn_loop, life_gate_allowed=final_allowed,\n                    reasons=list(final_reasons or []), proposal_key="final",\n                )\n                if isinstance(run_state, dict):\n                    run_state["completion_proof"] = proof_result or {}\n                    _simple_chain_save_run_state(run_state)\n                final_allowed = proof_allowed\n                final_reasons = proof_reasons\n            if not final_allowed:\n                final_guard_exhausted = True\n',
    "final completion proof",
)

zong_path.write_text(zong, encoding="utf-8", newline="\n")
