"""能力健康状态机：成败记账 -> 补丁验证门 -> 版本回滚 -> 自动降级。

补齐生命学习链路的“退役/降级”出口：
  - 发布/激活后每次真实执行结果记账（幂等、版本隔离）；
  - 连续失败达到阈值触发自动补丁；
  - 补丁必须通过验证门才 CAS 替换当前版本；未过门回滚保留旧版；
  - 补丁轮次用尽仍失败则自动降级（停用 runtime，保留历史与映射标记）；
  - 仅用户可手动重新激活。

本模块是纯状态推导，不写持久化。原子性由调用方沿用
“scope 深拷贝 + 异常回滚”模式保证（同 capabilities_before/pointers_before）。

指针状态机：
  pending -> active -> degraded（自动降级） / disabled（用户删除）
  active 期间 health.patch_pending 表示补丁验证中，旧版继续可用。
"""
from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any, Mapping


HEALTH_SCHEMA = "tiangong.life.capability-health.v1"
POINTER_SCHEMA = "tiangong.life.capability-pointer.v1"

# 连续失败多少次后请求自动补丁（参数扫描最优组合：3）。
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
# 同一版本最多允许几轮补丁；轮次用尽仍未过验证门则自动降级（最优组合：2）。
DEFAULT_MAX_PATCH_ROUNDS = 2
# 幂等 outcome_id 保留上限（FIFO），避免健康档案无限膨胀。
SEEN_OUTCOME_CAP = 200


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _text(value: Any, field: str, *, limit: int = 200) -> str:
    result = str(value or "").strip()
    if len(result.encode("utf-8")) > limit:
        raise ValueError(f"health.{field}.too_large")
    return result


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"health.{field}.invalid")
    return value


def initial_health(artifact_id: str, *, now_ms: int) -> dict[str, Any]:
    artifact_id = _text(artifact_id, "artifact_id", limit=160)
    if not artifact_id:
        raise ValueError("health.artifact_id.required")
    return {
        "schema": HEALTH_SCHEMA,
        "uses": 0,
        "successes": 0,
        "failures": 0,
        "consecutive_failures": 0,
        "patch_rounds": 0,
        "patch_pending": None,
        "patch_history": [],
        "seen_outcome_ids": [],
        "last_outcome_at_ms": 0,
        "reactivated_at_ms": None,
        "created_at_ms": _int(now_ms, "now_ms"),
    }


def _refresh_pointer_sha256(pointer: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(pointer)
    value["pointer_sha256"] = canonical_sha256(
        {key: value[key] for key in value if key != "pointer_sha256"}
    )
    return value


def attach_health(
    pointer: Mapping[str, Any],
    *,
    artifact: Mapping[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """发布/激活时为指针挂载健康档案（幂等：已有则不动）。"""
    value = dict(pointer)
    if not isinstance(value.get("health"), Mapping):
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("health.attach.artifact_required")
        value["health"] = initial_health(artifact_id, now_ms=now_ms)
    return _refresh_pointer_sha256(value)


def ingest_outcome(
    pointer: Mapping[str, Any],
    outcome: Mapping[str, Any],
    *,
    now_ms: int,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    max_patch_rounds: int = DEFAULT_MAX_PATCH_ROUNDS,
) -> tuple[dict[str, Any], str, str]:
    """记录一次能力执行结果，返回 (新指针, 动作, 原因码)。

    动作：none（仅记账/忽略）或 request_patch（连续失败达到阈值）。
    """
    _int(now_ms, "now_ms")
    if not isinstance(outcome, Mapping):
        raise ValueError("health.outcome.invalid")
    status = str(pointer.get("status") or "")
    if status not in {"active", "patch_verifying"}:
        return dict(pointer), "none", "not_active"
    outcome_id = _text(outcome.get("outcome_id"), "outcome_id", limit=160)
    if not outcome_id:
        raise ValueError("health.outcome_id.required")
    artifact_id = _text(outcome.get("artifact_id"), "artifact_id", limit=160)
    result = str(outcome.get("outcome") or "").strip().casefold()
    if result not in {"success", "failure"}:
        raise ValueError("health.outcome.kind_invalid")
    occurred_ms = _int(outcome.get("occurred_at_ms") or now_ms, "occurred_at_ms")
    value = dict(pointer)
    health = dict(value.get("health") or {})
    if health.get("schema") != HEALTH_SCHEMA:
        raise ValueError("health.missing")
    seen = list(health.get("seen_outcome_ids") or [])
    if outcome_id in seen:
        return value, "none", "duplicate"
    if artifact_id != str(value.get("current_artifact_id") or ""):
        # 旧版本的结果不污染当前版本的健康档案（版本隔离）。
        return value, "none", "stale_version"
    seen = (seen + [outcome_id])[-SEEN_OUTCOME_CAP:]
    health["uses"] = int(health.get("uses") or 0) + 1
    if result == "success":
        health["successes"] = int(health.get("successes") or 0) + 1
        health["consecutive_failures"] = 0
    else:
        health["failures"] = int(health.get("failures") or 0) + 1
        health["consecutive_failures"] = int(health.get("consecutive_failures") or 0) + 1
    health["seen_outcome_ids"] = seen
    health["last_outcome_at_ms"] = occurred_ms
    value["health"] = health
    value = _refresh_pointer_sha256(value)
    if (
        result == "failure"
        and int(health["consecutive_failures"]) >= max_consecutive_failures
        and not health.get("patch_pending")
        and int(health.get("patch_rounds") or 0) < max_patch_rounds
    ):
        return value, "request_patch", "consecutive_failures"
    return value, "none", "recorded"


def propose_patch(
    pointer: Mapping[str, Any],
    patched_artifact: Mapping[str, Any],
    *,
    now_ms: int,
    max_patch_rounds: int = DEFAULT_MAX_PATCH_ROUNDS,
) -> dict[str, Any]:
    """提交补丁草案进入验证（不替换指针；旧版验证期间继续可用）。"""
    _int(now_ms, "now_ms")
    if str(pointer.get("status") or "") != "active":
        raise ValueError("health.patch.not_active")
    value = dict(pointer)
    health = dict(value.get("health") or {})
    if health.get("schema") != HEALTH_SCHEMA:
        raise ValueError("health.missing")
    if health.get("patch_pending"):
        raise ValueError("health.patch.already_pending")
    rounds = int(health.get("patch_rounds") or 0)
    if rounds >= max_patch_rounds:
        raise ValueError("health.patch.rounds_exhausted")
    from_id = str(value.get("current_artifact_id") or "")
    to_id = str(patched_artifact.get("artifact_id") or "")
    to_sha = str(patched_artifact.get("artifact_sha256") or "")
    if not from_id or not to_id or not to_sha:
        raise ValueError("health.patch.artifact_invalid")
    if from_id == to_id:
        raise ValueError("health.patch.same_version")
    health["patch_rounds"] = rounds + 1
    health["patch_pending"] = {
        "round": rounds + 1,
        "from_artifact_id": from_id,
        "to_artifact_id": to_id,
        "to_artifact_sha256": to_sha,
        "proposed_at_ms": _int(now_ms, "now_ms"),
    }
    value["health"] = health
    return _refresh_pointer_sha256(value)


def settle_patch(
    pointer: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    now_ms: int,
    max_patch_rounds: int = DEFAULT_MAX_PATCH_ROUNDS,
) -> tuple[dict[str, Any], bool, str]:
    """验证门结算补丁。

    返回 (新指针, 是否替换成功, 原因码)：
      applied - 验证通过，指针 CAS 到补丁版本，连续失败清零；
      rolled_back - 验证失败，指针保持旧版；未超轮次可再试；
      degraded - 验证失败且轮次用尽，自动降级停用 runtime。
    """
    _int(now_ms, "now_ms")
    if not isinstance(verification, Mapping):
        raise ValueError("health.verification.invalid")
    passed = bool(verification.get("passed"))
    value = dict(pointer)
    health = dict(value.get("health") or {})
    if health.get("schema") != HEALTH_SCHEMA:
        raise ValueError("health.missing")
    pending = health.get("patch_pending")
    if not isinstance(pending, Mapping):
        raise ValueError("health.patch.nothing_pending")
    to_id = str(pending.get("to_artifact_id") or "")
    to_sha = str(pending.get("to_artifact_sha256") or "")
    evidence = _text(verification.get("evidence_sha256") or "", "evidence_sha256", limit=160)
    history = list(health.get("patch_history") or [])
    if passed:
        history.append({
            "round": int(pending.get("round") or 0),
            "from_artifact_id": str(pending.get("from_artifact_id") or ""),
            "to_artifact_id": to_id,
            "result": "applied",
            "verified_at_ms": now_ms,
            "evidence_sha256": evidence,
        })
        health["patch_pending"] = None
        health["consecutive_failures"] = 0
        health["patch_history"] = history[-64:]
        value["health"] = health
        value["current_artifact_id"] = to_id
        value["current_artifact_sha256"] = to_sha
        value["history"] = list(value.get("history") or [])[-63:] + [{
            "operation": "patch_applied",
            "from_artifact_id": str(pending.get("from_artifact_id") or ""),
            "to_artifact_id": to_id,
            "at": _render_utc(now_ms),
            "verified": True,
        }]
        return _refresh_pointer_sha256(value), True, "applied"
    history.append({
        "round": int(pending.get("round") or 0),
        "from_artifact_id": str(pending.get("from_artifact_id") or ""),
        "to_artifact_id": to_id,
        "result": "rolled_back",
        "verified_at_ms": now_ms,
        "evidence_sha256": evidence,
    })
    health["patch_pending"] = None
    health["patch_history"] = history[-64:]
    value["health"] = health
    if int(health.get("patch_rounds") or 0) >= max_patch_rounds:
        return degrade_pointer(
            value,
            reason=f"patch_rounds_exhausted:{int(health.get('patch_rounds') or 0)}",
            now_ms=now_ms,
        ), False, "degraded"
    return _refresh_pointer_sha256(value), False, "rolled_back"


def degrade_pointer(
    pointer: Mapping[str, Any],
    *,
    reason: str,
    now_ms: int,
) -> dict[str, Any]:
    """自动降级：停用 runtime，保留版本历史、健康档案与工作区映射标记。"""
    _int(now_ms, "now_ms")
    status = str(pointer.get("status") or "")
    if status not in {"active", "patch_verifying"}:
        raise ValueError("health.degrade.invalid_state")
    value = dict(pointer)
    health = dict(value.get("health") or {})
    health["patch_pending"] = None
    value["health"] = health
    value["status"] = "degraded"
    value["degraded_at"] = _render_utc(now_ms)
    value["degraded_reason"] = _text(reason, "reason", limit=400)
    value["history"] = list(value.get("history") or [])[-63:] + [{
        "operation": "auto_degrade",
        "from_artifact_id": str(value.get("current_artifact_id") or ""),
        "to_artifact_id": "",
        "at": value["degraded_at"],
        "reason": value["degraded_reason"],
    }]
    return _refresh_pointer_sha256(value)


def reactivate_pointer(
    pointer: Mapping[str, Any],
    *,
    actor: str,
    now_ms: int,
) -> dict[str, Any]:
    """用户手动重新激活降级能力；只接受 user 显式操作。"""
    _int(now_ms, "now_ms")
    if str(actor or "").strip() != "user":
        raise ValueError("health.reactivate.user_only")
    if str(pointer.get("status") or "") != "degraded":
        raise ValueError("health.reactivate.not_degraded")
    value = dict(pointer)
    health = dict(value.get("health") or {})
    health["consecutive_failures"] = 0
    health["patch_rounds"] = 0
    health["patch_pending"] = None
    health["reactivated_at_ms"] = _int(now_ms, "now_ms")
    value["health"] = health
    value["status"] = "active"
    value.pop("degraded_at", None)
    value.pop("degraded_reason", None)
    value["history"] = list(value.get("history") or [])[-63:] + [{
        "operation": "user_reactivate",
        "from_artifact_id": str(value.get("current_artifact_id") or ""),
        "to_artifact_id": str(value.get("current_artifact_id") or ""),
        "at": _render_utc(now_ms),
    }]
    return _refresh_pointer_sha256(value)


def runtime_usable(pointer: Mapping[str, Any]) -> bool:
    return str(pointer.get("status") or "") == "active"


def _render_utc(now_ms: int) -> str:
    return datetime.datetime.fromtimestamp(
        now_ms / 1000, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


__all__ = [
    "HEALTH_SCHEMA",
    "POINTER_SCHEMA",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "DEFAULT_MAX_PATCH_ROUNDS",
    "SEEN_OUTCOME_CAP",
    "canonical_sha256",
    "initial_health",
    "attach_health",
    "ingest_outcome",
    "propose_patch",
    "settle_patch",
    "degrade_pointer",
    "reactivate_pointer",
    "runtime_usable",
]
