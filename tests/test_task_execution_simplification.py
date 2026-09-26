from __future__ import annotations

from structured_task_fixtures import registered_contract

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








def test_admission_and_nested_process_failures_do_not_satisfy_execution():
    obligation = [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'command_execution'}][0]
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
    obligation = [{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'report.json', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}][0]
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
    required = [{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'D:\\jobs\\case\\report.csv', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}][0]
    assert integrity.obligation_is_satisfied(required, [payload])
    elsewhere = {**required, "target_path": r"E:\unrelated\case\report.csv"}
    assert not integrity.obligation_is_satisfied(elsewhere, [payload])
    receipt["execution"]["changed_files"] = ["../escape.csv"]
    assert _observed_write_evidence("omni_body", receipt, True) is None


def test_failure_can_recover_but_later_target_mutation_invalidates_observation():
    goal = [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'report.json', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}][0]
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
    obligations = [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'report.json', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}]
    obligations[0]["status"] = "satisfied"
    contract = registered_contract([{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'report.json', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}])
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




def test_real_test_summary_rejects_echo_masked_failure_zero_tests_and_too_few_tests():
    goal = [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'tests_passed', 'evidence_dependency_paths': [], 'minimum_test_count': 3}][0]
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
    goals = [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'tests_passed', 'evidence_dependency_paths': []}, {'id': 'execution:execution:2', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'program_execution', 'evidence_dependency_paths': []}]
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
    goal = next(item for item in [{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'worker.py', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'README.md', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'tests_passed', 'evidence_dependency_paths': ['worker.py'], 'minimum_test_count': 3}] if item.get("evidence_predicate") == "tests_passed")
    tested = observation("shell.run", result={"execution": {
        "ok": True, "returncode": 0, "stderr": "Ran 3 tests in 0.1s\n\nOK\n",
    }})
    tested["tool_args"]["args"]["command"] = "python -m unittest"
    def write(path):
        return observation("file.write", path, contract={"ok": True, "observed_write_effect": True,
            "write_evidence": {"authoritative": True, "changed_files": [path]}})
    assert integrity.obligation_is_satisfied(goal, [tested, write("README.md")])
    assert not integrity.obligation_is_satisfied(goal, [tested, write("worker.py")])


def test_deleted_output_does_not_keep_old_creation_pass():
    goal = [{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'result.json', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}][0]
    created = observation("file.write", "result.json", contract={"ok": True, "observed_write_effect": True})
    deleted = observation("file.delete", "result.json", contract={"ok": True,
        "write_evidence": {"authoritative": True, "deleted_files": ["result.json"],
                           "post": [{"path": "result.json", "exists": False}]}})
    assert integrity.obligation_is_satisfied(goal, [created])
    assert not integrity.obligation_is_satisfied(goal, [created, deleted])




def test_later_program_failure_reopens_requirement_until_recovery():
    goal = [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'command_execution'}][0]
    passed = observation("python.run", result={"execution": {"ok": True, "returncode": 0}})
    failed = observation("python.run", ok=False, result={"execution": {"ok": False, "returncode": 1}})
    assert integrity.obligation_is_satisfied(goal, [passed])
    assert not integrity.obligation_is_satisfied(goal, [passed, failed])
    assert integrity.obligation_is_satisfied(goal, [passed, failed, passed])


def test_native_python_test_script_requires_actual_framework_summary_and_process_success():
    goal = [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'tests_passed', 'evidence_dependency_paths': [], 'minimum_test_count': 3}][0]
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
    goal = [{'id': 'execution:execution:1', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'tests_passed', 'evidence_dependency_paths': [], 'minimum_test_count': 3}][0]
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
    goals = [{'id': 'execution:observation:1', 'kind': 'observation', 'object_kind': 'file', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'orders.csv', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2}, {'id': 'execution:effect:2', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'worker.py', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}, {'id': 'execution:effect:3', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'test_worker.py', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}, {'id': 'execution:execution:4', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'tests_passed', 'evidence_dependency_paths': ['orders.csv', 'worker.py', 'test_worker.py']}, {'id': 'execution:execution:5', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'program_execution', 'evidence_dependency_paths': ['orders.csv', 'worker.py']}]
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








def test_program_input_changes_still_invalidate_real_execution_evidence():
    prompt = "请创建 worker.py，只用标准库读取 orders.csv。请实际运行单元测试和脚本。"
    goals = [{'id': 'execution:effect:1', 'kind': 'effect', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': 'worker.py', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'target_state': 'present'}, {'id': 'execution:execution:2', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'tests_passed', 'evidence_dependency_paths': ['worker.py', 'orders.csv']}, {'id': 'execution:execution:3', 'kind': 'execution', 'object_kind': '', 'floor': 'ACT_REQUIRED', 'status': 'pending', 'actionable': True, 'target_path': '', 'evidence_policy': 'successful_real_tool_result', 'source': 'current_user_message', 'requirement_version': 2, 'evidence_predicate': 'program_execution', 'evidence_dependency_paths': ['worker.py', 'orders.csv']}]
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
