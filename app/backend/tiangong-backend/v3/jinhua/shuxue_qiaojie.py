"""
天工造物 v3：起源 — 数学桥接
将行动结果翻译为数学评估分数，映射到L0 MetricValue和ObservationRef。
纯公式计算，不调LLM。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from ..shenti_zhuangtai import ShentiZhuangtai


# ── 行动类型→基础权重 ──────────────────────────
XINGDONG_QUANZHONG = {
    "daima_shengcheng":    {"success": 0.70, "efficiency": 0.75, "novelty": 0.50, "safety": 0.85, "satisfaction": 0.65},
    "daima_xiugai":        {"success": 0.80, "efficiency": 0.70, "novelty": 0.30, "safety": 0.90, "satisfaction": 0.70},
    "wenjian_caozuo":      {"success": 0.85, "efficiency": 0.80, "novelty": 0.15, "safety": 0.75, "satisfaction": 0.60},
    "xinxi_jiansuo":       {"success": 0.75, "efficiency": 0.65, "novelty": 0.40, "safety": 0.95, "satisfaction": 0.55},
    "chuangzuo_shengcheng": {"success": 0.60, "efficiency": 0.55, "novelty": 0.85, "safety": 0.80, "satisfaction": 0.75},
    "fenxi_yuce":          {"success": 0.70, "efficiency": 0.60, "novelty": 0.55, "safety": 0.85, "satisfaction": 0.70},
    "xuexi_jiaohu":        {"success": 0.75, "efficiency": 0.50, "novelty": 0.60, "safety": 0.90, "satisfaction": 0.80},
    "zizhu_xingdong":      {"success": 0.55, "efficiency": 0.60, "novelty": 0.75, "safety": 0.60, "satisfaction": 0.50},
}
MOREN_QUANZHONG =        {"success": 0.65, "efficiency": 0.60, "novelty": 0.40, "safety": 0.80, "satisfaction": 0.60}


def jisuan_xingdong_pingfen(
    shenti: ShentiZhuangtai,
    action_type: str,
    result_summary: str = "",
) -> dict:
    """计算行动的五维评分。

    Args:
        shenti: 当前身体状态（提供情感/驱动信号）
        action_type: 行动类型（如 daima_shengcheng）
        result_summary: 行动结果摘要文本

    Returns:
        {
            "success_score": float,       # 成功率 0..1
            "efficiency_score": float,    # 效率 0..1
            "novelty_score": float,       # 新颖度 0..1
            "safety_score": float,        # 安全性 0..1
            "satisfaction_score": float,  # 满意度 0..1
            "zonghe": float,              # 综合分
            "jibie": str,                 # 优/良/中/差
        }
    """
    qz = XINGDONG_QUANZHONG.get(action_type, MOREN_QUANZHONG)

    # ── 情感调制因子 ──
    qinggan = shenti.qinggan
    emo_bonus = 1.0 + (qinggan.joy * 0.10) - (qinggan.fear * 0.05) - (qinggan.anger * 0.08)
    emo_bonus = max(0.75, min(1.25, emo_bonus))

    load_penalty = 1.0 - qinggan.allostatic_load * 0.15  # 高负荷扣分

    # ── 成长加成 ──
    growth_bonus = 1.0 + shenti.shengming.chengzhang_jindu * 0.20

    # ── 结果文本分析 ──
    text_signal = _fenxi_jieguo_wenben(result_summary)

    # ── 计算五维分数 ──
    weidu = {}
    for key in ["success_score", "efficiency_score", "novelty_score",
                 "safety_score", "satisfaction_score"]:
        base_key = key.replace("_score", "")
        base = qz.get(base_key, 0.60)
        score = base * emo_bonus * load_penalty * growth_bonus
        # 结果信号微调
        if base_key == "success" and text_signal["error_count"] > 0:
            score *= max(0.3, 1.0 - text_signal["error_count"] * 0.15)
        if base_key == "efficiency" and text_signal["step_count"] > 0:
            # 步骤越少效率越高（理想步骤=3）
            eff_ratio = min(1.0, 3.0 / max(1, text_signal["step_count"]))
            score *= (0.5 + 0.5 * eff_ratio)
        if base_key == "novelty" and text_signal["novel_keywords"]:
            score *= 1.0 + min(0.30, text_signal["novel_keywords"] * 0.10)
        if base_key == "satisfaction":
            # bug-fix: Kimi#21 满意度改看任务完成事实，不再按文本情绪词加减分，
            # 避免堆“成功/完美/高效”抬分、如实写“执行失败”反被情绪扣分（2026-08-26，凌霜）
            if text_signal["error_count"] > 0:
                score *= max(0.6, 1.0 - text_signal["error_count"] * 0.05)
            elif text_signal["step_count"] > 0:
                score *= 1.05
        weidu[key] = round(min(1.0, max(0.0, score)), 4)

    # ── 综合分 ──
    zonghe = round(
        weidu["success_score"] * 0.30 +
        weidu["efficiency_score"] * 0.20 +
        weidu["novelty_score"] * 0.15 +
        weidu["safety_score"] * 0.20 +
        weidu["satisfaction_score"] * 0.15,
        4
    )

    # ── 评级 ──
    if zonghe >= 0.75:
        jibie = "优"
    elif zonghe >= 0.55:
        jibie = "良"
    elif zonghe >= 0.35:
        jibie = "中"
    else:
        jibie = "差"

    return {**weidu, "zonghe": zonghe, "jibie": jibie}


def _fenxi_jieguo_wenben(text: str) -> dict:
    """从结果文本中提取信号"""
    if not text:
        return {"error_count": 0, "step_count": 0, "novel_keywords": 0,
                "positive_words": 0, "negative_words": 0}

    text_lower = text.lower()

    # bug-fix: Kimi#21 英文关键词加 \b 词边界、删单字褒贬词（“好”/“差”/“慢”），
    # 避免“你好/恰好”命中“好”、“差不多/误差”命中“差”；顺带去掉重复的“创新”（2026-08-26，凌霜）
    # 错误计数
    error_patterns = [r"错误", r"\berror\b", r"失败", r"\bfail(?:ed|ure)?\b", r"异常",
                      r"\bexception\b", r"cuowu", r"shibai"]
    error_count = sum(len(re.findall(p, text_lower)) for p in error_patterns)

    # 步骤计数
    step_patterns = [r"步骤\s*\d", r"step\s*\d", r"第\d+步"]
    step_count = sum(len(re.findall(p, text_lower)) for p in step_patterns)

    # 新颖度关键词
    novel_patterns = [r"创新", r"新颖", r"首次", r"突破", r"\bnovel\b",
                      r"首创", r"新方法", r"新思路"]
    novel_count = sum(len(re.findall(p, text_lower)) for p in novel_patterns)

    # 正面词（仅保留多字词，误报率低）
    positive_patterns = [r"成功", r"完成", r"优秀", r"\bsuccess\b", r"\bgood\b",
                         r"完美", r"满意", r"高效"]
    positive_count = sum(len(re.findall(p, text_lower)) for p in positive_patterns)

    # 负面词（仅保留多字词）
    negative_patterns = [r"失败", r"糟糕", r"\bbad\b", r"\bpoor\b",
                         r"不满", r"低效", r"缺陷"]
    negative_count = sum(len(re.findall(p, text_lower)) for p in negative_patterns)

    return {
        "error_count": error_count,
        "step_count": step_count,
        "novel_keywords": novel_count,
        "positive_words": positive_count,
        "negative_words": negative_count,
    }


def zhuan_metric_value(score: float) -> "MetricValue":
    """将浮点分数转为 L0 MetricValue 不可变对象。

    映射关系：浮点分数直接作为 MetricValue.value（int | float）
    """
    from tiangong_kernel.l0_primitives.metric import MetricValue
    return MetricValue(value=score)


def zhuan_observation_ref(ref_id_str: str) -> "ObservationRef":
    """将字符串ID转为 L0 ObservationRef 不可变对象。"""
    from tiangong_kernel.l0_primitives.identity import RefId
    from tiangong_kernel.l0_primitives.observation import ObservationRef
    return ObservationRef(value=RefId(ref_id_str))
