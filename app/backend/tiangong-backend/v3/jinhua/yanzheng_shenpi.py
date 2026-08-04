"""Phase 5: validate and approval-gate evolution candidates.

The validator is static and ledger-only.  It checks candidate records produced
by phase 4, assigns risk/approval states, and records dry-run results.  It does
not activate candidates, register tools, or execute proposed behavior.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .houxuan_shengcheng import HOUXUAN_LEDGER_LUJING, JinhuaHouxuanShengcheng
from .yuanyu_yingshe import JINHUA_BIHUAN_ROOT, L0_EVOLUTION_STATE


YANZHENG_LEDGER_LUJING = JINHUA_BIHUAN_ROOT / "jinhua_yanzheng_shenpi.json"

VALID_CANDIDATE_TYPES = {
    "prompt_candidate",
    "workflow_candidate",
    "skill_candidate",
    "tool_candidate",
}

A4_TERMS = (
    "execute",
    "command",
    "shell",
    "cmd",
    "powershell",
    "install",
    "network",
    "browser",
    "write file",
    "modify file",
    "register tool",
    "plugin",
    "mcp",
    "api key",
)
A4_TERMS_CN = ("执行", "命令", "终端", "安装", "联网", "写文件", "改文件", "注册工具", "插件")
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


def _candidate_text(candidate: dict[str, Any]) -> str:
    behavior = candidate.get("proposed_behavior") if isinstance(candidate.get("proposed_behavior"), dict) else {}
    chunks = [
        candidate.get("candidate_id"),
        candidate.get("candidate_type"),
        candidate.get("title"),
        candidate.get("summary"),
        behavior.get("intent") if isinstance(behavior, dict) else "",
        behavior.get("input_summary") if isinstance(behavior, dict) else "",
    ]
    return _safe_text(" ".join(str(chunk or "") for chunk in chunks), 1800)


class JinhuaYanzhengShenpi:
    """Static validator and approval ledger for generated candidates."""

    def __init__(
        self,
        *,
        candidate_generator: JinhuaHouxuanShengcheng | None = None,
        candidate_ledger_path: Path | None = None,
        ledger_path: Path | None = None,
    ) -> None:
        self.candidate_generator = candidate_generator
        self.candidate_ledger_path = candidate_ledger_path or HOUXUAN_LEDGER_LUJING
        self.ledger_path = ledger_path or YANZHENG_LEDGER_LUJING

    def yanzheng(
        self,
        candidate_report: dict[str, Any] | None = None,
        *,
        reason: str = "runtime",
    ) -> dict[str, Any]:
        if candidate_report is None:
            candidate_report = self._load_or_generate_candidates(reason=reason)
        candidates = candidate_report.get("candidates") if isinstance(candidate_report, dict) else []
        if not isinstance(candidates, list):
            candidates = []

        validation_results = [
            self._validate_candidate(item)
            for item in candidates
            if isinstance(item, dict)
        ]
        payload = {
            "schema": "tiangong.v3.evolution_validation_approval.v1",
            "phase": "phase5_validation_approval",
            "generated_at": _now_iso(),
            "reason": _safe_text(reason, 80),
            "source_candidate_path": str(self.candidate_ledger_path),
            "source_candidate_digest": _digest(candidate_report, 14) if candidate_report else "",
            "safety_contract": {
                "static_validation_only": True,
                "dry_run_only": True,
                "auto_register_allowed": False,
                "auto_execute_allowed": False,
                "human_approval_required_for_activation": True,
            },
            "summary": self._summary(validation_results),
            "validation_results": validation_results,
        }
        _atomic_write_json(self.ledger_path, payload)
        return payload

    def load_ledger(self) -> dict[str, Any]:
        data = _read_json(self.ledger_path, {})
        return data if isinstance(data, dict) else {}

    def _load_or_generate_candidates(self, *, reason: str) -> dict[str, Any]:
        data = _read_json(self.candidate_ledger_path, {})
        if isinstance(data, dict) and data.get("candidates") is not None:
            return data
        if self.candidate_generator is not None:
            return self.candidate_generator.shengcheng(reason=f"phase5_missing_candidates:{reason}")
        return {"candidates": []}

    def _validate_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        text = _candidate_text(candidate)
        checks = self._checks(candidate, text)
        risk = self._effective_risk(candidate, text)
        rank = _risk_rank(risk)
        schema_ok = all(check.get("passed") for check in checks if check.get("category") in {"schema", "boundary"})
        blocked = rank >= 5 or any(check.get("severity") == "blocker" and not check.get("passed") for check in checks)
        if blocked:
            approval_state = "blocked"
            dry_run_status = "blocked"
            evolution_state = L0_EVOLUTION_STATE["quarantined"]
        elif rank >= 4:
            approval_state = "needs_human_approval"
            dry_run_status = "needs_review"
            evolution_state = L0_EVOLUTION_STATE["assessing"]
        elif schema_ok:
            approval_state = "review_ready"
            dry_run_status = "pass"
            evolution_state = L0_EVOLUTION_STATE["assessing"]
        else:
            approval_state = "needs_revision"
            dry_run_status = "failed"
            evolution_state = L0_EVOLUTION_STATE["rejected"]

        manual = candidate.get("manual") if isinstance(candidate.get("manual"), dict) else {}
        human_state = _safe_text(manual.get("human_approval_state"), 80)
        human_approved = human_state.lower() in {"approved", "approve", "yes", "passed"}
        return {
            "candidate_id": candidate.get("candidate_id"),
            "candidate_type": candidate.get("candidate_type"),
            "title": _safe_text(candidate.get("title"), 120),
            "risk_level": risk,
            "risk_rank": rank,
            "validation_id": f"validation_{_digest([candidate.get('candidate_id'), risk, checks], 14)}",
            "evaluation_ref": candidate.get("l0_refs", {}).get("evaluation_ref") if isinstance(candidate.get("l0_refs"), dict) else "",
            "evolution_state": evolution_state,
            "checks": checks,
            "dry_run": {
                "status": dry_run_status,
                "mode": "static_ledger_only",
                "executed": False,
                "registered_tool": False,
                "wrote_skill_file": False,
                "network_access": False,
            },
            "approval": {
                "approval_state": approval_state,
                "human_approval_required": True,
                "human_approved": human_approved,
                "human_approval_state": human_state,
                "can_promote_to_phase6": approval_state in {"review_ready", "needs_human_approval"} and not blocked,
                "can_activate_runtime": False,
                "can_register_tool": False,
                "can_execute": False,
            },
            "source_candidate": {
                "candidate_id": candidate.get("candidate_id"),
                "source": candidate.get("source"),
                "l0_refs": candidate.get("l0_refs"),
            },
            "validated_at": _now_iso(),
        }

    def _checks(self, candidate: dict[str, Any], text: str) -> list[dict[str, Any]]:
        ctype = str(candidate.get("candidate_type") or "")
        title = _safe_text(candidate.get("title"), 120)
        summary = _safe_text(candidate.get("summary"), 500)
        dry_run = candidate.get("dry_run_contract") if isinstance(candidate.get("dry_run_contract"), dict) else {}
        checks = [
            self._check("schema", "has_candidate_id", bool(candidate.get("candidate_id")), "blocker"),
            self._check("schema", "known_candidate_type", ctype in VALID_CANDIDATE_TYPES, "blocker"),
            self._check("schema", "has_title", bool(title), "warning"),
            self._check("schema", "has_summary", bool(summary), "warning"),
            self._check("boundary", "auto_register_disabled", candidate.get("auto_register_allowed") is False, "blocker"),
            self._check("boundary", "auto_execute_disabled", candidate.get("auto_execute_allowed") is False, "blocker"),
            self._check("boundary", "skill_file_write_disabled", candidate.get("writes_skill_files") is False, "blocker"),
            self._check("boundary", "tool_registry_write_disabled", candidate.get("writes_tool_registry") is False, "blocker"),
            self._check("boundary", "system_policy_change_disabled", candidate.get("changes_system_policy") is False, "blocker"),
            self._check("dry_run", "ledger_only_mode", dry_run.get("mode") == "ledger_only", "warning"),
        ]
        a4_hit = _contains_any(text, A4_TERMS) or any(term in text for term in A4_TERMS_CN)
        a5_hit = _contains_any(text, A5_TERMS) or any(term in text for term in A5_TERMS_CN)
        checks.append(self._check("risk", "a4_terms_detected", not a4_hit, "notice" if ctype in {"tool_candidate", "skill_candidate"} else "warning"))
        checks.append(self._check("risk", "a5_terms_absent", not a5_hit, "blocker"))
        if ctype == "tool_candidate":
            checks.append(self._check("approval", "tool_candidate_manual_only", True, "notice"))
        return checks

    @staticmethod
    def _check(category: str, name: str, passed: bool, severity: str) -> dict[str, Any]:
        return {
            "category": category,
            "name": name,
            "passed": bool(passed),
            "severity": severity,
        }

    def _effective_risk(self, candidate: dict[str, Any], text: str) -> str:
        rank = _risk_rank(candidate.get("risk_level") or "A3")
        ctype = str(candidate.get("candidate_type") or "")
        if ctype == "tool_candidate":
            rank = max(rank, 4)
        elif ctype == "skill_candidate":
            rank = max(rank, 3)
        elif ctype == "workflow_candidate":
            rank = max(rank, 2)
        elif ctype == "prompt_candidate":
            rank = max(rank, 2)
        if _contains_any(text, A4_TERMS) or any(term in text for term in A4_TERMS_CN):
            rank = max(rank, 4)
        if _contains_any(text, A5_TERMS) or any(term in text for term in A5_TERMS_CN):
            rank = max(rank, 5)
        return _risk_level(rank)

    @staticmethod
    def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
        states: dict[str, int] = {}
        risks: dict[str, int] = {}
        blocked = 0
        for item in results:
            approval = item.get("approval") if isinstance(item.get("approval"), dict) else {}
            state = str(approval.get("approval_state") or "unknown")
            risk = str(item.get("risk_level") or "unknown")
            states[state] = states.get(state, 0) + 1
            risks[risk] = risks.get(risk, 0) + 1
            if state == "blocked":
                blocked += 1
        return {
            "validated_count": len(results),
            "approval_states": states,
            "by_risk": risks,
            "blocked_count": blocked,
            "runtime_activation_count": 0,
            "tool_registration_count": 0,
        }
