"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const workspaceRoot = __dirname;
const appRoot = path.join(workspaceRoot, "app");
const buildRoot = path.join(workspaceRoot, "build");
const platform = String(process.env.TIANGONG_RELEASE_PLATFORM || process.platform);
const architecture = String(process.env.TIANGONG_RELEASE_ARCH || process.arch);
const stageRoot = path.resolve(
  process.env.TIANGONG_RELEASE_STAGE
    || path.join(workspaceRoot, "release-stage", `${platform}-${architecture}`),
);
const requireSigning = process.env.TIANGONG_RELEASE_REQUIRE_SIGNING === "1";
const flavor = String(
  process.env.TIANGONG_RELEASE_FLAVOR || (requireSigning ? "signed" : "unsigned"),
).replace(/[^a-z0-9_-]+/gi, "-");
const releasePython = String(
  process.env.TIANGONG_RELEASE_PYTHON
    || (platform === "win32"
      ? path.join(appRoot, "runtime", "python312", "python.exe")
      : "python3"),
);

function packagedResourcesRoot(context) {
  if (platform === "win32") return path.join(context.appOutDir, "resources");
  const productFilename = String(context.packager?.appInfo?.productFilename || "");
  if (!productFilename) throw new Error("packaged product filename is unavailable");
  return path.join(
    context.appOutDir,
    `${productFilename}.app`,
    "Contents",
    "Resources",
  );
}

async function finalizePackagedReleaseBinding(context) {
  const resourcesRoot = packagedResourcesRoot(context);
  const desktopArchive = path.join(resourcesRoot, "app.asar");
  const stageRelease = path.join(stageRoot, "release", "release-manifest.json");
  if (!fs.statSync(desktopArchive).isFile()) {
    throw new Error(`packaged desktop archive is missing: ${desktopArchive}`);
  }
  if (!fs.statSync(stageRelease).isFile()) {
    throw new Error(`provisional release manifest is missing: ${stageRelease}`);
  }
  // afterPack 在 Windows/macOS 共用：在 release manifest 绑定 app.asar 之前，
  // 先对真实归档执行 VRM 依赖闭包与禁止再分发资产硬门。这样 package:dir、
  // Windows 安装器和 macOS 包都不能绕过该契约。
  const avatarVerifier = path.join(
    workspaceRoot,
    "scripts",
    "verify-app-asar-avatar-contract.mjs",
  );
  const avatarVerification = childProcess.spawnSync(
    process.execPath,
    [avatarVerifier, desktopArchive],
    {
      cwd: workspaceRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  if (avatarVerification.error) throw avatarVerification.error;
  if (avatarVerification.status !== 0) {
    throw new Error(
      `packaged avatar ASAR contract failed (${avatarVerification.status}): `
      + `${avatarVerification.stdout || ""}${avatarVerification.stderr || ""}`,
    );
  }
  let avatarContract;
  try {
    avatarContract = JSON.parse(String(avatarVerification.stdout || "").trim());
  } catch (_error) {
    throw new Error("packaged avatar ASAR contract returned invalid JSON");
  }
  if (
    avatarContract?.ok !== true
    || avatarContract.requiredModuleCount !== 11
    || avatarContract.forbiddenAssetCount !== 2
  ) {
    throw new Error("packaged avatar ASAR contract returned an incomplete result");
  }

  const temporaryOutput = fs.mkdtempSync(path.join(stageRoot, ".release-final-"));
  try {
    const generatedAtMs = Date.now();
    const args = [
      "-m", "total_gateway.release_manifest",
      "--workspace", workspaceRoot,
      "--runtime-root", stageRoot,
      "--desktop-archive", desktopArchive,
      "--output", temporaryOutput,
      "--platform", platform,
      "--arch", architecture,
      "--production",
    ];
    const completed = childProcess.spawnSync(releasePython, args, {
      cwd: workspaceRoot,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONPATH: path.join(workspaceRoot, "src"),
        TIANGONG_RELEASE_GENERATED_AT_MS: String(generatedAtMs),
      },
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    if (completed.error) throw completed.error;
    if (completed.status !== 0) {
      throw new Error(
        `final release binding failed (${completed.status}): `
        + `${completed.stdout || ""}${completed.stderr || ""}`,
      );
    }
    const finalized = path.join(temporaryOutput, "release-manifest.json");
    if (!fs.statSync(finalized).isFile()) {
      throw new Error("final release manifest was not generated");
    }
    const packagedRelease = path.join(resourcesRoot, "release", "release-manifest.json");
    fs.mkdirSync(path.dirname(packagedRelease), { recursive: true });
    fs.copyFileSync(finalized, stageRelease);
    fs.copyFileSync(finalized, packagedRelease);
    if (!fs.readFileSync(stageRelease).equals(fs.readFileSync(packagedRelease))) {
      throw new Error("staged and packaged release bindings diverged");
    }
  } finally {
    fs.rmSync(temporaryOutput, { recursive: true, force: true });
  }
}

function resource(from, to, extraFilters = []) {
  return {
    from: path.resolve(from),
    to,
    filter: [
      "**/*",
      ...extraFilters,
      "!**/__pycache__/**",
      "!**/.pytest_cache/**",
      "!**/.omni_audit/**",
      "!**/.omni_backups/**",
      "!**/.tiangong/**",
      "!**/browser_snapshots/**",
      "!**/*.log",
      "!**/*.lock",
      "!**/*.tmp",
      "!**/*.bak*",
      "!**/desktop_renderer.jsonl",
    ],
  };
}

function nativeResources() {
  const runtimeRoot = platform === "darwin"
    ? path.resolve(
      process.env.TIANGONG_RELEASE_RUNTIME_ROOT
        || path.join(workspaceRoot, "release-stage", `${platform}-${architecture}`),
    )
    : stageRoot;
  return [
    resource(path.join(runtimeRoot, "total-gateway"), "total-gateway"),
    resource(path.join(stageRoot, "release"), "release"),
    // pip-generated launchers bind the publisher machine's absolute Python
    // path. Product code invokes modules through python.exe -m instead.
    resource(path.join(appRoot, "runtime", "python312"), "python", ["!Scripts/**"]),
  ];
}


module.exports = {
  appId: "com.tiangong.v3.qiyuan",
  productName: "天工造物 v3.0.3 完整版",
  copyright: "Copyright © 2026 于泳翔",
  artifactName: `天工造物-v${"${version}"}-完整版-${"${os}"}-${"${arch}"}-${flavor}.${"${ext}"}`,
  asar: true,
  compression: "maximum",
  npmRebuild: false,
  nodeGypRebuild: false,
  forceCodeSigning: requireSigning,
  directories: {
    app: appRoot,
    buildResources: buildRoot,
    output: path.join(stageRoot, "electron-builder"),
  },
  files: [
    "main.js",
    "preload.js",
    "qa-web-preload.js",
    "service-supervisor.js",
    "runtime-root.js",
    "secure-updater.js",
    "update-trust.json",
    "vrc-import.js",
    "avatar-asset-host.cjs",
    "avatar-storage-host.cjs",
    "build-info.json",
    "package.json",
    "LICENSE.txt",
    "assets/**/*",
    // 内置 VRM 的 licenseName=Redistribution_Prohibited：源码保留供本机测试，
    // 正式候选包不得分发模型字节。清单仍可打包，由运行时过滤不存在的条目。
    "!assets/avatars/imported/*.vrm",
    "frontend-v2/**/*",
    "!frontend-v2/renderer/plugins/persona-panel.mjs",
    "!frontend-v2/renderer/plugins/lifecycle-panel.mjs",
    "!frontend-v2/renderer/plugins/lifecycle-side-block.mjs",
    "lib/**/*",
    // three 主模块由依赖收集器打包；保留显式规则并由最终 app.asar 硬门复核。
    "node_modules/three/build/**/*",
    // electron-builder 26.15.3 的 NodeModuleCopyHelper 默认排除 node_modules
    // 顶层 examples。独立 FileSet 绕过该默认忽略，同时只复制实际模块闭包，
    // 避免 onNodeModuleFile 放行整个 three/examples（约 25 MB）。
    {
      from: path.join(appRoot, "node_modules", "three", "examples", "jsm"),
      to: "node_modules/three/examples/jsm",
      filter: [
        "loaders/GLTFLoader.js",
        "utils/BufferGeometryUtils.js",
        "controls/OrbitControls.js",
      ],
    },
    "scripts/update-transaction.ps1",
    "*.html",
    "*.wav",
    "!**/__pycache__/**",
    "!**/.pytest_cache/**",
    "!**/.omni_audit/**",
    "!**/.omni_backups/**",
    "!**/.tiangong/**",
    "!**/browser_snapshots/**",
    "!**/*.log",
    "!**/*.lock",
    "!**/*.tmp",
    "!**/*.bak*",
    "!**/desktop_renderer.jsonl",
  ],
  extraResources: nativeResources(),
  afterPack: finalizePackagedReleaseBinding,
  extraMetadata: {
    author: { name: "于泳翔" },
    developer: "于泳翔",
  },
  win: {
    target: [{ target: "nsis", arch: ["x64"] }],
    icon: path.join(appRoot, "assets", "tiangong-logo.ico"),
    executableName: "天工造物 v3.0.3 完整版",
    legalTrademarks: "天工造物",
    signtoolOptions: {
      publisherName: "于泳翔",
    },
  },
  nsis: {
    guid: "8a691210-edfd-57d2-ac97-15799e433fcd",
    oneClick: false,
    perMachine: true,
    allowElevation: true,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: "always",
    createStartMenuShortcut: true,
    menuCategory: "天工造物",
    shortcutName: "天工造物 v3.0.3 完整版",
    uninstallDisplayName: "天工造物 v3.0.3 完整版",
    deleteAppDataOnUninstall: false,
    runAfterFinish: true,
    unicode: true,
    warningsAsErrors: true,
    differentialPackage: true,
    installerLanguages: ["zh_CN"],
    installerIcon: path.join(appRoot, "assets", "tiangong-logo.ico"),
    uninstallerIcon: path.join(appRoot, "assets", "tiangong-logo.ico"),
    installerHeaderIcon: path.join(appRoot, "assets", "tiangong-logo.ico"),
    include: path.join(buildRoot, "installer.nsh"),
  },
  mac: {
    target: ["dmg", "zip"],
    icon: path.join(appRoot, "assets", "tiangong-logo.icns"),
    category: "public.app-category.productivity",
    hardenedRuntime: true,
    gatekeeperAssess: false,
    entitlements: path.join(buildRoot, "entitlements.mac.plist"),
    entitlementsInherit: path.join(buildRoot, "entitlements.mac.plist"),
    identity: requireSigning ? undefined : null,
    notarize: requireSigning,
    extendInfo: {
      CFBundleDisplayName: "天工造物 v3.0.3 完整版",
      NSHumanReadableCopyright: "Copyright © 2026 于泳翔",
    },
  },
  dmg: {
    title: "天工造物 v3.0.3 完整版 ${version}",
    icon: path.join(appRoot, "assets", "tiangong-logo.icns"),
    iconSize: 128,
    window: { width: 600, height: 420 },
    contents: [
      { x: 170, y: 210, type: "file" },
      { x: 430, y: 210, type: "link", path: "/Applications" },
    ],
  },
};
