from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.orchestration import GatewayOrchestrationWorker, manifest_authority_scope
from total_gateway.release_manifest import (
    RELEASE_MANIFEST_FILENAME,
    write_production_release_manifest,
)
from runtime_security import EphemeralTestProtector
from total_gateway.runtime_authority import RuntimeAuthorityError, RuntimeTicketAuthority
from total_gateway.store import GatewayStateStore
from tests.test_delivery_contracts import component_manifest


class RuntimeTicketAuthorityTests(unittest.TestCase):
    def test_manifest_authority_scope_is_bounded_and_collision_safe(self) -> None:
        first = manifest_authority_scope("a" * 64)
        second = manifest_authority_scope("b" * 64)

        self.assertRegex(first, r"^[A-Za-z0-9_-]{27}$")
        self.assertNotEqual(first, second)

    def test_development_orchestration_uses_a_manifest_scoped_authority(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as resources:
            state_root = Path(temporary).resolve()
            config = SimpleNamespace(
                release_manifest_path=None,
                release_source_root=source,
                environment="development",
                state_root=state_root,
                workspace_root=state_root,
                backend_internal_token="b" * 48,
                life_internal_token="l" * 48,
                communication_api_token="c" * 48,
                runtime_key_protector=EphemeralTestProtector(),
            )
            # D-06 统一 admission：authority 必须接真实 effect 台账（机械适配）
            store = GatewayStateStore.open(state_root / "gateway-state" / "gateway.sqlite3", now_ms=1_000)
            resources.callback(store.close)
            objects = ContentAddressedObjectStore.open(
                state_root / "gateway-objects",
                now_ms=1_000,
            )
            resources.callback(objects.close)
            worker = GatewayOrchestrationWorker.from_runtime_config(
                config=config,
                activator=SimpleNamespace(),
                store=store,
                objects=objects,
                facts=SimpleNamespace(),
                gateway_epoch=1,
                gateway_instance_id="gateway-test",
                now_ms=1_000,
            )
            resources.callback(worker.close)
            records = tuple((state_root / "ta" / "m").glob("*/authority.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0].parent.name,
                manifest_authority_scope(worker.component_manifest.manifest_sha256),
            )

    def test_production_upgrade_preserves_legacy_authority_and_opens_scoped_keys(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as resources:
            temporary_root = Path(temporary).resolve()
            state_root = temporary_root / "state"
            state_root.mkdir()
            legacy_manifest = component_manifest()
            protector = EphemeralTestProtector()
            RuntimeTicketAuthority.open(
                state_root / "ticket-authority",
                legacy_manifest,
                now_ms=1_000,
                protector=protector,
            )
            runtime_root = temporary_root / "native-runtime"
            artifacts = {
                "backend/tiangong-backend/tiangong-backend.exe": b"backend-upgrade-test",
                "life-service/runtime314/tiangong-life-service-runtime.exe": b"life-upgrade-test",
                "communication-service/tiangong-communication-service.exe": b"communication-upgrade-test",
                "total-gateway/tiangong-total-gateway.exe": b"gateway-upgrade-test",
            }
            for relative_path, content in artifacts.items():
                path = runtime_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            desktop_archive = runtime_root / "electron-builder/win-unpacked/resources/app.asar"
            desktop_archive.parent.mkdir(parents=True, exist_ok=True)
            desktop_archive.write_bytes(b"complete-desktop-archive")
            release_root = runtime_root / "release"
            release = write_production_release_manifest(
                release_root,
                source,
                runtime_root,
                platform_name="win32",
                architecture="x64",
                desktop_archive_path=desktop_archive,
            )
            self.assertNotEqual(
                legacy_manifest.manifest_sha256,
                release.component_manifest.manifest_sha256,
            )
            config = SimpleNamespace(
                release_manifest_path=release_root / RELEASE_MANIFEST_FILENAME,
                release_source_root=source,
                environment="production",
                state_root=state_root,
                workspace_root=state_root,
                backend_internal_token="b" * 48,
                life_internal_token="l" * 48,
                communication_api_token="c" * 48,
                runtime_key_protector=protector,
            )

            # D-06 统一 admission：authority 必须接真实 effect 台账（机械适配）
            store = GatewayStateStore.open(state_root / "gateway-state" / "gateway.sqlite3", now_ms=2_000)
            resources.callback(store.close)
            objects = ContentAddressedObjectStore.open(
                state_root / "gateway-objects",
                now_ms=2_000,
            )
            resources.callback(objects.close)
            worker = GatewayOrchestrationWorker.from_runtime_config(
                config=config,
                activator=SimpleNamespace(),
                store=store,
                objects=objects,
                facts=SimpleNamespace(),
                gateway_epoch=2,
                gateway_instance_id="gateway-upgrade-test",
                now_ms=2_000,
            )
            resources.callback(worker.close)

            scoped_record = (
                state_root
                / "ta"
                / "m"
                / manifest_authority_scope(worker.component_manifest.manifest_sha256)
                / "authority.json"
            )
            self.assertTrue(scoped_record.is_file())
            self.assertTrue((state_root / "ticket-authority" / "authority.json").is_file())
            self.assertEqual(
                worker.component_manifest.manifest_sha256,
                release.component_manifest.manifest_sha256,
            )

    def test_dpapi_authority_reopens_from_its_canonical_json_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ticket-authority"
            manifest = component_manifest()
            protector = EphemeralTestProtector()
            created = RuntimeTicketAuthority.open(
                root, manifest, now_ms=1_000, protector=protector
            )
            first_execution_kid = created.execution_signer.kid
            first_delivery_kid = created.delivery_signer.kid

            reopened = RuntimeTicketAuthority.open(
                root, manifest, now_ms=2_000, protector=protector
            )

            self.assertEqual(reopened.execution_signer.kid, first_execution_kid)
            self.assertEqual(reopened.delivery_signer.kid, first_delivery_kid)
            self.assertEqual(reopened.component_manifest_sha256, manifest.manifest_sha256)

    def test_duplicate_json_key_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ticket-authority"
            manifest = component_manifest()
            protector = EphemeralTestProtector()
            RuntimeTicketAuthority.open(
                root, manifest, now_ms=1_000, protector=protector
            )
            path = root / "authority.json"
            raw = path.read_bytes()
            path.write_bytes(raw.replace(b'{', b'{"revision":1,', 1))

            with self.assertRaises(RuntimeAuthorityError):
                RuntimeTicketAuthority.open(
                    root, manifest, now_ms=2_000, protector=protector
                )


if __name__ == "__main__":
    unittest.main()
