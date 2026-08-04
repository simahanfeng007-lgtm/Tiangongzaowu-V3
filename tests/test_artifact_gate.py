from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from contracts import (
    derive_effect_identity,
    derive_request_identity,
    derive_run_identity,
)
from total_gateway.artifact_gate import ArtifactCandidate, ArtifactGate, ArtifactGateError
from total_gateway.backend_client import BackendClient
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore
from tests.test_backend_client import FakeBackendTransport, backend_response, signed_ticket


HASH_B = "b" * 64


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def minimal_docx(*, unsafe_name: str | None = None) -> bytes:
    entries = {
        "[Content_Types].xml": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            b'<Override PartName="/word/document.xml" '
            b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            b'</Types>'
        ),
        "_rels/.rels": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            b'Target="word/document.xml"/>'
            b'</Relationships>'
        ),
        "word/document.xml": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>'
        ),
    }
    if unsafe_name is not None:
        entries[unsafe_name] = b"unsafe"
    return zip_bytes(entries)


class ArtifactGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.gateway_store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=1_000)
        self.object_store = ContentAddressedObjectStore.open(root / "objects", now_ms=1_000)
        self.fact_ledger = FactLedger.open(
            root / "facts.sqlite3",
            self.object_store,
            now_ms=1_000,
        )
        self.gate = ArtifactGate(self.object_store, self.fact_ledger, max_artifact_bytes=10_000_000)
        self.request = derive_request_identity("9" * 64)
        self.run = derive_run_identity(self.request.request_id, 1)
        self.effect = derive_effect_identity(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=0,
            intent_sha256="8" * 64,
        )

    def tearDown(self) -> None:
        self.fact_ledger.close()
        self.object_store.close()
        self.gateway_store.close()
        self.temporary.cleanup()

    def put_artifact(
        self,
        data: bytes,
        *,
        kind: str = "artifact",
        tenant_id: str = "tenant_001",
    ):
        return self.object_store.put_bytes(
            data,
            kind=kind,
            tenant_id=tenant_id,
            link_account_id="wechat_001",
            conversation_scope_hash=HASH_B,
            created_at_ms=20_000,
        ).reference

    def record_producer(self, object_ids: tuple[str, ...]):
        arguments = {"content": "create artifact"}
        ticket, manifest, trust = signed_ticket(
            arguments,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            effect_id=self.effect.effect_id,
        )
        transport = FakeBackendTransport()
        envelope = backend_response(ticket, {"objects": sorted(object_ids)})
        result = dict(envelope["execution_result"])
        result["result_id"] = "execution_result_artifact_gate"
        result["fact_ids"] = ["fact_artifact_producer"]
        result["output_object_refs"] = sorted(object_ids)
        envelope["execution_result"] = result
        transport.response = envelope
        response = BackendClient(
            transport,
            self.gateway_store,
            ticket_consumer_instance_id="artifact_gate_test",
        ).execute(
            ticket,
            arguments,
            capability_manifest=manifest,
            trust_bundle=trust,
            now_ms=20_000,
            expected_gateway_epoch=3,
            minimum_generation=2,
        )
        self.fact_ledger.record_execution(response, observed_at_ms=20_200)
        return response

    @staticmethod
    def candidate(reference, *, format_id: str, mime: str, filename: str, **overrides):
        values = {
            "producer_fact_id": "fact_artifact_producer",
            "object_id": reference.object_id,
            "expected_sha256": reference.sha256,
            "expected_size_bytes": reference.size_bytes,
            "run_sequence": 1,
            "artifact_intent_id": "primary_artifact",
            "revision": 1,
            "workspace_id": "workspace_001",
            "filename": filename,
            "declared_mime": mime,
            "format_id": format_id,
            "created_at_ms": 20_300,
        }
        values.update(overrides)
        return ArtifactCandidate(**values)

    def test_docx_object_is_hash_size_magic_structure_and_readback_bound(self) -> None:
        reference = self.put_artifact(minimal_docx())
        self.record_producer((reference.object_id,))
        result = self.gate.accept(
            self.candidate(
                reference,
                format_id="docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename="ai_essay.docx",
            )
        )
        self.assertEqual(result.manifest.qc_state, "PENDING")
        self.assertTrue(result.manifest.has_valid_manifest_sha256())
        self.assertEqual(result.manifest.content_object_id, reference.object_id)
        self.assertEqual(result.manifest.sha256, reference.sha256)
        self.assertEqual(result.manifest.source_effect_id, self.effect.effect_id)
        self.assertEqual(result.manifest.producer_fact_id, "fact_artifact_producer")
        self.assertTrue(result.evidence.has_valid_sha256())
        self.assertTrue(result.evidence.magic_verified)
        self.assertTrue(result.evidence.structure_verified)
        self.assertEqual(result.evidence.immutable_read_count, 2)

    def test_declaration_scope_and_output_reference_mismatches_fail_closed(self) -> None:
        reference = self.put_artifact(minimal_docx())
        unreported = self.put_artifact(minimal_docx() + b"x")
        self.record_producer((reference.object_id,))
        base = self.candidate(
            reference,
            format_id="docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="ai_essay.docx",
        )
        variants = (
            ("digest_mismatch", {"expected_sha256": "f" * 64}),
            ("size_mismatch", {"expected_size_bytes": reference.size_bytes + 1}),
            ("mime.mismatch", {"declared_mime": "application/zip"}),
            ("extension.mismatch", {"filename": "ai_essay.zip"}),
            ("scope_mismatch", {"workspace_id": "workspace_other"}),
            ("producer_fact.invalid", {"object_id": unreported.object_id, "expected_sha256": unreported.sha256, "expected_size_bytes": unreported.size_bytes}),
        )
        for reason, changes in variants:
            with self.subTest(reason=reason), self.assertRaisesRegex(ArtifactGateError, reason):
                self.gate.accept(ArtifactCandidate(**{**base.__dict__, **changes}))

    def test_cross_tenant_and_wrong_object_kind_are_rejected(self) -> None:
        cross_tenant = self.put_artifact(minimal_docx(), tenant_id="tenant_other")
        payload_kind = self.put_artifact(minimal_docx(), kind="payload")
        self.record_producer(tuple(sorted((cross_tenant.object_id, payload_kind.object_id))))
        with self.assertRaisesRegex(ArtifactGateError, "scope_mismatch"):
            self.gate.accept(
                self.candidate(
                    cross_tenant,
                    format_id="docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    filename="cross.docx",
                )
            )
        with self.assertRaisesRegex(ArtifactGateError, "kind_mismatch"):
            self.gate.accept(
                self.candidate(
                    payload_kind,
                    format_id="docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    filename="payload.docx",
                )
            )

    def test_malformed_and_path_traversing_docx_are_rejected(self) -> None:
        for index, data in enumerate((b"PK-not-a-zip", minimal_docx(unsafe_name="../evil.txt"))):
            with self.subTest(index=index):
                reference = self.put_artifact(data)
                self.record_producer((reference.object_id,))
                with self.assertRaisesRegex(ArtifactGateError, "zip"):
                    self.gate.accept(
                        self.candidate(
                            reference,
                            format_id="docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="bad.docx",
                        )
                    )
                self.fact_ledger.close()
                self.gateway_store.close()
                root = Path(self.temporary.name)
                self.gateway_store = GatewayStateStore.open(root / f"gateway-{index}.sqlite3", now_ms=1_000)
                self.fact_ledger = FactLedger.open(
                    root / f"facts-{index}.sqlite3", self.object_store, now_ms=1_000
                )
                self.gate = ArtifactGate(self.object_store, self.fact_ledger, max_artifact_bytes=10_000_000)

    def test_pdf_and_json_use_exact_format_policies(self) -> None:
        pdf = self.put_artifact(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
        json_ref = self.put_artifact(b'{"answer":42}')
        self.record_producer(tuple(sorted((pdf.object_id, json_ref.object_id))))
        pdf_result = self.gate.accept(
            self.candidate(
                pdf,
                format_id="pdf",
                mime="application/pdf",
                filename="report.pdf",
                artifact_intent_id="pdf_report",
            )
        )
        json_result = self.gate.accept(
            self.candidate(
                json_ref,
                format_id="json",
                mime="application/json",
                filename="data.json",
                artifact_intent_id="json_data",
            )
        )
        self.assertEqual(pdf_result.manifest.artifact_kind, "document")
        self.assertEqual(json_result.manifest.artifact_kind, "data")


if __name__ == "__main__":
    unittest.main()
