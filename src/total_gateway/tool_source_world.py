"""Bind observed source inputs to the existing non-authorizing Tool World.

Git provenance, source topology, isolated compilation and publication remain
the caller's existing lifecycle responsibilities. This pure projection never
imports candidate code, executes an Action or ingests a WorldState. It accepts
no model-provided source revision, permission, risk override or schema hash.

Source identity conservatively covers the complete measured input closure.
The implementation reference points to the measured ACTIONS/BodyRuntime entry
module, not a guessed leaf handler or transitive dependency graph. Indirect
helper edits therefore invalidate identity too, even when the table is equal.
"""

from __future__ import annotations

from typing import Any, Mapping

from contracts import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1, SourceSpanRefV1
from world_understanding.tool_capability_world import (
    ToolCapabilityWorldSnapshotV1,
    compile_tool_capability_world,
)

from .action_registry import compile_action_authority
from .tool_source_candidate import _repository_path
from .tool_source_inputs import ToolSourceInputsV1


class ToolSourceWorldError(ValueError):
    pass


def compile_source_bound_tool_world(
    manifest: Mapping[str, Any],
    source_inputs: ToolSourceInputsV1,
    *,
    action_source_binding: Mapping[str, str],
) -> ToolCapabilityWorldSnapshotV1:
    """Derive all Action source references from the exact measured build.

    ``action_source_binding`` is the trusted parent's already-verified ACTIONS
    module path/digest, not a runtime handler selected by a model. The entry
    module and complete input closure are independently bound. Their observation
    alone does not prove source approval, safe effects, availability or publication.
    """
    if not isinstance(source_inputs, ToolSourceInputsV1) or not source_inputs.has_valid_sha256():
        raise ToolSourceWorldError("tool source input evidence is invalid")
    if not isinstance(action_source_binding, Mapping) or set(action_source_binding) != {"path", "sha256"}:
        raise ToolSourceWorldError("tool source ACTIONS binding is invalid")
    try:
        source_path = _repository_path(action_source_binding["path"])
    except (TypeError, ValueError) as exc:
        raise ToolSourceWorldError("tool source ACTIONS path is invalid") from exc
    entry = next((item for item in source_inputs.files if item.path == source_path), None)
    if (
        entry is None or not source_path.endswith(".py")
        or action_source_binding["sha256"] != entry.content_sha256
    ):
        raise ToolSourceWorldError("tool source ACTIONS binding differs from measured inputs")
    if not isinstance(manifest, Mapping):
        raise ToolSourceWorldError("tool source manifest is invalid")
    # Use the existing permission/schema compiler. Its exact payload, rather
    # than a caller-owned nested dictionary, owns all subsequent projections.
    authority = compile_action_authority(manifest, generated_at_ms=0)
    document = authority.manifest
    if document.get("source_inputs_sha256") != source_inputs.source_inputs_sha256:
        raise ToolSourceWorldError("tool source manifest input revision mismatch")

    sources = {}
    for permission in authority.registry.permissions:
        schema = authority.schema_catalog.resolve(permission.action_id, permission.action_version)
        source_descriptor = {
            "domain": "tiangong.tool-action-source-revision.v1",
            "action_id": permission.action_id,
            "action_version": permission.action_version,
            "canonical_action_id": schema.canonical_action_id,
            "source_inputs_sha256": source_inputs.source_inputs_sha256,
            "entry_path": source_path,
            "entry_sha256": entry.content_sha256,
        }
        source_sha256 = canonical_sha256(source_descriptor)
        sources[permission.action_id] = SourceRevisionRefV1(
            source_kind="TOOL_ACTION",
            semantic_id=permission.action_id,
            version=permission.action_version,
            source_files=(source_path,),
            source_spans=(SourceSpanRefV1(path=source_path),),
            source_sha256=source_sha256,
            descriptor_sha256=canonical_sha256({
                "domain": "tiangong.tool-source-entry-descriptor.v1",
                "source_revision": source_descriptor,
                "source_manifest_sha256": authority.manifest_sha256,
                "argument_schema_sha256": schema.argument_schema_sha256,
                "result_schema_sha256": schema.result_schema_sha256,
            }),
            manifest_sha256=authority.manifest_sha256,
        )
    # P2 still owns descriptor semantics, schemas, relation kinds and the
    # non-authorizing snapshot. P4 derives its exact SourceRevisionRef from the
    # returned primitives; no second Tool registry or World materializer.
    return compile_tool_capability_world(
        document, authority.registry,
        source_revisions=sources, action_schema_catalog=authority.schema_catalog,
    )
