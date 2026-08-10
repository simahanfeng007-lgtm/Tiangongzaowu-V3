"""P10 read-only bridge from the current V3 run into WORLD_CONTEXT_SLOT.

This module is a consumer of World Understanding, not a new WU input.  All
context requests still enter through WorldUnderstandingFacade.accept().
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from contracts.canonical import canonical_sha256
from contracts.world_understanding.query import WorldQuery, derive_world_query_id
from world_understanding.context_output import (
    ContextOutputPort,
    WorldContextProjector,
    WorldContextRequestHandler,
    build_context_request_envelope,
    build_world_context_slot,
)
from world_understanding.facade import WorldUnderstandingFacade
from world_understanding.world_state.store import MaterializedWorldSnapshot, WorldStateStore

from .context_compactor import estimate_tokens

_log = logging.getLogger("tiangong.world_context")
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})
_DEFAULT_TOKEN_BUDGET = 2400


def world_understanding_enabled() -> bool:
    value = os.environ.get("TIANGONG_WORLD_UNDERSTANDING_ENABLED")
    if value is None or not value.strip():
        return True
    normalized = value.strip().lower()
    if normalized in _DISABLED_VALUES:
        return False
    return normalized in _ENABLED_VALUES


def _bounded_token_budget() -> int:
    raw = os.environ.get("TIANGONG_WORLD_CONTEXT_TOKEN_BUDGET", str(_DEFAULT_TOKEN_BUDGET)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_TOKEN_BUDGET
    return max(128, min(1_000_000, value))


def _state_root() -> Path:
    configured = os.environ.get("TIANGONG_WORLD_STATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (Path.home() / ".tiangong" / "v3" / "world_understanding" / "world_state").resolve(strict=False)


class WorldContextIntegration:
    """Synchronous, fail-open context consumer over the canonical WU ingress."""

    def __init__(
        self,
        *,
        store: WorldStateStore,
        token_budget: int = _DEFAULT_TOKEN_BUDGET,
        facade: WorldUnderstandingFacade | None = None,
        output_port: ContextOutputPort | None = None,
    ) -> None:
        self.store = store
        self.token_budget = max(128, min(1_000_000, int(token_budget)))
        if (facade is None) != (output_port is None):
            raise ValueError("WORLD_CONTEXT_SHARED_RUNTIME_BINDING_INCOMPLETE")
        if facade is not None and output_port is not None:
            self.output_port = output_port
            self.projector = None
            self.handler = None
            self.facade = facade
        else:
            self.output_port = ContextOutputPort(max_pending=256)
            self.projector = WorldContextProjector(token_estimator=estimate_tokens)
            self.handler = WorldContextRequestHandler(
                state_resolver=self._resolve_state,
                projector=self.projector,
                output_port=self.output_port,
            )
            self.facade = WorldUnderstandingFacade(enabled=True, context_request_handler=self.handler)

    def _resolve_state(self, query: WorldQuery) -> MaterializedWorldSnapshot | None:
        basis = query.basis_world_state_ref
        if basis is None or basis.record_type != "world_state":
            return None
        snapshot = self.store.get(basis.record_id)
        if snapshot is None:
            return None
        if snapshot.state_ref != basis or snapshot.state.scope != query.scope:
            return None
        if query.frame_ref is not None and snapshot.state.frame_ref != query.frame_ref:
            return None
        return snapshot

    def _select_current(self, run_context: Any) -> MaterializedWorldSnapshot | None:
        life_id = str(getattr(run_context, "life_id", "") or "").strip()
        principal = str(getattr(run_context, "principal_scope_hash", "") or "").strip()
        if not life_id or len(principal) != 64:
            return None
        candidates = self.store.current_candidates(life_id=life_id, principal_scope_hash=principal)
        # Never guess between branches/worktrees/world scopes. P9 frame identity is
        # part of the state stream; ambiguity means no context for this turn.
        return candidates[0] if len(candidates) == 1 else None

    def render_for_turn(self, *, run_context: Any, user_text: str, now_ms: int | None = None) -> str:
        snapshot = self._select_current(run_context)
        if snapshot is None:
            return ""
        text = str(user_text or "").strip()
        if not text:
            return ""
        created_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        task_sha256 = canonical_sha256({"current_user_text": text})
        identity = canonical_sha256({
            "request_id": str(getattr(run_context, "request_id", "") or ""),
            "run_id": str(getattr(run_context, "run_id", "") or ""),
            "generation": int(getattr(run_context, "generation", 0) or 0),
            "task_sha256": task_sha256,
            "created_at_ms": created_at_ms,
        })
        correlation_id = "wctx." + identity[:32]
        task_ref = "task." + task_sha256[:32]
        focus = text[:20_000]
        query_id = derive_world_query_id(
            world_scope_hash=snapshot.state.scope.world_scope_hash,
            correlation_id=correlation_id,
            task_ref=task_ref,
            task_sha256=task_sha256,
            focus=focus,
            created_at_ms=created_at_ms,
        )
        query = WorldQuery(
            query_id=query_id,
            correlation_id=correlation_id,
            scope=snapshot.state.scope,
            frame_ref=snapshot.state.frame_ref,
            basis_world_state_ref=snapshot.state_ref,
            task_ref=task_ref,
            task_sha256=task_sha256,
            focus=focus,
            required_refs=(),
            token_budget=self.token_budget,
            requested_depth="L0",
            created_at_ms=created_at_ms,
            query_sha256="0" * 64,
        ).with_computed_hash()
        envelope = build_context_request_envelope(
            query,
            run_id=str(getattr(run_context, "run_id", "") or "") or None,
            request_id=str(getattr(run_context, "request_id", "") or "") or None,
            session_id=str(getattr(run_context, "session_id", "") or "") or None,
            conversation_id=str(getattr(run_context, "conversation_id", "") or "") or None,
            workspace_id=str(getattr(run_context, "workspace_id", "") or "") or None,
        )
        receipt = self.facade.accept(envelope)
        if receipt.disposition != "ACCEPTED" or not receipt.processed:
            return ""
        emission = self.output_port.take(correlation_id)
        if emission is None or emission.query_id != query.query_id:
            return ""
        slot = build_world_context_slot(emission.packet, token_estimator=estimate_tokens)
        return "[WORLD_CONTEXT_SLOT]\n" + slot.rendered_text + "\n[/WORLD_CONTEXT_SLOT]"


_runtime_lock = threading.Lock()
_runtime: WorldContextIntegration | None = None


def _runtime_instance() -> WorldContextIntegration:
    global _runtime
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            from .world_understanding_production import (
                production_context_output_port,
                production_world_understanding_runtime,
            )
            production = production_world_understanding_runtime()
            _runtime = WorldContextIntegration(
                store=production.store,
                token_budget=_bounded_token_budget(),
                facade=production.facade,
                output_port=production_context_output_port(),
            )
        return _runtime


def render_world_context_slot_for_turn(*, run_context: Any, user_text: str) -> str:
    if not world_understanding_enabled():
        return ""
    try:
        return _runtime_instance().render_for_turn(run_context=run_context, user_text=user_text)
    except Exception as exc:
        # Context is optional and non-authorizing. Failure must preserve the exact
        # legacy V3 execution path rather than becoming a Runtime failure.
        _log.warning("WORLD_CONTEXT_SLOT unavailable: %s", str(exc)[:240])
        return ""


__all__ = [
    "WorldContextIntegration",
    "render_world_context_slot_for_turn",
    "world_understanding_enabled",
]
