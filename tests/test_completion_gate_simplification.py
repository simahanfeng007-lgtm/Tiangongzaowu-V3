"""Real failure shapes from the expanded dictionary execution comparison."""
from copy import deepcopy

import pytest

from v3 import execution_integrity as integrity
from v3.simple_chain import kernel
from test_task_execution_simplification import observation








def test_optional_quality_review_is_advisory_but_explicit_review_stays_required():
    args = {"action": "qc.ppt.delivery_check", "target": "weekly.pptx", "args": {}}
    result = {"ok": True, "action": "qc.ppt.delivery_check", "result": {
        "acceptance": False, "score": 54, "issues": [{"code": "missing_cta", "message": "add a decision request"}],
    }}
    ordinary = kernel._simple_chain_quality_gate_payload("r", "创建普通三页周报 weekly.pptx", "omni_body", args, result, 0)
    assert ordinary["ok"] is True
    assert ordinary["final_requirement_gaps"] == []
    assert ordinary["quality_advisories"]
    explicit = kernel._simple_chain_quality_gate_payload("r", "创建 weekly.pptx，并通过 qc.ppt.delivery_check", "omni_body", args, result, 0)
    assert explicit["final_requirement_gaps"] == []
    assert explicit["quality_advisories"]


def test_short_document_is_deliverable_without_an_invented_word_floor(tmp_path, monkeypatch):
    from docx import Document
    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "notice.docx"
    doc = Document()
    doc.add_paragraph("明天九点开会。")
    doc.save(target)
    payload = observation("docx.create", str(target), contract={"ok": True, "observed_write_effect": True,
        "write_evidence": {"authoritative": True, "changed_files": [str(target)]}, "paths": [str(target)]})
    allowed, _, reasons = kernel._simple_chain_evidence_check(
        "生成 notice.docx，内容为明天九点开会。", [payload], [{"path": str(target)}])
    assert allowed, reasons
    target.write_text("pretend this is a Word file", encoding="utf-8")
    allowed, _, reasons = kernel._simple_chain_evidence_check(
        "生成 notice.docx，内容为明天九点开会。", [payload], [{"path": str(target)}])
    assert not allowed
    assert any("file format" in reason for reason in reasons)


def execution_and_goal():
    goal = next(item for item in [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'command_execution'}]
                if item.get("evidence_predicate") == "command_execution")
    run = observation("python.run", "aggregate.py", result={"execution": {"ok": True, "returncode": 0}})
    run["tool_args"]["args"]["argv"] = ["records.json", "totals.json"]
    return goal, run


def changed(path):
    return observation("file.write", path, contract={"ok": True, "observed_write_effect": True,
        "write_evidence": {"authoritative": True, "changed_files": [path]}})


@pytest.mark.parametrize("path,expected", [("test_aggregate.py", True), ("README.md", True),
    ("aggregate.py", False), ("records.json", False), ("totals.json", False)])
def test_execution_freshness_uses_actual_program_and_argument_paths(path, expected):
    goal, run = execution_and_goal()
    assert integrity.obligation_is_satisfied(goal, [run])
    assert integrity.obligation_is_satisfied(goal, [run, changed(path)]) is expected
    state = {"obligations": [deepcopy(goal)], "round": 1}
    integrity.update_run_state_obligations(state, run)
    integrity.update_run_state_obligations(state, changed(path))
    assert (state["obligations"][0]["status"] == "satisfied") is expected
    assert integrity.obligation_is_satisfied(goal, [run, changed(path), run])
