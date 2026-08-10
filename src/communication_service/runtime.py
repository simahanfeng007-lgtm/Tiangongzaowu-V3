"""7176 runtime containing only communication persistence and adapters."""

from __future__ import annotations

import secrets
import time
import json
import hashlib
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from contracts import (
    ChannelOwnershipLease,
    ComponentManifest,
    DeliveryReceipt,
    DeliveryTicket,
    OutboundPlan,
    TrustBundle,
)

from runtime_security import DataProtector, ephemeral_test_protector_for_scope

from . import COMPONENT_ID
from .adapters import AdapterRegistry
from .attachment_quarantine import AttachmentQuarantineLedger
from .bootstrap import CommunicationConfig, CommunicationInstanceLease
from .channel_authority import ChannelAuthorityGate
from .delivery_ledger import DeliveryLedger
from .delivery_dispatcher import DeliveryDispatchError, VerifiedDeliveryDispatcher
from .credential_vault import ChannelCredentialVault, CredentialStatus
from .feishu_route import FeishuRouteLedger
from .feishu_attachment import FeishuAttachmentIngestor
from .feishu_worker import FeishuProductionAdapter
from .feishu_outbound import (
    FeishuCredentials,
    FeishuDeliveryService,
    FeishuTokenProvider,
    HttpFeishuApiTransport,
    default_feishu_outbound_policy,
)
from .gateway_artifact_source import LoopbackGatewayArtifactSource
from .inbox import CommunicationInbox
from .production_ingress import (
    CommunicationProductionIngress,
    LoopbackProductionIngressTransport,
    ProductionIngressError,
)
from .raw_inbound_store import RawInboundStore
from .gateway_attachment_sink import LoopbackGatewayAttachmentSink
from .shadow_mirror import CommunicationShadowMirror, LoopbackShadowMirrorTransport
from .wechat_session import WechatSessionLedger
from .wechat_worker import WechatProductionAdapter
from .wechat_attachment import WechatAttachmentGate, WechatInboundAttachmentIngestor
from .wechat_media import WechatMediaDownloader
from .wechat_login import WECHAT_LOGIN_TTL_MS, WechatLoginError, WechatLoginManager
from .wechat_typing import WechatTypingManager
from .wechat_text_outbound import (
    HttpWechatIlinkTextTransport,
    WechatTextDeliveryService,
    default_wechat_text_policy,
)
from .wechat_file_outbound import HttpWechatIlinkFileTransport, WechatFileDeliveryService

if TYPE_CHECKING:
    from .feishu_inbound import FeishuInboundOutcome
    from .wechat_inbound import WechatInboundOutcome


class CommunicationRuntime:
    def __init__(
        self,
        config: CommunicationConfig,
        instance_id: str,
        lease: CommunicationInstanceLease,
        inbox: CommunicationInbox,
        deliveries: DeliveryLedger,
        wechat_sessions: WechatSessionLedger,
        attachments: AttachmentQuarantineLedger,
        feishu_routes: FeishuRouteLedger,
        adapters: AdapterRegistry,
        shadow_mirror: CommunicationShadowMirror | None,
        production_ingress: CommunicationProductionIngress | None,
        credentials: ChannelCredentialVault,
        raw_inbound: RawInboundStore,
        started_monotonic_ns: int,
    ) -> None:
        self.config = config
        self.instance_id = instance_id
        self.lease = lease
        self.inbox = inbox
        self.deliveries = deliveries
        self.wechat_sessions = wechat_sessions
        self.attachments = attachments
        self.feishu_routes = feishu_routes
        self.adapters = adapters
        self.shadow_mirror = shadow_mirror
        self.production_ingress = production_ingress
        self.credentials = credentials
        self.raw_inbound = raw_inbound
        self.wechat_login = WechatLoginManager()
        self.wechat_typing = WechatTypingManager()
        self._wechat_login_pollers: dict[str, threading.Thread] = {}
        self._wechat_login_pollers_lock = threading.RLock()
        self._workers: dict[str, object] = {}
        self._workers_lock = threading.RLock()
        self._delivery_lock = threading.RLock()
        self._delivery_dispatcher: VerifiedDeliveryDispatcher | None = None
        self._wechat_text_delivery = WechatTextDeliveryService(
            deliveries,
            wechat_sessions,
            HttpWechatIlinkTextTransport(),
        )
        self._artifact_source = (
            None
            if not config.gateway_api_token
            else LoopbackGatewayArtifactSource(
                config.total_gateway_origin,
                config.gateway_api_token,
            )
        )
        self._wechat_file_delivery = (
            None
            if self._artifact_source is None
            else WechatFileDeliveryService(
                deliveries,
                wechat_sessions,
                self._artifact_source,
                HttpWechatIlinkFileTransport(),
                staging_root=config.state_root / "staging" / "wechat-outbound",
                clock_ms=lambda: time.time_ns() // 1_000_000,
                sleeper=time.sleep,
            )
        )
        self._feishu_transport = HttpFeishuApiTransport()
        self._feishu_tokens = FeishuTokenProvider(self._feishu_transport)
        self._feishu_delivery = (
            None
            if self._artifact_source is None
            else FeishuDeliveryService(
                deliveries,
                feishu_routes,
                self._artifact_source,
                self._feishu_transport,
                self._feishu_tokens,
                staging_root=config.state_root / "staging" / "feishu-outbound",
            )
        )
        self._started_monotonic_ns = started_monotonic_ns
        self._close_lock = threading.RLock()
        self._closing = False
        self._closed = False

    @classmethod
    def start(
        cls,
        config: CommunicationConfig,
        *,
        now_ms: int | None = None,
        protector: DataProtector | None = None,
    ) -> "CommunicationRuntime":
        observed_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        if protector is None and config.environment == "test":
            protector = ephemeral_test_protector_for_scope(
                f"communication-runtime:{config.state_root.resolve()}"
            )
        lease = CommunicationInstanceLease.acquire(config.state_root)
        try:
            inbox = CommunicationInbox.open(
                config.state_root / "communication-inbox.sqlite3",
                now_ms=observed_ms,
            )
            deliveries = DeliveryLedger.open(
                config.state_root / "communication-delivery.sqlite3",
                now_ms=observed_ms,
            )
            wechat_sessions = WechatSessionLedger.open(
                config.state_root / "communication-wechat-session.sqlite3",
                now_ms=observed_ms,
                protector=protector,
            )
            attachments = AttachmentQuarantineLedger.open(
                config.state_root / "communication-attachments.sqlite3",
                now_ms=observed_ms,
            )
            feishu_routes = FeishuRouteLedger.open(
                config.state_root / "communication-feishu-route.sqlite3",
                now_ms=observed_ms,
                protector=protector,
            )
            credentials = ChannelCredentialVault.open(
                config.state_root / "communication-credentials.sqlite3",
                now_ms=observed_ms,
                protector=protector,
            )
            raw_inbound = RawInboundStore(config.state_root / "raw-inbound")
        except Exception:
            if "credentials" in locals():
                credentials.close()
            if "feishu_routes" in locals():
                feishu_routes.close()
            if "attachments" in locals():
                attachments.close()
            if "wechat_sessions" in locals():
                wechat_sessions.close()
            if "deliveries" in locals():
                deliveries.close()
            if "inbox" in locals():
                inbox.close()
            lease.release()
            raise
        instance_id = "communication-" + secrets.token_hex(16)
        shadow_mirror = (
            None
            if not config.shadow_api_token
            else CommunicationShadowMirror(
                LoopbackShadowMirrorTransport(
                    config.total_gateway_origin,
                    config.shadow_api_token,
                ),
                source_instance_id=instance_id,
            )
        )
        production_ingress = (
            None
            if not config.gateway_api_token
            else CommunicationProductionIngress(
                LoopbackProductionIngressTransport(
                    config.total_gateway_origin,
                    config.gateway_api_token,
                ),
                source_instance_id=instance_id,
            )
        )
        return cls(
            config,
            instance_id,
            lease,
            inbox,
            deliveries,
            wechat_sessions,
            attachments,
            feishu_routes,
            AdapterRegistry(),
            shadow_mirror,
            production_ingress,
            credentials,
            raw_inbound,
            time.monotonic_ns(),
        )

    def health_payload(self) -> dict[str, object]:
        uptime_ms = max(0, (time.monotonic_ns() - self._started_monotonic_ns) // 1_000_000)
        return {
            "ok": True,
            "api_contract": "tiangong.communication.api.v1",
            "component_id": COMPONENT_ID,
            "instance_id": self.instance_id,
            "status": "ALIVE",
            "uptime_ms": uptime_ms,
            "authority": "transport_only",
            "delivery_ticket_required": True,
            "legacy_business_dependencies_permitted": False,
            "total_gateway_origin": self.config.total_gateway_origin,
            "shadow_mode": "OBSERVE_ONLY" if self.shadow_mirror is not None else "DISABLED",
            "shadow_effects_permitted": False,
            "production_ingress_configured": self.production_ingress is not None,
            "production_ingress_effects_permitted": False,
            "channel_authority_bound": self.adapters.channel_authority_bound,
            "credential_vault_configured": True,
            "raw_inbound_store_configured": True,
        }

    def ready_payload(self, *, now_ms: int | None = None) -> tuple[int, dict[str, object]]:
        observed_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        inbox = self.inbox.health_check(now_ms=observed_ms)
        deliveries = self.deliveries.health_check(now_ms=observed_ms)
        wechat_sessions = self.wechat_sessions.health_check(now_ms=observed_ms)
        attachments = self.attachments.health_check(now_ms=observed_ms)
        feishu_routes = self.feishu_routes.health_check(now_ms=observed_ms)
        reasons: list[str] = []
        if not self.lease.active:
            reasons.append("communication.instance_lease.inactive")
        if not inbox.healthy:
            reasons.append(inbox.reason_code)
        if not deliveries.healthy:
            reasons.append(deliveries.reason_code)
        if not wechat_sessions.healthy:
            reasons.append(wechat_sessions.reason_code)
        if not attachments.healthy:
            reasons.append(attachments.reason_code)
        if not feishu_routes.healthy:
            reasons.append(feishu_routes.reason_code)
        try:
            self.credentials.health_check()
        except Exception:
            reasons.append("communication.credential_vault.invalid")
        try:
            adapters = self.adapters.snapshots(now_ms=observed_ms)
        except Exception:
            adapters = {}
            reasons.append("communication.adapter_registry.invalid")
        ready = not reasons
        status = 200 if ready else 503
        return status, {
            "ok": ready,
            "api_contract": "tiangong.communication.api.v1",
            "component_id": COMPONENT_ID,
            "instance_id": self.instance_id,
            "status": "READY" if ready else "NOT_READY",
            "http_status": status,
            "reason_codes": sorted(set(reasons)),
            "inbox": {
                "healthy": inbox.healthy,
                "reason_code": inbox.reason_code,
                "schema_sha256": inbox.schema_sha256,
            },
            "deliveries": {
                "healthy": deliveries.healthy,
                "reason_code": deliveries.reason_code,
                "schema_sha256": deliveries.schema_sha256,
            },
            "wechat_sessions": {
                "healthy": wechat_sessions.healthy,
                "reason_code": wechat_sessions.reason_code,
                "schema_sha256": wechat_sessions.schema_sha256,
            },
            "attachments": {
                "healthy": attachments.healthy,
                "reason_code": attachments.reason_code,
                "schema_sha256": attachments.schema_sha256,
            },
            "feishu_routes": {
                "healthy": feishu_routes.healthy,
                "reason_code": feishu_routes.reason_code,
                "schema_sha256": feishu_routes.schema_sha256,
            },
            "adapter_count": len(adapters),
            "delivery_ticket_required": True,
            "legacy_business_dependencies_permitted": False,
            "shadow_mode": "OBSERVE_ONLY" if self.shadow_mirror is not None else "DISABLED",
            "shadow_effects_permitted": False,
            "production_ingress_configured": self.production_ingress is not None,
            "production_ingress_effects_permitted": False,
            "channel_authority_bound": self.adapters.channel_authority_bound,
            "credential_count": len(self.credentials.list_statuses()),
            "raw_inbound_store_configured": True,
        }

    def _forward_persisted_inbound(
        self,
        envelope: object,
        ack_permit: object,
        *,
        now_ms: int,
    ):
        from contracts import ChannelAckPermit, InboundEnvelope

        if self.production_ingress is None:
            raise ProductionIngressError("production ingress is not configured")
        if not isinstance(envelope, InboundEnvelope) or not isinstance(
            ack_permit, ChannelAckPermit
        ):
            raise TypeError("production ingress requires shared envelope and ACK contracts")
        if envelope.channel == "wechat":
            try:
                decision = self.wechat_sessions.get_decision(envelope.channel_message_ref)
                if decision is not None and decision.should_forward:
                    recipient = self.wechat_sessions.resolve_reply_target(
                        session_key=decision.session_key,
                        account_id=envelope.link_account_id,
                        conversation_scope_hash=envelope.conversation_scope_hash,
                    )
                    context_token = self.wechat_sessions.resolve_context_token(
                        session_key=decision.session_key,
                        account_id=envelope.link_account_id,
                        conversation_scope_hash=envelope.conversation_scope_hash,
                    )
                    values = self.credentials.get(
                        "wechat",
                        envelope.tenant_id,
                        envelope.link_account_id,
                    )
                    if values and recipient:
                        self.wechat_typing.start(
                            bot_token=values["bot_token"],
                            ilink_user_id=recipient,
                            session_key=decision.session_key,
                            context_token=context_token or "",
                            to_user_id=recipient,
                        )
            except Exception:
                # Typing feedback is best-effort and must never block inbound.
                pass
        with self.adapters.operation_authority(
            channel=envelope.channel,
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            operation="POLL",
            now_ms=now_ms,
        ) as lease:
            return self.production_ingress.forward(
                envelope,
                ack_permit,
                lease,
                submitted_at_ms=now_ms,
            )

    def forward_wechat_outcome(
        self,
        outcome: WechatInboundOutcome,
        *,
        now_ms: int,
    ):
        if not outcome.should_forward:
            raise ProductionIngressError("suppressed WeChat ingress cannot be forwarded")
        return self._forward_persisted_inbound(
            outcome.envelope,
            outcome.ack_permit,
            now_ms=now_ms,
        )

    def forward_feishu_outcome(
        self,
        outcome: FeishuInboundOutcome,
        *,
        now_ms: int,
    ):
        if not outcome.should_forward:
            raise ProductionIngressError("suppressed Feishu ingress cannot be forwarded")
        return self._forward_persisted_inbound(
            outcome.envelope,
            outcome.ack_permit,
            now_ms=now_ms,
        )

    def bind_channel_authority(self, channel_authority: ChannelAuthorityGate) -> None:
        if channel_authority.owner_instance_id != self.instance_id:
            raise ValueError("channel authority is not bound to this communication instance")
        self.adapters.bind_channel_authority(channel_authority)

    def ensure_channel_authority(self, lease: ChannelOwnershipLease) -> None:
        if not self.adapters.channel_authority_bound:
            self.bind_channel_authority(
                ChannelAuthorityGate(
                    owner_instance_id=self.instance_id,
                    expected_gateway_epoch=lease.gateway_epoch,
                    expected_component_manifest_sha256=lease.component_manifest_sha256,
                )
            )

    def install_channel_lease(
        self,
        lease: ChannelOwnershipLease,
        *,
        now_ms: int,
    ) -> bool:
        if lease.owner_instance_id != self.instance_id:
            raise ValueError("channel lease is not issued to this communication instance")
        self.ensure_channel_authority(lease)
        return self.adapters.install_channel_lease(lease, now_ms=now_ms)

    class _WechatDeliveryHandler:
        def __init__(self, runtime: "CommunicationRuntime") -> None:
            self._runtime = runtime

        def send(self, payload, plan) -> DeliveryReceipt:
            if plan.reply_to_message_ref is None:
                raise DeliveryDispatchError("delivery.wechat.reply_target_required")
            decision = self._runtime.wechat_sessions.get_decision(plan.reply_to_message_ref)
            if decision is None or not decision.should_forward:
                raise DeliveryDispatchError("delivery.wechat.session_not_found")
            values = self._runtime.credentials.get(
                "wechat", payload.tenant_id, payload.link_account_id
            )
            if values is None:
                raise DeliveryDispatchError("delivery.wechat.credentials_missing")
            try:
                policy = default_wechat_text_policy()
                if any(part.kind == "artifact" for part in plan.parts):
                    if any(part.kind != "artifact" for part in plan.parts):
                        raise DeliveryDispatchError("delivery.wechat.mixed_plan_unsupported")
                    service = self._runtime._wechat_file_delivery
                    if service is None:
                        raise DeliveryDispatchError("delivery.wechat.artifact_source_unconfigured")
                    return service.send(
                        payload,
                        plan,
                        policy=policy,
                        bot_token=values["bot_token"],
                        ilink_account_id=values["account_id"],
                        session_key=decision.session_key,
                    )
                return self._runtime._wechat_text_delivery.send(
                    payload,
                    plan,
                    policy=policy,
                    bot_token=values["bot_token"],
                    ilink_account_id=values["account_id"],
                    session_key=decision.session_key,
                )
            finally:
                try:
                    self._runtime.wechat_typing.stop(decision.session_key)
                except Exception:
                    pass

    class _FeishuDeliveryHandler:
        def __init__(self, runtime: "CommunicationRuntime") -> None:
            self._runtime = runtime

        def send(self, payload, plan) -> DeliveryReceipt:
            values = self._runtime.credentials.get(
                "feishu", payload.tenant_id, payload.link_account_id
            )
            if values is None:
                raise DeliveryDispatchError("delivery.feishu.credentials_missing")
            service = self._runtime._feishu_delivery
            if service is None:
                raise DeliveryDispatchError("delivery.feishu.artifact_source_unconfigured")
            return service.send(
                payload,
                plan,
                policy=default_feishu_outbound_policy(),
                credentials=FeishuCredentials(
                    app_id=values["app_id"],
                    app_secret=values["app_secret"],
                ),
            )

    def install_delivery_authority(
        self,
        trust_bundle: TrustBundle,
        component_manifest: ComponentManifest,
    ) -> None:
        gate = self.adapters.channel_authority
        if gate is None:
            raise ValueError("channel authority must be installed before delivery authority")
        dispatcher = VerifiedDeliveryDispatcher(
            self.deliveries,
            trust_bundle,
            component_manifest,
            {
                "wechat": self._WechatDeliveryHandler(self),
                "feishu": self._FeishuDeliveryHandler(self),
            },
            clock_ms=lambda: time.time_ns() // 1_000_000,
            generation_floor=lambda _request_id, _run_id: 0,
            channel_authority=gate,
        )
        with self._delivery_lock:
            self._delivery_dispatcher = dispatcher

    def dispatch_delivery(
        self,
        ticket: DeliveryTicket,
        plan: OutboundPlan,
    ) -> DeliveryReceipt:
        with self._delivery_lock:
            dispatcher = self._delivery_dispatcher
        if dispatcher is None:
            raise DeliveryDispatchError("delivery.dispatcher.unconfigured")
        return dispatcher.dispatch(ticket, plan)

    def credential_status_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "credentials": [
                {
                    "channel": item.channel,
                    "tenant_id": item.tenant_id,
                    "link_account_id": item.link_account_id,
                    "revision": item.revision,
                    "configured": item.configured,
                    "updated_at_ms": item.updated_at_ms,
                    "public_metadata": item.public_metadata,
                    "evidence_sha256": item.evidence_sha256,
                }
                for item in self.credentials.list_statuses()
            ],
        }

    def install_credentials(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        values: dict[str, str],
        now_ms: int,
    ) -> CredentialStatus:
        return self.credentials.put(
            channel,
            tenant_id,
            link_account_id,
            values,
            updated_at_ms=now_ms,
            source="control_plane",
        )

    def migrate_legacy_credentials(self, *, now_ms: int) -> tuple[CredentialStatus, ...]:
        legacy_path = Path.home() / ".tiangong" / "v3" / "gateway_links.json"
        # A clean installation intentionally has no legacy file.  Absence is
        # not a startup error and must not prevent the gateway from discovering
        # credentials created by the current QR/settings flow.
        if not legacy_path.exists():
            return ()
        if legacy_path.is_symlink() or not legacy_path.is_file() or legacy_path.stat().st_size > 1_048_576:
            raise ValueError("legacy credential source is missing or unsafe")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("legacy credential source contains duplicate keys")
                result[key] = value
            return result

        document = json.loads(
            legacy_path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(document, dict):
            raise ValueError("legacy credential source is invalid")
        statuses: list[CredentialStatus] = []
        wechat = document.get("wechat", {})
        direct = wechat.get("direct", {}) if isinstance(wechat, dict) else {}
        if isinstance(direct, dict):
            account_id = str(direct.get("account_id") or "").strip()
            values = {
                "account_id": account_id,
                "bot_token": str(direct.get("bot_token") or "").strip(),
                "cursor": str(direct.get("get_updates_buf") or "").strip(),
                "user_id": str(direct.get("user_id") or "").strip(),
            }
            if account_id and values["bot_token"] and values["user_id"]:
                statuses.append(
                    self.credentials.put(
                        "wechat",
                        "wechat",
                        account_id,
                        values,
                        updated_at_ms=now_ms,
                        source="legacy_migration",
                    )
                )
        feishu = document.get("feishu", {})
        if isinstance(feishu, dict):
            app_id = str(feishu.get("app_id") or "").strip()
            app_secret = str(feishu.get("app_secret") or "").strip()
            if app_id and app_secret:
                link_account_id = "feishu_" + hashlib.sha256(app_id.encode()).hexdigest()[:32]
                statuses.append(
                    self.credentials.put(
                        "feishu",
                        "feishu",
                        link_account_id,
                        {
                            "app_id": app_id,
                            "app_secret": app_secret,
                            "bot_open_id": str(feishu.get("bot_open_id") or "").strip(),
                            "encrypt_key": str(feishu.get("encrypt_key") or "").strip(),
                            "platform_tenant_key": str(
                                feishu.get("platform_tenant_key") or ""
                            ).strip(),
                            "verification_token": str(
                                feishu.get("verification_token") or ""
                            ).strip(),
                        },
                        updated_at_ms=now_ms,
                        source="legacy_migration",
                    )
                )
        return tuple(statuses)

    def start_wechat_adapter(
        self,
        *,
        tenant_id: str,
        link_account_id: str,
        now_ms: int,
    ) -> bool:
        key = f"wechat:{tenant_id}:{link_account_id}"
        with self._workers_lock:
            if key in self._workers:
                return False
            if self.credentials.get("wechat", tenant_id, link_account_id) is None:
                raise ValueError("WeChat credentials are not configured")
            adapter = WechatProductionAdapter(
                self.adapters,
                self.inbox,
                self.wechat_sessions,
                self.credentials,
                self.raw_inbound,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
                forward=lambda envelope, permit, *, now_ms: self._forward_persisted_inbound(
                    envelope, permit, now_ms=now_ms
                ),
                attachment_ingestor=WechatInboundAttachmentIngestor(
                    WechatMediaDownloader(self.config.state_root / "staging" / "wechat"),
                    WechatAttachmentGate(
                        self.config.state_root / "staging" / "wechat",
                        LoopbackGatewayAttachmentSink(
                            self.config.total_gateway_origin,
                            self.config.gateway_api_token,
                        ),
                        self.attachments,
                    ),
                ),
            )
            self.adapters.register(adapter, now_ms=now_ms)
            self._workers[key] = adapter
        adapter.start()
        return True

    def start_feishu_adapter(
        self,
        *,
        tenant_id: str,
        link_account_id: str,
        now_ms: int,
    ) -> bool:
        key = f"feishu:{tenant_id}:{link_account_id}"
        with self._workers_lock:
            if key in self._workers:
                return False
            if self.credentials.get("feishu", tenant_id, link_account_id) is None:
                raise ValueError("Feishu credentials are not configured")
            staging_root = self.config.state_root / "staging" / "feishu"
            adapter = FeishuProductionAdapter(
                self.adapters,
                self.inbox,
                self.feishu_routes,
                self.credentials,
                self.raw_inbound,
                FeishuAttachmentIngestor(
                    staging_root,
                    self.feishu_routes,
                    WechatAttachmentGate(
                        staging_root,
                        LoopbackGatewayAttachmentSink(
                            self.config.total_gateway_origin,
                            self.config.gateway_api_token,
                        ),
                        self.attachments,
                    ),
                ),
                tenant_id=tenant_id,
                link_account_id=link_account_id,
                forward=lambda envelope, permit, *, now_ms: self._forward_persisted_inbound(
                    envelope, permit, now_ms=now_ms
                ),
            )
            self.adapters.register(adapter, now_ms=now_ms)
            self._workers[key] = adapter
        adapter.start()
        return True

    def _latest_wechat_credentials(self) -> tuple[CredentialStatus | None, dict[str, str] | None]:
        statuses = [item for item in self.credentials.list_statuses() if item.channel == "wechat"]
        if not statuses:
            return None, None
        status = max(statuses, key=lambda item: (item.updated_at_ms, item.link_account_id))
        return status, self.credentials.get("wechat", status.tenant_id, status.link_account_id)

    @staticmethod
    def _wechat_login_failure(exc: WechatLoginError, message: str) -> dict[str, object]:
        return {
            "ok": False,
            "connected": False,
            "error": exc.code,
            "message": message,
        }

    def wechat_login_start(self, payload: dict[str, object], *, now_ms: int) -> dict[str, object]:
        _status, existing = self._latest_wechat_credentials()
        local_tokens = () if existing is None else (existing["bot_token"],)
        try:
            outcome = self.wechat_login.start(
                payload,
                now_ms=now_ms,
                local_tokens=local_tokens,
            )
        except WechatLoginError as exc:
            return self._wechat_login_failure(exc, "生成微信登录二维码失败，请稍后重试。")
        public = outcome.public
        session_key = str(public.get("session_key") or "").strip()
        if session_key:
            self._start_wechat_login_autopoll(session_key, now_ms)
        return public

    def _start_wechat_login_autopoll(self, session_key: str, started_ms: int) -> None:
        """Poll iLink scan/confirm state in the background after QR generation.

        The phone-side flow is scan -> confirm; once the gateway observes
        ``confirmed`` the login wait installs credentials and starts the
        adapter, so the desktop user does not need extra manual steps.
        """
        with self._wechat_login_pollers_lock:
            if session_key in self._wechat_login_pollers:
                return
            thread = threading.Thread(
                target=self._wechat_login_autopoll_worker,
                args=(session_key, int(started_ms)),
                name=f"wechat-login-autopoll-{session_key[:8]}",
                daemon=True,
            )
            self._wechat_login_pollers[session_key] = thread
            thread.start()

    def _wechat_login_autopoll_worker(self, session_key: str, started_ms: int) -> None:
        deadline_ms = int(started_ms) + WECHAT_LOGIN_TTL_MS
        try:
            while not self._closed and not self._closing:
                now_ms = time.time_ns() // 1_000_000
                if now_ms >= deadline_ms:
                    break
                try:
                    result = self.wechat_login_wait({"session_key": session_key}, now_ms=now_ms)
                except Exception:
                    break
                if result.get("connected") or result.get("need_verify_code") or result.get("error"):
                    break
                time.sleep(2)
        finally:
            with self._wechat_login_pollers_lock:
                self._wechat_login_pollers.pop(session_key, None)

    def wechat_login_wait(self, payload: dict[str, object], *, now_ms: int) -> dict[str, object]:
        _status, existing = self._latest_wechat_credentials()
        try:
            outcome = self.wechat_login.wait(
                payload,
                now_ms=now_ms,
                existing_credentials=existing,
            )
        except WechatLoginError as exc:
            return self._wechat_login_failure(exc, "确认微信登录状态失败，请稍后重试。")
        if outcome.credentials is None:
            return outcome.public
        account_id = outcome.credentials["account_id"]
        self.install_credentials(
            channel="wechat",
            tenant_id="wechat",
            link_account_id=account_id,
            values=outcome.credentials,
            now_ms=now_ms,
        )
        started = False
        if self.config.gateway_api_token and self.adapters.channel_authority_bound:
            started = self.start_wechat_adapter(
                tenant_id="wechat",
                link_account_id=account_id,
                now_ms=now_ms,
            )
        self.wechat_login.mark_configured(account_id, running=started)
        result = dict(outcome.public)
        result["credentials_saved"] = True
        result["adapter_started"] = started
        if not started:
            result["message"] = "微信已连接，凭据已安全保存，等待总网关授权启动。"
        return result

    def wechat_adapter_start(self, payload: dict[str, object], *, now_ms: int) -> dict[str, object]:
        if payload:
            raise ValueError("communication.control.fields_invalid")
        status, _values = self._latest_wechat_credentials()
        if status is None:
            return {
                "ok": False,
                "error": "missing_credentials",
                "message": "尚未完成微信登录，请先生成二维码并扫码。",
            }
        if not self.config.gateway_api_token or not self.adapters.channel_authority_bound:
            self.wechat_login.mark_configured(status.link_account_id, running=False)
            return {
                "ok": True,
                "started": False,
                "account_id": status.link_account_id,
                "message": "微信凭据已就绪，等待总网关授权启动。",
            }
        started = self.start_wechat_adapter(
            tenant_id=status.tenant_id,
            link_account_id=status.link_account_id,
            now_ms=now_ms,
        )
        self.wechat_login.mark_configured(status.link_account_id, running=True)
        return {
            "ok": True,
            "started": started,
            "account_id": status.link_account_id,
            "message": "微信连接已启动。" if started else "微信连接已经在运行。",
        }

    def wechat_adapter_stop(self, payload: dict[str, object], *, now_ms: int) -> dict[str, object]:
        if payload:
            raise ValueError("communication.control.fields_invalid")
        status, _values = self._latest_wechat_credentials()
        if status is None:
            return {"ok": True, "stopped": False, "message": "微信连接尚未配置。"}
        key = f"wechat:{status.tenant_id}:{status.link_account_id}"
        stopped = self.adapters.unregister(
            channel="wechat",
            tenant_id=status.tenant_id,
            link_account_id=status.link_account_id,
        )
        with self._workers_lock:
            self._workers.pop(key, None)
        self.wechat_login.mark_configured(status.link_account_id, running=False)
        return {
            "ok": True,
            "stopped": stopped,
            "account_id": status.link_account_id,
            "message": "微信连接已停止。" if stopped else "微信连接当前未运行。",
        }

    def links_status_payload(self, *, now_ms: int | None = None) -> dict[str, object]:
        observed_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        adapter_links = {
            key: health.model_dump(mode="json")
            for key, health in self.adapters.snapshots(now_ms=observed_ms).items()
        }
        wechat_direct = self.wechat_login.snapshot(now_ms=observed_ms)
        wechat_adapters = [
            value for value in adapter_links.values() if value.get("channel") == "wechat"
        ]
        pending_login = wechat_direct.get("state") in {
            "waiting_login",
            "waiting_confirm",
            "need_verifycode",
        } and bool(wechat_direct.get("session_key"))
        # A newly generated QR session is the active user control flow.  An old
        # configured adapter may still be present while the user rebinds the
        # account; replacing the session snapshot with that adapter health made
        # the QR render for one frame and disappear on the immediate status
        # refresh.
        if wechat_adapters and not pending_login:
            wechat_direct = dict(wechat_adapters[-1])
        else:
            status, _values = self._latest_wechat_credentials()
            if status is not None and wechat_direct.get("state") == "missing_credentials":
                wechat_direct = {
                    "state": "available",
                    "account_id": status.link_account_id,
                    "configured": True,
                }
        links: dict[str, object] = dict(adapter_links)
        links["wechat_direct"] = wechat_direct
        return {
            "ok": True,
            "api_contract": "tiangong.communication.api.v1",
            "authority": "tiangong-total-gateway",
            "settings": {},
            "links": links,
        }

    def channel_drain_facts_payload(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
    ) -> dict[str, object]:
        inbox = self.inbox.channel_drain_facts(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
        )
        deliveries = self.deliveries.channel_drain_facts(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
        )
        gate = self.adapters.channel_authority
        poll_inflight = send_inflight = 0
        if gate is not None:
            poll_inflight, send_inflight = gate.inflight_counts(
                channel=channel,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
            )
        return {
            "channel": channel,
            "tenant_id": tenant_id,
            "link_account_id": link_account_id,
            "poll_inflight": poll_inflight,
            "send_inflight": max(send_inflight, deliveries.inflight_send_count),
            "unacknowledged_inbox_count": inbox.unacknowledged_count,
            "unresolved_delivery_count": deliveries.unresolved_delivery_count,
            "inbox_ledger_sha256": inbox.ledger_sha256,
            "delivery_ledger_sha256": deliveries.ledger_sha256,
            "last_cursor_sha256": inbox.last_cursor_sha256,
        }

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closing = True

        with self._wechat_login_pollers_lock:
            self._wechat_login_pollers.clear()
        self.wechat_typing.close()

        # Transport workers must stop before their ledgers and credentials.
        # Failed adapters remain registered by AdapterRegistry.close(), so the
        # operation can be retried without losing ownership evidence.
        try:
            self.adapters.close()
        except Exception as exc:
            raise RuntimeError("communication adapters failed to close") from exc
        with self._workers_lock:
            self._workers.clear()

        resource_errors: list[Exception] = []
        for resource in (
            self.credentials,
            self.feishu_routes,
            self.attachments,
            self.wechat_sessions,
            self.deliveries,
            self.inbox,
        ):
            try:
                resource.close()
            except Exception as exc:
                resource_errors.append(exc)
        if resource_errors:
            # Retain the single-instance lease until every mutable ledger is
            # proven closed.  Some peers may already be closed; their close
            # methods are required to be idempotent for the retry.
            raise RuntimeError("communication resources failed to close") from resource_errors[0]
        try:
            self.lease.release()
        except Exception as exc:
            raise RuntimeError("communication lease failed to release") from exc
        with self._close_lock:
            self._closed = True
            self._closing = False

    def __enter__(self) -> "CommunicationRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["CommunicationRuntime"]
