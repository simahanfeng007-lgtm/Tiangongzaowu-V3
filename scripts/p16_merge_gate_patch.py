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
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def patch_runtime() -> None:
    rel = "src/life_service/embedded_runtime.py"
    text = read(rel)

    text = replace_once(
        text,
        "from .memory_coordinator import MemoryCoordinator\nfrom .proactive_initiative import evaluate_proactive_candidate\n",
        "from .memory_coordinator import MemoryCoordinator\nfrom .memory_context import select_layered_memories\nfrom .proactive_initiative import evaluate_proactive_candidate\n",
        "P15 selector import",
    )

    text = replace_once(
        text,
        '                "proactive_mode": "shadow",\n                "proactive_decision_interval_seconds": 900,\n',
        '                "proactive_mode": "shadow",\n'
        '                # P16 has a sub-budget in addition to the existing global Life LLM cap.\n'
        '                "proactive_llm_daily_budget": 6,\n'
        '                "proactive_llm_daily_attempt_budget": 8,\n'
        '                "proactive_decision_interval_seconds": 900,\n',
        "proactive subbudget defaults",
    )
    text = replace_once(
        text,
        '                "proactive_min_margin_milli": 80,\n                "learned_boundary_rules": [],\n',
        '                "proactive_min_margin_milli": 80,\n'
        '                "proactive_reply_link_window_seconds": 21600,\n'
        '                "learned_boundary_rules": [],\n',
        "reply window default",
    )
    text = replace_once(
        text,
        '                "last_proactive_reason": "",\n                "last_user_run_id": "",\n',
        '                "last_proactive_reason": "",\n'
        '                "proactive_model_budget_date": "",\n'
        '                "proactive_model_attempts": 0,\n'
        '                "proactive_model_successes": 0,\n'
        '                "proactive_model_failures": 0,\n'
        '                "proactive_model_timeouts": 0,\n'
        '                "proactive_model_skipped": 0,\n'
        '                "last_user_run_id": "",\n',
        "proactive subbudget scheduler fields",
    )

    start_marker = '        observations: list[dict[str, Any]] = []\n        memories = scope.get("memories") if isinstance(scope.get("memories"), Mapping) else {}\n'
    start = text.index(start_marker)
    end = text.index("        autonomy = self._autonomy_state(life_id)\n", start)
    memory_block = '''        observations: list[dict[str, Any]] = []\n        # P15 Memory SSoT is the only proactive memory read authority.  Never\n        # fall back to the legacy scope["memories"] projection: that would\n        # bypass derivation invalidation, expiry, privacy scope and lineage dedupe.\n        try:\n            store = self._contract_store()\n            instruction_items, data_items, evidence_items, _skipped = select_layered_memories(\n                store,\n                life_id=life_id,\n                principal_ref=life_id,\n                privacy_scope="private",\n                now_ms=now_ms,\n                limit=24,\n            )\n            layered_items = instruction_items + data_items + evidence_items\n        except Exception:\n            layered_items = ()\n            store = None\n        privacy = scope.get("settings", {}).get("privacy") if isinstance(scope.get("settings"), Mapping) else {}\n        for item in layered_items:\n            if store is None:\n                break\n            derivation = store.get_memory_derivation(item.derivation_id)\n            if derivation is None or derivation.context_eligible is not True:\n                continue\n            assertion = store.get_memory_assertion(derivation.memory_id, derivation.memory_revision)\n            if assertion is None or assertion.lifecycle_status != "active":\n                continue\n            summary = str(item.summary or "")[:1600]\n            if isinstance(privacy, Mapping) and bool(privacy.get("redact_llm", True)):\n                summary = self._redact_sensitive_text(summary)\n            if not summary.strip():\n                continue\n            observations.append({\n                "source_ref": f"memory:{item.derivation_id}",\n                "observed_at_ms": int(derivation.created_at_ms),\n                "confidence_milli": max(0, min(1000, int(assertion.verification_strength_milli))),\n                "epistemic_state": "KNOWN",\n                "kind": f"memory:{str(item.semantic_domain).casefold()}",\n                "authority": "memory_context_layered",\n                "memory_section": str(item.section),\n                "summary": summary,\n            })\n\n'''
    text = text[:start] + memory_block + text[end:]

    budget_start = text.index("    def _reset_proactive_model_budget_if_needed(self, scheduler: dict[str, Any]) -> None:\n")
    budget_end = text.index("    def _schedule_native_proactive(self, *, life_id: str) -> None:\n", budget_start)
    budget_block = '''    def _reset_proactive_model_budget_if_needed(self, scheduler: dict[str, Any]) -> None:\n        budget_day = utc_now()[:10]\n        # Preserve the existing global Life-model hard cap.\n        if str(scheduler.get("model_budget_date") or "") != budget_day:\n            scheduler.update({\n                "model_budget_date": budget_day,\n                "model_attempts": 0,\n                "model_successes": 0,\n                "model_failures": 0,\n                "model_timeouts": 0,\n                "model_skipped": 0,\n            })\n        # A smaller proactive pool prevents shadow/live initiative from starving\n        # autonomy, learning or self-iteration out of the global budget.\n        if str(scheduler.get("proactive_model_budget_date") or "") != budget_day:\n            scheduler.update({\n                "proactive_model_budget_date": budget_day,\n                "proactive_model_attempts": 0,\n                "proactive_model_successes": 0,\n                "proactive_model_failures": 0,\n                "proactive_model_timeouts": 0,\n                "proactive_model_skipped": 0,\n            })\n\n    def _reserve_proactive_model_call_locked(\n        self,\n        *,\n        scheduler: dict[str, Any],\n        settings: Mapping[str, Any],\n    ) -> bool:\n        """Reserve one real LLM call against both global and proactive pools."""\n        self._reset_proactive_model_budget_if_needed(scheduler)\n        global_success_limit = max(0, int(settings.get("llm_daily_budget") or 20))\n        global_attempt_limit = max(0, int(settings.get("llm_daily_attempt_budget") or 30))\n        proactive_success_limit = max(0, int(settings.get("proactive_llm_daily_budget") or 6))\n        proactive_attempt_limit = max(0, int(settings.get("proactive_llm_daily_attempt_budget") or 8))\n        exhausted = (\n            (global_success_limit and int(scheduler.get("model_successes") or 0) >= global_success_limit)\n            or (global_attempt_limit and int(scheduler.get("model_attempts") or 0) >= global_attempt_limit)\n            or (proactive_success_limit and int(scheduler.get("proactive_model_successes") or 0) >= proactive_success_limit)\n            or (proactive_attempt_limit and int(scheduler.get("proactive_model_attempts") or 0) >= proactive_attempt_limit)\n        )\n        if exhausted:\n            scheduler["model_skipped"] = int(scheduler.get("model_skipped") or 0) + 1\n            scheduler["proactive_model_skipped"] = int(scheduler.get("proactive_model_skipped") or 0) + 1\n            return False\n        scheduler["model_attempts"] = int(scheduler.get("model_attempts") or 0) + 1\n        scheduler["proactive_model_attempts"] = int(scheduler.get("proactive_model_attempts") or 0) + 1\n        return True\n\n'''
    text = text[:budget_start] + budget_block + text[budget_end:]

    old_schedule = '''        if not self._reserve_proactive_model_call_locked(scheduler=scheduler, settings=settings):\n            scheduler["last_proactive_decision_at_ms"] = now_ms\n            scheduler["last_proactive_reason"] = "life.proactive.model_budget_exhausted"\n            self._persist(life_id)\n            return\n\n        context = self._build_proactive_context(life_id=life_id, now_ms=now_ms)\n        scheduler["proactive_decision_inflight"] = True\n'''
    new_schedule = '''        try:\n            context = self._build_proactive_context(life_id=life_id, now_ms=now_ms)\n        except Exception as exc:\n            scheduler["last_proactive_decision_at_ms"] = now_ms\n            scheduler["last_proactive_reason"] = "life.proactive.context_unavailable"\n            self.system.journal.append(\n                life_id,\n                "life.proactive.suppressed",\n                {"reason_code": "life.proactive.context_unavailable", "error_type": type(exc).__name__},\n                actor="life_proactive",\n                idempotency_key=f"life.proactive.context-unavailable:{life_id}:{now_ms // max(60_000, interval_ms)}",\n            )\n            self._persist(life_id)\n            return\n        if not context.get("observations"):\n            scheduler["last_proactive_decision_at_ms"] = now_ms\n            scheduler["last_proactive_reason"] = "life.proactive.evidence_missing"\n            self._persist(life_id)\n            return\n        if not self._reserve_proactive_model_call_locked(scheduler=scheduler, settings=settings):\n            scheduler["last_proactive_decision_at_ms"] = now_ms\n            scheduler["last_proactive_reason"] = "life.proactive.model_budget_exhausted"\n            self._persist(life_id)\n            return\n\n        scheduler["proactive_decision_inflight"] = True\n'''
    text = replace_once(text, old_schedule, new_schedule, "context before budget")

    text = replace_once(
        text,
        '                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1\n                self.system.journal.append(\n                    life_id,\n                    "life.proactive.suppressed",\n                    {"reason_code": "life.proactive.decision_failed",',
        '                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1\n'
        '                scheduler["proactive_model_failures"] = int(scheduler.get("proactive_model_failures") or 0) + 1\n'
        '                self.system.journal.append(\n                    life_id,\n                    "life.proactive.suppressed",\n                    {"reason_code": "life.proactive.decision_failed",',
        "decision failure subcounter",
    )
    text = replace_once(
        text,
        '            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1\n            now_ms = time.time_ns() // 1_000_000\n',
        '            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1\n'
        '            scheduler["proactive_model_successes"] = int(scheduler.get("proactive_model_successes") or 0) + 1\n'
        '            now_ms = time.time_ns() // 1_000_000\n',
        "decision success subcounter",
    )
    text = replace_once(
        text,
        '                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1\n                self.system.journal.append(\n                    life_id,\n                    "life.proactive.suppressed",\n                    {"reason_code": "life.proactive.expression_unavailable", "decision": decision},',
        '                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1\n'
        '                scheduler["proactive_model_failures"] = int(scheduler.get("proactive_model_failures") or 0) + 1\n'
        '                self.system.journal.append(\n                    life_id,\n                    "life.proactive.suppressed",\n                    {"reason_code": "life.proactive.expression_unavailable", "decision": decision},',
        "expression failure subcounter",
    )
    text = replace_once(
        text,
        '            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1\n            privacy = scope.get("settings", {}).get("privacy") if isinstance(scope.get("settings"), Mapping) else {}\n',
        '            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1\n'
        '            scheduler["proactive_model_successes"] = int(scheduler.get("proactive_model_successes") or 0) + 1\n'
        '            privacy = scope.get("settings", {}).get("privacy") if isinstance(scope.get("settings"), Mapping) else {}\n',
        "expression success subcounter",
    )

    reply_old = '''        scope = self._scope_state(life_id)\n        for row in reversed(scope.get("proactive_chats", [])):\n'''
    reply_new = '''        scope = self._scope_state(life_id)\n        settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}\n        reply_window_ms = max(60, int(settings.get("proactive_reply_link_window_seconds") or 21600)) * 1000\n        for row in reversed(scope.get("proactive_chats", [])):\n'''
    text = replace_once(text, reply_old, reply_new, "reply link window setup")
    text = replace_once(
        text,
        '            if not created_at_ms or created_at_ms > int(user_activity_at_ms):\n                continue\n            row["replied"] = True\n',
        '            if not created_at_ms or created_at_ms > int(user_activity_at_ms):\n                continue\n'
        '            if int(user_activity_at_ms) - created_at_ms > reply_window_ms:\n'
        '                return False\n'
        '            row["replied"] = True\n'
        '            row["reply_link_kind"] = "first_user_turn_after_delivery"\n',
        "reply link window enforcement",
    )
    text = replace_once(
        text,
        '                    "reply_run_id": row["reply_run_id"],\n',
        '                    "reply_run_id": row["reply_run_id"],\n'
        '                    "reply_link_kind": row["reply_link_kind"],\n',
        "reply audit semantics",
    )

    allow_old = '                        "proactive_mode",\n                        "proactive_decision_interval_seconds",\n'
    allow_new = '                        "proactive_mode",\n                        "proactive_llm_daily_budget",\n                        "proactive_llm_daily_attempt_budget",\n                        "proactive_decision_interval_seconds",\n'
    text = replace_once(text, allow_old, allow_new, "settings proactive budget allowlist")
    text = replace_once(
        text,
        '                        "proactive_min_margin_milli",\n',
        '                        "proactive_min_margin_milli",\n                        "proactive_reply_link_window_seconds",\n',
        "settings reply window allowlist",
    )
    limits_old = '                        "proactive_decision_interval_seconds": (60, 86400),\n'
    limits_new = '                        "proactive_llm_daily_budget": (0, 1000),\n                        "proactive_llm_daily_attempt_budget": (0, 2000),\n                        "proactive_decision_interval_seconds": (60, 86400),\n'
    text = replace_once(text, limits_old, limits_new, "proactive budget limits")
    text = replace_once(
        text,
        '                        "proactive_min_margin_milli": (0, 4000),\n',
        '                        "proactive_min_margin_milli": (0, 4000),\n                        "proactive_reply_link_window_seconds": (60, 604800),\n',
        "reply window limit",
    )

    write(rel, text)


def patch_tests() -> None:
    rel = "tests/test_p16_native_proactive_runtime.py"
    text = read(rel)
    if "from unittest import mock\n" not in text:
        text = replace_once(text, "from pathlib import Path\n", "from pathlib import Path\nfrom unittest import mock\n", "mock import")

    text = replace_once(
        text,
        '            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 30})\n',
        '            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 30, "proactive_llm_daily_budget": 6, "proactive_llm_daily_attempt_budget": 8})\n',
        "two-call budget settings",
    )
    text = replace_once(
        text,
        '            assert scheduler["model_attempts"] == 2\n            assert scheduler["model_successes"] == 2\n            assert scheduler["model_failures"] == 0\n',
        '            assert scheduler["model_attempts"] == 2\n            assert scheduler["model_successes"] == 2\n            assert scheduler["model_failures"] == 0\n            assert scheduler["proactive_model_attempts"] == 2\n            assert scheduler["proactive_model_successes"] == 2\n',
        "two-call subbudget assertions",
    )
    text = replace_once(
        text,
        '            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 1})\n',
        '            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 1, "proactive_llm_daily_budget": 6, "proactive_llm_daily_attempt_budget": 8})\n',
        "expression exhausted budget settings",
    )

    if "test_proactive_context_uses_p15_layered_memory_authority_only" not in text:
        text += r'''


def test_proactive_context_uses_p15_layered_memory_authority_only():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            # A legacy row must never be a fallback source for P16 cognition.
            life._scope_state(life_id)["memories"] = {
                "legacy-secret": {
                    "status": "active",
                    "memory_type": "goal",
                    "created_at_ms": NOW - 1_000,
                    "confidence_milli": 1000,
                    "content": "LEGACY-MEMORY-MUST-NOT-LEAK",
                }
            }
            with mock.patch(
                "life_service.embedded_runtime.select_layered_memories",
                return_value=((), (), (), 0),
            ) as selector:
                context = life._build_proactive_context(life_id=life_id, now_ms=NOW)
            selector.assert_called_once_with(
                life._contract_store(),
                life_id=life_id,
                principal_ref=life_id,
                privacy_scope="private",
                now_ms=NOW,
                limit=24,
            )
            assert "LEGACY-MEMORY-MUST-NOT-LEAK" not in repr(context)
            source = (Path(__file__).resolve().parents[1] / "src" / "life_service" / "embedded_runtime.py").read_text(encoding="utf-8")
            block = source.split("def _build_proactive_context", 1)[1].split("\n    def ", 1)[0]
            assert 'scope.get("memories")' not in block
            assert "select_layered_memories(" in block
        finally:
            life.close()


def test_context_failure_does_not_spend_model_budget_or_break_scheduler():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="shadow")
            life.set_proactive_decider(lambda _context: proposal())
            life_id = str(life._active()["life_id"])
            scheduler = life._scope_state(life_id)["scheduler"]
            before = int(scheduler.get("model_attempts") or 0)
            with mock.patch.object(life, "_build_proactive_context", side_effect=RuntimeError("context failed")):
                life._schedule_native_proactive(life_id=life_id)
            assert int(scheduler.get("model_attempts") or 0) == before
            assert int(scheduler.get("proactive_model_attempts") or 0) == 0
            assert scheduler["proactive_decision_inflight"] is False
            assert scheduler["last_proactive_reason"] == "life.proactive.context_unavailable"
        finally:
            life.close()


def test_proactive_subbudget_prevents_shadow_from_starving_global_pool():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            scope = life._scope_state()
            scope["settings"].update({
                "llm_daily_budget": 20,
                "llm_daily_attempt_budget": 30,
                "proactive_llm_daily_budget": 6,
                "proactive_llm_daily_attempt_budget": 1,
            })
            scheduler = scope["scheduler"]
            scheduler.update({
                "model_budget_date": "",
                "model_attempts": 0,
                "model_successes": 0,
                "model_skipped": 0,
                "proactive_model_budget_date": "",
                "proactive_model_attempts": 0,
                "proactive_model_successes": 0,
                "proactive_model_skipped": 0,
            })
            assert life._reserve_proactive_model_call_locked(scheduler=scheduler, settings=scope["settings"]) is True
            assert life._reserve_proactive_model_call_locked(scheduler=scheduler, settings=scope["settings"]) is False
            assert scheduler["model_attempts"] == 1
            assert scheduler["proactive_model_attempts"] == 1
            assert scheduler["proactive_model_skipped"] == 1
        finally:
            life.close()


def test_reply_lineage_expires_after_bounded_temporal_window():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            scope = life._scope_state()
            scope["settings"]["proactive_reply_link_window_seconds"] = 3600
            life.set_proactive_decider(lambda _context: proposal())
            life.set_proactive_expression_writer(lambda _material: {"text": "还需要继续吗？"})
            life_id = str(life._active()["life_id"])
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            row = scope["proactive_chats"][0]
            row["created_at_ms"] = NOW - 3_600_001
            linked = life._mark_latest_proactive_replied(
                life_id=life_id,
                user_activity_at_ms=NOW,
                run_id="run-too-late",
            )
            assert linked is False
            assert row["replied"] is False
        finally:
            life.close()
'''
    write(rel, text)


if __name__ == "__main__":
    patch_runtime()
    patch_tests()
    print("P16 merge-gate patch applied")
