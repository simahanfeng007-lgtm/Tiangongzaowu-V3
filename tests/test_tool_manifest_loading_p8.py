"""P8 byte-pinned production loader integration, not running-task evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from contracts import canonical_json_bytes, canonical_sha256
from total_gateway import skill_selection
from total_gateway.skill_selection import (
    SkillSelectionError,
    compile_composition_execution_manifest,
    load_model_capability_manifest,
)

from tests.test_tool_manifest_evolution_p8 import compiled


COMPONENT = "c" * 64  # Test component identity, not a production release claim.


def manifest_bytes(document):
    # Deliberately non-canonical: release byte identity and canonical registry
    # identity are separate domains and must not be accidentally conflated.
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_bytes(path, data, *, expected_sha256=None):
    path.write_bytes(data)
    return load_model_capability_manifest(
        path,
        expected_sha256=expected_sha256 or hashlib.sha256(data).hexdigest(),
        component_manifest_hash=COMPONENT,
        generated_at_ms=1000,
    )


@pytest.mark.parametrize("revision", [None, "0" * 64, "f" * 64])
def test_existing_compiler_loads_through_the_production_manifest_path(tmp_path, revision):
    document = compiled().to_gateway_dict(source_inputs_sha256=revision)
    data = manifest_bytes(document)
    loaded = load_bytes(tmp_path / "manifest.json", data)
    assert loaded.source_sha256 == hashlib.sha256(data).hexdigest()
    assert loaded.action_authority.manifest_sha256 == canonical_sha256(document)
    assert loaded.source_sha256 != loaded.action_authority.manifest_sha256
    assert loaded.action_authority.manifest == document
    assert loaded.executable_count == 3
    execution = compile_composition_execution_manifest(
        loaded.manifest,
        loaded.action_authority.registry,
        loaded.action_authority.schema_catalog,
    )
    assert execution.has_valid_sha256()
    assert {action.action_id for action in execution.actions} == {
        "demo.alias", "demo.read", "skill.list",
    }
    assert all(action.risk_class == "A0" for action in execution.actions)
    assert all(action.allowed_side_effects == ("read",) for action in execution.actions)


@pytest.mark.parametrize("invalid", [
    None, True, False, 0, 1.0, [], {}, "", "A" * 64, "g" * 64,
    "0" * 63, "0" * 65, "0" * 64 + "\n",
])
def test_present_source_revision_must_be_strict_lowercase_sha256(tmp_path, invalid):
    document = compiled().to_gateway_dict()
    document["source_inputs_sha256"] = invalid
    with pytest.raises(SkillSelectionError, match="source input revision"):
        load_bytes(tmp_path / "manifest.json", manifest_bytes(document))


@pytest.mark.parametrize("revision", [None, "a" * 64])
@pytest.mark.parametrize("field", ["may_execute", "approved", "source_inputs_sha25", "extra"])
def test_source_binding_does_not_admit_arbitrary_root_extensions(tmp_path, revision, field):
    document = compiled().to_gateway_dict(source_inputs_sha256=revision)
    document[field] = True
    with pytest.raises(SkillSelectionError, match="schema or validation"):
        load_bytes(tmp_path / "manifest.json", manifest_bytes(document))


@pytest.mark.parametrize("field", [
    "capabilities", "executable", "schema", "source_hash", "total", "unavailable", "validation",
])
def test_source_bound_manifest_still_requires_every_original_root_field(tmp_path, field):
    document = compiled().to_gateway_dict(source_inputs_sha256="a" * 64)
    document.pop(field)
    with pytest.raises(SkillSelectionError, match="schema or validation"):
        load_bytes(tmp_path / "manifest.json", manifest_bytes(document))


def test_file_replacement_does_not_mutate_loaded_authority_or_bypass_release_pin(tmp_path):
    path = tmp_path / "manifest.json"
    old_document = compiled().to_gateway_dict(source_inputs_sha256="a" * 64)
    new_document = compiled().to_gateway_dict(source_inputs_sha256="b" * 64)
    old_bytes, new_bytes = manifest_bytes(old_document), manifest_bytes(new_document)
    pinned = load_bytes(path, old_bytes)
    old_registry = pinned.action_authority.registry
    old_schema = pinned.action_authority.schema_catalog
    old_model = pinned.manifest
    with pytest.raises(SkillSelectionError, match="pinned release"):
        load_bytes(path, new_bytes, expected_sha256=pinned.source_sha256)
    newer = load_bytes(path, new_bytes)
    assert pinned.manifest is old_model
    assert pinned.action_authority.registry is old_registry
    assert pinned.action_authority.schema_catalog is old_schema
    assert pinned.action_authority.manifest == old_document
    assert newer.action_authority.manifest == new_document
    assert pinned.source_sha256 != newer.source_sha256
    assert old_registry.registry_sha256 != newer.action_authority.registry.registry_sha256
    assert old_schema.catalog_sha256 != newer.action_authority.schema_catalog.catalog_sha256
    assert old_model.actions == newer.manifest.actions
    for old, new in zip(old_registry.permissions, newer.action_authority.registry.permissions, strict=True):
        # Permission identity includes its source manifest; only the policy
        # semantics are unchanged when implementation bytes alone change.
        assert old.permission_sha256 != new.permission_sha256
        excluded = {"source_manifest_sha256", "permission_sha256"}
        assert old.model_dump(exclude=excluded) == new.model_dump(exclude=excluded)
    with pytest.raises(SkillSelectionError, match="source manifest mismatch"):
        compile_composition_execution_manifest(old_model, old_registry, newer.action_authority.schema_catalog)


def test_model_and_registry_share_one_verified_file_read_even_during_replacement(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    old_document = compiled().to_gateway_dict(source_inputs_sha256="a" * 64)
    new_document = compiled().to_gateway_dict(source_inputs_sha256="b" * 64)
    old_bytes, new_bytes = manifest_bytes(old_document), manifest_bytes(new_document)
    path.write_bytes(old_bytes)
    original_read = Path.read_bytes
    reads = []

    def replace_after_read(current):
        value = original_read(current)
        if current == path:
            reads.append(current)
            path.write_bytes(new_bytes)
        return value

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    loaded = load_model_capability_manifest(
        path, expected_sha256=hashlib.sha256(old_bytes).hexdigest(),
        component_manifest_hash=COMPONENT, generated_at_ms=1000,
    )
    assert reads == [path]
    assert loaded.source_sha256 == hashlib.sha256(old_bytes).hexdigest()
    assert loaded.action_authority.manifest == old_document
    assert original_read(path) == new_bytes


def test_source_revision_tampering_is_rejected_before_authority_compilation(tmp_path, monkeypatch):
    document = compiled().to_gateway_dict(source_inputs_sha256="a" * 64)
    original_digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    document["source_inputs_sha256"] = "b" * 64

    def forbidden(*args, **kwargs):
        raise AssertionError("unpinned bytes must not reach authority compilation")

    monkeypatch.setattr(skill_selection, "compile_action_authority", forbidden)
    with pytest.raises(SkillSelectionError, match="pinned release"):
        load_bytes(tmp_path / "manifest.json", canonical_json_bytes(document), expected_sha256=original_digest)


def test_duplicate_source_revision_is_rejected_by_existing_strict_json_parser(tmp_path):
    document = compiled().to_gateway_dict(source_inputs_sha256="a" * 64)
    data = canonical_json_bytes(document)
    duplicate = data[:-1] + b',"source_inputs_sha256":"' + b"b" * 64 + b'"}'
    with pytest.raises(SkillSelectionError, match="duplicate JSON key"):
        load_bytes(tmp_path / "manifest.json", duplicate)
