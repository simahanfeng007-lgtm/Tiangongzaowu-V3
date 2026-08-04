from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from contracts import (
    canonical_sha256,
    derive_effect_identity,
    derive_request_identity,
    derive_run_identity,
)
from total_gateway.artifact_gate import ArtifactCandidate, ArtifactGate, ArtifactGateError
from total_gateway.backend_client import BackendClient
from total_gateway.docx_qc import (
    DOCX_QC_CHECK_ID,
    DOCX_QC_CHECK_VERSION,
    DocxQcError,
    DocxQcPolicy,
    DocxQcService,
)
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.store import GatewayStateStore
from tests.test_backend_client import FakeBackendTransport, backend_response, signed_ticket


HASH_B = "b" * 64
CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def docx_bytes(
    live_text: str,
    *,
    deleted_text: str = "",
    main_content_type: str = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    ),
    missing_image_relationship: bool = False,
    external_hyperlink: bool = False,
) -> bytes:
    relationships = []
    document_attributes = ""
    additional_entries: dict[str, bytes] = {}
    if missing_image_relationship:
        document_attributes += ' r:embed="rIdImage"'
        relationships.append(
            '<Relationship Id="rIdImage" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/missing.png"/>'
        )
    if external_hyperlink:
        document_attributes += ' r:id="rIdExternal"'
        relationships.append(
            '<Relationship Id="rIdExternal" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="https://example.com/resource" TargetMode="External"/>'
        )
    if relationships:
        additional_entries["word/_rels/document.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(relationships)
            + "</Relationships>"
        ).encode("utf-8")

    deleted = ""
    if deleted_text:
        deleted = f"<w:del><w:r><w:t>{deleted_text}</w:t></w:r></w:del>"
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<w:body><w:p><w:r><w:t{document_attributes}>{live_text}</w:t></w:r>{deleted}</w:p>'
        '<w:sectPr/></w:body></w:document>'
    ).encode("utf-8")
    entries = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="{main_content_type}"/>'
            '</Types>'
        ).encode("utf-8"),
        "_rels/.rels": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            b'Target="word/document.xml"/>'
            b'</Relationships>'
        ),
        "word/document.xml": document,
        **additional_entries,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return output.getvalue()


class DocxQcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.gateway_store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=1_000)
        self.object_store = ContentAddressedObjectStore.open(root / "objects", now_ms=1_000)
        self.fact_ledger = FactLedger.open(root / "facts.sqlite3", self.object_store, now_ms=1_000)
        self.artifact_gate = ArtifactGate(self.object_store, self.fact_ledger)
        self.qc = DocxQcService(self.object_store, self.fact_ledger)
        self.request = derive_request_identity("7" * 64)
        self.run = derive_run_identity(self.request.request_id, 1)
        self.effect = derive_effect_identity(
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            run_sequence=1,
            generation=2,
            effect_kind="execution",
            ordinal=0,
            intent_sha256="6" * 64,
        )

    def tearDown(self) -> None:
        self.fact_ledger.close()
        self.object_store.close()
        self.gateway_store.close()
        self.temporary.cleanup()

    def prepare(self, data: bytes):
        reference = self.object_store.put_bytes(
            data,
            kind="artifact",
            tenant_id="tenant_001",
            link_account_id="wechat_001",
            conversation_scope_hash=HASH_B,
            created_at_ms=20_000,
        ).reference
        arguments = {"content": "create checked docx"}
        ticket, capability, trust = signed_ticket(
            arguments,
            request_id=self.request.request_id,
            run_id=self.run.run_id,
            effect_id=self.effect.effect_id,
        )
        transport = FakeBackendTransport()
        envelope = backend_response(ticket, {"object_id": reference.object_id})
        result = dict(envelope["execution_result"])
        result["result_id"] = "execution_result_docx_qc"
        result["fact_ids"] = ["fact_docx_producer"]
        result["output_object_refs"] = [reference.object_id]
        envelope["execution_result"] = result
        transport.response = envelope
        response = BackendClient(
            transport,
            self.gateway_store,
            ticket_consumer_instance_id="docx_qc_test",
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
        # D-02 双源交叉（CompletionGate head_state_reader）：FactLedger 投影
        # 必须与 gateway effect head 一致 —— 夹具同步提交 head 的
        # claim → started → SUCCEEDED receipt（机械适配，不改变既有断言）。
        self.gateway_store.claim_effect(
            EffectClaim(
                effect_id=self.effect.effect_id,
                request_id=self.request.request_id,
                run_id=self.run.run_id,
                run_sequence=1,
                generation=2,
                effect_kind="execution",
                ordinal=0,
                intent_sha256="6" * 64,
                owner_component_id="tiangong-backend",
                claimed_at_ms=20_000,
                claim_sha256="0" * 64,
            ).with_computed_sha256()
        )
        self.gateway_store.mark_effect_started(self.effect.effect_id, started_at_ms=20_001)
        self.gateway_store.complete_effect(
            EffectResult(
                result_id="effect_result_docx_qc",
                effect_id=self.effect.effect_id,
                status="SUCCEEDED",
                fact_id="fact_docx_producer",
                evidence_sha256=canonical_sha256({"evidence": "docx_qc_execution"}),
                observed_at_ms=20_200,
                result_sha256="0" * 64,
            ).with_computed_sha256()
        )
        return self.artifact_gate.accept(
            ArtifactCandidate(
                producer_fact_id="fact_docx_producer",
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

    @staticmethod
    def metrics(outcome) -> dict[str, str | int | bool]:
        return {
            metric.name: metric.value
            for metric in outcome.registration.record.result.metrics
        }

    def test_1000_real_words_pass_and_deleted_text_is_not_counted(self) -> None:
        gate_result = self.prepare(docx_bytes("字" * 1000, deleted_text="删" * 800))
        outcome = self.qc.evaluate(
            gate_result,
            run_sequence=1,
            policy=DocxQcPolicy(minimum_word_count=1000),
            checked_at_ms=20_500,
        )
        self.assertTrue(outcome.passed)
        record = outcome.registration.record
        self.assertEqual(self.metrics(outcome)["word_count"], 1000)
        self.assertEqual(record.manifest.qc_state, "PASSED")
        self.assertTrue(record.manifest.has_valid_manifest_sha256())
        self.assertEqual(record.fact.fact_type, "artifact.qc_passed")
        self.assertFalse(record.fact.model_generated)
        self.assertTrue(record.fact.has_valid_sha256())
        self.assertEqual(self.fact_ledger.count_facts(), 2)
        restored = self.fact_ledger.get_artifact_qc(
            record.manifest.artifact_revision_id,
            check_id=DOCX_QC_CHECK_ID,
            check_version=DOCX_QC_CHECK_VERSION,
        )
        self.assertEqual(restored, record)
        self.assertTrue(self.fact_ledger.health_check(now_ms=21_000, full=True).healthy)

        duplicate = self.qc.evaluate(
            gate_result,
            run_sequence=1,
            policy=DocxQcPolicy(minimum_word_count=1000),
            checked_at_ms=30_000,
        )
        self.assertFalse(duplicate.registration.created_by_this_call)
        self.assertEqual(duplicate.registration.record, record)

    def test_real_word_count_below_requirement_creates_failed_machine_fact(self) -> None:
        gate_result = self.prepare(docx_bytes("字" * 999))
        outcome = self.qc.evaluate(
            gate_result,
            run_sequence=1,
            policy=DocxQcPolicy(minimum_word_count=1000),
            checked_at_ms=20_500,
        )
        self.assertFalse(outcome.passed)
        result = outcome.registration.record.result
        self.assertEqual(result.reason_codes, ("qc.docx.word_count_below_minimum",))
        self.assertEqual(self.metrics(outcome)["word_count"], 999)
        self.assertEqual(outcome.registration.record.manifest.qc_state, "FAILED")
        self.assertEqual(outcome.registration.record.fact.fact_type, "artifact.qc_failed")

    def test_content_types_relationships_and_external_policy_fail_closed(self) -> None:
        cases = (
            (
                docx_bytes("有效内容", main_content_type="application/octet-stream"),
                "main_content_type_invalid",
                DocxQcPolicy(),
            ),
            (
                docx_bytes("有效内容", missing_image_relationship=True),
                "relationship_target_missing",
                DocxQcPolicy(),
            ),
            (
                docx_bytes("有效内容", external_hyperlink=True),
                "external_relationship_forbidden",
                DocxQcPolicy(allow_external_hyperlinks=False),
            ),
        )
        for index, (data, reason, policy) in enumerate(cases):
            with self.subTest(reason=reason):
                gate_result = self.prepare(data)
                outcome = self.qc.evaluate(
                    gate_result,
                    run_sequence=1,
                    policy=policy,
                    checked_at_ms=20_500,
                )
                self.assertFalse(outcome.passed)
                self.assertEqual(outcome.registration.record.result.reason_codes, ("qc.docx." + reason,))
            if index < len(cases) - 1:
                self._reset_runtime(index)

    def _reset_runtime(self, index: int) -> None:
        self.fact_ledger.close()
        self.gateway_store.close()
        self.object_store.close()
        root = Path(self.temporary.name)
        self.gateway_store = GatewayStateStore.open(root / f"gateway-{index}.sqlite3", now_ms=1_000)
        self.object_store = ContentAddressedObjectStore.open(
            root / f"objects-{index}", now_ms=1_000
        )
        self.fact_ledger = FactLedger.open(
            root / f"facts-{index}.sqlite3", self.object_store, now_ms=1_000
        )
        self.artifact_gate = ArtifactGate(self.object_store, self.fact_ledger)
        self.qc = DocxQcService(self.object_store, self.fact_ledger)

    def test_qc_fact_and_batch_rollback_together_on_crash_boundary(self) -> None:
        gate_result = self.prepare(docx_bytes("有效内容"))
        self.fact_ledger._connection.execute(  # noqa: SLF001 - deliberate crash injection
            """
            CREATE TRIGGER abort_qc_batch
            BEFORE INSERT ON artifact_qc_batches
            BEGIN SELECT RAISE(ABORT, 'fault injection'); END
            """
        )
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.qc.evaluate(
                    gate_result,
                    run_sequence=1,
                    policy=DocxQcPolicy(),
                    checked_at_ms=20_500,
                )
        finally:
            self.fact_ledger._connection.execute("DROP TRIGGER abort_qc_batch")  # noqa: SLF001
        self.assertEqual(self.fact_ledger.count_facts(), 1)
        self.assertIsNone(
            self.fact_ledger.get_artifact_qc(
                gate_result.manifest.artifact_revision_id,
                check_id=DOCX_QC_CHECK_ID,
                check_version=DOCX_QC_CHECK_VERSION,
            )
        )

    def test_246_byte_or_plain_zip_fake_docx_never_reaches_qc(self) -> None:
        with self.assertRaises(ArtifactGateError):
            self.prepare(b"PK" + b"x" * 244)
        self.assertEqual(self.fact_ledger.count_facts(), 1)


if __name__ == "__main__":
    unittest.main()
