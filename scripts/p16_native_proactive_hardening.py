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
        "        self._proactive_expression_writer: Any = None\n        self._world_identity_provider: Any = None\n",
        "        self._proactive_expression_writer: Any = None\n"
        "        self._proactive_world_provider: Any = None\n"
        "        self._world_identity_provider: Any = None\n",
        "proactive world provider slot",
    )

    text = replace_once(
        text,
        '                "proactive_dnd_end_hour": 7,\n'
        '                "proactive_respect_user_activity": True,\n',
        '                "proactive_dnd_end_hour": 7,\n'
        '                # Explicit timezone keeps DND independent of host locale.\n'
        '                "proactive_timezone_offset_minutes": 0,\n'
        '                "proactive_max_future_skew_seconds": 300,\n'
        '                "proactive_respect_user_activity": True,\n',
        "proactive temporal defaults",
    )

    setter_anchor = '''    def set_self_iteration_decider(self, decider: Any) -> None:\n'''
    world_setter = '''    def set_proactive_world_provider(self, provider: Any) -> None:\n        \"\"\"Bind a read-only provider backed by committed World Understanding state.\"\"\"\n        if provider is not None and not callable(provider):\n            raise TypeError(\"proactive world provider must be callable\")\n        with self._lock:\n            self._proactive_world_provider = provider\n\n'''
    if "def set_proactive_world_provider" not in text:
        text = replace_once(text, setter_anchor, world_setter + setter_anchor, "world provider setter")

    build_anchor = "    def _build_proactive_context(self, *, life_id: str, now_ms: int) -> dict[str, Any]:\n"
    helpers = r'''    def _project_proactive_relationships(self, *, life_id: str) -> list[dict[str, Any]]:
        """Return bounded relationship metrics without raw promises/obligations/text."""
        scope = self._scope_state(life_id)
        raw = scope.get("relationships") if isinstance(scope.get("relationships"), Mapping) else {}
        rows: list[dict[str, Any]] = []
        for key, relation in sorted(raw.items(), key=lambda item: str(item[0])):
            if len(rows) >= 16 or not isinstance(relation, Mapping):
                continue
            target = str(relation.get("target_life_id") or key or "")[:240]
            metrics: dict[str, Any] = {
                "relationship_ref": "relationship_" + canonical_sha256({
                    "domain": "tiangong.life.proactive-relationship.v1",
                    "life_id": life_id,
                    "target": target,
                })[:24],
                "direction": str(relation.get("direction") or "")[:32],
                "updated_at": str(relation.get("updated_at") or "")[:48],
            }
            for field in (
                "trust_milli",
                "familiarity_milli",
                "liking_milli",
                "attachment_milli",
                "cooperation_milli",
            ):
                value = relation.get(field)
                if isinstance(value, int) and not isinstance(value, bool):
                    metrics[field] = max(0, min(1000, value))
            for source_field, count_field in (
                ("obligations", "obligation_count"),
                ("promises", "promise_count"),
                ("relationship_tags", "tag_count"),
            ):
                value = relation.get(source_field)
                metrics[count_field] = min(64, len(value)) if isinstance(value, (list, tuple, set)) else 0
            rows.append(metrics)
        return rows

    def _proactive_world_observations(self, *, life_id: str, now_ms: int) -> list[dict[str, Any]]:
        """Project only committed WU evidence; unavailable/invalid authority yields no facts."""
        provider = self._proactive_world_provider
        if not callable(provider):
            return []
        try:
            snapshot = provider(life_id)
        except Exception:
            return []
        if not isinstance(snapshot, Mapping):
            return []
        if str(snapshot.get("schema") or "") != "tiangong.life.repository-evidence.v1":
            return []
        observed_at_ms = self._proactive_timestamp_ms(snapshot.get("observed_at_ms"))
        frame_id = str(snapshot.get("frame_id") or "").strip()[:240]
        revision_hash = str(snapshot.get("frame_revision_hash") or "").strip()[:128]
        if not observed_at_ms or not frame_id or not revision_hash:
            return []
        entity_refs = snapshot.get("entity_refs") if isinstance(snapshot.get("entity_refs"), list) else []
        bounded_entities: list[dict[str, str]] = []
        for entity in entity_refs[:24]:
            if not isinstance(entity, Mapping):
                continue
            record_id = str(entity.get("record_id") or entity.get("entity_id") or "")[:160]
            sha256 = str(entity.get("sha256") or "")[:128]
            if record_id and sha256:
                bounded_entities.append({"record_id": record_id, "sha256": sha256})
        summary = {
            "frame_id": frame_id,
            "frame_revision_hash": revision_hash,
            "branch": str(snapshot.get("branch") or "")[:160],
            "commit": str(snapshot.get("commit") or "")[:160],
            "entity_refs": bounded_entities,
        }
        return [{
            "source_ref": f"world:repository:{frame_id}:{revision_hash[:24]}",
            "observed_at_ms": observed_at_ms,
            "confidence_milli": 1000,
            "epistemic_state": "KNOWN",
            "authority": "world_understanding_committed",
            "kind": "world:repository_evidence",
            "summary": json.dumps(summary, ensure_ascii=False, sort_keys=True)[:1600],
        }]

'''
    if "def _project_proactive_relationships" not in text:
        text = replace_once(text, build_anchor, helpers + build_anchor, "proactive bounded projections")

    text = replace_once(
        text,
        "        deliveries = [\n",
        "        observations.extend(self._proactive_world_observations(life_id=life_id, now_ms=now_ms))\n\n"
        "        deliveries = [\n",
        "world observations in initiative context",
    )
    text = replace_once(
        text,
        '            "relationships": deepcopy(scope.get("relationships") or {}),\n',
        '            "relationships": self._project_proactive_relationships(life_id=life_id),\n',
        "bounded relationship projection",
    )

    start = text.index("    def _schedule_native_proactive(self, *, life_id: str) -> None:\n")
    end = text.index("    def _mark_latest_proactive_replied(\n", start)
    replacement = r'''    def _reset_proactive_model_budget_if_needed(self, scheduler: dict[str, Any]) -> None:
        budget_day = utc_now()[:10]
        if str(scheduler.get("model_budget_date") or "") == budget_day:
            return
        scheduler.update({
            "model_budget_date": budget_day,
            "model_attempts": 0,
            "model_successes": 0,
            "model_failures": 0,
            "model_timeouts": 0,
            "model_skipped": 0,
        })

    def _reserve_proactive_model_call_locked(
        self,
        *,
        scheduler: dict[str, Any],
        settings: Mapping[str, Any],
    ) -> bool:
        """Reserve exactly one LLM call before invoking it."""
        self._reset_proactive_model_budget_if_needed(scheduler)
        success_limit = max(0, int(settings.get("llm_daily_budget") or 20))
        attempt_limit = max(0, int(settings.get("llm_daily_attempt_budget") or 30))
        if (
            (success_limit and int(scheduler.get("model_successes") or 0) >= success_limit)
            or (attempt_limit and int(scheduler.get("model_attempts") or 0) >= attempt_limit)
        ):
            scheduler["model_skipped"] = int(scheduler.get("model_skipped") or 0) + 1
            return False
        scheduler["model_attempts"] = int(scheduler.get("model_attempts") or 0) + 1
        return True

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
        if not self._reserve_proactive_model_call_locked(scheduler=scheduler, settings=settings):
            scheduler["last_proactive_decision_at_ms"] = now_ms
            scheduler["last_proactive_reason"] = "life.proactive.model_budget_exhausted"
            self._persist(life_id)
            return

        context = self._build_proactive_context(life_id=life_id, now_ms=now_ms)
        scheduler["proactive_decision_inflight"] = True
        scheduler["last_proactive_decision_at_ms"] = now_ms
        scheduler["last_proactive_reason"] = "life.proactive.decision_started"
        self._persist(life_id)
        slot = now_ms // max(60_000, interval_ms)
        threading.Thread(
            target=self._proactive_worker,
            args=(life_id, context, slot),
            daemon=True,
            name="life-native-proactive",
        ).start()

    def _proactive_worker(self, life_id: str, context: Mapping[str, Any], slot: int) -> None:
        """Run one P16 decision/compose turn; each actual model call is budgeted."""
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
            scheduler = scope.setdefault("scheduler", {})
            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
            now_ms = time.time_ns() // 1_000_000
            decision = evaluate_proactive_candidate(
                proposal,
                context=context,
                settings=settings,
                now_ms=now_ms,
            )
            scheduler["last_proactive_reason"] = str(decision.get("reason_code") or "")
            if decision.get("allowed") is not True:
                scheduler["proactive_decision_inflight"] = False
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
                scheduler["proactive_decision_inflight"] = False
                scheduler["last_proactive_reason"] = "life.proactive.expression_unavailable"
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.expression_unavailable", "decision": decision},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.compose-unavailable:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            if not self._reserve_proactive_model_call_locked(scheduler=scheduler, settings=settings):
                scheduler["proactive_decision_inflight"] = False
                scheduler["last_proactive_reason"] = "life.proactive.expression_budget_exhausted"
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.expression_budget_exhausted", "decision": decision},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.compose-budget:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            self._persist(life_id)

        try:
            expression_result: object = writer({
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
            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
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
            self.system.journal.append(
                life_id,
                "life.proactive.delivered",
                {"message_id": message_id, "initiative_id": initiative_id, "decision": decision},
                actor="life_proactive",
                idempotency_key=f"life.proactive.delivered:{initiative_id}",
            )
            self._persist(life_id)

'''
    text = text[:start] + replacement + text[end:]

    option_anchor = '                        "proactive_dnd_end_hour",\n                        "proactive_respect_user_activity",\n'
    if option_anchor in text:
        text = replace_once(
            text,
            option_anchor,
            '                        "proactive_dnd_end_hour",\n'
            '                        "proactive_timezone_offset_minutes",\n'
            '                        "proactive_max_future_skew_seconds",\n'
            '                        "proactive_respect_user_activity",\n',
            "proactive settings allowlist",
        )

    write(rel, text)


def patch_tests() -> None:
    rel = "tests/test_legacy_proactive_freeze.py"
    text = read(rel)
    old = '''    assert '["proactive_chats"].append(' not in text\n'''
    if old in text:
        new = '''    greeting = _def_block(text, "_schedule_greeting")\n    learning = _def_block(text, "_learning_report")\n    assert "proactive_chats" not in greeting\n    assert 'proactive_chats"].append' not in learning\n    assert text.count('proactive_chats"].append') == 1\n    assert 'scope["proactive_chats"].append(row)' in text\n'''
        text = replace_once(text, old, new, "legacy producer test contract")
        write(rel, text)

    rel = "tests/test_p16_proactive_initiative.py"
    text = read(rel)
    if "test_future_evidence_timestamp_fails_closed" not in text:
        text += r'''


def test_future_evidence_timestamp_fails_closed():
    rows = normalize_observations(
        [{
            "source_ref": "memory:future",
            "observed_at_ms": NOW + 301_000,
            "confidence_milli": 1000,
        }],
        now_ms=NOW,
        future_skew_ms=300_000,
    )
    assert rows[0]["epistemic_state"] == "UNKNOWN"
    assert rows[0]["timestamp_state"] == "FUTURE_INVALID"


def test_dnd_uses_explicit_timezone_not_host_timezone():
    # NOW is 08:00 UTC. +08:00 projects to 16:00, which is inside 15:00-17:00 DND.
    result = evaluate_proactive_candidate(
        proposal(),
        context=context(),
        settings=settings(
            proactive_dnd_enabled=True,
            proactive_dnd_start_hour=15,
            proactive_dnd_end_hour=17,
            proactive_timezone_offset_minutes=480,
        ),
        now_ms=NOW,
    )
    assert result["reason_code"] == "life.proactive.dnd"

    invalid = evaluate_proactive_candidate(
        proposal(),
        context=context(),
        settings=settings(proactive_dnd_enabled=True, proactive_timezone_offset_minutes="+08:00"),
        now_ms=NOW,
    )
    assert invalid["reason_code"] == "life.proactive.timezone_invalid"


def test_future_activity_and_delivery_clocks_fail_closed():
    activity = evaluate_proactive_candidate(
        proposal(),
        context=context(last_user_activity_at_ms=NOW + 301_000),
        settings=settings(),
        now_ms=NOW,
    )
    assert activity["reason_code"] == "life.proactive.user_activity_clock_invalid"

    delivery = evaluate_proactive_candidate(
        proposal(),
        context=context(recent_delivery_times_ms=[NOW + 301_000]),
        settings=settings(),
        now_ms=NOW,
    )
    assert delivery["reason_code"] == "life.proactive.delivery_clock_invalid"


def test_world_evidence_requires_committed_world_authority():
    world = [{
        "source_ref": "world:repo:frame-1",
        "observed_at_ms": NOW - 1_000,
        "confidence_milli": 1000,
        "kind": "world:repository_evidence",
        "authority": "model_claim",
        "summary": "repo changed",
    }]
    blocked = evaluate_proactive_candidate(
        proposal(evidence_refs=["world:repo:frame-1"]),
        context=context(observations=world),
        settings=settings(),
        now_ms=NOW,
    )
    assert blocked["reason_code"] == "life.proactive.world_authority_invalid"

    world[0]["authority"] = "world_understanding_committed"
    allowed = evaluate_proactive_candidate(
        proposal(evidence_refs=["world:repo:frame-1"]),
        context=context(observations=world),
        settings=settings(),
        now_ms=NOW,
    )
    assert allowed["allowed"] is True
'''
        write(rel, text)

    rel = "tests/test_p16_native_proactive_runtime.py"
    text = read(rel)
    if "test_relationship_projection_is_bounded_and_does_not_leak_raw_text" not in text:
        text += r'''


def test_relationship_projection_is_bounded_and_does_not_leak_raw_text():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            scope = life._scope_state(life_id)
            scope["relationships"] = {
                f"person-{index}": {
                    "target_life_id": f"target-{index}",
                    "direction": "outbound",
                    "trust_milli": 700,
                    "familiarity_milli": 500,
                    "obligations": [f"SECRET-OBLIGATION-{index}"],
                    "promises": [f"SECRET-PROMISE-{index}"],
                    "relationship_tags": [f"SECRET-TAG-{index}"],
                }
                for index in range(24)
            }
            rows = life._project_proactive_relationships(life_id=life_id)
            assert len(rows) == 16
            serialized = repr(rows)
            assert "SECRET-" not in serialized
            assert all("relationship_ref" in row for row in rows)
            assert all(row["obligation_count"] == 1 for row in rows)
        finally:
            life.close()


def test_world_projection_accepts_only_committed_wu_snapshot_shape():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            life.set_proactive_world_provider(lambda _life_id: {"schema": "untrusted", "observed_at_ms": NOW})
            assert life._proactive_world_observations(life_id=life_id, now_ms=NOW) == []

            life.set_proactive_world_provider(lambda _life_id: {
                "schema": "tiangong.life.repository-evidence.v1",
                "frame_id": "frame-1",
                "frame_revision_hash": "a" * 64,
                "observed_at_ms": NOW - 1_000,
                "branch": "main",
                "commit": "b" * 40,
                "entity_refs": [{"record_id": "file:1", "sha256": "c" * 64}],
            })
            rows = life._proactive_world_observations(life_id=life_id, now_ms=NOW)
            assert len(rows) == 1
            assert rows[0]["authority"] == "world_understanding_committed"
            assert rows[0]["kind"] == "world:repository_evidence"
        finally:
            life.close()


def test_live_turn_counts_decision_and_expression_as_two_model_calls():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            scope = life._scope_state()
            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 30})
            scheduler = scope["scheduler"]
            scheduler.update({
                "model_budget_date": "",
                "model_attempts": 0,
                "model_successes": 0,
                "model_failures": 0,
                "model_skipped": 0,
            })
            life.set_proactive_decider(lambda _context: proposal())
            life.set_proactive_expression_writer(lambda _material: {"text": "继续处理方案吗？"})
            life_id = str(life._active()["life_id"])
            # Simulate the scheduler's pre-call reservation for decision LLM #1.
            assert life._reserve_proactive_model_call_locked(
                scheduler=scheduler, settings=scope["settings"]
            ) is True
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            assert scheduler["model_attempts"] == 2
            assert scheduler["model_successes"] == 2
            assert scheduler["model_failures"] == 0
        finally:
            life.close()


def test_expression_call_is_not_made_when_second_model_budget_is_exhausted():
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            configure(life, mode="live")
            scope = life._scope_state()
            scope["settings"].update({"llm_daily_budget": 20, "llm_daily_attempt_budget": 1})
            scheduler = scope["scheduler"]
            scheduler.update({
                "model_budget_date": "",
                "model_attempts": 0,
                "model_successes": 0,
                "model_failures": 0,
                "model_skipped": 0,
            })
            life.set_proactive_decider(lambda _context: proposal())
            writer_calls: list[dict] = []
            life.set_proactive_expression_writer(lambda material: writer_calls.append(dict(material)) or {"text": "x"})
            life_id = str(life._active()["life_id"])
            assert life._reserve_proactive_model_call_locked(
                scheduler=scheduler, settings=scope["settings"]
            ) is True
            life._proactive_worker(life_id, initiative_context(life_id), NOW // 900_000)
            assert writer_calls == []
            assert scheduler["model_attempts"] == 1
            assert scheduler["model_successes"] == 1
            assert scheduler["model_skipped"] == 1
            assert scheduler["last_proactive_reason"] == "life.proactive.expression_budget_exhausted"
            assert scope["proactive_chats"] == []
        finally:
            life.close()
'''
        write(rel, text)


if __name__ == "__main__":
    patch_life_runtime()
    patch_tests()
    print("P16 merge hardening applied")
