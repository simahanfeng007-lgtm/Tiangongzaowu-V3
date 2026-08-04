import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptsRoot = dirname(fileURLToPath(import.meta.url));
const workspaceRoot = resolve(scriptsRoot, "..");
const requireFromApp = createRequire(join(workspaceRoot, "app", "package.json"));

export const REQUIRED_AVATAR_MODULE_FILES = Object.freeze([
  "avatar-storage-host.cjs",
  "frontend-v2/index.html",
  "frontend-v2/renderer/bootstrap.mjs",
  "frontend-v2/renderer/avatar/avatar-boot.mjs",
  "frontend-v2/renderer/avatar/engines/three-vrm-engine.mjs",
  "node_modules/three/build/three.module.js",
  "node_modules/three/examples/jsm/loaders/GLTFLoader.js",
  "node_modules/three/examples/jsm/utils/BufferGeometryUtils.js",
  "node_modules/three/examples/jsm/controls/OrbitControls.js",
  "node_modules/@pixiv/three-vrm/lib/three-vrm.module.js",
  "node_modules/@pixiv/three-vrm-animation/lib/three-vrm-animation.module.js",
]);

export const FORBIDDEN_BUNDLED_AVATAR_ASSETS = Object.freeze([
  "assets/avatars/imported/天工造物z1.vrm",
  "assets/avatars/imported/造物v2.vrm",
]);

function normalizedArchiveEntries(asar, asarPath) {
  return new Set(
    asar.listPackage(asarPath).map((entry) =>
      String(entry).replaceAll("\\", "/").replace(/^\/+/, "")
    ),
  );
}

export function verifyAppAsarAvatarContract(asarPath, { asar = null } = {}) {
  const asarApi = asar ?? requireFromApp("@electron/asar");
  const entries = normalizedArchiveEntries(asarApi, asarPath);
  const missing = REQUIRED_AVATAR_MODULE_FILES.filter((entry) => !entries.has(entry));
  if (missing.length > 0) {
    throw new Error(
      `packaged app.asar is missing required avatar module closure: ${missing.join(", ")}`,
    );
  }

  const empty = [];
  let requiredBytes = 0;
  for (const entry of REQUIRED_AVATAR_MODULE_FILES) {
    const bytes = asarApi.extractFile(asarPath, join(...entry.split("/")));
    if (bytes.length === 0) empty.push(entry);
    requiredBytes += bytes.length;
  }
  if (empty.length > 0) {
    throw new Error(
      `packaged app.asar contains empty required avatar modules: ${empty.join(", ")}`,
    );
  }

  const forbidden = FORBIDDEN_BUNDLED_AVATAR_ASSETS.filter((entry) => entries.has(entry));
  if (forbidden.length > 0) {
    throw new Error(
      `packaged app.asar contains non-redistributable avatar assets: ${forbidden.join(", ")}`,
    );
  }

  return Object.freeze({
    requiredModuleCount: REQUIRED_AVATAR_MODULE_FILES.length,
    requiredModuleBytes: requiredBytes,
    forbiddenAssetCount: FORBIDDEN_BUNDLED_AVATAR_ASSETS.length,
  });
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  try {
    const result = verifyAppAsarAvatarContract(resolve(process.argv[2] || ""));
    process.stdout.write(`${JSON.stringify({ ok: true, ...result })}\n`);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
