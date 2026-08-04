"""Verified Feishu message events persisted before SDK/WebSocket ACK."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from contracts import (
    AttachmentRef,
    InboundEnvelope,
    InboundScope,
    canonical_json_bytes,
    canonical_sha256,
    derive_inbound_scope_keys,
)

from .feishu_route import FeishuRouteLedger, derive_feishu_route_key
from .inbox import (
    ChannelAckPermit,
    CommunicationInbox,
    InboxIngress,
    cursor_token_sha256,
    derive_cursor_stream_key,
)


class FeishuInboundError(ValueError):
    pass


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if tuple(sorted(set(values))) != values:
        raise ValueError("Feishu policy sets must be sorted and unique")
    return values


class FeishuInboundPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: str = Field(min_length=1, max_length=160)
    link_account_id: str = Field(min_length=1, max_length=160)
    app_id: str = Field(min_length=1, max_length=256)
    platform_tenant_key: str = Field(min_length=1, max_length=512)
    bot_open_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    allowed_sender_ids: tuple[str, ...] = Field(default=(), max_length=512)
    allow_p2p: bool = True
    allow_groups: bool = False
    group_requires_mention: bool = True

    @field_validator("bot_open_ids", "allowed_sender_ids")
    @classmethod
    def validate_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(value)


class FeishuEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    raw_payload_object_id: str = Field(min_length=1, max_length=160)
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_payload_size_bytes: int = Field(ge=1, le=16_777_216)
    signature_verified: bool
    app_id_verified: bool
    captured_at_ms: int = Field(ge=0)
    persisted_at_ms: int = Field(ge=0)


@dataclass(frozen=True)
class FeishuInboundOutcome:
    envelope: InboundEnvelope
    ack_permit: ChannelAckPermit
    classification: str
    should_forward: bool
    route_key: str | None
    resource_ids: tuple[str, ...]
    duplicate: bool


@dataclass(frozen=True)
class FeishuEventResource:
    resource_type: str
    resource_key: str
    filename: str | None


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise FeishuInboundError("Feishu content contains a duplicate JSON key")
        result[key] = value
    return result


def _content_json(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1_048_576:
        raise FeishuInboundError("Feishu message content is invalid")
    try:
        parsed = json.loads(raw or "{}", object_pairs_hook=_pairs)
    except (ValueError, json.JSONDecodeError) as exc:
        raise FeishuInboundError("Feishu message content JSON is invalid") from exc
    if not isinstance(parsed, Mapping):
        raise FeishuInboundError("Feishu message content must be an object")
    return parsed


def _string(mapping: Mapping[str, Any], key: str, *, limit: int = 2_048) -> str:
    value = mapping.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FeishuInboundError(f"Feishu field {key} is invalid")
    value = value.strip()
    if "\x00" in value or len(value.encode("utf-8")) > limit:
        raise FeishuInboundError(f"Feishu field {key} is invalid")
    return value


def _opaque(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mention_identifier(mention: Mapping[str, Any]) -> str:
    raw_id = mention.get("id")
    if isinstance(raw_id, Mapping):
        for key in ("open_id", "user_id", "union_id"):
            identifier = _string(raw_id, key)
            if identifier:
                return identifier
        return ""
    if raw_id is not None:
        identifier = _string(mention, "id")
        if identifier:
            return identifier
    for key in ("open_id", "user_id", "union_id"):
        identifier = _string(mention, key)
        if identifier:
            return identifier
    return ""


def _rich_text(content: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    locale = None
    for key in ("zh_cn", "en_us", "ja_jp"):
        candidate = content.get(key)
        if isinstance(candidate, Mapping):
            locale = candidate
            break
    if locale is None:
        locale = content
    title = locale.get("title", "")
    blocks = locale.get("content", ())
    if title is not None and not isinstance(title, str):
        raise FeishuInboundError("Feishu post title is invalid")
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)) or len(blocks) > 1_000:
        raise FeishuInboundError("Feishu post blocks are invalid")
    lines = [title.strip()] if isinstance(title, str) and title.strip() else []
    mentions = []
    node_count = 0
    for block in blocks:
        if not isinstance(block, Sequence) or isinstance(block, (str, bytes)) or len(block) > 1_000:
            raise FeishuInboundError("Feishu post line is invalid")
        pieces = []
        for node in block:
            node_count += 1
            if node_count > 10_000 or not isinstance(node, Mapping):
                raise FeishuInboundError("Feishu post is too complex")
            tag = node.get("tag")
            if tag in {"text", "a"}:
                value = node.get("text", "")
                if not isinstance(value, str):
                    raise FeishuInboundError("Feishu post text node is invalid")
                pieces.append(value)
            elif tag == "at":
                user_id = node.get("user_id", "")
                name = node.get("user_name", "")
                if isinstance(user_id, str) and user_id:
                    mentions.append(user_id)
                pieces.append("@" + (name if isinstance(name, str) and name else "用户"))
            elif tag in {"img", "media"}:
                pieces.append("[附件]")
            elif tag in {"emotion", "hr"}:
                pieces.append("[表情]" if tag == "emotion" else "---")
            elif tag is not None:
                pieces.append(f"[{str(tag)[:32]}]")
        line = "".join(pieces).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) > 100_000:
        raise FeishuInboundError("Feishu post text is too large")
    return text, tuple(sorted(set(mentions)))


def extract_feishu_text(
    message_type: str, content: Mapping[str, Any]
) -> tuple[str, tuple[str, ...]]:
    if message_type == "text":
        text = content.get("text", "")
        if not isinstance(text, str):
            raise FeishuInboundError("Feishu text content is invalid")
        text = text.strip()
        if len(text) > 100_000:
            raise FeishuInboundError("Feishu text is too large")
        return text, ()
    if message_type == "post":
        return _rich_text(content)
    return "", ()


def extract_feishu_resources(
    message_type: str, content: Mapping[str, Any]
) -> tuple[FeishuEventResource, ...]:
    if message_type == "image":
        key = _string(content, "image_key")
        if not key:
            raise FeishuInboundError("Feishu image key is missing")
        return (FeishuEventResource("image", key, None),)
    if message_type == "file":
        key = _string(content, "file_key")
        filename = _string(content, "file_name")
        if not key or not filename:
            raise FeishuInboundError("Feishu file identity is missing")
        return (FeishuEventResource("file", key, filename),)
    if message_type != "post":
        return ()
    locale: Mapping[str, Any] = content
    for key in ("zh_cn", "en_us", "ja_jp"):
        candidate = content.get(key)
        if isinstance(candidate, Mapping):
            locale = candidate
            break
    blocks = locale.get("content", ())
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        return ()
    resources: list[FeishuEventResource] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        if not isinstance(block, Sequence) or isinstance(block, (str, bytes)):
            continue
        for node in block:
            if not isinstance(node, Mapping):
                continue
            tag = node.get("tag")
            resource_type = "image" if tag == "img" else "file" if tag == "media" else None
            if resource_type is None:
                continue
            key_name = "image_key" if resource_type == "image" else "file_key"
            resource_key = _string(node, key_name)
            if not resource_key:
                continue
            identity = (resource_type, resource_key)
            if identity in seen:
                continue
            seen.add(identity)
            filename = _string(node, "file_name") or None
            resources.append(FeishuEventResource(resource_type, resource_key, filename))
            if len(resources) > 20:
                raise FeishuInboundError("Feishu post has too many resources")
    return tuple(resources)


class FeishuInboundProcessor:
    def __init__(
        self,
        inbox: CommunicationInbox,
        routes: FeishuRouteLedger,
        *,
        attachment_loader: Callable[..., AttachmentRef] | None = None,
    ) -> None:
        self._inbox = inbox
        self._routes = routes
        self._attachment_loader = attachment_loader
        self._lock = threading.RLock()

    def process(
        self,
        raw_event: Mapping[str, Any],
        *,
        policy: FeishuInboundPolicy,
        record: FeishuEventRecord,
    ) -> FeishuInboundOutcome:
        if not record.signature_verified or not record.app_id_verified:
            raise FeishuInboundError("Feishu event transport verification is missing")
        if record.persisted_at_ms < record.captured_at_ms:
            raise FeishuInboundError("Feishu persistence time predates capture")
        try:
            raw_payload = canonical_json_bytes(raw_event)
        except (TypeError, ValueError) as exc:
            raise FeishuInboundError("Feishu raw event is not canonical JSON data") from exc
        if (
            len(raw_payload) != record.raw_payload_size_bytes
            or hashlib.sha256(raw_payload).hexdigest() != record.raw_payload_sha256
        ):
            raise FeishuInboundError("Feishu raw event does not match persisted payload")
        header = raw_event.get("header")
        event = raw_event.get("event")
        if not isinstance(header, Mapping) or not isinstance(event, Mapping):
            raise FeishuInboundError("Feishu event envelope is invalid")
        event_id = _string(header, "event_id")
        event_type = _string(header, "event_type")
        app_id = _string(header, "app_id", limit=256)
        tenant_key = _string(header, "tenant_key", limit=512)
        if (
            not event_id
            or event_type != "im.message.receive_v1"
            or app_id != policy.app_id
            or tenant_key != policy.platform_tenant_key
        ):
            raise FeishuInboundError("Feishu event identity or tenant is invalid")
        sender = event.get("sender")
        message = event.get("message")
        if not isinstance(sender, Mapping) or not isinstance(message, Mapping):
            raise FeishuInboundError("Feishu sender or message is missing")
        sender_id = sender.get("sender_id")
        if not isinstance(sender_id, Mapping):
            raise FeishuInboundError("Feishu sender identity is invalid")
        open_id = _string(sender_id, "open_id")
        user_id = _string(sender_id, "user_id")
        union_id = _string(sender_id, "union_id")
        sender_identity = open_id or user_id or union_id
        if not sender_identity:
            raise FeishuInboundError("Feishu sender identity is missing")
        sender_type = _string(sender, "sender_type", limit=64)
        message_id = _string(message, "message_id")
        chat_id = _string(message, "chat_id")
        chat_type = _string(message, "chat_type", limit=64)
        message_type = _string(message, "message_type", limit=64)
        root_id = _string(message, "root_id") or None
        parent_id = _string(message, "parent_id") or None
        thread_id = _string(message, "thread_id") or None
        if not message_id or not chat_id or chat_type not in {"p2p", "group"}:
            raise FeishuInboundError("Feishu message route is invalid")
        content = _content_json(message.get("content", ""))
        text, rich_mentions = extract_feishu_text(message_type, content)
        resources = extract_feishu_resources(message_type, content)
        mentions_raw = message.get("mentions", ()) or ()
        if not isinstance(mentions_raw, Sequence) or isinstance(mentions_raw, (str, bytes)):
            raise FeishuInboundError("Feishu mentions are invalid")
        mention_ids = set(rich_mentions)
        for mention in mentions_raw:
            if not isinstance(mention, Mapping):
                raise FeishuInboundError("Feishu mention is invalid")
            identifier = _mention_identifier(mention)
            if identifier:
                mention_ids.add(identifier)
        mentioned_bot = bool(mention_ids.intersection(policy.bot_open_ids))

        classification = "ACCEPTED"
        if sender_type in {"app", "bot"} or sender_identity in policy.bot_open_ids:
            classification = "SELF_MESSAGE"
        elif policy.allowed_sender_ids and sender_identity not in policy.allowed_sender_ids:
            classification = "UNEXPECTED_SENDER"
        elif chat_type == "p2p" and not policy.allow_p2p:
            classification = "P2P_DISABLED"
        elif chat_type == "group" and not policy.allow_groups:
            classification = "GROUP_DISABLED"
        elif chat_type == "group" and policy.group_requires_mention and not mentioned_bot:
            classification = "GROUP_MENTION_REQUIRED"
        elif resources:
            classification = "ATTACHMENT_PENDING"
        elif message_type not in {"text", "post"}:
            classification = "UNSUPPORTED_MESSAGE_TYPE"
        elif not text:
            classification = "EMPTY_TEXT"

        conversation_identity = thread_id or root_id or chat_id
        conversation_ref = _opaque("fsconv_", conversation_identity)
        sender_ref = _opaque("fsuser_", sender_identity)
        channel_message_ref = _opaque("fsmsg_", message_id)
        scope = InboundScope(
            channel="feishu",
            tenant_id=policy.tenant_id,
            link_account_id=policy.link_account_id,
            conversation_ref=conversation_ref,
            channel_message_ref=channel_message_ref,
            sender_ref=sender_ref,
        )
        keys = derive_inbound_scope_keys(scope)
        ingress_id = "fsin_" + keys.idempotency_key
        existing_ingress = self._inbox.get_ingress(ingress_id)
        if existing_ingress is not None:
            if (
                existing_ingress.raw_payload_sha256 != record.raw_payload_sha256
                or existing_ingress.raw_payload_size_bytes != record.raw_payload_size_bytes
            ):
                raise FeishuInboundError("Feishu duplicate event changed its raw payload")
            persisted = self._inbox.persist_and_advance_cursor(
                existing_ingress,
                persisted_at_ms=record.persisted_at_ms,
            )
            stored = existing_ingress.envelope
            forwarded = not stored.text.startswith("[communication event withheld:")
            return FeishuInboundOutcome(
                envelope=stored,
                ack_permit=persisted.permit,
                classification="ACCEPTED" if forwarded else "DUPLICATE_WITHHELD",
                should_forward=forwarded,
                route_key=derive_feishu_route_key(
                    policy.tenant_id,
                    policy.link_account_id,
                    keys.conversation_scope_hash,
                ),
                resource_ids=(),
                duplicate=True,
            )
        route_key = None
        resource_ids: tuple[str, ...] = ()
        admitted_attachments: tuple[AttachmentRef, ...] = ()
        if classification in {
            "ACCEPTED",
            "ATTACHMENT_PENDING",
            "UNSUPPORTED_MESSAGE_TYPE",
            "EMPTY_TEXT",
        }:
            with self._lock:
                route_key = self._routes.upsert(
                    tenant_id=policy.tenant_id,
                    link_account_id=policy.link_account_id,
                    conversation_scope_hash=keys.conversation_scope_hash,
                    chat_id=chat_id,
                    message_id=message_id,
                    root_id=root_id,
                    parent_id=parent_id,
                    thread_id=thread_id,
                    sender_open_id=open_id or None,
                    observed_at_ms=record.captured_at_ms,
                )
                resource_ids = tuple(
                    self._routes.register_resource(
                        tenant_id=policy.tenant_id,
                        link_account_id=policy.link_account_id,
                        conversation_scope_hash=keys.conversation_scope_hash,
                        source_message_ref=channel_message_ref,
                        message_id=message_id,
                        resource_type=resource.resource_type,
                        resource_key=resource.resource_key,
                        filename=resource.filename,
                        created_at_ms=record.captured_at_ms,
                    )
                    for resource in resources
                )
            if resources and self._attachment_loader is not None:
                try:
                    admitted_attachments = tuple(
                        self._attachment_loader(
                            resource_id,
                            tenant_id=policy.tenant_id,
                            link_account_id=policy.link_account_id,
                            conversation_scope_hash=keys.conversation_scope_hash,
                        )
                        for resource_id in resource_ids
                    )
                    classification = "ACCEPTED"
                except Exception:
                    admitted_attachments = ()
                    classification = "ATTACHMENT_REJECTED"
        envelope = InboundEnvelope(
            inbound_id="fsin_" + keys.idempotency_key,
            channel="feishu",
            tenant_id=policy.tenant_id,
            link_account_id=policy.link_account_id,
            conversation_ref=conversation_ref,
            conversation_scope_hash=keys.conversation_scope_hash,
            principal_scope_hash=keys.principal_scope_hash,
            message_scope_hash=keys.message_scope_hash,
            channel_message_ref=channel_message_ref,
            sender_ref=sender_ref,
            received_at_ms=record.captured_at_ms,
            idempotency_key=keys.idempotency_key,
            channel_metadata_hash=canonical_sha256(
                {
                    "event_id_sha256": hashlib.sha256(event_id.encode()).hexdigest(),
                    "message_type": message_type,
                    "chat_type": chat_type,
                    "chat_id_sha256": hashlib.sha256(chat_id.encode()).hexdigest(),
                    "root_id_sha256": None
                    if root_id is None
                    else hashlib.sha256(root_id.encode()).hexdigest(),
                    "parent_id_sha256": None
                    if parent_id is None
                    else hashlib.sha256(parent_id.encode()).hexdigest(),
                    "thread_id_sha256": None
                    if thread_id is None
                    else hashlib.sha256(thread_id.encode()).hexdigest(),
                    "mentioned_bot": mentioned_bot,
                    "classification": classification,
                }
            ),
            text=(
                text
                if text
                else "[收到飞书附件]"
                if admitted_attachments
                else f"[communication event withheld:{classification.lower()}]"
            ),
            attachments=admitted_attachments,
            reply_to_message_ref=None
            if parent_id is None
            else _opaque("fsmsg_", parent_id),
            root_message_ref=None if root_id is None else _opaque("fsmsg_", root_id),
        )
        ingress_id = envelope.inbound_id
        stream_key = derive_cursor_stream_key(
            "feishu", policy.tenant_id, policy.link_account_id
        )
        with self._lock:
            existing = self._inbox.get_ingress(ingress_id)
            if existing is None:
                cursor = self._inbox.get_cursor(stream_key)
                previous = None if cursor is None else cursor.cursor_sha256
                next_token = event_id
            else:
                previous = existing.previous_cursor_sha256
                next_token = existing.next_cursor_token
            ingress = InboxIngress(
                ingress_id=ingress_id,
                envelope=envelope,
                raw_payload_object_id=record.raw_payload_object_id,
                raw_payload_sha256=record.raw_payload_sha256,
                raw_payload_size_bytes=record.raw_payload_size_bytes,
                cursor_stream_key=stream_key,
                previous_cursor_sha256=previous,
                next_cursor_token=next_token,
                next_cursor_sha256=cursor_token_sha256(next_token),
                captured_at_ms=record.captured_at_ms,
                ingress_sha256="0" * 64,
            ).with_computed_sha256()
            persisted = self._inbox.persist_and_advance_cursor(
                ingress, persisted_at_ms=record.persisted_at_ms
            )
        return FeishuInboundOutcome(
            envelope=envelope,
            ack_permit=persisted.permit,
            classification=classification,
            should_forward=classification == "ACCEPTED",
            route_key=route_key,
            resource_ids=resource_ids,
            duplicate=persisted.duplicate,
        )


__all__ = [
    "FeishuEventRecord",
    "FeishuEventResource",
    "FeishuInboundError",
    "FeishuInboundOutcome",
    "FeishuInboundPolicy",
    "FeishuInboundProcessor",
    "extract_feishu_text",
    "extract_feishu_resources",
]
