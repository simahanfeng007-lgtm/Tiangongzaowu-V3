"""Real host transaction/supervisor and profile CLI, simulated bounded probes.

No Electron application, backend, model, or production configuration is used.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from total_gateway.composition_source_trial import install_source_trial_profile, load_source_trial_profile


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["delayed_ready", "never_ready", "child_exit", "late_ready", "start_failed", "ordinary_start"])
def test_workspace_transaction_waits_for_ready_or_restores_profile(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("TIANGONG_SOURCE_MODE", "1")
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    runtime = tmp_path / "runtime"
    state = runtime / "gateway"
    state.mkdir(parents=True)
    profile = install_source_trial_profile(state_root=state, workspace_root=old)
    original = profile.read_bytes()
    main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
    workspace_functions = main[main.index("async function startServicesForWorkspaceChange"):main.index("async function setWorkspaceRoot")]
    readiness_functions = main[main.index("async function waitForTotalGatewayReadiness"):main.index("async function startTotalGateway")]
    assert "ready: totalGatewayServiceReadyCheck" in main
    config = dict(mode=mode, old=str(old), new=str(new), runtime=str(runtime), profile=str(profile), root=str(ROOT), python=sys.executable)
    harness = r'''
const assert = require("node:assert/strict"), vm = require("node:vm");
const fs = require("node:fs"), path = require("node:path");
const {execFile} = require("node:child_process");
const c = CONFIG;
const {ServiceSupervisor} = require(path.join(c.root,"app","service-supervisor.js"));
let clock = 0, starts = 0, committed = c.old, probesThisStart = 0;
const probes = [], transitions = [], commits = [], stops = [], profilesAtStart = [];
const ctx = {
  SOURCE_MODE:true, CREDENTIAL_RESTART_TIMEOUT_MS:1000, __dirname:path.join(c.root,"app"),
  fs, path, execFile, pythonCommand:()=>c.python,
  process:{env:{...process.env,TIANGONG_SOURCE_MODE:"1",TIANGONG_WORKSPACE_MODE:"workspace",
    TIANGONG_DESKTOP_WORKSPACE_ROOT:c.old,TIANGONG_WORKSPACE_ROOT:c.old,
    TIANGONG_FORCE_WORKSPACE_ROOT:c.old,TIANGONG_OMNI_BODY_WORKSPACE:c.old}},
  runtimeStateRoot:()=>c.runtime, mainWindow:null, totalGatewayProcess:null,
  workspaceChangeRevision:0, workspaceServiceStartInProgress:false,
  Date:{now:()=>clock}, setTimeout(callback,ms){clock+=ms;queueMicrotask(callback);},
  committedWorkspaceRoot:()=>committed,
  sameWindowsPath:(a,b)=>String(a).toLowerCase()===String(b).toLowerCase(),
  writeDesktopDiagnostic(){}, startBackendWatchdog(){},
  writeWorkspacePreference(workspace){
    assert.equal(ctx.serviceSupervisor.snapshot()["total-gateway"].ready,true);
    commits.push(workspace);
  },
  async stopServicesForWorkspaceChange(reason){stops.push(reason);await ctx.serviceSupervisor.stop("total-gateway",reason);},
  async totalGatewayReadyCheck(){
    probesThisStart++;
    // Preference/committed authority cannot change while readiness is pending.
    assert.equal(committed,c.old); assert.equal(commits.length,0);
    clock+=100;
    if(c.mode==="child_exit" && starts===1) ctx.totalGatewayProcess.exitCode=9;
    if(c.mode==="late_ready" && starts===1) clock+=1000;
    const ready = starts>1 ? probesThisStart>=2 : c.mode==="delayed_ready" ? probesThisStart>=3 : c.mode==="late_ready";
    probes.push({start:starts,ready,clock});
    return ready;
  },
};
Object.defineProperty(ctx,"workspaceCommittedRoot",{get:()=>committed,set:v=>{committed=v;}});
vm.createContext(ctx);
vm.runInContext(__HOST_FUNCTIONS__,ctx);
ctx.serviceSupervisor = new ServiceSupervisor({
  services:[{name:"total-gateway",phase:0,
    async start(){
      starts++;probesThisStart=0;ctx.totalGatewayProcess={exitCode:null};
      const bound=JSON.parse(fs.readFileSync(c.profile,"utf8")).workspace_root;
      profilesAtStart.push(bound);
      assert.equal(bound,ctx.process.env.TIANGONG_WORKSPACE_ROOT);
      return !(c.mode==="start_failed" && starts===1);
    },health:async()=>true,ready:ctx.totalGatewayServiceReadyCheck,
    async stop(){if(ctx.totalGatewayProcess)ctx.totalGatewayProcess.exitCode=0;ctx.totalGatewayProcess=null;}}],
  onTransition:event=>transitions.push({status:event.status,ready:event.ready}),
});
(async()=>{
 const result=c.mode==="ordinary_start" ? await ctx.serviceSupervisor.start("total-gateway") : await ctx.applyWorkspaceRootChange(c.new,0);
 assert.equal(ctx.workspaceServiceStartInProgress,false);
 const snapshot=ctx.serviceSupervisor.snapshot();
 if(c.mode==="delayed_ready") {
   assert.equal(result.ok,true); assert.equal(starts,1); assert.equal(probes.length,3);
   assert.equal(result.services.snapshot["total-gateway"].ready,true);
   assert.equal(snapshot["total-gateway"].status,"RUNNING");
   assert.deepEqual(commits,[c.new]); assert.equal(committed,c.new);
   assert.deepEqual(stops,["workspace-root-change"]);
 } else if(c.mode==="ordinary_start") {
   assert.equal(result.running,true);assert.equal(result.ready,false);
   assert.equal(probes.length,1);assert.equal(commits.length,0);
   assert.equal(snapshot["total-gateway"].status,"DEGRADED");
 } else {
   assert.equal(result.ok,false);assert.equal(result.rolledBack,true);
   assert.equal(result.profileRollbackError,"");assert.equal(starts,2);
   assert.equal(result.rollbackServices.totalGatewayReady,true);
   assert.equal(result.rollbackServices.snapshot["total-gateway"].ready,true);
   assert.equal(snapshot["total-gateway"].status,"RUNNING");
   assert.deepEqual(commits,[]);assert.equal(committed,c.old);
   assert.equal(ctx.process.env.TIANGONG_WORKSPACE_ROOT,c.old);
   assert.deepEqual(profilesAtStart,[c.new,c.old]);
   if(c.mode==="never_ready") assert.ok(probes.filter(p=>p.start===1).length>1);
   if(c.mode==="child_exit") assert.equal(probes.filter(p=>p.start===1).length,1);
   if(c.mode==="late_ready") assert.equal(probes.filter(p=>p.start===1)[0].ready,true);
 }
 console.log(JSON.stringify({mode:c.mode,result,probes,transitions,commits,profilesAtStart}));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    harness = harness.replace("CONFIG", json.dumps(config)).replace("__HOST_FUNCTIONS__", json.dumps(workspace_functions + readiness_functions))
    completed = subprocess.run(["node", "-e", harness], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8", timeout=40)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["mode"] == mode
    if mode == "delayed_ready":
        assert load_source_trial_profile(state_root=state, workspace_root=new)
    else:
        assert profile.read_bytes() == original
        assert load_source_trial_profile(state_root=state, workspace_root=old)
