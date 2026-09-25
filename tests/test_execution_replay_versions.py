"""Real SQLite effects and real processes: correction must execute, not replay."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from capability_dictionary.composition import compile_task_composition
from tests.test_task_generated_composition import gateway, register, prepare
from total_gateway.regenerative_provider import RegenerativeExecutionAuthority
from v3.simple_chain import kernel
from v3.runtime_turn_orchestration import PreparedStep, coordinate_parallel_steps


def program(*calls):
    return {"tools": [{"id": "work", "description": "perform concrete actions", "actions": list(calls)}],
            "skill": {"id": "task", "description": "inspect and repair",
                      "steps": [{"id": "s", "tool": "work", "depends_on": []}]}}


def action(name, target, **args):
    return {"action": name, "target": target, "args": args}


def invoke(case, call, step, handler=None, *, outcome="succeeded", registration=None, leaf=None):
    proposal = program(call)
    registration = registration or register(case, proposal)
    leaf = leaf or compile_task_composition(proposal)["leaves"][0]
    prepared = prepare(case, registration, leaf, step)
    if prepared["disposition"] != "prepared":
        return prepared
    effect = {k: prepared[k] for k in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}
    started = case.provider(case.payload("start_effect", now_ms=8000 + step * 10, **effect))
    assert started["dispatch_permitted"]
    result = handler() if handler else {"ok": outcome == "succeeded"}
    finished = case.provider(case.payload("finish_effect", now_ms=8001 + step * 10,
        outcome=outcome, result_summary=result, **effect))
    return {**prepared, **finished, "actual_result": result}


def run_script(root):
    completed = subprocess.run([sys.executable, str(root / "aggregate.py"), "records.json", "totals.json"],
                               cwd=root, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    return {"ok": True, "value": json.loads((root / "totals.json").read_text())}


OLD = "import json,sys\na=json.load(open(sys.argv[1]));r={}\nfor k,v in a:r[k]=v\njson.dump(r,open(sys.argv[2],'w'))\n"
NEW = OLD.replace("r[k]=v", "r[k]=r.get(k,0)+v")


def test_exit_zero_old_code_patch_same_argv_runs_again_and_updates_original_output(gateway, tmp_path):
    (tmp_path / "aggregate.py").write_text(OLD)
    (tmp_path / "records.json").write_text('[["X",7],["X",5]]')
    call = action("python.run", "aggregate.py", argv=["records.json", "totals.json"], timeout=60)
    first = invoke(gateway, call, 1, lambda: run_script(tmp_path))
    assert first["actual_result"]["value"] == {"X": 5}
    duplicate = invoke(gateway, call, 2)
    assert duplicate["disposition"] == "already_committed"
    patch = action("code.patch_replace", "aggregate.py", find="r[k]=v", replace="r[k]=r.get(k,0)+v")
    def change():
        (tmp_path / "aggregate.py").write_text(NEW)
        return {"ok": True}
    invoke(gateway, patch, 3, change)
    # The host must preserve validity across restart, too.
    gateway.provider = RegenerativeExecutionAuthority(gateway.store, workspace_root=tmp_path, require_compositions=True)
    repaired = invoke(gateway, call, 4, lambda: run_script(tmp_path))
    assert repaired["disposition"] == "prepared"
    assert repaired["logical_effect_id"] != first["logical_effect_id"]
    assert repaired["actual_result"]["value"] == {"X": 12}
    assert invoke(gateway, call, 5)["disposition"] == "already_committed"


def test_changed_input_and_read_target_are_observed_even_outside_prior_tool_call(gateway, tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("old")
    call = action("file.read", "data.txt")
    invoke(gateway, call, 1, lambda: {"ok": True, "text": target.read_text()})
    target.write_text("new")  # external editor; no run-local mutation receipt
    assert invoke(gateway, call, 2, lambda: {"ok": True, "text": target.read_text()})["actual_result"]["text"] == "new"
    (tmp_path / "aggregate.py").write_text(NEW)
    data = tmp_path / "records.json"
    data.write_text('[["X",8],["X",-11]]')
    command = action("python.run", "aggregate.py", argv=["records.json", "totals.json"])
    assert invoke(gateway, command, 3, lambda: run_script(tmp_path))["actual_result"]["value"] == {"X": -3}
    data.write_text('[]')
    assert invoke(gateway, command, 4, lambda: run_script(tmp_path))["actual_result"]["value"] == {}


def test_recorded_mutation_invalidates_implicit_process_dependency(gateway, tmp_path):
    dependency = tmp_path / "implicit.txt"
    dependency.write_text("1")
    call = action("python.run", "", code="print(open('implicit.txt').read())")
    invoke(gateway, call, 1)
    write = action("file.write", "implicit.txt", content="2")
    invoke(gateway, write, 2, lambda: (dependency.write_text("2"), {"ok": True})[1])
    assert invoke(gateway, call, 3)["disposition"] == "prepared"


def test_same_external_side_effect_remains_deduplicated_after_other_mutations(gateway, tmp_path):
    # file.append is non-repeatable just like send/create effects: no blanket
    # invalidation of every success after a workspace write.
    target = tmp_path / "append.txt"
    call = action("file.append", "append.txt", content="once")
    invoke(gateway, call, 1, lambda: (target.write_text("once"), {"ok": True})[1])
    invoke(gateway, action("file.write", "other.txt", content="changed"), 2)
    assert invoke(gateway, call, 3)["disposition"] == "already_committed"
    assert target.read_text() == "once"


def test_applied_patch_is_reused_after_unrelated_mutation_but_not_target_change(gateway, tmp_path):
    path = tmp_path / "script.py"
    path.write_text("old")
    patch = action("code.patch_replace", "script.py", find="old", replace="new")
    invoke(gateway, patch, 1, lambda: (path.write_text("new"), {"ok": True})[1])
    invoke(gateway, action("file.write", "unrelated.txt", content="other"), 2)
    assert invoke(gateway, patch, 3)["disposition"] == "already_committed"
    path.write_text("old")
    assert invoke(gateway, patch, 4, lambda: (path.write_text("new"), {"ok": True})[1])["disposition"] == "prepared"
    assert path.read_text() == "new"


@pytest.mark.parametrize("outcome", ["ambiguous", "in_flight"])
def test_state_change_cannot_evade_unresolved_process_effect(gateway, tmp_path, outcome):
    script = tmp_path / "process.py"
    script.write_text("print(1)")
    call = action("python.run", "process.py")
    if outcome == "ambiguous":
        invoke(gateway, call, 1, outcome="ambiguous")
    else:
        value = program(call)
        registered = register(gateway, value)
        prepared = prepare(gateway, registered, compile_task_composition(value)["leaves"][0], 1)
        gateway.provider(gateway.payload("start_effect", now_ms=4000,
            **{k: prepared[k] for k in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}))
    script.write_text("print(2)")
    assert invoke(gateway, call, 2)["disposition"] == ("reconcile_required" if outcome == "ambiguous" else "in_flight")


def test_serial_and_parallel_cache_defer_task_calls_to_gateway(monkeypatch):
    monkeypatch.setattr(kernel, "current_run_context", lambda: SimpleNamespace(outer_execution_ticket_id="bound"))
    call = action("python.run", "same.py", argv=["in.json", "out.json"])
    reusable = kernel._simple_chain_should_replay_cached_call({"ok": True}, tool_name="omni_body", tool_args=call)
    assert reusable is False  # same decision used by the serial path
    candidate = PreparedStep("omni_body", call, "python.run", (), "same-call", reusable)
    coordinated = coordinate_parallel_steps([candidate, candidate])
    assert coordinated.ready == (candidate,) and not coordinated.reused
    assert kernel._simple_chain_should_replay_cached_call({"ok": True}, tool_name="omni_body",
        tool_args=action("system.action_schema", "python.run")) is True


def test_state_bound_predecessor_receipts_unlock_next_leaf(gateway, tmp_path):
    (tmp_path / "a.txt").write_text("v1")
    value = program(action("file.read", "a.txt"), action("file.hash", "a.txt"))
    registered = register(gateway, value)
    leaves = compile_task_composition(value)["leaves"]
    for step, leaf in enumerate(leaves, 1):
        assert invoke(gateway, leaf["invocation"], step, registration=registered, leaf=leaf)["disposition"] == "prepared"


def test_remote_read_target_is_not_misclassified_as_a_missing_local_file(tmp_path):
    from total_gateway.execution_replay import replay_basis
    release = SimpleNamespace(tools={"remote.read": {"effect": "read", "binding": {"kind": "method"}}})
    one = replay_basis(action("remote.read", "record123"), base_id="same", events=(),
        workspace=tmp_path, release=release, global_step=1)
    two = replay_basis(action("remote.read", "record123"), base_id="same", events=(),
        workspace=tmp_path, release=release, global_step=2)
    assert one != two and one["local_versions"] == {}


def test_reconciliation_proof_allows_composition_to_continue(gateway, tmp_path):
    (tmp_path / "a.txt").write_text("observed")
    value = program(action("file.read", "a.txt"), action("file.hash", "a.txt"))
    registered = register(gateway, value)
    leaves = compile_task_composition(value)["leaves"]
    first = invoke(gateway, leaves[0]["invocation"], 1, outcome="ambiguous", registration=registered, leaf=leaves[0])
    effect = {key: first[key] for key in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}
    gateway.provider(gateway.payload("reconcile_effect", now_ms=10000, verdict="APPLIED",
        evidence={"source": "test.actual_readback", "content": (tmp_path / "a.txt").read_text()}, **effect))
    assert invoke(gateway, leaves[1]["invocation"], 2, registration=registered, leaf=leaves[1])["disposition"] == "prepared"
