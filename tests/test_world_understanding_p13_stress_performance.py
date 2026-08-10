from __future__ import annotations
import time
import pytest
from world_understanding.common.budgets import BudgetConfig, BudgetLedger, WorkCost
from world_understanding.common.event import HardBoundary, RhythmEvent, EventCoalescer
from world_understanding.common.rhythm import RhythmConfig, RhythmPlane, WorkItem, QueueTelemetry, adaptive_debounce_ms
from world_understanding.dynamics.hazard import ChangeHazardWindow, estimate_stale_hazard, stale_hazard_milli
from world_understanding.dynamics.queue_control import derive_queue_control, apply_queue_control


def boundary(queue="SEMANTIC", life="life.A", world="a"*64, principal="b"*64):
    return HardBoundary(life, world, principal, queue)


def event(i, *, key="same", q="SEMANTIC", at=None, priority=100):
    return RhythmEvent(f"e{i}", key, boundary(q), i if at is None else at, f"{i%10}"*64, priority)


def test_10000_same_boundary_events_coalesce_without_queue_growth():
    budget=BudgetLedger(BudgetConfig(100000,100000,100000,100000,1000,1000,1000,1000))
    r=RhythmPlane(config=RhythmConfig(queue_capacity=32, debounce_ms=20_000), budget=budget)
    first=r.submit(WorkItem(event(0), semantic=True))
    assert first.disposition == "ADMITTED"
    last=None
    for i in range(1,10_000):
        last=r.submit(WorkItem(event(i), semantic=True))
    assert last is not None and last.disposition == "COALESCED"
    assert r.telemetry("SEMANTIC", now_ms=20_000).queue_depth == 1
    assert last.coalesced_count == 10_000


def test_coalescing_never_crosses_life_boundary():
    c=EventCoalescer(debounce_ms=10000)
    a=RhythmEvent("a","same",boundary(life="life.A"),0,"a"*64,1)
    b=RhythmEvent("b","same",boundary(life="life.B"),1,"b"*64,1)
    assert c.offer(a)[0] == "NEW"
    assert c.offer(b)[0] == "NEW"


def test_coalescing_never_crosses_principal_boundary():
    c=EventCoalescer(debounce_ms=10000)
    a=RhythmEvent("a","same",boundary(principal="b"*64),0,"a"*64,1)
    b=RhythmEvent("b","same",boundary(principal="c"*64),1,"b"*64,1)
    assert c.offer(a)[0] == "NEW"
    assert c.offer(b)[0] == "NEW"


def test_background_cannot_consume_interactive_reserve():
    budget=BudgetLedger(BudgetConfig(100,100,100,100,50,50,50,50))
    r=RhythmPlane(config=RhythmConfig(), budget=budget)
    bg=WorkItem(event(1,key="bg",q="BACKGROUND"), WorkCost(50,50,50,50))
    assert r.submit(bg).disposition == "ADMITTED"
    assert r.submit(WorkItem(event(2,key="bg2",q="BACKGROUND"), WorkCost(1,1,1,1))).disposition == "BACKPRESSURE"
    ui=WorkItem(event(3,key="ui",q="INTERACTIVE"), WorkCost(50,50,50,50))
    assert r.submit(ui).disposition == "ADMITTED"


def test_queue_capacity_backpressure_is_bounded():
    budget=BudgetLedger(BudgetConfig(1000,1000,1000,1000))
    r=RhythmPlane(config=RhythmConfig(queue_capacity=2), budget=budget)
    assert r.submit(WorkItem(event(1,key="1"), semantic=True)).disposition == "ADMITTED"
    assert r.submit(WorkItem(event(2,key="2"), semantic=True)).disposition == "ADMITTED"
    assert r.submit(WorkItem(event(3,key="3"), semantic=True)).reason_code == "QUEUE_CAPACITY"


def test_adaptive_queue_control_does_not_rewrite_interactive_debounce():
    budget=BudgetLedger(BudgetConfig(1000,1000,1000,1000))
    r=RhythmPlane(config=RhythmConfig(debounce_ms=100), budget=budget)
    t=QueueTelemetry("INTERACTIVE",50,10,5000,1000,5000,0,0,0,0,0)
    p=derive_queue_control(t, semantic_ratio_milli=1000)
    before=r.debounce_ms_for("INTERACTIVE")
    apply_queue_control(r,p)
    assert r.debounce_ms_for("INTERACTIVE") == before == 100


def test_high_churn_semantic_queue_increases_debounce():
    budget=BudgetLedger(BudgetConfig(1000,1000,1000,1000))
    r=RhythmPlane(config=RhythmConfig(debounce_ms=100), budget=budget)
    t=QueueTelemetry("SEMANTIC",50,10,5000,1000,5000,0,0,0,0,4)
    p=derive_queue_control(t, semantic_ratio_milli=1000)
    assert p.overload and p.recommended_debounce_ms > 100
    apply_queue_control(r,p)
    assert r.debounce_ms_for("SEMANTIC") == p.recommended_debounce_ms
    assert r.debounce_ms_for("INTERACTIVE") == 100


def test_service_unavailable_is_conservative_overload():
    t=QueueTelemetry("SEMANTIC",1,0,1000,0,None,0,0,0,0,1)
    p=derive_queue_control(t, semantic_ratio_milli=1000)
    assert p.overload and not p.telemetry_available
    assert p.reason_code == "QUEUE_SERVICE_UNAVAILABLE"


def test_stale_hazard_is_monotone_deterministic_and_bounded():
    values=[stale_hazard_milli(change_count=2, exposure_ms=10000, age_ms=a) for a in (0,1000,5000,10000,100000)]
    assert values == sorted(values)
    assert values[0] == 0 and values[-1] <= 1000
    assert values[2] == stale_hazard_milli(change_count=2, exposure_ms=10000, age_ms=5000)


def test_zero_change_observation_does_not_invent_hazard():
    w=ChangeHazardWindow("life.A","a"*64,0,0,60000,"git:main")
    h=estimate_stale_hazard(w, now_ms=120000, last_validated_at_ms=60000)
    assert h.hazard_milli == 0 and h.telemetry_available

@pytest.mark.parametrize("kwargs",[
    dict(change_count=-1,exposure_ms=1,age_ms=1),
    dict(change_count=1,exposure_ms=0,age_ms=1),
    dict(change_count=1,exposure_ms=1,age_ms=-1),
])
def test_invalid_hazard_telemetry_fails_closed(kwargs):
    with pytest.raises(ValueError): stale_hazard_milli(**kwargs)


def test_adaptive_debounce_is_deterministic_for_identical_telemetry():
    a=adaptive_debounce_ms(arrival_rate_milli_per_sec=5000,service_rate_milli_per_sec=1000,semantic_ratio_milli=1000,rho_target_milli=800)
    b=adaptive_debounce_ms(arrival_rate_milli_per_sec=5000,service_rate_milli_per_sec=1000,semantic_ratio_milli=1000,rho_target_milli=800)
    assert a == b and a > 0


def test_10000_event_coalescer_runtime_is_measured_not_asserted_as_production_sla():
    c=EventCoalescer(debounce_ms=20000); b=boundary()
    start=time.perf_counter()
    for i in range(10000): c.offer(RhythmEvent(str(i),"same",b,i,"a"*64,1))
    elapsed=time.perf_counter()-start
    assert elapsed >= 0
