"""
天工造物 v3：起源 — 版本迁移
banben_qianyi.py: 数据格式的版本检查、迁移与备份
使用 L0 versioning.py 模式 (MigrationKind, SchemaVersion)
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from ..peizhi import (
    DANGQIAN_BANBEN,
    BANBEN_LUJING,
    SHENTI_DANGQIAN,
    JIYI_LUJING,
    JINGYAN_LUJING,
    NENGLI_ZHUCE_LUJING,
)


# ---- L0 versioning.py 模式定义 ----

class MigrationKind(Enum):
    """L0 迁移类型"""
    NONE = "none"                   # 无需迁移
    ADDITIVE = "additive"           # 新增字段（向前兼容）
    TRANSFORM = "transform"         # 字段转换（需代码处理）
    BREAKING = "breaking"           # 破坏性变更（需完全重建）


class SchemaVersion:
    """L0 模式版本"""
    def __init__(self, major: int, minor: int, patch: int):
        self.major = major
        self.minor = minor
        self.patch = patch

    def __str__(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SchemaVersion):
            return (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)
        return False

    def __lt__(self, other: "SchemaVersion") -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __gt__(self, other: "SchemaVersion") -> bool:
        return (self.major, self.minor, self.patch) > (other.major, other.minor, other.patch)

    @classmethod
    def from_string(cls, version_str: str) -> "SchemaVersion":
        """从 'v3.0.0' 字符串解析"""
        v = version_str.lstrip("vV")
        parts = v.split(".")
        return cls(
            major=int(parts[0]) if len(parts) > 0 else 0,
            minor=int(parts[1]) if len(parts) > 1 else 0,
            patch=int(parts[2]) if len(parts) > 2 else 0,
        )


def _jueding_qianyi_leixing(cong: SchemaVersion, dao: SchemaVersion) -> MigrationKind:
    """
    根据版本差异决定迁移类型。
    - 补丁版本变更 -> ADDITIVE
    - 次版本变更 -> TRANSFORM
    - 主版本变更 -> BREAKING
    """
    if cong.major != dao.major:
        return MigrationKind.BREAKING
    if cong.minor != dao.minor:
        return MigrationKind.TRANSFORM
    if cong.patch != dao.patch:
        return MigrationKind.ADDITIVE
    return MigrationKind.NONE


# ---- 需要跟踪版本的数据文件 ----
_SHUJU_WENJIAN = [
    SHENTI_DANGQIAN,
    JIYI_LUJING,
    JINGYAN_LUJING,
    NENGLI_ZHUCE_LUJING,
]


class BanbenQianyi:
    """
    版本迁移管理器。
    检查当前代码版本与数据版本，执行数据格式迁移。
    """

    def __init__(self, banben_lujing: Optional[Path] = None):
        self.banben_lujing = banben_lujing or BANBEN_LUJING
        self.dangqian_banben = DANGQIAN_BANBEN

    def jiancha_banben(self) -> dict:
        """
        检查当前代码版本与数据版本是否一致。
        
        Returns:
            dict: {
                "daima_banben": str,
                "shuju_banben": str,
                "yizhi": bool,
                "xuyao_qianyi": bool,
                "qianyi_leixing": str,  # MigrationKind
                "xiangqing": str,
            }
        """
        daima = SchemaVersion.from_string(self.dangqian_banben)
        shuju_banben_str = self._du_shuju_banben()
        shuju = SchemaVersion.from_string(shuju_banben_str) if shuju_banben_str else daima

        qianyi_leixing = _jueding_qianyi_leixing(shuju, daima)

        # 检查数据文件是否存在
        que_shi_wenjian = [f for f in _SHUJU_WENJIAN if not f.exists()]

        return {
            "daima_banben": str(daima),
            "shuju_banben": str(shuju),
            "yizhi": daima == shuju,
            "xuyao_qianyi": qianyi_leixing != MigrationKind.NONE,
            "qianyi_leixing": qianyi_leixing.value,
            "que_shi_wenjian": que_shi_wenjian,
            "xiangqing": self._shengcheng_xiangqing(daima, shuju, qianyi_leixing),
        }

    def qianyi_shuju(self, cong_banben: str, dao_banben: str) -> bool:
        """
        执行数据格式迁移。
        
        Args:
            cong_banben: 源版本 'v3.0.0'
            dao_banben: 目标版本 'v3.1.0'
        
        Returns:
            True 迁移成功
        """
        cong = SchemaVersion.from_string(cong_banben)
        dao = SchemaVersion.from_string(dao_banben)
        qianyi_leixing = _jueding_qianyi_leixing(cong, dao)

        # 1. 先备份
        beifen_lujing = self.beifen_dangqian()
        if not beifen_lujing:
            raise RuntimeError("备份失败，迁移中止")

        # 2. 按迁移类型处理
        try:
            if qianyi_leixing == MigrationKind.ADDITIVE:
                self._qianyi_zengliang()
            elif qianyi_leixing == MigrationKind.TRANSFORM:
                self._qianyi_zhuanhuan(cong, dao)
            elif qianyi_leixing == MigrationKind.BREAKING:
                self._qianyi_pohuai(cong, dao)
            # NONE: 无需迁移

            # 3. 更新版本记录
            self._xie_shuju_banben(str(dao))
            return True
        except Exception:
            # 迁移失败，需要手动恢复备份
            return False

    def beifen_dangqian(self) -> str:
        """
        备份当前数据。
        
        Returns:
            备份目录路径字符串
        """
        shijian_chuo = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        beifen_mulu = self.banben_lujing.parent / "beifen" / f"qianyi_{shijian_chuo}"
        beifen_mulu.mkdir(parents=True, exist_ok=True)

        for wenjian in _SHUJU_WENJIAN:
            if wenjian.exists():
                mubiao = beifen_mulu / wenjian.name
                if wenjian.is_dir():
                    shutil.copytree(wenjian, mubiao)
                else:
                    shutil.copy2(wenjian, mubiao)

        # 也备份版本号
        if self.banben_lujing.exists():
            shutil.copy2(self.banben_lujing, beifen_mulu / "banben.json")

        return str(beifen_mulu)

    # ---- 内部方法 ----

    def _du_shuju_banben(self) -> str:
        """读取数据版本号"""
        if not self.banben_lujing.exists():
            return self.dangqian_banben
        try:
            data = json.loads(self.banben_lujing.read_text(encoding="utf-8"))
            return data.get("banben", self.dangqian_banben)
        except (json.JSONDecodeError, KeyError):
            return self.dangqian_banben

    def _xie_shuju_banben(self, banben: str):
        """写入数据版本号"""
        self.banben_lujing.parent.mkdir(parents=True, exist_ok=True)
        self.banben_lujing.write_text(
            json.dumps({
                "banben": banben,
                "gengxin_shijian": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _shengcheng_xiangqing(
        self,
        daima: SchemaVersion,
        shuju: SchemaVersion,
        qianyi_leixing: MigrationKind,
    ) -> str:
        """生成版本差异详情"""
        if daima == shuju:
            return "代码版本与数据版本一致，无需迁移"
        if qianyi_leixing == MigrationKind.ADDITIVE:
            return f"补丁升级：{shuju} → {daima}，新增字段向前兼容"
        elif qianyi_leixing == MigrationKind.TRANSFORM:
            return f"次版本升级：{shuju} → {daima}，字段需转换处理"
        elif qianyi_leixing == MigrationKind.BREAKING:
            return f"主版本升级：{shuju} → {daima}，破坏性变更需重建数据"
        return "未知迁移类型"

    def _qianyi_zengliang(self):
        """ADDITIVE 迁移：补充缺失字段（默认值）"""
        # 对 SHENTI_DANGQIAN 补充新字段
        if SHENTI_DANGQIAN.exists():
            try:
                data = json.loads(SHENTI_DANGQIAN.read_text(encoding="utf-8"))
                data.setdefault("banben", str(SchemaVersion.from_string(self.dangqian_banben)))
                data.setdefault("zhili_ceng", {"zuihou_jiancha": None, "anquan_jibie": "di"})
                SHENTI_DANGQIAN.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except json.JSONDecodeError:
                pass

    def _qianyi_zhuanhuan(self, cong: SchemaVersion, dao: SchemaVersion):
        """TRANSFORM 迁移：字段格式转换"""
        # 子类可覆写具体转换逻辑
        # 默认直接调用增量迁移
        self._qianyi_zengliang()

    def _qianyi_pohuai(self, cong: SchemaVersion, dao: SchemaVersion):
        """BREAKING 迁移：破坏性变更（仅标记，实际由上层决策）"""
        self.banben_lujing.parent.mkdir(parents=True, exist_ok=True)
        qianyi_biaoji = self.banben_lujing.parent / "qianyi_BREAKING.biaoji"
        qianyi_biaoji.write_text(
            json.dumps({
                "cong_banben": str(cong),
                "dao_banben": str(dao),
                "shijian": datetime.now(timezone.utc).isoformat(),
                "beifen_lujing": str(self.beifen_dangqian()),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
