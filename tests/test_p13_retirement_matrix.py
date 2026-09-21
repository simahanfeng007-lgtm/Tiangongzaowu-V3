"""P13-A: the retirement matrix is a faithful inventory of the live tree.

The matrix is generated from the CURRENT source (no hard-coded counts),
zero-caller retire candidates are guarded against caller drift, retained
modern authorities are pinned, the legacy data-protocol consumer is
recorded for the registry surface, and nothing claims production zero use.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-p13-retirement-map.py"
MATRIX = ROOT / "docs/capability-composition/P13_A_RETIREMENT_MATRIX_2026-09-20.json"


def _matrix():
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def test_rebuild_matches_committed_matrix():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_every_surface_exists_and_is_classified():
    for entry in _matrix()["entries"]:
        # DELETED entries correctly have exists=False
        if "DELETED" not in entry.get("note", ""):
            assert entry["exists"], entry["path"]
        assert entry["kind"] in {
            "legacy_write_authority", "execution_entry", "compatibility_shim",
            "read_projection", "historical_read", "retained_modern",
            "retire_candidate"}
        assert entry["migration_target"] and entry["note"]


def test_retire_candidates_really_have_no_callers():
    for entry in _matrix()["entries"]:
        if entry["kind"] == "retire_candidate":
            assert entry["caller_count"] == 0, entry["path"]


def test_retained_modern_authorities_are_pinned():
    kinds = {e["path"]: e["kind"] for e in _matrix()["entries"]}
    for pinned in (
            "src/life_service/memory_coordinator.py",
            "src/life_service/memory_invalidation.py",
            "src/world_understanding/capability_composition/capability_experience_api.py"):
        assert kinds.get(pinned) == "retained_modern", pinned


def test_registry_data_protocol_consumer_is_recorded():
    entry = next(e for e in _matrix()["entries"]
                 if e["path"].endswith("zhili/nengli_zhuche.py"))
    assert entry["kind"] == "compatibility_shim"
    assert any(c.endswith("duihua_qiaojie.py") for c in entry["data_consumers"])


def test_no_production_zero_use_is_claimed():
    matrix = _matrix()
    assert matrix["summary"]["zero_production_use_proven"] is False
    assert "P13-B" in matrix["honesty"] or "zero-use" in matrix["honesty"] \
        or "zero use" in matrix["honesty"]
