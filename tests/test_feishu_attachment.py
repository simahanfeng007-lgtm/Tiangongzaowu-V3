import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from communication_service.attachment_quarantine import AttachmentQuarantineLedger
from communication_service.feishu_attachment import (
    FeishuAttachmentError,
    FeishuAttachmentIngestor,
    FeishuResourceLimits,
    build_feishu_resource_url,
    validate_feishu_resource_url,
)
from communication_service.feishu_route import FeishuRouteConflict, FeishuRouteLedger
from communication_service.wechat_attachment import StoredAttachmentObject, WechatAttachmentGate


CONVERSATION = "c" * 64
SOURCE_MESSAGE = "fsmsg_" + "d" * 64


class _Protector:
    def protect(self, plaintext, entropy):
        key = hashlib.sha256(entropy).digest()
        return b"TEST" + bytes(
            value ^ key[index % len(key)] for index, value in enumerate(plaintext)
        )

    def unprotect(self, ciphertext, entropy):
        if not ciphertext.startswith(b"TEST"):
            raise OSError("invalid ciphertext")
        key = hashlib.sha256(entropy).digest()
        return bytearray(
            value ^ key[index % len(key)] for index, value in enumerate(ciphertext[4:])
        )


class _Response:
    def __init__(self, body, *, status=200, mime="application/octet-stream", length=True):
        self.status = status
        self.headers = {"content-type": mime}
        if length:
            self.headers["content-length"] = str(len(body))
        self._stream = io.BytesIO(body)
        self.closed = False

    def read(self, amount):
        return self._stream.read(amount)

    def close(self):
        self.closed = True


class _Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, url, *, access_token, timeout_seconds):
        self.calls.append((url, access_token, timeout_seconds))
        return self.responses.pop(0)


class _Sink:
    def __init__(self):
        self.calls = []

    def put_attachment(self, source, **kwargs):
        data = source.read_bytes()
        self.calls.append((data, kwargs))
        digest = hashlib.sha256(data).hexdigest()
        return StoredAttachmentObject(
            object_id="attachment_object_" + digest,
            revision=1,
            sha256=digest,
            size_bytes=len(data),
        )


def make_docx() -> bytes:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "document.docx"
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
        return path.read_bytes()


class FeishuAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.routes = FeishuRouteLedger.open(
            self.root / "routes.sqlite3", now_ms=1_000, protector=_Protector()
        )
        self.ledger = AttachmentQuarantineLedger.open(
            self.root / "attachments.sqlite3", now_ms=1_000
        )
        self.sink = _Sink()
        self.gate = WechatAttachmentGate(self.stage, self.sink, self.ledger)

    def tearDown(self):
        self.ledger.close()
        self.routes.close()
        self.temporary.cleanup()

    def resource(self, *, kind="file", key="file-key-1", filename="报告.docx"):
        return self.routes.register_resource(
            tenant_id="tenant-a",
            link_account_id="account-a",
            conversation_scope_hash=CONVERSATION,
            source_message_ref=SOURCE_MESSAGE,
            message_id="message-1",
            resource_type=kind,
            resource_key=key,
            filename=filename,
            created_at_ms=2_000,
        )

    def ingestor(self, response, *, max_bytes=536_870_912):
        transport = _Transport(response)
        return (
            FeishuAttachmentIngestor(
                self.stage,
                self.routes,
                self.gate,
                transport=transport,
                limits=FeishuResourceLimits(
                    max_bytes=max_bytes,
                    chunk_bytes=4_096,
                    timeout_seconds=10,
                ),
            ),
            transport,
        )

    def test_resource_url_is_exact_message_bound_openapi_route(self):
        url = build_feishu_resource_url("om_message", "file_v2_key", "file")
        self.assertEqual(
            url,
            "https://open.feishu.cn/open-apis/im/v1/messages/om_message/resources/file_v2_key?type=file",
        )
        self.assertEqual(validate_feishu_resource_url(url), url)
        for bad in (
            url.replace("https://", "http://"),
            url.replace("open.feishu.cn", "open.feishu.cn.evil.test"),
            url.replace("open.feishu.cn", "127.0.0.1"),
            url + "&type=image",
            url.replace("?type=file", "?type=video"),
            "https://user@open.feishu.cn/open-apis/im/v1/messages/a/resources/b?type=file",
        ):
            with self.subTest(url=bad), self.assertRaises(FeishuAttachmentError):
                validate_feishu_resource_url(bad)

    def test_file_is_scope_bound_streamed_and_admitted_by_shared_security_gate(self):
        body = make_docx()
        response = _Response(body)
        ingestor, transport = self.ingestor(response)
        reference = ingestor.ingest(
            self.resource(),
            tenant_id="tenant-a",
            link_account_id="account-a",
            conversation_scope_hash=CONVERSATION,
            access_token="tenant-token",
        )
        self.assertEqual(reference.filename, "报告.docx")
        self.assertEqual(reference.source_message_ref, SOURCE_MESSAGE)
        self.assertEqual(reference.tenant_id, "tenant-a")
        self.assertTrue(reference.magic_verified)
        self.assertTrue(self.ledger.is_active(reference, now_ms=2_001))
        self.assertEqual(len(self.sink.calls), 1)
        self.assertTrue(response.closed)
        self.assertEqual(transport.calls[0][1:], ("tenant-token", 10))
        self.assertEqual(list(self.stage.iterdir()), [])
        database = self.routes.path.read_bytes()
        self.assertNotIn(b"file-key-1", database)
        self.assertNotIn(b"message-1", database)

    def test_image_uses_verified_response_mime_and_safe_generated_filename(self):
        body = b"\x89PNG\r\n\x1a\n" + b"payload" + b"IEND\xaeB`\x82"
        response = _Response(body, mime="image/png")
        ingestor, _ = self.ingestor(response)
        reference = ingestor.ingest(
            self.resource(kind="image", key="img-key-1", filename=None),
            tenant_id="tenant-a",
            link_account_id="account-a",
            conversation_scope_hash=CONVERSATION,
            access_token="tenant-token",
        )
        self.assertEqual(reference.mime, "image/png")
        self.assertTrue(reference.filename.startswith("feishu-image-"))
        self.assertTrue(reference.filename.endswith(".png"))

    def test_cross_tenant_scope_is_rejected_before_network(self):
        response = _Response(make_docx())
        ingestor, transport = self.ingestor(response)
        with self.assertRaises(FeishuRouteConflict):
            ingestor.ingest(
                self.resource(),
                tenant_id="tenant-b",
                link_account_id="account-a",
                conversation_scope_hash=CONVERSATION,
                access_token="tenant-token",
            )
        self.assertEqual(transport.calls, [])

    def test_missing_scope_redirect_truncation_and_size_limit_fail_cleanly(self):
        cases = (
            (_Response(b"denied", status=403), None, "feishu.attachment.scope_denied"),
            (_Response(b"limited", status=429), None, "feishu.attachment.rate_limited"),
            (_Response(b"unavailable", status=503), None, "feishu.attachment.platform_unavailable"),
            (_Response(b"redirect", status=302), None, "feishu.attachment.redirect_forbidden"),
            (_Response(b"12345", length=False), 4, "feishu.attachment.resource_too_large"),
        )
        for index, (response, limit, code) in enumerate(cases):
            with self.subTest(index=index):
                ingestor, transport = self.ingestor(
                    response,
                    max_bytes=536_870_912 if limit is None else limit,
                )
                with self.assertRaises(FeishuAttachmentError) as caught:
                    ingestor.ingest(
                        self.resource(key=f"file-key-{index}"),
                        tenant_id="tenant-a",
                        link_account_id="account-a",
                        conversation_scope_hash=CONVERSATION,
                        access_token="tenant-token",
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(response.closed)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(list(self.stage.iterdir()), [])

    def test_file_content_must_pass_shared_magic_and_mime_gate(self):
        response = _Response(b"not a docx")
        ingestor, _ = self.ingestor(response)
        with self.assertRaises(FeishuAttachmentError) as caught:
            ingestor.ingest(
                self.resource(),
                tenant_id="tenant-a",
                link_account_id="account-a",
                conversation_scope_hash=CONVERSATION,
                access_token="tenant-token",
            )
        self.assertEqual(caught.exception.code, "feishu.attachment.archive.magic.invalid")
        self.assertEqual(self.sink.calls, [])
        self.assertEqual(list(self.stage.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
