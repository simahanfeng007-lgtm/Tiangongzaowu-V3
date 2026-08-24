import hashlib
import tempfile
import unittest
from pathlib import Path

from communication_service.inbox import CommunicationInbox, cursor_token_sha256
from communication_service.wechat_inbound import (
    WechatInboundError,
    WechatInboundPolicy,
    WechatPollRecord,
    WechatTextInboundProcessor,
)
from communication_service.wechat_session import WechatSessionLedger


class _Protector:
    def protect(self, plaintext, entropy):
        key = hashlib.sha256(entropy).digest()
        return b"TEST" + bytes(value ^ key[index % len(key)] for index, value in enumerate(plaintext))

    def unprotect(self, ciphertext, entropy):
        if not ciphertext.startswith(b"TEST"):
            raise OSError("invalid protected data")
        key = hashlib.sha256(entropy).digest()
        return bytearray(
            value ^ key[index % len(key)] for index, value in enumerate(ciphertext[4:])
        )


def message(
    identity="message-1",
    *,
    sequence=1,
    sender="user-1",
    text="你好",
    context_token="context-secret-1",
    group_id="",
    mentioned_bot=False,
):
    value = {
        "message_type": 1,
        "message_id": identity,
        "seq": sequence,
        "client_id": "client-" + identity,
        "from_user_id": sender,
        "session_id": "session-1",
        "group_id": group_id,
        "mentioned_bot": mentioned_bot,
        "item_list": [{"text_item": {"text": text}}],
    }
    if context_token is not None:
        value["context_token"] = context_token
    return value


class WechatInboundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.inbox = CommunicationInbox.open(root / "inbox.sqlite3", now_ms=1_000)
        self.sessions = WechatSessionLedger.open(
            root / "wechat.sqlite3",
            now_ms=1_000,
            protector=_Protector(),
        )
        self.processor = WechatTextInboundProcessor(self.inbox, self.sessions)
        self.policy = WechatInboundPolicy(
            tenant_id="tenant-a",
            link_account_id="account-a",
            account_id="bot-account-a",
            self_user_ids=("bot-user-a",),
            allowed_sender_ids=(),
        )

    def tearDown(self):
        self.sessions.close()
        self.inbox.close()
        self.temporary.cleanup()

    @staticmethod
    def poll(
        raw_hash="a" * 64,
        *,
        previous=None,
        next_token="cursor-1",
        captured=2_000,
    ):
        return WechatPollRecord(
            raw_payload_object_id="raw-wechat-batch-1",
            raw_payload_sha256=raw_hash,
            raw_payload_size_bytes=100,
            previous_cursor_sha256=previous,
            next_cursor_token=next_token,
            captured_at_ms=captured,
            persisted_at_ms=captured + 1,
        )

    def test_text_is_scoped_persisted_and_duplicate_is_safe_to_reforward(self):
        raw = message()
        first = self.processor.process(raw, policy=self.policy, poll=self.poll())
        self.assertTrue(first.should_forward)
        self.assertFalse(first.inbox_duplicate)
        self.assertEqual(first.envelope.text, "你好")
        self.assertEqual(first.envelope.channel, "wechat")
        self.assertEqual(first.context_token, "context-secret-1")
        self.assertEqual(first.decision.context_token_source, "incoming")
        duplicate = self.processor.process(raw, policy=self.policy, poll=self.poll())
        self.assertTrue(duplicate.should_forward)
        self.assertTrue(duplicate.inbox_duplicate)
        self.assertTrue(duplicate.decision.duplicate)
        self.assertEqual(duplicate.context_token, "context-secret-1")
        self.assertEqual(self.inbox.count_records(), 1)

    def test_context_token_is_dpapi_style_protected_and_reused_after_restart(self):
        first = self.processor.process(message(), policy=self.policy, poll=self.poll())
        cursor = first.ack_permit.next_cursor_sha256
        second = self.processor.process(
            message("message-2", sequence=2, text="继续", context_token=None),
            policy=self.policy,
            poll=self.poll(
                raw_hash="b" * 64,
                previous=cursor,
                next_token="cursor-2",
                captured=3_000,
            ),
        )
        self.assertEqual(second.decision.context_token_source, "cache")
        self.assertEqual(second.context_token, "context-secret-1")
        database_bytes = self.sessions.path.read_bytes()
        self.assertNotIn(b"context-secret-1", database_bytes)
        self.sessions.close()
        self.sessions = WechatSessionLedger.open(
            self.sessions.path,
            now_ms=4_000,
            protector=_Protector(),
        )
        self.processor = WechatTextInboundProcessor(self.inbox, self.sessions)
        third = self.processor.process(
            message("message-3", sequence=3, text="再继续", context_token=None),
            policy=self.policy,
            poll=self.poll(
                raw_hash="c" * 64,
                previous=second.ack_permit.next_cursor_sha256,
                next_token="cursor-3",
                captured=4_000,
            ),
        )
        self.assertEqual(third.context_token, "context-secret-1")
        self.assertEqual(third.decision.context_token_source, "cache")

    def test_out_of_order_followup_is_persisted_but_not_forwarded_or_used_for_token(self):
        first = self.processor.process(
            message("message-2", sequence=2, context_token="new-token"),
            policy=self.policy,
            poll=self.poll(),
        )
        late = self.processor.process(
            message("message-1", sequence=1, context_token="stale-token"),
            policy=self.policy,
            poll=self.poll(
                raw_hash="b" * 64,
                previous=first.ack_permit.next_cursor_sha256,
                next_token="cursor-2",
                captured=3_000,
            ),
        )
        self.assertFalse(late.should_forward)
        self.assertEqual(late.decision.classification, "OUT_OF_ORDER")
        current = self.processor.process(
            message("message-3", sequence=3, context_token=None),
            policy=self.policy,
            poll=self.poll(
                raw_hash="c" * 64,
                previous=late.ack_permit.next_cursor_sha256,
                next_token="cursor-3",
                captured=4_000,
            ),
        )
        self.assertEqual(current.context_token, "new-token")

    def test_self_unexpected_and_group_policy_events_never_reach_gateway(self):
        cases = (
            (message(sender="bot-user-a"), self.policy, "SELF_MESSAGE"),
            (
                message(sender="intruder"),
                self.policy.model_copy(update={"allowed_sender_ids": ("user-1",)}),
                "UNEXPECTED_SENDER",
            ),
            (message(group_id="group-1"), self.policy, "GROUP_DISABLED"),
            (
                message(group_id="group-1"),
                self.policy.model_copy(update={"allow_group_messages": True}),
                "GROUP_MENTION_REQUIRED",
            ),
        )
        previous = None
        for index, (raw, policy, expected) in enumerate(cases, start=1):
            raw["message_id"] = f"blocked-{index}"
            raw["seq"] = index
            outcome = self.processor.process(
                raw,
                policy=policy,
                poll=self.poll(
                    raw_hash=f"{index}" * 64,
                    previous=previous,
                    next_token=f"cursor-{index}",
                    captured=2_000 + index,
                ),
            )
            self.assertFalse(outcome.should_forward)
            self.assertEqual(outcome.decision.classification, expected)
            previous = outcome.ack_permit.next_cursor_sha256

    def test_empty_cursor_voice_transcript_and_malformed_items_are_handled_explicitly(self):
        raw = message(text="")
        raw["item_list"] = [{"voice_item": {"text": "语音转写"}}]
        outcome = self.processor.process(
            raw,
            policy=self.policy,
            poll=self.poll(next_token=""),
        )
        self.assertEqual(outcome.envelope.text, "语音转写")
        self.assertEqual(outcome.ack_permit.next_cursor_sha256, cursor_token_sha256(""))
        bad = message("message-bad")
        bad["item_list"] = ["not-an-object"]
        with self.assertRaises(WechatInboundError):
            self.processor.process(
                bad,
                policy=self.policy,
                poll=self.poll(
                    raw_hash="b" * 64,
                    previous=outcome.ack_permit.next_cursor_sha256,
                    next_token="next",
                    captured=3_000,
                ),
            )

    def test_getupdates_batch_uses_local_checkpoints_and_exposes_external_cursor_last(self):
        poll = self.poll(next_token="external-buffer-2")
        batch = self.processor.process_batch(
            (
                message("message-1", sequence=1, context_token="token-1"),
                message("message-2", sequence=2, context_token=None),
            ),
            policy=self.policy,
            poll=poll,
        )
        self.assertEqual(len(batch.outcomes), 2)
        self.assertTrue(batch.outcomes[0].should_forward)
        self.assertTrue(batch.outcomes[1].should_forward)
        self.assertEqual(batch.outcomes[1].context_token, "token-1")
        cursor = self.inbox.get_cursor(batch.ack_permit.cursor_stream_key)
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor.cursor_token, "external-buffer-2")
        self.assertEqual(cursor.cursor_sha256, batch.external_cursor_sha256)
        self.assertEqual(cursor.revision, 2)
        self.assertEqual(self.inbox.count_records(), 2)

    def _poll_with_object(self, object_id, *, previous=None, next_token="cursor-1", captured=2_000):
        return WechatPollRecord(
            raw_payload_object_id=object_id,
            raw_payload_sha256="a" * 64,
            raw_payload_size_bytes=100,
            previous_cursor_sha256=previous,
            next_cursor_token=next_token,
            captured_at_ms=captured,
            persisted_at_ms=captured + 1,
        )

    def test_redelivery_with_fresh_volatile_fields_replays_stored_ingress(self):
        """平台重投：raw 对象 id/捕获时间全新，必须走 duplicate 而非冲突。

        旧实现按新 poll 重建 InboxIngress，volatile 字段必然与存量不同，
        InboxConflictError 让 duplicate 分支永远不可达、轮询器死循环。
        """
        first = self.processor.process(
            message(), policy=self.policy, poll=self.poll(captured=2_000)
        )
        self.assertFalse(first.inbox_duplicate)
        # 新一轮轮询：同一消息被平台重发，本地 raw store 落了新对象、
        # 捕获时间也变了——只有内容 sha 与首投一致。
        redelivered = self.processor.process(
            message(),
            policy=self.policy,
            poll=self._poll_with_object("raw-wechat-batch-2", captured=9_000),
        )
        self.assertTrue(redelivered.inbox_duplicate)
        self.assertTrue(redelivered.decision.duplicate)
        self.assertEqual(redelivered.envelope.text, "你好")
        self.assertEqual(self.inbox.count_records(), 1)

    def test_local_checkpoint_cursor_carries_external_token(self):
        """批中途崩溃后 cursor state 是本地检查点：必须能解码回外部游标。"""
        from contracts import canonical_json_bytes
        from communication_service.wechat_inbound import external_cursor_from_local

        poll = self.poll(next_token="external-buffer-9")
        batch = self.processor.process_batch(
            (
                message("message-1", sequence=1),
                message("message-2", sequence=2, context_token=None),
            ),
            policy=self.policy,
            poll=poll,
        )
        # 成员 0 的检查点在 permit 链上只暴露 sha；用同源构造验证解析。
        checkpoint = "tg-local-wechat-batch-v1:" + canonical_json_bytes(
            {
                "raw_payload_object_id": "raw-wechat-batch-1",
                "raw_payload_sha256": "a" * 64,
                "member_index": 1,
                "member_count": 2,
                "external_cursor_token": "external-buffer-9",
            }
        ).decode("utf-8")
        # 崩溃重启：worker 读到的合成检查点可还原出整批的外部游标。
        self.assertEqual(external_cursor_from_local(checkpoint), "external-buffer-9")
        # 批正常完成时 cursor state 落的是外部游标本身。
        cursor = self.inbox.get_cursor(batch.ack_permit.cursor_stream_key)
        self.assertEqual(cursor.cursor_token, "external-buffer-9")
        # 普通外部游标原样返回；旧格式/损坏的检查点返回空串（调用方回退）。
        self.assertEqual(external_cursor_from_local("plain-token"), "plain-token")
        self.assertEqual(external_cursor_from_local("tg-local-wechat-batch-v1:not-json"), "")

    def test_mid_batch_crash_then_full_redelivery_recovers_without_conflict(self):
        """批处理中途崩溃：重发整批，已持久化成员走 duplicate、其余正常插入。"""
        poll = self.poll(next_token="external-buffer-2")
        batch = self.processor.process_batch(
            (
                message("message-1", sequence=1, context_token="token-1"),
                message("message-2", sequence=2, context_token=None),
            ),
            policy=self.policy,
            poll=poll,
        )
        self.assertEqual(self.inbox.count_records(), 2)
        # 平台视角整批重发（新 raw 对象/新捕获时间，内容 sha 相同）。
        redelivered = self.processor.process_batch(
            (
                message("message-1", sequence=1, context_token="token-1"),
                message("message-2", sequence=2, context_token=None),
            ),
            policy=self.policy,
            poll=self._poll_with_object(
                "raw-wechat-batch-2", previous=None, next_token="external-buffer-3", captured=9_000
            ),
        )
        self.assertTrue(redelivered.outcomes[0].inbox_duplicate)
        self.assertTrue(redelivered.outcomes[1].inbox_duplicate)
        self.assertEqual(self.inbox.count_records(), 2)


if __name__ == "__main__":
    unittest.main()
