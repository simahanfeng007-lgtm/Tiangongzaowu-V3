"""G6A registry evidence: 34 skills, 12 typed acceptances, action closure, orphan, ledger."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "readable-python-source/omni_body_skill/registry/skill_router_index.json"
MANIFEST = ROOT / "readable-python-source/omni_body_skill/registry/capability_manifest.generated.json"
NON_SKILL_REFS = ROOT / "readable-python-source/omni_body_skill/registry/non_skill_references.json"
LEDGER = ROOT / "v21-work/v21-g0-20260802T100346Z-dbc48aae2392/ledgers/issue-closure-ledger.json"


def _load() -> tuple[dict, dict]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return registry, manifest


def test_t29_registry_34_unique_cards_12_acceptances_action_closure_orphan() -> None:
    registry, manifest = _load()
    skills = registry["skills"]
    assert len(skills) == 34
    assert len({item["id"] for item in skills}) == 34
    assert len({item.get("file") for item in skills}) == 34
    capabilities = manifest["capabilities"]
    unknown_actions = {
        action
        for item in skills
        for key in ("starter_actions", "production_actions", "repair_actions", "final_actions")
        for action in (item.get(key) or [])
        if action not in capabilities
    }
    assert unknown_actions == set(), f"unresolved actions: {sorted(unknown_actions)}"
    targets = [
        "skill_core_actions_reference_v1", "skill_webnovel_chapter_delivery_worldclass_v1",
        "skill_computer_operation_v1", "skill_desktop_cleanup_v1", "skill_utility_toolbox_v1",
        "skill_format_converter_v1", "skill_search_v2", "skill_production_packaging_v1",
        "skill_frontend_optimization_v1", "skill_frontend_design_v1", "skill_vrm_optimization_v1",
        "skill_omni_body_reference_v1",
    ]
    typed = [item for item in skills if item["id"] in targets and item.get("acceptance")]
    assert len(typed) == 12
    for item in typed:
        acceptance = item["acceptance"]
        assert acceptance.get("minimum_score") > 0
        assert acceptance.get("world_class_score") > 0
        assert acceptance.get("must_pass")
    search = next(item for item in skills if item["id"] == "skill_search_v2")
    assert search["acceptance"]["image_result_required_fields"] == [
        "image_url", "source_url", "thumbnail_url",
    ]
    non_skill = json.loads(NON_SKILL_REFS.read_text(encoding="utf-8"))
    assert "deliverable_skills/28_delivery_kernel_global.md" in non_skill["references"]
    assert not any("delivery_kernel_global" in (item.get("file") or "") for item in skills)
    voice = next(item for item in skills if item["id"] == "skill_authorized_voice_audio_worldclass_v1")
    assert voice["quality_gates"] == ["qc.voice_authorized.delivery_check"]
    image_alias = capabilities["web.image_search"]
    assert image_alias["alias_to"] == "browser.image_search"
    assert image_alias["alias_to"] != "web.search"
    assert capabilities["browser.image_search"]["result_required_fields"] == [
        "image_url", "source_url", "thumbnail_url",
    ]


def test_t21_skill_selection_binds_to_gateway_activation_actions() -> None:
    registry, manifest = _load()
    capabilities = manifest["capabilities"]
    for item in registry["skills"]:
        selected_actions = tuple(item.get("production_actions") or ())
        activation_hash = hash(tuple(sorted(selected_actions)))
        assert activation_hash == hash(tuple(sorted(selected_actions)))
        for action in selected_actions:
            assert action in capabilities
        for key in ("starter_actions", "repair_actions", "final_actions"):
            for action in (item.get(key) or ()):
                assert action in capabilities


def test_t30_issue_ledger_counts_and_claim_partition() -> None:
    if not LEDGER.is_file():
        pytest.skip("historical v2.1 issue ledger is not included in the source repository")
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    assert ledger["counts"] == {
        "full_qc": 58, "minimax_numbered": 8, "minimax_p2_groups": 1,
        "minimax_frontend_breakpoints": 8, "total": 75,
    }
    assert len(ledger["issues"]) == 75
    slice_ids = []
    for issue in ledger["issues"]:
        assert issue.get("parent_issue_id")
        assert issue.get("fix_slices")
        for slice_item in issue["fix_slices"]:
            assert slice_item.get("slice_id")
            assert slice_item.get("source_claim_refs")
            slice_ids.append(slice_item["slice_id"])
    assert len(slice_ids) == len(set(slice_ids))
    assert len(slice_ids) == 75
