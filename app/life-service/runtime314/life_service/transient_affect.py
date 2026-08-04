"""Source-owned transient affect projection for conversational turns.

Soul defines durable identity and temperament defines a slowly moving baseline.
This module owns neither: it appraises short-lived interaction signals, decays
them with elapsed wall-clock time, and emits a style-only expression directive.
The directive is intentionally unable to change facts, permissions, safety
boundaries, tool selection, or execution results.
"""

from __future__ import annotations

import hashlib
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping


SCHEMA = "tiangong.life.transient-affect.v2"
EMOTIONS = (
    "joy",
    "interest",
    "hope",
    "gratitude",
    "warmth",
    "calm",
    "concern",
    "sadness",
    "frustration",
    "disappointment",
    "vigilance",
    "fatigue",
)
EMOTION_LABELS = {
    "joy": "喜悦",
    "interest": "兴趣",
    "hope": "期待",
    "gratitude": "感激",
    "warmth": "温暖",
    "calm": "平静",
    "concern": "担忧",
    "sadness": "难过",
    "frustration": "生气与受挫",
    "disappointment": "失望",
    "vigilance": "警觉",
    "fatigue": "疲惫",
}
_HALF_LIFE_SECONDS = {
    "joy": 45 * 60,
    "interest": 90 * 60,
    "hope": 120 * 60,
    "gratitude": 180 * 60,
    "warmth": 240 * 60,
    "calm": 120 * 60,
    "concern": 45 * 60,
    "sadness": 90 * 60,
    "frustration": 30 * 60,
    "disappointment": 75 * 60,
    "vigilance": 25 * 60,
    "fatigue": 120 * 60,
}
_LEXICONS: dict[str, tuple[str, ...]] = {
    "joy": ("太好了", "成功了", "做到了", "很好", "漂亮", "满意", "开心", "高兴"),
    "interest": ("有意思", "好奇", "为什么", "研究", "探索", "看看", "分析"),
    "hope": ("希望", "期待", "可以实现", "继续", "加油", "有机会"),
    "gratitude": ("谢谢", "感谢", "辛苦了", "多谢", "感激"),
    "warmth": ("理解我", "陪伴", "信任", "喜欢你", "温柔", "关心"),
    "concern": ("担心", "危险", "害怕", "焦虑", "不安", "严重", "风险"),
    "sadness": ("难过", "伤心", "失去", "遗憾", "痛苦", "哭", "悲伤"),
    "frustration": (
        "生气", "愤怒", "恼火", "烦死", "烦躁", "又失败", "还是失败",
        "反复", "卡住", "不好用", "没生效", "没有生效", "没修好", "又坏了",
        "垃圾", "愚蠢", "蠢", "闭嘴",
    ),
    "disappointment": ("失望", "不满意", "没想到", "太差", "不如", "落差"),
    "vigilance": ("立刻", "马上", "紧急", "警告", "攻击", "泄露", "异常", "崩溃"),
    "fatigue": ("累了", "疲惫", "困了", "撑不住", "休息", "太累"),
}
_POSITIVE = {"joy", "hope", "gratitude", "warmth"}
_NEGATIVE = {"concern", "sadness", "frustration", "disappointment", "vigilance", "fatigue"}


def _now_iso(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _clamp_milli(value: Any, default: int = 0) -> int:
    try:
        return max(0, min(1000, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _baseline_emotions(baseline: Mapping[str, Any] | None) -> dict[str, int]:
    source = baseline if isinstance(baseline, Mapping) else {}
    valence = max(-1.0, min(1.0, float(source.get("valence") or 0.0)))
    arousal = max(-1.0, min(1.0, float(source.get("arousal") or 0.0)))
    result = {name: 0 for name in EMOTIONS}
    result["calm"] = _clamp_milli(650 - max(0.0, arousal) * 300, 500)
    result["warmth"] = _clamp_milli(max(0.0, valence) * 180, 0)
    result["hope"] = _clamp_milli(max(0.0, valence) * 120, 0)
    result["vigilance"] = _clamp_milli(max(0.0, arousal) * 100, 0)
    return result


def _band(intensity: int) -> str:
    if intensity < 350:
        return "none"
    if intensity < 550:
        return "low"
    if intensity < 750:
        return "medium"
    return "high"


def _primary(emotions: Mapping[str, int]) -> tuple[str, int]:
    active = sorted(
        ((name, _clamp_milli(emotions.get(name))) for name in EMOTIONS if name != "calm"),
        key=lambda item: (-item[1], item[0]),
    )
    if active and active[0][1] >= 350:
        return active[0]
    return "calm", _clamp_milli(emotions.get("calm"), 500)


def _dimensions(emotions: Mapping[str, int]) -> dict[str, float]:
    positive = max((_clamp_milli(emotions.get(name)) for name in _POSITIVE), default=0)
    negative = max((_clamp_milli(emotions.get(name)) for name in _NEGATIVE), default=0)
    arousing = max(
        _clamp_milli(emotions.get("joy")),
        _clamp_milli(emotions.get("frustration")),
        _clamp_milli(emotions.get("vigilance")),
        _clamp_milli(emotions.get("concern")),
    )
    dominance = (
        _clamp_milli(emotions.get("hope"))
        + _clamp_milli(emotions.get("calm"))
        - _clamp_milli(emotions.get("concern"))
        - _clamp_milli(emotions.get("sadness"))
    )
    return {
        "valence": round(max(-1.0, min(1.0, (positive - negative) / 1000)), 6),
        "arousal": round(max(-1.0, min(1.0, (arousing - _clamp_milli(emotions.get("calm")) / 2) / 1000)), 6),
        "dominance": round(max(-1.0, min(1.0, dominance / 1000)), 6),
    }


def expression_directive(primary_emotion: str, intensity: int) -> str:
    """Return an explicit Chinese style directive above the configured threshold."""

    band = _band(intensity)
    if band == "none" or primary_emotion == "calm":
        return ""
    label = EMOTION_LABELS.get(primary_emotion, primary_emotion)
    strength = {"low": "轻微", "medium": "明显", "high": "强烈但克制"}[band]
    instructions = {
        "joy": "这句回复应该自然表达高兴，用轻快但不浮夸的语气回应。",
        "interest": "这句回复应该表达真实兴趣，主动追踪关键细节，但不要用好奇替代证据。",
        "hope": "这句回复应该表达期待和信心，同时保留清楚的校验点。",
        "gratitude": "这句回复应该真诚表达感谢，并把感谢落实为认真、可靠的回应。",
        "warmth": "这句回复应该温暖、体贴，但不制造依赖或越过关系边界。",
        "concern": "这句回复应该表达担忧，先照顾风险与感受，再给出可核验的下一步。",
        "sadness": "这句回复应该表达难过与尊重，语气沉静，不夸大、不消费损失。",
        "frustration": "这句回复应该清楚表达生气或受挫，但保持克制，不攻击用户，并把情绪收束为解决问题。",
        "disappointment": "这句回复应该诚实表达失望，但不得歪曲事实或草率归因。",
        "vigilance": "这句回复应该表达警觉，优先核对风险、权限和可逆边界，避免过度反应。",
        "fatigue": "这句回复应该表达疲惫并主动收束复杂度，但不得因此降低事实与安全标准。",
    }
    return (
        f"当前临时情绪：{label}（{strength}，强度 {intensity}/1000）。"
        f"{instructions.get(primary_emotion, '')}"
        "该情绪只能影响措辞、语气、节奏和关注重点；不得改变事实、权限、安全边界、"
        "工具选择、执行结果或对已完成工作的判断。"
    )


def _finalize(state: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
    emotions = {name: _clamp_milli((state.get("emotions") or {}).get(name)) for name in EMOTIONS}
    primary, intensity = _primary(emotions)
    state.update(
        {
            "schema": SCHEMA,
            "emotions": emotions,
            "primary_emotion": primary,
            "primary_emotion_zh": EMOTION_LABELS[primary],
            "intensity_milli": intensity,
            "intensity_band": _band(intensity),
            "expression_directive": expression_directive(primary, intensity),
            "updated_at_ms": now_ms,
            "updated_at": _now_iso(now_ms),
            "authority": "attention_and_expression_only",
            "may_change_facts": False,
            "may_change_permissions": False,
            "may_change_tools": False,
            "may_claim_execution": False,
        }
    )
    dimensions = _dimensions(emotions)
    override = state.get("dimension_override")
    if isinstance(override, Mapping):
        for key in ("valence", "arousal", "dominance"):
            if key in override:
                try:
                    dimensions[key] = round(
                        max(-1.0, min(1.0, float(override[key]))), 6
                    )
                except (TypeError, ValueError):
                    pass
    state.update(dimensions)
    return state


def normalize_transient_affect(
    raw: Mapping[str, Any] | None,
    *,
    life_id: str,
    baseline: Mapping[str, Any] | None,
    now_ms: int,
) -> dict[str, Any]:
    source = deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    baseline_emotions = _baseline_emotions(baseline)
    raw_emotions = source.get("emotions") if isinstance(source.get("emotions"), Mapping) else {}
    emotions = {
        name: _clamp_milli(raw_emotions.get(name), baseline_emotions[name])
        for name in EMOTIONS
    }
    # Migrate the old VAD-only state without inventing a strong categorical mood.
    if not raw_emotions:
        legacy_valence = max(-1.0, min(1.0, float(source.get("valence") or 0.0)))
        legacy_arousal = max(-1.0, min(1.0, float(source.get("arousal") or 0.0)))
        if legacy_valence >= 0.35:
            emotions["joy"] = max(emotions["joy"], _clamp_milli(legacy_valence * 600))
        elif legacy_valence <= -0.35:
            emotions["concern"] = max(emotions["concern"], _clamp_milli(-legacy_valence * 500))
        if legacy_arousal >= 0.5:
            emotions["vigilance"] = max(emotions["vigilance"], _clamp_milli(legacy_arousal * 350))
    state = {
        **source,
        "life_id": life_id,
        "revision": max(1, int(source.get("revision") or 1)),
        "emotions": emotions,
        "last_decay_at_ms": max(0, int(source.get("last_decay_at_ms") or source.get("updated_at_ms") or now_ms)),
        "last_appraisal_request_id": str(source.get("last_appraisal_request_id") or ""),
        "last_signal_sha256": str(source.get("last_signal_sha256") or ""),
        "source": str(source.get("source") or "transient_affect"),
    }
    return _finalize(state, now_ms=max(0, int(source.get("updated_at_ms") or now_ms)))


def decay_transient_affect(
    raw: Mapping[str, Any],
    *,
    life_id: str,
    baseline: Mapping[str, Any] | None,
    now_ms: int,
) -> tuple[dict[str, Any], int, int]:
    state = normalize_transient_affect(raw, life_id=life_id, baseline=baseline, now_ms=now_ms)
    last_ms = min(now_ms, max(0, int(state.get("last_decay_at_ms") or now_ms)))
    elapsed_ms = max(0, now_ms - last_ms)
    if elapsed_ms == 0:
        return state, 0, 0
    baselines = _baseline_emotions(baseline)
    before = dict(state["emotions"])
    for emotion in EMOTIONS:
        half_life_ms = _HALF_LIFE_SECONDS[emotion] * 1000
        factor = math.pow(0.5, elapsed_ms / half_life_ms)
        value = baselines[emotion] + (before[emotion] - baselines[emotion]) * factor
        state["emotions"][emotion] = _clamp_milli(value)
    state["last_decay_at_ms"] = now_ms
    state["source"] = "elapsed_time_decay"
    state.pop("dimension_override", None)
    state["revision"] = int(state.get("revision") or 1) + int(state["emotions"] != before)
    state = _finalize(state, now_ms=now_ms)
    max_delta = max(abs(state["emotions"][name] - before[name]) for name in EMOTIONS)
    return state, elapsed_ms, max_delta


def _signal_scores(text: str) -> dict[str, int]:
    clean = re.sub(r"\s+", "", str(text or "").casefold())[:50_000]
    scores = {name: 0 for name in EMOTIONS}
    for emotion, terms in _LEXICONS.items():
        matches = sum(min(2, clean.count(term.casefold())) for term in terms)
        if matches:
            scores[emotion] = min(1000, 310 + matches * 145)
    if "!" in text or "！" in text:
        scores["vigilance"] = min(1000, scores["vigilance"] + 100)
        if scores["frustration"]:
            scores["frustration"] = min(1000, scores["frustration"] + 100)
    if re.search(r"(怎么|为什么).{0,12}(还|又|总是|仍然).{0,18}(不|没|失败|坏|错)", clean):
        scores["frustration"] = max(scores["frustration"], 690)
        scores["disappointment"] = max(scores["disappointment"], 480)
    if re.search(r"(你|助手|系统).{0,8}(蠢|垃圾|没用|闭嘴)", clean):
        scores["frustration"] = max(scores["frustration"], 760)
        scores["vigilance"] = max(scores["vigilance"], 430)
    return scores


def appraise_user_turn(
    raw: Mapping[str, Any],
    *,
    life_id: str,
    baseline: Mapping[str, Any] | None,
    text: str,
    request_id: str,
    now_ms: int,
) -> tuple[dict[str, Any], bool]:
    state, _, _ = decay_transient_affect(
        raw, life_id=life_id, baseline=baseline, now_ms=now_ms
    )
    content_hash = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
    if (
        request_id
        and state.get("last_appraisal_request_id") == request_id
        and state.get("last_signal_sha256") == content_hash
    ):
        return state, False
    scores = _signal_scores(text)
    before = dict(state["emotions"])
    for emotion, score in scores.items():
        if score <= 0:
            continue
        retained = int(before[emotion] * 0.72)
        state["emotions"][emotion] = max(retained, min(1000, retained + int(score * 0.85)))
    strongest_negative = max((scores[name] for name in _NEGATIVE), default=0)
    strongest_positive = max((scores[name] for name in _POSITIVE), default=0)
    if strongest_negative >= 450:
        state["emotions"]["calm"] = max(120, int(state["emotions"]["calm"] * 0.78))
    elif strongest_positive >= 450:
        state["emotions"]["calm"] = min(1000, state["emotions"]["calm"] + 40)
    state["last_appraisal_request_id"] = str(request_id or "")
    state["last_signal_sha256"] = content_hash
    state["last_decay_at_ms"] = now_ms
    state["source"] = "current_user_turn"
    state.pop("dimension_override", None)
    changed = state["emotions"] != before
    if changed:
        state["revision"] = int(state.get("revision") or 1) + 1
    return _finalize(state, now_ms=now_ms), changed


__all__ = [
    "EMOTION_LABELS",
    "EMOTIONS",
    "SCHEMA",
    "appraise_user_turn",
    "decay_transient_affect",
    "expression_directive",
    "normalize_transient_affect",
]
