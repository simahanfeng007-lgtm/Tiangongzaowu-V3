"""P17-A dynamic-surface guards + P13-D deletion-drill outcome.

The dynamic-surface snapshot must stay reproducible; retire candidates
must never appear as dynamic import targets; and the P13-D drill's
outcome is pinned: capability_lifecycle is deletion-safe (85 tests
green without it) while life_learning_memory is NOT (its hidden
consumer proved the matrix wrong, which the fixed pattern now records).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DYNAMIC_SCRIPT = ROOT / "scripts/audit-p17-dynamic-surface.py"
DYNAMIC_SNAPSHOT = ROOT / "docs/capability-composition/P17_A_DYNAMIC_SURFACE_2026-09-20.json"
MATRIX_SCRIPT = ROOT / "scripts/audit-p13-retirement-map.py"
MATRIX = ROOT / "docs/capability-composition/P13_A_RETIREMENT_MATRIX_2026-09-20.json"


def test_dynamic_surface_rebuilds():
    result = subprocess.run(
        [sys.executable, str(DYNAMIC_SCRIPT), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_every_dynamic_hit_is_classified():
    snapshot = json.loads(DYNAMIC_SNAPSHOT.read_text(encoding="utf-8"))
    for hit in snapshot["hits"]:
        assert hit["classification"] in {
            "explicit_feature_use", "needs_review", "verified"}
        if hit["classification"] == "verified":
            assert hit["verified_category"]


def test_retire_candidates_never_appear_as_dynamic_targets():
    snapshot = json.loads(DYNAMIC_SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot["summary"]["retire_candidates_as_dynamic_targets"] == 0


def test_p13d_drill_outcome_is_recorded():
    """The deletion drill's two verdicts are pinned by the matrix."""
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    by_path = {e["path"]: e for e in matrix["entries"]}
    lifecycle = by_path["src/life_service/capability_lifecycle.py"]
    assert lifecycle["kind"] == "retire_candidate"
    assert lifecycle["caller_count"] == 0
    memory = by_path["src/life_service/life_learning_memory.py"]
    # P13-C deep pass reclassified it: the drill proved it is consumed, and
    # reading the source proved it is the P15 M4 strategy library — modern.
    assert memory["kind"] == "retained_modern"
    assert memory["caller_count"] >= 1
    assert "STRATEGY LIBRARY" in memory["note"]


def test_retirement_matrix_rebuilds():
    result = subprocess.run(
        [sys.executable, str(MATRIX_SCRIPT), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_retire_candidate_stays_singleton_after_drill():
    """After the P13-D correction, exactly ONE true retire candidate."""
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    candidates = [e for e in matrix["entries"]
                  if e["kind"] == "retire_candidate"]
    assert len(candidates) == 1
    assert candidates[0]["path"].endswith("capability_lifecycle.py")


def test_all_dynamic_hits_are_verified_or_classified():
    """P17-A deep pass: every hit carries a verification category."""
    snapshot = json.loads(DYNAMIC_SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot["summary"]["needs_review"] == 0
    assert snapshot["summary"]["verified"] == snapshot["summary"]["total_hits"]


def test_verified_ledger_categories_are_known():
    ledger = json.loads(
        (ROOT / "docs/capability-composition/P17_A_DYNAMIC_VERIFIED_2026-09-20.json")
        .read_text(encoding="utf-8"))
    known = {"model_field_iteration", "structural_field_access",
             "lazy_module_import", "handler_dispatch",
             "optional_dependency", "inline_stdlib_import",
             "patch_dispatch", "route_handler_dispatch",
             "tool_handler_dispatch"}
    for key, entry in ledger["annotations"].items():
        assert entry["category"] in known, f"{key}: {entry['category']}"
        assert entry["reason"]
