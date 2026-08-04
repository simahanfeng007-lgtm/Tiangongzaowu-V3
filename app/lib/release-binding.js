"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const MAX_MANIFEST_BYTES = 4 * 1024 * 1024;
const NATIVE_COMPONENT_IDS = Object.freeze([
  "tiangong-backend",
  "tiangong-communication-service",
  "tiangong-life-service",
  "tiangong-total-gateway",
]);
const SEMVER = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;

function compareText(left, right) {
  const a = String(left || "");
  const b = String(right || "");
  return a < b ? -1 : a > b ? 1 : 0;
}

function compareNumericText(left, right) {
  if (left.length !== right.length) return left.length < right.length ? -1 : 1;
  return compareText(left, right);
}

function parseSemver(value) {
  const raw = String(value || "");
  const match = SEMVER.exec(raw);
  if (!match) return null;
  const prerelease = match[4] ? match[4].split(".") : [];
  if (prerelease.some((item) => /^\d+$/.test(item) && item.length > 1 && item.startsWith("0"))) {
    return null;
  }
  return { raw, core: [match[1], match[2], match[3]], prerelease };
}

function compareSemver(left, right) {
  const a = parseSemver(left);
  const b = parseSemver(right);
  if (!a || !b) {
    if (Boolean(a) !== Boolean(b)) return a ? 1 : -1;
    const folded = compareText(String(left || "").toLowerCase(), String(right || "").toLowerCase());
    return folded || compareText(left, right);
  }
  for (let index = 0; index < 3; index += 1) {
    const compared = compareNumericText(a.core[index], b.core[index]);
    if (compared) return compared;
  }
  if (!a.prerelease.length || !b.prerelease.length) {
    if (a.prerelease.length === b.prerelease.length) return 0;
    return a.prerelease.length ? -1 : 1;
  }
  const count = Math.max(a.prerelease.length, b.prerelease.length);
  for (let index = 0; index < count; index += 1) {
    if (index >= a.prerelease.length) return -1;
    if (index >= b.prerelease.length) return 1;
    const aPart = a.prerelease[index];
    const bPart = b.prerelease[index];
    const aNumeric = /^\d+$/.test(aPart);
    const bNumeric = /^\d+$/.test(bPart);
    if (aNumeric !== bNumeric) return aNumeric ? -1 : 1;
    const compared = aNumeric
      ? compareNumericText(aPart, bPart)
      : compareText(aPart, bPart);
    if (compared) return compared;
  }
  return 0;
}

function canonicalValue(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error("release manifest contains a non-integer or unsafe number");
    return value;
  }
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (!value || typeof value !== "object") throw new Error("release manifest contains an unsupported value");
  const result = {};
  for (const key of Object.keys(value).sort()) result[key] = canonicalValue(value[key]);
  return result;
}

function canonicalJson(value) {
  return JSON.stringify(canonicalValue(value));
}

function sha256Bytes(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function sha256File(filePath) {
  return sha256Bytes(fs.readFileSync(filePath));
}

function pathWithin(rootPath, candidatePath) {
  const relative = path.relative(rootPath, candidatePath);
  return relative === "" || (
    relative !== ".."
    && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
  );
}

function verifiedFile(resourcesRoot, descriptor) {
  const relative = String(descriptor?.executable_relative_path || "");
  if (
    !relative
    || relative.includes("\\")
    || path.posix.isAbsolute(relative)
    || relative.split("/").some((part) => !part || part === "." || part === "..")
  ) return null;
  const expectedHash = String(descriptor?.sha256 || "");
  const expectedBytes = Number(descriptor?.size_bytes);
  if (!/^[0-9a-f]{64}$/.test(expectedHash) || !Number.isSafeInteger(expectedBytes) || expectedBytes < 1) {
    return null;
  }
  const rootReal = fs.realpathSync.native(resourcesRoot);
  const candidate = path.resolve(resourcesRoot, ...relative.split("/"));
  if (!pathWithin(path.resolve(resourcesRoot), candidate)) return null;
  const stat = fs.lstatSync(candidate);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== expectedBytes) return null;
  const candidateReal = fs.realpathSync.native(candidate);
  if (!pathWithin(rootReal, candidateReal) || sha256File(candidateReal) !== expectedHash) return null;
  return candidateReal;
}

function readVerifiedReleaseBinding(manifestPath) {
  try {
    const resolved = path.resolve(String(manifestPath || ""));
    const stat = fs.lstatSync(resolved);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 2 || stat.size > MAX_MANIFEST_BYTES) return null;
    const raw = fs.readFileSync(resolved, "utf8");
    const manifest = JSON.parse(raw);
    if (raw !== `${canonicalJson(manifest)}\n`) return null;
    if (
      manifest?.release_schema !== "tiangong.release-manifest.v1"
      || manifest?.release_channel !== "stable"
      || manifest?.production_claim !== true
      || !parseSemver(manifest?.product_version)
      || !Number.isSafeInteger(manifest?.generated_at_ms)
      || manifest.generated_at_ms < 0
      || !/^[0-9a-f]{64}$/.test(String(manifest?.release_manifest_sha256 || ""))
    ) return null;
    const unsignedRelease = { ...manifest };
    delete unsignedRelease.release_manifest_sha256;
    if (sha256Bytes(Buffer.from(canonicalJson(unsignedRelease), "utf8")) !== manifest.release_manifest_sha256) {
      return null;
    }
    const componentManifest = manifest.component_manifest;
    if (
      !componentManifest
      || typeof componentManifest !== "object"
      || componentManifest.production_claim !== true
      || componentManifest.product_version !== manifest.product_version
      || componentManifest.generated_at_ms !== manifest.generated_at_ms
      || !/^[0-9a-f]{64}$/.test(String(componentManifest.manifest_sha256 || ""))
      || !Array.isArray(componentManifest.components)
    ) return null;
    const unsignedComponents = { ...componentManifest };
    delete unsignedComponents.manifest_sha256;
    if (sha256Bytes(Buffer.from(canonicalJson(unsignedComponents), "utf8")) !== componentManifest.manifest_sha256) {
      return null;
    }
    const releaseDirectory = path.dirname(resolved);
    if (path.basename(releaseDirectory).toLowerCase() !== "release") return null;
    const resourcesRoot = path.dirname(releaseDirectory);
    const descriptors = new Map();
    for (const descriptor of componentManifest.components) {
      const componentId = String(descriptor?.component_id || "");
      if (!componentId || descriptors.has(componentId)) return null;
      descriptors.set(componentId, descriptor);
    }
    const desktop = descriptors.get("tiangong-desktop");
    if (
      descriptors.size !== NATIVE_COMPONENT_IDS.length + 1
      || !desktop
      || desktop.version !== manifest.product_version
      || !String(desktop.build_id || "")
      || desktop.executable_relative_path !== "app.asar"
    ) return null;
    const desktopPath = verifiedFile(resourcesRoot, desktop);
    if (!desktopPath) return null;
    const componentPaths = {};
    for (const componentId of NATIVE_COMPONENT_IDS) {
      const descriptor = descriptors.get(componentId);
      if (!descriptor) return null;
      const executable = verifiedFile(resourcesRoot, descriptor);
      if (!executable) return null;
      componentPaths[componentId] = executable;
    }
    return Object.freeze({
      manifestPath: resolved,
      resourcesRoot: fs.realpathSync.native(resourcesRoot),
      productVersion: manifest.product_version,
      generatedAtMs: manifest.generated_at_ms,
      releaseManifestSha256: manifest.release_manifest_sha256,
      desktopBuildId: String(desktop.build_id),
      desktopPath,
      desktopSha256: String(desktop.sha256),
      componentPaths: Object.freeze(componentPaths),
    });
  } catch (_error) {
    return null;
  }
}

function compareReleaseBindings(left, right) {
  return compareSemver(left.productVersion, right.productVersion)
    || (left.generatedAtMs < right.generatedAtMs ? -1 : left.generatedAtMs > right.generatedAtMs ? 1 : 0)
    || compareText(left.releaseManifestSha256, right.releaseManifestSha256)
    || compareText(String(left.manifestPath).toLowerCase(), String(right.manifestPath).toLowerCase())
    || compareText(left.manifestPath, right.manifestPath);
}

function discoverVerifiedReleaseBindings(candidates) {
  const seen = new Set();
  const verified = [];
  for (const candidate of candidates || []) {
    if (!candidate) continue;
    const resolved = path.resolve(String(candidate));
    const key = resolved.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const binding = readVerifiedReleaseBinding(resolved);
    if (binding) verified.push(binding);
  }
  return verified.sort((left, right) => compareReleaseBindings(right, left));
}

module.exports = {
  NATIVE_COMPONENT_IDS,
  compareReleaseBindings,
  compareSemver,
  discoverVerifiedReleaseBindings,
  parseSemver,
  readVerifiedReleaseBinding,
};
