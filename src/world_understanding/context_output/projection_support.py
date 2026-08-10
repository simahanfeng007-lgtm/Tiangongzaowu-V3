"""Deterministic P10 projection policy, item factories, and bounded ranking helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.context_packet import ExpansionHandle, WorldContextItem, WorldContextPacket, derive_expansion_handle_id, derive_world_packet_id
from contracts.world_understanding.query import WorldQuery

from .enrichment import ContextProjectionCandidate


@dataclass(frozen=True, slots=True)
class ProjectionPolicy:
    policy_ref: str = "policy.world-context.p10.v1"
    mandatory_summary_chars: int = 800
    ranked_summary_chars: int = 480
    expansion_ttl_ms: int = 300_000
    max_ranked_items: int = 64
    max_digest_refs: int = 4096

    def __post_init__(self) -> None:
        if not 128 <= self.mandatory_summary_chars <= 4000:
            raise ValueError("WORLD_CONTEXT_POLICY_MANDATORY_CHARS_INVALID")
        if not 96 <= self.ranked_summary_chars <= 2000:
            raise ValueError("WORLD_CONTEXT_POLICY_RANKED_CHARS_INVALID")
        if not 1_000 <= self.expansion_ttl_ms <= 86_400_000:
            raise ValueError("WORLD_CONTEXT_POLICY_TTL_INVALID")
        if not 1 <= self.max_ranked_items <= 4096:
            raise ValueError("WORLD_CONTEXT_POLICY_RANKED_LIMIT_INVALID")
        if not 1 <= self.max_digest_refs <= 4096:
            raise ValueError("WORLD_CONTEXT_POLICY_DIGEST_LIMIT_INVALID")

    @property
    def sha256(self) -> str:
        return canonical_sha256({
            "domain": "tiangong.world.context-projection-policy.v1",
            "policy_ref": self.policy_ref,
            "mandatory_summary_chars": self.mandatory_summary_chars,
            "ranked_summary_chars": self.ranked_summary_chars,
            "expansion_ttl_ms": self.expansion_ttl_ms,
            "max_ranked_items": self.max_ranked_items,
            "max_digest_refs": self.max_digest_refs,
        })


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    packet: object
    estimated_tokens: int


def unique_refs(refs: Iterable[WorldRecordRef]) -> tuple[WorldRecordRef, ...]:
    by_key = {ref.sort_key(): ref for ref in refs}
    return tuple(by_key[key] for key in sorted(by_key))


def state_ref(snapshot: object) -> WorldRecordRef:
    return snapshot.state_ref


def clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 3)].rstrip() + "..."


def build_item(
    *, kind: str, summary: str, refs: tuple[WorldRecordRef, ...], mandatory: bool,
    task_relevance_milli: int, impact_milli: int, freshness_need_milli: int,
    truth_state: str | None = None, epistemic_state: str | None = None,
    cognition_stability: str | None = None, expansion_handle_id: str | None = None,
) -> WorldContextItem:
    refs = unique_refs(refs)
    item_id = "wci." + kind.replace("_", ".") + "." + canonical_sha256({
        "kind": kind, "summary": summary,
        "refs": [ref.model_dump(mode="json") for ref in refs], "mandatory": mandatory,
    })[:24]
    candidate = WorldContextItem(
        item_id=item_id, item_kind=kind, summary=summary, referenced_world_records=refs,
        truth_state=truth_state, epistemic_state=epistemic_state, cognition_stability=cognition_stability,
        task_relevance_milli=max(0, min(1000, int(task_relevance_milli))),
        impact_milli=max(0, min(1000, int(impact_milli))),
        freshness_need_milli=max(0, min(1000, int(freshness_need_milli))),
        mandatory=mandatory, expansion_handle_id=expansion_handle_id, item_sha256="0" * 64,
    )
    return candidate.with_computed_hash()


def build_handle(*, refs: tuple[WorldRecordRef, ...], query: WorldQuery, depth: str, expires_at_ms: int) -> ExpansionHandle:
    refs = unique_refs(refs)
    handle_id = derive_expansion_handle_id(
        target_refs=refs, allowed_depth=depth, scope_hash=query.scope.world_scope_hash,
        principal_scope_hash=query.scope.principal_scope_hash, privacy_scope=query.scope.privacy_scope,
        expires_at_ms=expires_at_ms,
    )
    candidate = ExpansionHandle(
        handle_id=handle_id, target_refs=refs, allowed_depth=depth,
        scope_hash=query.scope.world_scope_hash, principal_scope_hash=query.scope.principal_scope_hash,
        privacy_scope=query.scope.privacy_scope, expires_at_ms=expires_at_ms, handle_sha256="0" * 64,
    )
    return candidate.with_computed_hash()


def ref_priority(ref: WorldRecordRef, query: WorldQuery) -> int:
    required = any(ref.sort_key() == candidate.sort_key() for candidate in query.required_refs)
    focus = query.focus.lower()
    lexical_hit = ref.record_type.lower() in focus or ref.record_id.lower() in focus
    relevance = 1000 if required else (760 if lexical_hit else 520)
    impact = {"world_entity": 620, "world_relation": 580, "world_cognition": 820,
              "world_hypothesis": 560, "world_prediction": 600}.get(ref.record_type, 500)
    return 5 * relevance + 3 * impact + 2 * 650


def diversity_ref_order(pairs: tuple[tuple[str, WorldRecordRef], ...], *, query: WorldQuery, limit: int) -> tuple[tuple[str, WorldRecordRef], ...]:
    buckets: dict[str, list[tuple[str, WorldRecordRef]]] = {}
    for pair in pairs:
        buckets.setdefault(pair[1].record_type, []).append(pair)
    for values in buckets.values():
        values.sort(key=lambda pair: (-ref_priority(pair[1], query), pair[1].sort_key()))
    ordered: list[tuple[str, WorldRecordRef]] = []
    while len(ordered) < limit:
        active = [kind for kind, values in buckets.items() if values]
        if not active:
            break
        active.sort(key=lambda kind: (-ref_priority(buckets[kind][0][1], query), kind))
        for kind in active:
            if len(ordered) >= limit:
                break
            ordered.append(buckets[kind].pop(0))
    return tuple(ordered)


def _next_expansion(
    *, ref: WorldRecordRef, query: WorldQuery, generated_at_ms: int, policy: ProjectionPolicy
) -> ExpansionHandle | None:
    next_depth = {"L0": "L1", "L1": "L2", "L2": None}[query.requested_depth]
    return None if next_depth is None else build_handle(
        refs=(ref,), query=query, depth=next_depth, expires_at_ms=generated_at_ms + policy.expansion_ttl_ms
    )


def build_ranked_item(
    ref: WorldRecordRef, *, query: WorldQuery, source_keys: tuple[str, ...],
    evidence_ids: tuple[str, ...], generated_at_ms: int, policy: ProjectionPolicy,
    mandatory: bool = False,
) -> tuple[WorldContextItem, ExpansionHandle | None]:
    required = any(ref.sort_key() == candidate.sort_key() for candidate in query.required_refs)
    focus = query.focus.lower()
    lexical_hit = ref.record_type.lower() in focus or ref.record_id.lower() in focus
    relevance = 1000 if required else (760 if lexical_hit else 520)
    impact = {"world_entity": 620, "world_relation": 580, "world_cognition": 820,
              "world_hypothesis": 560, "world_prediction": 600}.get(ref.record_type, 500)
    expansion = _next_expansion(ref=ref, query=query, generated_at_ms=generated_at_ms, policy=policy)
    detail = f"{ref.record_type} {ref.record_id}"
    if query.requested_depth in {"L1", "L2"}:
        detail += f" revision={ref.revision or 0}; dependency_roots={len(source_keys)}"
    if query.requested_depth == "L2":
        detail += f"; sha256={ref.sha256}; evidence_roots={len(evidence_ids)}"
    context_item = build_item(
        kind=ref.record_type, summary=clip(detail, policy.ranked_summary_chars), refs=(ref,),
        mandatory=mandatory, task_relevance_milli=relevance, impact_milli=impact, freshness_need_milli=650,
        cognition_stability="stable" if ref.record_type == "world_cognition" else None,
        expansion_handle_id=None if expansion is None else expansion.handle_id)
    return context_item, expansion


def build_enriched_ranked_item(
    candidate: ContextProjectionCandidate,
    *,
    query: WorldQuery,
    generated_at_ms: int,
    policy: ProjectionPolicy,
) -> tuple[WorldContextItem, ExpansionHandle | None]:
    """Build one normal WorldContextItem using an internal summary/rank override."""
    expansion = _next_expansion(
        ref=candidate.ref, query=query, generated_at_ms=generated_at_ms, policy=policy
    )
    item = build_item(
        kind=candidate.item_kind,
        summary=clip(candidate.summary, policy.ranked_summary_chars),
        refs=(candidate.ref,),
        mandatory=False,
        task_relevance_milli=candidate.task_relevance_milli,
        impact_milli=candidate.impact_milli,
        freshness_need_milli=candidate.freshness_need_milli,
        expansion_handle_id=None if expansion is None else expansion.handle_id,
    )
    return item, expansion


def build_packet(
    *, query: WorldQuery, snapshot: object, generated_at_ms: int, policy: ProjectionPolicy,
    mandatory_items: tuple[WorldContextItem, ...], ranked_items: tuple[WorldContextItem, ...],
    uncertainty_items: tuple[WorldContextItem, ...], prediction_items: tuple[WorldContextItem, ...],
    evidence_digest: tuple[WorldRecordRef, ...], expansion_handles: tuple[ExpansionHandle, ...],
    overflow_state: str,
) -> WorldContextPacket:
    packet_id = derive_world_packet_id(
        world_scope_hash=query.scope.world_scope_hash, frame_ref=snapshot.state.frame_ref,
        basis_world_state_ref=state_ref(snapshot), task_ref=query.task_ref, task_sha256=query.task_sha256,
        generated_at_ms=generated_at_ms, projection_policy_sha256=policy.sha256)
    packet = WorldContextPacket(
        packet_id=packet_id, scope=query.scope, frame_ref=snapshot.state.frame_ref,
        basis_world_state_ref=state_ref(snapshot), task_ref=query.task_ref, task_sha256=query.task_sha256,
        generated_at_ms=generated_at_ms, token_budget=query.token_budget, mandatory_items=mandatory_items,
        ranked_items=ranked_items, uncertainty_items=uncertainty_items, prediction_items=prediction_items,
        evidence_digest=evidence_digest, expansion_handles=expansion_handles, overflow_state=overflow_state,
        projection_policy_ref=policy.policy_ref, projection_policy_sha256=policy.sha256, packet_sha256="0" * 64)
    return packet.with_computed_hash()


__all__ = [
    "ProjectionPolicy", "ProjectionResult", "unique_refs", "state_ref", "clip", "build_item", "build_handle",
    "diversity_ref_order", "build_ranked_item", "build_enriched_ranked_item", "build_packet"
]
