"""Lease-gated Feishu/Lark long-connection production adapter."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from contracts import canonical_json_bytes, canonical_sha256

from .adapters import AdapterHealth, AdapterRegistry
from .channel_authority import ChannelAuthorityError
from .credential_vault import ChannelCredentialVault
from .feishu_attachment import FeishuAttachmentIngestor
from .feishu_inbound import (
    FeishuEventRecord,
    FeishuInboundPolicy,
    FeishuInboundProcessor,
)
from .feishu_outbound import (
    FeishuCredentials,
    FeishuTokenProvider,
    HttpFeishuApiTransport,
)
from .feishu_route import FeishuRouteLedger
from .inbox import CommunicationInbox
from .production_ingress import ProductionIngressError
from .raw_inbound_store import RawInboundStore


class FeishuWorkerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FeishuLongConnectionTransport(Protocol):
    def run_once(
        self,
        *,
        app_id: str,
        app_secret: str,
        encrypt_key: str,
        verification_token: str,
        on_event: Callable[[bytes], None],
        should_continue: Callable[[], bool],
    ) -> None: ...


class LarkSdkLongConnectionTransport:
    """Pinned official SDK transport with a bounded, lease-aware lifecycle."""

    def run_once(
        self,
        *,
        app_id: str,
        app_secret: str,
        encrypt_key: str,
        verification_token: str,
        on_event: Callable[[bytes], None],
        should_continue: Callable[[], bool],
    ) -> None:
        if importlib.metadata.version("lark-oapi") != "1.7.1":
            raise FeishuWorkerError("feishu.worker.sdk_version_mismatch")
        import lark_oapi as lark
        import lark_oapi.ws.client as ws_client

        local = threading.local()

        def callback(_event: object) -> None:
            raw = getattr(local, "raw", None)
            if not isinstance(raw, bytes):
                raise FeishuWorkerError("feishu.worker.raw_event_missing")
            on_event(raw)

        inner = (
            lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
            .register_p2_im_message_receive_v1(callback)
            .build()
        )

        class CapturingDispatcher:
            def _do_without_validation(self, raw: bytes):
                if not isinstance(raw, bytes) or not raw:
                    raise FeishuWorkerError("feishu.worker.raw_event_invalid")
                local.raw = bytes(raw)
                try:
                    return inner._do_without_validation(raw)
                finally:
                    local.raw = None

        client = lark.ws.Client(
            app_id,
            app_secret,
            log_level=lark.LogLevel.ERROR,
            event_handler=CapturingDispatcher(),
            auto_reconnect=False,
            source="tiangong-v3-qiyuan",
        )
        loop = ws_client.loop
        try:
            loop.run_until_complete(client._connect())
            if client._conn is None:
                raise FeishuWorkerError("feishu.worker.connect_failed")
            while should_continue() and client._conn is not None:
                loop.run_until_complete(asyncio.sleep(0.25))
        finally:
            try:
                loop.run_until_complete(client._disconnect())
            except Exception:
                pass


def _strict_event(raw: bytes) -> tuple[dict[str, Any], bytes]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise FeishuWorkerError("feishu.worker.event_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict):
            raise ValueError("event is not an object")
        canonical = canonical_json_bytes(value)
    except FeishuWorkerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise FeishuWorkerError("feishu.worker.event_json_invalid") from exc
    return value, canonical


class FeishuProductionAdapter:
    def __init__(
        self,
        registry: AdapterRegistry,
        inbox: CommunicationInbox,
        routes: FeishuRouteLedger,
        credentials: ChannelCredentialVault,
        raw_store: RawInboundStore,
        attachment_ingestor: FeishuAttachmentIngestor,
        *,
        tenant_id: str,
        link_account_id: str,
        forward: Callable[..., object],
        transport: FeishuLongConnectionTransport | None = None,
        api_transport: HttpFeishuApiTransport | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._registry = registry
        self._inbox = inbox
        self._routes = routes
        self._credentials = credentials
        self._raw_store = raw_store
        self._attachment_ingestor = attachment_ingestor
        self._tenant_id = tenant_id
        self._link_account_id = link_account_id
        self._forward = forward
        self._transport = transport or LarkSdkLongConnectionTransport()
        self._api_transport = api_transport or HttpFeishuApiTransport()
        self._tokens = FeishuTokenProvider(self._api_transport)
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._state_lock = threading.RLock()
        self._state = "starting"
        self._reason: str | None = None
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_values: Mapping[str, str] | None = None
        self._processor = FeishuInboundProcessor(
            inbox,
            routes,
            attachment_loader=self._load_attachment,
        )

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name=f"feishu-long-connection-{self._link_account_id[-12:]}",
                daemon=True,
            )
            self._thread.start()

    def _set_state(self, state: str, reason: str | None = None) -> None:
        with self._state_lock:
            self._state = state
            self._reason = reason

    def health_snapshot(self, *, now_ms: int) -> AdapterHealth:
        with self._state_lock:
            state = self._state
            reason = self._reason
        return AdapterHealth(
            channel="feishu",
            tenant_id=self._tenant_id,
            link_account_id=self._link_account_id,
            state=state,
            reason_code=reason,
            observed_at_ms=now_ms,
            health_sha256="0" * 64,
        ).with_computed_sha256()

    def _policy(self, values: Mapping[str, str]) -> FeishuInboundPolicy:
        return FeishuInboundPolicy(
            tenant_id=self._tenant_id,
            link_account_id=self._link_account_id,
            app_id=values["app_id"],
            platform_tenant_key=values["platform_tenant_key"],
            bot_open_ids=(values["bot_open_id"],),
            allow_p2p=True,
            allow_groups=False,
        )

    def _load_attachment(self, resource_id: str, **scope: str):
        values = self._active_values
        if values is None:
            raise FeishuWorkerError("feishu.worker.credentials_not_active")
        token = self._tokens.get_token(
            f"{self._tenant_id}:{self._link_account_id}",
            FeishuCredentials(values["app_id"], values["app_secret"]),
            timeout_seconds=30,
        )
        return self._attachment_ingestor.ingest(
            resource_id,
            tenant_id=scope["tenant_id"],
            link_account_id=scope["link_account_id"],
            conversation_scope_hash=scope["conversation_scope_hash"],
            access_token=token,
        )

    def _flush_pending(self, *, now_ms: int) -> None:
        for pending in self._inbox.list_unacknowledged(
            channel="feishu",
            tenant_id=self._tenant_id,
            link_account_id=self._link_account_id,
        ):
            envelope = pending.ingress.envelope
            if envelope.text.startswith("[communication event withheld:"):
                evidence: object = {"forwarded": False, "reason": "persisted_withheld"}
            else:
                acceptance = self._forward(envelope, pending.permit, now_ms=now_ms)
                evidence = (
                    acceptance.model_dump(mode="json")
                    if hasattr(acceptance, "model_dump")
                    else acceptance
                )
            self._inbox.mark_acknowledged(
                pending.permit.permit_id,
                platform_receipt_sha256=canonical_sha256(evidence),
                acknowledged_at_ms=now_ms,
            )

    def _handle_event(self, raw: bytes) -> None:
        now_ms = self._clock_ms()
        values = self._active_values
        if values is None:
            raise FeishuWorkerError("feishu.worker.credentials_not_active")
        event, canonical = _strict_event(raw)
        raw_object = self._raw_store.put(canonical)
        header = event.get("header")
        app_verified = isinstance(header, Mapping) and header.get("app_id") == values["app_id"]
        outcome = self._processor.process(
            event,
            policy=self._policy(values),
            record=FeishuEventRecord(
                raw_payload_object_id=raw_object.object_id,
                raw_payload_sha256=raw_object.sha256,
                raw_payload_size_bytes=raw_object.size_bytes,
                signature_verified=True,
                app_id_verified=app_verified,
                captured_at_ms=now_ms,
                persisted_at_ms=now_ms,
            ),
        )
        if outcome.should_forward:
            acceptance = self._forward(
                outcome.envelope,
                outcome.ack_permit,
                now_ms=self._clock_ms(),
            )
            evidence: object = (
                acceptance.model_dump(mode="json")
                if hasattr(acceptance, "model_dump")
                else acceptance
            )
        else:
            evidence = {
                "classification": outcome.classification,
                "forwarded": False,
            }
        self._inbox.mark_acknowledged(
            outcome.ack_permit.permit_id,
            platform_receipt_sha256=canonical_sha256(evidence),
            acknowledged_at_ms=self._clock_ms(),
        )

    def _continue(self) -> bool:
        if self._closed.is_set():
            return False
        try:
            self._registry.authorize_operation(
                channel="feishu",
                tenant_id=self._tenant_id,
                link_account_id=self._link_account_id,
                operation="POLL",
                now_ms=self._clock_ms(),
            )
            return True
        except ChannelAuthorityError:
            return False

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                values = self._credentials.get(
                    "feishu", self._tenant_id, self._link_account_id
                )
                if values is None:
                    self._set_state("missing_credentials")
                    self._closed.wait(1.0)
                    continue
                self._active_values = values
                with self._registry.operation_authority(
                    channel="feishu",
                    tenant_id=self._tenant_id,
                    link_account_id=self._link_account_id,
                    operation="POLL",
                    now_ms=self._clock_ms(),
                ):
                    self._flush_pending(now_ms=self._clock_ms())
                    self._set_state("ready")
                    self._transport.run_once(
                        app_id=values["app_id"],
                        app_secret=values["app_secret"],
                        encrypt_key=values["encrypt_key"],
                        verification_token=values["verification_token"],
                        on_event=self._handle_event,
                        should_continue=self._continue,
                    )
                if not self._closed.is_set():
                    self._set_state("starting", "feishu.worker.lease_or_connection_ended")
                    self._closed.wait(0.5)
            except ChannelAuthorityError as exc:
                self._set_state("starting", exc.code)
                self._closed.wait(0.5)
            except (FeishuWorkerError, ProductionIngressError) as exc:
                self._set_state("degraded", str(exc))
                self._closed.wait(1.0)
            except Exception:
                self._set_state("error", "feishu.worker.internal_error")
                self._closed.wait(1.0)
            finally:
                self._active_values = None
        self._set_state("closed")

    def close(self) -> None:
        self._closed.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10.0)
            if thread.is_alive():
                raise RuntimeError("Feishu long-connection worker did not stop")


__all__ = [
    "FeishuLongConnectionTransport",
    "FeishuProductionAdapter",
    "FeishuWorkerError",
    "LarkSdkLongConnectionTransport",
]
