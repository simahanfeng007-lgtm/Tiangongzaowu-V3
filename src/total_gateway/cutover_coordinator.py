"""Gateway-owned migration and lease renewal for replacement channel workers."""

from __future__ import annotations

import threading
import time

from contracts import (
    GatePromotionRecord,
    begin_channel_cutover,
    build_channel_drain_evidence,
    canonical_sha256,
    derive_cutover_id,
)

from .communication_client import CommunicationClientError, CommunicationControlClient
from .store import StoreConflictError


class CutoverCoordinatorError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class V21GateCutoverCoordinator:
    """Small persistence boundary for the frozen v2.1 gate DAG.

    This does not own policy or change authorization.  Its sole authority is
    to reject a mode promotion unless the receipt and the durable head bind to
    exactly the same build and source manifest.
    """

    def __init__(self, store) -> None:
        self._store = store

    def promote(self, record: GatePromotionRecord, receipt: dict[str, object]) -> bool:
        if not record.has_valid_sha256():
            raise CutoverCoordinatorError("v21_gate.promotion_digest_invalid")
        if (
            receipt.get("status") != "PASS"
            or receipt.get("promotion_allowed") is not True
            or receipt.get("gate") != record.to_gate
            or receipt.get("build_id") != record.build_id
            or receipt.get("source_manifest_sha256") != record.source_manifest_sha256
        ):
            raise CutoverCoordinatorError("v21_gate.receipt_binding_invalid")
        return self._store.promote_v21_gate(record)

    def head(self) -> tuple[int, str, str, str]:
        return self._store.get_v21_gate_promotion_head()


class ChannelCutoverCoordinator:
    def __init__(
        self,
        *,
        runtime,
        communication_token: str,
        communication_control=None,
        component_manifest,
        delivery_trust_bundle_factory,
    ) -> None:
        self._runtime = runtime
        self._client = communication_control or CommunicationControlClient(communication_token)
        self._components = component_manifest
        self._trust_bundle = delivery_trust_bundle_factory
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._leases: dict[tuple[str, str, str], tuple[str, int]] = {}
        self._lock = threading.Lock()
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="tiangong-channel-cutover",
            daemon=True,
        )
        self._thread.start()

    def status_payload(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "active_lease_count": len(self._leases),
                "last_error_code": self._last_error,
            }

    def _set_error(self, code: str | None) -> None:
        with self._lock:
            self._last_error = code

    @staticmethod
    def _require_zero_drain(facts: dict[str, object]) -> None:
        for name in (
            "poll_inflight",
            "send_inflight",
            "unacknowledged_inbox_count",
            "unresolved_delivery_count",
        ):
            value = facts.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value != 0:
                raise CutoverCoordinatorError("cutover.channel_not_drained")
        for name in ("inbox_ledger_sha256", "delivery_ledger_sha256"):
            value = facts.get(name)
            if not isinstance(value, str) or len(value) != 64:
                raise CutoverCoordinatorError("cutover.drain_evidence_invalid")

    def _activate(self, candidate_instance: str, item: dict[str, object], now_ms: int) -> None:
        channel = str(item["channel"])
        tenant_id = str(item["tenant_id"])
        account = str(item["link_account_id"])
        key = (channel, tenant_id, account)
        active_lease = self._runtime.get_active_channel_lease(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=account,
            now_ms=now_ms,
        )
        if active_lease is not None:
            if active_lease.owner_instance_id != candidate_instance:
                raise CutoverCoordinatorError("cutover.candidate_identity_changed")
            self._client.install_channel_lease(active_lease)
            with self._lock:
                self._leases[key] = (
                    active_lease.cutover_id,
                    active_lease.expires_at_ms,
                )
            return
        cutover_id = derive_cutover_id(
            channel, tenant_id, account, self._runtime.lease.gateway_epoch
        )
        snapshot = self._runtime.store.get_channel_cutover(cutover_id)
        if snapshot is not None and snapshot.candidate_owner_instance_id != candidate_instance:
            raise CutoverCoordinatorError("cutover.candidate_identity_changed")
        facts = self._client.channel_drain_facts(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=account,
        )
        self._require_zero_drain(facts)
        if snapshot is None:
            legacy_instance = "legacy-port-owner-replaced-" + canonical_sha256(
                {"channel": channel, "tenant_id": tenant_id, "link_account_id": account}
            )[:32]
            snapshot = begin_channel_cutover(
                channel=channel,  # type: ignore[arg-type]
                tenant_id=tenant_id,
                link_account_id=account,
                gateway_epoch=self._runtime.lease.gateway_epoch,
                legacy_owner_component_id="legacy-communication-service",
                legacy_owner_instance_id=legacy_instance,
                candidate_owner_instance_id=candidate_instance,
                started_at_ms=now_ms,
            )
            self._runtime.begin_channel_cutover(snapshot)
        if snapshot.state == "DRAINING":
            evidence = build_channel_drain_evidence(
                channel=channel,  # type: ignore[arg-type]
                tenant_id=tenant_id,
                link_account_id=account,
                gateway_epoch=self._runtime.lease.gateway_epoch,
                legacy_owner_component_id=snapshot.legacy_owner_component_id,
                legacy_owner_instance_id=snapshot.legacy_owner_instance_id,
                inbox_ledger_sha256=str(facts["inbox_ledger_sha256"]),
                delivery_ledger_sha256=str(facts["delivery_ledger_sha256"]),
                last_cursor_sha256=(
                    None
                    if facts.get("last_cursor_sha256") is None
                    else str(facts["last_cursor_sha256"])
                ),
                observed_at_ms=now_ms,
            )
            snapshot = self._runtime.record_channel_drain(evidence)
        if snapshot.state == "DRAINED":
            registration = self._runtime.activate_channel_candidate(
                snapshot.cutover_id,
                component_manifest_sha256=self._components.manifest_sha256,
                issued_at_ms=max(now_ms, snapshot.updated_at_ms),
                lease_ttl_ms=30_000,
            )
        else:
            raise CutoverCoordinatorError("cutover.active_lease_missing_or_expired")
        self._client.install_channel_lease(registration.lease)
        with self._lock:
            self._leases[key] = (
                snapshot.cutover_id,
                registration.lease.expires_at_ms,
            )

    def _renew_due(self, now_ms: int) -> None:
        with self._lock:
            leases = tuple(self._leases.items())
        for key, (cutover_id, expires_at_ms) in leases:
            if expires_at_ms - now_ms > 12_000:
                continue
            registration = self._runtime.renew_channel_candidate(
                cutover_id,
                issued_at_ms=now_ms,
                lease_ttl_ms=30_000,
            )
            self._client.install_channel_lease(registration.lease)
            with self._lock:
                self._leases[key] = (cutover_id, registration.lease.expires_at_ms)

    def _bootstrap(self) -> None:
        health = self._client.health()
        candidate = str(health.get("instance_id") or "")
        if not candidate:
            raise CutoverCoordinatorError("cutover.candidate_identity_missing")
        now_ms = time.time_ns() // 1_000_000
        migrated = self._client.migrate_legacy_credentials().get("migrated")
        if not isinstance(migrated, list):
            raise CutoverCoordinatorError("cutover.credential_migration_invalid")
        credentials = self._client.credential_status().get("credentials")
        if not isinstance(credentials, list):
            raise CutoverCoordinatorError("cutover.credential_status_invalid")
        active_credentials: dict[tuple[str, str, str], dict[str, object]] = {}
        for item in [*migrated, *credentials]:
            if not isinstance(item, dict) or item.get("channel") not in {"wechat", "feishu"}:
                continue
            if item.get("configured", True) is not True:
                continue
            try:
                key = (
                    str(item["channel"]),
                    str(item["tenant_id"]),
                    str(item["link_account_id"]),
                )
            except KeyError as exc:
                raise CutoverCoordinatorError("cutover.credential_status_invalid") from exc
            if not all(key):
                raise CutoverCoordinatorError("cutover.credential_status_invalid")
            active_credentials[key] = item
        for item in active_credentials.values():
            self._activate(candidate, item, now_ms)
        if active_credentials:
            self._client.install_delivery_authority(
                self._trust_bundle(now_ms),
                self._components,
            )

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                self._renew_due(time.time_ns() // 1_000_000)
                self._bootstrap()
                self._set_error(None)
                self._closed.wait(2.0)
            except (
                CommunicationClientError,
                CutoverCoordinatorError,
                StoreConflictError,
                RuntimeError,
                ValueError,
            ) as exc:
                self._set_error(getattr(exc, "code", None) or str(exc)[:160])
                self._closed.wait(1.0)

    def close(self) -> None:
        self._closed.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError("cutover coordinator did not stop")


__all__ = [
    "ChannelCutoverCoordinator", "CutoverCoordinatorError",
    "V21GateCutoverCoordinator",
]
