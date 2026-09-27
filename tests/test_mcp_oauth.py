from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import pytest

from omni_body_skill.tools import mcp_client as mcp, mcp_oauth as oauth


@pytest.fixture
def auth_server(tmp_path, monkeypatch):
    state = {"exchanges": 0, "refreshes": 0, "pkce": None, "resource": None, "scope": None,
             "bad_issuer": False, "bad_endpoint": False, "deny_refresh": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def answer(self, value, status=200):
            raw = json.dumps(value).encode(); self.send_response(status)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            p = urllib.parse.urlsplit(self.path)
            if p.path == "/.well-known/oauth-protected-resource/mcp":
                self.answer({"resource": origin + "/mcp", "authorization_servers": [origin]}); return
            if p.path == "/.well-known/oauth-authorization-server":
                self.answer({"issuer": origin + "/evil" if state["bad_issuer"] else origin,
                    "authorization_endpoint": origin + "/authorize", "token_endpoint": "https://untrusted.invalid/token" if state["bad_endpoint"] else origin + "/token",
                    "code_challenge_methods_supported": ["S256"]}); return
            if p.path == "/authorize":
                q = urllib.parse.parse_qs(p.query)
                state["pkce"] = q["code_challenge"][0]
                state["resource"] = q["resource"][0]
                state["scope"] = q["scope"][0]
                self.send_response(302)
                self.send_header("Location", q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"code": "actual-code", "state": q["state"][0], "iss": origin}))
                self.end_headers(); return
            self.answer({},404)
        def do_POST(self):
            q = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            if q["grant_type"] == ["authorization_code"]:
                state["exchanges"] += 1
                challenge = base64.urlsafe_b64encode(hashlib.sha256(q["code_verifier"][0].encode()).digest()).decode().rstrip("=")
                if challenge != state["pkce"]: self.answer({},400); return
            else:
                state["refreshes"] += 1
                if state["deny_refresh"]: self.answer({},400); return
            self.answer({"access_token": "secret-access-" + str(state["refreshes"]), "refresh_token": "secret-refresh",
                         "token_type": "Bearer", "expires_in": 3600})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    config = tmp_path / "owner.json"
    config.write_text(json.dumps({"servers": {"office": {"transport": "streamable_http", "url": origin + "/mcp",
        "oauth": {"issuer": origin, "client_id": "owner-client", "scopes": ["files.read"]},
        "applications": ["google.docs"], "environment": {"account_label": "owner-a"}}}}))
    config.chmod(0o600)
    monkeypatch.setattr(mcp, "CONFIG_PATH", config)
    yield state, config, origin
    with oauth._LOCK:
        for flow in oauth._FLOWS.values():
            if flow["status"] == "awaiting_user": flow["status"] = "cancelled"
        oauth._FLOWS.clear()
    server.shutdown(); server.server_close(); thread.join(2)


def login(config):
    begun = oauth.begin(config,"office",scope="host")
    with urllib.request.urlopen(begun["authorization_url"], timeout=5) as response:
        assert response.status == 200
    return begun


def test_real_pkce_flow_stores_in_original_config_and_never_exposes_tokens(auth_server):
    state, config, origin = auth_server
    assert mcp.list_servers()[0]["connection_state"] == "authorization_required"
    with pytest.raises(mcp.McpClientError, match="authorization_required"): mcp._resolve_server("office")
    begun = login(config)
    assert state["exchanges"] == 1 and state["scope"] == "files.read" and state["resource"] == origin + "/mcp"
    public = oauth.status(config,"office",scope="host")
    assert public["status"] == "authorized"
    assert "secret-access" not in json.dumps([begun,public,mcp.list_servers()])
    cfg = mcp._resolve_server("office")
    assert cfg["headers"]["Authorization"] == "Bearer secret-access-0"
    assert "secret-" not in mcp._redact("secret-access-0 secret-refresh",cfg)
    if os.name != "nt": assert config.stat().st_mode & 0o077 == 0


def test_refresh_keeps_authorization_identity_but_new_login_changes_it(auth_server):
    state, config, _ = auth_server; login(config)
    first = mcp._resolve_server("office")
    data = json.loads(config.read_text()); data["servers"]["office"]["oauth_credentials"]["expires_at"] = 1
    config.write_text(json.dumps(data))
    second = mcp._resolve_server("office")
    assert state["refreshes"] == 1 and first["headers"] != second["headers"]
    assert mcp._configuration_fingerprint(first) == mcp._configuration_fingerprint(second)
    login(config)
    third = mcp._resolve_server("office")
    assert mcp._configuration_fingerprint(third) != mcp._configuration_fingerprint(first)


@pytest.mark.parametrize("parameter",["state","iss"])
def test_wrong_callback_identity_never_exchanges_code(auth_server,parameter):
    state, config, origin = auth_server
    begun = oauth.begin(config,"office",scope="host")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(begun["authorization_url"]).query)
    values = {"code":"actual-code","state":q["state"][0],"iss":origin}; values[parameter]="wrong"
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(q["redirect_uri"][0]+"?"+urllib.parse.urlencode(values),timeout=5)
    assert state["exchanges"] == 0
    assert "oauth_credentials" not in json.loads(config.read_text())["servers"]["office"]


@pytest.mark.parametrize("flag",["bad_issuer","bad_endpoint"])
def test_discovery_cannot_redirect_credentials_to_unapproved_issuer(auth_server,flag):
    state, config, _ = auth_server; state[flag]=True
    with pytest.raises(mcp.McpClientError): oauth.begin(config,"office",scope="host")
    assert state["exchanges"] == 0


def test_configuration_change_during_login_cannot_overwrite_owner(auth_server):
    state, config, _ = auth_server
    begun = oauth.begin(config,"office",scope="host")
    data=json.loads(config.read_text());data["servers"]["office"]["environment"]["account_label"]="owner-b"
    config.write_text(json.dumps(data))
    with pytest.raises(urllib.error.HTTPError): urllib.request.urlopen(begun["authorization_url"],timeout=5)
    row=json.loads(config.read_text())["servers"]["office"]
    assert row["environment"]["account_label"]=="owner-b" and "oauth_credentials" not in row
    assert oauth.status(config,"office",scope="host")["status"]=="failed"


def test_revoked_refresh_fails_before_mcp_dispatch(auth_server):
    state,config,_=auth_server;login(config)
    data=json.loads(config.read_text());data["servers"]["office"]["oauth_credentials"]["expires_at"]=1
    config.write_text(json.dumps(data));state["deny_refresh"]=True
    with pytest.raises(mcp.McpClientError,match="mcp.oauth.http_400"):mcp._resolve_server("office")
    assert state["refreshes"]==1


def test_completed_login_does_not_survive_changed_owner_configuration(auth_server):
    _,config,_=auth_server;login(config)
    data=json.loads(config.read_text());data["servers"]["office"]["oauth"]["client_id"]="different-owner-client"
    config.write_text(json.dumps(data))
    status=oauth.status(config,"office",scope="host")
    assert status["status"]=="failed" and status["error"]=="mcp.oauth.configuration_changed"
    with pytest.raises(mcp.McpClientError,match="mcp.oauth.configuration_changed"):
        mcp._resolve_server("office")
