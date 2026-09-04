from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from contracts import canonical_sha256
from omni_body_skill.tool_contracts import build_action_schema_catalog
from total_gateway.action_registry import ActionRegistryError, compile_action_authority
from total_gateway.skill_selection import load_model_capability_manifest


ROOT = Path(__file__).resolve().parents[1]
H = "a" * 64
EXPLICIT_ACTIONS = {
    "life.body.state.query",
    "pptx.read",
    "qc.ppt.delivery_check",
    "skill.get",
    "skill.list",
    "skill.read",
}


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
    for action_id, descriptor in build_action_schema_catalog(capabilities).items():
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


def _skill_list_result(action_id: str = "skill.list") -> dict:
    return {
        "schema": "tiangong.v3.omni_body.v1",
        "ok": True,
        "zhuangtai": "wancheng",
        "gongju": "omni_body",
        "action": action_id,
        "target": "",
        "result": {
            "success": True,
            "op_id": "op-1",
            "action": action_id,
            "risk_level": "A0",
            "elapsed_seconds": 0.001,
            "result": {"items": [], "selection": {}},
            "evidence": {},
        },
        "llm_brief": "completed",
        "evidence": {},
    }


def test_live_manifest_explicit_allowlist_is_closed() -> None:
    path = (
        ROOT
        / "src"
        / "omni_body_skill"
        / "registry"
        / "capability_manifest.generated.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    explicit = {
        action_id
        for action_id, raw in document["capabilities"].items()
        if raw["result_schema_kind"] == "EXPLICIT"
    }
    assert explicit == EXPLICIT_ACTIONS
    for action_id, raw in document["capabilities"].items():
        assert raw["result_schema_sha256"] == canonical_sha256(raw["result_schema"])
        assert raw["result_schema"]["kind"] == raw["result_schema_kind"]
        if action_id in EXPLICIT_ACTIONS:
            assert raw["risk"] == "A0"
            assert raw["effect"] in {"read", "verify"}
            assert raw["value_schema_kind"] == "EXPLICIT"
            assert raw["value_schemas"]
        else:
            assert raw["result_schema_kind"] == "OPAQUE"
            assert raw["value_schema_kind"] == "OPAQUE"
            assert raw["value_schemas"] == {}


def test_alias_copies_result_and_value_authority_byte_for_byte() -> None:
    capabilities = _manifest()["capabilities"]
    canonical = capabilities["skill.list"]
    alias = capabilities["demo.skill.list"]
    for field in (
        "argument_schema",
        "argument_schema_sha256",
        "argument_schema_kind",
        "argument_validator_source_sha256",
        "result_schema",
        "result_schema_sha256",
        "result_schema_kind",
        "result_validator_source_sha256",
        "value_schemas",
        "value_schema_kind",
        "value_validator_source_sha256",
    ):
        assert alias[field] == canonical[field]


def test_catalog_validates_exact_result_and_declared_values() -> None:
    catalog = compile_action_authority(_manifest(), generated_at_ms=1).schema_catalog
    catalog.validate_result_exact(
        "skill.list",
        "omni-registry-v1",
        _skill_list_result(),
    )
    catalog.validate_result_exact(
        "demo.skill.list",
        "omni-registry-v1",
        _skill_list_result("demo.skill.list"),
    )

    entry = catalog.resolve(
        "skill.list",
        "omni-registry-v1",
        require_result_explicit=True,
    )
    items = next(item for item in entry.value_schemas if item.value_schema_id == "items")
    assert catalog.resolve_value_schema(
        "skill.list",
        "omni-registry-v1",
        items.value_schema_sha256,
    ) == items
    catalog.validate_value_exact(items.value_schema_sha256, [])

    with pytest.raises(ActionRegistryError, match="type"):
        catalog.validate_value_exact(items.value_schema_sha256, {})
    with pytest.raises(ActionRegistryError, match="identity"):
        catalog.validate_result_exact(
            "skill.list",
            "omni-registry-v1",
            _skill_list_result("demo.skill.list"),
        )
    with pytest.raises(ActionRegistryError, match="absent"):
        catalog.validate_value_exact("0" * 64, [])


def test_catalog_rejects_opaque_and_stale_result_authority() -> None:
    catalog = compile_action_authority(_manifest(), generated_at_ms=1).schema_catalog
    with pytest.raises(ActionRegistryError, match="not explicit"):
        catalog.validate_result_exact(
            "demo.opaque",
            "omni-registry-v1",
            {},
        )

    entries = tuple(
        replace(item, result_validator_source_sha256="0" * 64)
        if item.action_id == "skill.list"
        else item
        for item in catalog.entries
    )
    draft = replace(catalog, entries=entries, catalog_sha256="0" * 64)
    stale = replace(draft, catalog_sha256=canonical_sha256(draft.payload()))
    with pytest.raises(ActionRegistryError, match="validator source hash"):
        stale.validate_result_exact(
            "skill.list",
            "omni-registry-v1",
            _skill_list_result(),
        )


def test_model_projection_uses_exact_catalog_result_hash() -> None:
    path = (
        ROOT
        / "src"
        / "omni_body_skill"
        / "registry"
        / "capability_manifest.generated.json"
    ).resolve()
    raw = path.read_bytes()
    loaded = load_model_capability_manifest(
        path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        component_manifest_hash=H,
        generated_at_ms=1,
    )
    for action in loaded.manifest.actions:
        schema = loaded.action_authority.schema_catalog.resolve(
            action.action_id,
            "omni-registry-v1",
        )
        assert action.result_schema_sha256 == schema.result_schema_sha256
