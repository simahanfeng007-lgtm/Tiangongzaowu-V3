from __future__ import annotations

from copy import deepcopy

from v3 import execution_integrity as integrity
from v3.simple_chain import kernel


def observation(action, target="", *, ok=True, result=None, contract=None):
    return {
        "ok": ok, "tool_action": action,
        "tool_args": {"target": target, "args": {}},
        "tool_result": {"ok": ok, "result": result or {}},
        "tool_result_contract": contract or {"ok": ok},
    }


def test_actions_bind_to_their_objects_and_not_workspace_or_input():
    prompt = r"工作目录是 D:\jobs\case，里面已有 orders.csv。请读取 orders.csv 并创建 report.json。修改 test_report.py，不要重复创建 worker.py。然后运行测试。"
    obligations = integrity.build_action_obligations(prompt)
    assert {(item["kind"], item["target_path"]) for item in obligations} == {
        ("observation", "orders.csv"), ("effect", "report.json"),
        ("effect", "test_report.py"), ("execution", ""),
    }
    assert kernel._simple_chain_requested_target_paths(prompt) == []
    assert set(kernel._simple_chain_explicit_deliverable_paths(prompt)) == {"report.json", "test_report.py"}


def test_followup_preserves_existing_program_but_requires_run_and_outputs():
    prompt = (
        "工作目录单独列行：\nD:\\jobs\\case\n"
        "只处理现有四个产物：worker.py、test_worker.py、summary.json、README.md。"
        "请先检查文件，然后实际运行测试和脚本，根据执行结果更新 README.md。"
        "不需要重复创建已有程序，不安装依赖、不联网。"
    )
    obligations = integrity.build_action_obligations(prompt)
    assert [item["target_path"] for item in obligations if item["kind"] == "effect"] == ["README.md"]
    assert any(item["kind"] == "execution" for item in obligations)
    assert set(kernel._simple_chain_explicit_deliverable_paths(prompt)) == {
        "worker.py", "test_worker.py", "summary.json", "README.md",
    }


def test_english_negation_is_local_and_quoted_punctuation_is_literal():
    prompt = 'Please read "D:\\jobs\\input, 1.txt"; do not modify keep.py; write out.json.'
    items = integrity.build_action_obligations(prompt)
    assert [(item["kind"], item["target_path"]) for item in items] == [
        ("observation", r"D:\jobs\input, 1.txt"), ("effect", "out.json"),
    ]


def test_admission_and_nested_process_failures_do_not_satisfy_execution():
    obligation = integrity.build_action_obligations("请运行测试")[0]
    payload = observation("shell.run", result={"receipt_role": "admission", "ok": True})
    assert not integrity.obligation_is_satisfied(obligation, [payload])
    payload = observation("shell.run", result={"execution": {"ok": False, "returncode": 1}})
    assert not integrity.obligation_is_satisfied(obligation, [payload])
    payload = observation("shell.run", result={
        "receipt_role": "execution", "execution_state": "completed",
        "execution": {"ok": True, "returncode": 0}, "commit_state": "committed",
    })
    assert integrity.obligation_is_satisfied(obligation, [payload])
    payload["tool_result"]["result"]["commit_state"] = "discarded"
    assert not integrity.obligation_is_satisfied(obligation, [payload])


def test_unchanged_requires_authoritative_target_witness():
    obligation = integrity.build_action_obligations("请保存 report.json")[0]
    empty = observation("shell.run", r"D:\jobs", result={"changed_files": [], "changed_bytes": 0})
    assert not integrity.obligation_is_satisfied(obligation, [empty])
    witnessed = observation("file.write", r"D:\jobs", contract={
        "ok": True, "write_evidence": {"authoritative": True,
            "verified_unchanged_files": [r"D:\jobs\report.json"],
            "post": [{"path": r"D:\jobs\report.json", "exists": True, "sha256": "a" * 64}]},
    })
    assert integrity.obligation_is_satisfied(obligation, [witnessed])
    forged = deepcopy(witnessed)
    forged["tool_result_contract"]["write_evidence"]["authoritative"] = False
    assert not integrity.obligation_is_satisfied(obligation, [forged])


def test_broker_outputs_bind_absolute_requirements_to_actual_commit_workspace():
    from v3.tool_result_contract import _observed_write_evidence
    receipt = {"action": "python.run", "execution": {
        "receipt_role": "execution", "commit_state": "committed",
        "committed_workspace": r"D:\jobs", "changed_files": ["case/report.csv"],
        "deleted_files": [], "returncode": 0, "ok": True,
    }}
    evidence = _observed_write_evidence("omni_body", receipt, True)
    payload = observation("python.run", r"D:\jobs\case\worker.py", result=receipt,
        contract={"ok": True, "write_evidence": evidence})
    required = integrity.build_action_obligations(r"请保存 D:\jobs\case\report.csv")[0]
    assert integrity.obligation_is_satisfied(required, [payload])
    elsewhere = {**required, "target_path": r"E:\unrelated\case\report.csv"}
    assert not integrity.obligation_is_satisfied(elsewhere, [payload])
    receipt["execution"]["changed_files"] = ["../escape.csv"]
    assert _observed_write_evidence("omni_body", receipt, True) is None


def test_failure_can_recover_but_later_target_mutation_invalidates_observation():
    goal = integrity.build_action_obligations("请读取 report.json")[0]
    failed = observation("file.read", "report.json", ok=False)
    passed = observation("file.read", "report.json", result={"content": "first"})
    changed = observation("file.write", "report.json", contract={
        "ok": True, "observed_write_effect": True,
        "write_evidence": {"authoritative": True, "changed_files": ["report.json"]},
    })
    assert integrity.obligation_is_satisfied(goal, [failed, passed])
    assert not integrity.obligation_is_satisfied(goal, [passed, changed])
    assert integrity.obligation_is_satisfied(goal, [passed, changed, passed])


def test_completion_reconciles_stale_flags_from_current_evidence():
    prompt = "请读取 report.json"
    obligations = integrity.build_action_obligations(prompt)
    obligations[0]["status"] = "satisfied"
    contract = integrity.initialize_task_contract(prompt)
    refreshed, current = integrity.reconcile_completion_evidence(contract, obligations, [])
    assert current[0]["status"] == "pending"
    assert refreshed["desired_facts"][0]["status"] == "pending"
    assert refreshed["completion_evidence"]["satisfied"] == 0
    assert obligations[0]["status"] == "satisfied"  # no mutation of stored history


def test_stalled_compares_evidence_progress_without_resetting_budget():
    reasons = ["required_evidence_missing"]
    correction = {"last_blockers": reasons, "attempts_used": 1}
    state = {"observations": [], "obligations": []}
    assert not kernel._simple_chain_completion_correction_stalled(correction, reasons, state)
    assert kernel._simple_chain_completion_correction_stalled(correction, reasons, state)
    state["observations"].append(observation("file.read", "a.txt", result={"content": "new"}))
    assert not kernel._simple_chain_completion_correction_stalled(correction, reasons, state)
    state["observations"].append(deepcopy(state["observations"][0]))
    assert kernel._simple_chain_completion_correction_stalled(correction, reasons, state)
    assert correction["attempts_used"] == 1


def test_existing_deliverable_is_allowed_but_missing_output_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(kernel, "_delivery_workspace_root", lambda: str(tmp_path))
    prompt = "请处理现有产物：worker.py、summary.json。请实际运行脚本，不需要重复创建已有程序。"
    (tmp_path / "worker.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "summary.json").write_text('{"count": 3}', encoding="utf-8")
    assert kernel._simple_chain_missing_deliverable_paths(prompt, [], []) == []
    (tmp_path / "summary.json").unlink()
    assert kernel._simple_chain_missing_deliverable_paths(prompt, [], []) == ["summary.json"]
    assert integrity.execution_integrity_blockers(prompt, [])  # files are not proof of execution


def test_real_test_summary_rejects_echo_masked_failure_zero_tests_and_too_few_tests():
    goal = integrity.build_action_obligations("请实际运行三项单元测试")[0]
    payload = observation("shell.run", result={"execution": {
        "ok": True, "returncode": 0, "stdout": "TESTS_EXIT=0\n", "stderr": "Ran 3 tests in 0.1s\n\nFAILED (errors=1)\n",
    }})
    payload["tool_args"]["args"]["command"] = "python -m unittest & echo TESTS_EXIT=0"
    assert not integrity.obligation_is_satisfied(goal, [payload])
    for count in (0, 2):
        payload["tool_result"]["result"]["execution"]["stderr"] = f"Ran {count} tests in 0.1s\n\nOK\n"
        assert not integrity.obligation_is_satisfied(goal, [payload])
    payload["tool_result"]["result"]["execution"]["stderr"] = "Ran 3 tests in 0.1s\n\nOK\n"
    assert integrity.obligation_is_satisfied(goal, [payload])


def test_tests_and_program_are_separate_requirements():
    goals = integrity.build_action_obligations("请实际运行单元测试和脚本")
    assert {goal.get("evidence_predicate") for goal in goals} == {"tests_passed", "program_execution"}
    tested = observation("shell.run", result={"execution": {
        "ok": True, "returncode": 0, "stderr": "Ran 3 tests in 0.1s\n\nOK\n", "stdout": "",
    }})
    tested["tool_args"]["args"]["command"] = "python -m unittest"
    program = observation("python.run", result={"execution": {"ok": True, "returncode": 0, "stdout": "42"}})
    assert integrity.execution_integrity_blockers("请实际运行单元测试和脚本", [tested], obligations=goals)
    assert not integrity.execution_integrity_blockers("请实际运行单元测试和脚本", [tested, program], obligations=goals)
    assert len(integrity.merge_action_obligations(goals, goals)) == 2


def test_cancel_probe_is_host_only_and_not_part_of_signed_authority(monkeypatch, tmp_path):
    from v3.jineng.guge_ceng import GugeCeng
    from v3.jineng import jirou_ceng
    authority = {"grant": {"grant_id": "test"}, "runtime": {"request_id": "real"}}
    proposals, dispatched = [], []
    def issue(proposal, **kwargs):
        proposals.append(proposal)
        return authority
    def run(value):
        dispatched.append(value)
        return {"ok": True, "zhuangtai": "wancheng", "action": "system.health"}
    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(jirou_ceng, "issue_omni_grant", issue)
    monkeypatch.setattr(jirou_ceng, "_run_omni_body_tool", run)
    probe = lambda: True
    mapping = GugeCeng().duiying("omni_body")
    result = jirou_ceng.JirouCeng().zhixing(mapping, {
        "action": "system.health", "__runtime": {"cancel_check": "model-supplied"},
    }, cancel_check=probe)
    assert result["ok"] is True
    assert "__runtime" not in proposals[0]
    assert dispatched[0]["__runtime"]["cancel_check"] is probe
    assert "cancel_check" not in authority["runtime"]
    assert "cancel_check" not in result.get("authority_receipt", {})


def test_late_code_change_invalidates_pass_but_documenting_results_does_not():
    prompt = "请修改 worker.py，运行三项单元测试，并创建 README.md 记录结果"
    goal = next(item for item in integrity.build_action_obligations(prompt) if item.get("evidence_predicate") == "tests_passed")
    tested = observation("shell.run", result={"execution": {
        "ok": True, "returncode": 0, "stderr": "Ran 3 tests in 0.1s\n\nOK\n",
    }})
    tested["tool_args"]["args"]["command"] = "python -m unittest"
    def write(path):
        return observation("file.write", path, contract={"ok": True, "observed_write_effect": True,
            "write_evidence": {"authoritative": True, "changed_files": [path]}})
    assert integrity.obligation_is_satisfied(goal, [tested, write("README.md")])
    assert not integrity.obligation_is_satisfied(goal, [tested, write("worker.py")])
    assert not kernel._simple_chain_has_post_mutation_verification([tested, write("worker.py")], prompt)
    assert kernel._simple_chain_has_post_mutation_verification([tested, write("worker.py"), tested, write("README.md")], prompt)


def test_deleted_output_does_not_keep_old_creation_pass():
    goal = integrity.build_action_obligations("请创建 result.json")[0]
    created = observation("file.write", "result.json", contract={"ok": True, "observed_write_effect": True})
    deleted = observation("file.delete", "result.json", contract={"ok": True,
        "write_evidence": {"authoritative": True, "deleted_files": ["result.json"],
                           "post": [{"path": "result.json", "exists": False}]}})
    assert integrity.obligation_is_satisfied(goal, [created])
    assert not integrity.obligation_is_satisfied(goal, [created, deleted])


def test_missing_retained_output_cannot_use_a_historical_read_or_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr(kernel, "_delivery_workspace_root", lambda: str(tmp_path))
    prompt = "请处理现有产物：summary.json。请实际运行脚本，不需要重复创建已有程序。"
    history = [observation("file.read", str(tmp_path / "summary.json"), result={"content": "{}"})]
    assert kernel._simple_chain_missing_deliverable_paths(prompt, history, [{"path": str(tmp_path / "summary.json")}]) == ["summary.json"]


def test_later_program_failure_reopens_requirement_until_recovery():
    goal = integrity.build_action_obligations("请实际运行脚本")[0]
    passed = observation("python.run", result={"execution": {"ok": True, "returncode": 0}})
    failed = observation("python.run", ok=False, result={"execution": {"ok": False, "returncode": 1}})
    assert integrity.obligation_is_satisfied(goal, [passed])
    assert not integrity.obligation_is_satisfied(goal, [passed, failed])
    assert integrity.obligation_is_satisfied(goal, [passed, failed, passed])


def test_native_python_test_script_requires_actual_framework_summary_and_process_success():
    goal = integrity.build_action_obligations("请实际运行三项单元测试")[0]
    payload = observation("python.run", "D:/workspace/test_worker.py", result={"execution": {
        "ok": True, "returncode": 0, "stdout": "", "stderr": "Ran 3 tests in 0.1s\n\nOK\n",
    }})
    payload["tool_args"]["args"] = {"argv": []}
    assert integrity.obligation_is_satisfied(goal, [payload])
    execution = payload["tool_result"]["result"]["execution"]
    for summary in ("Ran 0 tests in 0.1s\n\nOK\n", "Ran 2 tests in 0.1s\n\nOK\n",
                    "Ran 3 tests in 0.1s\n\nFAILED (errors=1)\n", "tests_passed=true\n"):
        execution["stderr"] = summary
        execution["stdout"] = "TESTS_EXIT=0\n"
        payload["tool_result"]["result"]["tests_passed"] = True
        assert not integrity.obligation_is_satisfied(goal, [payload])
    execution.update(stderr="Ran 3 tests in 0.1s\n\nOK\n", returncode=1)
    assert not integrity.obligation_is_satisfied(goal, [payload])
    execution.update(returncode=0, ok=False)
    assert not integrity.obligation_is_satisfied(goal, [payload])
    execution.update(ok=True)
    payload["receipt_role"] = "admission"
    assert not integrity.obligation_is_satisfied(goal, [payload])


def test_runner_uses_commands_not_directory_names_or_printed_claims():
    program = observation("python.run", "D:/pytest-workspace-chain3/generated.py", result={
        "command": ["D:/python.exe", "D:/pytest-workspace-chain3/generated.py"],
        "execution": {"ok": True, "returncode": 0, "stdout": "executed"},
    })
    assert integrity.execution_result_ok(program)
    assert integrity._execution_test_count(program) is None
    shell = observation("shell.run", "D:/unittest-workspace", result={"execution": {"ok": True, "returncode": 0}})
    for command, expected in [
        ('cd /d D:/pytest-workspace && "D:/some dir/python.exe" -X utf8 -m unittest discover & echo exit=0', "unittest"),
        ('& "D:/bin/pytest.exe" -q', "pytest"),
        ('echo python -m unittest', None),
        ('python -c "import unittest; print(42)"', None),
        ('python generated.py --description pytest', None),
    ]:
        shell["tool_args"]["args"] = {"command": command}
        assert integrity._execution_test_runner(shell) == expected


def test_inline_python_requires_invoked_ast_runner_and_real_summary():
    goal = integrity.build_action_obligations("请实际运行三项单元测试")[0]
    payload = observation("python.run", result={"command": ["D:/python.exe", "D:/temp/tmp123.py"],
        "execution": {"ok": True, "returncode": 0, "stdout": "Ran 3 tests in 0.1s\n\nOK\n"}})
    for code in (
        'import subprocess, sys\ncmd = [sys.executable, "-m", "unittest", "discover"]\np = subprocess.run(cmd, capture_output=True)\nprint(p.stdout)',
        'import unittest as ut\nut.main()',
        'from unittest import TextTestRunner\nrunner = TextTestRunner()\nrunner.run(suite)',
    ):
        payload["tool_args"]["args"] = {"code": code}
        assert integrity.obligation_is_satisfied(goal, [payload])
    for code in (
        'path = "D:/pytest-project"\nprint(path)',
        'import sys\ncmd = [sys.executable, "-m", "unittest"]\nprint(cmd)',
        'print("python -m unittest")',
        'import unittest\ndef unused():\n    unittest.main()',
    ):
        payload["tool_args"]["args"] = {"code": code}
        assert not integrity.obligation_is_satisfied(goal, [payload])
    payload["tool_args"]["args"] = {"code": 'import unittest\nunittest.main()'}
    payload["tool_result"]["result"]["execution"]["stdout"] = "Ran 3 tests in 0.1s\n\nFAILED (errors=1)\nEXIT_CODE=0"
    assert not integrity.obligation_is_satisfied(goal, [payload])


def test_test_only_change_does_not_reopen_program_but_inputs_and_program_do():
    prompt = "请读取 orders.csv。请创建 worker.py 和 test_worker.py，请实际运行单元测试和脚本"
    goals = integrity.build_action_obligations(prompt)
    tests = next(item for item in goals if item.get("evidence_predicate") == "tests_passed")
    program = next(item for item in goals if item.get("evidence_predicate") == "program_execution")
    tested = observation("python.run", "test_worker.py", result={"execution": {
        "ok": True, "returncode": 0, "stderr": "Ran 3 tests in 0.1s\n\nOK\n"}})
    executed = observation("python.run", "worker.py", result={"execution": {"ok": True, "returncode": 0}})
    for path in ("test_worker.py", "worker.py", "orders.csv"):
        changed = observation("file.write", path, contract={"ok": True, "observed_write_effect": True,
            "write_evidence": {"authoritative": True, "changed_files": [path]}})
        assert not integrity.obligation_is_satisfied(tests, [tested, changed])
        assert integrity.obligation_is_satisfied(program, [executed, changed]) == (path == "test_worker.py")


def test_real_dictionary_program_read_is_not_an_independent_agent_read():
    prompt = (
        r"字典任务：帮我完成一个小型 Python 编程任务。工作目录是 D:\Apps\Tiangongzaowu-V3\data\workspace\qa-execution-opt-cedfa39f26\dictionary，里面已有 orders.csv，字段为 order_id,status,amount。"
        "\n请在这个目录创建 summarize_orders.py，只用 Python 标准库读取 CSV，只统计 status 为 paid 的订单，使用 Decimal 精确累加金额，生成 summary.json，字段为 paid_count（整数）和 paid_total（保留两位小数的字符串）。"
        "\n再创建 test_summarize_orders.py，覆盖空数据、混合订单状态和小数金额三种情况。请实际运行单元测试和脚本，生成真实 summary.json，并创建 README.md 记录使用方法、实际运行命令、测试结果和退出码。"
        "\n需要把代码保存到文件并真正运行。所有写入及测试临时文件只在这个工作目录；保留 orders.csv 原样，不删除已有数据，不安装依赖，不联网。若执行失败，请明确说明失败步骤，不要说已完成。"
    )
    goals = integrity.build_action_obligations(prompt)
    assert len(goals) == 6
    assert not any(goal["kind"] == "observation" for goal in goals)
    assert {goal["target_path"] for goal in goals if goal["kind"] == "effect"} == {
        "summarize_orders.py", "test_summarize_orders.py", "summary.json", "README.md",
    }
    executed = {goal.get("evidence_predicate"): goal for goal in goals if goal["kind"] == "execution"}
    assert set(executed) == {"tests_passed", "program_execution"}
    assert all("orders.csv" in goal["evidence_dependency_paths"] for goal in executed.values())


def test_program_input_is_retained_without_broadcasting_a_separate_read_goal():
    for description in (
        "创建 worker.py，只用 Python 标准库读取 orders.csv",
        "创建 worker.py。程序读取 orders.csv",
        "编写一个读取 orders.csv 的程序并保存为 worker.py",
        "创建 worker.py，脚本需要读取 orders.csv",
    ):
        prompt = "请先读取 config.json。请" + description + "。请实际运行单元测试和脚本。"
        goals = integrity.build_action_obligations(prompt)
        assert [goal["target_path"] for goal in goals if goal["kind"] == "observation"] == ["config.json"]
        rows = [row for row in integrity.request_target_bindings(prompt) if row["target_path"] == "orders.csv"]
        assert rows and all(row["role"] == "input" and not row["kind"] for row in rows)
        assert all("orders.csv" in goal["evidence_dependency_paths"]
                   for goal in goals if goal.get("evidence_predicate") in {"tests_passed", "program_execution"})
        read_goal = next(goal for goal in goals if goal["kind"] == "observation")
        run = observation("python.run", "worker.py", result={"execution": {"ok": True, "returncode": 0}})
        assert not integrity.obligation_is_satisfied(read_goal, [run])
        assert not integrity.obligation_is_satisfied(read_goal, [observation("file.read", "orders.csv")])
        assert integrity.obligation_is_satisfied(read_goal, [observation("file.read", "config.json")])


def test_direct_and_ambiguous_read_requests_stay_required_in_programming_tasks():
    for prompt in (
        "请先读取 orders.csv 再编写 worker.py。请运行脚本。",
        "请创建 worker.py，然后读取 orders.csv。",
        "请创建 worker.py 并先读取 orders.csv。",
        "请创建 worker.py 并读取 orders.csv。",
        "请创建 worker.py 后读取 orders.csv。",
        "请创建 worker.py。请用 Python 工具读取 orders.csv。",
        "请创建 worker.py，读取 orders.csv。",  # no explicit program subject
        "请创建 worker.py，只用标准库读取 orders.csv。然后请读取 orders.csv。",
    ):
        goals = integrity.build_action_obligations(prompt)
        read_goals = [goal for goal in goals if goal["kind"] == "observation"]
        assert [goal["target_path"] for goal in read_goals] == ["orders.csv"], prompt
        run = observation("python.run", "worker.py", result={"execution": {"ok": True, "returncode": 0}})
        assert not integrity.obligation_is_satisfied(read_goals[0], [run])
        assert not integrity.obligation_is_satisfied(read_goals[0], [observation("file.write", "orders.csv")])


def test_program_input_changes_still_invalidate_real_execution_evidence():
    prompt = "请创建 worker.py，只用标准库读取 orders.csv。请实际运行单元测试和脚本。"
    goals = integrity.build_action_obligations(prompt)
    tested = observation("python.run", "test_worker.py", result={"execution": {
        "ok": True, "returncode": 0, "stderr": "Ran 3 tests in 0.1s\n\nOK\n"}})
    executed = observation("python.run", "worker.py", result={"execution": {"ok": True, "returncode": 0}})
    changed = observation("file.write", "orders.csv", contract={"ok": True, "observed_write_effect": True,
        "write_evidence": {"authoritative": True, "changed_files": ["orders.csv"]}})
    for goal in goals:
        if goal.get("evidence_predicate") in {"tests_passed", "program_execution"}:
            assert integrity.obligation_is_satisfied(goal, [tested, executed])
            assert not integrity.obligation_is_satisfied(goal, [tested, executed, changed])


def test_action_observation_semantics_does_not_substitute_for_execution_evidence():
    for action in ("file.read", "file.list", "sheet.read", "pdf.extract_text", "web.search", "system.status"):
        assert integrity.action_has_observation_semantics(action)
    for action in ("python.run", "file.write", "code.write", "file.hash", "shell.run", "unknown"):
        assert not integrity.action_has_observation_semantics(action)
