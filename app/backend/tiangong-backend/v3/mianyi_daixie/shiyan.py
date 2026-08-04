"""
天工造物 v3：起源 — 实验框架 (ShiyanKuangjia)
沙箱隔离实验 → 结果评估 → 生产迁移
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..shenti_zhuangtai import ShentiZhuangtai
from .. import peizhi


class ShiyanKuangjia:
    """实验框架：她进行自我改进的沙箱机制。

    流程：kaishi_shiyan → 隔离执行 → jieshu_shiyan(收集+评估)
          → qianyi_shengchan(提升) 或 丢弃。

    支持实验类型：
    - jiyi_celve / jingyan_youhua / jinhua_xingwei
    - qudong_tiaozheng / goutong_fangshi / gongju_shiyong
    """

    KEHUO_LEIXING = {
        "jiyi_celve", "jingyan_youhua", "jinhua_xingwei",
        "qudong_tiaozheng", "goutong_fangshi", "gongju_shiyong",
    }

    def __init__(self):
        self._shiyan_list: list[dict] = []
        self._huoyue: Optional[dict] = None
        peizhi.SHIYAN_LUJING.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════
    #  kaishi_shiyan — 开始实验
    # ═══════════════════════════════════════════════════════

    def kaishi_shiyan(
        self, shenti: ShentiZhuangtai, shiyan_leixing: str
    ) -> ShentiZhuangtai:
        """创建实验沙箱，在隔离环境中实验。

        1. 验证实验类型是否合法
        2. 如已有活跃实验则先结束
        3. 创建沙箱目录、快照身体状态
        4. 更新 shenti.jinhua 标记为实验中
        """
        if shiyan_leixing not in self.KEHUO_LEIXING:
            shenti.zuijin_xingdong.append({
                "shijian": datetime.now(timezone.utc).isoformat(),
                "dongzuo": "shiyan_leixing_wuxiao",
                "shiyan_leixing": shiyan_leixing,
            })
            return shenti

        # 先结束已有实验
        if self._huoyue:
            shenti = self.jieshu_shiyan(shenti)

        shy_id = f"shiyan_{uuid.uuid4().hex[:8]}"
        shaxiang = peizhi.SHIYAN_LUJING / shy_id
        shaxiang.mkdir(parents=True, exist_ok=True)

        shiyan = {
            "shiyan_id": shy_id,
            "shiyan_leixing": shiyan_leixing,
            "kaishi_shijian": datetime.now(timezone.utc).isoformat(),
            "yuanshi_shengmingli": shenti.shengmingli,
            "yuanshi_zizhu": shenti.anquan.zizhu_jibie,
            "yuanshi_jinhua": shenti.jinhua.dangqian_jieduan,
            "zhuangtai": "jinxingzhong",
            "jilu": [],
        }

        # 持久化实验元数据
        (shaxiang / "yuanshuju.json").write_text(
            json.dumps(shiyan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self._shiyan_list.append(shiyan)
        self._huoyue = shiyan

        shenti.jinhua.huoyue_shiyan = shy_id
        shenti.jinhua.dangqian_jieduan = "shiyan"
        shenti.zuijin_xingdong.append({
            "shijian": datetime.now(timezone.utc).isoformat(),
            "dongzuo": "shiyan_kaishi",
            "shiyan_id": shy_id,
            "shiyan_leixing": shiyan_leixing,
        })

        return shenti

    # ═══════════════════════════════════════════════════════
    #  jieshu_shiyan — 结束实验
    # ═══════════════════════════════════════════════════════

    def jieshu_shiyan(self, shenti: ShentiZhuangtai) -> ShentiZhuangtai:
        """结束当前实验，收集结果并评估。

        评估标准：
        - 有实验记录 + 生命值未显著下降 → tisheng（提升）
        - 有实验记录但生命值下降较多 → baochi（保持）
        - 无实验记录 → diuqi（丢弃）
        """
        if not self._huoyue:
            return shenti

        shy = self._huoyue
        shy_id = shy["shiyan_id"]
        shaxiang = peizhi.SHIYAN_LUJING / shy_id

        # 读取实验记录条数
        jilu_file = shaxiang / "shiyan_jilu.jsonl"
        tiaoshu = 0
        if jilu_file.exists():
            try:
                text = jilu_file.read_text(encoding="utf-8").strip()
                tiaoshu = len([l for l in text.split("\n") if l.strip()])
            except Exception:
                pass

        # 评估判定
        shengming_bianhua = shenti.shengmingli - shy.get(
            "yuanshi_shengmingli", 1.0
        )
        if tiaoshu > 0 and shengming_bianhua > -0.05:
            pinggu = "tisheng"
        elif tiaoshu > 0:
            pinggu = "baochi"
        else:
            pinggu = "diuqi"

        jieguo = {
            "pinggu": pinggu,
            "jilu_tiaoshu": tiaoshu,
            "shengmingli_bianhua": shengming_bianhua,
            "shengmingli_dangqian": shenti.shengmingli,
        }

        shy["jieshu_shijian"] = datetime.now(timezone.utc).isoformat()
        shy["zhuangtai"] = pinggu
        shy["jieguo"] = jieguo

        (shaxiang / "jieguo.json").write_text(
            json.dumps(shy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self._huoyue = None
        shenti.jinhua.huoyue_shiyan = None
        shenti.jinhua.dangqian_jieduan = "pinggu"
        shenti.zuijin_xingdong.append({
            "shijian": datetime.now(timezone.utc).isoformat(),
            "dongzuo": "shiyan_jieshu",
            "shiyan_id": shy_id,
            "pinggu": pinggu,
        })

        return shenti

    # ═══════════════════════════════════════════════════════
    #  qianyi_shengchan — 提升到生产环境
    # ═══════════════════════════════════════════════════════

    def qianyi_shengchan(
        self, shenti: ShentiZhuangtai, shiyan_chanwu: dict
    ) -> ShentiZhuangtai:
        """将评估为 tisheng 的实验产物迁移到生产环境。

        迁移条件：
        - 实验记录存在
        - 评估结果为 tisheng
        - 产物格式正确

        迁移后：
        - 写入 jinhua.gaijin_lishi
        - 恢复为 guancha 阶段
        """
        shy_id = shiyan_chanwu.get("shiyan_id", "")
        mubiao = next(
            (s for s in self._shiyan_list if s["shiyan_id"] == shy_id),
            None,
        )

        if not mubiao:
            shenti.zuijin_xingdong.append({
                "shijian": datetime.now(timezone.utc).isoformat(),
                "dongzuo": "qianyi_shibai",
                "yuanyin": "shiyan_bucunzai",
                "shiyan_id": shy_id,
            })
            return shenti

        if mubiao.get("jieguo", {}).get("pinggu") != "tisheng":
            shenti.zuijin_xingdong.append({
                "shijian": datetime.now(timezone.utc).isoformat(),
                "dongzuo": "qianyi_shibai",
                "yuanyin": "pinggu_bugou",
                "shiyan_id": shy_id,
            })
            return shenti

        # 执行迁移
        shenti.jinhua.gaijin_lishi.append({
            "shiyan_id": shy_id,
            "shiyan_leixing": mubiao["shiyan_leixing"],
            "qianyi_shijian": datetime.now(timezone.utc).isoformat(),
            "chanwu": shiyan_chanwu,
        })

        # 截断历史
        if len(shenti.jinhua.gaijin_lishi) > 100:
            shenti.jinhua.gaijin_lishi = shenti.jinhua.gaijin_lishi[-50:]

        shenti.jinhua.dangqian_jieduan = "guancha"
        shenti.zuijin_xingdong.append({
            "shijian": datetime.now(timezone.utc).isoformat(),
            "dongzuo": "qianyi_chenggong",
            "shiyan_id": shy_id,
            "shiyan_leixing": mubiao["shiyan_leixing"],
        })

        return shenti

    # ── 查询 ──

    def shiyan_zuijin(self, xian: int = 10) -> list[dict]:
        """获取最近 N 个实验（按时间倒序）"""
        return sorted(
            self._shiyan_list,
            key=lambda s: s.get("kaishi_shijian", ""),
            reverse=True,
        )[:xian]
