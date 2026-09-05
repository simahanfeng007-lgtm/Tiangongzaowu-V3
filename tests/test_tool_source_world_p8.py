"""Source-byte to existing P2/P4 contracts; not production or model evaluation."""

from dataclasses import replace
import hashlib
import json

import pytest

from contracts import canonical_sha256
from total_gateway.action_registry import compile_action_authority
from total_gateway.tool_source_inputs import (
    compile_tool_source_inputs,
)
from total_gateway.tool_source_world import (
    ToolSourceWorldError,
    compile_source_bound_tool_world,
)
from world_understanding.capability_composition.models import (
    ActionCandidateBindingV1,
    derive_action_source_revision,
)

from tests.test_tool_manifest_evolution_p8 import actions, compiled
from tests.test_tool_source_inputs_p8 import write


ENTRY = "src/omni_body_skill/tools/omni_body_tool.py"


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    marker = tmp_path / "must-not-import"
    policy = {
        "schema": "tiangong.source-ownership.v2",
        "authority_policy": {"editable_roots": ["src"], "frozen_roots": []},
        "mappings": [{"id": "body", "source": "src/omni_body_skill", "source_role": "authoritative", "targets": []}],
    }
    write(root / "source-ownership.json", json.dumps(policy))
    write(root / ENTRY, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    write(root / "src/omni_body_skill/tools/helper.py", "VALUE = 1\n")
    return root, marker


def inputs_and_binding(root):
    inputs = compile_tool_source_inputs(root)
    binding = {"path": ENTRY, "sha256": hashlib.sha256((root / ENTRY).read_bytes()).hexdigest()}
    return inputs, binding


def project(root, *, metadata=None):
    inputs, binding = inputs_and_binding(root)
    manifest = compiled(metadata).to_gateway_dict(source_inputs_sha256=inputs.source_inputs_sha256)
    return manifest, inputs, binding, compile_source_bound_tool_world(manifest, inputs, action_source_binding=binding)


def test_measured_source_closes_existing_tool_world_and_p4_source_contracts(source):
    root, marker = source
    manifest, inputs, binding, world = project(root)
    authority = compile_action_authority(manifest, generated_at_ms=0)
    assert world.has_valid_sha256()
    assert world.may_authorize is world.may_execute is False
    assert world.source_manifest_sha256 == authority.manifest_sha256
    assert world.action_registry_sha256 == authority.registry.registry_sha256
    assert tuple(p.action_id for p in world.primitives) == ("demo.alias", "demo.read", "skill.list")
    for index, primitive in enumerate(world.primitives, 1):
        revision = derive_action_source_revision(primitive)
        permission = next(p for p in authority.registry.permissions if p.action_id == primitive.action_id)
        assert revision.source_files == (ENTRY,)
        assert revision.manifest_sha256 == authority.manifest_sha256
        assert revision.descriptor_sha256 == primitive.descriptor_sha256
        assert revision.source_sha256 == primitive.implementation_hashes[0]
        assert primitive.risk_floor == permission.effective_risk
        assert primitive.side_effects == permission.allowed_side_effects
        assert primitive.idempotency == "UNKNOWN"
        assert primitive.determinism_class == "NONDETERMINISTIC"
        bound = ActionCandidateBindingV1(
            candidate_id=f"A{index:02d}", primitive=primitive,
            source_revision=revision, binding_sha256="0" * 64,
        ).with_computed_sha256()
        assert bound.has_valid_sha256()
    assert not marker.exists()
    assert compile_source_bound_tool_world(manifest, inputs, action_source_binding=binding) == world


def test_transitive_source_only_change_updates_revisions_without_policy_changes(source):
    root, _ = source
    before, old_inputs, _, old_world = project(root)
    write(root / "src/omni_body_skill/tools/helper.py", "VALUE = 2\n")
    after, new_inputs, _, new_world = project(root)
    assert before["capabilities"] == after["capabilities"]
    assert old_inputs.source_inputs_sha256 != new_inputs.source_inputs_sha256
    assert old_world.snapshot_sha256 != new_world.snapshot_sha256
    for old, new in zip(old_world.primitives, new_world.primitives, strict=True):
        assert old.action_id == new.action_id
        assert old.implementation_refs == new.implementation_refs
        assert old.implementation_hashes != new.implementation_hashes
        assert old.risk_floor == new.risk_floor
        assert old.side_effects == new.side_effects
        assert old.descriptor_sha256 != new.descriptor_sha256


def test_alias_risk_is_from_existing_registry_and_unavailable_actions_are_not_invented(source):
    root, _ = source
    metadata = actions()
    metadata["demo.read"]["effect"] = "execute"
    manifest, _, _, world = project(root, metadata=metadata)
    by_id = {item.action_id: item for item in world.primitives}
    assert by_id["demo.alias"].risk_floor == by_id["demo.read"].risk_floor == "A3"
    assert "demo.future" not in by_id
    assert manifest["capabilities"]["demo.future"]["executable"] is False
    assert any(r.relation_type == "ALIASES" and r.target_ref == "action:demo.read" for r in world.relations)


@pytest.mark.parametrize("mode", ["missing", "wrong", "null", "bool"])
def test_missing_or_foreign_manifest_source_binding_fails_closed(source, mode):
    root, _ = source
    manifest, inputs, binding, _ = project(root)
    if mode == "missing":
        manifest.pop("source_inputs_sha256")
    else:
        manifest["source_inputs_sha256"] = {"wrong": "0" * 64, "null": None, "bool": False}[mode]
    with pytest.raises(ToolSourceWorldError, match="input revision mismatch"):
        compile_source_bound_tool_world(manifest, inputs, action_source_binding=binding)


@pytest.mark.parametrize("binding", [
    {}, {"path": ENTRY}, {"path": ENTRY, "sha256": "0" * 64},
    {"path": "../outside.py", "sha256": "a" * 64},
    {"path": None, "sha256": "a" * 64},
    {"path": "src/absent.py", "sha256": "a" * 64},
    {"path": ENTRY, "sha256": "a" * 64, "may_execute": True},
])
def test_entry_reference_must_match_the_measured_binding(source, binding):
    root, _ = source
    manifest, inputs, _, _ = project(root)
    with pytest.raises(ToolSourceWorldError, match="ACTIONS"):
        compile_source_bound_tool_world(manifest, inputs, action_source_binding=binding)


def test_forged_input_snapshot_with_recomputed_digest_cannot_match_old_manifest(source):
    root, _ = source
    manifest, inputs, binding, _ = project(root)
    changed = tuple(replace(row, content_sha256="f" * 64) if row.path.endswith("helper.py") else row for row in inputs.files)
    forged = replace(inputs, files=changed)
    forged = replace(forged, source_inputs_sha256=canonical_sha256(forged.payload()))
    assert forged.has_valid_sha256()  # Self-consistency is not release provenance.
    with pytest.raises(ToolSourceWorldError, match="revision mismatch"):
        compile_source_bound_tool_world(manifest, forged, action_source_binding=binding)


@pytest.mark.parametrize("mode", [
    "list", "empty", "duplicate", "reverse", "case_collision", "bool_size", "negative_size",
    "oversize", "bad_hash", "policy_missing", "policy_drift", "unsafe_path", "generated_manifest", "no_source",
])
def test_rehashed_malformed_input_records_are_not_valid_evidence(source, mode):
    root, _ = source
    _, inputs, _, _ = project(root)
    rows = list(inputs.files)
    entry = next(row for row in rows if row.path == ENTRY)
    policy = next(row for row in rows if row.path == "source-ownership.json")
    if mode == "list":
        modified = replace(inputs, files=rows)
    elif mode == "empty":
        modified = replace(inputs, files=())
    elif mode == "duplicate":
        modified = replace(inputs, files=tuple(sorted([*rows, entry], key=lambda row: row.path)))
    elif mode == "reverse":
        modified = replace(inputs, files=tuple(reversed(rows)))
    elif mode == "case_collision":
        modified = replace(inputs, files=tuple(sorted([*rows, replace(entry, path=ENTRY.upper())], key=lambda row: row.path)))
    elif mode == "policy_missing":
        modified = replace(inputs, files=tuple(row for row in rows if row != policy))
    elif mode == "policy_drift":
        modified = replace(inputs, ownership_sha256="f" * 64)
    elif mode == "no_source":
        modified = replace(inputs, files=(policy,))
    else:
        fields = {
            "bool_size": {"size_bytes": True}, "negative_size": {"size_bytes": -1},
            "oversize": {"size_bytes": 33 * 1024 * 1024}, "bad_hash": {"content_sha256": "A" * 64},
            "unsafe_path": {"path": "src/../escape.py"},
            "generated_manifest": {"path": "src/omni_body_skill/registry/capability_manifest.generated.json"},
        }[mode]
        modified = replace(inputs, files=tuple(sorted(
            (replace(row, **fields) if row == entry else row for row in rows), key=lambda row: row.path,
        )))
    modified = replace(modified, source_inputs_sha256=canonical_sha256(modified.payload()))
    assert not modified.has_valid_sha256()


def test_output_does_not_track_mutable_manifest_or_binding_objects(source):
    root, _ = source
    manifest, inputs, binding, world = project(root)
    expected = world.snapshot_sha256
    manifest["capabilities"]["demo.read"]["risk"] = "A4"
    binding["sha256"] = "0" * 64
    assert world.snapshot_sha256 == expected and world.has_valid_sha256()
    assert next(p for p in world.primitives if p.action_id == "demo.read").risk_floor == "A0"
    assert inputs.has_valid_sha256()
