"""Read the installed dictionary into the current task's World frame.

This is an observation of available definitions. It installs no business Skill,
does not register executors, and cannot replace Gateway execution admission.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import json

from capability_dictionary import load_dictionary
from contracts.canonical import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from world_understanding.domain_contribution import compile_tool_capability_contribution
from world_understanding.tool_capability_world import compile_tool_capability_world
from world_understanding.world_state.domain_contributions import bind_domain_contributions


@lru_cache(maxsize=1)
def _installed_tools(release_hash):
    from total_gateway.action_registry import compile_action_authority
    release = load_dictionary()
    if release.sha256 != release_hash:
        raise ValueError("WORLD_DICTIONARY_RELEASE_CHANGED")
    release.verify_published()
    manifest = json.loads((release.root / "registry/capability_manifest.generated.json").read_text(encoding="utf-8"))
    authority = compile_action_authority(manifest, generated_at_ms=0)
    sources = {}
    for permission in authority.registry.permissions:
        action = permission.action_id
        sources[action] = SourceRevisionRefV1(
            source_kind="TOOL_ACTION", semantic_id=action, version=permission.action_version,
            source_files=("dictionaries/tools/catalog.json", "dictionaries/tools/schemas.json"),
            source_sha256=canonical_sha256({"dictionary": release.sha256, "action": action}),
            descriptor_sha256=canonical_sha256(release.tools[action]), manifest_sha256=authority.manifest_sha256)
    return compile_tool_capability_world(manifest, authority.registry, source_revisions=sources,
                                        action_schema_catalog=authority.schema_catalog)


def bind_dictionary_world(data, previous):
    # Static Git-bound publication has its own signed/pinned source resolver.
    # Ordinary generated compositions use the exact runtime workspace frame.
    if data.frame.branch != "runtime-current" or not data.frame.repository.startswith("workspace:"):
        return data
    release = load_dictionary()
    tools = _installed_tools(release.sha256)
    from .run_context import current_run_context
    from world_understanding.context_output.world_reference_context import action_context_relevance
    focus = str(current_run_context().current_user_text or "")
    candidates = [p for p in tools.primitives if release.tools[p.action_id]["binding"]["kind"] != "alias"
                  and not p.action_id.startswith(("skill.", "skill_"))]
    candidates.sort(key=lambda p: action_context_relevance(p.action_id, release.tools[p.action_id]["runtime"].get("summary", ""), focus))
    selected = {p.action_id for p in candidates[:24]}
    selected.update({"system.capabilities", "system.action_schema", "system.health", "file.read", "file.list", "file.write", "python.run", "shell.run"})
    # Bounded reference projection, not a duplicate of the entire executable
    # registry in every task snapshot. Full discovery remains in the dictionary.
    tools = replace(tools, primitives=tuple(p for p in tools.primitives if p.action_id in selected), relations=(), snapshot_sha256="0"*64)
    tools = replace(tools, snapshot_sha256=canonical_sha256(tools.payload()))
    prior_entities = {e.entity_id: e for e in data.graph.entities()}
    prior_relations = {r.relation_id: r for r in data.graph.relations()}
    contribution = compile_tool_capability_contribution(data.frame, data.cut, tools,
        previous_entities=prior_entities, previous_relations=prior_relations,
        action_metadata={key: {"semantic_summary": value["runtime"].get("summary", ""),
                               "required_dependencies": ",".join(value.get("required_dependencies", []))}
                         for key, value in release.tools.items()})
    removed = {e.entity_id for e in prior_entities.values() if e.entity_type == "ToolCapability"}
    for relation in tuple(data.graph.relations()):
        if relation.subject_ref.record_id in removed or relation.predicate.startswith("tool."):
            data.graph.delete_relation(relation.relation_id)
    for entity_id in removed:
        data.graph.delete_entity(entity_id)
    dependencies = tuple(b for b in data.dependency_bindings
                         if b.ref.record_id not in removed and not (b.ref.record_type == "world_relation"
                            and b.ref.record_id in prior_relations and prior_relations[b.ref.record_id].predicate.startswith("tool.")))
    return bind_domain_contributions(replace(data, dependency_bindings=dependencies), (contribution,))
