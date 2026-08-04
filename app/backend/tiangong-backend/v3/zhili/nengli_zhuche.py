"""
天工造物 v3：起源 — 能力注册
nengli_zhuche.py: L5 注册表模式的能力注册管理
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..l0_ability_projection import REGISTRY_SCHEMA, read_json_compat, with_l0_projection
from ..peizhi import NENGLI_ZHUCE_LUJING


# ---- 能力定义类型 ----
NENGLI_LEIXING = [
    "gongju",          # 工具调用能力 (read_file, terminal, ...)
    "jieru",           # 接入能力 (web_search, api_call, ...)
    "tuili",           # 推理能力
    "chuangzuo",       # 创作能力
    "fenxi",           # 分析能力
    "duihua",          # 对话能力
    "kongzhi",         # 控制能力 (鼠标、键盘等)
    "qita",            # 其他
]

NENGLI_ZHUANGTAI = [
    "jihuo",           # 已激活
    "tingyong",        # 已停用
    "daijihuo",        # 待激活
    "shiyanzhong",     # 实验中
    "baofei",          # 已报废
]


class NengliDingyi(dict):
    """
    能力定义的标准化模型。
    字段: id, mingcheng, leixing, miaoshu, banben, laiyuan, zhuangtai, zhuce_shijian
    """
    
    def __init__(
        self,
        mingcheng: str,
        leixing: str,
        miaoshu: str = "",
        banben: str = "1.0.0",
        laiyuan: str = "",
        zhuangtai: str = "daijihuo",
        nengli_id: Optional[str] = None,
    ):
        super().__init__(
            id=nengli_id or f"nengli_{uuid.uuid4().hex[:12]}",
            mingcheng=mingcheng,
            leixing=leixing,
            miaoshu=miaoshu,
            banben=banben,
            laiyuan=laiyuan,
            zhuangtai=zhuangtai,
            zhuce_shijian=datetime.now(timezone.utc).isoformat(),
        )

    def yanzheng(self) -> tuple[bool, str]:
        """验证能力定义是否合法"""
        if not self.get("mingcheng"):
            return False, "mingcheng 不能为空"
        if self.get("leixing") not in NENGLI_LEIXING:
            return False, f"leixing 必须为 {NENGLI_LEIXING} 之一，当前: {self.get('leixing')}"
        if self.get("zhuangtai") not in NENGLI_ZHUANGTAI:
            return False, f"zhuangtai 必须为 {NENGLI_ZHUANGTAI} 之一，当前: {self.get('zhuangtai')}"
        if not self.get("id"):
            return False, "id 不能为空"
        return True, "合法"


class NengliZhuche:
    """
    能力注册表 — L5 注册表模式。
    管理所有已注册的能力定义，支持注册、查询、激活/停用。
    """

    def __init__(self, zhuce_lujing: Optional[Path] = None):
        self.zhuce_lujing = zhuce_lujing or NENGLI_ZHUCE_LUJING
        self._nengli_dict: dict[str, dict] = {}
        self._jiazai()

    # ---- 持久化 ----

    def _jiazai(self):
        """从磁盘加载注册表"""
        if self.zhuce_lujing.exists():
            try:
                data = read_json_compat(self.zhuce_lujing, {})
                rows = data.get("nengli_liebiao") or data.get("nengli_list") or []
                self._nengli_dict = {item["id"]: with_l0_projection(item) for item in rows if isinstance(item, dict) and item.get("id")}
            except (json.JSONDecodeError, KeyError):
                self._nengli_dict = {}

    def _baocun(self):
        """持久化注册表到磁盘"""
        self.zhuce_lujing.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema": REGISTRY_SCHEMA,
            "nengli_list": list(self._nengli_dict.values()),
            "zuihou_gengxin": datetime.now(timezone.utc).isoformat(),
            "zongshu": len(self._nengli_dict),
        }
        self.zhuce_lujing.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- 注册 ----

    def zhuce_nengli(self, nengli_dingyi: NengliDingyi | dict) -> bool:
        """
        注册新能力。
        
        Args:
            nengli_dingyi: 能力定义 (NengliDingyi 或标准 dict)
        
        Returns:
            True 注册成功
        """
        if isinstance(nengli_dingyi, dict) and not isinstance(nengli_dingyi, NengliDingyi):
            payload = {
                k: v for k, v in nengli_dingyi.items()
                if k in ("mingcheng", "leixing", "miaoshu", "banben", "laiyuan", "zhuangtai")
            }
            if nengli_dingyi.get("id"):
                payload["nengli_id"] = nengli_dingyi.get("id")
            nengli_dingyi = NengliDingyi(**payload)

        hefa, msg = nengli_dingyi.yanzheng()
        if not hefa:
            raise ValueError(f"能力定义不合法: {msg}")

        nengli_id = nengli_dingyi["id"]

        # 如果已存在同 id，更新之
        if nengli_id in self._nengli_dict:
            nengli_dingyi["zhuce_shijian"] = self._nengli_dict[nengli_id]["zhuce_shijian"]
        else:
            nengli_dingyi["zhuce_shijian"] = datetime.now(timezone.utc).isoformat()

        self._nengli_dict[nengli_id] = with_l0_projection(dict(nengli_dingyi))
        self._baocun()
        return True

    def zhuxiao_nengli(self, nengli_id: str) -> bool:
        """
        注销能力。
        
        Args:
            nengli_id: 能力ID
        
        Returns:
            True 注销成功，False 不存在
        """
        if nengli_id not in self._nengli_dict:
            return False
        del self._nengli_dict[nengli_id]
        self._baocun()
        return True

    # ---- 查询 ----

    def suoyou_nengli(self, zhuangtai: Optional[str] = None) -> list[dict]:
        """
        列出所有已注册的能力。
        
        Args:
            zhuangtai: 可按状态过滤 (jihuo/tingyong/daijihuo/shiyanzhong/baofei)
        
        Returns:
            能力定义列表
        """
        nengli_list = list(self._nengli_dict.values())
        if zhuangtai:
            nengli_list = [
                n for n in nengli_list
                if n.get("zhuangtai") == zhuangtai
            ]
        return sorted(nengli_list, key=lambda n: n.get("zhuce_shijian", ""))

    def chaxun_nengli(self, leixing: str) -> list[dict]:
        """
        按类型查询能力。
        
        Args:
            leixing: 能力类型 (gongju/jieru/tuili/chuangzuo/fenxi/duihua/kongzhi/qita)
        
        Returns:
            匹配的能力定义列表
        """
        return sorted(
            [n for n in self._nengli_dict.values() if n.get("leixing") == leixing],
            key=lambda n: n.get("zhuce_shijian", ""),
        )

    def huoqu_nengli(self, nengli_id: str) -> Optional[dict]:
        """
        按 ID 获取能力详情。
        """
        return self._nengli_dict.get(nengli_id)

    # ---- 状态管理 ----

    def jihuo_nengli(self, nengli_id: str) -> bool:
        """激活能力"""
        if nengli_id not in self._nengli_dict:
            return False
        self._nengli_dict[nengli_id]["zhuangtai"] = "jihuo"
        self._baocun()
        return True

    def tingyong_nengli(self, nengli_id: str) -> bool:
        """停用能力"""
        if nengli_id not in self._nengli_dict:
            return False
        self._nengli_dict[nengli_id]["zhuangtai"] = "tingyong"
        self._baocun()
        return True

    def tongji(self) -> dict:
        """
        统计信息：各类型和各状态的数量。
        """
        leixing_tongji = {t: 0 for t in NENGLI_LEIXING}
        zhuangtai_tongji = {s: 0 for s in NENGLI_ZHUANGTAI}
        for n in self._nengli_dict.values():
            lt = n.get("leixing", "qita")
            zt = n.get("zhuangtai", "daijihuo")
            if lt in leixing_tongji:
                leixing_tongji[lt] += 1
            if zt in zhuangtai_tongji:
                zhuangtai_tongji[zt] += 1

        return {
            "zongshu": len(self._nengli_dict),
            "an_leixing": leixing_tongji,
            "an_zhuangtai": zhuangtai_tongji,
        }
