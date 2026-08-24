"""Deterministic WeChat iLink text ingress into the durable communication Inbox."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from contracts import (
    AttachmentRef,
    InboundEnvelope,
    InboundScope,
    canonical_json_bytes,
    canonical_sha256,
    derive_inbound_scope_keys,
)

from .inbox import (
    ChannelAckPermit,
    CommunicationInbox,
    InboxIngress,
    cursor_token_sha256,
    derive_cursor_stream_key,
)
from .wechat_session import WechatSessionDecision, WechatSessionLedger


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if tuple(sorted(set(values))) != values:
        raise ValueError("policy set fields must be sorted and unique")
    return values


_LOCAL_BATCH_CURSOR_PREFIX = "tg-local-wechat-batch-v1:"


def external_cursor_from_local(token: str) -> str:
    """Map a persisted cursor token to the token the platform will accept.

    A mid-batch crash leaves a local checkpoint token in cursor state; that
    checkpoint carries the batch's external ``get_updates_buf`` token (JSON
    encoded) and only the external form may be sent to the platform. Returns
    "" when the checkpoint predates this encoding or fails to parse — the
    caller falls back to the credentials cursor instead of replaying the
    local token at the platform (which it would reject).
    """

    if not token.startswith(_LOCAL_BATCH_CURSOR_PREFIX):
        return token
    try:
        payload = json.loads(token[len(_LOCAL_BATCH_CURSOR_PREFIX):])
    except ValueError:
        return ""
    external = payload.get("external_cursor_token") if isinstance(payload, dict) else None
    return external if isinstance(external, str) and external else ""


class WechatInboundPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: str = Field(min_length=1, max_length=160)
    link_account_id: str = Field(min_length=1, max_length=160)
    account_id: str = Field(min_length=1, max_length=160)
    self_user_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    allowed_sender_ids: tuple[str, ...] = Field(default=(), max_length=256)
    allow_direct_messages: bool = True
    allow_group_messages: bool = False
    allowed_group_ids: tuple[str, ...] = Field(default=(), max_length=256)
    group_requires_mention: bool = True

    @field_validator("self_user_ids", "allowed_sender_ids", "allowed_group_ids")
    @classmethod
    def validate_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value)


class WechatPollRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    raw_payload_object_id: str = Field(min_length=1, max_length=160)
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_payload_size_bytes: int = Field(ge=1, le=16_777_216)
    previous_cursor_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    next_cursor_token: str = Field(max_length=4_096)
    captured_at_ms: int = Field(ge=0)
    persisted_at_ms: int = Field(ge=0)


@dataclass(frozen=True)
class WechatInboundOutcome:
    envelope: InboundEnvelope
    ack_permit: ChannelAckPermit
    decision: WechatSessionDecision
    context_token: str | None
    inbox_duplicate: bool

    @property
    def should_forward(self) -> bool:
        return self.decision.should_forward


@dataclass(frozen=True)
class WechatBatchOutcome:
    outcomes: tuple[WechatInboundOutcome, ...]
    external_cursor_sha256: str

    @property
    def ack_permit(self) -> ChannelAckPermit:
        if not self.outcomes:
            raise WechatInboundError("an empty WeChat poll batch has no message ACK permit")
        return self.outcomes[-1].ack_permit


class WechatInboundError(ValueError):
    pass


class WechatAttachmentIngestor(Protocol):
    def ingest_item(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        tenant_id: str,
        link_account_id: str,
        conversation_scope_hash: str,
        source_message_ref: str,
        created_at_ms: int,
    ) -> AttachmentRef: ...


def _opaque(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _string(raw: Mapping[str, Any], key: str, *, limit: int = 4_096) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise WechatInboundError(f"wechat field {key} has an invalid type")
    text = str(value).strip()
    if "\x00" in text or len(text.encode("utf-8")) > limit:
        raise WechatInboundError(f"wechat field {key} is invalid")
    return text


def _optional_sequence(raw: Mapping[str, Any]) -> int | None:
    value = raw.get("seq")
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise WechatInboundError("wechat sequence has an invalid type")
    if isinstance(value, str):
        if not value.isdecimal():
            raise WechatInboundError("wechat sequence is not an unsigned integer")
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= 9_223_372_036_854_775_807:
        raise WechatInboundError("wechat sequence is outside the supported range")
    return value


def extract_ilink_text(raw: Mapping[str, Any]) -> str:
    items = raw.get("item_list", ())
    if items is None:
        items = ()
    if not isinstance(items, (list, tuple)) or len(items) > 50:
        raise WechatInboundError("wechat item_list is invalid")
    parts: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise WechatInboundError("wechat message item is invalid")
        for name in ("text_item", "voice_item"):
            nested = item.get(name)
            if nested is None:
                continue
            if not isinstance(nested, Mapping):
                raise WechatInboundError(f"wechat {name} is invalid")
            value = nested.get("text")
            if value is None:
                continue
            if not isinstance(value, str):
                raise WechatInboundError(f"wechat {name}.text is invalid")
            text = value.strip()
            if text:
                parts.append(text)
    result = "\n".join(parts).strip()
    if "\x00" in result or len(result) > 100_000:
        raise WechatInboundError("wechat text exceeds the accepted contract")
    return result


def extract_ilink_media_items(
    raw: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    items = raw.get("item_list", ())
    if items is None:
        items = ()
    if not isinstance(items, (list, tuple)) or len(items) > 50:
        raise WechatInboundError("wechat item_list is invalid")
    result: list[tuple[str, Mapping[str, Any]]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise WechatInboundError("wechat message item is invalid")
        present = []
        for kind in ("image", "voice", "video", "file"):
            nested = item.get(kind + "_item")
            if nested is None:
                continue
            if not isinstance(nested, Mapping):
                raise WechatInboundError(f"wechat {kind}_item is invalid")
            if kind == "voice" and not any(
                    name in nested
                    for name in (
                        "media",
                        "encrypted_query_param",
                        "download_param",
                        "full_url",
                        "download_url",
                    )
                ):
                continue
            present.append((kind, nested))
        if len(present) > 1:
            raise WechatInboundError("wechat message item contains multiple media kinds")
        result.extend(present)
    if len(result) > 20:
        raise WechatInboundError("wechat message exceeds attachment limit")
    return tuple(result)


class WechatTextInboundProcessor:
    def __init__(
        self,
        inbox: CommunicationInbox,
        sessions: WechatSessionLedger,
        attachment_ingestor: WechatAttachmentIngestor | None = None,
    ) -> None:
        self._inbox = inbox
        self._sessions = sessions
        self._attachment_ingestor = attachment_ingestor

    def process(
        self,
        raw_message: Mapping[str, Any],
        *,
        policy: WechatInboundPolicy,
        poll: WechatPollRecord,
    ) -> WechatInboundOutcome:
        if poll.persisted_at_ms < poll.captured_at_ms:
            raise WechatInboundError("wechat persistence time predates capture")
        message_type = raw_message.get("message_type")
        supported_type = message_type in {1, "1"}
        sender_id = _string(raw_message, "from_user_id", limit=1_024)
        if not sender_id:
            raise WechatInboundError("wechat sender is missing")
        message_id = _string(raw_message, "message_id", limit=1_024)
        client_id = _string(raw_message, "client_id", limit=1_024)
        sequence = _optional_sequence(raw_message)
        platform_identity = message_id or (str(sequence) if sequence is not None else "") or client_id
        if not platform_identity:
            platform_identity = poll.raw_payload_sha256
        group_id = _string(raw_message, "group_id", limit=1_024)
        session_id = _string(raw_message, "session_id", limit=1_024)
        conversation_id = group_id or session_id or sender_id
        mentioned_bot = raw_message.get("mentioned_bot") is True
        incoming_context_token = _string(raw_message, "context_token", limit=8_192) or None
        text = extract_ilink_text(raw_message) if supported_type else ""
        media_items = extract_ilink_media_items(raw_message) if supported_type else ()

        classification = "ACCEPTED"
        if not supported_type:
            classification = "UNSUPPORTED_MESSAGE_TYPE"
        elif sender_id in policy.self_user_ids:
            classification = "SELF_MESSAGE"
        elif policy.allowed_sender_ids and sender_id not in policy.allowed_sender_ids:
            classification = "UNEXPECTED_SENDER"
        elif group_id and (
            not policy.allow_group_messages
            or (policy.allowed_group_ids and group_id not in policy.allowed_group_ids)
        ):
            classification = "GROUP_DISABLED"
        elif group_id and policy.group_requires_mention and not mentioned_bot:
            classification = "GROUP_MENTION_REQUIRED"
        elif not group_id and not policy.allow_direct_messages:
            classification = "UNEXPECTED_SENDER"
        elif not text and not media_items:
            classification = "EMPTY_TEXT"

        conversation_ref = _opaque("wxconv_", conversation_id)
        sender_ref = _opaque("wxuser_", sender_id)
        channel_message_ref = _opaque(
            "wxmsg_",
            f"{policy.account_id}\x00{platform_identity}",
        )
        scope = InboundScope(
            channel="wechat",
            tenant_id=policy.tenant_id,
            link_account_id=policy.link_account_id,
            conversation_ref=conversation_ref,
            channel_message_ref=channel_message_ref,
            sender_ref=sender_ref,
        )
        keys = derive_inbound_scope_keys(scope)
        inbound_id = "wxin_" + keys.idempotency_key
        existing_ingress = self._inbox.get_ingress(inbound_id)
        attachments: tuple[AttachmentRef, ...] = ()
        if existing_ingress is not None:
            attachments = existing_ingress.envelope.attachments
            if attachments:
                classification = "ACCEPTED"
        elif classification == "ACCEPTED" and media_items:
            if self._attachment_ingestor is None:
                classification = "ATTACHMENT_HANDLER_UNAVAILABLE"
            else:
                accepted = []
                try:
                    for kind, item in media_items:
                        accepted.append(
                            self._attachment_ingestor.ingest_item(
                                kind,
                                item,
                                tenant_id=policy.tenant_id,
                                link_account_id=policy.link_account_id,
                                conversation_scope_hash=keys.conversation_scope_hash,
                                source_message_ref=channel_message_ref,
                                created_at_ms=poll.captured_at_ms,
                            )
                        )
                except Exception:
                    classification = "ATTACHMENT_REJECTED"
                else:
                    attachments = tuple(accepted)
        if existing_ingress is not None:
            envelope = existing_ingress.envelope
        else:
            metadata_hash = canonical_sha256(
                {
                    "domain": "tiangong.communication.wechat-ilink-metadata.v1",
                    "message_type": str(message_type),
                    "message_identity_sha256": hashlib.sha256(platform_identity.encode()).hexdigest(),
                    "sender_sha256": hashlib.sha256(sender_id.encode()).hexdigest(),
                    "group_sha256": None if not group_id else hashlib.sha256(group_id.encode()).hexdigest(),
                    "session_sha256": None if not session_id else hashlib.sha256(session_id.encode()).hexdigest(),
                    "sequence": sequence,
                    "mentioned_bot": mentioned_bot,
                    "context_token_sha256": (
                        None
                        if incoming_context_token is None
                        else hashlib.sha256(incoming_context_token.encode()).hexdigest()
                    ),
                    "classification": classification,
                    "attachment_count": len(attachments),
                    "attachment_sha256": tuple(item.sha256 for item in attachments),
                }
            )
            if text:
                envelope_text = text
            elif attachments:
                envelope_text = "[attachment message]"
            else:
                envelope_text = f"[communication event withheld:{classification.lower()}]"
            envelope = InboundEnvelope(
                inbound_id=inbound_id,
                channel="wechat",
                tenant_id=policy.tenant_id,
                link_account_id=policy.link_account_id,
                conversation_ref=conversation_ref,
                conversation_scope_hash=keys.conversation_scope_hash,
                principal_scope_hash=keys.principal_scope_hash,
                message_scope_hash=keys.message_scope_hash,
                channel_message_ref=channel_message_ref,
                sender_ref=sender_ref,
                received_at_ms=poll.captured_at_ms,
                idempotency_key=keys.idempotency_key,
                channel_metadata_hash=metadata_hash,
                text=envelope_text,
                attachments=attachments,
                sequence=sequence,
            )
        session = self._sessions.decide(
            account_id=policy.account_id,
            sender_ref=sender_ref,
            conversation_scope_hash=keys.conversation_scope_hash,
            message_ref=channel_message_ref,
            message_fingerprint=canonical_sha256(
                {
                    "raw_payload_sha256": poll.raw_payload_sha256,
                    "envelope_sha256": canonical_sha256(envelope.model_dump(mode="json")),
                }
            ),
            envelope_sha256=canonical_sha256(envelope.model_dump(mode="json")),
            preliminary_classification=classification,
            recipient_user_id=sender_id,
            sequence=sequence,
            received_at_ms=poll.captured_at_ms,
            incoming_context_token=incoming_context_token,
        )
        if existing_ingress is not None:
            # 平台重投已持久化消息：存量 ingress 原样重放（对照飞书实现）。
            # 绝不能按新 poll 重建——raw 对象 id、游标链、captured_at_ms
            # 等易变字段必然不同，会触发 InboxConflictError 让 duplicate
            # 分支永远不可达，轮询器在重投循环里永久报错。
            if (
                existing_ingress.raw_payload_sha256 != poll.raw_payload_sha256
                or existing_ingress.raw_payload_size_bytes != poll.raw_payload_size_bytes
            ):
                raise WechatInboundError("wechat duplicate event changed its raw payload")
            ingress = existing_ingress
        else:
            ingress = InboxIngress(
                ingress_id=envelope.inbound_id,
                envelope=envelope,
                raw_payload_object_id=poll.raw_payload_object_id,
                raw_payload_sha256=poll.raw_payload_sha256,
                raw_payload_size_bytes=poll.raw_payload_size_bytes,
                cursor_stream_key=derive_cursor_stream_key(
                    "wechat", policy.tenant_id, policy.link_account_id
                ),
                previous_cursor_sha256=poll.previous_cursor_sha256,
                next_cursor_token=poll.next_cursor_token,
                next_cursor_sha256=cursor_token_sha256(poll.next_cursor_token),
                captured_at_ms=poll.captured_at_ms,
                ingress_sha256="0" * 64,
            ).with_computed_sha256()
        persisted = self._inbox.persist_and_advance_cursor(
            ingress,
            persisted_at_ms=poll.persisted_at_ms,
        )
        return WechatInboundOutcome(
            envelope=envelope,
            ack_permit=persisted.permit,
            decision=session.decision,
            context_token=session.context_token,
            inbox_duplicate=persisted.duplicate,
        )

    def process_batch(
        self,
        raw_messages: tuple[Mapping[str, Any], ...],
        *,
        policy: WechatInboundPolicy,
        poll: WechatPollRecord,
    ) -> WechatBatchOutcome:
        """Persist a getupdates message batch before exposing its final external cursor.

        Intermediate cursor tokens are local recovery checkpoints bound to the immutable
        raw batch object. Only the final member stores the platform get_updates_buf;
        every intermediate checkpoint also carries that external token (JSON
        encoded) so a crash mid-batch can resume polling the platform instead of
        replaying the local checkpoint token at it.
        """

        if not raw_messages or len(raw_messages) > 1_000:
            raise WechatInboundError("wechat poll batch must contain 1..1000 messages")
        previous = poll.previous_cursor_sha256
        outcomes: list[WechatInboundOutcome] = []
        final_cursor_sha256 = cursor_token_sha256(poll.next_cursor_token)
        for index, raw_message in enumerate(raw_messages):
            final = index == len(raw_messages) - 1
            next_token = poll.next_cursor_token if final else (
                _LOCAL_BATCH_CURSOR_PREFIX
                + canonical_json_bytes(
                    {
                        "raw_payload_object_id": poll.raw_payload_object_id,
                        "raw_payload_sha256": poll.raw_payload_sha256,
                        "member_index": index + 1,
                        "member_count": len(raw_messages),
                        "external_cursor_token": poll.next_cursor_token,
                    }
                ).decode("utf-8")
            )
            member_poll = poll.model_copy(
                update={
                    "previous_cursor_sha256": previous,
                    "next_cursor_token": next_token,
                }
            )
            outcome = self.process(raw_message, policy=policy, poll=member_poll)
            outcomes.append(outcome)
            previous = outcome.ack_permit.next_cursor_sha256
        return WechatBatchOutcome(tuple(outcomes), final_cursor_sha256)


__all__ = [
    "WechatInboundError",
    "WechatBatchOutcome",
    "WechatInboundOutcome",
    "WechatInboundPolicy",
    "WechatPollRecord",
    "WechatTextInboundProcessor",
    "external_cursor_from_local",
    "extract_ilink_text",
    "extract_ilink_media_items",
]
