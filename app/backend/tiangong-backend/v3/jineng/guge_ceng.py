"""
天工造物 v3：起源 — 骨骼层

当前主执行链只向模型暴露 omni_body。旧的直接工具和动态 skill 工具释放口
已经退役，所有工作动作都必须通过 omni_body action 执行。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


OMNI_BODY_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "要执行的动作名。URL 正文用 web.read，关键词检索用 web.search；文件、代码、质检、交付和文档动作直接传对应 action。",
        },
        "target": {
            "type": "string",
            "description": "主操作对象、URL 或相对工作区路径。路径权限由系统裁决，模型不能声明权限。",
        },
        "args": {
            "type": "object",
            "description": "动作专用业务参数，例如 content、destination、output、sheets、text、command。不得包含确认、权限或内部运行字段。",
            "additionalProperties": True,
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}



@dataclass
class GongjuYingshe:
    """工具映射条目。"""

    mingcheng: str
    miaoshu: str
    canshu_schema: dict
    shipeiqi_han_shu: Callable
    fengxian_dengji: str = "A1"
    tool_kind: str = "executable"
    effect: str = "unknown"
    plan_only: bool = False


@dataclass
class CanShuYanZhengJieGuo:
    tongguo: bool
    cuowu: str = ""


class GugeCeng:
    """骨骼层：模型可见工具注册表。"""

    def __init__(self):
        self._yingshe_biao: dict[str, GongjuYingshe] = {}
        self._chushihua_yingshe()

    def _chushihua_yingshe(self):
        """初始化工具映射表。"""
        self.zhuce(
            GongjuYingshe(
                "omni_body",
                "统一身体工具入口。直接传 action 执行生产动作。URL 正文用 web.read，关键词检索用 web.search；文件、代码、质检、交付和文档动作直接执行。传 target/args 即可；权限与 workspace 由系统绑定。",
                OMNI_BODY_PARAMETERS,
                self._shipeiqi_duben,
                "A4",
                "executable",
                "execute",
            )
        )

    @staticmethod
    def _infer_effect(name: str) -> str:
        return "execute" if str(name or "").strip() == "omni_body" else "retired"

    def zhuce(self, yingshe: GongjuYingshe):
        """注册一个工具。当前只接受 omni_body。"""
        if yingshe.mingcheng != "omni_body":
            return
        if not getattr(yingshe, "effect", "") or yingshe.effect == "unknown":
            yingshe.effect = self._infer_effect(yingshe.mingcheng)
        self._yingshe_biao[yingshe.mingcheng] = yingshe

    def duiying(self, mingcheng: str) -> GongjuYingshe | None:
        """名字到适配器映射。"""
        clean = str(mingcheng or "").strip()
        if clean != "omni_body":
            return None
        return self._yingshe_biao.get(clean)

    def yanzheng_canshu(self, yingshe: GongjuYingshe, canshu: dict) -> CanShuYanZhengJieGuo:
        """参数校验。"""
        schema = yingshe.canshu_schema if isinstance(yingshe.canshu_schema, dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for key in required:
            if key not in canshu:
                return CanShuYanZhengJieGuo(tongguo=False, cuowu=f"缺少必填参数: {key}")
        if not properties:
            return CanShuYanZhengJieGuo(tongguo=True, cuowu="")
        return CanShuYanZhengJieGuo(tongguo=True, cuowu="")

    def suoyou_gongju(self, include_plan_only: bool = False) -> list[dict]:
        """返回模型可见工具描述。"""
        return [
            {
                "name": y.mingcheng,
                "description": y.miaoshu,
                "parameters": y.canshu_schema,
                "risk": y.fengxian_dengji,
                "tool_kind": y.tool_kind,
                "effect": y.effect,
                "plan_only": y.plan_only,
            }
            for y in self._yingshe_biao.values()
            if include_plan_only or not y.plan_only
        ]

    @staticmethod
    def _shipeiqi_duben(**kwargs):
        """占位适配器，实际由肌肉层执行 omni_body。"""
        return {"zhuangtai": "weishixian", "canshu": kwargs}


GUGE = GugeCeng()
