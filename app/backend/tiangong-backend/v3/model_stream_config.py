"""
大模型专属通道适配
═══════════════════════════════════
每个供应商的 streaming/thinking 能力配置。
统一处理 thinking 阶段的心跳，并把各家推理字段归一为仅后端可见的数据；
前端只接收自然语言 content。具体控制项按供应商能力分别声明，不假设完全一致。

P18.1 adds an EffectiveModelCapability resolver beside the legacy table.  The
legacy table remains compatibility data; connection/protocol authority lives in
model_endpoint.py.
"""
from __future__ import annotations
from dataclasses import dataclass
import fnmatch
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ModelStreamConfig:
    """模型 streaming 专属配置"""
    provider_id: str
    context_window: int
    # thinking 模式
    thinking_enabled: bool = True
    thinking_param: dict | None = None       # 传给 API 的 thinking 参数
    reasoning_split: bool = False             # 是否拆分 reasoning 流
    # delta 中 reasoning 字段名（默认 reasoning_content）
    reasoning_field: str = "reasoning_content"
    # 心跳间隔（秒），thinking 阶段每 N 秒发一次空事件
    heartbeat_interval: float = 15.0
    # Product-facing reasoning control.  These are real provider controls, not
    # presentation choices: private reasoning is never user-visible.
    reasoning_modes: tuple[str, ...] = ()
    default_reasoning_mode: str = "off"
    reasoning_control: str = "unsupported"

    def reasoning_capability(self) -> dict[str, Any]:
        modes = list(self.reasoning_modes)
        return {
            "supported": bool(modes),
            "control": self.reasoning_control,
            "modes": modes,
            "default_mode": self.default_reasoning_mode if self.default_reasoning_mode in modes else (modes[0] if modes else "off"),
            "private_reasoning_visible": False,
        }


# ═══════════════════════════════════════════
# 模型配置表 — 数据来源：各模型官方文档 + API 实测
# ═══════════════════════════════════════════

MODEL_STREAM_CONFIGS: dict[str, ModelStreamConfig] = {
    # ── DeepSeek V4 ──
    "deepseek_v4": ModelStreamConfig(
        provider_id="deepseek_v4",
        context_window=1_048_576,
        thinking_enabled=True,
        thinking_param={"type": "enabled"},
        reasoning_split=False,
        reasoning_field="reasoning_content",
        heartbeat_interval=15.0,
        reasoning_modes=("off", "high", "max"),
        default_reasoning_mode="high",
        reasoning_control="thinking_and_effort",
    ),

    # ── MiniMax M3 ──
    "minimax_m3": ModelStreamConfig(
        provider_id="minimax_m3",
        context_window=524_288,
        thinking_enabled=True,
        thinking_param={"type": "adaptive"},
        reasoning_split=True,
        reasoning_field="reasoning_content",
        heartbeat_interval=15.0,
        reasoning_modes=("off", "auto"),
        default_reasoning_mode="auto",
        reasoning_control="adaptive_toggle",
    ),

    # ── MiMo V2.5-Pro ──
    "mimo": ModelStreamConfig(
        provider_id="mimo",
        context_window=1_048_576,
        thinking_enabled=True,
        thinking_param=None,
        reasoning_split=False,
        reasoning_field="reasoning_content",
        heartbeat_interval=15.0,
        reasoning_modes=("off", "on"),
        default_reasoning_mode="on",
        reasoning_control="thinking_toggle",
    ),

    # ── GLM-5.2 ──
    "glm_5_2": ModelStreamConfig(
        provider_id="glm_5_2",
        context_window=1_048_576,
        thinking_enabled=True,
        thinking_param={"type": "enabled"},
        reasoning_split=False,
        reasoning_field="reasoning_content",
        heartbeat_interval=15.0,
        reasoning_modes=("off", "minimal", "low", "medium", "high", "xhigh", "max"),
        default_reasoning_mode="high",
        reasoning_control="thinking_and_effort",
    ),

    # ── GPT-5.6 ──
    "gpt_5_6": ModelStreamConfig(
        provider_id="gpt_5_6",
        context_window=1_048_576,
        thinking_enabled=True,
        thinking_param={"type": "enabled"},
        reasoning_split=False,
        reasoning_field="reasoning_content",
        heartbeat_interval=15.0,
        reasoning_modes=("off", "minimal", "low", "medium", "high", "xhigh"),
        default_reasoning_mode="medium",
        reasoning_control="reasoning_effort",
    ),
}


def get_model_stream_config(provider_id: str) -> ModelStreamConfig | None:
    """获取模型的 streaming 专属配置"""
    for key in (provider_id, provider_id.replace("_", "-"), provider_id.replace("-", "_")):
        if key in MODEL_STREAM_CONFIGS:
            return MODEL_STREAM_CONFIGS[key]
    return None


def get_context_window(provider_id: str) -> int:
    """获取模型上下文窗口。优先从 stream config 读取，fallback 128K"""
    cfg = get_model_stream_config(provider_id)
    if cfg:
        return cfg.context_window
    return 131072


def get_model_reasoning_capability(provider_id: str, model_name: str = "") -> dict[str, Any]:
    """Legacy authoritative user-configurable reasoning contract.

    P18.1 callers that need unknown-model behavior should use
    ``resolve_model_capability`` instead; this compatibility function keeps the
    previous conservative API for existing tests/UI until Stage D migration.
    """
    cfg = get_model_stream_config(provider_id)
    normalized_model = str(model_name or "").strip().lower()
    if cfg is not None and cfg.provider_id == "gpt_5_6" and normalized_model:
        known_reasoning_model = normalized_model.startswith(("gpt-5", "o1", "o3", "o4"))
        if not known_reasoning_model:
            cfg = None
    if cfg is None:
        return {
            "supported": False,
            "control": "unsupported",
            "modes": [],
            "default_mode": "off",
            "private_reasoning_visible": False,
        }
    capability = cfg.reasoning_capability()
    capability.update({"provider_id": cfg.provider_id, "model_name": str(model_name or "")})
    return capability


@dataclass(frozen=True, slots=True)
class EffectiveModelCapability:
    known_model: bool
    model_family: str
    protocol_family: str
    chat: bool
    streaming: bool
    native_tools: bool
    prompt_contract_tools: bool
    structured_output: bool
    parallel_tools: bool
    reasoning_modes: tuple[str, ...]
    reasoning_control: str
    provider_continuation: str
    server_state_optional: bool
    max_context: int
    max_output: int
    capability_sources: tuple[str, ...]
    capability_confidence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "known_model": self.known_model,
            "model_family": self.model_family,
            "protocol_family": self.protocol_family,
            "chat": self.chat,
            "streaming": self.streaming,
            "native_tools": self.native_tools,
            "prompt_contract_tools": self.prompt_contract_tools,
            "structured_output": self.structured_output,
            "parallel_tools": self.parallel_tools,
            "reasoning_modes": list(self.reasoning_modes),
            "reasoning_control": self.reasoning_control,
            "provider_continuation": self.provider_continuation,
            "server_state_optional": self.server_state_optional,
            "max_context": self.max_context,
            "max_output": self.max_output,
            "capability_sources": list(self.capability_sources),
            "capability_confidence": self.capability_confidence,
            "private_reasoning_visible": False,
        }


_MODEL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("deepseek_v4", ("deepseek-v4*", "deepseek-reasoner*", "deepseek-chat*")),
    ("glm_5_2", ("glm-5.2*", "glm-5*")),
    ("minimax_m3", ("minimax-m3*", "minimax-m2*")),
    ("mimo", ("mimo-v2.5*", "mimo-v2*")),
    ("gpt_5_6", ("gpt-5.6*", "gpt-5*", "o1*", "o3*", "o4*")),
)

_MODEL_MAX_OUTPUT = {
    "deepseek_v4": 32768,
    "glm_5_2": 32768,
    "minimax_m3": 32768,
    "mimo": 16384,
    "gpt_5_6": 32768,
}

_PROTOCOL_CAPS: dict[str, dict[str, Any]] = {
    "openai_chat_completions": {
        "chat": True,
        "streaming": True,
        "native_tools": True,
        "structured_output": True,
        "parallel_tools": True,
        "provider_continuation": "local_replay",
        "server_state_optional": False,
    },
    "openai_responses": {
        "chat": True,
        "streaming": True,
        "native_tools": True,
        "structured_output": True,
        "parallel_tools": True,
        "provider_continuation": "remote_optional",
        "server_state_optional": True,
    },
    "anthropic_messages": {
        "chat": True,
        "streaming": True,
        "native_tools": True,
        "structured_output": False,
        "parallel_tools": True,
        "provider_continuation": "local_replay",
        "server_state_optional": False,
    },
}


def _family_for_model(model_name: str, optimization_family: str) -> tuple[str, bool]:
    normalized = str(model_name or "").strip().lower()
    for family, patterns in _MODEL_PATTERNS:
        if normalized and any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns):
            return family, True
    # Optimization family is advisory evidence, not enough to declare an
    # arbitrary user model known.  It can identify the family label only.
    family = str(optimization_family or "").strip()
    return (family if family in MODEL_STREAM_CONFIGS else "unknown"), False


def resolve_model_capability(
    model_name: str,
    optimization_family: str,
    protocol_family: str,
    service_preset: str = "custom",
    endpoint_capability_override: Mapping[str, Any] | None = None,
) -> EffectiveModelCapability:
    """Resolve Model ∩ Protocol ∩ Endpoint ∩ Runtime capabilities.

    Unknown OpenAI-compatible endpoints never receive native-tools=true by
    assumption. A successful protocol probe may opt them in via
    ``endpoint_capability_override``. Unknown model reasoning is ``raw_optional``
    so an empty setting emits no reasoning/thinking field.
    """
    protocol = str(protocol_family or "").strip()
    protocol_caps = dict(_PROTOCOL_CAPS.get(protocol) or {})
    if not protocol_caps:
        protocol_caps = {
            "chat": False,
            "streaming": False,
            "native_tools": False,
            "structured_output": False,
            "parallel_tools": False,
            "provider_continuation": "none",
            "server_state_optional": False,
        }

    family, known_model = _family_for_model(model_name, optimization_family)
    stream_cfg = get_model_stream_config(family) if known_model else None
    if stream_cfg is not None:
        reasoning_modes = stream_cfg.reasoning_modes
        reasoning_control = stream_cfg.reasoning_control
        max_context = stream_cfg.context_window
        max_output = _MODEL_MAX_OUTPUT.get(family, 16384)
    else:
        reasoning_modes = ()
        reasoning_control = "raw_optional"
        max_context = 131072
        max_output = 16384

    endpoint = dict(endpoint_capability_override or {})
    sources = ["runtime:p18.1", f"protocol:{protocol}"]
    if known_model:
        sources.append(f"model:{family}")
    if endpoint:
        sources.append("endpoint:probe_or_override")

    def intersect_bool(name: str, model_default: bool = True) -> bool:
        value = bool(protocol_caps.get(name, False)) and bool(model_default)
        if name in endpoint:
            value = value and bool(endpoint[name])
        return value

    # Unknown endpoints/models must prove native tool support. Known official
    # service/model combinations may use protocol capability by default.
    endpoint_proves_native = endpoint.get("native_tools_supported") is True or endpoint.get("native_tools") is True
    preset_is_known = str(service_preset or "") in {"openai", "deepseek", "zhipu", "minimax", "mimo", "scnet"}
    native_tools = bool(protocol_caps.get("native_tools")) and (
        endpoint_proves_native or (known_model and preset_is_known)
    )
    if endpoint.get("native_tools_supported") is False or endpoint.get("native_tools") is False:
        native_tools = False

    prompt_contract_tools = bool(endpoint.get("prompt_contract_tools", not native_tools))
    structured_output = intersect_bool("structured_output", known_model)
    parallel_tools = native_tools and intersect_bool("parallel_tools", True)
    streaming = intersect_bool("streaming", True)
    chat = intersect_bool("chat", True)

    continuation = str(protocol_caps.get("provider_continuation") or "none")
    if endpoint.get("continuation_supported") is False:
        continuation = "none"
    elif isinstance(endpoint.get("provider_continuation"), str):
        continuation = str(endpoint["provider_continuation"])

    server_state_optional = bool(protocol_caps.get("server_state_optional"))
    if "server_state_optional" in endpoint:
        server_state_optional = server_state_optional and bool(endpoint["server_state_optional"])

    confidence = "high" if known_model and preset_is_known else ("medium" if endpoint else "conservative")
    return EffectiveModelCapability(
        known_model=known_model,
        model_family=family,
        protocol_family=protocol,
        chat=chat,
        streaming=streaming,
        native_tools=native_tools,
        prompt_contract_tools=prompt_contract_tools,
        structured_output=structured_output,
        parallel_tools=parallel_tools,
        reasoning_modes=reasoning_modes,
        reasoning_control=reasoning_control,
        provider_continuation=continuation,
        server_state_optional=server_state_optional,
        max_context=max_context,
        max_output=max_output,
        capability_sources=tuple(sources),
        capability_confidence=confidence,
    )
