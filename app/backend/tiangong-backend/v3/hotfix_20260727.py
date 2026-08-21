# -*- coding: utf-8 -*-
"""2026-07-27 热修（运行时猴子补丁）：修复两个 P0 Bug。

本模块由 ``v3/peizhi.py`` 末尾 ``from v3 import hotfix_20260727`` 触发导入。
网关 exe 执行的是 PyInstaller 冻结字节码，无法直接改源码生效；而
``total_gateway/embedded_backend.py`` 的 ``_initialize`` 在启动早期把本活源码
目录插入 ``sys.path[0]`` 并 import ``v3.peizhi``，因此在本模块 import 时对
已冻结加载的 ``total_gateway.*`` / ``life_service.*`` 模块做猴子补丁，是
对冻结代码最小侵入的热修方式。

修复内容：

- Bug A（P0，聊天被 backend.arguments.host_path_forbidden 误杀）：
  ``total_gateway/backend_client.py`` 的 ``_reject_host_paths`` 对
  ``BackendClient.execute()`` 的完整 arguments 无差别递归做主机路径检查。
  而 ``total_gateway/orchestration.py`` 的 ``RequestProcessor.process`` 会把
  最近 12 轮历史消息原文放在 ``recent_messages``、当前用户输入放在 ``text``
  ——历史消息里一旦出现 ``C:\\Users\\...`` 之类路径字样，之后所有聊天都被拒。
  补丁：递归时携带 key 路径上下文，``recent_messages``/``messages``/``text``
  自然语言分支子树（含每条消息的 ``content``）跳过路径检查；分支之外的真正
  工具/文件路径参数仍然严格拒绝（不全局关闭保护）。

- Bug B（P0，生命调度器 journal_idempotency_conflict 导致 not_ready）：
  ``life_service/embedded_runtime.py`` 的
  ``_schedule_autonomous_activity_decision`` 把 ``running`` 状态任务当作可
  启动候选。进程重启后内存 inflight 丢失、任务持久化为 running，被原地重复
  启动（running→running 不递增 attempt_count，但刷新 updated_at_ms），同一
  幂等键 ``autonomy.task.model-start:<task_id>:<attempt>`` 对应不同 payload，
  journal 严格守卫抛 ``journal_idempotency_conflict``，ready_payload 返回
  503 NOT_READY。
  补丁：① eligible 集合排除 ``running``；② 新增
  ``_recover_stale_running_autonomy_tasks``——对超过租约（取决策冷却
  cooldown_ms，默认 600s，与 ``TIANGONG_LIFE_AUTONOMY_DECISION_SECONDS``
  一致）未更新的陈旧 running 任务，先转为 ``blocked``（result 带
  ``life.autonomy.stale_running_recovered`` 恢复标记）并 journal 一条
  ``autonomy.task.recover-stale-running:<task_id>:<attempt>`` 事件，使其按
  正常状态机以新 attempt / 新幂等键重新调度，而不是原地 running→running；
  租约内的新鲜 running 任务不动（可能仍有存活 worker）。补丁在每次决策
  （含 cooldown 提前返回之前）都会执行，不会无限重试：恢复只在状态确为
  running 且超租约时发生一次。

注意：同样的修复语义已同步落到冻结模块的源码镜像
（resources/python/Lib/site-packages/total_gateway/backend_client.py、
life_service/embedded_runtime.py）和源仓库 src/ 对应文件，三处保持一致；
但对已发布 exe 真正生效的是本猴子补丁。

补丁失败只记录日志、绝不阻断网关启动；补丁幂等（重复 import 不会重复打）。
"""
from __future__ import annotations

import os
import re
import threading
import time
from copy import deepcopy
from typing import Mapping

APPLIED: list[str] = []


def _log(message: str) -> None:
    # 网关启动日志走 stdout，print 即可被外层日志收集；flush 保证立即可见。
    print(f"[HOTFIX-20260727] {message}", flush=True)


# --------------------------------------------------------------------------
# Bug A：_reject_host_paths 误杀自然语言消息
# --------------------------------------------------------------------------

def _patch_backend_host_path_guard() -> None:
    from total_gateway import backend_client

    original = getattr(backend_client, "_reject_host_paths", None)
    if original is None:
        _log("Bug A 补丁跳过：backend_client._reject_host_paths 不存在（版本不匹配？）")
        return
    if getattr(original, "_hotfix_20260727", False):
        _log("Bug A 补丁已生效，跳过重复打补丁")
        return

    BackendClientError = backend_client.BackendClientError
    # 自然语言消息分支：orchestration.py 组装的 recent_messages（历史消息
    # 原文）/ messages / text（当前输入）。这些子树里的字符串是用户聊天
    # 内容，出现路径字样属正常，不做主机路径检查；其余 key（如 path、
    # content、options.output 等工具/文件参数）继续严格检查。
    natural_text_keys = frozenset({"messages", "recent_messages", "text"})

    def _reject_host_paths_hotfix(value: object, _in_natural_text: bool = False) -> None:
        if value is None or isinstance(value, (bool, int)):
            return
        if isinstance(value, float):
            raise BackendClientError("backend.arguments.float_forbidden")
        if isinstance(value, str):
            if _in_natural_text:
                return
            normalized = value.replace("\\", "/")
            if (
                re.match(r"^[A-Za-z]:/", normalized)
                or normalized.startswith("//")
                or normalized.startswith("/")
                or normalized.casefold().startswith("file:")
                or ".." in normalized.split("/")
            ):
                raise BackendClientError("backend.arguments.host_path_forbidden")
            return
        if isinstance(value, list):
            for item in value:
                _reject_host_paths_hotfix(item, _in_natural_text)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise BackendClientError("backend.arguments.non_string_key")
                _reject_host_paths_hotfix(item, _in_natural_text or key in natural_text_keys)
            return
        raise BackendClientError("backend.arguments.unsupported_type")

    _reject_host_paths_hotfix._hotfix_20260727 = True  # type: ignore[attr-defined]
    # execute() 通过模块全局名调用 _reject_host_paths，重绑模块属性即生效。
    backend_client._reject_host_paths = _reject_host_paths_hotfix
    APPLIED.append("bugA:total_gateway.backend_client._reject_host_paths")
    _log("Bug A 已修补：recent_messages/messages/text 自然语言分支跳过 host_path 检查，路径参数保护保留")


# --------------------------------------------------------------------------
# Bug B：生命调度器 running 任务幂等冲突
# --------------------------------------------------------------------------

def _patch_life_autonomy_scheduler() -> None:
    from life_service import embedded_runtime as er

    cls = er.EmbeddedLifeRuntime
    original = getattr(cls, "_schedule_autonomous_activity_decision", None)
    if original is None:
        _log("Bug B 补丁跳过：EmbeddedLifeRuntime._schedule_autonomous_activity_decision 不存在（版本不匹配？）")
        return
    if getattr(original, "_hotfix_20260727", False):
        _log("Bug B 补丁已生效，跳过重复打补丁")
        return
    # 源码版本守卫：本补丁只为冻结 exe 里的旧版 life_service 服务。若当前
    # 加载的模块已内置陈旧恢复逻辑且带有后续新增能力（如 _drift_affinity，
    # 2026-08 引入），说明是更新的原生实现——用本补丁覆盖会把漂移排序、
    # 反思链接线等新逻辑整体回退（全量测试下 test_foundation_closeout 导入
    # v3.peizhi 即触发）。原生实现自 2026-07-27 起已包含 Bug B 语义，跳过。
    if hasattr(cls, "_recover_stale_running_autonomy_tasks") and hasattr(cls, "_drift_affinity"):
        APPLIED.append("bugB:skipped-native-source-newer")
        _log("Bug B 跳过：当前 life_service 为更新的原生实现（内置恢复逻辑），猴子补丁不覆盖")
        return

    # 以下别名来自冻结模块本身，保证与冻结版常量/函数完全一致。
    utc_now = er.utc_now
    canonical_json_bytes = er.canonical_json_bytes
    build_activity_scope = er.build_activity_scope
    update_task_status = er.update_task_status
    max_task_result_bytes = er._MAX_TASK_RESULT_BYTES

    def _recover_stale_running_autonomy_tasks(
        self,
        *,
        life_id: str,
        now_ms: int,
        stale_after_ms: int,
    ) -> int:
        """把孤儿的陈旧 running 自主任务转回 blocked。

        进程在 model-start 落 journal 之后、worker 终态更新之前崩溃/中止，
        会留下持久化为 running 的任务，且没有任何 inflight 决策能替它收尾
        （存活 worker 在完成状态更新前一直持有 autonomy_decision_inflight，
        所以走到这里且 inflight 为假时，running 任务必然是孤儿）。原地重新
        选中它会用新 payload 复用已消费的 autonomy.task.model-start 幂等键，
        触发 journal 严格冲突守卫。只有 updated_at_ms 超过 stale_after_ms
        （决策冷却即一次 attempt 的租约）的任务才算陈旧，租约内的新鲜
        running 不动（可能仍有存活 worker）。转为 blocked 后走正常状态机，
        下次启动是新 attempt、新幂等键，而不是 running→running 刷新循环。
        """
        scope = self._scope_state(life_id)
        autonomy = self._autonomy_state(life_id)
        stale_task_ids = [
            str(task.get("task_id") or "")
            for task in autonomy.get("tasks", {}).values()
            if isinstance(task, Mapping)
            and str(task.get("status") or "") == "running"
            and now_ms - int(task.get("updated_at_ms") or task.get("created_at_ms") or 0)
            >= int(stale_after_ms)
        ]
        recovered_count = 0
        for task_id in stale_task_ids:
            before = deepcopy(autonomy)
            try:
                recovered = update_task_status(
                    autonomy,
                    task_id=task_id,
                    status="blocked",
                    now_ms=now_ms,
                    result={
                        "reason_code": "life.autonomy.stale_running_recovered",
                        "recovered_at_ms": now_ms,
                    },
                )
                self.system.journal.append(
                    life_id,
                    "autonomy.task_status_changed",
                    {"task_id": task_id, "task": recovered},
                    actor="life_autonomy",
                    idempotency_key=(
                        f"autonomy.task.recover-stale-running:{task_id}:"
                        f"{recovered.get('attempt_count', 0)}"
                    ),
                )
                self._persist(life_id)
            except Exception:
                scope["autonomy"] = before
                raise
            recovered_count += 1
        if recovered_count:
            _log(f"Bug B 恢复：{recovered_count} 个陈旧 running 任务已转为 blocked（life_id={life_id}）")
        return recovered_count

    def _schedule_autonomous_activity_decision(self, *, life_id: str) -> None:
        """Execute one catalog activity through the gateway model bridge.

        Only catalog-defined, internal A0/A1 work is eligible here.  Tool use,
        file mutation, messaging and all other external effects remain outside
        this method and must use the normal Gateway authorization chain.
        """
        scope = self._scope_state(life_id)
        scheduler = scope.setdefault("scheduler", {})
        autonomy = self._autonomy_state(life_id)
        if not autonomy.get("enabled") or not callable(getattr(self, "_autonomy_decider", None)):
            return
        if bool(scheduler.get("autonomy_decision_inflight")):
            return
        now_ms = time.time_ns() // 1_000_000
        cooldown_ms = max(
            60_000,
            int(float(os.environ.get("TIANGONG_LIFE_AUTONOMY_DECISION_SECONDS") or 600) * 1000),
        )
        # 选取候选之前先恢复孤儿陈旧 running：让它们以 blocked + 新
        # attempt / 新幂等键重新进入调度，而不是原地冲突。
        self._recover_stale_running_autonomy_tasks(
            life_id=life_id,
            now_ms=now_ms,
            stale_after_ms=cooldown_ms,
        )
        if now_ms - int(scheduler.get("last_autonomy_decision_at_ms") or 0) < cooldown_ms:
            return
        budget_day = utc_now()[:10]
        if str(scheduler.get("model_budget_date") or "") != budget_day:
            scheduler.update(
                {
                    "model_budget_date": budget_day,
                    "model_attempts": 0,
                    "model_successes": 0,
                    "model_failures": 0,
                    "model_timeouts": 0,
                    "model_skipped": 0,
                }
            )
        settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
        permission_mode = str(settings.get("permission_mode") or "confirm_high_risk")
        if permission_mode == "confirm_all":
            scheduler["last_autonomy_decision_error"] = "life.autonomy.user_confirmation_required"
            return
        risk_rank = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}
        configured_risk = str(settings.get("autonomous_risk_max") or "A4")
        configured_risk_rank = risk_rank.get(configured_risk, 0)
        success_limit = max(0, int(settings.get("llm_daily_budget") or 20))
        attempt_limit = max(0, int(settings.get("llm_daily_attempt_budget") or 30))
        if (
            (success_limit and int(scheduler.get("model_successes") or 0) >= success_limit)
            or (attempt_limit and int(scheduler.get("model_attempts") or 0) >= attempt_limit)
        ):
            scheduler["model_skipped"] = int(scheduler.get("model_skipped") or 0) + 1
            scheduler["last_autonomy_decision_at_ms"] = now_ms
            scheduler["last_autonomy_decision_error"] = "life.autonomy.model_budget_exhausted"
            self._persist(life_id)
            return
        # 修复点①：eligible 不再包含 running（原冻结代码为
        # {"pending", "blocked", "running"}），running 永不被当作可启动候选。
        eligible = [
            task for task in autonomy.get("tasks", {}).values()
            if isinstance(task, Mapping)
            and str(task.get("status") or "") in {"pending", "blocked"}
            and str(task.get("source") or "") == "life_activity_catalog"
            and str(task.get("risk_class") or "") in {"A0", "A1"}
            and risk_rank.get(str(task.get("risk_class") or ""), 99) <= configured_risk_rank
            and task.get("requires_user") is not True
        ]
        if not eligible:
            return
        eligible.sort(
            key=lambda task: (
                -int(task.get("priority") or 0),
                int(task.get("sequence") or 0),
                str(task.get("task_id") or ""),
            )
        )
        task_id = str(eligible[0].get("task_id") or "")
        before = deepcopy(autonomy)
        try:
            running = update_task_status(autonomy, task_id=task_id, status="running", now_ms=now_ms)
            self.system.journal.append(
                life_id,
                "autonomy.task_status_changed",
                {"task_id": task_id, "task": running},
                actor="life_autonomy",
                idempotency_key=f"autonomy.task.model-start:{task_id}:{running.get('attempt_count', 0)}",
            )
            scheduler["autonomy_decision_inflight"] = True
            scheduler["last_autonomy_decision_at_ms"] = now_ms
            scheduler["last_autonomy_decision_error"] = ""
            scheduler["model_attempts"] = int(scheduler.get("model_attempts") or 0) + 1
            self._persist(life_id)
        except Exception:
            scope["autonomy"] = before
            raise

        def worker() -> None:
            try:
                with self._lock:
                    activity_scope = build_activity_scope(
                        life_id=life_id,
                        soul=self._soul(),
                        scope=self._scope_state(life_id),
                    )
                    task = deepcopy(self._autonomy_state(life_id)["tasks"][task_id])
                decision = self._autonomy_decider(activity_scope, task)
                if not isinstance(decision, Mapping):
                    raise ValueError("autonomy model result is invalid")
                result = deepcopy(dict(decision))
                if len(canonical_json_bytes(result)) > max_task_result_bytes:
                    raise ValueError("autonomy model result is too large")
                summary = str(result.get("summary") or result.get("outcome") or "").strip()
                if not summary:
                    raise ValueError("autonomy model result has no summary")
                result["summary"] = summary[:4000]
                result["activity_id"] = str(task.get("activity_id") or "")
                result["execution_scope"] = "internal_life_state"
                result["external_side_effects"] = False
                with self._lock:
                    if self._closed or self._closing or not self._lease.active:
                        return
                    current = self._autonomy_state(life_id)["tasks"].get(task_id)
                    if not isinstance(current, Mapping) or str(current.get("status") or "") != "running":
                        return
                    completed = update_task_status(
                        self._autonomy_state(life_id),
                        task_id=task_id,
                        status="completed",
                        result=result,
                    )
                    scheduler_state = self._scope_state(life_id).setdefault("scheduler", {})
                    scheduler_state["model_successes"] = int(
                        scheduler_state.get("model_successes") or 0
                    ) + 1
                    self.system.journal.append(
                        life_id,
                        "autonomy.task_status_changed",
                        {"task_id": task_id, "task": completed},
                        actor="life_autonomy",
                        idempotency_key=f"autonomy.task.model-complete:{task_id}:{completed.get('attempt_count', 0)}",
                    )
                    self._sync_daily_summary(life_id)
                    self._persist(life_id)
            except Exception as exc:
                with self._lock:
                    if self._closed or self._closing or not self._lease.active:
                        return
                    current = self._autonomy_state(life_id)["tasks"].get(task_id)
                    if isinstance(current, Mapping) and str(current.get("status") or "") == "running":
                        blocked = update_task_status(
                            self._autonomy_state(life_id),
                            task_id=task_id,
                            status="blocked",
                            result={
                                "reason_code": "life.autonomy.model_activity_failed",
                                "error_type": type(exc).__name__,
                            },
                        )
                        self.system.journal.append(
                            life_id,
                            "autonomy.task_status_changed",
                            {"task_id": task_id, "task": blocked},
                            actor="life_autonomy",
                            idempotency_key=f"autonomy.task.model-blocked:{task_id}:{blocked.get('attempt_count', 0)}",
                        )
                    scheduler_state = self._scope_state(life_id).setdefault("scheduler", {})
                    scheduler_state["last_autonomy_decision_error"] = (
                        f"life.autonomy.model_activity_failed:{type(exc).__name__}"
                    )
                    scheduler_state["model_failures"] = int(
                        scheduler_state.get("model_failures") or 0
                    ) + 1
                    if isinstance(exc, TimeoutError):
                        scheduler_state["model_timeouts"] = int(
                            scheduler_state.get("model_timeouts") or 0
                        ) + 1
                    self._persist(life_id)
            finally:
                with self._lock:
                    if not self._closed and not self._closing and self._lease.active:
                        self._scope_state(life_id).setdefault("scheduler", {})[
                            "autonomy_decision_inflight"
                        ] = False
                        self._persist(life_id)

        threading.Thread(
            target=worker,
            name="tiangong-life-autonomy-decision",
            daemon=True,
        ).start()

    _schedule_autonomous_activity_decision._hotfix_20260727 = True  # type: ignore[attr-defined]
    cls._recover_stale_running_autonomy_tasks = _recover_stale_running_autonomy_tasks
    cls._schedule_autonomous_activity_decision = _schedule_autonomous_activity_decision
    APPLIED.append("bugB:life_service.embedded_runtime.EmbeddedLifeRuntime._schedule_autonomous_activity_decision")
    _log("Bug B 已修补：eligible 排除 running；陈旧 running（超过决策冷却租约）恢复为 blocked 后以新 attempt/新幂等键调度")


def _apply_all() -> None:
    for name, patch in (
        ("Bug A", _patch_backend_host_path_guard),
        ("Bug B", _patch_life_autonomy_scheduler),
    ):
        try:
            patch()
        except Exception as exc:  # 补丁失败绝不阻断网关启动
            _log(f"{name} 补丁应用失败（已忽略，不影响启动）：{type(exc).__name__}: {exc}")
    _log(f"热修模块加载完成，已应用补丁：{APPLIED or '无'}")


_apply_all()
