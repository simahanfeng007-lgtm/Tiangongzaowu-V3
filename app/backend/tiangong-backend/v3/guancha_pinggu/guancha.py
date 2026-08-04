"""
天工造物 v3：观察引擎 GuanchaYinqing
每次LLM回复后观察质量指标，生成ObservationRef并存入shenti.zuijin_xingdong
"""
from __future__ import annotations
import uuid, time as _time
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..shenti_zhuangtai import ShentiZhuangtai
from tiangong_kernel.l0_primitives.observation import ObservationKind, ObservationQuality
from tiangong_kernel.l0_primitives.identity import RefId
from tiangong_kernel.l0_primitives.time import Timestamp

class GuanchaZhibiao(str, Enum):
    """观察指标类型 —— 对应 L0 ObservationKind.METRIC"""
    CHANGDU = "changdu"
    XIANGSHI_SHIJIAN = "xiangying_shijian"
    CUOWU_CUNZAI = "cuowu_cunzai"
    CUOWU_XIANGXI = "cuowu_xiangxi"
    GONGJU_DIAOYONGSHU = "gongju_diaoyongshu"
    YONGHU_MANYIDU = "yonghu_manyidu"

@dataclass
class HuifuXinxi:
    """LLM回复元信息（上层传入）"""
    neirong: str = ""
    xiangying_shijian_miao: float = 0.0
    cuowu_xinxi: Optional[str] = None
    gongju_diaoyong_cishu: int = 0
    yonghu_fankui: Optional[str] = None  # "satisfied" / "unsatisfied" / None

@dataclass
class GuanchaJieguo:
    """单次观察结果 —— 对应 L0 ObservationRef + ObservationPayloadRef"""
    guancha_id: str = ""
    shijianchuo: Optional[datetime] = None
    zhibiao: dict = field(default_factory=dict)
    zhiliang: str = "raw"
    kind: str = "metric"
    beizhu: str = ""

class GuanchaYinqing:
    """观察引擎：测量长度/响应时间/错误/工具调用/满意度，追加到zuijin_xingdong"""

    def __init__(self):
        self._ci = 0
        self._kaishi: Optional[float] = None

    def guancha(self, shenti: ShentiZhuangtai, huifu: str,
                huifu_xinxi: Optional[HuifuXinxi] = None) -> ShentiZhuangtai:
        """观察LLM回复，原地修改shenti并返回"""
        self._ci += 1
        xianzai = datetime.now()
        xinxi = huifu_xinxi or HuifuXinxi(neirong=huifu)

        zhibiao = self._celiang(huifu, xinxi)
        zhiliang = self._panjue_zhiliang(huifu, xinxi)
        obs_ref = self._obs_ref(zhibiao, zhiliang, xianzai)

        jieguo = GuanchaJieguo(
            guancha_id=f"obs_{uuid.uuid4().hex[:12]}",
            shijianchuo=xianzai, zhibiao=zhibiao,
            zhiliang=zhiliang.value, kind=ObservationKind.METRIC.value,
            beizhu=self._beizhu(zhibiao, zhiliang)
        )
        shenti.zuijin_xingdong.append({
            "leixing": "guancha", "obs_ref": obs_ref,
            "jieguo": {
                "guancha_id": jieguo.guancha_id,
                "shijianchuo": jieguo.shijianchuo.isoformat(),
                "zhibiao": jieguo.zhibiao, "zhiliang": jieguo.zhiliang,
                "kind": jieguo.kind, "beizhu": jieguo.beizhu
            }
        })
        if len(shenti.zuijin_xingdong) > 100:
            shenti.zuijin_xingdong = shenti.zuijin_xingdong[-50:]
        return shenti

    def kaishi_jishi(self) -> None:
        self._kaishi = _time.monotonic()

    def jieshu_jishi(self) -> float:
        if self._kaishi is None: return 0.0
        t = _time.monotonic() - self._kaishi
        self._kaishi = None
        return t

    # ── 内部 ──

    def _celiang(self, huifu: str, xinxi: HuifuXinxi) -> dict:
        z = {}
        z["changdu"] = len(huifu) if huifu else 0
        z["xiangying_shijian"] = round(xinxi.xiangying_shijian_miao, 3)
        z["cuowu_cunzai"] = 1 if xinxi.cuowu_xinxi else 0
        z["cuowu_xiangxi"] = xinxi.cuowu_xinxi or ""
        z["gongju_diaoyongshu"] = xinxi.gongju_diaoyong_cishu
        fb = xinxi.yonghu_fankui
        z["yonghu_manyidu"] = 1.0 if fb == "satisfied" else (0.0 if fb == "unsatisfied" else None)
        return z

    def _panjue_zhiliang(self, huifu: str, xinxi: HuifuXinxi) -> ObservationQuality:
        """映射到 L0 ObservationQuality"""
        if xinxi.cuowu_xinxi:     return ObservationQuality.CONFLICTED
        if not huifu or not huifu.strip(): return ObservationQuality.PARTIAL
        if xinxi.yonghu_fankui is None:    return ObservationQuality.RAW
        if xinxi.yonghu_fankui == "unsatisfied": return ObservationQuality.LOW_CONFIDENCE
        return ObservationQuality.NORMALIZED

    def _obs_ref(self, zhibiao: dict, zhiliang: ObservationQuality, shijian: datetime) -> dict:
        """对齐 L0 ObservationRef / ObservationSource 概念"""
        return {
            "ref_id": f"obs:{uuid.uuid4().hex}",
            "kind": ObservationKind.METRIC.value,
            "quality": zhiliang.value,
            "timestamp_ms": int(shijian.timestamp() * 1000),
            "metrics": zhibiao,
            "source": {"source_kind": "llm_reply", "trust_boundary": "internal"},
            "schema_version": "0.1"
        }

    def _beizhu(self, zhibiao: dict, zhiliang: ObservationQuality) -> str:
        p = []
        if zhibiao.get("cuowu_cunzai"):
            x = zhibiao.get("cuowu_xiangxi", "")
            p.append(f"错误: {x}" if x else "检测到错误")
        if zhibiao.get("changdu", 0) == 0: p.append("空回复")
        if zhibiao.get("xiangying_shijian", 0) > 10: p.append(f"慢({zhibiao['xiangying_shijian']}s)")
        if zhibiao.get("yonghu_manyidu") == 0.0: p.append("用户不满意")
        if zhiliang == ObservationQuality.LOW_CONFIDENCE: p.append("置信度低")
        if zhiliang == ObservationQuality.CONFLICTED: p.append("冲突")
        return "; ".join(p) if p else "正常"
