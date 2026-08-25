"""
天工造物 v3：起源 — HTTP API 客户端

P18.1 production boundary:
configured Endpoint -> Protocol Transport -> ProviderTurnEnvelope.
L4 optimization remains advisory and can never choose the endpoint/protocol.
"""
from __future__ import annotations

import base64
import contextvars
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import httpx

from tiangong_kernel.l4_action_grounding import optimization_profile_for

from ..endpoint_security import EndpointSecurityError, validate_model_endpoint
from ..l0_ability_projection import read_json_compat, registry_rows, with_l0_projection
from ..model_endpoint import ProtocolFamily, duqu_model_endpoint_config
from ..model_protocol_contract import ModelTurnReply, ProviderTurnEnvelope
from ..model_stream_config import resolve_model_capability
from ..peizhi import (
    MOREN_PROVIDER,
    NENGLI_ZHUCE_LUJING,
    ZHUIZONG_LUJING,
    duqu_endpoint_api_miyao,
    duqu_model_reasoning_config,
    duqu_moren_provider,
    normalize_provider_identity,
)
from ..shenti_zhuangtai import ShentiZhuangtai
from .deepseek_zhuanshu import (
    apply_deepseek_request_profile,
    deepseek_response_metrics,
    deepseek_usage_metrics,
    is_deepseek_provider,
)
from .guge_ceng import GUGE
from .minimax_m3_adapter import MINIMAX_M3
from .model_transport_executor import TransportExecutionError, execute_streaming_turn
from .moxing_shipei import MOXING_SHIPEI


HTTP_RETRY_LIMIT = 3
HTTP_RETRY_SLEEP_SECONDS = 0.5
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_NATIVE_AUDIO_FORMATS = {".mp3": "mp3", ".wav": "wav"}
_NATIVE_AUDIO_MAX_BYTES = int(
    (os.environ.get("TIANGONG_NATIVE_AUDIO_MAX_BYTES") or str(20 * 1024 * 1024)).strip()
)
# One LLM call is always bounded by both this ceiling and the current Gateway
# effect deadline. Transport SSE keepalives cannot extend that authority.
_LLM_CALL_MAX_SECONDS = float(
    (os.environ.get("TIANGONG_LLM_CALL_MAX_SECONDS") or "300").strip() or "300"
)
if _LLM_CALL_MAX_SECONDS <= 0:
    _LLM_CALL_MAX_SECONDS = 300.0
L4_OPTIMIZATION_TRACE_PATH = ZHUIZONG_LUJING / "l4_model_optimization.jsonl"
_MODEL_ADAPTER_CORE: Any | None = None


class NativeAudioModelReply(ModelTurnReply):
    """ProviderTurnEnvelope carrying a non-user-visible native-audio receipt."""

    def __new__(cls, value: Any, evidence: dict[str, Any], **turn: Any):
        obj = super().__new__(cls, value, **turn)
        obj.native_audio_evidence = dict(evidence or {})
        return obj


def _stream_reasoning_text(delta: dict[str, Any]) -> str:
    """Compatibility helper used by focused reasoning tests and old adapters."""
    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return value
    details = delta.get("reasoning_details")
    if isinstance(details, list):
        return "".join(
            str(item.get("text") or "")
            for item in details
            if isinstance(item, dict) and item.get("text")
        )
    return ""


def _render_tool_turn_legacy(visible_text: str, tool_calls: list[dict[str, Any]]) -> str:
    """Serialize structured calls for the old Gutong parser only."""
    parts: list[str] = [str(visible_text or "").strip()] if str(visible_text or "").strip() else []
    for call in tool_calls:
        name = str(call.get("name") or "")
        args = call.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                pass
        args_text = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)[:500]
        parts.append(
            f"<tool_call>\n<name>{name}</name>\n<arguments>{args_text}</arguments>\n</tool_call>"
        )
    return "\n".join(parts).strip()


def _apply_reasoning_profile(
    pid: str,
    payload: dict[str, Any],
    *,
    base_url: str,
    model_name: str,
) -> dict[str, Any]:
    """Apply one persisted product setting through the L4 optimization family."""
    profile = duqu_model_reasoning_config(pid, base_url, model_name)
    mode = str(profile.get("effective_mode") or "off")
    if not bool(profile.get("supported")):
        # Unknown models deliberately receive no forced thinking/reasoning field.
        return {
            "reasoning_mode": "unsupported",
            "reasoning_control": "unsupported",
            "reasoning_binding_key": profile.get("binding_key") or "",
            "private_reasoning_visible": False,
        }
    control = str(profile.get("control") or "unsupported")
    if control == "thinking_and_effort":
        payload["thinking"] = {"type": "disabled" if mode == "off" else "enabled"}
        if mode == "off":
            payload.pop("reasoning_effort", None)
        else:
            payload["reasoning_effort"] = mode
    elif control == "thinking_toggle":
        payload["thinking"] = {"type": "disabled" if mode == "off" else "enabled"}
        payload.pop("reasoning_effort", None)
    elif control == "adaptive_toggle":
        if mode == "off":
            payload["thinking"] = {"type": "disabled"}
            payload.pop("reasoning_split", None)
        # auto deliberately preserves the adapter's task-sensitive profile.
    elif control == "reasoning_effort":
        payload["reasoning_effort"] = "none" if mode == "off" else mode
    elif control == "reasoning_effort_always_on":
        payload["reasoning_effort"] = mode
    return {
        "reasoning_mode": mode,
        "reasoning_control": control,
        "reasoning_binding_key": profile.get("binding_key") or "",
        "private_reasoning_visible": False,
    }



def _apply_endpoint_raw_reasoning(endpoint: Any, capability: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply user-entered unknown-model reasoning only when non-empty."""
    if getattr(capability, "reasoning_control", "") != "raw_optional":
        return {"raw_reasoning_sent": False}
    mode = str(getattr(endpoint, "reasoning_mode", "") or "").strip()
    if not mode:
        payload.pop("reasoning_effort", None)
        # Do not manufacture thinking={} for unknown models.
        if isinstance(payload.get("thinking"), dict) and not payload.get("thinking"):
            payload.pop("thinking", None)
        return {"raw_reasoning_sent": False, "raw_reasoning_mode": ""}
    if endpoint.protocol_family in {"openai_chat_completions", "openai_responses"}:
        payload["reasoning_effort"] = mode
    elif endpoint.protocol_family == "anthropic_messages":
        payload["thinking"] = {"type": mode}
    return {"raw_reasoning_sent": True, "raw_reasoning_mode": mode}

def _inject_native_audio_input(payload: dict[str, Any], paths: tuple[str, ...]) -> dict[str, Any] | None:
    """Attach verified local audio bytes to an OpenAI-chat-shaped canonical payload."""
    if not paths:
        return None
    path = Path(paths[0]).expanduser().resolve(strict=False)
    audio_format = _NATIVE_AUDIO_FORMATS.get(path.suffix.lower())
    if not audio_format:
        return {
            "schema": "tiangong.v3.native_audio_receipt.v1",
            "semantic_visibility": "unavailable",
            "reason": "unsupported_audio_container",
            "path": str(path),
        }
    try:
        body = path.read_bytes()
    except Exception as exc:
        return {
            "schema": "tiangong.v3.native_audio_receipt.v1",
            "semantic_visibility": "unavailable",
            "reason": f"audio_read_failed:{type(exc).__name__}",
            "path": str(path),
        }
    if not body or len(body) > max(1, _NATIVE_AUDIO_MAX_BYTES):
        return {
            "schema": "tiangong.v3.native_audio_receipt.v1",
            "semantic_visibility": "unavailable",
            "reason": "audio_size_not_supported",
            "path": str(path),
            "size_bytes": len(body),
        }
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    user_message = next(
        (item for item in reversed(messages) if isinstance(item, dict) and item.get("role") == "user"),
        None,
    )
    if user_message is None:
        return None
    content = user_message.get("content")
    text = content if isinstance(content, str) else ""
    user_message["content"] = [
        {"type": "text", "text": text},
        {
            "type": "input_audio",
            "input_audio": {
                "data": base64.b64encode(body).decode("ascii"),
                "format": audio_format,
            },
        },
    ]
    return {
        "schema": "tiangong.v3.native_audio_receipt.v1",
        "semantic_visibility": "submitted",
        "reason": "",
        "path": str(path),
        "format": audio_format,
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _native_audio_unavailable_for_protocol(paths: tuple[str, ...], protocol_family: str) -> dict[str, Any] | None:
    if not paths:
        return None
    return {
        "schema": "tiangong.v3.native_audio_receipt.v1",
        "semantic_visibility": "unavailable",
        "reason": f"native_audio_not_certified_for:{protocol_family}",
        "path": str(Path(paths[0]).expanduser().resolve(strict=False)),
    }


def _learned_skill_context(limit: int = 8) -> str:
    # 默认开启：激活技能对模型可见是"先读"的前提（capability-based
    # 权限的前半段）。此前默认关闭导致理念管道建好但进水阀关着。
    # 环境变量仍可显式关闭（0/false/off/no）。
    if os.environ.get("TIANGONG_ENABLE_LEARNED_SKILL_CONTEXT", "1").strip().lower() in {"0", "false", "off", "no"}:
        return ""
    raw = read_json_compat(NENGLI_ZHUCE_LUJING, {})
    lines: list[str] = []
    for item in registry_rows(raw):
        ability = with_l0_projection(item)
        l0 = ability.get("l0") if isinstance(ability.get("l0"), dict) else {}
        if not l0.get("model_visible_skill"):
            continue
        name = _safe_error_part(ability.get("mingcheng") or ability.get("name") or ability.get("id"), 80)
        desc = _safe_error_part(ability.get("miaoshu") or ability.get("description") or "", 180)
        skill_ref = _safe_error_part(l0.get("skill_ref"), 80)
        release_state = _safe_error_part(l0.get("tool_release_state"), 40)
        model_visible_tool = "true" if l0.get("model_visible_tool") else "false"
        lines.append(
            f"- {name}: {desc} "
            f"(skill_ref={skill_ref}; tool_release_state={release_state}; model_visible_tool={model_visible_tool})"
        )
        if len(lines) >= max(1, int(limit or 1)):
            break
    if not lines:
        return ""
    return (
        "\n\n[已学习能力 / L0 SkillRef]\n"
        "这些条目是学习能力上下文；只有 model_visible_tool=true 且出现在 tools 中的条目才可作为工具调用。\n"
        + "\n".join(lines)
    )


def _safe_error_part(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def _allowed_tools_from_system_prompt(system_tishi: str) -> set[str] | None:
    text = str(system_tishi or "")
    marker = "本轮系统只向你暴露该 skill 对应工具:"
    if marker not in text:
        return None
    tail = text.rsplit(marker, 1)[-1]
    line = tail.splitlines()[0].strip()
    if not line or line.lower() == "none":
        return set()
    return {
        part.strip()
        for part in re.split(r"[,，、\s]+", line)
        if part.strip()
    }


def _omni_body_skill_root_for_model_adapter() -> Path | None:
    candidates: list[Path] = []
    forced = os.environ.get("TIANGONG_OMNI_BODY_ROOT")
    if forced:
        candidates.append(Path(forced).expanduser())
    candidates.extend([
        Path.home() / ".tiangong" / "v3" / "omni_body_skill",
        Path(__file__).resolve().parents[1] / "omni_body_skill",
        Path(__file__).resolve().parents[1] / "bundled_skills" / "omni_body_skill",
    ])
    for candidate in candidates:
        try:
            root = candidate.resolve(strict=False)
            if (root / "model_adapters" / "core.py").exists():
                return root
        except Exception:
            continue
    return None


def _model_adapter_core() -> Any | None:
    global _MODEL_ADAPTER_CORE
    if _MODEL_ADAPTER_CORE is not None:
        return _MODEL_ADAPTER_CORE
    root = _omni_body_skill_root_for_model_adapter()
    if root is None:
        return None
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    os.environ.setdefault("TIANGONG_OMNI_BODY_ROOT", root_text)
    try:
        from model_adapters import core as adapter_core  # type: ignore
    except Exception:
        return None
    _MODEL_ADAPTER_CORE = adapter_core
    return adapter_core


def _only_omni_body_tool(gongju_yuanshi: list[dict]) -> bool:
    names = {
        str(item.get("name") or "").strip()
        for item in gongju_yuanshi or []
        if str(item.get("name") or "").strip()
    }
    return names == {"omni_body"}


def _render_omni_body_schema_via_model_adapter(
    gongju_yuanshi: list[dict],
    *,
    provider_id: str,
    model_name: str,
    protocol_family: str = ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
    native_tools: bool = True,
) -> tuple[list[dict] | None, str, dict[str, Any]]:
    """Render the authoritative Omni schema for the selected wire protocol."""
    if not _only_omni_body_tool(gongju_yuanshi):
        return None, "", {}
    adapter_core = _model_adapter_core()
    if adapter_core is None or not hasattr(adapter_core, "render_tool_schema"):
        return None, "", {}
    kwargs: dict[str, Any] = {"provider": provider_id, "model": model_name}
    if not native_tools:
        kwargs["style"] = "xml_prompt_contract"
    elif protocol_family == ProtocolFamily.OPENAI_RESPONSES.value:
        kwargs.update({"profile_id": "gpt_openai_responses", "style": "openai_responses_tools"})
    elif protocol_family == ProtocolFamily.ANTHROPIC_MESSAGES.value:
        kwargs["style"] = "anthropic_tools"
    try:
        rendered = adapter_core.render_tool_schema(**kwargs)
    except Exception:
        return None, "", {}
    if not isinstance(rendered, dict):
        return None, "", {}
    profile = rendered.get("profile") if isinstance(rendered.get("profile"), dict) else {}
    tool_schema = rendered.get("tool_schema")
    if isinstance(tool_schema, list):
        return tool_schema, "", dict(profile or {})
    if isinstance(tool_schema, str):
        return [], tool_schema, dict(profile or {})
    return None, "", dict(profile or {})


def _json_loads_maybe(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        return {}


def _canonical_to_omni_arguments(call: dict[str, Any], raw_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Strip model-authored authority fields before the Gate sees a tool call."""
    raw = raw_args if isinstance(raw_args, dict) else {}
    known = {
        "action", "command", "operation", "op", "target", "path", "url", "resource",
        "args", "payload", "confirm", "confirmed",
        "workspace", "allow_shell", "allow_python", "allow_absolute_paths",
    }
    nested = raw.get("args") if isinstance(raw.get("args"), dict) else raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    extras = {k: v for k, v in raw.items() if k not in known}
    args_payload: dict[str, Any] = {}
    if extras:
        args_payload.update(extras)
    if isinstance(nested, dict):
        args_payload.update(nested)
    if isinstance(call.get("args"), dict):
        args_payload.update(call.get("args") or {})
    return {
        "action": str(call.get("action") or "").strip(),
        "target": str(call.get("target") or raw.get("target") or raw.get("path") or raw.get("url") or raw.get("resource") or "").strip(),
        "args": args_payload,
    }


def _canonicalize_provider_turn(turn: ProviderTurnEnvelope) -> ProviderTurnEnvelope:
    """Keep provider IDs/bindings while enforcing the existing Omni input boundary."""
    calls: list[dict[str, Any]] = []
    changed = False
    for raw_call in turn.tool_calls:
        call = dict(raw_call)
        if str(call.get("name") or "") == "omni_body" and isinstance(call.get("arguments"), dict):
            raw_args = dict(call.get("arguments") or {})
            canonical = {
                "action": raw_args.get("action") or raw_args.get("command") or raw_args.get("operation") or raw_args.get("op"),
                "target": raw_args.get("target") or raw_args.get("path") or raw_args.get("url") or raw_args.get("resource") or "",
                "args": raw_args.get("args") if isinstance(raw_args.get("args"), dict) else raw_args.get("payload") if isinstance(raw_args.get("payload"), dict) else {},
            }
            call["arguments"] = _canonical_to_omni_arguments(canonical, raw_args)
            changed = True
        calls.append(call)
    if not changed:
        return turn
    legacy = _render_tool_turn_legacy(_qingli_sikao(turn.visible_text), calls)
    return ProviderTurnEnvelope(
        legacy,
        turn_id=turn.turn_id,
        provider_identity=turn.provider_identity,
        service_preset=turn.service_preset,
        protocol_family=turn.protocol_family,
        optimization_family=turn.optimization_family,
        model_id=turn.model_id,
        visible_text=_qingli_sikao(turn.visible_text),
        tool_calls=calls,
        tool_call_bindings=turn.tool_call_bindings,
        private_reasoning=turn.private_reasoning,
        provider_continuation_state=turn.provider_continuation_state,
        finish_reason=turn.finish_reason,
        stop_semantics=turn.stop_semantics,
        usage=turn.usage,
        stream_metadata=turn.stream_metadata,
        retry_semantics=turn.retry_semantics,
        raw_response_hash=turn.raw_response_hash,
        provider_id=turn.provider_id,
    )


def _http_status_hint(status: int | None) -> str:
    if status == 400:
        return "请求参数不被服务商接受（HTTP 400）：请检查模型名、接口协议与 Base URL 是否匹配。"
    if status in {401, 403}:
        return "API Key 或权限校验失败（HTTP 401/403）：请检查 API Key、Header 策略、账号权限或余额。"
    if status == 404:
        return "模型或协议接口不存在（HTTP 404）：请检查模型名、Base URL 和接口协议。"
    if status == 429:
        return "限流或额度不足（HTTP 429）：请到服务商控制台检查用量，稍后重试或切换模型。"
    if status and status >= 500:
        return "服务商服务异常（HTTP 5xx）：请稍后重试或切换模型。"
    return ""


def _llm_error_text(
    reason: str,
    *,
    provider: str,
    model: str | None = None,
    base_url: str | None = None,
    endpoint: str | None = None,
    http_status: int | None = None,
    retry_count: int | None = None,
    response_preview: str | None = None,
    hint: str | None = None,
) -> str:
    parts = [f"reason={_safe_error_part(reason)}", f"provider={_safe_error_part(provider)}"]
    if model:
        parts.append(f"model={_safe_error_part(model)}")
    if base_url:
        parts.append(f"base_url={_safe_error_part(base_url)}")
    if endpoint:
        parts.append(f"endpoint={_safe_error_part(endpoint, 320)}")
    if http_status is not None:
        parts.append(f"http_status={http_status}")
    if retry_count is not None:
        parts.append(f"retry={retry_count}")
    if hint:
        parts.append(f"hint={_safe_error_part(hint, 320)}")
    if response_preview:
        parts.append(f"response={_safe_error_part(response_preview, 320)}")
    return "[LLM错误: " + "; ".join(parts) + "]"


def _effective_llm_deadline_seconds() -> float:
    """Resolve the Runtime call ceiling against the current Gateway effect deadline."""
    effective_llm_max_seconds = _LLM_CALL_MAX_SECONDS
    try:
        from contracts.reliability import current_execution_deadline_ms

        deadline_ms = current_execution_deadline_ms()
        if deadline_ms <= 0:
            deadline_ms = int(os.environ.get("TIANGONG_EFFECT_DEADLINE_MS", "0") or "0")
        if deadline_ms > 0:
            remaining = (deadline_ms - int(time.time() * 1000)) / 1000.0
            if remaining <= 3600.0:
                effective_llm_max_seconds = min(
                    effective_llm_max_seconds,
                    max(5.0, remaining - 2.0),
                )
    except Exception:
        pass
    return effective_llm_max_seconds


def _turn_kwargs(turn: ProviderTurnEnvelope) -> dict[str, Any]:
    return {
        "turn_id": turn.turn_id,
        "provider_identity": turn.provider_identity,
        "service_preset": turn.service_preset,
        "protocol_family": turn.protocol_family,
        "optimization_family": turn.optimization_family,
        "model_id": turn.model_id,
        "visible_text": turn.visible_text,
        "tool_calls": list(turn.tool_calls),
        "tool_call_bindings": list(turn.tool_call_bindings),
        "private_reasoning": turn.private_reasoning,
        "provider_continuation_state": turn.provider_continuation_state,
        "finish_reason": turn.finish_reason,
        "stop_semantics": turn.stop_semantics,
        "usage": turn.usage,
        "stream_metadata": turn.stream_metadata,
        "retry_semantics": turn.retry_semantics,
        "raw_response_hash": turn.raw_response_hash,
        "provider_id": turn.provider_id,
    }


def _with_native_audio(turn: ProviderTurnEnvelope, evidence: dict[str, Any] | None, *, visible: bool = False, reason: str = "") -> ProviderTurnEnvelope:
    if not isinstance(evidence, dict):
        return turn
    receipt = dict(evidence)
    if reason:
        receipt["semantic_visibility"] = "unavailable"
        receipt["reason"] = reason[:300]
    elif visible and receipt.get("semantic_visibility") != "unavailable":
        receipt["semantic_visibility"] = "visible"
        receipt["reason"] = ""
    return NativeAudioModelReply(str(turn), receipt, **_turn_kwargs(turn))


def _error_turn(
    value: str,
    *,
    provider_identity: str,
    service_preset: str,
    protocol_family: str,
    optimization_family: str,
    model_name: str,
) -> ProviderTurnEnvelope:
    return ProviderTurnEnvelope(
        value,
        provider_identity=provider_identity,
        service_preset=service_preset,
        protocol_family=protocol_family,
        optimization_family=optimization_family,
        model_id=model_name,
        visible_text="",
        provider_id=optimization_family,
        finish_reason="error",
        stop_semantics="error",
    )


class HttpKehuduan:
    """唯一生产 HTTP 客户端；协议差异只经 Transport Registry。"""

    def __init__(self, moren_provider: str = MOREN_PROVIDER):
        self._moren_provider = normalize_provider_identity(moren_provider)
        trust_env = os.environ.get("TIANGONG_HTTP_TRUST_ENV", "0").strip().lower() in {"1", "true", "yes", "on"}
        self._kehuduan = httpx.Client(timeout=120.0, follow_redirects=False, trust_env=trust_env)
        self._allowed_tool_names = contextvars.ContextVar("tiangong_allowed_tool_names", default=None)
        self._disable_tools = contextvars.ContextVar("tiangong_disable_tools", default=False)
        self._native_audio_paths = contextvars.ContextVar("tiangong_native_audio_paths", default=())

    @contextmanager
    def scoped_tools(self, allowed_tool_names: list[str] | set[str] | tuple[str, ...] | None = None, disable_tools: bool = False):
        clean_names = None
        if allowed_tool_names is not None:
            clean_names = {str(name).strip() for name in allowed_tool_names if str(name or "").strip()}
        token_names = self._allowed_tool_names.set(clean_names)
        token_disable = self._disable_tools.set(bool(disable_tools))
        try:
            yield
        finally:
            self._disable_tools.reset(token_disable)
            self._allowed_tool_names.reset(token_names)

    @contextmanager
    def scoped_native_audio(self, paths: list[str] | tuple[str, ...] | None = None):
        clean_paths = tuple(str(path).strip() for path in (paths or []) if str(path or "").strip())
        token = self._native_audio_paths.set(clean_paths)
        try:
            yield
        finally:
            self._native_audio_paths.reset(token)

    def llm_diaoyong(
        self,
        system_tishi: str,
        yonghu_tishi: str,
        provider_id: str | None = None,
        shenti: ShentiZhuangtai | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
        on_reasoning_chunk: Callable[[str], None] | None = None,
        prior_assistant_messages: list[Any] | None = None,
        stable_user_message: str | None = None,
        prior_provider_turn: Any = None,
        provider_tool_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """Resolve endpoint first, optimize second, then execute one native protocol turn."""
        requested_identity = normalize_provider_identity(
            provider_id or duqu_moren_provider(self._moren_provider)
        )
        try:
            endpoint = duqu_model_endpoint_config(requested_identity)
        except Exception as exc:
            return ModelTurnReply(
                _llm_error_text(str(exc), provider=requested_identity),
                provider_identity=requested_identity,
                provider_id="",
                finish_reason="error",
            )

        provider_identity = endpoint.provider_identity
        pid = endpoint.optimization_family
        base_url = endpoint.base_url
        model_name = endpoint.model_name
        parsed_base_url = urlparse(base_url)
        if base_url and (parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc):
            return _error_turn(
                _llm_error_text(
                    "Base URL invalid",
                    provider=provider_identity,
                    model=model_name,
                    base_url=base_url,
                    hint="Base URL should look like https://host/path.",
                ),
                provider_identity=provider_identity,
                service_preset=endpoint.service_preset,
                protocol_family=endpoint.protocol_family,
                optimization_family=pid,
                model_name=model_name,
            )
        if not base_url:
            return _error_turn(
                f"[LLM错误: 未找到 {provider_identity} 的endpoint映射]",
                provider_identity=provider_identity,
                service_preset=endpoint.service_preset,
                protocol_family=endpoint.protocol_family,
                optimization_family=pid,
                model_name=model_name,
            )
        try:
            endpoint_binding = validate_model_endpoint(provider_identity, base_url, resolve_dns=True)
        except EndpointSecurityError as exc:
            return _error_turn(
                _llm_error_text(
                    str(exc), provider=provider_identity, model=model_name, base_url=base_url,
                    hint="模型地址未通过HTTPS、DNS或私有地址安全校验",
                ),
                provider_identity=provider_identity,
                service_preset=endpoint.service_preset,
                protocol_family=endpoint.protocol_family,
                optimization_family=pid,
                model_name=model_name,
            )
        miyao = duqu_endpoint_api_miyao(provider_identity, base_url)
        if not miyao:
            scope = "官方供应商" if endpoint_binding.official else "该自定义地址"
            return _error_turn(
                f"[LLM错误: 未配置 {scope} 的独立API密钥]",
                provider_identity=provider_identity,
                service_preset=endpoint.service_preset,
                protocol_family=endpoint.protocol_family,
                optimization_family=pid,
                model_name=model_name,
            )

        endpoint_cap_override = endpoint.endpoint_overrides.get("capability_override") if isinstance(endpoint.endpoint_overrides, Mapping) else None
        capability = resolve_model_capability(
            model_name,
            pid,
            endpoint.protocol_family,
            endpoint.service_preset,
            endpoint_cap_override if isinstance(endpoint_cap_override, Mapping) else None,
        )

        st = shenti or ShentiZhuangtai()
        native_audio_receipt: dict[str, Any] | None = None
        try:
            learned_skill_context = _learned_skill_context()
            effective_system_tishi = system_tishi + learned_skill_context if learned_skill_context else system_tishi
            if self._disable_tools.get(False):
                gongju_yuanshi = []
            else:
                gongju_yuanshi = GUGE.suoyou_gongju()
                allowed_names = self._allowed_tool_names.get(None)
                if allowed_names is None:
                    allowed_names = _allowed_tools_from_system_prompt(effective_system_tishi)
                if allowed_names is not None:
                    gongju_yuanshi = [
                        item for item in gongju_yuanshi
                        if str(item.get("name") or "").strip() in allowed_names
                    ]

            adapter_schema, adapter_prompt, adapter_profile = _render_omni_body_schema_via_model_adapter(
                gongju_yuanshi,
                provider_id=pid,
                model_name=model_name,
                protocol_family=endpoint.protocol_family,
                native_tools=capability.native_tools,
            )
            if adapter_schema is not None:
                gongju_dingyi = adapter_schema
                if adapter_prompt:
                    effective_system_tishi += "\n\n[Model tool protocol]\n" + adapter_prompt
            else:
                adapter_profile = {}
                # Canonical request form remains Chat-shaped internally; each
                # Transport converts it into its own native wire schema.
                gongju_dingyi = _zhuanhuan_openai_geshi(gongju_yuanshi) if capability.native_tools else []
                if gongju_yuanshi and not capability.native_tools:
                    effective_system_tishi += (
                        "\n\n[Model tool protocol]\n"
                        "当前端点未证明原生 function calling 能力；不得伪装 native tool。"
                    )

            payload = MOXING_SHIPEI.goujian_qingqiu(
                pid,
                effective_system_tishi,
                yonghu_tishi,
                st,
                gongju_dingyi=gongju_dingyi,
                model_name=model_name,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message,
            )
            if isinstance(prior_provider_turn, ProviderTurnEnvelope) and provider_tool_results:
                # Internal transport metadata only. Transports remove these keys
                # before network release and bind results through ToolCallBinding.
                payload["__provider_turn"] = prior_provider_turn
                payload["__provider_tool_results"] = [
                    dict(item) for item in provider_tool_results if isinstance(item, dict)
                ]
            audio_paths = self._native_audio_paths.get(())
            if endpoint.protocol_family == ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value:
                native_audio_receipt = _inject_native_audio_input(payload, audio_paths)
            else:
                native_audio_receipt = _native_audio_unavailable_for_protocol(audio_paths, endpoint.protocol_family)

            if gongju_dingyi:
                payload["tool_choice"] = "auto"
            if pid == "minimax_m3":
                optimization_trace = MINIMAX_M3.apply_profile(
                    payload, model_name, effective_system_tishi, yonghu_tishi
                )
            else:
                optimization_trace = _yingyong_l4_youhua(pid, payload, model_name, base_url)
            reasoning_trace = _apply_reasoning_profile(
                pid, payload, base_url=base_url, model_name=model_name
            )
            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)
            reasoning_trace.update(raw_reasoning_trace)
            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)
            reasoning_trace.update(raw_reasoning_trace)
            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)
            reasoning_trace.update(raw_reasoning_trace)
            if isinstance(optimization_trace, dict):
                optimization_trace.update(reasoning_trace)
                optimization_trace.update(_cache_prefix_observation(payload))
                optimization_trace["endpoint_authority"] = {
                    "provider_identity": provider_identity,
                    "service_preset": endpoint.service_preset,
                    "protocol_family": endpoint.protocol_family,
                    "optimization_family": pid,
                    "config_fingerprint": endpoint.config_fingerprint,
                }
                optimization_trace["effective_capability"] = capability.as_dict()
            if adapter_profile and isinstance(optimization_trace, dict):
                optimization_trace["model_tool_adapter"] = {
                    "profile_id": adapter_profile.get("profile_id"),
                    "schema_style": adapter_profile.get("schema_style"),
                    "call_style": adapter_profile.get("call_style"),
                    "confidence": adapter_profile.get("confidence"),
                }
        except ValueError as exc:
            return _with_native_audio(
                _error_turn(
                    f"[LLM错误: {exc}]",
                    provider_identity=provider_identity,
                    service_preset=endpoint.service_preset,
                    protocol_family=endpoint.protocol_family,
                    optimization_family=pid,
                    model_name=model_name,
                ),
                native_audio_receipt,
                reason="native_audio_request_build_error",
            )

        # Keep Chat final-usage semantics explicit for inherited cache telemetry.
        if endpoint.protocol_family == ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value and (
            pid == "minimax_m3" or is_deepseek_provider(pid)
        ):
            payload["stream_options"] = {"include_usage": True}

        effective_llm_max_seconds = _effective_llm_deadline_seconds()
        try:
            executed = execute_streaming_turn(
                client=self._kehuduan,
                endpoint=endpoint,
                api_key=miyao,
                canonical_payload=payload,
                on_text_chunk=on_text_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
                retry_limit=HTTP_RETRY_LIMIT,
                retry_sleep_seconds=HTTP_RETRY_SLEEP_SECONDS,
                transient_status_codes=TRANSIENT_STATUS_CODES,
                max_wall_clock_seconds=effective_llm_max_seconds,
            )
        except TransportExecutionError as exc:
            api_status = "wall_clock_deadline" if exc.deadline_exceeded else (
                "http_error" if exc.http_status is not None else "transport_error"
            )
            _jilu_l4_youhua_zhuizong(
                optimization_trace,
                api_status=api_status,
                http_status=exc.http_status,
                latency_ms=exc.latency_ms,
                retry_count=exc.retry_count,
                error_preview=exc.response_preview or exc.reason,
            )
            hint = (
                "A single LLM call exceeded the platform wall-clock deadline; the run stopped instead of waiting forever."
                if exc.deadline_exceeded
                else _http_status_hint(exc.http_status)
                if exc.http_status is not None
                else "Network/proxy/DNS failed, or Base URL points to a non-API host."
            )
            error = _error_turn(
                _llm_error_text(
                    exc.reason,
                    provider=provider_identity,
                    model=model_name,
                    base_url=base_url,
                    endpoint=exc.url,
                    http_status=exc.http_status,
                    retry_count=exc.retry_count,
                    response_preview=exc.response_preview,
                    hint=hint,
                ),
                provider_identity=provider_identity,
                service_preset=endpoint.service_preset,
                protocol_family=endpoint.protocol_family,
                optimization_family=pid,
                model_name=model_name,
            )
            return _with_native_audio(
                error,
                native_audio_receipt,
                reason="native_audio_model_deadline" if exc.deadline_exceeded else "native_audio_model_error",
            )

        turn = _canonicalize_provider_turn(executed.turn)

        # MiniMax legacy safety rescue is retained, but only after a turn with
        # no tool call/effect, so it cannot duplicate a committed side effect.
        if (
            pid == "minimax_m3"
            and turn.finish_reason == "length"
            and not turn.visible_text.strip()
            and not turn.tool_calls
        ):
            rescue_payload = _minimax_empty_length_rescue_payload(payload)
            try:
                rescue = execute_streaming_turn(
                    client=self._kehuduan,
                    endpoint=endpoint,
                    api_key=miyao,
                    canonical_payload=rescue_payload,
                    on_text_chunk=on_text_chunk,
                    on_reasoning_chunk=on_reasoning_chunk,
                    retry_limit=HTTP_RETRY_LIMIT,
                    retry_sleep_seconds=HTTP_RETRY_SLEEP_SECONDS,
                    transient_status_codes=TRANSIENT_STATUS_CODES,
                    max_wall_clock_seconds=effective_llm_max_seconds,
                )
                turn = _canonicalize_provider_turn(rescue.turn)
                optimization_trace["empty_length_rescue"] = True
            except TransportExecutionError:
                pass

        usage = dict(turn.usage or {})
        usage_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        cached_tokens = int(
            usage.get("cached_input_tokens")
            or usage.get("cache_read_input_tokens")
            or usage_details.get("cached_tokens")
            or usage_details.get("cache_read_tokens")
            or 0
        )
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        if isinstance(optimization_trace, dict):
            optimization_trace["cached_tokens"] = cached_tokens
            optimization_trace["prompt_tokens"] = prompt_tokens
            optimization_trace["cache_hit_rate"] = round(cached_tokens / prompt_tokens, 4) if prompt_tokens else 0.0

        _jilu_l4_youhua_zhuizong(
            optimization_trace,
            api_status="ok",
            http_status=executed.http_status,
            latency_ms=executed.latency_ms,
            retry_count=executed.retry_count,
            usage=usage,
            response_metrics=_turn_response_metrics(turn, pid),
        )

        if turn.visible_text:
            cleaned = _qingli_sikao(turn.visible_text)
            if cleaned != turn.visible_text:
                turn = ProviderTurnEnvelope(
                    _render_tool_turn_legacy(cleaned, list(turn.tool_calls)) if turn.tool_calls else cleaned,
                    **{**_turn_kwargs(turn), "visible_text": cleaned},
                )
        visible = bool(turn.visible_text.strip()) and not turn.tool_calls
        return _with_native_audio(turn, native_audio_receipt, visible=visible)

    def zuowei_huidiao(
        self, provider_id: str | None = None
    ) -> Callable[[str, str], str]:
        """返回 Gutong 使用的 LLM 回调；provider 参数保持真实配置身份。"""
        if provider_id:
            identity = normalize_provider_identity(provider_id)
            return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None, prior_provider_turn=None, provider_tool_results=None: self.llm_diaoyong(
                system,
                user,
                identity,
                on_text_chunk=on_text_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message,
                prior_provider_turn=prior_provider_turn,
                provider_tool_results=provider_tool_results,
            )
        return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None, prior_provider_turn=None, provider_tool_results=None: self.llm_diaoyong(
            system,
            user,
            on_text_chunk=on_text_chunk,
            on_reasoning_chunk=on_reasoning_chunk,
            prior_assistant_messages=prior_assistant_messages,
            stable_user_message=stable_user_message,
            prior_provider_turn=prior_provider_turn,
            provider_tool_results=provider_tool_results,
        )

    def guanbi(self):
        self._kehuduan.close()


def _duqu_zhengshu_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _duqu_zhengshu_env_or_none(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _payload_text_chars(payload: dict[str, Any]) -> int:
    total = 0
    for message in payload.get("messages") or []:
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += sum(len(str(item)) for item in content)
    return total


def _minimax_empty_length_rescue_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rescued = dict(payload)
    rescued["thinking"] = {"type": "disabled"}
    rescued.pop("reasoning_split", None)
    try:
        current_limit = int(rescued.get("max_completion_tokens") or 0)
    except Exception:
        current_limit = 0
    rescued["max_completion_tokens"] = max(current_limit, 8192)
    rescue_note = (
        "上一轮没有返回可见回复且达到输出上限。"
        "本轮必须优先返回用户可见答案；避免长篇隐藏思考。"
        "如需工具，只调用一个最必要的工具。"
    )
    messages = rescued.get("messages")
    if isinstance(messages, list):
        cloned: list[Any] = []
        injected = False
        for message in messages:
            if isinstance(message, dict):
                item = dict(message)
                if not injected and item.get("role") == "system":
                    item["content"] = str(item.get("content") or "") + "\n\n[输出保障]\n" + rescue_note
                    injected = True
                cloned.append(item)
            else:
                cloned.append(message)
        if not injected:
            cloned.insert(0, {"role": "system", "content": "[输出保障]\n" + rescue_note})
        rescued["messages"] = cloned
    return rescued


def _jianyi_shuchu_shangxian(pid: str, payload: dict[str, Any], provider_env_name: str) -> tuple[int, str]:
    explicit = _duqu_zhengshu_env_or_none(provider_env_name) or _duqu_zhengshu_env_or_none("TIANGONG_LLM_MAX_OUTPUT_TOKENS")
    if explicit:
        return explicit, f"env:{provider_env_name}"
    tier = os.environ.get("TIANGONG_LLM_BUDGET_TIER", "adaptive").strip().lower() or "adaptive"
    tier_limits = {"quick": 2048, "balanced": 8192, "deep": 32768}
    if tier in tier_limits:
        return tier_limits[tier], f"tier:{tier}"
    if payload.get("tools"):
        return _duqu_zhengshu_env("TIANGONG_LLM_TOOL_MAX_OUTPUT_TOKENS", 8192), "adaptive:tool"
    prompt_chars = _payload_text_chars(payload)
    if prompt_chars <= 4000:
        return 4096, "adaptive:short_context"
    if prompt_chars <= 12000:
        return 8192, "adaptive:medium_context"
    return 16384, "adaptive:long_context"


def _yingyong_l4_youhua(pid: str, payload: dict[str, Any], model_name: str, base_url: str = "") -> dict[str, Any]:
    """Consume L4 optimization advice without changing endpoint authority."""
    applied: list[str] = []
    trace: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": pid,
        "model": payload.get("model"),
        "configured_model": model_name,
        "l4_profile_consumed": False,
        "advisory_only": True,
        "applied": applied,
    }
    if payload.get("model") == model_name:
        applied.append("model_name_preserved")
    try:
        profile = optimization_profile_for(pid)
        trace["l4_profile_consumed"] = True
        trace["advisory_only"] = profile.advisory_only
        trace["observability_metrics"] = list(profile.observability_metrics)
        trace["cache_strategy_hint_count"] = len(profile.cache_strategy_hints)
        trace["structured_output_hint_count"] = len(profile.structured_output_hints)
        trace["tool_calling_hint_count"] = len(profile.tool_calling_hints)
        applied.append("optimization_profile_loaded")
    except Exception as exc:
        trace["profile_error"] = str(exc)[:200]
        trace["payload_keys"] = sorted(payload.keys())
        return trace

    if is_deepseek_provider(pid, model_name=model_name):
        output_limit, output_policy = _jianyi_shuchu_shangxian(pid, payload, "DEEPSEEK_MAX_TOKENS")
        deepseek_trace = apply_deepseek_request_profile(
            payload,
            model_name=model_name,
            output_limit=output_limit,
            output_policy=output_policy,
        )
        applied.extend(deepseek_trace.get("applied") or [])
        final_thinking = payload.get("thinking", {}).get("type") if isinstance(payload.get("thinking"), dict) else payload.get("thinking")
        trace.update({
            "deepseek_chain": True,
            "thinking_type": final_thinking or deepseek_trace.get("thinking_type"),
            "removed_params": deepseek_trace.get("removed_params") or [],
            "cache_prefix_chars": deepseek_trace.get("cache_prefix_chars"),
            "cache_prefix_message_count": deepseek_trace.get("cache_prefix_message_count"),
        })
    elif pid == "minimax_m3":
        minimax_thinking = (os.environ.get("MINIMAX_THINKING_TYPE") or os.environ.get("MINIMAX_M3_THINKING_TYPE") or "adaptive").strip().lower()
        if minimax_thinking == "enabled":
            minimax_thinking = "adaptive"
        if minimax_thinking not in {"adaptive", "disabled"}:
            minimax_thinking = "adaptive"
        payload.setdefault("thinking", {"type": minimax_thinking})
        output_limit, output_policy = _jianyi_shuchu_shangxian(pid, payload, "MINIMAX_MAX_COMPLETION_TOKENS")
        payload.setdefault("max_completion_tokens", output_limit)
        applied.extend(["minimax_thinking_profile_applied", "max_completion_tokens_present", f"output_policy:{output_policy}"])
    elif pid == "glm_5_2":
        output_limit, output_policy = _jianyi_shuchu_shangxian(pid, payload, "ZAI_MAX_TOKENS")
        payload.setdefault("max_tokens", output_limit)
        applied.extend(["glm_structured_reasoning_profile_applied", "max_tokens_present", f"output_policy:{output_policy}"])
    elif pid == "mimo":
        output_limit, output_policy = _jianyi_shuchu_shangxian(pid, payload, "MIMO_MAX_TOKENS")
        payload.setdefault("max_tokens", output_limit)
        applied.extend(["mimo_open_weight_profile_applied", "max_tokens_present", f"output_policy:{output_policy}"])
    elif pid == "gpt_5_6":
        output_limit, output_policy = _jianyi_shuchu_shangxian(pid, payload, "OPENAI_MAX_TOKENS")
        payload.setdefault("max_tokens", output_limit)
        applied.extend(["openai_compatible_profile_applied", "max_tokens_present", f"output_policy:{output_policy}"])
    else:
        output_limit, output_policy = _jianyi_shuchu_shangxian(pid, payload, "TIANGONG_LLM_MAX_TOKENS")
        payload.setdefault("max_tokens", output_limit)
        applied.extend(["generic_output_cap_present", f"output_policy:{output_policy}"])

    if payload.get("tools"):
        applied.append("tool_schema_present")
    if payload.get("tool_choice"):
        applied.append("tool_choice_present")
    trace["payload_keys"] = sorted(payload.keys())
    return trace


def _jilu_l4_youhua_zhuizong(
    trace: dict[str, Any],
    *,
    api_status: str,
    http_status: int | None,
    latency_ms: int,
    retry_count: int,
    usage: dict[str, Any] | None = None,
    response_metrics: dict[str, Any] | None = None,
    error_preview: str | None = None,
) -> None:
    if not trace:
        return
    if not trace.get("l4_profile_consumed") and os.environ.get("TIANGONG_TRACE_UNSUPPORTED_PROVIDER", "").strip() != "1":
        return
    row = dict(trace)
    row.update({
        "api_status": api_status,
        "http_status": http_status,
        "latency_ms": latency_ms,
        "retry_count": retry_count,
    })
    if isinstance(usage, dict):
        prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        completion_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        cached_input_tokens = (
            usage.get("cached_input_tokens")
            or usage.get("cache_read_input_tokens")
            or prompt_details.get("cached_tokens")
            or prompt_details.get("cache_read_tokens")
            or 0
        )
        reasoning_tokens = (
            usage.get("reasoning_tokens")
            or completion_details.get("reasoning_tokens")
            or 0
        )
        row["usage"] = {
            "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
            "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cached_input_tokens": cached_input_tokens,
            "reasoning_tokens": reasoning_tokens,
            "usage_keys": sorted(str(key) for key in usage.keys()),
        }
        if is_deepseek_provider(str(row.get("provider") or "")):
            row["usage"].update(deepseek_usage_metrics(usage))
    if response_metrics:
        row["response_metrics"] = response_metrics
    if error_preview:
        row["error_preview"] = error_preview
    try:
        L4_OPTIMIZATION_TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with L4_OPTIMIZATION_TRACE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _cache_prefix_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Record the stable provider cache prefix without persisting prompt text."""
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    stable_messages: list[dict[str, Any]] = []
    prefix_chars = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
            break
        content = message.get("content")
        if isinstance(content, str):
            prefix_chars += len(content)
        elif isinstance(content, list):
            prefix_chars += sum(len(str(item)) for item in content)
        stable_messages.append(message)
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    canonical = json.dumps(
        {"tools": tools, "messages": stable_messages},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "cache_prefix_sha256": hashlib.sha256(canonical).hexdigest(),
        "cache_prefix_chars": prefix_chars,
        "cache_prefix_message_count": len(stable_messages),
        "cache_prefix_tool_count": len(tools),
    }


def _turn_response_metrics(turn: ProviderTurnEnvelope, pid: str | None = None) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "response_shape": "ProviderTurnEnvelope",
        "tool_call_count": len(turn.tool_calls),
        "has_text_content": bool(turn.visible_text.strip()),
        "finish_reason": turn.finish_reason or "unknown",
        "protocol_family": turn.protocol_family,
        "provider_continuation_mode": turn.provider_continuation_mode,
    }
    # DeepSeek/MiniMax legacy metrics only understand Chat-shaped responses;
    # native generic metrics above remain authoritative for other protocols.
    if turn.protocol_family == ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value:
        synthetic = {
            "choices": [{
                "finish_reason": turn.finish_reason,
                "message": {
                    "content": turn.visible_text,
                    "tool_calls": [
                        {
                            "id": call.get("id"),
                            "function": {
                                "name": call.get("name"),
                                "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                        for call in turn.tool_calls
                    ],
                },
            }],
            "usage": dict(turn.usage or {}),
        }
        if pid == "minimax_m3":
            try:
                metrics.update(MINIMAX_M3.response_metrics(synthetic))
            except Exception:
                pass
        elif is_deepseek_provider(pid):
            try:
                metrics.update(deepseek_response_metrics(synthetic))
            except Exception:
                pass
    return metrics


def _tiqu_xiangying_zhibiao(data: Any, pid: str | None = None) -> dict[str, Any]:
    """Legacy response metrics helper retained for compatibility tests/tools."""
    if not isinstance(data, dict):
        return {"response_shape": type(data).__name__}
    metrics: dict[str, Any] = {
        "choice_count": len(data.get("choices") or []) if isinstance(data.get("choices"), list) else 0,
        "tool_call_count": 0,
        "has_text_content": False,
        "finish_reason": "unknown",
    }
    try:
        choice = (data.get("choices") or [{}])[0]
        if isinstance(choice, dict):
            metrics["finish_reason"] = str(choice.get("finish_reason") or "unknown")
            message = choice.get("message")
            if isinstance(message, dict):
                metrics["has_text_content"] = bool(str(message.get("content") or "").strip())
                tool_calls = message.get("tool_calls")
                metrics["tool_call_count"] = len(tool_calls) if isinstance(tool_calls, list) else 0
    except Exception:
        pass
    if pid == "minimax_m3":
        try:
            metrics.update(MINIMAX_M3.response_metrics(data))
        except Exception:
            pass
    elif is_deepseek_provider(pid):
        try:
            metrics.update(deepseek_response_metrics(data))
        except Exception:
            pass
    return metrics


def structured_model_attempt_result(
    *,
    provider: str,
    model: str,
    text: str,
    transport_run_id: str | None = None,
    provider_response_id: str | None = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """G3 structured ModelAttemptResult envelope (non-production wrapper)."""
    output_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "schema": "tiangong.v3.model_attempt_result_wrapper.v1",
        "production": False,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "transport_run_id": transport_run_id,
        "provider_response_id": provider_response_id,
        "output_text_sha256": output_sha256,
        "finish_reason": str(finish_reason or "stop"),
        "text_object_id": "obj_" + output_sha256,
    }


def _zhuanhuan_openai_geshi(gongju_yuanshi: list[dict]) -> list[dict]:
    """Convert Guge tool declarations into the canonical Chat-shaped form."""
    jieguo = []
    for gj in gongju_yuanshi:
        name = gj.get("name", "")
        miaoshu = gj.get("description", "")
        canshu = gj.get("parameters", {})
        if (
            isinstance(canshu, dict)
            and canshu.get("type") == "object"
            and isinstance(canshu.get("properties"), dict)
        ):
            openai_def = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": miaoshu,
                    "parameters": {
                        "type": "object",
                        "properties": canshu.get("properties") or {},
                        "required": canshu.get("required") if isinstance(canshu.get("required"), list) else [],
                    },
                },
            }
            if isinstance(canshu.get("additionalProperties"), bool):
                openai_def["function"]["parameters"]["additionalProperties"] = canshu["additionalProperties"]
            jieguo.append(openai_def)
            continue

        properties = {}
        bixuan = []
        for key, value in canshu.items():
            value_text = str(value)
            leixing = "string"
            miaoshu_wenben = value_text
            if "," in value_text:
                parts = value_text.split(",", 1)
                hint = parts[0].strip().lower()
                miaoshu_wenben = parts[1].strip() if len(parts) > 1 else value_text
                if "int" in hint or "integer" in hint:
                    leixing = "integer"
                elif "bool" in hint or "boolean" in hint:
                    leixing = "boolean"
                elif "number" in hint or "float" in hint:
                    leixing = "number"
            properties[key] = {"type": leixing, "description": miaoshu_wenben}
            if "可选" not in value_text and "optional" not in value_text.lower():
                bixuan.append(key)
        openai_def = {
            "type": "function",
            "function": {
                "name": name,
                "description": miaoshu,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": bixuan if bixuan else [],
                },
            },
        }
        if name.startswith("skill_"):
            openai_def["function"]["parameters"]["properties"] = {
                "action": {"type": "string", "description": "执行动作"}
            }
            openai_def["function"]["parameters"]["required"] = ["action"]
        jieguo.append(openai_def)
    return jieguo


def _qingli_sikao(neirong: str) -> str:
    """Remove provider reasoning blocks that arrive inside assistant content."""
    return re.sub(r"<think>.*?</think>\s*", "", str(neirong or ""), flags=re.DOTALL | re.IGNORECASE).strip()
