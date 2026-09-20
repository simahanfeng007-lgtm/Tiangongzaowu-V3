"""P12-R1E: the capability parity table is a faithful re-enumeration.

The table is generated from the CURRENT index/manifest/documents (no
hard-coded count), every required action is mechanically covered by the
current generated manifest, the retained machine-fact/shared-security
actions are pinned, and — the honesty contract — no item may claim task
parity without formal evidence.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-p12-capability-parity.py"
TABLE = ROOT / "docs/capability-composition/P12_R1E_CAPABILITY_PARITY_2026-09-20.json"
INDEX = ROOT / "src/omni_body_skill/registry/skill_router_index.json"
MANIFEST = ROOT / "src/omni_body_skill/registry/capability_manifest.generated.json"
SKILLS = ROOT / "src/omni_body_skill"


def _table():
    return json.loads(TABLE.read_text(encoding="utf-8"))


def test_rebuild_matches_committed_table():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_table_reenumerates_the_current_index_exactly():
    index = json.loads(INDEX.read_text(encoding="utf-8"))["skills"]
    table = _table()
    assert table["schema"] == "tiangong.p12.capability-parity.v1"
    assert table["summary"]["items_total"] == len(index)
    assert [item["id"] for item in table["items"]] == [
        entry["id"] for entry in index]
    for item in table["items"]:
        doc = SKILLS / item["file"]
        assert doc.is_file()
        assert item["required_actions"], item["id"]


def test_every_required_action_is_covered_by_the_current_manifest():
    capabilities = json.loads(
        MANIFEST.read_text(encoding="utf-8"))["capabilities"]
    table = _table()
    assert table["summary"]["uncovered_actions"] == []
    for item in table["items"]:
        assert item["action_surface_covered"] is True, item["id"]
        assert item["missing_actions"] == []
        assert set(item["required_actions"]) <= set(capabilities)


def test_retained_shared_actions_are_pinned():
    table = _table()
    retained = {row["action"]: row for row in table["retained_shared_actions"]}
    assert set(retained) == {"skill.step.check", "skill.progress.report"}
    for action, row in retained.items():
        assert row["present_in_current_manifest"] is True, action


def test_honesty_contract_no_item_claims_task_parity():
    table = _table()
    assert table["summary"]["task_parity_open_items"] == \
        table["summary"]["items_total"]
    for item in table["items"]:
        assert item["task_parity"] == "REQUIRES_TASK_PARITY"
    assert "NOT behavioural parity" in table["honesty"]


def test_high_risk_items_are_flagged_not_hidden():
    table = _table()
    flagged = sum(1 for item in table["items"] if item["high_risk_effects"])
    assert flagged == table["summary"]["high_risk_effect_items"]
    for item in table["items"]:
        if item["high_risk_effects"]:
            assert set(item["action_effects"]) & {
                "write", "create", "update", "execute", "send"}
