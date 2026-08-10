"""In-memory scoped transaction primitive for deterministic common-kernel batches."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.world_cut import WorldCut
from .scope import require_exact_scope
from .world_cut import require_compatible_world_cuts

@dataclass(frozen=True, slots=True)
class TransactionItem:
    item_hash:str
    scope:WorldScope
    world_cut:WorldCut|None=None

class ScopedTransaction:
    __slots__=("scope","_items","_closed")
    def __init__(self,scope:WorldScope)->None: self.scope=scope; self._items=[]; self._closed=False
    def stage(self,item:TransactionItem)->None:
        if self._closed: raise RuntimeError("transaction closed")
        require_exact_scope(self.scope,item.scope)
        cuts=tuple(x.world_cut for x in (*self._items,item) if x.world_cut is not None)
        require_compatible_world_cuts(cuts)
        self._items.append(item)
    def commit(self)->tuple[TransactionItem,...]:
        if self._closed: raise RuntimeError("transaction closed")
        self._closed=True; return tuple(sorted(self._items,key=lambda x:x.item_hash))
    def rollback(self)->None: self._items.clear(); self._closed=True

__all__=["TransactionItem","ScopedTransaction"]
