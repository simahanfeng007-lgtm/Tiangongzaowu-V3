"""Fail-closed poll/send authority bound to one gateway cutover epoch."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from contracts import ChannelOwnershipLease


ChannelOperation = Literal["POLL", "SEND"]


class ChannelAuthorityError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _scope_key(channel: str, tenant_id: str, link_account_id: str) -> tuple[str, str, str]:
    return channel, tenant_id, link_account_id


class ChannelAuthorityGate:
    """Pin channel effects to one communication instance and gateway epoch."""

    def __init__(
        self,
        *,
        owner_instance_id: str,
        expected_gateway_epoch: int,
        expected_component_manifest_sha256: str,
    ) -> None:
        if not owner_instance_id or not isinstance(expected_gateway_epoch, int):
            raise ValueError("channel authority identity is invalid")
        if isinstance(expected_gateway_epoch, bool) or expected_gateway_epoch < 1:
            raise ValueError("channel authority gateway epoch is invalid")
        if (
            len(expected_component_manifest_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_component_manifest_sha256)
        ):
            raise ValueError("channel authority component manifest digest is invalid")
        self.owner_instance_id = owner_instance_id
        self.expected_gateway_epoch = expected_gateway_epoch
        self.expected_component_manifest_sha256 = expected_component_manifest_sha256
        self._lock = threading.RLock()
        self._leases: dict[tuple[str, str, str], ChannelOwnershipLease] = {}
        self._draining: set[tuple[str, str, str]] = set()
        self._inflight: dict[tuple[str, str, str], dict[str, int]] = {}

    def install_lease(self, lease: ChannelOwnershipLease, *, now_ms: int) -> bool:
        try:
            validated = ChannelOwnershipLease.model_validate(
                lease.model_dump(mode="python"), strict=True
            )
        except (AttributeError, ValueError):
            validated = None
        if (
            not isinstance(lease, ChannelOwnershipLease)
            or validated != lease
            or not lease.has_valid_sha256()
        ):
            raise ChannelAuthorityError("channel.authority.lease_invalid")
        if (
            lease.owner_component_id != "tiangong-communication-service"
            or lease.owner_instance_id != self.owner_instance_id
            or lease.gateway_epoch != self.expected_gateway_epoch
            or lease.migration_epoch != self.expected_gateway_epoch
            or lease.component_manifest_sha256
            != self.expected_component_manifest_sha256
            or lease.allowed_operations != ("POLL", "SEND")
        ):
            raise ChannelAuthorityError("channel.authority.lease_scope_mismatch")
        if not lease.not_before_ms <= now_ms < lease.expires_at_ms:
            raise ChannelAuthorityError("channel.authority.lease_inactive")
        key = _scope_key(lease.channel, lease.tenant_id, lease.link_account_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing is not None:
                if existing == lease:
                    return False
                if (
                    lease.previous_lease_sha256 != existing.lease_sha256
                    or not existing.issued_at_ms < lease.issued_at_ms <= existing.expires_at_ms
                ):
                    raise ChannelAuthorityError("channel.authority.lease_chain_conflict")
            self._leases[key] = lease
            self._draining.discard(key)
            return True

    def begin_drain(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
    ) -> tuple[int, int]:
        key = _scope_key(channel, tenant_id, link_account_id)
        with self._lock:
            self._leases.pop(key, None)
            self._draining.add(key)
            counts = self._inflight.get(key, {})
            return counts.get("POLL", 0), counts.get("SEND", 0)

    def _authorize_locked(
        self,
        key: tuple[str, str, str],
        operation: ChannelOperation,
        now_ms: int,
    ) -> ChannelOwnershipLease:
        if key in self._draining:
            raise ChannelAuthorityError("channel.authority.draining")
        lease = self._leases.get(key)
        if lease is None:
            raise ChannelAuthorityError("channel.authority.lease_missing")
        if (
            not lease.has_valid_sha256()
            or lease.owner_instance_id != self.owner_instance_id
            or lease.gateway_epoch != self.expected_gateway_epoch
            or lease.component_manifest_sha256
            != self.expected_component_manifest_sha256
            or operation not in lease.allowed_operations
        ):
            raise ChannelAuthorityError("channel.authority.lease_invalid")
        if not lease.not_before_ms <= now_ms < lease.expires_at_ms:
            self._leases.pop(key, None)
            raise ChannelAuthorityError("channel.authority.lease_expired")
        return lease

    def authorize(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        operation: ChannelOperation,
        now_ms: int,
    ) -> ChannelOwnershipLease:
        if operation not in {"POLL", "SEND"}:
            raise ChannelAuthorityError("channel.authority.operation_invalid")
        key = _scope_key(channel, tenant_id, link_account_id)
        with self._lock:
            return self._authorize_locked(key, operation, now_ms)

    @contextmanager
    def operation(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        operation: ChannelOperation,
        now_ms: int,
    ) -> Iterator[ChannelOwnershipLease]:
        if operation not in {"POLL", "SEND"}:
            raise ChannelAuthorityError("channel.authority.operation_invalid")
        key = _scope_key(channel, tenant_id, link_account_id)
        with self._lock:
            lease = self._authorize_locked(key, operation, now_ms)
            counts = self._inflight.setdefault(key, {"POLL": 0, "SEND": 0})
            counts[operation] += 1
        try:
            yield lease
        finally:
            with self._lock:
                counts = self._inflight.get(key)
                if counts is None or counts[operation] < 1:
                    raise RuntimeError("channel authority inflight accounting is corrupt")
                counts[operation] -= 1
                if counts["POLL"] == 0 and counts["SEND"] == 0:
                    self._inflight.pop(key, None)

    def inflight_counts(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
    ) -> tuple[int, int]:
        key = _scope_key(channel, tenant_id, link_account_id)
        with self._lock:
            counts = self._inflight.get(key, {})
            return counts.get("POLL", 0), counts.get("SEND", 0)

    def active_lease_count(self, *, now_ms: int) -> int:
        with self._lock:
            keys = tuple(self._leases)
        active = 0
        for channel, tenant_id, link_account_id in keys:
            try:
                self.authorize(
                    channel=channel,
                    tenant_id=tenant_id,
                    link_account_id=link_account_id,
                    operation="POLL",
                    now_ms=now_ms,
                )
            except ChannelAuthorityError:
                continue
            active += 1
        return active


__all__ = ["ChannelAuthorityError", "ChannelAuthorityGate", "ChannelOperation"]
