"""Phases 4-6 orchestration for the v3 evolution closed loop."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bihuan_pinggu import JinhuaBihuanPinggu
from .houxuan_shengcheng import JinhuaHouxuanShengcheng
from .yanzheng_shenpi import JinhuaYanzhengShenpi
from .yuanyu_yingshe import JINHUA_BIHUAN_ROOT


BIHUAN_REPORT_LUJING = JINHUA_BIHUAN_ROOT / "jinhua_bihuan_report.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").replace("\r", " ")
    return " ".join(text.split())[: max(0, int(limit or 0))]


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class JinhuaBihuanYinqing:
    """Runs candidate generation, validation, and closed-loop scoring."""

    def __init__(
        self,
        *,
        houxuan: JinhuaHouxuanShengcheng | None = None,
        yanzheng: JinhuaYanzhengShenpi | None = None,
        pinggu: JinhuaBihuanPinggu | None = None,
        report_path: Path | None = None,
    ) -> None:
        self.houxuan = houxuan or JinhuaHouxuanShengcheng()
        self.yanzheng = yanzheng or JinhuaYanzhengShenpi(candidate_generator=self.houxuan)
        self.pinggu = pinggu or JinhuaBihuanPinggu()
        self.report_path = report_path or BIHUAN_REPORT_LUJING

    def yunxing(
        self,
        shenti: Any | None = None,
        *,
        xiaoxi: str = "",
        reason: str = "runtime",
    ) -> dict[str, Any]:
        candidate_report = self.houxuan.shengcheng(shenti, xiaoxi=xiaoxi, reason=reason)
        validation_report = self.yanzheng.yanzheng(candidate_report, reason=reason)
        loop_report = self.pinggu.pinggu(candidate_report, validation_report, reason=reason)
        payload = {
            "schema": "tiangong.v3.evolution_phases_4_6_report.v1",
            "generated_at": _now_iso(),
            "reason": _safe_text(reason, 80),
            "phase4": {
                "ledger_path": str(self.houxuan.ledger_path),
                "summary": candidate_report.get("summary", {}),
            },
            "phase5": {
                "ledger_path": str(self.yanzheng.ledger_path),
                "summary": validation_report.get("summary", {}),
            },
            "phase6": {
                "ledger_path": str(self.pinggu.ledger_path),
                "summary": loop_report.get("summary", {}),
            },
            "safety_contract": {
                "auto_register_allowed": False,
                "auto_execute_allowed": False,
                "commit_executed": False,
                "rollback_executed": False,
                "runtime_policy_changed": False,
            },
        }
        _atomic_write_json(self.report_path, payload)
        return payload
