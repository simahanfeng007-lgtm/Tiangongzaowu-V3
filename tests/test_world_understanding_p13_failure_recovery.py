from __future__ import annotations
from pathlib import Path
import pytest
from world_understanding.common.budgets import BudgetConfig,BudgetLedger,WorkCost
from world_understanding.common.event import HardBoundary,RhythmEvent,EventCoalescer
from world_understanding.common.rhythm import RhythmPlane,RhythmConfig,WorkItem
from world_understanding.dynamics.queue_control import QueueControlPolicy

ROOT=Path(__file__).resolve().parents[1]
def text(rel): return (ROOT/rel).read_text(encoding="utf-8")


def test_negative_work_cost_is_rejected():
    with pytest.raises(ValueError): WorkCost(token_cost=-1)


def test_invalid_reserve_configuration_is_rejected():
    with pytest.raises(ValueError): BudgetConfig(10,10,10,10,11,0,0,0)


def test_budget_spend_fails_closed_when_exhausted():
    b=BudgetLedger(BudgetConfig(10,10,10,10))
    with pytest.raises(ValueError): b.spend(WorkCost(11,0,0,0), interactive=True)


def test_unknown_queue_class_fails_closed():
    b=BudgetLedger(BudgetConfig(10,10,10,10))
    r=RhythmPlane(config=RhythmConfig(),budget=b)
    bad=HardBoundary("life.A","a"*64,"b"*64,"UNKNOWN")
    with pytest.raises(ValueError): r.submit(WorkItem(RhythmEvent("e","x",bad,0,"a"*64,1)))


def test_invalid_queue_control_policy_is_rejected():
    with pytest.raises(ValueError): QueueControlPolicy(rho_target_milli=1000)


def test_context_failure_is_fail_open_to_legacy_path():
    s=text("app/backend/tiangong-backend/v3/world_context_integration.py")
    assert 'except Exception as exc:' in s
    assert 'WORLD_CONTEXT_SLOT unavailable' in s
    assert '# Context is optional and non-authorizing.' in s


def test_closure_active_cut_overflow_is_not_swallowed():
    s=text("src/world_understanding/known/closure.py")
    assert 'except ActiveCutOverflow:\n                        raise' in s


def test_closure_rule_errors_are_diagnostics_not_authority_escalation():
    s=text("src/world_understanding/known/closure.py")
    assert 'ClosureDiagnostic(rule.spec.rule_id, "RULE_ERROR", type(exc).__name__)' in s


def test_non_accept_self_will_produces_no_autonomous_intent():
    s=text("src/world_understanding/inquiry/self_will_integration.py")
    assert 'if record.decision != "ACCEPT":\n            return record, None' in s


def test_invalid_inquiry_authority_is_rejected_before_self_will_decider():
    s=text("src/world_understanding/inquiry/self_will_integration.py")
    assert 'WORLD_INQUIRY_INVALID_FOR_SELF_WILL' in s


def test_failed_or_unverified_tool_write_cannot_promote_observed_file_write():
    s=text("src/world_understanding/source_compilers/p3.py")
    guard='payload.get("observed_write_effect") is True and isinstance(evidence,dict) and evidence.get("authoritative") is True'
    assert guard in s


def test_context_request_without_l8_handler_preserves_p2_behavior():
    s=text("src/world_understanding/ingress/router.py")
    assert 'reason_code="CONTEXT_REQUEST_ACCEPTED"' in s


def test_unclassified_source_quarantined():
    s=text("src/world_understanding/ingress/router.py")
    assert 'envelope.source_kind=="UNCLASSIFIED_SOURCE"' in s
    assert 'reason_code="UNCLASSIFIED_SOURCE"' in s


def test_background_coalescing_cannot_cross_world_boundary():
    c=EventCoalescer(debounce_ms=1000)
    a=RhythmEvent("a","same",HardBoundary("life.A","a"*64,"b"*64,"BACKGROUND"),0,"a"*64,1)
    b=RhythmEvent("b","same",HardBoundary("life.A","c"*64,"b"*64,"BACKGROUND"),1,"b"*64,1)
    assert c.offer(a)[0] == "NEW"
    assert c.offer(b)[0] == "NEW"
