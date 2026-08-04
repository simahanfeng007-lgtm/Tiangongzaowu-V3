"""G3 saga evidence: plan/slots/outcome/assistant-status with crash recovery."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from contracts import (
    ModelAttemptPlan,
    ModelAttemptResult,
    ProviderSlot,
    derive_model_attempt_id,
    derive_model_inference_effect_id,
)
from total_gateway.response_saga import ResponseCommitSaga, ResponseSagaError
from total_gateway.store import GatewayStateStore


H = "a" * 64
REQ = "req_" + "1" * 64
RUN = "run_" + "2" * 64


def make_plan(slot_count: int = 2, **overrides):
    model_effect_id = derive_model_inference_effect_id(
        origin_request_id=REQ, origin_run_id=RUN, root_experience_id="root_1",
        response_episode_id="ep_1", request_sha256=H,
    )
    values = dict(
        model_attempt_plan_id="map_" + "3" * 64, model_effect_id=model_effect_id,
        request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        run_life_binding_sha256=H, root_experience_id="root_1", response_episode_id="ep_1",
        response_episode_sha256=H, context_pack_ref="ctx_1", context_pack_sha256=H,
        response_basis_kind="conversation", response_basis_sha256=H,
        capability_profile_sha256=H,
        provider_slots=tuple(
            ProviderSlot(slot_no=index, provider=f"p{index}", model=f"m{index}", transport_profile_sha256=H)
            for index in range(1, slot_count + 1)
        ),
        plan_revision=1, request_sha256=H, conversation_basis_ref="cb_1",
        plan_sha256="0" * 64,
    )
    values.update(overrides)
    return ModelAttemptPlan(**values).with_computed_plan_sha256()


class RecordingAdapter:
    def __init__(self, outcomes: list[dict]):
        self._outcomes = list(outcomes)
        self.calls: list[int] = []

    def __call__(self, context):
        slot = int(context["slot_no"])
        self.calls.append(slot)
        outcome = self._outcomes[slot - 1]
        if outcome["status"] == "SUCCEEDED":
            return {**outcome, "text": outcome.get("text", "ok"), "finish_reason": "stop"}
        return outcome


@pytest.fixture()
def harness():
    with tempfile.TemporaryDirectory() as temporary:
        store = GatewayStateStore.open(Path(temporary) / "gateway.sqlite3", now_ms=1)
        commits: list[dict] = []

        def life_committer(payload):
            commits.append(dict(payload))

        try:
            yield store, commits
        finally:
            store.close()


def test_t10_pure_chat_opens_model_inference_effect_without_completion_gate(harness) -> None:
    store, _ = harness
    adapter = RecordingAdapter([{"status": "SUCCEEDED", "text": "hello"}])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: None)
    plan = make_plan(1)
    saga.begin(plan)
    row = store._connection.execute(
        "SELECT effective_status FROM effect_outcome_head WHERE effect_id=?", (plan.model_effect_id,)
    ).fetchone()
    assert row is not None
    assert plan.response_basis_kind == "conversation"
    assert plan.completion_delivery_mode is None
    outcome = saga.run_plan(plan)
    assert outcome.status == "SUCCEEDED"


def test_t12_all_models_down_has_no_assistant_and_system_status_only(harness) -> None:
    store, commits = harness
    adapter = RecordingAdapter([{"status": "FAILED_FINAL"}, {"status": "FAILED_FINAL"}])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: commits.append(payload))
    plan = make_plan(2)
    saga.begin(plan)
    outcome = saga.run_plan(plan)
    assert outcome.status == "EXHAUSTED"
    envelope = saga.commit_response(plan, outcome, life_id="life_1")
    assert envelope.assistant_message is None
    assert envelope.system_status is not None
    assert envelope.system_status.status_code == "all_models_unavailable"
    count = store._connection.execute("SELECT count(*) FROM assistant_commit").fetchone()[0]
    assert count == 0
    assert commits[-1]["expression_status"] == "model_unavailable"


def test_t11_failed_tool_then_model_available_has_assistant_and_status(harness) -> None:
    store, commits = harness
    adapter = RecordingAdapter([
        {"status": "FAILED_FINAL", "error_code": "tool.failed"},
        {"status": "SUCCEEDED", "text": "recovered"},
    ])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: commits.append(payload))
    plan = make_plan(2)
    saga.begin(plan)
    outcome = saga.run_plan(plan)
    assert outcome.status == "SUCCEEDED"
    envelope = saga.commit_response(
        plan, outcome, life_id="life_1", text="recovered", system_status="tool.failed"
    )
    assert envelope.assistant_message is not None
    assert envelope.system_status is not None
    assert envelope.system_status.status_code == "tool.failed"
    assert commits[-1]["expression_status"] == "model_available"


def test_t27_crash_matrix_no_replay_of_terminal_slots(harness) -> None:
    store, _ = harness
    adapter = RecordingAdapter([
        {"status": "SUCCEEDED", "text": "winner"},
        {"status": "FAILED_FINAL"},
    ])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: None)
    plan = make_plan(2)
    saga.begin(plan)
    # Crash after dispatch of slot 1 before its result: marker dispatched, no result.
    marker_id = "dm_" + "1" * 64
    attempt_id = derive_model_attempt_id(model_attempt_plan_id=plan.model_attempt_plan_id, slot_no=1)
    assert store.create_dispatch_marker(
        marker_id=marker_id, plan_id=plan.model_attempt_plan_id,
        attempt_id=attempt_id, slot_no=1, now_ms=10,
    )
    assert store.mark_dispatch_marker_dispatched(marker_id=marker_id, now_ms=11)
    # Crash after slot 1 result was written: terminal result must never replay.
    first_result = saga.execute_slot(plan, plan.provider_slots[0].model_dump(mode="json"))
    assert first_result.status == "SUCCEEDED"
    calls_before = list(adapter.calls)
    outcome = saga.run_plan(plan)
    assert outcome.status == "SUCCEEDED"
    assert adapter.calls == calls_before
    assert outcome.winner_attempt_ref == first_result.model_attempt_receipt_id


def test_t27_ambiguous_slot_is_never_redispatched_and_next_slot_runs(harness) -> None:
    store, _ = harness
    adapter = RecordingAdapter([
        {"status": "AMBIGUOUS"},
        {"status": "SUCCEEDED", "text": "next"},
    ])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: None)
    plan = make_plan(2)
    saga.begin(plan)
    outcome = saga.run_plan(plan)
    assert outcome.status == "SUCCEEDED"
    assert adapter.calls == [1, 2]
    ambiguous = store.get_model_attempt_result(plan_id=plan.model_attempt_plan_id, slot_no=1)
    assert ambiguous is not None and ambiguous.status == "AMBIGUOUS"
    again = saga.run_plan(plan)
    assert again.status == "SUCCEEDED"
    assert adapter.calls == [1, 2]


def test_later_slot_result_after_winner_is_rejected(harness) -> None:
    store, _ = harness
    adapter = RecordingAdapter([{"status": "SUCCEEDED", "text": "w"}])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: None)
    plan = make_plan(2)
    saga.begin(plan)
    first = saga.execute_slot(plan, plan.provider_slots[0].model_dump(mode="json"))
    assert first.status == "SUCCEEDED"
    later = ModelAttemptResult(
        model_attempt_receipt_id="mar_" + "b" * 64, model_attempt_plan_id=plan.model_attempt_plan_id,
        model_attempt_plan_sha256=plan.plan_sha256, model_effect_id=plan.model_effect_id,
        request_id=REQ, run_id=RUN, run_sequence=1, generation=0,
        run_life_binding_sha256=H, root_experience_id="root_1", response_episode_id="ep_1",
        attempt_id="mat_" + "c" * 64, slot_no=2, provider="p2", model="m2",
        status="FAILED_FINAL", attempt_plan_revision=1, request_sha256=H,
        dispatched=True, started_at_ms=1, completed_at_ms=2, response_schema_valid=False,
        dispatch_marker_ref="dm_2", transport_run_id="trn_2", error_code="late",
    )
    store.put_model_attempt_result(later)
    with pytest.raises(ResponseSagaError, match="later slot"):
        saga.run_plan(plan)


def test_t13_stream_fallback_single_winning_attempt_not_concatenated(harness) -> None:
    store, _ = harness
    adapter = RecordingAdapter([
        {"status": "FAILED_FINAL"},
        {"status": "SUCCEEDED", "text": "final"},
    ])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: None)
    plan = make_plan(2)
    saga.begin(plan)
    outcome = saga.run_plan(plan)
    assert outcome.status == "SUCCEEDED"
    envelope = saga.commit_response(plan, outcome, life_id="life_1", text="final")
    assert envelope.assistant_message is not None
    assert envelope.assistant_message.text == "final"
    count = store._connection.execute("SELECT count(*) FROM assistant_commit").fetchone()[0]
    assert count == 1


def test_t18_rebuild_replay_causes_zero_model_calls() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "gateway.sqlite3"
        store = GatewayStateStore.open(path, now_ms=1)
        adapter = RecordingAdapter([{"status": "SUCCEEDED", "text": "ok"}])
        plan = make_plan(1)
        saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: None)
        saga.begin(plan)
        saga.run_plan(plan)
        assert adapter.calls == [1]
        store.close()
        reopened = GatewayStateStore.open(path, now_ms=2)
        try:
            rebuilt = ResponseCommitSaga(
                reopened, transport_adapter=adapter, life_committer=lambda payload: None
            )
            outcome = rebuilt.run_plan(plan)
            assert outcome.status == "SUCCEEDED"
            assert adapter.calls == [1]
        finally:
            reopened.close()


@pytest.mark.parametrize(
    "code",
    ["cancel", "fence", "authorization_denied", "commitment_failure",
     "tool_failure", "qc_failure", "delivery_failure", "internal_failure"],
)
def test_t31_terminal_kinds_enter_response_saga_assistant_null_iff_exhausted(harness, code) -> None:
    store, commits = harness
    adapter = RecordingAdapter([{"status": "FAILED_FINAL"}])
    saga = ResponseCommitSaga(store, transport_adapter=adapter, life_committer=lambda payload: commits.append(payload))
    plan = make_plan(1)
    saga.begin(plan)
    outcome = saga.run_plan(plan)
    assert outcome.status == "EXHAUSTED"
    envelope = saga.commit_response(plan, outcome, life_id="life_1", system_status=code)
    assert envelope.assistant_message is None
    assert envelope.system_status is not None
    assert envelope.system_status.status_code == code
    assert commits[-1]["expression_status"] == "model_unavailable"
