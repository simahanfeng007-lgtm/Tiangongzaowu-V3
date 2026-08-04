"""Frozen v2.1 gate runner.

This runner deliberately treats absent evidence as NOT_TESTED.  It never
promotes a gate from prose, historical test output, or a changed build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORK_BASE = Path(r"C:\TG3Clean\v21-work")
SPEC = Path(r"C:\Users\77571\Desktop\天工造物v3-v2.1-AI落地规格-20260802.yaml")
FROZEN_SPEC_SHA256 = "84eb745308c051fcc3679f8a83253c7e0fae1cf256be8502b0672b36142be7ea"
FROZEN_IMPORTS = {
    Path(r"C:\Users\77571\Desktop\天工造物v3-生命链核心架构简化修复设计-v2-20260802.md"):
        "442dff898de187d971b40fdfc5d3a67447bb937265e55934a70a0337a11e6bcd",
    Path(r"C:\Users\77571\Desktop\天工造物v3-全量逻辑与前端质检报告-20260801.txt"):
        "b8d71f3fc dad207c8030631c5c3328a84528be5f5bdd8f79236e7dc85176ce86".replace(" ", ""),
    Path(r"C:\Users\77571\Desktop\天工造物v3-MiniMax真实模型与全技能质检报告-20260802.txt"):
        "4196837cbe cdddffb9817347011af24cfb99c01b654a76e21489e5ec7a556148".replace(" ", ""),
}
GATE_DEPENDENCIES = {
    "G0": (), "G1": ("G0",), "G2": ("G1",), "G3": ("G2",),
    "G4": ("G3",), "G5": ("G4",), "G6A": ("G5",), "G6B": ("G6A",),
}

_CURRENT_GATE: str = "G0"

EXPECTED_T22_ARTIFACTS = [
    "learning_cards/sandwich_reading.md",
    "output/e2e/02-core.md",
    "output/e2e/03-long.md",
    "output/e2e/04-proposal.docx",
    "output/e2e/05-report.pptx",
    "output/e2e/06-calc.py",
    "output/e2e/07-research.md",
    "output/e2e/08-video.mp4",
    "output/e2e/09-novel/chapter-01.md",
    "output/e2e/10-chapter.docx",
    "output/e2e/11-poster.png",
    "output/e2e/12-analysis.xlsx",
    "output/e2e/13-minutes.docx",
    "output/e2e/14-sales.docx",
    "output/e2e/15-course.pptx",
    "output/e2e/16-knowledge.md",
    "output/e2e/17-voice.md",
    "output/e2e/18-seo.md",
    "output/e2e/19-calendar.xlsx",
    "output/e2e/20-probe.md",
    "output/e2e/21-browser.png",
    "output/e2e/22-office.docx",
    "output/e2e/23-scene.py",
    "output/e2e/24-python.txt",
    "output/e2e/25-cleanup.md",
    "output/e2e/26-utility.md",
    "output/e2e/27-converter.md",
    "output/e2e/28-search.md",
    "output/e2e/29-packaging.md",
    "output/e2e/30-frontend.md",
    "output/e2e/31-design.md",
    "output/e2e/32-vrm.json",
    "output/e2e/33-map.md",
    "output/e2e/34-omni.txt",
]

# Skill 01 may land its learning card through the real learning.ingest
# channel (source profile with host credentials) or as a local markdown note
# (packaged QA profile without host credentials).  Either real artifact is
# acceptable for T22; the row still must be ok and terminal.
T22_ARTIFACT_ALTERNATIVES = {
    "learning_cards/sandwich_reading.md": [
        "notes/sandwich-reading-method.md",
        "learning-card-sandwich-reading.md",
    ],
    "output/e2e/09-novel/chapter-01.md": [
        "output/e2e/09-novel/正文/第一章.md",
        "output/e2e/09-novel/正文/第一章_来自霜环的信.md",
    ],
}

T22_ARTIFACT_PREFIXES = {
    "output/e2e/09-novel/": "output/e2e/09-novel/",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest() -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(p for p in ROOT.rglob("*") if p.is_file()):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith((".git/", "app/node_modules/", "app/runtime/", "release-stage/", "release-artifacts/")) or (
            "/__pycache__/" in relative or relative.endswith(".pyc") or relative.startswith((".pytest_cache/", ".ruff_cache/"))
        ):
            continue
        item = f"{relative}\0{path.stat().st_size}\0{sha256(path)}\n".encode("utf-8")
        digest.update(item)
        count += 1
    return digest.hexdigest(), count


def frozen_input_result() -> dict[str, Any]:
    inputs = {str(SPEC): (FROZEN_SPEC_SHA256, sha256(SPEC))}
    inputs.update({str(path): (expected, sha256(path)) for path, expected in FROZEN_IMPORTS.items()})
    mismatches = [
        {"path": path, "expected": expected, "actual": actual}
        for path, (expected, actual) in inputs.items() if expected != actual
    ]
    return {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches}


def catalog_baseline_result() -> dict[str, Any]:
    authority = ROOT / "readable-python-source/omni_body_skill/registry/skill_router_index.json"
    mirror = ROOT / "app/backend/tiangong-backend/v3/bundled_skills/omni_body_skill/registry/skill_router_index.json"
    manifest = ROOT / "readable-python-source/omni_body_skill/registry/capability_manifest.generated.json"
    manifest_mirror = ROOT / "app/backend/tiangong-backend/v3/bundled_skills/omni_body_skill/registry/capability_manifest.generated.json"
    current = {
        "skill_router_authority_sha256": sha256(authority),
        "skill_router_mirror_sha256": sha256(mirror),
        "capability_manifest_authority_sha256": sha256(manifest),
        "capability_manifest_mirror_sha256": sha256(manifest_mirror),
    }
    target_keys = [
        "skill_router_authority_sha256",
        "skill_router_mirror_sha256",
        "capability_manifest_authority_sha256",
        "capability_manifest_mirror_sha256",
    ]
    paths = {
        "skill_router_authority_sha256": authority,
        "skill_router_mirror_sha256": mirror,
        "capability_manifest_authority_sha256": manifest,
        "capability_manifest_mirror_sha256": manifest_mirror,
    }
    if _CURRENT_GATE == "G0":
        expected = {
            "skill_router_authority_sha256": "a334160283f64ab61522a3583c33b17425e81db3aa7f95c44f6c65b393bbe776",
            "skill_router_mirror_sha256": "a334160283f64ab61522a3583c33b17425e81db3aa7f95c44f6c65b393bbe776",
            "capability_manifest_authority_sha256": "42d0f3bc4717a015bb664fa1849e1bab24d7e146df51abf8e081fe66c94f7fca",
            "capability_manifest_mirror_sha256": "42d0f3bc4717a015bb664fa1849e1bab24d7e146df51abf8e081fe66c94f7fca",
        }
        mismatches = [
            {"path": str(paths[key]), "expected": v, "actual": current[key]}
            for key, v in expected.items() if current[key] != v
        ]
        return {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches}
    bindings_path = WORK_BASE / "g6a-target-bindings.json"
    if not bindings_path.is_file():
        return {
            "status": "FAIL",
            "reason": "G6A target bindings file missing; bind catalog/capability target hashes and the real 34-skill E2E evidence first",
        }
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    target = bindings.get("catalog_target") or {}
    missing = [key for key in target_keys if not target.get(key)]
    if missing:
        return {"status": "FAIL", "reason": "incomplete G6A catalog target binding", "missing_keys": missing}
    mismatches = [
        {"path": str(paths[key]), "expected": target[key], "actual": current[key]}
        for key in target_keys if current[key] != target[key]
    ]
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "mismatches": mismatches,
        "bindings_path": str(bindings_path),
    }


def t22_skill_all_result(bindings_name: str = "g6a-target-bindings.json") -> dict[str, Any]:
    """T22: validate the real 34-skill UI E2E report and artifact manifest.

    Absent evidence stays NOT_TESTED; historical output is never accepted.
    The bindings file ties the report/manifest hashes to this build's
    source/contract/config identities (same-build requirement).
    """
    bindings_path = WORK_BASE / bindings_name
    if not bindings_path.is_file():
        return {"status": "NOT_TESTED", "reason": f"{bindings_name} missing"}
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    report_rel = str(bindings.get("e2e_report") or "output/packaged-skill-e2e-v3/report.json")
    report_path = ROOT / report_rel
    if not report_path.is_file():
        return {"status": "FAIL", "reason": f"E2E report missing: {report_path}"}
    if sha256(report_path) != bindings.get("e2e_report_sha256"):
        problems.append("E2E report sha256 does not match the bound evidence")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "FAIL", "reason": f"E2E report unreadable: {exc}"}
    rows = report.get("rows") or []
    if report.get("total") != 34 or len(rows) != 34:
        problems.append(f"report total/rows != 34 (total={report.get('total')}, rows={len(rows)})")
    if report.get("pass") != 34 or any(not bool(row.get("ok")) for row in rows):
        problems.append("not all 34 rows pass")
    if any(not bool(row.get("terminal_observed")) for row in rows):
        problems.append("not all rows observed a terminal state")
    ordinals = sorted(int(row.get("ordinal") or 0) for row in rows)
    if ordinals != list(range(1, 35)):
        problems.append(f"ordinals mismatch: {ordinals}")
    artifact_manifest_rel = str(bindings.get("artifact_manifest") or "v21-work/g6a-ccfix-e2e-artifacts.json")
    artifact_manifest_path = WORK_BASE / artifact_manifest_rel
    if not artifact_manifest_path.is_file():
        problems.append(f"artifact manifest missing: {artifact_manifest_path}")
    elif sha256(artifact_manifest_path) != bindings.get("artifact_manifest_sha256"):
        problems.append("artifact manifest sha256 does not match the bound evidence")
    else:
        artifact_document = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
        artifacts = artifact_document.get("artifacts") if isinstance(artifact_document, dict) else artifact_document
        if not isinstance(artifacts, list):
            problems.append("artifact manifest has no artifacts list")
            artifacts = []
        rels = {
            str(item.get("rel") or "").replace("\\", "/"): str(item.get("state") or "real")
            for item in artifacts
        }

        def satisfied(rel: str) -> bool:
            if rel in rels:
                return True
            alternatives = T22_ARTIFACT_ALTERNATIVES.get(rel)
            if alternatives and any(alt in rels for alt in alternatives):
                return True
            for prefix in T22_ARTIFACT_PREFIXES.values():
                if rel.startswith(prefix) and any(key.startswith(prefix) for key in rels):
                    return True
            return False

        missing_artifacts = [rel for rel in EXPECTED_T22_ARTIFACTS if not satisfied(rel)]
        fixture_only = [
            rel for rel, state in rels.items()
            if rel in EXPECTED_T22_ARTIFACTS and state not in {"real", "ok"}
        ]
        if fixture_only:
            problems.append(f"artifacts are fixture-only (not produced by the run): {fixture_only}")
        if missing_artifacts:
            problems.append(f"missing artifacts in manifest: {missing_artifacts}")
    for key in ("build_id", "source_manifest_sha256", "contract_set_hash", "config_hash"):
        if not bindings.get(key):
            problems.append(f"bindings missing {key}")
    return {
        "status": "PASS" if not problems else "FAIL",
        "problems": problems,
        "report": str(report_path),
        "report_sha256": sha256(report_path),
        "artifact_manifest_sha256": bindings.get("artifact_manifest_sha256"),
        "pass": report.get("pass"),
        "total": report.get("total"),
        "bindings_path": str(bindings_path),
    }


def g6b_final_build_result() -> dict[str, Any]:
    """G6B: bind the final clean packaged build and its release evidence.

    Requires the packaged release manifest, the packaged 34-skill E2E report
    and artifact manifest, plus performance/vitality/rollback-soak evidence,
    all tied to one build_id/source_manifest/config identity.
    """
    bindings_path = WORK_BASE / "g6b-final-bindings.json"
    if not bindings_path.is_file():
        return {
            "status": "NOT_TESTED",
            "reason": "g6b-final-bindings.json missing; final packaged build not bound yet",
        }
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    required_keys = (
        "build_id",
        "source_manifest_sha256",
        "contract_set_hash",
        "config_hash",
        "release_manifest",
        "release_manifest_sha256",
        "e2e_report",
        "e2e_report_sha256",
        "artifact_manifest",
        "artifact_manifest_sha256",
        "performance_report",
        "performance_report_sha256",
        "vitality_report",
        "vitality_report_sha256",
        "rollback_soak_evidence",
        "rollback_soak_evidence_sha256",
    )
    for key in required_keys:
        if not bindings.get(key):
            problems.append(f"missing {key}")
    for key in (
        "release_manifest",
        "e2e_report",
        "artifact_manifest",
        "performance_report",
        "vitality_report",
        "rollback_soak_evidence",
    ):
        raw = str(bindings.get(key) or "")
        if not raw:
            continue
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else ROOT / candidate
        if not path.is_file():
            problems.append(f"{key} file missing: {path}")
        elif bindings.get(key + "_sha256") != sha256(path):
            problems.append(f"{key} sha256 mismatch")
    return {
        "status": "PASS" if not problems else "FAIL",
        "problems": problems,
        "bindings_path": str(bindings_path),
    }


def contract_set_hash() -> str:
    """Reproducible v2.1 contract set identity.

    SHA-256 over the canonical JSON map {relative path: file sha256} of every
    file under ``src/contracts`` (sorted by relative path, bytecode excluded).
    """
    rows: dict[str, str] = {}
    for path in sorted((ROOT / "src/contracts").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            rows[path.relative_to(ROOT).as_posix()] = sha256(path)
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def config_hash() -> str:
    """Reproducible v2.1 frozen gate configuration identity.

    SHA-256 over the canonical JSON map {path: file sha256} of the frozen
    specification, the imported design/report inputs, the gate case manifest
    and this runner (sorted by path).  It excludes source code and runtime
    state so it stays stable across identical gate configurations.
    """
    rows: dict[str, str] = {
        str(SPEC): sha256(SPEC),
        "tests/v21_cases.yaml": sha256(ROOT / "tests/v21_cases.yaml"),
        "scripts/run_v21_gate.py": sha256(ROOT / "scripts/run_v21_gate.py"),
    }
    rows.update({str(path): sha256(path) for path in FROZEN_IMPORTS})
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def run_pytest(paths: list[str]) -> dict[str, Any]:
    """Run the frozen source tests; no prior receipt can substitute for this."""
    python = ROOT / "app/runtime/python312/python.exe"
    if not python.is_file():
        return {"status": "FAIL", "reason": "embedded Python 3.12 missing"}
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(
            (str(ROOT / "src"), str(ROOT / "app/backend/tiangong-backend"), str(ROOT / "readable-python-source"))
        ),
    }
    completed = subprocess.run(
        [str(python), "-m", "pytest", "-q", *paths], cwd=ROOT,
        env=environment, text=True, capture_output=True, check=False,
    )
    return {
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "command": ["embedded-python", "-m", "pytest", "-q", *paths],
        "exit_code": completed.returncode,
        "summary": (completed.stdout + completed.stderr)[-4000:],
    }


def run_node(paths: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["node", "--test", *paths], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    return {
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "command": ["node", "--test", *paths],
        "exit_code": completed.returncode,
        "summary": (completed.stdout + completed.stderr)[-2000:],
    }


def issue_ledger_result(work: Path) -> dict[str, Any]:
    ledger_path = work / "ledgers/issue-closure-ledger.json"
    if not ledger_path.is_file():
        inherited = sorted(
            WORK_BASE.glob("v21-g0-*/ledgers/issue-closure-ledger.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if inherited:
            ledger_path = inherited[0]
    if not ledger_path.is_file():
        return {"status": "FAIL", "reason": "G0 issue ledger missing"}
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    expected = {"full_qc": 58, "minimax_numbered": 8, "minimax_p2_groups": 1, "minimax_frontend_breakpoints": 8, "total": 75}
    actual = ledger.get("counts")
    return {"status": "PASS" if actual == expected and len(ledger.get("issues", ())) == 75 else "FAIL", "counts": actual}


def skill_case_manifest_result() -> dict[str, Any]:
    document = json.loads((ROOT / "tests/v21_cases.yaml").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "readable-python-source/omni_body_skill/registry/skill_router_index.json").read_text(encoding="utf-8"))
    expected_ids = [item["id"] for item in catalog["skills"]]
    cases = document.get("skill_cases", [])
    actual_ids = [item.get("skill_id") for item in cases]
    required = {
        "case_id", "skill_id", "success_fixture", "relevant_failure_fixture",
        "deterministic_oracle", "acceptance_profile", "environment_requirements",
        "artifact_checks", "aesthetic_profile", "expected_model_response_contract",
    }
    valid = (
        len(expected_ids) == 34
        and actual_ids == expected_ids
        and len({item.get("case_id") for item in cases}) == 34
        and all(required <= item.keys() for item in cases)
    )
    return {"status": "PASS" if valid else "FAIL", "catalog_count": len(expected_ids), "case_count": len(cases)}


def run_g0_cases(work: Path) -> dict[str, dict[str, Any]]:
    frozen = frozen_input_result()
    catalog = catalog_baseline_result()
    return {
        "T00_import_baseline": frozen,
        "T19a_fixture_matrix": run_pytest([
            "tests/test_contracts_vnext.py", "tests/test_contract_artifacts.py",
            "tests/test_gate_promotion_v21.py",
            "tests/test_gateway_store.py",
            "tests/test_v21_gate_cutover.py",
            "tests/test_v21_cases_manifest.py",
            "tests/test_active_request_activation.py",
            "tests/test_confirmation_retirement_v15.py",
            "tests/test_effect_fact_chain_v14.py",
        ]),
        "T26a_auth_golden": run_pytest(["tests/test_execution_contracts.py", "tests/test_execution_continuity_priority_30.py"]),
        "G0_catalog_baseline": catalog,
        "G0_issue_ledger": issue_ledger_result(work),
        "G0_skill_case_manifest": skill_case_manifest_result(),
    }


def run_g1_cases(work: Path) -> dict[str, dict[str, Any]]:
    results = dict(run_g0_cases(work))
    results.update({
        "T01_root_child": run_pytest(["tests/test_v21_life_contracts.py"]),
        "T02_candidate_shape": run_pytest(["tests/test_v21_life_contracts.py"]),
        "T03a_identity_state": run_pytest(["tests/test_v21_g1_gate.py"]),
        "T04_true_CAS": run_pytest(["tests/test_v21_g1_gate.py"]),
        "T05a_ingress_saga": run_pytest(["tests/test_life_event_ingress.py"]),
        "T19b_contract_runtime": run_pytest([
            "tests/test_contracts_vnext.py",
            "tests/test_v21_g1_gate.py",
        ]),
        "T28_root_continuation": run_pytest(["tests/test_v21_g1_gate.py"]),
    })
    return results


def run_g2_cases(work: Path) -> dict[str, dict[str, Any]]:
    results = dict(run_g1_cases(work))
    results.update({
        "T03b_identity_runtime": run_pytest(["tests/test_v21_g2_gate.py"]),
        "T17_no_IO_lock": run_pytest(["tests/test_v21_g2_gate.py"]),
        "T24_performance": run_pytest(["tests/test_v21_g2_gate.py"]),
    })
    return results


def run_g3_cases(work: Path) -> dict[str, dict[str, Any]]:
    results = dict(run_g2_cases(work))
    saga_tests = ["tests/test_v21_g3_saga.py"]
    contract_tests = ["tests/test_contracts_vnext.py", "tests/test_v21_g3_contracts.py"]
    results.update({
        "T05b_response_saga": run_pytest([
            "tests/test_v21_g3_saga.py",
            "tests/test_v21_g3_contracts.py",
        ]),
        "T10_pure_chat": run_pytest(saga_tests),
        "T11_assistant_status": run_pytest(saga_tests),
        "T12_all_models_down": run_pytest(saga_tests),
        "T13_stream_fallback": run_pytest(saga_tests),
        "T18_replay": run_pytest(saga_tests),
        "T20_frontend_truth": run_node(["tests/frontend-truth-projection.test.mjs"]),
        "T27_model_plan_recovery": run_pytest(saga_tests),
        "T31_terminal_model_reply_matrix": run_pytest(saga_tests),
        "T19b_contract_runtime": run_pytest(contract_tests),
    })
    return results


def run_g4_cases(work: Path) -> dict[str, dict[str, Any]]:
    results = dict(run_g3_cases(work))
    engine_tests = [
        "tests/test_v21_g4_store_engine.py",
        "tests/test_v21_g4_contracts.py",
    ]
    results.update({
        "T05c_effect_delivery_saga": run_pytest(engine_tests),
        "T06_commitment_coverage": run_pytest(engine_tests),
        "T07_commitment_monotonic": run_pytest(engine_tests),
        "T08_result_layers": run_pytest(engine_tests),
        "T09_ambiguous": run_pytest(engine_tests),
        "T14_internal_action": run_pytest(engine_tests),
        "T15_effect_identity": run_pytest(engine_tests),
        "T16_cancel_dispatch": run_pytest(engine_tests),
        "T19b_contract_runtime": run_pytest([
            "tests/test_contracts_vnext.py",
            "tests/test_v21_g4_contracts.py",
            "tests/test_v21_g4_store_engine.py",
        ]),
        "T26b_auth_equivalence": run_pytest(engine_tests),
    })
    return results


def run_g5_cases(work: Path) -> dict[str, dict[str, Any]]:
    results = dict(run_g4_cases(work))
    g5_tests = [
        "tests/test_v21_g5_gate.py",
        "tests/test_v21_g4_contracts.py",
    ]
    results.update({
        "T23_artifact_current": run_pytest(g5_tests),
        "T25_vitality": run_pytest(g5_tests),
        "T19b_contract_runtime": run_pytest([
            "tests/test_contracts_vnext.py",
            "tests/test_v21_g4_contracts.py",
            "tests/test_v21_g5_gate.py",
        ]),
    })
    return results


def run_g6a_cases(work: Path) -> dict[str, dict[str, Any]]:
    results = dict(run_g5_cases(work))
    g6a_tests = ["tests/test_v21_g6a_registry.py"]
    results.update({
        "T21_skill_selection": run_pytest(g6a_tests),
        "T22_skill_all": t22_skill_all_result(),
        "T29_skill_registry": run_pytest(g6a_tests),
        "T30_issue_ledger": run_pytest(g6a_tests),
    })
    return results


def run_g6b_cases(work: Path) -> dict[str, dict[str, Any]]:
    results = dict(run_g6a_cases(work))
    results.update({
        "T19c_gate_cutover": run_pytest([
            "tests/test_v21_gate_cutover.py",
            "tests/test_gate_promotion_v21.py",
        ]),
        "T22_skill_all": t22_skill_all_result(bindings_name="g6b-final-bindings.json"),
        "T24_performance": run_pytest(["tests/test_v21_g2_gate.py"]),
        "T25_vitality": run_pytest(["tests/test_v21_g2_gate.py"]),
        "T30_issue_ledger": run_pytest(["tests/test_v21_g6a_registry.py"]),
        "G6B_final_build_binding": g6b_final_build_result(),
    })
    return results


def effective_required_tests(gate: str, cases: dict[str, Any]) -> list[str]:
    """Union of this gate's required tests and every ancestor's required tests."""
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(current: str) -> None:
        for parent in GATE_DEPENDENCIES[current]:
            visit(parent)
        for test in cases["gates"][current]:
            if test not in seen:
                seen.add(test)
                ordered.append(test)

    visit(gate)
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", choices=GATE_DEPENDENCIES)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--work-root", type=Path)
    args = parser.parse_args()
    global _CURRENT_GATE
    _CURRENT_GATE = args.gate
    work = args.work_root or WORK_BASE / args.build_id
    evidence_dir = work / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    cases = json.loads((ROOT / "tests/v21_cases.yaml").read_text(encoding="utf-8"))
    source_hash, source_count = source_manifest()
    required = effective_required_tests(args.gate, cases)
    if args.gate == "G0":
        results = run_g0_cases(work)
    elif args.gate == "G1":
        results = run_g1_cases(work)
    elif args.gate == "G2":
        results = run_g2_cases(work)
    elif args.gate == "G3":
        results = run_g3_cases(work)
    elif args.gate == "G4":
        results = run_g4_cases(work)
    elif args.gate == "G5":
        results = run_g5_cases(work)
    elif args.gate == "G6A":
        results = run_g6a_cases(work)
    elif args.gate == "G6B":
        results = run_g6b_cases(work)
    else:
        results = {case: {"status": "NOT_TESTED", "reason": "case runner not implemented"} for case in required}
    passed = all(results.get(case, {}).get("status") == "PASS" for case in required)
    receipt = {
        "schema": "tiangong.v21.gate-receipt.v1", "gate": args.gate, "build_id": args.build_id,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(), "source_root": str(ROOT),
        "source_manifest_sha256": source_hash, "source_file_count": source_count,
        "contract_set_hash": contract_set_hash(), "config_hash": config_hash(),
        "required_tests": required, "results": results,
        "status": "PASS" if passed else "FAIL", "promotion_allowed": passed,
    }
    payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt["evidence_manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    output = evidence_dir / f"{args.gate}-receipt-{args.build_id}.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(output), "status": receipt["status"], "evidence_manifest_sha256": receipt["evidence_manifest_sha256"]}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
