"""Delivery-effect-bound WeChat iLink text sending."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import (
    DeliveryPartReceipt,
    DeliveryReceipt,
    DeliveryTicketPayload,
    OutboundPart,
    OutboundPlan,
    canonical_json_bytes,
    canonical_sha256,
    grant_from_outbound_part,
)

from .delivery_ledger import DeliveryLedger, DeliveryLedgerRecord
from .wechat_session import WechatSessionLedger


WECHAT_ILINK_ORIGIN = "https://ilinkai.weixin.qq.com"
WECHAT_SEND_PATH = "/ilink/bot/sendmessage"
WECHAT_GET_UPLOAD_URL_PATH = "/ilink/bot/getuploadurl"
WECHAT_ILINK_APP_ID = "bot"
WECHAT_ILINK_CHANNEL_VERSION = "2.4.6"
WECHAT_ILINK_CLIENT_VERSION = "132102"
WECHAT_SESSION_EXPIRED_CODE = -14
WECHAT_RATE_LIMIT_CODE = -2


class WechatTextOutboundError(RuntimeError):
    def __init__(self, code: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.outcome_unknown = outcome_unknown


class WechatOutboundPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_id: str = Field(default="wechat.ilink.text.v1", pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    origin: Literal[WECHAT_ILINK_ORIGIN] = WECHAT_ILINK_ORIGIN
    max_chars_per_segment: int = Field(default=1_800, ge=1, le=5_000)
    max_segments_per_part: int = Field(default=50, ge=1, le=100)
    max_total_segments: int = Field(default=200, ge=1, le=1_000)
    min_attempt_interval_ms: int = Field(default=200, ge=0, le=60_000)
    rate_limit_retries: int = Field(default=2, ge=0, le=5)
    rate_limit_delay_ms: int = Field(default=2_000, ge=100, le=60_000)
    cdn_origin: Literal["https://novac2c.cdn.weixin.qq.com/c2c"] = (
        "https://novac2c.cdn.weixin.qq.com/c2c"
    )
    max_file_bytes: int = Field(default=134_217_728, ge=1, le=2_147_483_632)
    file_io_chunk_bytes: int = Field(default=262_144, ge=4_096, le=4_194_304)
    max_cdn_response_bytes: int = Field(default=1_048_576, ge=1_024, le=8_388_608)
    max_concurrent_files_per_account: int = Field(default=1, ge=1, le=16)
    max_reserved_bytes_per_account: int = Field(
        default=268_435_456, ge=1, le=8_589_934_592
    )
    progress_interval_bytes: int = Field(default=4_194_304, ge=65_536, le=67_108_864)
    upload_base_timeout_ms: int = Field(default=5_000, ge=0, le=3_600_000)
    upload_min_timeout_ms: int = Field(default=5_000, ge=1, le=3_600_000)
    upload_max_timeout_ms: int = Field(default=3_600_000, ge=1, le=3_600_000)
    upload_nominal_throughput_bps: int = Field(
        default=2_000_000, ge=1, le=10_000_000_000
    )
    upload_minimum_throughput_bps: int = Field(
        default=131_072, ge=1, le=10_000_000_000
    )
    upload_safety_factor_milli: int = Field(default=1_500, ge=1_000, le=10_000)
    upload_idle_timeout_ms: int = Field(default=30_000, ge=1, le=3_600_000)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_file_controls(self) -> Self:
        if self.max_reserved_bytes_per_account < self.max_file_bytes:
            raise ValueError("WeChat account byte budget is smaller than one allowed file")
        if self.upload_min_timeout_ms > self.upload_max_timeout_ms:
            raise ValueError("WeChat upload timeout range is invalid")
        if self.upload_idle_timeout_ms > self.upload_max_timeout_ms:
            raise ValueError("WeChat upload idle timeout exceeds operation maximum")
        if self.upload_minimum_throughput_bps > self.upload_nominal_throughput_bps:
            raise ValueError("WeChat upload throughput range is invalid")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"policy_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.policy_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"policy_sha256": self.computed_sha256()})


WechatTextOutboundPolicy = WechatOutboundPolicy


def default_wechat_text_policy() -> WechatOutboundPolicy:
    return WechatOutboundPolicy(policy_sha256="0" * 64).with_computed_sha256()


@dataclass(frozen=True)
class WechatIlinkResponse:
    status_code: int
    payload: Mapping[str, Any]
    body_sha256: str

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("WeChat HTTP status is invalid")
        if len(self.body_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.body_sha256
        ):
            raise ValueError("WeChat response digest is invalid")
        if not isinstance(self.payload, Mapping) or len(self.payload) > 64:
            raise ValueError("WeChat response payload is invalid")
        object.__setattr__(self, "payload", dict(self.payload))


class WechatIlinkTextTransport(Protocol):
    def send_message(
        self,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse: ...


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class HttpWechatIlinkTextTransport:
    """Small fixed-origin HTTPS transport with no proxy, cookies or redirect support."""

    def __init__(self, *, origin: str = WECHAT_ILINK_ORIGIN, max_response_bytes: int = 1_048_576) -> None:
        parsed = urlsplit(origin)
        if (
            origin != WECHAT_ILINK_ORIGIN
            or parsed.scheme != "https"
            or parsed.hostname != "ilinkai.weixin.qq.com"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("WeChat iLink origin is not allowed")
        if not 1_024 <= max_response_bytes <= 8_388_608:
            raise ValueError("WeChat response limit is invalid")
        self._host = "ilinkai.weixin.qq.com"
        self._max_response_bytes = max_response_bytes

    def send_message(
        self,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse:
        return self._post_json(
            WECHAT_SEND_PATH,
            body,
            bot_token=bot_token,
            timeout_seconds=timeout_seconds,
        )

    def get_upload_url(
        self,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse:
        return self._post_json(
            WECHAT_GET_UPLOAD_URL_PATH,
            body,
            bot_token=bot_token,
            timeout_seconds=timeout_seconds,
        )

    def _post_json(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> WechatIlinkResponse:
        if path not in {WECHAT_SEND_PATH, WECHAT_GET_UPLOAD_URL_PATH}:
            raise WechatTextOutboundError("wechat.send.endpoint.not_allowed")
        token = bot_token.strip()
        if (
            not token
            or token != bot_token
            or "\x00" in token
            or len(token.encode("utf-8")) > 8_192
            or not 1 <= timeout_seconds <= 3_600
        ):
            raise WechatTextOutboundError("wechat.send.credentials_or_timeout.invalid")
        request_bytes = canonical_json_bytes(dict(body))
        uin = base64.b64encode(str(secrets.randbits(32)).encode("ascii")).decode("ascii")
        headers = {
            "Authorization": f"Bearer {token}",
            "AuthorizationType": "ilink_bot_token",
            "Content-Type": "application/json",
            "Content-Length": str(len(request_bytes)),
            "iLink-App-Id": WECHAT_ILINK_APP_ID,
            "iLink-App-ClientVersion": WECHAT_ILINK_CLIENT_VERSION,
            "X-WECHAT-UIN": uin,
        }
        connection = http.client.HTTPSConnection(self._host, 443, timeout=timeout_seconds)
        response = None
        try:
            connection.request("POST", path, body=request_bytes, headers=headers)
            response = connection.getresponse()
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_bytes = int(declared)
                except ValueError as exc:
                    raise WechatTextOutboundError(
                        "wechat.send.response_length.invalid", outcome_unknown=True
                    ) from exc
                if declared_bytes < 0 or declared_bytes > self._max_response_bytes:
                    raise WechatTextOutboundError(
                        "wechat.send.response_too_large", outcome_unknown=True
                    )
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise WechatTextOutboundError(
                    "wechat.send.response_too_large", outcome_unknown=True
                )
            if not raw.strip():
                payload: Mapping[str, Any] = {}
            else:
                try:
                    parsed = json.loads(
                        raw.decode("utf-8", errors="strict"),
                        object_pairs_hook=_reject_duplicate_pairs,
                        parse_constant=lambda value: (_ for _ in ()).throw(
                            ValueError(f"invalid JSON constant: {value}")
                        ),
                    )
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                    raise WechatTextOutboundError(
                        "wechat.send.response_json.invalid", outcome_unknown=True
                    ) from exc
                if not isinstance(parsed, dict):
                    raise WechatTextOutboundError(
                        "wechat.send.response_shape.invalid", outcome_unknown=True
                    )
                payload = parsed
            return WechatIlinkResponse(
                status_code=response.status,
                payload=payload,
                body_sha256=hashlib.sha256(raw).hexdigest(),
            )
        except WechatTextOutboundError:
            raise
        except Exception as exc:
            raise WechatTextOutboundError(
                "wechat.send.transport.unknown", outcome_unknown=True
            ) from exc
        finally:
            if response is not None:
                response.close()
            connection.close()


class WechatRateGate:
    def __init__(
        self,
        *,
        clock_ms: Callable[[], int],
        sleeper: Callable[[float], None],
    ) -> None:
        self._clock_ms = clock_ms
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_attempt_ms: dict[str, int] = {}

    def wait(self, account_key: str, *, minimum_interval_ms: int) -> None:
        with self._lock:
            now = self._clock_ms()
            earliest = self._next_attempt_ms.get(account_key, now)
            if now < earliest:
                self._sleeper((earliest - now) / 1_000)
                now = self._clock_ms()
                if now < earliest:
                    raise WechatTextOutboundError("wechat.send.rate_gate.clock_stalled")
            self._next_attempt_ms[account_key] = max(now, earliest) + minimum_interval_ms


def split_wechat_text(text: str, *, limit: int) -> tuple[str, ...]:
    if not text or "\x00" in text or not 1 <= limit <= 5_000:
        raise ValueError("WeChat text or segment limit is invalid")
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        elif cut < limit:
            cut += 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if not chunks or any(not chunk or len(chunk) > limit for chunk in chunks):
        raise ValueError("WeChat text segmentation failed")
    if "".join(chunks) != text:
        raise ValueError("WeChat text segmentation changed the payload")
    return tuple(chunks)


def derive_wechat_client_id(effect_id: str, part_id: str, segment_index: int) -> str:
    digest = canonical_sha256(
        {
            "domain": "tiangong.communication.wechat-client-id.v1",
            "effect_id": effect_id,
            "part_id": part_id,
            "segment_index": segment_index,
        }
    )
    return "tiangong-wechat-" + digest[:32]


def wechat_response_integer(response: WechatIlinkResponse, key: str) -> int | None:
    if key not in response.payload or response.payload[key] is None:
        return None
    value = response.payload[key]
    if isinstance(value, bool):
        raise WechatTextOutboundError("wechat.send.response_code.invalid", outcome_unknown=True)
    if isinstance(value, str):
        if not value or (value[0] == "-" and not value[1:].isdecimal()) or (
            value[0] != "-" and not value.isdecimal()
        ):
            raise WechatTextOutboundError("wechat.send.response_code.invalid", outcome_unknown=True)
        value = int(value)
    if not isinstance(value, int):
        raise WechatTextOutboundError("wechat.send.response_code.invalid", outcome_unknown=True)
    return value


def classify_wechat_ilink_response(response: WechatIlinkResponse) -> Literal[
    "accepted", "context_expired", "rate_limited", "rejected_final", "ambiguous"
]:
    if response.status_code == 429:
        return "rate_limited"
    if response.status_code in {401, 403} or 400 <= response.status_code < 500:
        return "rejected_final"
    if response.status_code < 200 or response.status_code >= 300:
        return "ambiguous"
    ret = wechat_response_integer(response, "ret")
    errcode = wechat_response_integer(response, "errcode")
    if ret == WECHAT_SESSION_EXPIRED_CODE or errcode == WECHAT_SESSION_EXPIRED_CODE:
        return "context_expired"
    if ret == WECHAT_RATE_LIMIT_CODE or errcode == WECHAT_RATE_LIMIT_CODE:
        return "rate_limited"
    if ret in {0, None} and errcode in {0, None}:
        return "accepted"
    return "rejected_final"


def _bind_text_plan(
    payload: DeliveryTicketPayload,
    plan: OutboundPlan,
    policy: WechatOutboundPolicy,
) -> tuple[tuple[OutboundPart, tuple[str, ...]], ...]:
    if not policy.has_valid_sha256() or plan.channel_policy_hash != policy.policy_sha256:
        raise WechatTextOutboundError("wechat.send.policy.mismatch")
    if not plan.has_valid_plan_sha256() or payload.outbound_plan_sha256 != plan.plan_sha256:
        raise WechatTextOutboundError("wechat.send.plan_digest.mismatch")
    fields = (
        "delivery_id",
        "effect_id",
        "request_id",
        "run_id",
        "generation",
        "channel",
        "tenant_id",
        "link_account_id",
        "conversation_ref",
        "conversation_scope_hash",
        "recipient_scope_hash",
        "reply_to_message_ref",
        "outbound_plan_id",
        "channel_policy_hash",
    )
    if any(getattr(payload, field) != getattr(plan, field) for field in fields):
        raise WechatTextOutboundError("wechat.send.ticket_plan.mismatch")
    if payload.channel != "wechat" or not payload.allow_text or payload.allow_files:
        raise WechatTextOutboundError("wechat.send.text_only_ticket.required")
    expected_grants = tuple(grant_from_outbound_part(part) for part in plan.parts)
    if payload.parts != expected_grants or any(part.kind != "text" for part in plan.parts):
        raise WechatTextOutboundError("wechat.send.parts.mismatch")
    segmented: list[tuple[OutboundPart, tuple[str, ...]]] = []
    total = 0
    for part in plan.parts:
        assert part.text is not None
        segments = split_wechat_text(part.text, limit=policy.max_chars_per_segment)
        if len(segments) > policy.max_segments_per_part:
            raise WechatTextOutboundError("wechat.send.segment_limit.exceeded")
        total += len(segments)
        segmented.append((part, segments))
    if total > policy.max_total_segments:
        raise WechatTextOutboundError("wechat.send.total_segment_limit.exceeded")
    return tuple(segmented)


def _response_fact(
    response: WechatIlinkResponse,
    *,
    client_id: str,
    segment_index: int,
    context_used: bool,
    context_retry: bool,
    rate_retries: int,
) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "segment_index": segment_index,
        "http_status": response.status_code,
        "ret": wechat_response_integer(response, "ret"),
        "errcode": wechat_response_integer(response, "errcode"),
        "body_sha256": response.body_sha256,
        "context_used": context_used,
        "context_retry": context_retry,
        "rate_retries": rate_retries,
    }


def _receipt_id(effect_id: str, status: str, parts: tuple[DeliveryPartReceipt, ...]) -> str:
    return "wxreceipt_" + canonical_sha256(
        {
            "effect_id": effect_id,
            "status": status,
            "parts": [part.model_dump(mode="json") for part in parts],
        }
    )


class WechatTextDeliveryService:
    def __init__(
        self,
        ledger: DeliveryLedger,
        sessions: WechatSessionLedger,
        transport: WechatIlinkTextTransport,
        *,
        clock_ms: Callable[[], int] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        rate_gate: WechatRateGate | None = None,
    ) -> None:
        self._ledger = ledger
        self._sessions = sessions
        self._transport = transport
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._sleeper = sleeper
        self._rate_gate = rate_gate or WechatRateGate(
            clock_ms=self._clock_ms,
            sleeper=sleeper,
        )
        self._send_lock = threading.RLock()

    @staticmethod
    def _build_body(
        *,
        to_user_id: str,
        text: str,
        context_token: str | None,
        run_id: str,
        client_id: str,
    ) -> dict[str, Any]:
        return {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
                "context_token": context_token or None,
                "run_id": run_id,
            },
            "base_info": {
                "channel_version": WECHAT_ILINK_CHANNEL_VERSION,
                "bot_agent": "TiangongZaowu/3.0.0",
            },
        }

    def _make_receipt(
        self,
        payload: DeliveryTicketPayload,
        *,
        status: str,
        parts: tuple[DeliveryPartReceipt, ...],
        observed_at_ms: int,
        error_code: str | None,
    ) -> DeliveryReceipt:
        return DeliveryReceipt(
            receipt_id=_receipt_id(payload.effect_id, status, parts),
            ticket_id=payload.ticket_id,
            delivery_id=payload.delivery_id,
            effect_id=payload.effect_id,
            request_id=payload.request_id,
            run_id=payload.run_id,
            generation=payload.generation,
            channel="wechat",
            status=status,
            parts=parts,
            observed_at_ms=observed_at_ms,
            error_code=error_code,
            receipt_sha256="0" * 64,
        ).with_computed_receipt_sha256()

    @staticmethod
    def _planned_receipts(
        remaining: tuple[OutboundPart, ...], *, observed_at_ms: int
    ) -> tuple[DeliveryPartReceipt, ...]:
        return tuple(
            DeliveryPartReceipt(
                part_id=part.part_id,
                index=part.index,
                kind="text",
                stage="PLANNED",
                attempt=1,
                started_at_ms=observed_at_ms,
                finished_at_ms=observed_at_ms,
                evidence_sha256=canonical_sha256(
                    {
                        "domain": "tiangong.communication.wechat-planned-part.v1",
                        "part_id": part.part_id,
                        "text_sha256": part.text_sha256,
                    }
                ),
            )
            for part in remaining
        )

    def _recover_started(
        self,
        record: DeliveryLedgerRecord,
        payload: DeliveryTicketPayload,
        plan: OutboundPlan,
        *,
        now_ms: int,
    ) -> DeliveryReceipt:
        started = record.side_effect_started_at_ms or now_ms
        parts = tuple(
            DeliveryPartReceipt(
                part_id=part.part_id,
                index=part.index,
                kind="text",
                stage="AMBIGUOUS",
                attempt=1,
                started_at_ms=started,
                finished_at_ms=now_ms,
                evidence_sha256=canonical_sha256(
                    {
                        "domain": "tiangong.communication.wechat-restart-ambiguity.v1",
                        "effect_id": plan.effect_id,
                        "part_id": part.part_id,
                    }
                ),
                error_code="wechat.send.receipt_missing_after_restart",
            )
            for part in plan.parts
        )
        receipt = self._make_receipt(
            payload,
            status="RECONCILE_REQUIRED",
            parts=parts,
            observed_at_ms=now_ms,
            error_code="wechat.send.receipt_missing_after_restart",
        )
        return self._ledger.record_receipt(receipt).receipt or receipt

    def send(
        self,
        payload: DeliveryTicketPayload,
        plan: OutboundPlan,
        *,
        policy: WechatOutboundPolicy,
        bot_token: str,
        ilink_account_id: str,
        session_key: str,
    ) -> DeliveryReceipt:
        token = bot_token.strip()
        if not token or token != bot_token or "\x00" in token or len(token.encode()) > 8_192:
            raise WechatTextOutboundError("wechat.send.bot_token.invalid")
        if not ilink_account_id or "\x00" in ilink_account_id:
            raise WechatTextOutboundError("wechat.send.account.invalid")
        segmented = _bind_text_plan(payload, plan, policy)
        to_user_id = self._sessions.resolve_reply_target(
            session_key=session_key,
            account_id=ilink_account_id,
            conversation_scope_hash=plan.conversation_scope_hash,
        )
        context_token = self._sessions.resolve_context_token(
            session_key=session_key,
            account_id=ilink_account_id,
            conversation_scope_hash=plan.conversation_scope_hash,
        )
        with self._send_lock:
            claimed_at = self._clock_ms()
            if claimed_at < payload.not_before_ms or claimed_at > payload.expires_at_ms:
                raise WechatTextOutboundError("wechat.send.ticket_time.invalid")
            record = self._ledger.require_verified_delivery(payload)
            if record.receipt is not None:
                return record.receipt
            if record.state == "RECONCILE_REQUIRED":
                raise WechatTextOutboundError("wechat.send.reconciliation_required")
            if record.state == "SIDE_EFFECT_STARTED":
                return self._recover_started(record, payload, plan, now_ms=self._clock_ms())
            if record.state != "CLAIMED":
                raise WechatTextOutboundError("wechat.send.effect_state.invalid")

            completed: list[DeliveryPartReceipt] = []
            effect_has_acceptance = False
            side_effect_started = False
            for part_offset, (part, segments) in enumerate(segmented):
                part_started = self._clock_ms()
                attempts = 0
                segment_facts: list[dict[str, Any]] = []
                accepted_in_part = 0
                for segment_index, segment in enumerate(segments):
                    client_id = derive_wechat_client_id(
                        payload.effect_id, part.part_id, segment_index
                    )
                    rate_retries = 0
                    retried_without_context = False
                    segment_context = context_token
                    while True:
                        self._rate_gate.wait(
                            f"{payload.tenant_id}:{payload.link_account_id}",
                            minimum_interval_ms=policy.min_attempt_interval_ms,
                        )
                        attempt_at = self._clock_ms()
                        if not side_effect_started:
                            if attempt_at > payload.expires_at_ms:
                                error = "wechat.send.ticket_expired_before_attempt"
                                failed = DeliveryPartReceipt(
                                    part_id=part.part_id,
                                    index=part.index,
                                    kind="text",
                                    stage="FAILED_RETRYABLE",
                                    attempt=max(1, attempts),
                                    started_at_ms=part_started,
                                    finished_at_ms=attempt_at,
                                    evidence_sha256=canonical_sha256(
                                        {"error": error, "part_id": part.part_id}
                                    ),
                                    error_code=error,
                                )
                                parts = tuple(completed) + (failed,) + self._planned_receipts(
                                    tuple(item for item, _ in segmented[part_offset + 1 :]),
                                    observed_at_ms=attempt_at,
                                )
                                receipt = self._make_receipt(
                                    payload,
                                    status="FAILED_RETRYABLE",
                                    parts=parts,
                                    observed_at_ms=attempt_at,
                                    error_code=error,
                                )
                                return self._ledger.record_receipt(
                                    receipt, side_effect_absent_verified=True
                                ).receipt or receipt
                            self._ledger.mark_side_effect_started(
                                payload.effect_id,
                                started_at_ms=attempt_at,
                            )
                            side_effect_started = True
                        attempts += 1
                        body = self._build_body(
                            to_user_id=to_user_id,
                            text=segment,
                            context_token=segment_context,
                            run_id=payload.run_id,
                            client_id=client_id,
                        )
                        try:
                            response = self._transport.send_message(
                                body,
                                bot_token=token,
                                timeout_seconds=max(1, payload.send_timeout_ms // 1_000),
                            )
                            outcome = classify_wechat_ilink_response(response)
                            response_fact = _response_fact(
                                response,
                                client_id=client_id,
                                segment_index=segment_index,
                                context_used=bool(segment_context),
                                context_retry=retried_without_context,
                                rate_retries=rate_retries,
                            )
                        except WechatTextOutboundError as exc:
                            finished = self._clock_ms()
                            error = exc.code
                            failed = DeliveryPartReceipt(
                                part_id=part.part_id,
                                index=part.index,
                                kind="text",
                                stage="AMBIGUOUS",
                                attempt=attempts,
                                started_at_ms=part_started,
                                finished_at_ms=finished,
                                evidence_sha256=canonical_sha256(
                                    {
                                        "error": error,
                                        "part_id": part.part_id,
                                        "accepted_segments": accepted_in_part,
                                        "segment_facts": segment_facts,
                                    }
                                ),
                                error_code=error,
                            )
                            parts = tuple(completed) + (failed,) + self._planned_receipts(
                                tuple(item for item, _ in segmented[part_offset + 1 :]),
                                observed_at_ms=finished,
                            )
                            receipt = self._make_receipt(
                                payload,
                                status="RECONCILE_REQUIRED",
                                parts=parts,
                                observed_at_ms=finished,
                                error_code=error,
                            )
                            return self._ledger.record_receipt(receipt).receipt or receipt

                        if outcome == "accepted":
                            segment_facts.append(response_fact)
                            accepted_in_part += 1
                            effect_has_acceptance = True
                            break
                        if outcome == "context_expired" and segment_context and not retried_without_context:
                            if not self._sessions.clear_context_token(session_key=session_key):
                                raise WechatTextOutboundError(
                                    "wechat.send.context_clear_failed", outcome_unknown=False
                                )
                            context_token = None
                            segment_context = None
                            retried_without_context = True
                            segment_facts.append(response_fact)
                            continue
                        if outcome == "rate_limited" and rate_retries < policy.rate_limit_retries:
                            rate_retries += 1
                            segment_facts.append(response_fact)
                            self._sleeper(
                                policy.rate_limit_delay_ms * rate_retries / 1_000
                            )
                            continue

                        finished = self._clock_ms()
                        if outcome == "ambiguous" or accepted_in_part or (
                            outcome == "rate_limited" and effect_has_acceptance
                        ):
                            stage = "AMBIGUOUS"
                            status = "RECONCILE_REQUIRED"
                            error = (
                                "wechat.send.partial_segment_unknown"
                                if accepted_in_part
                                else "wechat.send.platform_outcome_unknown"
                            )
                        elif outcome == "rate_limited":
                            stage = "FAILED_RETRYABLE"
                            status = "FAILED_RETRYABLE"
                            error = "wechat.send.rate_limit_exhausted"
                        else:
                            stage = "FAILED_FINAL"
                            status = "FAILED_FINAL"
                            error = (
                                "wechat.send.context_expired"
                                if outcome == "context_expired"
                                else "wechat.send.platform_rejected"
                            )
                        segment_facts.append(response_fact)
                        failed = DeliveryPartReceipt(
                            part_id=part.part_id,
                            index=part.index,
                            kind="text",
                            stage=stage,
                            attempt=attempts,
                            started_at_ms=part_started,
                            finished_at_ms=finished,
                            evidence_sha256=canonical_sha256(
                                {
                                    "error": error,
                                    "part_id": part.part_id,
                                    "segment_facts": segment_facts,
                                }
                            ),
                            error_code=error,
                        )
                        parts = tuple(completed) + (failed,) + self._planned_receipts(
                            tuple(item for item, _ in segmented[part_offset + 1 :]),
                            observed_at_ms=finished,
                        )
                        receipt = self._make_receipt(
                            payload,
                            status=status,
                            parts=parts,
                            observed_at_ms=finished,
                            error_code=error,
                        )
                        return self._ledger.record_receipt(
                            receipt,
                            side_effect_absent_verified=status == "FAILED_RETRYABLE",
                        ).receipt or receipt

                part_finished = self._clock_ms()
                platform_receipt_sha256 = canonical_sha256(
                    {
                        "domain": "tiangong.communication.wechat-text-platform-receipt.v1",
                        "part_id": part.part_id,
                        "segments": segment_facts,
                    }
                )
                completed.append(
                    DeliveryPartReceipt(
                        part_id=part.part_id,
                        index=part.index,
                        kind="text",
                        stage="CHANNEL_ACCEPTED",
                        attempt=max(1, attempts),
                        started_at_ms=part_started,
                        finished_at_ms=part_finished,
                        channel_message_ref="wxout_" + platform_receipt_sha256,
                        evidence_sha256=canonical_sha256(
                            {
                                "domain": "tiangong.communication.wechat-text-part.v1",
                                "part_id": part.part_id,
                                "text_sha256": part.text_sha256,
                                "segment_count": len(segments),
                                "platform_receipt_sha256": platform_receipt_sha256,
                            }
                        ),
                        platform_receipt_sha256=platform_receipt_sha256,
                    )
                )

            observed = self._clock_ms()
            receipt = self._make_receipt(
                payload,
                status="CHANNEL_ACCEPTED",
                parts=tuple(completed),
                observed_at_ms=observed,
                error_code=None,
            )
            return self._ledger.record_receipt(receipt).receipt or receipt


__all__ = [
    "HttpWechatIlinkTextTransport",
    "WechatIlinkResponse",
    "WechatIlinkTextTransport",
    "WechatRateGate",
    "WechatTextDeliveryService",
    "WechatTextOutboundError",
    "WechatOutboundPolicy",
    "WechatTextOutboundPolicy",
    "classify_wechat_ilink_response",
    "default_wechat_text_policy",
    "derive_wechat_client_id",
    "split_wechat_text",
    "wechat_response_integer",
]
