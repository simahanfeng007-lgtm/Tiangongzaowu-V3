from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def patch_life_runtime() -> None:
    rel = "src/life_service/embedded_runtime.py"
    text = read(rel)
    text = replace_once(
        text,
        "from .memory_coordinator import MemoryCoordinator\n",
        "from .memory_coordinator import MemoryCoordinator\nfrom .proactive_initiative import evaluate_proactive_candidate\n",
        "life import proactive gate",
    )
    text = replace_once(
        text,
        "        self._learning_share_writer: Any = None\n        self._world_identity_provider: Any = None\n",
        "        self._learning_share_writer: Any = None\n        self._proactive_decider: Any = None\n        self._proactive_expression_writer: Any = None\n        self._world_identity_provider: Any = None\n",
        "life callbacks",
    )
    text = replace_once(
        text,
        '                        "greeting_inflight",\n',
        '                        "greeting_inflight",\n                        "proactive_decision_inflight",\n',
        "life inflight recovery",
    )
    text = replace_once(
        text,
        '                "share_dnd_end": "08:00",\n                "learned_boundary_rules": [],\n',
        '                "share_dnd_end": "08:00",\n'
        '                # P16 native proactive cognition. Legacy share/greeting settings\n'
        '                # remain compatibility-only and cannot authorize this producer.\n'
        '                "proactive_enabled": True,\n'
        '                "proactive_mode": "shadow",\n'
        '                "proactive_decision_interval_seconds": 900,\n'
        '                "proactive_min_interval_seconds": 3600,\n'
        '                "proactive_max_messages_per_hour": 2,\n'
        '                "proactive_max_messages_per_day": 6,\n'
        '                "proactive_dnd_enabled": False,\n'
        '                "proactive_dnd_start_hour": 22,\n'
        '                "proactive_dnd_end_hour": 7,\n'
        '                "proactive_respect_user_activity": True,\n'
        '                "proactive_user_active_window_seconds": 180,\n'
        '                "proactive_min_evidence_confidence_milli": 350,\n'
        '                "proactive_evidence_stale_after_seconds": 86400,\n'
        '                "proactive_min_utility_lcb_milli": 120,\n'
        '                "proactive_min_margin_milli": 80,\n'
        '                "learned_boundary_rules": [],\n',
        "life proactive defaults",
    )
    text = replace_once(
        text,
        '                "last_share_decision_reason": "",\n',
        '                "last_share_decision_reason": "",\n'
        '                "last_proactive_decision_at_ms": 0,\n'
        '                "proactive_decision_inflight": False,\n'
        '                "last_proactive_delivery_at_ms": 0,\n'
        '                "last_proactive_reason": "",\n'
        '                "last_user_run_id": "",\n',
        "life proactive scheduler state",
    )
    text = replace_once(
        text,
        "            self._schedule_greeting(life_id=life_id)\n            self._cognition_shadow_tick(life_id=life_id)\n",
        "            self._schedule_greeting(life_id=life_id)\n            self._schedule_native_proactive(life_id=life_id)\n            self._cognition_shadow_tick(life_id=life_id)\n",
        "life scheduler wire",
    )
    setter_anchor = "    def set_self_iteration_decider(self, decider: Any) -> None:\n"
    proactive_setters = '''    def set_proactive_decider(self, decider: Any) -> None:\n        \"\"\"Install the gateway-owned model-only P16 initiative decider.\"\"\"\n        if decider is not None and not callable(decider):\n            raise TypeError(\"proactive decider must be callable\")\n        with self._lock:\n            self._proactive_decider = decider\n\n    def set_proactive_expression_writer(self, writer: Any) -> None:\n        \"\"\"Install the normal-dialogue-backed P16 expression writer.\"\"\"\n        if writer is not None and not callable(writer):\n            raise TypeError(\"proactive expression writer must be callable\")\n        with self._lock:\n            self._proactive_expression_writer = writer\n\n'''
    if proactive_setters.strip() not in text:
        text = replace_once(text, setter_anchor, proactive_setters + setter_anchor, "life proactive setters")

    greeting_anchor = "    def _schedule_greeting(self, *, life_id: str) -> None:\n"
    proactive_methods = r'''    @staticmethod
    def _proactive_timestamp_ms(value: object) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        raw = str(value or "").strip()
        if not raw:
            return 0
        try:
            return max(0, int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000))
        except (TypeError, ValueError, OSError):
            return 0

    def _build_proactive_context(self, *, life_id: str, now_ms: int) -> dict[str, Any]:
        """Build a bounded, rebuildable P16 projection from existing authorities."""
        scope = self._scope_state(life_id)
        scheduler = scope.setdefault("scheduler", {})
        observations: list[dict[str, Any]] = []
        memories = scope.get("memories") if isinstance(scope.get("memories"), Mapping) else {}
        for memory_id, row in reversed(list(memories.items())):
            if len(observations) >= 24 or not isinstance(row, Mapping):
                continue
            if str(row.get("status") or "active") != "active":
                continue
            classification = row.get("classification") if isinstance(row.get("classification"), Mapping) else {}
            memory_type = str(classification.get("memory_type") or row.get("memory_type") or "")
            if memory_type not in {"goal", "user_preference", "hard_constraint", "relationship", "causal_summary", "observation"}:
                continue
            observed_at_ms = self._proactive_timestamp_ms(
                row.get("created_at_ms") or row.get("created_at") or ""
            )
            content = json.dumps(row.get("content"), ensure_ascii=False, sort_keys=True)
            if not content.strip():
                continue
            privacy = scope.get("settings", {}).get("privacy") if isinstance(scope.get("settings"), Mapping) else {}
            if isinstance(privacy, Mapping) and bool(privacy.get("redact_llm", True)):
                content = self._redact_sensitive_text(content)
            observations.append({
                "source_ref": f"memory:{memory_id}",
                "observed_at_ms": observed_at_ms,
                "confidence_milli": max(0, min(1000, int(row.get("confidence_milli") or 800))),
                "epistemic_state": "KNOWN" if observed_at_ms else "UNKNOWN",
                "kind": f"memory:{memory_type or 'unknown'}",
                "summary": content[:1600],
            })

        autonomy = self._autonomy_state(life_id)
        task_projection: list[dict[str, Any]] = []
        for task in autonomy.get("tasks", {}).values():
            if len(task_projection) >= 16 or not isinstance(task, Mapping):
                continue
            result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
            task_row = {
                "task_id": str(task.get("task_id") or ""),
                "activity_id": str(task.get("activity_id") or task.get("task_kind") or ""),
                "title": str(task.get("title") or task.get("objective") or "")[:240],
                "status": str(task.get("status") or ""),
                "summary": str(result.get("summary") or "")[:800],
                "updated_at_ms": int(task.get("updated_at_ms") or 0),
            }
            task_projection.append(task_row)
            if task_row["updated_at_ms"] and (task_row["title"] or task_row["summary"]):
                observations.append({
                    "source_ref": f"life-task:{task_row['task_id']}",
                    "observed_at_ms": task_row["updated_at_ms"],
                    "confidence_milli": 1000,
                    "epistemic_state": "KNOWN",
                    "kind": "life_task",
                    "summary": json.dumps(task_row, ensure_ascii=False, sort_keys=True)[:1600],
                })

        deliveries = [
            int(row.get("created_at_ms") or 0)
            for row in scope.get("proactive_chats", [])
            if isinstance(row, Mapping)
            and str(row.get("reason") or "") == "life.proactive.native"
            and int(row.get("created_at_ms") or 0) > 0
        ]
        affect = scope.get("affect") if isinstance(scope.get("affect"), Mapping) else {}
        return {
            "schema": "tiangong.life.initiative-context.v1",
            "life_id": life_id,
            "observed_at_ms": now_ms,
            "authority": "embedded_life_runtime",
            "epistemic_rule": "missing_source_is_UNKNOWN",
            "last_user_activity_at_ms": int(scheduler.get("last_user_activity_at_ms") or 0),
            "last_user_run_id": str(scheduler.get("last_user_run_id") or ""),
            "recent_delivery_times_ms": deliveries[-64:],
            "observations": observations[:40],
            "recent_tasks": task_projection,
            "relationships": deepcopy(scope.get("relationships") or {}),
            "affect": {
                "primary_emotion": str(affect.get("primary_emotion") or "calm"),
                "primary_emotion_zh": str(affect.get("primary_emotion_zh") or "平静"),
                "intensity_milli": int(affect.get("intensity_milli") or 0),
                "expression_directive": str(affect.get("expression_directive") or "")[:800],
            },
        }

    def _schedule_native_proactive(self, *, life_id: str) -> None:
        """Schedule the sole post-P15 proactive producer without blocking heartbeat."""
        scope = self._scope_state(life_id)
        settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
        scheduler = scope.setdefault("scheduler", {})
        now_ms = time.time_ns() // 1_000_000
        if not bool(settings.get("proactive_enabled", True)):
            scheduler["last_proactive_reason"] = "life.proactive.disabled"
            return
        if not callable(self._proactive_decider):
            scheduler["last_proactive_reason"] = "life.proactive.decider_unavailable"
            return
        if scheduler.get("proactive_decision_inflight") is True:
            return
        interval_ms = max(60, int(settings.get("proactive_decision_interval_seconds") or 900)) * 1000
        last_ms = int(scheduler.get("last_proactive_decision_at_ms") or 0)
        if last_ms and now_ms - last_ms < interval_ms:
            return

        budget_day = utc_now()[:10]
        if str(scheduler.get("model_budget_date") or "") != budget_day:
            scheduler.update({
                "model_budget_date": budget_day,
                "model_attempts": 0,
                "model_successes": 0,
                "model_failures": 0,
                "model_timeouts": 0,
                "model_skipped": 0,
            })
        success_limit = max(0, int(settings.get("llm_daily_budget") or 20))
        attempt_limit = max(0, int(settings.get("llm_daily_attempt_budget") or 30))
        if (
            (success_limit and int(scheduler.get("model_successes") or 0) >= success_limit)
            or (attempt_limit and int(scheduler.get("model_attempts") or 0) >= attempt_limit)
        ):
            scheduler["model_skipped"] = int(scheduler.get("model_skipped") or 0) + 1
            scheduler["last_proactive_decision_at_ms"] = now_ms
            scheduler["last_proactive_reason"] = "life.proactive.model_budget_exhausted"
            self._persist(life_id)
            return

        context = self._build_proactive_context(life_id=life_id, now_ms=now_ms)
        scheduler["proactive_decision_inflight"] = True
        scheduler["last_proactive_decision_at_ms"] = now_ms
        scheduler["last_proactive_reason"] = "life.proactive.decision_started"
        scheduler["model_attempts"] = int(scheduler.get("model_attempts") or 0) + 1
        self._persist(life_id)
        slot = now_ms // max(60_000, interval_ms)
        threading.Thread(
            target=self._proactive_worker,
            args=(life_id, context, slot),
            daemon=True,
            name="life-native-proactive",
        ).start()

    def _proactive_worker(self, life_id: str, context: Mapping[str, Any], slot: int) -> None:
        """Run one P16 decision/compose turn; model latency stays off writer lock."""
        proposal: Mapping[str, Any] | None = None
        try:
            value = self._proactive_decider(deepcopy(dict(context)))
            if not isinstance(value, Mapping):
                raise ValueError("proactive model decision is invalid")
            proposal = dict(value)
        except Exception as exc:
            with self._lock:
                scope = self._scope_state(life_id)
                scheduler = scope.setdefault("scheduler", {})
                scheduler["proactive_decision_inflight"] = False
                scheduler["last_proactive_reason"] = "life.proactive.decision_failed"
                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.decision_failed", "error_type": type(exc).__name__},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.decision-failed:{life_id}:{slot}",
                )
                self._persist(life_id)
            return

        with self._lock:
            scope = self._scope_state(life_id)
            settings = deepcopy(scope.get("settings") or {})
            now_ms = time.time_ns() // 1_000_000
            decision = evaluate_proactive_candidate(
                proposal,
                context=context,
                settings=settings,
                now_ms=now_ms,
            )
            scheduler = scope.setdefault("scheduler", {})
            scheduler["last_proactive_reason"] = str(decision.get("reason_code") or "")
            if decision.get("allowed") is not True:
                scheduler["proactive_decision_inflight"] = False
                scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"decision": decision, "context_observed_at_ms": int(context.get("observed_at_ms") or 0)},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.suppressed:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            if str(settings.get("proactive_mode") or "shadow").casefold() != "live":
                scheduler["proactive_decision_inflight"] = False
                scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
                self.system.journal.append(
                    life_id,
                    "life.proactive.decision",
                    {"decision": decision, "delivery": "shadow"},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.shadow:{life_id}:{slot}",
                )
                self._persist(life_id)
                return

        writer = self._proactive_expression_writer
        if not callable(writer):
            expression_result: object = None
        else:
            try:
                expression_result = writer({
                    "schema": "tiangong.life.proactive-expression-material.v1",
                    "life_id": life_id,
                    "decision": deepcopy(decision),
                    "initiative_context": deepcopy(dict(context)),
                })
            except Exception:
                expression_result = None

        if isinstance(expression_result, Mapping):
            text_value = str(expression_result.get("text") or expression_result.get("summary") or "").strip()
            conversation_id = str(expression_result.get("conversation_id") or "")[:240]
        else:
            text_value = str(expression_result or "").strip()
            conversation_id = ""
        text_value = text_value[:4000]

        with self._lock:
            scope = self._scope_state(life_id)
            scheduler = scope.setdefault("scheduler", {})
            scheduler["proactive_decision_inflight"] = False
            if not text_value:
                scheduler["last_proactive_reason"] = "life.proactive.expression_unavailable"
                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.expression_unavailable", "decision": decision},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.compose-failed:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            privacy = scope.get("settings", {}).get("privacy") if isinstance(scope.get("settings"), Mapping) else {}
            if isinstance(privacy, Mapping) and bool(privacy.get("redact_share", True)):
                text_value = self._redact_sensitive_text(text_value)
            initiative_id = "initiative_" + canonical_sha256({
                "domain": "tiangong.life.proactive-initiative.v1",
                "life_id": life_id,
                "slot": int(slot),
                "candidate_kind": decision.get("candidate_kind"),
                "evidence_refs": decision.get("evidence_refs") or [],
            })[:40]
            message_id = "proactive_" + canonical_sha256({"initiative_id": initiative_id})[:40]
            if any(
                isinstance(row, Mapping) and row.get("initiative_id") == initiative_id
                for row in scope.get("proactive_chats", [])
            ):
                scheduler["last_proactive_reason"] = "life.proactive.duplicate"
                self._persist(life_id)
                return
            created_at_ms = time.time_ns() // 1_000_000
            row = {
                "message_id": message_id,
                "initiative_id": initiative_id,
                "text": text_value,
                "created_at": utc_now(),
                "created_at_ms": created_at_ms,
                "reason": "life.proactive.native",
                "candidate_kind": str(decision.get("candidate_kind") or "respond"),
                "trigger_event_refs": list(decision.get("evidence_refs") or [])[:24],
                "conversation_id": conversation_id,
                "acked": False,
                "replied": False,
            }
            scope["proactive_chats"].append(row)
            del scope["proactive_chats"][:-100]
            scheduler["last_proactive_delivery_at_ms"] = created_at_ms
            scheduler["last_proactive_reason"] = "life.proactive.delivered"
            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
            self.system.journal.append(
                life_id,
                "life.proactive.delivered",
                {"message_id": message_id, "initiative_id": initiative_id, "decision": decision},
                actor="life_proactive",
                idempotency_key=f"life.proactive.delivered:{initiative_id}",
            )
            self._persist(life_id)

    def _mark_latest_proactive_replied(
        self,
        *,
        life_id: str,
        user_activity_at_ms: int,
        run_id: str,
    ) -> bool:
        """Link a real later user turn to the latest delivered initiative."""
        scope = self._scope_state(life_id)
        for row in reversed(scope.get("proactive_chats", [])):
            if not isinstance(row, dict):
                continue
            if str(row.get("reason") or "") != "life.proactive.native" or row.get("replied") is True:
                continue
            created_at_ms = int(row.get("created_at_ms") or 0)
            if not created_at_ms or created_at_ms > int(user_activity_at_ms):
                continue
            row["replied"] = True
            row["replied_at_ms"] = int(user_activity_at_ms)
            row["reply_run_id"] = str(run_id or "")[:160]
            initiative_id = str(row.get("initiative_id") or "")
            self.system.journal.append(
                life_id,
                "life.proactive.replied",
                {
                    "initiative_id": initiative_id,
                    "message_id": str(row.get("message_id") or ""),
                    "reply_run_id": row["reply_run_id"],
                },
                actor="user",
                idempotency_key=f"life.proactive.replied:{initiative_id}",
            )
            return True
        return False

'''
    if "def _schedule_native_proactive" not in text:
        text = replace_once(text, greeting_anchor, proactive_methods + greeting_anchor, "life proactive methods")

    text = replace_once(
        text,
        '                    scope.setdefault("scheduler", {})["last_user_activity_at_ms"] = int(\n                        body.get("issued_at_ms") or 0\n                    )\n                    if changed:\n',
        '                    scheduler_state = scope.setdefault("scheduler", {})\n'
        '                    scheduler_state["last_user_activity_at_ms"] = int(body.get("issued_at_ms") or 0)\n'
        '                    scheduler_state["last_user_run_id"] = str(body.get("run_id") or "")[:160]\n'
        '                    replied = self._mark_latest_proactive_replied(\n'
        '                        life_id=life_id,\n'
        '                        user_activity_at_ms=scheduler_state["last_user_activity_at_ms"],\n'
        '                        run_id=scheduler_state["last_user_run_id"],\n'
        '                    )\n'
        '                    if replied:\n'
        '                        self._persist(life_id)\n'
        '                    if changed:\n',
        "life user-turn reply link",
    )

    old_ack = '''                elif verb == "GET" and path == "/api/v1/v3/life/proactive-chat/pending":
                    result = {"ok": True, "messages": [deepcopy(row) for row in self._scope_state()["proactive_chats"] if not row.get("acked")]}
                elif verb == "POST" and path == "/api/v1/v3/life/proactive-chat/ack":
                    message_id = str(body.get("message_id") or "")
                    found = False
                    for row in self._scope_state()["proactive_chats"]:
                        if row.get("message_id") == message_id:
                            row["acked"] = True
                            found = True
                            break
                    self._persist()
                    result = {"ok": True, "message_id": message_id, "found": found}
'''
    new_ack = '''                elif verb == "GET" and path == "/api/v1/v3/life/proactive/status":
                    proactive_scope = self._scope_state()
                    result = {
                        "ok": True,
                        "settings": {key: deepcopy(value) for key, value in proactive_scope["settings"].items() if str(key).startswith("proactive_")},
                        "scheduler": {key: deepcopy(value) for key, value in proactive_scope["scheduler"].items() if "proactive" in str(key)},
                        "pending": sum(1 for row in proactive_scope["proactive_chats"] if not row.get("acked")),
                    }
                elif verb == "GET" and path == "/api/v1/v3/life/proactive-chat/pending":
                    result = {"ok": True, "messages": [deepcopy(row) for row in self._scope_state()["proactive_chats"] if not row.get("acked")]}
                elif verb == "POST" and path == "/api/v1/v3/life/proactive-chat/ack":
                    message_id = str(body.get("message_id") or "")
                    found = False
                    scope = self._scope_state()
                    life_id = str(self._active()["life_id"])
                    for row in scope["proactive_chats"]:
                        if row.get("message_id") == message_id:
                            if row.get("acked") is not True:
                                row["acked"] = True
                                row["acked_at_ms"] = time.time_ns() // 1_000_000
                                initiative_id = str(row.get("initiative_id") or "")
                                if initiative_id:
                                    self.system.journal.append(
                                        life_id,
                                        "life.proactive.acked",
                                        {"initiative_id": initiative_id, "message_id": message_id},
                                        actor="delivery",
                                        idempotency_key=f"life.proactive.acked:{initiative_id}",
                                    )
                            found = True
                            break
                    self._persist(life_id)
                    result = {"ok": True, "message_id": message_id, "found": found}
'''
    text = replace_once(text, old_ack, new_ack, "life proactive ack/status")

    text = replace_once(
        text,
        '                        "share_dnd_end",\n                    }\n',
        '                        "share_dnd_end",\n'
        '                        "proactive_enabled",\n'
        '                        "proactive_mode",\n'
        '                        "proactive_decision_interval_seconds",\n'
        '                        "proactive_min_interval_seconds",\n'
        '                        "proactive_max_messages_per_hour",\n'
        '                        "proactive_max_messages_per_day",\n'
        '                        "proactive_dnd_enabled",\n'
        '                        "proactive_dnd_start_hour",\n'
        '                        "proactive_dnd_end_hour",\n'
        '                        "proactive_respect_user_activity",\n'
        '                        "proactive_user_active_window_seconds",\n'
        '                        "proactive_min_evidence_confidence_milli",\n'
        '                        "proactive_evidence_stale_after_seconds",\n'
        '                        "proactive_min_utility_lcb_milli",\n'
        '                        "proactive_min_margin_milli",\n'
        '                    }\n',
        "life settings allowed",
    )
    text = replace_once(
        text,
        '                    for key in ("share_enabled", "share_quiet_if_user_active"):\n',
        '                    for key in (\n'
        '                        "share_enabled", "share_quiet_if_user_active",\n'
        '                        "proactive_enabled", "proactive_dnd_enabled",\n'
        '                        "proactive_respect_user_activity",\n'
        '                    ):\n',
        "life proactive booleans",
    )
    text = replace_once(
        text,
        '                        "share_daily_limit": (0, 1000),\n                    }\n',
        '                        "share_daily_limit": (0, 1000),\n'
        '                        "proactive_decision_interval_seconds": (60, 86400),\n'
        '                        "proactive_min_interval_seconds": (0, 604800),\n'
        '                        "proactive_max_messages_per_hour": (0, 60),\n'
        '                        "proactive_max_messages_per_day": (0, 1000),\n'
        '                        "proactive_dnd_start_hour": (0, 23),\n'
        '                        "proactive_dnd_end_hour": (0, 23),\n'
        '                        "proactive_user_active_window_seconds": (0, 3600),\n'
        '                        "proactive_min_evidence_confidence_milli": (0, 1000),\n'
        '                        "proactive_evidence_stale_after_seconds": (60, 604800),\n'
        '                        "proactive_min_utility_lcb_milli": (0, 4000),\n'
        '                        "proactive_min_margin_milli": (0, 4000),\n'
        '                    }\n',
        "life proactive integer limits",
    )
    text = replace_once(
        text,
        '                    for key in ("share_dnd_start", "share_dnd_end"):\n',
        '                    if "proactive_mode" in updates and str(updates["proactive_mode"]).casefold() not in {"shadow", "live"}:\n'
        '                        raise EmbeddedLifeError("life.settings.proactive_mode_invalid")\n'
        '                    if "proactive_mode" in updates:\n'
        '                        updates["proactive_mode"] = str(updates["proactive_mode"]).casefold()\n'
        '                    for key in ("share_dnd_start", "share_dnd_end"):\n',
        "life proactive mode validation",
    )
    write(rel, text)


def patch_gateway_runtime() -> None:
    rel = "src/total_gateway/runtime.py"
    text = read(rel)
    anchor = '''                runtime.life_service.set_greeting_writer(write_greeting)\n\n                # Self-iteration reviewer:'''
    replacement = '''                runtime.life_service.set_greeting_writer(write_greeting)\n\n                # P16 native proactive cognition. Decision and expression are\n                # injected in-process through the one 7184 assembly point; Life\n                # retains gating/journal/queue authority and Backend only uses\n                # the existing model/dialogue engine.\n                def decide_proactive_initiative(material: object) -> dict[str, object]:\n                    scoped = dict(material) if isinstance(material, dict) else {}\n                    status, payload, _ = runtime.backend_service.request(\n                        "POST",\n                        "/api/v1/internal/proactive/decision",\n                        {"initiative_context": scoped},\n                        timeout_seconds=120,\n                    )\n                    if status >= 400 or payload.get("ok") is not True:\n                        raise RuntimeError(str(payload.get("error") or "proactive decision failed"))\n                    decision = payload.get("decision")\n                    if not isinstance(decision, dict):\n                        raise RuntimeError("proactive decision is invalid")\n                    return decision\n\n                def write_proactive_expression(material: object) -> dict[str, object]:\n                    scoped = dict(material) if isinstance(material, dict) else {}\n                    status, payload, _ = runtime.backend_service.request(\n                        "POST",\n                        "/api/v1/internal/proactive/compose",\n                        {"material": scoped},\n                        timeout_seconds=120,\n                    )\n                    if status >= 400 or payload.get("ok") is not True:\n                        raise RuntimeError(str(payload.get("error") or "proactive expression failed"))\n                    preview = payload.get("preview")\n                    if not isinstance(preview, dict):\n                        raise RuntimeError("proactive expression is invalid")\n                    return preview\n\n                runtime.life_service.set_proactive_decider(decide_proactive_initiative)\n                runtime.life_service.set_proactive_expression_writer(write_proactive_expression)\n\n                # Self-iteration reviewer:'''
    text = replace_once(text, anchor, replacement, "gateway P16 callbacks")
    write(rel, text)


def patch_backend() -> None:
    rel = "src/total_gateway/embedded_backend.py"
    text = read(rel)
    text = replace_once(
        text,
        "import importlib\nimport json\n",
        "import importlib\nimport json\nfrom copy import deepcopy\n",
        "backend deepcopy import",
    )
    text = replace_once(
        text,
        "        self._p15_memory_recall_provider: Any = None\n",
        "        self._p15_memory_recall_provider: Any = None\n"
        "        self._last_conversation_context: dict[str, Any] = {}\n"
        "        self._last_user_name = \"\"\n"
        "        self._last_user_text = \"\"\n"
        "        self._last_conversation_at_ms = 0\n",
        "backend proactive conversation projection",
    )
    text = replace_once(
        text,
        "        result = self.qiaojie.chuli_duihua(text, user, context)\n",
        "        # P16 keeps only a derived in-process continuity projection. It\n"
        "        is not a second conversation store and is rebuilt by each real\n"
        "        user turn. Proactive compose reads it without persisting a fake\n"
        "        user message.\n"
        "        self._last_conversation_context = deepcopy(context)\n"
        "        self._last_user_name = user\n"
        "        self._last_user_text = text\n"
        "        self._last_conversation_at_ms = int(time.time() * 1000)\n"
        "        result = self.qiaojie.chuli_duihua(text, user, context)\n",
        "backend capture real conversation",
    )
    compose_anchor = "    def _share_compose(self, body: Mapping[str, Any]) -> dict[str, Any]:\n"
    methods = r'''    def _proactive_decision(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Model-only P16 initiative proposal; Life recomputes every gate/score."""
        initiative_context = body.get("initiative_context")
        if not isinstance(initiative_context, Mapping):
            raise ValueError("proactive initiative_context is required")
        encoded = json.dumps(dict(initiative_context), ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 128 * 1024:
            raise ValueError("proactive initiative_context is too large")
        system_prompt = (
            "你是天工生命体的主动沟通候选生成层，不是发送器，也没有执行权限。"
            "只依据 initiative_context 中真实提供的 observations 决定是否值得主动开口。"
            "没有 source_ref 的现实变化一律视为 UNKNOWN；UNKNOWN、过期或低可信信息不能被你补全。"
            "只返回一个 JSON 对象，不要 Markdown。candidate_kind 只能是 respond、ask_user、wait、no_op。"
            "respond/ask_user 必须提供 evidence_refs，且每个 ref 必须逐字来自 observations.source_ref；"
            "expression_intent 只描述想表达什么，不写最终话术，不得声称工具已执行或外部世界已变化。"
            "score 必须包含 goal_gain_milli、viability_gain_milli、information_gain_milli、"
            "relationship_value_milli、resource_cost_milli、expected_harm_milli、"
            "uncertainty_penalty_milli、irreversibility_penalty_milli，均为 0..1000 整数。"
            "证据不足、只是想打招呼、没有新信息或打扰价值大于收益时选 wait/no_op。"
        )
        llm = getattr(self.scheduler, "_zhiming_llm", None)
        if not callable(llm):
            raise RuntimeError("proactive decision model bridge unavailable")
        raw = str(llm(system_prompt, encoded) or "").strip()
        if raw.startswith("[LLM"):
            raise RuntimeError(raw[:240])
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match is None:
            raise ValueError("proactive decision model did not return JSON")
        decision = json.loads(match.group(0))
        if not isinstance(decision, dict):
            raise ValueError("proactive decision model output is invalid")
        return {
            "ok": True,
            "decision": decision,
            "model_output_sha256": __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest(),
        }

    def _proactive_compose(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Express an already-authorized initiative through the normal dialogue voice."""
        material = body.get("material")
        if not isinstance(material, Mapping):
            raise ValueError("proactive compose material is required")
        context = deepcopy(self._last_conversation_context)
        packed_context = self._module._duihua_shangxiawen(
            context,
            self._last_user_text,
        )
        text = self.scheduler.shengcheng_zhudong_biaoda(
            dict(material),
            duihua_shangxiawen=packed_context,
            last_user_text=self._last_user_text,
            user_name=self._last_user_name,
        )
        text = str(text or "").strip()
        if not text:
            raise RuntimeError("proactive dialogue expression returned empty")
        conversation_id = str(
            context.get("conversation_id")
            or context.get("active_session_id")
            or context.get("session_id")
            or ""
        )[:240]
        return {
            "ok": True,
            "preview": {
                "text": text[:4000],
                "conversation_id": conversation_id,
                "source": "normal_dialogue_engine",
            },
        }

'''
    if "def _proactive_decision" not in text:
        text = replace_once(text, compose_anchor, methods + compose_anchor, "backend proactive methods")
    text = replace_once(
        text,
        '''            elif verb == "POST" and path == "/api/v1/internal/share/compose":\n                # Model-only persona copywriting lane; no core lock.\n                result = self._share_compose(body)\n''',
        '''            elif verb == "POST" and path == "/api/v1/internal/proactive/decision":\n                # P16 model-only candidate lane; Life remains the decision authority.\n                result = self._proactive_decision(body)\n            elif verb == "POST" and path == "/api/v1/internal/proactive/compose":\n                # P16 expression lane reuses the normal dialogue engine, with no tools.\n                result = self._proactive_compose(body)\n            elif verb == "POST" and path == "/api/v1/internal/share/compose":\n                # Model-only persona copywriting lane; no core lock.\n                result = self._share_compose(body)\n''',
        "backend proactive request routes",
    )
    write(rel, text)


def patch_zongdiaodu() -> None:
    rel = "app/backend/tiangong-backend/v3/zongdiaodu.py"
    text = read(rel)
    anchor = "    def body_state_snapshot(self, payload: dict | None = None) -> dict:\n"
    method = r'''    def shengcheng_zhudong_biaoda(
        self,
        material: dict[str, Any],
        *,
        duihua_shangxiawen: str = "",
        last_user_text: str = "",
        user_name: str = "",
    ) -> str:
        """P16 model-only proactive expression using the normal dialogue context.

        This is not a user turn: it does not route tools, create a Run, mutate
        conversation history, or claim execution. The Life kernel has already
        decided that speaking is eligible before this method is reached.
        """
        if not isinstance(material, dict):
            raise ValueError("proactive expression material is invalid")
        decision = material.get("decision") if isinstance(material.get("decision"), dict) else {}
        initiative_context = (
            material.get("initiative_context")
            if isinstance(material.get("initiative_context"), dict)
            else {}
        )
        if str(decision.get("candidate_kind") or "") not in {"respond", "ask_user"}:
            return ""
        authoritative_soul = _authoritative_life_soul_prompt(duihua_shangxiawen)
        soul_text = authoritative_soul if authoritative_soul is not None else duqu_soul()
        system_tishi = goujian_system_tishi(
            self.shenti,
            soul_text,
            self._body_settings_for_context(),
        )
        system_tishi = system_tishi.rstrip() + "\n\n" + BIAOXIAN_SYSTEM_PROMPT
        system_tishi += (
            "\n\n[P16 主动表达边界]\n"
            "这是生命体内部已经裁决通过的一次表达，不是用户刚刚发送的新消息。"
            "你只能把 decision.expression_intent 用你平时和该用户对话的自然口吻表达出来；"
            "事实只能来自 initiative_context.observations 和已有最近对话。"
            "不得调用或描述工具调用，不得补全 UNKNOWN 信息，不得声称外部变化、执行成功、"
            "文件修改或用户授权。若证据与表达意图不一致，返回空字符串。"
        )
        body_tishi = goujian_shenti_tishi(
            self.shenti,
            include_legacy_affect=authoritative_soul is None,
        )
        payload = {
            "schema": "tiangong.life.proactive-expression-request.v1",
            "internal_event_not_user_message": True,
            "candidate_kind": decision.get("candidate_kind"),
            "topic": decision.get("topic"),
            "expression_intent": decision.get("expression_intent"),
            "evidence_refs": decision.get("evidence_refs") or [],
            "observations": initiative_context.get("observations") or [],
            "affect": initiative_context.get("affect") or {},
            "last_real_user_text": str(last_user_text or "")[:1600],
            "user_name": str(user_name or "")[:160],
        }
        user_prompt_parts = [body_tishi]
        if duihua_shangxiawen:
            user_prompt_parts.append("[最近真实对话与权威上下文]\n" + duihua_shangxiawen[:24000])
        user_prompt_parts.append(
            "[TIANGONG_LIFE_INITIATIVE_V1]\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            + "\n[/TIANGONG_LIFE_INITIATIVE_V1]\n"
            "直接输出你要对用户说的话；可以包含正常 <biaoxian> 表现标记，但不要输出 JSON、计划或内部说明。"
        )
        llm = getattr(self, "_zhiming_llm", None)
        if not callable(llm):
            raise RuntimeError("normal dialogue model bridge unavailable")
        raw = str(llm(system_tishi, "\n\n".join(user_prompt_parts)) or "").strip()
        if raw.startswith("[LLM"):
            raise RuntimeError(raw[:240])
        lowered = raw.casefold()
        if any(marker in lowered for marker in ("<tool_call", "<function_calls", "<invoke", "<omni_body")):
            return ""
        cleaned = strip_internal_reply_markers(raw).strip()
        cleaned = re.sub(r"```(?:json)?|```", "", cleaned, flags=re.IGNORECASE).strip()
        return cleaned[:4000]

'''
    if "def shengcheng_zhudong_biaoda" not in text:
        text = replace_once(text, anchor, method + anchor, "zongdiaodu proactive expression")
    write(rel, text)


def patch_desktop_routes() -> None:
    rel = "src/total_gateway/desktop_api.py"
    text = read(rel)
    text = replace_once(
        text,
        '    _route("GET", "/api/v1/v3/life/proactive-chat/pending", "life"),\n',
        '    _route("GET", "/api/v1/v3/life/proactive/status", "life"),\n'
        '    _route("GET", "/api/v1/v3/life/proactive-chat/pending", "life"),\n',
        "desktop proactive status route",
    )
    write(rel, text)


def main() -> None:
    patch_life_runtime()
    patch_gateway_runtime()
    patch_backend()
    patch_zongdiaodu()
    patch_desktop_routes()
    print("P16 native proactive patch applied")


if __name__ == "__main__":
    main()
