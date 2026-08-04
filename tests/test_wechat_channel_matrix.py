import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from communication_service.attachment_quarantine import AttachmentQuarantineLedger
from communication_service.inbox import CommunicationInbox
from communication_service.wechat_attachment import (
    StoredAttachmentObject,
    WechatAttachmentGate,
    WechatInboundAttachmentIngestor,
)
from communication_service.wechat_inbound import (
    WechatInboundPolicy,
    WechatPollRecord,
    WechatTextInboundProcessor,
)
from communication_service.wechat_media import WechatMediaDownloader
from communication_service.wechat_session import WechatSessionLedger
from tests.protocol_simulators import StreamScenario, WechatProtocolSimulator
from tests.security_file_corpus import security_file_corpus


class _Protector:
    def protect(self, plaintext, entropy):
        key = hashlib.sha256(entropy).digest()
        return b"TEST" + bytes(
            value ^ key[index % len(key)] for index, value in enumerate(plaintext)
        )

    def unprotect(self, ciphertext, entropy):
        key = hashlib.sha256(entropy).digest()
        return bytearray(
            value ^ key[index % len(key)] for index, value in enumerate(ciphertext[4:])
        )


class _Sink:
    def __init__(self):
        self.calls = []

    def put_attachment(self, source, **kwargs):
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self.calls.append((digest, kwargs))
        return StoredAttachmentObject(
            object_id="wechat_matrix_object_" + digest,
            revision=1,
            sha256=digest,
            size_bytes=len(data),
        )


def _encrypt(content, key):
    padder = padding.PKCS7(128).padder()
    padded = padder.update(content) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


class WechatChannelMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.stage = self.root / "stage"
        self.inbox = CommunicationInbox.open(self.root / "inbox.sqlite3", now_ms=1_000)
        self.sessions = WechatSessionLedger.open(
            self.root / "sessions.sqlite3",
            now_ms=1_000,
            protector=_Protector(),
        )
        self.quarantine = AttachmentQuarantineLedger.open(
            self.root / "attachments.sqlite3",
            now_ms=1_000,
        )
        self.simulator = WechatProtocolSimulator()
        self.sink = _Sink()
        self.gate = WechatAttachmentGate(self.stage, self.sink, self.quarantine)
        self.ingestor = WechatInboundAttachmentIngestor(
            WechatMediaDownloader(self.stage, transport=self.simulator),
            self.gate,
        )
        self.processor = WechatTextInboundProcessor(
            self.inbox,
            self.sessions,
            self.ingestor,
        )
        self.policy = WechatInboundPolicy(
            tenant_id="tenant-matrix",
            link_account_id="account-matrix",
            account_id="bot-matrix",
            self_user_ids=("bot-user-matrix",),
        )
        self.previous_cursor = None
        self.index = 0
        self.key = b"0123456789abcdef"

    def tearDown(self):
        self.quarantine.close()
        self.sessions.close()
        self.inbox.close()
        self.temporary.cleanup()

    def process(self, item, *, identity=None, text_item=None, same_poll=None):
        if same_poll is None:
            self.index += 1
            identity = identity or f"matrix-message-{self.index}"
            poll = WechatPollRecord(
                raw_payload_object_id=f"matrix-raw-{self.index}",
                raw_payload_sha256=hashlib.sha256(
                    f"matrix-raw-{self.index}".encode()
                ).hexdigest(),
                raw_payload_size_bytes=100,
                previous_cursor_sha256=self.previous_cursor,
                next_cursor_token=f"matrix-cursor-{self.index}",
                captured_at_ms=2_000 + self.index,
                persisted_at_ms=2_100 + self.index,
            )
        else:
            identity, poll = same_poll
        items = [item]
        if text_item is not None:
            items.insert(0, {"text_item": {"text": text_item}})
        raw = {
            "message_type": 1,
            "message_id": identity,
            "seq": self.index,
            "from_user_id": "matrix-user",
            "session_id": "matrix-session",
            "context_token": "synthetic-context",
            "item_list": items,
        }
        outcome = self.processor.process(raw, policy=self.policy, poll=poll)
        if same_poll is None:
            self.previous_cursor = outcome.ack_permit.next_cursor_sha256
        return outcome, (identity, poll)

    def media_item(
        self,
        kind,
        content,
        *,
        filename=None,
        mime=None,
        url="https://novac2c.cdn.weixin.qq.com/c2c/download?synthetic=1",
        cipher_size=True,
        declared_length=None,
    ):
        ciphertext = _encrypt(content, self.key)
        self.simulator.script(
            "media.open",
            StreamScenario(
                200,
                ciphertext,
                declared_length=declared_length,
                max_chunk_bytes=7,
            ),
        )
        payload = {
            "full_url": url,
            "aes_key": base64.b64encode(self.key).decode("ascii"),
            "size": len(content),
        }
        if cipher_size:
            payload["cipher_size"] = len(ciphertext)
        if filename is not None:
            payload["filename"] = filename
        if mime is not None:
            payload["mime"] = mime
        return {kind + "_item": payload}

    def test_text_voice_image_video_and_file_are_forwarded_with_real_attachment_refs(self):
        text, _ = self.process({"text_item": {"text": "文本消息"}})
        self.assertTrue(text.should_forward)
        self.assertEqual(text.envelope.text, "文本消息")

        transcript, _ = self.process({"voice_item": {"text": "语音转写"}})
        self.assertTrue(transcript.should_forward)
        self.assertEqual(transcript.envelope.text, "语音转写")

        corpus = {case.case_id: case for case in security_file_corpus()}
        matrix = (
            ("image", corpus["valid.image.png"], None, None),
            ("voice", corpus["valid.audio.silk"], None, None),
            ("video", corpus["valid.video.mp4"], None, None),
            (
                "file",
                corpus["valid.docx.1000-chars"],
                "synthetic-report.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
        first_file = None
        first_poll = None
        for kind, case, filename, mime in matrix:
            with self.subTest(kind=kind):
                item = self.media_item(
                    kind,
                    case.content,
                    filename=filename,
                    mime=mime,
                )
                outcome, poll_identity = self.process(item)
                self.assertTrue(outcome.should_forward)
                self.assertEqual(len(outcome.envelope.attachments), 1)
                self.assertEqual(outcome.envelope.attachments[0].sha256, case.sha256)
                self.assertEqual(outcome.envelope.text, "[attachment message]")
                if kind == "file":
                    first_file = outcome
                    first_poll = poll_identity

        calls_before_duplicate = self.simulator.call_count("media.open")
        duplicate, _ = self.process(
            self.media_item(
                "file",
                corpus["valid.docx.1000-chars"].content,
                filename="synthetic-report.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            same_poll=first_poll,
        )
        # The duplicate script remains unused because Inbox identity is checked first.
        self.assertTrue(duplicate.inbox_duplicate)
        self.assertEqual(duplicate.envelope.attachments, first_file.envelope.attachments)
        self.assertEqual(self.simulator.call_count("media.open"), calls_before_duplicate)

    def test_pseudo_mime_oversize_half_download_and_ssrf_are_persisted_but_not_forwarded(self):
        corpus = {case.case_id: case for case in security_file_corpus()}
        pseudo = corpus["invalid.image.pseudo-png"]
        cases = (
            self.media_item(
                "file",
                pseudo.content,
                filename="pseudo.png",
                mime="image/png",
            ),
            self.media_item(
                "file",
                b"small",
                filename="oversize.txt",
                mime="text/plain",
            ),
            self.media_item(
                "file",
                b"half-download",
                filename="half.txt",
                mime="text/plain",
                cipher_size=False,
                declared_length=10_000,
            ),
        )
        cases[1]["file_item"]["cipher_size"] = 536_870_913
        for item in cases:
            outcome, _ = self.process(item)
            self.assertFalse(outcome.should_forward)
            self.assertEqual(outcome.decision.classification, "ATTACHMENT_REJECTED")
            self.assertEqual(outcome.envelope.attachments, ())

        calls_before_ssrf = self.simulator.call_count("media.open")
        ssrf_payload = {
            "file_item": {
                "full_url": "https://127.0.0.1/internal",
                "aes_key": base64.b64encode(self.key).decode("ascii"),
                "size": 5,
                "filename": "ssrf.txt",
                "mime": "text/plain",
            }
        }
        outcome, _ = self.process(ssrf_payload)
        self.assertFalse(outcome.should_forward)
        self.assertEqual(outcome.decision.classification, "ATTACHMENT_REJECTED")
        self.assertEqual(self.simulator.call_count("media.open"), calls_before_ssrf)
        self.assertEqual(self.inbox.count_records(), 4)
        self.assertFalse(tuple(self.stage.glob("*.part")))


if __name__ == "__main__":
    unittest.main()
