"""简单链事件流：追加式 JSONL，供监控/网关/回放消费。

纯增量设计，不影响任何现有系统：
- 发射失败只返回 False，绝不影响主流程；
- 位置不写死：环境变量 → 自动生成的位置指针文件 → 自动推导，首次运行生成指针；
- 按日轮转（events-YYYYMMDD.jsonl），保留 30 天；
- 跨进程追加用现有文件锁，seq 单调递增。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .duihua_qiaojie import _exclusive_file_lock

EVENT_SCHEMA = "tiangong.v3.simple_chain.event.v1"
LOCATION_SCHEMA = "tiangong.v3.simple_chain.events_location.v1"

EVENT_TYPES = frozenset({
    "chain_started",
    "continue_decision",
    "turn.failed",
    "force_stopped",
    "budget_limited",
    "run_interrupted",
    "chain_completed",
})
TERMINAL_EVENT_TYPES = frozenset({
    "turn.failed",
    "force_stopped",
    "budget_limited",
    "run_interrupted",
    "chain_completed",
})

_RETAIN_DAYS = 30
_lock = threading.RLock()
_cached_root: str | None = None


def _run_state_root() -> Path:
    from .zongdiaodu import _simple_chain_run_state_path

    return _simple_chain_run_state_path("__probe__").parent


def _pointer_path() -> Path:
    return _run_state_root().parent / "simple_chain_events_location.json"


def _derive_root() -> Path:
    return _run_state_root().parent / "simple_chain_events"


def events_root(*, refresh: bool = False) -> Path:
    """解析事件目录：env > 自动生成的位置指针 > 自动推导；缺失/失效自动重建。"""
    global _cached_root
    with _lock:
        if _cached_root and not refresh:
            return Path(_cached_root)
        env = str(os.environ.get("TIANGONG_SIMPLE_CHAIN_EVENTS_ROOT") or "").strip()
        root: Path | None = None
        source = "env"
        if env:
            root = Path(env).expanduser().resolve(strict=False)
        else:
            pointer = _pointer_path()
            try:
                if pointer.exists():
                    data = json.loads(pointer.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and str(data.get("root") or "").strip():
                        candidate = Path(str(data["root"])).expanduser().resolve(strict=False)
                        if candidate.exists() or str(candidate.drive).strip():
                            root = candidate
                            source = "pointer"
            except Exception:
                pass
        if root is None:
            root = _derive_root()
            source = "derived"
        try:
            root.mkdir(parents=True, exist_ok=True)
            _pointer_path().write_text(
                json.dumps(
                    {
                        "schema": LOCATION_SCHEMA,
                        "root": str(root),
                        "source": source,
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        _cached_root = str(root)
        return root


def _today_path(root: Path) -> Path:
    return root / ("events-%s.jsonl" % datetime.now().strftime("%Y%m%d"))


def _next_seq(path: Path) -> int:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return 1
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            tail = fh.read().decode("utf-8", errors="ignore").strip().splitlines()
        if not tail:
            return 1
        last = json.loads(tail[-1])
        return int(last.get("seq") or 0) + 1
    except Exception:
        return 1


def _retain(root: Path) -> None:
    try:
        now = datetime.now()
        for path in root.glob("events-*.jsonl"):
            try:
                age_days = (now - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 86400
                if age_days > _RETAIN_DAYS:
                    path.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass


def append_event(event: dict[str, Any]) -> bool:
    """追加一个事件。失败返回 False，绝不抛异常。"""
    if not isinstance(event, dict):
        return False
    etype = str(event.get("type") or "")
    if etype not in EVENT_TYPES:
        return False
    try:
        root = events_root()
        path = _today_path(root)
        lock_path = root / ".events.lock"
        with _exclusive_file_lock(lock_path):
            seq = _next_seq(path)
            payload = {
                "schema": EVENT_SCHEMA,
                "seq": seq,
                "type": etype,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            for key in ("run_id", "request_id", "session_id", "round", "tool_rounds",
                        "wall_clock_used_s", "reason", "source", "status", "attempt",
                        "decided_to_continue", "fingerprint_changed"):
                if key in event:
                    payload[key] = event[key]
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
        _retain(root)
        return True
    except Exception:
        return False


def event_type_for(status: str, reasons: list[str] | None) -> str:
    """终局状态 → 事件类型（预算/模型层失败单独归类，其余归 force_stopped/completed）。"""
    text = " ".join(str(item) for item in (reasons or [])).lower()
    status_text = str(status or "").strip()
    if status_text == "interrupted":
        return "run_interrupted"
    if status_text == "force_stopped":
        if "[terminal_model_error]" in text:
            return "turn.failed"
        if "budget" in text:
            return "budget_limited"
        return "force_stopped"
    return "chain_completed"


def list_terminal_run_ids(root: Path) -> set[str]:
    """扫描当日事件文件中已有终局事件的 run_id（用于启动回填去重）。"""
    path = _today_path(root)
    run_ids: set[str] = set()
    try:
        if not path.exists():
            return run_ids
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if str(item.get("type") or "") in TERMINAL_EVENT_TYPES and str(item.get("run_id") or ""):
                    run_ids.add(str(item["run_id"]))
    except Exception:
        pass
    return run_ids
