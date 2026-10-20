import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { configureIsolatedInstallation } = require("../app/lib/isolated-installation.js");

function fixture(t, { isPackaged = true } = {}) {
  // Windows TEMP may use an 8.3 alias; use the same canonical parent as the cleanup guard.
  const root = fs.mkdtempSync(path.join(fs.realpathSync.native(os.tmpdir()), "tiangong-isolated-profile-"));
  const appData = path.join(root, "host-appdata");
  const calls = [];
  const paths = { appData };
  const app = {
    isPackaged,
    getPath(name) {
      calls.push(["getPath", name]);
      return paths[name];
    },
    setPath(name, directory) {
      calls.push(["setPath", name, directory]);
      paths[name] = directory;
    },
    setName(name) {
      calls.push(["setName", name]);
    },
  };
  t.after(() => {
    assert.equal(path.dirname(root), fs.realpathSync.native(os.tmpdir()));
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { root, appData, app, calls, paths };
}

function within(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

test("default and source installations have no isolation side effects", (t) => {
  const entries = [
    { isPackaged: true, metadata: {} },
    { isPackaged: true, metadata: { tiangongIsolatedInstallation: false } },
    { isPackaged: true, metadata: { tiangongIsolatedInstallation: "true" } },
    { isPackaged: false, metadata: { tiangongIsolatedInstallation: true } },
  ];
  for (const entry of entries) {
    const value = fixture(t, entry);
    const env = { TIANGONG_DESKTOP_RUNTIME_ROOT: "old-value", PORTABLE_EXECUTABLE_DIR: "portable" };
    const original = { ...env };
    assert.equal(configureIsolatedInstallation({ app: value.app, env, metadata: entry.metadata }), null);
    assert.deepEqual(env, original);
    assert.deepEqual(value.calls, []);
    assert.deepEqual(fs.readdirSync(value.root), []);
  }
});

test("the isolated package creates its own complete profile and replaces inherited roots", (t) => {
  const value = fixture(t);
  const oldRoot = path.join(value.root, "existing-profile");
  fs.mkdirSync(oldRoot);
  const marker = path.join(oldRoot, "user-material.txt");
  fs.writeFileSync(marker, "preserve existing data", "utf8");
  const env = {
    APPDATA: oldRoot,
    HOME: oldRoot,
    TIANGONG_DESKTOP_RUNTIME_ROOT: oldRoot,
    TIANGONG_DESKTOP_STATE_DIR: oldRoot,
    TIANGONG_DESKTOP_WORKSPACE_ROOT: oldRoot,
    TIANGONG_HOME_PATH: oldRoot,
    TIANGONG_DOCUMENTS_PATH: oldRoot,
    TIANGONG_LIFE_DATA_ROOT: oldRoot,
    TIANGONG_EXECUTION_RUNTIME_ROOT: oldRoot,
    TIANGONG_EXECUTION_LIFE_ROOT: oldRoot,
    TIANGONG_BACKEND_PYTHON: path.join(oldRoot, "python.exe"),
    TIANGONG_PLAYWRIGHT_BROWSERS_PATH: oldRoot,
    TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT: oldRoot,
    TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH: path.join(oldRoot, "release-manifest.json"),
    TIANGONG_GATEWAY_RELEASE_MANIFEST_CANDIDATES: "old-manifest-list",
    PORTABLE_EXECUTABLE_DIR: oldRoot,
    TIANGONG_PORTABLE_MODE: "1",
    TIANGONG_WORKSPACE_MODE: "full",
    TIANGONG_DESKTOP_TOKEN: "caller-local-token",
  };
  const profile = configureIsolatedInstallation({
    app: value.app, env, metadata: { tiangongIsolatedInstallation: true },
  });
  assert.equal(profile.profileRoot, path.join(value.appData, "tiangong-v3-isolated"));
  assert.equal(profile.userData, path.join(profile.profileRoot, "electron-user-data"));
  assert.equal(profile.workspaceRoot, path.join(profile.profileRoot, "workspace"));
  assert.equal(profile.lifeDataRoot, path.join(profile.profileRoot, "life-data"));
  assert.equal(value.paths.userData, profile.userData);
  assert.equal(value.paths.sessionData, profile.userData);
  assert.equal(value.paths.documents, env.TIANGONG_DOCUMENTS_PATH);
  for (const [name, directory] of Object.entries(env)) {
    if (name === "TIANGONG_DESKTOP_TOKEN") continue;
    assert.ok(within(profile.profileRoot, directory), `${name}: ${directory}`);
    assert.ok(fs.statSync(directory).isDirectory(), name);
    assert.equal(fs.realpathSync.native(directory), directory, name);
  }
  assert.equal(env.TIANGONG_DESKTOP_TOKEN, "caller-local-token");
  assert.equal(fs.readFileSync(marker, "utf8"), "preserve existing data");
  assert.deepEqual(fs.readdirSync(oldRoot), ["user-material.txt"]);
  assert.ok(!("PORTABLE_EXECUTABLE_DIR" in env));
  assert.ok(!("TIANGONG_PORTABLE_MODE" in env));
  assert.ok(!("TIANGONG_WORKSPACE_MODE" in env));
  for (const name of [
    "TIANGONG_BACKEND_PYTHON", "TIANGONG_PLAYWRIGHT_BROWSERS_PATH",
    "TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT", "TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH",
    "TIANGONG_GATEWAY_RELEASE_MANIFEST_CANDIDATES",
  ]) assert.ok(!(name in env), name);
});

test("the dedicated QA override is absolute and repeated startup preserves profile content", (t) => {
  const value = fixture(t);
  const profileRoot = path.join(value.root, "测试 Profile");
  const env = { TIANGONG_ISOLATED_PROFILE_ROOT: profileRoot };
  const metadata = { tiangongIsolatedInstallation: true };
  const first = configureIsolatedInstallation({ app: value.app, env, metadata });
  assert.equal(first.profileRoot, profileRoot);
  const existing = path.join(first.workspaceRoot, "existing-work.txt");
  fs.writeFileSync(existing, "latest user work", "utf8");
  const second = configureIsolatedInstallation({ app: value.app, env, metadata });
  assert.deepEqual(second, first);
  assert.equal(fs.readFileSync(existing, "utf8"), "latest user work");
  assert.equal(value.calls.filter(([name]) => name === "getPath").length, 0);
});

test("relative, volume, file and linked profile roots fail before application or environment mutation", (t) => {
  const value = fixture(t);
  const file = path.join(value.root, "profile-file");
  fs.writeFileSync(file, "not a profile");
  const target = path.join(value.root, "junction-target");
  fs.mkdirSync(target);
  const link = path.join(value.root, "profile-link");
  fs.symlinkSync(target, link, process.platform === "win32" ? "junction" : "dir");
  for (const candidate of ["relative-profile", path.parse(value.root).root, file, link]) {
    const env = { TIANGONG_ISOLATED_PROFILE_ROOT: candidate, TIANGONG_HOME_PATH: "old-home" };
    const original = { ...env };
    assert.throws(() => configureIsolatedInstallation({
      app: value.app, env, metadata: { tiangongIsolatedInstallation: true },
    }), /isolated_profile_root/);
    assert.deepEqual(env, original);
    assert.deepEqual(value.calls, []);
  }
  assert.equal(fs.readFileSync(file, "utf8"), "not a profile");
  assert.deepEqual(fs.readdirSync(target), []);
});

test("a junction inside a profile cannot route runtime creation into existing user data", (t) => {
  const value = fixture(t);
  const profileRoot = path.join(value.root, "isolated-profile");
  const outside = path.join(value.root, "old-runtime");
  fs.mkdirSync(profileRoot);
  fs.mkdirSync(outside);
  const marker = path.join(outside, "keep.txt");
  fs.writeFileSync(marker, "keep");
  fs.symlinkSync(outside, path.join(profileRoot, "runtime"), process.platform === "win32" ? "junction" : "dir");
  const env = { TIANGONG_ISOLATED_PROFILE_ROOT: profileRoot, TIANGONG_DESKTOP_STATE_DIR: outside };
  const original = { ...env };
  assert.throws(() => configureIsolatedInstallation({
    app: value.app, env, metadata: { tiangongIsolatedInstallation: true },
  }), /isolated_profile_path_contains_file_or_link/);
  assert.deepEqual(env, original);
  assert.deepEqual(value.calls, []);
  assert.deepEqual(fs.readdirSync(outside), ["keep.txt"]);
  assert.equal(fs.readFileSync(marker, "utf8"), "keep");
  assert.deepEqual(fs.readdirSync(profileRoot), ["runtime"]);
});
