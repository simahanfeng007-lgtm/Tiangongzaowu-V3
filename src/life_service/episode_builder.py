"""P8 反思链生产构建器：纯函数，无 I/O，不 import runtime。

任务/能力执行 →（事前）预测快照 + OPEN episode →（事后）结果证据 →
store.commit_episode_reflection 原子闭环反思。所有写入由 embedded_runtime
在 ``self._lock`` 内完成；本模块只负责构造合法契约对象。

诚实基线（决策 C）：生产路径没有因果假设写入口，所以成功证据
``supported_cause_ids=()`` → ``causal_support="correlation_only"``、
``eligible_success=False``——成功晋升留给未来假设管线，这是 P8 出口
条件的认识论分层，不是缺陷。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from contracts import (
    ActionImpact,
    CausalEpisode,
    EpisodeOutcomeEvidence,
    LifeEventEnvelope,
    canonical_sha256,
    derive_life_event_id,
)

from .capability_learning import _impact_floor


ZERO_SHA256 = "0" * 64
ZERO_SIGNATURE = "0" * 128
# 无历史时的确定性预测基线（决策 C：不做随机猜测）。
DEFAULT_PREDICTED_SUCCESS_MILLI = 700

# 风险档位 → 影响面毫值（保证 _impact_floor(impact) == risk_class）。
_RISK_SCOPE_MILLI = {
    "A0": 0,
    "A1": 100,
    "A2": 300,
    "A3": 500,
    "A4": 700,
    "A5": 900,
}


@dataclass(frozen=True, slots=True)
class PredictionSnapshot:
    """事前预测快照：先于执行落账，事后可审计防编造。"""

    predicted_success_milli: int
    basis: str  # "history" | "default"
    snapshot: dict[str, Any]
    snapshot_sha256: str
    prior_prediction: str  # 嵌入 OPEN episode 的可读 JSON 文本


def fingerprint(parts: Mapping[str, Any]) -> str:
    """上下文指纹：canonical 哈希（输入必须 canonical 安全，禁 float）。"""
    return canonical_sha256(dict(parts))


def build_prediction(
    *,
    basis_inputs: Mapping[str, Any],
    successes: int = 0,
    uses: int = 0,
    fallback_milli: int = DEFAULT_PREDICTED_SUCCESS_MILLI,
) -> PredictionSnapshot:
    """确定性预测：有历史用完成率（毫值整数），无历史回退基线。

    ``basis_inputs`` 记录预测依据（如 activity_id、历史样本数、健康档案
    摘要），进入快照哈希，事后不可伪造。
    """
    if uses > 0:
        predicted = max(0, min(1000, successes * 1000 // uses))
        basis = "history"
    else:
        predicted = max(0, min(1000, fallback_milli))
        basis = "default"
    snapshot = {
        "domain": "tiangong.life.prediction-snapshot.v1",
        "basis": basis,
        "predicted_success_milli": predicted,
        "sample_successes": int(successes),
        "sample_uses": int(uses),
        **{str(key): value for key, value in basis_inputs.items()},
    }
    snapshot_sha256 = canonical_sha256(snapshot)
    from contracts import canonical_json_bytes

    prior_prediction = canonical_json_bytes(snapshot).decode("utf-8")
    return PredictionSnapshot(
        predicted_success_milli=predicted,
        basis=basis,
        snapshot=snapshot,
        snapshot_sha256=snapshot_sha256,
        prior_prediction=prior_prediction,
    )


def build_life_event(
    *,
    life_id: str,
    sequence: int,
    writer_epoch: int,
    previous_event_hash: str | None,
    event_kind: str,
    content: Mapping[str, Any],
    occurred_at_ms: int,
    observed_at_ms: int,
    correlation_id: str,
    causation_id: str | None = None,
    signer_key_id: str,
    sign: Callable[[str], str],
    source_kind: str = "reflection",
    evidence_class: str = "reflection",
) -> LifeEventEnvelope:
    """构造链形生命事件：确定性 identity + 哈希链 + 外部签名回调。"""
    content_sha256 = canonical_sha256({"domain": "life.reflection.content.v1", "content": dict(content)})
    ingress_id = f"internal:{event_kind}:{sequence}"
    dedupe_key = canonical_sha256(
        {"dedupe": event_kind, "correlation_id": correlation_id, "life_id": life_id, "sequence": sequence}
    )
    unsigned = LifeEventEnvelope(
        event_id=derive_life_event_id(
            life_id=life_id,
            writer_epoch=writer_epoch,
            sequence=sequence,
            ingress_id=ingress_id,
        ),
        life_id=life_id,
        sequence=sequence,
        writer_epoch=writer_epoch,
        source_service="life_reflection_chain",
        source_kind=source_kind,
        event_kind=event_kind,
        occurred_at_ms=occurred_at_ms,
        observed_at_ms=max(occurred_at_ms, observed_at_ms),
        principal_ref=life_id,
        subject_refs=(life_id,),
        evidence_class=evidence_class,
        source_credibility_milli=1000,
        privacy_scope="system",
        content_object_id=f"obj:{content_sha256[:40]}",
        content_sha256=content_sha256,
        dedupe_key=dedupe_key,
        causation_id=causation_id,
        correlation_id=correlation_id,
        previous_event_hash=previous_event_hash,
        event_hash=ZERO_SHA256,
        signer_key_id=signer_key_id,
        signature=ZERO_SIGNATURE,
    ).with_computed_event_hash()
    return unsigned.model_copy(update={"signature": sign(unsigned.event_hash.encode("ascii"))})


def build_open_episode(
    *,
    life_id: str,
    trigger_event_ids: Sequence[str],
    context_state_hashes: Sequence[str],
    intention: str,
    prediction: PredictionSnapshot,
    candidate_action_ids: Sequence[str] = (),
    selected_action_id: str | None = None,
    created_at_ms: int,
) -> CausalEpisode:
    """构造 OPEN 事前 episode；identity 由触发事件+预测快照确定性推导。"""
    triggers = tuple(sorted(set(str(value) for value in trigger_event_ids)))
    contexts = tuple(sorted(set(str(value) for value in context_state_hashes)))
    if not triggers or not contexts:
        raise ValueError("open episode requires trigger events and context hashes")
    episode_id = "cep_" + canonical_sha256(
        {
            "domain": "tiangong.life.reflection-episode-id.v1",
            "life_id": life_id,
            "trigger_event_ids": triggers,
            "prediction_snapshot_sha256": prediction.snapshot_sha256,
            "intention": intention,
        }
    )
    return CausalEpisode(
        episode_id=episode_id,
        life_id=life_id,
        revision=1,
        supersedes_episode_sha256=None,
        trigger_event_ids=triggers,
        context_state_hashes=contexts,
        intention=intention,
        prior_prediction=prediction.prior_prediction,
        candidate_action_ids=tuple(sorted(set(str(value) for value in candidate_action_ids))),
        selected_action_id=selected_action_id,
        authorization_ref=None,
        mediator_event_ids=(),
        outcome_event_ids=(),
        outcome_evaluation=None,
        prediction_error_milli=None,
        terminal_status="OPEN",
        created_at_ms=created_at_ms,
        closed_at_ms=None,
        episode_sha256=ZERO_SHA256,
    ).with_computed_episode_sha256()


def build_outcome_evidence(
    *,
    life_id: str,
    episode_id: str,
    outcome_status: str,
    observed_outcome: str,
    observed_quality_milli: int,
    prediction: PredictionSnapshot,
    completion_decision_sha256: str,
    terminal_fact_hashes: Sequence[str],
    outcome_event_ids: Sequence[str],
    context_fingerprint_sha256: str,
    action_risk: str,
    failure_category: str | None = None,
    method_attribution: str = "capability",
    counterfactual_actions: Sequence[str] = (),
    next_minimal_experiment: str | None = None,
    occurred_at_ms: int,
) -> EpisodeOutcomeEvidence:
    """构造结果证据；失败时补反事实与最小实验模板（若未提供）。"""
    if outcome_status == "success":
        if failure_category is not None:
            raise ValueError("success outcome cannot carry a failure category")
    elif failure_category is None:
        failure_category = "stale_context" if outcome_status == "aborted" else "unknown"
    if outcome_status == "failure" and not counterfactual_actions:
        counterfactual_actions = ("复跑同一任务，先执行只读探针再决定是否继续。",)
        next_minimal_experiment = next_minimal_experiment or (
            "先执行只读探针确认环境与输入，再重试原任务。"
        )
    facts = tuple(sorted(set(str(value) for value in terminal_fact_hashes)))
    events = tuple(sorted(set(str(value) for value in outcome_event_ids)))
    if not facts or not events:
        raise ValueError("episode outcome requires terminal facts and outcome events")
    outcome_evidence_id = "oev_" + canonical_sha256(
        {
            "domain": "tiangong.life.outcome-evidence-id.v1",
            "life_id": life_id,
            "episode_id": episode_id,
            "outcome_status": outcome_status,
            "prediction_snapshot_sha256": prediction.snapshot_sha256,
            "occurred_at_ms": occurred_at_ms,
        }
    )
    return EpisodeOutcomeEvidence(
        outcome_evidence_id=outcome_evidence_id,
        life_id=life_id,
        episode_id=episode_id,
        outcome_status=outcome_status,
        observed_outcome=observed_outcome,
        observed_quality_milli=max(0, min(1000, observed_quality_milli)),
        predicted_success_milli=prediction.predicted_success_milli,
        prediction_snapshot_hash=prediction.snapshot_sha256,
        completion_decision_sha256=completion_decision_sha256,
        terminal_fact_hashes=facts,
        outcome_event_ids=events,
        failure_category=failure_category,
        method_attribution=method_attribution,
        supported_cause_ids=(),
        counterevidence_refs=(),
        alternative_explanation_refs=(),
        context_fingerprint_sha256=context_fingerprint_sha256,
        preference_domain=None,
        user_preference_uncertainty_milli=0,
        action_risk=action_risk,
        counterfactual_actions=tuple(counterfactual_actions),
        next_minimal_experiment=next_minimal_experiment,
        candidate_user_question=None,
        occurred_at_ms=occurred_at_ms,
        evidence_sha256=ZERO_SHA256,
    ).with_computed_evidence_sha256()


def build_action_impact(
    *,
    life_id: str,
    action_id: str,
    risk_class: str,
    source_event_ids: Sequence[str],
    created_at_ms: int,
) -> ActionImpact:
    """按风险档位构造影响面；保证 _impact_floor(impact) == risk_class。"""
    scope_milli = _RISK_SCOPE_MILLI.get(str(risk_class).upper())
    if scope_milli is None:
        raise ValueError(f"action impact risk class is invalid: {risk_class}")
    events = tuple(sorted(set(str(value) for value in source_event_ids)))
    if not events:
        raise ValueError("action impact requires source events")
    impact_id = "imp_" + canonical_sha256(
        {
            "domain": "tiangong.life.reflection-impact-id.v1",
            "life_id": life_id,
            "action_id": action_id,
            "risk_class": str(risk_class).upper(),
            "created_at_ms": created_at_ms,
        }
    )
    impact = ActionImpact(
        impact_id=impact_id,
        life_id=life_id,
        action_id=action_id,
        dynamic_risk=str(risk_class).upper(),
        affected_internal_nodes=(),
        touches_identity=False,
        touches_soul=False,
        touches_memory_keys=False,
        touches_policy=False,
        touches_core_code=False,
        workspace_scope_milli=scope_milli,
        external_recipient_count=0,
        credential_scope_milli=0,
        privacy_scope_milli=scope_milli,
        blast_radius_milli=scope_milli,
        irreversibility_milli=scope_milli,
        uncertainty_milli=scope_milli,
        rollback_proof_ref=None,
        estimated_resource_cost_milli=100,
        predicted_viability_deltas=(),
        source_event_ids=events,
        created_at_ms=created_at_ms,
        impact_sha256=ZERO_SHA256,
    ).with_computed_impact_sha256()
    if _impact_floor(impact) != str(risk_class).upper():
        raise ValueError("action impact floor disagrees with its risk class")
    return impact


# 异常类型 → 九类失败映射。运行时错误码（EmbeddedLifeError.reason 等）优先，
# 此表兜底。
_ERROR_CATEGORY_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("policy", "forbidden", "blocked", "not_allowed"), "policy_block"),
    (("permission", "unauthorized", "forbidden_resource"), "insufficient_permission"),
    (("timeout", "connection", "network", "unreachable", "oserror"), "environment_error"),
    (("input", "argument", "schema", "invalid_json", "decode"), "input_error"),
    (("stale", "expired", "conflict", "discontinuous"), "stale_context"),
    (("preference",), "user_preference_mismatch"),
    (("reasoning", "model", "generation"), "model_reasoning_error"),
    (("tool", "action", "executor"), "tool_error"),
)

_EXCEPTION_CATEGORIES: tuple[tuple[tuple[type[BaseException], ...], str], ...] = (
    ((PermissionError,), "insufficient_permission"),
    ((TimeoutError, ConnectionError, OSError), "environment_error"),
    ((ValueError, TypeError, KeyError), "input_error"),
)


def failure_category_from_error(error: BaseException | None) -> str:
    """按异常类型与错误文本映射九类失败；兜底 unknown。"""
    if error is None:
        return "unknown"
    for types, category in _EXCEPTION_CATEGORIES:
        if isinstance(error, types):
            return category
    text = f"{type(error).__name__} {error}".casefold()
    for needles, category in _ERROR_CATEGORY_PATTERNS:
        if any(needle in text for needle in needles):
            return category
    return "unknown"


def failure_category_from_step_error(step: Mapping[str, Any]) -> str:
    """能力步骤失败的九类映射；缺错误信息时按工具失败处理。"""
    for key in ("error_code", "reason_code", "error"):
        text = str(step.get(key) or "").casefold()
        if not text:
            continue
        for needles, category in _ERROR_CATEGORY_PATTERNS:
            if any(needle in text for needle in needles):
                return category
    return "tool_error"


def observed_quality_from_steps(steps: Sequence[Mapping[str, Any]]) -> int:
    """步骤成功比例 → 观察质量毫值；无步骤的内部行动回退 800。"""
    rows = [step for step in steps if isinstance(step, Mapping)]
    if not rows:
        return 800
    ok_count = sum(1 for step in rows if step.get("ok") is True)
    return ok_count * 1000 // len(rows)


def prediction_from_snapshot(snapshot: Mapping[str, Any]) -> PredictionSnapshot:
    """从注册表持久化的快照字典重建预测（哈希与文本按 canonical 重算）。"""
    from contracts import canonical_json_bytes

    payload = {str(key): value for key, value in snapshot.items()}
    try:
        predicted = int(payload.get("predicted_success_milli"))
    except (TypeError, ValueError):
        predicted = DEFAULT_PREDICTED_SUCCESS_MILLI
    return PredictionSnapshot(
        predicted_success_milli=max(0, min(1000, predicted)),
        basis=str(payload.get("basis") or "default"),
        snapshot=payload,
        snapshot_sha256=canonical_sha256(payload),
        prior_prediction=canonical_json_bytes(payload).decode("utf-8"),
    )


__all__ = [
    "DEFAULT_PREDICTED_SUCCESS_MILLI",
    "PredictionSnapshot",
    "ZERO_SHA256",
    "build_action_impact",
    "build_life_event",
    "build_open_episode",
    "build_outcome_evidence",
    "build_prediction",
    "failure_category_from_error",
    "failure_category_from_step_error",
    "fingerprint",
    "observed_quality_from_steps",
    "prediction_from_snapshot",
]
