"use strict";

const fs = require("fs");
const crypto = require("crypto");
const path = require("path");

function isFile(candidate) {
  try {
    return Boolean(candidate) && fs.statSync(candidate).isFile();
  } catch (_error) {
    return false;
  }
}

function writableRuntimeRootCandidates({ explicitRoot = "", userData, appData, tempRoot }) {
  return [
    String(explicitRoot || "").trim(),
    path.join(String(userData || ""), "runtime"),
    path.join(String(appData || ""), "tiangong-v3-qiyuan", "runtime"),
    path.join(String(tempRoot || ""), "tiangong-v3-qiyuan", "runtime"),
  ].filter(Boolean);
}

function resolveWritableRuntimeRoot(options = {}) {
  const rejected = [];
  for (const raw of writableRuntimeRootCandidates(options)) {
    let probe = "";
    try {
      if (!path.isAbsolute(raw)) throw new Error("runtime_root_not_absolute");
      const candidate = path.resolve(raw);
      if (candidate === path.parse(candidate).root) throw new Error("runtime_root_is_volume_root");
      fs.mkdirSync(candidate, { recursive: true });
      const canonical = fs.realpathSync.native(candidate);
      probe = path.join(
        canonical,
        `.tiangong-write-probe-${process.pid}-${Date.now()}-${crypto.randomBytes(6).toString("hex")}`,
      );
      let descriptor = null;
      try {
        descriptor = fs.openSync(probe, "wx", 0o600);
        fs.writeSync(descriptor, Buffer.from("tiangong-runtime-root-probe", "utf8"));
        fs.fsyncSync(descriptor);
      } finally {
        if (descriptor !== null) fs.closeSync(descriptor);
      }
      fs.unlinkSync(probe);
      return { root: canonical, rejected };
    } catch (error) {
      try { if (probe && isFile(probe)) fs.unlinkSync(probe); } catch (_cleanupError) {}
      rejected.push({ path: raw, error: error?.message || String(error) });
    }
  }
  const failure = new Error(`no_writable_runtime_root:${JSON.stringify(rejected)}`);
  failure.code = "no_writable_runtime_root";
  failure.rejected = rejected;
  throw failure;
}

module.exports = { resolveWritableRuntimeRoot, writableRuntimeRootCandidates };
