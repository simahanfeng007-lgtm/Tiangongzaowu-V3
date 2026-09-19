"""Reference-only capability DATA from one already materialized WorldState.

This reads the retained addresses emitted by the existing domain compilers. It
never re-opens source files, derives authority from summaries, or reconstructs a
P4 executable candidate snapshot. Old states without these addresses require
normal source re-observation; they are not silently upgraded.
"""
from __future__ import annotations

from dataclasses import fields, replace
import re

from contracts.canonical import canonical_json_bytes, canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.query import WorldQuery
from world_understanding.domain_contribution import FrameBindingV1
from world_understanding.software_world.frame import SoftwareWorldFrame
from world_understanding.world_state.manifests import HeadManifest, DependencyManifest, DeltaManifest
from world_understanding.world_state.store import MaterializedWorldSnapshot, WorldStateStore
from .capability_context import (
    ActionContextEntryV1, MethodContextEntryV1, CapabilityContextPacketV1,
    ProtectedContextIdentityV1, capability_context_reserved_tokens,
)
from .slot import conservative_token_estimate


_REFERENCE_ABI = (
    "candidate_snapshot_kind=WORLD_STATE_SOURCE_REFERENCES;candidate_ids=DISPLAY_ONLY;"
    "not_a_composition_candidate_snapshot=true;source_resolution_required_for_planning=true;"
    "model_authority=false;may_execute=false"
)


def _validate_snapshot(snapshot: MaterializedWorldSnapshot, query: WorldQuery) -> None:
    if (type(snapshot) is not MaterializedWorldSnapshot or not query.has_valid_hash()
            or not snapshot.state.has_valid_hash() or not snapshot.cut.has_valid_hash()
            or snapshot.state.scope != query.scope or snapshot.cut.scope != query.scope
            or snapshot.state_ref != query.basis_world_state_ref
            or snapshot.state.frame_ref != query.frame_ref
            or snapshot.frame_id != query.frame_ref.record_id):
        raise ValueError("CAPABILITY_CONTEXT_SNAPSHOT_BINDING_INVALID")
    WorldStateStore._validate_snapshot(snapshot)
    for manifest in (snapshot.entity_heads, snapshot.relation_heads):
        if manifest != HeadManifest.build(manifest.kind, manifest.refs, max_items=100_000):
            raise ValueError("CAPABILITY_CONTEXT_HEAD_HASH_INVALID")
    if snapshot.dependencies != DependencyManifest.build(snapshot.dependencies.bindings, max_items=100_000):
        raise ValueError("CAPABILITY_CONTEXT_DEPENDENCY_HASH_INVALID")
    args = {f.name: getattr(snapshot.delta, f.name) for f in fields(snapshot.delta) if f.name != "manifest_sha256"}
    if snapshot.delta != DeltaManifest.build(**args):
        raise ValueError("CAPABILITY_CONTEXT_DELTA_HASH_INVALID")


def _address(entity, snapshot):
    if not entity.has_valid_hash() or entity.scope != snapshot.state.scope:
        raise ValueError("CAPABILITY_CONTEXT_ENTITY_INVALID")
    attrs = {}
    for item in entity.attributes:
        if not item.has_valid_hash():
            raise ValueError("CAPABILITY_CONTEXT_ATTRIBUTE_HASH_INVALID")
        if item.value.kind == "string":
            attrs[item.key] = item.value.string_value
    required = {"context_source_ref", "context_frame_binding", "context_frame_revision",
                "context_world_cut", "context_workspace", "context_frame_ref", "descriptor_sha256"}
    if not required <= attrs.keys():
        raise ValueError("CAPABILITY_CONTEXT_SOURCE_ADDRESS_UNAVAILABLE")
    raw = attrs["context_source_ref"]
    source = SourceRevisionRefV1.model_validate_json(raw)
    if canonical_json_bytes(source.model_dump(mode="json")).decode("utf-8") != raw:
        raise ValueError("CAPABILITY_CONTEXT_SOURCE_ADDRESS_NONCANONICAL")
    binding = FrameBindingV1.model_validate_json(attrs["context_frame_ref"])
    frame = SoftwareWorldFrame.build(scope=snapshot.state.scope, workspace=attrs["context_workspace"],
        repository=binding.repository, worktree=binding.worktree, branch=binding.branch,
        commit=binding.commit, environment=binding.environment, time=entity.time, world_cut=snapshot.cut)
    if (canonical_json_bytes(binding.model_dump(mode="json")).decode("utf-8") != attrs["context_frame_ref"]
            or binding != FrameBindingV1.from_frame(frame, snapshot.cut)
            or binding.binding_sha256 != attrs["context_frame_binding"]
            or binding.frame_id != snapshot.frame_id
            or binding.frame_revision_hash != snapshot.state.frame_ref.sha256):
        raise ValueError("CAPABILITY_CONTEXT_SOURCE_FRAME_BINDING_INVALID")
    # The identity grammar is one unquoted token. Unsupported version spellings
    # remain unavailable, never rewritten, truncated or treated as instructions.
    if not re.fullmatch(r"[A-Za-z0-9._:+-]{1,80}", source.version):
        raise ValueError("CAPABILITY_CONTEXT_VERSION_GRAMMAR_INVALID")
    method = entity.entity_type == "SkillMethod"
    semantic = attrs.get("method_id" if method else "action_id")
    version = attrs.get("version" if method else "action_version")
    expected_type = "skill_method_descriptor" if method else "tool_capability_descriptor"
    if (source.source_kind != ("SKILL_METHOD" if method else "TOOL_ACTION")
            or source.semantic_id != semantic or source.version != version
            or source.descriptor_sha256 != attrs["descriptor_sha256"]
            or len(entity.source_observation_refs) != 1
            or entity.source_observation_refs[0].record_type != expected_type
            or entity.source_observation_refs[0].sha256 != source.descriptor_sha256
            or (method and (source.manifest_sha256 is not None or source.source_sha256 != attrs.get("source_sha256")))
            or (not method and source.manifest_sha256 != attrs.get("source_manifest_sha256"))):
        raise ValueError("CAPABILITY_CONTEXT_SOURCE_DESCRIPTOR_MISMATCH")
    ref = WorldRecordRef(record_type="world_entity", record_id=entity.entity_id,
                         revision=entity.revision, sha256=entity.entity_sha256)
    deps = snapshot.dependencies.source_keys_for(ref)
    if (attrs["context_frame_revision"] != snapshot.state.frame_ref.sha256
            or attrs["context_world_cut"] != snapshot.cut.cut_sha256
            or not re.fullmatch(r"[0-9a-f]{64}", attrs["context_frame_binding"])
            or "frame:" + attrs["context_frame_revision"] not in deps
            or "world-cut:" + attrs["context_world_cut"] not in deps
            or "source:" + source.source_sha256 not in deps
            or "source-ref:" + canonical_sha256(source.model_dump(mode="json")) not in deps
            or "context-frame:" + binding.binding_sha256 not in deps
            or not any(key.startswith("contribution:") for key in deps)):
        raise ValueError("CAPABILITY_CONTEXT_SOURCE_FRAME_OR_CUT_MISMATCH")
    if not method and (not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", attrs.get("effect_class", ""))
                       or attrs.get("risk_floor") not in {"A0", "A1", "A2", "A3", "A4", "A5"}
                       or attrs.get("availability") not in {"AVAILABLE", "UNAVAILABLE", "DEGRADED", "UNKNOWN"}):
        raise ValueError("CAPABILITY_CONTEXT_ACTION_SEMANTICS_INVALID")
    return entity, source, ref, attrs


def build_world_reference_context_packet(snapshot: MaterializedWorldSnapshot, query: WorldQuery,
                                         *, token_estimator=conservative_token_estimate) -> CapabilityContextPacketV1 | None:
    """Project bounded display candidates; execution still needs exact Source resolution.

    Selection is a deterministic presentation order (literal task match, then ID),
    not a planner. Whole optional records may be omitted; identity strings never
    are truncated. Ineligible records and omissions remain explicitly counted.
    """
    _validate_snapshot(snapshot, query)
    cap_entities = [e for e in snapshot.entities if e.entity_type in {"ToolCapability", "SkillMethod"}]
    if not cap_entities:
        return None
    stale = {(ref.record_type, ref.record_id) for ref in (*snapshot.state.stale_refs, *snapshot.state.unresolved_conflict_refs)}
    rows = [_address(e, snapshot) for e in cap_entities
            if e.lifecycle == "ACTIVE" and e.truth_state == "TRUE" and e.epistemic_state == "CURRENT"
            and ("world_entity", e.entity_id) not in stale]
    if not rows:
        raise ValueError("CAPABILITY_CONTEXT_NO_CURRENT_SOURCES")
    if len({(r[1].source_kind, r[1].semantic_id) for r in rows}) != len(rows):
        raise ValueError("CAPABILITY_CONTEXT_DUPLICATE_SOURCE")
    frame_bindings = {r[3]["context_frame_binding"] for r in rows}
    workspaces = {r[3]["context_workspace"] for r in rows}
    if len(frame_bindings) != 1 or len(workspaces) != 1:
        raise ValueError("CAPABILITY_CONTEXT_SOURCE_FRAME_AMBIGUOUS")
    scope_workspace = {v.key: v.value for v in query.scope.scope_bindings}.get("workspace_id")
    if scope_workspace is not None and scope_workspace not in workspaces:
        raise ValueError("CAPABILITY_CONTEXT_SOURCE_WORKSPACE_MISMATCH")
    focus = query.focus.casefold()
    rows.sort(key=lambda r: (not (r[1].semantic_id.casefold() in focus or r[0].canonical_name.casefold() in focus),
                             r[1].semantic_id, r[0].entity_id))
    methods = [r for r in rows if r[1].source_kind == "SKILL_METHOD"][:15]
    actions = [r for r in rows if r[1].source_kind == "TOOL_ACTION"][:30]

    def packet():
        method_entries = tuple(MethodContextEntryV1(f"M{i:02d}", "method:" + s.semantic_id, s.version,
            canonical_sha256(s.model_dump(mode="json")), s.descriptor_sha256, e.canonical_name, a.get("semantic_summary", ""))
            for i, (e, s, _ref, a) in enumerate(methods, 1))
        action_entries = tuple(ActionContextEntryV1(f"A{i:02d}", "action:" + s.semantic_id, s.version,
            canonical_sha256(s.model_dump(mode="json")), s.descriptor_sha256, a["effect_class"], a["risk_floor"], a["availability"])
            for i, (_e, s, _ref, a) in enumerate(actions, 1))
        selected = methods + actions
        digest = canonical_sha256({"domain": "tiangong.world-source-reference-projection.v1",
            "query_sha256": query.query_sha256, "state": snapshot.state_ref.model_dump(mode="json"),
            "selected": [{"entity_ref": r[2].model_dump(mode="json"), "source_ref": r[1].model_dump(mode="json")} for r in selected]})
        identities = tuple(sorted((ProtectedContextIdentityV1("world_state_ref", f"{snapshot.state_ref.record_id}@{snapshot.state_ref.sha256}"),
                                   ProtectedContextIdentityV1("query_ref", f"{query.query_id}@{query.query_sha256}"),
                                   ProtectedContextIdentityV1("workspace_id", next(iter(workspaces)))), key=lambda x: (x.key, x.value)))
        value = CapabilityContextPacketV1(schema="tiangong.capability-context-packet.v1", world_state_ref=snapshot.state_ref,
            frame_binding_sha256=next(iter(frame_bindings)), candidate_snapshot_sha256=digest,
            method_candidates=method_entries, action_candidates=action_entries, procedural_experience=(), negative_evidence=(),
            protected_identities=identities, composition_abi=_REFERENCE_ABI + f";omitted_records={len(rows)-len(selected)};ineligible_records={len(cap_entities)-len(rows)}",
            packet_sha256="0"*64)
        return replace(value, packet_sha256=value.computed_sha256())

    result = packet()
    # Keep at least one record of each present kind. Smaller budgets fail
    # explicitly instead of silently turning a partial method/action pair whole.
    while capability_context_reserved_tokens(result, token_estimator=token_estimator) > query.token_budget // 2:
        if len(actions) > max(1, len(methods)):
            actions.pop()
        elif len(methods) > 1:
            methods.pop()
        elif len(actions) > 1:
            actions.pop()
        else:
            raise ValueError("CAPABILITY_CONTEXT_IDENTITY_BUDGET_EXCEEDED")
        result = packet()
    return result


__all__ = ["build_world_reference_context_packet"]
