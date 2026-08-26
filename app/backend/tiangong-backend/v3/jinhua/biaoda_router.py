"""Safe runtime expression router for learned v3 capabilities.

The router consumes the L0 projection ledger and exposes only low-risk learned
facts as prompt hints.  It does not register tools, run commands, or write code.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .yuanyu_yingshe import (
    JINHUA_BIHUAN_ROOT,
    L0_LEARNING_STATE,
    L0YuanyuYingshe,
    SAFE_EXPRESSION_TARGETS,
)


BIAODA_LEDGER_LUJING = JINHUA_BIHUAN_ROOT / "biaoda_router.json"
MAX_EXPRESSIONS = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").replace("\r", " ")
    return " ".join(text.split())[: max(0, int(limit or 0))]


def _digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _risk_rank(value: Any) -> int:
    text = str(value or "").upper().strip()
    if text.startswith("A"):
        try:
            return int(text[1:2])
        except Exception:
            return 3
    return 3


def _is_active_source(item: dict[str, Any]) -> bool:
    state = str(item.get("learning_state") or "")
    status = str(item.get("status") or "").lower()
    stage = str(item.get("promotion_stage") or "").lower()
    if state in {L0_LEARNING_STATE["active"], L0_LEARNING_STATE["committed"]}:
        return True
    return status in {"active", "learned", "jihuo"} or stage == "active"


def _expression_text(item: dict[str, Any]) -> str:
    title = _safe_text(item.get("title"), 120)
    summary = _safe_text(item.get("summary"), 420)
    if title and summary and title not in summary:
        return f"{title}: {summary}"
    return summary or title


def _expression_kind_label(target: str) -> str:
    return {
        "memory": "知识/记忆",
        "context": "上下文",
        "preference": "偏好",
        "procedural_hint": "流程提示",
    }.get(target, "学习提示")


# bug-fix: 学习表达注入前按当前消息做相关性过滤——不再每条消息都塞 6-8 条可能
# 毫不相干的“学习成果”（2026-08-26，凌霜修 logic 类）
_BIAODA_TONGYONG_CI = frozenset({
    "一个", "我们", "你们", "他们", "这个", "那个", "可以", "什么", "没有",
    "就是", "如果", "但是", "然后", "已经", "现在", "可能", "需要", "进行",
    "时候", "问题", "情况", "内容", "一些", "一下", "自己", "这样", "怎样",
})


def _biaoda_guanjianci(text: Any) -> set[str]:
    """提取关键词集合：英文/数字词（≥3 字符）+ 中文相邻二元词，去掉通用填充词。"""
    raw = str(text or "").lower()
    if not raw:
        return set()
    tokens = set(re.findall(r"[a-z0-9_]{3,}", raw))
    hanzi_runs = re.findall(r"[一-鿿]+", raw)
    for run in hanzi_runs:
        for a, b in zip(run, run[1:]):
            tokens.add(a + b)
    return tokens - _BIAODA_TONGYONG_CI


def _biaoda_xiangguan(xiaoxi: str, item: dict[str, Any]) -> bool:
    """当前消息与学习表达是否相关：偏好类天然相关，其余按关键词交集判定。"""
    if str(item.get("target") or "") == "preference":
        return True
    query = _biaoda_guanjianci(xiaoxi)
    if not query:
        return False
    return bool(query & _biaoda_guanjianci(item.get("text")))


class JinhuaBiaodaRouter:
    """Turns approved low-risk learning into prompt-visible expressions."""

    def __init__(
        self,
        *,
        mapper: L0YuanyuYingshe | None = None,
        ledger_path: Path | None = None,
    ) -> None:
        self.mapper = mapper or L0YuanyuYingshe()
        self.ledger_path = ledger_path or BIAODA_LEDGER_LUJING

    def shuaxin(
        self,
        shenti: Any | None = None,
        *,
        xiaoxi: str = "",
        reason: str = "runtime",
    ) -> dict[str, Any]:
        projection = self.mapper.write_projection(reason=reason)
        expressions, blocked = self._build_expressions(projection)
        payload = {
            "schema": "tiangong.v3.learning_expression_router.v1",
            "generated_at": _now_iso(),
            "reason": _safe_text(reason, 80),
            "current_message_digest": _digest(xiaoxi, 10) if xiaoxi else "",
            "body_state": {
                "shenti_id": _safe_text(getattr(shenti, "shenti_id", ""), 80) if shenti is not None else "",
                "growth": round(float(getattr(getattr(shenti, "shengming", None), "chengzhang_jindu", 0.0) or 0.0), 4) if shenti is not None else 0.0,
            },
            "source_projection_path": str(self.mapper.ledger_path),
            "safe_targets": sorted(SAFE_EXPRESSION_TARGETS),
            "summary": {
                "expression_count": len(expressions),
                "blocked_count": len(blocked),
                "active_safe_count": sum(1 for item in expressions if item.get("safe_to_inject")),
            },
            "expressions": expressions[:MAX_EXPRESSIONS],
            "blocked": blocked[:MAX_EXPRESSIONS],
        }
        _atomic_write_json(self.ledger_path, payload)
        return payload

    def goujian_tishi(
        self,
        shenti: Any | None = None,
        xiaoxi: str = "",
        *,
        limit: int = 3,
    ) -> str:
        report = self.shuaxin(shenti, xiaoxi=xiaoxi, reason="context_injection")
        expressions = [
            item for item in report.get("expressions", [])
            if isinstance(item, dict) and item.get("safe_to_inject")
        ]
        # bug-fix: 注入前按当前消息做相关性过滤，无命中不注入；
        # 默认条数 6→3，减少不相干“学习成果”挤占上下文（2026-08-26，凌霜修 logic 类）
        expressions = [item for item in expressions if _biaoda_xiangguan(xiaoxi, item)]
        if not expressions:
            return ""
        expressions.sort(key=lambda item: (item.get("priority", 9), item.get("target", ""), item.get("text", "")))
        # bug-fix: Kimi#20 删除对模型喊话的元指令（不能调用未注册工具等约束由系统层隔离实现），
        # prompt 只保留事实性参考内容（2026-08-26，凌霜）
        lines = ["以下为最近激活的自学习表达记录（参考信息）："]
        for item in expressions[: max(1, int(limit or 1))]:
            label = _expression_kind_label(str(item.get("target") or "context"))
            lines.append(f"- {label}: {_safe_text(item.get('text'), 220)}")
        return "\n".join(lines)

    def _build_expressions(self, projection: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        sources: list[dict[str, Any]] = []
        for key in ("abilities", "candidates"):
            rows = projection.get(key)
            if isinstance(rows, list):
                sources.extend(item for item in rows if isinstance(item, dict))

        expressions: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in sources:
            text = _expression_text(item)
            if not text:
                continue
            target = str(item.get("expression_target") or "context")
            risk = str(item.get("risk_level") or "A3")
            active = _is_active_source(item)
            safe = active and target in SAFE_EXPRESSION_TARGETS and _risk_rank(risk) <= 2
            base = {
                "source_type": item.get("source_type"),
                "source_id": item.get("source_id"),
                "learning_ref": item.get("learning_ref"),
                "lesson_ref": item.get("lesson_ref"),
                "proposal_ref": item.get("proposal_ref"),
                "target": target,
                "risk_level": risk,
                "learning_kind": item.get("learning_kind"),
                "learning_state": item.get("learning_state"),
                "safe_to_inject": safe,
                "text": text,
                "updated_at": item.get("updated_at") or "",
            }
            base["expression_id"] = f"expr_{_digest([base.get('source_id'), target, text], 14)}"
            if base["expression_id"] in seen:
                continue
            seen.add(base["expression_id"])
            if safe:
                base["priority"] = {"memory": 1, "preference": 2, "procedural_hint": 3, "context": 4}.get(target, 5)
                expressions.append(base)
            else:
                base["block_reason"] = self._block_reason(item, target, risk, active)
                blocked.append(base)
        return expressions, blocked

    @staticmethod
    def _block_reason(item: dict[str, Any], target: str, risk: str, active: bool) -> str:
        if not active:
            return "not_active"
        if _risk_rank(risk) > 2:
            return "risk_requires_l4_l5_gate"
        if target not in SAFE_EXPRESSION_TARGETS:
            return "target_not_supported_in_phase_1_3"
        if item.get("tool_callable") or item.get("registers_tool") or item.get("invokes_tool"):
            return "tool_expression_requires_gate"
        return "not_safe_to_inject"
