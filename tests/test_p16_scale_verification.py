"""P16 scale verification: the framework holds at the full 150-round size.

A CONTROLLED mechanism run — every round comes from an injected runner,
never a real model. The value is scale: 150 rounds exercise the
continuity checker, the stop rules, the observation hash chain and the
injection harness at the exact size the frozen plan demands, proving the
machinery has no off-by-one, no gap tolerance and no silent recovery
BEFORE real credentials arrive. Every artifact says is_real_execution
false; this never counts toward acceptance.
"""
from __future__ import annotations

import json

import pytest

from total_gateway.p16_injection import (
    InjectionError, InjectionHarness, InjectionRule,
)
from total_gateway.p16_long_horizon import (
    RoundObservation, check_continuity, drive, evaluate_stop,
)

FROZEN_POINTS = (
    "BEFORE_PLANNING", "MODEL_RESPONSE", "REGISTRATION",
    "POST_TICKET_PRE_EFFECT", "POST_SIDE_EFFECT_START",
    "AROUND_FACT_WRITE", "DURING_VERIFICATION", "AROUND_DELIVERY",
)


def _obs(index, **updates):
    base = dict(
        round_index=index, request_id=f"req_{index:064x}",
        run_id=f"run_{index:064x}", generation=1,
        active_path="controlled_composition", outcome="completed",
        completion_decision_sha256=f"{index:064x}")
    base.update(updates)
    return RoundObservation(**base)


def test_full_150_round_clean_run_is_continuous():
    """150 clean rounds: zero gaps, zero duplicates, fully continuous."""
    result = drive(lambda i: _obs(i), 150)
    assert result.rounds_observed == 150
    assert result.stop_reason is None
    assert result.continuity.continuous is True
    assert result.continuity.gaps == ()
    assert result.continuity.duplicates == ()
    assert result.continuity.out_of_order == ()
    artifact = result.artifact()
    assert artifact["rounds_observed"] == 150
    assert artifact["is_real_execution"] is False


def test_150_round_observation_hash_chain_is_unique():
    """Every observation payload hashes uniquely across the full run."""
    observations = [_obs(i) for i in range(1, 151)]
    digests = {o.observation_sha256() for o in observations}
    assert len(digests) == 150


def test_gap_at_round_100_breaks_continuity_at_scale():
    """A single missing round is caught regardless of run length."""
    rows = [_obs(i) for i in range(1, 151) if i != 100]
    report = check_continuity(rows)
    assert report.continuous is False
    assert report.gaps == (100,)


def test_stop_rule_fires_at_round_149_of_150():
    """A false completion at the second-to-last round still stops the run."""
    def runner(index):
        return _obs(index, false_completion=(index == 149))
    result = drive(runner, 150)
    assert result.stop_reason == "false_completion"
    assert result.rounds_observed == 149


def test_injection_at_every_frozen_point_in_one_run():
    """All 8 points fire across one 150-round run; continuity survives."""
    rules = tuple(
        InjectionRule(point=point, action="raise", rounds=(i,))
        for i, point in enumerate(FROZEN_POINTS, 1))
    harness = InjectionHarness(rules=rules)

    def runner(index):
        point = FROZEN_POINTS[index - 1] if index <= len(FROZEN_POINTS) \
            else None
        if point is not None:
            try:
                harness.at(point, index)
                outcome = "completed"
            except InjectionError:
                outcome = "failed"
        else:
            outcome = "completed"
        return RoundObservation(
            round_index=index, request_id=f"req_{index:064x}",
            run_id=f"run_{index:064x}", generation=1,
            active_path="controlled_composition", outcome=outcome,
            completion_decision_sha256=None if outcome == "failed"
            else f"{index:064x}",
            note="injected" if outcome == "failed" else "")

    result = drive(runner, 150)
    assert result.rounds_observed == 150
    assert result.continuity.continuous is True
    assert len(harness.log_payload()) == 8
    injected = [o for o in result.observations if o.outcome == "failed"]
    assert len(injected) == 8  # one per frozen point


def test_collector_disconnect_at_round_75_stops_immediately():
    """A collector drop at the halfway mark truncates the run honestly."""
    def runner(index):
        return _obs(index) if index < 75 else None
    result = drive(runner, 150)
    assert result.stop_reason == "collector_disconnected"
    assert result.rounds_observed == 74
    assert result.continuity.continuous is True  # 74 clean rounds, just short


def test_mixed_outcomes_preserve_ledger_order():
    """150 rounds with mixed outcomes keep their ledger positions."""
    def runner(index):
        if index % 10 == 0:
            return _obs(index, outcome="cancelled")
        if index % 7 == 0:
            return _obs(index, outcome="failed")
        return _obs(index)
    result = drive(runner, 150)
    assert result.rounds_observed == 150
    assert result.stop_reason is None
    assert result.continuity.continuous is True
    cancelled = sum(1 for o in result.observations if o.outcome == "cancelled")
    failed = sum(1 for o in result.observations if o.outcome == "failed")
    assert cancelled == 15  # 10, 20, ..., 150
    assert failed >= 18     # multiples of 7 not overlapping with cancelled
