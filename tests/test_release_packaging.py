from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackagingTests(unittest.TestCase):
    def test_desktop_identity_and_release_commands_are_pinned(self) -> None:
        package = json.loads((ROOT / "app/package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["productName"], "天工造物 v3.0.3 完整版")
        self.assertEqual(package["author"]["name"], "于泳翔")
        self.assertEqual(package["devDependencies"]["electron"], "43.1.1")
        self.assertEqual(package["devDependencies"]["electron-builder"], "26.15.3")
        self.assertEqual(package["scripts"]["release:win"], "node ../scripts/release-win.mjs")

    def test_packaging_uses_current_gateway_sources_and_qr_probe(self) -> None:
        config = (ROOT / "electron-builder.config.cjs").read_text(encoding="utf-8")
        release = (ROOT / "scripts/release-common.mjs").read_text(encoding="utf-8")
        probe = (ROOT / "scripts/frozen_total_gateway_entry.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('path.join(runtimeRoot, "total-gateway")', config)
        self.assertNotIn('path.join(stageRoot, "backend")', config)
        self.assertNotIn('path.join(stageRoot, "life-service")', config)
        self.assertNotIn('path.join(stageRoot, "communication-service")', config)
        self.assertIn('"--paths", join(workspaceRoot, "src")', release)
        self.assertIn('entryName: "frozen_total_gateway_entry.py"', release)
        self.assertNotIn('entryName: "frozen_backend_entry.py"', release)
        self.assertNotIn('entryName: "frozen_life_service_entry.py"', release)
        self.assertIn('outputRelativePath: "total-gateway"', release)
        self.assertIn('sourceOverlayTarget: "total-gateway/backend/tiangong-backend"', release)
        self.assertIn('totalProbe.runtime_api_contract !== "tiangong.desktop.backend.v3"', release)
        self.assertIn('totalProbe.life_api_contract !== "tiangong.life.api.v2"', release)
        self.assertIn('totalProbe.communication_api_contract !== "tiangong.communication.api.v1"', release)
        self.assertIn("totalProbe.wechat_qr !== true", release)
        self.assertIn("verifyPackagedWindowsRelease", release)
        self.assertIn("finalizeWindowsStage", release)
        self.assertIn("dead frontend module leaked into app.asar", release)
        for excluded in (
            '"!frontend-v2/renderer/plugins/persona-panel.mjs"',
            '"!frontend-v2/renderer/plugins/lifecycle-panel.mjs"',
            '"!frontend-v2/renderer/plugins/lifecycle-side-block.mjs"',
            '"!assets/avatars/imported/*.vrm"',
        ):
            self.assertIn(excluded, config)
        self.assertIn(
            'from: path.join(appRoot, "node_modules", "three", "examples", "jsm")',
            config,
        )
        for required_three_module in (
            '"loaders/GLTFLoader.js"',
            '"utils/BufferGeometryUtils.js"',
            '"controls/OrbitControls.js"',
        ):
            self.assertIn(required_three_module, config)
        self.assertNotIn("disableDefaultIgnoredFiles: true", config)
        self.assertIn('const productVersion = String(appPackage.version', release)
        self.assertIn('const productName = String(appPackage.productName', release)
        self.assertIn('const developerName = String(appPackage.author?.name', release)
        self.assertIn("product: productName", release)
        self.assertIn("developer: developerName", release)
        self.assertIn('join(workspaceRoot, "release-artifacts", productVersion)', release)
        self.assertIn("TIANGONG_RELEASE_ARTIFACTS_ROOT", release)
        self.assertIn('for (const candidate of ["pwsh.exe", "powershell.exe"])', release)
        self.assertIn("windowsPowerShellCommand()", release)
        self.assertIn("version: productVersion", release)
        self.assertNotIn('join(workspaceRoot, "release-artifacts", "3.0.0")', release)
        for required in (
            '"secure-updater.js"',
            '"update-trust.json"',
            '"vrc-import.js"',
            '"scripts/update-transaction.ps1"',
        ):
            self.assertIn(required, config)
        self.assertIn('resource(path.join(appRoot, "runtime", "python312"), "python", ["!Scripts/**"])', config)
        self.assertIn('"audit-portable-paths.py"', release)
        self.assertIn("publisher-bound Python Scripts launchers leaked", release)
        self.assertIn('join(appRoot, "runtime", "python312", "python.exe")', release)
        self.assertIn("Windows frozen release requires CPython 3.12", release)
        self.assertIn("run(releasePython, args", release)
        self.assertIn(
            'const preflightPython = process.platform === "win32" ? releasePython : "python"',
            release,
        )
        self.assertIn('run(preflightPython, [join(workspaceRoot, "scripts", "audit-portable-paths.py")', release)
        self.assertIn('run(preflightPython, [join(workspaceRoot, "scripts", "sync-generated-sources.py")', release)
        self.assertIn("function releaseStageRoot(platform, architecture)", release)
        self.assertIn('process.env.TIANGONG_RELEASE_STAGE', release)
        self.assertIn('join(localRoot, "TiangongV3Release", workspaceId', release)
        verifier = (ROOT / "scripts/verify-windows-artifacts.ps1").read_text(encoding="utf-8")
        self.assertIn("$Package.version", verifier)
        self.assertIn("$Package.productName", verifier)
        self.assertIn("$Package.author.name", verifier)
        self.assertIn("release-artifacts\\{0}\\win32-x64", verifier)
        self.assertIn('"deployment_mode": "embedded"', probe)
        self.assertIn('"listener_port": 7184', probe)
        self.assertIn('"life_api_contract": LIFE_API_CONTRACT', probe)
        self.assertIn('"communication_api_contract": "tiangong.communication.api.v1"', probe)
        self.assertIn(
            "from life_service.identity_migration import migrate_legacy_identities",
            probe,
        )
        self.assertNotIn("migrate_legacy_identity\n", probe)
        self.assertIn('"wechat_qr": WECHAT_ILINK_ORIGIN.startswith', probe)

    def test_asar_and_nsis_compression_have_separate_integrity_gates(self) -> None:
        config = (ROOT / "electron-builder.config.cjs").read_text(encoding="utf-8")
        release = (ROOT / "scripts/release-common.mjs").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify-windows-artifacts.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("asar: true", config)
        self.assertIn('compression: "maximum"', config)
        self.assertIn("afterPack: finalizePackagedReleaseBinding", config)
        self.assertIn("verify-app-asar-avatar-contract.mjs", config)
        self.assertIn("packaged avatar ASAR contract failed", config)
        self.assertIn('"--desktop-archive", desktopArchive', config)
        self.assertIn('executable_relative_path !== "app.asar"', release)
        self.assertIn("bytes = readFileSync(asarPath)", release)
        self.assertIn("spawned/native runtime leaked into read-only app.asar", release)
        self.assertIn("app.asar changed critical source bytes", release)
        self.assertIn("verifyAppAsarAvatarContract", release)
        self.assertIn("app_asar_avatar_module_closure", release)
        self.assertIn("app_asar_forbidden_avatar_assets_absent", release)
        self.assertIn("sandboxed preload has unsupported imports", release)
        self.assertIn("contains a symbolic link or junction", release)
        self.assertIn("assertNoPublisherPathLeak", release)
        self.assertIn("contains a publisher-machine path", release)
        self.assertIn("Installer payload extraction failed", verifier)
        self.assertIn("changed during NSIS compression", verifier)
        self.assertIn("Desktop component must bind the complete app.asar", verifier)
        self.assertIn("app.asar changed between the unpacked build and NSIS payload", verifier)
        self.assertIn("A5 confirmation overlay was lost during NSIS compression", verifier)
        self.assertIn("verify-app-asar-avatar-contract.mjs", verifier)
        self.assertIn("NsisAvatarModuleClosure", verifier)
        self.assertIn("NsisForbiddenAvatarAssetsAbsent", verifier)
        self.assertIn('"verify-windows-artifacts.ps1"', release)
        self.assertIn('"-ArtifactRoot", artifactRoot', release)
        self.assertIn("TIANGONG_RELEASE_STAGE: stageRoot", release)
        self.assertIn('"v3\\permission_settings.py"', verifier)
        self.assertIn(
            '"_internal\\frozen_modules\\v3\\execution_kernel\\confirmation_bridge.py"',
            verifier,
        )
        self.assertNotIn('"execution_kernel\\runtime.py"', verifier)
        for residue in (
            ".omni_audit",
            ".omni_backups",
            ".tiangong",
            "browser_snapshots",
            "desktop_renderer.jsonl",
        ):
            self.assertIn(residue, config)
            self.assertIn(residue, release)
            self.assertIn(residue, verifier)

    def test_app_asar_avatar_contract_is_fail_closed(self) -> None:
        verifier_url = (ROOT / "scripts/verify-app-asar-avatar-contract.mjs").as_uri()
        script = f"""
import {{
  FORBIDDEN_BUNDLED_AVATAR_ASSETS,
  REQUIRED_AVATAR_MODULE_FILES,
  verifyAppAsarAvatarContract,
}} from {json.dumps(verifier_url)};

const entries = [...REQUIRED_AVATAR_MODULE_FILES];
const completeAsar = {{
  listPackage: () => entries.map((entry) => `/${{entry}}`),
  extractFile: (_archive, entry) => Buffer.from(String(entry), "utf8"),
}};
const complete = verifyAppAsarAvatarContract("fixture.asar", {{ asar: completeAsar }});

let missingMessage = "";
try {{
  verifyAppAsarAvatarContract("fixture.asar", {{
    asar: {{
      ...completeAsar,
      listPackage: () => entries
        .filter((entry) => !entry.endsWith("GLTFLoader.js"))
        .map((entry) => `/${{entry}}`),
    }},
  }});
}} catch (error) {{
  missingMessage = String(error.message || error);
}}

let emptyMessage = "";
try {{
  verifyAppAsarAvatarContract("fixture.asar", {{
    asar: {{
      ...completeAsar,
      extractFile: (_archive, entry) =>
        String(entry).includes("OrbitControls.js")
          ? Buffer.alloc(0)
          : Buffer.from(String(entry), "utf8"),
    }},
  }});
}} catch (error) {{
  emptyMessage = String(error.message || error);
}}

let forbiddenMessage = "";
try {{
  verifyAppAsarAvatarContract("fixture.asar", {{
    asar: {{
      ...completeAsar,
      listPackage: () => [...entries, ...FORBIDDEN_BUNDLED_AVATAR_ASSETS]
        .map((entry) => `/${{entry}}`),
    }},
  }});
}} catch (error) {{
  forbiddenMessage = String(error.message || error);
}}

console.log(JSON.stringify({{
  complete,
  missingMessage,
  emptyMessage,
  forbiddenMessage,
}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["complete"]["requiredModuleCount"], 11)
        self.assertEqual(payload["complete"]["forbiddenAssetCount"], 2)
        self.assertIn("GLTFLoader.js", payload["missingMessage"])
        self.assertIn("OrbitControls.js", payload["emptyMessage"])
        self.assertIn("天工造物z1.vrm", payload["forbiddenMessage"])

    def test_source_check_prunes_binary_archives_before_recursing(self) -> None:
        check = (ROOT / "scripts/check.ps1").read_text(encoding="utf-8")
        self.assertIn('$skipNames = @(".git", "node_modules", ".pytest_cache", ".ruff_cache", "__pycache__")', check)
        self.assertIn('Get-ChildItem -LiteralPath $current -Force -ErrorAction SilentlyContinue', check)
        self.assertIn('(Join-Path $Root "release-stage")', check)
        self.assertIn('(Join-Path $Root "release-artifacts")', check)
        self.assertIn('(Join-Path $Root "release-repair")', check)
        self.assertIn('(Join-Path $Root "app\\runtime")', check)
        self.assertIn("$SourceRoot, $BackendRoot, $ReadableSourceRoot", check)
        self.assertIn("runtime\\python312\\python.exe", check)
        self.assertIn("-m pytest -q --maxfail=1", check)
        self.assertIn('Get-ChildItem -LiteralPath $TestsRoot -Filter "*.test.mjs"', check)
        self.assertIn("node --test @NodeTests", check)
        self.assertIn('"sync-generated-sources.py") --check', check)
        self.assertNotIn("python -m unittest discover", check)
        self.assertNotIn(
            "Get-ChildItem -LiteralPath $Root -Recurse -File -Force", check
        )

    def test_mac_release_is_fail_closed(self) -> None:
        release = (ROOT / "scripts/release-common.mjs").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/release-desktop.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('process.platform !== "darwin"', release)
        self.assertIn('entryName: "frozen_total_gateway_entry.py"', release)
        self.assertIn('sourceOverlayTarget: "total-gateway/backend/tiangong-backend"', release)
        self.assertIn('totalProbe.deployment_mode !== "embedded"', release)
        self.assertNotIn("TIANGONG_MAC_RUNTIME_READY == 'true'", workflow)
        self.assertNotIn("MACOS_NATIVE_RUNTIME_SHA256", workflow)
        self.assertNotIn("native macOS backend and life runtimes", workflow)


if __name__ == "__main__":
    unittest.main()
