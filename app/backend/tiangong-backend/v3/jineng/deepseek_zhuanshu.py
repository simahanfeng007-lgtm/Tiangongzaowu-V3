"""DeepSeek-specific request and telemetry helpers.

This module is intentionally provider-scoped. Generic OpenAI-compatible
providers must not import DeepSeek behavior just because they share the
``/chat/completions`` wire shape.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse


DEEPSEEK_PROVIDER_ID = "deepseek_v4"
DEEPSEEK_FORBIDDEN_THINKING_PARAMS = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
)


def is_deepseek_provider(
    provider_id: str | None = None,
    *,
    base_url: str | None = None,
    model_name: str | None = None,
) -> bool:
    """Return True only for explicit DeepSeek evidence."""
    pid = str(provider_id or "").strip().lower().replace("-", "_")
    if pid in {"deepseek", DEEPSEEK_PROVIDER_ID}:
        return True

    model = str(model_name or "").strip().lower()
    if model.startswith("deepseek"):
        return True

    value = str(base_url or "").strip().lower()
    if value:
        parsed = urlparse(value if "://" in value else "https://" + value)
        host = parsed.netloc.lower()
        if host == "deepseek.com" or host.endswith(".deepseek.com"):
            return True
    return False


def normalize_deepseek_base_url(base_url: str | None) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = parsed.netloc.lower()
    if host == "deepseek.com" or host.endswith(".deepseek.com"):
        path = parsed.path.rstrip("/")
        if path == "/v1":
            return f"{parsed.scheme}://{parsed.netloc}"
    return value


def apply_deepseek_request_profile(
    payload: dict[str, Any],
    *,
    model_name: str | None = None,
    output_limit: int | None = None,
    output_policy: str = "",
) -> dict[str, Any]:
    """Mutate a DeepSeek payload with DeepSeek-only controls."""
    applied: list[str] = ["deepseek_chain_active"]
    removed: list[str] = []

    thinking_type = _deepseek_thinking_type()
    payload["thinking"] = {"type": thinking_type}
    applied.append(f"thinking:{thinking_type}")

    if thinking_type == "enabled":
        payload["reasoning_effort"] = _deepseek_reasoning_effort(payload.get("tools"))
        applied.append(f"reasoning_effort:{payload['reasoning_effort']}")
        for key in DEEPSEEK_FORBIDDEN_THINKING_PARAMS:
            if key in payload:
                payload.pop(key, None)
                removed.append(key)
    else:
        payload.pop("reasoning_effort", None)

    if output_limit and "max_tokens" not in payload:
        payload["max_tokens"] = int(output_limit)
        applied.append("max_tokens_present")
    if output_policy:
        applied.append(f"output_policy:{output_policy}")

    cache_hint = _cache_prefix_hint(payload)
    return {
        "applied": applied,
        "removed_params": removed,
        "model_name": str(model_name or payload.get("model") or ""),
        "thinking_type": thinking_type,
        "cache_prefix_chars": cache_hint["cache_prefix_chars"],
        "cache_prefix_message_count": cache_hint["cache_prefix_message_count"],
    }


def deepseek_response_metrics(data: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "deepseek_chain": True,
        "has_reasoning_content": False,
        "reasoning_content_chars": 0,
        "reasoning_redacted": True,
    }
    message = _first_message(data)
    if isinstance(message, dict):
        reasoning = message.get("reasoning_content")
        if reasoning is not None:
            text = str(reasoning or "")
            metrics["has_reasoning_content"] = bool(text.strip())
            metrics["reasoning_content_chars"] = len(text)
        content = message.get("content")
        metrics["content_chars"] = len(str(content or ""))
    return metrics


def deepseek_redact_reasoning(data: dict[str, Any]) -> dict[str, Any]:
    """Remove reasoning_content from DeepSeek response so it never reaches frontend."""
    if not isinstance(data, dict):
        return data
    for choice in (data.get("choices") or []):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            message.pop("reasoning_content", None)
            message.pop("reasoning_details", None)
    return data


def deepseek_usage_metrics(usage: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}
    prompt_tokens = _int_value(usage.get("prompt_tokens"))
    hit_tokens = _int_value(
        usage.get("prompt_cache_hit_tokens")
        or usage.get("cached_input_tokens")
        or usage.get("cache_read_input_tokens")
    )
    miss_tokens = _int_value(
        usage.get("prompt_cache_miss_tokens")
        or usage.get("cache_creation_input_tokens")
        or usage.get("cache_write_input_tokens")
    )
    total_cache_tokens = hit_tokens + miss_tokens
    ratio_base = total_cache_tokens or prompt_tokens
    result = {
        "prompt_cache_hit_tokens": hit_tokens,
        "prompt_cache_miss_tokens": miss_tokens,
        "cached_input_tokens": hit_tokens,
    }
    if ratio_base:
        result["cache_hit_ratio"] = round(hit_tokens / ratio_base, 4)
    return result


def _deepseek_thinking_type() -> str:
    raw_type = os.environ.get("DEEPSEEK_THINKING_TYPE", "").strip().lower()
    if raw_type in {"enabled", "disabled"}:
        return raw_type
    raw_enabled = os.environ.get("DEEPSEEK_THINKING_ENABLED", "").strip().lower()
    if raw_enabled in {"0", "false", "no", "off", "disabled"}:
        return "disabled"
    return "enabled"


def _deepseek_reasoning_effort(has_tools: Any) -> str:
    raw = os.environ.get("DEEPSEEK_REASONING_EFFORT", "").strip().lower()
    if raw in {"max", "xhigh"}:
        return "max"
    if raw in {"high", "medium", "low"}:
        return "high"
    if has_tools:
        return "max"
    return "high"


def _cache_prefix_hint(payload: dict[str, Any]) -> dict[str, int]:
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    chars = 0
    count = 0
    for message in messages:
        if not isinstance(message, dict):
            break
        if message.get("role") not in {"system", "developer"}:
            break
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            chars += sum(len(str(item)) for item in content)
        count += 1
    return {"cache_prefix_chars": chars, "cache_prefix_message_count": count}


def _first_message(data: Any) -> dict[str, Any] | None:
    try:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        return message if isinstance(message, dict) else None
    except Exception:
        return None


def _int_value(value: Any) -> int:
    try:
        number = int(value or 0)
    except Exception:
        return 0
    return number if number > 0 else 0
