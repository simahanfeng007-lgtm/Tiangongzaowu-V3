from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT_MODULE = ROOT / "app" / "runtime-root.js"


class WindowsFirstRunMatrixTests(unittest.TestCase):
    def _resolve(self, payload: dict[str, str]) -> dict[str, object]:
        script = f"""
          const {{ resolveWritableRuntimeRoot }} = require({json.dumps(str(RUNTIME_ROOT_MODULE))});
          try {{
            process.stdout.write(JSON.stringify({{ ok: true, ...resolveWritableRuntimeRoot({json.dumps(payload)}) }}));
          }} catch (error) {{
            process.stdout.write(JSON.stringify({{ ok: false, code: error.code || "", message: error.message }}));
          }}
        """
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        return json.loads(completed.stdout)

    def test_clean_chinese_space_profile_uses_the_requested_writable_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-first-run-") as temporary:
            base = Path(temporary)
            explicit = base / "新 用户" / "天工 运行数据"
            result = self._resolve(
                {
                    "explicitRoot": str(explicit),
                    "userData": str(base / "User Data"),
                    "appData": str(base / "Roaming"),
                    "tempRoot": str(base / "Temp"),
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(Path(str(result["root"])).resolve(), explicit.resolve())
            self.assertEqual(result["rejected"], [])
            self.assertFalse(list(explicit.glob(".tiangong-write-probe-*")))

    def test_stale_runtime_override_that_is_a_file_falls_back_to_user_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-stale-root-") as temporary:
            base = Path(temporary)
            stale = base / "旧版运行目录"
            stale.write_text("not a directory", encoding="utf-8")
            user_data = base / "User Data"
            result = self._resolve(
                {
                    "explicitRoot": str(stale),
                    "userData": str(user_data),
                    "appData": str(base / "Roaming"),
                    "tempRoot": str(base / "Temp"),
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(Path(str(result["root"])).resolve(), (user_data / "runtime").resolve())
            self.assertEqual(len(result["rejected"]), 1)
            self.assertEqual(Path(result["rejected"][0]["path"]), stale)

    def test_relative_or_volume_root_override_is_never_accepted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-invalid-root-") as temporary:
            base = Path(temporary)
            for invalid in ("relative-runtime", Path(base.anchor)):
                with self.subTest(invalid=str(invalid)):
                    user_data = base / ("profile-" + str(len(str(invalid))))
                    result = self._resolve(
                        {
                            "explicitRoot": str(invalid),
                            "userData": str(user_data),
                            "appData": str(base / "Roaming"),
                            "tempRoot": str(base / "Temp"),
                        }
                    )
                    self.assertTrue(result["ok"])
                    self.assertEqual(
                        Path(str(result["root"])).resolve(),
                        (user_data / "runtime").resolve(),
                    )
                    self.assertTrue(result["rejected"])

    def test_long_unicode_profile_path_and_repeated_first_start_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-long-profile-") as temporary:
            base = Path(temporary)
            explicit = base / ("中文 空格-" + "x" * 80) / ("二级-" + "y" * 80)
            payload = {
                "explicitRoot": str(explicit),
                "userData": str(base / "User Data"),
                "appData": str(base / "Roaming"),
                "tempRoot": str(base / "Temp"),
            }
            first = self._resolve(payload)
            second = self._resolve(payload)
            self.assertTrue(first["ok"] and second["ok"])
            self.assertEqual(first["root"], second["root"])
            self.assertFalse(list(explicit.glob(".tiangong-write-probe-*")))

    def test_desktop_source_has_fail_closed_security_and_upgrade_guards(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        preload = (ROOT / "app" / "preload.js").read_text(encoding="utf-8")
        self.assertIn("resolveWritableRuntimeRoot", main)
        self.assertIn("function bindRuntimeKnownFolders()", main)
        self.assertIn("function knownFolderPath(name, envName", main)
        self.assertIn('["TIANGONG_DOCUMENTS_PATH", "documents"]', main)
        self.assertIn('"天工造物生命数据"', main)
        self.assertNotIn(
            'path.join(String(process.env.USERPROFILE || process.env.HOME || ""), "Documents"',
            main,
        )
        self.assertNotIn("candidates.push(process.env.TIANGONG_HOME_PATH || \"\", process.cwd())", main)
        self.assertIn('optionalAppPath("appData")', main)
        self.assertIn('optionalAppPath("temp")', main)
        self.assertIn('onTrusted("gateway:getBootstrap"', main)
        self.assertIn('ipcRenderer.sendSync("gateway:getBootstrap")', preload)
        builder = (ROOT / "electron-builder.config.cjs").read_text(encoding="utf-8")
        build_info = json.loads((ROOT / "app" / "build-info.json").read_text(encoding="utf-8"))
        backend_release = json.loads(
            (ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "release.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("payload?.build_id === EXPECTED_BACKEND_BUILD_ID", main)
        self.assertEqual(build_info["backend_build_id"], backend_release["build_id"])
        self.assertIn('"runtime-root.js"', builder)
        self.assertIn("os_credential_encryption_unavailable", main)
        self.assertIn('getModelSettings: () => ipcRenderer.invoke("model:getSettings")', preload)
        self.assertIn('setModelSettings: (payload) => ipcRenderer.invoke("model:setSettings", payload || {})', preload)
        self.assertGreaterEqual(main.count('PYTHONDONTWRITEBYTECODE: "1"'), 4)
        first_run = (ROOT / "scripts" / "qa-packaged-first-run.ps1").read_text(encoding="utf-8")
        self.assertIn("mutated the installation payload with Python caches", first_run)


if __name__ == "__main__":
    unittest.main()
