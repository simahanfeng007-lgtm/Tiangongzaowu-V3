from __future__ import annotations

from life_service.proactive_initiative import evaluate_proactive_candidate, normalize_observations


NOW = 1_800_000_000_000


def settings(**patch):
    base = {
        "proactive_enabled": True,
        "proactive_mode": "live",
        "proactive_min_interval_seconds": 3600,
        "proactive_max_messages_per_hour": 2,
        "proactive_max_messages_per_day": 6,
        "proactive_dnd_enabled": False,
        "proactive_dnd_start_hour": 22,
        "proactive_dnd_end_hour": 7,
        "proactive_respect_user_activity": True,
        "proactive_user_active_window_seconds": 180,
        "proactive_min_evidence_confidence_milli": 350,
        "proactive_evidence_stale_after_seconds": 86400,
        "proactive_min_utility_lcb_milli": 120,
        "proactive_min_margin_milli": 80,
    }
    base.update(patch)
    return base


def context(**patch):
    base = {
        "last_user_activity_at_ms": 0,
        "recent_delivery_times_ms": [],
        "observations": [
            {
                "source_ref": "life:event:goal-1",
                "observed_at_ms": NOW - 10_000,
                "confidence_milli": 900,
                "kind": "open_loop",
                "summary": "用户明确说今天要提交方案，任务仍未关闭。",
            }
        ],
    }
    base.update(patch)
    return base


def proposal(**patch):
    base = {
        "candidate_kind": "respond",
        "topic": "方案提交",
        "expression_intent": "提醒用户方案仍未提交，并询问是否需要我继续处理。",
        "evidence_refs": ["life:event:goal-1"],
        "score": {
            "goal_gain_milli": 450,
            "viability_gain_milli": 0,
            "information_gain_milli": 120,
            "relationship_value_milli": 160,
            "resource_cost_milli": 30,
            "expected_harm_milli": 20,
            "uncertainty_penalty_milli": 30,
            "irreversibility_penalty_milli": 0,
        },
    }
    base.update(patch)
    return base


def test_missing_source_is_unknown_not_no_change():
    result = evaluate_proactive_candidate(
        proposal(evidence_refs=["world:missing"]),
        context=context(),
        settings=settings(),
        now_ms=NOW,
    )
    assert result["allowed"] is False
    assert result["candidate_kind"] == "no_op"
    assert result["reason_code"] == "life.proactive.evidence_unknown"


def test_explicit_unknown_source_cannot_authorize_speech():
    observations = [{
        "source_ref": "world:customer-approval",
        "observed_at_ms": NOW - 10_000,
        "confidence_milli": 900,
        "epistemic_state": "UNKNOWN",
    }]
    result = evaluate_proactive_candidate(
        proposal(evidence_refs=["world:customer-approval"]),
        context=context(observations=observations),
        settings=settings(),
        now_ms=NOW,
    )
    assert result["reason_code"] == "life.proactive.evidence_unknown"


def test_stale_or_low_confidence_evidence_is_suppressed():
    stale = [{
        "source_ref": "life:event:goal-1",
        "observed_at_ms": NOW - 90_000_000,
        "confidence_milli": 900,
    }]
    result = evaluate_proactive_candidate(
        proposal(), context=context(observations=stale), settings=settings(), now_ms=NOW
    )
    assert result["reason_code"] == "life.proactive.evidence_stale"

    weak = [{
        "source_ref": "life:event:goal-1",
        "observed_at_ms": NOW - 1_000,
        "confidence_milli": 200,
    }]
    result = evaluate_proactive_candidate(
        proposal(), context=context(observations=weak), settings=settings(), now_ms=NOW
    )
    assert result["reason_code"] == "life.proactive.evidence_low_confidence"


def test_user_activity_and_frequency_gates_precede_model_score():
    result = evaluate_proactive_candidate(
        proposal(),
        context=context(last_user_activity_at_ms=NOW - 30_000),
        settings=settings(),
        now_ms=NOW,
    )
    assert result["reason_code"] == "life.proactive.user_active"

    result = evaluate_proactive_candidate(
        proposal(),
        context=context(recent_delivery_times_ms=[NOW - 60_000]),
        settings=settings(),
        now_ms=NOW,
    )
    assert result["reason_code"] == "life.proactive.minimum_interval"

    result = evaluate_proactive_candidate(
        proposal(),
        context=context(recent_delivery_times_ms=[NOW - 3_700_000, NOW - 3_800_000]),
        settings=settings(proactive_min_interval_seconds=0, proactive_max_messages_per_day=2),
        now_ms=NOW,
    )
    assert result["reason_code"] == "life.proactive.daily_limit"


def test_model_cannot_fake_utility_lcb():
    low = proposal(score={
        "goal_gain_milli": 10,
        "viability_gain_milli": 0,
        "information_gain_milli": 0,
        "relationship_value_milli": 0,
        "resource_cost_milli": 0,
        "expected_harm_milli": 0,
        "uncertainty_penalty_milli": 0,
        "irreversibility_penalty_milli": 0,
        "utility_lcb_milli": 999999,
    })
    result = evaluate_proactive_candidate(low, context=context(), settings=settings(), now_ms=NOW)
    assert result["reason_code"] == "life.proactive.utility_below_threshold"
    assert result["score"]["utility_lcb_milli"] < 120


def test_evidence_confidence_sets_uncertainty_floor():
    observations = [{
        "source_ref": "life:event:goal-1",
        "observed_at_ms": NOW - 1_000,
        "confidence_milli": 400,
    }]
    result = evaluate_proactive_candidate(
        proposal(), context=context(observations=observations), settings=settings(), now_ms=NOW
    )
    assert result["score"]["uncertainty_penalty_milli"] == 600


def test_live_and_shadow_eligibility_are_distinct_but_both_recomputed():
    live = evaluate_proactive_candidate(proposal(), context=context(), settings=settings(), now_ms=NOW)
    assert live["allowed"] is True
    assert live["candidate_kind"] == "respond"
    assert live["reason_code"] == "life.proactive.live_eligible"

    shadow = evaluate_proactive_candidate(
        proposal(), context=context(), settings=settings(proactive_mode="shadow"), now_ms=NOW
    )
    assert shadow["allowed"] is True
    assert shadow["reason_code"] == "life.proactive.shadow_eligible"


def test_wait_and_noop_never_enqueue_by_themselves():
    for kind in ("wait", "no_op"):
        result = evaluate_proactive_candidate(
            proposal(candidate_kind=kind), context=context(), settings=settings(), now_ms=NOW
        )
        assert result["allowed"] is False
        assert result["candidate_kind"] == kind
        assert result["reason_code"] == f"life.proactive.model_{kind}"


def test_normalize_observations_fails_closed_without_timestamp():
    rows = normalize_observations(
        [{"source_ref": "world:x", "confidence_milli": 1000}], now_ms=NOW
    )
    assert rows[0]["epistemic_state"] == "UNKNOWN"



def test_future_evidence_timestamp_fails_closed():
    rows = normalize_observations(
        [{
            "source_ref": "memory:future",
            "observed_at_ms": NOW + 301_000,
            "confidence_milli": 1000,
        }],
        now_ms=NOW,
        future_skew_ms=300_000,
    )
    assert rows[0]["epistemic_state"] == "UNKNOWN"
    assert rows[0]["timestamp_state"] == "FUTURE_INVALID"


def test_dnd_uses_explicit_timezone_not_host_timezone():
    # NOW is 08:00 UTC. +08:00 projects to 16:00, which is inside 15:00-17:00 DND.
    result = evaluate_proactive_candidate(
        proposal(),
        context=context(),
        settings=settings(
            proactive_dnd_enabled=True,
            proactive_dnd_start_hour=15,
            proactive_dnd_end_hour=17,
            proactive_timezone_offset_minutes=480,
        ),
        now_ms=NOW,
    )
    assert result["reason_code"] == "life.proactive.dnd"

    invalid = evaluate_proactive_candidate(
        proposal(),
        context=context(),
        settings=settings(proactive_dnd_enabled=True, proactive_timezone_offset_minutes="+08:00"),
        now_ms=NOW,
    )
    assert invalid["reason_code"] == "life.proactive.timezone_invalid"


def test_future_activity_and_delivery_clocks_fail_closed():
    activity = evaluate_proactive_candidate(
        proposal(),
        context=context(last_user_activity_at_ms=NOW + 301_000),
        settings=settings(),
        now_ms=NOW,
    )
    assert activity["reason_code"] == "life.proactive.user_activity_clock_invalid"

    delivery = evaluate_proactive_candidate(
        proposal(),
        context=context(recent_delivery_times_ms=[NOW + 301_000]),
        settings=settings(),
        now_ms=NOW,
    )
    assert delivery["reason_code"] == "life.proactive.delivery_clock_invalid"


def test_world_evidence_requires_committed_world_authority():
    world = [{
        "source_ref": "world:repo:frame-1",
        "observed_at_ms": NOW - 1_000,
        "confidence_milli": 1000,
        "kind": "world:repository_evidence",
        "authority": "model_claim",
        "summary": "repo changed",
    }]
    blocked = evaluate_proactive_candidate(
        proposal(evidence_refs=["world:repo:frame-1"]),
        context=context(observations=world),
        settings=settings(),
        now_ms=NOW,
    )
    assert blocked["reason_code"] == "life.proactive.world_authority_invalid"

    world[0]["authority"] = "world_understanding_committed"
    allowed = evaluate_proactive_candidate(
        proposal(evidence_refs=["world:repo:frame-1"]),
        context=context(observations=world),
        settings=settings(),
        now_ms=NOW,
    )
    assert allowed["allowed"] is True
