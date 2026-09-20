"""P17-B seed: the obligation scan snapshot stays reproducible and clean.

The authoritative-tree marker scan must rebuild byte-identically, every hit
must be classified, and the current snapshot must show zero needs_review —
the mechanical half of the 'no unexplained obligation' book.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit-p17-traceability.py"
SNAPSHOT = ROOT / "docs/capability-composition/P17_B_OBLIGATION_SCAN_2026-09-20.json"
SEED = ROOT / "docs/capability-composition/P17_B_TRACEABILITY_SEED_2026-09-20.md"


def test_rebuild_matches_committed_snapshot():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_every_hit_is_classified():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot["schema"] == "tiangong.p17.obligation-scan.v1"
    for hit in snapshot["hits"]:
        assert hit["classification"] in {
            "explicit_feature_use", "needs_review"}


def test_snapshot_has_no_unexplained_hits():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot["summary"]["needs_review"] == 0, [
        h for h in snapshot["hits"] if h["classification"] == "needs_review"]
    assert snapshot["summary"]["total_hits"] == \
        snapshot["summary"]["explicit_feature_uses"]


def test_traceability_seed_covers_all_twenty_clauses():
    text = SEED.read_text(encoding="utf-8")
    for number in range(1, 21):
        assert f"| {number} |" in text, f"clause {number} missing"
    assert "BLOCKED" in text  # honest blockers stay explicit
    assert "母版原文逐字对齐待原件" in text  # the master-copy caveat stays
