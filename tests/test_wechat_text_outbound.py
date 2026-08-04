import hashlib
import tempfile
import unittest
from pathlib import Path

from communication_service.delivery_ledger import DeliveryLedger, DeliveryLedgerConflict
from communication_service.wechat_session import WechatSessionLedger
from communication_service.wechat_text_outbound import (
    HttpWechatIlinkTextTransport,
    WechatIlinkResponse,
    WechatTextDeliveryService,
    WechatTextOutboundError,
    default_wechat_text_policy,
    derive_wechat_client_id,
    split_wechat_text,
)
from contracts import OutboundPart, OutboundPlan, text_sha256
from tests.test_delivery_contracts import (
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
        if not ciphertext.startswith(b"TEST"):
            raise OSError("invalid protected data")
        key = hashlib.sha256(entropy).digest()
        return bytearray(
            value ^ key[index % len(key)] for index, value in enumerate(ciphertext[4:])
        )


class _Clock:
    def __init__(self, value=23_000):
        self.value = value
        self.sleeps = []

    def now(self):
        current = self.value
        self.value += 1
        return current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += max(1, int(seconds * 1_000))


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send_message(self, body, *, bot_token, timeout_seconds):
        self.calls.append((body, bot_token, timeout_seconds))
        if not self.responses:
            raise AssertionError("unexpected transport call")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(index, *, status=200, ret=0, errcode=0):
    payload = {}
    if ret is not None:
        payload["ret"] = ret
    if errcode is not None:
        payload["errcode"] = errcode
    return WechatIlinkResponse(
        status_code=status,
        payload=payload,
        body_sha256=f"{index:064x}",
    )


def text_plan(texts, policy):
    base = outbound_plan()
    parts = tuple(
        OutboundPart(
            part_id=f"part_text_{index + 1:03d}",
            index=index,
            kind="text",
            text=text,
            text_sha256=text_sha256(text),
        )
        for index, text in enumerate(texts)
    )
    values = base.model_dump(mode="python")
    values.update(
        {
            "parts": parts,
            "channel_policy_hash": policy.policy_sha256,
            "plan_sha256": "0" * 64,
        }
    )
    return OutboundPlan(**values).with_computed_plan_sha256()


def ticket_for(plan):
    return delivery_ticket(
        plan=plan,
        allow_text=True,
        allow_files=False,
        max_text_parts=len(plan.parts),
        max_file_parts=0,
    )


class WechatTextOutboundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.ledger = DeliveryLedger.open(root / "delivery.sqlite3", now_ms=1_000)
        self.sessions = WechatSessionLedger.open(
            root / "sessions.sqlite3", now_ms=1_000, protector=_Protector()
        )
        self.clock = _Clock()
        self.policy = default_wechat_text_policy().model_copy(
            update={
                "max_chars_per_segment": 4,
                "min_attempt_interval_ms": 0,
                "policy_sha256": "0" * 64,
            }
        ).with_computed_sha256()

    def tearDown(self):
        self.sessions.close()
        self.ledger.close()
        self.temporary.cleanup()

    def bind_session(self, plan, *, token="context-secret", recipient="raw-user-secret"):
        result = self.sessions.decide(
            account_id="ilink-account",
            sender_ref="wxuser_" + "1" * 64,
            conversation_scope_hash=plan.conversation_scope_hash,
            message_ref="wxmsg_" + "2" * 64,
            message_fingerprint="3" * 64,
            envelope_sha256="4" * 64,
            preliminary_classification="ACCEPTED",
            recipient_user_id=recipient,
            sequence=1,
            received_at_ms=2_000,
            incoming_context_token=token,
        )
        self.assertNotIn(recipient.encode(), self.sessions.path.read_bytes())
        return result.decision.session_key

    def service(self, transport):
        return WechatTextDeliveryService(
            self.ledger,
            self.sessions,
            transport,
            clock_ms=self.clock.now,
            sleeper=self.clock.sleep,
        )

    def send(self, service, ticket, plan, session_key, *, policy=None):
        consume_verified_delivery_for_test(self.ledger, ticket, at_ms=22_000)
        return service.send(
            ticket.payload,
            plan,
            policy=policy or self.policy,
            bot_token="bot-token",
            ilink_account_id="ilink-account",
            session_key=session_key,
        )

    def test_split_send_uses_stable_client_ids_and_duplicate_does_not_resend(self):
        plan = text_plan(("abcd\nefgh",), self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport([response(1), response(2), response(3)])
        service = self.service(transport)

        receipt = self.send(service, ticket, plan, session_key)

        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual(receipt.parts[0].attempt, 3)
        self.assertEqual(
            "".join(call[0]["msg"]["item_list"][0]["text_item"]["text"] for call in transport.calls),
            "abcd\nefgh",
        )
        client_ids = [call[0]["msg"]["client_id"] for call in transport.calls]
        self.assertEqual(
            client_ids,
            [derive_wechat_client_id(plan.effect_id, plan.parts[0].part_id, index) for index in range(3)],
        )
        duplicate = self.send(service, ticket, plan, session_key)
        self.assertEqual(duplicate, receipt)
        self.assertEqual(len(transport.calls), 3)

    def test_context_expiry_retries_same_client_id_without_token_and_clears_cache(self):
        plan = text_plan(("hi",), self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport([response(1, ret=-14), response(2)])
        receipt = self.send(self.service(transport), ticket, plan, session_key)
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        first = transport.calls[0][0]["msg"]
        second = transport.calls[1][0]["msg"]
        self.assertEqual(first["client_id"], second["client_id"])
        self.assertEqual(first["context_token"], "context-secret")
        self.assertIsNone(second["context_token"])
        self.assertIsNone(
            self.sessions.resolve_context_token(
                session_key=session_key,
                account_id="ilink-account",
                conversation_scope_hash=plan.conversation_scope_hash,
            )
        )

    def test_rate_limit_retries_are_bounded_and_reuse_client_id(self):
        policy = self.policy.model_copy(
            update={"rate_limit_retries": 2, "policy_sha256": "0" * 64}
        ).with_computed_sha256()
        plan = text_plan(("hi",), policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport([response(1, ret=-2), response(2, ret=-2), response(3)])
        receipt = self.send(
            self.service(transport), ticket, plan, session_key, policy=policy
        )
        self.assertEqual(receipt.status, "CHANNEL_ACCEPTED")
        self.assertEqual([2.0, 4.0], self.clock.sleeps)
        self.assertEqual(len({call[0]["msg"]["client_id"] for call in transport.calls}), 1)

    def test_exhausted_rate_limit_is_retryable_only_when_no_segment_was_accepted(self):
        policy = self.policy.model_copy(
            update={"rate_limit_retries": 1, "policy_sha256": "0" * 64}
        ).with_computed_sha256()
        plan = text_plan(("hi",), policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport([response(1, ret=-2), response(2, ret=-2)])
        receipt = self.send(
            self.service(transport), ticket, plan, session_key, policy=policy
        )
        self.assertEqual(receipt.status, "FAILED_RETRYABLE")
        self.assertEqual(receipt.parts[0].stage, "FAILED_RETRYABLE")
        self.assertEqual(self.ledger.get(plan.effect_id).state, "FAILED_RETRYABLE")

    def test_partial_part_acceptance_then_rate_limit_requires_reconciliation(self):
        policy = self.policy.model_copy(
            update={"rate_limit_retries": 1, "policy_sha256": "0" * 64}
        ).with_computed_sha256()
        plan = text_plan(("abcdefgh",), policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport(
            [response(1), response(2, ret=-2), response(3, ret=-2)]
        )
        receipt = self.send(
            self.service(transport), ticket, plan, session_key, policy=policy
        )
        self.assertEqual(receipt.status, "RECONCILE_REQUIRED")
        self.assertEqual(receipt.parts[0].stage, "AMBIGUOUS")
        self.assertEqual(self.ledger.get(plan.effect_id).state, "RECONCILE_REQUIRED")

    def test_explicit_platform_rejection_is_final_and_not_called_ambiguous(self):
        plan = text_plan(("hi",), self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport([response(1, status=401, ret=None, errcode=None)])
        receipt = self.send(self.service(transport), ticket, plan, session_key)
        self.assertEqual(receipt.status, "FAILED_FINAL")
        self.assertEqual(receipt.parts[0].stage, "FAILED_FINAL")
        self.assertEqual(self.ledger.get(plan.effect_id).state, "FAILED_FINAL")

    def test_unknown_transport_result_is_reconcile_required_and_never_resent(self):
        plan = text_plan(("hi",), self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport(
            [WechatTextOutboundError("wechat.send.transport.unknown", outcome_unknown=True)]
        )
        service = self.service(transport)
        first = self.send(service, ticket, plan, session_key)
        second = self.send(service, ticket, plan, session_key)
        self.assertEqual(first, second)
        self.assertEqual(first.status, "RECONCILE_REQUIRED")
        self.assertEqual(self.ledger.get(plan.effect_id).state, "RECONCILE_REQUIRED")
        self.assertEqual(len(transport.calls), 1)

    def test_restart_after_send_boundary_becomes_ambiguous_without_transport_call(self):
        plan = text_plan(("hi",), self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        consume_verified_delivery_for_test(self.ledger, ticket, at_ms=22_000)
        self.ledger.mark_side_effect_started(plan.effect_id, started_at_ms=22_500)
        transport = _Transport([])
        receipt = self.send(self.service(transport), ticket, plan, session_key)
        self.assertEqual(receipt.status, "RECONCILE_REQUIRED")
        self.assertFalse(transport.calls)

    def test_policy_or_part_rebinding_fails_before_claim_or_network(self):
        plan = text_plan(("hi",), self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport([])
        wrong_policy = default_wechat_text_policy()
        with self.assertRaisesRegex(WechatTextOutboundError, "policy.mismatch"):
            self.service(transport).send(
                ticket.payload,
                plan,
                policy=wrong_policy,
                bot_token="bot-token",
                ilink_account_id="ilink-account",
                session_key=session_key,
            )
        self.assertIsNone(self.ledger.get(plan.effect_id))
        self.assertFalse(transport.calls)

    def test_transport_service_rejects_unverified_payload_before_network(self):
        plan = text_plan(("hi",), self.policy)
        ticket = ticket_for(plan)
        session_key = self.bind_session(plan)
        transport = _Transport([response(1)])
        with self.assertRaises(DeliveryLedgerConflict):
            self.service(transport).send(
                ticket.payload,
                plan,
                policy=self.policy,
                bot_token="bot-token",
                ilink_account_id="ilink-account",
                session_key=session_key,
            )
        self.assertFalse(transport.calls)

    def test_split_preserves_exact_text_and_transport_origin_is_fixed(self):
        text = "第一段\n第二段  "
        chunks = split_wechat_text(text, limit=4)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 4 for chunk in chunks))
        HttpWechatIlinkTextTransport()
        with self.assertRaises(ValueError):
            HttpWechatIlinkTextTransport(origin="https://ilinkai.weixin.qq.com.evil.test")


if __name__ == "__main__":
    unittest.main()
