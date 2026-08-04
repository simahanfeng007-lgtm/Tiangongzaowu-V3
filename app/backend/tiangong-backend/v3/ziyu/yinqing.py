"""
天工造物 v3：起源 — 自愈引擎
ZiyuYinqing: 周期性自检→诊断→修复→验证
映射到 L0 health.py, L0 failure.py, L3 self_healing_flow
概念对等: SelfHealingFlowKind.SELF_REPAIR, RecoveryAction
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..peizhi import (
    QIYONG_ZIYU, SHENTI_LUJING, JIYI_LUJING,
    JINGYAN_LUJING, API_ZUIDA_MEICI,
)
from ..shenti_zhuangtai import ShentiZhuangtai


# ═══════════════════════════════════════════════════
# 自愈阈值配置
# ═══════════════════════════════════════════════════
ZIYU_YUZHI = {
    "lianxu_cuowu_yuzhi": 3,            # 连续错误触发诊断的阈值
    "cipan_kongxian_yuzhi_mb": 100,     # 磁盘空间告警阈值(MB)
    "neicun_gaojing_yuzhi": 0.85,       # 内存使用率告警阈值
    "shengmingli_weixian_yuzhi": 0.30,  # 生命力危险阈值
    "sunshang_gaojing_yuzhi": 0.40,     # 累积损伤告警阈值
    "zuida_xiufu_changshi": 3,          # 单轮最大修复尝试次数
    "xiufu_jiange_miao": 120,           # 两次修复最小间隔(秒)
}

# 错误类型关键词（用于识别 zuijin_xingdong 中的错误）
CUOWU_LEIXING = {
    "jiazai_soul_shibai", "xieru_shibai", "duqu_shibai",
    "API_diaoyong_shibai", "shendu_shi", "cunchu_yichang",
    "neicun_yichang", "jiansuo_yichang", "yiwang_yichang",
    "xintiao_yichang", "zhouxin_yichang", "bianjie_shijian",
}


class ZiyuYinqing:
    """自愈引擎：心跳 tick 中周期性执行自检/诊断/修复/验证

    生命周期映射:
    - zijian()  → L0 health.py HealthState 检查
    - zhenduan() → L0 failure.py FailureKind 判定
    - xiufu()    → L3 self_healing_flow RecoveryAction
    - yanzheng_xiufu() → 验证循环闭合
    """

    def __init__(self):
        self._zijian_ji_shu = 0
        self._zhenduan_ji_shu = 0
        self._xiufu_ji_shu = 0
        self._xiufu_chenggong_ji_shu = 0
        self._xiufu_shibai_ji_shu = 0
        self._zuihou_xiufu: Optional[datetime] = None
        self._lianxu_xiufu_changshi = 0
        # 快照：修复前的身体状态（用于验证）
        self._yuanlai_zhuangtai_kuaizhao: Optional[dict] = None

    # ═══════════════════════════════════════════════════
    # zijian — 周期性自检
    # ═══════════════════════════════════════════════════
    def zijian(self, shenti: ShentiZhuangtai) -> ShentiZhuangtai:
        """心跳 tick 中调用：执行全面健康自检。

        检查项:
        ① 最近行动中连续错误数
        ② 记忆层数据损坏
        ③ 磁盘空间不足
        ④ 生命力/累积损伤

        任一异常 → 自动触发 zhenduan → xiufu 流程。

        Args:
            shenti: 当前身体状态

        Returns:
            可能已被修复的更新后身体状态
        """
        if not QIYONG_ZIYU:
            return shenti

        self._zijian_ji_shu += 1
        xianzai = datetime.now()

        yichang_list: list[dict] = []

        # ── ① 连续错误检查 ──
        lianxu_cuowu = self._tongji_lianxu_cuowu(shenti)
        if lianxu_cuowu >= ZIYU_YUZHI["lianxu_cuowu_yuzhi"]:
            yichang_list.append({
                "leixing": "lianxu_cuowu",
                "miaoshu": f"连续 {lianxu_cuowu} 次错误",
                "yanzhong_du": min(0.90, 0.30 + lianxu_cuowu * 0.15),
                "xiangqing": self._zhaiqu_zuijin_cuowu(shenti),
            })

        # ── ② 记忆层损坏检查 ──
        jiyi_sunhuai = self._jiancha_jiyi_ceng(shenti)
        if jiyi_sunhuai:
            yichang_list.append({
                "leixing": "jiyi_ceng_sunhuai",
                "miaoshu": f"记忆层损坏: {'; '.join(jiyi_sunhuai)}",
                "yanzhong_du": 0.75,
                "xiangqing": jiyi_sunhuai,
            })

        # ── ③ 磁盘空间检查 ──
        cipan_buzu = self._jiancha_cipan_kongjian(shenti)
        if cipan_buzu:
            yichang_list.append({
                "leixing": "cipan_kongjian_buzu",
                "miaoshu": cipan_buzu,
                "yanzhong_du": 0.55,
                "xiangqing": {"cipan_kongxian_mb": shenti.huanjing.cipan_kongxian},
            })

        # ── ④ 生命力/损伤检查 ──
        if shenti.shengmingli <= ZIYU_YUZHI["shengmingli_weixian_yuzhi"]:
            yichang_list.append({
                "leixing": "shengmingli_weixian",
                "miaoshu": f"生命力过低: {shenti.shengmingli:.3f}",
                "yanzhong_du": 0.85,
                "xiangqing": {"shengmingli": shenti.shengmingli},
            })

        if shenti.sunshang_leiji >= ZIYU_YUZHI["sunshang_gaojing_yuzhi"]:
            yichang_list.append({
                "leixing": "sunshang_leiji",
                "miaoshu": f"累积损伤过高: {shenti.sunshang_leiji:.3f}",
                "yanzhong_du": 0.70,
                "xiangqing": {"sunshang_leiji": shenti.sunshang_leiji},
            })

        # ── 无异常 → 直接返回 ──
        if not yichang_list:
            shenti.jiankang_zhuangtai = "zhengchang"
            return shenti

        # ── 按严重度排序 ──
        yichang_list.sort(key=lambda x: x["yanzhong_du"], reverse=True)

        # ── 触发诊断+修复 ──
        shenti.jiankang_zhuangtai = "yichang"
        shenti.zuijin_xingdong.append({
            "leixing": "zijian_yichang",
            "yichang_shu": len(yichang_list),
            "zuiyanzhong": yichang_list[0]["leixing"],
            "shijian": xianzai.isoformat(),
        })
        if len(shenti.zuijin_xingdong) > 50:
            shenti.zuijin_xingdong = shenti.zuijin_xingdong[-50:]

        # 修复频率控制
        if not self._keyi_xiufu(xianzai):
            return shenti

        # 对每个异常进行诊断+修复
        for yichang in yichang_list:
            zd = self.zhenduan(shenti)
            if zd.get("xuyao_xiufu"):
                # 保存修复前快照
                self._yuanlai_zhuangtai_kuaizhao = self._kuaizhao(shenti)
                shenti = self.xiufu(shenti, zd)
                # 验证
                chenggong = self.yanzheng_xiufu(shenti)
                if chenggong:
                    self._xiufu_chenggong_ji_shu += 1
                    self._lianxu_xiufu_changshi = 0
                    shenti.jiankang_zhuangtai = "zhengchang"
                    break  # 一次成功即可
                else:
                    self._xiufu_shibai_ji_shu += 1
                    self._lianxu_xiufu_changshi += 1

            if self._lianxu_xiufu_changshi >= ZIYU_YUZHI["zuida_xiufu_changshi"]:
                break

        self._zuihou_xiufu = xianzai
        return shenti

    # ═══════════════════════════════════════════════════
    # zhenduan — 根因诊断
    # ═══════════════════════════════════════════════════
    def zhenduan(self, shenti: ShentiZhuangtai) -> dict:
        """诊断身体异常的根因。

        分析 zuijin_xingdong 中的错误模式，判定故障类别。
        映射到 L0 failure.py FailureKind 概念。

        Args:
            shenti: 当前身体状态

        Returns:
            诊断结果字典:
            {
                "xuyao_xiufu": bool,
                "genyuanyin": str,        # 根因类别
                "yanzhong_du": float,     # 严重度 0-1
                "xiangqing": dict,        # 详细分析
                "jianyi_xiufu": str,      # 建议修复策略
            }
        """
        self._zhenduan_ji_shu += 1

        xingdong = shenti.zuijin_xingdong[-20:] if shenti.zuijin_xingdong else []
        cuowu_list = [x for x in xingdong if x.get("leixing", "") in CUOWU_LEIXING
                      or "shibai" in str(x.get("leixing", ""))
                      or "yichang" in str(x.get("leixing", ""))]

        # 无错误 → 无需修复
        if not cuowu_list:
            return {
                "xuyao_xiufu": False,
                "genyuanyin": "wu",
                "yanzhong_du": 0.0,
                "xiangqing": {},
                "jianyi_xiufu": "",
            }

        # ── 错误模式分析 ──
        cuowu_leixing_tongji: dict[str, int] = {}
        for cw in cuowu_list:
            lt = cw.get("leixing", "weizhi")
            cuowu_leixing_tongji[lt] = cuowu_leixing_tongji.get(lt, 0) + 1

        zhuyao_cuowu = max(cuowu_leixing_tongji, key=lambda k: cuowu_leixing_tongji[k])
        yanzhong_du = min(0.95, 0.20 + len(cuowu_list) * 0.12)

        # ── 根因判定 ──
        if "cunchu" in zhuyao_cuowu or "xieru" in zhuyao_cuowu or "duqu" in zhuyao_cuowu:
            genyuanyin = "cunchu_ceng_guzhang"
            jianyi = "chongzhi_cunchu_lujing"
        elif "API" in zhuyao_cuowu:
            genyuanyin = "API_lianjie_guzhang"
            jianyi = "xianliu_huo_chonglian"
        elif "jiazai" in zhuyao_cuowu or "soul" in zhuyao_cuowu.lower():
            genyuanyin = "soul_jiazai_guzhang"
            jianyi = "chongxin_jiazai_soul"
        elif "neicun" in zhuyao_cuowu:
            genyuanyin = "neicun_yichu"
            jianyi = "shifang_neicun"
        elif "jiansuo" in zhuyao_cuowu or "yiwang" in zhuyao_cuowu:
            genyuanyin = "jiyi_ceng_guzhang"
            jianyi = "xiufu_jiyi_ceng"
        else:
            genyuanyin = "weizhi_guzhang"
            jianyi = "chongzhi_zhuangtai"

        return {
            "xuyao_xiufu": True,
            "genyuanyin": genyuanyin,
            "yanzhong_du": round(yanzhong_du, 4),
            "xiangqing": {
                "cuowu_zongshu": len(cuowu_list),
                "zhuyao_cuowu": zhuyao_cuowu,
                "cuowu_fenbu": cuowu_leixing_tongji,
            },
            "jianyi_xiufu": jianyi,
        }

    # ═══════════════════════════════════════════════════
    # xiufu — 自动修复
    # ═══════════════════════════════════════════════════
    def xiufu(self, shenti: ShentiZhuangtai,
              zhenduan_jieguo: dict) -> ShentiZhuangtai:
        """根据诊断结果尝试自动修复。

        修复策略映射:
        - cunchu_ceng_guzhang → 重建存储路径
        - API_lianjie_guzhang → 重置API计数器
        - soul_jiazai_guzhang → 轻量级修复标记
        - neicun_yichu       → 清理冗余数据
        - jiyi_ceng_guzhang  → 修复记忆层文件
        - weizhi_guzhang     → 通用状态重置

        Args:
            shenti: 当前身体状态
            zhenduan_jieguo: zhenduan() 返回的诊断结果

        Returns:
            修复后的身体状态
        """
        self._xiufu_ji_shu += 1
        xianzai = datetime.now()

        genyuanyin = zhenduan_jieguo.get("genyuanyin", "")
        jianyi = zhenduan_jieguo.get("jianyi_xiufu", "")

        xiufu_dongzuo = "wu"

        # ── 策略1: 存储层故障 → 重建路径 ──
        if genyuanyin == "cunchu_ceng_guzhang":
            xiufu_dongzuo = self._xiufu_cunchu_lujing()
            shenti.huanjing.API_diaoyong_shu = max(0, shenti.huanjing.API_diaoyong_shu - 5)

        # ── 策略2: API 连接故障 → 重置计数器 ──
        elif genyuanyin == "API_lianjie_guzhang":
            shenti.huanjing.API_diaoyong_shu = 0
            shenti.huanjing.API_diaoyong_yue = API_ZUIDA_MEICI
            xiufu_dongzuo = "chongzhi_API_jishu"

        # ── 策略3: Soul 加载故障 → 轻量修复 ──
        elif genyuanyin == "soul_jiazai_guzhang":
            shenti.shengmingli = min(1.0, shenti.shengmingli + 0.10)
            xiufu_dongzuo = "tisheng_shengmingli"

        # ── 策略4: 内存溢出 → 清理+恢复生命力 ──
        elif genyuanyin == "neicun_yichu":
            shenti.shengmingli = min(1.0, shenti.shengmingli + 0.15)
            shenti.sunshang_leiji = max(0.0, shenti.sunshang_leiji - 0.10)
            xiufu_dongzuo = "shifang_neicun_huifu"

        # ── 策略5: 记忆层故障 → 修复记忆文件 ──
        elif genyuanyin == "jiyi_ceng_guzhang":
            xiufu_dongzuo = self._xiufu_jiyi_ceng()

        # ── 策略6: 通用重置 ──
        else:
            shenti.jiankang_zhuangtai = "zhengchang"
            shenti.shengmingli = max(shenti.shengmingli, 0.40)
            shenti.sunshang_leiji = max(0.0, shenti.sunshang_leiji - 0.15)
            xiufu_dongzuo = "tongyong_chongzhi"

        # ── 记录修复行动 ──
        shenti.zuijin_xingdong.append({
            "leixing": "ziyu_xiufu",
            "genyuanyin": genyuanyin,
            "dongzuo": xiufu_dongzuo,
            "jianyi": jianyi,
            "shijian": xianzai.isoformat(),
        })
        if len(shenti.zuijin_xingdong) > 50:
            shenti.zuijin_xingdong = shenti.zuijin_xingdong[-50:]

        return shenti

    # ═══════════════════════════════════════════════════
    # yanzheng_xiufu — 验证修复
    # ═══════════════════════════════════════════════════
    def yanzheng_xiufu(self, shenti: ShentiZhuangtai,
                       yuanlai_zhuangtai: Optional[dict] = None) -> bool:
        """验证修复是否成功。

        通过比较修复前后关键指标判定:
        - 健康状况是否恢复 'zhengchang'
        - 生命力是否不低于修复前
        - 连续错误是否已清零

        Args:
            shenti: 修复后的身体状态
            yuanlai_zhuangtai: 修复前快照（不传则使用内部快照）

        Returns:
            修复成功返回 True，否则 False
        """
        if yuanlai_zhuangtai is None:
            yuanlai_zhuangtai = self._yuanlai_zhuangtai_kuaizhao
        self._yuanlai_zhuangtai_kuaizhao = None  # 单次使用

        # ── ① 健康状态检查 ──
        if shenti.jiankang_zhuangtai != "zhengchang":
            return False

        # ── ② 连续错误检查 ──
        if self._tongji_lianxu_cuowu(shenti) >= ZIYU_YUZHI["lianxu_cuowu_yuzhi"]:
            return False

        # ── ③ 生命力对比（不低于修复前） ──
        if yuanlai_zhuangtai:
            yl_sml = yuanlai_zhuangtai.get("shengmingli", 0)
            if shenti.shengmingli < yl_sml - 0.05:  # 容忍5%波动
                return False

        # ── ④ 记忆层再次验证 ──
        if self._jiancha_jiyi_ceng(shenti):
            return False

        return True

    # ═══════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════
    def _tongji_lianxu_cuowu(self, shenti: ShentiZhuangtai) -> int:
        """统计 zuijin_xingdong 末尾连续错误数。"""
        if not shenti.zuijin_xingdong:
            return 0
        lianxu = 0
        for xd in reversed(shenti.zuijin_xingdong):
            lt = xd.get("leixing", "")
            if lt in CUOWU_LEIXING or "shibai" in lt or "yichang" in lt:
                lianxu += 1
            else:
                break
        return lianxu

    def _zhaiqu_zuijin_cuowu(self, shenti: ShentiZhuangtai) -> list[dict]:
        """摘取 zuijin_xingdong 末尾连续错误详情。"""
        cuowu_list = []
        for xd in reversed(shenti.zuijin_xingdong):
            lt = xd.get("leixing", "")
            if lt in CUOWU_LEIXING or "shibai" in lt or "yichang" in lt:
                cuowu_list.append(xd)
            else:
                break
        return list(reversed(cuowu_list))

    def _jiancha_jiyi_ceng(self, shenti: ShentiZhuangtai) -> list[str]:
        """检查记忆层数据文件完整性。

        检查 L1-L5 记忆文件是否存在且可读。
        Returns:
            损坏的层列表（空列表表示正常）
        """
        sunhuai = []
        ceng_lujing = {
            "L1": JIYI_LUJING / "l1_liushui",
            "L2": JIYI_LUJING / "l2_duanqi",
            "L3": JIYI_LUJING / "l3_xuexi",
            "L4": JIYI_LUJING / "l4_changqi",
            "L5": JIYI_LUJING / "l5_yongjiu",
        }
        for ceng, lp in ceng_lujing.items():
            try:
                if not lp.exists():
                    sunhuai.append(f"{ceng}_wenjian_quexian")
                    continue
                # 尝试读取前几个字节验证可读性
                _ = lp.stat()
                if lp.is_file():
                    text = lp.read_text(encoding="utf-8")
                    # 检查是否有明显的JSON损坏
                    for i, line in enumerate(text.strip().splitlines()):
                        if line.strip() and not line.strip().startswith("{"):
                            sunhuai.append(f"{ceng}_geshi_yichang_line{i+1}")
                            break
            except Exception as e:
                sunhuai.append(f"{ceng}_duqu_yichang: {str(e)[:50]}")
        return sunhuai

    def _jiancha_cipan_kongjian(self, shenti: ShentiZhuangtai) -> str:
        """检查磁盘空间是否不足。"""
        try:
            # 优先使用身体状态中缓存的值
            if shenti.huanjing.cipan_kongxian > 0:
                kongxian_mb = shenti.huanjing.cipan_kongxian
            else:
                # 实时检查 SHENTI_LUJING 所在磁盘
                total, used, free = shutil.disk_usage(str(SHENTI_LUJING))
                kongxian_mb = free // (1024 * 1024)
                shenti.huanjing.cipan_kongxian = kongxian_mb

            if kongxian_mb < ZIYU_YUZHI["cipan_kongxian_yuzhi_mb"]:
                return f"磁盘空间不足: {kongxian_mb}MB (阈值{ ZIYU_YUZHI['cipan_kongxian_yuzhi_mb']}MB)"
        except Exception:
            pass
        return ""

    def _keyi_xiufu(self, xianzai: datetime) -> bool:
        """检查修复频率是否允许本次修复。"""
        if self._zuihou_xiufu is not None:
            jiange = (xianzai - self._zuihou_xiufu).total_seconds()
            if jiange < ZIYU_YUZHI["xiufu_jiange_miao"]:
                return False
        return True

    def _kuaizhao(self, shenti: ShentiZhuangtai) -> dict:
        """创建身体状态快照（用于修复前后对比）。"""
        return {
            "shenti_id": shenti.shenti_id,
            "jiankang_zhuangtai": shenti.jiankang_zhuangtai,
            "shengmingli": shenti.shengmingli,
            "sunshang_leiji": shenti.sunshang_leiji,
            "zuijin_xingdong_changdu": len(shenti.zuijin_xingdong),
            "shijian": datetime.now().isoformat(),
        }

    def _xiufu_cunchu_lujing(self) -> str:
        """修复存储路径：确保关键目录存在且可写，文件路径只创建父目录。"""
        guanjian_lujing = [
            (SHENTI_LUJING, "dir"),
            (JIYI_LUJING, "dir"),
            (JINGYAN_LUJING, "file"),
        ]
        xiufu_shu = 0
        for lp, lx in guanjian_lujing:
            try:
                if lx == "dir":
                    lp.mkdir(parents=True, exist_ok=True)
                    test_file = lp / ".ziyu_xieru_ceshi"
                    test_file.write_text("ok", encoding="utf-8")
                    test_file.unlink()
                else:
                    # 文件路径：只创建父目录 + touch
                    lp.parent.mkdir(parents=True, exist_ok=True)
                    if not lp.exists():
                        lp.write_text("", encoding="utf-8")
                xiufu_shu += 1
            except Exception:
                continue
        return f"chongjian_lujing_{xiufu_shu}ge" if xiufu_shu > 0 else "chongjian_shibai"

    def _xiufu_jiyi_ceng(self) -> str:
        """修复记忆层：重置损坏的记忆文件。"""
        ceng_lujing = {
            "L1": JIYI_LUJING / "l1_liushui",
            "L2": JIYI_LUJING / "l2_duanqi",
            "L3": JIYI_LUJING / "l3_xuexi",
            "L4": JIYI_LUJING / "l4_changqi",
            "L5": JIYI_LUJING / "l5_yongjiu",
        }
        xiufu_shu = 0
        for ceng, lp in ceng_lujing.items():
            try:
                if not lp.exists():
                    lp.parent.mkdir(parents=True, exist_ok=True)
                    lp.write_text("", encoding="utf-8")
                    xiufu_shu += 1
                    continue
                # 验证内容，损坏则备份后重置
                text = lp.read_text(encoding="utf-8")
                if text.strip():
                    try:
                        for line in text.strip().splitlines():
                            import json
                            json.loads(line)
                    except Exception:
                        # 损坏：备份并重置
                        backup = lp.with_suffix(f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
                        lp.rename(backup)
                        lp.write_text("", encoding="utf-8")
                        xiufu_shu += 1
            except Exception:
                try:
                    lp.write_text("", encoding="utf-8")
                    xiufu_shu += 1
                except Exception:
                    pass
        return f"xiufu_jiyi_ceng_{xiufu_shu}ge" if xiufu_shu > 0 else "xiufu_wuxiao"

    # ═══════════════════════════════════════════════════
    # 统计接口
    # ═══════════════════════════════════════════════════
    def ziyu_tongji(self) -> dict:
        """获取自愈引擎运行统计。"""
        return {
            "zijian_cishu": self._zijian_ji_shu,
            "zhenduan_cishu": self._zhenduan_ji_shu,
            "xiufu_cishu": self._xiufu_ji_shu,
            "xiufu_chenggong": self._xiufu_chenggong_ji_shu,
            "xiufu_shibai": self._xiufu_shibai_ji_shu,
            "lianxu_xiufu_changshi": self._lianxu_xiufu_changshi,
            "zuihou_xiufu": self._zuihou_xiufu.isoformat() if self._zuihou_xiufu else None,
            "chenggong_lv": round(
                self._xiufu_chenggong_ji_shu / max(1, self._xiufu_ji_shu), 4
            ),
        }
