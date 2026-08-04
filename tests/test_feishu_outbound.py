import hashlib
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path

from communication_service.delivery_ledger import DeliveryLedger
from communication_service.feishu_outbound import (
    FeishuApiResponse,
    FeishuCredentials,
    FeishuDeliveryService,
    FeishuOutboundError,
    FeishuTokenProvider,
    FeishuTokenResult,
    default_feishu_outbound_policy,
    derive_feishu_dedup_uuid,
)
from communication_service.feishu_route import FeishuRouteLedger
from contracts import (
    OutboundPart,
    OutboundPlan,
    OutboundScope,
    derive_outbound_scope_keys,
    text_sha256,
)
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
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            value = self.value
            self.value += 1
            return value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += max(1, int(seconds * 1_000))


class _Source:
    def __init__(self, content=b"artifact-bytes"):
        self.content = content
        self.calls = []

    def open_artifact(self, grant, *, timeout_seconds):
        self.calls.append((grant, timeout_seconds))
        return io.BytesIO(self.content)


def api(index, *, status=200, code=0, data=None, retry_after=None):
    return FeishuApiResponse(
        status_code=status,
        code=code,
        data={} if data is None else data,
        body_sha256=f"{index:064x}",
        retry_after_ms=retry_after,
    )


class _Transport:
    def __init__(self, *, tokens=None, uploads=None, sends=None):
        self.tokens = list(tokens or ["token-1"])
        self.uploads = list(uploads or [])
        self.sends = list(sends or [])
        self.token_calls = []
        self.upload_calls = []
        self.send_calls = []

    def fetch_tenant_token(self, credentials, *, timeout_seconds):
        self.token_calls.append((credentials.app_id, timeout_seconds))
        value = self.tokens.pop(0)
        if isinstance(value, Exception):
            raise value
        return FeishuTokenResult(200, 0, value, 7_200, "a" * 64)

    def upload_artifact(
        self,
        artifact,
        grant,
        *,
        as_image,
        access_token,
        timeout_seconds,
        max_response_bytes,
    ):
        self.upload_calls.append(
            (
                artifact.path.read_bytes(),
                grant,
                as_image,
                access_token,
                timeout_seconds,
                max_response_bytes,
            )
        )
        value = self.uploads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def send_message(self, **kwargs):
        self.send_calls.append(kwargs)
        value = self.sends.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def feishu_plan(
    policy,
    *,
    text="已完成",
    artifact_content=None,
    artifact_mime="text/plain",
    artifact_filename="report.txt",
    reply=True,
    include_text=True,
):
    conversation_ref = "fsconv_" + "1" * 64
    reply_ref = "fsmsg_" + hashlib.sha256(b"message-1").hexdigest() if reply else None
    scope = OutboundScope(
        channel="feishu",
        tenant_id="tenant_001",
        link_account_id="feishu_001",
        conversation_ref=conversation_ref,
        recipient_ref=conversation_ref,
        reply_to_message_ref=reply_ref,
    )
    keys = derive_outbound_scope_keys(scope)
    parts = []
    if include_text:
        parts.append(
            OutboundPart(
                part_id="part_text_001",
                index=0,
                kind="text",
                text=text,
                text_sha256=text_sha256(text),
            )
        )
    if artifact_content is not None:
        digest = hashlib.sha256(artifact_content).hexdigest()
        artifact = artifact_manifest(
            tenant_id="tenant_001",
            link_account_id="feishu_001",
            conversation_scope_hash=keys.conversation_scope_hash,
            sha256=digest,
            size_bytes=len(artifact_content),
            filename=artifact_filename,
            mime=artifact_mime,
            artifact_kind="image" if artifact_mime.startswith("image/") else "data",
            format_id="png" if artifact_mime == "image/png" else "text",
        )
        parts.append(
            OutboundPart(
                part_id="part_artifact_001",
                index=len(parts),
                kind="artifact",
                artifact=artifact,
            )
        )
    base = outbound_plan()
    values = base.model_dump(mode="python")
    values.update(
        {
            "channel": "feishu",
            "link_account_id": "feishu_001",
            "conversation_ref": conversation_ref,
            "conversation_scope_hash": keys.conversation_scope_hash,
            "recipient_scope_hash": keys.recipient_scope_hash,
            "reply_to_message_ref": reply_ref,
            "channel_policy_hash": policy.policy_sha256,
            "parts": tuple(parts),
            "plan_sha256": "0" * 64,
        }
    )
    return OutboundPlan(**values).with_computed_plan_sha256()


def ticket_for(plan):
    text_parts = sum(part.kind == "text" for part in plan.parts)
    file_parts = sum(part.kind == "artifact" for part in plan.parts)
    return delivery_ticket(
        plan=plan,
        allow_text=bool(text_parts),
        allow_files=bool(file_parts),
        max_text_parts=text_parts,
        max_file_parts=file_parts,
    )


class FeishuTokenTests(unittest.TestCase):
    def test_concurrent_cache_miss_has_exactly_one_token_refresh(self):
        class SlowTransport(_Transport):
            def fetch_tenant_token(self, credentials, *, timeout_seconds):
                result = super().fetch_tenant_token(
                    credentials, timeout_seconds=timeout_seconds
                )
                time.sleep(0.05)
                return result

        transport = SlowTransport(tokens=["single-token"])
        provider = FeishuTokenProvider(
            transport, clock_ms=lambda: 1_000, refresh_skew_ms=1_000
        )
        credentials = FeishuCredentials("app-id", "app-secret")
        barrier = threading.Barrier(32)
        results = []
        errors = []

        def worker():
            try:
                barrier.wait()
                results.append(
                    provider.get_token(
                        "tenant:account", credentials, timeout_seconds=2
                    )
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(32)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(results, ["single-token"] * 32)
        self.assertEqual(len(transport.token_calls), 1)


class FeishuOutboundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.staging = self.root / "staging"
        self.ledger = DeliveryLedger.open(
            self.root / "delivery.sqlite3", now_ms=1_000
        )
        self.routes = FeishuRouteLedger.open(
            self.root / "routes.sqlite3", now_ms=1_000, protector=_Protector()
        )
        self.clock = _Clock()
        self.policy = default_feishu_outbound_policy().model_copy(
            update={"rate_limit_delay_ms": 100, "policy_sha256": "0" * 64}
        ).with_computed_sha256()

    def tearDown(self):
        self.routes.close()
        self.ledger.close()
        self.temporary.cleanup()

    def bind_route(self, plan):
        return self.routes.upsert(
            tenant_id=plan.tenant_id,
            link_account_id=plan.link_account_id,
            conversation_scope_hash=plan.conversation_scope_hash,
            chat_id="chat-1",
            message_id="message-1",
            root_id="root-1",
            parent_id="parent-1",
            thread_id="thread-1",
            sender_open_id="user-open-1",
            observed_at_ms=2_000,
        )

    def service(self, source, transport):
        tokens = FeishuTokenProvider(
            transport,
            clock_ms=self.clock.now,
            refresh_skew_ms=self.policy.token_refresh_skew_ms,
        )
        return FeishuDeliveryService(
            self.ledger,
            self.routes,
            source,
            transport,
            tokens,
            staging_root=self.staging,
            clock_ms=self.clock.now,
            sleeper=self.clock.sleep,
        )

    def send(self, service, ticket, plan, *, policy=None):
        consume_verified_delivery_for_test(self.ledger, ticket, at_ms=22_000)
        return service.send(
            ticket.payload,
            plan,
            policy=policy or self.policy,
            credentials=FeishuCredentials("app-id", "app-secret"),
        )

    def test_mixed_text_and_file_reply_is_accepted_with_durable_stages_and_no_resend(self):
        content = b"artifact-bytes"
        plan = feishu_plan(self.policy, artifact_content=content)
        ticket = ticket_for(plan)
        self.bind_route(plan)
        transport = _Transport(
            uploads=[api(1, data={"file_key": "uploaded-file-key"})],
            sends=[
                api(2, data={"message_id": "sent-text"}),
                api(3, data={"message_id": "sent-file"}),
            ],
        )
        source = _Source(content)
        service = self.service(source, transport)

        receipt = self.send(service, ticket, plan)

        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual([part.stage for part in receipt.parts], ["CHANNEL_ACCEPTED"] * 2)
        self.assertEqual(len(transport.token_calls), 1)
        self.assertEqual(len(transport.upload_calls), 1)
        self.assertEqual(len(transport.send_calls), 2)
        self.assertTrue(all(call["reply_in_thread"] for call in transport.send_calls))
        self.assertTrue(all(call["reply_to_message_id"] == "message-1" for call in transport.send_calls))
        self.assertEqual(transport.send_calls[1]["content"], {"file_key": "uploaded-file-key"})
        self.assertEqual(
            [call["dedup_uuid"] for call in transport.send_calls],
            [
                derive_feishu_dedup_uuid(plan.effect_id, part.part_id)
                for part in plan.parts
            ],
        )
        stages = [fact.stage for fact in self.ledger.list_part_stages(plan.effect_id)]
        self.assertEqual(
            stages,
            [
                "SEND_STARTED",
                "CHANNEL_ACCEPTED",
                "FETCHED",
                "READY_TO_UPLOAD",
                "UPLOADED",
                "SEND_STARTED",
                "CHANNEL_ACCEPTED",
            ],
        )
        self.assertEqual(list(self.staging.iterdir()), [])
        duplicate = self.send(service, ticket, plan)
        self.assertEqual(duplicate, receipt)
        self.assertEqual(len(transport.send_calls), 2)

    def test_card_mode_is_policy_bound_and_sent_as_interactive(self):
        policy = self.policy.model_copy(
            update={"text_mode": "interactive_card", "policy_sha256": "0" * 64}
        ).with_computed_sha256()
        plan = feishu_plan(policy, text="**结果**")
        ticket = ticket_for(plan)
        self.bind_route(plan)
        transport = _Transport(sends=[api(1, data={"message_id": "sent-card"})])
        receipt = self.send(self.service(_Source(), transport), ticket, plan, policy=policy)
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        call = transport.send_calls[0]
        self.assertEqual(call["msg_type"], "interactive")
        self.assertEqual(call["content"]["elements"][0]["content"], "**结果**")

    def test_image_upload_uses_image_key_message(self):
        content = b"\x89PNG\r\n\x1a\nimageIEND\xaeB`\x82"
        plan = feishu_plan(
            self.policy,
            artifact_content=content,
            artifact_mime="image/png",
            artifact_filename="result.png",
            include_text=False,
        )
        ticket = ticket_for(plan)
        self.bind_route(plan)
        transport = _Transport(
            uploads=[api(1, data={"image_key": "uploaded-image-key"})],
            sends=[api(2, data={"message_id": "sent-image"})],
        )
        receipt = self.send(
            self.service(_Source(content), transport), ticket, plan
        )
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertTrue(transport.upload_calls[0][2])
        self.assertEqual(transport.send_calls[0]["msg_type"], "image")
        self.assertEqual(
            transport.send_calls[0]["content"],
            {"image_key": "uploaded-image-key"},
        )

    def test_unauthorized_refreshes_once_and_429_obeys_retry_after(self):
        plan = feishu_plan(self.policy)
        ticket = ticket_for(plan)
        self.bind_route(plan)
        transport = _Transport(
            tokens=["token-1", "token-2"],
            sends=[
                api(1, status=401, code=99991663),
                api(2, status=429, code=99991400, retry_after=1_500),
                api(3, data={"message_id": "sent-after-retry"}),
            ],
        )
        receipt = self.send(self.service(_Source(), transport), ticket, plan)
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual(len(transport.token_calls), 2)
        self.assertEqual(
            [call["access_token"] for call in transport.send_calls],
            ["token-1", "token-2", "token-2"],
        )
        self.assertEqual(len({call["dedup_uuid"] for call in transport.send_calls}), 1)
        self.assertEqual(self.clock.sleeps, [1.5])

    def test_unknown_send_is_reconcile_required_and_never_replayed(self):
        plan = feishu_plan(self.policy)
        ticket = ticket_for(plan)
        self.bind_route(plan)
        transport = _Transport(
            sends=[FeishuOutboundError("feishu.transport.unknown", outcome_unknown=True)]
        )
        service = self.service(_Source(), transport)
        receipt = self.send(service, ticket, plan)
        self.assertEqual(receipt.status, "RECONCILE_REQUIRED")
        self.assertEqual(receipt.parts[0].stage, "AMBIGUOUS")
        duplicate = self.send(service, ticket, plan)
        self.assertEqual(duplicate, receipt)
        self.assertEqual(len(transport.send_calls), 1)

    def test_recipient_or_reply_scope_mismatch_is_rejected_before_token_or_send(self):
        plan = feishu_plan(self.policy)
        self.bind_route(plan)
        transport = _Transport(sends=[api(1, data={"message_id": "unexpected"})])
        service = self.service(_Source(), transport)
        wrong_recipient = plan.model_copy(
            update={"recipient_scope_hash": "f" * 64, "plan_sha256": "0" * 64}
        ).with_computed_plan_sha256()
        wrong_recipient_ticket = ticket_for(wrong_recipient)
        with self.assertRaises(FeishuOutboundError):
            service.send(
                wrong_recipient_ticket.payload,
                wrong_recipient,
                policy=self.policy,
                credentials=FeishuCredentials("app-id", "app-secret"),
            )
        wrong_reply = plan.model_copy(
            update={"reply_to_message_ref": "fsmsg_" + "e" * 64, "plan_sha256": "0" * 64}
        ).with_computed_plan_sha256()
        # Recompute recipient scope so the route-specific reply check is the rejecting gate.
        scope = OutboundScope(
            channel="feishu",
            tenant_id=wrong_reply.tenant_id,
            link_account_id=wrong_reply.link_account_id,
            conversation_ref=wrong_reply.conversation_ref,
            recipient_ref=wrong_reply.conversation_ref,
            reply_to_message_ref=wrong_reply.reply_to_message_ref,
        )
        keys = derive_outbound_scope_keys(scope)
        wrong_reply = wrong_reply.model_copy(
            update={
                "recipient_scope_hash": keys.recipient_scope_hash,
                "plan_sha256": "0" * 64,
            }
        ).with_computed_plan_sha256()
        wrong_reply_ticket = ticket_for(wrong_reply)
        with self.assertRaisesRegex(FeishuOutboundError, "reply_target"):
            service.send(
                wrong_reply_ticket.payload,
                wrong_reply,
                policy=self.policy,
                credentials=FeishuCredentials("app-id", "app-secret"),
            )
        self.assertEqual(transport.token_calls, [])
        self.assertEqual(transport.send_calls, [])

    def test_upload_rejection_is_retryable_without_recipient_send_and_cleans_stage(self):
        content = b"artifact-bytes"
        policy = self.policy.model_copy(
            update={"rate_limit_retries": 0, "policy_sha256": "0" * 64}
        ).with_computed_sha256()
        plan = feishu_plan(
            policy, artifact_content=content, include_text=False
        )
        ticket = ticket_for(plan)
        self.bind_route(plan)
        transport = _Transport(
            uploads=[api(1, status=429, code=99991400)],
            sends=[],
        )
        receipt = self.send(
            self.service(_Source(content), transport), ticket, plan, policy=policy
        )
        self.assertEqual(receipt.status, "FAILED_RETRYABLE")
        self.assertEqual(len(transport.send_calls), 0)
        self.assertEqual(list(self.staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
