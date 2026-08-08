import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFileSync,
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";
import { verifyAppAsarAvatarContract } from "./verify-app-asar-avatar-contract.mjs";
import { verifyFrontendModuleClosure } from "./verify-frontend-module-closure.mjs";

const scriptsRoot = dirname(fileURLToPath(import.meta.url));
export const workspaceRoot = resolve(scriptsRoot, "..");
const appRoot = join(workspaceRoot, "app");
const appPackage = JSON.parse(readFileSync(join(appRoot, "package.json"), "utf8"));
const releasePython = String(
  process.env.TIANGONG_RELEASE_PYTHON
    || (process.platform === "win32"
      ? join(appRoot, "runtime", "python312", "python.exe")
      : "python3"),
);
const productVersion = String(appPackage.version || "").trim();
if (!/^\d+\.\d+\.\d+$/.test(productVersion)) {
  throw new Error(`app package version must be a numeric SemVer triplet: ${productVersion}`);
}
const productName = String(appPackage.productName || "").trim();
const developerName = String(appPackage.author?.name || "").trim();
if (!productName || !developerName) {
  throw new Error("app package productName and author.name are required");
}
// 默认发布位置保持稳定；显式覆盖只用于隔离候选验证，避免覆盖已有正式制品。
const configuredArtifactsRoot = String(
  process.env.TIANGONG_RELEASE_ARTIFACTS_ROOT || "",
).trim();
const releaseArtifactsRoot = configuredArtifactsRoot
  ? resolve(configuredArtifactsRoot)
  : join(workspaceRoot, "release-artifacts", productVersion);

function releaseStageRoot(platform, architecture) {
  const configured = String(process.env.TIANGONG_RELEASE_STAGE || "").trim();
  if (configured) return resolve(configured);
  if (platform === "win32" && workspaceRoot.length > 80) {
    const localRoot = String(process.env.LOCALAPPDATA || process.env.TEMP || "").trim();
    if (!localRoot) {
      throw new Error("Windows long-path release staging requires LOCALAPPDATA or TEMP");
    }
    const workspaceId = createHash("sha256")
      .update(workspaceRoot, "utf8")
      .digest("hex")
      .slice(0, 12);
    return join(localRoot, "TiangongV3Release", workspaceId, `${platform}-${architecture}`);
  }
  return join(workspaceRoot, "release-stage", `${platform}-${architecture}`);
}

function commandName(name) {
  return process.platform === "win32" ? `${name}.cmd` : name;
}

function windowsPowerShellCommand() {
  const configured = String(process.env.TIANGONG_RELEASE_POWERSHELL || "").trim();
  if (configured) return configured;
  for (const candidate of ["pwsh.exe", "powershell.exe"]) {
    const probe = spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/c", "where", candidate], {
      encoding: "utf8",
      stdio: ["ignore", "ignore", "ignore"],
      windowsHide: true,
    });
    if (!probe.error && probe.status === 0) return candidate;
  }
  throw new Error("Windows release requires pwsh.exe or powershell.exe");
}

function run(command, args, options = {}) {
  const printable = [command, ...args].map((item) => JSON.stringify(String(item))).join(" ");
  process.stdout.write(`\n> ${printable}\n`);
  const isWindowsCommandScript = process.platform === "win32"
    && command.toLowerCase().endsWith(".cmd");
  const commandLine = [
    command,
    ...args.map((item) => `"${String(item).replaceAll('"', '""')}"`),
  ].join(" ");
  const executable = isWindowsCommandScript
    ? (process.env.ComSpec || "cmd.exe")
    : command;
  const executableArgs = isWindowsCommandScript
    ? ["/d", "/s", "/c", commandLine]
    : args;
  const result = spawnSync(executable, executableArgs, {
    cwd: options.cwd || workspaceRoot,
    env: { ...process.env, ...options.env },
    encoding: options.capture ? "utf8" : undefined,
    stdio: options.capture ? ["ignore", "pipe", "pipe"] : "inherit",
    windowsHide: true,
    windowsVerbatimArguments: isWindowsCommandScript,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture
      ? `\nstdout:\n${result.stdout || ""}\nstderr:\n${result.stderr || ""}`
      : "";
    throw new Error(`command failed (${result.status}): ${printable}${detail}`);
  }
  return options.capture ? String(result.stdout || "").trim() : "";
}

function assertFile(path, label) {
  if (!existsSync(path) || !statSync(path).isFile()) {
    throw new Error(`${label} is missing: ${path}`);
  }
}

function assertDirectory(path, label) {
  if (!existsSync(path) || !statSync(path).isDirectory()) {
    throw new Error(`${label} is missing: ${path}`);
  }
}

function createEmptyDirectory(path, label) {
  if (existsSync(path)) {
    if (!statSync(path).isDirectory() || readdirSync(path).length !== 0) {
      throw new Error(`${label} must be absent or empty; refusing to overwrite: ${path}`);
    }
    return;
  }
  mkdirSync(path, { recursive: true });
}

function sha256(path) {
  const digest = createHash("sha256");
  digest.update(readFileSync(path));
  return digest.digest("hex").toUpperCase();
}

function listFiles(root) {
  const result = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) result.push(...listFiles(path));
    else if (entry.isFile()) result.push(path);
  }
  return result.sort((left, right) => left.localeCompare(right));
}

function assertNoPackageLinks(root, label) {
  assertDirectory(root, label);
  const visit = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      const stat = lstatSync(path);
      if (entry.isSymbolicLink() || stat.isSymbolicLink()) {
        throw new Error(`${label} contains a symbolic link or junction: ${path}`);
      }
      if (entry.isDirectory()) visit(path);
    }
  };
  visit(root);
}

function assertNoPublisherPathLeak(root, label) {
  assertDirectory(root, label);
  const profileRoot = String(process.env.USERPROFILE || process.env.HOME || "").trim();
  const rawMarkers = [workspaceRoot, profileRoot].filter(Boolean);
  const markers = new Set();
  for (const raw of rawMarkers) {
    markers.add(raw);
    markers.add(raw.replaceAll("\\", "/"));
    markers.add(`file:///${raw.replaceAll("\\", "/")}`);
  }
  for (const file of listFiles(root)) {
    const bytes = readFileSync(file);
    for (const marker of markers) {
      const utf8 = Buffer.from(marker, "utf8");
      const utf16 = Buffer.from(marker, "utf16le");
      if (bytes.includes(utf8) || bytes.includes(utf16)) {
        throw new Error(
          `${label} contains a publisher-machine path: ${relative(root, file).replaceAll("\\", "/")}`,
        );
      }
    }
  }
}

const RUNTIME_RESIDUE_SEGMENTS = new Set([
  ".omni_audit",
  ".omni_backups",
  ".tiangong",
  ".pytest_cache",
  "__pycache__",
  "browser_snapshots",
]);

function runtimeResidueReason(relativePath) {
  const normalized = String(relativePath || "").replaceAll("\\", "/");
  const segments = normalized.split("/").filter(Boolean);
  const folded = segments.map((item) => item.toLowerCase());
  const forbiddenSegment = folded.find((item) => RUNTIME_RESIDUE_SEGMENTS.has(item));
  if (forbiddenSegment) return `directory:${forbiddenSegment}`;
  const name = folded.at(-1) || "";
  if (
    name.endsWith(".log")
    || name.endsWith(".lock")
    || name.endsWith(".tmp")
    || name === "desktop_renderer.jsonl"
    || /\.bak(?:[-._0-9].*)?$/.test(name)
  ) {
    return `file:${name}`;
  }
  return "";
}

function assertNoRuntimeResidue(root, label) {
  assertDirectory(root, label);
  for (const file of listFiles(root)) {
    const relativePath = relative(root, file).replaceAll("\\", "/");
    const reason = runtimeResidueReason(relativePath);
    if (reason) {
      throw new Error(`${label} contains runtime residue (${reason}): ${relativePath}`);
    }
  }
}

function parseProbe(output, componentId) {
  const lines = output.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  let payload;
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      const candidate = JSON.parse(lines[index]);
      if (candidate && candidate.component_id === componentId) {
        payload = candidate;
        break;
      }
    } catch {
      // A frozen dependency may write a diagnostic line before the JSON probe.
    }
  }
  if (!payload || payload.ok !== true) {
    throw new Error(`${componentId} release probe did not report a healthy component`);
  }
  return payload;
}

function stagedVersionFile(stageRoot, directoryName) {
  const source = join(workspaceRoot, "build", `version-${directoryName}.txt`);
  assertFile(source, `${directoryName} version template`);
  const [major, minor, patch] = productVersion.split(".").map(Number);
  const tuple = `${major}, ${minor}, ${patch}, 0`;
  const fileVersion = `${productVersion}.0`;
  let content = readFileSync(source, "utf8");
  content = content
    .replace(/filevers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)/, `filevers=(${tuple})`)
    .replace(/prodvers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)/, `prodvers=(${tuple})`)
    .replace(/StringStruct\('FileVersion',\s*'[^']+'\)/, `StringStruct('FileVersion', '${fileVersion}')`)
    .replace(/StringStruct\('ProductVersion',\s*'[^']+'\)/, `StringStruct('ProductVersion', '${productVersion}')`);
  for (const marker of [
    `filevers=(${tuple})`,
    `prodvers=(${tuple})`,
    `StringStruct('FileVersion', '${fileVersion}')`,
    `StringStruct('ProductVersion', '${productVersion}')`,
  ]) {
    if (!content.includes(marker)) throw new Error(`${directoryName} version template could not bind ${marker}`);
  }
  const target = join(stageRoot, "pyinstaller-spec", `version-${directoryName}.txt`);
  writeFileSync(target, content, "utf8");
  return target;
}

function copyRuntimeSource(sourceRoot, destinationRoot) {
  cpSync(sourceRoot, destinationRoot, {
    recursive: true,
    force: true,
    filter: (source) => {
      const relativePath = relative(sourceRoot, source).replaceAll("\\", "/");
      if (runtimeResidueReason(relativePath)) return false;
      return true;
    },
  });
}

function buildFrozenService({
  stageRoot,
  directoryName,
  executableName,
  entryName,
  collectAll = [],
  additionalPaths = [],
  outputRelativePath = directoryName,
  platform,
  sourceOverlay = "",
  sourceOverlayTarget = outputRelativePath,
  versionTemplateName = directoryName,
}) {
  const distRoot = join(stageRoot, "pyinstaller-dist", directoryName);
  const workRoot = join(stageRoot, "pyinstaller-work", directoryName);
  const specRoot = join(stageRoot, "pyinstaller-spec");
  mkdirSync(distRoot, { recursive: true });
  mkdirSync(workRoot, { recursive: true });
  mkdirSync(specRoot, { recursive: true });
  const args = [
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", executableName,
    "--distpath", distRoot,
    "--workpath", workRoot,
    "--specpath", specRoot,
    "--paths", join(workspaceRoot, "src"),
    "--icon", join(appRoot, "assets", platform === "darwin" ? "tiangong-logo.icns" : "tiangong-logo.ico"),
  ];
  for (const path of additionalPaths) args.push("--paths", path);
  if (platform === "win32") {
    args.push(
      "--version-file",
      stagedVersionFile(stageRoot, versionTemplateName),
    );
  }
  for (const packageName of collectAll) args.push("--collect-all", packageName);
  args.push(join(workspaceRoot, "scripts", entryName));
  run(releasePython, args, {
    env: {
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONPATH: [join(workspaceRoot, "src"), ...additionalPaths].join(process.platform === "win32" ? ";" : ":"),
    },
  });

  const generatedName = platform === "win32" ? `${executableName}.exe` : executableName;
  const generatedRoot = join(distRoot, executableName);
  const desiredRoot = join(stageRoot, ...String(outputRelativePath).split("/"));
  mkdirSync(dirname(desiredRoot), { recursive: true });
  if (existsSync(desiredRoot)) {
    throw new Error(`refusing to replace frozen service directory: ${desiredRoot}`);
  }
  renameSync(generatedRoot, desiredRoot);
  rmSync(distRoot, { recursive: true, force: true });
  if (sourceOverlay) {
    copyRuntimeSource(
      sourceOverlay,
      join(stageRoot, ...String(sourceOverlayTarget).split("/")),
    );
  }
  const executable = join(desiredRoot, generatedName);
  assertFile(executable, `${directoryName} frozen executable`);
  return executable;
}

function signingMode(platform) {
  const required = process.env.TIANGONG_RELEASE_REQUIRE_SIGNING === "1";
  if (!required) return { required: false, mode: "unsigned-candidate" };
  if (!process.env.CSC_LINK) {
    throw new Error("production signing is required but CSC_LINK is not configured");
  }
  if (platform === "darwin") {
    const apiKeyMode = process.env.APPLE_API_KEY
      && process.env.APPLE_API_KEY_ID
      && process.env.APPLE_API_ISSUER;
    const appleIdMode = process.env.APPLE_ID
      && process.env.APPLE_APP_SPECIFIC_PASSWORD
      && process.env.APPLE_TEAM_ID;
    if (!apiKeyMode && !appleIdMode) {
      throw new Error("macOS signing requires Apple notarization credentials");
    }
  }
  return { required: true, mode: "signed-production" };
}

function gitMetadata() {
  let commit = "unavailable";
  let dirty = null;
  try {
    commit = run("git", ["rev-parse", "HEAD"], { capture: true });
    dirty = run("git", ["status", "--porcelain"], { capture: true }).length > 0;
  } catch {
    // A source export without Git metadata remains buildable and is marked as such.
  }
  return { commit, dirty };
}

function prepareArtifactDirectory(platform, architecture) {
  const artifactRoot = join(releaseArtifactsRoot, `${platform}-${architecture}`);
  createEmptyDirectory(artifactRoot, "release artifact directory");
  return artifactRoot;
}

function publishArtifacts({ builderOutput, stageRoot, platform, architecture, signing }) {
  const artifactRoot = prepareArtifactDirectory(platform, architecture);
  const allowed = platform === "win32"
    ? /(?:\.exe|\.exe\.blockmap|^latest[^/]*\.ya?ml)$/i
    : /(?:\.dmg|\.zip|\.(?:dmg|zip)\.blockmap|^latest[^/]*\.ya?ml)$/i;
  const candidates = readdirSync(builderOutput, { withFileTypes: true })
    .filter((entry) => entry.isFile() && allowed.test(entry.name))
    .map((entry) => join(builderOutput, entry.name));
  if (!candidates.some((path) => platform === "win32" ? /\.exe$/i.test(path) : /\.dmg$/i.test(path))) {
    throw new Error(`electron-builder did not produce the expected ${platform} installer`);
  }
  for (const source of candidates) copyFileSync(source, join(artifactRoot, source.split(/[\\/]/).pop()));
  copyFileSync(
    join(stageRoot, "release", "release-manifest.json"),
    join(artifactRoot, "release-manifest.json"),
  );
  copyFileSync(join(appRoot, "LICENSE.txt"), join(artifactRoot, "LICENSE.txt"));

  const metadata = gitMetadata();
  const provenancePath = join(artifactRoot, "release-provenance.json");
  const initialFiles = listFiles(artifactRoot).map((path) => ({
    path: relative(artifactRoot, path).replaceAll("\\", "/"),
    bytes: statSync(path).size,
    sha256: sha256(path),
  }));
  writeFileSync(provenancePath, `${JSON.stringify({
    schema: "tiangong.v3.desktop-release-provenance.v1",
    product: productName,
    version: productVersion,
    developer: developerName,
    platform,
    architecture,
    signing: signing.mode,
    source: metadata,
    qr_release_probe: platform === "win32" ? "passed" : "required",
    files: initialFiles,
  }, null, 2)}\n`, "utf8");

  const checksumFiles = listFiles(artifactRoot)
    .filter((path) => !path.endsWith("SHA256SUMS.txt"));
  const checksumText = checksumFiles.map((path) => (
    `${sha256(path)}  ${relative(artifactRoot, path).replaceAll("\\", "/")}`
  )).join("\n") + "\n";
  writeFileSync(join(artifactRoot, "SHA256SUMS.txt"), checksumText, "utf8");
  return artifactRoot;
}

function prepareReleaseManifestPlaceholder(stageRoot) {
  // extraResources needs the release directory before Electron packing.  This
  // development authority is deliberately non-production; the afterPack hook
  // replaces it only after app.asar exists and can be hashed as a whole.
  run(releasePython, [
    "-m", "total_gateway.release_manifest",
    "--workspace", workspaceRoot,
    "--output", join(stageRoot, "release"),
  ], {
    env: {
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONPATH: join(workspaceRoot, "src"),
    },
  });
  assertFile(
    join(stageRoot, "release", "release-manifest.json"),
    "provisional release manifest",
  );
}

function runElectronBuilder({ stageRoot, platform, architecture, signing }) {
  const args = [
    "electron-builder",
    "--config", join(workspaceRoot, "electron-builder.config.cjs"),
    platform === "win32" ? "--win" : "--mac",
    architecture === "arm64" ? "--arm64" : "--x64",
    "--publish", "never",
  ];
  const output = join(stageRoot, "electron-builder");
  const builderEnv = {
    TIANGONG_RELEASE_PLATFORM: platform,
    TIANGONG_RELEASE_ARCH: architecture,
    TIANGONG_RELEASE_STAGE: stageRoot,
    TIANGONG_RELEASE_RUNTIME_ROOT: stageRoot,
    TIANGONG_RELEASE_REQUIRE_SIGNING: signing.required ? "1" : "0",
    TIANGONG_RELEASE_FLAVOR: signing.required ? "signed" : "unsigned",
  };
  try {
    run(commandName("npx"), args, { cwd: appRoot, env: builderEnv });
  } catch (error) {
    if (String(process.env.TIANGONG_DISABLE_DEPENDENCY_FALLBACK || "").trim() === "1") {
      throw error;
    }
    const fallback = String(
      process.env.TIANGONG_ELECTRON_BUILDER_FALLBACK_MIRROR
        || "https://npmmirror.com/mirrors/electron-builder-binaries/"
    ).trim();
    process.stdout.write(
      `[dependency-fallback] electron-builder: retrying with ${fallback}\n`,
    );
    rmSync(output, { recursive: true, force: true });
    run(commandName("npx"), args, {
      cwd: appRoot,
      env: { ...builderEnv, ELECTRON_BUILDER_BINARIES_MIRROR: fallback },
    });
  }
  assertDirectory(output, "electron-builder output");
  return output;
}

function verifyPackagedWindowsRelease(stageRoot, builderOutput) {
  const unpackedRoot = join(builderOutput, "win-unpacked");
  const resourcesRoot = join(unpackedRoot, "resources");
  const asarPath = join(resourcesRoot, "app.asar");
  assertFile(asarPath, "packaged app.asar");
  assertFile(join(resourcesRoot, "python", "python.exe"), "packaged embedded Python");
  if (existsSync(join(resourcesRoot, "python", "Scripts"))) {
    throw new Error("publisher-bound Python Scripts launchers leaked into the packaged application");
  }
  assertNoPublisherPathLeak(unpackedRoot, "packaged Windows application");
  assertNoRuntimeResidue(unpackedRoot, "packaged Windows application");
  const requireFromApp = createRequire(join(appRoot, "package.json"));
  const asar = requireFromApp("@electron/asar");
  const packagedManifestPath = join(resourcesRoot, "release", "release-manifest.json");
  assertFile(packagedManifestPath, "packaged release manifest");
  if (
    !readFileSync(packagedManifestPath).equals(
      readFileSync(join(stageRoot, "release", "release-manifest.json")),
    )
  ) {
    throw new Error("staged and packaged release manifests differ");
  }
  const { readVerifiedReleaseBinding } = requireFromApp(
    join(appRoot, "lib", "release-binding.js"),
  );
  const verifiedBinding = readVerifiedReleaseBinding(packagedManifestPath);
  if (
    !verifiedBinding
    || verifiedBinding.desktopPath !== realpathSync.native(asarPath)
  ) {
    throw new Error("packaged release binding does not authorize the complete app.asar");
  }
  const manifest = JSON.parse(
    readFileSync(packagedManifestPath, "utf8"),
  );
  const components = manifest?.component_manifest?.components;
  if (!manifest?.production_claim || !Array.isArray(components) || components.length !== 5) {
    throw new Error("packaged production manifest is incomplete");
  }
  const runtimePaths = new Set();
  for (const component of components) {
    let bytes;
    if (component.component_id === "tiangong-desktop") {
      if (component.executable_relative_path !== "app.asar") {
        throw new Error("desktop release manifest path is invalid");
      }
      bytes = readFileSync(asarPath);
    } else {
      const logicalPath = String(component.executable_relative_path);
      runtimePaths.add(logicalPath);
      const path = join(resourcesRoot, ...logicalPath.split("/"));
      assertFile(path, `${component.component_id} packaged executable`);
      bytes = readFileSync(path);
    }
    const actualHash = createHash("sha256").update(bytes).digest("hex");
    if (actualHash !== component.sha256 || bytes.length !== component.size_bytes) {
      throw new Error(`${component.component_id} does not match the production manifest`);
    }
  }
  if (runtimePaths.size !== 1 || !runtimePaths.has("total-gateway/tiangong-total-gateway.exe")) {
    throw new Error("logical runtime components are not bound to the one 7184 executable");
  }

  const packagedFiles = new Set(asar.listPackage(asarPath).map((item) => item.replaceAll("\\", "/")));
  for (const entry of packagedFiles) {
    if (/\.(?:exe|dll|node|pyd)$/i.test(entry)) {
      throw new Error(`spawned/native runtime leaked into read-only app.asar: ${entry}`);
    }
  }
  const avatarAsarContract = verifyAppAsarAvatarContract(asarPath, { asar });
  const frontendModuleClosure = verifyFrontendModuleClosure({
    packagedFiles,
    readText: (archivePath) => asar.extractFile(
      asarPath,
      join(...archivePath.replace(/^\/+/, "").split("/")),
    ).toString("utf8"),
  });
  const asarUnpackedRoot = join(resourcesRoot, "app.asar.unpacked");
  if (existsSync(asarUnpackedRoot) && listFiles(asarUnpackedRoot).length) {
    throw new Error("desktop archive unexpectedly produced app.asar.unpacked payload");
  }
  const asarSources = new Map([
    ["main.js", join(appRoot, "main.js")],
    ["preload.js", join(appRoot, "preload.js")],
    ["runtime-root.js", join(appRoot, "runtime-root.js")],
    ["service-supervisor.js", join(appRoot, "service-supervisor.js")],
    ["lib/release-binding.js", join(appRoot, "lib", "release-binding.js")],
  ]);
  for (const [relativePath, sourcePath] of asarSources) {
    const archived = asar.extractFile(asarPath, relativePath);
    const source = readFileSync(sourcePath);
    if (!archived.equals(source)) {
      throw new Error(`app.asar changed critical source bytes: ${relativePath}`);
    }
  }
  const preload = asar.extractFile(asarPath, "preload.js").toString("utf8");
  const preloadRequires = [...preload.matchAll(/require\(\s*["']([^"']+)["']\s*\)/g)]
    .map((match) => match[1]);
  if (preloadRequires.length !== 1 || preloadRequires[0] !== "electron") {
    throw new Error(`sandboxed preload has unsupported imports: ${preloadRequires.join(",")}`);
  }
  for (const forbidden of [
    "/frontend-v2/renderer/plugins/persona-panel.mjs",
    "/frontend-v2/renderer/plugins/lifecycle-side-block.mjs",
  ]) {
    if (packagedFiles.has(forbidden)) throw new Error(`dead frontend module leaked into app.asar: ${forbidden}`);
  }
  const settings = asar.extractFile(
    asarPath,
    join("frontend-v2", "renderer", "plugins", "settings-panel.mjs"),
  ).toString("utf8");
  for (const marker of [
    "wechat_direct_login_start",
    "linkWechatQrWrap.hidden = false",
    "linkWechatQrImage.src = qrcodeUrl",
    "qrTextToSvgDataUrl(qrcodeUrl)",
  ]) {
    if (!settings.includes(marker)) throw new Error(`packaged WeChat QR marker is missing: ${marker}`);
  }

  const totalGateway = join(resourcesRoot, "total-gateway", "tiangong-total-gateway.exe");
  const overlayRoot = join(resourcesRoot, "total-gateway", "backend", "tiangong-backend");
  assertDirectory(overlayRoot, "embedded backend source overlay");
  for (const relativePath of [
    join("v3", "permission_settings.py"),
    join("_internal", "frozen_modules", "v3", "execution_kernel", "confirmation_bridge.py"),
  ]) {
    const packaged = join(overlayRoot, relativePath);
    const source = join(appRoot, "backend", "tiangong-backend", relativePath);
    assertFile(packaged, `packaged A5 confirmation overlay ${relativePath}`);
    if (!readFileSync(packaged).equals(readFileSync(source))) {
      throw new Error(`packaged A5 confirmation overlay changed bytes: ${relativePath}`);
    }
  }
  const totalProbe = parseProbe(
    run(totalGateway, ["--release-probe"], { capture: true }),
    "tiangong-total-gateway",
  );
  if (
    totalProbe.deployment_mode !== "embedded"
    || totalProbe.listener_port !== 7184
    || totalProbe.runtime_api_contract !== "tiangong.desktop.backend.v3"
    || totalProbe.life_api_contract !== "tiangong.life.api.v2"
    || totalProbe.communication_api_contract !== "tiangong.communication.api.v1"
    || totalProbe.identity_migration !== true
    || totalProbe.wechat_qr !== true
    || totalProbe.lark_oapi !== true
  ) {
    throw new Error("single-process release probe is missing a required logical subsystem");
  }

  const installers = readdirSync(builderOutput, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.exe$/i.test(entry.name))
    .map((entry) => join(builderOutput, entry.name));
  if (installers.length !== 1) {
    throw new Error(`expected exactly one NSIS installer, found ${installers.length}`);
  }
  const blockmapPath = `${installers[0]}.blockmap`;
  assertFile(blockmapPath, "NSIS differential blockmap");
  let blockmap;
  try {
    blockmap = JSON.parse(gunzipSync(readFileSync(blockmapPath)).toString("utf8"));
  } catch (error) {
    throw new Error(`NSIS differential blockmap is invalid: ${error instanceof Error ? error.message : error}`);
  }
  const blockFiles = Array.isArray(blockmap?.files) ? blockmap.files : [];
  const blockBytes = blockFiles.reduce(
    (total, item) => total + (Array.isArray(item?.sizes) ? item.sizes.reduce((sum, value) => sum + Number(value || 0), 0) : 0),
    0,
  );
  if (blockmap?.version !== "2" || blockFiles.length !== 1 || blockBytes !== statSync(installers[0]).size) {
    throw new Error("NSIS differential blockmap does not cover the exact compressed installer bytes");
  }
  return {
    production_manifest: "passed",
    logical_component_hashes: components.length,
    physical_python_executables: 1,
    app_asar: "passed",
    app_asar_critical_bytes: asarSources.size,
    app_asar_native_files: 0,
    app_asar_unpacked_files: 0,
    app_asar_avatar_module_closure: avatarAsarContract.requiredModuleCount,
    app_asar_frontend_module_closure: frontendModuleClosure.moduleCount,
    app_asar_forbidden_avatar_assets_absent: avatarAsarContract.forbiddenAssetCount,
    sandboxed_preload_requires: preloadRequires,
    a5_confirmation_overlays: 2,
    dead_modules_absent: true,
    release_binding: "complete-app.asar",
    runtime: totalProbe.runtime_api_contract,
    life: totalProbe.life_api_contract,
    communication: totalProbe.communication_api_contract,
    total_gateway: totalProbe.ok === true,
    differential_blockmap: "passed",
  };
}

export function finalizeWindowsStage() {
  if (process.platform !== "win32") throw new Error("Windows release must be finalized on Windows");
  const platform = "win32";
  const architecture = "x64";
  const stageRoot = releaseStageRoot(platform, architecture);
  const builderOutput = join(stageRoot, "electron-builder");
  const signing = signingMode(platform);
  assertDirectory(builderOutput, "completed electron-builder output");
  assertFile(join(stageRoot, "release", "release-manifest.json"), "production release manifest");
  const packagedVerification = verifyPackagedWindowsRelease(stageRoot, builderOutput);
  const artifactRoot = publishArtifacts({ builderOutput, stageRoot, platform, architecture, signing });
  const result = {
    ok: true,
    artifact_root: artifactRoot,
    signing: signing.mode,
    packaged_verification: packagedVerification,
  };
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  return result;
}

function commonPreflight({ platform, architecture }) {
  for (const [path, label] of [
    [join(appRoot, "assets", "tiangong-logo.ico"), "Windows logo"],
    [join(appRoot, "assets", "tiangong-logo.icns"), "macOS logo"],
    [join(workspaceRoot, "electron-builder.config.cjs"), "electron-builder config"],
    [join(workspaceRoot, "build", "installer.nsh"), "NSIS include"],
  ]) assertFile(path, label);
  assertFile(join(appRoot, "package-lock.json"), "npm lock file");
  if (process.platform === "win32") {
    assertFile(releasePython, "embedded CPython release interpreter");
    const version = run(
      releasePython,
      ["-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
      { capture: true },
    );
    if (!version.startsWith("3.12.")) {
      throw new Error(`Windows frozen release requires CPython 3.12, found ${version}`);
    }
  }
  const preflightPython = process.platform === "win32" ? releasePython : "python";
  run(preflightPython, [join(workspaceRoot, "scripts", "audit-portable-paths.py"), "--root", workspaceRoot]);
  run(preflightPython, [join(workspaceRoot, "scripts", "sync-generated-sources.py"), "--check"]);
  if (process.platform === "win32") {
    run(releasePython, [join(workspaceRoot, "scripts", "rebuild_frozen_release_overlays.py")]);
  }
  assertNoPackageLinks(appRoot, "desktop application input");
  run("node", [
    join(workspaceRoot, "scripts", "install-node-dependencies.mjs"),
    "--platform", platform,
    "--arch", architecture,
  ]);
  const electronDistribution = join(
    appRoot,
    "node_modules",
    "electron",
    "dist",
    platform === "win32" ? "electron.exe" : "Electron.app",
  );
  if (platform === "win32") {
    assertFile(electronDistribution, "Electron distribution");
  } else {
    assertDirectory(electronDistribution, "Electron distribution");
  }
}

export function releaseWindows({ resume = false } = {}) {
  if (process.platform !== "win32") throw new Error("Windows release must run on Windows");
  const architecture = "x64";
  const platform = "win32";
  const stageRoot = releaseStageRoot(platform, architecture);
  if (resume) assertDirectory(stageRoot, "resumable Windows release stage");
  else createEmptyDirectory(stageRoot, "Windows release stage");
  const signing = signingMode(platform);
  const backendRoot = join(appRoot, "backend", "tiangong-backend");
  const totalGateway = join(stageRoot, "total-gateway", "tiangong-total-gateway.exe");
  if (resume) {
    assertFile(join(appRoot, "node_modules", ".bin", "electron-builder.cmd"), "electron-builder CLI");
    assertFile(totalGateway, "resumable single-process gateway");
    assertFile(join(stageRoot, "release", "release-manifest.json"), "resumable release manifest");
  } else {
    commonPreflight({ platform, architecture });
    run(windowsPowerShellCommand(), ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", join(workspaceRoot, "scripts", "check.ps1")]);
    buildFrozenService({
      stageRoot,
      directoryName: "total-gateway",
      executableName: "tiangong-total-gateway",
      entryName: "frozen_total_gateway_entry.py",
      collectAll: ["lark_oapi"],
      additionalPaths: [backendRoot],
      outputRelativePath: "total-gateway",
      platform,
      sourceOverlay: backendRoot,
      sourceOverlayTarget: "total-gateway/backend/tiangong-backend",
      versionTemplateName: "total-gateway",
    });
  }
  const totalProbe = parseProbe(
    run(totalGateway, ["--release-probe"], { capture: true }),
    "tiangong-total-gateway",
  );
  if (
    totalProbe.deployment_mode !== "embedded"
    || totalProbe.listener_port !== 7184
    || totalProbe.runtime_api_contract !== "tiangong.desktop.backend.v3"
    || totalProbe.life_api_contract !== "tiangong.life.api.v2"
    || totalProbe.communication_api_contract !== "tiangong.communication.api.v1"
    || totalProbe.identity_migration !== true
    || totalProbe.wechat_qr !== true
    || totalProbe.lark_oapi !== true
  ) {
    throw new Error("single-process release probe is incomplete");
  }
  assertNoPackageLinks(join(stageRoot, "total-gateway"), "single-process runtime input");
  if (!resume) prepareReleaseManifestPlaceholder(stageRoot);
  const builderOutput = runElectronBuilder({ stageRoot, platform, architecture, signing });
  const packagedVerification = verifyPackagedWindowsRelease(stageRoot, builderOutput);
  const artifactRoot = publishArtifacts({ builderOutput, stageRoot, platform, architecture, signing });
  // 最终发布硬门必须检查 NSIS 实际负载，不能只相信 win-unpacked。
  // 脚本会解包安装器、重验 release binding、app.asar VRM 模块闭包和禁发模型，
  // 并比较压缩前后的 app.asar 哈希；失败即阻断一键发布。
  run(windowsPowerShellCommand(), [
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", join(workspaceRoot, "scripts", "verify-windows-artifacts.ps1"),
    "-ArtifactRoot", artifactRoot,
  ], {
    env: {
      TIANGONG_RELEASE_STAGE: stageRoot,
    },
  });
  process.stdout.write(`${JSON.stringify({
    ok: true,
    artifact_root: artifactRoot,
    signing: signing.mode,
    packaged_verification: packagedVerification,
    probes: { total_gateway: totalProbe },
  }, null, 2)}\n`);
}

export function releaseMac() {
  if (process.platform !== "darwin") throw new Error("macOS release must run on a macOS host");
  const architecture = process.env.TIANGONG_RELEASE_ARCH === "arm64" ? "arm64" : "x64";
  const platform = "darwin";
  const stageRoot = releaseStageRoot(platform, architecture);
  createEmptyDirectory(stageRoot, "macOS release stage");
  const signing = signingMode(platform);
  const backendRoot = join(appRoot, "backend", "tiangong-backend");
  commonPreflight({ platform, architecture });
  run("pwsh", ["-NoProfile", "-File", join(workspaceRoot, "scripts", "check.ps1")]);
  const totalGateway = buildFrozenService({
    stageRoot,
    directoryName: "total-gateway",
    executableName: "tiangong-total-gateway",
    entryName: "frozen_total_gateway_entry.py",
    collectAll: ["lark_oapi"],
    additionalPaths: [backendRoot],
    outputRelativePath: "total-gateway",
    platform,
    sourceOverlay: backendRoot,
    sourceOverlayTarget: "total-gateway/backend/tiangong-backend",
    versionTemplateName: "total-gateway",
  });
  const totalProbe = parseProbe(
    run(totalGateway, ["--release-probe"], { capture: true }),
    "tiangong-total-gateway",
  );
  if (totalProbe.deployment_mode !== "embedded" || totalProbe.listener_port !== 7184) {
    throw new Error("macOS single-process release probe is incomplete");
  }
  prepareReleaseManifestPlaceholder(stageRoot);
  const builderOutput = runElectronBuilder({ stageRoot, platform, architecture, signing });
  const artifactRoot = publishArtifacts({ builderOutput, stageRoot, platform, architecture, signing });
  process.stdout.write(`${JSON.stringify({ ok: true, artifact_root: artifactRoot, signing: signing.mode, probes: { total_gateway: totalProbe } }, null, 2)}\n`);
}
