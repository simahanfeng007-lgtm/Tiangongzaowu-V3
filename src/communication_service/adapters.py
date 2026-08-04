"""Narrow channel-adapter protocol; orchestration is deliberately absent."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import ChannelOwnershipLease, canonical_sha256

from .channel_authority import ChannelAuthorityError, ChannelAuthorityGate, ChannelOperation


class AdapterHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    channel: Literal["wechat", "feishu"]
    tenant_id: str = Field(min_length=1, max_length=160)
    link_account_id: str = Field(min_length=1, max_length=160)
    state: Literal[
        "disabled",
        "starting",
        "ready",
        "waiting_login",
        "missing_credentials",
        "degraded",
        "error",
        "closed",
    ]
    reason_code: str | None = Field(default=None, min_length=1, max_length=160)
    observed_at_ms: int = Field(ge=0)
    health_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        if self.state in {"degraded", "error"} and self.reason_code is None:
            raise ValueError("unhealthy adapter state requires a reason code")
        return self

    def computed_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"health_sha256"}))

    def has_valid_sha256(self) -> bool:
        return self.health_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={"health_sha256": self.computed_sha256()})


@runtime_checkable
class ChannelAdapter(Protocol):
    """Transport-only surface implemented by WeChat and Feishu adapters."""

    def health_snapshot(self, *, now_ms: int) -> AdapterHealth: ...

    def close(self) -> None: ...


def adapter_key(channel: str, tenant_id: str, link_account_id: str) -> str:
    return f"{channel}:{tenant_id}:{link_account_id}"


class AdapterRegistry:
    def __init__(self, channel_authority: ChannelAuthorityGate | None = None) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[str, ChannelAdapter] = {}
        self._channel_authority = channel_authority
        self._closing = False
        self._closed = False

    @property
    def channel_authority_bound(self) -> bool:
        with self._lock:
            return self._channel_authority is not None

    @property
    def channel_authority(self) -> ChannelAuthorityGate | None:
        with self._lock:
            return self._channel_authority

    def bind_channel_authority(self, channel_authority: ChannelAuthorityGate) -> None:
        if not isinstance(channel_authority, ChannelAuthorityGate):
            raise TypeError("adapter registry channel authority is invalid")
        with self._lock:
            if self._channel_authority is not None and self._channel_authority is not channel_authority:
                raise ValueError("adapter registry channel authority is already bound")
            self._channel_authority = channel_authority

    def authorize_operation(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        operation: ChannelOperation,
        now_ms: int,
    ) -> None:
        with self._lock:
            authority = self._channel_authority
        if authority is None:
            raise ChannelAuthorityError("channel.authority.unconfigured")
        authority.authorize(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            operation=operation,
            now_ms=now_ms,
        )

    def install_channel_lease(
        self,
        lease: ChannelOwnershipLease,
        *,
        now_ms: int,
    ) -> bool:
        with self._lock:
            authority = self._channel_authority
        if authority is None:
            raise ChannelAuthorityError("channel.authority.unconfigured")
        return authority.install_lease(lease, now_ms=now_ms)

    def begin_drain(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
    ) -> tuple[int, int]:
        with self._lock:
            authority = self._channel_authority
        if authority is None:
            raise ChannelAuthorityError("channel.authority.unconfigured")
        return authority.begin_drain(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
        )

    @contextmanager
    def operation_authority(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        operation: ChannelOperation,
        now_ms: int,
    ) -> Iterator[ChannelOwnershipLease]:
        with self._lock:
            authority = self._channel_authority
        if authority is None:
            raise ChannelAuthorityError("channel.authority.unconfigured")
        with authority.operation(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            operation=operation,
            now_ms=now_ms,
        ) as lease:
            yield lease

    def register(self, adapter: ChannelAdapter, *, now_ms: int) -> str:
        if not isinstance(adapter, ChannelAdapter):
            raise TypeError("adapter does not implement the transport-only protocol")
        health = adapter.health_snapshot(now_ms=now_ms)
        if not health.has_valid_sha256():
            raise ValueError("adapter health evidence is invalid")
        key = adapter_key(health.channel, health.tenant_id, health.link_account_id)
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("adapter registry is closing")
            if key in self._adapters:
                raise ValueError("adapter account is already registered")
            self._adapters[key] = adapter
        return key

    def snapshots(self, *, now_ms: int) -> dict[str, AdapterHealth]:
        with self._lock:
            pairs = tuple(sorted(self._adapters.items()))
        result: dict[str, AdapterHealth] = {}
        for key, adapter in pairs:
            health = adapter.health_snapshot(now_ms=now_ms)
            expected = adapter_key(health.channel, health.tenant_id, health.link_account_id)
            if key != expected or not health.has_valid_sha256():
                raise RuntimeError("adapter identity or health evidence changed")
            result[key] = health
        return result

    def unregister(self, *, channel: str, tenant_id: str, link_account_id: str) -> bool:
        key = adapter_key(channel, tenant_id, link_account_id)
        with self._lock:
            adapter = self._adapters.pop(key, None)
        if adapter is None:
            return False
        adapter.close()
        return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            pairs = tuple(sorted(self._adapters.items(), reverse=True))
        errors: list[Exception] = []
        closed: list[tuple[str, ChannelAdapter]] = []
        for key, adapter in pairs:
            try:
                adapter.close()
                closed.append((key, adapter))
            except Exception as exc:  # keep failed adapters for a retry
                errors.append(exc)
        with self._lock:
            for key, adapter in closed:
                if self._adapters.get(key) is adapter:
                    self._adapters.pop(key, None)
            if not errors and not self._adapters:
                self._closed = True
                self._closing = False
        if errors:
            raise RuntimeError("one or more channel adapters failed to close") from errors[0]


__all__ = ["AdapterHealth", "AdapterRegistry", "ChannelAdapter", "adapter_key"]
