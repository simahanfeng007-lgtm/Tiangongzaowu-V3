"""In-process host for the transport-only communication subsystem."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from contracts import (
    ChannelOwnershipLease,
    ComponentManifest,
    DeliveryTicket,
    OutboundPlan,
    TrustBundle,
    canonical_json_bytes,
)

from .bootstrap import CommunicationConfig
from runtime_security import ephemeral_test_protector_for_scope
from .runtime import CommunicationRuntime


EMBEDDED_COMMUNICATION_BUILD_ID = "tiangong-v3.0.3-embedded-communication-source-20260722"


class EmbeddedCommunicationService:
    def __init__(self, runtime: CommunicationRuntime, *, mode: str = "embedded") -> None:
        self.runtime = runtime
        self.mode = mode
        self._closed = False

    @classmethod
    def start(
        cls,
        *,
        gateway_state_root: Path,
        gateway_environment: str,
        gateway_token: str,
        shadow_token: str,
        mode: str = "embedded",
    ) -> "EmbeddedCommunicationService":
        environment = gateway_environment if gateway_environment in {"production", "development", "test"} else "development"
        # The state/security implementation remains exactly the communication
        # module's own implementation; only its HTTP listener is removed.
        config = CommunicationConfig(
            environment=environment,
            port=7176,
            state_root=(gateway_state_root.parent / "communication").resolve(strict=False),
            total_gateway_origin="http://127.0.0.1:7184",
            shadow_api_token=shadow_token,
            gateway_api_token=gateway_token,
        )
        protector = None
        if os.name != "nt" and environment != "production":
            protector = ephemeral_test_protector_for_scope(
                f"communication:{config.state_root}"
            )
        return cls(CommunicationRuntime.start(config, protector=protector), mode=mode)

    def health_payload(self) -> dict[str, Any]:
        payload = dict(self.runtime.health_payload())
        payload["deployment_mode"] = self.mode
        payload["listener_port"] = None
        payload["build_id"] = EMBEDDED_COMMUNICATION_BUILD_ID
        return payload

    def ready_payload(self, *, now_ms: int | None = None) -> tuple[int, dict[str, Any]]:
        status, payload = self.runtime.ready_payload(now_ms=now_ms)
        value = dict(payload)
        value["deployment_mode"] = self.mode
        value["listener_port"] = None
        value["build_id"] = EMBEDDED_COMMUNICATION_BUILD_ID
        return status, value

    def request(
        self,
        method: str,
        target: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> tuple[int, dict[str, Any], str]:
        del timeout_seconds
        verb = str(method).upper()
        path = urlsplit(target).path
        body = dict(payload or {})
        now_ms = time.time_ns() // 1_000_000
        try:
            if self._closed:
                return 503, {"ok": False, "reason_code": "communication.embedded.closed"}, "application/problem+json"
            if verb == "GET" and path == "/health":
                return 200, self.health_payload(), "application/json; charset=utf-8"
            if verb == "GET" and path in {"/ready", "/api/v1/internal/control/readiness"}:
                status, value = self.ready_payload(now_ms=now_ms)
                return status, value, "application/json; charset=utf-8"
            if verb == "GET" and path == "/api/v1/gateway/links/status":
                return 200, self.runtime.links_status_payload(now_ms=now_ms), "application/json; charset=utf-8"
            if verb == "GET" and path == "/api/v1/internal/control/credentials/status":
                return 200, self.runtime.credential_status_payload(), "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/gateway/links/action":
                action = str(body.get("action") or body.get("type") or "").strip().lower()
                if action in {"wechat_login_start", "wechat.login.start", "login_start"}:
                    result = self.runtime.wechat_login_start(dict(body.get("payload") or {}), now_ms=now_ms)
                elif action in {"wechat_login_wait", "wechat.login.wait", "login_wait"}:
                    result = self.runtime.wechat_login_wait(dict(body.get("payload") or body), now_ms=now_ms)
                elif action in {"wechat_start", "wechat.adapter.start", "start"}:
                    result = self.runtime.wechat_adapter_start({}, now_ms=now_ms)
                elif action in {"wechat_stop", "wechat.adapter.stop", "stop"}:
                    result = self.runtime.wechat_adapter_stop({}, now_ms=now_ms)
                elif action in {"status", "refresh", ""}:
                    result = self.runtime.links_status_payload(now_ms=now_ms)
                else:
                    return 400, {"ok": False, "reason_code": "communication.links.action_unsupported"}, "application/problem+json"
                return 200, result, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/gateway/links/settings":
                channel = str(body.get("channel") or "")
                tenant_id = str(body.get("tenant_id") or channel or "desktop")
                link_account_id = str(body.get("link_account_id") or body.get("account_id") or "default")
                credentials = body.get("credentials") if isinstance(body.get("credentials"), dict) else {}
                status = self.runtime.install_credentials(
                    channel=channel,
                    tenant_id=tenant_id,
                    link_account_id=link_account_id,
                    values={str(k): str(v) for k, v in credentials.items()},
                    now_ms=now_ms,
                )
                return 200, {"ok": True, "configured": status.configured, "revision": status.revision, "evidence_sha256": status.evidence_sha256}, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/credentials/install":
                status = self.runtime.install_credentials(
                    channel=str(body["channel"]),
                    tenant_id=str(body["tenant_id"]),
                    link_account_id=str(body["link_account_id"]),
                    values={str(k): str(v) for k, v in dict(body["credentials"]).items()},
                    now_ms=now_ms,
                )
                return 200, {"ok": True, "configured": status.configured, "revision": status.revision, "evidence_sha256": status.evidence_sha256}, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/credentials/migrate-legacy":
                statuses = self.runtime.migrate_legacy_credentials(now_ms=now_ms)
                return 200, {"ok": True, "migrated": [{"channel": item.channel, "tenant_id": item.tenant_id, "link_account_id": item.link_account_id, "revision": item.revision, "evidence_sha256": item.evidence_sha256} for item in statuses]}, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/lease/install":
                lease = ChannelOwnershipLease.model_validate_json(
                    canonical_json_bytes(body["lease"]),
                    strict=True,
                )
                installed = self.runtime.install_channel_lease(lease, now_ms=now_ms)
                started = False
                if lease.channel == "wechat":
                    started = self.runtime.start_wechat_adapter(tenant_id=lease.tenant_id, link_account_id=lease.link_account_id, now_ms=now_ms)
                elif lease.channel == "feishu":
                    started = self.runtime.start_feishu_adapter(tenant_id=lease.tenant_id, link_account_id=lease.link_account_id, now_ms=now_ms)
                return 200, {"ok": True, "installed": installed, "adapter_started": started, "lease_sha256": lease.lease_sha256}, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/delivery/authority/install":
                trust = TrustBundle.model_validate_json(
                    canonical_json_bytes(body["trust_bundle"]),
                    strict=True,
                )
                manifest = ComponentManifest.model_validate_json(
                    canonical_json_bytes(body["component_manifest"]),
                    strict=True,
                )
                self.runtime.install_delivery_authority(trust, manifest)
                return 200, {"ok": True, "installed": True, "trust_bundle_sha256": trust.bundle_sha256, "component_manifest_sha256": manifest.manifest_sha256}, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/drain/facts":
                result = self.runtime.channel_drain_facts_payload(channel=str(body["channel"]), tenant_id=str(body["tenant_id"]), link_account_id=str(body["link_account_id"]))
                return 200, {"ok": True, **result}, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/delivery":
                ticket = DeliveryTicket.model_validate_json(
                    canonical_json_bytes(body["ticket"]),
                    strict=True,
                )
                plan = OutboundPlan.model_validate_json(
                    canonical_json_bytes(body["plan"]),
                    strict=True,
                )
                receipt = self.runtime.dispatch_delivery(ticket, plan)
                return 200, {"ok": True, "api_contract": "tiangong.communication.api.v1", "delivery_receipt": receipt.model_dump(mode="json")}, "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/wechat/login/start":
                return 200, self.runtime.wechat_login_start(body, now_ms=now_ms), "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/wechat/login/wait":
                return 200, self.runtime.wechat_login_wait(body, now_ms=now_ms), "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/wechat/adapter/start":
                return 200, self.runtime.wechat_adapter_start(body, now_ms=now_ms), "application/json; charset=utf-8"
            if verb == "POST" and path == "/api/v1/internal/control/wechat/adapter/stop":
                return 200, self.runtime.wechat_adapter_stop(body, now_ms=now_ms), "application/json; charset=utf-8"
            return 404, {"ok": False, "reason_code": "communication.route.not_found"}, "application/problem+json"
        except Exception as exc:
            code = str(getattr(exc, "code", None) or "communication.embedded.failed")[:160]
            # P2-30: distinguish input errors (400), conflicts (409),
            # transient/unavailable (503) and internal failures (500) so
            # clients do not treat every failure as a conflict.
            if code.startswith(("communication.input.", "communication.validation.")):
                status = 400
            elif code.startswith("communication.conflict."):
                status = 409
            elif code.startswith(("communication.unavailable.", "communication.timeout.", "communication.closed")):
                status = 503
            else:
                status = 500
            return status, {"ok": False, "reason_code": code, "error_type": type(exc).__name__}, "application/problem+json"

    # Direct control port consumed by orchestration/cutover.  These methods
    # preserve the old client contract while avoiding HTTP and internal tokens.
    def health(self) -> dict[str, Any]:
        return self.health_payload()

    def credential_status(self) -> dict[str, Any]:
        return self.runtime.credential_status_payload()

    def migrate_legacy_credentials(self) -> dict[str, Any]:
        status, payload, _ = self.request("POST", "/api/v1/internal/control/credentials/migrate-legacy", {})
        if status >= 400:
            raise RuntimeError(str(payload.get("reason_code") or "communication.migrate_failed"))
        return payload

    def install_channel_lease(self, lease: ChannelOwnershipLease) -> dict[str, Any]:
        status, payload, _ = self.request("POST", "/api/v1/internal/control/lease/install", {"lease": lease.model_dump(mode="json")})
        if status >= 400:
            raise RuntimeError(str(payload.get("reason_code") or "communication.lease_failed"))
        return payload

    def install_delivery_authority(self, trust_bundle: TrustBundle, component_manifest: ComponentManifest) -> dict[str, Any]:
        status, payload, _ = self.request("POST", "/api/v1/internal/control/delivery/authority/install", {"trust_bundle": trust_bundle.model_dump(mode="json"), "component_manifest": component_manifest.model_dump(mode="json")})
        if status >= 400:
            raise RuntimeError(str(payload.get("reason_code") or "communication.authority_failed"))
        return payload

    def channel_drain_facts(self, *, channel: str, tenant_id: str, link_account_id: str) -> dict[str, Any]:
        status, payload, _ = self.request("POST", "/api/v1/internal/control/drain/facts", {"channel": channel, "tenant_id": tenant_id, "link_account_id": link_account_id})
        if status >= 400:
            raise RuntimeError(str(payload.get("reason_code") or "communication.drain_failed"))
        return payload

    def dispatch_delivery(self, ticket: DeliveryTicket, plan: OutboundPlan):
        return self.runtime.dispatch_delivery(ticket, plan)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.runtime.close()


__all__ = ["EMBEDDED_COMMUNICATION_BUILD_ID", "EmbeddedCommunicationService"]
