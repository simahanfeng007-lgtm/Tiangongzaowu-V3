"""
天工造物 v3：起源 — HTTP API 客户端
真实LLM调用：构建请求 → 发送 → 解析 → 返回文本
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from tiangong_kernel.l4_action_grounding import optimization_profile_for

from ..shenti_zhuangtai import ShentiZhuangtai
from ..peizhi import (
    MOREN_PROVIDER,
    NENGLI_ZHUCE_LUJING,
    ZHUIZONG_LUJING,
    duqu_endpoint_api_miyao,
    duqu_model_ming,
    duqu_moren_provider,
    duqu_provider_base_url,
    infer_provider_id,
    normalize_provider_base_url,
)
from ..l0_ability_projection import read_json_compat, registry_rows, with_l0_projection
from ..endpoint_security import EndpointSecurityError, validate_model_endpoint
from .moxing_shipei import MOXING_SHIPEI
from .guge_ceng import GUGE
from .minimax_m3_adapter import MINIMAX_M3
from .deepseek_zhuanshu import (
    apply_deepseek_request_profile,
    deepseek_redact_reasoning,
    deepseek_response_metrics,
    deepseek_usage_metrics,
    is_deepseek_provider,
    normalize_deepseek_base_url,
)


HTTP_RETRY_LIMIT = 3
HTTP_RETRY_SLEEP_SECONDS = 0.5
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_NATIVE_AUDIO_FORMATS = {".mp3": "mp3", ".wav": "wav"}
_NATIVE_AUDIO_MAX_BYTES = int(
    (os.environ.get("TIANGONG_NATIVE_AUDIO_MAX_BYTES") or str(20 * 1024 * 1024)).strip()
)
# CC-loop structure: a single LLM streaming call must never wedge a worker
# thread indefinitely. SSE keepalive pings reset httpx's per-read timeout, so
# an overall wall-clock deadline is the only hard bound on one call.
_LLM_CALL_MAX_SECONDS = float(
    (os.environ.get("TIANGONG_LLM_CALL_MAX_SECONDS") or "300").strip() or "300"
)
if _LLM_CALL_MAX_SECONDS <= 0:
    _LLM_CALL_MAX_SECONDS = 300.0
L4_OPTIMIZATION_TRACE_PATH = ZHUIZONG_LUJING / "l4_model_optimization.jsonl"
_MODEL_ADAPTER_CORE: Any | None = None


class NativeAudioModelReply(str):
    """Text reply carrying a non-user-visible receipt for native audio input."""

    def __new__(cls, value: Any, evidence: dict[str, Any]):
        obj = super().__new__(cls, str(value or ""))
        obj.native_audio_evidence = dict(evidence or {})
        return obj


def _inject_native_audio_input(payload: dict[str, Any], paths: tuple[str, ...]) -> dict[str, Any] | None:
    """Attach one verified local audio object to the active model request."""
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


def _learned_skill_context(limit: int = 8) -> str:
    if os.environ.get("TIANGONG_ENABLE_LEARNED_SKILL_CONTEXT", "0").strip().lower() not in {"1", "true", "yes", "on"}:
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
    names = {
        part.strip()
        for part in re.split(r"[,，、\s]+", line)
        if part.strip()
    }
    return names


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
) -> tuple[list[dict] | None, str, dict[str, Any]]:
    """Use v3.5 model-protocol adapter when the model only sees omni_body."""
    if not _only_omni_body_tool(gongju_yuanshi):
        return None, "", {}
    adapter_core = _model_adapter_core()
    if adapter_core is None or not hasattr(adapter_core, "render_tool_schema"):
        return None, "", {}
    try:
        rendered = adapter_core.render_tool_schema(provider=provider_id, model=model_name)
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


def _response_content_text(data: Any) -> str:
    try:
        message = data.get("choices", [{}])[0].get("message", {})
        content = message.get("content", "")
    except Exception:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content") or ""
                if value:
                    parts.append(str(value))
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def _raw_tool_argument_objects(payload: Any, text: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_calls: list[Any] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("tool_calls"), list):
            raw_calls = payload.get("tool_calls") or []
        else:
            try:
                raw_calls = payload.get("choices", [{}])[0].get("message", {}).get("tool_calls", []) or []
            except Exception:
                raw_calls = []
        if raw_calls:
            for item in raw_calls:
                if not isinstance(item, dict):
                    rows.append({})
                    continue
                fn = item.get("function") if isinstance(item.get("function"), dict) else {}
                parsed = _json_loads_maybe(fn.get("arguments") or item.get("arguments") or {})
                rows.append(parsed if isinstance(parsed, dict) else {})
            return rows
        content = payload.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    parsed = block.get("input") if isinstance(block.get("input"), dict) else {}
                    rows.append(dict(parsed or {}))
            if rows:
                return rows
    value = str(text or "")
    for block in re.findall(r"<tool_call[^>]*>(.*?)</tool_call>", value, flags=re.I | re.S):
        match = re.search(r"<arguments[^>]*>(.*?)</arguments>", block, flags=re.I | re.S)
        parsed = _json_loads_maybe(match.group(1).strip() if match else "{}")
        rows.append(parsed if isinstance(parsed, dict) else {})
    return rows


def _canonical_to_omni_arguments(call: dict[str, Any], raw_args: dict[str, Any] | None = None) -> dict[str, Any]:
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

    # Canonical model output never carries authority.  Missing target/args are
    # normalized here so no provider-specific parser can turn an empty args
    # object into an absent field before the gateway signs the invocation.
    output: dict[str, Any] = {
        "action": str(call.get("action") or "").strip(),
        "target": str(call.get("target") or raw.get("target") or raw.get("path") or raw.get("url") or raw.get("resource") or "").strip(),
        "args": args_payload,
    }
    return output


def _parse_tool_calls_via_model_adapter(provider_id: str, model_name: str, data: Any) -> list[dict[str, Any]]:
    adapter_core = _model_adapter_core()
    if adapter_core is None or not hasattr(adapter_core, "parse_tool_calls"):
        return []
    text = _response_content_text(data)
    try:
        parsed = adapter_core.parse_tool_calls(payload=data, text=text, provider=provider_id, model=model_name)
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    raw_args_rows = _raw_tool_argument_objects(data, text)
    output: list[dict[str, Any]] = []
    for index, call in enumerate(parsed.get("calls") or []):
        if not isinstance(call, dict):
            continue
        args = _canonical_to_omni_arguments(call, raw_args_rows[index] if index < len(raw_args_rows) else {})
        if not args.get("action"):
            continue
        output.append({
            "name": "omni_body",
            "arguments": args,
            "adapter_profile": call.get("profile") or (parsed.get("profile") or {}).get("profile_id"),
            "adapter_call_id": call.get("call_id"),
        })
    return output


def _http_status_hint(status: int | None) -> str:
    if status == 400:
        return "请求参数不被服务商接受（HTTP 400）：请检查模型名与 Base URL 是否匹配该服务商。"
    if status in {401, 403}:
        return "API Key 或权限校验失败（HTTP 401/403）：请检查 API Key 是否正确、账号是否有权限或余额。"
    if status == 404:
        return "模型或接口地址不存在（HTTP 404）：请检查模型名和 Base URL 是否拼写正确。"
    if status == 429:
        return "限流或额度不足（HTTP 429）：账号额度可能已用完、每分钟/每日调用次数超限，或共享 Key 被占满；请到服务商控制台查看用量，稍后重试或切换模型。"
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


class HttpKehuduan:
    """HTTP API 客户端 — 真实的 LLM 调用"""

    def __init__(self, moren_provider: str = MOREN_PROVIDER):
        self._moren_provider = infer_provider_id(moren_provider)
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

    # ── 高层接口 ──────────────

    def llm_diaoyong(
        self,
        system_tishi: str,
        yonghu_tishi: str,
        provider_id: str | None = None,
        shenti: ShentiZhuangtai | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
        on_reasoning_chunk: Callable[[str], None] | None = None,
        prior_assistant_messages: list[str] | None = None,
        stable_user_message: str | None = None,
    ) -> str:
        """流式调用LLM，返回回复文本。

        on_text_chunk 用于前端流式展示可见正文；
        on_reasoning_chunk 转发模型的思考内容（如 DeepSeek reasoning_content），
        让“思考中”阶段也有逐段内容可看。
        """
        pid = infer_provider_id(provider_id or duqu_moren_provider(self._moren_provider))

        # Resolve and validate the endpoint before releasing any credential.
        base_url = normalize_provider_base_url(duqu_provider_base_url(pid))
        if is_deepseek_provider(pid, base_url=base_url):
            base_url = normalize_deepseek_base_url(base_url)
        parsed_base_url = urlparse(base_url)
        if base_url and (parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc):
            return _llm_error_text(
                "Base URL invalid",
                provider=pid,
                model=duqu_model_ming(pid),
                base_url=base_url,
                hint="Base URL should look like https://host/path, for example https://api.deepseek.com",
            )
        if not base_url:
            return f"[LLM错误: 未找到 {pid} 的endpoint映射]"
        try:
            endpoint_binding = validate_model_endpoint(pid, base_url, resolve_dns=True)
        except EndpointSecurityError as exc:
            return _llm_error_text(
                str(exc), provider=pid, model=duqu_model_ming(pid), base_url=base_url,
                hint="模型地址未通过HTTPS、DNS或私有地址安全校验",
            )
        miyao = duqu_endpoint_api_miyao(pid, base_url)
        if not miyao:
            scope = "官方供应商" if endpoint_binding.official else "该自定义地址"
            return f"[LLM错误: 未配置 {scope} 的独立API密钥]"
        model_name = duqu_model_ming(pid)

        # 用模型适配器构建请求
        st = shenti or ShentiZhuangtai()
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
            )
            if adapter_schema is not None:
                gongju_dingyi = adapter_schema
                if adapter_prompt:
                    effective_system_tishi += "\n\n[Model tool protocol]\n" + adapter_prompt
            else:
                adapter_profile = {}
                gongju_dingyi = _zhuanhuan_openai_geshi(gongju_yuanshi)
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
            native_audio_receipt = _inject_native_audio_input(
                payload,
                self._native_audio_paths.get(()),
            )
            if gongju_dingyi:
                payload["tool_choice"] = "auto"
            if pid == "minimax_m3":
                optimization_trace = MINIMAX_M3.apply_profile(payload, model_name, effective_system_tishi, yonghu_tishi)
            else:
                optimization_trace = _yingyong_l4_youhua(pid, payload, model_name, base_url)
            if isinstance(optimization_trace, dict):
                optimization_trace.update(_cache_prefix_observation(payload))
            if adapter_profile and isinstance(optimization_trace, dict):
                optimization_trace["model_tool_adapter"] = {
                    "profile_id": adapter_profile.get("profile_id"),
                    "schema_style": adapter_profile.get("schema_style"),
                    "call_style": adapter_profile.get("call_style"),
                    "confidence": adapter_profile.get("confidence"),
                }
        except ValueError as e:
            return f"[LLM错误: {e}]"

        def _native_reply(value: Any, *, visible: bool = False, reason: str = "") -> str:
            if not isinstance(native_audio_receipt, dict):
                return str(value or "")
            evidence = dict(native_audio_receipt)
            if reason:
                evidence["semantic_visibility"] = "unavailable"
                evidence["reason"] = reason[:300]
            elif visible and evidence.get("semantic_visibility") != "unavailable":
                evidence["semantic_visibility"] = "visible"
                evidence["reason"] = ""
            return NativeAudioModelReply(value, evidence)

        # 组装URL和请求头
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {miyao}",
            "Content-Type": "application/json",
        }
        payload["stream"] = True
        if pid == "minimax_m3" or is_deepseek_provider(pid):
            # Both official OpenAI-compatible APIs return the complete usage
            # object in a final choices=[] SSE chunk when this is enabled.
            # Without it, streaming calls cannot observe prompt/cache tokens.
            payload["stream_options"] = {"include_usage": True}

        last_error = ""
        data = None
        empty_length_rescued = False
        call_started_at = time.perf_counter()
        # CC-loop structure: never let one LLM call outlive the gateway's
        # absolute effect deadline; otherwise the watchdog marks the effect
        # AMBIGUOUS while this thread keeps occupying a pool slot.
        effective_llm_max_seconds = _LLM_CALL_MAX_SECONDS
        try:
            from contracts.reliability import current_execution_deadline_ms

            _deadline_ms = current_execution_deadline_ms()
            if _deadline_ms <= 0:
                _deadline_ms = int(os.environ.get("TIANGONG_EFFECT_DEADLINE_MS", "0") or "0")
            if _deadline_ms > 0:
                _remaining_s = (_deadline_ms - int(time.time() * 1000)) / 1000.0
                if _remaining_s <= 3600.0:
                    effective_llm_max_seconds = min(
                        effective_llm_max_seconds,
                        max(5.0, _remaining_s - 2.0),
                    )
        except Exception:
            pass
        for attempt in range(1, HTTP_RETRY_LIMIT + 1):
            if effective_llm_max_seconds > 0 and (time.perf_counter() - call_started_at) > effective_llm_max_seconds:
                return _native_reply(_llm_error_text(
                    f"llm_call_wall_clock_deadline exceeded {effective_llm_max_seconds:g}s",
                    provider=pid,
                    model=payload.get("model") or model_name,
                    base_url=base_url,
                    endpoint=url,
                    retry_count=attempt - 1,
                    hint="A single LLM call exceeded the platform wall-clock deadline; the run stopped instead of waiting forever.",
                ), reason="native_audio_model_deadline")
            started = time.perf_counter()
            try:
                validate_model_endpoint(pid, base_url, resolve_dns=True)
                accumulated_content: list[str] = []
                accumulated_tool_calls: list[dict[str, Any]] = []
                tool_call_index: dict[int, dict[str, Any]] = {}
                finish_reason = ""
                stream_usage: dict[str, Any] | None = None

                with self._kehuduan.stream("POST", url, json=payload, headers=headers) as resp:
                    latency_ms = round((time.perf_counter() - started) * 1000)
                    if resp.status_code in TRANSIENT_STATUS_CODES and attempt < HTTP_RETRY_LIMIT:
                        last_error = f"HTTP {resp.status_code}"
                        time.sleep(HTTP_RETRY_SLEEP_SECONDS * attempt)
                        continue
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if effective_llm_max_seconds > 0 and (time.perf_counter() - call_started_at) > effective_llm_max_seconds:
                            raise httpx.TimeoutException(
                                f"llm_call_wall_clock_deadline exceeded {effective_llm_max_seconds:g}s"
                            )
                        if not line or not line.startswith("data: "):
                            continue
                        chunk_text = line[6:]
                        if chunk_text == "[DONE]":
                            break
                        try:
                            chunk = json.loads(chunk_text)
                        except json.JSONDecodeError:
                            continue
                        chunk_usage = chunk.get("usage")
                        if isinstance(chunk_usage, dict):
                            stream_usage = chunk_usage
                        choices = chunk.get("choices") or []
                        for choice in choices:
                            delta = choice.get("delta") or {}
                            if "content" in delta and delta["content"]:
                                text = str(delta["content"])
                                accumulated_content.append(text)
                                if on_text_chunk:
                                    on_text_chunk(text)
                            reasoning = delta.get("reasoning_content")
                            if reasoning and on_reasoning_chunk:
                                on_reasoning_chunk(str(reasoning))
                            # 注意：reasoning_content 不再整段丢弃；
                            # 是否展示由上层 on_reasoning_chunk 决定。
                            # SSE 连接通过 duihua_qiaojie 的 300s ping 保活
                            tc_list = delta.get("tool_calls") or []
                            for tc in tc_list:
                                idx = tc.get("index", 0)
                                if idx not in tool_call_index:
                                    tool_call_index[idx] = {
                                        "id": tc.get("id") or "",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                entry = tool_call_index[idx]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    old_name = entry["function"]["name"]
                                    entry["function"]["name"] += fn["name"]
                                    if not old_name and entry["function"]["name"]:
                                        # Tool call name just resolved — notify
                                        pass
                                if fn.get("arguments"):
                                    entry["function"]["arguments"] += fn["arguments"]
                            if choice.get("finish_reason"):
                                finish_reason = str(choice["finish_reason"])
                latency_ms = round((time.perf_counter() - started) * 1000)
                accumulated_tool_calls = list(tool_call_index.values())
                usage_details = stream_usage.get("prompt_tokens_details") if isinstance(stream_usage, dict) else {}
                cached_tokens = int(usage_details.get("cached_tokens") or 0)
                prompt_tokens = int(stream_usage.get("prompt_tokens") or 0) if isinstance(stream_usage, dict) else 0
                if isinstance(optimization_trace, dict):
                    optimization_trace["cached_tokens"] = cached_tokens
                    optimization_trace["prompt_tokens"] = prompt_tokens
                    optimization_trace["cache_hit_rate"] = round(cached_tokens / prompt_tokens, 4) if prompt_tokens else 0.0
                # Build a synthetic response for the existing parsing pipeline
                visible_content = "".join(accumulated_content)
                data = {
                    "choices": [{
                        "index": 0,
                        "finish_reason": finish_reason or "stop",
                        "message": {
                            "role": "assistant",
                            "content": visible_content or None,
                            "tool_calls": accumulated_tool_calls if accumulated_tool_calls else None,
                        },
                    }],
                    "usage": stream_usage or {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "usage_unavailable": True,
                    },
                }
                if (
                    pid == "minimax_m3"
                    and finish_reason == "length"
                    and not visible_content.strip()
                    and not accumulated_tool_calls
                    and not empty_length_rescued
                    and attempt < HTTP_RETRY_LIMIT
                ):
                    empty_length_rescued = True
                    payload = _minimax_empty_length_rescue_payload(payload)
                    if isinstance(optimization_trace, dict):
                        optimization_trace["empty_length_rescue"] = True
                        optimization_trace["payload_keys"] = sorted(payload.keys())
                    continue
                _jilu_l4_youhua_zhuizong(
                    optimization_trace,
                    api_status="ok",
                    http_status=resp.status_code,
                    latency_ms=latency_ms,
                    retry_count=attempt - 1,
                    usage=stream_usage,
                    response_metrics=_tiqu_xiangying_zhibiao(data, pid),
                )
                break
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                last_error = f"HTTP {status}"
                # 安全读取响应体（流式响应需先 read）
                try:
                    error_body = e.response.text[:240]
                except Exception:
                    try:
                        error_body = e.response.read().decode("utf-8", errors="replace")[:240]
                    except Exception:
                        error_body = ""
                if status in TRANSIENT_STATUS_CODES and attempt < HTTP_RETRY_LIMIT:
                    time.sleep(HTTP_RETRY_SLEEP_SECONDS * attempt)
                    continue
                _jilu_l4_youhua_zhuizong(
                    optimization_trace,
                    api_status="http_error",
                    http_status=status,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    retry_count=attempt - 1,
                    error_preview=error_body,
                )
                return _native_reply(_llm_error_text(
                    last_error,
                    provider=pid,
                    model=payload.get("model") or model_name,
                    base_url=base_url,
                    endpoint=url,
                    http_status=status,
                    retry_count=attempt - 1,
                    response_preview=error_body,
                    hint=_http_status_hint(status),
                ), reason=f"native_audio_http_{status}")
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = str(e)
                if effective_llm_max_seconds > 0 and (time.perf_counter() - call_started_at) > effective_llm_max_seconds:
                    _jilu_l4_youhua_zhuizong(
                        optimization_trace,
                        api_status="wall_clock_deadline",
                        http_status=None,
                        latency_ms=round((time.perf_counter() - call_started_at) * 1000),
                        retry_count=attempt - 1,
                        error_preview=str(e)[:240],
                    )
                    return _native_reply(_llm_error_text(
                        last_error,
                        provider=pid,
                        model=payload.get("model") or model_name,
                        base_url=base_url,
                        endpoint=url,
                        retry_count=attempt - 1,
                        hint="A single LLM call exceeded the platform wall-clock deadline; the run stopped instead of waiting forever.",
                    ), reason="native_audio_model_deadline")
                if attempt < HTTP_RETRY_LIMIT:
                    time.sleep(HTTP_RETRY_SLEEP_SECONDS * attempt)
                    continue
                _jilu_l4_youhua_zhuizong(
                    optimization_trace,
                    api_status="transport_error",
                    http_status=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    retry_count=attempt - 1,
                    error_preview=str(e)[:240],
                )
                return _native_reply(_llm_error_text(
                    last_error,
                    provider=pid,
                    model=payload.get("model") or model_name,
                    base_url=base_url,
                    endpoint=url,
                    retry_count=attempt - 1,
                    hint="Network/proxy/DNS failed, or Base URL points to a non-API host.",
                ), reason="native_audio_transport_error")
            except Exception as e:
                last_error = str(e)
                if attempt < HTTP_RETRY_LIMIT:
                    time.sleep(HTTP_RETRY_SLEEP_SECONDS * attempt)
                    continue
                _jilu_l4_youhua_zhuizong(
                    optimization_trace,
                    api_status="exception",
                    http_status=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    retry_count=attempt - 1,
                    error_preview=str(e)[:240],
                )
                return _native_reply(_llm_error_text(
                    last_error,
                    provider=pid,
                    model=payload.get("model") or model_name,
                    base_url=base_url,
                    endpoint=url,
                    retry_count=attempt - 1,
                ), reason="native_audio_model_error")
        if data is None:
            return _native_reply(_llm_error_text(
                last_error or "empty_response",
                provider=pid,
                model=payload.get("model") or model_name,
                base_url=base_url,
                endpoint=url,
                retry_count=HTTP_RETRY_LIMIT - 1,
            ), reason="native_audio_empty_response")

        # Redact reasoning from DeepSeek responses — never leak to frontend
        if is_deepseek_provider(pid):
            deepseek_redact_reasoning(data)

        # 解析响应 — 优先取结构化 tool_calls
        gongju_diaoyong = []
        try:
            gongju_diaoyong = _parse_tool_calls_via_model_adapter(pid, model_name, data)
        except Exception:
            gongju_diaoyong = []

        if pid == "minimax_m3" and not gongju_diaoyong:
            try:
                rendered = MINIMAX_M3.render_legacy_reply(data)
                if rendered:
                    return _native_reply(rendered, visible="<tool_call>" not in rendered)
            except Exception:
                pass

        try:
            if not gongju_diaoyong:
                gongju_diaoyong = MOXING_SHIPEI.jiexi_gongju_diaoyong(pid, data)
        except Exception:
            pass
        
        # 兜底：直接从 OpenAI 格式读取 tool_calls
        if not gongju_diaoyong:
            try:
                raw_tcs = data["choices"][0]["message"].get("tool_calls", [])
                for tc in raw_tcs:
                    fn = tc.get("function", {})
                    gongju_diaoyong.append({
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "{}"),
                    })
            except Exception:
                pass
        
        if gongju_diaoyong:
            # 转为 gutong_ceng 能识别的 <tool_call> 文本格式
            tc_parts = []
            for tc in gongju_diaoyong:
                name = tc.get("name", "")
                args = tc.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                if isinstance(args, dict):
                    args_str = json.dumps(args, ensure_ascii=False)
                    tc_parts.append(
                        f"<tool_call>\n<name>{name}</name>\n<arguments>{args_str}</arguments>\n</tool_call>"
                    )
                else:
                    tc_parts.append(
                        f"<tool_call>\n<name>{name}</name>\n<arguments>{str(args)[:500]}</arguments>\n</tool_call>"
                    )
            neirong = "\n".join(tc_parts)
            return _native_reply(neirong.strip(), visible=False)
        
        # 没有结构化 tool_calls → 读 content
        try:
            neirong = data["choices"][0]["message"]["content"]
            finish_reason = str(data["choices"][0].get("finish_reason") or "")
            if neirong:
                return _native_reply(_qingli_sikao(neirong), visible=True)
            if finish_reason == "length":
                return _native_reply(
                    "[模型本轮达到输出上限但未返回可见内容；系统已启用低思考重试策略，请重新发送。]",
                    reason="native_audio_empty_response",
                )
        except (KeyError, IndexError, TypeError):
            pass
        
        try:
            guiyi = MOXING_SHIPEI.jiexi_xiangying(pid, data)
            neirong = guiyi.get("neirong", "")
            return _native_reply(_qingli_sikao(neirong), visible=True) if neirong else _native_reply("[空响应]", reason="native_audio_empty_response")
        except Exception:
            return _native_reply(
                f"[解析错误: {str(data)[:200]}]",
                reason="native_audio_response_parse_error",
            )

        # 最终兜底：空回复
        if not neirong or not neirong.strip():
            return _native_reply(
                "[空响应 — 模型未生成回复]",
                reason="native_audio_empty_response",
            )

    def zuowei_huidiao(
        self, provider_id: str | None = None
    ) -> Callable[[str, str], str]:
        """返回 (system, user, on_text_chunk=None) -> str 回调函数，供 GutongCeng 使用"""
        if provider_id:
            pid = infer_provider_id(provider_id)
            return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None: self.llm_diaoyong(
                system, user, pid, on_text_chunk=on_text_chunk, on_reasoning_chunk=on_reasoning_chunk,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message,
            )
        return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None: self.llm_diaoyong(
            system, user, on_text_chunk=on_text_chunk, on_reasoning_chunk=on_reasoning_chunk,
            prior_assistant_messages=prior_assistant_messages,
            stable_user_message=stable_user_message,
        )

    def guanbi(self):
        """关闭HTTP客户端"""
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
    current_limit = 0
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
    tier_limits = {
        "quick": 2048,
        "balanced": 8192,
        "deep": 32768,
    }
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
    """Consume L4 provider optimization advice without changing L4's no-live-call boundary."""
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
    row.update(
        {
            "api_status": api_status,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "retry_count": retry_count,
        }
    )
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
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
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
        with L4_OPTIMIZATION_TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _cache_prefix_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Record the exact provider cache prefix without persisting prompt text."""
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


def _tiqu_xiangying_zhibiao(data: Any, pid: str | None = None) -> dict[str, Any]:
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
                content = message.get("content")
                metrics["has_text_content"] = bool(str(content or "").strip())
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
    """G3 structured ModelAttemptResult envelope (non-production wrapper).

    The legacy backend result remains the compatible fallback; this additive
    envelope carries the verifiable provenance fields the Gateway
    ModelAttemptResult contract consumes.  It is intentionally marked
    ``production: false`` until the canary cutover.
    """
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
    """将 guge_ceng 的工具定义转为 OpenAI/DeepSeek 兼容格式"""
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
        for k, v in canshu.items():
            v_str = str(v)
            leixing = "string"
            miaoshu_wenben = v_str
            
            if "," in v_str:
                parts = v_str.split(",", 1)
                leixing_hint = parts[0].strip().lower()
                miaoshu_wenben = parts[1].strip() if len(parts) > 1 else v_str
                if "int" in leixing_hint or "integer" in leixing_hint:
                    leixing = "integer"
                elif "bool" in leixing_hint or "boolean" in leixing_hint:
                    leixing = "boolean"
                elif "number" in leixing_hint or "float" in leixing_hint:
                    leixing = "number"
            
            properties[k] = {
                "type": leixing,
                "description": miaoshu_wenben,
            }
            
            if "可选" not in v_str and "optional" not in v_str.lower():
                bixuan.append(k)
        
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
    return re.sub(r"<think>.*?</think>\s*", "", neirong, flags=re.DOTALL | re.IGNORECASE).strip()
