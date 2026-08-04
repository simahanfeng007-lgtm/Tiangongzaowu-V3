"""Phase 4: generate review-only evolution candidates.

This module converts the L0 learning projection into candidate records for
prompt, workflow, skill, or tool evolution.  It never registers tools, writes
skill files, imports plugins, or executes candidate behavior.
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
    L0_EVOLUTION_KIND,
    L0_EVOLUTION_STATE,
    L0_LEARNING_STATE,
    L0YuanyuYingshe,
)


HOUXUAN_LEDGER_LUJING = JINHUA_BIHUAN_ROOT / "jinhua_houxuan.json"
MAX_CANDIDATES = 80

PROMPT_TERMS = ("memory", "context", "preference", "prompt", "knowledge")
WORKFLOW_TERMS = ("workflow", "route", "process", "step", "review", "pipeline", "flow")
SKILL_TERMS = ("skill", "capability", "procedural", "habit", "method", "pattern")
TOOL_TERMS = (
    "tool",
    "plugin",
    "mcp",
    "api",
    "shell",
    "cmd",
    "powershell",
    "terminal",
    "network",
    "browser",
    "install",
    "write file",
    "modify file",
)
TOOL_TERMS_CN = ("工具", "插件", "接口", "命令", "终端", "联网", "安装", "写文件", "改文件", "执行")
A5_TERMS = (
    "secret",
    "credential",
    "password",
    "token",
    "delete all",
    "rm -rf",
    "format",
    "system32",
    "registry",
    "shutdown",
)
A5_TERMS_CN = ("密钥", "凭证", "密码", "删除全部", "格式化", "注册表", "关机")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _risk_level(rank: int) -> str:
    return f"A{max(0, min(5, int(rank or 0)))}"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(term.lower() in low for term in terms)


def _source_text(item: dict[str, Any]) -> str:
    fields = (
        item.get("title"),
        item.get("summary"),
        item.get("learning_kind"),
        item.get("evolution_kind"),
        item.get("expression_target"),
        item.get("status"),
        item.get("promotion_stage"),
    )
    return _safe_text(" ".join(str(value or "") for value in fields), 1200)


def _is_active_or_reviewable(item: dict[str, Any]) -> bool:
    state = str(item.get("learning_state") or "")
    status = str(item.get("status") or "").lower()
    stage = str(item.get("promotion_stage") or "").lower()
    if state in {
        L0_LEARNING_STATE["approved"],
        L0_LEARNING_STATE["active"],
        L0_LEARNING_STATE["committed"],
    }:
        return True
    return status in {"candidate", "review_ready", "active", "learned"} or stage in {
        "candidate",
        "review_ready",
        "sandbox_passed",
        "active",
    }


def _candidate_type(item: dict[str, Any], text: str) -> str:
    target = str(item.get("expression_target") or "").lower()
    evolution = str(item.get("evolution_kind") or "").lower()
    learning = str(item.get("learning_kind") or "").lower()
    if target == "tool_candidate" or "tool_evolution" in evolution:
        return "tool_candidate"
    if target == "plugin_candidate" or "plugin_evolution" in evolution:
        return "tool_candidate"
    if target == "procedural_hint" or "skill_evolution" in evolution:
        return "skill_candidate"
    if _contains_any(text, TOOL_TERMS) or any(term in text for term in TOOL_TERMS_CN):
        return "tool_candidate"
    if _contains_any(text, SKILL_TERMS) or "procedural" in learning:
        return "skill_candidate"
    if _contains_any(text, WORKFLOW_TERMS) or any(term in text for term in ("流程", "步骤", "审查", "路由")):
        return "workflow_candidate"
    if target in {"memory", "context", "preference"} or _contains_any(text, PROMPT_TERMS):
        return "prompt_candidate"
    return "workflow_candidate"


def _candidate_risk(candidate_type: str, item: dict[str, Any], text: str) -> str:
    rank = _risk_rank(item.get("risk_level") or "A3")
    if candidate_type == "tool_candidate":
        rank = max(rank, 4)
    elif candidate_type == "skill_candidate":
        rank = max(rank, 3)
    elif candidate_type == "workflow_candidate":
        rank = max(rank, 2)
    elif candidate_type == "prompt_candidate":
        rank = max(rank, 2)
    if _contains_any(text, A5_TERMS) or any(term in text for term in A5_TERMS_CN):
        rank = max(rank, 5)
    return _risk_level(rank)


def _evolution_kind_for_type(candidate_type: str) -> str:
    if candidate_type == "tool_candidate":
        return L0_EVOLUTION_KIND["tool"]
    if candidate_type == "skill_candidate":
        return L0_EVOLUTION_KIND["skill"]
    if candidate_type == "prompt_candidate":
        return L0_EVOLUTION_KIND["memory"]
    return L0_EVOLUTION_KIND["policy"]


def _capability_namespace(candidate_type: str) -> str:
    return {
        "prompt_candidate": "prompt",
        "workflow_candidate": "workflow",
        "skill_candidate": "skill",
        "tool_candidate": "tool",
    }.get(candidate_type, "candidate")


class JinhuaHouxuanShengcheng:
    """Builds phase-4 review candidates from learning projection facts."""

    def __init__(
        self,
        *,
        mapper: L0YuanyuYingshe | None = None,
        ledger_path: Path | None = None,
    ) -> None:
        self.mapper = mapper or L0YuanyuYingshe()
        self.ledger_path = ledger_path or HOUXUAN_LEDGER_LUJING

    def shengcheng(
        self,
        shenti: Any | None = None,
        *,
        xiaoxi: str = "",
        reason: str = "runtime",
        limit: int = MAX_CANDIDATES,
    ) -> dict[str, Any]:
        projection = self.mapper.write_projection(reason=f"phase4:{reason}")
        previous = self._previous_candidates()
        all_candidates = self._build_candidates(projection, previous)
        selected_candidates = all_candidates[: max(1, int(limit or 1))]
        payload = {
            "schema": "tiangong.v3.evolution_candidates.v1",
            "phase": "phase4_candidate_generation",
            "generated_at": _now_iso(),
            "reason": _safe_text(reason, 80),
            "current_message_digest": _digest(xiaoxi, 10) if xiaoxi else "",
            "source_projection_path": str(self.mapper.ledger_path),
            "safety_contract": {
                "auto_register_allowed": False,
                "auto_execute_allowed": False,
                "writes_skill_files": False,
                "writes_tool_registry": False,
                "changes_system_policy": False,
            },
            "selection_policy": {
                "limit": max(1, int(limit or 1)),
                "total_before_limit": len(all_candidates),
                "omitted_after_limit": max(0, len(all_candidates) - len(selected_candidates)),
                "sort_order": ["risk_desc", "tool_and_skill_first", "evidence_desc", "candidate_id"],
            },
            "summary": self._summary(selected_candidates, total_before_limit=len(all_candidates)),
            "candidates": selected_candidates,
        }
        _atomic_write_json(self.ledger_path, payload)
        return payload

    def load_ledger(self) -> dict[str, Any]:
        data = _read_json(self.ledger_path, {})
        return data if isinstance(data, dict) else {}

    def _previous_candidates(self) -> dict[str, dict[str, Any]]:
        data = self.load_ledger()
        rows = data.get("candidates") if isinstance(data, dict) else []
        if not isinstance(rows, list):
            return {}
        return {
            str(item.get("candidate_id")): item
            for item in rows
            if isinstance(item, dict) and item.get("candidate_id")
        }

    def _build_candidates(
        self,
        projection: dict[str, Any],
        previous: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in ("abilities", "candidates", "experiences"):
            values = projection.get(key)
            if isinstance(values, list):
                rows.extend(item for item in values if isinstance(item, dict))

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in rows:
            text = _source_text(item)
            if not text or not _is_active_or_reviewable(item):
                continue
            ctype = _candidate_type(item, text)
            risk = _candidate_risk(ctype, item, text)
            source_id = _safe_text(item.get("source_id") or _digest(item, 12), 80)
            cid = f"{_capability_namespace(ctype)}_cand_{_digest([source_id, ctype, text], 14)}"
            if cid in seen:
                continue
            seen.add(cid)
            prev = previous.get(cid, {})
            candidate = self._candidate_record(cid, ctype, risk, item, text, prev)
            candidates.append(candidate)

        candidates.sort(key=self._candidate_sort_key)
        return candidates

    def _candidate_record(
        self,
        candidate_id: str,
        candidate_type: str,
        risk_level: str,
        item: dict[str, Any],
        text: str,
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now_iso()
        title = _safe_text(item.get("title") or item.get("source_id") or candidate_id, 120)
        summary = _safe_text(item.get("summary") or text, 500)
        risk_rank = _risk_rank(risk_level)
        evidence_refs = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
        evidence_count = len(evidence_refs) + (1 if item.get("lesson_ref") else 0) + (1 if item.get("proposal_ref") else 0)
        candidate = {
            "candidate_id": candidate_id,
            "candidate_type": candidate_type,
            "title": title,
            "summary": summary,
            "source": {
                "source_type": item.get("source_type"),
                "source_id": item.get("source_id"),
                "learning_ref": item.get("learning_ref"),
                "lesson_ref": item.get("lesson_ref"),
                "proposal_ref": item.get("proposal_ref"),
                "experience_ref": item.get("experience_ref"),
            },
            "l0_refs": {
                "improvement_candidate_ref": f"improvement_candidate:{candidate_id}",
                "evolution_ref": f"evolution:{candidate_id}",
                "skill_ref": f"skill:{candidate_id}" if candidate_type == "skill_candidate" else "",
                "tool_ref": f"tool:{candidate_id}" if candidate_type == "tool_candidate" else "",
                "capability_ref": f"capability:{candidate_id}",
                "evaluation_ref": f"evaluation:{candidate_id}",
                "commit_ref": f"commit:{candidate_id}",
                "rollback_ref": f"rollback:{candidate_id}",
            },
            "learning_kind": item.get("learning_kind"),
            "learning_state": item.get("learning_state"),
            "evolution_kind": _evolution_kind_for_type(candidate_type),
            "evolution_state": L0_EVOLUTION_STATE["proposed"],
            "risk_level": risk_level,
            "risk_rank": risk_rank,
            "status": "candidate_generated",
            "candidate_only": True,
            "requires_validation": True,
            "requires_human_approval": True,
            "auto_register_allowed": False,
            "auto_execute_allowed": False,
            "writes_skill_files": False,
            "writes_tool_registry": False,
            "changes_system_policy": False,
            "proposed_behavior": self._proposed_behavior(candidate_type, title, summary),
            "dry_run_contract": {
                "mode": "ledger_only",
                "allowed_effects": ["write_candidate_ledger", "write_validation_ledger", "write_evaluation_ledger"],
                "forbidden_effects": ["register_tool", "execute_command", "write_skill_file", "install_dependency", "network_access"],
            },
            "evidence_score": min(1.0, round((float(item.get("score") or 0) * 0.7) + min(evidence_count, 3) * 0.1, 4)),
            "created_at": _safe_text(previous.get("created_at") or now, 80),
            "updated_at": now,
            "manual": {
                "human_approval_state": _safe_text(previous.get("manual", {}).get("human_approval_state") if isinstance(previous.get("manual"), dict) else previous.get("human_approval_state"), 80),
                "human_approval_actor": _safe_text(previous.get("manual", {}).get("human_approval_actor") if isinstance(previous.get("manual"), dict) else previous.get("human_approval_actor"), 80),
                "human_approval_at": _safe_text(previous.get("manual", {}).get("human_approval_at") if isinstance(previous.get("manual"), dict) else previous.get("human_approval_at"), 80),
                "manual_notes": _safe_text(previous.get("manual", {}).get("manual_notes") if isinstance(previous.get("manual"), dict) else previous.get("manual_notes"), 300),
            },
        }
        return candidate

    @staticmethod
    def _proposed_behavior(candidate_type: str, title: str, summary: str) -> dict[str, Any]:
        behavior = {
            "prompt_candidate": "Use the learned fact as a low-risk prompt/context hint after validation.",
            "workflow_candidate": "Represent the learned pattern as a reviewable workflow suggestion.",
            "skill_candidate": "Draft a reusable skill proposal for manual review; do not create a skill file.",
            "tool_candidate": "Draft a tool capability proposal for manual review; do not register or execute it.",
        }.get(candidate_type, "Keep as a reviewable evolution candidate.")
        return {
            "intent": behavior,
            "input_summary": summary,
            "display_title": title,
            "runtime_binding": "none",
        }

    @staticmethod
    def _candidate_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        type_priority = {
            "tool_candidate": 0,
            "skill_candidate": 1,
            "workflow_candidate": 2,
            "prompt_candidate": 3,
        }.get(str(item.get("candidate_type") or ""), 9)
        return (
            -int(item.get("risk_rank") or 0),
            type_priority,
            -float(item.get("evidence_score") or 0.0),
            str(item.get("candidate_id") or ""),
        )

    @staticmethod
    def _summary(candidates: list[dict[str, Any]], *, total_before_limit: int | None = None) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        for item in candidates:
            by_type[str(item.get("candidate_type") or "unknown")] = by_type.get(str(item.get("candidate_type") or "unknown"), 0) + 1
            by_risk[str(item.get("risk_level") or "unknown")] = by_risk.get(str(item.get("risk_level") or "unknown"), 0) + 1
        return {
            "candidate_count": len(candidates),
            "total_before_limit": total_before_limit if total_before_limit is not None else len(candidates),
            "omitted_after_limit": max(0, (total_before_limit if total_before_limit is not None else len(candidates)) - len(candidates)),
            "by_type": by_type,
            "by_risk": by_risk,
            "auto_register_allowed": False,
            "auto_execute_allowed": False,
        }
