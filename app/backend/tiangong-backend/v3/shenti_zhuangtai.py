"""
天工造物 v3：起源 — 身体状态定义
ShentiZhuangtai: 所有引擎共享的统一身体状态
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# L0 内核类型导入
from tiangong_kernel.l0_primitives.identity import CoreId, TypedRef
from tiangong_kernel.l0_primitives.health import HealthState, VitalityKind
from tiangong_kernel.l0_primitives.autonomy import AutonomyLevel
from tiangong_kernel.l0_primitives.lifecycle import LifecyclePhase
from tiangong_kernel.l0_primitives.time import Timestamp, Duration


@dataclass
class QingganZhuangtai:
    """七情六欲 + 稳态负荷"""
    joy: float = 0.50
    anger: float = 0.05
    worry: float = 0.10
    thoughtfulness: float = 0.40
    sadness: float = 0.05
    fear: float = 0.05
    surprise: float = 0.10
    
    survival: float = 0.30
    curiosity: float = 0.50
    achievement: float = 0.40
    connection: float = 0.50
    order: float = 0.35
    rest: float = 0.20
    
    allostatic_load: float = 0.30
    dominant_emotion: str = "thoughtfulness"
    dominant_desire: str = "curiosity"


@dataclass
class QudongZhuangtai:
    """15种驱动的压力和就绪度"""
    qudong_yali: dict = field(
        default_factory=lambda: {
            "survival": 0.30, "curiosity": 0.50, "achievement": 0.40,
            "connection": 0.50, "order": 0.35, "rest": 0.20,
            "exploration": 0.45, "creation": 0.35, "mastery": 0.40,
            "autonomy_drive": 0.55, "belonging": 0.45, "purpose": 0.50,
            "play": 0.30, "safety": 0.25, "transcendence": 0.20
        }
    )
    qudong_jiuxu: dict = field(
        default_factory=lambda: {k: 0.60 for k in [
            "survival", "curiosity", "achievement", "connection", "order", "rest",
            "exploration", "creation", "mastery", "autonomy_drive", "belonging",
            "purpose", "play", "safety", "transcendence"
        ]}
    )


@dataclass
class JiyiTongji:
    """记忆池统计"""
    zongshu: int = 0
    geceng_fenbu: dict = field(default_factory=lambda: {
        "l1": 0, "l2": 0, "l3": 0, "l4": 0, "l5": 0
    })
    zuijin_jiansuo: str = ""
    zuijin_zongshu: int = 0


@dataclass
class JinhuaZhuangtai:
    """进化状态"""
    dangqian_jieduan: str = "guancha"  # 观察→评估→改进→验证
    gaijin_houxuan: list = field(default_factory=list)
    huoyue_shiyan: Optional[str] = None
    gaijin_lishi: list = field(default_factory=list)


@dataclass
class HuanjingZhuangtai:
    """环境感知"""
    huanjing_leixing: str = "desktop_gui"  # desktop_gui / server / cli
    API_diaoyong_shu: int = 0
    API_diaoyong_yue: int = 1000
    cipan_kongxian: int = 0
    neicun_shiyong: int = 0
    pingzheng_zhuangtai: str = "weipeizhi"


@dataclass
class AnquanZhuangtai:
    """安全状态"""
    zizhu_jibie: str = "fuzhu"  # chenshui/fuzhu/banzizhu/zizhu/wanquan_zizhu
    fengxian_pouxi: dict = field(default_factory=dict)
    xinren_jiaozhun: float = 0.50
    lianxu_zizhu_xingdong: int = 0
    zizhu_zuida_lianxu: int = 5


@dataclass
class ShengmingZhuangtai:
    """生命管理"""
    zhouqi_jieduan: str = "fuhuo"  # chenshui/fuhuo/fuzhu/banzizhu/zizhu
    chengzhang_jindu: float = 0.0
    zong_huanxing_cishu: int = 0
    zhuodong_kaishi: Optional[datetime] = None
    zuihou_yonghu_xiaoxi: Optional[datetime] = None


@dataclass
class ShentiZhuangtai:
    """统一身体状态 —— 所有引擎共享"""

    # 基础标识
    shenti_id: str = ""
    
    # 情感
    qinggan: QingganZhuangtai = field(default_factory=QingganZhuangtai)
    
    # 驱动
    qudong: QudongZhuangtai = field(default_factory=QudongZhuangtai)
    
    # 健康
    jiankang_zhuangtai: str = "zhengchang"
    shengmingli: float = 1.0
    sunshang_leiji: float = 0.0
    
    # 记忆统计
    jiyi_tongji: JiyiTongji = field(default_factory=JiyiTongji)
    
    # 时间
    zuihou_huanxing: Optional[datetime] = None
    chenmo_shichang_miao: float = 0.0
    zong_huanxing_cishu: int = 0
    
    # 进化
    jinhua: JinhuaZhuangtai = field(default_factory=JinhuaZhuangtai)
    
    # 环境
    huanjing: HuanjingZhuangtai = field(default_factory=HuanjingZhuangtai)
    
    # 安全
    anquan: AnquanZhuangtai = field(default_factory=AnquanZhuangtai)
    
    # 生命
    shengming: ShengmingZhuangtai = field(default_factory=ShengmingZhuangtai)
    
    # 最近行动
    zuijin_xingdong: list = field(default_factory=list)
    
    # 追踪
    dangqian_zhuizong_id: str = ""

    def __post_init__(self):
        if not self.shenti_id:
            import uuid
            self.shenti_id = f"shenti_{uuid.uuid4().hex[:12]}"
