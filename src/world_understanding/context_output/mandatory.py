"""Mandatory-first P10 L8 context construction."""
from __future__ import annotations

from contracts.world_understanding.context_packet import WorldContextItem
from contracts.world_understanding.query import WorldQuery
from world_understanding.world_state.store import MaterializedWorldSnapshot

from .projection_support import ProjectionPolicy, build_item, clip, state_ref


def build_mandatory_items(query: WorldQuery, snapshot: MaterializedWorldSnapshot, policy: ProjectionPolicy) -> list[WorldContextItem]:
    state = snapshot.state
    mandatory: list[WorldContextItem] = [
        build_item(kind="frame", summary=clip(
            f"Current frame {state.frame_ref.record_id}; frame revision {state.frame_ref.sha256[:16]}", policy.mandatory_summary_chars),
            refs=(state.frame_ref,), mandatory=True, task_relevance_milli=1000, impact_milli=1000, freshness_need_milli=1000),
        build_item(kind="task_focus", summary=clip(f"Task focus: {query.focus}", policy.mandatory_summary_chars),
            refs=(state_ref(snapshot),), mandatory=True, task_relevance_milli=1000, impact_milli=900, freshness_need_milli=800),
        build_item(kind="reasoning_constraints", summary=clip(
            "World context is context-only. STALE is not FALSE; CONFLICTED alternatives remain unresolved; hypotheses/predictions are not empirical facts.",
            policy.mandatory_summary_chars), refs=(state_ref(snapshot),), mandatory=True,
            task_relevance_milli=1000, impact_milli=1000, freshness_need_milli=1000),
        build_item(kind="current_state", summary=clip(
            f"Coherent WorldState sequence={state.world_sequence}; cut={snapshot.cut.cut_id}; stale={len(state.stale_refs)}; conflicts={len(state.unresolved_conflict_refs)}",
            policy.mandatory_summary_chars), refs=(state_ref(snapshot), state.world_cut_ref), mandatory=True,
            task_relevance_milli=1000, impact_milli=1000, freshness_need_milli=1000),
    ]
    if snapshot.delta.changed_source_keys or snapshot.delta.added_refs or snapshot.delta.removed_refs or snapshot.delta.changed_refs:
        mandatory.append(build_item(kind="current_delta", summary=clip(
            "Current delta: source_keys=" + ",".join(snapshot.delta.changed_source_keys[:12])
            + f"; added={len(snapshot.delta.added_refs)}; removed={len(snapshot.delta.removed_refs)}; changed={len(snapshot.delta.changed_refs)}",
            policy.mandatory_summary_chars), refs=(snapshot.delta.ref,), mandatory=True,
            task_relevance_milli=900, impact_milli=900, freshness_need_milli=1000))
    if state.unresolved_conflict_refs:
        mandatory.append(build_item(kind="active_conflicts", summary=clip(
            f"[CONFLICTED] {len(state.unresolved_conflict_refs)} unresolved world conflicts remain active.", policy.mandatory_summary_chars),
            refs=state.unresolved_conflict_refs, mandatory=True, task_relevance_milli=1000, impact_milli=1000,
            freshness_need_milli=1000, truth_state="CONFLICTED"))
    if state.stale_refs:
        mandatory.append(build_item(kind="stale_world_refs", summary=clip(
            f"[STALE] {len(state.stale_refs)} world records are stale and must not be treated as current truth.", policy.mandatory_summary_chars),
            refs=state.stale_refs, mandatory=True, task_relevance_milli=1000, impact_milli=950,
            freshness_need_milli=1000, epistemic_state="STALE"))
    if snapshot.uncertainty is not None and snapshot.uncertainty.refs:
        mandatory.append(build_item(kind="critical_uncertainty", summary=clip(
            f"[UNCERTAINTY] {len(snapshot.uncertainty.refs)} uncertainty records are active and must be preserved in reasoning.",
            policy.mandatory_summary_chars), refs=snapshot.uncertainty.refs, mandatory=True,
            task_relevance_milli=1000, impact_milli=950, freshness_need_milli=1000))
    return mandatory


__all__ = ["build_mandatory_items"]
