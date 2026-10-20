"use strict";

const fs = require("fs");
const path = require("path");

function within(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (
    relative !== ".." && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
  );
}

function environmentValue(env, name) {
  if (Object.prototype.hasOwnProperty.call(env, name)) return String(env[name] || "");
  const key = Object.keys(env).find((candidate) => candidate.toUpperCase() === name);
  return key ? String(env[key] || "") : "";
}

function trustedPath(root, relative, label, file = true) {
  const candidate = path.resolve(root, relative);
  if (!within(root, candidate) || candidate === root) {
    throw new Error(`bundled_native_tools_path_escaped_resources:${label}:${candidate}`);
  }
  let current = root;
  const segments = path.relative(root, candidate).split(path.sep);
  for (const [index, segment] of segments.entries()) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = fs.lstatSync(current);
    } catch (error) {
      if (error.code === "ENOENT") {
        throw new Error(`bundled_native_tools_missing:${label}:${current}`);
      }
      throw error;
    }
    if (stat.isSymbolicLink()) {
      throw new Error(`bundled_native_tools_link_refused:${label}:${current}`);
    }
    const last = index === segments.length - 1;
    const expectedType = last && file ? stat.isFile() : stat.isDirectory();
    if (!expectedType) {
      throw new Error(`bundled_native_tools_invalid_path_type:${label}:${current}`);
    }
    if (!within(root, fs.realpathSync.native(current))) {
      throw new Error(`bundled_native_tools_path_escaped_resources:${label}:${current}`);
    }
  }
  fs.accessSync(candidate, fs.constants.R_OK);
  return fs.realpathSync.native(candidate);
}

const INHERITED_OVERRIDES = new Set([
  "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE",
  "PYTHONEXECUTABLE", "PYTHONPLATLIBDIR",
  "NODE_OPTIONS", "NODE_PATH", "NODE_EXTRA_CA_CERTS",
  "PLAYWRIGHT_NODEJS_PATH", "PLAYWRIGHT_BROWSERS_PATH",
  "IMAGEIO_FFMPEG_EXE", "FFMPEG_BINARY", "FFPROBE_BINARY",
  "TIANGONG_FFMPEG_EXE", "TIANGONG_FFPROBE_EXE",
  "TIANGONG_PYTHON_EXECUTABLE", "TIANGONG_BUNDLED_FFMPEG_EXECUTABLE",
  "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
  "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_EXEC_PATH", "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
  "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
  "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS",
]);

function configureBundledNativeTools({
  isPackaged, isolatedInstallation, resourcesPath, env = process.env,
}) {
  if (isPackaged !== true || isolatedInstallation !== true) return null;

  const rawRoot = String(resourcesPath || "").trim();
  if (!rawRoot || rawRoot.includes("\0") || !path.isAbsolute(rawRoot)) {
    throw new Error("bundled_native_tools_resources_must_be_absolute");
  }
  const requestedRoot = path.resolve(rawRoot);
  if (requestedRoot === path.parse(requestedRoot).root) {
    throw new Error("bundled_native_tools_resources_may_not_be_volume_root");
  }
  let rootStat;
  try {
    rootStat = fs.lstatSync(requestedRoot);
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error(`bundled_native_tools_missing:resources:${requestedRoot}`);
    }
    throw error;
  }
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) {
    throw new Error("bundled_native_tools_resources_must_be_unlinked_directory");
  }
  const root = fs.realpathSync.native(requestedRoot);
  const pythonExecutable = trustedPath(root, path.join("python", "python.exe"), "python");
  const nodeExecutable = trustedPath(root, path.join("python", "Lib", "site-packages", "playwright", "driver", "node.exe"), "node");
  const gitExecutable = trustedPath(root, path.join("git", "cmd", "git.exe"), "git");
  const ffmpegDirectory = trustedPath(root, path.join("python", "Lib", "site-packages", "imageio_ffmpeg", "binaries"), "ffmpeg", false);
  const ffmpegCandidates = fs.readdirSync(ffmpegDirectory)
    .filter((name) => /^ffmpeg-win-x86_64-.+\.exe$/i.test(name));
  if (ffmpegCandidates.length !== 1) {
    throw new Error(`bundled_native_tools_ffmpeg_requires_one_executable:found=${ffmpegCandidates.length}`);
  }
  const ffmpegExecutable = trustedPath(root, path.relative(root, path.join(ffmpegDirectory, ffmpegCandidates[0])), "ffmpeg");

  // The profile helper must run first. Do not let Git fall back to the real
  // user's global credential/configuration file when that binding is absent.
  const home = environmentValue(env, "HOME").trim();
  if (!home || !path.isAbsolute(home) || home.includes("\0")) {
    throw new Error("bundled_native_tools_profile_home_must_be_absolute");
  }
  const systemRoot = (environmentValue(env, "SYSTEMROOT") || environmentValue(env, "WINDIR")
    || environmentValue(process.env, "SYSTEMROOT") || environmentValue(process.env, "WINDIR")).trim();
  if (!systemRoot || !path.isAbsolute(systemRoot) || systemRoot.includes("\0")) {
    throw new Error("bundled_native_tools_windows_system_root_must_be_absolute");
  }
  const windowsRoot = path.resolve(systemRoot);
  const powershellDirectory = path.join(windowsRoot, "System32", "WindowsPowerShell", "v1.0");
  const directories = [
    path.dirname(pythonExecutable), path.dirname(nodeExecutable), path.dirname(gitExecutable),
    ffmpegDirectory, path.join(windowsRoot, "System32"), path.join(windowsRoot, "System32", "Wbem"),
    windowsRoot, powershellDirectory,
  ];
  const seen = new Set();
  const searchPath = directories.filter((directory) => {
    const key = process.platform === "win32" ? directory.toLowerCase() : directory;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).join(path.delimiter);

  // Every required bundle path has been verified before the first mutation.
  // Collapse PATH case variants too: Windows child-process environments must
  // not accidentally choose a second, inherited Path entry over this one.
  for (const name of Object.keys(env)) {
    const upper = name.toUpperCase();
    if (upper === "PATH" || INHERITED_OVERRIDES.has(upper)
      || /^GIT_CONFIG_(?:KEY|VALUE)_\d+$/.test(upper)) delete env[name];
  }
  Object.assign(env, {
    PATH: searchPath,
    TIANGONG_PYTHON_EXECUTABLE: pythonExecutable,
    PLAYWRIGHT_NODEJS_PATH: nodeExecutable,
    IMAGEIO_FFMPEG_EXE: ffmpegExecutable,
    TIANGONG_BUNDLED_FFMPEG_EXECUTABLE: ffmpegExecutable,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_GLOBAL: path.join(path.resolve(home), ".gitconfig"),
    // Python 3.12 shutil.which consults this Windows switch before placing
    // the current workspace in front of the verified PATH directories.
    NoDefaultCurrentDirectoryInExePath: "1",
  });
  return Object.freeze({
    resourcesRoot: root, pythonExecutable, nodeExecutable, gitExecutable,
    ffmpegExecutable, powershellDirectory, directories: Object.freeze(directories),
  });
}

module.exports = { configureBundledNativeTools };
