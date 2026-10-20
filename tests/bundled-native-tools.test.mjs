import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { configureBundledNativeTools } = require("../app/lib/bundled-native-tools.js");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tiangong-native-tools-"));
  t.after(() => {
    assert.equal(path.dirname(root), fs.realpathSync.native(os.tmpdir()));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const resourcesPath = path.join(root, "resources");
  const files = {
    python: path.join(resourcesPath, "python", "python.exe"),
    node: path.join(resourcesPath, "python", "Lib", "site-packages", "playwright", "driver", "node.exe"),
    git: path.join(resourcesPath, "git", "cmd", "git.exe"),
    ffmpeg: path.join(resourcesPath, "python", "Lib", "site-packages", "imageio_ffmpeg", "binaries", "ffmpeg-win-x86_64-v7.1.exe"),
  };
  for (const file of Object.values(files)) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "native executable fixture", "utf8");
  }
  const env = {
    HOME: path.join(root, "isolated-profile", "home"),
    SystemRoot: path.join(root, "Windows"),
    PATH: path.join(root, "host-tools"),
  };
  fs.mkdirSync(env.HOME, { recursive: true });
  fs.mkdirSync(path.join(env.SystemRoot, "System32", "Wbem"), { recursive: true });
  fs.mkdirSync(path.join(env.SystemRoot, "System32", "WindowsPowerShell", "v1.0"), { recursive: true });
  return {
    root, resourcesPath, files, env,
    configure: (options = {}) => configureBundledNativeTools({
      isPackaged: true, isolatedInstallation: true, resourcesPath, env, ...options,
    }),
  };
}

test("source and ordinary installations have no native environment side effects", () => {
  for (const options of [
    { isPackaged: false, isolatedInstallation: true },
    { isPackaged: true, isolatedInstallation: false },
    { isPackaged: true, isolatedInstallation: "true" },
    { isPackaged: true, isolatedInstallation: {} },
  ]) {
    const env = { PATH: "original-path", PYTHONPATH: "original-source", GIT_DIR: "original-repository" };
    const original = { ...env };
    assert.equal(configureBundledNativeTools({ ...options, resourcesPath: "missing-source-runtime", env }), null);
    assert.deepEqual(env, original);
  }
});

test("isolated bundle and Windows directories exclude inherited host and external app paths", (t) => {
  const f = fixture(t);
  const hostPath = f.env.PATH;
  const externalApps = path.join(f.root, "external-apps");
  const alternateHostPath = path.join(f.root, "alternate-host-tools");
  f.env.Path = externalApps;
  f.env.path = alternateHostPath;
  for (const directory of [hostPath, externalApps, alternateHostPath]) {
    fs.mkdirSync(directory, { recursive: true });
    for (const executable of ["python.exe", "node.exe", "git.exe", "ffprobe.exe"]) {
      fs.writeFileSync(path.join(directory, executable), "host tool must not participate", "utf8");
    }
  }
  const result = f.configure();
  const directories = f.env.PATH.split(path.delimiter);
  assert.deepEqual(directories, result.directories);
  assert.deepEqual(directories.slice(0, 4), [
    path.dirname(fs.realpathSync.native(f.files.python)),
    path.dirname(fs.realpathSync.native(f.files.node)),
    path.dirname(fs.realpathSync.native(f.files.git)),
    path.dirname(fs.realpathSync.native(f.files.ffmpeg)),
  ]);
  for (const directory of [hostPath, externalApps, alternateHostPath]) {
    assert.ok(!directories.includes(directory), `inherited host PATH survived: ${directory}`);
    assert.ok(fs.existsSync(path.join(directory, "ffprobe.exe")), "poisoned host ffprobe fixture is absent");
  }
  for (const directory of [
    path.join(f.env.SystemRoot, "System32"),
    path.join(f.env.SystemRoot, "System32", "Wbem"),
    path.resolve(f.env.SystemRoot),
    path.join(f.env.SystemRoot, "System32", "WindowsPowerShell", "v1.0"),
  ]) {
    assert.ok(fs.statSync(directory).isDirectory(), `Windows helper directory is absent: ${directory}`);
    assert.equal(directories.filter((candidate) => candidate.toLowerCase() === directory.toLowerCase()).length, 1);
  }
  assert.equal(new Set(directories.map((directory) => directory.toLowerCase())).size, directories.length);
  assert.equal(Object.keys(f.env).filter((name) => name.toUpperCase() === "PATH").length, 1);
  const resolve = (name) => directories.map((directory) => path.join(directory, name))
    .find((candidate) => fs.existsSync(candidate));
  assert.equal(resolve("python.exe"), result.pythonExecutable);
  assert.equal(resolve("node.exe"), result.nodeExecutable);
  assert.equal(resolve("git.exe"), result.gitExecutable);
  assert.equal(f.env.TIANGONG_PYTHON_EXECUTABLE, fs.realpathSync.native(f.files.python));
  assert.equal(f.env.PLAYWRIGHT_NODEJS_PATH, fs.realpathSync.native(f.files.node));
  assert.equal(f.env.IMAGEIO_FFMPEG_EXE, fs.realpathSync.native(f.files.ffmpeg));
  assert.equal(f.env.TIANGONG_BUNDLED_FFMPEG_EXECUTABLE, fs.realpathSync.native(f.files.ffmpeg));
  assert.equal(f.env.GIT_CONFIG_NOSYSTEM, "1");
  assert.equal(f.env.GIT_CONFIG_GLOBAL, path.join(f.env.HOME, ".gitconfig"));
  assert.equal(f.env.NoDefaultCurrentDirectoryInExePath, "1");
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.directories));
});

test("inherited interpreter, preload, media and Git configuration overrides are replaced", (t) => {
  const f = fixture(t);
  const removed = [
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONEXECUTABLE", "PYTHONPLATLIBDIR",
    "NODE_OPTIONS", "NODE_PATH", "NODE_EXTRA_CA_CERTS", "PLAYWRIGHT_BROWSERS_PATH",
    "FFMPEG_BINARY", "FFPROBE_BINARY", "TIANGONG_FFMPEG_EXE", "TIANGONG_FFPROBE_EXE",
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_EXEC_PATH",
    "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_CONFIG_KEY_19", "GIT_CONFIG_VALUE_19",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS",
  ];
  for (const name of removed) f.env[name] = "foreign-source-or-user-configuration";
  f.env.PythonPath = "case-variant-source-path";
  f.env.PLAYWRIGHT_NODEJS_PATH = "host-node.exe";
  f.env.IMAGEIO_FFMPEG_EXE = "host-ffmpeg.exe";
  f.env.TIANGONG_PYTHON_EXECUTABLE = "host-python.exe";
  f.env.TIANGONG_BUNDLED_FFMPEG_EXECUTABLE = "host-ffmpeg.exe";
  f.env.GIT_CONFIG_GLOBAL = "host-user-gitconfig";
  f.env.GIT_CONFIG_NOSYSTEM = "0";
  f.env.PYTHONUTF8 = "1";
  f.env.EXTERNAL_APP_SETTING = "preserved";
  f.configure();
  for (const name of [...removed, "PythonPath"]) assert.ok(!(name in f.env), name);
  assert.equal(f.env.GIT_CONFIG_GLOBAL, path.join(f.env.HOME, ".gitconfig"));
  assert.equal(f.env.GIT_CONFIG_NOSYSTEM, "1");
  assert.equal(f.env.PYTHONUTF8, "1");
  assert.equal(f.env.EXTERNAL_APP_SETTING, "preserved");
});

for (const tool of ["python", "node", "git", "ffmpeg"]) {
  test(`a missing bundled ${tool} fails before any environment mutation`, (t) => {
    const f = fixture(t);
    fs.unlinkSync(f.files[tool]);
    f.env.PYTHONPATH = "poisoned-source";
    const original = { ...f.env };
    assert.throws(() => f.configure(), /bundled_native_tools_(missing|ffmpeg_requires_one_executable)/);
    assert.deepEqual(f.env, original);
  });
}

test("multiple bundled FFmpeg candidates fail without selecting a host or arbitrary version", (t) => {
  const f = fixture(t);
  fs.writeFileSync(path.join(path.dirname(f.files.ffmpeg), "ffmpeg-win-x86_64-another.exe"), "second fixture");
  const original = { ...f.env };
  assert.throws(() => f.configure(), /ffmpeg_requires_one_executable:found=2/);
  assert.deepEqual(f.env, original);
});

test("a source runtime linked into packaged resources is refused", (t) => {
  const f = fixture(t);
  const sourcePython = path.join(f.root, "source-checkout", "app", "runtime", "python312");
  fs.mkdirSync(path.dirname(sourcePython), { recursive: true });
  fs.renameSync(path.join(f.resourcesPath, "python"), sourcePython);
  fs.symlinkSync(sourcePython, path.join(f.resourcesPath, "python"), process.platform === "win32" ? "junction" : "dir");
  const original = { ...f.env };
  assert.throws(() => f.configure(), /bundled_native_tools_link_refused:python/);
  assert.deepEqual(f.env, original);
});

test("a source directory cannot substitute for the packaged resources layout", (t) => {
  const f = fixture(t);
  const source = path.join(f.root, "source-checkout", "app");
  fs.mkdirSync(path.join(source, "runtime", "python312"), { recursive: true });
  fs.writeFileSync(path.join(source, "runtime", "python312", "python.exe"), "source python");
  const original = { ...f.env };
  assert.throws(() => f.configure({ resourcesPath: source }), /bundled_native_tools_missing:python/);
  assert.deepEqual(f.env, original);
});

test("a linked resources root and nonabsolute roots are rejected before mutation", (t) => {
  const f = fixture(t);
  const linked = path.join(f.root, "linked-resources");
  fs.symlinkSync(f.resourcesPath, linked, process.platform === "win32" ? "junction" : "dir");
  const original = { ...f.env };
  assert.throws(() => f.configure({ resourcesPath: linked }), /resources_must_be_unlinked_directory/);
  assert.throws(() => f.configure({ resourcesPath: "relative-resources" }), /resources_must_be_absolute/);
  assert.throws(() => f.configure({ resourcesPath: path.join(f.root, "missing-resources") }), /bundled_native_tools_missing:resources/);
  assert.deepEqual(f.env, original);
});

test("Git never falls back to the real user profile when the isolated HOME binding is invalid", (t) => {
  const f = fixture(t);
  f.env.HOME = "relative-or-missing-profile";
  const original = { ...f.env };
  assert.throws(() => f.configure(), /profile_home_must_be_absolute/);
  assert.deepEqual(f.env, original);
});
