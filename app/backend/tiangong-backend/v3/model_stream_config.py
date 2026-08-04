"""
大模型专属通道适配
═══════════════════════════════════
每个模型的 staring/thinking 行为配置。
统一处理 thinking 阶段的心跳——避免前端 180s 超时。

实测依据:
  DeepSeek V4:   API 实测 — delta 含 reasoning_content
  MiniMax M3:    API 实测 — reasoning_split=true 时 delta 含 reasoning_content
  Mimo:          Mimo-V2.5-Pro 文档标注 "Deep Thinking"，OpenAI 兼容
  GLM-5.2:       Zhipu 官方文档标注 "深度思考"，OpenAI 兼容
  GPT-5.5:       OpenAI 兼容，thinking 模式同标准

所有模型在 thinking 阶段的 streaming delta 均使用 reasoning_content 字段。
"""
from __future__ import annotations
from dataclasses import dataclass, field
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
    ),

    # ── GPT-5.5 ──
    # OpenAI 兼容，thinking 模式标准
    "gpt_5_5": ModelStreamConfig(
        provider_id="gpt_5_5",
        context_window=1_048_576,
        thinking_enabled=True,
        thinking_param={"type": "enabled"},
        reasoning_split=False,
        reasoning_field="reasoning_content",
        heartbeat_interval=15.0,
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
