from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from contracts.reliability import compute_dynamic_timeout, decide_retry
from omni_body_skill.tools.omni_body_tool import (
    BodyRuntime,
    BodyRuntimeConfig,
    _workspace_lock_timeout_seconds,
    set_body_state_query_provider,
    set_life_activity_query_provider,
)
from total_gateway.embedded_backend import EmbeddedBackendRuntime
from total_gateway.orchestration import compatibility_capability_manifest
from total_gateway.runtime import GatewayRuntime, _gateway_body_state_query
from tests.test_reliability import error_descriptor, retry_policy, timeout_policy
from v3.duihua_qiaojie import _free_will_state
from v3.shenti_zhuangtai import ShentiZhuangtai
from v3.zhili.anquan import jiancha_anquan_bianjie
from v3.zongdiaodu import (
    Zongdiaodu,
    _gongju_chongfu_chujing_renhua,
    _gongju_chongfu_zhenduan_huifu,
    _simple_chain_repeat_guard_step_meta,
    _simple_chain_natural_closeout_payload,
    _simple_chain_tool_batch_requires_order,
)

ROOT = Path(__file__).resolve().parents[1]


def test_natural_closeout_is_persona_reply_not_system_card() -> None:
    payload = _simple_chain_natural_closeout_payload(
        status="incomplete",
        reasons=["format verification is still pending"],
        quality_history=[],
        generated_attachments=[],
        tool_count=7,
    )
    instruction = payload["instruction"]
    assert "active Soul/persona voice" in instruction
    assert "never a system card" in instruction
    assert "Never claim success" in instruction


def test_autonomous_a1_a4_has_no_consecutive_action_gate() -> None:
    body = ShentiZhuangtai()
    body.anquan.lianxu_zizhu_xingdong = 999
    body.anquan.zizhu_zuida_lianxu = 1
    with mock.patch("v3.zhili.anquan.QIYONG_ZIZHU_XINGDONG", True):
        decision = jiancha_anquan_bianjie(
            body,
            {"leixing": "整理", "miaoshu": "继续整理项目资料", "canshu": {}},
        )
    assert decision["yunxu"] is True
    assert not any("连续自主行动" in reason for reason in decision["yuanyin"])


def test_free_will_projection_reports_unbounded_a1_a4_policy() -> None:
    body = ShentiZhuangtai()
    body.anquan.lianxu_zizhu_xingdong = 999
    body.anquan.zizhu_zuida_lianxu = 1
    body.qudong.qudong_yali["curiosity"] = 0.9
    dispatcher = SimpleNamespace(
        xintiao=SimpleNamespace(zhuangtai="yunxing", yunxing_zhong=True, jiange=30)
    )
    with (
        mock.patch("v3.peizhi.QIYONG_ZIZHU_XINGDONG", True),
        mock.patch("v3.duihua_qiaojie._latest_free_will_trace", return_value={}),
    ):
        state = _free_will_state(dispatcher, body)
    assert state["ready_for_action"] is True
    assert state["max_consecutive_actions"] is None
    levels = {item["level"]: item for item in state["autonomy_policy"]["levels"]}
    assert levels["A3"]["auto"] is True
    assert levels["A4"]["auto"] is True
    assert levels["A5"]["blocked"] is True


def runtime_at(root: Path, **overrides) -> BodyRuntime:
    values = {
        "workspace": str(root),
        "fact_kernel_enabled": False,
        "require_confirmation_for_a4": False,
    }
    values.update(overrides)
    return BodyRuntime(BodyRuntimeConfig(**values))


def deny_all_audits(runtime: BodyRuntime) -> None:
    runtime._probe_writable_directory = lambda _path: "fault-injected audit outage"  # type: ignore[method-assign]


def test_c01_a0_observation_continues_during_audit_outage(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path)
    deny_all_audits(runtime)
    result = runtime.run("system.health", "", {})
    assert result["success"] is True
    assert result["audit_persisted"] is False


def test_life_activity_query_is_a0_and_uses_in_process_authority(
    tmp_path: Path,
) -> None:
    captured: list[dict[str, object]] = []

    def provider(arguments: dict[str, object]) -> dict[str, object]:
        captured.append(arguments)
        return {
            "ok": True,
            "read_only": True,
            "date": "2026-07-22",
            "relative_day": "yesterday",
            "activities": [{"title": "昨天完成的行动"}],
        }

    set_life_activity_query_provider(provider)
    runtime = runtime_at(tmp_path)
    result = runtime.run(
        "life.activity.query",
        "",
        {"relative_day": "yesterday", "limit": 5},
    )
    assert result["success"] is True
    assert result["risk_level"] == "A0"
    assert result["read_only"] is True
    assert captured == [
        {
            "relative_day": "yesterday",
            "date": "",
            "status": "",
            "limit": 5,
        }
    ]


def test_body_state_query_is_a0_and_writes_gateway_fact_receipt(tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []

    def provider(arguments: dict[str, object]) -> dict[str, object]:
        captured.append(arguments)
        return {
            "ok": True,
            "read_only": True,
            "schema": "tiangong.gateway.self-body-state.v1",
            "state_sha256": "a" * 64,
            "life": {"life_id": "life_test", "state": {"status": "ALIVE"}},
            "runtime_body": {"sections": {"health": {"vitality": 1.0}}},
        }

    ledger = tmp_path / "gateway-fact-ledger"
    set_body_state_query_provider(provider)
    try:
        runtime = BodyRuntime(BodyRuntimeConfig(
            workspace=str(tmp_path),
            fact_kernel_enabled=True,
            fact_ledger_root=str(ledger),
            request_id="req_body_state_test",
            run_id="run_body_state_test",
            session_id="session_body_state_test",
        ))
        result = runtime.run(
            "life.body.state.query",
            "",
            {"sections": ["health", "context"], "recent_limit": 7},
        )
    finally:
        set_body_state_query_provider(None)

    assert result["success"] is True
    assert result["risk_level"] == "A0"
    assert result["read_only"] is True
    assert result["fact_transaction"]["state"] == "OBSERVED"
    assert result["fact_transaction"]["action"] == "life.body.state.query"
    assert captured == [{"sections": ["health", "context"], "recent_limit": 7}]
    events = [json.loads(line) for line in (ledger / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["fact_transaction"]["action"] == "life.body.state.query"
    assert events[-1]["fact_transaction"]["request_id"] == "req_body_state_test"


def test_body_state_query_rejects_unknown_sections_before_provider(tmp_path: Path) -> None:
    called = False

    def provider(_arguments):
        nonlocal called
        called = True
        return {"ok": True}

    set_body_state_query_provider(provider)
    try:
        result = runtime_at(tmp_path).run(
            "life.body.state.query",
            "",
            {"sections": ["credentials"]},
        )
    finally:
        set_body_state_query_provider(None)
    assert result["success"] is False
    assert result["status"] == "INVALID_TOOL_ARGUMENTS"
    assert result["executed"] is False
    assert called is False


def test_runtime_body_snapshot_reads_live_object_without_mutation() -> None:
    scheduler = object.__new__(Zongdiaodu)
    body = ShentiZhuangtai()
    scheduler._shenti_by_scope = {"default": body}
    body.shengmingli = 0.73
    body.qinggan.curiosity = 0.81
    body.zuijin_xingdong = [{"action": "observe"}, {"action": "verify"}]
    before = json.dumps(scheduler._state_to_plain_dict(scheduler.shenti), ensure_ascii=False, sort_keys=True)

    snapshot = scheduler.body_state_snapshot({
        "sections": ["health", "emotion", "recent_actions"],
        "recent_limit": 1,
    })

    after = json.dumps(scheduler._state_to_plain_dict(scheduler.shenti), ensure_ascii=False, sort_keys=True)
    assert snapshot["ok"] is True
    assert snapshot["read_only"] is True
    assert snapshot["sections"]["health"]["vitality"] == 0.73
    assert snapshot["sections"]["emotion"]["curiosity"] == 0.81
    assert snapshot["sections"]["recent_actions"] == [{"action": "verify"}]
    assert len(snapshot["state_sha256"]) == 64
    assert before == after


def test_gateway_composes_life_and_runtime_body_and_writes_specialized_log() -> None:
    class Backend:
        def body_state_snapshot(self, request):
            return {
                "ok": True,
                "read_only": True,
                "run_identity": {
                    "request_id": "req_gateway_body",
                    "run_id": "run_gateway_body",
                    "life_id": "life_gateway_body",
                },
                "selected_sections": request["sections"],
                "sections": {"health": {"vitality": 0.9}},
                "state_sha256": "b" * 64,
            }

    class Life:
        def request(self, method, path, body, *, timeout_seconds):
            assert (method, path, body, timeout_seconds) == (
                "GET", "/api/v1/v3/life/panel", {}, 10,
            )
            return 200, {
                "ok": True,
                "life_id": "life_gateway_body",
                "projection_status": "authoritative",
                "generated_at": "2026-08-01T00:00:00Z",
                "state": {"status": "ALIVE"},
                "chat_gate": {"ready": True},
                "sections": {"overview": {"available": True}},
                "context": {"current_context_tokens": 1200, "token_budget": 120000},
                "budget": {"context_utilization_milli": 10},
                "summary": {"current_focus": "idle"},
            }, "application/json"

    runtime = SimpleNamespace(backend_service=Backend(), life_service=Life())
    with mock.patch("total_gateway.runtime.diagnostic_log") as log:
        result = _gateway_body_state_query(
            runtime,
            {"sections": ["health", "context", "summary"], "recent_limit": 3},
        )
    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["life"]["state"]["status"] == "ALIVE"
    assert result["life"]["context"]["current_context_tokens"] == 1200
    assert result["runtime_body"]["sections"]["health"]["vitality"] == 0.9
    assert len(result["state_sha256"]) == 64
    assert log.call_args.kwargs["filename"] == "gateway_body_state_reads.log"
    assert '"status":"observed"' in log.call_args.args[0]


def test_c02_a2_local_creation_continues_with_explicit_audit_warning(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path)
    deny_all_audits(runtime)
    result = runtime.run("file.mkdir", "created", {})
    assert result["success"] is True
    assert (tmp_path / "created").is_dir()
    assert result["audit_persisted"] is False


def test_c03_a3_durable_write_continues_when_only_audit_is_unavailable(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path)
    deny_all_audits(runtime)
    result = runtime.run("file.write", "result.txt", {"content": "completed"})
    assert result["success"] is True
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "completed"
    assert result["audit_persisted"] is False


def test_c04_a4_uses_workspace_emergency_audit_without_losing_execution(tmp_path: Path) -> None:
    victim = tmp_path / "delete-me.txt"
    victim.write_text("x", encoding="utf-8")
    runtime = runtime_at(tmp_path)
    runtime._probe_writable_directory = (  # type: ignore[method-assign]
        lambda path: "primary blocked" if path == runtime.audit_dir else ""
    )
    result = runtime.run("file.delete_to_trash", "delete-me.txt", {})
    assert result["success"] is True
    assert result["audit_persisted"] is True
    assert not victim.exists()
    records = list(runtime.emergency_audit_dir.glob("*.json"))
    assert records
    assert json.loads(records[-1].read_text(encoding="utf-8"))["audit_fallback"] is True


def test_c05_a4_fails_before_side_effect_when_no_audit_location_exists(tmp_path: Path) -> None:
    victim = tmp_path / "keep-me.txt"
    victim.write_text("safe", encoding="utf-8")
    runtime = runtime_at(tmp_path)
    deny_all_audits(runtime)
    result = runtime.run("file.delete_to_trash", "keep-me.txt", {})
    assert result["success"] is False
    assert result["error_type"] == "AuditUnavailable"
    assert victim.read_text(encoding="utf-8") == "safe"


def test_c06_a5_remains_hard_blocked_under_execution_first_policy(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path)
    result = runtime.run("voice.clone_authorized", "voice.wav", {})
    assert result["success"] is False
    assert result["risk_level"] == "A5"
    assert "hard-gate" in result["reason"]


def test_c07_read_only_observation_of_hardlink_is_allowed(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    linked = tmp_path / "linked.txt"
    original.write_text("observable", encoding="utf-8")
    try:
        os.link(original, linked)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    result = runtime_at(tmp_path).run("file.read", "linked.txt", {})
    assert result["success"] is True
    assert result["content"] == "observable"


def test_c08_hardlink_mutation_is_blocked_without_damaging_original(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    linked = tmp_path / "linked.txt"
    original.write_text("unchanged", encoding="utf-8")
    try:
        os.link(original, linked)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    result = runtime_at(tmp_path).run("file.write", "linked.txt", {"content": "tampered"})
    assert result["success"] is False
    assert "hard-linked" in result["message"]
    assert original.read_text(encoding="utf-8") == "unchanged"


def test_c09_malformed_lock_timeout_environment_cannot_stop_all_mutations() -> None:
    with mock.patch.dict(os.environ, {"TIANGONG_WORKSPACE_LOCK_TIMEOUT_SECONDS": "not-a-number"}):
        assert _workspace_lock_timeout_seconds() == 900.0


def test_c10_lock_timeout_is_bounded_but_supports_long_tasks() -> None:
    with mock.patch.dict(os.environ, {"TIANGONG_WORKSPACE_LOCK_TIMEOUT_SECONDS": "1"}):
        assert _workspace_lock_timeout_seconds() == 10.0
    with mock.patch.dict(os.environ, {"TIANGONG_WORKSPACE_LOCK_TIMEOUT_SECONDS": "7200"}):
        assert _workspace_lock_timeout_seconds() == 3600.0


def test_c11_concurrent_appends_are_serialized_without_lost_lines(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path)
    (tmp_path / "journal.txt").write_text("", encoding="utf-8")

    def append(index: int) -> bool:
        return bool(runtime.run("file.append", "journal.txt", {"content": f"line-{index}\n"})["success"])

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert all(pool.map(append, range(40)))
    lines = (tmp_path / "journal.txt").read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == sorted(f"line-{index}" for index in range(40))


def test_c12_empty_tool_args_remain_executable(tmp_path: Path) -> None:
    result = runtime_at(tmp_path).run("system.health", "", {})
    assert result["success"] is True
    assert "healthy" in result


def test_c13_unknown_action_returns_structured_error_instead_of_crashing(tmp_path: Path) -> None:
    result = runtime_at(tmp_path).run("unknown.action", "", {})
    assert result["success"] is False
    assert result["status"] == "INVALID_TOOL_ARGUMENTS"
    assert result["executed"] is False
    assert result["retryable"] is False


def test_c14_missing_optional_adapter_returns_actionable_structured_result(tmp_path: Path) -> None:
    result = runtime_at(tmp_path).run("browser.edge.click", "button", {})
    assert result["success"] is False
    assert result.get("requires_adapter") or "adapter" in result.get("message", "").lower()


def test_c15_cross_platform_unicode_workspace_path_executes_normally(tmp_path: Path) -> None:
    workspace = tmp_path / "project-项目-😀"
    workspace.mkdir()
    runtime = runtime_at(workspace)
    result = runtime.run("file.write", "output-结果.txt", {"content": "UTF-8 stable"})
    assert result["success"] is True
    assert (workspace / "output-结果.txt").read_bytes() == b"UTF-8 stable"


def test_c16_write_read_roundtrip_preserves_crlf_and_non_bmp_text(tmp_path: Path) -> None:
    content = "first\r\n中文 😀\r\nlast\n"
    runtime = runtime_at(tmp_path)
    assert runtime.run("file.write", "roundtrip.txt", {"content": content})["success"]
    result = runtime.run("file.read", "roundtrip.txt", {})
    assert result["content"] == content


def test_c17_python_sandbox_forces_canonical_utf8_output(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path, allow_python=True)
    result = runtime.run("python.run", "", {"code": "print('English 中文 😀')"})
    assert result["success"] is True, result
    execution = result["execution"]
    assert "English 中文 😀" in execution["stdout"]
    assert execution["stdout_encoding"] == "utf-8"


def test_c18_python_sandbox_decodes_gb18030_without_replacement(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path, allow_python=True)
    code = "import sys; sys.stdout.buffer.write('控制台中文'.encode('gb18030'))"
    result = runtime.run("python.run", "", {"code": code})
    assert result["success"] is True, result
    execution = result["execution"]
    assert execution["stdout"] == "控制台中文"
    assert execution["legacy_output_encoding"] is True
    assert "�" not in execution["stdout"]


def test_c19_python_sandbox_decodes_utf16_bom_output(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path, allow_python=True)
    code = "import codecs,sys; sys.stdout.buffer.write(codecs.BOM_UTF16_LE + 'wide'.encode('utf-16-le'))"
    result = runtime.run("python.run", "", {"code": code})
    assert result["success"] is True, result
    assert result["execution"]["stdout"] == "wide"
    assert result["execution"]["stdout_encoding"] == "utf-16-le"


def test_c20_undecodable_process_output_fails_explicitly_not_with_corrupted_text(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path, allow_python=True)
    code = "import sys; sys.stdout.buffer.write(bytes([0x81]))"
    result = runtime.run("python.run", "", {"code": code})
    assert result["success"] is False
    assert result["error_type"] in {"PortableTextError", "SandboxError"}
    assert "�" not in result.get("message", "")


def test_c21_outer_model_lease_supports_hour_long_deliverables() -> None:
    manifest = compatibility_capability_manifest("a" * 64, generated_at_ms=1_000)
    action = manifest.actions[0]
    assert action.max_runtime_ms == 3_600_000
    assert action.max_output_bytes == 536_870_912
    assert action.max_tool_calls == 1_000


def test_c22_outer_model_capability_is_internal_not_model_self_authority() -> None:
    action = compatibility_capability_manifest("a" * 64, generated_at_ms=1_000).actions[0]
    assert action.model_visible is False
    assert action.risk_class == "A3"


def test_c23_model_tool_schema_does_not_expose_confirmation_or_privilege_switches() -> None:
    tool_source = (ROOT / "readable-python-source/omni_body_skill/api/v1/v3/tools/omni_body.py").read_text(encoding="utf-8")
    contract = json.loads((ROOT / "readable-python-source/omni_body_skill/api/v1/v3/tools/omni_body.tool.json").read_text(encoding="utf-8"))
    adapter = (ROOT / "readable-python-source/omni_body_skill/model_adapters/core.py").read_text(encoding="utf-8")
    assert '"required": ["action"]' in tool_source
    assert contract["parameters"]["required"] == ["action"]
    assert 'schema["required"] = ["action"]' in adapter
    schema_text = json.dumps(contract["parameters"], ensure_ascii=False)
    for forbidden in ("allow_shell", "allow_python", "allow_absolute_paths", "confirmed", "confirm"):
        assert forbidden not in schema_text


def test_c24_same_reply_mutations_are_forced_into_dependency_order() -> None:
    tools = [
        ("omni_body", {"action": "file.write", "target": "a.txt", "args": {"content": "x"}}, 1, "c1"),
        ("omni_body", {"action": "file.rename", "target": "a.txt", "args": {"new_name": "b.txt"}}, 2, "c2"),
        ("omni_body", {"action": "file.read", "target": "b.txt", "args": {}}, 3, "c3"),
    ]
    assert _simple_chain_tool_batch_requires_order(tools) is True


def test_c25_independent_read_only_tools_remain_parallelizable() -> None:
    tools = [
        ("omni_body", {"action": "file.read", "target": "a.txt", "args": {}}, 1, "c1"),
        ("omni_body", {"action": "file.hash", "target": "b.txt", "args": {}}, 2, "c2"),
    ]
    assert _simple_chain_tool_batch_requires_order(tools) is False


def test_c25_repeat_guard_reports_misspelled_leaf_without_requesting_a_new_workspace() -> None:
    reply = _gongju_chongfu_zhenduan_huifu(
        "omni_body",
        {"action": "file.read", "target": "tests/testinventory.py"},
        {
            "last_result": {
                "zhuangtai": "cuowu",
                "cuowu": "[PATH_NOT_FOUND] tests/testinventory.py",
            }
        },
        "verify the existing inventory project",
    )

    assert "目标文件或目录名未匹配" in reply
    assert "父目录" in reply
    assert "不需要用户重新提供项目路径" in reply
    assert "请确认这个目录在本机能打开" not in reply


def test_c25_repeat_guard_keeps_diagnostic_internal_and_public_reply_short() -> None:
    repeated = {
        "last_result": {
            "zhuangtai": "cuowu",
            "cuowu": "[PATH_NOT_FOUND] tests/missing.py",
        }
    }
    diagnostic = _gongju_chongfu_zhenduan_huifu(
        "omni_body",
        {"action": "file.read", "target": "tests/missing.py"},
        repeated,
        "inspect the project",
    )
    public_reply = _gongju_chongfu_chujing_renhua(repeated)
    meta = _simple_chain_repeat_guard_step_meta(repeated, diagnostic)

    assert "PATH_NOT_FOUND" in diagnostic
    assert "PATH_NOT_FOUND" not in public_reply
    assert meta["visibility"] == "internal"
    assert meta["diagnostic"] == diagnostic
    assert meta["last_result"] == repeated["last_result"]


def test_c26_retryable_failure_before_side_effect_continues_with_backoff() -> None:
    decision = decide_retry(error_descriptor(), retry_policy(), now_ms=20_000, elapsed_ms=1_000)
    assert decision.should_retry is True
    assert decision.should_reconcile is False


def test_c27_ambiguous_side_effect_is_reconciled_not_blindly_repeated() -> None:
    decision = decide_retry(
        error_descriptor(disposition="AMBIGUOUS", side_effect_started=True, error_code="tool.outcome_unknown"),
        retry_policy(),
        now_ms=20_000,
        elapsed_ms=1_000,
    )
    assert decision.should_retry is False
    assert decision.should_reconcile is True


def test_c28_dynamic_timeout_allows_large_slow_transfer_with_bounded_ceiling() -> None:
    decision = compute_dynamic_timeout(
        timeout_policy(), payload_bytes=100_000_000, observed_throughput_bps=10_000
    )
    assert decision.allowed is True
    assert decision.timeout_ms == 600_000


def test_c29_stop_control_bypasses_busy_model_lane() -> None:
    core_lock = threading.RLock()
    started = threading.Event()
    release = threading.Event()

    class Bridge:
        _core_execution_lock = core_lock

        def chuli_duihua(self, _text, _user, _context):
            with core_lock:
                started.set()
                release.wait(3)
                return json.dumps({"huifu": "done"})

        def run_control(self, payload):
            return {"ok": True, "request_id": payload.get("request_id")}

    class Module:
        @staticmethod
        def _safe_bridge_json(value, *, source):
            del source
            return json.loads(value)

    backend = EmbeddedBackendRuntime.__new__(EmbeddedBackendRuntime)
    backend._lock = threading.RLock()
    backend._closed = False
    backend.qiaojie = Bridge()
    backend._module = Module()
    chat_thread = threading.Thread(
        target=lambda: backend.request("POST", "/api/v1/gateway/internal/inbound", {"text": "long", "request_id": "req"}),
        daemon=True,
    )
    chat_thread.start()
    assert started.wait(1)
    begin = time.monotonic()
    status, payload, _ = backend.request("POST", "/api/v1/run/control", {"request_id": "req", "action": "stop"})
    elapsed = time.monotonic() - begin
    assert status == 200 and payload["ok"] is True
    assert elapsed < 0.5
    release.set()
    chat_thread.join(timeout=2)
    assert not chat_thread.is_alive()


def test_c30_shutdown_never_tears_down_life_after_busy_execution_runtime() -> None:
    calls: list[str] = []

    class Component:
        def __init__(self, name: str, fail: bool = False):
            self.name = name
            self.fail = fail

        def close(self):
            calls.append(self.name)
            if self.fail:
                raise RuntimeError(self.name + "-busy")

    runtime = object.__new__(GatewayRuntime)
    runtime.cutover = Component("cutover")
    runtime.orchestration = Component("orchestration")
    runtime.backend_service = Component("backend", fail=True)
    runtime.communication_service = Component("communication")
    runtime.life_service = Component("life")
    runtime.facts = Component("facts")
    runtime.objects = Component("objects")
    runtime.store = Component("store")
    runtime.lease = Component("lease")
    with pytest.raises(RuntimeError, match="execution failed to close"):
        runtime.close()
    assert calls == ["cutover", "orchestration", "backend"]
