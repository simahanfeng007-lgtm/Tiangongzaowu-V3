import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const configPath = fileURLToPath(new URL("../electron-builder.config.cjs", import.meta.url));

function loadConfiguration(mode) {
  const env = { ...process.env, TIANGONG_RELEASE_FLAVOR: "unsigned" };
  delete env.TIANGONG_ISOLATED_INSTALL;
  delete env.TIANGONG_RELEASE_REQUIRE_SIGNING;
  if (mode !== undefined) env.TIANGONG_ISOLATED_INSTALL = mode;
  const result = spawnSync(process.execPath, [
    "-e", "process.stdout.write(JSON.stringify(require(process.argv[1])))", configPath,
  ], { env, encoding: "utf8", windowsHide: true });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test("normal installation retains its release and Windows installation identity", () => {
  const config = loadConfiguration();
  assert.equal(config.appId, "com.tiangong.v3.qiyuan");
  assert.equal(config.nsis.guid, "8a691210-edfd-57d2-ac97-15799e433fcd");
  assert.equal(config.nsis.perMachine, true);
  assert.equal(config.nsis.allowElevation, true);
  assert.equal(config.nsis.menuCategory, "天工造物");
  assert.equal(config.nsis.shortcutName, "天工造物 v3.0.3 完整版");
  assert.equal(config.nsis.uninstallDisplayName, "天工造物 v3.0.3 完整版");
  assert.equal(config.nsis.runAfterFinish, true);
  assert.equal(config.win.executableName, "天工造物 v3.0.3 完整版");
  assert.equal(basename(config.nsis.include), "installer.nsh");
  assert.equal(config.extraMetadata.name, undefined);
  assert.equal(config.extraMetadata.tiangongIsolatedInstallation, undefined);
  assert.equal(config.extraMetadata.tiangongAutomaticUpdates, undefined);
  assert.equal(config.artifactName, "天工造物-v${version}-完整版-${os}-${arch}-unsigned.${ext}");
});

test("isolated installation separates uninstall registration, shortcuts and package policy", () => {
  const normal = loadConfiguration();
  const isolated = loadConfiguration("1");
  assert.equal(isolated.appId, "com.tiangong.v3.qiyuan.isolated");
  assert.equal(isolated.nsis.guid, "6095a57b-5cb5-4a21-ae7e-1e2efc353918");
  assert.match(isolated.nsis.guid, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.notEqual(isolated.appId, normal.appId);
  assert.notEqual(isolated.nsis.guid, normal.nsis.guid);
  for (const field of ["menuCategory", "shortcutName", "uninstallDisplayName"]) {
    assert.notEqual(isolated.nsis[field], normal.nsis[field]);
  }
  assert.equal(isolated.nsis.perMachine, false);
  assert.equal(isolated.nsis.allowElevation, false);
  assert.equal(isolated.nsis.runAfterFinish, false);
  assert.equal(isolated.nsis.deleteAppDataOnUninstall, false);
  assert.equal(isolated.extraMetadata.name, "tiangong-v3-isolated");
  assert.equal(isolated.extraMetadata.name.startsWith("tiangong-v3-qiyuan"), false);
  assert.equal(isolated.extraMetadata.tiangongIsolatedInstallation, true);
  assert.equal(isolated.extraMetadata.tiangongAutomaticUpdates, false);
  assert.equal(isolated.productName, normal.productName);
  assert.equal(isolated.win.executableName, "天工造物 v3.0.3 独立完整版");
  assert.notEqual(isolated.win.executableName, normal.win.executableName);
  assert.equal(basename(isolated.nsis.include), "isolated-installer.nsh");
  const wrapper = readFileSync(isolated.nsis.include, "utf8");
  assert.match(wrapper, /^!define TIANGONG_ISOLATED_INSTALL$/m);
  assert.match(wrapper, /!include "\$\{__FILEDIR__\}\\installer\.nsh"/);
  const installer = readFileSync(normal.nsis.include, "utf8");
  assert.match(installer, /!ifdef TIANGONG_ISOLATED_INSTALL\s+ExecWait [^\r\n]+ -IsolatedInstallation' \$0\s+!else\s+ExecWait [^\r\n]+ -InstallDirectory "\$INSTDIR"' \$0\s+!endif/);
  assert.notEqual(isolated.artifactName, normal.artifactName);
  assert.equal(isolated.artifactName, "天工造物-v${version}-完整版-${os}-${arch}-isolated-unsigned.${ext}");
  assert.deepEqual(loadConfiguration("1"), isolated);
});

test("installation identity changes only for the explicit isolated flag", () => {
  const normal = loadConfiguration();
  for (const mode of ["0", "", "true"]) {
    assert.deepEqual(loadConfiguration(mode), normal);
  }
});
