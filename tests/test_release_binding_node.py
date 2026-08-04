from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from contracts import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "lib" / "release-binding.js"


def _write_binding(
    root: Path,
    version: str,
    generated_at_ms: int,
    *,
    tamper=False,
    tamper_desktop=False,
) -> Path:
    resources = root / "resources"
    rows = (
        ("tiangong-backend", "backend/tiangong-backend/tiangong-backend.exe"),
        (
            "tiangong-communication-service",
            "communication-service/tiangong-communication-service.exe",
        ),
        (
            "tiangong-life-service",
            "life-service/runtime314/tiangong-life-service-runtime.exe",
        ),
        ("tiangong-total-gateway", "total-gateway/tiangong-total-gateway.exe"),
    )
    desktop_data = f"complete-app-asar:{version}:{generated_at_ms}".encode()
    desktop_path = resources / "app.asar"
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.write_bytes(desktop_data)
    components = [
        {
            "component_id": "tiangong-desktop",
            "version": version,
            "build_id": f"desktop-{version}",
            "executable_relative_path": "app.asar",
            "sha256": hashlib.sha256(desktop_data).hexdigest(),
            "size_bytes": len(desktop_data),
        }
    ]
    for component_id, relative in rows:
        data = f"{component_id}:{version}:{generated_at_ms}".encode()
        target = resources.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        components.append(
            {
                "component_id": component_id,
                "version": version,
                "build_id": f"{component_id}-{version}",
                "executable_relative_path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    component_manifest = {
        "production_claim": True,
        "product_version": version,
        "generated_at_ms": generated_at_ms,
        "components": components,
    }
    component_manifest["manifest_sha256"] = canonical_sha256(component_manifest)
    release = {
        "release_schema": "tiangong.release-manifest.v1",
        "release_channel": "stable",
        "production_claim": True,
        "product_version": version,
        "generated_at_ms": generated_at_ms,
        "component_manifest": component_manifest,
    }
    release["release_manifest_sha256"] = canonical_sha256(release)
    manifest = resources / "release" / "release-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(
        (
            json.dumps(release, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    )
    if tamper:
        resources.joinpath(*rows[0][1].split("/")).write_bytes(b"tampered")
    if tamper_desktop:
        desktop_path.write_bytes(desktop_data + b"-tampered")
    return manifest


class ReleaseBindingNodeTests(unittest.TestCase):
    def test_verified_whole_release_binding_uses_semver_then_generation_time(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-release-binding-") as temporary:
            base = Path(temporary)
            paths = [
                _write_binding(base / "old", "3.9.99", 90_000),
                _write_binding(base / "prerelease", "3.10.0-rc.1", 80_000),
                _write_binding(base / "stable-old", "3.10.0", 10_000),
                _write_binding(base / "stable-new", "3.10.0+build.7", 20_000),
                _write_binding(base / "tampered", "99.0.0", 100_000, tamper=True),
                _write_binding(
                    base / "tampered-desktop",
                    "100.0.0",
                    110_000,
                    tamper_desktop=True,
                ),
            ]
            script = f"""
              const binding = require({json.dumps(str(MODULE))});
              const rows = binding.discoverVerifiedReleaseBindings({json.dumps([str(p) for p in paths])});
              process.stdout.write(JSON.stringify({{
                versions: rows.map((row) => row.productVersion),
                generated: rows.map((row) => row.generatedAtMs),
                selectedBackend: rows[0]?.componentPaths?.['tiangong-backend'] || '',
                selectedDesktop: rows[0]?.desktopPath || '',
                malformed: binding.parseSemver('3.10.0-01'),
              }}));
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
            result = json.loads(completed.stdout)
            self.assertEqual(
                result["versions"],
                ["3.10.0+build.7", "3.10.0", "3.10.0-rc.1", "3.9.99"],
            )
            self.assertEqual(result["generated"][:2], [20_000, 10_000])
            self.assertIn("stable-new", result["selectedBackend"])
            self.assertIn("stable-new", result["selectedDesktop"])
            self.assertIsNone(result["malformed"])

    def test_desktop_uses_one_verified_release_for_all_native_components(self) -> None:
        source = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        self.assertIn("discoverVerifiedReleaseBindings", source)
        self.assertIn("binding.productVersion === productVersion", source)
        self.assertIn("binding.desktopBuildId === desktopBuildId", source)
        self.assertIn("binding.desktopPath === runningDesktopPath", source)
        for component_id in (
            "tiangong-backend",
            "tiangong-communication-service",
            "tiangong-life-service",
            "tiangong-total-gateway",
        ):
            self.assertIn(f'boundComponentExecutable("{component_id}")', source)


if __name__ == "__main__":
    unittest.main()
