import json
import math

import pytest

from contracts import canonical_sha256
from contracts.native_json import native_json_sha256
from omni_body_skill.tools.omni_capability import invocation_arguments_sha256, CapabilityGrantError
from total_gateway.omni_grant_authority import OmniGrantAuthority


@pytest.mark.parametrize("args", [
    {"times": [0, 1]}, {"times": [0, 0.5]}, {"amount": -0.0},
    {"amount": 1.0}, {"rows": [{"x": 1e-120, "y": -1.25}]},
    {"\ue000": 0.5, "\U00010000": {"values": [None, True, False, "0.5"]}},
])
def test_issuer_and_standalone_consumer_bind_exact_numeric_input(args):
    original = json.dumps(args, ensure_ascii=False)
    issuer = OmniGrantAuthority._invocation_hash("video.observe_frames", "video.mp4", args)
    assert issuer == invocation_arguments_sha256("video.observe_frames", "video.mp4", args)
    assert issuer == invocation_arguments_sha256("video.observe_frames", "video.mp4", json.loads(original))
    assert original == json.dumps(args, ensure_ascii=False)


def test_existing_integer_contract_hashes_remain_unchanged():
    value = {"action": "video.observe_frames", "args": {"times": [0, 1]}, "target": "video.mp4"}
    assert native_json_sha256(value) == canonical_sha256(value)
    with pytest.raises(TypeError): canonical_sha256({"signed_duration": 0.5})


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_non_finite_arguments_never_acquire_a_binding(number):
    with pytest.raises(ValueError): native_json_sha256({"times": [number]})
    with pytest.raises(CapabilityGrantError): invocation_arguments_sha256("video.observe_frames", "x", {"times": [number]})


def test_typed_hash_does_not_confuse_strings_markers_or_signed_zero():
    values = [0.5, "0.5", ["float64", (0.5).hex()], {"float64": (0.5).hex()}, 0.0, -0.0, 0, False]
    assert len({native_json_sha256({"force_float": 0.25, "value": v}) for v in values}) == len(values)
    assert native_json_sha256({"times": [0, 0.5]}) != native_json_sha256({"times": [0, 0.6]})
    assert native_json_sha256(0.5) != native_json_sha256({
        "domain": "tiangong.native-json-float-binding.v1", "value": ["float64", (0.5).hex()]})
    with pytest.raises(ValueError): native_json_sha256({"float": 0.5, "unsafe_integer": 2**60})


def test_world_observation_keeps_fractional_invocation_and_result_binding(monkeypatch):
    from world_understanding.cognition import runtime
    monkeypatch.setattr(runtime, "execution_condition", lambda: "a" * 64)
    invocation = {"action": "video.observe_frames", "target": "video.mp4", "args": {"times": [0, 0.5]}}
    result = {"ok": True, "elapsed_seconds": 1.25}
    first = runtime.observation_binding(invocation, result)
    assert first["input_sha256"] == native_json_sha256(invocation)
    assert first["result_sha256"] == native_json_sha256(result)
    result["elapsed_seconds"] = 1.5
    assert runtime.observation_binding(invocation, result)["result_sha256"] != first["result_sha256"]
