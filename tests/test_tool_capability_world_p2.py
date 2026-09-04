from __future__ import annotations

import pytest

from contracts import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1, SourceSpanRefV1
from omni_body_skill.tool_contracts import build_action_schema_catalog
from total_gateway.action_registry import compile_action_registry
from world_understanding.tool_capability_world import (
    ToolCapabilityRelationV1,
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
    for action_id, descriptor in build_action_schema_catalog(capabilities).items():
        capabilities[action_id].update(descriptor)
    return {
        "schema": "tiangong.v3.capability_manifest.v1",
        "runtime_class": "demo.BodyRuntime",
        "source_hash": H,
        "total": 2,
        "executable": 2,
        "unavailable": 0,
        "dynamic_actions": [],
        "capabilities": capabilities,
        "validation": {
            "ok": True,
            "source_hash": H,
            "executable_without_route": [],
        },
    }


def _argument_schema_hashes(document: dict) -> dict[str, str]:
    return {
        action_id: raw["argument_schema_sha256"]
        for action_id, raw in document["capabilities"].items()
    }


def _result_schema_hashes(document: dict) -> dict[str, str]:
    return {
        action_id: raw["result_schema_sha256"]
        for action_id, raw in document["capabilities"].items()
    }


def source_ref(
    action_id: str,
    manifest_sha: str | None,
    *,
    version: str = "omni-registry-v1",
    source_files: tuple[str, ...] = (
        "src/omni_body_skill/tools/omni_body_tool.py",
    ),
    source_spans: tuple[SourceSpanRefV1, ...] = (),
) -> SourceRevisionRefV1:
    return SourceRevisionRefV1(
        source_kind="TOOL_ACTION",
        semantic_id=action_id,
        version=version,
        source_files=source_files,
        source_spans=source_spans,
        source_sha256=H,
        descriptor_sha256=H,
        manifest_sha256=manifest_sha,
    )


def compile_snapshot():
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    schemas = _argument_schema_hashes(document)
    return document, registry, compile_tool_capability_world(
        document,
        registry,
        source_revisions=sources,
        argument_schema_hashes=schemas,
        result_schema_hashes=_result_schema_hashes(document),
    )


def compile_with_sources(
    sources: dict[str, SourceRevisionRefV1],
    *,
    argument_schema_hashes: dict[str, str] | None = None,
    result_schema_hashes: dict[str, str] | None = None,
):
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    schemas = _argument_schema_hashes(document)
    return compile_tool_capability_world(
        document,
        registry,
        source_revisions=sources,
        argument_schema_hashes=(
            argument_schema_hashes
            if argument_schema_hashes is not None
            else schemas
        ),
        result_schema_hashes=(
            _result_schema_hashes(document)
            if result_schema_hashes is None
            else result_schema_hashes
        ),
    )


def test_projection_is_deterministic_and_bound_to_existing_registry() -> None:
    document, registry, snapshot = compile_snapshot()
    assert snapshot.source_manifest_sha256 == registry.source_manifest_sha256
    assert snapshot.action_registry_sha256 == registry.registry_sha256
    assert snapshot.has_valid_sha256()
    assert snapshot.may_authorize is False
    assert snapshot.may_execute is False
    assert tuple(item.action_id for item in snapshot.primitives) == (
        "file.inspect",
        "file.read",
    )
    assert all(item.availability == "AVAILABLE" for item in snapshot.primitives)
    assert all(
        item.action_manifest_sha256 == registry.source_manifest_sha256
        for item in snapshot.primitives
    )
    assert all(
        item.provider_component_id == "omni-body"
        for item in snapshot.primitives
    )
    assert all(item.idempotency == "UNKNOWN" for item in snapshot.primitives)
    assert all(
        item.determinism_class == "NONDETERMINISTIC"
        for item in snapshot.primitives
    )
    assert {
        item.action_id: item.result_schema_sha256 for item in snapshot.primitives
    } == _result_schema_hashes(document)

    second = compile_tool_capability_world(
        document,
        registry,
        source_revisions={
            action_id: source_ref(action_id, registry.source_manifest_sha256)
            for action_id in document["capabilities"]
        },
        argument_schema_hashes=_argument_schema_hashes(document),
        result_schema_hashes=_result_schema_hashes(document),
    )
    assert second.snapshot_sha256 == snapshot.snapshot_sha256


def test_result_schema_is_manifest_authority_and_caller_is_only_an_assertion() -> None:
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    snapshot = compile_tool_capability_world(
        document,
        registry,
        source_revisions=sources,
    )
    assert {
        item.action_id: item.result_schema_sha256 for item in snapshot.primitives
    } == _result_schema_hashes(document)

    with pytest.raises(
        ToolCapabilityWorldError,
        match="caller result schema differs from manifest authority",
    ):
        compile_tool_capability_world(
            document,
            registry,
            source_revisions=sources,
            result_schema_hashes={action_id: H for action_id in sources},
        )


def test_source_file_order_is_rejected_instead_of_creating_two_identities() -> None:
    document = manifest()
    manifest_sha = canonical_sha256(document)
    files = (
        "app/backend/tiangong-backend/v3/fact_kernel/__init__.py",
        "src/omni_body_skill/tools/omni_body_tool.py",
    )
    sources = {
        action_id: source_ref(action_id, manifest_sha, source_files=files)
        for action_id in document["capabilities"]
    }
    compile_with_sources(sources)

    reversed_sources = {
        action_id: source_ref(
            action_id,
            manifest_sha,
            source_files=tuple(reversed(files)),
        )
        for action_id in document["capabilities"]
    }
    with pytest.raises(
        ToolCapabilityWorldError,
        match="files must be sorted and unique",
    ):
        compile_with_sources(reversed_sources)


def test_projection_contains_only_deterministic_structural_relations() -> None:
    _document, _registry, snapshot = compile_snapshot()
    relations = {
        (item.relation_type, item.source_ref, item.target_ref)
        for item in snapshot.relations
    }
    assert ("ALIASES", "action:file.inspect", "action:file.read") in relations
    assert ("COMPILES_TO", "tool-source:file.read", "action:file.read") in relations
    assert ("PRODUCES", "tool-source:file.read", "effect:read") in relations
    forbidden = {
        "SUITABLE_FOR",
        "COMPOSES_WITH",
        "PREFERRED_WHEN",
        "CONFLICTS_WITH",
    }
    assert forbidden.isdisjoint(
        {item.relation_type for item in snapshot.relations}
    )

    with pytest.raises(ToolCapabilityWorldError, match="relation type"):
        ToolCapabilityRelationV1(
            "SUITABLE_FOR",
            "tool-source:file.read",
            "goal:read-file",
        )


def test_projection_fails_closed_on_registry_manifest_drift() -> None:
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    changed = manifest()
    changed["capabilities"]["file.read"]["risk"] = "A1"
    manifest_sha = canonical_sha256(changed)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in changed["capabilities"]
    }
    schemas = _argument_schema_hashes(changed)
    with pytest.raises(ToolCapabilityWorldError, match="registry is not bound"):
        compile_tool_capability_world(
            changed,
            registry,
            source_revisions=sources,
            argument_schema_hashes=schemas,
            result_schema_hashes=_result_schema_hashes(changed),
        )


def test_projection_fails_closed_on_stale_manifest_validation_hash() -> None:
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    document["validation"]["source_hash"] = "b" * 64
    manifest_sha = canonical_sha256(document)
    schemas = _argument_schema_hashes(document)
    with pytest.raises(ToolCapabilityWorldError, match="validation hash is stale"):
        compile_tool_capability_world(
            document,
            registry,
            source_revisions={
                action_id: source_ref(action_id, manifest_sha)
                for action_id in document["capabilities"]
            },
            argument_schema_hashes=schemas,
            result_schema_hashes=_result_schema_hashes(document),
        )


def test_projection_fails_closed_when_source_binding_is_missing() -> None:
    document = manifest()
    registry = compile_action_registry(document, generated_at_ms=1)
    manifest_sha = canonical_sha256(document)
    schemas = _argument_schema_hashes(document)
    with pytest.raises(
        ToolCapabilityWorldError,
        match="missing deterministic source/schema binding",
    ):
        compile_tool_capability_world(
            document,
            registry,
            source_revisions={
                "file.read": source_ref("file.read", manifest_sha)
            },
            argument_schema_hashes=schemas,
            result_schema_hashes=_result_schema_hashes(document),
        )


def test_projection_requires_exact_tool_source_manifest_binding() -> None:
    document = manifest()
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    sources["file.read"] = source_ref("file.read", None)
    with pytest.raises(
        ToolCapabilityWorldError,
        match="source revision manifest mismatch",
    ):
        compile_with_sources(sources)


def test_projection_requires_exact_action_version_binding() -> None:
    document = manifest()
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    sources["file.read"] = source_ref(
        "file.read",
        manifest_sha,
        version="stale-action-version",
    )
    with pytest.raises(
        ToolCapabilityWorldError,
        match="identity/version mismatch",
    ):
        compile_with_sources(sources)


def test_projection_rejects_source_span_outside_declared_files() -> None:
    document = manifest()
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    sources["file.read"] = source_ref(
        "file.read",
        manifest_sha,
        source_spans=(
            SourceSpanRefV1(
                path="src/unrelated.py",
                start_line=1,
                end_line=10,
            ),
        ),
    )
    with pytest.raises(
        ToolCapabilityWorldError,
        match="implementation refs are invalid",
    ):
        compile_with_sources(sources)


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "../outside.py",
        "src//double-separator.py",
        "src/./dot-segment.py",
        " src/leading-space.py",
    ),
)
def test_projection_rejects_unsafe_repository_source_path(
    unsafe_path: str,
) -> None:
    document = manifest()
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    sources["file.read"] = source_ref(
        "file.read",
        manifest_sha,
        source_files=(unsafe_path,),
    )
    with pytest.raises(
        ToolCapabilityWorldError,
        match="unsafe repository path",
    ):
        compile_with_sources(sources)


def test_projection_rejects_malformed_schema_hash_binding() -> None:
    document = manifest()
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    schemas = _argument_schema_hashes(document)
    schemas["file.read"] = "not-a-sha256"
    with pytest.raises(ToolCapabilityWorldError, match="argument schema"):
        compile_with_sources(
            sources,
            argument_schema_hashes=schemas,
        )


def test_projection_cannot_create_new_executable_action() -> None:
    document, registry, _snapshot = compile_snapshot()
    manifest_sha = canonical_sha256(document)
    sources = {
        action_id: source_ref(action_id, manifest_sha)
        for action_id in document["capabilities"]
    }
    sources["invented.action"] = source_ref("invented.action", manifest_sha)
    schemas = _argument_schema_hashes(document)
    schemas["invented.action"] = H
    snapshot = compile_tool_capability_world(
        document,
        registry,
        source_revisions=sources,
        argument_schema_hashes=schemas,
        result_schema_hashes=_result_schema_hashes(document),
    )
    assert "invented.action" not in {
        item.action_id for item in snapshot.primitives
    }
