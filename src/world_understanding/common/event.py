"""Rhythm event and hard coalescing boundary primitives."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from contracts.canonical import canonical_sha256

@dataclass(frozen=True, slots=True)
class HardBoundary:
    life_id: str
    world_scope_hash: str
    principal_scope_hash: str
    queue_class: str
    world_cut_id: str | None = None
    transaction_ref: str | None = None

    @property
    def boundary_sha256(self) -> str:
        return canonical_sha256({"domain":"tiangong.world.rhythm-hard-boundary.v1", **asdict(self)})

@dataclass(frozen=True, slots=True)
class RhythmEvent:
    event_id: str
    coalesce_key: str
    boundary: HardBoundary
    arrived_at_ms: int
    payload_sha256: str
    priority: int = 0

    @property
    def identity_key(self) -> tuple[str, str]:
        return (self.boundary.boundary_sha256, self.coalesce_key)

@dataclass(frozen=True, slots=True)
class CoalescedEvent:
    event: RhythmEvent
    coalesced_count: int
    first_arrived_at_ms: int
    last_arrived_at_ms: int

class EventCoalescer:
    __slots__=("debounce_ms","_pending","_debounce_by_queue")
    def __init__(self, *, debounce_ms: int = 100) -> None:
        if debounce_ms < 0: raise ValueError("debounce_ms must be non-negative")
        self.debounce_ms=int(debounce_ms); self._pending:dict[tuple[str,str],CoalescedEvent]={}; self._debounce_by_queue:dict[str,int]={}
    def set_debounce_ms(self, debounce_ms: int, *, queue_class: str | None = None) -> None:
        if isinstance(debounce_ms, bool) or not isinstance(debounce_ms, int) or debounce_ms < 0:
            raise ValueError("debounce_ms must be a non-negative integer")
        if queue_class is None:
            self.debounce_ms = int(debounce_ms)
        else:
            if not queue_class:
                raise ValueError("queue_class must be non-empty")
            self._debounce_by_queue[str(queue_class)] = int(debounce_ms)
    def debounce_for(self, queue_class: str) -> int:
        return self._debounce_by_queue.get(str(queue_class), self.debounce_ms)
    def offer(self,event:RhythmEvent)->tuple[str,CoalescedEvent]:
        key=event.identity_key; prior=self._pending.get(key)
        debounce_ms = self.debounce_for(event.boundary.queue_class)
        if prior is not None and event.arrived_at_ms-prior.last_arrived_at_ms <= debounce_ms:
            current=CoalescedEvent(event,prior.coalesced_count+1,prior.first_arrived_at_ms,event.arrived_at_ms)
            self._pending[key]=current; return "COALESCED",current
        current=CoalescedEvent(event,1,event.arrived_at_ms,event.arrived_at_ms)
        self._pending[key]=current; return "NEW",current
    def forget(self,event:RhythmEvent)->None: self._pending.pop(event.identity_key,None)

__all__=["HardBoundary","RhythmEvent","CoalescedEvent","EventCoalescer"]
