from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_case_manifest_is_complete_and_is_only_an_acceptance_input() -> None:
    cases = json.loads((ROOT / "tests" / "v21_cases.yaml").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "readable-python-source" / "omni_body_skill" / "registry" / "skill_router_index.json").read_text(encoding="utf-8"))
    expected_ids = [item["id"] for item in catalog["skills"]]
    skill_cases = cases["skill_cases"]
    assert len(expected_ids) == 34
    assert [item["skill_id"] for item in skill_cases] == expected_ids
    assert len({item["case_id"] for item in skill_cases}) == 34
    required = {
        "case_id", "skill_id", "success_fixture", "relevant_failure_fixture",
        "deterministic_oracle", "acceptance_profile", "environment_requirements",
        "artifact_checks", "aesthetic_profile", "expected_model_response_contract",
    }
    for item in skill_cases:
        assert required <= item.keys()
        assert item["case_id"] == f"SKILL_{item['skill_id']}"
        assert item["success_fixture"]["fixture_id"].endswith(":success")
        assert item["relevant_failure_fixture"]["fixture_id"].endswith(":relevant-failure")
        assert item["expected_model_response_contract"]["when_model_available"].startswith("AssistantCommit+")
    assert "results" not in cases
