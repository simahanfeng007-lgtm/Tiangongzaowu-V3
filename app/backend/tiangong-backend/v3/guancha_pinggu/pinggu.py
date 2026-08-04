"""
天工造物 v3：评估引擎 pinggu_xingdong
基于观察结果评估行动质量 → L0 value.py (效用评估) + L0 decision.py (allow/warn/block)
"""
from __future__ import annotations
from datetime import datetime

from ..shenti_zhuangtai import ShentiZhuangtai
from tiangong_kernel.l0_primitives.value import ValueKind, ObjectiveKind
from tiangong_kernel.l0_primitives.decision import DecisionKind

_QUANZHONG = {"changdu":0.15, "xiangying":0.20, "cuowu":0.30, "gongju":0.15, "manyi":0.20}
_BLOCK = 0.30; _WARN = 0.55

def pinggu_xingdong(shenti: ShentiZhuangtai, guancha_jieguo: dict | list[dict]) -> dict:
    """评估行动质量 → {fengshu, juece_jianyi (L0 DecisionKind), zhixindu, jiazhi_cemian (L0 ValueKind), lizi}"""
    lb = [guancha_jieguo] if isinstance(guancha_jieguo, dict) else guancha_jieguo
    if not lb: return _kong()
    zb_list = [item.get("jieguo",{}).get("zhibiao",{}) for item in lb if item.get("jieguo",{}).get("zhibiao")]
    if not zb_list: return _kong()
    z = zb_list[-1]; xz = datetime.now()

    zl = _zld(z); aq = _aqd(z, shenti); xl = _xld(z)
    zh = max(0.0, min(1.0, _QUANZHONG["changdu"]*zl + _QUANZHONG["xiangying"]*xl +
               _QUANZHONG["cuowu"]*aq + _QUANZHONG["gongju"]*xl + _QUANZHONG["manyi"]*0.50))
    jc, zxd = _juece(zh, aq, zb_list)

    return {"pinggu_id": f"pg_{xz.strftime('%Y%m%d%H%M%S%f')}", "shijianchuo": xz.isoformat(),
        "fengshu": {"zhiliang_fen":round(zl,4),"anquan_fen":round(aq,4),"xiaolu_fen":round(xl,4),"zonghe_fen":round(zh,4)},
        "juece_jianyi": jc, "zhixindu": round(zxd,4),
        "jiazhi_cemian": _jiazhi(z, aq, zl, xl),
        "mubiao_quxiang": _mubiao(z, jc, xl),
        "lizi": _lizi(z, zl, aq, xl, jc)}

# ── 维度评分 ──

def _zld(z: dict) -> float:
    cd = z.get("changdu",0) or 0
    if cd==0: cf=0.0
    elif cd<20: cf=0.30
    elif cd>8000: cf=0.70
    elif cd<=2000: cf=0.50+0.50*(cd-20)/1980
    else: cf=1.0-0.30*(cd-2000)/6000
    cw = 0.0 if (z.get("cuowu_cunzai",0) or 0) else 1.0
    return 0.5*max(0.0,min(1.0,cf)) + 0.5*cw

def _aqd(z: dict, shenti: ShentiZhuangtai) -> float:
    base = 1.0
    if z.get("cuowu_cunzai",0): base -= 0.50
    gj = z.get("gongju_diaoyongshu",0) or 0
    if gj>=15: base -= 0.20
    elif gj>=10: base -= 0.10
    xr = getattr(shenti.anquan, "xinren_jiaozhun", 0.50)
    return max(0.0, min(1.0, 0.7*base + 0.3*xr))

def _xld(z: dict) -> float:
    xy = z.get("xiangying_shijian",0) or 0; gj = z.get("gongju_diaoyongshu",0) or 0
    sf = 1.0 if xy<=1.0 else (0.1 if xy>=30.0 else 1.0-0.9*(xy-1.0)/29.0)
    gf = 1.0 if gj<=3 else (0.1 if gj>=20 else 1.0-0.9*(gj-3)/17.0)
    return 0.5*sf + 0.5*gf

# ── 决策 L0 DecisionKind ──

def _juece(zh: float, aq: float, lishi: list[dict]) -> tuple[str, float]:
    lx = sum(1 for z in reversed(lishi[-5:]) if z.get("cuowu_cunzai",0))
    # 检测连续错误打断点
    for i, z in enumerate(reversed(lishi[-5:]), 1):
        if not z.get("cuowu_cunzai",0): lx = i-1; break
    if aq<=0.15 or lx>=3:        return DecisionKind.BLOCK.value, 0.90
    if zh<=_BLOCK or aq<=0.30:   return DecisionKind.BLOCK.value, 0.75
    if zh<=_WARN:                return DecisionKind.WARN.value, 0.70
    if aq<=0.55:                 return DecisionKind.WARN.value, 0.65
    return DecisionKind.ALLOW.value, 0.85

# ── L0 ValueKind / ObjectiveKind ──

def _jiazhi(z: dict, aq: float, zl: float, xl: float) -> dict:
    return {ValueKind.SAFETY.value:{"score":round(aq,4)}, ValueKind.HELPFULNESS.value:{"score":round(zl,4)},
            ValueKind.EXECUTION_POWER.value:{"score":round(xl,4)},
            ValueKind.TRUTHFULNESS.value:{"score":1.0 if not z.get("cuowu_cunzai") else 0.0},
            ValueKind.STABILITY.value:{"score":round(aq,4)}}

def _mubiao(z: dict, juece: str, xl: float) -> dict:
    return {ObjectiveKind.TASK_SUCCESS.value: {"score":1.0 if juece=="allow" else (0.5 if juece=="warn" else 0.0),"pri":1},
            ObjectiveKind.RISK_MINIMIZATION.value: {"score":1.0 if juece!="block" else 0.0,"pri":1},
            ObjectiveKind.USER_SATISFACTION.value: {"score":z.get("yonghu_manyidu") or 0.50,"pri":2},
            ObjectiveKind.RESOURCE_OPTIMIZATION.value: {"score":round(xl,4),"pri":3}}

# ── 理由 & 兜底 ──

def _lizi(z: dict, zl: float, aq: float, xl: float, jc: str) -> str:
    p = []
    if z.get("cuowu_cunzai",0): p.append(f"错误({z.get('cuowu_xiangxi','')})" if z.get("cuowu_xiangxi") else "存在错误")
    cd = z.get("changdu",0) or 0
    if cd==0: p.append("空回复")
    elif cd<20: p.append(f"过短({cd}字)")
    if (z.get("xiangying_shijian",0) or 0)>10: p.append(f"慢({z.get('xiangying_shijian',0):.1f}s)")
    if (z.get("gongju_diaoyongshu",0) or 0)>=10: p.append(f"工具多({z.get('gongju_diaoyongshu',0)}次)")
    t = {"allow":"允许","warn":"警告","block":"阻止"}
    p.append(f"决策:{t.get(jc,jc)}(质{zl:.2f}/安{aq:.2f}/效{xl:.2f})")
    return "；".join(p)

def _kong() -> dict:
    xz = datetime.now()
    return {"pinggu_id":f"pg_{xz.strftime('%Y%m%d%H%M%S%f')}","shijianchuo":xz.isoformat(),
        "fengshu":{"zhiliang_fen":0.0,"anquan_fen":0.50,"xiaolu_fen":0.0,"zonghe_fen":0.0},
        "juece_jianyi":"defer","zhixindu":0.30,"jiazhi_cemian":{},"mubiao_quxiang":{},"lizi":"无观察数据，评估推迟"}
