"""OAuth inside the existing owner MCP configuration, with no token in results.

The owner supplies a registered public client and issuer. Discovery is pinned
to that issuer. The browser login and consent remain an account-owner action.
Credentials use the same private configuration as existing literal MCP headers;
this module does not claim an OS-encrypted vault or silently register a client.
"""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_LOCK = threading.RLock()
_FLOWS = {}
_MAX_RESPONSE = 262144


def _error(code):
    from .mcp_client import McpClientError
    return McpClientError("mcp.oauth." + code)


def _origin(url):
    p = urllib.parse.urlsplit(url)
    if (not p.hostname or p.username or p.password or p.fragment
            or p.scheme not in {"http", "https"}
            or (p.scheme == "http" and p.hostname not in {"127.0.0.1", "localhost", "::1"})):
        raise _error("url_invalid")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, "", "", ""))


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise _error("config_invalid")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise _error("config_permissions")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise _error("config_invalid") from None


def _configuration(path, server):
    data = _read(path)
    row = data.get("servers", {}).get(server)
    if not isinstance(row, dict) or row.get("enabled") is False or row.get("transport") != "streamable_http":
        raise _error("server_invalid")
    auth = row.get("oauth")
    if not isinstance(auth, dict) or not auth.get("client_id") or not auth.get("issuer"):
        raise _error("owner_client_and_issuer_required")
    _origin(row["url"]); _origin(auth["issuer"])
    if urllib.parse.urlsplit(auth["issuer"]).query or not isinstance(auth.get("scopes", []), list):
        raise _error("config_invalid")
    if any(not isinstance(s, str) or not s or any(c.isspace() for c in s) for s in auth.get("scopes", [])):
        raise _error("scope_invalid")
    public = {k: v for k, v in row.items() if k != "oauth_credentials"}
    fingerprint = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    return data, row, auth, fingerprint


def _json_request(url, *, form=None, allowed_origins):
    from .mcp_client import _NoRedirect
    if _origin(url) not in allowed_origins:
        raise _error("endpoint_not_authorized")
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data,
        headers={"Accept": "application/json", **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=15) as response:
            # The bounded reader also imposes an absolute deadline on drip feeds.
            from .mcp_client import _HttpServer
            reader = _HttpServer({"headers": {}}, 15000)
            body = b"".join(reader._response_chunks(response))
            if len(body) > _MAX_RESPONSE:
                raise _error("response_too_large")
            result = json.loads(body)
            if not isinstance(result, dict): raise _error("response_invalid")
            return result
    except urllib.error.HTTPError as exc:
        raise _error("http_" + str(exc.code)) from None
    except (ValueError, OSError):
        raise _error("transport_failed") from None


def _discover(row, auth):
    resource = row["url"]
    p = urllib.parse.urlsplit(resource)
    origin = _origin(resource)
    expected_resource = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    metadata_url = origin + "/.well-known/oauth-protected-resource" + p.path.rstrip("/")
    protected = _json_request(metadata_url, allowed_origins={origin})
    issuer = auth["issuer"].rstrip("/")
    if (protected.get("resource", "").rstrip("/") != expected_resource.rstrip("/")
            or issuer not in [str(v).rstrip("/") for v in protected.get("authorization_servers", [])]):
        raise _error("resource_issuer_mismatch")
    parsed = urllib.parse.urlsplit(issuer)
    auth_origin = _origin(issuer)
    candidates = [auth_origin + "/.well-known/oauth-authorization-server" + parsed.path,
                  auth_origin + "/.well-known/openid-configuration" + parsed.path,
                  issuer + "/.well-known/openid-configuration"]
    metadata = None
    for address in dict.fromkeys(candidates):
        try:
            metadata = _json_request(address, allowed_origins={auth_origin})
            break
        except Exception as exc:
            if getattr(exc, "code", "") not in {"mcp.oauth.http_404", "mcp.oauth.http_405"}:
                raise
    if not metadata or str(metadata.get("issuer", "")).rstrip("/") != issuer:
        raise _error("issuer_mismatch")
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise _error("pkce_required")
    allowed = {auth_origin, *[_origin(v) for v in auth.get("allowed_endpoint_origins", [])]}
    for key in ("authorization_endpoint", "token_endpoint"):
        if _origin(metadata.get(key, "")) not in allowed:
            raise _error("endpoint_not_authorized")
    return metadata, allowed, expected_resource


def _token(response, *, previous=None, auth_id=None):
    token = response.get("access_token")
    if (not isinstance(token, str) or not token or len(token) > 65536
            or any(c in token for c in "\r\n") or str(response.get("token_type", "")).lower() != "bearer"):
        raise _error("token_invalid")
    expires = response.get("expires_in", 3600)
    if type(expires) not in {int, float} or not 1 <= expires <= 31536000:
        raise _error("expiry_invalid")
    refresh = response.get("refresh_token", (previous or {}).get("refresh_token", ""))
    if not isinstance(refresh, str) or len(refresh) > 65536:
        raise _error("token_invalid")
    scope = response.get("scope", (previous or {}).get("scope"))
    if scope is not None and (not isinstance(scope, str) or len(scope) > 8192):
        raise _error("scope_invalid")
    return {"access_token": token, "refresh_token": refresh, "expires_at": time.time() + expires, "scope": scope,
            "authorization_id": auth_id or secrets.token_hex(24)}


def _save(path, server, fingerprint, credentials):
    # Compare the current owner configuration under the local lock. Never
    # overwrite a concurrently edited owner entry with the login's old copy.
    with _LOCK:
        original = path.read_bytes()
        data, row, _, current = _configuration(path, server)
        if fingerprint != current:
            raise _error("configuration_changed")
        row["oauth_credentials"] = {**credentials, "configuration_fingerprint": fingerprint}
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            with temp.open("xb") as f:
                os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
                f.write(raw); f.flush(); os.fsync(f.fileno())
            if path.is_symlink() or path.read_bytes() != original:
                raise _error("configuration_changed")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def authorization_headers(path: Path, server: str):
    """Called only by the existing connection resolver; return secrets privately."""
    with _LOCK:
        _, row, auth, fingerprint = _configuration(path, server)
        token = row.get("oauth_credentials")
        if not isinstance(token, dict) or not token.get("access_token"):
            raise _error("authorization_required")
        if token.get("configuration_fingerprint") != fingerprint:
            raise _error("configuration_changed")
        if token.get("expires_at", 0) <= time.time() + 30:
            if not token.get("refresh_token"):
                raise _error("authorization_required")
            metadata, allowed, resource = _discover(row, auth)
            response = _json_request(metadata["token_endpoint"], allowed_origins=allowed,
                form={"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                      "client_id": auth["client_id"], "resource": resource})
            token = _token(response, previous=token, auth_id=token["authorization_id"])
            _save(path, server, fingerprint, token)
        return {"Authorization": "Bearer " + token["access_token"]}, token["authorization_id"], token.get("refresh_token", "")


def begin(path: Path, server: str, *, scope: str):
    if not scope: raise _error("scope_required")
    _, row, auth, fingerprint = _configuration(path, server)
    metadata, allowed, resource = _discover(row, auth)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    key = (str(path.resolve()), server, scope)
    with _LOCK:
        for old_key, old in list(_FLOWS.items()):
            if old["expires_at"] < time.time(): _FLOWS.pop(old_key, None)
        if key in _FLOWS and _FLOWS[key]["status"] == "awaiting_user":
            if _FLOWS[key]["fingerprint"] == fingerprint:
                return dict(_FLOWS[key]["public"])
            _FLOWS[key]["status"] = "cancelled"
        if len(_FLOWS) >= 8: raise _error("flow_capacity")
    flow = {"status": "awaiting_user", "expires_at": time.time() + 600, "error": None, "fingerprint": fingerprint}
    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)
        def log_message(self, *args): pass
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            valid = (parsed.path == "/callback" and q.get("state") == [state]
                     and ("iss" not in q or q["iss"] == [auth["issuer"]]))
            if not valid:
                self.send_response(400); self.end_headers(); self.wfile.write(b"Invalid authorization response."); return
            if flow["status"] != "awaiting_user" or time.time() >= flow["expires_at"]:
                self.send_response(410); self.end_headers(); return
            flow["status"] = "exchanging"
            try:
                if "error" in q or len(q.get("code", [])) != 1: raise _error("authorization_denied")
                response = _json_request(metadata["token_endpoint"], allowed_origins=allowed,
                    form={"grant_type": "authorization_code", "code": q["code"][0],
                          "client_id": auth["client_id"], "redirect_uri": redirect,
                          "code_verifier": verifier, "resource": resource})
                _save(path, server, fingerprint, _token(response))
                flow["status"] = "authorized"
            except Exception as exc:
                flow["status"] = "failed"
                flow["error"] = getattr(exc, "code", "mcp.oauth.authorization_failed")
            self.send_response(200 if flow["status"] == "authorized" else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers(); self.wfile.write(b"Authorization processed. Return to Tiangong.")
    listener = HTTPServer(("127.0.0.1", 0), Callback)
    listener.timeout = 1
    redirect = f"http://127.0.0.1:{listener.server_port}/callback"
    params = {"response_type": "code", "client_id": auth["client_id"], "redirect_uri": redirect,
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
              "resource": resource, "scope": " ".join(auth.get("scopes", []))}
    separator = "&" if urllib.parse.urlsplit(metadata["authorization_endpoint"]).query else "?"
    public = {"server": server, "status": "awaiting_user", "expires_at": flow["expires_at"],
              "authorization_url": metadata["authorization_endpoint"] + separator + urllib.parse.urlencode(params),
              "next_action": "mcp.auth.status"}
    flow["public"] = public
    with _LOCK: _FLOWS[key] = flow
    def run():
        try:
            while flow["status"] == "awaiting_user" and time.time() < flow["expires_at"]:
                listener.handle_request()
            if flow["status"] == "awaiting_user": flow["status"] = "expired"
        finally:
            listener.server_close()
    threading.Thread(target=run, name="tiangong-mcp-oauth-callback", daemon=True).start()
    return dict(public)


def status(path: Path, server: str, *, scope: str):
    _, row, _, fingerprint = _configuration(path, server)
    with _LOCK:
        flow = _FLOWS.get((str(path.resolve()), server, scope))
        if flow:
            if flow["fingerprint"] != fingerprint:
                flow.update(status="failed", error="mcp.oauth.configuration_changed")
            return {"server": server, "status": flow["status"], "error": flow["error"], "expires_at": flow["expires_at"]}
    token = row.get("oauth_credentials") or {}
    current = token.get("configuration_fingerprint") == fingerprint
    return {"server": server, "status": "authorized" if current and token.get("expires_at", 0) > time.time() else "authorization_required",
            "granted_scope": token.get("scope") if current else None}
