from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from contracts import CapabilityManifest, canonical_json_bytes, canonical_sha256
from total_gateway.action_registry import ActionSchemaCatalog
from total_gateway.skill_selection import (
    LoadedModelCapabilityManifest,
    SkillSelectionError,
    compile_composition_execution_manifest,
    load_model_capability_manifest,
)


ZERO = "0" * 64
OTHER = "f" * 64
COMPONENT_MANIFEST_SHA256 = "c" * 64
ROOT = Path(__file__).resolve().parents[1]
MODEL_MANIFEST_PATH = (
    ROOT
    / "src"
    / "omni_body_skill"
    / "registry"
    / "capability_manifest.generated.json"
).resolve()


@pytest.fixture(scope="module")
def loaded() -> LoadedModelCapabilityManifest:
    raw = MODEL_MANIFEST_PATH.read_bytes()
    return load_model_capability_manifest(
        MODEL_MANIFEST_PATH,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        component_manifest_hash=COMPONENT_MANIFEST_SHA256,
        generated_at_ms=1_000,
    )


def _rehash_catalog(
    catalog: ActionSchemaCatalog,
    *,
    entries=None,
    source_manifest_sha256: str | None = None,
) -> ActionSchemaCatalog:
    draft = replace(
        catalog,
        entries=catalog.entries if entries is None else entries,
        source_manifest_sha256=(
            catalog.source_manifest_sha256
            if source_manifest_sha256 is None
            else source_manifest_sha256
        ),
        catalog_sha256=ZERO,
    )
    return replace(draft, catalog_sha256=canonical_sha256(draft.payload()))


def _rehash_manifest(
    manifest: CapabilityManifest,
    *,
    actions,
) -> CapabilityManifest:
    return manifest.model_copy(
        update={"actions": tuple(actions), "sha256": ZERO}
    ).with_computed_sha256()


def test_compiles_real_execution_manifest_from_all_three_authorities(
    loaded: LoadedModelCapabilityManifest,
) -> None:
    model_manifest = loaded.manifest
    registry = loaded.action_authority.registry
    catalog = loaded.action_authority.schema_catalog

    execution_manifest = compile_composition_execution_manifest(
        model_manifest,
        registry,
        catalog,
        generated_at_ms=1_001,
    )

    assert execution_manifest.has_valid_sha256()
    assert execution_manifest.generated_at_ms == 1_001
    assert execution_manifest.component_manifest_hash == COMPONENT_MANIFEST_SHA256
    assert len(execution_manifest.actions) == registry.executable_count == 290
    assert tuple(
        (item.action_id, item.version) for item in execution_manifest.actions
    ) == tuple(
        (permission.action_id, permission.action_version)
        for permission in registry.permissions
    )

    model_by_id = {item.action_id: item for item in model_manifest.actions}
    permission_by_id = {
        item.action_id: item for item in registry.permissions
    }
    schema_by_id = {item.action_id: item for item in catalog.entries}
    for action in execution_manifest.actions:
        model_action = model_by_id[action.action_id]
        permission = permission_by_id[action.action_id]
        schema = schema_by_id[action.action_id]
        assert action.version == permission.action_version
        assert action.argument_schema_sha256 == schema.argument_schema_sha256
        assert action.result_schema_sha256 == model_action.result_schema_sha256
        assert action.max_runtime_ms == model_action.max_runtime_ms
        assert action.max_output_bytes == model_action.max_output_bytes
        assert action.max_tool_calls == model_action.max_tool_calls
        assert action.available == model_action.available
        assert action.unavailable_reason == model_action.unavailable_reason
        assert action.risk_class == permission.effective_risk
        assert action.allowed_side_effects == permission.allowed_side_effects

    # This action proves the join does not accidentally retain the model-view
    # risk and broad routing side effects when it constructs execution policy.
    photoshop = next(
        item
        for item in execution_manifest.actions
        if item.action_id == "adobe.photoshop.document.open"
    )
    assert model_by_id[photoshop.action_id].risk_class == "A0"
    assert photoshop.risk_class == "A3"
    assert (
        model_by_id[photoshop.action_id].allowed_side_effects
        != photoshop.allowed_side_effects
    )


def test_default_generation_time_is_the_verified_model_time(
    loaded: LoadedModelCapabilityManifest,
) -> None:
    execution_manifest = compile_composition_execution_manifest(
        loaded.manifest,
        loaded.action_authority.registry,
        loaded.action_authority.schema_catalog,
    )
    assert execution_manifest.generated_at_ms == loaded.manifest.generated_at_ms


def test_raw_source_hash_cannot_masquerade_as_capability_manifest_digest(
    loaded: LoadedModelCapabilityManifest,
) -> None:
    raw_source_sha256 = loaded.action_authority.manifest_sha256
    assert raw_source_sha256 != loaded.manifest.sha256
    forged_model = loaded.manifest.model_copy(
        update={"sha256": raw_source_sha256}
    )

    with pytest.raises(SkillSelectionError, match="manifest digest"):
        compile_composition_execution_manifest(
            forged_model,
            loaded.action_authority.registry,
            loaded.action_authority.schema_catalog,
        )

    execution_manifest = compile_composition_execution_manifest(
        loaded.manifest,
        loaded.action_authority.registry,
        loaded.action_authority.schema_catalog,
    )
    assert execution_manifest.sha256 == execution_manifest.computed_sha256()
    assert execution_manifest.sha256 != raw_source_sha256


def test_missing_or_ambiguous_model_action_fails_closed(
    loaded: LoadedModelCapabilityManifest,
) -> None:
    missing = _rehash_manifest(
        loaded.manifest,
        actions=loaded.manifest.actions[:-1],
    )
    with pytest.raises(SkillSelectionError, match="coverage mismatch"):
        compile_composition_execution_manifest(
            missing,
            loaded.action_authority.registry,
            loaded.action_authority.schema_catalog,
        )

    first = loaded.manifest.actions[0]
    duplicate_version = first.model_copy(update={"version": "second-version"})
    ambiguous = _rehash_manifest(
        loaded.manifest,
        actions=tuple(
            sorted(
                (*loaded.manifest.actions, duplicate_version),
                key=lambda item: (item.action_id, item.version),
            )
        ),
    )
    with pytest.raises(SkillSelectionError, match="identity is ambiguous"):
        compile_composition_execution_manifest(
            ambiguous,
            loaded.action_authority.registry,
            loaded.action_authority.schema_catalog,
        )


def test_current_schema_mismatch_and_rehashed_body_drift_fail_closed(
    loaded: LoadedModelCapabilityManifest,
) -> None:
    first = loaded.manifest.actions[0]
    changed = first.model_copy(update={"argument_schema_sha256": OTHER})
    drifted_model = _rehash_manifest(
        loaded.manifest,
        actions=tuple(
            changed if item.action_id == first.action_id else item
            for item in loaded.manifest.actions
        ),
    )
    with pytest.raises(SkillSelectionError, match="current schema mismatch"):
        compile_composition_execution_manifest(
            drifted_model,
            loaded.action_authority.registry,
            loaded.action_authority.schema_catalog,
        )

    catalog = loaded.action_authority.schema_catalog
    schema = catalog.entries[0]
    drifted_schema = replace(
        schema,
        _body_json=canonical_json_bytes(
            {"action": schema.canonical_action_id, "drifted": True}
        ),
    )
    drifted_catalog = _rehash_catalog(
        catalog,
        entries=(drifted_schema, *catalog.entries[1:]),
    )
    assert drifted_catalog.has_valid_sha256()
    with pytest.raises(SkillSelectionError, match="schema body"):
        compile_composition_execution_manifest(
            loaded.manifest,
            loaded.action_authority.registry,
            drifted_catalog,
        )


def test_registry_catalog_digests_source_and_exact_coverage_fail_closed(
    loaded: LoadedModelCapabilityManifest,
) -> None:
    registry = loaded.action_authority.registry
    catalog = loaded.action_authority.schema_catalog

    invalid_registry = registry.model_copy(update={"registry_sha256": ZERO})
    with pytest.raises(SkillSelectionError, match="registry digest"):
        compile_composition_execution_manifest(
            loaded.manifest,
            invalid_registry,
            catalog,
        )

    invalid_catalog = replace(catalog, catalog_sha256=ZERO)
    with pytest.raises(SkillSelectionError, match="catalog digest"):
        compile_composition_execution_manifest(
            loaded.manifest,
            registry,
            invalid_catalog,
        )

    changed_entries = tuple(
        replace(item, source_manifest_sha256=OTHER) for item in catalog.entries
    )
    other_source_catalog = _rehash_catalog(
        catalog,
        entries=changed_entries,
        source_manifest_sha256=OTHER,
    )
    assert other_source_catalog.has_valid_sha256()
    with pytest.raises(SkillSelectionError, match="source manifest mismatch"):
        compile_composition_execution_manifest(
            loaded.manifest,
            registry,
            other_source_catalog,
        )

    missing_schema_catalog = _rehash_catalog(
        catalog,
        entries=catalog.entries[:-1],
    )
    assert missing_schema_catalog.has_valid_sha256()
    with pytest.raises(SkillSelectionError, match="schema coverage mismatch"):
        compile_composition_execution_manifest(
            loaded.manifest,
            registry,
            missing_schema_catalog,
        )
