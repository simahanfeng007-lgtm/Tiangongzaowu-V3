import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from communication_service.feishu_inbound import (
    FeishuEventRecord,
    FeishuInboundError,
    FeishuInboundPolicy,
    FeishuInboundProcessor,
)
from communication_service.feishu_route import FeishuRouteLedger
from communication_service.inbox import CommunicationInbox
from contracts import AttachmentRef, canonical_json_bytes


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


def event(
    identity="event-1",
    *,
    message_id="message-1",
    sender="user-open-1",
    chat_id="chat-1",
    chat_type="p2p",
    message_type="text",
    content=None,
    mentions=None,
    root_id="",
    parent_id="",
    thread_id="",
    tenant_key="tenant-key-a",
    app_id="app-a",
):
    if content is None:
        content = {"text": "你好，飞书"}
    return {
        "header": {
            "event_id": identity,
            "event_type": "im.message.receive_v1",
            "app_id": app_id,
            "tenant_key": tenant_key,
        },
        "event": {
            "sender": {
                "sender_id": {
                    "open_id": sender,
                    "user_id": "",
                    "union_id": "",
                },
                "sender_type": "user",
                "tenant_key": tenant_key,
            },
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "message_type": message_type,
                "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                "mentions": [] if mentions is None else mentions,
                "root_id": root_id,
                "parent_id": parent_id,
                "thread_id": thread_id,
            },
        },
    }


class FeishuInboundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.inbox = CommunicationInbox.open(root / "inbox.sqlite3", now_ms=1_000)
        self.routes = FeishuRouteLedger.open(
            root / "routes.sqlite3", now_ms=1_000, protector=_Protector()
        )
        self.processor = FeishuInboundProcessor(self.inbox, self.routes)
        self.policy = FeishuInboundPolicy(
            tenant_id="tenant-a",
            link_account_id="feishu-account-a",
            app_id="app-a",
            platform_tenant_key="tenant-key-a",
            bot_open_ids=("bot-open-a",),
            allow_groups=True,
        )

    def tearDown(self):
        self.routes.close()
        self.inbox.close()
        self.temporary.cleanup()

    @staticmethod
    def record(identity="1", *, raw=None, captured=2_000, verified=True):
        payload = canonical_json_bytes(event() if raw is None else raw)
        return FeishuEventRecord(
            raw_payload_object_id="raw-feishu-" + identity,
            raw_payload_sha256=hashlib.sha256(payload).hexdigest(),
            raw_payload_size_bytes=len(payload),
            signature_verified=verified,
            app_id_verified=verified,
            captured_at_ms=captured,
            persisted_at_ms=captured + 1,
        )

    def test_text_is_persisted_before_ack_deduplicated_and_route_is_encrypted(self):
        raw = event()
        first = self.processor.process(raw, policy=self.policy, record=self.record())
        self.assertTrue(first.should_forward)
        self.assertFalse(first.duplicate)
        self.assertEqual(first.envelope.text, "你好，飞书")
        self.assertEqual(first.envelope.channel, "feishu")
        self.assertIsNotNone(first.route_key)
        route = self.routes.resolve(
            route_key=first.route_key,
            tenant_id=self.policy.tenant_id,
            link_account_id=self.policy.link_account_id,
            conversation_scope_hash=first.envelope.conversation_scope_hash,
        )
        self.assertEqual(route.chat_id, "chat-1")
        self.assertEqual(route.message_id, "message-1")
        database = self.routes.path.read_bytes()
        self.assertNotIn(b"chat-1", database)
        self.assertNotIn(b"message-1", database)
        duplicate = self.processor.process(raw, policy=self.policy, record=self.record())
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.ack_permit, first.ack_permit)
        self.assertEqual(self.inbox.count_records(), 1)

    def test_post_thread_root_and_mentions_are_scoped_without_href_leak(self):
        content = {
            "zh_cn": {
                "title": "标题",
                "content": [
                    [
                        {"tag": "text", "text": "请看"},
                        {"tag": "a", "text": "文档", "href": "https://secret.invalid"},
                        {"tag": "at", "user_id": "bot-open-a", "user_name": "机器人"},
                    ]
                ],
            }
        }
        raw = event(
                chat_type="group",
                message_type="post",
                content=content,
                root_id="root-1",
                parent_id="parent-1",
                thread_id="thread-1",
            )
        outcome = self.processor.process(
            raw,
            policy=self.policy,
            record=self.record(raw=raw),
        )
        self.assertTrue(outcome.should_forward)
        self.assertIn("标题", outcome.envelope.text)
        self.assertIn("请看文档@机器人", outcome.envelope.text)
        self.assertNotIn("secret.invalid", outcome.envelope.text)
        self.assertTrue(outcome.envelope.reply_to_message_ref.startswith("fsmsg_"))
        self.assertTrue(outcome.envelope.root_message_ref.startswith("fsmsg_"))
        self.assertTrue(outcome.envelope.conversation_ref.startswith("fsconv_"))

    def test_sdk_nested_mention_identity_is_recognized(self):
        raw = event(
            chat_type="group",
            mentions=[
                {
                    "key": "@_user_1",
                    "id": {"open_id": "bot-open-a", "user_id": "", "union_id": ""},
                    "name": "机器人",
                    "tenant_key": "tenant-key-a",
                }
            ],
        )
        outcome = self.processor.process(
            raw, policy=self.policy, record=self.record(raw=raw)
        )
        self.assertEqual(outcome.classification, "ACCEPTED")
        self.assertTrue(outcome.should_forward)

    def test_post_embedded_image_is_registered_before_forwarding(self):
        content = {
            "zh_cn": {
                "title": "图片",
                "content": [
                    [
                        {"tag": "text", "text": "请查看"},
                        {"tag": "img", "image_key": "img-key-post"},
                    ]
                ],
            }
        }
        raw = event(message_type="post", content=content)
        outcome = self.processor.process(
            raw, policy=self.policy, record=self.record(raw=raw)
        )
        self.assertEqual(outcome.classification, "ATTACHMENT_PENDING")
        self.assertFalse(outcome.should_forward)
        self.assertEqual(len(outcome.resource_ids), 1)
        resource = self.routes.resolve_resource(
            resource_id=outcome.resource_ids[0],
            tenant_id=self.policy.tenant_id,
            link_account_id=self.policy.link_account_id,
            conversation_scope_hash=outcome.envelope.conversation_scope_hash,
        )
        self.assertEqual(resource.resource_type, "image")
        self.assertEqual(resource.resource_key, "img-key-post")

    def test_production_attachment_loader_finalizes_envelope_before_ack(self):
        calls = []

        def load(resource_id, **scope):
            calls.append((resource_id, scope))
            return AttachmentRef(
                object_id="feishu_attachment_object",
                revision=1,
                sha256="a" * 64,
                size_bytes=123,
                mime="application/pdf",
                filename="报告.pdf",
                tenant_id=scope["tenant_id"],
                link_account_id=scope["link_account_id"],
                conversation_scope_hash=scope["conversation_scope_hash"],
                source_message_ref="fsmsg_" + hashlib.sha256(b"message-1").hexdigest(),
                created_at_ms=2_000,
            )

        processor = FeishuInboundProcessor(
            self.inbox,
            self.routes,
            attachment_loader=load,
        )
        raw = event(
            message_type="file",
            content={"file_key": "file-key-production", "file_name": "报告.pdf"},
        )
        outcome = processor.process(
            raw,
            policy=self.policy,
            record=self.record(raw=raw),
        )

        self.assertEqual(outcome.classification, "ACCEPTED")
        self.assertTrue(outcome.should_forward)
        self.assertEqual(len(outcome.envelope.attachments), 1)
        self.assertEqual(outcome.envelope.attachments[0].filename, "报告.pdf")
        self.assertEqual(len(calls), 1)
        duplicate = processor.process(
            raw,
            policy=self.policy,
            record=self.record(raw=raw),
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(len(calls), 1)

    def test_persisted_raw_payload_digest_and_size_are_enforced(self):
        raw = event()
        swapped = event(content={"text": "被替换的内容"})
        with self.assertRaisesRegex(FeishuInboundError, "persisted payload"):
            self.processor.process(
                swapped,
                policy=self.policy,
                record=self.record(raw=raw),
            )
        self.assertEqual(self.inbox.count_records(), 0)

    def test_group_without_mention_and_self_message_are_persisted_but_withheld(self):
        group_raw = event(chat_type="group")
        group = self.processor.process(
            group_raw, policy=self.policy, record=self.record(raw=group_raw)
        )
        self.assertEqual(group.classification, "GROUP_MENTION_REQUIRED")
        self.assertFalse(group.should_forward)
        self.assertIsNone(group.route_key)
        self_raw = event("event-2", message_id="message-2", sender="bot-open-a")
        self_message = self.processor.process(
            self_raw,
            policy=self.policy,
            record=self.record("2", raw=self_raw, captured=3_000),
        )
        self.assertEqual(self_message.classification, "SELF_MESSAGE")
        self.assertFalse(self_message.should_forward)
        self.assertEqual(self.inbox.count_records(), 2)

    def test_unverified_wrong_app_or_wrong_tenant_is_rejected_before_inbox(self):
        with self.assertRaises(FeishuInboundError):
            self.processor.process(
                event(), policy=self.policy, record=self.record(verified=False)
            )
        wrong_app = event(app_id="other")
        with self.assertRaises(FeishuInboundError):
            self.processor.process(
                wrong_app,
                policy=self.policy,
                record=self.record("2", raw=wrong_app),
            )
        wrong_tenant = event(tenant_key="other")
        with self.assertRaises(FeishuInboundError):
            self.processor.process(
                wrong_tenant,
                policy=self.policy,
                record=self.record("3", raw=wrong_tenant),
            )
        self.assertEqual(self.inbox.count_records(), 0)

    def test_file_event_is_durable_and_protected_for_attachment_stage(self):
        raw = event(
            message_type="file",
            content={"file_key": "file-key-1", "file_name": "报告.docx"},
        )
        outcome = self.processor.process(
            raw,
            policy=self.policy,
            record=self.record(raw=raw),
        )
        self.assertEqual(outcome.classification, "ATTACHMENT_PENDING")
        self.assertFalse(outcome.should_forward)
        self.assertIsNotNone(outcome.route_key)
        self.assertEqual(len(outcome.resource_ids), 1)
        protected = self.routes.resolve_resource(
            resource_id=outcome.resource_ids[0],
            tenant_id=self.policy.tenant_id,
            link_account_id=self.policy.link_account_id,
            conversation_scope_hash=outcome.envelope.conversation_scope_hash,
        )
        self.assertEqual(protected.resource_type, "file")
        self.assertEqual(protected.filename, "报告.docx")
        database = self.routes.path.read_bytes()
        self.assertNotIn(b"file-key-1", database)
        self.assertEqual(self.inbox.count_records(), 1)

    def test_old_duplicate_after_new_message_returns_original_ack_without_route_rollback(self):
        first_raw = event()
        first = self.processor.process(
            first_raw, policy=self.policy, record=self.record(captured=2_000)
        )
        second = self.processor.process(
            (second_raw := event("event-2", message_id="message-2")),
            policy=self.policy,
            record=self.record("2", raw=second_raw, captured=3_000),
        )
        duplicate = self.processor.process(
            first_raw, policy=self.policy, record=self.record(captured=2_000)
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.ack_permit, first.ack_permit)
        route = self.routes.resolve(
            route_key=second.route_key,
            tenant_id=self.policy.tenant_id,
            link_account_id=self.policy.link_account_id,
            conversation_scope_hash=second.envelope.conversation_scope_hash,
        )
        self.assertEqual(route.message_id, "message-2")

    def test_same_platform_ids_in_different_tenant_account_have_different_scope(self):
        first = self.processor.process(event(), policy=self.policy, record=self.record())
        other_policy = FeishuInboundPolicy(
            tenant_id="tenant-b",
            link_account_id="feishu-account-b",
            app_id="app-b",
            platform_tenant_key="tenant-key-b",
            bot_open_ids=("bot-open-b",),
        )
        second_raw = event(app_id="app-b", tenant_key="tenant-key-b")
        second = self.processor.process(
            second_raw,
            policy=other_policy,
            record=self.record("b", raw=second_raw, captured=3_000),
        )
        self.assertNotEqual(first.envelope.idempotency_key, second.envelope.idempotency_key)
        self.assertNotEqual(first.envelope.conversation_scope_hash, second.envelope.conversation_scope_hash)


if __name__ == "__main__":
    unittest.main()
