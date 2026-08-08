import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, rmSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const NPM_FALLBACK_REGISTRY = "https://registry.npmmirror.com";
export const ELECTRON_FALLBACK_MIRROR = "https://npmmirror.com/mirrors/electron/";

const scriptRoot = dirname(fileURLToPath(import.meta.url));
const workspaceRoot = resolve(scriptRoot, "..");

function disabled() {
  return String(process.env.TIANGONG_DISABLE_DEPENDENCY_FALLBACK || "").trim() === "1";
}

function commandName(name) {
  return process.platform === "win32" ? `${name}.cmd` : name;
}

function run(command, args, { cwd, env = {} } = {}) {
  const isWindowsCommand = process.platform === "win32" && command.toLowerCase().endsWith(".cmd");
  const printable = [command, ...args].map((value) => JSON.stringify(String(value))).join(" ");
  process.stdout.write(`\n> ${printable}\n`);
  const commandLine = [command, ...args.map((value) => `"${String(value).replaceAll('"', '""')}"`)].join(" ");
  const completed = spawnSync(
    isWindowsCommand ? (process.env.ComSpec || "cmd.exe") : command,
    isWindowsCommand ? ["/d", "/s", "/c", commandLine] : args,
    {
      cwd,
      env: { ...process.env, ...env },
      stdio: "inherit",
      windowsHide: true,
      windowsVerbatimArguments: isWindowsCommand,
    },
  );
  if (completed.error) throw completed.error;
  return Number(completed.status ?? 1);
}

function retry(primary, fallback, label) {
  if (primary() === 0) return;
  if (disabled()) throw new Error(`${label} failed and dependency fallback is disabled`);
  process.stdout.write(`[dependency-fallback] ${label}: retrying with the mainland China mirror\n`);
  if (fallback() !== 0) throw new Error(`${label} failed with both primary and fallback sources`);
}

export function installNodeDependencies({
  appRoot = join(workspaceRoot, "app"),
  platform = process.platform,
  architecture = process.arch,
} = {}) {
  const packageFile = join(appRoot, "package.json");
  const lockFile = join(appRoot, "package-lock.json");
  if (!existsSync(packageFile) || !statSync(packageFile).isFile()) {
    throw new Error(`app package.json is missing: ${packageFile}`);
  }
  if (!existsSync(lockFile) || !statSync(lockFile).isFile()) {
    throw new Error(`app package-lock.json is missing: ${lockFile}`);
  }
  JSON.parse(readFileSync(packageFile, "utf8"));

  const npm = commandName("npm");
  const npmArgs = ["--prefix", appRoot, "ci", "--ignore-scripts"];
  const npmFallback = String(
    process.env.TIANGONG_NPM_FALLBACK_REGISTRY || NPM_FALLBACK_REGISTRY
  ).trim();
  retry(
    () => run(npm, npmArgs, { cwd: workspaceRoot }),
    () => run(npm, [...npmArgs, "--registry", npmFallback], {
      cwd: workspaceRoot,
      env: { npm_config_replace_registry_host: "always" },
    }),
    "npm locked dependency install",
  );

  const electronRoot = join(appRoot, "node_modules", "electron");
  const electronInstall = join(electronRoot, "install.js");
  if (!existsSync(electronInstall) || !statSync(electronInstall).isFile()) {
    throw new Error(`Electron distribution installer is missing: ${electronInstall}`);
  }
  const electronEnv = {
    ELECTRON_INSTALL_PLATFORM: platform,
    ELECTRON_INSTALL_ARCH: architecture,
  };
  const electronFallback = String(
    process.env.TIANGONG_ELECTRON_FALLBACK_MIRROR || ELECTRON_FALLBACK_MIRROR
  ).trim();
  retry(
    () => run(process.execPath, [electronInstall], { cwd: appRoot, env: electronEnv }),
    () => {
      rmSync(join(electronRoot, "dist"), { recursive: true, force: true });
      rmSync(join(electronRoot, "path.txt"), { force: true });
      return run(process.execPath, [electronInstall], {
        cwd: appRoot,
        env: { ...electronEnv, ELECTRON_MIRROR: electronFallback },
      });
    },
    "Electron distribution install",
  );

  const electronDistribution = join(
    electronRoot,
    "dist",
    platform === "win32" ? "electron.exe" : "Electron.app",
  );
  if (!existsSync(electronDistribution)) {
    throw new Error(`Electron distribution is missing after installation: ${electronDistribution}`);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const platformIndex = process.argv.indexOf("--platform");
  const archIndex = process.argv.indexOf("--arch");
  installNodeDependencies({
    platform: platformIndex >= 0 ? process.argv[platformIndex + 1] : process.platform,
    architecture: archIndex >= 0 ? process.argv[archIndex + 1] : process.arch,
  });
}
