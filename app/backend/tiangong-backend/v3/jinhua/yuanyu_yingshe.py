"""Runtime projection from v3 learning artifacts to L0 source primitives.

This module is deliberately a projection layer.  It reads existing v3 artifacts
and writes a compact ledger that names the corresponding L0/L2 concepts, but it
does not execute learning, register tools, or mutate kernel primitives.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..peizhi import JINGYAN_LUJING, NENGLI_ZHUCE_LUJING

try:
    from ..shengming.zizhu_xuexi import DEFAULT_ROOT as ZIZHU_XUEXI_ROOT
except Exception:  # pragma: no cover - fallback for partial imports
    ZIZHU_XUEXI_ROOT = Path.home() / ".tiangong" / "v3" / "zizhu_xuexi"

try:
    from tiangong_kernel.l0_primitives.learning import (
        EvolutionKind,
        EvolutionState,
        LearningKind,
        LearningState,
    )
except Exception:  # pragma: no cover - keeps runtime alive if kernel is partial
    EvolutionKind = EvolutionState = LearningKind = LearningState = None


JINHUA_BIHUAN_ROOT = Path.home() / ".tiangong" / "v3" / "jinhua_bihuan"
YUANYU_YINGSHE_LUJING = JINHUA_BIHUAN_ROOT / "yuanyu_yingshe.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enum_value(enum_cls: Any, name: str, fallback: str) -> str:
    try:
        return str(getattr(enum_cls, name).value)
    except Exception:
        return fallback


L0_LEARNING_KIND = {
    "episodic": _enum_value(LearningKind, "EPISODIC_LEARNING", "episodic_learning"),
    "semantic": _enum_value(LearningKind, "SEMANTIC_LEARNING", "semantic_learning"),
    "procedural": _enum_value(LearningKind, "PROCEDURAL_LEARNING", "procedural_learning"),
    "failure": _enum_value(LearningKind, "FAILURE_LEARNING", "failure_learning"),
    "feedback": _enum_value(LearningKind, "FEEDBACK_LEARNING", "feedback_learning"),
    "preference": _enum_value(LearningKind, "PREFERENCE_LEARNING", "preference_learning"),
    "policy": _enum_value(LearningKind, "POLICY_LEARNING", "policy_learning"),
}

L0_LEARNING_STATE = {
    "proposed": _enum_value(LearningState, "PROPOSED", "proposed"),
    "assessing": _enum_value(LearningState, "ASSESSING", "assessing"),
    "approved": _enum_value(LearningState, "APPROVED", "approved"),
    "active": _enum_value(LearningState, "ACTIVE", "active"),
    "committed": _enum_value(LearningState, "COMMITTED", "committed"),
    "rejected": _enum_value(LearningState, "REJECTED", "rejected"),
    "rolled_back": _enum_value(LearningState, "ROLLED_BACK", "rolled_back"),
    "quarantined": _enum_value(LearningState, "QUARANTINED", "quarantined"),
    "archived": _enum_value(LearningState, "ARCHIVED", "archived"),
}

L0_EVOLUTION_KIND = {
    "memory": _enum_value(EvolutionKind, "MEMORY_EVOLUTION", "memory_evolution"),
    "skill": _enum_value(EvolutionKind, "SKILL_EVOLUTION", "skill_evolution"),
    "tool": _enum_value(EvolutionKind, "TOOL_EVOLUTION", "tool_evolution"),
    "plugin": _enum_value(EvolutionKind, "PLUGIN_EVOLUTION", "plugin_evolution"),
    "policy": _enum_value(EvolutionKind, "POLICY_EVOLUTION", "policy_evolution"),
    "contract": _enum_value(EvolutionKind, "CONTRACT_EVOLUTION", "contract_evolution"),
    "schema": _enum_value(EvolutionKind, "SCHEMA_EVOLUTION", "schema_evolution"),
    "code": _enum_value(EvolutionKind, "CODE_EVOLUTION", "code_evolution"),
    "architecture": _enum_value(EvolutionKind, "ARCHITECTURE_EVOLUTION", "architecture_evolution"),
}

L0_EVOLUTION_STATE = {
    "proposed": _enum_value(EvolutionState, "PROPOSED", "proposed"),
    "assessing": _enum_value(EvolutionState, "ASSESSING", "assessing"),
    "approved": _enum_value(EvolutionState, "APPROVED", "approved"),
    "active": _enum_value(EvolutionState, "ACTIVE", "active"),
    "committed": _enum_value(EvolutionState, "COMMITTED", "committed"),
    "rejected": _enum_value(EvolutionState, "REJECTED", "rejected"),
    "rolled_back": _enum_value(EvolutionState, "ROLLED_BACK", "rolled_back"),
    "quarantined": _enum_value(EvolutionState, "QUARANTINED", "quarantined"),
    "archived": _enum_value(EvolutionState, "ARCHIVED", "archived"),
}

SAFE_EXPRESSION_TARGETS = {"memory", "context", "preference", "procedural_hint"}


def _safe_text(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(0, int(limit or 0))]


def _digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback


def _read_jsonl(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return []
    for line in lines[-max(1, int(limit or 1)):]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_cards() -> list[dict[str, Any]]:
    cards_dir = Path(ZIZHU_XUEXI_ROOT) / "cards"
    if not cards_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(cards_dir.glob("*.json")):
        item = _read_json(path, {})
        if isinstance(item, dict) and item:
            item.setdefault("_source_path", str(path))
            rows.append(item)
    return rows


def _load_abilities() -> list[dict[str, Any]]:
    raw = _read_json(NENGLI_ZHUCE_LUJING, {})
    if isinstance(raw, dict):
        rows = raw.get("nengli_liebiao") or raw.get("nengli_list") or raw.get("abilities") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    return [item for item in rows if isinstance(item, dict)]


def _risk_rank(value: Any) -> int:
    text = str(value or "").upper().strip()
    if text.startswith("A"):
        try:
            return int(text[1:2])
        except Exception:
            return 3
    return 3


def _risk_level(item: dict[str, Any]) -> str:
    explicit = item.get("risk_level") or item.get("riskLevel") or item.get("fengxian_dengji")
    if explicit:
        return str(explicit).upper()
    kind = str(item.get("kind") or item.get("leixing") or "").lower()
    topic = str(item.get("topic") or "").lower()
    if "tool" in kind or "tool" in topic or "gongju" in kind:
        return "A4"
    if "code" in kind or "architecture" in topic or "xitong" in kind:
        return "A3"
    return "A2"


def _learning_state(item: dict[str, Any]) -> str:
    status = str(item.get("promotion_stage") or item.get("status") or item.get("zhuangtai") or "").strip()
    normalized = {
        "candidate": "proposed",
        "daijihuo": "proposed",
        "review_ready": "approved",
        "accepted": "approved",
        "draft": "approved",
        "model_review": "assessing",
        "sandbox_passed": "approved",
        "active": "active",
        "learned": "active",
        "jihuo": "active",
        "yiwancheng": "committed",
        "duplicate_removed": "archived",
        "discarded": "archived",
        "disabled": "archived",
        "tingyong": "archived",
        "no_value": "rejected",
        "reject": "rejected",
        "failed": "quarantined",
        "baofei": "archived",
    }.get(status, "proposed")
    return L0_LEARNING_STATE[normalized]


def _learning_kind(item: dict[str, Any]) -> str:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("leixing", "kind", "topic", "title", "mingcheng", "summary", "miaoshu")
    ).lower()
    if any(token in text for token in ("failure", "shibai", "错误", "失败", "cuowu")):
        return L0_LEARNING_KIND["failure"]
    if any(token in text for token in ("preference", "偏好", "习惯", "style", "风格")):
        return L0_LEARNING_KIND["preference"]
    if any(token in text for token in ("policy", "策略", "governance", "安全")):
        return L0_LEARNING_KIND["policy"]
    if any(token in text for token in ("xingwei", "procedural", "流程", "步骤", "tuili", "duihua")):
        return L0_LEARNING_KIND["procedural"]
    if any(token in text for token in ("zhishi", "knowledge", "知识", "资料", "semantic")):
        return L0_LEARNING_KIND["semantic"]
    if any(token in text for token in ("context_memory", "conversation", "memory")):
        return L0_LEARNING_KIND["episodic"]
    return L0_LEARNING_KIND["feedback"]


def _expression_target(item: dict[str, Any]) -> str:
    explicit = str(item.get("expression_target") or item.get("target") or "").strip()
    if explicit:
        return explicit
    text = " ".join(
        str(item.get(key) or "")
        for key in ("leixing", "kind", "topic", "title", "mingcheng", "summary", "miaoshu")
    ).lower()
    if _risk_rank(_risk_level(item)) > 2:
        if "tool" in text or "gongju" in text:
            return "tool_candidate"
        if "code" in text or "architecture" in text or "xitong" in text:
            return "review_only"
        return "review_only"
    if any(token in text for token in ("preference", "偏好", "习惯", "style", "风格")):
        return "preference"
    if any(token in text for token in ("zhishi", "knowledge", "知识", "资料", "semantic")):
        return "memory"
    if any(token in text for token in ("xingwei", "procedural", "流程", "步骤", "tuili")):
        return "procedural_hint"
    return "context"


def _evolution_kind(target: str) -> str:
    if target in {"memory", "context", "preference"}:
        return L0_EVOLUTION_KIND["memory"]
    if target == "procedural_hint":
        return L0_EVOLUTION_KIND["skill"]
    if target == "tool_candidate":
        return L0_EVOLUTION_KIND["tool"]
    if target == "plugin_candidate":
        return L0_EVOLUTION_KIND["plugin"]
    if target == "code_candidate":
        return L0_EVOLUTION_KIND["code"]
    if target == "architecture_candidate":
        return L0_EVOLUTION_KIND["architecture"]
    return L0_EVOLUTION_KIND["policy"]


def _summary_for_item(item: dict[str, Any], limit: int = 360) -> str:
    return _safe_text(
        item.get("summary")
        or item.get("miaoshu")
        or item.get("description")
        or item.get("neirong")
        or item.get("assistant_text")
        or item.get("user_text")
        or "",
        limit,
    )


def _base_projection(item: dict[str, Any], source_type: str, source_id: str) -> dict[str, Any]:
    target = _expression_target(item)
    risk = _risk_level(item)
    state = _learning_state(item)
    return {
        "source_type": source_type,
        "source_id": source_id,
        "learning_ref": f"learning:{source_id}",
        "experience_ref": f"experience:{source_id}" if source_type == "experience" else "",
        "lesson_ref": f"lesson:{source_id}" if source_type in {"experience", "card", "ability"} else "",
        "proposal_ref": f"proposal:{source_id}" if source_type in {"card", "ability"} else "",
        "learning_kind": _learning_kind(item),
        "learning_state": state,
        "evolution_kind": _evolution_kind(target),
        "evolution_state": L0_EVOLUTION_STATE["active"] if state == L0_LEARNING_STATE["active"] else L0_EVOLUTION_STATE["proposed"],
        "risk_level": risk,
        "risk_rank": _risk_rank(risk),
        "expression_target": target,
        "safe_expression": target in SAFE_EXPRESSION_TARGETS and _risk_rank(risk) <= 2,
        "title": _safe_text(item.get("title") or item.get("mingcheng") or item.get("topic") or source_id, 120),
        "summary": _summary_for_item(item),
        "evidence_refs": [str(ref) for ref in item.get("evidence_refs", []) if ref] if isinstance(item.get("evidence_refs"), list) else [],
        "updated_at": _safe_text(item.get("updated_at") or item.get("zuihou_gengxin") or item.get("created_at") or item.get("timestamp") or "", 80),
    }


class L0YuanyuYingshe:
    """Builds a v3-native L0 projection ledger."""

    def __init__(self, ledger_path: Path | None = None) -> None:
        self.ledger_path = ledger_path or YUANYU_YINGSHE_LUJING

    def build_projection(self, *, reason: str = "runtime", limit: int = 80) -> dict[str, Any]:
        events = self._project_events(limit=limit)
        experiences = self._project_experiences(limit=limit)
        cards = self._project_cards()
        abilities = self._project_abilities()
        return {
            "schema": "tiangong.v3.l0_source_projection.v1",
            "generated_at": _now_iso(),
            "reason": _safe_text(reason, 80),
            "paths": {
                "events": str(Path(ZIZHU_XUEXI_ROOT) / "events.jsonl"),
                "cards": str(Path(ZIZHU_XUEXI_ROOT) / "cards"),
                "experiences": str(JINGYAN_LUJING),
                "abilities": str(NENGLI_ZHUCE_LUJING),
                "ledger": str(self.ledger_path),
            },
            "counts": {
                "observations": len(events),
                "experiences": len(experiences),
                "candidates": len(cards),
                "abilities": len(abilities),
            },
            "observations": events,
            "experiences": experiences,
            "candidates": cards,
            "abilities": abilities,
        }

    def write_projection(self, *, reason: str = "runtime", limit: int = 80) -> dict[str, Any]:
        projection = self.build_projection(reason=reason, limit=limit)
        _atomic_write_json(self.ledger_path, projection)
        return projection

    def load_projection(self) -> dict[str, Any]:
        data = _read_json(self.ledger_path, {})
        return data if isinstance(data, dict) else {}

    def _project_events(self, *, limit: int) -> list[dict[str, Any]]:
        rows = _read_jsonl(Path(ZIZHU_XUEXI_ROOT) / "events.jsonl", limit=limit)
        result: list[dict[str, Any]] = []
        for item in rows:
            source_id = str(item.get("event_id") or f"event_{_digest(item, 12)}")
            result.append({
                "source_type": "event",
                "source_id": source_id,
                "observation_ref": f"observation:{source_id}",
                "signal_refs": [f"signal:{signal}" for signal in item.get("signals", []) if signal],
                "source": _safe_text(item.get("source"), 80),
                "summary": _safe_text(item.get("user_text") or item.get("assistant_text") or item.get("error"), 360),
                "created_at": _safe_text(item.get("created_at"), 80),
            })
        return result

    def _project_experiences(self, *, limit: int) -> list[dict[str, Any]]:
        rows = _read_jsonl(JINGYAN_LUJING, limit=limit)
        result: list[dict[str, Any]] = []
        for item in rows:
            source_id = str(item.get("id") or item.get("timestamp") or f"jingyan_{_digest(item, 12)}")
            projected = _base_projection(item, "experience", source_id)
            projected.update({
                "status": _safe_text(item.get("zhuangtai") or "", 80),
                "priority": item.get("youxianji", item.get("priority", 0)),
                "confidence": item.get("confidence", item.get("zhixindu", 0)),
            })
            result.append(projected)
        return result

    def _project_cards(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in _load_cards():
            source_id = str(item.get("card_id") or item.get("id") or f"card_{_digest(item, 12)}")
            projected = _base_projection(item, "card", source_id)
            projected.update({
                "status": _safe_text(item.get("status") or "", 80),
                "promotion_stage": _safe_text(item.get("promotion_stage") or "", 80),
                "score": item.get("score", 0),
                "ability_id": _safe_text(item.get("ability_id") or "", 120),
                "activation_allowed": bool(item.get("activation_allowed")),
                "candidate_only": bool(item.get("candidate_only")),
            })
            result.append(projected)
        result.sort(key=lambda item: (str(item.get("learning_state") != L0_LEARNING_STATE["active"]), -float(item.get("score") or 0)))
        return result

    def _project_abilities(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in _load_abilities():
            source_id = str(item.get("id") or f"ability_{_digest(item, 12)}")
            projected = _base_projection(item, "ability", source_id)
            projected.update({
                "status": _safe_text(item.get("status") or item.get("zhuangtai") or "", 80),
                "promotion_stage": _safe_text(item.get("promotion_stage") or "", 80),
                "source": _safe_text(item.get("laiyuan") or "", 80),
                "card_id": _safe_text(item.get("laiyuan_card_id") or "", 120),
                "activation_allowed": item.get("activation_allowed") is True,
                "candidate_only": item.get("candidate_only") is True,
                "tool_callable": item.get("tool_callable") is True,
                "registers_tool": item.get("registers_tool") is True,
                "invokes_tool": item.get("invokes_tool") is True,
            })
            result.append(projected)
        result.sort(key=lambda item: (str(item.get("learning_state") != L0_LEARNING_STATE["active"]), item.get("title", "")))
        return result
