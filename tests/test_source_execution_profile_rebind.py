"""Actual filesystem/CLI coverage; no Electron, model, or application startup."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from contracts import canonical_sha256
from contracts.composition_profile import WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_ID
from total_gateway.composition_source_trial import (
    PROFILE_FILE, install_source_trial_profile, load_source_trial_profile,
)

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "src").is_dir():
    ROOT = Path(__file__).resolve().parents[3] / "source"
SCRIPT = ROOT / "scripts" / "rebind-source-execution-profile.py"
if not SCRIPT.exists():
    SCRIPT = Path(__file__).with_name("rebind-source-execution-profile.py")
spec = importlib.util.spec_from_file_location("rebind_source_profile_tested", SCRIPT)
rebind = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebind)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("TIANGONG_SOURCE_MODE", "1")
    state, old, new = (tmp_path / item for item in ("state", "old", "新任务 & spaces"))
    for item in (state, old, new):
        item.mkdir()
    return state, old, new


def prepare(roots):
    state, old, new = roots
    return rebind.prepare(state_root=state, previous_workspace=old, workspace=new)


def test_absent_profile_does_not_enable_or_create_any_config(roots):
    assert prepare(roots) is None
    assert not list(roots[0].iterdir())


@pytest.mark.parametrize("profile_id", [WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_ID])
def test_rebind_and_rollback_preserve_authority_and_exact_original_bytes(roots, profile_id):
    state, old, new = roots
    path = install_source_trial_profile(state_root=state, workspace_root=old, profile_id=profile_id)
    original = path.read_bytes()
    transaction = prepare(roots)
    assert path.read_bytes() == original  # prepare has no mutation
    assert rebind.apply(transaction)
    assert not rebind.apply(transaction)  # response loss/retry is idempotent
    profile = load_source_trial_profile(state_root=state, workspace_root=new)
    assert profile.execution_profile_id == profile_id
    new.rmdir()  # failed service restart may lose its newly selected directory
    assert rebind.apply(transaction, rollback=True)
    assert not rebind.apply(transaction, rollback=True)
    assert path.read_bytes() == original


@pytest.mark.parametrize("corruption", ["json", "digest", "profile", "extra", "binding"])
def test_invalid_config_is_never_repaired_or_enabled(roots, corruption):
    state, old, new = roots
    path = install_source_trial_profile(state_root=state, workspace_root=old)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "json":
        raw = b"broken profile"
    else:
        if corruption == "digest": payload["profile_config_sha256"] = "0" * 64
        if corruption == "profile": payload["execution_profile_id"] = "model.selected.arbitrary"
        if corruption == "extra": payload["enable_shell"] = True
        if corruption == "binding":
            payload["workspace_root"] = str(new)
            payload["profile_config_sha256"] = canonical_sha256({k: v for k, v in payload.items() if k != "profile_config_sha256"})
        raw = json.dumps(payload).encode("utf-8")
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        prepare(roots)
    assert path.read_bytes() == raw


def test_atomic_replace_failure_keeps_original_and_cleans_temporary(roots, monkeypatch):
    state, old, new = roots
    path = install_source_trial_profile(state_root=state, workspace_root=old)
    original = path.read_bytes()
    transaction = prepare(roots)
    def fail(*args):
        raise OSError("injected replace failure")
    monkeypatch.setattr(rebind.os, "replace", fail)
    with pytest.raises(OSError): rebind.apply(transaction)
    assert path.read_bytes() == original
    assert list(state.iterdir()) == [path]


def test_rollback_refuses_to_overwrite_a_concurrent_operator_change(roots):
    state, old, new = roots
    path = install_source_trial_profile(state_root=state, workspace_root=old)
    transaction = prepare(roots)
    rebind.apply(transaction)
    install_source_trial_profile(state_root=state, workspace_root=new, profile_id=WORKSPACE_WRITE_PROFILE_ID)
    concurrent = path.read_bytes()
    with pytest.raises(ValueError, match="concurrent_change"):
        rebind.apply(transaction, rollback=True)
    assert path.read_bytes() == concurrent


def test_transaction_cannot_promote_write_profile_to_python(roots):
    state, old, new = roots
    path = install_source_trial_profile(state_root=state, workspace_root=old, profile_id=WORKSPACE_WRITE_PROFILE_ID)
    original = path.read_bytes()
    transaction = prepare(roots)
    other_state = state / "other"
    other_state.mkdir()
    promoted = install_source_trial_profile(state_root=other_state, workspace_root=new)
    transaction["next_content"] = promoted.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="changes_authority"):
        rebind.apply(transaction)
    assert path.read_bytes() == original


def test_non_source_mode_cannot_prepare_or_mutate(roots, monkeypatch):
    state, old, new = roots
    path = install_source_trial_profile(state_root=state, workspace_root=old)
    original = path.read_bytes()
    transaction = prepare(roots)
    monkeypatch.delenv("TIANGONG_SOURCE_MODE")
    with pytest.raises(ValueError, match="requires_source_mode"): prepare(roots)
    with pytest.raises(ValueError, match="requires_source_mode"): rebind.apply(transaction)
    assert path.read_bytes() == original


def test_actual_cli_transaction_across_three_processes(roots):
    state, old, new = roots
    path = install_source_trial_profile(state_root=state, workspace_root=old)
    original = path.read_bytes()
    # runpy permits this draft's QA location; installed script resolves src
    # from its real scripts/ directory when executed by the Electron host.
    bootstrap = "import runpy,sys;sys.dont_write_bytecode=True;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    base = [sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT / "src"), str(SCRIPT)]
    def invoke(operation, args=(), transaction=None):
        result = subprocess.run([*base, operation, *args], input=json.dumps(transaction) if transaction else "",
                                text=True, encoding="utf-8", capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr + result.stdout
        return json.loads(result.stdout)
    prepared = invoke("prepare", ["--state-root", str(state), "--previous-workspace", str(old), "--workspace", str(new)])
    assert prepared["enabled"]
    invoke("apply", transaction=prepared["transaction"])
    assert load_source_trial_profile(state_root=state, workspace_root=new)
    invoke("rollback", transaction=prepared["transaction"])
    assert path.read_bytes() == original


@pytest.mark.parametrize("mode", ["success", "restart_failure", "lost_apply_response", "invalid", "absent", "packaged", "rollback_conflict"])
def test_host_workspace_transaction_restarts_only_with_matching_profile(roots, mode):
    desktop_state, old, new = roots
    runtime_root = desktop_state.parent / "runtime"
    state = runtime_root / "gateway"
    state.mkdir(parents=True)
    # Desktop state and Gateway authority are distinct in the real host.
    # A decoy at the desktop path must neither activate nor block a trial.
    desktop_decoy = desktop_state / PROFILE_FILE
    desktop_decoy.write_text("desktop state is not gateway profile authority", encoding="utf-8")
    original_desktop = desktop_decoy.read_bytes()
    path = state / PROFILE_FILE
    if mode != "absent":
        install_source_trial_profile(state_root=state, workspace_root=old)
    if mode == "invalid": path.write_text("broken json", encoding="utf-8")
    original = path.read_bytes() if path.exists() else None
    main = (ROOT / "app/main.js").read_text(encoding="utf-8")
    if "async function sourceTrialProfileCommand" in main:
        helper_start = (main.index("function gatewayRuntimeStateRoot") if "function gatewayRuntimeStateRoot" in main
                        else main.index("async function sourceTrialProfileCommand"))
        helper = main[helper_start:main.index("async function applyWorkspaceRootChange")]
        apply_source = main[main.index("async function applyWorkspaceRootChange"):main.index("async function setWorkspaceRoot")]
    else:
        draft = Path(__file__).parent
        helper = (draft / "source-profile-host-helper.js").read_text(encoding="utf-8")
        apply_source = (draft / "apply-workspace-function.js").read_text(encoding="utf-8")
    config = {"state": str(state), "desktop_state": str(desktop_state), "runtime_root": str(runtime_root),
              "old": str(old), "new": str(new), "root": str(ROOT),
              "script": str(SCRIPT), "python": sys.executable, "mode": mode}
    harness = r'''
const vm = require("vm"), fs = require("fs"), path = require("path");
const realExecFile = require("child_process").execFile;
const c = CONFIG;
const calls = [], starts = [], persisted = [];
let workspaceCommittedRoot = c.old;
const ctx = {
  SOURCE_MODE: c.mode !== "packaged", __dirname: path.join(c.root, "app"), fs, path,
  process: {env: {...process.env, TIANGONG_DESKTOP_STATE_DIR: c.desktop_state}},
  runtimeStateRoot: () => c.runtime_root,
  execFile(command, args, options, callback) {
    if (!options.windowsHide || typeof args === "string") throw Error("unsafe spawn");
    // The implementation remains a QA draft until the root unfreezes source.
    args = [...args]; args[3] = c.script;
    const bootstrap = "import runpy,sys;sys.dont_write_bytecode=True;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name='__main__')";
    args = ["-B", "-X", "utf8", "-c", bootstrap, path.join(c.root,"src"), ...args.slice(3)];
    return realExecFile(command, args, options, callback);
  },
  pythonCommand: () => c.python, mainWindow: null,
  workspaceChangeRevision: 0,
  committedWorkspaceRoot: () => workspaceCommittedRoot,
  sameWindowsPath: (a,b) => String(a).toLowerCase() === String(b).toLowerCase(),
  writeWorkspacePreference: (root) => persisted.push(root),
  writeDesktopDiagnostic() {}, startBackendWatchdog() {},
  async stopServicesForWorkspaceChange(reason) {calls.push(reason);},
  async startServicesForWorkspaceChange() {
    let profile = null;
    try {profile=JSON.parse(fs.readFileSync(path.join(c.state,"source-execution-profile.json"),"utf8")).workspace_root;} catch {}
    starts.push({env:ctx.process.env.TIANGONG_WORKSPACE_ROOT, profile});
    if ((c.mode === "restart_failure" || c.mode === "rollback_conflict") && starts.length === 1) {
      if(c.mode === "rollback_conflict") fs.writeFileSync(path.join(c.state,"source-execution-profile.json"),"operator update");
      throw Error("injected restart failure");
    }
    return {backendReady:true,totalGatewayReady:true};
  },
};
Object.defineProperty(ctx,"workspaceCommittedRoot",{get(){return workspaceCommittedRoot},set(v){workspaceCommittedRoot=v}});
vm.createContext(ctx);
vm.runInContext(SOURCE,ctx);
const originalCommand = ctx.sourceTrialProfileCommand;
ctx.sourceTrialProfileCommand = async (...args) => {
  calls.push(args[0]);
  const result = await originalCommand(...args);
  if(c.mode === "lost_apply_response" && args[0] === "apply") throw Error("lost child response");
  return result;
};
(async()=>{
  const result = await ctx.applyWorkspaceRootChange(c.new,0);
  console.log(JSON.stringify({result,starts,calls,persisted,workspaceCommittedRoot}));
})().catch(error=>{console.error(error);process.exit(1)});
'''
    harness = harness.replace("const c = CONFIG;", "const c = " + json.dumps(config) + ";")
    harness = harness.replace("vm.runInContext(SOURCE,ctx);", "vm.runInContext(" + json.dumps(helper + "\n" + apply_source) + ",ctx);")
    result = subprocess.run(["node", "-e", harness], text=True, capture_output=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert desktop_decoy.read_bytes() == original_desktop
    if mode in {"success", "absent", "packaged"}:
        assert outcome["result"]["ok"]
        assert outcome["workspaceCommittedRoot"] == str(new)
        assert outcome["persisted"] == [str(new)]
        if mode == "success": assert outcome["starts"] == [{"env": str(new), "profile": str(new)}]
        else: assert "prepare" not in outcome["calls"]
    else:
        assert not outcome["result"]["ok"]
        assert outcome["workspaceCommittedRoot"] == str(old)
        assert not outcome["persisted"]
        if mode == "rollback_conflict":
            assert not outcome["result"]["rolledBack"]
            assert outcome["result"]["profileRollbackError"]
            assert len(outcome["starts"]) == 1  # no restart under mismatched authority
            assert path.read_text(encoding="utf-8") == "operator update"
        else:
            assert outcome["result"]["rolledBack"]
            assert path.read_bytes() == original
            assert outcome["starts"][-1]["env"] == str(old)
    if mode == "absent": assert not path.exists()
    if mode == "packaged": assert path.read_bytes() == original
