"""Real HTTP state, connection lifetime and task effects across runtime calls."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import uuid

import pytest

from omni_body_skill.tools import mcp_client as mcp
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig


@pytest.fixture
def service(tmp_path, monkeypatch):
    state = {"sessions": {}, "tasks": {}, "creates": 0, "cancels": 0, "initializes": 0,
             "task_support": True, "disconnect_cancel": False, "wrong_result": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def answer(self, data, status=200, sid=None):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if sid: self.send_header("Mcp-Session-Id", sid)
            self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method, params = req["method"], req.get("params", {})
            sid = self.headers.get("Mcp-Session-Id")
            if method == "initialize":
                state["initializes"] += 1
                sid = uuid.uuid4().hex
                state["sessions"][sid] = {"value": 0}
                result = {"protocolVersion": mcp.PROTOCOL_VERSION, "capabilities": {"tools": {},
                    "tasks": {"list": {}, "cancel": {}, "requests": {"tools": {"call": {}}}}}}
            elif sid not in state["sessions"]:
                self.answer({}, 404); return
            elif "id" not in req:
                self.answer({}, 202); return
            elif method == "tools/list":
                result = {"tools": [{"name": "work", "inputSchema": {"type": "object"},
                    "execution": {"taskSupport": "optional" if state["task_support"] else "forbidden"}}]}
            elif method == "tools/call":
                if "task" in params:
                    state["creates"] += 1
                    job = {"taskId": uuid.uuid4().hex, "status": "working", "pollInterval": 25,
                           "ttl": 60000, "createdAt": "2026-09-27T00:00:00Z", "lastUpdatedAt": "2026-09-27T00:00:00Z"}
                    state["tasks"][job["taskId"]] = job
                    result = {"task": job}
                else:
                    value = params.get("arguments", {}).get("set")
                    if value is not None: state["sessions"][sid]["value"] = value
                    result = {"content": [{"type": "text", "text": str(state["sessions"][sid]["value"])}]}
            elif method == "tasks/list":
                result = {"tasks": list(state["tasks"].values())}
            elif method == "tasks/get":
                result = state["tasks"][params["taskId"]]
            elif method == "tasks/cancel":
                state["cancels"] += 1
                result = state["tasks"][params["taskId"]]
                result["status"] = "cancelled"
                if state["disconnect_cancel"]:
                    self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
            elif method == "tasks/result":
                result = {"content": [{"type": "text", "text": "answer=73"}],
                    "_meta": {"io.modelcontextprotocol/related-task": {"taskId": "wrong" if state["wrong_result"] else params["taskId"]}}}
            else:
                self.answer({"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601}}); return
            self.answer({"jsonrpc": "2.0", "id": req["id"], "result": result}, sid=sid)
        def do_DELETE(self):
            state["sessions"].pop(self.headers.get("Mcp-Session-Id"), None)
            self.answer({}, 200)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"actual": {"transport": "streamable_http",
        "url": f"http://127.0.0.1:{server.server_port}/mcp", "environment": {"account_label": "a"}}}}))
    monkeypatch.setattr(mcp, "CONFIG_PATH", path)
    yield state, path
    mcp.close_sessions()
    server.shutdown(); server.server_close(); thread.join(2)


def test_runtime_recreation_reuses_session_but_other_run_is_isolated(service, tmp_path):
    def runtime(run): return BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id=run))
    first = runtime("r1").run("mcp.tools.list", "actual", {})
    cid = first["result"]["connection_id"]
    assert cid
    written = runtime("r1").run("mcp.tool.call", "actual", {"tool": "work", "arguments": {"set": 73}, "connection_id": cid})
    assert written["success"]
    read = runtime("r1").run("mcp.tool.call", "actual", {"tool": "work", "connection_id": cid})
    assert read["result"]["text"] == "73"
    other = runtime("r2").run("mcp.tool.call", "actual", {"tool": "work", "connection_id": cid})
    assert not other["success"] and other["error"] == "mcp.session.changed"
    assert service[0]["initializes"] == 1
    closed = runtime("r1").run("mcp.session.close", "actual", {})
    assert closed["success"] and closed["result"]["closed"] == 1
    assert not service[0]["sessions"]


def start():
    return mcp.call_tool("actual", "work", {}, task={"ttl": 60000}, scope="host-run")


def query(started, operation, **kw):
    return mcp.task_request("actual", operation, task_id=started["task"]["taskId"],
        fingerprint=started["connection_fingerprint"], scope="host-run", **kw)


def test_job_acceptance_is_pending_and_survives_transport_reconnect(service):
    started = start()
    assert started["execution_state"] == "pending" and not started["result_available"]
    assert not query(started, "result")["result_available"]
    mcp.close_sessions()  # Simulate loss of the in-process transport, not a second job submission.
    job = query(started, "get")["task"]
    assert job["status"] == "working" and service[0]["creates"] == 1
    service[0]["tasks"][job["taskId"]]["status"] = "completed"
    final = query(started, "result")
    assert final["result_available"] and final["result"]["content"][0]["text"] == "answer=73"
    assert service[0]["creates"] == 1


def test_task_support_is_checked_before_creation(service):
    service[0]["task_support"] = False
    with pytest.raises(mcp.McpClientError, match="mcp.task.tool_unsupported"): start()
    assert service[0]["creates"] == 0


def test_account_change_invalidates_session_and_job_reference(service):
    started = start()
    data = json.loads(service[1].read_text()); data["servers"]["actual"]["environment"]["account_label"] = "b"
    service[1].write_text(json.dumps(data))
    with pytest.raises(mcp.McpClientError, match="mcp.session.changed"):
        mcp.call_tool("actual", "work", {}, scope="host-run", expected_connection=started["connection_id"])
    with pytest.raises(mcp.McpClientError, match="mcp.task.connection_changed"): query(started, "get")
    assert service[0]["creates"] == 1


def test_cancel_then_disconnect_is_unknown_and_not_replayed(service):
    started = start(); service[0]["disconnect_cancel"] = True
    with pytest.raises(mcp.McpClientError, match="mcp.outcome.unknown"): query(started, "cancel")
    assert service[0]["cancels"] == 1
    assert query(started, "get")["task"]["status"] == "cancelled"
    assert not query(started, "result")["result_available"]


def test_reading_cancelled_state_and_successful_cancel_are_completed_rpc_observations(service):
    from v3.execution_integrity import execution_result_ok
    started = start()
    for operation in ("cancel", "get", "list"):
        result = query(started, operation)
        assert result["execution_state"] == "completed"
        assert execution_result_ok({"ok":True,"tool_result":{"success":True,"result":result}})
    assert not query(started,"result")["result_available"]


def test_wrong_task_result_id_cannot_be_delivered(service):
    started = start(); service[0]["tasks"][started["task"]["taskId"]]["status"] = "completed"
    service[0]["wrong_result"] = True
    with pytest.raises(mcp.McpClientError, match="mcp.task.result_mismatch"): query(started, "result")


def test_session_expiry_does_not_replay_a_write(service):
    started = start(); service[0]["sessions"].clear()
    with pytest.raises(mcp.McpClientError, match="mcp.outcome.unknown"):
        mcp.call_tool("actual", "work", {}, scope="host-run", expected_connection=started["connection_id"])
    assert service[0]["creates"] == 1
    with pytest.raises(mcp.McpClientError, match="mcp.session.changed"):
        mcp.call_tool("actual", "work", {}, scope="host-run", expected_connection=started["connection_id"])


def test_requirement_binding_checks_actual_schema_before_effect(service):
    import hashlib
    state,path=service
    config=json.loads(path.read_text());row=config["servers"]["actual"]
    row["applications"]=["google.docs"]
    digest=hashlib.sha256(b'{"type":"object"}').hexdigest()
    row["action_bindings"]={"google.docs.document.create":{"tool":"work","input_schema_sha256":digest}}
    path.write_text(json.dumps(config))
    result=mcp.call_bound_action("actual","google.docs.document.create",{"set":41},scope="host-run")
    assert result["text"]=="41" and result["requirement_action"]=="google.docs.document.create"
    assert result["native_application_execution_not_inferred"]
    config["servers"]["actual"]["action_bindings"]["google.docs.document.create"]["input_schema_sha256"]="0"*64
    path.write_text(json.dumps(config))
    with pytest.raises(mcp.McpClientError,match="mcp.binding.contract_changed"):
        mcp.call_bound_action("actual","google.docs.document.create",{"set":99},scope="host-run")
    assert all(v["value"]!=99 for v in state["sessions"].values())


def test_native_excel_requirement_can_bind_only_its_declared_application(service):
    import hashlib
    from capability_dictionary import load_dictionary
    action = "microsoft.excel.native.chart.create"
    state, path = service
    config = json.loads(path.read_text())
    row = config["servers"]["actual"]
    row["applications"] = ["microsoft.excel"]
    row["action_bindings"] = {action: {"tool": "work",
        "input_schema_sha256": hashlib.sha256(b'{"type":"object"}').hexdigest()}}
    path.write_text(json.dumps(config))
    result = mcp.call_bound_action("actual", action, {"set": 47}, scope="excel-owner")
    assert result["text"] == "47" and result["requirement_action"] == action
    assert mcp.call_tool("actual", "work", {}, scope="excel-owner")["text"] == "47"
    # A controlled transport binding does not prove native Excel execution.
    assert result["native_application_execution_not_inferred"]
    assert not load_dictionary().tools[action]["runtime"]["implemented"]
    calls = state["initializes"]
    row["applications"] = ["google.docs"]
    path.write_text(json.dumps(config))
    with pytest.raises(mcp.McpClientError, match="mcp.binding.application_mismatch"):
        mcp.call_bound_action("actual", action, {"set": 99}, scope="excel-owner")
    assert state["initializes"] == calls
    assert all(value["value"] != 99 for value in state["sessions"].values())


def test_all_requirements_remain_discoverable_without_fabricated_backends(service):
    from capability_dictionary import load_dictionary
    rows=[];offset=0
    while True:
        page=mcp.action_bindings(offset=offset,limit=100);rows.extend(page["actions"])
        if page["next_offset"] is None:break
        offset=page["next_offset"]
    assert len(rows)==len(load_dictionary().tools)==len({r["action"] for r in rows})
    assert all(not r["bindings"] and r["state"]=="no_owner_binding" for r in rows)


def test_process_exit_then_resume_queries_the_original_remote_job(service, tmp_path):
    receipt = tmp_path / "task-receipt.json"
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        str(root / path) for path in ("src", "app/backend/tiangong-backend")))
    setup = "from pathlib import Path; import json,os; from omni_body_skill.tools import mcp_client as m; m.CONFIG_PATH=Path(" + repr(str(service[1])) + "); "
    creation = "r=m.call_tool('actual','work',{},task={'ttl':60000},scope='original'); Path(" + repr(str(receipt)) + ").write_text(json.dumps(r)); os._exit(0)"
    subprocess.run([sys.executable, "-c", setup + creation], check=True, timeout=15, env=env, cwd=tmp_path)
    job = json.loads(receipt.read_text())
    service[0]["tasks"][job["task"]["taskId"]]["status"] = "completed"
    resumed = "r=json.loads(Path(" + repr(str(receipt)) + ").read_text()); print(json.dumps(m.task_request('actual','result',task_id=r['task']['taskId'],fingerprint=r['connection_fingerprint'],scope='restarted')))"
    result = json.loads(subprocess.check_output([sys.executable, "-c", setup + resumed], timeout=15, env=env, cwd=tmp_path))
    assert result["result_available"] and result["execution_state"] == "completed"
    assert result["result"]["content"][0]["text"] == "answer=73"
    assert service[0]["creates"] == 1


def test_failed_job_cannot_be_reported_as_success_even_if_result_forgets_is_error(service):
    started = start()
    service[0]["tasks"][started["task"]["taskId"]]["status"] = "failed"
    result = query(started, "result")
    assert result["is_error"] and result["execution_state"] == "failed"


def test_large_and_binary_task_results_remain_bounded_and_require_observation():
    original = {"content": [{"type":"text","text":"a"*(mcp.MAX_OUTPUT_BYTES+1)},
                            {"type":"image","data":"private-pixel-bytes"}],
                "structuredContent":{"large":"b"*500000}}
    result, truncated, omitted = mcp._task_result_projection(original)
    assert truncated and omitted == ["image"]
    assert "private-pixel-bytes" not in json.dumps(result) and "structuredContent" not in result
    assert len(result["content"][0]["text"]) == mcp.MAX_OUTPUT_BYTES


def test_busy_connection_does_not_block_another_application(service, monkeypatch):
    # Hold an actual transport initialization; unrelated scopes must still work.
    entered, release = threading.Event(), threading.Event()
    original = mcp._HttpServer.handshake
    def handshake(self):
        if threading.current_thread().name == "held-handshake":
            entered.set(); release.wait(5)
        return original(self)
    monkeypatch.setattr(mcp._HttpServer, "handshake", handshake)
    errors=[]
    def work():
        try: mcp.list_tools("actual", scope="held", timeout_ms=10000)
        except Exception as exc: errors.append(exc)
    thread=threading.Thread(target=work,name="held-handshake");thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(mcp.McpClientError,match="mcp.session.busy"):
            mcp.list_tools("actual",scope="held",timeout_ms=1000)
        assert mcp.list_tools("actual",scope="independent",timeout_ms=1000)["tools"]
    finally:
        release.set();thread.join(5)
    assert not errors and not thread.is_alive()
