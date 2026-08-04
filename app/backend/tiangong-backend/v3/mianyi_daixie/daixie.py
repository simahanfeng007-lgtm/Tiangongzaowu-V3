"""
天工造物 v3：起源 — 代谢层 (DaixieCeng)
资源预算 / 凭证管理 / 消耗记录 / 环境感知
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..shenti_zhuangtai import ShentiZhuangtai
from .. import peizhi


class DaixieCeng:
    """代谢层：管理她的能量预算与资源消耗。

    类比生物新陈代谢：
    - gengxin: 更新身体资源统计
    - jiancha_yusuan: 行动前预算检查
    - jilu_xiaohao: 记录每次消耗（磨损生命值）
    - huanjing_guanzhi: 更新环境感知
    """

    # 各操作类型的周期上限
    _SHANGXIAN: dict[str, int] = {
        "wenjian_duqu": 100,
        "wenjian_xieru": 50,
        "zhongduan_mingling": 20,
        "wangluo_qingqiu": 10,
    }

    def __init__(self):
        self._xiaohao: dict[str, int] = {
            "API_diaoyong": 0,
            "wenjian_duqu": 0,
            "wenjian_xieru": 0,
            "zhongduan_mingling": 0,
            "wangluo_qingqiu": 0,
            "neicun_shiyong": 0,
        }
        self._lishi: list[dict] = []
        self._token_guji: int = 0

    # ═══════════════════════════════════════════════════════
    #  gengxin — 更新身体资源统计
    # ═══════════════════════════════════════════════════════

    def gengxin(self, shenti: ShentiZhuangtai) -> ShentiZhuangtai:
        """将当前周期消耗数据同步到 shenti.huanjing。

        更新：API调用数、内存使用、磁盘空间。
        """
        h = shenti.huanjing
        h.API_diaoyong_shu += self._xiaohao.get("API_diaoyong", 0)
        h.neicun_shiyong = self._xiaohao.get("neicun_shiyong", 0)
        try:
            import shutil
            h.cipan_kongxian = shutil.disk_usage(Path.home()).used
        except Exception:
            pass
        return shenti

    # ═══════════════════════════════════════════════════════
    #  jiancha_yusuan — 行动前预算检查
    # ═══════════════════════════════════════════════════════

    def jiancha_yusuan(
        self, shenti: ShentiZhuangtai, action_type: str
    ) -> bool:
        """检查当前是否有足够预算执行该操作。

        action_type:
            API_diaoyong / wenjian_duqu / wenjian_xieru
            / zhongduan_mingling / wangluo_qingqiu

        检查项：月配额、单次上限、周期上限、生命值。
        返回 True 表示预算充足。
        """
        h = shenti.huanjing

        # 生命值过低 → 拒绝一切消耗
        if shenti.shengmingli < 0.1:
            return False

        # API 调用：检查月配额和单次唤醒上限
        if action_type == "API_diaoyong":
            if h.API_diaoyong_shu >= h.API_diaoyong_yue:
                return False
            if self._xiaohao.get("API_diaoyong", 0) >= peizhi.API_ZUIDA_MEICI:
                return False
            return True

        # 其他操作类型：检查周期上限
        shangxian = self._SHANGXIAN.get(action_type)
        if shangxian is not None:
            if self._xiaohao.get(action_type, 0) >= shangxian:
                return False
            return True

        return True

    # ═══════════════════════════════════════════════════════
    #  jilu_xiaohao — 记录消耗
    # ═══════════════════════════════════════════════════════

    def jilu_xiaohao(
        self,
        shenti: ShentiZhuangtai,
        action_type: str,
        xiangqing: Optional[dict] = None,
    ) -> ShentiZhuangtai:
        """记录一次资源消耗。

        xiangqing 格式::
            {
                "liang": 1,        # 消耗量
                "beizhu": "...",   # 备注
                "token_shu": 500,  # API调用时的token估算
            }

        副作用：微量磨损生命值、累积伤害。
        """
        q = xiangqing or {}
        liang = q.get("liang", 1)

        # 更新计数器
        if action_type in self._xiaohao:
            self._xiaohao[action_type] += liang
        if action_type == "API_diaoyong":
            self._token_guji += q.get("token_shu", 500)

        # 记录历史
        self._lishi.append({
            "shijian": datetime.now(timezone.utc).isoformat(),
            "action_type": action_type,
            "liang": liang,
            "beizhu": q.get("beizhu", ""),
            "leiji_API": self._xiaohao.get("API_diaoyong", 0),
        })

        # 生命值磨损
        moli = 0.001 * liang
        shenti.shengmingli = max(0.0, shenti.shengmingli - moli)
        shenti.sunshang_leiji += moli

        return shenti

    # ═══════════════════════════════════════════════════════
    #  huanjing_guanzhi — 环境感知
    # ═══════════════════════════════════════════════════════

    def huanjing_guanzhi(self, shenti: ShentiZhuangtai) -> ShentiZhuangtai:
        """更新环境感知信息。

        探测：
        - 凭证配置状态（已配置/未配置）
        - 磁盘空间使用
        - 运行环境类型（桌面/服务器/CI）
        """
        h = shenti.huanjing
        h.pingzheng_zhuangtai = self._jiancha_pingzheng()
        h.huanjing_leixing = self._tance_huanjing()
        try:
            import shutil
            h.cipan_kongxian = shutil.disk_usage(Path.home()).used
        except Exception:
            pass
        return shenti

    @staticmethod
    def _jiancha_pingzheng() -> str:
        """检查凭证配置状态：扫描环境变量和配置文件"""
        keys = []
        env_map = {
            "OPENAI_API_KEY": "openai",
            "ANTHROPIC_API_KEY": "anthropic",
            "DEEPSEEK_API_KEY": "deepseek",
            "GOOGLE_API_KEY": "google",
        }
        for env, name in env_map.items():
            if os.environ.get(env):
                keys.append(name)

        kf = Path.home() / ".tiangong" / "api_keys.json"
        if kf.exists():
            try:
                keys.extend(json.loads(kf.read_text()).keys())
            except Exception:
                pass

        return f"yipeizhi: {','.join(keys)}" if keys else "weipeizhi"

    @staticmethod
    def _tance_huanjing() -> str:
        """探测当前运行环境类型"""
        if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
            return "ci_server"
        if os.environ.get("SSH_TTY") or os.environ.get("SSH_CONNECTION"):
            return "remote_server"
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return "desktop_gui"
        return "desktop_gui"

    # ── 查询 ──

    def dangqian_xiaohao_baogao(self) -> dict:
        """获取当前周期消耗报告"""
        return {
            "dangqian_xiaohao": dict(self._xiaohao),
            "token_guji": self._token_guji,
            "lishi_tiaoshu": len(self._lishi),
        }
