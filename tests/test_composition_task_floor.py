"""Minimum execution requirements never imply arbitrary business acceptance."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import WORKSPACE_PYTHON_PROFILE_ID
from total_gateway.composition_task_floor import validate_request_plan_floor, validate_workspace_step_order


def step(action, target, identity="step.1", dependencies=()):
    return SimpleNamespace(action_id=action, target_skeleton=str(target), step_id=identity,
        depends_on=dependencies, execution_profile_id=WORKSPACE_PYTHON_PROFILE_ID)






def test_program_read_requirement_does_not_force_an_agent_read(tmp_path):
    text="请创建 summarize_orders.py，只用 Python 标准库读取 CSV 并生成 summary.json。请实际运行脚本。"
    validate_request_plan_floor(text, [step("file.write", "summarize_orders.py"),
        step("python.run", "summarize_orders.py", "step.2", ("step.1",))], workspace_root=tmp_path)


@pytest.mark.parametrize("second", ["code.patch_replace", "file.read", "python.run"])
def test_same_target_followups_require_real_dependency(tmp_path, second):
    first = step("file.write", "worker.py")
    later = step(second, "worker.py", "step.2")
    with pytest.raises(ValueError, match="dependency_missing"):
        validate_workspace_step_order([first, later], workspace_root=tmp_path)
    later.depends_on = (first.step_id,)
    validate_workspace_step_order([first, later], workspace_root=tmp_path)


def test_python_effects_require_order_even_for_other_files(tmp_path):
    with pytest.raises(ValueError, match="dependency_missing"):
        validate_workspace_step_order([step("python.run", "worker.py"), step("file.read", "summary.json", "step.2")], workspace_root=tmp_path)






def test_empty_floor_cannot_claim_execution_requirements_verified(tmp_path):
    from total_gateway.composition_task_floor import validate_request_execution_floor
    evidence = validate_request_execution_floor("你好", [], workspace_root=tmp_path)
    assert evidence["execution_requirements_verified"] is False
    assert evidence["business_outcome_verified"] is False


def test_attestation_binds_user_text_and_current_output_bytes(tmp_path):
    from total_gateway.composition_task_floor import (validate_request_execution_floor,
        seal_execution_requirements_attestation, validate_execution_requirements_attestation_shape)
    text = "请保留现有产物 summary.json。"
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    evidence = validate_request_execution_floor(text, [], workspace_root=tmp_path)
    value = seal_execution_requirements_attestation(evidence, request_id="request.test", user_text=text,
        executable_plan_id="plan.test", executable_plan_sha256="a"*64, supporting_fact_ids=("fact.actual",), execution_completed_at_ms=5)
    assert value["request_text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert value["sha256"] == canonical_sha256({k: v for k, v in value.items() if k != "sha256"})
    value["business_outcome_verified"] = True
    value["sha256"] = canonical_sha256({k: v for k, v in value.items() if k != "sha256"})
    with pytest.raises(ValueError, match="invalid"):
        validate_execution_requirements_attestation_shape(value)

@pytest.mark.parametrize("text", ["请写入 README.md。", "请实际运行脚本。", "请读取 orders.csv。", "请保留现有产物 ../summary.json。"])
def test_request_prose_creates_no_execution_or_output_floor(tmp_path, text):
    from total_gateway.composition_task_floor import validate_request_execution_floor
    validate_request_plan_floor(text, [], workspace_root=tmp_path)
    proof = validate_request_execution_floor(text, [], workspace_root=tmp_path)
    assert proof["obligations_count"] == 0
    assert proof["required_outputs"] == proof["output_witnesses"] == []
    assert proof["execution_requirements_verified"] is False
