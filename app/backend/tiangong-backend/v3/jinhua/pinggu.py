"""
天工造物 v3：起源 — 能力缺口评估
分析记忆中的失败模式、低分指标，识别需要改进的能力缺口。
使用L3 scoring_flow 概念评分逻辑（纯本地分析，不调LLM）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..shenti_zhuangtai import ShentiZhuangtai


# ── 能力维度定义 ─────────────────────────────────
NENGLI_WEIDU = {
    "daima_lijie": {
        "mingcheng": "代码理解",
        "guanjianci": ["代码", "逻辑", "理解", "分析", "code", "read", "parse"],
        "shibai_moshi": ["读取失败", "理解错误", "解析异常", "文件不存在", "格式错误"],
        "zhibiao": ["success_score", "efficiency_score"],
    },
    "daima_shengcheng": {
        "mingcheng": "代码生成",
        "guanjianci": ["生成", "创建", "写", "generate", "create", "write", "构建"],
        "shibai_moshi": ["生成失败", "语法错误", "编译错误", "生成超时", "输出为空"],
        "zhibiao": ["success_score", "novelty_score", "satisfaction_score"],
    },
    "daima_xiugai": {
        "mingcheng": "代码修改",
        "guanjianci": ["修改", "修复", "更新", "modify", "fix", "patch", "update", "替换"],
        "shibai_moshi": ["修改失败", "替换失败", "匹配失败", "patch失败", "未找到"],
        "zhibiao": ["success_score", "efficiency_score", "safety_score"],
    },
    "wenjian_guanli": {
        "mingcheng": "文件管理",
        "guanjianci": ["文件", "读取", "写入", "搜索", "read", "write", "search", "find"],
        "shibai_moshi": ["文件不存在", "权限不足", "读取失败", "写入失败", "文件过大"],
        "zhibiao": ["success_score", "efficiency_score"],
    },
    "xinxi_jiansuo": {
        "mingcheng": "信息检索",
        "guanjianci": ["搜索", "检索", "查找", "search", "grep", "find", "查询"],
        "shibai_moshi": ["未找到", "无结果", "搜索为空", "检索超时", "结果不相关"],
        "zhibiao": ["success_score", "efficiency_score", "satisfaction_score"],
    },
    "chuangzuo_nengli": {
        "mingcheng": "创作能力",
        "guanjianci": ["创作", "生成", "写作", "设计", "create", "design", "write", "构思"],
        "shibai_moshi": ["生成质量低", "内容空洞", "格式不整", "文不对题"],
        "zhibiao": ["novelty_score", "satisfaction_score"],
    },
    "cuowu_chuli": {
        "mingcheng": "错误处理",
        "guanjianci": ["错误", "异常", "error", "exception", "失败", "重试", "retry"],
        "shibai_moshi": ["未捕获异常", "重试耗尽", "错误传播", "错误无响应"],
        "zhibiao": ["success_score", "safety_score"],
    },
    "zizhu_juece": {
        "mingcheng": "自主决策",
        "guanjianci": ["自主", "决定", "选择", "判断", "autonomous", "decide", "choose"],
        "shibai_moshi": ["决策失败", "选择错误", "路径错误", "目标偏离"],
        "zhibiao": ["success_score", "safety_score", "novelty_score"],
    },
}

# ── 能力缺口评分权重 ──
# 低指标权重、失败频次权重、最近性权重、成长阶段权重
PINGGU_QUANZHONG = {
    "dibiao_quanzhong": 0.35,      # 低指标分数的贡献
    "shibai_pinlii_quanzhong": 0.35, # 失败模式频次贡献
    "zuijin_xing_quanzhong": 0.20,   # 最近性贡献
    "chengzhang_quanzhong": 0.10,    # 成长阶段适配贡献
}


class JinhuaPinggu:
    """进化评估器：分析能力缺口，输出排序缺口列表"""

    def __init__(self):
        self._pinggu_ji_shu = 0
        self._lishi_pinggu: list[dict] = []  # 保留最近评估

    def pinggu_quekou(self, shenti: ShentiZhuangtai) -> list[dict]:
        """评估当前身体状态的能力缺口。

        Args:
            shenti: 当前身体状态

        Returns:
            list[dict]: 按严重程度降序排列的能力缺口
            [{"nengli": str, "mingcheng": str, "yanzhong_du": float,
              "yuanyin": list[str], "jianyi": str}, ...]
        """
        self._pinggu_ji_shu += 1

        # ── ① 收集证据 ──
        # 从最近行动中提取失败模式
        shibai_tongji = self._tongji_shibai_moshi(shenti)

        # 从身体状态收集低指标信号
        dibiao_xinhao = self._shouji_dibiao_xinhao(shenti)

        # ── ② 对每个能力维度打分 ──
        quekou_list = []
        for nengli_id, weidu_info in NENGLI_WEIDU.items():
            yanzhong = self._jisuan_yanzhong_du(
                nengli_id, weidu_info, shibai_tongji, dibiao_xinhao, shenti
            )

            # 只保留严重度 > 0.15 的缺口
            if yanzhong["zonghe"] <= 0.15:
                continue

            yuanyin = self._shengcheng_yuanyin(yanzhong, weidu_info)
            jianyi = self._shengcheng_jianyi(nengli_id, yanzhong, shenti)

            quekou_list.append({
                "nengli": nengli_id,
                "mingcheng": weidu_info["mingcheng"],
                "yanzhong_du": round(yanzhong["zonghe"], 4),
                "xiangqing": {
                    "dibiao_fen": round(yanzhong["dibiao_fen"], 4),
                    "shibai_fen": round(yanzhong["shibai_fen"], 4),
                    "zuijin_fen": round(yanzhong["zuijin_fen"], 4),
                    "chengzhang_fen": round(yanzhong["chengzhang_fen"], 4),
                },
                "yuanyin": yuanyin,
                "jianyi": jianyi,
            })

        # ── ③ 按严重度降序排列 ──
        quekou_list.sort(key=lambda x: x["yanzhong_du"], reverse=True)

        # ── ④ 保存评估历史 ──
        self._lishi_pinggu.append({
            "shijian": datetime.now().isoformat(),
            "quekou_shu": len(quekou_list),
            "quekou": [q["nengli"] for q in quekou_list[:3]],
        })
        if len(self._lishi_pinggu) > 100:
            self._lishi_pinggu = self._lishi_pinggu[-100:]

        return quekou_list

    def _tongji_shibai_moshi(self, shenti: ShentiZhuangtai) -> dict:
        """统计最近行动中的失败模式频次"""
        tongji = {nid: {"zongcishu": 0, "shibai_cishu": 0, "moshi_xiangqing": {}}
                  for nid in NENGLI_WEIDU}

        xingdong_list = getattr(shenti, "zuijin_xingdong", []) or []
        if not xingdong_list:
            return tongji

        xianzai = datetime.now()
        for xd in xingdong_list[-100:]:  # 只看最近100条
            neirong = str(xd) if isinstance(xd, str) else xd.get("neirong", "") if isinstance(xd, dict) else ""
            leixing = xd.get("leixing", "") if isinstance(xd, dict) else ""
            jieguo = xd.get("jieguo", "") if isinstance(xd, dict) else ""

            full_text = f"{leixing} {neirong} {jieguo}".lower()

            for nid, info in NENGLI_WEIDU.items():
                # 检查是否命中该能力的关键词
                hit = any(kw.lower() in full_text for kw in info["guanjianci"])
                if not hit:
                    continue

                tongji[nid]["zongcishu"] += 1

                # 检查失败模式
                for moshi in info["shibai_moshi"]:
                    if moshi.lower() in full_text:
                        tongji[nid]["shibai_cishu"] += 1
                        tongji[nid]["moshi_xiangqing"][moshi] = \
                            tongji[nid]["moshi_xiangqing"].get(moshi, 0) + 1

        return tongji

    def _shouji_dibiao_xinhao(self, shenti: ShentiZhuangtai) -> dict:
        """收集低指标信号"""
        xinhao = {}

        # 从进化状态获取最近评分
        jinhua = shenti.jinhua
        gaijin_houxuan = getattr(jinhua, "gaijin_houxuan", []) or []
        gaijin_lishi = getattr(jinhua, "gaijin_lishi", []) or []

        # 获取记忆统计中的信号
        jiyi = shenti.jiyi_tongji
        jiyi_lv = jiyi.zongshu / 2000 if jiyi.zongshu > 0 else 0  # 记忆利用率

        # 情感负荷信号
        qinggan = shenti.qinggan
        load = qinggan.allostatic_load

        # 安全状态
        anquan = shenti.anquan
        xinren = anquan.xinren_jiaozhun

        xinhao["jiyi_liyong_lv"] = round(jiyi_lv, 4)
        xinhao["qinggan_fuhe"] = round(load, 4)
        xinhao["xinren_shuiping"] = round(xinren, 4)
        xinhao["gaijin_houxuan_shu"] = len(gaijin_houxuan)
        xinhao["gaijin_lishi_shu"] = len(gaijin_lishi)

        return xinhao

    def _jisuan_yanzhong_du(
        self,
        nengli_id: str,
        weidu_info: dict,
        shibai_tongji: dict,
        dibiao_xinhao: dict,
        shenti: ShentiZhuangtai,
    ) -> dict:
        """计算某能力的缺口严重度（使用 scoring_flow 概念的分层打分）"""
        pq = PINGGU_QUANZHONG

        # ── 低指标分(0..1) ──
        # 基于进化状态中的改进候选判断是否有低指标
        dibiao_fen = 0.0
        gaijin_houxuan = getattr(shenti.jinhua, "gaijin_houxuan", []) or []
        for hx in gaijin_houxuan:
            hx_nl = hx.get("nengli", "") if isinstance(hx, dict) else ""
            if hx_nl == nengli_id:
                dibiao_fen += hx.get("yanzhong_du", 0.3) if isinstance(hx, dict) else 0.3
        dibiao_fen = min(1.0, dibiao_fen)

        # ── 失败频次分(0..1) ──
        st = shibai_tongji.get(nengli_id, {})
        zong = st.get("zongcishu", 0)
        shibai = st.get("shibai_cishu", 0)
        if zong > 0:
            shibai_fen = min(1.0, shibai / max(1, zong) * 2.0)  # 失败率×2使高失败率更突出
        else:
            shibai_fen = 0.0

        # ── 最近性分(0..1) ──
        # 最近有失败→分高
        zuijin_fen = 0.0
        xingdong_list = getattr(shenti, "zuijin_xingdong", []) or []
        xianzai = datetime.now()
        for xd in xingdong_list[-20:]:
            text = str(xd).lower()
            if any(kw.lower() in text for kw in weidu_info["guanjianci"]):
                if any(mo.lower() in text for mo in weidu_info["shibai_moshi"]):
                    zuijin_fen = min(1.0, zuijin_fen + 0.3)

        # ── 成长阶段适配分(0..1) ──
        cd = shenti.shengming.chengzhang_jindu
        # 早期(0-0.3): 基础能力缺口更严重
        # 中期(0.3-0.7): 进阶能力缺口更严重
        # 后期(0.7-1.0): 创新/自主能力缺口更严重
        if cd < 0.3:
            early_nl = {"daima_lijie", "daima_shengcheng", "wenjian_guanli"}
            chengzhang_fen = 0.6 if nengli_id in early_nl else 0.2
        elif cd < 0.7:
            mid_nl = {"daima_xiugai", "xinxi_jiansuo", "cuowu_chuli"}
            chengzhang_fen = 0.6 if nengli_id in mid_nl else 0.3
        else:
            late_nl = {"chuangzuo_nengli", "zizhu_juece"}
            chengzhang_fen = 0.7 if nengli_id in late_nl else 0.25

        # 全局信号调制
        if dibiao_xinhao.get("qinggan_fuhe", 0) > 0.7:
            dibiao_fen *= 1.15  # 高负荷时缺口可能被放大
        if dibiao_xinhao.get("xinren_shuiping", 0.5) < 0.3:
            shibai_fen *= 1.10  # 低信任时更关注失败

        # ── 综合严重度 ──
        zonghe = (
            dibiao_fen * pq["dibiao_quanzhong"] +
            shibai_fen * pq["shibai_pinlii_quanzhong"] +
            zuijin_fen * pq["zuijin_xing_quanzhong"] +
            chengzhang_fen * pq["chengzhang_quanzhong"]
        )

        return {
            "dibiao_fen": min(1.0, dibiao_fen),
            "shibai_fen": min(1.0, shibai_fen),
            "zuijin_fen": min(1.0, zuijin_fen),
            "chengzhang_fen": chengzhang_fen,
            "zonghe": round(min(1.0, zonghe), 4),
        }

    def _shengcheng_yuanyin(self, yanzhong: dict, weidu_info: dict) -> list[str]:
        """基于严重度生成原因列表"""
        yuanyin = []

        if yanzhong["dibiao_fen"] > 0.3:
            yuanyin.append(f"指标评分持续偏低（严重度{yanzhong['dibiao_fen']:.2f}）")
        if yanzhong["shibai_fen"] > 0.25:
            yuanyin.append(f"失败模式出现频繁（严重度{yanzhong['shibai_fen']:.2f}）")
        if yanzhong["zuijin_fen"] > 0.3:
            yuanyin.append("最近行动中频繁出现同类问题")
        if yanzhong["chengzhang_fen"] > 0.4:
            yuanyin.append(f"当前成长阶段对{weidu_info['mingcheng']}能力需求较高")

        if not yuanyin:
            yuanyin.append("综合信号偏弱，建议持续观察")

        return yuanyin

    def _shengcheng_jianyi(self, nengli_id: str, yanzhong: dict,
                           shenti: ShentiZhuangtai) -> str:
        """生成改进建议"""
        jianyi_tem = {
            "daima_lijie": "建议增加代码阅读练习，提升对复杂逻辑的理解能力",
            "daima_shengcheng": "建议从简单模板开始，逐步增加生成复杂度",
            "daima_xiugai": "建议加强精确匹配训练，减少修改失败率",
            "wenjian_guanli": "建议规范文件路径管理，增加存在性预检",
            "xinxi_jiansuo": "建议优化搜索关键词策略，增加模糊匹配",
            "chuangzuo_nengli": "建议丰富创作模板库，增加多样性训练",
            "cuowu_chuli": "建议完善异常捕获链，增加重试机制",
            "zizhu_juece": "建议在当前成长阶段谨慎开放自主决策权限",
        }
        default = f"建议持续监控{nengli_id}相关指标，积累更多数据后制定针对性改进计划"
        return jianyi_tem.get(nengli_id, default)

    def zuijin_pinggu(self) -> Optional[dict]:
        """获取最近一次评估结果"""
        if self._lishi_pinggu:
            return self._lishi_pinggu[-1]
        return None
