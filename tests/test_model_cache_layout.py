"""Stable native prefixes with fresh WU data and lossless receipt deduplication."""
import dataclasses
import json
from copy import deepcopy

import pytest

from test_dictionary_model_lifecycle import endpoint
from v3.jineng import http_kehuduan as http
from v3.jineng.model_transport_contract import (
    StreamState, compact_native_observations, extract_native_roundtrip_history,
)
from v3.jineng.model_transport_registry import get_model_transport


def pair(transport, ep, number, result):
    state = StreamState(tool_items={"0": {"id": f"call_{number}", "provider_item_id": f"item_{number}",
        "name": "omni_body", "arguments_text": '{"action":"file.read","target":"x"}',
        "sequence_index": 0}}, finish_reason="tool_calls")
    return {"turn": transport.finalize_turn(ep, state), "results": [result]}


def test_world_refresh_keeps_stable_prompt_but_preserves_exact_fresh_context():
    stable = "INSTRUCTIONS\n\n[Model tool protocol]\nABI"
    outputs = []
    for packet in ("old", "new"):
        slot = f"[WORLD_CONTEXT_SLOT]\npacket_id={packet}; signed_identity={packet}; fact={packet}\n[/WORLD_CONTEXT_SLOT]"
        system, context = http._split_runtime_context("INSTRUCTIONS\n\n" + slot + "\n\n[Model tool protocol]\nABI")
        assert context == slot and packet not in system
        outputs.append(system)
    assert outputs[0] == outputs[1]
    assert http._split_runtime_context(stable) == (stable, "")
    incomplete = "INSTRUCTIONS [WORLD_CONTEXT_SLOT] incomplete"
    assert http._split_runtime_context(incomplete) == (incomplete, "")


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "openai_responses", "anthropic_messages"])
def test_native_history_grows_before_host_checks_feedback_and_current_world(endpoint, protocol):
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    transport = get_model_transport(protocol)
    history = [pair(transport, ep, 1, {"ok": True, "content": "original immutable bytes"})]
    def wire(history, current):
        return transport.build_request(ep, "test", {
            "messages": [{"role": "system", "content": "stable policy"}, {"role": "user", "content": "stable task"},
                         {"role": "assistant", "content": f"HOST_WARNING_{current}"},
                         {"role": "user", "content": f"ADVERSARIAL_FEEDBACK_{current}"}],
            "__provider_history": history, "__native_observations_compacted": True,
            "__cache_ordered_history": True, "__runtime_context": f"WORLD_{current}",
        }).payload
    first = wire(history, "first")
    history.append(pair(transport, ep, 2, {"ok": False, "error": "real failure"}))
    second = wire(history, "second")
    key = "input" if protocol == "openai_responses" else "messages"
    first_items, second_items = first[key], second[key]
    # Everything through the first complete native pair is byte-identical.
    assert first_items[:-3] == second_items[:len(first_items) - 3]
    text = json.dumps(second)
    assert "WORLD_second" in text and "WORLD_first" not in text
    assert "HOST_WARNING_second" in text and "ADVERSARIAL_FEEDBACK_second" in text
    assert text.count('"call_1"') == 2 and text.count('"call_2"') == 2
    assert "original immutable bytes" in text and "real failure" in text
    assert "__cache_ordered_history" not in text and "__runtime_context" not in text
    assert http._cache_prefix_observation(first)["cache_prefix_sha256"] == http._cache_prefix_observation(second)["cache_prefix_sha256"]


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "openai_responses", "anthropic_messages"])
def test_exact_composition_leaf_is_sent_once_without_losing_host_failure(endpoint, protocol):
    ep = dataclasses.replace(endpoint, protocol_family=protocol)
    transport = get_model_transport(protocol)
    leaf = {"schema": "tiangong.v3.omni_body.v1", "ok": True, "result": {"content": "UNIQUE_LEAF_BYTES" * 1000}}
    result = {"schema": "tiangong.task-composition-result.v1", "results": [{"leaf_id": "s1.1", "result": leaf}]}
    history = [pair(transport, ep, 1, result)]
    observation = {"tool_action": "file.read", "tool_result": leaf, "ok": False, "failures": ["HOST_READBACK_FAILED"]}
    messages = [json.dumps(observation)]
    before = deepcopy(result)
    validated = extract_native_roundtrip_history({"__provider_history": history}, ep)
    compacted, changed = compact_native_observations(messages, [observation], validated)
    assert changed and len(compacted[0]) < 500
    assert "HOST_READBACK_FAILED" in compacted[0] and "UNIQUE_LEAF_BYTES" not in compacted[0]
    wire = transport.build_request(ep, "test", {
        "messages": [{"role": "user", "content": "task"}, {"role": "assistant", "content": compacted[0]}],
        "__provider_history": history, "__native_observations_compacted": True, "__cache_ordered_history": True,
    }).payload
    serialized = json.dumps(wire)
    assert serialized.count("UNIQUE_LEAF_BYTES") == 1000 and "HOST_READBACK_FAILED" in serialized
    assert result == before
    # A lookalike wrapper or changed receipt is never a dedup match.
    wrong = deepcopy(result); wrong["schema"] = "untrusted_wrapper"
    bad_history = extract_native_roundtrip_history({"__provider_history": [pair(transport, ep, 2, wrong)]}, ep)
    assert compact_native_observations(messages, [observation], bad_history) == (messages, False)
    observation["tool_result"] = {**leaf, "ok": False}
    assert compact_native_observations(messages, [observation], validated) == (messages, False)


def test_provider_changes_keep_legacy_observations(endpoint):
    transport = get_model_transport(endpoint.protocol_family)
    history = [pair(transport, endpoint, 1, {"content": "private-old-provider"})]
    other = dataclasses.replace(endpoint, model_name="new-model")
    wire = transport.build_request(other, "test", {
        "messages": [{"role": "user", "content": "task"}, {"role": "assistant", "content": "FALLBACK_FACT"}],
        "__provider_history": history, "__cache_ordered_history": True, "__runtime_context": "LATEST_WORLD",
    }).payload
    serialized = json.dumps(wire)
    assert "FALLBACK_FACT" in serialized and "LATEST_WORLD" in serialized
    assert "private-old-provider" not in serialized and "call_1" not in serialized


def test_large_window_keeps_valid_history_beyond_old_character_cap():
    from v3.zongdiaodu import _simple_chain_bound_native_history
    history = [{"turn": f"turn-{i}", "results": [{"content": "x" * 30000}]} for i in range(5)]
    before = deepcopy(history)
    assert _simple_chain_bound_native_history(history, window_tokens=262144, fixed_tokens=12000) == 0
    assert history == before


def test_history_eviction_is_bounded_batched_and_never_splits_a_pair():
    from v3.zongdiaodu import _simple_chain_bound_native_history
    history = [{"turn": f"turn-{i}", "results": [{"content": "x" * 3000}]} for i in range(12)]
    before = deepcopy(history)
    removed = _simple_chain_bound_native_history(history, window_tokens=12000, fixed_tokens=1000)
    assert removed > 1 and history == before[removed:]
    assert _simple_chain_bound_native_history(history, window_tokens=12000, fixed_tokens=1000) == 0
    tiny = deepcopy(before[-1:])
    _simple_chain_bound_native_history(tiny, window_tokens=100, fixed_tokens=100)
    assert tiny == before[-1:]
