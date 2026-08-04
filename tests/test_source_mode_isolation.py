from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SourceModeIsolationTests(unittest.TestCase):
    def test_source_launcher_binds_all_mutable_roots_before_electron(self) -> None:
        script = (ROOT / "scripts" / "start-source.ps1").read_text(encoding="utf-8")
        required = (
            "TIANGONG_SOURCE_MODE",
            "TIANGONG_SOURCE_PROFILE_ROOT",
            "TIANGONG_SOURCE_USER_DATA",
            "TIANGONG_DESKTOP_RUNTIME_ROOT",
            "TIANGONG_DESKTOP_STATE_DIR",
            "TIANGONG_RUN_STATE_DIR",
            "TIANGONG_V3_STATE_DIR",
            "TIANGONG_DESKTOP_WORKSPACE_ROOT",
            "TIANGONG_WORKSPACE_ROOT",
            "TIANGONG_FORCE_WORKSPACE_ROOT",
            "TIANGONG_OMNI_BODY_WORKSPACE",
            "TIANGONG_HOME_PATH",
            "TIANGONG_LIFE_DATA_ROOT",
            "TIANGONG_LIFE_RUNTIME_ROOT",
            "TIANGONG_LIFE_KERNEL_ROOT",
            "TIANGONG_LIFE_ROOT",
            "TIANGONG_EXECUTION_RUNTIME_ROOT",
            "TIANGONG_EXECUTION_LIFE_ROOT",
        )
        for name in required:
            with self.subTest(name=name):
                self.assertIn(f"$env:{name}", script)
        self.assertIn('Join-Path $HostLocalAppData "TiangongV3-SourceWork"', script)
        self.assertIn('"--user-data-dir=$SourceUserData"', script)
        self.assertLess(
            script.index('"--user-data-dir=$SourceUserData"'),
            script.index('$ElectronArgs += "."'),
        )

    def test_source_launcher_preserves_real_known_folders_and_fails_closed_on_7184(self) -> None:
        script = (ROOT / "scripts" / "start-source.ps1").read_text(encoding="utf-8")
        for name in (
            "TIANGONG_DESKTOP_PATH",
            "TIANGONG_DOWNLOADS_PATH",
            "TIANGONG_DOCUMENTS_PATH",
            "TIANGONG_PICTURES_PATH",
            "TIANGONG_MUSIC_PATH",
            "TIANGONG_VIDEOS_PATH",
        ):
            self.assertIn(f"$env:{name}", script)
        self.assertIn("Get-NetTCPConnection -State Listen -LocalPort 7184", script)
        self.assertIn("Source mode will not adopt or stop it", script)
        self.assertNotIn("Stop-Process", script)

    def test_main_process_isolates_before_portable_mode_and_single_instance_lock(self) -> None:
        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        configured = main.index("configureSourceIsolation();")
        portable = main.index("const portableExecutableDir")
        lock = main.index("app.requestSingleInstanceLock()")
        self.assertLess(configured, portable)
        self.assertLess(configured, lock)
        self.assertIn("const SOURCE_MODE = app.isPackaged === false;", main)
        self.assertIn('app.setPath("userData", userData);', main)
        self.assertIn('app.setName(SOURCE_PRODUCT_LABEL);', main)
        self.assertIn('"com.tiangong.v3.qiyuan.source"', main)
        self.assertIn('SOURCE_MODE\n  ? ""', main.replace("\r\n", "\n"))

    def test_runtime_identity_is_dynamic_but_release_identity_is_unchanged(self) -> None:
        package = json.loads((ROOT / "app" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["productName"], "天工造物 v3.0.3 完整版")
        self.assertEqual(
            package["tiangongRelease"]["canonicalAppId"],
            "com.tiangong.v3.qiyuan",
        )

        main = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        preload = (ROOT / "app" / "preload.js").read_text(encoding="utf-8")
        bootstrap = (
            ROOT / "app" / "frontend-v2" / "renderer" / "bootstrap.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("productLabel: PRODUCT_LABEL", main)
        self.assertIn("sourceMode: SOURCE_MODE", main)
        self.assertIn("bootstrapMetadata.productLabel", preload)
        self.assertIn("bootstrapMetadata.sourceMode === true", preload)
        self.assertIn("document.title = `${runtimeProductLabel} · 起源`", bootstrap)
        self.assertIn('"source"', bootstrap)


if __name__ == "__main__":
    unittest.main()
