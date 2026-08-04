"""
天工造物 v3：起源 — 模型适配层
接入L4适配器体系。纯翻译层：不选模型、不改prompt、不拦截回复。
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

# L4 内核适配器体系
from tiangong_kernel.l4_action_grounding.model_provider_adapter import (
    PROVIDER_IDS,
    capability_profile_for,
    descriptor_for,
    ModelProviderRequestMapper,
    ModelProviderResponseMapper,
    ModelProviderStreamMapper,
    ModelProviderToolCallMapper,
    ModelProviderErrorMapper,
)

from ..shenti_zhuangtai import ShentiZhuangtai
from ..peizhi import normalize_provider_id


class MoxingShipei:
    """模型适配层——神经末梢+感觉器官"""
    
    def __init__(self):
        self._shipei_qi = {}
        self._chushihua()
    
    def _chushihua(self):
        """初始化所有Provider的适配器"""
        from tiangong_kernel.l4_action_grounding.model_provider_adapter import all_provider_factsheets
        all_fs = all_provider_factsheets()
        for pid in PROVIDER_IDS:
            try:
                fs = all_fs[pid]
                mapper = ModelProviderRequestMapper(pid)
                self._shipei_qi[pid] = {
                    "factsheet": fs,
                    "capability": capability_profile_for(pid),
                    "descriptor": descriptor_for(pid),
                    "request_mapper": mapper,
                    "response_mapper": ModelProviderResponseMapper(pid),
                    "stream_mapper": ModelProviderStreamMapper(pid),
                    "tool_call_mapper": ModelProviderToolCallMapper(pid),
                }
            except Exception:
                continue
    
    def suoyou_provider(self) -> list[str]:
        """列出所有可用Provider"""
        return list(self._shipei_qi.keys())
    
    def provider_xinxi(self, pid: str) -> dict | None:
        """获取Provider能力信息（供代谢层展示）"""
        pid = normalize_provider_id(pid)
        if pid not in self._shipei_qi:
            return None
        fs = self._shipei_qi[pid]["factsheet"]
        return {
            "id": pid,
            "mingcheng": fs.provider_display_name,
            "moxing": fs.default_model_id,
            "xieyi": fs.protocol_family,
            "liushi": fs.streaming_supported,
            "gongju_diaoyong": fs.tool_calling_supported,
            "shangxiawen_chuangkou": fs.context_window_tokens,
            "zuidashuchu": fs.max_output_tokens,
            "sikao_moshi": fs.thinking_mode_supported,
            "jiegouhua_shuchu": fs.structured_output_supported,
        }
    
    def goujian_qingqiu(
        self,
        pid: str,
        system_tishi: str,
        yonghu_tishi: str,
        shenti: ShentiZhuangtai,
        gongju_dingyi: list[dict] | None = None,
        model_name: str | None = None,
        prior_assistant_messages: list[str] | None = None,
        stable_user_message: str | None = None,
    ) -> dict[str, Any]:
        """构建API请求payload"""
        pid = normalize_provider_id(pid)
        if pid not in self._shipei_qi:
            raise ValueError(f"未知Provider: {pid}")
        
        qi = self._shipei_qi[pid]
        fs = qi["factsheet"]
        caps = qi["capability"]
        mapper = qi["request_mapper"]
        
        # 基础消息（OpenAI兼容格式）。结构：
        # [system, user(稳定原始请求), assistant(工具结果1..N), user(稳定短指令)]
        # 前缀逐轮稳定，提升 MiniMax 前缀缓存命中率；stable_user_message 缺省
        # 时退化为旧结构 [system, user]。
        xiaoxi = [{"role": "system", "content": system_tishi}]
        if stable_user_message:
            xiaoxi.append({"role": "user", "content": str(stable_user_message)})
        for item in prior_assistant_messages or []:
            xiaoxi.append({"role": "assistant", "content": str(item)})
        xiaoxi.append({"role": "user", "content": yonghu_tishi})
        
        payload = {
            "model": str(model_name or "").strip() or fs.default_model_id,
            "messages": xiaoxi,
        }
        
        # 工具定义
        if gongju_dingyi and fs.tool_calling_supported:
            payload["tools"] = gongju_dingyi
        
        # 流式（默认关闭，由调用方决定）
        # 不在此处设置stream，由http_kehuduan.py控制
        
        # Provider专属参数映射
        try:
            zhuanshu = mapper.yingse_canshu(shenti)
            if zhuanshu:
                payload.update(zhuanshu)
        except Exception:
            pass
        
        return payload
    
    def jiexi_xiangying(
        self,
        pid: str,
        yuanshi_xiangying: dict,
    ) -> dict[str, Any]:
        pid = normalize_provider_id(pid)
        """解析API响应→归一化格式"""
        if pid not in self._shipei_qi:
            return {"neirong": str(yuanshi_xiangying), "cuowu": f"未知Provider: {pid}"}
        
        mapper = self._shipei_qi[pid]["response_mapper"]
        try:
            return mapper.guiyihua(yuanshi_xiangying)
        except Exception:
            return {"neirong": str(yuanshi_xiangying)}
    
    def jiexi_gongju_diaoyong(
        self,
        pid: str,
        yuanshi_xiangying: dict,
    ) -> list[dict]:
        pid = normalize_provider_id(pid)
        """从响应中提取工具调用"""
        if pid not in self._shipei_qi:
            return []
        
        mapper = self._shipei_qi[pid]["tool_call_mapper"]
        try:
            return mapper.tiqu(yuanshi_xiangying)
        except Exception:
            return []
    
    def chuli_liushi_kuai(
        self,
        pid: str,
        kuai: dict,
        leiji_wenben: str,
    ) -> tuple[str, str | None]:
        pid = normalize_provider_id(pid)
        """处理流式响应块→(累计文本, 新文本或None)"""
        if pid not in self._shipei_qi:
            return leiji_wenben, str(kuai)
        
        mapper = self._shipei_qi[pid]["stream_mapper"]
        try:
            return mapper.chuli_kuai(kuai, leiji_wenben)
        except Exception:
            return leiji_wenben, str(kuai)
    
    def jiexi_cuowu(
        self,
        pid: str,
        zhuangtai_ma: int,
        yuanshi: str,
    ) -> dict[str, Any]:
        pid = normalize_provider_id(pid)
        """解析Provider错误"""
        if pid not in self._shipei_qi:
            return {
                "cuowu": f"HTTP {zhuangtai_ma}: {yuanshi[:500]}",
                "zhongshi": zhuangtai_ma >= 500,
                "xuyao_zhongshi": zhuangtai_ma >= 500,
            }
        
        fs = self._shipei_qi[pid]["factsheet"]
        try:
            mapper = ModelProviderErrorMapper(pid)
            return mapper.jiexi(zhuangtai_ma, yuanshi)
        except Exception:
            return {
                "cuowu": f"HTTP {zhuangtai_ma}: {yuanshi[:500]}",
                "zhongshi": zhuangtai_ma >= 500,
                "xuyao_zhongshi": zhuangtai_ma >= 500,
            }


# 全局单例
MOXING_SHIPEI = MoxingShipei()
