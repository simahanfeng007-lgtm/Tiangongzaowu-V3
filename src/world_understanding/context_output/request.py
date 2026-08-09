"""CONTEXT_REQUEST builders/compiler. Control-only; never creates Known or Evidence."""
from __future__ import annotations

from contracts.canonical import canonical_sha256
from contracts.world_understanding.context_packet import ExpansionHandle
from contracts.world_understanding.ingress import (
    WorldIngressEnvelope,
    derive_ingress_dedup_key,
    derive_ingress_envelope_id,
)
from contracts.world_understanding.query import WorldQuery, derive_world_query_id
from contracts.world_understanding.time import WorldTime


def build_context_request_envelope(
    query: WorldQuery,
    *,
    producer_ref: str = "world.context-output.p10",
    run_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    workspace_id: str | None = None,
) -> WorldIngressEnvelope:
    payload = query.model_dump(mode="python")
    payload_sha256 = canonical_sha256(payload)
    dedup_key = derive_ingress_dedup_key(
        envelope_kind="CONTEXT_REQUEST",
        source_kind="CONTEXT_REQUEST",
        source_native_id=query.query_id,
        payload_sha256=payload_sha256,
        world_scope_hash=query.scope.world_scope_hash,
    )
    source_time = WorldTime(
        valid_from_ms=query.created_at_ms,
        valid_until_ms=None,
        observed_at_ms=None,
        recorded_at_ms=query.created_at_ms,
    )
    return WorldIngressEnvelope(
        envelope_id=derive_ingress_envelope_id(dedup_key=dedup_key),
        envelope_kind="CONTEXT_REQUEST",
        source_kind="CONTEXT_REQUEST",
        source_native_id=query.query_id,
        producer_ref=producer_ref,
        payload_inline=payload,
        payload_ref=None,
        payload_sha256=payload_sha256,
        source_time=source_time,
        life_id=query.scope.life_id,
        run_id=run_id,
        request_id=request_id,
        session_id=session_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        principal_scope_hash=query.scope.principal_scope_hash,
        scope_hint=query.scope,
        native_provenance_refs=(),
        native_authority_domain=None,
        observability_hint=None,
        integrity_ref=None,
        correlation_id=query.correlation_id,
        dedup_key=dedup_key,
    )


def compile_world_query(envelope: WorldIngressEnvelope) -> WorldQuery:
    if envelope.envelope_kind != "CONTEXT_REQUEST" or envelope.source_kind != "CONTEXT_REQUEST":
        raise ValueError("WORLD_QUERY_REQUIRES_CONTEXT_REQUEST")
    if envelope.payload_inline is None:
        raise ValueError("WORLD_QUERY_INLINE_PAYLOAD_REQUIRED")
    query = WorldQuery.model_validate(envelope.payload_inline)
    if query.scope != envelope.scope_hint:
        raise ValueError("WORLD_QUERY_SCOPE_MISMATCH")
    if query.correlation_id != envelope.correlation_id:
        raise ValueError("WORLD_QUERY_CORRELATION_MISMATCH")
    if not query.has_valid_hash():
        raise ValueError("WORLD_QUERY_HASH_INVALID")
    return query


def build_expansion_query(
    *,
    parent_query: WorldQuery,
    handle: ExpansionHandle,
    correlation_id: str,
    created_at_ms: int,
    token_budget: int | None = None,
) -> WorldQuery:
    if created_at_ms > handle.expires_at_ms:
        raise ValueError("WORLD_CONTEXT_EXPANSION_EXPIRED")
    if handle.scope_hash != parent_query.scope.world_scope_hash:
        raise ValueError("WORLD_CONTEXT_EXPANSION_SCOPE_MISMATCH")
    if handle.principal_scope_hash != parent_query.scope.principal_scope_hash:
        raise ValueError("WORLD_CONTEXT_EXPANSION_PRINCIPAL_MISMATCH")
    if handle.privacy_scope != parent_query.scope.privacy_scope:
        raise ValueError("WORLD_CONTEXT_EXPANSION_PRIVACY_MISMATCH")
    if not handle.has_valid_hash():
        raise ValueError("WORLD_CONTEXT_EXPANSION_HANDLE_HASH_INVALID")
    depth = handle.allowed_depth
    focus = f"Expand world context for {handle.handle_id} at {depth}."
    budget = parent_query.token_budget if token_budget is None else int(token_budget)
    query_id = derive_world_query_id(
        world_scope_hash=parent_query.scope.world_scope_hash,
        correlation_id=correlation_id,
        task_ref=parent_query.task_ref,
        task_sha256=parent_query.task_sha256,
        focus=focus,
        created_at_ms=created_at_ms,
    )
    query = WorldQuery(
        query_id=query_id,
        correlation_id=correlation_id,
        scope=parent_query.scope,
        frame_ref=parent_query.frame_ref,
        basis_world_state_ref=parent_query.basis_world_state_ref,
        task_ref=parent_query.task_ref,
        task_sha256=parent_query.task_sha256,
        focus=focus,
        required_refs=handle.target_refs,
        token_budget=budget,
        requested_depth=depth,
        created_at_ms=created_at_ms,
        query_sha256="0" * 64,
    )
    return query.with_computed_hash()


__all__ = ["build_context_request_envelope", "compile_world_query", "build_expansion_query"]
