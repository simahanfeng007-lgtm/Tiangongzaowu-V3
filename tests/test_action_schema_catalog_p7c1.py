from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from contracts import canonical_sha256
from omni_body_skill import tool_contracts as tool_contracts_module
from omni_body_skill.tool_contracts import (
    action_schema_descriptor,
    build_action_schema_catalog,
)
from total_gateway.action_registry import (
    ActionRegistryError,
    compile_action_authority,
    load_action_authority,
    load_action_registry,
)
from total_gateway.skill_selection import load_model_capability_manifest
from v3.fact_kernel import compile_manifest


H = "a" * 64
ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    capabilities = {
        "skill.list": {
            "id": "skill.list",
            "risk": "A0",
            "effect": "read",
            "handler": "_action_skill_list",
            "alias_to": "",
            "executable": True,
        },
        "demo.skill.list": {
            "id": "demo.skill.list",
            "risk": "A0",
            "effect": "read",
            "handler": "alias:skill.list",
            "alias_to": "skill.list",
            "executable": True,
        },
        "demo.opaque": {
            "id": "demo.opaque",
            "risk": "A0",
            "effect": "verify",
            "handler": "_action_demo_opaque",
            "alias_to": "",
            "executable": True,
        },
    }
    descriptors = build_action_schema_catalog(capabilities)
    for action_id, descriptor in descriptors.items():
        capabilities[action_id].update(descriptor)
    source_hash = canonical_sha256(capabilities)
    return {
        "schema": "tiangong.v3.capability_manifest.v1",
        "source_hash": source_hash,
        "total": len(capabilities),
        "executable": len(capabilities),
        "unavailable": 0,
        "capabilities": capabilities,
        "validation": {
            "ok": True,
            "source_hash": source_hash,
            "executable_without_route": [],
        },
    }


def test_manifest_rows_carry_canonical_explicit_and_opaque_schema_authority() -> None:
    manifest = _manifest()
    explicit = manifest["capabilities"]["skill.list"]
    alias = manifest["capabilities"]["demo.skill.list"]
    opaque = manifest["capabilities"]["demo.opaque"]

    assert explicit["argument_schema_kind"] == "EXPLICIT"
    assert explicit["argument_schema_sha256"] == canonical_sha256(
        explicit["argument_schema"]
    )
    assert alias["argument_schema"] == explicit["argument_schema"]
    assert alias["argument_schema_sha256"] == explicit["argument_schema_sha256"]
    assert alias["argument_validator_source_sha256"] == explicit[
        "argument_validator_source_sha256"
    ]
    assert opaque["argument_schema_kind"] == "OPAQUE"
    assert opaque["argument_schema"] == action_schema_descriptor(
        "demo.opaque"
    )["argument_schema"]


def test_loaded_authority_returns_registry_and_immutable_schema_catalog(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    path = tmp_path / "capability.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    loaded = load_action_authority(path.resolve(), generated_at_ms=1)
    assert loaded.registry == load_action_registry(path.resolve(), generated_at_ms=1)
    assert loaded.manifest_sha256 == canonical_sha256(manifest)
    assert loaded.manifest_source_hash == manifest["source_hash"]
    assert loaded.manifest["capabilities"]["skill.list"]["id"] == "skill.list"

    resolved = loaded.schema_catalog.resolve(
        "demo.skill.list",
        "omni-registry-v1",
        expected_sha256=manifest["capabilities"]["skill.list"][
            "argument_schema_sha256"
        ],
        require_explicit=True,
    )
    assert resolved.action_id == "demo.skill.list"
    assert resolved.canonical_action_id == "skill.list"
    first = resolved.body()
    first["args"]["intent"] = "tampered"
    assert resolved.body()["args"]["intent"] != "tampered"

    with pytest.raises(ActionRegistryError, match="explicit"):
        loaded.schema_catalog.resolve(
            "demo.opaque",
            "omni-registry-v1",
            require_explicit=True,
        )


def test_exact_validation_rejects_every_normalization_and_is_side_effect_free(
    tmp_path: Path,
) -> None:
    loaded = compile_action_authority(_manifest(), generated_at_ms=1)
    schema = loaded.schema_catalog.resolve(
        "demo.skill.list",
        "omni-registry-v1",
        require_explicit=True,
    )

    accepted = schema.validate_exact(
        "demo.skill.list",
        "",
        {"intent": "inspect"},
        workspace=tmp_path,
        available_actions=("demo.skill.list", "skill.list"),
    )
    assert accepted["ok"] is True
    assert accepted["action"] == "demo.skill.list"
    assert accepted["target"] == ""
    assert accepted["args"] == {"intent": "inspect"}

    with pytest.raises(ActionRegistryError, match="normalization"):
        schema.validate_exact(
            "demo.skill.list",
            "  target  ",
            {"intent": "inspect"},
            workspace=tmp_path,
            available_actions=("demo.skill.list", "skill.list"),
        )
    with pytest.raises(ActionRegistryError, match="identity"):
        schema.validate_exact(
            "skill.list",
            "",
            {},
            workspace=tmp_path,
            available_actions=("demo.skill.list", "skill.list"),
        )
    with pytest.raises(ActionRegistryError, match="validator source hash"):
        replace(schema, validator_source_sha256="0" * 64).validate_exact(
            "demo.skill.list",
            "",
            {},
            workspace=tmp_path,
            available_actions=("demo.skill.list", "skill.list"),
        )


@pytest.mark.parametrize(
    "mutate, message",
    (
        (
            lambda document: document["capabilities"]["skill.list"].update(
                argument_schema_sha256="0" * 64
            ),
            "schema hash",
        ),
        (
            lambda document: document["capabilities"]["demo.skill.list"].update(
                argument_schema={"action": "demo.skill.list", "args": {}}
            ),
            "schema",
        ),
        (
            lambda document: document["capabilities"]["skill.list"].update(
                argument_validator_source_sha256="0" * 64
            ),
            "schema|validator",
        ),
    ),
)
def test_schema_hash_alias_and_validator_tampering_fail_closed(mutate, message) -> None:
    manifest = _manifest()
    mutate(manifest)
    manifest["source_hash"] = canonical_sha256(manifest["capabilities"])
    manifest["validation"]["source_hash"] = manifest["source_hash"]
    with pytest.raises(ActionRegistryError, match=message):
        compile_action_authority(manifest, generated_at_ms=1)


def test_strict_loader_rejects_duplicate_schema_keys(tmp_path: Path) -> None:
    manifest = _manifest()
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    raw = raw.replace(
        '"argument_schema_kind": "EXPLICIT"',
        '"argument_schema_kind": "EXPLICIT", "argument_schema_kind": "EXPLICIT"',
        1,
    )
    path = tmp_path / "duplicate.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ActionRegistryError, match="duplicate JSON keys"):
        load_action_authority(path.resolve(), generated_at_ms=1)


def test_catalog_resolution_requires_exact_version_and_hash() -> None:
    catalog = compile_action_authority(_manifest(), generated_at_ms=1).schema_catalog
    with pytest.raises(ActionRegistryError, match="version"):
        catalog.resolve("skill.list", "stale-version")
    with pytest.raises(ActionRegistryError, match="hash"):
        catalog.resolve(
            "skill.list",
            "omni-registry-v1",
            expected_sha256="0" * 64,
        )


def test_tool_contract_import_does_not_eagerly_import_body_runtime() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import omni_body_skill.tool_contracts; "
                "assert 'omni_body_skill.tools' not in sys.modules; "
                "assert 'omni_body_skill.tools.omni_body_tool' not in sys.modules"
            ),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_live_fact_manifest_carries_the_same_schema_authority() -> None:
    class DemoRuntime:
        def _action_skill_list(self):
            return None

    actions = {
        "skill.list": {
            "risk": "A0",
            "effect": "read",
            "implemented": True,
        },
        "demo.skill.list": {
            "risk": "A0",
            "effect": "read",
            "implemented": True,
            "alias_to": "skill.list",
        },
    }
    catalog = build_action_schema_catalog(actions)
    manifest = compile_manifest(
        actions,
        DemoRuntime,
        action_schema_catalog=catalog,
    ).to_dict()
    canonical = manifest["capabilities"]["skill.list"]
    alias = manifest["capabilities"]["demo.skill.list"]
    assert canonical["argument_schema_kind"] == "EXPLICIT"
    assert alias["argument_schema"] == canonical["argument_schema"]
    assert alias["argument_schema_sha256"] == canonical[
        "argument_schema_sha256"
    ]
    assert alias["argument_validator_source_sha256"] == canonical[
        "argument_validator_source_sha256"
    ]

    broken = {key: dict(value) for key, value in catalog.items()}
    broken["skill.list"]["argument_schema_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="descriptor hash"):
        compile_manifest(
            actions,
            DemoRuntime,
            action_schema_catalog=broken,
        )


def test_manifest_generator_writes_only_src_authority(tmp_path: Path) -> None:
    script_path = ROOT / "scripts" / "sync_omni_capability_manifest.py"
    spec = importlib.util.spec_from_file_location("schema_manifest_sync", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    authority_root = tmp_path / "src" / "omni_body_skill"
    (authority_root / "registry").mkdir(parents=True)
    (authority_root / "tools").mkdir(parents=True)
    shutil.copy2(
        ROOT / "src" / "omni_body_skill" / "tool_contracts.py",
        authority_root / "tool_contracts.py",
    )
    manifest = _manifest()
    for row in manifest["capabilities"].values():
        for key in (
            "argument_schema",
            "argument_schema_sha256",
            "argument_schema_kind",
            "argument_validator_source_sha256",
        ):
            row.pop(key)
    manifest["source_hash"] = canonical_sha256(manifest["capabilities"])
    manifest["validation"]["source_hash"] = manifest["source_hash"]
    manifest_path = authority_root / "registry" / "capability_manifest.generated.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    mirror = (
        tmp_path
        / "readable-python-source"
        / "omni_body_skill"
        / "registry"
        / "capability_manifest.generated.json"
    )
    mirror.parent.mkdir(parents=True)
    mirror.write_text("mirror-sentinel", encoding="utf-8")

    module._sync_manifest(
        manifest_path,
        {},
        tool_contracts=tool_contracts_module,
    )
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written["capabilities"]["skill.list"]["argument_schema_kind"] == (
        "EXPLICIT"
    )
    assert mirror.read_text(encoding="utf-8") == "mirror-sentinel"


def test_model_capability_projection_uses_manifest_schema_hash() -> None:
    path = (
        ROOT
        / "src"
        / "omni_body_skill"
        / "registry"
        / "capability_manifest.generated.json"
    ).resolve()
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    loaded = load_model_capability_manifest(
        path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        component_manifest_hash=H,
        generated_at_ms=1,
    )
    action = next(
        item for item in loaded.manifest.actions if item.action_id == "skill.list"
    )
    assert action.argument_schema_sha256 == document["capabilities"][
        "skill.list"
    ]["argument_schema_sha256"]
    assert loaded.action_authority.schema_catalog.resolve(
        "skill.list", "omni-registry-v1"
    ).argument_schema_sha256 == action.argument_schema_sha256
    assert (
        loaded.action_authority.registry.source_manifest_sha256
        == loaded.action_authority.manifest_sha256
    )
