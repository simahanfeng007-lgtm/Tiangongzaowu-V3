"""Explicit A0 contracts validated against real filesystem handler results.

These are local contract tests, not model or signed gateway acceptance.
"""
from copy import deepcopy
import hashlib

import pytest

from contracts import canonical_sha256
from omni_body_skill.tool_contracts import build_action_schema_catalog
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
from total_gateway.action_registry import ActionRegistryError, compile_action_authority


@pytest.fixture(scope="module")
def catalog():
    capabilities = {action: {
        "id": action, "risk": "A0", "effect": "read",
        "handler": "_action_" + action.replace(".", "_"),
        "alias_to": "", "executable": True,
    } for action in ("file.read", "file.list", "file.hash")}
    for action, descriptor in build_action_schema_catalog(capabilities).items():
        capabilities[action].update(descriptor)
    digest = canonical_sha256(capabilities)
    manifest = {"schema": "tiangong.v3.capability_manifest.v1",
        "source_hash": digest, "total": 3, "executable": 3, "unavailable": 0,
        "capabilities": capabilities,
        "validation": {"ok": True, "source_hash": digest, "executable_without_route": []}}
    return compile_action_authority(manifest, generated_at_ms=0).schema_catalog


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.delenv("TIANGONG_OMNI_BODY_STATE_ROOT", raising=False)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), fact_kernel_enabled=False))
    text = tmp_path / "orders.csv"
    text.write_bytes("订单,金额\nA001,12.50\n".encode("utf-8"))
    binary = tmp_path / "unknown.custombinary"
    binary.write_bytes(b"\x00\xff\x01\x80")
    directory = tmp_path / "subdirectory"
    directory.mkdir()
    return runtime, text, binary, directory


def _raw_result(runtime, action, target, args):
    result = runtime.run(action, str(target), args)
    assert result["success"], result
    return {"schema": "tiangong.v3.omni_body.v1", "ok": True,
        "zhuangtai": "wancheng", "gongju": "omni_body", "action": action,
        "target": str(target), "result": result, "llm_brief": "completed",
        "evidence": result.get("evidence", {})}


@pytest.mark.parametrize("binary,max_chars", [(False, 200), (False, 3), (True, 200), (True, 2)])
def test_real_text_and_binary_read_variants(catalog, files, binary, max_chars):
    runtime, text, binary_path, _ = files
    target = binary_path if binary else text
    args = {"binary": binary, "max_chars": max_chars}
    entry = catalog.resolve("file.read", "omni-registry-v1",
                            require_explicit=True, require_result_explicit=True)
    entry.validate_exact("file.read", str(target), args, workspace=runtime.workspace,
                         available_actions=("file.read", "file.list", "file.hash"))
    payload = _raw_result(runtime, "file.read", target, args)
    catalog.validate_result_exact("file.read", "omni-registry-v1", payload)
    selector = next(item for item in entry.value_schemas if item.json_pointer == "/result")
    catalog.validate_value_exact(selector.value_schema_sha256, payload["result"])
    expected_size = len(target.read_bytes()) if binary else len(target.read_text(encoding="utf-8"))
    assert payload["result"]["truncated"] is (expected_size > max_chars)
    if not binary:
        assert payload["result"]["content"] == target.read_text(encoding="utf-8")[:max_chars]


def test_real_list_and_hash_preserve_full_observation(catalog, files):
    runtime, text, _, directory = files
    listing = _raw_result(runtime, "file.list", runtime.workspace, {})
    catalog.validate_result_exact("file.list", "omni-registry-v1", listing)
    assert listing["result"]["count"] == len(listing["result"]["entries"])
    entry = next(row for row in listing["result"]["entries"] if row["name"] == directory.name)
    assert entry["type"] == "dir" and entry["size_bytes"] is None
    hashed = _raw_result(runtime, "file.hash", text, {})
    catalog.validate_result_exact("file.hash", "omni-registry-v1", hashed)
    assert hashed["result"]["sha256"] == hashlib.sha256(text.read_bytes()).hexdigest()
    for action, payload in (("file.list", listing), ("file.hash", hashed)):
        schema = catalog.resolve(action, "omni-registry-v1", require_result_explicit=True)
        selector = next(item for item in schema.value_schemas if item.json_pointer == "/result")
        catalog.validate_value_exact(selector.value_schema_sha256, payload["result"])


def test_incomplete_ambiguous_and_failed_reads_cannot_be_success(catalog, files):
    runtime, text, _, _ = files
    original = _raw_result(runtime, "file.read", text, {})
    for alteration in ("missing_content", "both_variants", "false_success", "bad_type"):
        payload = deepcopy(original)
        if alteration == "missing_content":
            del payload["result"]["content"]
        elif alteration == "both_variants":
            payload["result"].update(base64_preview="", size_bytes=0, mime=None)
        elif alteration == "false_success":
            payload["result"]["success"] = False
        else:
            payload["result"]["size_chars"] = True
        with pytest.raises(ActionRegistryError):
            catalog.validate_result_exact("file.read", "omni-registry-v1", payload)


@pytest.mark.parametrize("action,args", [
    ("file.read", {"binary": "false"}),
    ("file.read", {"max_chars": True}),
    ("file.read", {"max_chars": -1}),
    ("file.read", {"encoding": "gbk"}),
    ("file.list", {"recursive": 1}),
    ("file.list", {"max_results": 0}),
    ("file.list", {"pattern": ""}),
    ("file.list", {"pattern": "../*"}),
    ("file.list", {"pattern": "..\\*"}),
    ("file.list", {"pattern": "D:\\outside\\*"}),
    ("file.hash", {"risk": "A0"}),
])
def test_invalid_filesystem_args_fail_before_dispatch(catalog, tmp_path, action, args):
    entry = catalog.resolve(action, "omni-registry-v1", require_explicit=True)
    with pytest.raises((ActionRegistryError, ValueError)):
        entry.validate_exact(action, str(tmp_path / "input.txt"), args, workspace=tmp_path,
                             available_actions=("file.read", "file.list", "file.hash"))
