from __future__ import annotations

import hashlib
import io
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from contracts import derive_effect_identity, derive_request_identity, derive_run_identity
from total_gateway.artifact_gate import ArtifactCandidate, ArtifactGate
from total_gateway.backend_client import BackendClient
from total_gateway.delivery_packager import DeliveryPackager, DeliveryPackagingError
from total_gateway.docx_qc import DocxQcPolicy, DocxQcService
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore
from tests.test_backend_client import FakeBackendTransport, backend_response, signed_ticket
from tests.test_docx_qc import CONTENT_TYPE, HASH_B, docx_bytes


def load_delivery_kernel():
    path = (
        Path(__file__).parents[1]
        / "app"
        / "backend"
        / "tiangong-backend"
        / "_internal"
        / "omni_body_skill"
        / "tools"
        / "delivery_kernel.py"
    )
    spec = importlib.util.spec_from_file_location("tiangong_delivery_kernel_source_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeWorkspaceRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve(strict=True)

    def _resolve(self, target: str | None, *, must_exist: bool = False) -> Path:
        if not target:
            raise ValueError("target is required")
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve(strict=must_exist)
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError("target escaped workspace")
        return resolved

    def _rel(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.workspace).as_posix()


class LegacySourcePackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kernel = load_delivery_kernel()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.runtime = FakeWorkspaceRuntime(self.workspace)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_package_is_new_zip_and_source_file_is_unchanged(self) -> None:
        source = self.workspace / "ai_essay.docx"
        source.write_bytes(b"real-docx-placeholder" * 100)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.kernel._deliverable_package(
            self.runtime,
            "delivery.zip",
            {"items": ["ai_essay.docx"], "notes": "checked"},
        )
        self.assertTrue(result["success"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
        output = self.workspace / "delivery.zip"
        with zipfile.ZipFile(output, "r") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"ai_essay.docx", "DELIVERY_MANIFEST.json"})
            manifest = json.loads(archive.read("DELIVERY_MANIFEST.json"))
            self.assertEqual(manifest["items"][0]["bytes"], source.stat().st_size)

    def test_empty_items_existing_output_and_historical_same_name_cannot_overwrite(self) -> None:
        source = self.workspace / "ai_essay.docx"
        source.write_bytes(b"original-word-bytes")
        original = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "new .zip"):
            self.kernel._deliverable_package(
                self.runtime,
                "ai_essay.docx",
                {"items": ["ai_essay.docx"]},
            )
        self.assertEqual(source.read_bytes(), original)

        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.kernel._deliverable_package(self.runtime, "empty.zip", {"items": []})
        self.assertFalse((self.workspace / "empty.zip").exists())

        existing = self.workspace / "existing.zip"
        existing.write_bytes(b"keep-me")
        with self.assertRaises(FileExistsError):
            self.kernel._deliverable_package(
                self.runtime,
                "existing.zip",
                {"items": ["ai_essay.docx"]},
            )
        self.assertEqual(existing.read_bytes(), b"keep-me")

    def test_input_directory_cannot_contain_output_and_failed_commit_cleans_temp(self) -> None:
        (self.workspace / "source.txt").write_text("source", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inside an input directory"):
            self.kernel._deliverable_package(
                self.runtime,
                "nested.zip",
                {"items": ["."]},
            )
        with mock.patch.object(self.kernel.os, "rename", side_effect=OSError("fault injection")):
            with self.assertRaises(OSError):
                self.kernel._deliverable_package(
                    self.runtime,
                    "failed.zip",
                    {"items": ["source.txt"]},
                )
        self.assertFalse((self.workspace / "failed.zip").exists())
        self.assertEqual(list(self.workspace.glob(".*.tmp")), [])


class ObjectDeliveryPackagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.gateway_store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=1_000)
        self.object_store = ContentAddressedObjectStore.open(root / "objects", now_ms=1_000)
        self.fact_ledger = FactLedger.open(root / "facts.sqlite3", self.object_store, now_ms=1_000)
        self.staging = root / "package-staging"
        self.packager = DeliveryPackager(
            self.object_store,
            self.fact_ledger,
            self.staging,
            max_package_bytes=10_000_000,
        )
        self.manifest = self._passed_manifest()

    def tearDown(self) -> None:
        self.fact_ledger.close()
        self.object_store.close()
        self.gateway_store.close()
        self.temporary.cleanup()

    def _passed_manifest(self):
        request = derive_request_identity("5" * 64)
        run = derive_run_identity(request.request_id, 1)
        effect = derive_effect_identity(
            request_id=request.request_id,
            run_id=run.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=0,
            intent_sha256="4" * 64,
        )
        reference = self.object_store.put_bytes(
            docx_bytes("交付内容" * 300),
            kind="artifact",
            tenant_id="tenant_001",
            link_account_id="wechat_001",
            conversation_scope_hash=HASH_B,
            created_at_ms=20_000,
        ).reference
        arguments = {"content": "create package input"}
        ticket, capability, trust = signed_ticket(
            arguments,
            request_id=request.request_id,
            run_id=run.run_id,
            effect_id=effect.effect_id,
        )
        transport = FakeBackendTransport()
        envelope = backend_response(ticket, {"object_id": reference.object_id})
        execution = dict(envelope["execution_result"])
        execution["result_id"] = "execution_result_package_input"
        execution["fact_ids"] = ["fact_package_producer"]
        execution["output_object_refs"] = [reference.object_id]
        envelope["execution_result"] = execution
        transport.response = envelope
        response = BackendClient(
            transport,
            self.gateway_store,
            ticket_consumer_instance_id="delivery_packager_test",
        ).execute(
            ticket,
            arguments,
            capability_manifest=capability,
            trust_bundle=trust,
            now_ms=20_000,
            expected_gateway_epoch=3,
            minimum_generation=2,
        )
        self.fact_ledger.record_execution(response, observed_at_ms=20_200)
        gate_result = ArtifactGate(self.object_store, self.fact_ledger).accept(
            ArtifactCandidate(
                producer_fact_id="fact_package_producer",
                object_id=reference.object_id,
                expected_sha256=reference.sha256,
                expected_size_bytes=reference.size_bytes,
                run_sequence=1,
                artifact_intent_id="primary_document",
                revision=1,
                workspace_id="workspace_001",
                filename="ai_essay.docx",
                declared_mime=CONTENT_TYPE,
                format_id="docx",
                created_at_ms=20_300,
            )
        )
        return DocxQcService(self.object_store, self.fact_ledger).evaluate(
            gate_result,
            run_sequence=1,
            policy=DocxQcPolicy(minimum_word_count=1000),
            checked_at_ms=20_500,
        ).registration.record.manifest

    def test_object_package_is_verified_atomic_and_does_not_change_input(self) -> None:
        before = self.object_store.read_bytes(self.manifest.content_object_id)
        result = self.packager.package(
            (self.manifest,),
            filename="delivery.zip",
            created_at_ms=21_000,
            notes="QC passed",
        )
        self.assertTrue(result.manifest.has_valid_sha256())
        self.assertEqual(result.object_reference.kind, "delivery_package")
        self.assertNotEqual(result.object_reference.object_id, self.manifest.content_object_id)
        self.assertEqual(self.object_store.read_bytes(self.manifest.content_object_id), before)
        package = self.object_store.read_bytes(result.object_reference.object_id)
        self.assertEqual(hashlib.sha256(package).hexdigest(), result.package_sha256)
        with zipfile.ZipFile(io.BytesIO(package), "r") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"ai_essay.docx", "DELIVERY_MANIFEST.json"})
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_identical_retry_reuses_same_content_object(self) -> None:
        first = self.packager.package(
            (self.manifest,), filename="delivery.zip", created_at_ms=21_000
        )
        second = self.packager.package(
            (self.manifest,), filename="delivery.zip", created_at_ms=21_000
        )
        self.assertEqual(second.object_reference, first.object_reference)
        self.assertEqual(second.package_sha256, first.package_sha256)
        self.assertFalse(second.created_by_this_call)

    def test_empty_nonzip_duplicate_or_forged_qc_manifest_is_rejected(self) -> None:
        with self.assertRaisesRegex(DeliveryPackagingError, "items.empty"):
            self.packager.package((), filename="delivery.zip", created_at_ms=21_000)
        with self.assertRaisesRegex(DeliveryPackagingError, "not_zip"):
            self.packager.package(
                (self.manifest,), filename="ai_essay.docx", created_at_ms=21_000
            )
        with self.assertRaisesRegex(DeliveryPackagingError, "duplicate_revision"):
            self.packager.package(
                (self.manifest, self.manifest), filename="delivery.zip", created_at_ms=21_000
            )
        forged = self.manifest.model_copy(
            update={"filename": "renamed.docx", "manifest_sha256": "0" * 64}
        ).with_computed_manifest_sha256()
        with self.assertRaisesRegex(DeliveryPackagingError, "qc_fact_missing"):
            self.packager.package((forged,), filename="delivery.zip", created_at_ms=21_000)

    def test_staging_failure_never_commits_package_and_cleans_temp(self) -> None:
        def fail(path, *_):
            path.write_bytes(b"partial")
            raise DeliveryPackagingError("fault.injected")

        with mock.patch.object(self.packager, "_write_verified_zip", side_effect=fail):
            with self.assertRaisesRegex(DeliveryPackagingError, "fault.injected"):
                self.packager.package(
                    (self.manifest,), filename="delivery.zip", created_at_ms=21_000
                )
        self.assertEqual(list(self.staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
