"""Regression of incorrect attribution, feedback races, heads and repair links."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
import re

from capability_dictionary.composition import compile_task_composition
from contracts import canonical_sha256
from tests.test_task_generated_composition import gateway, proposal, register, prepare
from tests.test_composition_experience_reuse import service, approve
from tests.test_composition_learning_closures import execute
from tests.test_execution_replay_versions import action, program, invoke


def test_repair_reference_is_advertised_on_model_protocol_and_stays_out_of_runtime_args():
    from capability_dictionary import load_dictionary
    from v3.jineng.http_kehuduan import _canonical_to_omni_arguments
    release = load_dictionary()
    leaf_schema = release.host_protocol["parameters"]["properties"]["composition"]["properties"]["tools"]["items"]["properties"]["actions"]["items"]
    reference_schema = leaf_schema["properties"]["repair_of"]
    assert leaf_schema["additionalProperties"] is False
    assert reference_schema["type"] == "string" and re.fullmatch(reference_schema["pattern"], "a" * 64)
    value = program({**action("file.read", "corrected.txt"), "repair_of": "a" * 64})
    native = _canonical_to_omni_arguments({}, {"composition": value})
    compiled = compile_task_composition(native["composition"])
    assert compiled["leaves"][0]["repair_of"] == "a" * 64
    assert "repair_of" not in compiled["leaves"][0]["invocation"]


def use_example(svc, case, ref, *, fail=False):
    case.provider._experience_service = svc
    value = proposal("fresh adapted totals")
    value["experience_refs"] = [ref]
    for tool in value["tools"]:
        for call in tool["actions"]:
            call["target"] = "adapted.txt"
    registered = register(case, value)
    for ordinal, leaf in enumerate(compile_task_composition(value)["leaves"], 4):
        invoke(case, leaf["invocation"], ordinal, outcome="failed_final" if fail else "succeeded",
               registration=registered, leaf=leaf)
        if fail:
            break


@pytest.mark.parametrize("terminal", ["FAILED", "CANCELLED"])
@pytest.mark.parametrize("failed", [False, True])
def test_attribution_uses_referenced_receipts_before_task_terminal(service, terminal, failed):
    svc, case, _, snapshot = service
    ref = approve(svc, case)["experience_id"]
    snapshot.state = "EXECUTING"
    use_example(svc, case, ref, fail=failed)
    if terminal == "FAILED" and not failed:
        execute(svc, case, "unrelated failure", 7, target="other.txt", outcome="failed_final")
    snapshot.state = terminal
    svc.observe_terminal(case.request_id)
    use = next(iter(svc._rows()[0][0]["uses"].values()))
    assert use["execution_status"] == ("failed" if failed else "succeeded")
    assert use["status"] == ("failed" if failed else "cancelled" if terminal == "CANCELLED" else "unattributed")
    assert bool(svc.candidates("sales totals")) is not failed


def test_slow_acceptance_cannot_overwrite_later_withdrawal_even_after_retry(service):
    svc, case, _, _ = service
    approve(svc, case)
    def delayed(text):
        svc.feedback({"request_id": case.request_id, "mode": "withdraw", "event_id": "newer_withdrawal"})
        return {"decision": "accept", "scope": "whole", "feedback_only": True}
    svc.interpret = delayed
    old = {"request_id": case.request_id, "mode": "interpret", "user_text": "以后记住", "event_id": "old_slow_feedback"}
    assert svc.feedback(old)["reason"] == "feedback_superseded"
    svc.interpret = lambda _: {"decision": "accept", "scope": "whole", "feedback_only": True}
    assert svc.feedback(old)["reason"] == "feedback_superseded"
    assert svc._rows()[0][0]["status"] == "withdrawn"
    assert approve(svc, case, "genuinely_new_acceptance")["status"] == "accepted"


def test_legacy_false_quarantine_is_repaired_from_ledger_on_recall(service):
    svc, case, _, snapshot = service
    ref = approve(svc, case)["experience_id"]
    snapshot.state = "EXECUTING"
    use_example(svc, case, ref)
    snapshot.state = "FAILED"
    row = deepcopy(svc._rows()[0][0])
    use = next(iter(row["uses"].values()))
    use["status"] = "failed"  # previous release's task-level attribution
    svc._write(row, "legacy_terminal_fixture")
    assert not svc.candidates("sales totals")
    assert ref in svc.recall("sales totals")
    updated = next(iter(svc._rows()[0][0]["uses"].values()))
    assert updated["status"] == "unattributed" and updated["attribution_version"] == 2


def test_withdraw_before_first_acceptance_prevents_late_creation(service):
    svc, case, _, _ = service
    def delayed(text):
        assert svc.feedback({"request_id": case.request_id, "mode": "withdraw", "event_id": "withdraw_not_saved"})["saved"] is False
        return {"decision": "accept", "scope": "whole", "feedback_only": True}
    svc.interpret = delayed
    assert svc.feedback({"request_id": case.request_id, "mode": "interpret", "user_text": "记住", "event_id": "first_slow_accept"})["reason"] == "feedback_superseded"
    assert not svc._rows()


def test_head_reader_ignores_history_and_pages_past_filtered_records(service, monkeypatch):
    svc, case, store, _ = service
    accepted = approve(svc, case)
    svc.feedback({"request_id": case.request_id, "mode": "withdraw", "event_id": "history_withdraw"})
    approve(svc, case, "history_accept_again")
    life, principal = svc._scope()
    template = deepcopy(svc._rows()[0][0])
    # Several valid independent current heads, each with historical revisions.
    for index in range(4):
        payload = deepcopy(template)
        payload["experience_id"] = "cex_" + canonical_sha256({"example": index})
        payload["event_ids"] = []
        svc._write(payload, f"extra_example_{index}")
        payload["status"] = "withdrawn"
        svc._write(payload, f"extra_withdraw_{index}")
    original = store.list_active_memory_heads
    calls = []
    def one_at_a_time(**kw):
        calls.append(kw)
        return original(**{**kw, "limit": 1})
    monkeypatch.setattr(store, "list_active_memory_heads", one_at_a_time)
    monkeypatch.setattr(store, "list_memory_derivations", lambda **kw: (_ for _ in ()).throw(AssertionError("history scan")))
    rows = svc._rows()
    assert len(rows) == 5 and any(row["experience_id"] == accepted["experience_id"] and row["status"] == "accepted" for row, _ in rows)
    assert all(call["life_id"] == life and call["principal_ref"] == principal for call in calls)
    assert len(calls) == 6
    assert not original(life_id=life, principal_ref="workspace_other", layer="L3_EXPERIENCE")


def test_current_example_remains_readable_after_4096_legal_historical_rows(service):
    # Exercise the actual former SQL window. Migrated historical versions use
    # valid native assertions/derivations, not hand-written corrupt SQL rows.
    from contracts import canonical_json_bytes
    from tests.test_memory_derivation_store_p15 import derivation
    svc, case, store, _ = service
    accepted = approve(svc, case)
    life, principal = svc._scope()
    old = deepcopy(svc._rows()[0][0])
    old["experience_id"] = "cex_" + "0" * 64
    old["status"] = "withdrawn"
    for index in range(4096):
        suffix = canonical_sha256({"historical": index})
        event = "lev_" + suffix
        assertion, _, _ = store.put_live_memory_assertion(canonical_json_bytes(old),
            memory_id="mem_" + suffix, life_id=life, assertion_kind="observation",
            epistemic_status="user_asserted", lifecycle_status="active", privacy_scope="private",
            retention_class="CHECKPOINT", source_event_ids=(event,), causal_utility_milli=0,
            user_importance_milli=0, verification_strength_milli=0, future_dependency_milli=0,
            valid_from_ms=index + 1, created_at_ms=index + 1, search_terms=())
        historical = derivation(derivation_id="mdr_" + suffix, memory_id=assertion.memory_id,
            assertion_sha256=assertion.assertion_sha256, created_at_ms=index + 1,
            life_id=life, principal_ref=principal, privacy_scope="private", layer="L3_EXPERIENCE",
            origin="MIGRATION", semantic_domain="CAPABILITY_KNOWLEDGE",
            claim_key="composition-experience:" + old["experience_id"],
            source_event_ids=(event,), lineage_root_event_ids=(event,))
        store.put_memory_derivation(historical, activate_head=True)
    window = store.list_memory_derivations(life_id=life, layer="L3_EXPERIENCE", active_only=True, limit=4096)
    assert len(window) == 4096 and all(d.created_at_ms <= 4096 for d in window)
    rows = svc._rows()
    assert len(rows) == 2
    assert next(row for row, _ in rows if row["experience_id"] == accepted["experience_id"])["status"] == "accepted"


def test_explicit_target_repair_records_receipts_and_later_failure_invalidates_it(service):
    svc, case, _, snapshot = service
    snapshot.state = "EXECUTING"
    execute(svc, case, "bad target", 4, outcome="failed_final", target="wrong.txt")
    failure = svc.lessons.rows()[0][0]["source"]["failure"]["event_hash"]
    corrected = {**action("file.write", "right.txt", content="corrected"), "repair_of": failure}
    first = invoke(case, corrected, 5)
    assert first["event_hash"]
    row = svc.lessons.rows()[0][0]
    assert row["status"] == "RECOVERY_OBSERVED"
    assert row["source"]["recovery"]["relation"] == "explicit_repair"
    assert row["source"]["recovery"]["call"]["target"] == "right.txt"
    assert "repair_of" not in row["source"]["recovery"]["call"]
    execute(svc, case, "new failure", 6, target="right.txt", outcome="failed_final")
    assert next(row for row, _ in svc.lessons.rows() if row["source"]["failure"]["event_hash"] == failure)["status"] == "FAILURE_OBSERVED"


@pytest.mark.parametrize("kind", ["invented", "success", "ambiguous", "different_action"])
def test_repair_relation_rejects_missing_success_unknown_and_wrong_action(service, kind):
    svc, case, _, _ = service
    result = invoke(case, action("file.write", "failed.txt", content="v"), 4,
                    outcome="ambiguous" if kind == "ambiguous" else "succeeded" if kind == "success" else "failed_final")
    failure = "f" * 64 if kind == "invented" else result["event_hash"]
    corrected = action("file.read" if kind == "different_action" else "file.write", "new.txt",
                       **({} if kind == "different_action" else {"content": "new"}))
    corrected["repair_of"] = failure
    with pytest.raises(ValueError, match="repair_reference_unbound"):
        register(case, program(corrected))
