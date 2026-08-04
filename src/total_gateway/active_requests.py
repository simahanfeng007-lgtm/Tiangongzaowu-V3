"""Fail-closed activation boundary for durable ACTIVE session requests."""

from __future__ import annotations

from .coordination import GenerationLeaseView
from .store import (
    ActiveRequestActivation,
    GatewayStateStore,
    StoreConflictError,
    StoreNotFoundError,
)


class ActiveRequestActivator:
    """Claims request authority without starting model, tool, or delivery effects."""

    def __init__(
        self,
        store: GatewayStateStore,
        *,
        gateway_epoch: int,
        owner_instance_id: str,
        lease_duration_ms: int = 30_000,
    ) -> None:
        if (
            gateway_epoch < 1
            or not 1 <= len(owner_instance_id) <= 160
            or not 1_000 <= lease_duration_ms <= 3_600_000
        ):
            raise ValueError("active request activator configuration is invalid")
        self._store = store
        self._gateway_epoch = gateway_epoch
        self._owner_instance_id = owner_instance_id
        self._lease_duration_ms = lease_duration_ms

    @property
    def gateway_epoch(self) -> int:
        return self._gateway_epoch

    @property
    def owner_instance_id(self) -> str:
        return self._owner_instance_id

    def claim(
        self,
        request_id: str,
        session_scope_hash: str,
        *,
        now_ms: int,
    ) -> ActiveRequestActivation:
        return self._store.activate_request_run(
            request_id,
            session_scope_hash,
            gateway_epoch=self._gateway_epoch,
            owner_instance_id=self._owner_instance_id,
            activated_at_ms=now_ms,
            lease_duration_ms=self._lease_duration_ms,
        )

    def claim_next(self, *, now_ms: int) -> ActiveRequestActivation | None:
        for candidate in self._store.list_unclaimed_active_requests():
            try:
                return self.claim(
                    candidate.entry.request_id,
                    candidate.entry.session_scope_hash,
                    now_ms=now_ms,
                )
            except (StoreConflictError, StoreNotFoundError):
                # Another transaction may have completed or claimed the candidate
                # after the read. The store remains the authority, so try the next.
                continue
        return None

    def recover_next(self, *, now_ms: int) -> ActiveRequestActivation | None:
        return self._store.recover_expired_active_request(
            gateway_epoch=self._gateway_epoch,
            owner_instance_id=self._owner_instance_id,
            recovered_at_ms=now_ms,
            lease_duration_ms=self._lease_duration_ms,
        )

    def heartbeat(
        self,
        activation: ActiveRequestActivation,
        *,
        now_ms: int,
    ) -> GenerationLeaseView:
        generation = activation.generation
        if (
            generation.gateway_epoch != self._gateway_epoch
            or generation.owner_instance_id != self._owner_instance_id
            or generation.lease_id is None
        ):
            raise StoreConflictError("active request activation belongs to another gateway")
        return self._store.heartbeat_generation_lease(
            activation.entry.request_id,
            lease_id=generation.lease_id,
            owner_instance_id=self._owner_instance_id,
            now_ms=now_ms,
            lease_duration_ms=self._lease_duration_ms,
        )


__all__ = ["ActiveRequestActivator"]
