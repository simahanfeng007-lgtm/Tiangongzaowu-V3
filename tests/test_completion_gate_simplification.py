"""Real failure shapes from the expanded dictionary execution comparison."""
from copy import deepcopy

import pytest

from v3 import execution_integrity as integrity
from v3.simple_chain import kernel
from test_task_execution_simplification import observation


@pytest.mark.parametrize("restriction", ["禁止访问桌面", "不要保存到桌面", "不要在桌面上写文件", "桌面清理主题", "do not access desktop"])
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


def inline_output_receipt(tmp_path):
    from v3.tool_result_contract import normalize_tool_result
    (tmp_path / "result.json").write_text('{"created": true}', encoding="utf-8")
    raw = {"ok": True, "action": "python.run", "result": {"action": "python.run", "success": True,
        "execution": {"receipt_role": "execution", "execution_state": "completed", "ok": True,
            "returncode": 0, "commit_state": "committed", "committed_workspace": str(tmp_path),
            "changed_files": ["result.json"], "deleted_files": []}}}
    payload = observation("python.run", contract=normalize_tool_result("omni_body", raw))
    payload["tool_result"] = raw
    payload["tool_args"]["args"] = {"code": "# submitted inline program"}
    return payload


def test_inline_output_receipt_satisfies_local_delivery_and_life_roundtrip(tmp_path):
    prompt = "请生成并交付 result.json。"
    payload = inline_output_receipt(tmp_path)
    assert payload["tool_result_contract"]["paths"] == []
    goal = next(item for item in integrity.build_action_obligations(prompt) if item["kind"] == "delivery")
    assert integrity.obligation_is_satisfied(goal, [payload])
    state = {"obligations": [deepcopy(goal)], "round": 1}
    integrity.update_run_state_obligations(state, payload)
    assert state["obligations"][0]["status"] == "satisfied"
    contract = integrity.initialize_task_contract(prompt)
    projected = integrity.build_task_contract_obligations(contract)
    assert next(item for item in projected if item["kind"] == "delivery")["delivery_mode"] == "local_artifact"
    updated = integrity.update_task_contract_evidence(contract, payload, round_number=1)
    assert next(item for item in updated["desired_facts"] if item["kind"] == "delivery")["status"] == "satisfied"
    reconciled, goals = integrity.reconcile_completion_evidence(contract, projected, [payload])
    assert all(item["status"] == "satisfied" for item in goals)
    assert all(item["status"] == "satisfied" for item in reconciled["desired_facts"])


@pytest.mark.parametrize("failure", ["execution_failed", "discarded", "untrusted", "deleted", "missing_post", "claim_only", "different_target", "external"])
def test_local_delivery_does_not_invent_output_evidence(tmp_path, failure):
    payload = inline_output_receipt(tmp_path)
    goal = {"kind": "delivery", "delivery_mode": "local_artifact", "target_path": "result.json"}
    evidence = payload["tool_result_contract"]["write_evidence"]
    if failure == "execution_failed":
        payload["tool_result"]["result"]["execution"].update(ok=False, returncode=1)
    elif failure == "discarded":
        payload["tool_result"]["result"]["execution"]["commit_state"] = "discarded"
    elif failure == "untrusted":
        evidence["authoritative"] = False
    elif failure == "deleted":
        evidence["deleted_files"] = evidence.pop("changed_files")
        payload["tool_result_contract"]["paths"] = list(evidence["deleted_files"])
    elif failure == "missing_post":
        evidence["post"] = [{"path": str(tmp_path / "result.json"), "exists": False}]
    elif failure == "claim_only":
        evidence["changed_files"] = []
        payload["tool_args"]["target"] = str(tmp_path / "result.json")
        payload["tool_result"]["result"]["execution"]["stdout"] = "result.json created successfully"
    elif failure == "different_target":
        goal["target_path"] = "other.json"
    elif failure == "external":
        goal["delivery_mode"] = "external"
    assert not integrity.obligation_is_satisfied(goal, [payload])


@pytest.mark.parametrize("instruction,path", [
    ("生成可正常打开的", "weekly.pptx"), ("生成一个可以直接编辑的", "sales.xlsx"),
    ("编写能够正确运行的", "worker.py"), ("生成可直接交付的", "report.pdf"),
])
def test_output_capability_modifier_does_not_become_an_input_command(instruction, path):
    prompt = f"请读取 brief.json，{instruction} {path}。"
    goals = integrity.build_action_obligations(prompt)
    assert any(item["kind"] == "effect" and item["target_path"] == path for item in goals)
    assert not any(item["kind"] == "observation" and item["target_path"] == path for item in goals)
    assert kernel._simple_chain_explicit_read_paths(prompt) == ["brief.json"]
    assert path in kernel._simple_chain_explicit_deliverable_paths(prompt)


@pytest.mark.parametrize("prompt", ["请打开可正常读取的 report.pptx。", "请生成 report.pptx，然后打开 report.pptx。"])
def test_explicit_open_command_still_requires_observation(prompt):
    goals = integrity.build_action_obligations(prompt)
    assert any(item["kind"] == "observation" and item["target_path"] == "report.pptx" for item in goals)
