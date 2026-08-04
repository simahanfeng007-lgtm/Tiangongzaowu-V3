"""MiniMax M3 native-side adapter.

This module is intentionally scoped to provider_id == minimax_m3.  It keeps
the existing v3 main chain contract intact by returning legacy text or
<tool_call> blocks while preserving M3-specific request/response metadata for
trace observation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

from tiangong_kernel.l4_action_grounding import optimization_profile_for


CANONICAL_MODEL = "MiniMax-M3"
VALID_MODES = {"quick", "agentic", "long"}
FALSE_VALUES = {"0", "false", "off", "no", "disabled"}
TRUE_VALUES = {"1", "true", "on", "yes", "enabled"}


def _env_text(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    return default


def _env_int(*names: str) -> int | None:
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "input_text", "output_text"}:
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") in {"image_url", "video_url"}:
                    parts.append(json.dumps(item, ensure_ascii=False))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _payload_text_chars(payload: dict[str, Any]) -> int:
    total = 0
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        total += len(_content_to_text(message.get("content")))
    return total


def _strip_reasoning_text(text: str) -> str:
    value = str(text or "")
    for tag in ("think", "thinking", "reasoning"):
        value = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>\s*",
            "",
            value,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return value.strip()


def _tool_call_arguments(value: Any) -> Any:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return value
        return parsed
    return value


def _tool_calls_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or item.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        if arguments is None:
            arguments = item.get("arguments") or {}
        calls.append(
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or "function"),
                "name": name,
                "arguments": _tool_call_arguments(arguments),
            }
        )
    return calls


def _reasoning_ref(message: dict[str, Any], choice: dict[str, Any]) -> dict[str, Any]:
    candidates = {
        "message_reasoning_details": message.get("reasoning_details"),
        "message_reasoning_content": message.get("reasoning_content"),
        "message_thinking": message.get("thinking"),
        "choice_reasoning_details": choice.get("reasoning_details"),
    }
    present = {key: value for key, value in candidates.items() if value}
    if not present:
        return {}
    raw = json.dumps(present, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "reasoning_ref": "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "reasoning_fields": sorted(present.keys()),
        "reasoning_chars": len(raw),
    }


class MiniMaxM3Adapter:
    provider_id = "minimax_m3"

    def enabled(self) -> bool:
        return _env_bool("MINIMAX_M3_NATIVE_ENABLED", True)

    def apply_profile(
        self,
        payload: dict[str, Any],
        model_name: str,
        system_tishi: str,
        yonghu_tishi: str,
    ) -> dict[str, Any]:
        applied: list[str] = []
        trace: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "provider": self.provider_id,
            "model": payload.get("model"),
            "configured_model": model_name,
            "canonical_model": CANONICAL_MODEL,
            "l4_profile_consumed": False,
            "advisory_only": True,
            "m3_native_enabled": self.enabled(),
            "applied": applied,
        }

        try:
            profile = optimization_profile_for(self.provider_id)
            trace["l4_profile_consumed"] = True
            trace["advisory_only"] = profile.advisory_only
            trace["observability_metrics"] = list(profile.observability_metrics)
            trace["cache_strategy_hint_count"] = len(profile.cache_strategy_hints)
            trace["structured_output_hint_count"] = len(profile.structured_output_hints)
            trace["tool_calling_hint_count"] = len(profile.tool_calling_hints)
            applied.append("optimization_profile_loaded")
        except Exception as exc:
            trace["profile_error"] = str(exc)[:200]

        if not self.enabled():
            if payload.get("model") == model_name:
                applied.append("model_name_preserved")
            payload.setdefault("thinking", {"type": self._thinking_type("quick")})
            limit, output_policy = self._output_limit("quick", payload)
            payload.setdefault("max_completion_tokens", limit)
            applied.extend(["minimax_legacy_profile_applied", "max_completion_tokens_present", f"output_policy:{output_policy}"])
            trace.update(
                {
                    "m3_mode": "legacy",
                    "thinking_type": payload.get("thinking", {}).get("type") if isinstance(payload.get("thinking"), dict) else payload.get("thinking"),
                    "reasoning_split": bool(payload.get("reasoning_split")),
                    "payload_keys": sorted(payload.keys()),
                }
            )
            return trace

        original_model = str(payload.get("model") or "")
        if _env_bool("MINIMAX_M3_CANONICAL_MODEL", True):
            payload["model"] = CANONICAL_MODEL
            if original_model and original_model != CANONICAL_MODEL:
                applied.append("model_name_canonicalized")
            else:
                applied.append("model_name_preserved")
        elif payload.get("model") == model_name:
            applied.append("model_name_preserved")

        mode = self._mode(payload, system_tishi, yonghu_tishi)
        thinking_type = self._thinking_type(mode)
        payload["thinking"] = {"type": thinking_type}

        reasoning_split = self._reasoning_split(mode, thinking_type)
        if reasoning_split:
            payload["reasoning_split"] = True
        else:
            payload.pop("reasoning_split", None)

        service_tier = _env_text("MINIMAX_M3_SERVICE_TIER", "MINIMAX_SERVICE_TIER").lower()
        if service_tier in {"standard", "priority"}:
            payload["service_tier"] = service_tier
            applied.append(f"service_tier:{service_tier}")

        limit, output_policy = self._output_limit(mode, payload)
        payload["max_completion_tokens"] = limit

        applied.extend(
            [
                f"m3_mode:{mode}",
                f"thinking:{thinking_type}",
                f"reasoning_split:{str(reasoning_split).lower()}",
                "minimax_m3_native_profile_applied",
                "max_completion_tokens_present",
                f"output_policy:{output_policy}",
            ]
        )
        if payload.get("tools"):
            applied.append("tool_schema_present")
        if payload.get("tool_choice"):
            applied.append("tool_choice_present")

        trace.update(
            {
                "m3_mode": mode,
                "thinking_type": thinking_type,
                "reasoning_split": reasoning_split,
                "max_completion_tokens": payload.get("max_completion_tokens"),
                "service_tier": payload.get("service_tier") or "",
                "prompt_chars": _payload_text_chars(payload),
                "tool_schema_count": len(payload.get("tools") or []) if isinstance(payload.get("tools"), list) else 0,
                "payload_keys": sorted(payload.keys()),
            }
        )
        return trace

    def _mode(self, payload: dict[str, Any], system_tishi: str, yonghu_tishi: str) -> str:
        forced = _env_text("MINIMAX_M3_MODE", "MINIMAX_MODE").lower()
        if forced in VALID_MODES:
            return forced
        tier = os.environ.get("TIANGONG_LLM_BUDGET_TIER", "").strip().lower()
        if tier == "quick":
            return "quick"
        if tier == "deep":
            return "long"
        if tier == "balanced":
            return "agentic"

        text = f"{system_tishi}\n{yonghu_tishi}".lower()
        prompt_chars = _payload_text_chars(payload)
        long_threshold = _env_int("MINIMAX_M3_LONG_CONTEXT_CHARS") or 32000
        if prompt_chars >= long_threshold:
            return "long"
        long_markers = (
            "long context", "deep research", "architecture", "codebase", "repository",
            "debug", "trace", "log", "package", "build", "test", "refactor",
            "implement", "fix", "patch", "review", "analyze", "workspace",
            ".py", ".js", ".ts", "powershell", "terminal", "git ", "npm ",
        )
        if any(marker in text for marker in long_markers):
            return "agentic"
        default_mode = _env_text("MINIMAX_M3_DEFAULT_MODE").lower()
        if default_mode in VALID_MODES:
            return default_mode
        return "agentic" if payload.get("tools") else "quick"

    def _thinking_type(self, mode: str) -> str:
        forced = _env_text("MINIMAX_THINKING_TYPE", "MINIMAX_M3_THINKING_TYPE").lower()
        if forced in {"disabled", "adaptive"}:
            return forced
        if forced == "enabled":
            return "adaptive"
        return "adaptive" if mode == "long" else "disabled"

    def _reasoning_split(self, mode: str, thinking_type: str) -> bool:
        forced = _env_text("MINIMAX_M3_REASONING_SPLIT", "MINIMAX_REASONING_SPLIT").lower()
        if forced in TRUE_VALUES:
            return True
        if forced in FALSE_VALUES:
            return False
        return mode == "long" and thinking_type != "disabled"

    def _output_limit(self, mode: str, payload: dict[str, Any]) -> tuple[int, str]:
        explicit = _env_int("MINIMAX_MAX_COMPLETION_TOKENS", "TIANGONG_LLM_MAX_OUTPUT_TOKENS")
        if explicit:
            return _clamp_int(explicit, 512, 524288), "env:MINIMAX_MAX_COMPLETION_TOKENS"
        if mode == "quick":
            return _env_int("MINIMAX_M3_QUICK_MAX_COMPLETION_TOKENS") or 4096, "m3:quick"
        if mode == "long":
            value = _env_int("MINIMAX_M3_LONG_MAX_COMPLETION_TOKENS") or 32768
            return _clamp_int(value, 4096, 131072), "m3:long"
        value = _env_int("MINIMAX_M3_AGENTIC_MAX_COMPLETION_TOKENS", "TIANGONG_LLM_TOOL_MAX_OUTPUT_TOKENS")
        if value is None:
            prompt_chars = _payload_text_chars(payload)
            value = 16384 if prompt_chars >= 20000 else 8192
        return _clamp_int(value, 2048, 32768), "m3:agentic"

    def normalize_response(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"visible_text": "", "tool_calls": [], "response_shape": type(data).__name__}
        choice = (data.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            choice = {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        text = _strip_reasoning_text(_content_to_text(message.get("content")))
        tool_calls = _tool_calls_from_message(message)
        result = {
            "visible_text": text,
            "tool_calls": tool_calls,
            "finish_reason": str(choice.get("finish_reason") or ""),
            "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
            "assistant_message_present": bool(message),
        }
        result.update(_reasoning_ref(message, choice))
        return result

    def render_legacy_reply(self, data: Any) -> str:
        result = self.normalize_response(data)
        tool_calls = result.get("tool_calls") or []
        if tool_calls:
            visible_text = str(result.get("visible_text") or "").strip()
            parts: list[str] = [visible_text] if visible_text else []
            for call in tool_calls:
                name = str(call.get("name") or "").strip()
                args = call.get("arguments")
                if isinstance(args, dict):
                    args_text = json.dumps(args, ensure_ascii=False)
                else:
                    args_text = str(args or "{}")[:2000]
                parts.append(f"<tool_call>\n<name>{name}</name>\n<arguments>{args_text}</arguments>\n</tool_call>")
            return "\n".join(parts).strip()
        return str(result.get("visible_text") or "").strip()

    def response_metrics(self, data: Any) -> dict[str, Any]:
        result = self.normalize_response(data)
        metrics = {
            "m3_tool_call_count": len(result.get("tool_calls") or []),
            "m3_has_visible_text": bool(str(result.get("visible_text") or "").strip()),
            "m3_finish_reason": result.get("finish_reason") or "",
            "m3_reasoning_ref_present": bool(result.get("reasoning_ref")),
        }
        if result.get("reasoning_fields"):
            metrics["m3_reasoning_fields"] = result.get("reasoning_fields")
            metrics["m3_reasoning_chars"] = result.get("reasoning_chars")
        return metrics


MINIMAX_M3 = MiniMaxM3Adapter()
