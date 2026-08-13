"""
大模型专属通道适配
═══════════════════════════════════
每个供应商的 streaming/thinking 能力配置。
统一处理 thinking 阶段的心跳，并把各家推理字段归一为仅后端可见的数据；
前端只接收自然语言 content。具体控制项按供应商能力分别声明，不假设完全一致。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


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
    # 官方: api-docs.deepseek.com/guides/thinking_mode
    # thinking 默认启用，delta 中 reasoning_content 与 content 同级
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
    # 实测: thinking=adaptive + reasoning_split=true 时 reasoning_content 出现
    # 账号实际 512K（更高 tier 才有 1M）
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
    # 官方: platform.xiaomimimo.com — "Deep Thinking" 能力，OpenAI 兼容
    "mimo": ModelStreamConfig(
        provider_id="mimo",
        context_window=1_048_576,
        thinking_enabled=True,
        thinking_param=None,          # MiMo 默认即开启深度思考
        reasoning_split=False,
        reasoning_field="reasoning_content",
        heartbeat_interval=15.0,
        reasoning_modes=("off", "on"),
        default_reasoning_mode="on",
        reasoning_control="thinking_toggle",
    ),

    # ── GLM-5.2 ──
    # 官方: open.bigmodel.cn/pricing — "深度思考"模式，1M 上下文
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
    # OpenAI 兼容，thinking 模式标准
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
    # 归一化: 去掉下划线前缀变体
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
    """Return the authoritative user-configurable reasoning contract."""
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
