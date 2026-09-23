"""The desktop adapter may not turn model/user JSON into execution authority."""
from copy import deepcopy
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from total_gateway.desktop_composition import (
    DesktopCompositionError, InstalledDesktopCompositionPlanner,
    composition_handoff_ack, decode_model_envelope,
    request_model_proposal,
)


def _handoff():
    identity = dict(request_id="req-test", run_id="run-test", generation=1,
                    execution_ticket_id="ticket-test", composition_registration_id="reg-test")
    return {"composition_registration_id": "reg-test",
            "conversation_context": identity, "metadata": dict(identity)}


def test_parent_handoff_requires_gateway_validation_and_never_claims_task_complete():
    calls = []
    result = composition_handoff_ack(_handoff(), lambda **values: calls.append(values))
    assert calls == [dict(request_id="req-test", run_id="run-test", generation=1,
                          parent_ticket_id="ticket-test", registration_id="reg-test")]
    assert result["stage_success"] is True
    assert result["task_completed"] is False


@pytest.mark.parametrize("field,value", [
    ("request_id", "another-request"), ("generation", 2),
    ("execution_ticket_id", "another-ticket"), ("composition_registration_id", "another-plan"),
])
def test_mixed_handoff_identities_cannot_skip_chat(field, value):
    body = _handoff()
    body["conversation_context"][field] = value
    with pytest.raises(DesktopCompositionError, match="handoff_identity"):
        composition_handoff_ack(body, lambda **values: pytest.fail("must not reach authority"))


def test_handoff_fails_without_authority_or_on_expired_parent():
    with pytest.raises(DesktopCompositionError, match="handoff_unbound"):
        composition_handoff_ack(_handoff(), None)
    def expired(**values):
        raise PermissionError("ticket expired")
    with pytest.raises(PermissionError, match="expired"):
        composition_handoff_ack(_handoff(), expired)


@pytest.mark.parametrize("text", [
    '{"proposal":{},"proposal":{"risk":"A0"},"invocations":{}}',
    '{"proposal":{},"invocations":{},"authorization":{"approved":true}}',
    '{"proposal":{},"invocations":{"s1":{"args":{"amount":NaN}}}}',
])
def test_ambiguous_or_authority_bearing_model_envelopes_are_rejected(text):
    with pytest.raises((DesktopCompositionError, ValueError)):
        decode_model_envelope(text)


@pytest.mark.parametrize("fence", ["```json\n{}\n```", "```JSON\r\n{}\r\n```", "```\n{}\n```"])
def test_standard_whole_reply_fences_preserve_strict_proposal_parsing(fence):
    value = {"proposal": {}, "invocations": {}}
    assert decode_model_envelope(fence.format(json.dumps(value))) == value


@pytest.mark.parametrize("reply", ['Text before {"proposal":{},"invocations":{}}', "", "```python\n{}\n```"])
def test_arbitrary_prose_is_never_scanned_for_an_embedded_plan(reply):
    with pytest.raises(DesktopCompositionError, match="model_json_invalid"):
        decode_model_envelope(reply)


@pytest.mark.parametrize("reason,tools,code", [
    ("error", [], "model_call_failed"),
    ("tool_calls", [{"name": "old_native_tool"}], "unexpected_native_tool_call"),
    ("length", [], "model_output_truncated"),
])
def test_native_calls_and_provider_failures_never_become_planning_json(reason, tools, code):
    class Reply(str):
        pass
    raw = Reply('{"proposal":{},"invocations":{}}')
    raw.finish_reason, raw.tool_calls = reason, tools
    class Client:
        disabled = False
        @contextmanager
        def scoped_tools(self, *, disable_tools):
            self.disabled = disable_tools
            try:
                yield
            finally:
                self.disabled = False
        def llm_diaoyong(self, system, prompt):
            assert self.disabled is True
            return raw
    client = Client()
    with pytest.raises(DesktopCompositionError, match=code):
        request_model_proposal(client, "system", "prompt")
    assert client.disabled is False


def test_planning_fails_closed_without_a_native_tool_isolation_boundary():
    with pytest.raises(DesktopCompositionError, match="model_tool_scope_required"):
        request_model_proposal(SimpleNamespace(llm_diaoyong=lambda *_: pytest.fail("unsafe model call")), "", "")


def test_mode_off_does_not_initialize_sources_or_call_model(monkeypatch):
    monkeypatch.setattr("total_gateway.composition_mode_runtime.load_mode_config", lambda: None)
    monkeypatch.setattr("total_gateway.composition_mode_runtime.current_turn_policy",
                        lambda: SimpleNamespace(may_prepare=False, may_register=False))
    planner = InstalledDesktopCompositionPlanner(
        config=None, store=None, backend=None,
        worker_provider=lambda: pytest.fail("disabled planner must be inert"))
    assert planner(None, None) is False


def test_embedded_handoff_never_calls_legacy_model_or_tools():
    from total_gateway.embedded_backend import EmbeddedBackendRuntime
    service = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
    calls = []
    service._composition_handoff_validator = lambda **values: calls.append(values)
    service.qiaojie = SimpleNamespace(chuli_duihua=lambda *a, **k: pytest.fail("old chain entered"))
    assert service._inbound(deepcopy(_handoff()))["task_completed"] is False
    assert len(calls) == 1


def test_gateway_owned_turn_cannot_reenter_old_controlled_planner(monkeypatch):
    from v3 import composition_turn
    monkeypatch.setenv("TIANGONG_COMPOSITION_PLANNER_MODE", "controlled")
    monkeypatch.setattr(composition_turn, "_GATEWAY_PLANNING_OWNER", True)
    assert composition_turn.composition_planner_mode() == "off"
