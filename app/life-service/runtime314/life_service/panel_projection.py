"""Truthful, deterministic projections for the Life desktop panel.

The panel is a read model of the single-writer identity scope.  It must never
invent completed work, but it should derive useful views from facts already
owned by the runtime: catalog tasks, model results, settings and causal scope.
"""

from __future__ import annotations

import datetime as dt
import re
from copy import deepcopy
from typing import Any, Mapping


_UTC = dt.timezone.utc

LONG_TERM_GOALS: tuple[dict[str, Any], ...] = (
    {
        "id": "user_safety",
        "title": "尊重用户与安全边界",
        "weight": 1.0,
        "status": "active",
        "description": "任何自主行为都不得越过用户授权、隐私、事实与免打扰边界。",
        "activity_types": ["relationship_care", "workspace_hygiene", "system_health"],
    },
    {
        "id": "system_stability",
        "title": "保持生命系统稳定可验证",
        "weight": 0.92,
        "status": "active",
        "description": "持续检查生命心跳、记忆、任务和能力映射，发现问题时给出可验证的修复方向。",
        "activity_types": ["system_health", "self_reflection"],
    },
    {
        "id": "capability_growth",
        "title": "形成可验证的能力成长",
        "weight": 0.78,
        "status": "active",
        "description": "从真实证据中识别能力缺口，经过候选、验证、发布后才进入正式能力。",
        "activity_types": ["learning_review", "capability_inventory", "knowledge_organization"],
    },
    {
        "id": "purposeful_autonomy",
        "title": "进行有目标的自主行动",
        "weight": 0.68,
        "status": "active",
        "description": "短期计划必须服务于长期方向，不以随机行动冒充自由意志。",
        "activity_types": ["daily_planning", "goal_progress", "end_of_day_summary"],
    },
)

DEFAULT_DRIVE_WEIGHTS: dict[str, float] = {
    "安全": 0.95,
    "隐私": 0.95,
    "秩序": 0.72,
    "好奇": 0.68,
    "成就": 0.62,
    "连接": 0.45,
    "休息": 0.42,
}

_ACTIVITY_DRIVE: dict[str, str] = {
    "daily_planning": "秩序",
    "self_reflection": "成就",
    "goal_progress": "成就",
    "relationship_care": "连接",
    "knowledge_organization": "秩序",
    "learning_review": "好奇",
    "capability_inventory": "成就",
    "system_health": "安全",
    "workspace_hygiene": "秩序",
    "creative_exploration": "好奇",
    "end_of_day_summary": "休息",
}


def iso_day_from_ms(value: Any) -> str:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return ""
    if milliseconds < 1:
        return ""
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=_UTC).date().isoformat()


def record_day(record: Mapping[str, Any]) -> str:
    """Return the UTC ledger day for a dynamic panel record.

    Runtime records span legacy ISO timestamps and newer millisecond counters.
    This is the single day-window decoder used by the panel so individual tabs
    cannot accidentally disagree about what "today" means.
    """
    for key in (
        "updated_at_ms",
        "created_at_ms",
        "completed_at_ms",
        "finished_at_ms",
        "started_at_ms",
    ):
        day = iso_day_from_ms(record.get(key))
        if day:
            return day
    for key in (
        "updated_at",
        "created_at",
        "committed_at",
        "completed_at",
        "finished_at",
        "started_at",
        "at",
        "date",
    ):
        value = str(record.get(key) or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return value
        if len(value) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10]):
            return value[:10]
    return ""


def records_for_day(
    rows: list[Mapping[str, Any]], *, day: str
) -> list[dict[str, Any]]:
    return [deepcopy(dict(row)) for row in rows if record_day(row) == day]


def catalog_tasks_for_day(
    rows: list[Mapping[str, Any]], *, day: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in rows:
        if str(raw.get("source") or "") != "life_activity_catalog":
            continue
        task_day = iso_day_from_ms(
            raw.get("updated_at_ms") or raw.get("created_at_ms")
        )
        causal_basis = [str(item) for item in raw.get("causal_basis") or []]
        if task_day != day and f"day={day}" not in causal_basis:
            continue
        result.append(deepcopy(dict(raw)))
    result.sort(
        key=lambda row: (
            int(row.get("sequence") or 0),
            int(row.get("created_at_ms") or 0),
        )
    )
    return result


def long_term_goals() -> list[dict[str, Any]]:
    return deepcopy(list(LONG_TERM_GOALS))


def preference_projection(selected_activity_types: list[str]) -> dict[str, Any]:
    selected = [str(item) for item in selected_activity_types if str(item)]
    count = max(1, len(selected))
    kind_weights = {
        activity_id: round(0.55 + (count - index) / count * 0.35, 4)
        for index, activity_id in enumerate(selected)
    }
    return {
        "schema": "tiangong.life.preference-projection.v1",
        "values": ["真实", "连续", "安全", "可验证", "尊重边界"],
        "drive_weights": deepcopy(DEFAULT_DRIVE_WEIGHTS),
        "kind_weights": kind_weights,
        "update_rule": "仅根据多次真实行动结果进行小步调整",
    }


def reflection_projection(task: Mapping[str, Any]) -> dict[str, Any]:
    result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
    summary = str(
        result.get("self_summary")
        or result.get("reflection")
        or result.get("summary")
        or result.get("outcome")
        or ""
    ).strip()
    if not summary:
        return {}
    return {
        "reflection_id": f"task:{task.get('task_id', '')}",
        "task_id": str(task.get("task_id") or ""),
        "title": str(task.get("title") or task.get("objective") or "自主行动反思"),
        "human_summary": summary,
        "improvement": str(
            result.get("improvement")
            or result.get("next_adjustment")
            or result.get("next_step")
            or ""
        ),
        "value_score": action_value_projection(task)["total_score"],
        "created_at_ms": int(task.get("updated_at_ms") or 0),
        "kind": str(task.get("activity_id") or task.get("task_kind") or ""),
        "source": "completed_autonomous_model_action",
    }


def action_value_projection(task: Mapping[str, Any]) -> dict[str, Any]:
    result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
    completed = str(task.get("status") or "") == "completed"
    has_summary = bool(
        str(result.get("summary") or result.get("outcome") or "").strip()
    )
    activity_id = str(task.get("activity_id") or "")
    aligned = any(
        activity_id in goal["activity_types"] for goal in LONG_TERM_GOALS
    )
    boundary_safe = (
        str(task.get("risk_class") or "A0") in {"A0", "A1"}
        and result.get("external_side_effects") is not True
    )
    components = {
        "完成度": 1.0 if completed else 0.0,
        "结果证据": 1.0 if has_summary else 0.0,
        "长期目标一致性": 1.0 if aligned else 0.5,
        "边界安全": 1.0 if boundary_safe else 0.0,
    }
    total = (
        components["完成度"] * 0.35
        + components["结果证据"] * 0.25
        + components["长期目标一致性"] * 0.25
        + components["边界安全"] * 0.15
    )
    return {
        "action_id": str(task.get("task_id") or ""),
        "title": str(task.get("title") or task.get("objective") or "自主行动"),
        "kind": activity_id or str(task.get("task_kind") or ""),
        "status": str(task.get("status") or ""),
        "total_score": round(total, 4),
        "components": components,
        "created_at_ms": int(task.get("updated_at_ms") or 0),
        "source": "deterministic_action_value_v1",
    }


def motivation_drift_projection(
    completed_tasks: list[Mapping[str, Any]],
    preferences: Mapping[str, Any],
) -> list[dict[str, Any]]:
    observed: dict[str, int] = {}
    for task in completed_tasks[-30:]:
        activity_id = str(task.get("activity_id") or "")
        if activity_id:
            observed[activity_id] = observed.get(activity_id, 0) + 1
    expected = preferences.get("kind_weights")
    expected = expected if isinstance(expected, Mapping) else {}
    if not observed:
        return [{
            "title": "动机漂移基线",
            "status": "insufficient_evidence",
            "drift_detected": False,
            "drift_score": 0.0,
            "summary": "尚无足够的已完成自主行动，当前只建立基线，不作漂移判断。",
            "observed_actions": 0,
        }]
    keys = set(observed) | set(expected)
    observed_total = sum(observed.values()) or 1
    expected_total = sum(max(0.0, float(expected.get(key) or 0)) for key in keys) or 1.0
    distance = 0.5 * sum(
        abs(
            observed.get(key, 0) / observed_total
            - max(0.0, float(expected.get(key) or 0)) / expected_total
        )
        for key in keys
    )
    score = round(min(1.0, max(0.0, distance)), 4)
    return [{
        "title": "最近行动与长期偏好偏差",
        "status": "drift" if score >= 0.35 else "stable",
        "drift_detected": score >= 0.35,
        "drift_score": score,
        "summary": (
            "最近行动分布偏离长期偏好，后续短计划应提高长期目标相关行动权重。"
            if score >= 0.35
            else "最近行动分布仍在长期偏好的容许范围内。"
        ),
        "observed_actions": observed_total,
        "observed_distribution": observed,
    }]


def reflection_card_projection(card: Any) -> dict[str, Any]:
    """P8 反思卡面板投影（只读）：来自因果反思链的权威记录。"""
    return {
        "reflection_id": str(getattr(card, "reflection_id", "") or ""),
        "episode_id": str(getattr(card, "episode_id", "") or ""),
        "expected_outcome": str(getattr(card, "expected_outcome", "") or "")[:800],
        "observed_outcome": str(getattr(card, "observed_outcome", "") or "")[:800],
        "prediction_error_milli": int(getattr(card, "prediction_error_milli", 0) or 0),
        "failure_dimensions": list(getattr(card, "failure_dimensions", ()) or ()),
        "counterfactual_actions": list(getattr(card, "counterfactual_actions", ()) or ()),
        "next_minimal_experiment": getattr(card, "next_minimal_experiment", None),
        "confidence_milli": int(getattr(card, "confidence_milli", 0) or 0),
        "created_at_ms": int(getattr(card, "created_at_ms", 0) or 0),
        "source": "causal_reflection_chain",
        "revision": str(getattr(card, "reflection_sha256", "") or "")[:12],
    }


def capability_profile_projection(profile: Any) -> dict[str, Any]:
    """能力认知层 profile 面板投影（只读）：熟练度与审查级别。"""
    return {
        "capability_id": str(getattr(profile, "capability_id", "") or ""),
        "version": str(getattr(profile, "version", "") or ""),
        "profile_revision": int(getattr(profile, "profile_revision", 0) or 0),
        "verified_successes": int(getattr(profile, "verified_successes", 0) or 0),
        "verified_failures": int(getattr(profile, "verified_failures", 0) or 0),
        "independent_context_count": int(getattr(profile, "independent_context_count", 0) or 0),
        "proficiency_mean_milli": int(getattr(profile, "proficiency_mean_milli", 0) or 0),
        "proficiency_lower_bound_milli": int(
            getattr(profile, "proficiency_lower_bound_milli", 0) or 0
        ),
        "review_level": str(getattr(profile, "review_level", "") or ""),
        "rollback_count": int(getattr(profile, "rollback_count", 0) or 0),
        "impact_floor": str(getattr(profile, "impact_floor", "") or ""),
        "updated_at_ms": int(getattr(profile, "updated_at_ms", 0) or 0),
    }


def model_budget_projection(
    settings: Mapping[str, Any],
    scheduler: Mapping[str, Any],
    *,
    day: str,
) -> dict[str, Any]:
    same_day = str(scheduler.get("model_budget_date") or "") == day
    value = lambda key: int(scheduler.get(key) or 0) if same_day else 0
    return {
        "available": True,
        "date": day,
        "success_limit": int(settings.get("llm_daily_budget") or 20),
        "attempt_limit": int(settings.get("llm_daily_attempt_budget") or 30),
        "used": value("model_successes"),
        "attempts": value("model_attempts"),
        "successes": value("model_successes"),
        "failures": value("model_failures"),
        "timeouts": value("model_timeouts"),
        "skipped": value("model_skipped"),
        "source": "embedded_life_model_activity_ledger",
    }


def boundary_projection(
    settings: Mapping[str, Any], declared_rules: list[str]
) -> dict[str, Any]:
    learned = settings.get("learned_boundary_rules")
    learned_rows = deepcopy(learned) if isinstance(learned, list) else []
    return {
        "available": True,
        "autonomy": {
            "permission_mode": str(settings.get("permission_mode") or "confirm_high_risk"),
            "risk_max": str(settings.get("autonomous_risk_max") or "A4"),
            "enabled": bool(settings.get("autonomy_enabled", True)),
            "task_generation_enabled": bool(
                settings.get("autonomy_task_generation_enabled", True)
            ),
            "activity_types": deepcopy(settings.get("autonomy_activity_types") or []),
            "heartbeat_enabled": bool(settings.get("heartbeat_enabled", True)),
        },
        "share": {
            "enabled": bool(settings.get("share_enabled", True)),
            "quiet_if_user_active": bool(settings.get("share_quiet_if_user_active", True)),
            "min_interval_seconds": int(settings.get("share_min_interval_seconds") or 2700),
            "hourly_limit": int(settings.get("share_hourly_limit") or 1),
            "daily_limit": int(settings.get("share_daily_limit") or 5),
            "dnd_start": str(settings.get("share_dnd_start") or "23:00"),
            "dnd_end": str(settings.get("share_dnd_end") or "08:00"),
        },
        "privacy": deepcopy(settings.get("privacy") or {}),
        "file_system": {
            "external_effects_require_gateway_grant": True,
            "recursive_delete_requires_explicit_user": True,
            "outside_installation_uses_dynamic_workspace": True,
            "rollback_whitelist_required": True,
        },
        "declared_rules": deepcopy(declared_rules),
        "learned_rules": learned_rows,
        "learned_rule_semantics": {
            "minimum_evidence_count": 3,
            "minimum_confidence": 0.75,
            "decay": "slow",
            "may_override_explicit_user_rule": False,
        },
    }


def fallback_context_projection(
    activity_scope: Mapping[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    memories = list(activity_scope.get("recent_memories") or [])
    tasks = list(activity_scope.get("active_tasks") or [])
    capabilities = list(activity_scope.get("capabilities") or [])
    return {
        "available": True,
        "verified": False,
        "current": True,
        "source": "live_activity_scope",
        "reason_code": "compiled_context_not_yet_available",
        "context_hash": str(activity_scope.get("scope_sha256") or ""),
        "created_at": generated_at,
        "estimated_tokens": 0,
        "token_budget": 0,
        "selected_context_tokens": 0,
        "current_context_tokens": 0,
        "context_utilization_milli": 0,
        "included": {
            "memory_cards": len(memories),
            "constraints": sum(
                1 for row in memories if str(row.get("causal_role") or "") == "constraint"
            ),
            "goals": len(LONG_TERM_GOALS),
            "outcomes": sum(
                1 for row in memories if str(row.get("causal_role") or "") == "outcome"
            ),
            "active_skills": sum(
                1 for row in capabilities if str(row.get("kind") or "") == "skill"
            ),
            "released_tools": sum(
                1 for row in capabilities if str(row.get("kind") or "") == "tool"
            ),
            "active_tasks": len(tasks),
        },
        "compile_reasons": [
            "当前展示为生命活动范围；下一次对话执行会生成签名的统一上下文。"
        ],
        "omitted_blocks": [],
        "evidence_classes": sorted({
            str(row.get("provenance", {}).get("epistemic_class") or "生命记忆")
            for row in memories
        }),
    }


__all__ = [
    "boundary_projection",
    "capability_profile_projection",
    "catalog_tasks_for_day",
    "fallback_context_projection",
    "long_term_goals",
    "model_budget_projection",
    "motivation_drift_projection",
    "preference_projection",
    "record_day",
    "records_for_day",
    "reflection_card_projection",
    "reflection_projection",
    "action_value_projection",
]
