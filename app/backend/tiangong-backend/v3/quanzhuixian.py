"""
天工造物 v3：起源 — 全追踪链
所有引擎的行为可回溯
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .peizhi import ZHUIZONG_LUJING


class Quanzhuixian:
    """全追踪链：记录因果链从触发到结果"""

    def __init__(self):
        self.dangqian_zhuizong_id: str = ""
        self.lujing = ZHUIZONG_LUJING
        self.lujing.mkdir(parents=True, exist_ok=True)

    def kaishi(self, chufa_yuan: str, xiaoxi: str = "") -> str:
        """开始新追踪"""
        zhuizong_id = f"trace_{uuid.uuid4().hex[:12]}"
        self.dangqian_zhuizong_id = zhuizong_id
        jilu = {
            "zhuizong_id": zhuizong_id,
            "chufa_yuan": chufa_yuan,
            "xiaoxi_yulan": xiaoxi[:200] if xiaoxi else "",
            "kaishi_shijian": datetime.now().isoformat(),
            "kuadu": []
        }
        self._xieru(zhuizong_id, jilu)
        return zhuizong_id

    def jilu_kuadu(
        self,
        zhuizong_id: str,
        kuadu_ming: str,
        zhuangtai: str,
        xiangqing: str = ""
    ):
        """记录一个跨度"""
        kuadu = {
            "kuadu_ming": kuadu_ming,
            "zhuangtai": zhuangtai,
            "shijian": datetime.now().isoformat(),
            "xiangqing": xiangqing[:500]
        }
        dangqian = self._duqu(zhuizong_id)
        if dangqian:
            dangqian["kuadu"].append(kuadu)
            self._xieru(zhuizong_id, dangqian)

    def jieshu(self, zhuizong_id: str, jieguo: str = ""):
        """结束追踪"""
        self.jilu_kuadu(zhuizong_id, "jieshu", "wancheng",
                        jieguo[:500] if jieguo else "wujieguo")

    def _xieru(self, zhuizong_id: str, jilu: dict):
        wenjian = self.lujing / f"{zhuizong_id}.json"
        wenjian.write_text(json.dumps(jilu, ensure_ascii=False, indent=2), encoding="utf-8")

    def _duqu(self, zhuizong_id: str) -> dict | None:
        wenjian = self.lujing / f"{zhuizong_id}.json"
        if wenjian.exists():
            return json.loads(wenjian.read_text(encoding="utf-8"))
        return None


# 全局单例
QUANZHUIXIAN = Quanzhuixian()
