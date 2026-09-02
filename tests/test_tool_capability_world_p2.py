from __future__ import annotations

import pytest

from contracts import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from total_gateway.action_registry import compile_action_registry
from world_understanding.tool_capability_world import (
    ToolCapabilityWorldError,
    compile_tool_capability_world,
)


H = "a" * 64


def manifest() -> dict:
    capabilities = {
        "file.read": {
            "id": "file.read",
            "risk": "A0",
            "effect": "read",
            "handler": "_action_file_read",
            "alias_to": "",
            "executable": True,
        },
        "file.inspect": {
            "id": "file.inspect",
            "risk": "A1",
            "effect": "read",
            "handler": "alias:file.read",
            "alias_to": "file.read",
            "executable": True,
        },
    }
    return {
        "schema": "tiangong.v3.capability_manifest.v1",
        "runtime_class": "demo.BodyRuntime",
        "source_hash": H,
        "total": 2,
        "executable": 2,
        "unavailable": 0,
        "dynamic_actions": [],
        "capabilities": capabilities,
        "validation": {"ok": True, "source_hash": H, "executable_without_route": []},
    }


def source_ref(action_id: str, manifest_sha: str) -> SourceRevisionRefV1:
    return SourceRevisionRefV1(
        source_kind="TOOL_ACTION",
        semantic_id=action_id,
        version="omni-registry-v1",
        source_files=("src/omni_body_skill/tools/omni_body_tool.py",),
        source_sha256=H,
        descriptor_sha256=H,
        manifest_sha256=manifest_sha,
    )


def compile_snapshot():
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    manifest_sha = canonical_sha256(document)
    sources = {action_id: source_ref(action_id, manifest_sha) for action_id in document["capabilities"]}
    schemas = {action_id: H for action_id in document["capabilities"]}
    return document, registry, compile_tool_capability_world(
        document,
        registry,
        source_revisions=sources,
        argument_schema_hashes=schemas,
        result_schema_hashes=schemas,
    )


def test_projection_is_deterministic_and_bound_to_existing_registry() -> None:
    document, registry, snapshot = compile_snapshot()
    assert snapshot.source_manifest_sha256 == registry.source_manifest_sha256
    assert snapshot.action_registry_sha256 == registry.registry_sha256
    assert snapshot.has_valid_sha256()
    assert tuple(item.action_id for item in snapshot.primitives) == ("file.inspect", "file.read")
    assert all(item.availability == "AVAILABLE" for item in snapshot.primitives)
    assert all(item.action_manifest_sha256 == registry.source_manifest_sha256 for item in snapshot.primitives)

    second = compile_tool_capability_world(
        document,
        registry,
        source_revisions={
            action_id: source_ref(action_id, registry.source_manifest_sha256)
            for action_id in document["capabilities"]
        },
        argument_schema_hashes={action_id: H for action_id in document["capabilities"]},
        result_schema_hashes={action_id: H for action_id in document["capabilities"]},
    )
    assert second.snapshot_sha256 == snapshot.snapshot_sha256


def test_projection_contains_only_deterministic_structural_relations() -> None:
    _document, _registry, snapshot = compile_snapshot()
    relations = {(item.relation_type, item.source_ref, item.target_ref) for item in snapshot.relations}
    assert ("ALIASES", "action:file.inspect", "action:file.read") in relations
    assert ("COMPILES_TO", "tool-source:file.read", "action:file.read") in relations
    assert ("PRODUCES", "tool-source:file.read", "effect:read") in relations
    forbidden = {"SUITABLE_FOR", "COMPOSES_WITH", "PREFERRED_WHEN", "CONFLICTS_WITH"}
    assert forbidden.isdisjoint({item.relation_type for item in snapshot.relations})


def test_projection_fails_closed_on_registry_manifest_drift() -> None:
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    changed = manifest()
    changed["capabilities"]["file.read"]["risk"] = "A1"
    manifest_sha = canonical_sha256(changed)
    sources = {action_id: source_ref(action_id, manifest_sha) for action_id in changed["capabilities"]}
    schemas = {action_id: H for action_id in changed["capabilities"]}
    with pytest.raises(ToolCapabilityWorldError, match="registry is not bound"):
        compile_tool_capability_world(
            changed,
            registry,
            source_revisions=sources,
            argument_schema_hashes=schemas,
            result_schema_hashes=schemas,
        )


def test_projection_fails_closed_when_source_binding_is_missing() -> None:
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    manifest_sha = canonical_sha256(document)
    schemas = {action_id: H for action_id in document["capabilities"]}
    with pytest.raises(ToolCapabilityWorldError, match="missing deterministic source/schema binding"):
        compile_tool_capability_world(
            document,
            registry,
            source_revisions={"file.read": source_ref("file.read", manifest_sha)},
            argument_schema_hashes=schemas,
            result_schema_hashes=schemas,
        )


def test_projection_cannot_create_new_executable_action() -> None:
    document, registry, _snapshot = compile_snapshot()
    manifest_sha = canonical_sha256(document)
    sources = {action_id: source_ref(action_id, manifest_sha) for action_id in document["capabilities"]}
    sources["invented.action"] = source_ref("invented.action", manifest_sha)
    schemas = {action_id: H for action_id in sources}
    snapshot = compile_tool_capability_world(
        document,
        registry,
        source_revisions=sources,
        argument_schema_hashes=schemas,
        result_schema_hashes=schemas,
    )
    assert "invented.action" not in {item.action_id for item in snapshot.primitives}
