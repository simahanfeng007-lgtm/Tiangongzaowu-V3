from __future__ import annotations
from pathlib import Path
import pytest
from world_understanding.common.rhythm import QueueTelemetry
from world_understanding.dynamics.hazard import stale_hazard_milli
from world_understanding.dynamics.queue_control import derive_queue_control
from world_understanding.dynamics.revalidation import RevalidationPlanner, RevalidationSignals
from world_understanding.dynamics.projection_feedback import ProjectionFeedbackObservation


def test_missing_change_telemetry_is_not_fabricated():
    with pytest.raises(ValueError, match="STALE_HAZARD_INPUT_INVALID"):
        stale_hazard_milli(change_count=1, exposure_ms=0, age_ms=1000)


def test_missing_validation_cost_defers_revalidation():
    plan=RevalidationPlanner().plan(RevalidationSignals(900,900,900,900,1000,None))
    assert (plan.disposition,plan.reason_code)==("DEFERRED","REVALIDATION_COST_TELEMETRY_UNAVAILABLE")


def test_zero_service_queue_fails_conservative_without_inventing_mu():
    q=QueueTelemetry("SEMANTIC",10,0,1000,0,None,1000,0,0,0,10)
    plan=derive_queue_control(q,semantic_ratio_milli=1000)
    assert not plan.telemetry_available and plan.overload
    assert plan.reason_code=="QUEUE_SERVICE_UNAVAILABLE"


def test_projection_feedback_rejects_impossible_expansion_use():
    with pytest.raises(ValueError, match="PROJECTION_EXPANSION_USE_INVALID"):
        ProjectionFeedbackObservation("life.A","a"*64,"q",100,50,"NONE",1,1,2,1)


def test_p12_sources_have_no_runtime_gateway_or_direct_tool_execution_imports():
    root=Path(__file__).resolve().parents[1]/"src"/"world_understanding"/"dynamics"
    text="\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    forbidden=("total_gateway", "RuntimeTicketAuthority", "OmniGrantAuthority", "ToolCall", "subprocess", "requests.", "httpx.")
    assert not any(token in text for token in forbidden)


def test_probability_word_is_confined_to_explicit_calibration_gate():
    root=Path(__file__).resolve().parents[1]/"src"/"world_understanding"/"dynamics"
    for path in root.glob("*.py"):
        text=path.read_text(encoding="utf-8")
        if path.name not in {"prediction.py","__init__.py"}:
            assert "probability" not in text.lower()

def test_cross_life_inquiry_backoff_fails_closed():
    from world_understanding.dynamics.inquiry_backoff import InquiryGainObservation, derive_inquiry_backoff
    rows=(InquiryGainObservation("life.A","a"*64,"x","1"*64,1000,0,False),)
    with pytest.raises(ValueError, match="INQUIRY_BACKOFF_SCOPE_MISMATCH"):
        derive_inquiry_backoff(rows,family_key="x",life_id="life.B",world_scope_hash="b"*64,now_ms=2000)


def test_cross_scope_projection_feedback_fails_closed():
    from world_understanding.dynamics.projection_feedback import build_projection_feedback
    rows=(
        ProjectionFeedbackObservation("life.A","a"*64,"q1",100,50,"NONE",1,1,0,1),
        ProjectionFeedbackObservation("life.A","b"*64,"q2",100,50,"NONE",1,1,0,2),
    )
    with pytest.raises(ValueError, match="PROJECTION_FEEDBACK_SCOPE_MISMATCH"):
        build_projection_feedback(rows)


def test_cross_scope_cognition_damping_fails_closed():
    from world_understanding.cognition.stability import StabilityReport
    from world_understanding.dynamics.cognition_damping import TimedStabilityReport, damp_cognition_level
    report=StabilityReport(500,100,400,("g1",),(),("g1",),(),(),(),(),(),(),0,0)
    rows=(TimedStabilityReport("life.A","a"*64,report,1000),)
    with pytest.raises(ValueError, match="COGNITION_DAMPING_SCOPE_MISMATCH"):
        damp_cognition_level(current_level="C1",life_id="life.B",world_scope_hash="b"*64,recent_reports=rows,last_changed_at_ms=0,now_ms=10000)

def test_cross_scope_prediction_calibration_fails_closed():
    from contracts.world_understanding.prediction import PredictionOutcome, derive_prediction_outcome_id
    from contracts.world_understanding._base import WorldRecordRef
    from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
    from contracts import canonical_sha256
    from world_understanding.dynamics.prediction import build_calibration_profile
    def mk_scope(life, anchor):
        b=(ScopeBinding(key="workspace",value=anchor),)
        w=derive_world_id(life_id=life,namespace_anchor=anchor)
        return WorldScope(life_id=life,world_id=w,domain_id="software",scope_bindings=b,world_scope_hash=derive_world_scope_hash(life_id=life,world_id=w,domain_id="software",scope_bindings=b),principal_scope_hash="c"*64,privacy_scope="system")
    rows=[]
    for i,sc in enumerate((mk_scope("life.A","a"),mk_scope("life.B","b"))):
        rr=(WorldRecordRef(record_type="world_event",record_id=f"obs.{i}",revision=1,sha256=canonical_sha256({"r":i})),)
        pid="wprd_"+canonical_sha256({"p":i})
        oid=derive_prediction_outcome_id(world_scope_hash=sc.world_scope_hash,prediction_id=pid,outcome="SUPPORTED",resolved_at_ms=1000+i,outcome_observation_refs=rr)
        rows.append(PredictionOutcome(outcome_id=oid,scope=sc,prediction_id=pid,prediction_family="x",horizon_class="short",prediction_score_milli=800,outcome="SUPPORTED",resolved_at_ms=1000+i,outcome_observation_refs=rr,calibration_bucket=800,brier_component_millionths=40000,outcome_sha256="0"*64).with_computed_hash())
    with pytest.raises(ValueError, match="PREDICTION_CALIBRATION_SCOPE_MISMATCH"):
        build_calibration_profile(tuple(rows),prediction_family="x",horizon_class="short",life_id=rows[0].scope.life_id,world_scope_hash=rows[0].scope.world_scope_hash)
