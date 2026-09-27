"""Actual transport and byte observations, not application success fixtures."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sqlite3
import threading

import pytest

from capability_dictionary import load_dictionary
from omni_body_skill.tools import mcp_client
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig


@pytest.fixture
def runtime(tmp_path):
    return BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="ontology-observations"))


@pytest.fixture
def http_mcp(tmp_path, monkeypatch):
    state = {"calls": [], "created": 0, "deleted": 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append((value, dict(self.headers)))
            if self.path == "/redirect":
                self.send_response(307); self.send_header("Location", "/mcp"); self.end_headers(); return
            if "id" not in value:
                self.send_response(202); self.end_headers(); return
            if value["method"] == "initialize":
                result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture"}}
            elif value["method"] == "tools/list":
                second = value["params"].get("cursor") == "page2"
                result = {"tools": [{"name": "read" if second else "create", "inputSchema": {"type": "object"}}]}
                if not second: result["nextCursor"] = "page2"
            else:
                name = value["params"]["name"]
                if name == "create_then_disconnect":
                    state["created"] += 1
                    self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
                result = {"content": [{"type": "text", "text": "actual=41; secret=fixture-credential"}],
                          "structuredContent": {"actual": 41, "server_report_only": True}}
            body = json.dumps({"jsonrpc": "2.0", "id": value["id"], "result": result}).encode()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Mcp-Session-Id", "fixture-session"); self.end_headers()
            self.wfile.write(b"event: message\ndata: " + body + b"\n\n")
        def do_DELETE(self):
            state["deleted"] += 1
            self.send_response(204); self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    path = tmp_path / "owner-mcp.json"
    path.write_text(json.dumps({"servers": {"office-test": {
        "transport": "streamable_http", "url": origin + "/mcp",
        "session_mode": "invocation",
        "header_env": {"Authorization": "ONTOLOGY_TEST_AUTH"},
        "applications": ["sqlite"], "environment": {"location": origin, "workspace": "fixture-workspace"}
    }}}))
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", path)
    monkeypatch.setenv("ONTOLOGY_TEST_AUTH", "Bearer fixture-credential")
    yield state, path, origin
    server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_discovery_shows_environment_but_does_not_connect_or_leak_credentials(http_mcp, runtime):
    state, _, origin = http_mcp
    result = runtime.run("mcp.servers.list", None, {"app_id": "sqlite"})
    assert result["success"] and not state["calls"]
    app = result["result"]["applications"][0]
    assert app["connections"][0]["environment"]["location"] == origin
    assert app["connection_state"] == "configured_not_verified"
    assert "fixture-credential" not in json.dumps(result)


def test_real_http_handshake_pagination_call_and_full_readback(http_mcp, runtime, tmp_path):
    state, _, _ = http_mcp
    first = runtime.run("mcp.tools.list", "office-test", {})
    assert first["success"] and first["result"]["next_cursor"] == "page2"
    assert not first["result"]["complete"]
    second = runtime.run("mcp.tools.list", "office-test", {"cursor": "page2"})
    assert second["result"]["complete"] and second["result"]["tools"][0]["name"] == "read"
    called = runtime.run("mcp.tool.call", "office-test", {"tool": "read"})
    assert called["success"] and called["result"]["structured"]["actual"] == 41
    evidence = called["result"]["evidence"]
    raw = (tmp_path / evidence["rel_path"]).read_text()
    assert "fixture-credential" not in raw and "actual=41" in raw
    assert any(h.get("Mcp-Session-Id") == "fixture-session" for _, h in state["calls"])
    assert state["deleted"] == 3


def test_disconnect_after_effect_is_unknown_and_is_not_retried(http_mcp, runtime):
    state, _, _ = http_mcp
    result = runtime.run("mcp.tool.call", "office-test", {"tool": "create_then_disconnect"})
    assert result["success"] is False and result["ambiguous_effect"] is True
    assert result["reconciliation_required"] and state["created"] == 1


def test_missing_credential_and_redirect_never_forward_a_tool_call(http_mcp, runtime, monkeypatch):
    state, path, origin = http_mcp
    monkeypatch.delenv("ONTOLOGY_TEST_AUTH")
    assert runtime.run("mcp.tools.list", "office-test", {})["error"] == "mcp.credentials.missing"
    assert not state["calls"]
    monkeypatch.setenv("ONTOLOGY_TEST_AUTH", "Bearer fixture-credential")
    cfg = json.loads(path.read_text()); cfg["servers"]["office-test"]["url"] = origin + "/redirect"
    path.write_text(json.dumps(cfg))
    result = runtime.run("mcp.tools.list", "office-test", {})
    assert not result["success"] and result["error"] == "mcp.http.redirect_denied"
    assert len(state["calls"]) == 1


def test_sqlite_read_only_contract_is_enforced_by_database_not_sql_prefix(runtime, tmp_path):
    path = tmp_path / "data.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("create table data(value integer)")
        db.executemany("insert into data values(?)", [(i,) for i in range(105)])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for query in ("delete from data", "with x as (select 1) delete from data", "pragma user_version=3", "attach database 'escape.sqlite' as other"):
        result = runtime.run("sqlite.query", path.name, {"query": query})
        assert not result["success"], query
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    result = runtime.run("sqlite.query.run", path.name, {"query": "select * from data"})
    assert result["success"] and result["routed_to"] == "sqlite.query"
    assert result["result"]["truncated"] and result["result"]["row_count"] is None
    assert result["result"]["returned_rows"] == 100
    failed = runtime.run("sqlite.query", path.name, {"query": "select * from data", "output_csv": "partial.csv"})
    assert not failed["success"] and not (tmp_path / "partial.csv").exists()
    written = runtime.run("sqlite.query", path.name, {"query": "with x as (select 1) insert into data values(106)", "confirmed": True})
    assert written["success"]
    with sqlite3.connect(path) as db: assert db.execute("select count(*) from data").fetchone()[0] == 106


def test_static_fetch_and_download_do_not_hide_truncation(runtime, tmp_path):
    (tmp_path / "page.html").write_text("<html><body>" + "A" * 200 + "</body></html>")
    result = runtime.run("browser.chrome.goto", "page.html", {"max_bytes": 40})
    assert result["success"] and not result["browser_executed"] and result["body_truncated"]
    downloaded = runtime.run("web.download", "page.html", {"max_bytes": 40, "output": "partial.html"})
    assert not downloaded["success"] and not (tmp_path / "partial.html").exists()


def test_sqlite_blob_and_hardlink_export_preserve_database(runtime, tmp_path):
    db_path = tmp_path / "blob.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("create table item(value blob)")
        db.execute("insert into item values(?)", (b"\x00\xff",))
    import os
    original = db_path.read_bytes()
    result = runtime.run("sqlite.query", db_path.name, {"query": "select * from item"})
    assert result["result"]["rows"] == [[{"type": "blob", "hex": "00ff"}]]
    assert json.loads(json.dumps(result))["success"]
    os.link(db_path, tmp_path / "alias.csv")
    result = runtime.run("sqlite.query", db_path.name, {"query": "select * from item", "output_csv": "alias.csv"})
    assert not result["success"] and db_path.read_bytes() == original


def test_browser_refuses_to_overwrite_the_source_page(runtime, tmp_path):
    pytest.importorskip("playwright")
    page = tmp_path / "page.html"
    page.write_text("<html><body>keep this page</body></html>")
    original = page.read_bytes()
    result = runtime.run("browser.playwright.screenshot", page.name, {"output": page.name})
    assert not result["success"] and "overwrite" in result["message"]
    assert page.read_bytes() == original


def test_declared_readiness_never_claims_authorization_or_live_connection():
    release = load_dictionary()
    ready = release.readiness("file.read")
    assert ready["ready"] and ready["checks"]["binding"] == "not_checked"
    assert ready["checks"]["authorization"] == "evaluated_per_invocation_by_gateway"
    disabled = [r for r in release.tools.values() if r["runtime"].get("status") == "disabled"]
    assert len(disabled) == 2
    assert all(not release.readiness(r["id"])["ready"] for r in disabled)


@pytest.mark.parametrize("_cold_start", range(3))
def test_real_browser_javascript_click_and_render_are_observed(runtime, tmp_path, _cold_start):
    pytest.importorskip("playwright.sync_api")
    from PIL import Image
    page = tmp_path / "interactive.html"
    page.write_text('''<html><body style="margin:0;background:rgb(10,20,30);color:white">
        <button id="next" onclick="document.getElementById('value').textContent='second-page:73'">Next</button>
        <div id="value">first-page:41</div></body></html>''')
    result = runtime.run("browser.chrome.click", page.name, {"selector": "#next", "width": 320, "height": 200})
    if not result["success"] and "No working Chromium executable" in result.get("message", ""):
        pytest.skip("native Chromium runtime not available")
    assert result["success"], result.get("message") or json.dumps(result, ensure_ascii=False)
    actual = result["result"]
    assert "second-page:73" in actual["body_preview"] and "first-page:41" not in actual["body_preview"]
    assert actual["session_scope"] == "isolated_action"
    with Image.open(tmp_path / actual["screenshot"]["rel_path"]) as shot:
        assert shot.convert("RGB").getpixel((200, 150)) == (10, 20, 30)


def test_missing_browser_never_fabricates_a_screenshot(runtime, tmp_path, monkeypatch):
    from omni_body_skill.tools import pro_apps_v34
    monkeypatch.setattr(pro_apps_v34, "_module_available", lambda name: False)
    result = runtime.run("browser.chrome.screenshot", "out.png", {"source": "data:text/html,hello"})
    assert not result["success"] and result["execution_state"] == "not_executed"
    assert not (tmp_path / "out.png").exists()
