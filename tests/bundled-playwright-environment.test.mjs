import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const mainSource = fs.readFileSync(new URL("../app/main.js", import.meta.url), "utf8");
const environmentSource = mainSource.slice(
  mainSource.indexOf("function totalGatewayEnvironment("),
  mainSource.indexOf("async function waitForTotalGateway("),
);

function fixture(t, env = {}, sourceMode = false) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tiangong-browser-env-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const appRoot = path.join(root, "app");
  const resourcesRoot = path.join(root, "resources");
  const stateRoot = path.join(root, "state");
  const isDirectory = (candidate) => {
    try {
      return fs.statSync(candidate).isDirectory();
    } catch {
      return false;
    }
  };
  const context = vm.createContext({
    path,
    fs,
    process: { env: { ...env }, resourcesPath: resourcesRoot },
    app: {
      getAppPath: () => appRoot,
      getPath: (name) => path.join(root, name),
    },
    __dirname: appRoot,
    hydrateProviderApiKeys: () => {},
    writeDesktopDiagnostic: () => {},
    isDirectory,
    isFile: () => false,
    releaseManifestCandidatePaths: () => [],
    verifiedReleaseBindings: () => [],
    backendDir: () => "",
    runtimeStateRoot: () => stateRoot,
    gatewayRuntimeStateRoot: () => stateRoot,
    SOURCE_MODE: sourceMode,
    SOURCE_CHECKOUT_ID: "",
    DEFAULT_TOTAL_GATEWAY_PORT: 7184,
    SHADOW_API_TOKEN: "test-shadow",
    COMMUNICATION_GATEWAY_TOKEN: "test-communication",
    LIFE_ACTION_INTENT_TOKEN: "test-life-intent",
    BACKEND_INTERNAL_TOKEN: "test-backend",
    LIFE_INTERNAL_TOKEN: "test-life",
    ARTIFACT_OPEN_TOKEN: "test-artifact",
    TOTAL_GATEWAY_URL: "http://127.0.0.1:7184",
    EXPECTED_BACKEND_BUILD_ID: "test-build",
    BACKEND_API_CONTRACT: "test-contract",
    EXECUTION_SHADOW_AUDIT_VERSION: "test-audit",
    RELEASE_CONTRACT_VERSION: "test-release",
    EXECUTION_POLICY_VERSION: "test-policy",
    portableExecutableDir: "",
  });
  vm.runInContext(environmentSource, context);
  return {
    root,
    appRoot,
    resourcesRoot,
    environment: () => context.totalGatewayEnvironment({ cwd: root }),
    directory: (...parts) => {
      const directory = path.join(...parts);
      fs.mkdirSync(directory, { recursive: true });
      return directory;
    },
  };
}

test("packaged gateway uses Chromium beside the bundled Python runtime", (t) => {
  const f = fixture(t);
  const bundled = f.directory(f.resourcesRoot, "python", "ms-playwright");
  f.directory(f.appRoot, "runtime", "ms-playwright");
  assert.equal(f.environment().PLAYWRIGHT_BROWSERS_PATH, bundled);
});

test("source gateway uses the browser installed into python312", (t) => {
  const f = fixture(t, {}, true);
  const bundled = f.directory(f.appRoot, "runtime", "python312", "ms-playwright");
  assert.equal(f.environment().PLAYWRIGHT_BROWSERS_PATH, bundled);
});

test("an existing explicit browser override has priority over bundled directories", (t) => {
  const f = fixture(t);
  const override = f.directory(f.root, "override");
  const overridden = fixture(t, { TIANGONG_PLAYWRIGHT_BROWSERS_PATH: override });
  overridden.directory(overridden.resourcesRoot, "python", "ms-playwright");
  overridden.directory(overridden.appRoot, "runtime", "python312", "ms-playwright");
  assert.equal(overridden.environment().PLAYWRIGHT_BROWSERS_PATH, override);
});

test("a missing explicit override falls through to the packaged browser", (t) => {
  const absent = fixture(t);
  const f = fixture(t, { TIANGONG_PLAYWRIGHT_BROWSERS_PATH: path.join(absent.root, "missing") });
  const bundled = f.directory(f.resourcesRoot, "python", "ms-playwright");
  assert.equal(f.environment().PLAYWRIGHT_BROWSERS_PATH, bundled);
});

test("the previous runtime browser layout remains a fallback", (t) => {
  const f = fixture(t);
  const legacy = f.directory(f.appRoot, "runtime", "ms-playwright");
  assert.equal(f.environment().PLAYWRIGHT_BROWSERS_PATH, legacy);
});

test("without bundled directories the gateway preserves Playwright's inherited fallback", (t) => {
  const inherited = fixture(t, { PLAYWRIGHT_BROWSERS_PATH: "custom-browser-cache" });
  assert.equal(inherited.environment().PLAYWRIGHT_BROWSERS_PATH, "custom-browser-cache");
  const defaults = fixture(t);
  assert.equal(defaults.environment().PLAYWRIGHT_BROWSERS_PATH, undefined);
});
