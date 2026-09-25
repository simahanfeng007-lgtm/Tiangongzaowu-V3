"""Readable, bounded DATA from bodies already bound to this WorldState."""
from __future__ import annotations

import json

from contracts.world_understanding._base import WorldRecordRef
from .enrichment import ContextProjectionCandidate


def _data(value) -> str:
    # Source names and model claims cannot break out of the context delimiter.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("[", "\\u005b").replace("]", "\\u005d").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def runtime_context_candidates(query, snapshot):
    if snapshot.state.scope != query.scope or snapshot.state_ref != query.basis_world_state_ref:
        return ()
    stale = {r.sort_key() for r in (*snapshot.state.stale_refs, *snapshot.state.unresolved_conflict_refs)}
    heads = {r.sort_key() for r in snapshot.entity_heads.refs}
    candidates = []
    for entity in snapshot.entities:
        ref = WorldRecordRef(record_type="world_entity", record_id=entity.entity_id,
                             revision=entity.revision, sha256=entity.entity_sha256)
        if ref.sort_key() not in heads or ref.sort_key() in stale or not entity.has_valid_hash():
            continue
        attrs = {a.key: a.value.string_value for a in entity.attributes if a.value.kind == "string"}
        if entity.entity_type in {"SkillMethod", "ToolCapability"}:
            continue
        label = {"type": entity.entity_type, "name": entity.canonical_name,
                 "truth": entity.truth_state, "epistemic": entity.epistemic_state}
        for key in ("path", "action_id", "availability", "semantic_summary", "observed_status", "required_dependencies"):
            if key in attrs:
                label[key] = attrs[key]
        # Only hash-bound allowlisted metadata; never arbitrary source payloads/secrets.
        relevant = entity.canonical_name.casefold() in query.focus.casefold()
        candidates.append(ContextProjectionCandidate(ref, "observed_entity", _data(label),
            950 if relevant else 780 if entity.entity_type in {"File", "Runtime", "Tool"} else 620, 700, 850))
    heads = {r.sort_key() for r in (() if snapshot.active_hypotheses is None else snapshot.active_hypotheses.refs)}
    for hyp in snapshot.hypotheses:
        ref = WorldRecordRef(record_type="world_hypothesis", record_id=hyp.hypothesis_id, sha256=hyp.hypothesis_sha256)
        if (ref.sort_key() not in heads or ref.sort_key() in stale or not hyp.has_valid_hash()
                or (hyp.valid_until_ms is not None and hyp.valid_until_ms <= query.created_at_ms)):
            continue
        summary = _data({"hypothesis_only": True, "claim": hyp.claim.model_dump(mode="json"),
                         "uncertainty_milli": hyp.uncertainty_milli})
        candidates.append(ContextProjectionCandidate(ref, "semantic_hypothesis", summary, 900, 780, 950))
    return tuple(candidates)
