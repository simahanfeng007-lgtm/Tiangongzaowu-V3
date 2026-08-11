"""V3 composition root for the single production World Understanding runtime."""
from __future__ import annotations

import os
import platform
import re
import threading
from pathlib import Path

from contracts.canonical import canonical_sha256
from contracts.world_understanding.repository import RepositoryObservation, RepositoryRevision
from contracts.world_understanding.repository_query import (
    RepositoryGraphQuery,
    RepositoryGraphQueryResult,
)
from contracts.world_understanding.scope import (
    ScopeBinding,
    WorldScope,
    derive_world_id,
    derive_world_scope_hash,
)
from contracts.world_understanding.time import WorldTime
from world_understanding.post_commit import (
    NativePostCommitEvent,
    install_native_post_commit_observer,
)
from world_understanding.context_output import (
    ContextOutputPort,
    WorldContextProjector,
    WorldContextRequestHandler,
)
from world_understanding.production import ProductionWorldUnderstandingRuntime
from world_understanding.active_cognition import ActiveWorldCognitionCoordinator
from world_understanding.software_world import SoftwareWorldFrame
from world_understanding.software_world.git_observation import repository_frame_identity
from world_understanding.source_adapters import build_post_commit_source_envelope
from world_understanding.world_state import WorldStateStore

from .run_context import current_run_context
from .context_compactor import estimate_tokens

_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_runtime_lock = threading.Lock()
_runtime: ProductionWorldUnderstandingRuntime | None = None
_context_output: ContextOutputPort | None = None
_active_dispatcher = None
_active_coordinator: ActiveWorldCognitionCoordinator | None = None


def set_world_inquiry_dispatcher(dispatcher) -> None:
    """Bind the existing Total Gateway worker; never create a local worker."""
    if dispatcher is not None and not callable(dispatcher):
        raise TypeError("world inquiry dispatcher must be callable")
    global _active_dispatcher
    _active_dispatcher = dispatcher


def _dispatch_world_inquiry(inquiry, result_sink) -> bool:
    dispatcher = _active_dispatcher
    return False if dispatcher is None else bool(dispatcher(inquiry, result_sink))


def _state_root() -> Path:
    configured = os.environ.get("TIANGONG_WORLD_STATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return (Path.home() / ".tiangong" / "v3" / "world_understanding" / "world_state").resolve(strict=False)


def _identity(event: NativePostCommitEvent) -> dict[str, str]:
    context = current_run_context()
    supplied = {str(key): str(value or "").strip() for key, value in event.identity.items()}
    return {
        "life_id": supplied.get("life_id") or str(context.life_id or "").strip(),
        "principal_scope_hash": supplied.get("principal_scope_hash") or str(context.principal_scope_hash or "").strip(),
        "workspace_id": supplied.get("workspace_id") or str(context.workspace_id or "").strip(),
        "run_id": supplied.get("run_id") or str(context.run_id or "").strip(),
        "request_id": supplied.get("request_id") or str(context.request_id or "").strip(),
        "session_id": supplied.get("session_id") or str(context.session_id or "").strip(),
        "conversation_id": supplied.get("conversation_id") or str(context.conversation_id or "").strip(),
    }


def _current_identity() -> dict[str, str]:
    context = current_run_context()
    return {
        "life_id": str(context.life_id or "").strip(),
        "principal_scope_hash": str(context.principal_scope_hash or "").strip(),
        "workspace_id": str(context.workspace_id or "").strip(),
        "run_id": str(context.run_id or "").strip(),
        "request_id": str(context.request_id or "").strip(),
        "session_id": str(context.session_id or "").strip(),
        "conversation_id": str(context.conversation_id or "").strip(),
    }


def _scope(identity: dict[str, str]) -> WorldScope | None:
    life_id = identity["life_id"]
    principal = identity["principal_scope_hash"]
    workspace_id = identity["workspace_id"]
    if (
        not _OPAQUE.fullmatch(life_id)
        or not re.fullmatch(r"[0-9a-f]{64}", principal)
        or not _OPAQUE.fullmatch(workspace_id)
    ):
        return None
    bindings = (
        ScopeBinding(key="frame_kind", value="v3_runtime_workspace"),
        ScopeBinding(key="workspace_id", value=workspace_id),
    )
    world_id = derive_world_id(life_id=life_id, namespace_anchor="workspace:" + workspace_id)
    domain_id = "software_runtime"
    return WorldScope(
        life_id=life_id,
        world_id=world_id,
        domain_id=domain_id,
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id=life_id,
            world_id=world_id,
            domain_id=domain_id,
            scope_bindings=bindings,
        ),
        principal_scope_hash=principal,
        privacy_scope="system",
    )


def _frame_factory(envelope, cut):
    bindings = {item.key: item.value for item in envelope.scope_hint.scope_bindings}
    if bindings.get("frame_kind") != "v3_runtime_workspace":
        raise ValueError("WORLD_PRODUCTION_FRAME_KIND_UNSUPPORTED")
    workspace_id = str(bindings.get("workspace_id") or "").strip()
    if not workspace_id:
        raise ValueError("WORLD_PRODUCTION_FRAME_IDENTITY_INCOMPLETE")
    git_identity = repository_frame_identity(envelope)
    if git_identity is None:
        repository = "workspace:" + workspace_id
        worktree = "workspace:" + workspace_id
        branch = "runtime-current"
        commit = "runtime-current"
    else:
        repository, worktree, branch, commit = git_identity
    return SoftwareWorldFrame.build(
        scope=envelope.scope_hint,
        workspace=workspace_id,
        repository=repository,
        worktree=worktree,
        branch=branch,
        commit=commit,
        environment=platform.system().lower() or "unknown-platform",
        time=envelope.source_time,
        world_cut=cut,
    )


def production_world_understanding_runtime() -> ProductionWorldUnderstandingRuntime:
    global _runtime, _context_output, _active_coordinator
    if _runtime is not None:
        return _runtime
    with _runtime_lock:
        if _runtime is None:
            store = WorldStateStore(root=_state_root())
            output = ContextOutputPort(max_pending=256)

            def resolve_state(query):
                basis = query.basis_world_state_ref
                if basis is None or basis.record_type != "world_state":
                    return None
                snapshot = store.get(basis.record_id)
                if snapshot is None or snapshot.state_ref != basis or snapshot.state.scope != query.scope:
                    return None
                if query.frame_ref is not None and snapshot.state.frame_ref != query.frame_ref:
                    return None
                return snapshot

            def enrich_context(query, snapshot):
                runtime = _runtime
                if runtime is None:
                    return ()
                return runtime.repository_context_candidates(query, snapshot)

            handler = WorldContextRequestHandler(
                state_resolver=resolve_state,
                projector=WorldContextProjector(token_estimator=estimate_tokens),
                output_port=output,
                projection_enricher=enrich_context,
            )
            _active_coordinator = ActiveWorldCognitionCoordinator(
                store=store,
                dispatcher=_dispatch_world_inquiry,
            )
            _runtime = ProductionWorldUnderstandingRuntime(
                store=store,
                frame_factory=_frame_factory,
                context_request_handler=handler,
                committed_state_observer=_active_coordinator.observe,
            )
            _context_output = output
    return _runtime


def production_context_output_port() -> ContextOutputPort:
    production_world_understanding_runtime()
    assert _context_output is not None
    return _context_output


def production_repository_graph_query(
    query: RepositoryGraphQuery,
) -> RepositoryGraphQueryResult:
    """Use the one production WU runtime; never instantiate a query runtime."""
    return production_world_understanding_runtime().query_repository_graph(query)


def production_repository_evidence_snapshot(
    identity: dict[str, str],
) -> dict[str, object] | None:
    """Read bounded repository references for an exact Life/principal/workspace scope."""
    scope = _scope(identity)
    if scope is None:
        return None
    return production_world_understanding_runtime().repository_evidence_snapshot(
        scope=scope,
        max_entities=32,
    )


def production_repository_previous_revision(
    observation: RepositoryObservation,
) -> RepositoryRevision | None:
    """Resolve the last committed revision from the existing live WU frame.

    The lookup is exact on scope/repository/worktree/branch.  Branch switches
    therefore produce a different frame and intentionally return no baseline.
    No process-global repository revision cache is introduced.
    """
    scope = _scope(_current_identity())
    if scope is None:
        return None
    frame = production_world_understanding_runtime().live_repository_frame(
        scope=scope,
        repository=observation.identity.repository_id,
        worktree=observation.identity.worktree_id,
        branch=observation.revision.branch,
    )
    if frame is None:
        return None
    return RepositoryRevision(
        branch=frame.branch,
        head_commit=frame.commit,
        parent_commit=None,
        detached_head=frame.branch.startswith("detached:"),
        observed_at_ms=max(0, int(frame.time.observed_at_ms)),
    )


def _native_id(value: str) -> str:
    value = str(value or "").strip()
    if _OPAQUE.fullmatch(value):
        return value
    return "native." + canonical_sha256({"value": value})[:48]


def _tool_result_requests_repository_refresh(event: NativePostCommitEvent) -> bool:
    if event.source_kind != "TOOL_RESULT":
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    evidence = payload.get("write_evidence")
    if payload.get("observed_write_effect") is not True or not isinstance(evidence, dict):
        return False
    if evidence.get("authoritative") is not True:
        return False
    changed = evidence.get("changed_files") or ()
    deleted = evidence.get("deleted_files") or ()
    return bool(changed or deleted)


def observe_native_post_commit(event: NativePostCommitEvent):
    identity = _identity(event)
    scope = _scope(identity)
    if scope is None:
        return None
    recorded_at = max(0, int(event.occurred_at_ms))
    envelope = build_post_commit_source_envelope(
        source_kind=event.source_kind,
        source_native_id=_native_id(event.source_native_id),
        producer_ref=_native_id(event.producer_ref),
        payload=dict(event.payload),
        source_time=WorldTime(
            valid_from_ms=recorded_at,
            observed_at_ms=recorded_at,
            recorded_at_ms=recorded_at,
        ),
        scope=scope,
        correlation_id="corr." + canonical_sha256({
            "source_kind": event.source_kind,
            "source_native_id": event.source_native_id,
            "request_id": identity["request_id"],
            "run_id": identity["run_id"],
        })[:32],
        run_id=identity["run_id"] or None,
        request_id=identity["request_id"] or None,
        session_id=identity["session_id"] or None,
        conversation_id=identity["conversation_id"] or None,
        workspace_id=identity["workspace_id"],
    )
    disposition = production_world_understanding_runtime().facade.accept(envelope)
    if _tool_result_requests_repository_refresh(event):
        try:
            from .repository_perception import publish_active_repository_observation

            publish_active_repository_observation()
        except Exception:
            pass
    return disposition


def install_world_understanding_observer() -> None:
    install_native_post_commit_observer(observe_native_post_commit)


__all__ = [
    "install_world_understanding_observer",
    "observe_native_post_commit",
    "production_context_output_port",
    "production_repository_graph_query",
    "production_repository_evidence_snapshot",
    "production_repository_previous_revision",
    "production_world_understanding_runtime",
    "set_world_inquiry_dispatcher",
]
