from __future__ import annotations

from contracts import canonical_sha256
from contracts.world_understanding._base import WorldClaim, WorldRecordRef, WorldValue
from contracts.world_understanding.inquiry import InquiryOutcome, WorldInquiry, derive_inquiry_id, derive_inquiry_outcome_id
from contracts.world_understanding.prediction import PredictionOutcome, WorldPrediction, derive_prediction_id, derive_prediction_outcome_id
from contracts.world_understanding.scope import ScopeBinding, WorldScope, derive_world_id, derive_world_scope_hash
from contracts.world_understanding.transform_metrics import TransformCostObservation, TransformQualityProfile
from world_understanding.common.budgets import BudgetConfig, BudgetLedger, WorkCost
from world_understanding.common.event import HardBoundary, RhythmEvent
from world_understanding.common.rhythm import QueueTelemetry, RhythmConfig, RhythmPlane, WorkItem
from world_understanding.dynamics.cognition_damping import TimedStabilityReport, damp_cognition_level
from world_understanding.dynamics.hazard import ChangeHazardWindow, estimate_stale_hazard, stale_hazard_milli
from world_understanding.dynamics.inquiry_backoff import InquiryGainObservation, derive_inquiry_backoff
from world_understanding.dynamics.prediction import build_calibration_profile, calibrated_probability_milli, prediction_error_milli, resolve_prediction
from world_understanding.dynamics.projection_feedback import ProjectionFeedbackObservation, build_projection_feedback
from world_understanding.dynamics.queue_control import apply_queue_control, derive_queue_control
from world_understanding.dynamics.revalidation import RevalidationPlanner, RevalidationSignals
from world_understanding.dynamics.semantic_throttle import SemanticThrottleSnapshot, TelemetrySemanticAdmissionController
from world_understanding.dynamics.transform_feedback import build_transform_feedback
from world_understanding.inquiry.admission import InquiryAdmission, InquiryAdmissionSignals
from world_understanding.cognition.stability import StabilityReport


def scope() -> WorldScope:
    bindings = (ScopeBinding(key="workspace", value="repo"),)
    life_id = "life.A"
    world_id = derive_world_id(life_id=life_id, namespace_anchor="repo")
    return WorldScope(
        life_id=life_id,
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(life_id=life_id, world_id=world_id, domain_id="software", scope_bindings=bindings),
        principal_scope_hash="b"*64,
        privacy_scope="system",
    )


def ref(kind: str, ident: str, ch: str = "a") -> WorldRecordRef:
    return WorldRecordRef(record_type=kind, record_id=ident, revision=1, sha256=ch*64)


def make_inquiry(*, gain: int = 900, impact: int = 900) -> WorldInquiry:
    sc = scope()
    subjects = (ref("world_entity", "entity.1"),)
    gap_id = "wgap_" + "1"*64
    q = "Revalidate entity.1 from independent reality evidence"
    iid = derive_inquiry_id(world_scope_hash=sc.world_scope_hash, question=q, knowledge_gap_id=gap_id, subject_refs=subjects)
    return WorldInquiry(
        inquiry_id=iid,
        correlation_id="corr.1",
        curiosity_id="wcur_"+"2"*64,
        knowledge_gap_id=gap_id,
        scope=sc,
        subject_refs=subjects,
        question=q,
        inquiry_kind="revalidation",
        reason_codes=("world.stale",),
        missing_evidence_types=("filesystem_observation",),
        expected_information_gain_milli=gain,
        impact_milli=impact,
        urgency_milli=500,
        suggested_observation_modalities=("filesystem_observation",),
        dedup_key="3"*64,
        inquiry_budget_remaining=10,
        created_at_ms=1_000,
        expires_at_ms=100_000,
        inquiry_sha256="0"*64,
    ).with_computed_hash()


def make_outcome(i: int, *, gain: int, resolved: bool = False) -> InquiryOutcome:
    sc = scope()
    iq = make_inquiry()
    closed = 10_000 + i * 1_000
    oid = derive_inquiry_outcome_id(
        world_scope_hash=sc.world_scope_hash,
        inquiry_id=iq.inquiry_id,
        self_will_decision="DISMISS",
        closed_at_ms=closed,
        resulting_source_envelope_refs=(),
    )
    return InquiryOutcome(
        outcome_id=oid,
        scope=sc,
        inquiry_id=iq.inquiry_id,
        self_will_decision="DISMISS",
        resolved=resolved,
        residual_gap_milli=1000 if not resolved else 0,
        information_gain_milli=gain,
        closed_at_ms=closed,
        outcome_sha256="0"*64,
    ).with_computed_hash()


def test_stable_world_hazard_and_revalidation_cost_decline():
    sc=scope()
    stable = ChangeHazardWindow(sc.life_id, sc.world_scope_hash, 0, 0, 60_000, "git:main")
    h0 = estimate_stale_hazard(stable, now_ms=120_000, last_validated_at_ms=60_000)
    assert h0.hazard_milli == 0
    planner = RevalidationPlanner()
    p0 = planner.plan(RevalidationSignals(0, 900, 900, 700, 0, 100))
    assert (p0.disposition, p0.reason_code) == ("SKIP", "REVALIDATION_STABLE_WORLD")

    churn = ChangeHazardWindow(sc.life_id, sc.world_scope_hash, 10, 0, 60_000, "git:main")
    h1 = estimate_stale_hazard(churn, now_ms=120_000, last_validated_at_ms=60_000)
    assert h1.hazard_milli > 700
    p1 = planner.plan(RevalidationSignals(h1.hazard_milli, 900, 900, 700, 1000, 100))
    assert p1.priority_milli > p0.priority_milli
    assert p1.recommended_delay_ms < p0.recommended_delay_ms


def test_stale_hazard_is_monotone_and_deterministic():
    a = stale_hazard_milli(change_count=2, exposure_ms=10_000, age_ms=1_000)
    b = stale_hazard_milli(change_count=2, exposure_ms=10_000, age_ms=5_000)
    c = stale_hazard_milli(change_count=2, exposure_ms=10_000, age_ms=5_000)
    assert 0 < a < b <= 1000
    assert b == c


def test_transform_feedback_uses_real_cost_and_quality_only():
    obs = tuple(
        TransformCostObservation(transform_id="world.semantic.l4", transform_version="v1", input_count=1, output_count=1, token_cost=10+i, cpu_time_ms=1, wall_time_ms=20+i, io_bytes=100+i, llm_latency_ms=10+i, success=True, failure_type=None, created_at_ms=1000+i)
        for i in range(20)
    )
    quality = TransformQualityProfile(
        transform_id="world.semantic.l4", transform_version="v1", domain="software",
        coverage_estimate_milli=900, downstream_challenge_rate_milli=450,
        mean_cost_milli=300, p95_latency_ms=100, sample_count=20, last_calibrated_at_ms=2000,
    )
    profile = build_transform_feedback(obs, quality=quality)
    assert profile.sample_count == 20
    assert profile.validation_cost_milli == 300
    assert profile.downstream_challenge_rate_milli == 450
    assert profile.empirical_evidence_weight_milli == 0


def test_high_churn_queue_control_increases_debounce_and_coalesces():
    budget = BudgetLedger(BudgetConfig(1000,1000,1000,1000,100,100,100,100))
    rhythm = RhythmPlane(config=RhythmConfig(debounce_ms=100), budget=budget, start_ms=0)
    telemetry = QueueTelemetry("SEMANTIC", 50, 10, 5000, 1000, 5000, 10, 0, 0, 0, 4)
    plan = derive_queue_control(telemetry, semantic_ratio_milli=1000)
    assert plan.overload and plan.recommended_debounce_ms > 100
    apply_queue_control(rhythm, plan)
    assert rhythm.debounce_ms_for("SEMANTIC") == plan.recommended_debounce_ms
    assert rhythm.debounce_ms_for("INTERACTIVE") == 100
    boundary = HardBoundary("life.A","a"*64,"b"*64,"SEMANTIC")
    e1 = RhythmEvent("e1","same",boundary,0,"1"*64,100)
    e2 = RhythmEvent("e2","same",boundary,500,"2"*64,100)
    assert rhythm.submit(WorkItem(e1, semantic=True)).disposition == "ADMITTED"
    assert rhythm.submit(WorkItem(e2, semantic=True)).disposition == "COALESCED"


def test_query_burst_preserves_interactive_reserve_after_adaptation():
    budget = BudgetLedger(BudgetConfig(100,100,100,100,50,50,50,50))
    rhythm = RhythmPlane(config=RhythmConfig(debounce_ms=100), budget=budget, start_ms=0)
    bg = HardBoundary("life.A","a"*64,"b"*64,"BACKGROUND")
    ui = HardBoundary("life.A","a"*64,"b"*64,"INTERACTIVE")
    assert rhythm.submit(WorkItem(RhythmEvent("b1","b1",bg,0,"1"*64,1), WorkCost(50,50,50,50))).disposition == "ADMITTED"
    assert rhythm.submit(WorkItem(RhythmEvent("b2","b2",bg,1,"2"*64,1), WorkCost(1,1,1,1))).disposition == "BACKPRESSURE"
    assert rhythm.submit(WorkItem(RhythmEvent("u1","u1",ui,2,"3"*64,1000), WorkCost(50,50,50,50))).disposition == "ADMITTED"


def test_zero_gain_inquiry_backoff_is_exponential_bounded_and_admission_defers():
    observations = tuple(InquiryGainObservation.from_outcome(make_outcome(i, gain=0), family_key="stale.file") for i in range(4))
    sc=scope()
    state = derive_inquiry_backoff(observations, family_key="stale.file", life_id=sc.life_id, world_scope_hash=sc.world_scope_hash, now_ms=13_500)
    assert state.consecutive_zero_gain == 4
    assert state.backoff_remaining_ms > 0
    iq = make_inquiry()
    signals = InquiryAdmissionSignals(900,900,900,0,0,0,0,0,0,10_000,10, backoff_remaining_ms=state.backoff_remaining_ms, prior_zero_gain_count=4)
    decision = InquiryAdmission().evaluate(iq, signals)
    assert (decision.disposition, decision.reason_code) == ("DEFERRED", "INQUIRY_ZERO_GAIN_BACKOFF")


def test_p11_admission_behavior_unchanged_without_backoff_signal():
    iq = make_inquiry()
    signals = InquiryAdmissionSignals(900,900,900,0,0,0,0,0,0,10_000,10)
    assert InquiryAdmission().evaluate(iq, signals, charge=False).disposition == "ADMITTED"


def _report(level: str) -> StabilityReport:
    if level == "C2":
        return StabilityReport(800,100,700,("g1","g2"),("c1",),("g1",),(),(),(),(),(),(),0,0)
    if level == "C1":
        return StabilityReport(500,150,350,("g1",),("c1",),("g1",),(),(),(),(),(),(),0,0)
    return StabilityReport(100,400,0,("g1",),("c1",),("g1",),(),(),(),(),(),(),0,0)


def test_contradictory_cognition_does_not_oscillate():
    sc=scope()
    alternating = tuple(TimedStabilityReport(sc.life_id, sc.world_scope_hash, _report(level), i*1000) for i,level in enumerate(("C1","C2","C1","C2","C1"),1))
    hold = damp_cognition_level(current_level="C2", life_id=sc.life_id, world_scope_hash=sc.world_scope_hash, recent_reports=alternating, last_changed_at_ms=0, now_ms=100_000)
    assert hold.resulting_level == "C2"
    assert not hold.changed
    consistent = alternating + (TimedStabilityReport(sc.life_id, sc.world_scope_hash, _report("C1"),6000),)
    move = damp_cognition_level(current_level="C2", life_id=sc.life_id, world_scope_hash=sc.world_scope_hash, recent_reports=consistent, last_changed_at_ms=0, now_ms=100_000)
    assert move.resulting_level == "C1" and move.changed


def test_projection_feedback_reduces_optional_scale_only_from_measured_usage():
    sc=scope()
    rows = tuple(ProjectionFeedbackObservation(sc.life_id,sc.world_scope_hash,f"q{i}",1000,950,"BUDGET_TRUNCATED",20,10,1,1000+i) for i in range(8))
    profile = build_projection_feedback(rows)
    assert profile.truncation_rate_milli == 1000
    assert profile.expansion_use_rate_milli == 100
    assert profile.recommended_optional_scale_milli == 500
    assert profile.empirical_evidence_weight_milli == 0


def test_noisy_world_semantic_throttle_stops_before_base_admission():
    class Spy:
        rhythm = None
        calls = 0
        def admit(self, **kwargs):
            self.calls += 1
            raise AssertionError("base admission should not run")
    quality = TransformQualityProfile(
        transform_id="world.semantic.l4", transform_version="v1", domain="software",
        coverage_estimate_milli=800, downstream_challenge_rate_milli=700,
        mean_cost_milli=300, p95_latency_ms=100, sample_count=100, last_calibrated_at_ms=1000,
    )
    telemetry = QueueTelemetry("SEMANTIC",20,20,500,1000,500,0,0,0,0,0)
    snap = SemanticThrottleSnapshot(telemetry, stale_hazard_milli=900, quality=quality)
    spy = Spy()
    gate = TelemetrySemanticAdmissionController(base=spy, snapshot_provider=lambda: snap)
    out = gate.admit()
    assert not out.admitted and out.reason_code == "SEMANTIC_NOISY_WORLD"
    assert spy.calls == 0


def test_prediction_resolution_never_becomes_evidence():
    sc = scope(); basis = ref("world_state","state.1","c"); subject = ref("world_entity","entity.1","d")
    claim = WorldClaim(subject_ref=subject, predicate="runtime.available", value=WorldValue(kind="boolean", boolean_value=True))
    pid = derive_prediction_id(world_scope_hash=sc.world_scope_hash,basis_world_state_ref=basis,predicted_claim=claim,condition_claim=None,horizon_start_ms=1000,horizon_end_ms=2000)
    p = WorldPrediction(
        prediction_id=pid, scope=sc, basis_world_state_ref=basis, predicted_claim=claim,
        prediction_kind="runtime.change", horizon_start_ms=1000, horizon_end_ms=2000,
        prediction_score_milli=800, basis_refs=(basis,), created_at_ms=900, prediction_sha256="0"*64,
    ).with_computed_hash()
    revised, outcome = resolve_prediction(p, outcome_kind="SUPPORTED", resolved_at_ms=2000, outcome_observation_refs=(ref("world_event","obs.1","e"),), prediction_family="runtime", horizon_class="short")
    assert revised.status == "RESOLVED"
    assert outcome.empirical_evidence_weight_milli == 0 and outcome.evidence_authority == "none"
    assert prediction_error_milli(outcome) == 200


def make_prediction_outcome(i: int, score: int, supported: bool) -> PredictionOutcome:
    sc=scope(); o="SUPPORTED" if supported else "CONTRADICTED"; rr=(ref("world_event",f"obs.{i}",hex(i%16)[2:]),)
    pid="wprd_"+canonical_sha256({"i":i})
    oid=derive_prediction_outcome_id(world_scope_hash=sc.world_scope_hash,prediction_id=pid,outcome=o,resolved_at_ms=10000+i,outcome_observation_refs=rr)
    err=abs(score-(1000 if supported else 0))
    return PredictionOutcome(
        outcome_id=oid, scope=sc, prediction_id=pid, prediction_family="file-change", horizon_class="short",
        prediction_score_milli=score, outcome=o, resolved_at_ms=10000+i, outcome_observation_refs=rr,
        calibration_bucket=1000 if score==1000 else (score//100)*100, brier_component_millionths=err*err,
        outcome_sha256="0"*64,
    ).with_computed_hash()


def test_calibrated_probability_gate_stays_closed_then_opens_on_real_outcomes():
    rows=[]; idx=0
    for score,support_count in ((200,4),(500,10),(800,16)):
        for j in range(20):
            rows.append(make_prediction_outcome(idx, score, j<support_count)); idx+=1
    sc=scope()
    under=build_calibration_profile(tuple(rows[:30]), prediction_family="file-change", horizon_class="short", life_id=sc.life_id, world_scope_hash=sc.world_scope_hash)
    assert not under.gate_open
    assert calibrated_probability_milli(800, under) is None
    full=build_calibration_profile(tuple(rows), prediction_family="file-change", horizon_class="short", life_id=sc.life_id, world_scope_hash=sc.world_scope_hash)
    assert full.gate_open and full.expected_calibration_error_milli == 0
    estimate=calibrated_probability_milli(800, full)
    assert estimate is not None and estimate.calibrated_probability_milli == 800
    assert estimate.empirical_evidence_weight_milli == 0

def test_high_churn_100_same_key_events_remain_one_bounded_queue_item():
    budget = BudgetLedger(BudgetConfig(10000,10000,10000,10000,1000,1000,1000,1000))
    rhythm = RhythmPlane(config=RhythmConfig(debounce_ms=100), budget=budget, start_ms=0)
    telemetry = QueueTelemetry("SEMANTIC",100,10,10000,1000,10000,0,0,0,0,0)
    plan = derive_queue_control(telemetry, semantic_ratio_milli=1000)
    apply_queue_control(rhythm, plan)
    boundary = HardBoundary("life.A","a"*64,"b"*64,"SEMANTIC")
    dispositions=[]
    for i in range(100):
        event=RhythmEvent(f"e{i}","same",boundary,i*10,canonical_sha256({"i":i}),100)
        dispositions.append(rhythm.submit(WorkItem(event, semantic=True)).disposition)
    assert dispositions[0] == "ADMITTED"
    assert dispositions.count("COALESCED") == 99
    assert rhythm.telemetry("SEMANTIC", now_ms=1000).queue_depth == 1


def test_zero_gain_backoff_caps_and_positive_gain_resets():
    many=tuple(InquiryGainObservation.from_outcome(make_outcome(i,gain=0),family_key="x") for i in range(10))
    sc=scope()
    state=derive_inquiry_backoff(many,family_key="x",life_id=sc.life_id,world_scope_hash=sc.world_scope_hash,now_ms=19_500)
    assert state.backoff_until_ms-many[-1].closed_at_ms <= 3_600_000
    reset=many+(InquiryGainObservation.from_outcome(make_outcome(11,gain=500),family_key="x"),)
    clear=derive_inquiry_backoff(reset,family_key="x",life_id=sc.life_id,world_scope_hash=sc.world_scope_hash,now_ms=25_000)
    assert clear.consecutive_zero_gain == 0 and clear.backoff_remaining_ms == 0


def test_c4_is_not_rewritten_by_dynamic_damping():
    sc=scope()
    decision=damp_cognition_level(current_level="C4",life_id=sc.life_id,world_scope_hash=sc.world_scope_hash,recent_reports=(TimedStabilityReport(sc.life_id,sc.world_scope_hash,_report("C0"),1000),),last_changed_at_ms=0,now_ms=100_000)
    assert decision.resulting_level == "C4" and decision.reason_code == "COGNITION_C4_RULES_UNCHANGED"


def test_calibration_gate_rejects_well_sampled_but_badly_calibrated_history():
    rows=tuple(make_prediction_outcome(i,800,supported=(i%2==0)) for i in range(80))
    sc=scope()
    profile=build_calibration_profile(rows,prediction_family="file-change",horizon_class="short",life_id=sc.life_id,world_scope_hash=sc.world_scope_hash)
    assert profile.binary_sample_count == 80
    assert profile.expected_calibration_error_milli > 100
    assert not profile.gate_open
    assert calibrated_probability_milli(800,profile) is None


def test_semantic_queue_overload_is_deferred_before_base_admission():
    class Spy:
        rhythm=None
        calls=0
        def admit(self,**kwargs): self.calls+=1; raise AssertionError
    snap=SemanticThrottleSnapshot(QueueTelemetry("SEMANTIC",100,10,5000,1000,5000,0,0,0,0,10),stale_hazard_milli=100)
    spy=Spy(); gate=TelemetrySemanticAdmissionController(base=spy,snapshot_provider=lambda:snap)
    out=gate.admit()
    assert not out.admitted and out.reason_code=="SEMANTIC_QUEUE_OVERLOAD" and spy.calls==0
