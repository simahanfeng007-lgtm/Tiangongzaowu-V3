"use strict";

const fs = require("fs");
const path = require("path");

const PROFILE_DIRECTORY_NAME = "tiangong-v3-isolated";
const PRODUCT_LABEL = "天工造物 v3.0.3 独立环境版";

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (
    relative !== ".."
    && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
  );
}

function directoryStat(candidate) {
  try {
    return fs.lstatSync(candidate);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function validateRoot(rawRoot) {
  const raw = String(rawRoot || "").trim();
  if (!raw || raw.includes("\0") || !path.isAbsolute(raw)) {
    throw new Error("isolated_profile_root_must_be_absolute");
  }
  const resolved = path.resolve(raw);
  if (resolved === path.parse(resolved).root) {
    throw new Error("isolated_profile_root_may_not_be_volume_root");
  }
  const stat = directoryStat(resolved);
  if (stat && (!stat.isDirectory() || stat.isSymbolicLink())) {
    throw new Error("isolated_profile_root_must_be_a_directory_without_links");
  }
  fs.mkdirSync(resolved, { recursive: true });
  const canonical = fs.realpathSync.native(resolved);
  if (canonical === path.parse(canonical).root) {
    throw new Error("isolated_profile_root_may_not_be_volume_root");
  }
  fs.accessSync(canonical, fs.constants.R_OK | fs.constants.W_OK);
  return canonical;
}

function validateChild(root, candidate) {
  if (!isWithin(root, candidate) || candidate === root) {
    throw new Error("isolated_profile_path_escaped_root");
  }
  let current = root;
  for (const segment of path.relative(root, candidate).split(path.sep)) {
    current = path.join(current, segment);
    const stat = directoryStat(current);
    if (!stat) break;
    if (!stat.isDirectory() || stat.isSymbolicLink()) {
      throw new Error(`isolated_profile_path_contains_file_or_link:${current}`);
    }
    if (!isWithin(root, fs.realpathSync.native(current))) {
      throw new Error(`isolated_profile_path_escaped_root:${current}`);
    }
  }
}

function configureIsolatedInstallation({ app, env = process.env, metadata = {} }) {
  if (!app.isPackaged || metadata.tiangongIsolatedInstallation !== true) return null;

  const requestedRoot = String(env.TIANGONG_ISOLATED_PROFILE_ROOT || "").trim();
  const profileRoot = validateRoot(
    requestedRoot || path.join(app.getPath("appData"), PROFILE_DIRECTORY_NAME),
  );
  const userData = path.join(profileRoot, "electron-user-data");
  const homeRoot = path.join(profileRoot, "home");
  const roamingRoot = path.join(profileRoot, "AppData", "Roaming");
  const localRoot = path.join(profileRoot, "AppData", "Local");
  const tempRoot = path.join(profileRoot, "Temp");
  const crashRoot = path.join(profileRoot, "crash-dumps");
  const runtimeRoot = path.join(profileRoot, "runtime");
  const stateRoot = path.join(runtimeRoot, "state");
  const runRoot = path.join(runtimeRoot, "run");
  const workspaceRoot = path.join(profileRoot, "workspace");
  const lifeDataRoot = path.join(profileRoot, "life-data");
  const lifeRuntimeRoot = path.join(runtimeRoot, "complete-life");
  const lifeKernelRoot = path.join(stateRoot, "life_kernel");
  const lifeTransactionRoot = path.join(stateRoot, "life_transaction");
  const knownFolders = {
    documents: path.join(homeRoot, "Documents"),
    desktop: path.join(homeRoot, "Desktop"),
    downloads: path.join(homeRoot, "Downloads"),
    pictures: path.join(homeRoot, "Pictures"),
    videos: path.join(homeRoot, "Videos"),
    music: path.join(homeRoot, "Music"),
  };
  const bindings = {
    APPDATA: roamingRoot,
    LOCALAPPDATA: localRoot,
    HOME: homeRoot,
    USERPROFILE: homeRoot,
    TEMP: tempRoot,
    TMP: tempRoot,
    TIANGONG_ISOLATED_PROFILE_ROOT: profileRoot,
    TIANGONG_DESKTOP_RUNTIME_ROOT: runtimeRoot,
    TIANGONG_DESKTOP_STATE_DIR: stateRoot,
    TIANGONG_RUN_STATE_DIR: runRoot,
    TIANGONG_V3_STATE_DIR: path.join(stateRoot, "v3"),
    TIANGONG_DESKTOP_WORKSPACE_ROOT: workspaceRoot,
    TIANGONG_WORKSPACE_ROOT: workspaceRoot,
    TIANGONG_FORCE_WORKSPACE_ROOT: workspaceRoot,
    TIANGONG_OMNI_BODY_WORKSPACE: workspaceRoot,
    TIANGONG_HOME_PATH: homeRoot,
    TIANGONG_DOCUMENTS_PATH: knownFolders.documents,
    TIANGONG_DESKTOP_PATH: knownFolders.desktop,
    TIANGONG_DOWNLOADS_PATH: knownFolders.downloads,
    TIANGONG_PICTURES_PATH: knownFolders.pictures,
    TIANGONG_VIDEOS_PATH: knownFolders.videos,
    TIANGONG_MUSIC_PATH: knownFolders.music,
    TIANGONG_LIFE_DATA_ROOT: lifeDataRoot,
    TIANGONG_LIFE_RUNTIME_ROOT: lifeRuntimeRoot,
    TIANGONG_LIFE_KERNEL_ROOT: lifeKernelRoot,
    TIANGONG_LIFE_ROOT: lifeTransactionRoot,
    TIANGONG_EXECUTION_RUNTIME_ROOT: lifeKernelRoot,
    TIANGONG_EXECUTION_LIFE_ROOT: lifeTransactionRoot,
  };
  const directories = [...new Set([
    userData, crashRoot, ...Object.values(bindings),
  ])].filter((candidate) => candidate !== profileRoot);

  // Validate the complete existing layout before creating children. In
  // particular, a stale junction must never route a new directory outside it.
  for (const directory of directories) validateChild(profileRoot, directory);
  for (const directory of directories) {
    fs.mkdirSync(directory, { recursive: true });
    validateChild(profileRoot, directory);
    fs.accessSync(directory, fs.constants.R_OK | fs.constants.W_OK);
  }

  app.setName(PRODUCT_LABEL);
  for (const [name, directory] of Object.entries({
    appData: roamingRoot,
    userData,
    sessionData: userData,
    home: homeRoot,
    temp: tempRoot,
    crashDumps: crashRoot,
    ...knownFolders,
  })) app.setPath(name, directory);
  // Runtime locators inherited from a source launcher must not select an old
  // checkout, interpreter, browser or dictionary in the installed package.
  for (const name of Object.keys(env)) {
    if (name.startsWith("TIANGONG_")
      && /_(?:ROOT|DIR|PATH|WORKSPACE|EXE|PYTHON)$/.test(name)
      && !Object.prototype.hasOwnProperty.call(bindings, name)) {
      delete env[name];
    }
  }
  delete env.TIANGONG_GATEWAY_RELEASE_MANIFEST_CANDIDATES;
  Object.assign(env, bindings);
  // The later portable bootstrap must not move this installation back to an
  // inherited portable profile. Workspace mode also belongs to this profile.
  delete env.PORTABLE_EXECUTABLE_DIR;
  delete env.TIANGONG_PORTABLE_MODE;
  delete env.TIANGONG_WORKSPACE_MODE;
  return Object.freeze({
    profileRoot, userData, homeRoot, roamingRoot, localRoot, tempRoot,
    runtimeRoot, stateRoot, runRoot, workspaceRoot, lifeDataRoot,
    lifeRuntimeRoot, lifeKernelRoot, lifeTransactionRoot,
    knownFolders: Object.freeze(knownFolders),
  });
}

module.exports = { configureIsolatedInstallation };
