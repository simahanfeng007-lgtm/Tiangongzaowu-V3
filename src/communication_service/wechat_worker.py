"""Lease-gated production WeChat iLink poller."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from contracts import canonical_json_bytes, canonical_sha256

from .adapters import AdapterHealth, AdapterRegistry
from .channel_authority import ChannelAuthorityError
from .credential_vault import ChannelCredentialVault
from .inbox import CommunicationInbox, cursor_token_sha256, derive_cursor_stream_key
from .production_ingress import ProductionIngressError
from .raw_inbound_store import RawInboundStore
from .wechat_inbound import (
    WechatAttachmentIngestor,
    WechatInboundPolicy,
    WechatPollRecord,
    WechatTextInboundProcessor,
    external_cursor_from_local,
)
from .wechat_session import WechatSessionLedger


_HOST = "ilinkai.weixin.qq.com"
_APP_ID = "bot"
_CHANNEL_VERSION = "2.4.6"
_CLIENT_VERSION = "132102"
_START_PATH = "/ilink/bot/msg/notifystart"
_POLL_PATH = "/ilink/bot/getupdates"
_STOP_PATH = "/ilink/bot/msg/notifystop"


class WechatPollError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WechatPollError("wechat.poll.response_duplicate_key")
        result[key] = value
    return result


class WechatIlinkPollTransport:
    def __init__(self, *, max_response_bytes: int = 16_777_216) -> None:
        if not 1_024 <= max_response_bytes <= 67_108_864:
            raise ValueError("WeChat poll response limit is invalid")
        self._max_response_bytes = max_response_bytes

    @staticmethod
    def _base_info() -> dict[str, str]:
        return {"channel_version": _CHANNEL_VERSION, "bot_agent": "TiangongZaowu/3.0.0"}

    def post(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> tuple[dict[str, Any], bytes]:
        if path not in {_START_PATH, _POLL_PATH, _STOP_PATH}:
            raise WechatPollError("wechat.poll.path_forbidden")
        if not bot_token or bot_token != bot_token.strip() or not 1 <= timeout_seconds <= 65:
            raise WechatPollError("wechat.poll.credentials_or_timeout_invalid")
        wire = canonical_json_bytes(dict(body))
        uin = base64.b64encode(str(secrets.randbits(32)).encode("ascii")).decode("ascii")
        connection = http.client.HTTPSConnection(_HOST, 443, timeout=timeout_seconds)
        response = None
        try:
            connection.request(
                "POST",
                path,
                body=wire,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {bot_token}",
                    "AuthorizationType": "ilink_bot_token",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(wire)),
                    "iLink-App-Id": _APP_ID,
                    "iLink-App-ClientVersion": _CLIENT_VERSION,
                    "X-WECHAT-UIN": uin,
                },
            )
            response = connection.getresponse()
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise WechatPollError("wechat.poll.response_too_large")
            if response.status < 200 or response.status >= 300:
                raise WechatPollError("wechat.poll.http_rejected")
            try:
                value = json.loads(
                    raw.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                raise WechatPollError("wechat.poll.response_json_invalid") from exc
            if not isinstance(value, dict):
                raise WechatPollError("wechat.poll.response_shape_invalid")
            ret = value.get("ret")
            if isinstance(ret, bool) or ret not in {None, 0, "0"}:
                if ret in {-14, "-14"}:
                    # 会话/凭据过期（与出站侧 WECHAT_SESSION_EXPIRED_CODE 对齐）：
                    # 不能并进 platform_rejected 的 1 秒重试——那会退化成每秒
                    # 打一次平台直到人工干预。进入可见的过期状态并长退避，
                    # 等待重新扫码后 vault 换发新 token 自然恢复。
                    raise WechatPollError("wechat.poll.context_expired")
                raise WechatPollError("wechat.poll.platform_rejected")
            return value, raw
        except WechatPollError:
            raise
        except Exception as exc:
            raise WechatPollError("wechat.poll.transport_failed") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

    def notify_start(self, token: str) -> None:
        self.post(
            _START_PATH,
            {"base_info": self._base_info()},
            bot_token=token,
            timeout_seconds=10,
        )

    def get_updates(self, token: str, cursor: str, *, long_poll_timeout_ms: int) -> tuple[dict, bytes]:
        if not 1_000 <= long_poll_timeout_ms <= 50_000:
            raise ValueError("WeChat long poll timeout is invalid")
        return self.post(
            _POLL_PATH,
            {
                "get_updates_buf": cursor,
                "base_info": self._base_info(),
            },
            bot_token=token,
            timeout_seconds=long_poll_timeout_ms // 1_000 + 5,
        )

    def notify_stop(self, token: str) -> None:
        self.post(
            _STOP_PATH,
            {"base_info": self._base_info()},
            bot_token=token,
            timeout_seconds=10,
        )


class WechatProductionAdapter:
    """One account worker. A missing/expired ownership lease means zero network calls."""

    def __init__(
        self,
        registry: AdapterRegistry,
        inbox: CommunicationInbox,
        sessions: WechatSessionLedger,
        credentials: ChannelCredentialVault,
        raw_store: RawInboundStore,
        *,
        tenant_id: str,
        link_account_id: str,
        forward: Callable[..., object],
        attachment_ingestor: WechatAttachmentIngestor | None = None,
        transport: WechatIlinkPollTransport | None = None,
        long_poll_timeout_ms: int = 20_000,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not 1_000 <= long_poll_timeout_ms <= 50_000:
            raise ValueError("WeChat adapter long poll timeout is invalid")
        self._registry = registry
        self._inbox = inbox
        self._sessions = sessions
        self._credentials = credentials
        self._raw_store = raw_store
        self._tenant_id = tenant_id
        self._link_account_id = link_account_id
        self._forward = forward
        self._transport = transport or WechatIlinkPollTransport()
        self._long_poll_timeout_ms = long_poll_timeout_ms
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._processor = WechatTextInboundProcessor(
            inbox,
            sessions,
            attachment_ingestor=attachment_ingestor,
        )
        self._state_lock = threading.RLock()
        self._state = "starting"
        self._reason: str | None = None
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name=f"wechat-poller-{self._link_account_id[-12:]}",
                daemon=True,
            )
            self._thread.start()

    def health_snapshot(self, *, now_ms: int) -> AdapterHealth:
        with self._state_lock:
            state = self._state
            reason = self._reason
        return AdapterHealth(
            channel="wechat",
            tenant_id=self._tenant_id,
            link_account_id=self._link_account_id,
            state=state,
            reason_code=reason,
            observed_at_ms=now_ms,
            health_sha256="0" * 64,
        ).with_computed_sha256()

    def _set_state(self, state: str, reason: str | None = None) -> None:
        with self._state_lock:
            self._state = state
            self._reason = reason

    def _policy(self, values: Mapping[str, str]) -> WechatInboundPolicy:
        account_id = values["account_id"]
        return WechatInboundPolicy(
            tenant_id=self._tenant_id,
            link_account_id=self._link_account_id,
            account_id=account_id,
            self_user_ids=(account_id,),
            allow_direct_messages=True,
            allow_group_messages=False,
        )

    def _authority(self, *, now_ms: int):
        return self._registry.operation_authority(
            channel="wechat",
            tenant_id=self._tenant_id,
            link_account_id=self._link_account_id,
            operation="POLL",
            now_ms=now_ms,
        )

    def _flush_pending(self, *, now_ms: int) -> None:
        for pending in self._inbox.list_unacknowledged(
            channel="wechat",
            tenant_id=self._tenant_id,
            link_account_id=self._link_account_id,
        ):
            decision = self._sessions.get_decision(pending.ingress.envelope.channel_message_ref)
            if decision is None:
                # 旧版本"先 persist 后 decide"崩溃窗口留下的无决策记录：
                # 跳过并记状态，绝不 raise——那会让本账号每次轮询循环的
                # 第一步就失败，所有入站消息永久阻塞。新写入顺序（decide
                # 先于 persist）已杜绝产生新的此类记录。
                self._set_state("degraded")
                continue
            if decision.should_forward:
                acceptance = self._forward(pending.ingress.envelope, pending.permit, now_ms=now_ms)
                evidence = (
                    acceptance.model_dump(mode="json")
                    if hasattr(acceptance, "model_dump")
                    else acceptance
                )
            else:
                evidence = {
                    "classification": decision.classification,
                    "decision_sha256": decision.decision_sha256,
                    "forwarded": False,
                }
            receipt_sha = canonical_sha256(evidence)
            self._inbox.mark_acknowledged(
                pending.permit.permit_id,
                platform_receipt_sha256=receipt_sha,
                acknowledged_at_ms=now_ms,
            )

    def _run(self) -> None:
        started = False
        token = ""
        while not self._closed.is_set():
            try:
                values = self._credentials.get("wechat", self._tenant_id, self._link_account_id)
                if values is None:
                    self._set_state("missing_credentials")
                    self._closed.wait(1.0)
                    continue
                token = values["bot_token"]
                now_ms = self._clock_ms()
                self._flush_pending(now_ms=now_ms)
                if not started:
                    with self._authority(now_ms=now_ms):
                        self._transport.notify_start(token)
                    started = True
                stream_key = derive_cursor_stream_key("wechat", self._tenant_id, self._link_account_id)
                cursor_state = self._inbox.get_cursor(stream_key)
                cursor = cursor_state.cursor_token if cursor_state is not None else values["cursor"]
                # 批处理中途崩溃后 cursor state 里是本地检查点 token，平台
                # 不认识它；解码出检查点携带的外部游标，解码不了（旧格式/
                # 损坏）就退回凭据里的初始游标，绝不能把本地 token 发给平台。
                external = external_cursor_from_local(str(cursor))
                if not external:
                    external = str(values["cursor"])
                cursor = external
                previous_sha = None if cursor_state is None else cursor_state.cursor_sha256
                with self._authority(now_ms=self._clock_ms()):
                    response, raw = self._transport.get_updates(
                        token,
                        cursor,
                        long_poll_timeout_ms=self._long_poll_timeout_ms,
                    )
                raw_object = self._raw_store.put(raw)
                messages = response.get("msgs", ())
                next_cursor = response.get("get_updates_buf", cursor)
                if not isinstance(messages, list) or len(messages) > 1_000 or not isinstance(next_cursor, str):
                    raise WechatPollError("wechat.poll.batch_shape_invalid")
                if not messages:
                    self._set_state("ready")
                    continue
                if any(not isinstance(item, Mapping) for item in messages):
                    raise WechatPollError("wechat.poll.message_shape_invalid")
                captured = self._clock_ms()
                batch = self._processor.process_batch(
                    tuple(messages),
                    policy=self._policy(values),
                    poll=WechatPollRecord(
                        raw_payload_object_id=raw_object.object_id,
                        raw_payload_sha256=raw_object.sha256,
                        raw_payload_size_bytes=raw_object.size_bytes,
                        previous_cursor_sha256=previous_sha,
                        next_cursor_token=next_cursor,
                        captured_at_ms=captured,
                        persisted_at_ms=captured,
                    ),
                )
                for outcome in batch.outcomes:
                    if outcome.should_forward:
                        acceptance = self._forward(
                            outcome.envelope,
                            outcome.ack_permit,
                            now_ms=self._clock_ms(),
                        )
                        evidence = acceptance.model_dump(mode="json")
                    else:
                        evidence = {
                            "classification": outcome.decision.classification,
                            "forwarded": False,
                            "decision_sha256": outcome.decision.decision_sha256,
                        }
                    if outcome.inbox_duplicate:
                        # Re-polled duplicate: re-ACK with the receipt already
                        # recorded for this permit. A fresh acceptance changes
                        # the digest and would raise AckConflictError, looping
                        # the poller in error state forever.
                        stored_receipt = self._inbox.ack_permit_receipt(
                            outcome.ack_permit.permit_id
                        )
                        receipt_sha256 = (
                            stored_receipt
                            if stored_receipt is not None
                            else canonical_sha256(evidence)
                        )
                    else:
                        receipt_sha256 = canonical_sha256(evidence)
                    self._inbox.mark_acknowledged(
                        outcome.ack_permit.permit_id,
                        platform_receipt_sha256=receipt_sha256,
                        acknowledged_at_ms=self._clock_ms(),
                    )
                self._set_state("ready")
            except ChannelAuthorityError as exc:
                self._set_state("starting", exc.code)
                self._closed.wait(0.5)
            except (ProductionIngressError, WechatPollError) as exc:
                if str(exc) == "wechat.poll.context_expired":
                    self._set_state("credentials_expired", "wechat.poll.context_expired")
                    self._closed.wait(60.0)
                else:
                    self._set_state("degraded", str(exc))
                    self._closed.wait(1.0)
            except Exception:
                self._set_state("error", "wechat.poll.internal_error")
                self._closed.wait(1.0)
        if started and token:
            try:
                with self._authority(now_ms=self._clock_ms()):
                    self._transport.notify_stop(token)
            except Exception:
                pass
        self._set_state("closed")

    def close(self) -> None:
        self._closed.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(5.0, self._long_poll_timeout_ms / 1_000 + 7.0))
            if thread.is_alive():
                raise RuntimeError("WeChat poller did not stop within its bounded window")


__all__ = ["WechatIlinkPollTransport", "WechatPollError", "WechatProductionAdapter"]
