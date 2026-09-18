"""Synchronous Context Output Port for the single-ingress World Understanding design."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock

from contracts.world_understanding.context_packet import WorldContextPacket
from contracts.world_understanding.query import WorldQuery
from .capability_context import CapabilityContextPacketV1, ProtectedContextIdentityV1


@dataclass(frozen=True, slots=True)
class ContextEmission:
    correlation_id: str
    query_id: str
    packet: WorldContextPacket
    capability_packet: CapabilityContextPacketV1 | None = None


class ContextOutputPort:
    """Bounded one-shot output buffer keyed by ingress correlation identity.

    This is an output sink/readback surface, not a second World Understanding input.
    """

    def __init__(self, *, max_pending: int = 256) -> None:
        if not 1 <= int(max_pending) <= 4096:
            raise ValueError("WORLD_CONTEXT_OUTPUT_LIMIT_INVALID")
        self._max_pending = int(max_pending)
        self._lock = RLock()
        self._pending: OrderedDict[str, ContextEmission] = OrderedDict()

    def emit(self, query: WorldQuery, packet: WorldContextPacket, *,
             capability_packet: CapabilityContextPacketV1 | None = None) -> None:
        if packet.scope != query.scope:
            raise ValueError("WORLD_CONTEXT_OUTPUT_SCOPE_MISMATCH")
        if packet.task_ref != query.task_ref or packet.task_sha256 != query.task_sha256:
            raise ValueError("WORLD_CONTEXT_OUTPUT_TASK_MISMATCH")
        if not packet.has_valid_hash():
            raise ValueError("WORLD_CONTEXT_OUTPUT_PACKET_HASH_INVALID")
        if not packet.context_only or packet.authorizes or packet.confirms or packet.changes_risk or packet.may_execute:
            raise ValueError("WORLD_CONTEXT_OUTPUT_AUTHORITY_INVALID")
        if ((query.frame_ref is not None and packet.frame_ref != query.frame_ref)
                or (query.basis_world_state_ref is not None and packet.basis_world_state_ref != query.basis_world_state_ref)):
            raise ValueError("WORLD_CONTEXT_OUTPUT_STATE_MISMATCH")
        if capability_packet is not None:
            if (not capability_packet.has_valid_sha256()
                    or capability_packet.world_state_ref != packet.basis_world_state_ref
                    or ProtectedContextIdentityV1("query_ref", f"{query.query_id}@{query.query_sha256}")
                    not in capability_packet.protected_identities):
                raise ValueError("WORLD_CONTEXT_OUTPUT_CAPABILITY_BINDING_INVALID")
        emission = ContextEmission(query.correlation_id, query.query_id, packet, capability_packet)
        with self._lock:
            if query.correlation_id in self._pending:
                raise ValueError("WORLD_CONTEXT_DUPLICATE_CORRELATION")
            self._pending[query.correlation_id] = emission
            self._pending.move_to_end(query.correlation_id)
            while len(self._pending) > self._max_pending:
                self._pending.popitem(last=False)

    def take(self, correlation_id: str) -> ContextEmission | None:
        with self._lock:
            return self._pending.pop(str(correlation_id), None)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


__all__ = ["ContextEmission", "ContextOutputPort"]
