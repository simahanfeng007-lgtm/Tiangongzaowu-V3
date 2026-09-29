from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from total_gateway import server as gateway_server


ROOT = Path(__file__).resolve().parents[1]


class SourceModeIsolationTests(unittest.TestCase):
    def test_source_gateway_candidates_ignore_foreign_root_and_packaged_executable(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        start = main.index("function totalGatewayEntries()")
        end = main.index("function totalGatewayEntry()", start)
        candidates_function = main[start:end]
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const path = require('node:path');
const [firstRoot, siblingRoot] = __ROOTS__;
const firstSource = path.join(firstRoot, 'src');
const siblingSource = path.join(siblingRoot, 'src');
const packaged = path.join(siblingRoot, 'total-gateway.exe');
const files = new Set([
  path.join(firstSource, 'total_gateway', '__main__.py'),
  path.join(siblingSource, 'total_gateway', '__main__.py'),
  path.join(firstRoot, 'scripts', 'source-total-gateway-entry.py'),
  path.join(siblingRoot, 'scripts', 'source-total-gateway-entry.py'),
  packaged,
]);
function candidates(sourceMode) {
  let boundCalls = 0;
  const ctx = {
    SOURCE_MODE: sourceMode, path, __dirname: path.join(firstRoot, 'app'),
    process: {resourcesPath: siblingRoot, env: {
      TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT: siblingSource,
      TIANGONG_TOTAL_GATEWAY_EXE: packaged,
    }},
    boundComponentExecutable: () => { boundCalls++; return packaged; },
    isFile: file => files.has(file),
    pythonCommand: () => '/python',
  };
  const entries = vm.runInNewContext(__FUNCTION__ + '\ntotalGatewayEntries()', ctx);
  return {entries, boundCalls};
}
const source = candidates(true);
assert.equal(source.boundCalls, 0);
assert.equal(source.entries.length, 1);
assert.equal(source.entries[0].pythonPath, firstSource);
assert.equal(source.entries[0].cwd, firstRoot);
assert.equal(source.entries[0].kind, 'development-source');
const release = candidates(false);
assert.equal(release.boundCalls, 1);
assert.equal(release.entries[0].kind, 'release-bound-executable');
assert.equal(release.entries[0].command, packaged);
assert.equal(release.entries.some(entry => entry.pythonPath === siblingSource), true);
""".replace("__FUNCTION__", json.dumps(candidates_function))
        with tempfile.TemporaryDirectory() as temporary:
            roots = [Path(temporary) / "checkout-a", Path(temporary) / "checkout-b"]
            result = subprocess.run(
                ["node", "-e", script.replace("__ROOTS__", json.dumps([str(root) for root in roots]))],
                cwd=ROOT, text=True, capture_output=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_source_checkout_id_comes_from_canonical_app_tree(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        start = main.index("const SOURCE_CHECKOUT_ID = SOURCE_MODE")
        end = main.index("// 便携模式", start)
        identity_expression = main[start:end]
        script = r"""
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = __EXPRESSION__;
const roots = __ROOTS__;
function compute(root, packaged) {
  return vm.runInNewContext(source + '\nSOURCE_CHECKOUT_ID', {
    SOURCE_MODE: !packaged, crypto, fs, path, process,
    __dirname: path.join(root, 'app'),
  });
}
const first = compute(roots[0], false), second = compute(roots[1], false);
assert.match(first, /^[0-9a-f]{64}$/);
assert.notEqual(first, second);
assert.equal(first, compute(roots[0], false));
assert.equal(compute(roots[0], true), '');
""".replace("__EXPRESSION__", json.dumps(identity_expression))
        with tempfile.TemporaryDirectory() as temporary:
            roots = [Path(temporary) / "checkout-a", Path(temporary) / "checkout-b"]
            for root in roots:
                (root / "app").mkdir(parents=True)
            result = subprocess.run(
                ["node", "-e", script.replace("__ROOTS__", json.dumps([str(root) for root in roots]))],
                cwd=ROOT, text=True, capture_output=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_source_gateway_health_requires_same_checkout_even_with_shared_epoch(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        start = main.index("async function totalGatewayHealthCheck(")
        end = main.index("function totalGatewayEntries()", start)
        functions = main[start:end]
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const path = require('node:path');
const owner = 'a'.repeat(64), sibling = 'b'.repeat(64);
const ownProcess = 'c'.repeat(64), priorProcess = 'd'.repeat(64);
let identity = owner, processIdentity = ownProcess;
const ctx = {
  SOURCE_MODE: true,
  SOURCE_CHECKOUT_ID: owner,
  SOURCE_GATEWAY_OWNER_ID: ownProcess,
  totalGatewayRequest: async route => ({statusCode: 200, payload: route === '/health'
    ? {component_id: 'tiangong-total-gateway', status: 'ALIVE',
       instance_id: 'owner-instance', gateway_epoch: 4, source_checkout_id: identity,
       source_gateway_owner_id: processIdentity}
    : {component_id: 'tiangong-total-gateway', status: 'READY', source_checkout_id: identity,
       source_gateway_owner_id: processIdentity}}),
  runtimeStateRoot: () => '/shared/data/runtime/state',
  fs: {readFileSync: () => JSON.stringify({instance_id: 'owner-instance', gateway_epoch: 4})},
  path,
};
const probes = vm.runInNewContext(__FUNCTIONS__ + '\n({health: totalGatewayHealthCheck, ready: totalGatewayReadyCheck})', ctx);
(async () => {
  assert.equal(await probes.health(), true);
  assert.equal(await probes.ready(), true);
  processIdentity = priorProcess;
  assert.equal(await probes.health(), false);
  assert.equal(await probes.ready(), false);
  processIdentity = undefined;
  assert.equal(await probes.health(), false);
  assert.equal(await probes.ready(), false);
  processIdentity = ownProcess;
  identity = sibling;
  assert.equal(await probes.health(), false);
  assert.equal(await probes.ready(), false);
  identity = undefined;
  assert.equal(await probes.health(), false);
  assert.equal(await probes.ready(), false);
  ctx.SOURCE_MODE = false;
  assert.equal(await probes.health(), true);
  assert.equal(await probes.ready(), true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("__FUNCTIONS__", json.dumps(functions))
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preexisting_gateway_is_neither_adopted_nor_stopped_even_at_same_path(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        health_start = main.index("async function totalGatewayHealthCheck(")
        health_end = main.index("function totalGatewayEntries()", health_start)
        wait_start = main.index("async function waitForTotalGateway(")
        start = main.index("async function startTotalGateway()", wait_start)
        end = main.index("function sameWindowsPath(", start)
        functions = main[health_start:health_end] + main[wait_start:main.index("async function waitForTotalGatewayReadiness", wait_start)] + main[start:end]
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const path = require('node:path');
const checkout = 'a'.repeat(64), owner = 'b'.repeat(64);
let listenerPids = [48184], occupied = true, killed = [], stopped = [];
const ctx = {
  SOURCE_MODE: true,
  SOURCE_CHECKOUT_ID: checkout,
  SOURCE_GATEWAY_OWNER_ID: owner,
  SERVICE_START_ATTEMPTS: 1,
  serviceSupervisor: {draining: false},
  setTimeout,
  totalGatewayStarting: false,
  totalGatewayProcess: null,
  adoptedTotalGatewayPids: new Set(),
  totalGatewayRequest: async route => ({statusCode: 200, payload: route === '/health'
    ? {component_id: 'tiangong-total-gateway', status: 'ALIVE',
       instance_id: 'same-profile-instance', gateway_epoch: 2,
       source_checkout_id: checkout, source_gateway_owner_id: owner}
    : {component_id: 'tiangong-total-gateway', status: 'READY',
       source_checkout_id: checkout, source_gateway_owner_id: owner}}),
  runtimeStateRoot: () => '/shared/data/runtime/state',
  fs: {readFileSync: () => JSON.stringify({instance_id: 'same-profile-instance', gateway_epoch: 2})},
  path,
  stopBackendGatewayAsync: async () => {}, backendDir: () => '/checkout-a/backend',
  stopLifeServiceAsync: async () => {}, stopCommunicationServiceAsync: async () => {},
  totalGatewayListenerPids: () => listenerPids,
  totalGatewayPortOccupied: async () => occupied,
  writeDesktopDiagnostic: () => {},
  totalGatewayEntries: () => { throw new Error('pre-existing listener must prevent spawn'); },
  killProcessTreeSync: pid => killed.push(pid),
  stopChildGracefully: async child => { stopped.push(child.pid); },
  console,
};
const actions = vm.runInNewContext(__FUNCTIONS__ + '\n({start: startTotalGateway, stop: stopTotalGateway})', ctx);
(async () => {
  // A prior process from the exact same checkout can have the same path ID,
  // epoch, and even a synthetic matching owner ID. It is still not our child.
  assert.equal(await actions.start(), false);
  assert.equal(ctx.adoptedTotalGatewayPids.size, 0);
  await actions.stop('old-source');
  assert.deepEqual(killed, []);
  ctx.totalGatewayStarting = true;
  assert.equal(await actions.start(), false); // A concurrent call cannot accept the old listener.
  ctx.totalGatewayStarting = false;
  listenerPids = [];
  assert.equal(await actions.start(), false); // Linux has no PID scan; socket probe catches it.
  await actions.stop('old-source-no-pid');
  assert.deepEqual(killed, []);

  const child = {pid: 777, exitCode: null, signalCode: null};
  ctx.totalGatewayProcess = child;
  listenerPids = [777];
  ctx.totalGatewayStarting = true;
  assert.equal(await actions.start(), true); // Re-entry may wait for this Electron's own child.
  ctx.totalGatewayStarting = false;
  assert.equal(await actions.start(), true);
  assert.equal(ctx.adoptedTotalGatewayPids.size, 0);
  await actions.stop('own-child');
  assert.deepEqual(stopped, [777]);
  assert.deepEqual(killed, []);

  // Packaged mode retains its existing healthy-listener adoption semantics.
  ctx.SOURCE_MODE = false;
  listenerPids = [48184];
  assert.equal(await actions.start(), true);
  assert.equal(ctx.adoptedTotalGatewayPids.has(48184), true);
  await actions.stop('packaged');
  assert.deepEqual(killed, [48184]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("__FUNCTIONS__", json.dumps(functions))
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_gateway_identity_is_only_exposed_for_its_actual_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_file = root / "src" / "total_gateway" / "server.py"
            embedded_file = root / "app" / "runtime" / "python312" / "Lib" / "site-packages" / "total_gateway" / "server.py"
            sibling_file = root / "sibling" / "src" / "total_gateway" / "server.py"
            for file in (source_file, embedded_file, sibling_file):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("# source identity fixture\n", encoding="utf-8")
            main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
            start = main.index("const SOURCE_CHECKOUT_ID = SOURCE_MODE")
            end = main.index("// 便携模式", start)
            expression = main[start:end]
            script = "\n".join((
                "const crypto = require('node:crypto');",
                "const fs = require('node:fs');",
                "const path = require('node:path');",
                "const SOURCE_MODE = true;",
                f"const __dirname = path.join({json.dumps(str(root))}, 'app');",
                expression,
                "process.stdout.write(SOURCE_CHECKOUT_ID);",
            ))
            computed = subprocess.run(
                ["node", "-e", script], cwd=ROOT, text=True,
                capture_output=True, timeout=20,
            )
            self.assertEqual(computed.returncode, 0, computed.stdout + computed.stderr)
            identity = computed.stdout
            self.assertEqual(len(identity), 64)
            source_env = {
                "TIANGONG_SOURCE_MODE": "1",
                "TIANGONG_SOURCE_ROOT": str(root),
                "TIANGONG_SOURCE_CHECKOUT_ID": identity,
            }
            for file in (source_file, embedded_file):
                with self.subTest(file=file), mock.patch.object(gateway_server, "__file__", str(file)):
                    self.assertEqual(gateway_server._source_checkout_id(source_env), identity)
            with mock.patch.object(gateway_server, "__file__", str(sibling_file)):
                self.assertIsNone(gateway_server._source_checkout_id(source_env))
            with mock.patch.object(gateway_server, "__file__", str(source_file)):
                self.assertIsNone(gateway_server._source_checkout_id({**source_env, "TIANGONG_SOURCE_CHECKOUT_ID": ""}))
                self.assertIsNone(gateway_server._source_checkout_id({**source_env, "TIANGONG_SOURCE_CHECKOUT_ID": "a" * 64}))
                self.assertIsNone(gateway_server._source_checkout_id({**source_env, "TIANGONG_SOURCE_MODE": "0"}))
                self.assertIsNone(gateway_server._source_checkout_id({**source_env, "TIANGONG_SOURCE_ROOT": str(root / "sibling")}))

    def test_direct_electron_uses_the_launcher_source_profile(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        start = main.index("const SOURCE_MODE = app.isPackaged === false;")
        end = main.index("const SOURCE_ISOLATION = configureSourceIsolation();")
        source = main[start : end + len("const SOURCE_ISOLATION = configureSourceIsolation();")]
        script = """
const vm = require('node:vm');
const path = require('node:path');
const app = {
  isPackaged: false,
  getVersion: () => '3.0.3',
  getPath: () => path.parse(process.cwd()).root,
  setName() {},
  setPath() {},
};
const fs = {
  constants: {R_OK: 4, W_OK: 2},
  mkdirSync() {},
  accessSync() {},
};
const isolatedProcess = {env: {LOCALAPPDATA: path.join(process.cwd(), 'unrelated-localappdata')}};
const profile = vm.runInNewContext(__SOURCE_CODE__ + String.fromCharCode(10) + 'SOURCE_ISOLATION.profileRoot', {
  app, fs, path, process: isolatedProcess, __dirname: __APP_DIR__,
});
process.stdout.write(JSON.stringify(profile));
""".replace("__SOURCE_CODE__", json.dumps(source)).replace(
            "__APP_DIR__", json.dumps(str(ROOT / "app"))
        )
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=20
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), str((ROOT.parent / "data").resolve()))

    def test_source_launcher_binds_all_mutable_roots_before_electron(self) -> None:
        script = (ROOT / "scripts" / "start-source.ps1").read_text(encoding="utf-8")
        required = (
            "TIANGONG_SOURCE_MODE",
            "TIANGONG_SOURCE_PROFILE_ROOT",
            "TIANGONG_SOURCE_USER_DATA",
            "TIANGONG_DESKTOP_RUNTIME_ROOT",
            "TIANGONG_DESKTOP_STATE_DIR",
            "TIANGONG_RUN_STATE_DIR",
            "TIANGONG_V3_STATE_DIR",
            "TIANGONG_DESKTOP_WORKSPACE_ROOT",
            "TIANGONG_WORKSPACE_ROOT",
            "TIANGONG_FORCE_WORKSPACE_ROOT",
            "TIANGONG_OMNI_BODY_WORKSPACE",
            "TIANGONG_HOME_PATH",
            "TIANGONG_LIFE_DATA_ROOT",
            "TIANGONG_LIFE_RUNTIME_ROOT",
            "TIANGONG_LIFE_KERNEL_ROOT",
            "TIANGONG_LIFE_ROOT",
            "TIANGONG_EXECUTION_RUNTIME_ROOT",
            "TIANGONG_EXECUTION_LIFE_ROOT",
        )
        for name in required:
            with self.subTest(name=name):
                self.assertIn(f"$env:{name}", script)
        self.assertIn('[System.IO.Path]::GetFullPath($ProfileRoot)', script)
        self.assertIn('Join-Path (Split-Path $Root -Parent) "data"', script)
        self.assertNotIn('Join-Path $HostLocalAppData "TiangongV3-SourceWork"', script)
        self.assertIn('"--user-data-dir=$SourceUserData"', script)
        self.assertLess(
            script.index('"--user-data-dir=$SourceUserData"'),
            script.index('$ElectronArgs += "."'),
        )

    def test_source_launcher_preserves_real_known_folders_and_fails_closed_on_7184(self) -> None:
        script = (ROOT / "scripts" / "start-source.ps1").read_text(encoding="utf-8")
        for name in (
            "TIANGONG_DESKTOP_PATH",
            "TIANGONG_DOWNLOADS_PATH",
            "TIANGONG_DOCUMENTS_PATH",
            "TIANGONG_PICTURES_PATH",
            "TIANGONG_MUSIC_PATH",
            "TIANGONG_VIDEOS_PATH",
        ):
            self.assertIn(f"$env:{name}", script)
        self.assertIn("Get-NetTCPConnection -State Listen -LocalPort 7184", script)
        self.assertIn("if ($GatewayListeners.Count -gt 0)", script)
        self.assertNotIn("$OwnedBySource", script)
        self.assertLess(script.index("if ($GatewayListeners.Count -gt 0)"), script.index("$env:TIANGONG_SOURCE_MODE"))
        self.assertIn("Source mode will not adopt or stop it", script)
        self.assertNotIn("Stop-Process", script)

    def test_main_process_isolates_before_portable_mode_and_single_instance_lock(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        configured = main.index("configureSourceIsolation();")
        portable = main.index("const portableExecutableDir")
        lock = main.index("app.requestSingleInstanceLock()")
        self.assertLess(configured, portable)
        self.assertLess(configured, lock)
        self.assertIn("const SOURCE_MODE = app.isPackaged === false;", main)
        self.assertIn('app.setPath("userData", userData);', main)
        self.assertIn('app.setName(SOURCE_PRODUCT_LABEL);', main)
        self.assertIn('"com.tiangong.v3.qiyuan.source"', main)
        self.assertIn('SOURCE_MODE\n  ? ""', main.replace("\r\n", "\n"))

    def test_runtime_identity_is_dynamic_but_release_identity_is_unchanged(self) -> None:
        package = json.loads((ROOT / "app" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["productName"], "天工造物 v3.0.3 完整版")
        self.assertEqual(
            package["tiangongRelease"]["canonicalAppId"],
            "com.tiangong.v3.qiyuan",
        )

        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        preload = (ROOT / "app" / "preload.js").read_text(encoding="utf-8")
        bootstrap = (
            ROOT / "app" / "frontend-v2" / "renderer" / "bootstrap.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("productLabel: PRODUCT_LABEL", main)
        self.assertIn("sourceMode: SOURCE_MODE", main)
        self.assertIn("bootstrapMetadata.productLabel", preload)
        self.assertIn("bootstrapMetadata.sourceMode === true", preload)
        self.assertIn("document.title = `${runtimeProductLabel} · 起源`", bootstrap)
        self.assertIn('"source"', bootstrap)


if __name__ == "__main__":
    unittest.main()
