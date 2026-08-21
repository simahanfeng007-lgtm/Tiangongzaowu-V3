from __future__ import annotations

import re
from pathlib import Path


EMBEDDED = Path("src/life_service/embedded_runtime.py")
STORE = Path("src/life_service/store.py")
TEST = Path("tests/test_life_qc_regressions_20260821.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex match, got {count}")
    return updated


def patch_store() -> None:
    text = STORE.read_text(encoding="utf-8")
    pattern = r'''    def open_causal_episodes\(\n        self, life_id: str, \*, limit: int = 32\n    \) -> tuple\[CausalEpisode, \.\.\.\]:\n.*?(?=\n    def _verify_causal_memory_state)'''
    replacement = '''    def open_causal_episodes(\n        self, life_id: str, *, limit: int = 32, offset: int = 0\n    ) -> tuple[CausalEpisode, ...]:\n        """Episodes whose latest revision is still OPEN, oldest first.\n\n        Runtime registries are bounded caches, not authorities. ``offset``\n        allows recovery to page the complete authoritative OPEN set.\n        """\n        bounded_limit = max(1, min(int(limit), 256))\n        bounded_offset = max(0, int(offset))\n        rows = self._connection.execute(\n            """\n            SELECT e.payload FROM causal_episodes AS e\n            JOIN (\n                SELECT episode_id, MAX(revision) AS max_revision\n                FROM causal_episodes\n                WHERE life_id = ?\n                GROUP BY episode_id\n            ) AS latest\n              ON latest.episode_id = e.episode_id\n             AND latest.max_revision = e.revision\n            WHERE e.life_id = ? AND e.terminal_status = 'OPEN'\n            ORDER BY e.created_at_ms, e.episode_id\n            LIMIT ? OFFSET ?\n            """,\n            (life_id, life_id, bounded_limit, bounded_offset),\n        ).fetchall()\n        return tuple(\n            _parse_stored_contract(bytes(row["payload"]), CausalEpisode, "causal episode")\n            for row in rows\n        )\n\n    def find_action_impact_for_source_event(\n        self, *, life_id: str, action_id: str, source_event_id: str\n    ) -> ActionImpact | None:\n        """Read the immutable ActionImpact bound to one trigger event."""\n        rows = self._connection.execute(\n            """\n            SELECT payload FROM action_impacts\n            WHERE life_id = ? AND action_id = ?\n            ORDER BY created_at_ms, impact_id\n            """,\n            (life_id, action_id),\n        ).fetchall()\n        for row in rows:\n            impact = _parse_stored_contract(\n                bytes(row["payload"]), ActionImpact, "action impact"\n            )\n            if source_event_id in impact.source_event_ids:\n                return impact\n        return None\n'''
    if "find_action_impact_for_source_event" not in text:
        text = regex_once(text, pattern, replacement, "Store OPEN episode paging")
    STORE.write_text(text, encoding="utf-8", newline="\n")


def patch_embedded() -> None:
    text = EMBEDDED.read_text(encoding="utf-8")

    # P1: zero is a real hard limit, never a falsy "unlimited" sentinel.
    replacements = (
        (
            '''        if (\n            (success_limit and int(scheduler.get("model_successes") or 0) >= success_limit)\n            or (attempt_limit and int(scheduler.get("model_attempts") or 0) >= attempt_limit)\n        ):''',
            '''        if (\n            int(scheduler.get("model_successes") or 0) >= success_limit\n            or int(scheduler.get("model_attempts") or 0) >= attempt_limit\n        ):''',
            "global model budget zero",
        ),
        (
            '''        exhausted = (\n            (global_success_limit and int(scheduler.get("model_successes") or 0) >= global_success_limit)\n            or (global_attempt_limit and int(scheduler.get("model_attempts") or 0) >= global_attempt_limit)\n            or (proactive_success_limit and int(scheduler.get("proactive_model_successes") or 0) >= proactive_success_limit)\n            or (proactive_attempt_limit and int(scheduler.get("proactive_model_attempts") or 0) >= proactive_attempt_limit)\n        )''',
            '''        exhausted = (\n            int(scheduler.get("model_successes") or 0) >= global_success_limit\n            or int(scheduler.get("model_attempts") or 0) >= global_attempt_limit\n            or int(scheduler.get("proactive_model_successes") or 0) >= proactive_success_limit\n            or int(scheduler.get("proactive_model_attempts") or 0) >= proactive_attempt_limit\n        )''',
            "proactive model budget zero",
        ),
        (
            '''        return (\n            (global_success_limit and int(scheduler.get("model_successes") or 0) >= global_success_limit)\n            or (global_attempt_limit and int(scheduler.get("model_attempts") or 0) >= global_attempt_limit)\n            or (sub_success_limit and int(scheduler.get(f"{prefix}successes") or 0) >= sub_success_limit)\n            or (sub_attempt_limit and int(scheduler.get(f"{prefix}attempts") or 0) >= sub_attempt_limit)\n        )''',
            '''        return (\n            int(scheduler.get("model_successes") or 0) >= global_success_limit\n            or int(scheduler.get("model_attempts") or 0) >= global_attempt_limit\n            or int(scheduler.get(f"{prefix}successes") or 0) >= sub_success_limit\n            or int(scheduler.get(f"{prefix}attempts") or 0) >= sub_attempt_limit\n        )''',
            "sub model budget zero",
        ),
    )
    for old, new, label in replacements:
        if old in text:
            text = replace_once(text, old, new, label)
        elif new not in text:
            raise SystemExit(f"{label}: neither old nor repaired form found")

    if '"learning_llm_daily_budget": (0, 1000),' not in text:
        anchor = '''                        "llm_daily_attempt_budget": (0, 2000),\n'''
        insertion = anchor + '''                        "learning_llm_daily_budget": (0, 1000),\n                        "learning_llm_daily_attempt_budget": (0, 2000),\n                        "self_iteration_llm_daily_budget": (0, 1000),\n                        "self_iteration_llm_daily_attempt_budget": (0, 2000),\n                        "capability_patch_llm_daily_budget": (0, 1000),\n                        "capability_patch_llm_daily_attempt_budget": (0, 2000),\n'''
        text = replace_once(text, anchor, insertion, "F6 settings integer validation")

    # P2: capability-patch TimeoutError must count in both failure and timeout.
    old = '''                    try:\n                        decision = self._capability_patch_decider(material)\n                        valid_decision = isinstance(decision, Mapping)\n                    except Exception:\n                        with self._lock:\n                            self._account_sub_model_failure_locked(\n                                self._scope_state(life_id).setdefault("scheduler", {}),\n                                pool="capability_patch",\n                            )\n                        continue\n'''
    new = '''                    try:\n                        decision = self._capability_patch_decider(material)\n                        valid_decision = isinstance(decision, Mapping)\n                    except Exception as exc:\n                        with self._lock:\n                            self._account_sub_model_failure_locked(\n                                self._scope_state(life_id).setdefault("scheduler", {}),\n                                pool="capability_patch",\n                                timeout=isinstance(exc, TimeoutError),\n                            )\n                        continue\n'''
    if old in text:
        text = replace_once(text, old, new, "capability patch timeout accounting")
    elif new not in text:
        raise SystemExit("capability patch timeout accounting form not found")

    # P2: explicit idle status is an ordering dimension, not panel-only text.
    old_sort = '''        rows.sort(\n            key=lambda item: (\n                -int(item.get("composite_score_milli") or 0),\n                -int(item.get("last_outcome_at_ms") or 0),\n                str(item.get("kind")),\n                str(item.get("title")),\n                int(item.get("version") or 0),\n            )\n        )'''
    new_sort = '''        rows.sort(\n            key=lambda item: (\n                int(bool(item.get("idle"))),\n                -int(item.get("composite_score_milli") or 0),\n                -int(item.get("last_outcome_at_ms") or 0),\n                str(item.get("kind")),\n                str(item.get("title")),\n                int(item.get("version") or 0),\n            )\n        )'''
    if old_sort in text:
        text = replace_once(text, old_sort, new_sort, "idle capability ordering")
    elif new_sort not in text:
        raise SystemExit("idle capability ordering form not found")

    # P1 reflection durability: Store is SSoT, runtime registry is only a cache.
    if "def _resolve_runtime_episode_entry_locked(" not in text:
        marker = '''    def _reflection_chain_enabled(self) -> bool:\n'''
        helpers = '''    def _reflection_registry_entry_from_episode_locked(\n        self, *, life_id: str, episode: Any, event_by_id: Mapping[str, Any] | None = None\n    ) -> dict[str, Any] | None:\n        """Rehydrate one OPEN episode cache row from authoritative evidence."""\n        if str(getattr(episode, "life_id", "") or "") != life_id:\n            return None\n        if str(getattr(episode, "terminal_status", "") or "") != "OPEN":\n            return None\n        trigger_ids = tuple(getattr(episode, "trigger_event_ids", ()) or ())\n        if not trigger_ids:\n            return None\n        store = self._contract_store()\n        events = event_by_id or {\n            str(event.event_id): event for event in store.load_events(life_id)\n        }\n        trigger_id = str(trigger_ids[0])\n        trigger = events.get(trigger_id)\n        if trigger is None:\n            return None\n        correlation_id = str(getattr(trigger, "correlation_id", "") or "")\n        if ":" not in correlation_id:\n            return None\n        source, ref_id = correlation_id.split(":", 1)\n        source, ref_id = source.strip(), ref_id.strip()\n        if not source or not ref_id:\n            return None\n        try:\n            snapshot = json.loads(str(getattr(episode, "prior_prediction", "") or ""))\n        except (TypeError, ValueError):\n            return None\n        if not isinstance(snapshot, Mapping):\n            return None\n        context_hashes = tuple(getattr(episode, "context_state_hashes", ()) or ())\n        entry: dict[str, Any] = {\n            "source": source,\n            "ref": ref_id,\n            "episode_id": str(getattr(episode, "episode_id", "") or ""),\n            "correlation_id": correlation_id,\n            "predicted_success_milli": int(snapshot.get("predicted_success_milli") or 0),\n            "prediction_snapshot": deepcopy(dict(snapshot)),\n            "context_sha256": str(context_hashes[0] if context_hashes else ""),\n            "action_risk": "A1",\n            "opened_at_ms": int(getattr(episode, "created_at_ms", 0) or 0),\n        }\n        scope = self._scope_state(life_id)\n        if source == "autonomy":\n            task = (self._autonomy_state(life_id).get("tasks") or {}).get(ref_id)\n            if isinstance(task, Mapping):\n                entry["action_risk"] = str(task.get("risk_class") or "A0")\n        elif source == "capability":\n            capability_id = str(\n                getattr(episode, "selected_action_id", "")\n                or snapshot.get("artifact_id")\n                or ""\n            )\n            artifact = (scope.get("capabilities") or {}).get(capability_id)\n            artifact = artifact if isinstance(artifact, Mapping) else {}\n            entry.update({\n                "capability_id": capability_id,\n                "capability_version": str(artifact.get("version") or snapshot.get("version") or ""),\n                "capability_scope": str(artifact.get("title") or getattr(episode, "intention", "") or "能力学习"),\n                "action_risk": str(artifact.get("risk_level") or "A3"),\n            })\n            impact = store.find_action_impact_for_source_event(\n                life_id=life_id,\n                action_id=capability_id,\n                source_event_id=trigger_id,\n            )\n            if impact is not None:\n                entry["action_impact"] = impact.model_dump(mode="json")\n        return entry\n\n    def _resolve_runtime_episode_entry_locked(\n        self, *, life_id: str, source: str, ref_id: str\n    ) -> dict[str, Any] | None:\n        """Resolve OPEN episode from cache, then from the authoritative Store."""\n        scheduler = self._scope_state(life_id).setdefault("scheduler", {})\n        registry = [\n            row for row in scheduler.get("open_episodes") or [] if isinstance(row, Mapping)\n        ]\n        for row in registry:\n            if (\n                str(row.get("source") or "") == source\n                and str(row.get("ref") or "") == ref_id\n            ):\n                return dict(row)\n        store = self._contract_store()\n        event_by_id = {str(event.event_id): event for event in store.load_events(life_id)}\n        offset = 0\n        while True:\n            batch = store.open_causal_episodes(life_id, limit=256, offset=offset)\n            if not batch:\n                return None\n            for episode in batch:\n                entry = self._reflection_registry_entry_from_episode_locked(\n                    life_id=life_id, episode=episode, event_by_id=event_by_id\n                )\n                if entry is None:\n                    continue\n                if (\n                    str(entry.get("source") or "") == source\n                    and str(entry.get("ref") or "") == ref_id\n                ):\n                    registry.append(entry)\n                    scheduler["open_episodes"] = registry[-self._REFLECTION_EPISODE_REGISTRY_CAP:]\n                    return entry\n            if len(batch) < 256:\n                return None\n            offset += len(batch)\n\n    def _reconcile_open_reflection_episodes(self, life_id: str) -> int:\n        """Abort OPEN episodes orphaned by a previous process before scheduling."""\n        if not self._reflection_chain_enabled():\n            return 0\n        store = self._contract_store()\n        event_by_id = {str(event.event_id): event for event in store.load_events(life_id)}\n        episodes: list[Any] = []\n        offset = 0\n        while True:\n            batch = store.open_causal_episodes(life_id, limit=256, offset=offset)\n            episodes.extend(batch)\n            if len(batch) < 256:\n                break\n            offset += len(batch)\n        recovered = 0\n        for episode in episodes:\n            entry = self._reflection_registry_entry_from_episode_locked(\n                life_id=life_id, episode=episode, event_by_id=event_by_id\n            )\n            if entry is None:\n                self._reflection_journal_failure(life_id, "rehydrate_open", None)\n                continue\n            scheduler = self._scope_state(life_id).setdefault("scheduler", {})\n            registry = [\n                row for row in scheduler.get("open_episodes") or [] if isinstance(row, Mapping)\n            ]\n            registry.append(entry)\n            scheduler["open_episodes"] = registry[-self._REFLECTION_EPISODE_REGISTRY_CAP:]\n            before = len(store.open_causal_episodes(life_id, limit=1))\n            self._abort_runtime_episode_locked(\n                life_id=life_id,\n                source=str(entry["source"]),\n                ref_id=str(entry["ref"]),\n                reason="restart_recovery",\n            )\n            after = len(store.open_causal_episodes(life_id, limit=1))\n            if after < before or str(getattr(episode, "terminal_status", "")) == "OPEN":\n                recovered += 1\n        return recovered\n\n'''
        text = replace_once(text, marker, helpers + marker, "reflection Store rehydration helpers")

    old_init = '''            self._state = self._load_state()\n            heartbeat_recovered = self._reconcile_scheduler_heartbeat(active_life_id)\n            recovered_inflight = recover_inflight_scheduler_flags(self._state)\n'''
    new_init = '''            self._state = self._load_state()\n            reflection_recovered = self._reconcile_open_reflection_episodes(active_life_id)\n            heartbeat_recovered = self._reconcile_scheduler_heartbeat(active_life_id)\n            recovered_inflight = recover_inflight_scheduler_flags(self._state)\n'''
    if old_init in text:
        text = replace_once(text, old_init, new_init, "startup reflection recovery")
    elif new_init not in text:
        raise SystemExit("startup reflection recovery form not found")

    old_persist = '''            if projection_changed or classification_changed or memory_contract_changed or heartbeat_recovered or recovered_inflight:\n                self._persist(active_life_id)\n'''
    new_persist = '''            if (\n                projection_changed\n                or classification_changed\n                or memory_contract_changed\n                or heartbeat_recovered\n                or recovered_inflight\n                or reflection_recovered\n            ):\n                self._persist(active_life_id)\n'''
    if old_persist in text:
        text = replace_once(text, old_persist, new_persist, "startup recovery persistence")
    elif new_persist not in text:
        raise SystemExit("startup recovery persistence form not found")

    old_lookup = '''            scheduler = self._scope_state(life_id).setdefault("scheduler", {})\n            registry = [\n                row for row in scheduler.get("open_episodes") or [] if isinstance(row, Mapping)\n            ]\n            entry = next(\n                (\n                    row for row in registry\n                    if str(row.get("source") or "") == source\n                    and str(row.get("ref") or "") == ref_id\n                ),\n                None,\n            )\n            if entry is None:\n                return  # 没有事前落账（链路关闭或 opening 失败），无从收尾\n'''
    new_lookup = '''            scheduler = self._scope_state(life_id).setdefault("scheduler", {})\n            entry = self._resolve_runtime_episode_entry_locked(\n                life_id=life_id, source=source, ref_id=ref_id\n            )\n            if entry is None:\n                return  # Store 中没有事前 OPEN 证据，无法合法收尾\n'''
    if old_lookup in text:
        text = replace_once(text, old_lookup, new_lookup, "generic reflection Store fallback")
    elif new_lookup not in text:
        raise SystemExit("generic reflection Store fallback form not found")

    old_remove = '''            scheduler["open_episodes"] = [\n                row for row in registry if row is not entry\n            ][-self._REFLECTION_EPISODE_REGISTRY_CAP:]\n'''
    new_remove = '''            scheduler["open_episodes"] = [\n                row\n                for row in scheduler.get("open_episodes") or []\n                if isinstance(row, Mapping)\n                and str(row.get("episode_id") or "") != str(entry.get("episode_id") or "")\n            ][-self._REFLECTION_EPISODE_REGISTRY_CAP:]\n'''
    # Occurs in generic + capability commit. Replace both exact occurrences.
    remove_count = text.count(old_remove)
    if remove_count:
        text = text.replace(old_remove, new_remove)
    elif text.count(new_remove) < 2:
        raise SystemExit("reflection registry removal form not found")

    old_cap_lookup = '''            scheduler = self._scope_state(life_id).setdefault("scheduler", {})\n            registry = [\n                row for row in scheduler.get("open_episodes") or [] if isinstance(row, Mapping)\n            ]\n            entry = next(\n                (\n                    row for row in registry\n                    if str(row.get("source") or "") == "capability"\n                    and str(row.get("ref") or "") == execution_id\n                ),\n                None,\n            )\n            if entry is None:\n                return None\n'''
    new_cap_lookup = '''            scheduler = self._scope_state(life_id).setdefault("scheduler", {})\n            entry = self._resolve_runtime_episode_entry_locked(\n                life_id=life_id, source="capability", ref_id=execution_id\n            )\n            if entry is None:\n                return None\n'''
    if old_cap_lookup in text:
        text = replace_once(text, old_cap_lookup, new_cap_lookup, "capability reflection Store fallback")
    elif new_cap_lookup not in text:
        raise SystemExit("capability reflection Store fallback form not found")

    EMBEDDED.write_text(text, encoding="utf-8", newline="\n")


def write_tests() -> None:
    TEST.write_text(r'''from __future__ import annotations

import tempfile
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.episode_builder import build_prediction


def runtime(root: Path) -> EmbeddedLifeRuntime:
    life = EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "runtime",
        mode="embedded",
    )
    life.scheduler.stop(timeout_seconds=2)
    return life


def test_zero_sub_budget_is_exhausted_before_first_call() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            settings = scope["settings"]
            settings["llm_daily_budget"] = 0
            settings["llm_daily_attempt_budget"] = 0
            settings["learning_llm_daily_budget"] = 0
            settings["learning_llm_daily_attempt_budget"] = 0
            assert life._sub_model_budget_exhausted(
                scheduler, settings=settings, pool="learning"
            ) is True
            assert life._reserve_sub_model_call_locked(
                scheduler=scheduler, settings=settings, pool="learning"
            ) is False
        finally:
            life.close()


def test_evicted_open_episode_closes_from_store_ssot() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            with life._lock:
                for index in range(33):
                    life._open_runtime_episode_locked(
                        life_id=life_id,
                        source="autonomy",
                        ref_id=f"task_qc_{index:02d}",
                        event_kind="autonomy.task.attempt.started",
                        intention=f"QC task {index}",
                        context_sha256=(f"{index + 1:064x}"[-64:]),
                        prediction=build_prediction(
                            basis_inputs={"source": "autonomy", "task_id": f"task_qc_{index:02d}"}
                        ),
                        action_risk="A0",
                    )
            registry = life._scope_state(life_id)["scheduler"]["open_episodes"]
            assert len(registry) == 32
            assert all(row["ref"] != "task_qc_00" for row in registry)
            assert len(life._contract_store().open_causal_episodes(life_id, limit=64)) == 33
            with life._lock:
                life._abort_runtime_episode_locked(
                    life_id=life_id,
                    source="autonomy",
                    ref_id="task_qc_00",
                    reason="qc_eviction_recovery",
                )
            opens = life._contract_store().open_causal_episodes(life_id, limit=64)
            assert len(opens) == 32
            assert all(episode.episode_id for episode in opens)
        finally:
            life.close()


def test_registry_loss_reconciles_all_open_episodes_from_store() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            with life._lock:
                for index in range(3):
                    life._open_runtime_episode_locked(
                        life_id=life_id,
                        source="autonomy",
                        ref_id=f"task_restart_{index}",
                        event_kind="autonomy.task.attempt.started",
                        intention=f"restart task {index}",
                        context_sha256=(f"{index + 10:064x}"[-64:]),
                        prediction=build_prediction(
                            basis_inputs={"source": "autonomy", "task_id": f"task_restart_{index}"}
                        ),
                        action_risk="A0",
                    )
                life._scope_state(life_id)["scheduler"]["open_episodes"] = []
                recovered = life._reconcile_open_reflection_episodes(life_id)
            assert recovered == 3
            assert life._contract_store().open_causal_episodes(life_id, limit=64) == ()
            cards = life._contract_store().list_reflection_cards(life_id, limit=10)
            assert len(cards) == 3
            assert all("restart_recovery" in card.observed_outcome for card in cards)
        finally:
            life.close()


def test_idle_sort_is_explicit_before_composite_score() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/life_service/embedded_runtime.py").read_text(encoding="utf-8")
    sort_block = source[source.index("        rows.sort(\n            key=lambda item: (\n", source.index("def _capability_overlay_payload")):]
    sort_block = sort_block[:800]
    assert sort_block.index('int(bool(item.get("idle")))') < sort_block.index('composite_score_milli')


def test_capability_patch_timeout_is_accounted_as_timeout() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/life_service/embedded_runtime.py").read_text(encoding="utf-8")
    start = source.index("decision = self._capability_patch_decider(material)")
    block = source[start:start + 900]
    assert "except Exception as exc:" in block
    assert "timeout=isinstance(exc, TimeoutError)" in block
''', encoding="utf-8", newline="\n")


def main() -> None:
    patch_store()
    patch_embedded()
    write_tests()


if __name__ == "__main__":
    main()
