from __future__ import annotations

import pytest

import hashlib
import tempfile
import unittest
from pathlib import Path

from contracts import evaluate_readiness_contract
from communication_service.embedded_runtime import EMBEDDED_COMMUNICATION_BUILD_ID
from life_service.embedded_runtime import EMBEDDED_LIFE_BUILD_ID
from total_gateway.embedded_backend import EMBEDDED_BACKEND_BUILD_ID
from total_gateway.readiness_collector import ProductionReadinessCollector
from total_gateway.release_manifest import (
    generate_production_release_manifest,
    generate_release_manifest,
    release_manifest_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "readiness-test-token-0123456789abcdef"


class ProductionReadinessCollectorTests(unittest.TestCase):
    def _release(self, runtime: Path):
        path = runtime / "total-gateway/tiangong-total-gateway.exe"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = b"verified:single-process-runtime"
        path.write_bytes(content)
        desktop_archive = runtime / "electron-builder/win-unpacked/resources/app.asar"
        desktop_archive.parent.mkdir(parents=True, exist_ok=True)
        desktop_archive.write_bytes(b"verified:complete-desktop-archive")
        release = generate_production_release_manifest(
            ROOT,
            runtime,
            platform_name="win32",
            architecture="x64",
            desktop_archive_path=desktop_archive,
        )
        components = []
        for item in release.component_manifest.components:
            if item.component_id in {
                "tiangong-backend",
                "tiangong-communication-service",
                "tiangong-life-service",
                "tiangong-total-gateway",
            }:
                item = item.model_copy(
                    update={
                        "size_bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            components.append(item)
        component_manifest = release.component_manifest.model_copy(
            update={
                "components": tuple(components),
                "manifest_sha256": "0" * 64,
            }
        ).with_computed_manifest_sha256()
        return release.model_copy(
            update={
                "component_manifest": component_manifest,
                "release_manifest_sha256": "0" * 64,
            }
        ).with_computed_release_manifest_sha256()

    def test_development_source_release_requires_explicit_opt_in(self) -> None:
        release = generate_release_manifest(ROOT)
        manifest_path = ROOT / "app" / "release" / "release-manifest.json"
        with self.assertRaisesRegex(ValueError, "production release"):
            ProductionReadinessCollector(
                release=release,
                release_manifest_path=manifest_path,
                gateway_epoch=7,
                gateway_instance_id="gateway-source-test",
                backend_token=TOKEN,
                life_token=TOKEN,
                communication_token=TOKEN,
            )
        collector = ProductionReadinessCollector(
            release=release,
            release_manifest_path=manifest_path,
            gateway_epoch=7,
            gateway_instance_id="gateway-source-test",
            backend_token=TOKEN,
            life_token=TOKEN,
            communication_token=TOKEN,
            allow_development_release=True,
        )
        self.assertEqual(collector.evidence_profile, "development-source")
        collector._probe_component = lambda component_id: (  # type: ignore[method-assign]
            True,
            True,
            f"instance-{component_id}",
        )
        expectation, evidence, authenticated, binaries = collector.collect(now_ms=9_000)
        decision = evaluate_readiness_contract(
            expectation,
            evidence,
            decision_id="collector-source-ready",
            now_ms=9_000,
            authenticated_component_ids=authenticated,
            binary_verified_component_ids=binaries,
        )
        self.assertEqual(decision.status, "READY")

    def test_embedded_services_are_probed_in_process_without_loopback_tokens(self) -> None:
        release = generate_release_manifest(ROOT)
        manifest_path = ROOT / "app" / "release" / "release-manifest.json"
        backend_descriptor = next(
            item for item in release.component_manifest.components
            if item.component_id == "tiangong-backend"
        )

        class Backend:
            def health_payload(self):
                return {
                    "ok": True,
                    "component_id": "tiangong-backend",
                    "bridge_ready": True,
                    "build_id": EMBEDDED_BACKEND_BUILD_ID,
                    "api_contract_version": backend_descriptor.api_contract_ids[0],
                    "deployment_mode": "embedded",
                    "listener_port": None,
                }

        class Life:
            def health_payload(self):
                return {
                    "ok": True,
                    "life_ready": True,
                    "build_id": EMBEDDED_LIFE_BUILD_ID,
                    "component_id": "tiangong-life-service",
                    "api_contract": "tiangong.life.api.v2",
                    "deployment_mode": "embedded",
                    "listener_port": None,
                }

        class Communication:
            def ready_payload(self, *, now_ms=None):
                return 200, {
                    "ok": True,
                    "status": "READY",
                    "component_id": "tiangong-communication-service",
                    "build_id": EMBEDDED_COMMUNICATION_BUILD_ID,
                    "api_contract": "tiangong.communication.api.v1",
                    "deployment_mode": "embedded",
                    "listener_port": None,
                }

        collector = ProductionReadinessCollector(
            release=release,
            release_manifest_path=manifest_path,
            gateway_epoch=9,
            gateway_instance_id="gateway-embedded-test",
            backend_token="",
            life_token="",
            communication_token="",
            allow_development_release=True,
            embedded_services={
                "tiangong-backend": Backend(),
                "tiangong-life-service": Life(),
                "tiangong-communication-service": Communication(),
            },
        )
        expectation, evidence, authenticated, binaries = collector.collect(now_ms=12_000)
        decision = evaluate_readiness_contract(
            expectation,
            evidence,
            decision_id="collector-embedded-ready",
            now_ms=12_000,
            authenticated_component_ids=authenticated,
            binary_verified_component_ids=binaries,
        )
        self.assertEqual(decision.status, "READY")
        self.assertEqual(authenticated, {
            "tiangong-backend",
            "tiangong-communication-service",
            "tiangong-life-service",
            "tiangong-total-gateway",
        })

    def test_embedded_probe_rejects_old_listener_manifest_even_when_health_looks_alive(self) -> None:
        release = generate_release_manifest(ROOT)
        components = tuple(
            item.model_copy(update={"ports": (7174,)})
            if item.component_id == "tiangong-backend"
            else item
            for item in release.component_manifest.components
        )
        component_manifest = release.component_manifest.model_copy(
            update={"components": components, "manifest_sha256": "0" * 64}
        ).with_computed_manifest_sha256()
        stale = release.model_copy(
            update={"component_manifest": component_manifest, "release_manifest_sha256": "0" * 64}
        ).with_computed_release_manifest_sha256()

        class Backend:
            def health_payload(self):
                return {
                    "ok": True,
                    "component_id": "tiangong-backend",
                    "bridge_ready": True,
                    "build_id": EMBEDDED_BACKEND_BUILD_ID,
                    "api_contract_version": "tiangong.backend.api.v1",
                    "deployment_mode": "embedded",
                    "listener_port": None,
                }

        collector = ProductionReadinessCollector(
            release=stale,
            release_manifest_path=ROOT / "app" / "release" / "release-manifest.json",
            gateway_epoch=11,
            gateway_instance_id="gateway-stale-topology",
            backend_token="",
            life_token=TOKEN,
            communication_token=TOKEN,
            allow_development_release=True,
            embedded_services={"tiangong-backend": Backend()},
        )
        authenticated, passed, _instance = collector._probe_component("tiangong-backend")
        self.assertTrue(authenticated)
        self.assertFalse(passed)

    @pytest.mark.ci_fragile
    def test_exact_selected_binaries_and_authenticated_health_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "resources"
            release = self._release(runtime)
            release_dir = runtime / "release"
            release_dir.mkdir()
            manifest_path = release_dir / "release-manifest.json"
            manifest_path.write_bytes(release_manifest_bytes(release))
            collector = ProductionReadinessCollector(
                release=release,
                release_manifest_path=manifest_path,
                gateway_epoch=8,
                gateway_instance_id="gateway-test-instance",
                backend_token=TOKEN,
                life_token=TOKEN,
                communication_token=TOKEN,
            )
            collector._probe_component = lambda component_id: (  # type: ignore[method-assign]
                True,
                True,
                f"instance-{component_id}",
            )
            expectation, evidence, authenticated, binaries = collector.collect(now_ms=10_000)
            decision = evaluate_readiness_contract(
                expectation,
                evidence,
                decision_id="collector-ready",
                now_ms=10_000,
                authenticated_component_ids=authenticated,
                binary_verified_component_ids=binaries,
            )
            self.assertEqual(decision.status, "READY")

            backend = next(
                item
                for item in release.component_manifest.components
                if item.component_id == "tiangong-backend"
            )
            (runtime / Path(backend.executable_relative_path)).write_bytes(b"tampered")
            collector._refresh_binary_evidence(force=True)
            expectation, evidence, authenticated, binaries = collector.collect(now_ms=11_000)
            decision = evaluate_readiness_contract(
                expectation,
                evidence,
                decision_id="collector-tampered",
                now_ms=11_000,
                authenticated_component_ids=authenticated,
                binary_verified_component_ids=binaries,
            )
            self.assertEqual(decision.status, "NOT_READY")
            self.assertIn(
                "readiness.binary.unverified",
                {failure.reason_code for failure in decision.failures},
            )


if __name__ == "__main__":
    unittest.main()
