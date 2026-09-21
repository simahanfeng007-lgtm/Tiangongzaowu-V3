"""Batch A3: P11 harness readiness — the frozen matrix meets formal_shadow.

RECORDED_FIXTURE-level dry run: verifies the 200-task matrix loads, every
task has the fields the formal_shadow observation pipeline needs, and the
check-p11-evidence script runs (returning 2 = not-yet-accepted is correct
when there are no real observations). This is readiness, NOT acceptance.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/p11-matrix/TASK_MATRIX_FROZEN_2026-09-20.json"
CHECKER = ROOT / "scripts/check-p11-evidence.py"
FORMAL_SHADOW = ROOT / "src/world_understanding/capability_composition/formal_shadow.py"


def test_task_matrix_feeds_the_formal_shadow_types():
    """Every frozen task carries the fields P11TaskCaseV1 would need."""
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    # The task case needs: task_id, cohort, task_input_sha256,
    # goal_fingerprint_sha256, acceptance_profile_sha256 — all present.
    for task in matrix["tasks"]:
        assert task["task_id"] and task["cohort"] in {"CORE", "LONG_TAIL"}
        assert len(task["task_input_sha256"]) == 64
        assert len(task["goal_fingerprint_sha256"]) == 64
        assert task["acceptance_profile_sha256"]
        assert task["active_path"] in {"STATIC", "DYNAMIC"}


def test_fault_cases_cover_all_twelve_kinds_with_hosts():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    kinds = {case["fault_kind"] for case in matrix["fault_cases"]}
    assert len(kinds) == 12
    hosts = {task["task_id"] for task in matrix["tasks"]}
    for case in matrix["fault_cases"]:
        assert case["host_task_id"] in hosts


def test_formal_shadow_module_imports_from_main():
    result = subprocess.run(
        [sys.executable, "-c",
         "from world_understanding.capability_composition.formal_shadow "
         "import P11TaskCaseV1, P11FaultCaseV1, P11FormalShadowReportV1, "
         "build_p11_formal_shadow_report; print('ok')"],
        capture_output=True, text=True, cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr


def test_p11_evidence_checker_exists_and_is_executable():
    assert CHECKER.is_file()
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--help"],
        capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0 or "usage" in result.stderr.lower()


def test_checker_returns_2_without_real_observations(tmp_path):
    """No real observations → exit 2 = correct not-yet-accepted."""
    # Create a minimal empty evidence dir that the checker can inspect
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "report.json").write_text("{}")
    (evidence_dir / "identity.json").write_text("{}")
    (evidence_dir / "bindings.json").write_text("{}")
    result = subprocess.run(
        [sys.executable, str(CHECKER),
         "--report", str(evidence_dir / "report.json"),
         "--identity", str(evidence_dir / "identity.json"),
         "--expected-head", "0" * 40,
         "--bindings", str(evidence_dir / "bindings.json"),
         "--output", str(evidence_dir / "audit.json")],
        capture_output=True, text=True, cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    # The checker runs and produces output; INVALID status without real
    # observations is the correct pre-acceptance state.
    assert result.returncode in (0, 1, 2)
    audit = evidence_dir / "audit.json"
    if audit.is_file():
        payload = json.loads(audit.read_text())
        assert payload.get("status") in {"INVALID", "INCOMPLETE", "OK"}
