"""Real ledger/memory durability and adversarial boundaries for automatic lessons."""
from copy import deepcopy
from types import SimpleNamespace
import json

from capability_dictionary.composition import compile_task_composition
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.test_composition_experience_reuse import service
from tests.test_task_generated_composition import gateway, register, prepare


def execute(svc, case, content, ordinal, *, outcome="succeeded", target="learning.txt"):
    case.provider._experience_service = svc
    proposal = {"tools": [{"id": "write", "description": "write a new file", "actions": [
        {"action": "file.write", "target": target, "args": {"content": content}}]}],
        "skill": {"id": "task", "description": "sales totals output", "steps": [{"id": "write", "tool": "write", "depends_on": []}]}}
    registered = register(case, proposal)
    leaf = compile_task_composition(proposal)["leaves"][0]
    prepared = prepare(case, registered, leaf, ordinal)
    effect = {key: prepared[key] for key in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}
    case.provider(case.payload("start_effect", now_ms=5000 + ordinal * 10, **effect))
    case.provider(case.payload("finish_effect", now_ms=5001 + ordinal * 10, outcome=outcome,
        result_summary={"ok": outcome == "succeeded", "error": "test.observed_failure" if outcome != "succeeded" else ""}, **effect))


def test_first_failure_is_remembered_before_terminal_and_without_approval(service):
    svc, case, store, snapshot = service
    snapshot.state = "EXECUTING"
    execute(svc, case, "bad attempt", 4, outcome="failed_final")
    rows = svc.lessons.rows()
    assert len(rows) == 1
    row, derivation = rows[0]
    assert row["status"] == "FAILURE_OBSERVED" and not row["source"]["recovery"]
    assert store.get_memory_assertion(derivation.memory_id, derivation.memory_revision).epistemic_status == "observed"
    assert not svc._rows()  # no user approval was fabricated
    assert row["experience_id"] in svc.recall("销售汇总")
    assert svc.recall("写关于月亮的诗") == ""


def test_exact_later_recovery_is_evidence_not_causal_or_quality_claim(service):
    svc, case, _, _ = service
    execute(svc, case, "bad attempt", 4, outcome="failed_final")
    execute(svc, case, "unrelated success", 5, target="other.txt")
    assert svc.lessons.rows()[0][0]["source"]["recovery"] is None
    execute(svc, case, "corrected attempt", 6)
    row = svc.lessons.rows()[0][0]
    assert row["status"] == "RECOVERY_OBSERVED"
    assert row["source"]["recovery"]["changed_arguments"] is True
    assert "unproven" in row["source"]["recovery"]["claim"]
    assert len(row["event_ids"]) == 2
    svc.observe_execution(case.request_id)
    assert svc.lessons.rows()[0][0] == row  # repeated status reads cannot add evidence


def test_ambiguous_effect_never_learns_blind_retry(service):
    svc, case, _, _ = service
    execute(svc, case, "unknown if applied", 4, outcome="ambiguous")
    execute(svc, case, "later write", 5)
    assert svc.lessons.rows()[0][0]["source"]["recovery"] is None


def test_subsequent_failure_removes_recovery_recommendation(service):
    svc, case, _, _ = service
    execute(svc, case, "bad1", 4, outcome="failed_final")
    execute(svc, case, "good", 5)
    execute(svc, case, "bad2", 6, outcome="failed_final")
    assert all(row["status"] == "FAILURE_OBSERVED" for row, _ in svc.lessons.rows())


def test_restart_retains_failure_and_source_changes_suspend_recall(service, tmp_path):
    svc, case, store, _ = service
    execute(svc, case, "bad", 4, outcome="failed_final")
    eid = svc.lessons.rows()[0][0]["experience_id"]
    store.close()
    reopened = LifeShadowStore.open(tmp_path / "memory.shadow.sqlite3", create=False, now_ms=1)
    svc.runtime.life_service._memory_coordinator = lambda: MemoryCoordinator(reopened)
    try:
        assert eid in svc.recall("销售汇总")
        svc.runtime.orchestration = SimpleNamespace(release_manifest=SimpleNamespace(release_manifest_sha256="b"*64))
        assert svc.recall("销售汇总") == ""
        assert len(svc.lessons.rows()) == 1  # evidence retained, applicability suspended
    finally:
        reopened.close()


def test_restart_backfills_interrupted_learning_from_same_ledger(service):
    svc, case, _, _ = service
    original = svc.observe_execution
    svc.observe_execution = lambda _request: None  # simulate interruption after ledger commit
    execute(svc, case, "bad", 4, outcome="failed_final")
    svc.observe_execution = original
    assert not svc.lessons.rows()
    svc.recover()
    assert len(svc.lessons.rows()) == 1


def test_workspace_change_and_action_schema_drift_block_automatic_recall(service, monkeypatch):
    svc, case, _, _ = service
    execute(svc, case, "bad", 4, outcome="failed_final")
    old = svc.runtime.config.workspace_root
    svc.runtime.config.workspace_root = old / "another"
    assert not svc.lessons.candidates("销售汇总")
    svc.runtime.config.workspace_root = old
    from capability_dictionary import load_dictionary
    tools = deepcopy(load_dictionary().tools)
    tools["file.write"]["changed"] = True
    monkeypatch.setattr("total_gateway.composition_lessons.load_dictionary", lambda: SimpleNamespace(tools=tools))
    assert not svc.lessons.candidates("销售汇总")


def test_automatic_memory_redacts_secrets_and_never_truncates_program_into_code(service):
    svc, case, _, _ = service
    execute(svc, case, "sk-" + "x"*24 + "a"*13000, 4, outcome="failed_final")
    row = svc.lessons.rows()[0][0]
    assert "sk-" not in json.dumps(row)
    assert not row["may_authorize"] and not row["may_execute"]
