"""P16 native proactive initiative projection and deterministic hard gates.

This module is deliberately pure: it owns no persistence, scheduler, transport,
or model. The Life runtime supplies a bounded projection of already-authoritative
facts; the model may propose an initiative, but this module recomputes every
score and decides whether speaking is admissible.

Epistemic invariant:
    missing source != no change
    missing source == UNKNOWN
Unknown/stale/low-confidence evidence cannot authorize a proactive utterance.
"""
from __future__ import annotations

from typing import Any, Mapping

from .agency import compute_agency_score

_ALLOWED_KINDS = frozenset({"respond", "ask_user", "wait", "no_op"})
_SCORE_FIELDS = (
    "goal_gain_milli",
    "viability_gain_milli",
    "information_gain_milli",
    "relationship_value_milli",
    "resource_cost_milli",
    "expected_harm_milli",
    "uncertainty_penalty_milli",
    "irreversibility_penalty_milli",
)
_WORLD_AUTHORITY = "world_understanding_committed"


def _strict_int(value: object, *, default: int = 0, low: int = 0, high: int = 1000) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(high, max(low, value))


def _strict_nonnegative_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return default
    return value


def _strict_bounded_int(value: object, *, default: int, low: int, high: int) -> tuple[int, bool]:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return default, False
    return value, True


def _hour_in_window(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def normalize_observations(
    observations: object,
    *,
    now_ms: int,
    stale_after_ms: int = 24 * 60 * 60 * 1000,
    future_skew_ms: int = 5 * 60 * 1000,
) -> tuple[dict[str, Any], ...]:
    """Normalize evidence into explicit KNOWN/STALE/UNKNOWN epistemic states.

    Timestamps materially ahead of the decision clock fail closed to UNKNOWN.
    A small configured skew is tolerated for distributed-clock jitter only.
    """

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_rows = observations if isinstance(observations, (list, tuple)) else ()
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        source_ref = str(raw.get("source_ref") or "").strip()
        if not source_ref or source_ref in seen:
            continue
        seen.add(source_ref)
        observed_at_ms = _strict_nonnegative_int(raw.get("observed_at_ms"))
        confidence_milli = _strict_int(raw.get("confidence_milli"), default=0)
        future_invalid = bool(observed_at_ms and observed_at_ms > now_ms + max(0, future_skew_ms))
        effective_observed_at_ms = min(observed_at_ms, now_ms) if observed_at_ms else 0
        age_ms = now_ms - effective_observed_at_ms if effective_observed_at_ms else None
        explicit_state = str(raw.get("epistemic_state") or "").strip().upper()
        if explicit_state == "UNKNOWN" or observed_at_ms <= 0 or future_invalid:
            state = "UNKNOWN"
        elif explicit_state == "STALE" or (age_ms is not None and age_ms > stale_after_ms):
            state = "STALE"
        else:
            state = "KNOWN"
        rows.append(
            {
                "source_ref": source_ref,
                "observed_at_ms": observed_at_ms,
                "age_ms": age_ms,
                "confidence_milli": confidence_milli,
                "epistemic_state": state,
                "timestamp_state": "FUTURE_INVALID" if future_invalid else "VALID",
                "kind": str(raw.get("kind") or "fact")[:80],
                "authority": str(raw.get("authority") or "")[:120],
                "summary": str(raw.get("summary") or "")[:1200],
            }
        )
    return tuple(rows)


def _decision(
    *,
    kind: str,
    reason_code: str,
    allowed: bool,
    score: Mapping[str, Any] | None = None,
    evidence_refs: tuple[str, ...] = (),
    expression_intent: str = "",
    topic: str = "",
) -> dict[str, Any]:
    return {
        "schema": "tiangong.life.proactive-decision.v1",
        "candidate_kind": kind,
        "allowed": bool(allowed),
        "reason_code": str(reason_code),
        "score": dict(score or {}),
        "evidence_refs": list(evidence_refs),
        "expression_intent": str(expression_intent or "")[:2000],
        "topic": str(topic or "")[:240],
    }


def evaluate_proactive_candidate(
    proposal: object,
    *,
    context: Mapping[str, Any],
    settings: Mapping[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """Recompute one model proposal and apply deterministic proactive gates.

    The model cannot set delivery policy, freshness, quotas, timezone, or final
    utility. Only evidence already present in ``context.observations`` can
    support speech, and World evidence additionally requires committed WU
    authority.
    """

    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("proactive decision time is invalid")
    if not isinstance(proposal, Mapping):
        return _decision(kind="no_op", reason_code="life.proactive.proposal_invalid", allowed=False)

    kind = str(proposal.get("candidate_kind") or "no_op").strip().casefold()
    if kind not in _ALLOWED_KINDS:
        return _decision(kind="no_op", reason_code="life.proactive.kind_invalid", allowed=False)
    if kind in {"wait", "no_op"}:
        return _decision(
            kind=kind,
            reason_code=f"life.proactive.model_{kind}",
            allowed=False,
            expression_intent=str(proposal.get("expression_intent") or ""),
            topic=str(proposal.get("topic") or ""),
        )

    if settings.get("proactive_enabled") is not True:
        return _decision(kind="no_op", reason_code="life.proactive.disabled", allowed=False)
    mode = str(settings.get("proactive_mode") or "shadow").strip().casefold()
    if mode not in {"shadow", "live"}:
        return _decision(kind="no_op", reason_code="life.proactive.mode_invalid", allowed=False)

    future_skew_seconds = _strict_nonnegative_int(
        settings.get("proactive_max_future_skew_seconds"), default=300
    )
    future_skew_ms = future_skew_seconds * 1000

    timezone_offset_minutes, timezone_valid = _strict_bounded_int(
        settings.get("proactive_timezone_offset_minutes", 0),
        default=0,
        low=-14 * 60,
        high=14 * 60,
    )
    if settings.get("proactive_dnd_enabled") is True and not timezone_valid:
        return _decision(kind="no_op", reason_code="life.proactive.timezone_invalid", allowed=False)
    local_hour = ((now_ms + timezone_offset_minutes * 60_000) // 3_600_000) % 24
    if settings.get("proactive_dnd_enabled") is True:
        start = _strict_int(settings.get("proactive_dnd_start_hour"), default=22, high=23)
        end = _strict_int(settings.get("proactive_dnd_end_hour"), default=7, high=23)
        if _hour_in_window(int(local_hour), start, end):
            return _decision(kind="no_op", reason_code="life.proactive.dnd", allowed=False)

    last_user_ms = _strict_nonnegative_int(context.get("last_user_activity_at_ms"))
    if last_user_ms > now_ms + future_skew_ms:
        return _decision(
            kind="no_op",
            reason_code="life.proactive.user_activity_clock_invalid",
            allowed=False,
        )
    effective_last_user_ms = min(last_user_ms, now_ms) if last_user_ms else 0
    active_window_ms = _strict_nonnegative_int(
        settings.get("proactive_user_active_window_seconds"), default=180
    ) * 1000
    if (
        settings.get("proactive_respect_user_activity") is not False
        and effective_last_user_ms
        and active_window_ms
        and now_ms - effective_last_user_ms < active_window_ms
    ):
        return _decision(kind="no_op", reason_code="life.proactive.user_active", allowed=False)

    raw_deliveries = context.get("recent_delivery_times_ms") or []
    if any(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value > now_ms + future_skew_ms
        for value in raw_deliveries
    ):
        return _decision(
            kind="no_op",
            reason_code="life.proactive.delivery_clock_invalid",
            allowed=False,
        )
    deliveries = [
        min(value, now_ms)
        for value in raw_deliveries
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    last_delivery_ms = max(deliveries, default=0)
    min_interval_ms = _strict_nonnegative_int(
        settings.get("proactive_min_interval_seconds"), default=3600
    ) * 1000
    if last_delivery_ms and min_interval_ms and now_ms - last_delivery_ms < min_interval_ms:
        return _decision(kind="no_op", reason_code="life.proactive.minimum_interval", allowed=False)

    hourly_limit = _strict_nonnegative_int(settings.get("proactive_max_messages_per_hour"), default=2)
    daily_limit = _strict_nonnegative_int(settings.get("proactive_max_messages_per_day"), default=6)
    if hourly_limit <= 0 or sum(1 for value in deliveries if now_ms - value < 3_600_000) >= hourly_limit:
        return _decision(kind="no_op", reason_code="life.proactive.hourly_limit", allowed=False)
    if daily_limit <= 0 or sum(1 for value in deliveries if now_ms - value < 86_400_000) >= daily_limit:
        return _decision(kind="no_op", reason_code="life.proactive.daily_limit", allowed=False)

    stale_after_ms = _strict_nonnegative_int(
        settings.get("proactive_evidence_stale_after_seconds"), default=86_400
    ) * 1000
    observations = normalize_observations(
        context.get("observations"),
        now_ms=now_ms,
        stale_after_ms=stale_after_ms,
        future_skew_ms=future_skew_ms,
    )
    by_ref = {row["source_ref"]: row for row in observations}
    requested_refs: list[str] = []
    for value in proposal.get("evidence_refs") or []:
        ref = str(value or "").strip()
        if ref and ref not in requested_refs:
            requested_refs.append(ref)
    if not requested_refs:
        return _decision(kind="no_op", reason_code="life.proactive.evidence_missing", allowed=False)
    if any(ref not in by_ref for ref in requested_refs):
        return _decision(kind="no_op", reason_code="life.proactive.evidence_unknown", allowed=False)

    selected = [by_ref[ref] for ref in requested_refs]
    if any(row["epistemic_state"] == "UNKNOWN" for row in selected):
        return _decision(kind="no_op", reason_code="life.proactive.evidence_unknown", allowed=False)
    if any(row["epistemic_state"] == "STALE" for row in selected):
        return _decision(kind="no_op", reason_code="life.proactive.evidence_stale", allowed=False)
    if any(
        str(row.get("kind") or "").startswith("world:")
        and str(row.get("authority") or "") != _WORLD_AUTHORITY
        for row in selected
    ):
        return _decision(
            kind="no_op",
            reason_code="life.proactive.world_authority_invalid",
            allowed=False,
        )
    min_confidence = _strict_int(
        settings.get("proactive_min_evidence_confidence_milli"), default=350
    )
    if min(row["confidence_milli"] for row in selected) < min_confidence:
        return _decision(kind="no_op", reason_code="life.proactive.evidence_low_confidence", allowed=False)

    raw_score = proposal.get("score") if isinstance(proposal.get("score"), Mapping) else proposal
    score_inputs = {field: _strict_int(raw_score.get(field), default=0) for field in _SCORE_FIELDS}
    score_inputs["uncertainty_penalty_milli"] = max(
        score_inputs["uncertainty_penalty_milli"],
        1000 - min(row["confidence_milli"] for row in selected),
    )
    score = compute_agency_score(**score_inputs).model_dump(mode="json")
    threshold = _strict_nonnegative_int(settings.get("proactive_min_utility_lcb_milli"), default=120)
    margin = _strict_nonnegative_int(settings.get("proactive_min_margin_milli"), default=80)
    required = max(threshold, margin)
    if int(score.get("utility_lcb_milli") or 0) < required:
        return _decision(
            kind="no_op",
            reason_code="life.proactive.utility_below_threshold",
            allowed=False,
            score=score,
            evidence_refs=tuple(requested_refs),
        )

    expression_intent = str(proposal.get("expression_intent") or "").strip()
    if not expression_intent:
        return _decision(
            kind="no_op",
            reason_code="life.proactive.expression_intent_missing",
            allowed=False,
            score=score,
            evidence_refs=tuple(requested_refs),
        )

    return _decision(
        kind=kind,
        reason_code=(
            "life.proactive.shadow_eligible"
            if mode == "shadow"
            else "life.proactive.live_eligible"
        ),
        allowed=True,
        score=score,
        evidence_refs=tuple(requested_refs),
        expression_intent=expression_intent,
        topic=str(proposal.get("topic") or ""),
    )
