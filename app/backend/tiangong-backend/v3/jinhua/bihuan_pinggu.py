"""Phase 6: closed-loop scoring for evolution candidates.

The phase-6 ledger scores validated candidates and recommends promotion,
revision, quarantine, or rollback.  It records L0 commit/rollback references,
but does not perform commits, rollbacks, file changes, or policy changes.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .houxuan_shengcheng import HOUXUAN_LEDGER_LUJING
from .yanzheng_shenpi import YANZHENG_LEDGER_LUJING
from .yuanyu_yingshe import JINHUA_BIHUAN_ROOT, L0_EVOLUTION_STATE, L0_LEARNING_STATE


BIHUAN_LEDGER_LUJING = JINHUA_BIHUAN_ROOT / "jinhua_bihuan_pinggu.json"


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


class JinhuaBihuanPinggu:
    """Scores validated candidates and emits non-executing evolution advice."""

    def __init__(
        self,
        *,
        candidate_ledger_path: Path | None = None,
        validation_ledger_path: Path | None = None,
        ledger_path: Path | None = None,
    ) -> None:
        self.candidate_ledger_path = candidate_ledger_path or HOUXUAN_LEDGER_LUJING
        self.validation_ledger_path = validation_ledger_path or YANZHENG_LEDGER_LUJING
        self.ledger_path = ledger_path or BIHUAN_LEDGER_LUJING

    def pinggu(
        self,
        candidate_report: dict[str, Any] | None = None,
        validation_report: dict[str, Any] | None = None,
        *,
        reason: str = "runtime",
    ) -> dict[str, Any]:
        if candidate_report is None:
            candidate_report = self._load_candidate_report()
        if validation_report is None:
            validation_report = self._load_validation_report()

        candidates = {
            str(item.get("candidate_id")): item
            for item in (candidate_report.get("candidates") if isinstance(candidate_report, dict) else []) or []
            if isinstance(item, dict) and item.get("candidate_id")
        }
        validation_rows = [
            item for item in (validation_report.get("validation_results") if isinstance(validation_report, dict) else []) or []
            if isinstance(item, dict)
        ]
        evaluations = [
            self._evaluate_candidate(candidates.get(str(row.get("candidate_id")), {}), row)
            for row in validation_rows
        ]
        payload = {
            "schema": "tiangong.v3.evolution_closed_loop.v1",
            "phase": "phase6_closed_loop_scoring",
            "generated_at": _now_iso(),
            "reason": _safe_text(reason, 80),
            "source_candidate_path": str(self.candidate_ledger_path),
            "source_validation_path": str(self.validation_ledger_path),
            "safety_contract": {
                "commit_executed": False,
                "rollback_executed": False,
                "runtime_policy_changed": False,
                "tool_registry_changed": False,
                "skill_files_changed": False,
            },
            "loop_model": [
                "observe_learning_projection",
                "propose_candidate",
                "validate_static_contract",
                "score_expected_value",
                "recommend_next_state",
                "wait_for_manual_commit",
            ],
            "summary": self._summary(evaluations),
            "evaluations": evaluations,
        }
        _atomic_write_json(self.ledger_path, payload)
        return payload

    def load_ledger(self) -> dict[str, Any]:
        data = _read_json(self.ledger_path, {})
        return data if isinstance(data, dict) else {}

    def _load_candidate_report(self) -> dict[str, Any]:
        data = _read_json(self.candidate_ledger_path, {})
        return data if isinstance(data, dict) else {"candidates": []}

    def _load_validation_report(self) -> dict[str, Any]:
        data = _read_json(self.validation_ledger_path, {})
        return data if isinstance(data, dict) else {"validation_results": []}

    def _evaluate_candidate(self, candidate: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
        cid = str(validation.get("candidate_id") or candidate.get("candidate_id") or "")
        approval = validation.get("approval") if isinstance(validation.get("approval"), dict) else {}
        dry_run = validation.get("dry_run") if isinstance(validation.get("dry_run"), dict) else {}
        risk_rank = _risk_rank(validation.get("risk_level") or candidate.get("risk_level") or "A3")
        evidence_score = float(candidate.get("evidence_score") or 0.0) if candidate else 0.0
        validation_score = self._validation_score(validation)
        risk_penalty = min(0.6, risk_rank * 0.1)
        expected_value = max(0.0, min(1.0, round((evidence_score * 0.45) + (validation_score * 0.45) - risk_penalty + 0.1, 4)))
        recommendation, next_state = self._recommendation(expected_value, risk_rank, str(approval.get("approval_state") or ""), str(dry_run.get("status") or ""))
        return {
            "candidate_id": cid,
            "candidate_type": validation.get("candidate_type") or candidate.get("candidate_type"),
            "title": _safe_text(validation.get("title") or candidate.get("title"), 120),
            "loop_id": f"loop_{_digest([cid, expected_value, recommendation], 14)}",
            "learning_state": self._learning_state_for_recommendation(recommendation),
            "evolution_state": next_state,
            "risk_level": validation.get("risk_level") or candidate.get("risk_level"),
            "scores": {
                "evidence_score": round(evidence_score, 4),
                "validation_score": round(validation_score, 4),
                "risk_penalty": round(risk_penalty, 4),
                "expected_value": expected_value,
            },
            "recommendation": recommendation,
            "manual_gate": {
                "human_approval_required": True,
                "human_approved": bool(approval.get("human_approved")),
                "can_commit_now": False,
                "commit_executed": False,
                "rollback_executed": False,
            },
            "l0_refs": self._l0_refs(candidate, validation, cid),
            "rollback_plan": self._rollback_plan(recommendation, risk_rank, cid),
            "observed_effects": {
                "runtime_activation_count": 0,
                "tool_registration_count": 0,
                "skill_file_write_count": 0,
            },
            "evaluated_at": _now_iso(),
        }

    @staticmethod
    def _validation_score(validation: dict[str, Any]) -> float:
        checks = validation.get("checks") if isinstance(validation.get("checks"), list) else []
        if not checks:
            return 0.0
        weighted_total = 0.0
        weight_sum = 0.0
        severity_weight = {"blocker": 1.0, "warning": 0.6, "notice": 0.3}
        for check in checks:
            if not isinstance(check, dict):
                continue
            weight = severity_weight.get(str(check.get("severity") or ""), 0.5)
            weighted_total += weight if check.get("passed") else 0.0
            weight_sum += weight
        return weighted_total / max(1e-6, weight_sum)

    @staticmethod
    def _recommendation(score: float, risk_rank: int, approval_state: str, dry_run_status: str) -> tuple[str, str]:
        if risk_rank >= 5 or approval_state == "blocked" or dry_run_status == "blocked":
            return "quarantine_and_request_revision", L0_EVOLUTION_STATE["quarantined"]
        if score >= 0.72 and risk_rank <= 2 and approval_state == "review_ready":
            return "promote_to_manual_commit_review", L0_EVOLUTION_STATE["approved"]
        if score >= 0.58 and risk_rank <= 3:
            return "keep_in_review_and_collect_evidence", L0_EVOLUTION_STATE["assessing"]
        if approval_state == "needs_human_approval" or risk_rank >= 4:
            return "hold_for_human_approval", L0_EVOLUTION_STATE["assessing"]
        return "revise_or_archive_candidate", L0_EVOLUTION_STATE["rejected"]

    @staticmethod
    def _learning_state_for_recommendation(recommendation: str) -> str:
        if recommendation == "promote_to_manual_commit_review":
            return L0_LEARNING_STATE["approved"]
        if recommendation in {"keep_in_review_and_collect_evidence", "hold_for_human_approval"}:
            return L0_LEARNING_STATE["assessing"]
        if recommendation == "quarantine_and_request_revision":
            return L0_LEARNING_STATE["quarantined"]
        return L0_LEARNING_STATE["rejected"]

    @staticmethod
    def _l0_refs(candidate: dict[str, Any], validation: dict[str, Any], candidate_id: str) -> dict[str, Any]:
        refs = candidate.get("l0_refs") if isinstance(candidate.get("l0_refs"), dict) else {}
        source_refs = validation.get("source_candidate") if isinstance(validation.get("source_candidate"), dict) else {}
        validation_refs = source_refs.get("l0_refs") if isinstance(source_refs.get("l0_refs"), dict) else {}
        merged = dict(validation_refs or refs or {})
        merged.setdefault("improvement_candidate_ref", f"improvement_candidate:{candidate_id}")
        merged.setdefault("evaluation_ref", validation.get("evaluation_ref") or f"evaluation:{candidate_id}")
        merged.setdefault("commit_ref", f"commit:{candidate_id}")
        merged.setdefault("rollback_ref", f"rollback:{candidate_id}")
        return merged

    @staticmethod
    def _rollback_plan(recommendation: str, risk_rank: int, candidate_id: str) -> dict[str, Any]:
        return {
            "rollback_ref": f"rollback:{candidate_id}",
            "rollback_recommended": recommendation == "quarantine_and_request_revision" or risk_rank >= 5,
            "rollback_executed": False,
            "rollback_scope": "ledger_candidate_only",
            "notes": "No runtime activation exists in phases 4-6, so rollback is a recommendation record only.",
        }

    @staticmethod
    def _summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
        recommendations: dict[str, int] = {}
        states: dict[str, int] = {}
        for item in evaluations:
            rec = str(item.get("recommendation") or "unknown")
            state = str(item.get("evolution_state") or "unknown")
            recommendations[rec] = recommendations.get(rec, 0) + 1
            states[state] = states.get(state, 0) + 1
        return {
            "evaluated_count": len(evaluations),
            "recommendations": recommendations,
            "evolution_states": states,
            "commit_executed_count": 0,
            "rollback_executed_count": 0,
            "runtime_activation_count": 0,
        }
