from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "electron-builder.config.cjs"
BINDING = ROOT / "app" / "lib" / "release-binding.js"
_BUNDLED_RELEASE_PYTHON = ROOT / "app" / "runtime" / "python312" / "python.exe"
RELEASE_PYTHON = _BUNDLED_RELEASE_PYTHON if _BUNDLED_RELEASE_PYTHON.is_file() else Path(sys.executable)


class ReleaseAfterPackBindingTests(unittest.TestCase):
    def test_after_pack_binds_complete_archive_and_one_byte_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-after-pack-") as temporary:
            stage = Path(temporary).resolve()
            gateway_data = b"one-frozen-gateway-for-four-logical-components"

            staged_gateway = stage / "total-gateway" / "tiangong-total-gateway.exe"
            staged_gateway.parent.mkdir(parents=True)
            staged_gateway.write_bytes(gateway_data)
            staged_release = stage / "release" / "release-manifest.json"
            staged_release.parent.mkdir(parents=True)
            staged_release.write_text(
                '{"production_claim":false}\n',
                encoding="utf-8",
            )

            app_out = stage / "electron-builder" / "win-unpacked"
            resources = app_out / "resources"
            desktop_archive = resources / "app.asar"
            desktop_archive.parent.mkdir(parents=True)
            packaged_gateway = (
                resources / "total-gateway" / "tiangong-total-gateway.exe"
            )
            packaged_gateway.parent.mkdir(parents=True)
            packaged_gateway.write_bytes(gateway_data)

            script = f"""
              const fs = require("fs");
              const path = require("path");
              const crypto = require("crypto");
              const {{ pathToFileURL }} = require("url");
              const asar = require({json.dumps(str(ROOT / "app" / "node_modules" / "@electron" / "asar"))});
              const config = require({json.dumps(str(CONFIG))});
              const binding = require({json.dumps(str(BINDING))});
              (async () => {{
                const avatarContract = await import(pathToFileURL(
                  {json.dumps(str(ROOT / "scripts" / "verify-app-asar-avatar-contract.mjs"))}
                ).href);
                const asarSource = {json.dumps(str(stage / "asar-source"))};
                for (const relative of avatarContract.REQUIRED_AVATAR_MODULE_FILES) {{
                  const target = path.join(asarSource, ...relative.split("/"));
                  fs.mkdirSync(path.dirname(target), {{ recursive: true }});
                  fs.writeFileSync(target, `fixture:${{relative}}`);
                }}
                await asar.createPackage(
                  asarSource,
                  {json.dumps(str(desktop_archive))},
                );
                await config.afterPack({{ appOutDir: {json.dumps(str(app_out))} }});
                const staged = fs.readFileSync({json.dumps(str(staged_release))});
                const packagedPath = {json.dumps(str(resources / "release" / "release-manifest.json"))};
                const packaged = fs.readFileSync(packagedPath);
                const manifest = JSON.parse(packaged.toString("utf8"));
                const desktop = manifest.component_manifest.components
                  .find((item) => item.component_id === "tiangong-desktop");
                const beforeData = fs.readFileSync({json.dumps(str(desktop_archive))});
                const before = binding.readVerifiedReleaseBinding(packagedPath);
                fs.appendFileSync({json.dumps(str(desktop_archive))}, Buffer.from([0x00]));
                const after = binding.readVerifiedReleaseBinding(packagedPath);
                process.stdout.write(JSON.stringify({{
                  sameManifest: staged.equals(packaged),
                  production: manifest.production_claim,
                  desktop,
                  beforeBytes: beforeData.length,
                  beforeSha256: crypto.createHash("sha256").update(beforeData).digest("hex"),
                  beforeValid: Boolean(before),
                  beforeDesktopPath: before?.desktopPath || "",
                  afterValid: Boolean(after),
                }}));
              }})().catch((error) => {{
                console.error(error);
                process.exit(1);
              }});
            """
            env = os.environ.copy()
            env.update(
                {
                    "TIANGONG_RELEASE_STAGE": str(stage),
                    "TIANGONG_RELEASE_PLATFORM": "win32",
                    "TIANGONG_RELEASE_ARCH": "x64",
                    "TIANGONG_RELEASE_PYTHON": str(RELEASE_PYTHON),
                }
            )
            completed = subprocess.run(
                ["node", "-e", script],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=180,
            )
            result = json.loads(completed.stdout)

            self.assertTrue(result["sameManifest"])
            self.assertTrue(result["production"])
            self.assertEqual(result["desktop"]["executable_relative_path"], "app.asar")
            self.assertEqual(result["desktop"]["size_bytes"], result["beforeBytes"])
            self.assertEqual(
                result["desktop"]["sha256"],
                result["beforeSha256"],
            )
            self.assertTrue(result["beforeValid"])
            self.assertEqual(
                Path(result["beforeDesktopPath"]).resolve(),
                desktop_archive.resolve(),
            )
            self.assertFalse(result["afterValid"])
            self.assertFalse(any(stage.glob(".release-final-*")))


if __name__ == "__main__":
    unittest.main()
