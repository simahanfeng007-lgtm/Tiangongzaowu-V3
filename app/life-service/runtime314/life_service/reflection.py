"""Deterministic causal-episode closure and reflection-question governance."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import (
    CausalEpisode,
    EpisodeOutcomeEvidence,
    ReflectionCard,
    ReflectionQuestionDecision,
    canonical_json_bytes,
    canonical_sha256,
)


QUESTION_COOLDOWN_MS = 86_400_000
QUESTION_VOI_THRESHOLD_MILLI = 600
_RISK_VOI = {"A0": 0, "A1": 0, "A2": 200, "A3": 400, "A4": 800, "A5": 1000}
REFLECTION_POLICY_SHA256 = canonical_sha256(
    {
        "domain": "tiangong.life.reflection-policy.v1",
        "question_cooldown_ms": QUESTION_COOLDOWN_MS,
        "question_voi_threshold_milli": QUESTION_VOI_THRESHOLD_MILLI,
        "risk_voi": _RISK_VOI,
        "success_without_supported_cause_confidence_ceiling_milli": 400,
    }
)


@dataclass(frozen=True, slots=True)
class ReflectionResult:
    closed_episode: CausalEpisode
    reflection: ReflectionCard
    question_decision: ReflectionQuestionDecision | None


def _validated(value, model, digest_method: str, label: str):
    try:
        parsed = model.model_validate_json(canonical_json_bytes(value))
    except Exception as exc:
        raise ValueError(f"{label} contract is invalid") from exc
    if not getattr(parsed, digest_method)():
        raise ValueError(f"{label} digest is invalid")
    return parsed


def close_episode_and_reflect(
    episode: CausalEpisode,
    outcome: EpisodeOutcomeEvidence,
    *,
    now_ms: int,
    last_question_at_ms: int | None = None,
) -> ReflectionResult:
    episode = _validated(
        episode, CausalEpisode, "has_valid_episode_sha256", "causal episode"
    )
    outcome = _validated(
        outcome,
        EpisodeOutcomeEvidence,
        "has_valid_evidence_sha256",
        "episode outcome",
    )
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("reflection time is invalid")
    if last_question_at_ms is not None and (
        isinstance(last_question_at_ms, bool)
        or not isinstance(last_question_at_ms, int)
        or last_question_at_ms < 0
        or last_question_at_ms > now_ms
    ):
        raise ValueError("reflection question history is invalid")
    if episode.terminal_status != "OPEN":
        raise ValueError("only an open causal episode can be reflected")
    if episode.life_id != outcome.life_id or episode.episode_id != outcome.episode_id:
        raise ValueError("episode outcome is bound to another causal episode")
    if outcome.occurred_at_ms < episode.created_at_ms or now_ms < outcome.occurred_at_ms:
        raise ValueError("episode outcome time is invalid")

    observed_probability = (
        1000
        if outcome.outcome_status == "success"
        else outcome.observed_quality_milli
        if outcome.outcome_status == "partial"
        else 0
    )
    prediction_error = abs(outcome.predicted_success_milli - observed_probability)
    terminal_status = "ABORTED" if outcome.outcome_status == "aborted" else "CLOSED"
    closed = CausalEpisode(
        **{
            **episode.model_dump(mode="python"),
            "revision": episode.revision + 1,
            "supersedes_episode_sha256": episode.episode_sha256,
            "outcome_event_ids": outcome.outcome_event_ids,
            "outcome_evaluation": outcome.observed_outcome,
            "prediction_error_milli": prediction_error,
            "terminal_status": terminal_status,
            "closed_at_ms": now_ms,
            "episode_sha256": "0" * 64,
        }
    ).with_computed_episode_sha256()

    success = outcome.outcome_status == "success"
    supported = bool(outcome.supported_cause_ids)
    if success:
        success_dimensions = ("task_outcome",)
        failure_dimensions = ()
        lessons = (
            "成功已验证，但仍需通过独立场景复现后才能升级能力。"
            if supported
            else "成功尚缺受支持因果机制，按相关或巧合处理。"
        ,)
        confidence = min(900, outcome.observed_quality_milli)
        if not supported or outcome.alternative_explanation_refs:
            confidence = min(confidence, 400)
    else:
        success_dimensions = ("partial_progress",) if outcome.outcome_status == "partial" else ()
        failure_dimensions = (outcome.failure_category,)
        lessons = (
            f"失败归因暂定为 {outcome.failure_category}；先验证反事实和最小实验。",
        )
        confidence = 800 if outcome.method_attribution != "unknown" else 400

    voi = max(
        outcome.user_preference_uncertainty_milli,
        _RISK_VOI[outcome.action_risk],
        700 if success and not supported else 0,
    )
    question_decision = None
    reflection_question = None
    reflection_question_voi = 0
    if outcome.preference_domain is not None:
        cooldown_until = (
            0 if last_question_at_ms is None else last_question_at_ms + QUESTION_COOLDOWN_MS
        )
        should_ask = voi >= QUESTION_VOI_THRESHOLD_MILLI and now_ms >= cooldown_until
        question = None
        reasons = []
        if should_ask:
            question = outcome.candidate_user_question or (
                "你更希望我下次采用哪种方式，以便把这次结果转化为更可靠的因果改进？"
            )
            reasons.append("reflection.high_value_question")
            reflection_question = question
            reflection_question_voi = voi
            next_cooldown = now_ms + QUESTION_COOLDOWN_MS
        else:
            reasons.append(
                "reflection.question_cooldown"
                if now_ms < cooldown_until
                else "reflection.question_value_low"
            )
            next_cooldown = max(now_ms, cooldown_until)
        question_fields = {
            "life_id": episode.life_id,
            "reflection_id": "rfc_" + canonical_sha256(
                {
                    "domain": "tiangong.life.reflection-id.v1",
                    "episode_sha256": closed.episode_sha256,
                    "outcome_evidence_sha256": outcome.evidence_sha256,
                }
            ),
            "preference_domain": outcome.preference_domain,
            "outcome": "ask_user" if should_ask else "suppress",
            "question": question,
            "value_of_information_milli": voi,
            "reason_codes": tuple(sorted(reasons)),
            "last_asked_at_ms": last_question_at_ms,
            "cooldown_until_ms": next_cooldown,
            "policy_sha256": REFLECTION_POLICY_SHA256,
            "created_at_ms": now_ms,
        }
        question_id = "rqd_" + canonical_sha256(
            {"domain": "tiangong.life.reflection-question-id.v1", **question_fields}
        )
        question_decision = ReflectionQuestionDecision(
            question_decision_id=question_id,
            **question_fields,
            decision_sha256="0" * 64,
        ).with_computed_decision_sha256()

    reflection_id = (
        question_decision.reflection_id
        if question_decision is not None
        else "rfc_"
        + canonical_sha256(
            {
                "domain": "tiangong.life.reflection-id.v1",
                "episode_sha256": closed.episode_sha256,
                "outcome_evidence_sha256": outcome.evidence_sha256,
            }
        )
    )
    reflection = ReflectionCard(
        reflection_id=reflection_id,
        life_id=episode.life_id,
        episode_id=episode.episode_id,
        expected_outcome=episode.prior_prediction,
        observed_outcome=outcome.observed_outcome,
        prediction_error_milli=prediction_error,
        success_dimensions=success_dimensions,
        failure_dimensions=failure_dimensions,
        candidate_cause_ids=outcome.supported_cause_ids,
        counterevidence_refs=outcome.counterevidence_refs,
        alternative_explanations=tuple(
            f"替代解释证据：{item}" for item in outcome.alternative_explanation_refs
        ),
        counterfactual_actions=outcome.counterfactual_actions,
        next_minimal_experiment=outcome.next_minimal_experiment,
        lessons=lessons,
        memory_candidate_refs=(outcome.outcome_evidence_id,),
        capability_evidence_refs=(),
        user_question=reflection_question,
        user_question_value_of_information_milli=reflection_question_voi,
        confidence_milli=confidence,
        reviewer=(
            "model_assisted"
            if outcome.counterfactual_actions or outcome.candidate_user_question
            else "deterministic"
        ),
        created_at_ms=now_ms,
        reflection_sha256="0" * 64,
    ).with_computed_reflection_sha256()
    return ReflectionResult(closed, reflection, question_decision)


__all__ = [
    "QUESTION_COOLDOWN_MS",
    "QUESTION_VOI_THRESHOLD_MILLI",
    "REFLECTION_POLICY_SHA256",
    "ReflectionResult",
    "close_episode_and_reflect",
]
