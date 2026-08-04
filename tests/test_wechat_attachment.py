import hashlib
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from communication_service.attachment_quarantine import (
    AttachmentQuarantineLedger,
    AttachmentQuotaError,
    AttachmentQuotaPolicy,
)
from communication_service.wechat_attachment import (
    StoredAttachmentObject,
    WechatAttachmentCandidate,
    WechatAttachmentError,
    WechatAttachmentGate,
)
from communication_service.wechat_media import DownloadedWechatMedia
from tests.security_file_corpus import security_file_corpus


CONVERSATION = "c" * 64


class _Sink:
    def __init__(self, *, corrupt=False):
        self.calls = []
        self.corrupt = corrupt

    def put_attachment(self, source, **kwargs):
        data = source.read_bytes()
        self.calls.append((data, kwargs))
        digest = hashlib.sha256(data).hexdigest()
        return StoredAttachmentObject(
            object_id="attachment_object_" + digest,
            revision=1,
            sha256="0" * 64 if self.corrupt else digest,
            size_bytes=len(data),
        )


def docx(path: Path, *, malicious=False, macro=False):
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        )
        if malicious:
            archive.writestr("../escape.txt", "bad")
        if macro:
            archive.writestr("word/vbaProject.bin", b"macro")


class WechatAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.ledger = AttachmentQuarantineLedger.open(
            self.root / "attachments.sqlite3",
            now_ms=1_000,
        )
        self.sink = _Sink()
        self.gate = WechatAttachmentGate(self.stage, self.sink, self.ledger)

    def tearDown(self):
        self.ledger.close()
        self.temporary.cleanup()

    def media(self, name: str, data: bytes) -> DownloadedWechatMedia:
        path = self.stage / f"{name}.plain.part"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return DownloadedWechatMedia(
            plaintext_path=path,
            plaintext_sha256=digest,
            plaintext_size_bytes=len(data),
            ciphertext_sha256="a" * 64,
            ciphertext_size_bytes=max(16, len(data)),
            source_url_sha256="b" * 64,
        )

    def candidate(self, media, *, filename="报告.docx", mime=None, created=2_000):
        return WechatAttachmentCandidate(
            media=media,
            filename=filename,
            declared_mime=mime
            or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            tenant_id="tenant-a",
            link_account_id="account-a",
            conversation_scope_hash=CONVERSATION,
            source_message_ref="wxmsg_" + "d" * 64,
            created_at_ms=created,
        )

    def test_valid_docx_is_structurally_checked_stored_and_quarantined_with_ttl(self):
        path = self.stage / "source.plain.part"
        docx(path)
        data = path.read_bytes()
        reference = self.gate.accept(self.candidate(self.media("valid", data)))
        self.assertEqual(reference.filename, "报告.docx")
        self.assertTrue(reference.magic_verified)
        self.assertTrue(self.ledger.is_active(reference, now_ms=2_001))
        self.assertEqual(len(self.sink.calls), 1)
        self.assertFalse((self.stage / "valid.plain.part").exists())

    def test_filename_mime_magic_macro_traversal_and_active_zip_content_are_blocked(self):
        cases = []
        good = self.stage / "good.plain.part"
        docx(good)
        cases.append((good.read_bytes(), "../报告.docx", None, "wechat.attachment.filename_unsafe"))
        cases.append((good.read_bytes(), "报告.docx", "application/octet-stream", "wechat.attachment.mime_mismatch"))
        cases.append((b"not a word file", "报告.docx", None, "wechat.attachment.archive.magic.invalid"))
        bad = self.stage / "bad.plain.part"
        docx(bad, malicious=True)
        cases.append((bad.read_bytes(), "报告.docx", None, "wechat.attachment.archive.path.unsafe"))
        macro = self.stage / "macro.plain.part"
        docx(macro, macro=True)
        cases.append((macro.read_bytes(), "报告.docx", None, "wechat.attachment.ooxml.macro.forbidden"))
        active = self.stage / "active.plain.part"
        with ZipFile(active, "w", ZIP_DEFLATED) as archive:
            archive.writestr("run.ps1", "Write-Host bad")
        cases.append((active.read_bytes(), "资料.zip", "application/zip", "wechat.attachment.archive_active_content"))
        for index, (data, filename, mime, code) in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(WechatAttachmentError) as caught:
                    self.gate.accept(
                        self.candidate(
                            self.media(f"blocked-{index}", data),
                            filename=filename,
                            mime=mime,
                        )
                    )
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.sink.calls, [])

    def test_sink_mismatch_is_rejected_and_quarantine_source_is_consumed(self):
        gate = WechatAttachmentGate(self.stage, _Sink(corrupt=True), self.ledger)
        source = self.media("mismatch", b"plain text")
        with self.assertRaises(WechatAttachmentError) as caught:
            gate.accept(
                self.candidate(source, filename="note.txt", mime="text/plain")
            )
        self.assertEqual(caught.exception.code, "wechat.attachment.object_sink_mismatch")
        self.assertFalse(source.plaintext_path.exists())

    def test_quota_is_transactional_and_expired_references_stop_being_active(self):
        policy = AttachmentQuotaPolicy(
            max_active_files=2,
            max_total_active_bytes=10,
            max_account_active_bytes=10,
            max_conversation_active_bytes=5,
            ttl_ms=60_000,
        )
        gate = WechatAttachmentGate(self.stage, self.sink, self.ledger, quota_policy=policy)
        first = gate.accept(
            self.candidate(
                self.media("first", b"12345"),
                filename="one.txt",
                mime="text/plain",
            )
        )
        with self.assertRaises(AttachmentQuotaError) as caught:
            gate.accept(
                self.candidate(
                    self.media("second", b"67890"),
                    filename="two.txt",
                    mime="text/plain",
                    created=2_001,
                )
            )
        self.assertEqual(caught.exception.code, "attachment.quota.conversation_bytes")
        self.assertTrue(self.ledger.is_active(first, now_ms=61_999))
        self.assertEqual(self.ledger.expire(now_ms=62_000), (first.object_id,))
        self.assertFalse(self.ledger.is_active(first, now_ms=62_000))

    def test_shared_security_corpus_matches_attachment_gate_decisions(self):
        accepted = 0
        for index, case in enumerate(security_file_corpus()):
            if case.declared_size_bytes is not None:
                continue  # stream-level declaration cases are exercised by media simulators
            with self.subTest(case=case.case_id):
                candidate = self.candidate(
                    self.media(f"corpus-{index}", case.content),
                    filename=case.filename,
                    mime=case.declared_mime,
                    created=3_000 + index,
                )
                if case.expected_gate_accept:
                    reference = self.gate.accept(candidate)
                    self.assertEqual(reference.sha256, case.sha256)
                    accepted += 1
                else:
                    with self.assertRaises(WechatAttachmentError):
                        self.gate.accept(candidate)
        self.assertEqual(accepted, 5)


if __name__ == "__main__":
    unittest.main()
