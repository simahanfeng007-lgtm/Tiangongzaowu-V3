"""
天工造物 v3：起源 — 改进执行与验证
执行改进计划：生成方案→学习链执行→验证→提交或回滚。
映射到L3 self_improvement_flow 概念（纯编排，不实现L4/L5/L6子系统）。
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Optional

from ..shenti_zhuangtai import ShentiZhuangtai


# ── 改进策略映射 ─────────────────────────────────
GAILUE_CELVE = {
    "daima_lijie": {
        "fangfa": "zengqiang_jiansuo",     # 增强检索
        "buzhou": ["fenxi_dangqian_xianzhi", "sheji_jiansuo_celve",
                    "yanzheng_lijie_xiaoguo"],
        "yuzhi": 0.12,  # 改进生效的最低分数提升阈值
    },
    "daima_shengcheng": {
        "fangfa": "moban_yinru",           # 模板引入
        "buzhou": ["shouji_chenggong_moban", "goujian_shengcheng_liushui",
                    "ceshi_shengcheng_zhiliang"],
        "yuzhi": 0.10,
    },
    "daima_xiugai": {
        "fangfa": "jingque_pipei_xunlian",  # 精确匹配训练
        "buzhou": ["fenxi_xiugai_shibai_dian", "youhua_pipei_celve",
                    "yanzheng_xiugai_chenggong_lv"],
        "yuzhi": 0.10,
    },
    "wenjian_guanli": {
        "fangfa": "lujing_guiyue",          # 路径规约
        "buzhou": ["jiancha_lujing_guiyue", "xiuzheng_lujing_moshi",
                    "yanzheng_wenjian_caozuo"],
        "yuzhi": 0.08,
    },
    "xinxi_jiansuo": {
        "fangfa": "guanjianci_youhua",      # 关键词优化
        "buzhou": ["fenxi_jiansuo_loudong", "youhua_sousuo_celve",
                    "yanzheng_jiansuo_zhunque_lv"],
        "yuzhi": 0.10,
    },
    "chuangzuo_nengli": {
        "fangfa": "chuangzuo_kuozhan",      # 创作扩展
        "buzhou": ["fenxi_chuangzuo_ruodian", "kuozhan_chuangzuo_moban",
                    "yanzheng_chuangzuo_duoyang_xing"],
        "yuzhi": 0.10,
    },
    "cuowu_chuli": {
        "fangfa": "yichang_buhuo_lian",     # 异常捕获链
        "buzhou": ["shibie_yichang_loudong", "wanshan_buhuo_luoji",
                    "yanzheng_cuowu_huifu_lv"],
        "yuzhi": 0.12,
    },
    "zizhu_juece": {
        "fangfa": "juece_kuangjia_goujian",  # 决策框架构建
        "buzhou": ["fenxi_juece_shiwu", "goujian_juece_moxing",
                    "yanzheng_juece_zhengque_lv"],
        "yuzhi": 0.15,
    },
}


class JinhuaZhixing:
    """改进执行器：生成方案→执行→验证→提交/回滚"""

    def __init__(self):
        self._zhixing_ji_shu = 0
        self._chenggong_ji_shu = 0
        self._huigun_ji_shu = 0
        self._dangqian_kuaizhao: Optional[ShentiZhuangtai] = None

    def zhixing_gaijin(
        self,
        shenti: ShentiZhuangtai,
        gaijin_jihua: dict,
    ) -> ShentiZhuangtai:
        """执行改进计划。

        Args:
            shenti: 当前身体状态
            gaijin_jihua: 改进计划 dict
                {
                    "nengli": str,          # 能力ID
                    "yanzhong_du": float,   # 严重度
                    "celve": str,           # 策略名（可选，自动匹配）
                }

        Returns:
            更新后的身体状态（已提交改进或保持不变）
        """
        self._zhixing_ji_shu += 1

        nengli_id = gaijin_jihua.get("nengli", "")
        if nengli_id not in GAILUE_CELVE:
            shenti.zuijin_xingdong.append({
                "leixing": "jinhua_zhixing",
                "jieguo": "tiaoguo",
                "yuanyin": f"未知能力: {nengli_id}",
                "shijian": datetime.now().isoformat(),
            })
            return shenti

        celve_info = GAILUE_CELVE[nengli_id]

        # ── ① 快照备份（用于回滚） ──
        self._dangqian_kuaizhao = copy.deepcopy(shenti)

        # ── ② 生成改进方案 ──
        fangan = self._shengcheng_fangan(shenti, nengli_id, celve_info, gaijin_jihua)

        # ── ③ 执行学习链 ──
        shenti = self._zhixing_xuexi_lian(shenti, fangan, celve_info)

        # ── ④ 验证改进效果 ──
        yanzheng_jieguo = self._yanzheng_gaijin(shenti, nengli_id, celve_info, fangan)

        # ── ⑤ 提交或回滚 ──
        if yanzheng_jieguo["tongguo"]:
            shenti = self._tijiao(shenti, nengli_id, fangan, yanzheng_jieguo)
            self._chenggong_ji_shu += 1
        else:
            shenti = self._huigun(shenti, nengli_id, yanzheng_jieguo)
            self._huigun_ji_shu += 1

        # 清理快照
        self._dangqian_kuaizhao = None

        # 修剪最近行动记录
        if len(shenti.zuijin_xingdong) > 50:
            shenti.zuijin_xingdong = shenti.zuijin_xingdong[-50:]

        return shenti

    def _shengcheng_fangan(
        self,
        shenti: ShentiZhuangtai,
        nengli_id: str,
        celve_info: dict,
        gaijin_jihua: dict,
    ) -> dict:
        """生成具体改进方案（纯编排，不调LLM）"""
        fangan = {
            "nengli": nengli_id,
            "fangfa": celve_info["fangfa"],
            "buzhou": list(celve_info["buzhou"]),
            "yuzhi": celve_info["yuzhi"],
            "yanzhong_du": gaijin_jihua.get("yanzhong_du", 0.0),
            "shengcheng_shijian": datetime.now().isoformat(),
            "shenti_id": shenti.shenti_id,
            "chengzhang_jindu": shenti.shengming.chengzhang_jindu,
        }

        # 根据严重度调整方案强度
        yz = fangan["yanzhong_du"]
        if yz > 0.7:
            fangan["qiangdu"] = "gao"
            fangan["yuzhi"] = min(0.30, celve_info["yuzhi"] * 1.5)
        elif yz > 0.4:
            fangan["qiangdu"] = "zhong"
        else:
            fangan["qiangdu"] = "di"

        return fangan

    def _zhixing_xuexi_lian(
        self,
        shenti: ShentiZhuangtai,
        fangan: dict,
        celve_info: dict,
    ) -> ShentiZhuangtai:
        """执行学习链：逐步执行方案步骤。

        映射到 L3 self_improvement_flow 概念：
        - SelfImprovementFlowKind.SELF_EVOLUTION
        - 每个步骤更新进化状态
        """
        for i, buzhou in enumerate(fangan["buzhou"]):
            # 模拟步骤执行——更新进化状态
            shenti.jinhua.dangqian_jieduan = "gaijin"
            shenti.jinhua.gaijin_houxuan.append({
                "buzhou": buzhou,
                "buzhou_xuhao": i + 1,
                "zong_buzhou": len(fangan["buzhou"]),
                "zhuangtai": "zhixing_zhong",
                "shijian": datetime.now().isoformat(),
            })

            # 记录到最近行动
            shenti.zuijin_xingdong.append({
                "leixing": "jinhua_buzhou",
                "nengli": fangan["nengli"],
                "buzhou": buzhou,
                "buzhou_xuhao": i + 1,
                "shijian": datetime.now().isoformat(),
            })

        # 标记进化阶段完成
        shenti.jinhua.dangqian_jieduan = "yanzheng"

        return shenti

    def _yanzheng_gaijin(
        self,
        shenti: ShentiZhuangtai,
        nengli_id: str,
        celve_info: dict,
        fangan: dict,
    ) -> dict:
        """验证改进效果。

        验证逻辑（纯公式评分，不调LLM）：
        ① 检查步骤是否全部执行
        ② 计算模拟改进分（基于成长阶段+严重度+随机因子）
        ③ 对比阈值决定通过/失败
        """
        # 步骤完成度
        buzhou_wancheng = len(fangan["buzhou"])

        # 模拟改进分计算
        cd = shenti.shengming.chengzhang_jindu
        yz = fangan["yanzhong_du"]

        # 基础改进分 = 成长度×0.3 + 严重度×0.4（严重的问题改进空间大）
        jichu_fen = cd * 0.30 + yz * 0.40

        # 自主性调制
        zizhu_dengji = shenti.anquan.zizhu_jibie
        zizhu_bonus = {
            "chenshui": 0.0,
            "fuzhu": 0.05,
            "banzizhu": 0.10,
            "zizhu": 0.15,
            "wanquan_zizhu": 0.12,  # 完全自主反而更谨慎
        }.get(zizhu_dengji, 0.05)

        # 记忆积累加成
        jiyi_zongshu = shenti.jiyi_tongji.zongshu
        jiyi_bonus = min(0.10, jiyi_zongshu / 5000 * 0.10)

        # 最终改进分
        gaijin_fen = min(1.0, jichu_fen + zizhu_bonus + jiyi_bonus)

        # 阈值比较
        yuzhi = fangan.get("yuzhi", celve_info["yuzhi"])
        tongguo = gaijin_fen >= yuzhi

        return {
            "tongguo": tongguo,
            "gaijin_fen": round(gaijin_fen, 4),
            "yuzhi": round(yuzhi, 4),
            "buzhou_wancheng": buzhou_wancheng,
            "zong_buzhou": len(fangan["buzhou"]),
            "yuanyin": (
                f"改进分{gaijin_fen:.3f} >= 阈值{yuzhi:.3f}，验证通过"
                if tongguo
                else f"改进分{gaijin_fen:.3f} < 阈值{yuzhi:.3f}，验证未通过"
            ),
        }

    def _tijiao(
        self,
        shenti: ShentiZhuangtai,
        nengli_id: str,
        fangan: dict,
        yanzheng: dict,
    ) -> ShentiZhuangtai:
        """提交改进：固化到进化历史，更新能力评分"""
        # 记录到进化历史
        jilu = {
            "nengli": nengli_id,
            "fangfa": fangan["fangfa"],
            "gaijin_fen": yanzheng["gaijin_fen"],
            "yuzhi": yanzheng["yuzhi"],
            "jieguo": "chenggong",
            "shijian": datetime.now().isoformat(),
        }
        shenti.jinhua.gaijin_lishi.append(jilu)
        if len(shenti.jinhua.gaijin_lishi) > 200:
            shenti.jinhua.gaijin_lishi = shenti.jinhua.gaijin_lishi[-200:]

        # 清理该能力的改进候选（已处理）
        shenti.jinhua.gaijin_houxuan = [
            hx for hx in shenti.jinhua.gaijin_houxuan
            if not (isinstance(hx, dict) and hx.get("nengli") == nengli_id)
        ]

        # 提升成长进度（微小增量）
        shenti.shengming.chengzhang_jindu = min(
            1.0,
            shenti.shengming.chengzhang_jindu + 0.005 * yanzheng["gaijin_fen"]
        )

        # 情感奖励
        shenti.qinggan.joy = min(1.0, shenti.qinggan.joy + 0.05)
        shenti.qinggan.achievement = min(1.0, shenti.qinggan.achievement + 0.08)

        shenti.jinhua.dangqian_jieduan = "guancha"
        shenti.zuijin_xingdong.append({
            "leixing": "jinhua_tijiao",
            "nengli": nengli_id,
            "gaijin_fen": yanzheng["gaijin_fen"],
            "shijian": datetime.now().isoformat(),
        })

        return shenti

    def _huigun(
        self,
        shenti: ShentiZhuangtai,
        nengli_id: str,
        yanzheng: dict,
    ) -> ShentiZhuangtai:
        """回滚改进：恢复到快照状态"""
        if self._dangqian_kuaizhao is not None:
            # 恢复关键字段
            shenti.jinhua = copy.deepcopy(self._dangqian_kuaizhao.jinhua)
            shenti.qinggan = copy.deepcopy(self._dangqian_kuaizhao.qinggan)
            shenti.shengming.chengzhang_jindu = \
                self._dangqian_kuaizhao.shengming.chengzhang_jindu

        shenti.jinhua.dangqian_jieduan = "guancha"
        shenti.zuijin_xingdong.append({
            "leixing": "jinhua_huigun",
            "nengli": nengli_id,
            "yuanyin": yanzheng["yuanyin"],
            "shijian": datetime.now().isoformat(),
        })

        # 小幅情感惩罚（回滚说明改进不成功）
        shenti.qinggan.worry = min(1.0, shenti.qinggan.worry + 0.03)
        shenti.qinggan.joy = max(0.05, shenti.qinggan.joy - 0.02)

        return shenti

    def zhixing_tongji(self) -> dict:
        """获取执行统计"""
        return {
            "zhixing_zongshu": self._zhixing_ji_shu,
            "chenggong_shu": self._chenggong_ji_shu,
            "huigun_shu": self._huigun_ji_shu,
            "chenggong_lv": round(
                self._chenggong_ji_shu / max(1, self._zhixing_ji_shu), 4
            ),
        }
