"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { preflightVrcAvatarSource, unityCandidates } = require("../app/vrc-import");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "tiangong-vrc-import-"));
try {
  const packagePath = path.join(root, "owned-avatar.unitypackage");
  fs.writeFileSync(packagePath, "fixture");
  const packageReport = preflightVrcAvatarSource(packagePath, { env: {} });
  assert.equal(packageReport.ok, true);
  assert.equal(packageReport.source.kind, "unitypackage");
  assert.equal(packageReport.cache_or_downloaded_avatar_import_supported, false);
  assert.equal(packageReport.unity.detected, false);

  const projectPath = path.join(root, "avatar-project");
  for (const part of ["Assets", "Packages", "ProjectSettings"]) fs.mkdirSync(path.join(projectPath, part), { recursive: true });
  assert.equal(preflightVrcAvatarSource(projectPath, { env: {} }).source.kind, "unity_project");
  assert.equal(preflightVrcAvatarSource(path.join(root, "unknown.vrc"), { env: {} }).error, "source_not_found");
  const programFiles = path.join(root, "Program Files");
  const unityEditor = path.join(programFiles, "Unity", "Hub", "Editor", "2022.3.0f1", "Editor", "Unity.exe");
  fs.mkdirSync(path.dirname(unityEditor), { recursive: true });
  fs.writeFileSync(unityEditor, "fixture");
  assert.deepEqual(
    unityCandidates({ ProgramFiles: programFiles }),
    [unityEditor],
  );
  assert.equal(
    preflightVrcAvatarSource(packagePath, { env: { ProgramFiles: programFiles } }).unity.path,
    unityEditor,
  );
  console.log("vrc import preflight: ok");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
