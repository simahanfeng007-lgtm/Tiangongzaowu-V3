import unittest

from contracts import (
    AttachmentRef,
    InboundEnvelope,
    InboundScope,
    OutboundPart,
    OutboundPlan,
    OutboundScope,
    ScopeBindingError,
    bind_inbound_scope,
    bind_outbound_scope,
    derive_inbound_scope_keys,
    derive_outbound_scope_keys,
    text_sha256,
)


HASH_A = "a" * 64
HASH_C = "c" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64
DELIVERY_EFFECT_ID = "eff_" + "3" * 64
DELIVERY_ID = "del_" + "4" * 64


def inbound_scope(**overrides):
    values = {
        "channel": "wechat",
        "tenant_id": "tenant_001",
        "link_account_id": "wechat_001",
        "conversation_ref": "conversation_001",
        "channel_message_ref": "message_001",
        "sender_ref": "sender_001",
    }
    values.update(overrides)
    return InboundScope(**values)


def inbound_envelope(scope=None):
    scope = scope or inbound_scope()
    keys = derive_inbound_scope_keys(scope)
    attachment = AttachmentRef(
        object_id="attachment_001",
        revision=1,
        sha256=HASH_A,
        size_bytes=1024,
        mime="application/pdf",
        filename="report.pdf",
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_scope_hash=keys.conversation_scope_hash,
        source_message_ref=scope.channel_message_ref,
        created_at_ms=10_000,
    )
    return InboundEnvelope(
        inbound_id="inbound_001",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=10_000,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash=HASH_C,
        text="请读取附件",
        attachments=(attachment,),
    )


def outbound_scope(**overrides):
    values = {
        "channel": "wechat",
        "tenant_id": "tenant_001",
        "link_account_id": "wechat_001",
        "conversation_ref": "conversation_001",
        "recipient_ref": "recipient_001",
        "reply_to_message_ref": "message_001",
    }
    values.update(overrides)
    return OutboundScope(**values)


def outbound_plan(scope=None):
    scope = scope or outbound_scope()
    keys = derive_outbound_scope_keys(scope)
    text = "处理完成"
    return OutboundPlan(
        outbound_plan_id="outbound_plan_001",
        delivery_id=DELIVERY_ID,
        effect_id=DELIVERY_EFFECT_ID,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        generation=1,
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        recipient_scope_hash=keys.recipient_scope_hash,
        reply_to_message_ref=scope.reply_to_message_ref,
        channel_policy_hash=HASH_A,
        created_at_ms=11_000,
        parts=(
            OutboundPart(
                part_id="part_text_001",
                index=0,
                kind="text",
                text=text,
                text_sha256=text_sha256(text),
            ),
        ),
        plan_sha256=HASH_C,
    ).with_computed_plan_sha256()


class InboundScopeTests(unittest.TestCase):
    def test_same_scope_is_deterministic_and_binds_envelope(self) -> None:
        scope = inbound_scope()
        first = derive_inbound_scope_keys(scope)
        second = derive_inbound_scope_keys(scope)
        self.assertEqual(first, second)
        self.assertEqual(bind_inbound_scope(inbound_envelope(scope), scope), first)

    def test_tenant_account_conversation_or_message_changes_idempotency(self) -> None:
        base = derive_inbound_scope_keys(inbound_scope())
        variants = (
            inbound_scope(channel="feishu"),
            inbound_scope(tenant_id="tenant_002"),
            inbound_scope(link_account_id="wechat_002"),
            inbound_scope(conversation_ref="conversation_002"),
            inbound_scope(channel_message_ref="message_002"),
        )
        for variant in variants:
            with self.subTest(variant=variant):
                keys = derive_inbound_scope_keys(variant)
                self.assertNotEqual(keys.idempotency_key, base.idempotency_key)

    def test_sender_changes_principal_but_not_message_idempotency(self) -> None:
        first = derive_inbound_scope_keys(inbound_scope(sender_ref="sender_001"))
        second = derive_inbound_scope_keys(inbound_scope(sender_ref="sender_002"))
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertNotEqual(first.principal_scope_hash, second.principal_scope_hash)

    def test_rejects_envelope_bound_to_other_account(self) -> None:
        envelope = inbound_envelope(inbound_scope())
        with self.assertRaises(ScopeBindingError) as caught:
            bind_inbound_scope(envelope, inbound_scope(link_account_id="wechat_002"))
        self.assertEqual(caught.exception.code, "inbound_scope.link_account_id.mismatch")

    def test_rejects_tampered_derived_hashes_and_attachment_source(self) -> None:
        scope = inbound_scope()
        envelope = inbound_envelope(scope)
        cases = (
            (
                envelope.model_copy(update={"principal_scope_hash": HASH_A}),
                "inbound_scope.principal_hash.mismatch",
            ),
            (
                envelope.model_copy(update={"message_scope_hash": HASH_A}),
                "inbound_scope.message_hash.mismatch",
            ),
            (
                envelope.model_copy(update={"idempotency_key": HASH_A}),
                "inbound_scope.idempotency_key.mismatch",
            ),
            (
                envelope.model_copy(
                    update={
                        "attachments": (
                            envelope.attachments[0].model_copy(update={"source_message_ref": "message_002"}),
                        )
                    }
                ),
                "inbound_scope.attachment_message.mismatch",
            ),
        )
        for candidate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ScopeBindingError) as caught:
                    bind_inbound_scope(candidate, scope)
                self.assertEqual(caught.exception.code, expected_code)


class OutboundScopeTests(unittest.TestCase):
    def test_binds_conversation_recipient_and_reply_target(self) -> None:
        scope = outbound_scope()
        plan = outbound_plan(scope)
        keys = bind_outbound_scope(plan, scope)
        self.assertEqual(keys, derive_outbound_scope_keys(scope))

    def test_rejects_recipient_swap(self) -> None:
        plan = outbound_plan(outbound_scope(recipient_ref="recipient_001"))
        with self.assertRaises(ScopeBindingError) as caught:
            bind_outbound_scope(plan, outbound_scope(recipient_ref="recipient_002"))
        self.assertEqual(caught.exception.code, "outbound_scope.recipient_hash.mismatch")

    def test_tenant_account_conversation_and_recipient_are_isolated(self) -> None:
        base = derive_outbound_scope_keys(outbound_scope())
        variants = (
            outbound_scope(channel="feishu"),
            outbound_scope(tenant_id="tenant_002"),
            outbound_scope(link_account_id="wechat_002"),
            outbound_scope(conversation_ref="conversation_002"),
            outbound_scope(recipient_ref="recipient_002"),
        )
        for variant in variants:
            with self.subTest(variant=variant):
                keys = derive_outbound_scope_keys(variant)
                self.assertNotEqual(keys.recipient_scope_hash, base.recipient_scope_hash)


if __name__ == "__main__":
    unittest.main()
