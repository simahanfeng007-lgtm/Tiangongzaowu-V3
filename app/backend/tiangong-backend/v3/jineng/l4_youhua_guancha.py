"""L4 model optimization observation and routing advice.

This module turns local L4 optimization traces into provider health facts and
advisory routing hints. It does not call model providers and never reads or
returns plaintext credentials.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

from tiangong_kernel.l4_action_grounding import optimization_profile_for
from tiangong_kernel.l4_action_grounding.model_provider_adapter import PROVIDER_IDS

from ..peizhi import (
    ZHUIZONG_LUJING,
    duqu_api_miyao,
    duqu_model_ming,
    duqu_provider_base_url,
)


TRACE_PATH = ZHUIZONG_LUJING / "l4_model_optimization.jsonl"
MAX_TRACE_LINES = 800


def _read_trace_rows(path: Path = TRACE_PATH, max_lines: int = MAX_TRACE_LINES) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max_lines:]
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _percentile(values: list[int], q: float) -> int | None:
    clean = sorted(value for value in values if isinstance(value, int) and value >= 0)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = min(max(q, 0.0), 1.0) * (len(clean) - 1)
    lower = int(pos)
    upper = min(lower + 1, len(clean) - 1)
    if lower == upper:
        return clean[lower]
    return round(clean[lower] + (clean[upper] - clean[lower]) * (pos - lower))


def _health_for(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no_data"
    recent = rows[-20:]
    ok_count = sum(1 for row in recent if row.get("api_status") == "ok")
    rate_limited = any(row.get("http_status") == 429 for row in recent[-5:])
    transient_errors = sum(1 for row in recent if row.get("http_status") in {408, 409, 425, 429, 500, 502, 503, 504})
    ok_rate = ok_count / max(len(recent), 1)
    p95_latency = _percentile([int(row.get("latency_ms") or 0) for row in recent], 0.95)
    if rate_limited:
        return "rate_limited"
    if ok_rate >= 0.95 and (p95_latency is None or p95_latency <= 8000):
        return "available"
    if ok_rate >= 0.70:
        return "degraded"
    if transient_errors:
        return "degraded"
    return "failed"


def _usage_sum(rows: list[dict[str, Any]], field: str) -> int:
    total = 0
    for row in rows:
        usage = row.get("usage")
        if isinstance(usage, dict):
            try:
                total += int(usage.get(field) or 0)
            except Exception:
                pass
    return total


def _cache_prefix_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate compulsory cold-prefix misses from warm-prefix reuse."""
    seen: set[str] = set()
    observed_calls = 0
    cold_start_calls = 0
    warm_calls = 0
    warm_prompt_tokens = 0
    warm_cached_tokens = 0
    for row in rows:
        fingerprint = str(row.get("cache_prefix_sha256") or "").strip().lower()
        usage = row.get("usage")
        if len(fingerprint) != 64 or not isinstance(usage, dict):
            continue
        try:
            prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
            cached_tokens = max(
                0,
                int(
                    usage.get("prompt_cache_hit_tokens")
                    or usage.get("cached_input_tokens")
                    or 0
                ),
            )
        except Exception:
            continue
        if prompt_tokens <= 0:
            continue
        observed_calls += 1
        if fingerprint not in seen:
            seen.add(fingerprint)
            cold_start_calls += 1
            continue
        warm_calls += 1
        warm_prompt_tokens += prompt_tokens
        warm_cached_tokens += cached_tokens
    return {
        "cache_prefix_count": len(seen),
        "cache_prefix_observed_calls": observed_calls,
        "cache_prefix_cold_start_calls": cold_start_calls,
        "cache_prefix_warm_calls": warm_calls,
        "cache_prefix_reuse_rate": (
            round(warm_calls / observed_calls, 4) if observed_calls else None
        ),
        "warm_prompt_tokens": warm_prompt_tokens,
        "warm_cached_input_tokens": warm_cached_tokens,
        "warm_cache_hit_ratio": (
            round(warm_cached_tokens / warm_prompt_tokens, 4)
            if warm_prompt_tokens
            else None
        ),
    }


def _provider_summary(provider_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    provider_rows = [row for row in rows if row.get("provider") == provider_id]
    ok_rows = [row for row in provider_rows if row.get("api_status") == "ok"]
    latencies = [int(row.get("latency_ms") or 0) for row in provider_rows if row.get("latency_ms") is not None]
    retry_count = sum(int(row.get("retry_count") or 0) for row in provider_rows)
    prompt_tokens = _usage_sum(provider_rows, "prompt_tokens")
    completion_tokens = _usage_sum(provider_rows, "completion_tokens")
    total_tokens = _usage_sum(provider_rows, "total_tokens")
    cached_tokens = _usage_sum(provider_rows, "cached_input_tokens")
    prompt_cache_hit_tokens = _usage_sum(provider_rows, "prompt_cache_hit_tokens")
    prompt_cache_miss_tokens = _usage_sum(provider_rows, "prompt_cache_miss_tokens")
    if prompt_cache_hit_tokens:
        cached_tokens = prompt_cache_hit_tokens
    cache_hit_ratio = round(cached_tokens / prompt_tokens, 4) if prompt_tokens else None
    profile = optimization_profile_for(provider_id)
    summary = {
        "provider": provider_id,
        "model": duqu_model_ming(provider_id),
        "base_url": duqu_provider_base_url(provider_id) or "",
        "credential_state": "configured" if duqu_api_miyao(provider_id) else "missing",
        "health": _health_for(provider_rows),
        "calls": len(provider_rows),
        "ok_calls": len(ok_rows),
        "ok_rate": round(len(ok_rows) / len(provider_rows), 4) if provider_rows else None,
        "avg_latency_ms": round(statistics.mean(latencies)) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 0.95),
        "retry_count": retry_count,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_input_tokens": cached_tokens,
        "prompt_cache_hit_tokens": prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": prompt_cache_miss_tokens,
        "cache_hit_ratio": cache_hit_ratio,
        "last_status": provider_rows[-1].get("api_status") if provider_rows else "no_trace",
        "last_http_status": provider_rows[-1].get("http_status") if provider_rows else None,
        "last_applied": provider_rows[-1].get("applied") if provider_rows else [],
        "l4_profile": {
            "advisory_only": profile.advisory_only,
            "cache_hint_count": len(profile.cache_strategy_hints),
            "structured_hint_count": len(profile.structured_output_hints),
            "tool_hint_count": len(profile.tool_calling_hints),
            "routing_hint_count": len(profile.routing_hints),
        },
    }
    summary.update(_cache_prefix_metrics(provider_rows))
    if provider_id == "minimax_m3":
        last = provider_rows[-1] if provider_rows else {}
        mode_counts: dict[str, int] = {}
        for row in provider_rows:
            mode = str(row.get("m3_mode") or "unknown")
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
        summary["m3_native"] = {
            "enabled": bool(last.get("m3_native_enabled")) if provider_rows else False,
            "last_mode": last.get("m3_mode") if provider_rows else None,
            "last_thinking_type": last.get("thinking_type") if provider_rows else None,
            "last_reasoning_split": last.get("reasoning_split") if provider_rows else None,
            "last_max_completion_tokens": last.get("max_completion_tokens") if provider_rows else None,
            "last_service_tier": last.get("service_tier") if provider_rows else "",
            "mode_counts": mode_counts,
        }
    return summary


def _usable(summary: dict[str, Any]) -> bool:
    return summary.get("credential_state") == "configured" and summary.get("health") in {"available", "degraded", "no_data"}


def _pick(summaries: dict[str, dict[str, Any]], preferred: list[str], fallback: str = "gpt_5_5") -> dict[str, str]:
    for provider_id in preferred:
        row = summaries.get(provider_id) or {}
        if _usable(row):
            return {"provider": provider_id, "reason": f"{provider_id}:configured:{row.get('health')}"}
    row = summaries.get(fallback) or {}
    if _usable(row):
        return {"provider": fallback, "reason": f"{fallback}:fallback:{row.get('health')}"}
    return {"provider": fallback if fallback else (preferred[0] if preferred else "gpt_5_5"), "reason": "requires_credentials_or_health_data"}


def _routing_recommendations(summaries: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "task": "ordinary_chat",
            **_pick(summaries, ["gpt_5_5", "minimax_m3", "deepseek_v4"]),
        },
        {
            "task": "agentic_tool_call",
            **_pick(summaries, ["gpt_5_5", "glm_5_2", "minimax_m3", "deepseek_v4"]),
        },
        {
            "task": "deep_reasoning",
            **_pick(summaries, ["deepseek_v4", "gpt_5_5", "minimax_m3"]),
        },
        {
            "task": "structured_code_or_json",
            **_pick(summaries, ["glm_5_2", "minimax_m3", "gpt_5_5"]),
        },
        {
            "task": "frontier_fallback",
            **_pick(summaries, ["gpt_5_5", "deepseek_v4", "minimax_m3"]),
        },
    ]


def _observability_gaps(summaries: dict[str, dict[str, Any]]) -> list[str]:
    gaps: list[str] = []
    if not any((row.get("calls") or 0) >= 20 for row in summaries.values()):
        gaps.append("collect_at_least_20_real_calls_per_active_provider_before_quality_routing")
    if not any(row.get("cached_input_tokens") for row in summaries.values()):
        gaps.append("provider_cache_hit_tokens_not_observed_yet")
    if not any("tool_schema_present" in (row.get("last_applied") or []) for row in summaries.values()):
        gaps.append("tool_call_observation_sparse")
    gaps.append("ttft_tpot_need_streaming_probe_for_precise_token_latency")
    return gaps


def provider_optimization_status() -> dict[str, Any]:
    rows = _read_trace_rows()
    summaries = {provider_id: _provider_summary(provider_id, rows) for provider_id in PROVIDER_IDS}
    active = max(summaries.values(), key=lambda row: (row.get("calls") or 0, row.get("ok_calls") or 0), default={})
    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "trace_path": str(TRACE_PATH),
        "trace_rows": len(rows),
        "active_provider": active,
        "providers": list(summaries.values()),
        "route_recommendations": _routing_recommendations(summaries),
        "observability_gaps": _observability_gaps(summaries),
        "budget_tiers": {
            "adaptive": "tool:4096, short:4096, medium:8192, long:16384 unless env overrides",
            "quick": "2048 max output tokens",
            "balanced": "8192 max output tokens",
            "deep": "32768 max output tokens",
        },
        "research_basis": [
            "workload_router_pool_feedback_loop",
            "kv_or_prefix_cache_hit_observation",
            "tool_call_and_structured_output_validity_tracking",
            "quality_latency_cost_routing",
        ],
    }
