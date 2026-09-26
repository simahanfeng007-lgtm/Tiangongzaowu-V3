"""A sealed minimum execution attestation cannot assert semantic completion."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from contracts import canonical_sha256


def value(tmp_path):
    from total_gateway.composition_task_floor import validate_request_execution_floor, seal_execution_requirements_attestation
    text = "请保留现有产物 summary.json。"
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    import hashlib
    target = tmp_path / "summary.json"
    floor = {"obligations_count": 1, "obligations_sha256": canonical_sha256([{"kind": "retained_output"}]),
        "required_outputs": ["summary.json"], "output_witnesses": [{"requested_path": "summary.json",
        "native_path": str(target), "exists": True, "size_bytes": 2,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "source": "gateway.native-final-output-probe"}],
        "execution_requirements_verified": True, "business_outcome_verified": False}
    proof = seal_execution_requirements_attestation(floor, request_id="request.test", user_text=text,
        executable_plan_id="plan.test", executable_plan_sha256="a"*64, supporting_fact_ids=("fact.actual",), execution_completed_at_ms=3)
    from total_gateway.composition_final_result import encode_composition_final_result
    result = json.loads(encode_composition_final_result({"final.1": {"elapsed_seconds": 0.1}},
        parent_reply="plan admitted", execution_requirements_attestation=proof))
    expected = dict(expected_request_id="request.test", expected_request_text=text, expected_plan_id="plan.test",
                    expected_plan_sha256="a"*64, allowed_fact_ids=("fact.actual", "fact.parent"))
    return result, expected


def test_attestation_roundtrip_preserves_float_results_and_bound_facts(tmp_path):
    from total_gateway.composition_final_result import decode_composition_final_aliases, decode_composition_requirements_attestation
    result, expected = value(tmp_path)
    assert decode_composition_final_aliases(result)["final.1"]["elapsed_seconds"] == 0.1
    proof = decode_composition_requirements_attestation(result, **expected)
    assert proof["execution_requirements_verified"] is True
    assert proof["business_outcome_verified"] is False


@pytest.mark.parametrize("field,replacement", [
    ("expected_request_id", "request.other"), ("expected_request_text", "请保留现有产物 different.json。"),
    ("expected_plan_id", "plan.other"), ("expected_plan_sha256", "b"*64),
    ("allowed_fact_ids", ("fact.other",)),
])
def test_attestation_cannot_cross_request_plan_or_fact_scope(tmp_path, field, replacement):
    from total_gateway.composition_final_result import decode_composition_requirements_attestation
    result, expected = value(tmp_path)
    expected[field] = replacement
    with pytest.raises(ValueError, match="binding mismatch"):
        decode_composition_requirements_attestation(result, **expected)


def test_attestation_extra_authority_and_dropped_outputs_are_rejected(tmp_path):
    from total_gateway.composition_final_result import decode_composition_requirements_attestation
    result, expected = value(tmp_path)
    modified = deepcopy(result)
    proof = modified["execution_requirements_attestation"]
    proof["task_completed"] = True
    proof["sha256"] = canonical_sha256({k: v for k, v in proof.items() if k != "sha256"})
    with pytest.raises(ValueError):
        decode_composition_requirements_attestation(modified, **expected)
    modified = deepcopy(result)
    proof = modified["execution_requirements_attestation"]
    proof["required_outputs"], proof["output_witnesses"] = [], []
    proof["execution_requirements_verified"] = False
    proof["sha256"] = canonical_sha256({k: v for k, v in proof.items() if k != "sha256"})
    with pytest.raises(ValueError):
        decode_composition_requirements_attestation(modified, **expected)


def test_legacy_and_prior_float_envelopes_have_no_requirements_attestation():
    from total_gateway.composition_final_result import encode_composition_final_result, decode_composition_requirements_attestation
    for result in [{"composition_final_output_aliases": {"a": 1}, "parent_reply": "old"},
                   json.loads(encode_composition_final_result({"a": 1.2}, parent_reply="old"))]:
        assert decode_composition_requirements_attestation(result, expected_request_id="r", expected_request_text="", expected_plan_id="p",
            expected_plan_sha256="a"*64, allowed_fact_ids=()) is None
