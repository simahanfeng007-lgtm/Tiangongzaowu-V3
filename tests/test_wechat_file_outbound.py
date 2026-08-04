import base64
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from communication_service.delivery_ledger import DeliveryLedger
from communication_service.wechat_file_outbound import (
    WechatCdnUploadResponse,
    WechatFileDeliveryService,
    WechatFileOutboundError,
    build_wechat_upload_url,
    validate_wechat_upload_url,
)
from communication_service.wechat_session import WechatSessionLedger
from communication_service.wechat_text_outbound import (
    WechatIlinkResponse,
    WechatTextOutboundError,
    default_wechat_text_policy,
)
from contracts import OutboundPart, OutboundPlan
from tests.test_delivery_contracts import (
    artifact_manifest,
    consume_verified_delivery_for_test,
    delivery_ticket,
    outbound_plan,
)


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


class _Clock:
    def __init__(self, value=23_000):
        self.value = value
        self.sleeps = []

    def now(self):
        value = self.value
        self.value += 1
        return value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += max(1, int(seconds * 1_000))


class _Source:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def open_artifact(self, grant, *, timeout_seconds):
        self.calls.append((grant, timeout_seconds))
        return io.BytesIO(self.content)


def ilink_response(index, payload=None, *, status=200):
    return WechatIlinkResponse(
        status_code=status,
        payload=payload if payload is not None else {"ret": 0, "errcode": 0},
        body_sha256=f"{index:064x}",
    )


class _Transport:
    def __init__(self, *, upload_url_response=None, cdn_response=None, send_responses=None):
        self.upload_url_response = upload_url_response or ilink_response(
            1, {"ret": 0, "errcode": 0, "data": {"upload_param": "upload-secret"}}
        )
        self.cdn_response = cdn_response or WechatCdnUploadResponse(
            status_code=200,
            encrypted_query_param="encrypted-media-ref",
            body_sha256="2" * 64,
            bytes_sent=16,
        )
        self.send_responses = list(send_responses or [ilink_response(3)])
        self.get_calls = []
        self.upload_calls = []
        self.send_calls = []
        self.ciphertext = None

    def get_upload_url(self, body, *, bot_token, timeout_seconds):
        self.get_calls.append((body, bot_token, timeout_seconds))
        if isinstance(self.upload_url_response, Exception):
            raise self.upload_url_response
        return self.upload_url_response

    def upload_ciphertext(
        self,
        upload_url,
        ciphertext_path,
        *,
        ciphertext_size,
        timeout_seconds,
        max_response_bytes,
        progress_callback=None,
    ):
        self.ciphertext = ciphertext_path.read_bytes()
        self.upload_calls.append(
            (upload_url, ciphertext_size, timeout_seconds, max_response_bytes)
        )
        if isinstance(self.cdn_response, Exception):
            raise self.cdn_response
        if progress_callback is not None:
            progress_callback(ciphertext_size)
        return self.cdn_response.__class__(
            status_code=self.cdn_response.status_code,
            encrypted_query_param=self.cdn_response.encrypted_query_param,
            body_sha256=self.cdn_response.body_sha256,
            bytes_sent=ciphertext_size,
        )

    def send_message(self, body, *, bot_token, timeout_seconds):
        self.send_calls.append((body, bot_token, timeout_seconds))
        if not self.send_responses:
            raise AssertionError("unexpected send")
        response = self.send_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def file_plan(content, policy, *, filename="report.docx"):
    digest = hashlib.sha256(content).hexdigest()
    artifact = artifact_manifest(
        sha256=digest,
        size_bytes=len(content),
        filename=filename,
    )
    part = OutboundPart(
        part_id="part_artifact_001",
        index=0,
        kind="artifact",
        artifact=artifact,
    )
    base = outbound_plan()
    values = base.model_dump(mode="python")
    values.update(
        {
            "parts": (part,),
            "channel_policy_hash": policy.policy_sha256,
            "plan_sha256": "0" * 64,
        }
    )
    return OutboundPlan(**values).with_computed_plan_sha256()


def ticket_for(plan):
    return delivery_ticket(
        plan=plan,
        allow_text=False,
        allow_files=True,
        max_text_parts=0,
        max_file_parts=len(plan.parts),
    )


class WechatFileOutboundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.staging = root / "staging"
        self.ledger = DeliveryLedger.open(root / "delivery.sqlite3", now_ms=1_000)
        self.sessions = WechatSessionLedger.open(
            root / "sessions.sqlite3", now_ms=1_000, protector=_Protector()
        )
        self.clock = _Clock()
        self.policy = default_wechat_text_policy().model_copy(
            update={"min_attempt_interval_ms": 0, "policy_sha256": "0" * 64}
        ).with_computed_sha256()

    def tearDown(self):
        self.sessions.close()
        self.ledger.close()
        self.temporary.cleanup()

    def bind_session(self, plan, *, token="context-secret"):
        return self.sessions.decide(
            account_id="ilink-account",
            sender_ref="wxuser_" + "1" * 64,
            conversation_scope_hash=plan.conversation_scope_hash,
            message_ref="wxmsg_" + "2" * 64,
            message_fingerprint="3" * 64,
            envelope_sha256="4" * 64,
            preliminary_classification="ACCEPTED",
            recipient_user_id="raw-recipient-secret",
            sequence=1,
            received_at_ms=2_000,
            incoming_context_token=token,
        ).decision.session_key

    def service(self, source, transport):
        return WechatFileDeliveryService(
            self.ledger,
            self.sessions,
            source,
            transport,
            staging_root=self.staging,
            clock_ms=self.clock.now,
            sleeper=self.clock.sleep,
        )

    def send(self, service, ticket, plan, session_key):
        consume_verified_delivery_for_test(self.ledger, ticket, at_ms=22_000)
        return service.send(
            ticket.payload,
            plan,
            policy=self.policy,
            bot_token="bot-token",
            ilink_account_id="ilink-account",
            session_key=session_key,
        )

    def test_file_roundtrip_stages_aes_and_duplicate_without_paths(self):
        content = b"real-docx-bytes-for-wechat"
        plan = file_plan(content, self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        source = _Source(content)
        transport = _Transport()
        service = self.service(source, transport)

        receipt = self.send(service, ticket, plan, session_key)

        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual(receipt.parts[0].stage, "CHANNEL_ACCEPTED")
        self.assertEqual(
            [fact.stage for fact in self.ledger.list_part_stages(plan.effect_id)],
            [
                "FETCHED",
                "ENCRYPTED",
                "UPLOAD_URL_GRANTED",
                "UPLOADED",
                "SEND_STARTED",
                "CHANNEL_ACCEPTED",
            ],
        )
        progress = self.ledger.list_transfer_progress(plan.effect_id)
        self.assertEqual([fact.phase for fact in progress], ["FETCH", "ENCRYPT", "UPLOAD"])
        self.assertTrue(all(fact.bytes_completed == fact.total_bytes for fact in progress))
        upload_body = transport.get_calls[0][0]
        self.assertEqual(upload_body["rawsize"], len(content))
        self.assertEqual(upload_body["rawfilemd5"], hashlib.md5(content).hexdigest())
        self.assertEqual(upload_body["filesize"], len(transport.ciphertext))
        self.assertEqual(source.calls[0][1], 8)
        self.assertEqual(transport.get_calls[0][2], 8)
        self.assertEqual(transport.upload_calls[0][2], 8)
        media = transport.send_calls[0][0]["msg"]["item_list"][0]["file_item"]["media"]
        key = bytes.fromhex(base64.b64decode(media["aes_key"]).decode("ascii"))
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(transport.ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        self.assertEqual(unpadder.update(padded) + unpadder.finalize(), content)
        self.assertFalse(tuple(self.staging.iterdir()))
        duplicate = self.send(service, ticket, plan, session_key)
        self.assertEqual(duplicate, receipt)
        self.assertEqual(len(transport.send_calls), 1)

    def test_source_hash_mismatch_fails_retryable_before_network_and_cleans_stage(self):
        plan = file_plan(b"expected", self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        source = _Source(b"tampered")
        transport = _Transport()
        receipt = self.send(self.service(source, transport), ticket, plan, session_key)
        self.assertEqual(receipt.status, "FAILED_RETRYABLE")
        self.assertFalse(transport.get_calls)
        self.assertEqual(
            [fact.stage for fact in self.ledger.list_part_stages(plan.effect_id)],
            ["FAILED_RETRYABLE"],
        )
        self.assertFalse(tuple(self.staging.iterdir()))

    def test_malicious_upload_full_url_is_rejected_without_cdn_or_send(self):
        content = b"artifact"
        plan = file_plan(content, self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport(
            upload_url_response=ilink_response(
                1,
                {
                    "ret": 0,
                    "data": {
                        "upload_full_url": "https://novac2c.cdn.weixin.qq.com.evil.test/c2c/upload?encrypted_query_param=x&filekey=y"
                    },
                },
            )
        )
        receipt = self.send(
            self.service(_Source(content), transport), ticket, plan, session_key
        )
        self.assertEqual(receipt.status, "FAILED_RETRYABLE")
        self.assertFalse(transport.upload_calls)
        self.assertFalse(transport.send_calls)

    def test_cdn_failure_is_retryable_because_sendmessage_never_started(self):
        content = b"artifact"
        plan = file_plan(content, self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport(cdn_response=WechatFileOutboundError("cdn-down"))
        receipt = self.send(
            self.service(_Source(content), transport), ticket, plan, session_key
        )
        self.assertEqual(receipt.status, "FAILED_RETRYABLE")
        self.assertFalse(transport.send_calls)

    def test_unknown_send_result_is_ambiguous_and_not_retried(self):
        content = b"artifact"
        plan = file_plan(content, self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport(
            send_responses=[
                WechatTextOutboundError("wechat.file.send.transport.unknown", outcome_unknown=True)
            ]
        )
        service = self.service(_Source(content), transport)
        receipt = self.send(service, ticket, plan, session_key)
        self.assertEqual(receipt.status, "RECONCILE_REQUIRED")
        self.assertEqual(receipt.parts[0].stage, "AMBIGUOUS")
        duplicate = self.send(service, ticket, plan, session_key)
        self.assertEqual(duplicate, receipt)
        self.assertEqual(len(transport.send_calls), 1)

    def test_context_expiry_reuses_client_id_and_clears_token(self):
        content = b"artifact"
        plan = file_plan(content, self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport(
            send_responses=[ilink_response(3, {"ret": -14}), ilink_response(4)]
        )
        receipt = self.send(
            self.service(_Source(content), transport), ticket, plan, session_key
        )
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        first = transport.send_calls[0][0]["msg"]
        second = transport.send_calls[1][0]["msg"]
        self.assertEqual(first["client_id"], second["client_id"])
        self.assertEqual(first["context_token"], "context-secret")
        self.assertIsNone(second["context_token"])

    def test_restart_after_external_boundary_never_reuploads_or_resends(self):
        content = b"artifact"
        plan = file_plan(content, self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        consume_verified_delivery_for_test(self.ledger, ticket, at_ms=22_000)
        self.ledger.mark_side_effect_started(plan.effect_id, started_at_ms=22_500)
        transport = _Transport(send_responses=[])
        receipt = self.send(
            self.service(_Source(content), transport), ticket, plan, session_key
        )
        self.assertEqual(receipt.status, "RECONCILE_REQUIRED")
        self.assertFalse(transport.get_calls)
        self.assertFalse(transport.upload_calls)
        self.assertFalse(transport.send_calls)

    def test_upload_url_allowlist_and_filekey_binding(self):
        valid = build_wechat_upload_url("abc+/=", filekey="file-key")
        self.assertEqual(validate_wechat_upload_url(valid, filekey="file-key"), valid)
        with self.assertRaises(WechatFileOutboundError):
            validate_wechat_upload_url(valid, filekey="other")
        with self.assertRaises(WechatFileOutboundError):
            validate_wechat_upload_url(
                "http://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=x&filekey=file-key",
                filekey="file-key",
            )

    def test_too_short_upload_budget_refuses_before_fetch_or_network(self):
        content = b"artifact"
        plan = file_plan(content, self.policy)
        ticket = ticket_for(plan)
        payload = ticket.payload.model_copy(update={"upload_timeout_ms": 1_000})
        session_key = self.bind_session(plan)
        source = _Source(content)
        transport = _Transport()
        consume_verified_delivery_for_test(self.ledger, payload, at_ms=22_000)
        receipt = self.service(source, transport).send(
            payload,
            plan,
            policy=self.policy,
            bot_token="bot-token",
            ilink_account_id="ilink-account",
            session_key=session_key,
        )
        self.assertEqual(receipt.status, "FAILED_RETRYABLE")
        self.assertEqual(receipt.error_code, "wechat.file.dynamic_timeout.refused")
        self.assertFalse(source.calls)
        self.assertFalse(transport.get_calls)


if __name__ == "__main__":
    unittest.main()
