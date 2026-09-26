"""Production model binding, budget and actual HTTP-boundary isolation; no network."""
from contextlib import contextmanager
from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace

import pytest

from v3 import world_semantic_binding as binding
from v3.model_endpoint import ModelEndpointConfig
from v3.run_context import RunContext, bind_run_context
from v3.jineng.model_call_lifecycle import model_call_scope
from world_understanding.semantic import SemanticPipeline
from test_world_understanding_p8_semantic_pipeline import (
    build_semantic_input, known, proposal, run_pipeline, scope,
)
from test_world_understanding_p13_1_production_activation import _source


def endpoint(model="mimo-v2.6-pro"):
    return ModelEndpointConfig(service_preset="mimo", provider_identity="mimo",
        protocol_family="openai_chat_completions", base_url="https://example.test/v1",
        model_name=model, credential_scope="test", reasoning_mode="", endpoint_overrides={},
        optimization_family="mimo", config_fingerprint=model)


def bundle():
    return build_semantic_input(scope=scope(), known_records=(known("GIT_OBSERVED", "repo", "source", native="one"),))


class Client:
    def __init__(self):
        self._kehuduan = SimpleNamespace(is_closed=False)
        self.calls = []
        self.endpoints = []
        self.output = json.dumps({"hypotheses": [proposal(subject=0, basis=[0])]})

    @contextmanager
    def scoped_tools(self, *, disable_tools=False):
        assert disable_tools is True
        yield

    @contextmanager
    def scoped_semantic_inference(self, *, endpoint, max_output_tokens):
        self.endpoints.append((endpoint, max_output_tokens))
        yield

    def llm_diaoyong(self, system, user, provider_id=None):
        self.calls.append((system, user, provider_id))
        return self.output() if callable(self.output) else self.output

    def zuowei_huidiao(self):
        return self.llm_diaoyong


@pytest.fixture(autouse=True)
def isolated_binding(monkeypatch):
    monkeypatch.setenv("TIANGONG_WORLD_SEMANTIC_ENABLED", "1")
    monkeypatch.setattr(binding, "_client_ref", None)


def configured(client, **kwargs):
    return binding.ConfiguredWorldSemanticModel(client_provider=lambda: client,
        endpoint_resolver=lambda: endpoint(), credential_reader=lambda *args: "fixture", **kwargs)


def test_lazy_binding_follows_current_configuration_and_late_credentials(monkeypatch):
    client = Client()
    current = [endpoint()]
    credentials = [None]
    now = [0.0]
    model = binding.ConfiguredWorldSemanticModel(endpoint_resolver=lambda: current[0],
        credential_reader=lambda *args: credentials[0], clock=lambda: now[0])
    assert not model.is_available()
    binding.bind_world_semantic_client(client)
    assert not model.is_available() and not client.calls
    credentials[0] = "fixture"
    assert model.is_available()
    with bind_run_context(RunContext(run_id="run.one")):
        first = run_pipeline(bundle(), model)
    current[0] = replace(endpoint("replacement-model"), provider_identity="replacement")
    now[0] += 31
    with bind_run_context(RunContext(run_id="run.two")):
        second = run_pipeline(bundle(), model)
    assert first.status == second.status == "COMPLETED"
    assert first.trace.model_sha256 != second.trace.model_sha256
    assert [row[0].model_name for row in client.endpoints] == ["mimo-v2.6-pro", "replacement-model"]
    assert client.calls[-1][2] == "replacement"
    monkeypatch.setenv("TIANGONG_WORLD_SEMANTIC_ENABLED", "0")
    assert not model.is_available() and model.last_status == "DISABLED"
    monkeypatch.setenv("TIANGONG_WORLD_SEMANTIC_ENABLED", "1")
    client._kehuduan.is_closed = True
    assert not model.is_available()


def test_run_budget_and_cooldown_are_deferred_without_more_model_calls():
    client, now = Client(), [0.0]
    model = configured(client, clock=lambda: now[0])
    with bind_run_context(RunContext(run_id="run.one")):
        assert run_pipeline(bundle(), model).status == "COMPLETED"
        repeated = run_pipeline(bundle(), model)
    assert repeated.status == "NOT_ADMITTED"
    assert repeated.trace.admission_reason_code == "SEMANTIC_RUN_BUDGET"
    with bind_run_context(RunContext(run_id="run.two")):
        deferred = run_pipeline(bundle(), model)
        now[0] = 31
        assert run_pipeline(bundle(), model).status == "COMPLETED"
    assert deferred.trace.admission_reason_code == "SEMANTIC_COOLDOWN"
    assert len(client.calls) == 2


def test_large_input_does_not_start_a_provider_call():
    client = Client()
    model = configured(client, budget=binding.WorldSemanticBudget(max_input_chars=1))
    result = run_pipeline(bundle(), model)
    assert result.trace.admission_reason_code == "SEMANTIC_INPUT_BUDGET"
    assert not client.calls


def test_exhausted_gateway_deadline_defers_without_spending_a_call(monkeypatch):
    monkeypatch.setenv("TIANGONG_EFFECT_DEADLINE_MS", str(int(time.time() * 1000) - 100))
    client = Client()
    model = configured(client)
    result = run_pipeline(bundle(), model)
    assert result.trace.admission_reason_code == "SEMANTIC_DEADLINE_BUDGET"
    assert not client.calls and not model._attempts


def test_timeout_has_own_deadline_and_late_result_cannot_start_more_workers():
    client, release, entered, exited = Client(), threading.Event(), threading.Event(), threading.Event()
    def blocked():
        entered.set()
        try:
            release.wait(2)
            return '{"hypotheses":[]}'
        finally:
            exited.set()
    client.output = blocked
    model = configured(client, budget=binding.WorldSemanticBudget(timeout_seconds=0.08, min_interval_seconds=0))
    try:
        with model_call_scope(2) as parent:
            started = time.monotonic()
            result = run_pipeline(bundle(), model)
            assert entered.is_set()
            assert time.monotonic() - started < 0.7
            assert result.status == "LLM_UNAVAILABLE" and result.hypotheses == ()
            assert model.last_status == "TIMEOUT"
            parent.check()
            busy = run_pipeline(bundle(), model)
            assert busy.trace.admission_reason_code == "SEMANTIC_BUSY"
            assert len(client.calls) == 1
    finally:
        release.set()
        assert exited.wait(1)


def test_cancelled_parent_prevents_auxiliary_network_call():
    client = Client()
    model = configured(client)
    with model_call_scope(2) as parent:
        parent.stop("cancelled")
        result = run_pipeline(bundle(), model)
    assert result.status == "LLM_UNAVAILABLE" and not client.calls
    assert model._inflight is None


@pytest.mark.parametrize("model_output,expected_status", [
    (None, "COMPLETED"), ("not JSON", "OUTPUT_REJECTED"), ("[LLM错误: unavailable]", "LLM_UNAVAILABLE"),
])
def test_dispatcher_auto_binds_production_and_fact_commit_survives_model_failure(monkeypatch, tmp_path, model_output, expected_status):
    from v3 import runtime_composition as composition
    from v3 import world_understanding_production as production
    client = Client()
    if model_output is not None:
        client.output = model_output
    monkeypatch.setattr(composition, "HttpKehuduan", lambda: client)
    monkeypatch.setattr(composition, "GutongCeng", lambda callback: object())
    for name in ("GuanchaYinqing", "JinhuaYinqing", "JinhuaBiaodaRouter", "JinhuaBihuanYinqing", "ZiyuYinqing"):
        monkeypatch.setattr(composition, name, object)
    monkeypatch.setattr(binding, "duqu_model_endpoint_config", lambda: endpoint())
    monkeypatch.setattr(binding, "duqu_endpoint_api_miyao", lambda *args: "fixture")
    monkeypatch.setattr(production, "_runtime", None)
    monkeypatch.setattr(production, "_context_output", None)
    monkeypatch.setattr(production, "_active_coordinator", None)
    monkeypatch.setattr(production, "_active_dispatcher", None)
    monkeypatch.setenv("TIANGONG_WORLD_STATE_ROOT", str(tmp_path / "state"))
    runtime = production.production_world_understanding_runtime()
    assert not runtime._semantic.model.is_available()
    assembled = composition.build_zongdiaodu_composition()
    assert assembled.http_kehuduan is client and runtime._semantic.model.is_available()
    callback_only = composition.build_zongdiaodu_composition(lambda *args: "offline")
    assert callback_only.http_kehuduan is None
    assert runtime._semantic.model.client_provider() is client
    with bind_run_context(RunContext(run_id="run.production")):
        from test_world_dictionary_support import tool_source
        receipt = runtime.facade.accept(tool_source("semantic.binding", 100))
    snapshots = runtime.store.current_candidates(life_id="life.main", principal_scope_hash="a" * 64)
    assert receipt.processed and len(snapshots) == 1 and snapshots[0].entities
    assert runtime._last_semantic_trace.status == expected_status
    assert bool(snapshots[0].active_hypotheses) == (expected_status == "COMPLETED")


@pytest.mark.parametrize("provider,model_name", [
    ("mimo", "mimo-v2.6-pro"), ("deepseek", "deepseek-flash"),
    ("deepseek_v4", "deepseek-flash"), ("deepseek_v4", "deepseek-chat"),
    ("deepseek", "deepseek-reasoner"),
])
@pytest.mark.parametrize("protocol_family,output_limit", [
    ("openai_chat_completions", 768), ("openai_responses", 768),
    ("anthropic_messages", 2048), ("anthropic_messages", 768),
])
def test_http_semantic_scope_pins_endpoint_and_excludes_task_tools_history_audio(monkeypatch, protocol_family, output_limit, provider, model_name):
    from v3.jineng import http_kehuduan as http
    from v3.model_protocol_contract import ProviderTurnEnvelope
    from v3.endpoint_security import EndpointBinding
    seen = []
    pinned = replace(endpoint(), protocol_family=protocol_family)
    if provider.startswith("deepseek"):
        pinned = replace(pinned, provider_identity="deepseek", service_preset="deepseek",
                         optimization_family=provider, model_name=model_name)
    monkeypatch.setattr(http, "duqu_model_endpoint_config", lambda *args: (_ for _ in ()).throw(AssertionError("must use snapshot")))
    monkeypatch.setattr(http, "duqu_endpoint_api_miyao", lambda *args: "fixture")
    monkeypatch.setattr(http, "_learned_skill_context", lambda: (_ for _ in ()).throw(AssertionError("task context leaked")))
    monkeypatch.setattr(http, "_jilu_l4_youhua_zhuizong", lambda *args, **kwargs: None)
    def raw_reasoning(endpoint, capability, payload):
        payload.update(max_completion_tokens=32000, max_output_tokens=32000)
        if provider.startswith("deepseek"):
            payload.update(thinking={"type": "enabled"}, reasoning_effort="high")
        if protocol_family == "anthropic_messages":
            payload["thinking"] = {"type": "enabled", "budget_tokens": 8192}
        return {}
    monkeypatch.setattr(http, "_apply_endpoint_raw_reasoning", raw_reasoning)
    monkeypatch.setattr(http, "validate_model_endpoint", lambda *args, **kwargs: EndpointBinding(
        provider_id="mimo", base_url=pinned.base_url, origin="https://example.test", host="example.test",
        port=443, official=False, custom_scope="test", resolved_ips=("203.0.113.10",)))
    def execute(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(turn=ProviderTurnEnvelope('{"hypotheses":[]}', visible_text='{"hypotheses":[]}',
            provider_identity="mimo", service_preset="mimo", protocol_family=protocol_family,
            optimization_family="mimo", model_id=pinned.model_name),
            output_repaired=False, http_status=200, latency_ms=1, retry_count=0)
    monkeypatch.setattr(http, "execute_streaming_turn", execute)
    client = http.HttpKehuduan()
    try:
        with client.scoped_native_history(({"private_history": True},)), client.scoped_native_audio(("private.wav",)):
            with client.scoped_semantic_inference(endpoint=pinned, max_output_tokens=output_limit):
                result = client.llm_diaoyong("interpret records", "records")
            assert client._native_history.get() == ({"private_history": True},)
            assert client._native_audio_paths.get() == ("private.wav",)
        assert client._semantic_inference.get() is None and not client._disable_tools.get()
    finally:
        client.guanbi()
    assert str(result) == '{"hypotheses":[]}' and len(seen) == 1
    call = seen[0]
    payload = call["canonical_payload"]
    assert call["endpoint"] is pinned and call["retry_limit"] == 1
    assert call["allow_output_repair"] is False
    assert payload["max_tokens"] == output_limit and not payload.get("tools")
    assert "max_output_tokens" not in payload and "max_completion_tokens" not in payload
    assert "__provider_history" not in payload and "private.wav" not in json.dumps(payload)
    from v3.jineng.model_transport_registry import get_model_transport
    wire = get_model_transport(protocol_family).build_request(pinned, "fixture", payload).payload
    assert wire.get("max_tokens", wire.get("max_output_tokens")) == output_limit
    if protocol_family == "anthropic_messages":
        assert (wire["thinking"] == {"type": "enabled", "budget_tokens": 2047}
            if output_limit == 2048 else wire["thinking"] == {"type": "disabled"})
    if provider.startswith("deepseek") and protocol_family == "openai_chat_completions":
        if model_name == "deepseek-reasoner":
            assert payload["thinking"] == {"type": "enabled"}
            assert payload["reasoning_effort"] == "high"
        else:
            assert payload["thinking"] == {"type": "disabled"}
            assert "reasoning_effort" not in payload
