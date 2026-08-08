from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from contracts import ReleaseManifest, contract_schema_bundle_sha256
from total_gateway.release_manifest import (
    RELEASE_MANIFEST_FILENAME,
    ReleaseManifestError,
    _release_version_key,
    generate_production_release_manifest,
    generate_release_manifest,
    release_manifest_bytes,
    select_latest_release_manifest,
    verify_release_manifest_file,
    write_production_release_manifest,
    write_release_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


class ReleaseManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = generate_release_manifest(ROOT)

    def test_single_manifest_binds_components_contracts_actions_and_skills(self) -> None:
        manifest = self.manifest
        self.assertTrue(manifest.has_valid_release_manifest_sha256())
        self.assertTrue(manifest.component_manifest.has_valid_manifest_sha256())
        self.assertFalse(manifest.production_claim)
        self.assertEqual(manifest.release_channel, "development")
        self.assertEqual(manifest.contract_schema_bundle_sha256, contract_schema_bundle_sha256())
        self.assertEqual(
            manifest.action_registry_sha256,
            "c915c5c33292e632ccde754753167fc24854d9e304912469877a1634b39af78d",
        )
        self.assertEqual(
            manifest.capability_manifest_sha256,
            "3dbf2a2a67267f0c1cdf3c222a7ac8b2ec0ca21aa1d5c06b058167d4de29f1f6",
        )
        self.assertEqual(
            manifest.skill_index_sha256,
            "181c065471265728f7a55cdce28c2043ff0bf7d12ffa9c9dc00d577b24f1bc45",
        )
        self.assertEqual(
            manifest.skill_catalog_sha256,
            "fec4b0709945b614edce5b80aa1a69381ba66b0df85f4bf8f253eb47127d5b35",
        )
        self.assertEqual(
            [item.component_id for item in manifest.component_manifest.components],
            [
                "tiangong-backend",
                "tiangong-communication-service",
                "tiangong-desktop",
                "tiangong-life-service",
                "tiangong-total-gateway",
            ],
        )
        self.assertEqual(
            [item.tree_id for item in manifest.source_trees],
            ["communication-source", "desktop-source", "gateway-source", "life-source"],
        )
        self.assertTrue(all(item.file_count > 0 for item in manifest.source_trees))

    def test_generation_is_deterministic_and_output_contains_exactly_one_file(self) -> None:
        self.assertEqual(
            release_manifest_bytes(self.manifest),
            release_manifest_bytes(generate_release_manifest(ROOT)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release"
            written = write_release_manifest(output, ROOT)
            self.assertEqual(written, self.manifest)
            self.assertEqual(
                [item.name for item in output.iterdir()],
                [RELEASE_MANIFEST_FILENAME],
            )
            verified = verify_release_manifest_file(
                output / RELEASE_MANIFEST_FILENAME,
                ROOT,
            )
            self.assertEqual(verified, self.manifest)

            dirty = Path(temporary) / "dirty"
            dirty.mkdir()
            sentinel = dirty / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_release_manifest(dirty, ROOT)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_tampering_and_source_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / RELEASE_MANIFEST_FILENAME
            payload = self.manifest.model_dump(mode="json")
            payload["release_manifest_sha256"] = "0" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ReleaseManifestError,
                "self digest is invalid",
            ):
                verify_release_manifest_file(path)

            changed_inputs = tuple(
                item.model_copy(update={"sha256": "0" * 64})
                if item.input_id == "source-snapshot"
                else item
                for item in self.manifest.inputs
            )
            drifted = self.manifest.model_copy(
                update={
                    "inputs": changed_inputs,
                    "release_manifest_sha256": "0" * 64,
                }
            ).with_computed_release_manifest_sha256()
            path.write_bytes(release_manifest_bytes(drifted))
            with self.assertRaisesRegex(
                ReleaseManifestError,
                "does not match current source authority",
            ):
                verify_release_manifest_file(path, ROOT)

    def test_malformed_or_noncanonical_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / RELEASE_MANIFEST_FILENAME
            path.write_text('{"release_id":"one","release_id":"two"}', encoding="utf-8")
            with self.assertRaisesRegex(ReleaseManifestError, "duplicate JSON key"):
                verify_release_manifest_file(path)

            path.write_text('{"release_id":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ReleaseManifestError, "non-finite JSON number"):
                verify_release_manifest_file(path)

            noncanonical = json.dumps(
                self.manifest.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            path.write_text(noncanonical, encoding="utf-8")
            with self.assertRaisesRegex(ReleaseManifestError, "encoding is not canonical"):
                verify_release_manifest_file(path)

    def test_latest_verified_manifest_uses_version_then_generation_time(self) -> None:
        def revision(version: str, generated_at_ms: int) -> ReleaseManifest:
            components = tuple(
                item.model_copy(update={"version": version})
                if item.component_id == "tiangong-desktop"
                else item
                for item in self.manifest.component_manifest.components
            )
            component = self.manifest.component_manifest.model_copy(
                update={
                    "product_version": version,
                    "generated_at_ms": generated_at_ms,
                    "components": components,
                    "manifest_sha256": "0" * 64,
                }
            ).with_computed_manifest_sha256()
            return self.manifest.model_copy(
                update={
                    "product_version": version,
                    "generated_at_ms": generated_at_ms,
                    "component_manifest": component,
                    "release_manifest_sha256": "0" * 64,
                }
            ).with_computed_release_manifest_sha256()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older_version = revision("3.0.9", self.manifest.generated_at_ms + 20_000)
            older_build = revision("3.1.0", self.manifest.generated_at_ms + 10_000)
            latest_build = revision("3.1.0", self.manifest.generated_at_ms + 30_000)
            paths = []
            for index, manifest in enumerate((older_version, older_build, latest_build)):
                path = root / f"release-{index}.json"
                path.write_bytes(release_manifest_bytes(manifest))
                paths.append(path)
            invalid = root / "release-invalid.json"
            invalid.write_text('{"product_version":"99.0.0"}', encoding="utf-8")

            selected = select_latest_release_manifest([invalid, *paths, paths[0]])

            self.assertEqual(selected.product_version, "3.1.0")
            self.assertEqual(selected.generated_at_ms, latest_build.generated_at_ms)

    def test_semver_order_is_numeric_stable_and_rejects_malformed_prereleases(self) -> None:
        ordered = [
            "3.9.99",
            "3.10.0-alpha.2",
            "3.10.0-alpha.10",
            "3.10.0-rc.1",
            "3.10.0",
        ]
        self.assertEqual(sorted(reversed(ordered), key=_release_version_key), ordered)
        self.assertEqual(
            _release_version_key("3.10.0"),
            _release_version_key("3.10.0+build.7"),
        )
        self.assertEqual(_release_version_key("3.10.0-01")[0], 0)
        self.assertEqual(_release_version_key("3.10.0-alpha..1")[0], 0)

    def test_component_or_authority_cross_binding_cannot_validate(self) -> None:
        payload = self.manifest.model_dump(mode="json")
        payload["capability_manifest_sha256"] = "0" * 64
        with self.assertRaises(ValidationError):
            ReleaseManifest.model_validate_json(json.dumps(payload), strict=True)

    def test_build_script_emits_the_single_release_authority(self) -> None:
        script = (ROOT / "scripts/build.ps1").read_text(encoding="utf-8")
        self.assertIn('$ReleaseRoot = Join-Path $OutRoot "release"', script)
        self.assertIn(
            "python -m total_gateway.release_manifest --workspace $Root --output $ReleaseRoot",
            script,
        )
        self.assertNotIn("component-manifest.json", script)

    def test_production_manifest_binds_frozen_runtime_executables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            artifacts = {
                "total-gateway/tiangong-total-gateway.exe": b"single-process-release-runtime",
            }
            for relative_path, content in artifacts.items():
                path = runtime / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            gateway = runtime / "total-gateway/tiangong-total-gateway.exe"
            desktop_archive = runtime / "electron-builder/win-unpacked/resources/app.asar"
            desktop_archive.parent.mkdir(parents=True, exist_ok=True)
            desktop_archive.write_bytes(b"complete-desktop-archive")

            manifest = generate_production_release_manifest(
                ROOT,
                runtime,
                platform_name="win32",
                architecture="x64",
                desktop_archive_path=desktop_archive,
                generated_at_ms=self.manifest.generated_at_ms + 1_000,
            )

            self.assertTrue(manifest.production_claim)
            self.assertEqual(manifest.release_channel, "stable")
            self.assertTrue(manifest.has_valid_release_manifest_sha256())
            self.assertTrue(manifest.component_manifest.production_claim)
            self.assertTrue(manifest.component_manifest.has_valid_manifest_sha256())
            self.assertEqual(manifest.generated_at_ms, self.manifest.generated_at_ms + 1_000)
            by_id = {
                item.component_id: item
                for item in manifest.component_manifest.components
            }
            runtime_components = {
                component_id: by_id[component_id]
                for component_id in (
                    "tiangong-backend",
                    "tiangong-communication-service",
                    "tiangong-life-service",
                    "tiangong-total-gateway",
                )
            }
            self.assertEqual(
                {item.executable_relative_path for item in runtime_components.values()},
                {"total-gateway/tiangong-total-gateway.exe"},
            )
            self.assertEqual(
                {item.sha256 for item in runtime_components.values()},
                {hashlib.sha256(gateway.read_bytes()).hexdigest()},
            )
            self.assertEqual(by_id["tiangong-backend"].ports, ())
            self.assertEqual(by_id["tiangong-life-service"].ports, ())
            self.assertEqual(by_id["tiangong-communication-service"].ports, ())
            self.assertEqual(by_id["tiangong-total-gateway"].ports, (7184,))
            self.assertEqual(
                by_id["tiangong-desktop"].executable_relative_path,
                "app.asar",
            )
            self.assertEqual(
                by_id["tiangong-desktop"].sha256,
                hashlib.sha256(desktop_archive.read_bytes()).hexdigest(),
            )

            output = runtime / "release"
            written = write_production_release_manifest(
                output,
                ROOT,
                runtime,
                platform_name="win32",
                architecture="x64",
                desktop_archive_path=desktop_archive,
            )
            self.assertEqual(
                release_manifest_bytes(written),
                (output / RELEASE_MANIFEST_FILENAME).read_bytes(),
            )

    def test_same_version_production_rebuild_uses_the_newer_build_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            artifacts = {
                "total-gateway/tiangong-total-gateway.exe": b"single-process-release-runtime",
            }
            for relative_path, content in artifacts.items():
                path = runtime / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            desktop_archive = runtime / "electron-builder/win-unpacked/resources/app.asar"
            desktop_archive.parent.mkdir(parents=True, exist_ok=True)
            desktop_archive.write_bytes(b"complete-desktop-archive")
            first = generate_production_release_manifest(
                ROOT,
                runtime,
                platform_name="win32",
                architecture="x64",
                desktop_archive_path=desktop_archive,
                generated_at_ms=self.manifest.generated_at_ms + 1_000,
            )
            second = generate_production_release_manifest(
                ROOT,
                runtime,
                platform_name="win32",
                architecture="x64",
                desktop_archive_path=desktop_archive,
                generated_at_ms=self.manifest.generated_at_ms + 2_000,
            )
            first_path = runtime / "first.json"
            second_path = runtime / "second.json"
            first_path.write_bytes(release_manifest_bytes(first))
            second_path.write_bytes(release_manifest_bytes(second))
            selected = select_latest_release_manifest([second_path, first_path])
            self.assertEqual(selected.generated_at_ms, second.generated_at_ms)

    def test_production_manifest_refuses_missing_native_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            desktop_archive = runtime / "electron-builder/mac/resources/app.asar"
            desktop_archive.parent.mkdir(parents=True, exist_ok=True)
            desktop_archive.write_bytes(b"complete-desktop-archive")
            with self.assertRaises(ReleaseManifestError):
                generate_production_release_manifest(
                    ROOT,
                    runtime,
                    platform_name="darwin",
                    architecture="arm64",
                    desktop_archive_path=desktop_archive,
                )


if __name__ == "__main__":
    unittest.main()
