"""Machine-derived drain evidence for one communication channel account."""

from __future__ import annotations

from contracts import ChannelDrainEvidence, build_channel_drain_evidence

from .channel_authority import ChannelAuthorityGate
from .delivery_ledger import DeliveryLedger
from .inbox import CommunicationInbox


class ChannelDrainNotReady(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class CommunicationDrainInspector:
    def __init__(
        self,
        inbox: CommunicationInbox,
        deliveries: DeliveryLedger,
        channel_authority: ChannelAuthorityGate,
    ) -> None:
        self._inbox = inbox
        self._deliveries = deliveries
        self._channel_authority = channel_authority

    def capture(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        gateway_epoch: int,
        legacy_owner_component_id: str,
        legacy_owner_instance_id: str,
        observed_at_ms: int,
    ) -> ChannelDrainEvidence:
        if gateway_epoch != self._channel_authority.expected_gateway_epoch:
            raise ChannelDrainNotReady("channel.drain.gateway_epoch_mismatch")
        if legacy_owner_instance_id != self._channel_authority.owner_instance_id:
            raise ChannelDrainNotReady("channel.drain.owner_instance_mismatch")
        inflight_poll_count, gate_inflight_send_count = self._channel_authority.begin_drain(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
        )
        try:
            inbox = self._inbox.channel_drain_facts(
                channel=channel,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
            )
            deliveries = self._deliveries.channel_drain_facts(
                channel=channel,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
            )
            if inflight_poll_count:
                raise ChannelDrainNotReady("channel.drain.poll_inflight")
            if gate_inflight_send_count or deliveries.inflight_send_count:
                raise ChannelDrainNotReady("channel.drain.send_inflight")
            if inbox.unacknowledged_count:
                raise ChannelDrainNotReady("channel.drain.inbox_unacknowledged")
            if deliveries.unresolved_delivery_count:
                raise ChannelDrainNotReady("channel.drain.delivery_unresolved")
            return build_channel_drain_evidence(
                channel=channel,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
                gateway_epoch=gateway_epoch,
                legacy_owner_component_id=legacy_owner_component_id,
                legacy_owner_instance_id=legacy_owner_instance_id,
                inbox_ledger_sha256=inbox.ledger_sha256,
                delivery_ledger_sha256=deliveries.ledger_sha256,
                last_cursor_sha256=inbox.last_cursor_sha256,
                observed_at_ms=observed_at_ms,
            )
        except Exception:
            # 前置校验失败：回滚 begin_drain 摘掉的租约与 draining 状态，
            # 否则一次失败的 drain 会让该通道停止轮询/发送，直到外部
            # 重新安装租约才能恢复。
            self._channel_authority.abort_drain(
                channel=channel,
                tenant_id=tenant_id,
                link_account_id=link_account_id,
            )
            raise


__all__ = ["ChannelDrainNotReady", "CommunicationDrainInspector"]
