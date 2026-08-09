"""Λ Rhythm & Resource Plane V1. Synchronous, bounded and telemetry-first."""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
from math import ceil
from .event import RhythmEvent,EventCoalescer
from .budgets import WorkCost,BudgetLedger,BudgetSnapshot

QUEUE_CLASSES=("INTERACTIVE","FAST","SEMANTIC","REVALIDATION","BACKGROUND")

@dataclass(frozen=True, slots=True)
class RhythmConfig:
    queue_capacity:int=256
    debounce_ms:int=100
    semantic_min_priority:int=50
    revalidation_min_priority:int=25
    rho_target_milli:int=800
    def __post_init__(self):
        if self.queue_capacity<1: raise ValueError("queue_capacity must be positive")
        if self.debounce_ms<0 or not 1<=self.rho_target_milli<1000: raise ValueError("invalid rhythm config")

@dataclass(frozen=True, slots=True)
class WorkItem:
    event:RhythmEvent
    cost:WorkCost=WorkCost()
    semantic:bool=False
    revalidation:bool=False

@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    disposition:str
    reason_code:str
    queue_class:str
    coalesced_count:int=1

@dataclass(frozen=True, slots=True)
class QueueTelemetry:
    queue_class:str
    arrival_count:int
    service_count:int
    arrival_rate_milli_per_sec:int
    service_rate_milli_per_sec:int
    rho_milli:int|None
    oldest_queue_age_ms:int
    transform_latency_total_ms:int
    token_cost_total:int
    io_cost_total:int
    queue_depth:int

class RhythmPlane:
    __slots__=("config","budget","_queues","_coalescer","_start_ms","_arrivals","_services","_latency","_tokens","_io")
    def __init__(self,*,config:RhythmConfig,budget:BudgetLedger,start_ms:int=0)->None:
        self.config=config; self.budget=budget; self._queues={name:[] for name in QUEUE_CLASSES}; self._coalescer=EventCoalescer(debounce_ms=config.debounce_ms); self._start_ms=int(start_ms)
        self._arrivals={name:0 for name in QUEUE_CLASSES}; self._services={name:0 for name in QUEUE_CLASSES}; self._latency={name:0 for name in QUEUE_CLASSES}; self._tokens={name:0 for name in QUEUE_CLASSES}; self._io={name:0 for name in QUEUE_CLASSES}
    def set_debounce_ms(self, debounce_ms:int, *, queue_class:str|None=None)->None:
        """Apply a P12 telemetry-derived debounce to the existing coalescer.

        Queue-specific updates never change INTERACTIVE timing for background classes.
        """
        self._coalescer.set_debounce_ms(debounce_ms, queue_class=queue_class)
    @property
    def debounce_ms(self)->int:
        return self._coalescer.debounce_ms
    def debounce_ms_for(self, queue_class:str)->int:
        return self._coalescer.debounce_for(queue_class)
    def _validate(self,item:WorkItem)->str:
        q=item.event.boundary.queue_class
        if q not in self._queues: raise ValueError("UNKNOWN_QUEUE_CLASS")
        if item.semantic and item.event.priority<self.config.semantic_min_priority: return "SEMANTIC_ADMISSION_FLOOR"
        if item.revalidation and item.event.priority<self.config.revalidation_min_priority: return "REVALIDATION_ADMISSION_FLOOR"
        return ""
    def _committed_cost(self)->WorkCost:
        items=[item for queue in self._queues.values() for item in queue]
        return WorkCost(
            token_cost=sum(item.cost.token_cost for item in items),
            compute_ms=sum(item.cost.compute_ms for item in items),
            io_bytes=sum(item.cost.io_bytes for item in items),
            latency_ms=sum(item.cost.latency_ms for item in items),
        )
    def _can_commit(self,cost:WorkCost,*,interactive:bool)->bool:
        snap=self.budget.snapshot(); committed=self._committed_cost()
        token=max(0,snap.token_remaining-committed.token_cost)
        compute=max(0,snap.compute_remaining_ms-committed.compute_ms)
        io=max(0,snap.io_remaining_bytes-committed.io_bytes)
        latency=max(0,snap.latency_remaining_ms-committed.latency_ms)
        if not interactive:
            token=max(0,token-snap.interactive_token_reserve)
            compute=max(0,compute-snap.interactive_compute_reserve_ms)
            io=max(0,io-snap.interactive_io_reserve_bytes)
            latency=max(0,latency-snap.interactive_latency_reserve_ms)
        return cost.token_cost<=token and cost.compute_ms<=compute and cost.io_bytes<=io and cost.latency_ms<=latency
    @staticmethod
    def _sort_queue(queue:list[WorkItem])->None:
        queue.sort(key=lambda item:(-item.event.priority,item.event.arrived_at_ms,item.event.event_id))
    def submit(self,item:WorkItem)->AdmissionDecision:
        q=item.event.boundary.queue_class; self._arrivals[q]=self._arrivals.get(q,0)+1
        reason=self._validate(item)
        if reason: return AdmissionDecision("REJECTED",reason,q)
        queue=self._queues[q]
        for index,existing in enumerate(queue):
            if existing.event.identity_key==item.event.identity_key:
                status,coalesced=self._coalescer.offer(item.event)
                if status=="COALESCED":
                    delta=WorkCost(
                        token_cost=max(0,item.cost.token_cost-existing.cost.token_cost),
                        compute_ms=max(0,item.cost.compute_ms-existing.cost.compute_ms),
                        io_bytes=max(0,item.cost.io_bytes-existing.cost.io_bytes),
                        latency_ms=max(0,item.cost.latency_ms-existing.cost.latency_ms),
                    )
                    if not self._can_commit(delta,interactive=q=="INTERACTIVE"):
                        return AdmissionDecision("BACKPRESSURE","BUDGET_RESERVE",q,coalesced.coalesced_count)
                    queue[index]=item; self._sort_queue(queue)
                    return AdmissionDecision("COALESCED","DEBOUNCE_COALESCE",q,coalesced.coalesced_count)
        if len(queue)>=self.config.queue_capacity: return AdmissionDecision("BACKPRESSURE","QUEUE_CAPACITY",q)
        interactive=q=="INTERACTIVE"
        if not self._can_commit(item.cost,interactive=interactive): return AdmissionDecision("BACKPRESSURE","BUDGET_RESERVE",q)
        self._coalescer.offer(item.event)
        queue.append(item); self._sort_queue(queue)
        return AdmissionDecision("ADMITTED","OK",q)
    def service_one(self,queue_class:str,*,now_ms:int,transform_latency_ms:int=0)->WorkItem|None:
        queue=self._queues[queue_class]
        if not queue: return None
        item=queue.pop(0); interactive=queue_class=="INTERACTIVE"; self.budget.spend(item.cost,interactive=interactive); self._coalescer.forget(item.event)
        self._services[queue_class]+=1; self._latency[queue_class]+=max(0,int(transform_latency_ms)); self._tokens[queue_class]+=item.cost.token_cost; self._io[queue_class]+=item.cost.io_bytes
        return item
    def telemetry(self,queue_class:str,*,now_ms:int)->QueueTelemetry:
        elapsed=max(1,int(now_ms)-self._start_ms); arrivals=self._arrivals[queue_class]; services=self._services[queue_class]
        ar=arrivals*1_000_000//elapsed; sr=services*1_000_000//elapsed; rho=None if sr==0 else ar*1000//sr
        queue=self._queues[queue_class]; oldest=0 if not queue else max(0,int(now_ms)-min(item.event.arrived_at_ms for item in queue))
        return QueueTelemetry(queue_class,arrivals,services,ar,sr,rho,oldest,self._latency[queue_class],self._tokens[queue_class],self._io[queue_class],len(queue))
    def budget_snapshot(self)->BudgetSnapshot: return self.budget.snapshot()


def adaptive_debounce_ms(*,arrival_rate_milli_per_sec:int,service_rate_milli_per_sec:int,semantic_ratio_milli:int,rho_target_milli:int)->int:
    if arrival_rate_milli_per_sec<=0 or service_rate_milli_per_sec<=0 or semantic_ratio_milli<=0: return 0
    if not 1<=rho_target_milli<1000 or not 0<=semantic_ratio_milli<=1000: raise ValueError("invalid debounce parameters")
    lam=Fraction(arrival_rate_milli_per_sec,1000); mu=Fraction(service_rate_milli_per_sec,1000); p=Fraction(semantic_ratio_milli,1000); rho=Fraction(rho_target_milli,1000)
    seconds=max(Fraction(0,1), p/(rho*mu)-Fraction(1,1)/lam)
    return ceil(seconds*1000)

__all__=["QUEUE_CLASSES","RhythmConfig","WorkItem","AdmissionDecision","QueueTelemetry","RhythmPlane","adaptive_debounce_ms"]
