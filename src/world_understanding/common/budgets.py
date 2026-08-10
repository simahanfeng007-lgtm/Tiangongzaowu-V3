"""Conservative resource budgets with an interactive reserve background cannot spend."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class WorkCost:
    token_cost: int = 0
    compute_ms: int = 0
    io_bytes: int = 0
    latency_ms: int = 0
    def __post_init__(self):
        if min(self.token_cost,self.compute_ms,self.io_bytes,self.latency_ms) < 0: raise ValueError("work cost cannot be negative")

@dataclass(frozen=True, slots=True)
class BudgetConfig:
    token_budget: int
    compute_budget_ms: int
    io_budget_bytes: int
    latency_budget_ms: int
    interactive_token_reserve: int = 0
    interactive_compute_reserve_ms: int = 0
    interactive_io_reserve_bytes: int = 0
    interactive_latency_reserve_ms: int = 0
    def __post_init__(self):
        values=(self.token_budget,self.compute_budget_ms,self.io_budget_bytes,self.latency_budget_ms,self.interactive_token_reserve,self.interactive_compute_reserve_ms,self.interactive_io_reserve_bytes,self.interactive_latency_reserve_ms)
        if min(values)<0: raise ValueError("budgets cannot be negative")
        if self.interactive_token_reserve>self.token_budget or self.interactive_compute_reserve_ms>self.compute_budget_ms or self.interactive_io_reserve_bytes>self.io_budget_bytes or self.interactive_latency_reserve_ms>self.latency_budget_ms: raise ValueError("interactive reserve exceeds total budget")

@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    token_remaining:int; compute_remaining_ms:int; io_remaining_bytes:int; latency_remaining_ms:int
    interactive_token_reserve:int; interactive_compute_reserve_ms:int; interactive_io_reserve_bytes:int; interactive_latency_reserve_ms:int

class BudgetLedger:
    __slots__=("config","_token","_compute","_io","_latency")
    def __init__(self,config:BudgetConfig)->None:
        self.config=config; self._token=config.token_budget; self._compute=config.compute_budget_ms; self._io=config.io_budget_bytes; self._latency=config.latency_budget_ms
    def snapshot(self)->BudgetSnapshot:
        c=self.config
        return BudgetSnapshot(self._token,self._compute,self._io,self._latency,c.interactive_token_reserve,c.interactive_compute_reserve_ms,c.interactive_io_reserve_bytes,c.interactive_latency_reserve_ms)
    def _available(self,*,interactive:bool)->tuple[int,int,int,int]:
        c=self.config
        if interactive: return self._token,self._compute,self._io,self._latency
        return max(0,self._token-c.interactive_token_reserve),max(0,self._compute-c.interactive_compute_reserve_ms),max(0,self._io-c.interactive_io_reserve_bytes),max(0,self._latency-c.interactive_latency_reserve_ms)
    def can_spend(self,cost:WorkCost,*,interactive:bool)->bool:
        t,c,i,l=self._available(interactive=interactive)
        return cost.token_cost<=t and cost.compute_ms<=c and cost.io_bytes<=i and cost.latency_ms<=l
    def spend(self,cost:WorkCost,*,interactive:bool)->None:
        if not self.can_spend(cost,interactive=interactive): raise ValueError("BUDGET_EXHAUSTED")
        self._token-=cost.token_cost; self._compute-=cost.compute_ms; self._io-=cost.io_bytes; self._latency-=cost.latency_ms

__all__=["WorkCost","BudgetConfig","BudgetSnapshot","BudgetLedger"]
