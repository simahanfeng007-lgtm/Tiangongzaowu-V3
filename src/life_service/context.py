"""Fail-closed causal context selection and 75/85/92 compaction policy."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Callable

from contracts import (
    CausalContextEdge,
    CausalContextItem,
    CausalContextPack,
    ContextTokenBudget,
    MemoryAssertionV3,
    TaskContinuityCapsule,
    canonical_json_bytes,
    canonical_sha256,
    retention_priority,
)

from .store import LifeShadowStore, LifeShadowStoreError


TokenCounter = Callable[[str], int]
SummaryProvider = Callable[[MemoryAssertionV3, str], str]


class ContextBuildError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ContextProjectionResult:
    active_pack: CausalContextPack | None
    candidate_pack: CausalContextPack | None
    persisted: bool
    replaced_previous: bool
    reason_code: str


def conservative_token_count(value: str) -> int:
    """Safe fallback for byte-level tokenizers: one UTF-8 byte per token."""

    if not isinstance(value, str):
        raise TypeError("token counter input must be text")
    return max(1, len(value.encode("utf-8")))


def build_token_budget(
    *,
    model_context_limit_tokens: int,
    current_context_tokens: int,
    product_limit_tokens: int = 120_000,
    output_reserve_tokens: int = 20_000,
    tool_schema_reserve_tokens: int = 10_000,
    authority_reserve_tokens: int = 5_000,
    protocol_reserve_tokens: int = 5_000,
) -> ContextTokenBudget:
    values = (
        model_context_limit_tokens,
        current_context_tokens,
        product_limit_tokens,
        output_reserve_tokens,
        tool_schema_reserve_tokens,
        authority_reserve_tokens,
        protocol_reserve_tokens,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ContextBuildError("context token budget requires exact integers")
    if current_context_tokens < 0 or any(value < 0 for value in values[2:]):
        raise ContextBuildError("context token budget cannot be negative")
    reserve = sum(values[3:])
    usable = min(product_limit_tokens, model_context_limit_tokens - reserve)
    if usable < 1:
        raise ContextBuildError("context reserves consume the entire model window")
    utilization = min(1000, (current_context_tokens * 1000) // usable)
    watermark = (
        "BELOW_75"
        if utilization < 750
        else "CANDIDATE_75"
        if utilization < 850
        else "MUST_PERSIST_85"
        if utilization < 920
        else "MUST_SWITCH_92"
    )
    return ContextTokenBudget(
        model_context_limit_tokens=model_context_limit_tokens,
        product_limit_tokens=product_limit_tokens,
        output_reserve_tokens=output_reserve_tokens,
        tool_schema_reserve_tokens=tool_schema_reserve_tokens,
        authority_reserve_tokens=authority_reserve_tokens,
        protocol_reserve_tokens=protocol_reserve_tokens,
        usable_budget_tokens=usable,
        current_context_tokens=current_context_tokens,
        utilization_milli=utilization,
        watermark=watermark,
    )


def _default_summary(_assertion: MemoryAssertionV3, plaintext: str) -> str:
    if len(plaintext) <= 20_000:
        return plaintext
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    marker = f"\n...[protected summary omitted; sha256={digest}]...\n"
    remaining = 20_000 - len(marker)
    return plaintext[: remaining // 2] + marker + plaintext[-(remaining - remaining // 2) :]


def _item_kind(assertion: MemoryAssertionV3) -> str:
    if assertion.assertion_kind == "goal":
        return "goal"
    if assertion.assertion_kind == "hard_constraint":
        return "constraint"
    return "memory"


class CausalContextBuilder:
    """Build a bounded shadow projection without upgrading causal certainty."""

    def __init__(
        self,
        store: LifeShadowStore,
        *,
        token_counter: TokenCounter = conservative_token_count,
        summary_provider: SummaryProvider = _default_summary,
        max_graph_hops: int = 3,
        max_candidates: int = 4096,
    ) -> None:
        if not 0 <= max_graph_hops <= 16 or not 1 <= max_candidates <= 4096:
            raise ValueError("causal context graph bound is invalid")
        self.store = store
        self.token_counter = token_counter
        self.summary_provider = summary_provider
        self.max_graph_hops = max_graph_hops
        self.max_candidates = max_candidates

    def _count(self, value: str) -> int:
        count = self.token_counter(value)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ContextBuildError("tokenizer returned an invalid exact count")
        return count

    def _neighborhood(
        self,
        assertions: tuple[MemoryAssertionV3, ...],
        seed_refs: tuple[str, ...],
    ) -> dict[str, int]:
        known = {assertion.memory_id for assertion in assertions}
        adjacency: dict[str, set[str]] = {memory_id: set() for memory_id in known}
        for relation in self.store.list_memory_relations(assertions[0].life_id if assertions else ""):
            if relation.source_memory_id in known and relation.target_ref in known:
                adjacency[relation.source_memory_id].add(relation.target_ref)
                adjacency[relation.target_ref].add(relation.source_memory_id)
        if assertions:
            for edge in self.store.list_latest_causal_hypotheses(assertions[0].life_id):
                if edge.cause_ref in known and edge.effect_ref in known:
                    adjacency[edge.cause_ref].add(edge.effect_ref)
                    adjacency[edge.effect_ref].add(edge.cause_ref)
        roots = tuple(sorted(set(seed_refs) & known))
        distance: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque((root, 0) for root in roots)
        while queue and len(distance) < self.max_candidates:
            ref, hops = queue.popleft()
            if ref in distance or hops > self.max_graph_hops:
                continue
            distance[ref] = hops
            if hops < self.max_graph_hops:
                queue.extend((neighbor, hops + 1) for neighbor in sorted(adjacency[ref]))
        return distance

    def build(
        self,
        continuity: TaskContinuityCapsule,
        *,
        current_context_tokens: int,
        created_at_ms: int,
        seed_refs: tuple[str, ...] = (),
        external_items: tuple[CausalContextItem, ...] = (),
        model_context_limit_tokens: int = 160_000,
        product_limit_tokens: int = 120_000,
        output_reserve_tokens: int = 20_000,
        tool_schema_reserve_tokens: int = 10_000,
        authority_reserve_tokens: int = 5_000,
        protocol_reserve_tokens: int = 5_000,
    ) -> CausalContextPack:
        if not continuity.has_valid_capsule_sha256():
            raise ContextBuildError("continuity capsule digest is invalid")
        if created_at_ms < continuity.created_at_ms:
            raise ContextBuildError("context pack predates its continuity capsule")
        budget = build_token_budget(
            model_context_limit_tokens=model_context_limit_tokens,
            current_context_tokens=current_context_tokens,
            product_limit_tokens=product_limit_tokens,
            output_reserve_tokens=output_reserve_tokens,
            tool_schema_reserve_tokens=tool_schema_reserve_tokens,
            authority_reserve_tokens=authority_reserve_tokens,
            protocol_reserve_tokens=protocol_reserve_tokens,
        )
        continuity_tokens = self._count(
            canonical_json_bytes(continuity).decode("utf-8")
        )
        if continuity_tokens > budget.usable_budget_tokens:
            raise ContextBuildError("hard continuity state exceeds the usable context budget")

        if (
            tuple(item.item_ref for item in external_items)
            != tuple(sorted({item.item_ref for item in external_items}))
            or any(item.token_count != self._count(item.summary) for item in external_items)
        ):
            raise ContextBuildError("external context items are invalid")
        used = continuity_tokens
        selected: list[CausalContextItem] = []
        selected_refs: set[str] = set()
        for item in external_items:
            if used + item.token_count > budget.usable_budget_tokens:
                raise ContextBuildError("external continuity state exceeds the context budget")
            selected.append(item)
            selected_refs.add(item.item_ref)
            used += item.token_count

        assertions = self.store.list_latest_memory_assertions(continuity.life_id)
        required_ids = {
            assertion.memory_id
            for assertion in assertions
            if assertion.assertion_kind in {"goal", "hard_constraint"}
        }
        normalized_seeds = tuple(
            sorted(set(seed_refs) | required_ids | set(continuity.verified_fact_ids))
        )
        distances = self._neighborhood(assertions, normalized_seeds)
        fallback = tuple(
            assertion.memory_id
            for assertion in sorted(
                assertions,
                key=lambda value: (-retention_priority(value), value.memory_id),
            )[: min(128, self.max_candidates)]
        )
        candidate_ids = set(distances) | required_ids | set(fallback)
        candidates = tuple(
            assertion
            for assertion in assertions
            if assertion.memory_id in candidate_ids
        )
        candidates = tuple(
            sorted(
                candidates,
                key=lambda value: (
                    0 if value.assertion_kind == "goal" else 1
                    if value.assertion_kind == "hard_constraint"
                    else 2,
                    distances.get(value.memory_id, self.max_graph_hops + 1),
                    -retention_priority(value),
                    value.memory_id,
                ),
            )[: self.max_candidates]
        )

        for assertion in candidates:
            assert assertion.protected_payload_id is not None
            try:
                plaintext = self.store.read_protected_payload(
                    assertion.protected_payload_id
                ).decode("utf-8", errors="strict")
            except (UnicodeDecodeError, LifeShadowStoreError) as exc:
                if assertion.memory_id in required_ids:
                    raise ContextBuildError(
                        "required memory cannot be read from protected storage"
                    ) from exc
                continue
            summary = self.summary_provider(assertion, plaintext)
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 20_000:
                raise ContextBuildError("memory summarizer returned an invalid summary")
            item_tokens = self._count(summary)
            priority = max(-3_000, min(5_000, retention_priority(assertion)))
            item = CausalContextItem(
                item_ref=assertion.memory_id,
                item_kind=_item_kind(assertion),
                source_revision=assertion.revision,
                summary=summary,
                epistemic_status=assertion.epistemic_status,
                confidence_milli=assertion.verification_strength_milli,
                priority=priority,
                privacy_scope=assertion.privacy_scope,
                token_count=item_tokens,
                supporting_event_ids=assertion.source_event_ids,
            )
            if used + item_tokens > budget.usable_budget_tokens:
                if assertion.memory_id in required_ids:
                    raise ContextBuildError(
                        "required goal or hard constraint exceeds the context budget"
                    )
                continue
            if item.item_ref in selected_refs:
                raise ContextBuildError("context item identity collision")
            selected.append(item)
            selected_refs.add(assertion.memory_id)
            used += item_tokens

        edges: list[CausalContextEdge] = []
        for hypothesis in self.store.list_latest_causal_hypotheses(continuity.life_id):
            if (
                hypothesis.cause_ref not in selected_refs
                or hypothesis.effect_ref not in selected_refs
            ):
                continue
            edge = CausalContextEdge(
                hypothesis_id=hypothesis.hypothesis_id,
                revision=hypothesis.revision,
                cause_ref=hypothesis.cause_ref,
                effect_ref=hypothesis.effect_ref,
                relation=hypothesis.relation,
                causal_basis=hypothesis.causal_basis,
                status=hypothesis.status,
                confidence_milli=hypothesis.confidence_milli,
                supporting_event_ids=hypothesis.supporting_event_ids,
                counterevidence_event_ids=hypothesis.counterevidence_event_ids,
            )
            edge_tokens = self._count(canonical_json_bytes(edge).decode("utf-8"))
            if used + edge_tokens <= budget.usable_budget_tokens:
                edges.append(edge)
                used += edge_tokens

        selected_tuple = tuple(sorted(selected, key=lambda item: item.item_ref))
        edge_tuple = tuple(
            sorted(edges, key=lambda edge: (edge.hypothesis_id, edge.revision))
        )
        identity = {
            "domain": "tiangong.life.causal-context-pack.v1",
            "capsule_sha256": continuity.capsule_sha256,
            "seed_refs": normalized_seeds,
            "item_versions": tuple(
                (item.item_ref, item.source_revision) for item in selected_tuple
            ),
            "edge_versions": tuple(
                (edge.hypothesis_id, edge.revision) for edge in edge_tuple
            ),
            "token_budget": budget.model_dump(mode="json"),
            "created_at_ms": created_at_ms,
        }
        return CausalContextPack(
            pack_id="ccp_" + canonical_sha256(identity),
            life_id=continuity.life_id,
            continuity=continuity,
            seed_refs=normalized_seeds,
            items=selected_tuple,
            edges=edge_tuple,
            token_budget=budget,
            selected_token_count=used,
            omitted_item_count=max(
                0,
                len(assertions)
                - len(
                    selected_refs
                    & {assertion.memory_id for assertion in assertions}
                ),
            ),
            visible_raw_tool_process_count=0,
            integrity_status="VERIFIED",
            model_input_switched=False,
            created_at_ms=created_at_ms,
            pack_sha256="0" * 64,
        ).with_computed_pack_sha256()

    def persist_verified(
        self,
        candidate: CausalContextPack,
        *,
        privacy_scope: str,
        previous_verified_pack: CausalContextPack | None,
    ) -> ContextProjectionResult:
        try:
            record = self.store.put_causal_context_pack(
                candidate, privacy_scope=privacy_scope
            )
            verified = self.store.read_causal_context_pack(candidate.pack_id)
            if verified != candidate:
                raise LifeShadowStoreError("causal context readback is not exact")
        except Exception:
            return ContextProjectionResult(
                active_pack=previous_verified_pack,
                candidate_pack=candidate,
                persisted=False,
                replaced_previous=False,
                reason_code="context.integrity_rejected",
            )
        return ContextProjectionResult(
            active_pack=verified,
            candidate_pack=candidate,
            persisted=record.created_by_this_call,
            replaced_previous=(
                previous_verified_pack is not None
                and previous_verified_pack.pack_id != verified.pack_id
            ),
            reason_code="context.verified_shadow_projection",
        )


__all__ = [
    "CausalContextBuilder",
    "ContextBuildError",
    "ContextProjectionResult",
    "TokenCounter",
    "build_token_budget",
    "conservative_token_count",
]
