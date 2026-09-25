"""Real failure shapes from the expanded dictionary execution comparison."""
from copy import deepcopy

import pytest

from v3 import execution_integrity as integrity
from v3.simple_chain import kernel
from test_task_execution_simplification import observation


@pytest.mark.parametrize("restriction", ["禁止访问桌面", "不要保存到桌面", "桌面清理主题", "do not access desktop"])
def test_desktop_mention_does_not_relocate_input_or_output(tmp_path, monkeypatch, restriction):
    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("TIANGONG_DESKTOP_PATH", str(tmp_path / "desktop"))
    prompt = f"读取 story_brief.json，生成 story.md，保存为 story.md。{restriction}。"
    (tmp_path / "story.md").write_text("灯塔重新亮起。", encoding="utf-8")
    assert kernel._simple_chain_explicit_deliverable_paths(prompt) == ["story.md"]
    assert kernel._simple_chain_missing_deliverable_paths(prompt, [], []) == []
    assert kernel._simple_chain_paths_match_desktop([str(tmp_path / "story.md")], prompt)


def test_absolute_input_is_not_reclassified_as_a_deliverable():
    prompt = r"读取 D:\jobs\brief.json，生成 output.json。禁止访问桌面。"
    assert kernel._simple_chain_requested_target_paths(prompt) == []
    assert kernel._simple_chain_explicit_deliverable_paths(prompt) == ["output.json"]


def test_inbox_source_is_not_a_project_output_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    prompt = "整理工作区 inbox 下的全部文件，复制到 organized/，生成 manifest.csv。禁止访问桌面。"
    assert kernel._simple_chain_project_dir(prompt) == ""
    assert kernel._simple_chain_prepare_tool_call("r", prompt, "omni_body", {
        "action": "code.write", "target": "organize.py", "args": {"content": "print('stage')"},
    })[-1] is None
    assert "manifest.csv" in kernel._simple_chain_missing_deliverable_paths(prompt, [], [])


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
    assert explicit["final_requirement_gaps"]
    assert explicit["quality_advisories"] == []


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
    goal = next(item for item in integrity.build_action_obligations("请实际运行脚本 aggregate.py，输入 records.json，输出 totals.json")
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


def test_model_completion_claim_without_execution_is_still_rejected():
    prompt = "运行 aggregate.py 并生成 totals.json"
    assert integrity.execution_integrity_blockers(prompt, [], final_reply="已经全部完成。")


def test_hash_column_does_not_request_hashing_the_manifest_itself():
    prompt = ("整理工作区 inbox 下的文件，复制到 organized/，生成 manifest.csv。"
              "表头 source,target,size,sha256。确认副本数量、字节数和 SHA256 都正确。"
              "请实际生成并检查交付文件。")
    goals = integrity.build_action_obligations(prompt)
    assert not any(item.get("evidence_predicate") == "sha256_digest" for item in goals)
    assert not any(item["kind"] == "observation" and item["target_path"] == "manifest.csv" for item in goals)
    explicit = integrity.build_action_obligations("请创建 proof.txt，然后计算其 SHA-256。")
    assert any(item.get("evidence_predicate") == "sha256_digest" and item["target_path"] == "proof.txt" for item in explicit)
