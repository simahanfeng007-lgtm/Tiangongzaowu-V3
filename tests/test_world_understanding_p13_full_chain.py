from __future__ import annotations
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def text(rel): return (ROOT/rel).read_text(encoding="utf-8")


def test_one_physical_input_surface_is_facade_accept():
    f=text("src/world_understanding/facade.py")
    assert f.count("def accept(") == 1
    assert "WorldUnderstandingFacade" in f


def test_context_request_reuses_facade_accept():
    i=text("app/backend/tiangong-backend/v3/world_context_integration.py")
    assert "self.facade.accept(envelope)" in i
    assert "WorldUnderstandingIngress(" not in i


def test_context_slot_is_non_authorizing_by_construction():
    s=text("src/world_understanding/context_output/slot.py")
    for token in ("context_only: bool = True","authorizes: bool = False","confirms: bool = False","changes_risk: bool = False"):
        assert token in s


def test_context_slot_does_not_import_gateway_or_tool_execution():
    s=text("src/world_understanding/context_output/slot.py")
    assert "total_gateway" not in s and "ToolCall" not in s and "RuntimeTicketAuthority" not in s


def test_context_integration_fail_open_when_disabled_or_error():
    s=text("app/backend/tiangong-backend/v3/world_context_integration.py")
    assert 'if not world_understanding_enabled():\n        return ""' in s
    assert 'except Exception as exc:' in s and 'return ""' in s


def test_ambiguous_current_world_stream_is_not_guessed():
    s=text("app/backend/tiangong-backend/v3/world_context_integration.py")
    assert "return candidates[0] if len(candidates) == 1 else None" in s


def test_inquiry_acceptance_is_not_execution_grant():
    s=text("src/world_understanding/inquiry/self_will_integration.py")
    assert 'may_execute_directly: Literal[False] = False' in s
    assert 'requires_gateway_evaluation: Literal[True] = True' in s


def test_inquiry_origin_and_principal_are_not_user():
    s=text("src/world_understanding/inquiry/self_will_integration.py")
    assert 'origin: Literal["SELF_WILL"]' in s
    assert 'principal: Literal["life:self"]' in s
    assert 'principal: Literal["USER"]' not in s


def test_self_will_reuses_existing_life_action_intent_transport():
    s=text("src/world_understanding/inquiry/self_will_integration.py")
    e=text("src/life_service/action_intents.py")
    assert "LifeActionIntentEmitter" in s and "submit_self_will" in s
    assert "return self.submit(intent)" in e


def test_inquiry_provenance_cannot_be_laundered_as_user_authority():
    e=text("src/life_service/action_intents.py")
    assert 'exact[0].source_type != "EXTERNAL_DATA"' in e
    for trusted in ("CURRENT_USER_INSTRUCTION","PREAUTHORIZED_USER_FACT","AUTHENTICATED_DIRECTORY"):
        assert trusted in e


def test_source_compilers_keep_claims_and_reality_sources_distinct():
    s=text("src/world_understanding/source_compilers/p3.py")
    assert '"WEB_SOURCE_CLAIMS"' in s and '"DOCUMENT_CLAIMS"' in s and '"MODEL_PROPOSED"' in s
    assert '"FILESYSTEM_OBSERVED"' in s and '"FACT_EXECUTION_RECORDED"' in s


def test_tool_declared_write_does_not_equal_observed_write():
    s=text("src/world_understanding/source_compilers/p3.py")
    assert '"TOOL_WRITE_DECLARED"' in s
    assert 'authority_ceiling_milli=0,empirical_evidence_weight_milli=0' in s
    assert 'observed_write_effect' in s and 'authoritative' in s


def test_closure_has_scope_authority_provenance_and_cycle_guards():
    s=text("src/world_understanding/known/closure.py")
    for needle in ("require_same_scope_parents","intersect_authority","PROVENANCE_ROOTS_EMPTY","SAME_REVISION_CYCLE","model_assisted=False"):
        assert needle in s


def test_no_p13_production_subsystem_exists():
    # P13 is validation-only: no world_understanding/p13 runtime package.
    assert not (ROOT/"src/world_understanding/p13").exists()
