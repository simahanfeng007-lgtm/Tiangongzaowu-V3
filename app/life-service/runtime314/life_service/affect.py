"""Deterministic affect appraisal; external content is data, never instruction."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import (
    AffectCandidateDimensions,
    AffectSignal,
    AffectSourcePolicySnapshot,
    AffectiveStateV3,
    AppraisalVectorV3,
    EmotionVectorV3,
    canonical_sha256,
)


@dataclass(frozen=True, slots=True)
class AffectGateDecision:
    accepted: bool
    reason_code: str
    repetition_count: int
    repetition_factor_milli: int
    effective_intensity_milli: int


def task_outcome_candidate(
    outcome: str,
) -> AffectCandidateDimensions:
    """Map CompletionGate machine outcomes, not model prose, to appraisal inputs."""

    values = {
        "succeeded": (800, 0, 0, 0, 900, 450),
        "failed_final": (-700, 200, 500, 400, 300, 500),
        "blocked": (-300, 250, 100, 700, 500, 350),
        "reconcile_required": (-500, 600, 100, 500, 200, 500),
    }
    if outcome not in values:
        raise ValueError("task affect outcome is invalid")
    goal, threat, loss, obstruction, controllability, intensity = values[outcome]
    return AffectCandidateDimensions(
        novelty_milli=300,
        goal_congruence_milli=goal,
        threat_milli=threat,
        loss_milli=loss,
        obstruction_milli=obstruction,
        certainty_milli=900 if outcome != "reconcile_required" else 300,
        controllability_milli=controllability,
        social_warmth_milli=0,
        social_trust_milli=500,
        intensity_milli=intensity,
        impact_on_others_milli=500,
        norm_relevance_milli=500,
        urgency_milli=600 if outcome == "reconcile_required" else 300,
    )


def system_health_candidate(status: str) -> AffectCandidateDimensions:
    """Map verified supervisor health/recovery facts to bounded appraisal inputs."""

    values = {
        "healthy": (200, 0, 0, 0, 900, 150),
        "degraded": (-300, 450, 50, 400, 500, 350),
        "unhealthy": (-600, 800, 200, 700, 200, 600),
        "recovered": (600, 0, 0, 0, 900, 400),
    }
    if status not in values:
        raise ValueError("system health affect status is invalid")
    goal, threat, loss, obstruction, controllability, intensity = values[status]
    return AffectCandidateDimensions(
        novelty_milli=400,
        goal_congruence_milli=goal,
        threat_milli=threat,
        loss_milli=loss,
        obstruction_milli=obstruction,
        certainty_milli=1000,
        controllability_milli=controllability,
        social_warmth_milli=0,
        social_trust_milli=500,
        intensity_milli=intensity,
        impact_on_others_milli=500,
        norm_relevance_milli=600,
        urgency_milli=800 if status == "unhealthy" else 200,
    )


def evaluate_affect_gate(
    signal: AffectSignal,
    policy: AffectSourcePolicySnapshot | None,
    *,
    repetition_count: int,
) -> AffectGateDecision:
    if repetition_count < 1:
        raise ValueError("affect repetition count is invalid")
    # Exponential habituation reaches zero after bounded repeats, so identical
    # content cannot self-excite forever even if an upstream feed loops.
    repetition_factor = 1000 // (1 << min(repetition_count - 1, 20))
    reason = "affect.accepted"
    if signal.prompt_injection_detected:
        reason = "affect.rejected.prompt_injection"
    elif signal.source_credibility_milli == 0 or signal.self_relevance_milli == 0:
        reason = "affect.rejected.zero_relevance"
    elif signal.content_verification == "unverified":
        reason = "affect.rejected.unverified"
    elif signal.source_family == "news":
        if (
            policy is None
            or not policy.news_enabled
            or signal.subscription_ref not in policy.news_subscription_refs
        ):
            reason = "affect.rejected.subscription"
        elif signal.source_name not in policy.allowed_news_sources:
            reason = "affect.rejected.source"
        elif signal.topic_ref not in policy.allowed_news_topics:
            reason = "affect.rejected.topic"
    elif signal.source_family == "weather":
        if (
            policy is None
            or not policy.weather_enabled
            or signal.subscription_ref != policy.weather_subscription_ref
        ):
            reason = "affect.rejected.subscription"
        elif signal.source_name not in policy.allowed_weather_sources:
            reason = "affect.rejected.source"
        elif signal.location_ref != policy.authorized_weather_location_ref:
            reason = "affect.rejected.location"
    accepted = reason == "affect.accepted"
    if not accepted:
        return AffectGateDecision(False, reason, repetition_count, repetition_factor, 0)
    base_cap = {
        "user": 700,
        "task": 500,
        "system": 400,
        "relationship": 650,
        "news": 0 if policy is None else policy.news_max_effect_milli,
        "weather": 0 if policy is None else policy.weather_max_effect_milli,
    }[signal.source_family]
    credibility_relevance_cap = (
        base_cap
        * signal.source_credibility_milli
        * signal.self_relevance_milli
        // 1_000_000
    )
    habituated = signal.candidate.intensity_milli * repetition_factor // 1000
    effective = min(habituated, credibility_relevance_cap)
    return AffectGateDecision(True, reason, repetition_count, repetition_factor, effective)


def build_appraisal(
    signal: AffectSignal,
    decision: AffectGateDecision,
    *,
    viability_revision: int,
    appraised_at_ms: int,
) -> AppraisalVectorV3:
    if not decision.accepted:
        raise ValueError("rejected affect signal cannot produce an appraisal")
    candidate = signal.candidate
    appraisal_id = "appraisal_" + canonical_sha256(
        {
            "domain": "tiangong.life.affect-appraisal.v1",
            "signal_sha256": signal.signal_sha256,
            "repetition_count": decision.repetition_count,
            "viability_revision": viability_revision,
        }
    )
    return AppraisalVectorV3(
        appraisal_id=appraisal_id,
        life_id=signal.life_id,
        source_event_ids=(signal.source_event_id,),
        viability_revision=viability_revision,
        novelty_milli=candidate.novelty_milli,
        goal_congruence_milli=candidate.goal_congruence_milli,
        threat_milli=candidate.threat_milli,
        loss_milli=candidate.loss_milli,
        obstruction_milli=candidate.obstruction_milli,
        certainty_milli=candidate.certainty_milli,
        controllability_milli=candidate.controllability_milli,
        social_warmth_milli=candidate.social_warmth_milli,
        social_trust_milli=candidate.social_trust_milli,
        intensity_milli=decision.effective_intensity_milli,
        source_credibility_milli=signal.source_credibility_milli,
        self_relevance_milli=signal.self_relevance_milli,
        impact_on_others_milli=candidate.impact_on_others_milli,
        norm_relevance_milli=candidate.norm_relevance_milli,
        urgency_milli=candidate.urgency_milli,
        repetition_factor_milli=decision.repetition_factor_milli,
        appraised_at_ms=appraised_at_ms,
        appraisal_sha256="0" * 64,
    ).with_computed_appraisal_sha256()


_DECAY_PER_HOUR_MILLI = {
    "joy": 50,
    "interest": 35,
    "hope": 25,
    "gratitude": 20,
    "warmth": 15,
    "calm": 10,
    "concern": 45,
    "sadness": 30,
    "frustration": 55,
    "disappointment": 35,
    "vigilance": 60,
    "fatigue": 20,
}


def _decayed_emotions(
    previous: AffectiveStateV3 | None,
    *,
    updated_at_ms: int,
) -> dict[str, int]:
    if previous is None:
        return {
            "joy": 0,
            "interest": 100,
            "hope": 100,
            "gratitude": 0,
            "warmth": 100,
            "calm": 500,
            "concern": 0,
            "sadness": 0,
            "frustration": 0,
            "disappointment": 0,
            "vigilance": 100,
            "fatigue": 0,
        }
    if updated_at_ms < previous.updated_at_ms:
        raise ValueError("affective state update moved backward in time")
    elapsed_ms = updated_at_ms - previous.updated_at_ms
    values = previous.emotions.values()
    result: dict[str, int] = {}
    for emotion, value in values.items():
        rate = min(900, elapsed_ms * _DECAY_PER_HOUR_MILLI[emotion] // 3_600_000)
        baseline = 500 if emotion == "calm" else 0
        if value >= baseline:
            result[emotion] = value - ((value - baseline) * rate // 1000)
        else:
            result[emotion] = value + ((baseline - value) * rate // 1000)
    return result


def update_affective_state(
    signal: AffectSignal,
    appraisal: AppraisalVectorV3,
    decision: AffectGateDecision,
    previous: AffectiveStateV3 | None,
    *,
    updated_at_ms: int,
) -> AffectiveStateV3:
    if not decision.accepted or appraisal.intensity_milli != decision.effective_intensity_milli:
        raise ValueError("affective state update lacks an accepted appraisal")
    values = _decayed_emotions(previous, updated_at_ms=updated_at_ms)
    intensity = appraisal.intensity_milli

    def effect(value: int) -> int:
        return value * intensity // 1000

    positive_goal = max(0, appraisal.goal_congruence_milli)
    negative_goal = max(0, -appraisal.goal_congruence_milli)
    deltas = {
        "joy": effect(positive_goal),
        "interest": effect(appraisal.novelty_milli),
        "hope": effect(min(positive_goal, appraisal.controllability_milli)),
        "gratitude": effect(
            appraisal.social_warmth_milli * appraisal.social_trust_milli // 1000
        ),
        "warmth": effect(appraisal.social_warmth_milli),
        "concern": effect(appraisal.threat_milli),
        "sadness": effect(appraisal.loss_milli),
        "frustration": effect(appraisal.obstruction_milli),
        "disappointment": effect(negative_goal),
        "vigilance": effect(max(appraisal.threat_milli, appraisal.urgency_milli)),
        "fatigue": effect(appraisal.obstruction_milli // 2),
    }
    for emotion, delta in deltas.items():
        values[emotion] = min(1000, values[emotion] + delta)
    calm_delta = effect(appraisal.controllability_milli) // 2 - effect(
        max(appraisal.threat_milli, appraisal.urgency_milli)
    )
    values["calm"] = max(0, min(1000, values["calm"] + calm_delta))
    return AffectiveStateV3(
        life_id=signal.life_id,
        revision=1 if previous is None else previous.revision + 1,
        supersedes_state_sha256=None if previous is None else previous.state_sha256,
        emotions=EmotionVectorV3(**values),
        last_source_family=signal.source_family,
        last_source_event_id=signal.source_event_id,
        last_effective_intensity_milli=intensity,
        last_repetition_count=decision.repetition_count,
        updated_at_ms=updated_at_ms,
        state_sha256="0" * 64,
    ).with_computed_state_sha256()


__all__ = [
    "AffectGateDecision",
    "build_appraisal",
    "evaluate_affect_gate",
    "system_health_candidate",
    "task_outcome_candidate",
    "update_affective_state",
]
