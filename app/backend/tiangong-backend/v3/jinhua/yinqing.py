"""
天工造物 v3：起源 — 进化引擎
入口点，编排 评估→改进 全流程。
由心跳tick触发（当存在改进候选时），映射到L3 self_improvement_flow。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..shenti_zhuangtai import ShentiZhuangtai
from .pinggu import JinhuaPinggu
from .zhixing import JinhuaZhixing


# ── 进化触发条件 ─────────────────────────────────
JINHUA_CHUFA = {
    "zuixiao_jiange_miao": 300,       # 两次进化最小间隔（秒）
    "zuida_lianxu_gaijin": 3,         # 单轮最大连续改进次数
    "gaojing_yuzhi": 0.40,            # 严重度告警阈值
    "que fa_yuzhi": 0.15,             # 缺口最低阈值
}


class JinhuaYinqing:
    """进化引擎：周期性检查→评估→改进→验证"""

    def __init__(self):
        self.pinggu_qi = JinhuaPinggu()
        self.zhixing_qi = JinhuaZhixing()
        self._zuihou_jinhua: Optional[datetime] = None
        self._jinhua_ji_shu = 0
        self._lianxu_gaijin = 0

    def jiancha(self, shenti: ShentiZhuangtai) -> ShentiZhuangtai:
        """检查身体状态并触发进化流程。

        由心跳 tick 调用：当存在改进候选时执行评估→改进。
        映射到 L3 self_improvement_flow 概念：
        - SelfImprovementFlowKind.SELF_EVOLUTION
        - SelfImprovementFlowEntryAdvice（编排建议）

        Args:
            shenti: 当前身体状态

        Returns:
            更新后的身体状态（可能已执行改进）
        """
        xianzai = datetime.now()

        # ── ① 频率控制 ──
        if not self._keyi_jinhua(shenti, xianzai):
            return shenti

        # ── ② 评估缺口 ──
        quekou_list = self.pinggu_qi.pinggu_quekou(shenti)

        if not quekou_list:
            shenti.jinhua.dangqian_jieduan = "guancha"
            return shenti

        # ── ③ 筛选待改进项 ──
        daichuli = self._shaixuan_daichuli(quekou_list, shenti)

        if not daichuli:
            shenti.jinhua.dangqian_jieduan = "guancha"
            return shenti

        # ── ④ 执行改进 ──
        for gaijin_xiang in daichuli:
            # 更新进化状态
            shenti.jinhua.dangqian_jieduan = "pinggu"

            # 构建改进计划
            gaijin_jihua = {
                "nengli": gaijin_xiang["nengli"],
                "yanzhong_du": gaijin_xiang["yanzhong_du"],
            }

            # 执行改进
            shenti = self.zhixing_qi.zhixing_gaijin(shenti, gaijin_jihua)

            self._jinhua_ji_shu += 1
            self._lianxu_gaijin += 1

            # 连续改进上限
            if self._lianxu_gaijin >= JINHUA_CHUFA["zuida_lianxu_gaijin"]:
                shenti.zuijin_xingdong.append({
                    "leixing": "jinhua_xianzhi",
                    "yuanyin": f"达到单轮最大连续改进{self._lianxu_gaijin}次",
                    "shijian": xianzai.isoformat(),
                })
                break

        # ── ⑤ 更新状态 ──
        self._zuihou_jinhua = xianzai
        shenti.jinhua.dangqian_jieduan = "guancha"

        # 修剪行动记录
        if len(shenti.zuijin_xingdong) > 50:
            shenti.zuijin_xingdong = shenti.zuijin_xingdong[-50:]

        return shenti

    def _keyi_jinhua(self, shenti: ShentiZhuangtai,
                     xianzai: datetime) -> bool:
        """检查是否满足进化触发条件"""
        # 条件1: 最小间隔
        if self._zuihou_jinhua is not None:
            jiange = (xianzai - self._zuihou_jinhua).total_seconds()
            if jiange < JINHUA_CHUFA["zuixiao_jiange_miao"]:
                return False

        # 条件2: 存在改进候选
        gaijin_houxuan = getattr(shenti.jinhua, "gaijin_houxuan", []) or []
        gaijin_lishi = getattr(shenti.jinhua, "gaijin_lishi", []) or []

        # 有明确候选 → 可以进化
        if gaijin_houxuan:
            return True

        # 无候选但长时间未进化且成长进度>0 → 主动评估
        if self._zuihou_jinhua is None and shenti.shengming.chengzhang_jindu > 0.15:
            return True

        if self._zuihou_jinhua is not None:
            jiange_sec = (xianzai - self._zuihou_jinhua).total_seconds()
            # 超过30分钟且成长进度足够 → 主动评估
            if jiange_sec > 1800 and shenti.shengming.chengzhang_jindu > 0.20:
                return True

        return False

    def _shaixuan_daichuli(
        self,
        quekou_list: list[dict],
        shenti: ShentiZhuangtai,
    ) -> list[dict]:
        """筛选待处理缺口。

        策略：
        ① 严重度高于告警阈值的优先
        ② 检查进化历史，避免重复处理
        ③ 一次只处理最多 zuida_lianxu_gaijin 个
        """
        gaijin_lishi = getattr(shenti.jinhua, "gaijin_lishi", []) or []

        # 提取最近改进过的能力
        zuijin_gaijin_nl = set()
        for jl in gaijin_lishi[-5:]:  # 只看最近5条
            if isinstance(jl, dict):
                nl = jl.get("nengli", "")
                if nl:
                    zuijin_gaijin_nl.add(nl)

        daichuli = []
        yijing_chuli = set()

        for quekou in quekou_list:
            nl = quekou["nengli"]
            yz = quekou["yanzhong_du"]

            # 低于最低阈值 → 跳过
            if yz < JINHUA_CHUFA["que fa_yuzhi"]:
                continue

            # 已处理过 → 跳过
            if nl in yijing_chuli:
                continue

            # 最近改进过且严重度不高 → 跳过（避免频繁重复）
            if nl in zuijin_gaijin_nl and yz < JINHUA_CHUFA["gaojing_yuzhi"]:
                continue

            daichuli.append(quekou)
            yijing_chuli.add(nl)

            # 达到上限
            if len(daichuli) >= JINHUA_CHUFA["zuida_lianxu_gaijin"]:
                break

        # 按严重度排序
        daichuli.sort(key=lambda x: x["yanzhong_du"], reverse=True)

        # 高告警缺口优先
        gaojing_xiang = [d for d in daichuli
                         if d["yanzhong_du"] >= JINHUA_CHUFA["gaojing_yuzhi"]]
        putong_xiang = [d for d in daichuli
                        if d["yanzhong_du"] < JINHUA_CHUFA["gaojing_yuzhi"]]

        return gaojing_xiang + putong_xiang

    def jinhua_tongji(self) -> dict:
        """获取进化引擎统计"""
        return {
            "jinhua_zongshu": self._jinhua_ji_shu,
            "lianxu_gaijin": self._lianxu_gaijin,
            "zuihou_jinhua": self._zuihou_jinhua.isoformat() if self._zuihou_jinhua else None,
            "pinggu_tongji": {
                "pinggu_cishu": self.pinggu_qi._pinggu_ji_shu,
                "zuijin_pinggu": self.pinggu_qi.zuijin_pinggu(),
            },
            "zhixing_tongji": self.zhixing_qi.zhixing_tongji(),
        }

    def qiangzhi_jinhua(self, shenti: ShentiZhuangtai,
                        nengli: Optional[str] = None) -> ShentiZhuangtai:
        """强制执行一次进化（绕过间隔限制）。

        Args:
            shenti: 当前身体状态
            nengli: 指定能力ID（可选，不指定则自动评估最严重缺口）

        Returns:
            更新后的身体状态
        """
        if nengli:
            # 指定能力改进
            gaijin_jihua = {
                "nengli": nengli,
                "yanzhong_du": 0.5,  # 强制中等等级
            }
            shenti = self.zhixing_qi.zhixing_gaijin(shenti, gaijin_jihua)
            self._jinhua_ji_shu += 1
            self._zuihou_jinhua = datetime.now()
        else:
            # 自动评估改进
            quekou_list = self.pinggu_qi.pinggu_quekou(shenti)
            if quekou_list:
                gaijin_jihua = {
                    "nengli": quekou_list[0]["nengli"],
                    "yanzhong_du": quekou_list[0]["yanzhong_du"],
                }
                shenti = self.zhixing_qi.zhixing_gaijin(shenti, gaijin_jihua)
                self._jinhua_ji_shu += 1
                self._zuihou_jinhua = datetime.now()

        shenti.jinhua.dangqian_jieduan = "guancha"
        return shenti
