"""Freeze the v2.1 Skill acceptance input from the authoritative catalog.

The generated case manifest is intentionally an input to later Electron runs,
not a result: executing a case may only write evidence outside the source tree.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "v21_cases.yaml"
CATALOG = ROOT / "readable-python-source" / "omni_body_skill" / "registry" / "skill_router_index.json"


def main() -> int:
    document = json.loads(CASES.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    cases = []
    for skill in catalog["skills"]:
        skill_id = skill["id"]
        acceptance = skill.get("acceptance") or {
            "minimum_score": None,
            "world_class_score": None,
            "must_pass": ["requested_outcome", "artifact_or_typed_unavailable_fact"],
        }
        actions = list(dict.fromkeys(
            skill.get("starter_actions", [])
            + skill.get("production_actions", [])
            + skill.get("quality_gates", [])
            + skill.get("repair_actions", [])
            + skill.get("final_actions", [])
        ))
        cases.append({
            "case_id": f"SKILL_{skill_id}",
            "skill_id": skill_id,
            "success_fixture": {
                "fixture_id": f"{skill_id}:success",
                "input_nonce": f"v21-{skill_id}-success",
                "required_action_candidates": actions,
            },
            "relevant_failure_fixture": {
                "fixture_id": f"{skill_id}:relevant-failure",
                "input_nonce": f"v21-{skill_id}-relevant-failure",
                "expected_terminal_fact": "BLOCKED_EXTERNAL_or_typed_failure",
            },
            "deterministic_oracle": {
                "success": "requested_outcome+current_artifact+oracle_PASS",
                "failure": "typed_failure_fact_and_model_assistant_when_available",
            },
            "acceptance_profile": acceptance,
            "environment_requirements": ["final_Electron_frontend", "same_build_id", "same_source_manifest"],
            "artifact_checks": ["path", "bytes", "sha256", "openability", "format_specific_qc"],
            "aesthetic_profile": "applicable_artifacts_require_rendered_visual_review",
            "expected_model_response_contract": {
                "when_model_available": "AssistantCommit+SUCCEEDED_PlanOutcome+exact_text_hash",
                "when_all_models_unavailable": "EXHAUSTED+no_assistant+typed_system_status",
            },
        })
    document["skill_cases"] = cases
    CASES.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "PASS", "skill_case_count": len(cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
