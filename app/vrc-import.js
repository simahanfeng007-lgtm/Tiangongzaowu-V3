"use strict";

// A deliberately narrow intake boundary.  This product renders VRM in Three.js;
// VRC/VRChat source assets must be converted by a user-owned Unity project, never
// by reading VRChat caches or downloading remote avatar bundles.
const fs = require("fs");
const path = require("path");

const CONTRACT = "tiangong.vrc-avatar-import.v1";
const MAX_SOURCE_BYTES = 4 * 1024 * 1024 * 1024;

function extensionOf(target) {
  return path.extname(String(target || "")).toLowerCase();
}

function existingDirectory(target) {
  return ["Assets", "Packages", "ProjectSettings"].every((name) => {
    try { return fs.statSync(path.join(target, name)).isDirectory(); }
    catch (_error) { return false; }
  });
}

function unityCandidates(env = process.env) {
  const candidates = [];
  const configured = String(env.TIANGONG_UNITY_PATH || "").trim();
  if (configured) candidates.push(configured);
  const installationRoots = [
    env.ProgramFiles,
    env["ProgramFiles(x86)"],
    env.LOCALAPPDATA && path.join(env.LOCALAPPDATA, "Programs"),
  ].map((value) => String(value || "").trim()).filter(Boolean);
  for (const installationRoot of new Set(installationRoots)) {
    const hubRoot = path.join(installationRoot, "Unity", "Hub", "Editor");
    try {
      for (const entry of fs.readdirSync(hubRoot, { withFileTypes: true })) {
        if (entry.isDirectory()) candidates.push(path.join(hubRoot, entry.name, "Editor", "Unity.exe"));
      }
    } catch (_error) { /* Unity Hub is optional. */ }
  }
  return candidates;
}

function findUnityEditor(env = process.env) {
  for (const candidate of unityCandidates(env)) {
    try {
      if (fs.statSync(candidate).isFile()) return path.resolve(candidate);
    } catch (_error) { /* Keep checking candidates. */ }
  }
  return "";
}

function sourceKind(target, stat) {
  if (stat.isDirectory()) return existingDirectory(target) ? "unity_project" : "unsupported_directory";
  if (!stat.isFile()) return "unsupported";
  if (extensionOf(target) === ".unitypackage") return "unitypackage";
  return "unsupported_file";
}

function preflightVrcAvatarSource(target, options = {}) {
  const requested = String(target || "").trim();
  if (!requested) return { ok: false, contract: CONTRACT, error: "source_required" };
  const sourcePath = path.resolve(requested);
  let stat;
  try { stat = fs.lstatSync(sourcePath); }
  catch (_error) { return { ok: false, contract: CONTRACT, error: "source_not_found", source_path: sourcePath }; }
  if (stat.isSymbolicLink()) return { ok: false, contract: CONTRACT, error: "source_symlink_rejected", source_path: sourcePath };
  if (stat.isFile() && stat.size > MAX_SOURCE_BYTES) return { ok: false, contract: CONTRACT, error: "source_too_large", source_path: sourcePath };

  const kind = sourceKind(sourcePath, stat);
  if (kind === "unsupported_directory" || kind === "unsupported_file" || kind === "unsupported") {
    return {
      ok: false,
      contract: CONTRACT,
      error: "unsupported_source",
      source_path: sourcePath,
      accepted: ["Unity project directory (Assets, Packages, ProjectSettings)", ".unitypackage"],
    };
  }
  const unityPath = findUnityEditor(options.env || process.env);
  return {
    ok: true,
    contract: CONTRACT,
    source: { path: sourcePath, kind, bytes: stat.isFile() ? stat.size : null },
    authorization_required: true,
    cache_or_downloaded_avatar_import_supported: false,
    unity: { detected: Boolean(unityPath), path: unityPath, minimum: "Unity 2022.3 LTS + UniVRM" },
    output: { format: ".vrm", loader: "existing Three.js VRM loader" },
    preserved: ["humanoid rig", "mesh", "textures", "supported blendshapes", "supported animations"],
    degraded_or_excluded: ["VRChat menus", "SDK behaviours", "PhysBone/Dynamic Bone", "custom shaders", "particles"],
    next_action: unityPath ? "ready_for_unity_bridge_setup" : "unity_editor_required",
  };
}

module.exports = { CONTRACT, findUnityEditor, preflightVrcAvatarSource, unityCandidates };
