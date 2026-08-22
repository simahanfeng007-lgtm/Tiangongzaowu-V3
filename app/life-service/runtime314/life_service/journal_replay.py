"""P1-5（硬约束 H3）：Journal Event → Reducer Registry → Projection。

journal 是权威 WAL（逐事件 fsync + 哈希链 + 序号严格校验，见
complete_core.SemanticJournal）；scope 状态 JSON 只是可重建投影
（_persist 去抖，崩溃窗口由启动重放补齐）。本模块把启动重放从
``_reconcile_authoritative_journal`` 神函数改造为注册表驱动：

- 每个会改变持久状态的 journal 事件必须在本模块 ``EVENT_REGISTRY``
  登记三分类之一：
    replayable_projection       —— 重放可完整/最优努力恢复投影
    audit_only                  —— 只审计，投影权威在别处（Store/自愈）
    external_terminal_evidence  —— 不可逆外部效果的终态证据（按可重放处理）
- 未登记的事件类型在重放时 fail-closed（``life.projection.event_unclassified``），
  新增能力不可能再"忘记写重放"。
- 同一事件的投影内容与既有投影语义冲突时 fail-closed，绝不覆盖。

已知信息缺口（payload 未携带完整状态；重放按可恢复部分执行，写侧
payload 丰富化列为后续工作）：
- ``life.proactive.delivered``：消息正文与 conversation_id 不在 payload，
  无法重建 proactive_chats 行，只重放 scheduler 门控键（行缺失时
  acked/replied 补丁自然跳过）。
- ``life.share.published``：信箱正文不在 payload，只重放 scheduler 键。
- ``affect.appraised/decayed``：情绪向量为 transient 设计（下一次心跳/
  用户轮次自愈），audit-only。
- ``memory.lifecycle_advanced/recalled``：稳定性 checkpoint，lifecycle 由
  调度自愈，audit-only。
- ``capability.patch_proposed``：补丁工件本体优先从 bundle 路径恢复，
  恢复失败时降级为仅指针。
- ``learning.published``：指针（pending）由用户激活时重建，重放只恢复
  学习卡与工件条目。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from contracts import canonical_sha256
from .capability_health import ingest_outcome


class JournalReplayError(RuntimeError):
    """投影重放 fail-closed（调用方映射为对外的 EmbeddedLifeError）。"""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)


class EventClass(str, Enum):
    REPLAYABLE_PROJECTION = "replayable_projection"
    AUDIT_ONLY = "audit_only"
    EXTERNAL_TERMINAL_EVIDENCE = "external_terminal_evidence"


EVENT_REGISTRY: dict[str, EventClass] = {
    # ---- 记忆（既有重放语义原样搬迁） ----
    "memory.asserted": EventClass.REPLAYABLE_PROJECTION,
    "memory.corrected": EventClass.REPLAYABLE_PROJECTION,
    "memory.status_changed": EventClass.REPLAYABLE_PROJECTION,
    "memory.deleted": EventClass.REPLAYABLE_PROJECTION,
    "memory.relation_added": EventClass.REPLAYABLE_PROJECTION,
    "memory.candidates_proposed": EventClass.REPLAYABLE_PROJECTION,
    "memory.lifecycle_advanced": EventClass.AUDIT_ONLY,
    "memory.recalled": EventClass.AUDIT_ONLY,
    # ---- 自主任务 ----
    "autonomy.task_generated": EventClass.REPLAYABLE_PROJECTION,
    "autonomy.task_status_changed": EventClass.REPLAYABLE_PROJECTION,
    # ---- 终态执行（不可逆外部效果证据） ----
    "execution.committed": EventClass.EXTERNAL_TERMINAL_EVIDENCE,
    # ---- 能力治理（指针事件 payload 自带完整新状态） ----
    "capability.activated": EventClass.REPLAYABLE_PROJECTION,
    "capability.disabled": EventClass.REPLAYABLE_PROJECTION,
    "capability.rolled_back": EventClass.REPLAYABLE_PROJECTION,
    "capability.reactivated": EventClass.REPLAYABLE_PROJECTION,
    "capability.patch_proposed": EventClass.REPLAYABLE_PROJECTION,
    "capability.patch_settled": EventClass.REPLAYABLE_PROJECTION,
    "capability.patch_failed": EventClass.REPLAYABLE_PROJECTION,
    "capability.executed": EventClass.EXTERNAL_TERMINAL_EVIDENCE,
    "capability.outcome": EventClass.REPLAYABLE_PROJECTION,
    # ---- 学习卡（整卡入 payload） ----
    "learning.draft_created": EventClass.REPLAYABLE_PROJECTION,
    "learning.published": EventClass.REPLAYABLE_PROJECTION,
    "learning.confirmed": EventClass.REPLAYABLE_PROJECTION,
    "learning.discarded": EventClass.REPLAYABLE_PROJECTION,
    "learning.decision_noop": EventClass.AUDIT_ONLY,
    # ---- 自我迭代升级卡 ----
    "upgrade.card_created": EventClass.REPLAYABLE_PROJECTION,
    "upgrade.card_confirmed": EventClass.REPLAYABLE_PROJECTION,
    "upgrade.card_cancelled": EventClass.REPLAYABLE_PROJECTION,
    "upgrade.card_completed": EventClass.REPLAYABLE_PROJECTION,
    "upgrade.card_failed": EventClass.REPLAYABLE_PROJECTION,
    # ---- 主动消息（行内容缺口见模块 docstring） ----
    "life.proactive.delivered": EventClass.REPLAYABLE_PROJECTION,
    "life.proactive.acked": EventClass.REPLAYABLE_PROJECTION,
    "life.proactive.replied": EventClass.REPLAYABLE_PROJECTION,
    "life.proactive.decision": EventClass.AUDIT_ONLY,
    "life.proactive.suppressed": EventClass.AUDIT_ONLY,
    "life.proactive.decision_failed": EventClass.AUDIT_ONLY,
    "life.proactive.context_unavailable": EventClass.AUDIT_ONLY,
    "life.proactive.expression_budget_exhausted": EventClass.AUDIT_ONLY,
    "life.proactive.expression_unavailable": EventClass.AUDIT_ONLY,
    # ---- 生命信箱（正文缺口见模块 docstring） ----
    "life.share.published": EventClass.REPLAYABLE_PROJECTION,
    # ---- 观测/权威在别处 ----
    "life.heartbeat": EventClass.AUDIT_ONLY,
    "affect.appraised": EventClass.AUDIT_ONLY,
    "affect.decayed": EventClass.AUDIT_ONLY,
    "life.episode.opened": EventClass.AUDIT_ONLY,
    "life.episode.committed": EventClass.AUDIT_ONLY,
    "life.episode.aborted": EventClass.AUDIT_ONLY,
    "life.episode.failed": EventClass.AUDIT_ONLY,
    "life.capability.learning.committed": EventClass.AUDIT_ONLY,
}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _event_iso(event: Mapping[str, Any]) -> str:
    return str(event.get("created_at") or "")


def _event_ms(event: Mapping[str, Any]) -> int:
    import datetime as _dt

    raw = _event_iso(event)
    try:
        return int(_dt.datetime.fromisoformat(raw).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def _same(a: Any, b: Any) -> bool:
    return canonical_sha256(a) == canonical_sha256(b)


def _require_mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise JournalReplayError(code)
    return dict(value)


# --------------------------------------------------------------------------
# 既有家族（语义自 _reconcile_authoritative_journal 原样搬迁，仅异常类型换为
# JournalReplayError，错误码不变）
# --------------------------------------------------------------------------

def _merge_asserted_memory_projection(existing: dict[str, Any], asserted: Mapping[str, Any]) -> bool:
    """不可变断言语义校验；status/updated_at/lifecycle 是后续事件可变字段。"""
    changed = False
    mutable = {"status", "updated_at", "lifecycle"}
    deleted = existing.get("status") == "deleted"
    for key, value in asserted.items():
        if key in mutable or (deleted and key == "content"):
            continue
        if key not in existing:
            existing[key] = deepcopy(value)
            changed = True
            continue
        if canonical_sha256(existing.get(key)) != canonical_sha256(value):
            raise JournalReplayError("life.projection.memory_conflict")
    return changed


def _merge_generated_task_projection(existing: dict[str, Any], generated: Mapping[str, Any]) -> bool:
    """跨状态迁移的任务提案不可变字段校验（可变字段集与原实现严格一致）。"""
    changed = False
    mutable = {"status", "updated_at_ms", "attempt_count", "result", "task_sha256"}
    for key, value in generated.items():
        if key in mutable:
            continue
        if key not in existing:
            existing[key] = deepcopy(value)
            changed = True
            continue
        if canonical_sha256(existing.get(key)) != canonical_sha256(value):
            raise JournalReplayError("life.projection.autonomy_task_conflict")
    return changed


def _reduce_memory_asserted(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, life_id: str) -> bool:
    changed = False
    assertion = _require_mapping(payload.get("assertion"), "life.projection.memory_event_invalid")
    record = deepcopy(assertion)
    memory_id = str(record.get("memory_id") or "")
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", memory_id):
        raise JournalReplayError("life.projection.memory_id_invalid")
    record.setdefault("life_id", life_id)
    existing = scope["memories"].get(memory_id)
    if existing is None:
        scope["memories"][memory_id] = record
        changed = True
    elif not isinstance(existing, dict):
        raise JournalReplayError("life.projection.memory_conflict")
    else:
        changed |= _merge_asserted_memory_projection(existing, record)
    if str(event.get("event_type") or "") == "memory.corrected":
        target_memory_id = str(payload.get("target_memory_id") or "")
        target = scope["memories"].get(target_memory_id)
        if not isinstance(target, dict):
            raise JournalReplayError("life.projection.memory_target_missing")
        if target.get("status") != "corrected":
            target["status"] = "corrected"
            target["updated_at"] = str(payload.get("updated_at") or record.get("created_at") or _event_iso(event))
            changed = True
    return changed


def _reduce_memory_status(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, life_id: str) -> bool:
    memory_id = str(payload.get("memory_id") or "")
    row = scope["memories"].get(memory_id)
    if not isinstance(row, dict):
        raise JournalReplayError("life.projection.memory_target_missing")
    status = str(payload.get("status") or "")
    if row.get("status") != status or row.get("updated_at") != payload.get("updated_at"):
        row["status"] = status
        row["updated_at"] = str(payload.get("updated_at") or _event_iso(event))
        return True
    return False


def _reduce_memory_deleted(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, life_id: str) -> bool:
    memory_id = str(payload.get("memory_id") or "")
    row = scope["memories"].get(memory_id)
    if not isinstance(row, dict):
        raise JournalReplayError("life.projection.memory_target_missing")
    if row.get("status") != "deleted" or row.get("content") != {"tombstone": True}:
        row["status"] = "deleted"
        row["content"] = {"tombstone": True}
        row["updated_at"] = str(payload.get("updated_at") or _event_iso(event))
        return True
    return False


def _reduce_memory_relation(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, life_id: str) -> bool:
    import re as _re

    relation = _require_mapping(payload.get("relation"), "life.projection.memory_relation_invalid")
    record = deepcopy(relation)
    relation_id = str(record.get("relation_id") or "")
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", relation_id):
        raise JournalReplayError("life.projection.memory_relation_invalid")
    relations = scope["memory_relations"]
    found = next(
        (item for item in relations if isinstance(item, Mapping) and item.get("relation_id") == relation_id),
        None,
    )
    if found is None:
        relations.append(record)
        return True
    if not _same(found, record):
        raise JournalReplayError("life.projection.memory_relation_conflict")
    return False


def _reduce_memory_candidates(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, life_id: str) -> bool:
    import re as _re

    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise JournalReplayError("life.projection.memory_candidate_invalid")
    queue = scope.setdefault("memory_candidates", {})
    if not isinstance(queue, dict):
        raise JournalReplayError("life.projection.memory_candidate_invalid")
    changed = False
    for raw in candidates:
        record = _require_mapping(raw, "life.projection.memory_candidate_invalid")
        record = deepcopy(record)
        candidate_id = str(record.get("candidate_id") or "")
        if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", candidate_id):
            raise JournalReplayError("life.projection.memory_candidate_invalid")
        found = queue.get(candidate_id)
        if found is None:
            queue[candidate_id] = record
            changed = True
        elif not _same(found, record):
            raise JournalReplayError("life.projection.memory_candidate_conflict")
    return changed


def _reduce_task_generated(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, normalize_autonomy_state: Callable) -> bool:
    task = _require_mapping(payload.get("task"), "life.projection.autonomy_task_invalid")
    record = deepcopy(task)
    import re as _re

    task_id = str(record.get("task_id") or "")
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", task_id):
        raise JournalReplayError("life.projection.autonomy_task_invalid")
    autonomy = normalize_autonomy_state(scope.get("autonomy"))
    existing = autonomy["tasks"].get(task_id)
    changed = False
    if existing is None:
        autonomy["tasks"][task_id] = record
        changed = True
    elif not isinstance(existing, dict):
        raise JournalReplayError("life.projection.autonomy_task_conflict")
    else:
        changed |= _merge_generated_task_projection(existing, record)
    if changed:
        scope["autonomy"] = autonomy
    return changed


def _reduce_task_status(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, normalize_autonomy_state: Callable) -> bool:
    task_id = str(payload.get("task_id") or "")
    autonomy = normalize_autonomy_state(scope.get("autonomy"))
    task = autonomy["tasks"].get(task_id)
    if not isinstance(task, dict):
        raise JournalReplayError("life.projection.autonomy_task_missing")
    projected = _require_mapping(payload.get("task"), "life.projection.autonomy_task_invalid")
    if not _same(task, projected):
        scope["autonomy"]["tasks"][task_id] = deepcopy(projected)
        return True
    return False


def _reduce_execution_committed(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, life_id: str) -> bool:
    import re as _re

    record = deepcopy(dict(payload))
    request_id = str(record.get("request_id") or "")
    commit_sha256 = str(record.get("commit_sha256") or "")
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", request_id) or not _re.fullmatch(r"[0-9a-f]{64}", commit_sha256):
        raise JournalReplayError("life.projection.execution_event_invalid")
    existing = scope["executions"].get(request_id)
    if existing is None:
        scope["executions"][request_id] = record
        return True
    if not isinstance(existing, Mapping) or existing.get("commit_sha256") != commit_sha256:
        raise JournalReplayError("life.projection.execution_conflict")
    return False


# --------------------------------------------------------------------------
# 新增家族：能力治理
# --------------------------------------------------------------------------

def _assign_capability_pointer(scope: dict[str, Any], payload: Mapping[str, Any], *, event_type: str) -> bool:
    pointer = _require_mapping(payload.get("pointer"), "life.projection.capability_pointer_invalid")
    lineage_id = str(pointer.get("lineage_id") or "")
    if not lineage_id:
        raise JournalReplayError("life.projection.capability_pointer_invalid")
    pointers = scope.setdefault("capability_pointers", {})
    existing = pointers.get(lineage_id)
    if existing is None or not _same(existing, pointer):
        pointers[lineage_id] = deepcopy(pointer)
        return True
    return False


def _reduce_capability_lifecycle(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    changed = _assign_capability_pointer(scope, payload, event_type=event_type)
    if event_type == "capability.disabled":
        artifact_id = str(payload.get("artifact_id") or "")
        if artifact_id and artifact_id in scope.get("capabilities", {}):
            scope["capabilities"].pop(artifact_id, None)
            changed = True
    return changed


def _load_bundle_artifact(bundle_path: str) -> dict[str, Any] | None:
    """patch_proposed 的工件本体不在 payload；从 bundle 目录尽力恢复。"""
    try:
        path = Path(bundle_path)
        artifact_file = path / "artifact.json" if path.is_dir() else path
        if not artifact_file.exists():
            return None
        data = json.loads(artifact_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _reduce_capability_patch_proposed(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    changed = _assign_capability_pointer(scope, payload, event_type=event_type)
    patched_id = str(payload.get("patched_artifact_id") or "")
    if patched_id and patched_id not in scope.get("capabilities", {}):
        artifact = _load_bundle_artifact(str(payload.get("bundle_path") or ""))
        if artifact and str(artifact.get("artifact_id") or "") == patched_id:
            scope["capabilities"][patched_id] = {
                **artifact,
                "origin": "life_patch",
                "patch_of": str(payload.get("artifact_id") or ""),
            }
            changed = True
    return changed


def _ingest_pointer_outcome(scope: dict[str, Any], *, artifact_id: str, outcome_id: str, outcome: str, event: Mapping[str, Any]) -> bool:
    if outcome not in {"success", "failure"}:
        raise JournalReplayError("life.projection.capability_outcome_invalid")
    occurred_ms = _event_ms(event)
    for lineage_id, pointer in list(scope.get("capability_pointers", {}).items()):
        if not isinstance(pointer, dict) or str(pointer.get("current_artifact_id") or "") != artifact_id:
            continue
        updated, _action, reason = ingest_outcome(
            pointer,
            {
                "outcome_id": outcome_id,
                "artifact_id": artifact_id,
                "outcome": outcome,
                "occurred_at_ms": occurred_ms,
            },
            now_ms=occurred_ms or 1,
        )
        if reason in {"recorded", "request_patch"}:
            scope["capability_pointers"][lineage_id] = updated
            return True
        return False
    return False


def _reduce_capability_executed(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    execution = _require_mapping(payload.get("execution"), "life.projection.capability_execution_invalid")
    execution_id = str(execution.get("execution_id") or "")
    if not execution_id:
        raise JournalReplayError("life.projection.capability_execution_invalid")
    changed = False
    existing = scope.setdefault("executions", {}).get(execution_id)
    if existing is None:
        scope["executions"][execution_id] = deepcopy(execution)
        changed = True
    elif not _same(existing, execution):
        scope["executions"][execution_id] = deepcopy(execution)
        changed = True
    outcome = "success" if str(execution.get("status") or "") == "completed" else "failure"
    changed |= _ingest_pointer_outcome(
        scope,
        artifact_id=str(execution.get("artifact_id") or ""),
        outcome_id=execution_id,
        outcome=outcome,
        event=event,
    )
    return changed


def _reduce_capability_outcome(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    artifact_id = str(payload.get("artifact_id") or "")
    outcome = str(payload.get("outcome") or "")
    outcome_id = str(event.get("idempotency_key") or "").rsplit(":", 1)[-1]
    if not artifact_id or not outcome_id:
        raise JournalReplayError("life.projection.capability_outcome_invalid")
    return _ingest_pointer_outcome(
        scope, artifact_id=artifact_id, outcome_id=outcome_id, outcome=outcome, event=event
    )


# --------------------------------------------------------------------------
# 新增家族：学习卡
# --------------------------------------------------------------------------

def _reduce_learning_card(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    card = _require_mapping(payload.get("learning"), "life.projection.learning_event_invalid")
    learning_id = str(card.get("learning_id") or "")
    if not learning_id:
        raise JournalReplayError("life.projection.learning_event_invalid")
    learning = scope.setdefault("learning", {})
    if not isinstance(learning, dict):
        raise JournalReplayError("life.projection.learning_event_invalid")
    existing = learning.get(learning_id)
    if existing is None or not _same(existing, card):
        learning[learning_id] = deepcopy(card)
        return True
    return False


def _reduce_learning_published(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    changed = _reduce_learning_card(scope, payload, event, event_type=event_type)
    card = payload.get("learning") if isinstance(payload.get("learning"), Mapping) else {}
    artifact = payload.get("artifact")
    execution = card.get("execution") if isinstance(card.get("execution"), Mapping) else {}
    publication = execution.get("publication") if isinstance(execution.get("publication"), Mapping) else {}
    if isinstance(artifact, Mapping) and artifact.get("artifact_id"):
        artifact_id = str(artifact["artifact_id"])
        if str(artifact.get("kind") or "") in {"skill", "tool"}:
            entry = {
                **deepcopy(dict(artifact)),
                "origin": "life_learning",
                "publication": deepcopy(dict(publication)),
            }
        else:
            entry = {
                **deepcopy(dict(artifact)),
                "knowledge_document_id": str(publication.get("knowledge_document_id") or ""),
                "publication": deepcopy(dict(publication)),
            }
        store = scope.setdefault("capabilities" if str(artifact.get("kind") or "") in {"skill", "tool"} else "knowledge", {})
        existing = store.get(artifact_id)
        if existing is None or not _same(existing, entry):
            store[artifact_id] = entry
            changed = True
    return changed


# --------------------------------------------------------------------------
# 新增家族：升级卡
# --------------------------------------------------------------------------

def _reduce_upgrade_created(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    card = _require_mapping(payload.get("upgrade"), "life.projection.upgrade_event_invalid")
    card_id = str(card.get("card_id") or "")
    if not card_id:
        raise JournalReplayError("life.projection.upgrade_event_invalid")
    upgrades = scope.setdefault("upgrades", {})
    existing = upgrades.get(card_id)
    if existing is None or not _same(existing, card):
        upgrades[card_id] = deepcopy(card)
        return True
    return False


def _reduce_upgrade_cancelled(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    card_id = str(payload.get("card_id") or "")
    upgrades = scope.setdefault("upgrades", {})
    if card_id in upgrades:
        upgrades.pop(card_id, None)
        return True
    return False


def _upgrade_card(scope: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    card_id = str(payload.get("card_id") or "")
    card = scope.setdefault("upgrades", {}).get(card_id)
    if not isinstance(card, dict):
        raise JournalReplayError("life.projection.upgrade_card_missing")
    return card


def _reduce_upgrade_confirmed(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    card = _upgrade_card(scope, payload)
    iso = _event_iso(event)
    changed = False
    if card.get("status") != "confirmed":
        card["status"] = "confirmed"
        card["confirmed_at"] = iso
        card["updated_at"] = iso
        changed = True
    elif not card.get("confirmed_at"):
        card["confirmed_at"] = iso
        changed = True
    return changed


def _reduce_upgrade_terminal(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    card = _upgrade_card(scope, payload)
    status = "completed" if event_type == "upgrade.card_completed" else "failed"
    execution = payload.get("execution")
    iso = _event_iso(event)
    changed = False
    if isinstance(execution, Mapping):
        merged = {**(card.get("execution") if isinstance(card.get("execution"), Mapping) else {}), **deepcopy(dict(execution))}
        if not _same(card.get("execution"), merged):
            card["execution"] = merged
            changed = True
    if card.get("status") != status:
        card["status"] = status
        card["updated_at"] = iso
        changed = True
    return changed


# --------------------------------------------------------------------------
# 新增家族：主动消息 / 信箱（可恢复部分，见模块 docstring 的信息缺口）
# --------------------------------------------------------------------------

def _reduce_proactive_delivered(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    scheduler = scope.setdefault("scheduler", {})
    occurred = _event_ms(event)
    changed = False
    if occurred and int(scheduler.get("last_proactive_delivery_at_ms") or 0) != occurred:
        scheduler["last_proactive_delivery_at_ms"] = occurred
        changed = True
    if not scheduler.get("last_proactive_reason"):
        scheduler["last_proactive_reason"] = "life.proactive.native"
        changed = True
    return changed


def _reduce_proactive_acked(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    message_id = str(payload.get("message_id") or "")
    if not message_id:
        raise JournalReplayError("life.projection.proactive_event_invalid")
    occurred = _event_ms(event)
    for row in scope.get("proactive_chats", []):
        if isinstance(row, dict) and str(row.get("message_id") or "") == message_id:
            if not row.get("acked"):
                row["acked"] = True
                row["acked_at_ms"] = occurred
                return True
            return False
    return False


def _reduce_proactive_replied(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    message_id = str(payload.get("message_id") or "")
    if not message_id:
        raise JournalReplayError("life.projection.proactive_event_invalid")
    occurred = _event_ms(event)
    for row in scope.get("proactive_chats", []):
        if isinstance(row, dict) and str(row.get("message_id") or "") == message_id:
            if not row.get("replied"):
                row["replied"] = True
                row["reply_link_kind"] = str(payload.get("reply_link_kind") or "")
                row["replied_at_ms"] = occurred
                row["reply_run_id"] = str(payload.get("reply_run_id") or "")
                return True
            return False
    return False


def _reduce_share_published(scope: dict[str, Any], payload: Mapping[str, Any], event: Mapping[str, Any], *, event_type: str) -> bool:
    scheduler = scope.setdefault("scheduler", {})
    occurred = _event_ms(event)
    changed = False
    if occurred and int(scheduler.get("last_share_at_ms") or 0) != occurred:
        scheduler["last_share_at_ms"] = occurred
        changed = True
    if scheduler.get("last_share_decision_reason") != "life.share.published":
        scheduler["last_share_decision_reason"] = "life.share.published"
        changed = True
    return changed


# --------------------------------------------------------------------------
# 注册表分派
# --------------------------------------------------------------------------

_REDUCERS: dict[str, Callable[..., bool]] = {
    "memory.asserted": _reduce_memory_asserted,
    "memory.corrected": _reduce_memory_asserted,
    "memory.status_changed": _reduce_memory_status,
    "memory.deleted": _reduce_memory_deleted,
    "memory.relation_added": _reduce_memory_relation,
    "memory.candidates_proposed": _reduce_memory_candidates,
    "autonomy.task_generated": _reduce_task_generated,
    "autonomy.task_status_changed": _reduce_task_status,
    "execution.committed": _reduce_execution_committed,
    "capability.activated": _reduce_capability_lifecycle,
    "capability.disabled": _reduce_capability_lifecycle,
    "capability.rolled_back": _reduce_capability_lifecycle,
    "capability.reactivated": _reduce_capability_lifecycle,
    "capability.patch_settled": _reduce_capability_lifecycle,
    "capability.patch_failed": _reduce_capability_lifecycle,
    "capability.patch_proposed": _reduce_capability_patch_proposed,
    "capability.executed": _reduce_capability_executed,
    "capability.outcome": _reduce_capability_outcome,
    "learning.draft_created": _reduce_learning_card,
    "learning.confirmed": _reduce_learning_card,
    "learning.discarded": _reduce_learning_card,
    "learning.published": _reduce_learning_published,
    "upgrade.card_created": _reduce_upgrade_created,
    "upgrade.card_cancelled": _reduce_upgrade_cancelled,
    "upgrade.card_confirmed": _reduce_upgrade_confirmed,
    "upgrade.card_completed": _reduce_upgrade_terminal,
    "upgrade.card_failed": _reduce_upgrade_terminal,
    "life.proactive.delivered": _reduce_proactive_delivered,
    "life.proactive.acked": _reduce_proactive_acked,
    "life.proactive.replied": _reduce_proactive_replied,
    "life.share.published": _reduce_share_published,
}


def replay_event(
    scope: dict[str, Any],
    event: Mapping[str, Any],
    *,
    life_id: str,
    normalize_autonomy_state: Callable,
) -> bool:
    """按注册表重放单个事件；返回投影是否变化。

    未登记类型 fail-closed；audit-only 跳过。
    """
    event_type = str(event.get("event_type") or "")
    classification = EVENT_REGISTRY.get(event_type)
    if classification is None:
        raise JournalReplayError("life.projection.event_unclassified")
    if classification is EventClass.AUDIT_ONLY:
        return False
    reducer = _REDUCERS.get(event_type)
    if reducer is None:
        # 登记为可重放却没有 reducer：注册表自身不完整，fail-closed。
        raise JournalReplayError("life.projection.reducer_missing")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise JournalReplayError("life.projection.event_payload_invalid")
    if event_type in {"autonomy.task_generated", "autonomy.task_status_changed"}:
        return reducer(scope, payload, event, normalize_autonomy_state=normalize_autonomy_state)
    if event_type in {
        "memory.asserted",
        "memory.corrected",
        "memory.status_changed",
        "memory.deleted",
        "memory.relation_added",
        "memory.candidates_proposed",
        "execution.committed",
    }:
        return reducer(scope, payload, event, life_id=life_id)
    return reducer(scope, payload, event, event_type=event_type)


def replay_journal_events(
    scope: dict[str, Any],
    events: list[Mapping[str, Any]],
    *,
    life_id: str,
    normalize_autonomy_state: Callable,
) -> bool:
    """顺序重放全部事件（journal 序保证最终态正确）；返回投影是否变化。"""
    changed = False
    for event in events:
        if replay_event(scope, event, life_id=life_id, normalize_autonomy_state=normalize_autonomy_state):
            changed = True
    return changed


__all__ = [
    "EVENT_REGISTRY",
    "EventClass",
    "JournalReplayError",
    "replay_event",
    "replay_journal_events",
]
