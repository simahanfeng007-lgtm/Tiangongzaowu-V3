from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class OptimizationProfile:
    provider_id: str
    advisory_only: bool = True
    observability_metrics: tuple[str, ...] = ("latency_ms", "http_status", "prompt_tokens", "completion_tokens", "tool_call_validity")
    cache_strategy_hints: tuple[str, ...] = ("reuse_stable_system_prefix", "observe_cache_hit_tokens")
    structured_output_hints: tuple[str, ...] = ("prefer_provider_native_json_schema", "validate_before_commit")
    tool_calling_hints: tuple[str, ...] = ("use_strict_function_schema", "normalize_arguments_json")
    routing_hints: tuple[str, ...] = ("route_by_quality_latency_cost", "fallback_on_transient_error")

_PROFILES: dict[str, OptimizationProfile] = {}
def optimization_profile_for(provider_id: str) -> OptimizationProfile:
    key=str(provider_id or "").strip().lower()
    if not key: raise ValueError("provider_id is required")
    return _PROFILES.setdefault(key, OptimizationProfile(provider_id=key))

from .model_provider_adapter import PROVIDER_IDS
__all__=["OptimizationProfile","optimization_profile_for","PROVIDER_IDS"]
