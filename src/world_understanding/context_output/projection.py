"""P10 L8 reference-only WorldContextPacket projection from a coherent P9 snapshot."""
from __future__ import annotations

from typing import Callable

from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.context_packet import ExpansionHandle, WorldContextItem, WorldContextPacket
from contracts.world_understanding.query import WorldQuery
from world_understanding.common.scope import require_exact_scope
from world_understanding.world_state.store import MaterializedWorldSnapshot

from .mandatory import build_mandatory_items
from .projection_support import (
    ProjectionPolicy, ProjectionResult, build_packet, build_ranked_item, diversity_ref_order, state_ref, unique_refs,
)
from .slot import conservative_token_estimate, render_world_context_packet

class WorldContextProjector:
    def __init__(
        self,
        *,
        policy: ProjectionPolicy | None = None,
        token_estimator: Callable[[str], int] = conservative_token_estimate,
    ) -> None:
        self.policy = policy or ProjectionPolicy()
        self.token_estimator = token_estimator

    def project(
        self,
        query: WorldQuery,
        snapshot: MaterializedWorldSnapshot,
        *,
        generated_at_ms: int | None = None,
        prediction_refs: tuple[WorldRecordRef, ...] = (),
    ) -> ProjectionResult:
        if not query.has_valid_hash():
            raise ValueError("WORLD_QUERY_HASH_INVALID")
        require_exact_scope(query.scope, snapshot.state.scope)
        if query.frame_ref is not None and query.frame_ref != snapshot.state.frame_ref:
            raise ValueError("WORLD_QUERY_FRAME_MISMATCH")
        if query.basis_world_state_ref is not None and query.basis_world_state_ref != state_ref(snapshot):
            raise ValueError("WORLD_QUERY_WORLD_STATE_MISMATCH")
        now_ms = query.created_at_ms if generated_at_ms is None else int(generated_at_ms)
        if now_ms < query.created_at_ms:
            raise ValueError("WORLD_CONTEXT_GENERATED_BEFORE_QUERY")

        mandatory = build_mandatory_items(query, snapshot, self.policy)

        manifests = [snapshot.entity_heads, snapshot.relation_heads]
        if snapshot.cognition_heads is not None:
            manifests.append(snapshot.cognition_heads)
        if snapshot.active_hypotheses is not None:
            manifests.append(snapshot.active_hypotheses)
        all_ranked_refs = unique_refs(ref for manifest in manifests for ref in manifest.refs)

        prediction_refs = unique_refs(prediction_refs)
        if any(ref.record_type != "world_prediction" for ref in prediction_refs):
            raise ValueError("WORLD_CONTEXT_PREDICTION_REF_INVALID")

        dependency_map = {binding.ref.sort_key(): binding for binding in snapshot.dependencies.bindings}
        required_keys = {ref.sort_key() for ref in query.required_refs}
        mandatory_ref_keys = {
            ref.sort_key() for item in mandatory for ref in item.referenced_world_records
        }
        available_keys = mandatory_ref_keys | {ref.sort_key() for ref in all_ranked_refs} | {
            ref.sort_key() for ref in prediction_refs
        }
        if required_keys - available_keys:
            raise ValueError("WORLD_CONTEXT_REQUIRED_REF_UNAVAILABLE")

        mandatory_handles: list[ExpansionHandle] = []
        optional_ref_pairs: list[tuple[str, WorldRecordRef]] = []
        for ref in all_ranked_refs:
            if ref.sort_key() in required_keys:
                binding = dependency_map.get(ref.sort_key())
                item, handle = build_ranked_item(
                    ref, query=query,
                    source_keys=() if binding is None else binding.source_keys,
                    evidence_ids=() if binding is None else binding.evidence_ids,
                    generated_at_ms=now_ms, policy=self.policy, mandatory=True,
                )
                mandatory.append(item)
                if handle is not None:
                    mandatory_handles.append(handle)
            else:
                optional_ref_pairs.append(("ranked", ref))

        prediction_focus = "predict" in query.focus.lower() or "预测" in query.focus
        for ref in prediction_refs:
            if ref.sort_key() in required_keys:
                item, handle = build_ranked_item(
                    ref, query=query, source_keys=(), evidence_ids=(),
                    generated_at_ms=now_ms, policy=self.policy, mandatory=True,
                )
                mandatory.append(item)
                if handle is not None:
                    mandatory_handles.append(handle)
            elif prediction_focus:
                optional_ref_pairs.append(("prediction", ref))

        # Preselect refs cheaply before constructing hashed items/handles. This keeps
        # projection latency bounded for large sparse graphs while preserving one-
        # per-kind diversity before repeats.
        chosen_refs = diversity_ref_order(
            tuple(optional_ref_pairs), query=query, limit=self.policy.max_ranked_items
        )
        optional_pairs: list[tuple[str, WorldContextItem, ExpansionHandle | None]] = []
        for group, ref in chosen_refs:
            binding = dependency_map.get(ref.sort_key())
            item, handle = build_ranked_item(
                ref, query=query,
                source_keys=() if binding is None else binding.source_keys,
                evidence_ids=() if binding is None else binding.evidence_ids,
                generated_at_ms=now_ms, policy=self.policy, mandatory=False,
            )
            optional_pairs.append((group, item, handle))

        mandatory_tuple = tuple(mandatory)

        def _digest(*groups: tuple[WorldContextItem, ...]) -> tuple[WorldRecordRef, ...]:
            refs = unique_refs(
                ref for group in groups for item in group for ref in item.referenced_world_records
            )
            if len(refs) > self.policy.max_digest_refs:
                raise ValueError("WORLD_CONTEXT_EVIDENCE_DIGEST_LIMIT")
            return refs

        # Mandatory facts are never silently squeezed. If mandatory material alone
        # exceeds the requested budget, the packet advertises MANDATORY_OVERFLOW.
        mandatory_digest = _digest(mandatory_tuple)
        base_packet = build_packet(
            query=query, snapshot=snapshot, generated_at_ms=now_ms, policy=self.policy,
            mandatory_items=mandatory_tuple, ranked_items=(),
            uncertainty_items=(), prediction_items=(),
            evidence_digest=mandatory_digest, expansion_handles=tuple(mandatory_handles), overflow_state="NONE",
        )
        base_tokens = max(0, int(self.token_estimator(render_world_context_packet(base_packet))))
        if base_tokens > query.token_budget:
            overflow = build_packet(
                query=query, snapshot=snapshot, generated_at_ms=now_ms, policy=self.policy,
                mandatory_items=mandatory_tuple, ranked_items=(),
                uncertainty_items=(), prediction_items=(),
                evidence_digest=mandatory_digest, expansion_handles=tuple(mandatory_handles),
                overflow_state="MANDATORY_OVERFLOW",
            )
            tokens = max(0, int(self.token_estimator(render_world_context_packet(overflow))))
            return ProjectionResult(overflow, tokens)

        ordered_pairs = tuple(optional_pairs)

        selected_ranked: list[WorldContextItem] = []
        selected_predictions: list[WorldContextItem] = []
        selected_handles: list[ExpansionHandle] = list(mandatory_handles)
        accepted_pairs: list[tuple[str, WorldContextItem, ExpansionHandle | None]] = []
        truncated = False
        for group, item, handle in ordered_pairs:
            trial_ranked = tuple((*selected_ranked, item)) if group == "ranked" else tuple(selected_ranked)
            trial_predictions = tuple((*selected_predictions, item)) if group == "prediction" else tuple(selected_predictions)
            trial_handles = tuple((*selected_handles, *((handle,) if handle is not None else ())))
            trial_digest = _digest(mandatory_tuple, trial_ranked, trial_predictions)
            trial = build_packet(
                query=query, snapshot=snapshot, generated_at_ms=now_ms, policy=self.policy,
                mandatory_items=mandatory_tuple, ranked_items=trial_ranked,
                uncertainty_items=(), prediction_items=trial_predictions,
                evidence_digest=trial_digest, expansion_handles=trial_handles,
                overflow_state="NONE",
            )
            trial_tokens = max(0, int(self.token_estimator(render_world_context_packet(trial))))
            if trial_tokens <= query.token_budget:
                if group == "ranked":
                    selected_ranked.append(item)
                else:
                    selected_predictions.append(item)
                if handle is not None:
                    selected_handles.append(handle)
                accepted_pairs.append((group, item, handle))
            else:
                truncated = True

        def _final_packet() -> tuple[WorldContextPacket, int]:
            ranked_tuple = tuple(selected_ranked)
            prediction_tuple = tuple(selected_predictions)
            digest = _digest(mandatory_tuple, ranked_tuple, prediction_tuple)
            value = build_packet(
                query=query, snapshot=snapshot, generated_at_ms=now_ms, policy=self.policy,
                mandatory_items=mandatory_tuple, ranked_items=ranked_tuple,
                uncertainty_items=(), prediction_items=prediction_tuple,
                evidence_digest=digest, expansion_handles=tuple(selected_handles),
                overflow_state="BUDGET_TRUNCATED" if truncated else "NONE",
            )
            tokens = max(0, int(self.token_estimator(render_world_context_packet(value))))
            return value, tokens

        packet, estimated = _final_packet()
        # Changing NONE -> BUDGET_TRUNCATED slightly lengthens the rendered header.
        # If that boundary pushes the final rendering over budget, remove only the
        # last accepted optional item(s); mandatory context is never squeezed.
        while estimated > query.token_budget and accepted_pairs:
            truncated = True
            group, item, handle = accepted_pairs.pop()
            if group == "ranked":
                selected_ranked.remove(item)
            else:
                selected_predictions.remove(item)
            if handle is not None and handle in selected_handles:
                selected_handles.remove(handle)
            packet, estimated = _final_packet()
        if estimated > query.token_budget:
            raise ValueError("WORLD_CONTEXT_PACKET_BUDGET_INVARIANT")
        return ProjectionResult(packet, estimated)


__all__ = ["ProjectionPolicy", "ProjectionResult", "WorldContextProjector"]
