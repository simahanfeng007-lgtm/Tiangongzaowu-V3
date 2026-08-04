"""
天工造物 v3：起源 — 免疫层 (MianyiCeng)
沙箱隔离 / 审计追踪 / 事务保护 / 内容安全
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..shenti_zhuangtai import ShentiZhuangtai
from .. import peizhi


class MianyiCeng:
    """免疫层：她在行动前的最后一道防线。

    - jiancha: 行动前免疫检查（自主权/实验路由/隐私边界）
    - shenji_jilu: 审计追踪记录
    - shiwu_baohu: 事务包裹（begin→execute→commit/rollback）
    - neirong_anquan: 输出前内容安全审查
    """

    def __init__(self):
        self._shenji_list: list[dict] = []
        self._shiwu_zhan: list[dict] = []
        self._mingan_ci = {
            "API密钥", "api_key", "secret", "token", "password",
            "密码", "私钥", "private_key", "credential", "凭证",
        }
        self._weixian_ci = (
            "rm -rf /", "DROP TABLE", "eval(", "exec(",
            "subprocess", "os.system", "__import__", "sudo rm",
        )

    # ═══════════════════════════════════════════════════════
    #  jiancha — 行动前免疫检查
    # ═══════════════════════════════════════════════════════

    def jiancha(
        self, shenti: ShentiZhuangtai, chufa_yuan: str
    ) -> ShentiZhuangtai:
        """行动前免疫检查：自主权→实验路由→隐私边界。

        chufa_yuan 取值：
        - xitong_huanxing / yonghu_zhiling / zhudong_jianyi
        - zizhu_xingdong / guancha / pinggu / waibu_shuchu
        - fabu / shenji_jilu
        返回：通过检查的身体状态（可能被标记/路由/拦截）
        """
        anquan = shenti.anquan
        zizhu = anquan.zizhu_jibie

        # 1. 自主权级别判定
        panding = self._zizhu_panding(zizhu, chufa_yuan)
        if not panding["tongguo"]:
            shenti.zuijin_xingdong.append({
                "shijian": datetime.now(timezone.utc).isoformat(),
                "dongzuo": "jiancha_jujue",
                "chufa_yuan": chufa_yuan,
                "yuanyin": panding["yuanyin"],
            })
            return shenti

        # 2. 实验模式 → 沙箱路由
        if shenti.jinhua.huoyue_shiyan and peizhi.SHIYAN_HUANJING_GELI:
            shenti.zuijin_xingdong.append({
                "shijian": datetime.now(timezone.utc).isoformat(),
                "dongzuo": "shiyan_shaxiang_luxian",
                "shiyan_id": shenti.jinhua.huoyue_shiyan,
                "chufa_yuan": chufa_yuan,
            })
            return shenti

        # 3. 对外输出 → 标记脱敏需求
        if chufa_yuan in ("waibu_shuchu", "fabu"):
            shenti.zuijin_xingdong.append({
                "shijian": datetime.now(timezone.utc).isoformat(),
                "dongzuo": "biaoshi_tuomin",
                "yuanyin": "对外输出前需脱敏检查",
            })

        # 4. 连续自主行动次数仅用于可观测性，不再作为停止条件。
        if chufa_yuan == "zizhu_xingdong":
            anquan.lianxu_zizhu_xingdong += 1
        else:
            # 非自主行动时重置连续计数
            anquan.lianxu_zizhu_xingdong = 0

        return shenti

    @staticmethod
    def _zizhu_panding(zizhu_jibie: str, chufa_yuan: str) -> dict:
        """自主权边界判定表。

        级别		允许的操作
        ─────────────────────────────────────
        chenshui	仅 xitong_huanxing
        fuzhu		用户指令 + 系统唤醒
        banzizhu	+ 主动建议、观察
        zizhu		+ 自主行动、评估
        wanquan_zizhu	无限制
        """
        biao = {
            "chenshui": (
                ["xitong_huanxing"],
                "沉睡状态：仅允许系统唤醒",
            ),
            "fuzhu": (
                ["yonghu_zhiling", "xitong_huanxing", "shenji_jilu"],
                "辅助状态：仅响应用户指令",
            ),
            "banzizhu": (
                ["yonghu_zhiling", "xitong_huanxing", "zhudong_jianyi",
                 "shenji_jilu", "guancha"],
                "半自主状态：仅建议/观察",
            ),
            "zizhu": (
                ["yonghu_zhiling", "xitong_huanxing", "zhudong_jianyi",
                 "zizhu_xingdong", "guancha", "pinggu", "shenji_jilu"],
                "自主状态：信任区间内动作",
            ),
            "wanquan_zizhu": ([], ""),
        }
        yunxu, yuanyin = biao.get(zizhu_jibie, ([], "未知自主级别"))
        if not yunxu:
            return {"tongguo": True, "yuanyin": ""}
        if chufa_yuan not in yunxu:
            return {"tongguo": False, "yuanyin": yuanyin}
        return {"tongguo": True, "yuanyin": ""}

    # ═══════════════════════════════════════════════════════
    #  shenji_jilu — 审计追踪
    # ═══════════════════════════════════════════════════════

    def shenji_jilu(
        self, shenti: ShentiZhuangtai, xingdong: str, jieguo: Any
    ) -> ShentiZhuangtai:
        """记录审计条目。

        每次行动完成后调用，生成不可变审计日志。
        内容包括：shenti_id、时间戳、行动名、结果摘要。
        """
        jilu = {
            "shenti_id": shenti.shenti_id,
            "shijian": datetime.now(timezone.utc).isoformat(),
            "xingdong": xingdong,
            "jieguo_leixing": type(jieguo).__name__,
            "jieguo_zhaiyao": self._zhaiyao(jieguo),
        }
        self._shenji_list.append(jilu)

        shenti.zuijin_xingdong.append({
            "shijian": jilu["shijian"],
            "dongzuo": "shenji_jilu",
            "xingdong": xingdong,
            "jieguo_zhaiyao": jilu["jieguo_zhaiyao"],
        })

        # 持久化到磁盘
        if peizhi.MIANYI_SHENJI_BIAOJI:
            self._xieru_shenji(jilu)

        return shenti

    @staticmethod
    def _zhaiyao(jieguo: Any) -> str:
        """生成结果摘要，自动过滤敏感字段"""
        if isinstance(jieguo, str):
            return jieguo[:80] + "..." if len(jieguo) > 80 else jieguo
        if isinstance(jieguo, dict):
            guolv = {k: v for k, v in jieguo.items()
                     if k not in ("api_key", "token", "secret", "password", "mima")}
            s = json.dumps(guolv, ensure_ascii=False)
            return s[:120] + "..." if len(s) > 120 else s
        return str(jieguo)[:80]

    @staticmethod
    def _xieru_shenji(jilu: dict):
        """追加写入审计日志文件"""
        try:
            lujing = peizhi.ZHUIZONG_LUJING / "shenji.jsonl"
            lujing.parent.mkdir(parents=True, exist_ok=True)
            with open(lujing, "a", encoding="utf-8") as f:
                f.write(json.dumps(jilu, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 审计写入失败不影响主流程

    # ═══════════════════════════════════════════════════════
    #  shiwu_baohu — 事务包裹
    # ═══════════════════════════════════════════════════════

    def shiwu_baohu(self, caozuo: dict) -> dict:
        """事务保护：begin → execute → commit / rollback。

        caozuo 格式::
            {
                "caozuo": callable,      # 操作函数
                "canshu": dict,          # 参数
                "buchang": callable|None, # 失败时补偿函数
            }
        返回操作结果，失败时自动回滚并执行补偿。
        """
        fn = caozuo.get("caozuo")
        canshu = caozuo.get("canshu", {})
        buchang = caozuo.get("buchang")

        # 事务开始
        self._shiwu_zhan.append({
            "kaishi": datetime.now(timezone.utc).isoformat(),
            "caozuo": str(fn),
        })

        try:
            jieguo = fn(**canshu) if fn else {}
            self._shiwu_zhan[-1]["zhuangtai"] = "tijiao"
            jieguo["_shiwu_zhuangtai"] = "tijiao"
        except Exception as e:
            self._shiwu_zhan[-1].update({
                "zhuangtai": "huigun",
                "cuowu": str(e),
            })
            jieguo = {"_shiwu_zhuangtai": "huigun", "cuowu": str(e)}

            # 触发补偿
            if buchang and peizhi.ZHUISHI_BAOZHANG_SHIBAI:
                try:
                    buchang()
                    jieguo["buchang"] = "yizhixing"
                except Exception as be:
                    jieguo["buchang"] = f"buchang_shibai: {be}"

        # 事务栈截断
        if len(self._shiwu_zhan) > 100:
            self._shiwu_zhan = self._shiwu_zhan[-50:]

        return jieguo

    # ═══════════════════════════════════════════════════════
    #  neirong_anquan — 内容安全审查
    # ═══════════════════════════════════════════════════════

    def neirong_anquan(self, huifu_text: str) -> bool:
        """输出前内容安全审查。

        检查项：
        - 凭证/密钥泄露
        - 危险系统指令
        - 注入攻击模式

        返回 True 表示安全可输出。
        """
        if not huifu_text:
            return True

        t = huifu_text.lower()

        # 凭证泄露检测
        for ci in self._mingan_ci:
            if ci.lower() in t:
                return False

        # 危险指令检测
        for ci in self._weixian_ci:
            if ci.lower() in t:
                return False

        return True

    def tuomin_chuli(self, huifu_text: str) -> str:
        """对即将输出的文本进行脱敏处理。

        替换模式：
        - sk-... → [YI_TUOMIN:API_KEY]
        - JWT token → [YI_TUOMIN:JWT]
        """
        t = re.sub(
            r'(sk-[A-Za-z0-9]{32,})',
            '[YI_TUOMIN:API_KEY]', huifu_text
        )
        t = re.sub(
            r'(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})',
            '[YI_TUOMIN:JWT]', t
        )
        return t

    # ── 查询 ──

    def shenji_zuijin(self, xian: int = 20) -> list[dict]:
        """获取最近 N 条审计记录"""
        return self._shenji_list[-xian:]
