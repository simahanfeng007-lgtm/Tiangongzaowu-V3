"""R1G pre-freeze: the P11 task matrix is exact, unique and honest.

The frozen input side of the formal matrix: 200 semantically distinct
tasks (CORE 80 + LONG_TAIL 120) with unique digests, 40 fault cases over
the twelve frozen kinds, and no acceptance claim anywhere.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-p11-task-matrix.py"
MATRIX = ROOT / "docs/p11-matrix/TASK_MATRIX_FROZEN_2026-09-20.json"

_FAULT_KINDS = {
    "source_revision_drift", "tool_unavailable", "misleading_experience",
    "permission_denied", "provider_unavailable", "schema_mismatch",
    "stale_manifest", "workspace_drift", "ambiguous_effect",
    "verifier_unavailable", "context_truncation", "interrupted_run",
}


def _matrix():
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def test_rebuild_matches_committed_matrix():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr


def test_exact_cohort_split_and_unique_digests():
    matrix = _matrix()
    assert matrix["summary"]["tasks_total"] == 200
    assert matrix["summary"]["core"] == 80
    assert matrix["summary"]["long_tail"] == 120
    assert matrix["summary"]["unique_input_digests"] == 200
    assert matrix["summary"]["unique_goal_fingerprints"] == 200


def test_every_task_is_semantically_distinct_and_classified():
    seen_prompts = set()
    for task in _matrix()["tasks"]:
        assert task["prompt"].strip() and task["prompt"] not in seen_prompts
        seen_prompts.add(task["prompt"])
        assert task["cohort"] in {"CORE", "LONG_TAIL"}
        assert task["active_path"] in {"STATIC", "DYNAMIC"}
        assert task["category"]
        profile = task["acceptance_profile"]
        assert profile["must_deliver"] and profile["must_satisfy"]
        assert profile["must_not_fabricate"] is True
        assert task["acceptance_profile_sha256"]


def test_fault_matrix_covers_twelve_kinds_over_forty_cases():
    matrix = _matrix()
    assert matrix["summary"]["fault_cases"] == 40
    kinds = [case["fault_kind"] for case in matrix["fault_cases"]]
    assert set(kinds) == _FAULT_KINDS
    assert all(case["expected_containment"] for case in matrix["fault_cases"])
    task_ids = {task["task_id"] for task in matrix["tasks"]}
    assert all(case["host_task_id"] in task_ids
               for case in matrix["fault_cases"])


def test_no_acceptance_is_claimed():
    matrix = _matrix()
    assert "NOT acceptance" in matrix["honesty"]
    assert "R03" in matrix["honesty"]
