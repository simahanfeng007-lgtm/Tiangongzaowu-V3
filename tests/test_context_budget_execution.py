"""Long ordinary history must not prevent a fresh task from executing."""
import json
from types import SimpleNamespace

import pytest

from contracts import CausalContextItem
from life_service.context import CausalContextBuilder, ContextBuildError
from life_service.context_api import LifeContextCompileAuthorizeApi, LifeContextApiError, LifeProjectionInputs
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.store import LifeShadowStore
from tests.test_continuity_capsule import capsule
from tests.test_causal_context_builder import assertion
from total_gateway.desktop_api import DesktopApiRouter


@pytest.fixture
def store(tmp_path):
    value = LifeShadowStore.open(tmp_path / "budget.shadow.sqlite3", create=True, now_ms=100)
    try:
        yield value
    finally:
        value.close()


def external(ref, text, kind="memory", priority=900):
    return CausalContextItem(item_ref=ref, item_kind=kind, source_revision=1,
        summary=text, epistemic_status="observed", confidence_milli=800,
        priority=priority, privacy_scope="private", token_count=len(text.encode("utf-8")),
        supporting_event_ids=())


def test_optional_history_overflow_keeps_current_goal_and_all_hard_constraints(store):
    continuity = capsule(user_goal="执行新的报告任务", hard_constraints=("保留用户原件",),
                         created_at_ms=1000).with_computed_capsule_sha256()
    store.put_context_capsule(continuity)
    protected = store.put_protected_payload("不得覆盖现有结果".encode(), life_id=continuity.life_id,
        privacy_scope="private", created_at_ms=500)
    required = assertion(protected.payload_id, protected.ciphertext_sha256, marker="2",
        assertion_kind="hard_constraint", retention_class="LONG_TERM_MEMORY", importance=100)
    store.put_memory_assertion(required, search_terms=("结果",))
    items = tuple(sorted(
        [external(f"history_{i:02d}", "普通历史记录" * 2400, priority=5000) for i in range(64)]
        + [external("required_constraint", "输出必须可编辑", kind="constraint")],
        key=lambda item: item.item_ref))
    pack = CausalContextBuilder(store).build(continuity, current_context_tokens=1000,
                                            created_at_ms=2000, external_items=items)
    refs = {item.item_ref for item in pack.items}
    assert pack.continuity == continuity
    assert {required.memory_id, "required_constraint"} <= refs
    assert pack.omitted_item_count > 0
    assert pack.selected_token_count <= pack.token_budget.usable_budget_tokens
    assert pack.has_valid_pack_sha256()
    store.put_causal_context_pack(pack, privacy_scope="private")
    assert store.read_causal_context_pack(pack.pack_id) == pack


def test_required_context_budget_error_survives_api_without_private_text(store):
    items = tuple(external(f"constraint_{i}", "PRIVATE_CONTEXT" * 1300, kind="constraint")
                  for i in range(8))
    projection = LifeProjectionInputs(life_id="life_contract_test", writer_epoch=1,
        identity_revision=1, soul={"life_id":"life_contract_test", "revision":1, "name":"Test"},
        capabilities={}, external_items=items)
    payload = {"request_id":"req_" + "a" * 64, "run_id":"run_" + "b" * 64,
        "generation":1, "current_request":"制作报告", "principal_scope_hash":"c" * 64,
        "issued_at_ms":2000}
    with pytest.raises(LifeContextApiError) as failure:
        LifeContextCompileAuthorizeApi(store).compile_and_authorize(payload, projection)
    assert str(failure.value) == "life.context.budget_exceeded"
    assert store.get_context_authorization(payload["request_id"], run_id=payload["run_id"], generation=1) is None


def test_invalid_external_token_claim_is_not_treated_as_optional_history(store):
    continuity = capsule(created_at_ms=1000).with_computed_capsule_sha256()
    bad = external("memory_bad", "必须按实际字节计数").model_copy(update={"token_count":1})
    with pytest.raises(ContextBuildError, match="external context items are invalid"):
        CausalContextBuilder(store).build(continuity, current_context_tokens=0,
                                          created_at_ms=2000, external_items=(bad,))


def test_recent_memory_selection_and_truncated_count_survive_sorted_json_restart():
    records = {
        "mem_z_old": {"memory_id":"mem_z_old", "created_at":"2026-09-20T00:00:00Z", "content":"old"},
        "mem_a_new": {"memory_id":"mem_a_new", "created_at":"2026-09-24T00:00:00Z", "content":"中" * 25000},
    }
    selected = []
    for memories in (records, json.loads(json.dumps(records, sort_keys=True))):
        runtime = object.__new__(EmbeddedLifeRuntime)
        runtime._scope_state = lambda: {"memories":memories}
        items = runtime._external_memory_items(limit=1)
        memory = next(item for item in items if item.item_kind == "memory")
        assert memory.item_ref == "mem_a_new"
        assert len(memory.summary) == 20000
        assert memory.token_count == len(memory.summary.encode("utf-8"))
        selected.append(memory)
    assert selected[0] == selected[1]


@pytest.mark.parametrize("code", ["life.context.budget_exceeded", "life.context.atomic.failed:lifecontextauthorityerror"])
def test_context_failures_do_not_claim_identity_corruption(code):
    effect = SimpleNamespace(claim=SimpleNamespace(effect_kind="execution"),
        result=SimpleNamespace(error_code=code, observed_at_ms=20))
    router = object.__new__(DesktopApiRouter)
    router._runtime = SimpleNamespace(store=SimpleNamespace(
        list_effects_for_request=lambda *_a, **_k: (effect,), list_system_statuses=lambda *_a, **_k: ()))
    detail = router._desktop_error_detail("req_" + "a" * 64,
        (SimpleNamespace(machine="request", run_id="run_" + "b" * 64, generation=1),))
    assert detail["code"] == code
    assert "上下文" in detail["message"]
    assert "身份" not in detail["message"]
    assert "迁移" not in detail["action"]
