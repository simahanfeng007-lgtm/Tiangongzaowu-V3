"""Authenticated Android mobile-body link for Tiangong V3.

This module is deliberately a transport/body adapter, not a second Runtime.
It owns device pairing, task delivery and result return.  Callers still decide
whether an action is authorized before enqueueing it.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

MOBILE_ACTIONS = frozenset({
    "mobile.observe_ui",
    "mobile.tap",
    "mobile.tap_node",
    "mobile.swipe",
    "mobile.input_text",
    "mobile.back",
    "mobile.home",
    "mobile.open_app",
    "mobile.notification_list",
    "mobile.screenshot",
})
MAX_JSON_BYTES = 2 * 1024 * 1024


class MobileLinkError(RuntimeError):
    pass


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _safe_mobile_host(raw: str) -> str:
    host = (raw or "127.0.0.1").strip()
    if host == "0.0.0.0":
        return host
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise MobileLinkError("mobile_link.bind_host_must_be_ip") from exc
    shared = ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")
    if not (ip.is_loopback or ip.is_private or shared):
        raise MobileLinkError("mobile_link.public_bind_forbidden")
    return host


@dataclass
class _Pending:
    event: threading.Event
    result: dict[str, Any] | None = None


class MobileBodyBroker:
    """Thread-safe pairing + one-device action queue.

    Device tokens are never persisted in plaintext; only SHA-256 digests are
    stored.  Pairing codes are one-use, memory-only, and expire after 5 min.
    """

    def __init__(self, state_root: Path) -> None:
        self.root = state_root / "mobile-link"
        self.root.mkdir(parents=True, exist_ok=True)
        self.devices_path = self.root / "devices.json"
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._pair_codes: dict[str, int] = {}
        self._devices: dict[str, dict[str, Any]] = self._load_devices()
        self._tasks: list[dict[str, Any]] = []
        self._pending: dict[str, _Pending] = {}

    def _load_devices(self) -> dict[str, dict[str, Any]]:
        if not self.devices_path.is_file():
            return {}
        try:
            raw = json.loads(self.devices_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): dict(v) for k, v in raw.items() if isinstance(v, Mapping)}

    def _persist(self) -> None:
        tmp = self.devices_path.with_suffix(".tmp")
        body = json.dumps(self._devices, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        tmp.write_text(body, encoding="utf-8", newline="\n")
        os.replace(tmp, self.devices_path)

    def create_pairing_code(self, ttl_ms: int = 300_000) -> dict[str, Any]:
        now = _now_ms()
        with self._lock:
            self._pair_codes = {c: exp for c, exp in self._pair_codes.items() if exp > now}
            for _ in range(32):
                code = f"{secrets.randbelow(1_000_000):06d}"
                if code not in self._pair_codes:
                    self._pair_codes[code] = now + ttl_ms
                    return {"code": code, "expires_at_ms": now + ttl_ms}
        raise MobileLinkError("mobile_link.pairing_code_exhausted")

    def pair(self, code: str, device_name: str, capabilities: list[str]) -> dict[str, Any]:
        now = _now_ms()
        with self._lock:
            expiry = self._pair_codes.pop(code, None)
            if expiry is None or expiry <= now:
                raise MobileLinkError("mobile_link.pairing_code_invalid")
            token = secrets.token_urlsafe(48)
            device_id = "mob_" + uuid.uuid4().hex
            allowed = sorted(set(str(x) for x in capabilities) & MOBILE_ACTIONS)
            self._devices[device_id] = {
                "device_id": device_id,
                "name": (device_name or "Android").strip()[:80],
                "token_sha256": _token_hash(token),
                "capabilities": allowed,
                "paired_at_ms": now,
                "last_seen_ms": now,
                "revoked": False,
            }
            self._persist()
            return {"device_id": device_id, "device_token": token, "capabilities": allowed}

    def authenticate(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        digest = _token_hash(token)
        with self._lock:
            for device in self._devices.values():
                if not device.get("revoked") and hmac.compare_digest(str(device.get("token_sha256") or ""), digest):
                    return device
        return None

    def heartbeat(self, device_id: str, capabilities: list[str] | None = None) -> dict[str, Any]:
        now = _now_ms()
        with self._lock:
            device = self._devices.get(device_id)
            if not device or device.get("revoked"):
                raise MobileLinkError("mobile_link.device_unavailable")
            device["last_seen_ms"] = now
            if capabilities is not None:
                device["capabilities"] = sorted(set(str(x) for x in capabilities) & MOBILE_ACTIONS)
            self._persist()
            return self.public_device(device)

    @staticmethod
    def public_device(device: Mapping[str, Any]) -> dict[str, Any]:
        return {k: device.get(k) for k in ("device_id", "name", "capabilities", "paired_at_ms", "last_seen_ms", "revoked")}

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.public_device(v) for v in self._devices.values()]

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            device = self._devices.get(device_id)
            if not device:
                return False
            device["revoked"] = True
            self._persist()
            self._condition.notify_all()
            return True

    def enqueue(self, action: str, arguments: Mapping[str, Any], *, timeout_ms: int = 30_000) -> dict[str, Any]:
        if action not in MOBILE_ACTIONS:
            raise MobileLinkError("mobile_link.action_not_allowed")
        if not 1_000 <= timeout_ms <= 120_000:
            raise MobileLinkError("mobile_link.timeout_invalid")
        now = _now_ms()
        with self._condition:
            candidates = [d for d in self._devices.values() if not d.get("revoked") and action in set(d.get("capabilities") or []) and now - int(d.get("last_seen_ms") or 0) < 60_000]
            if not candidates:
                raise MobileLinkError("mobile_link.no_online_capable_device")
            target = max(candidates, key=lambda d: int(d.get("last_seen_ms") or 0))
            task_id = "mt_" + uuid.uuid4().hex
            task = {
                "schema": "tiangong.mobile.task.v1",
                "task_id": task_id,
                "device_id": target["device_id"],
                "action": action,
                "arguments": dict(arguments),
                "created_at_ms": now,
                "deadline_ms": now + timeout_ms,
            }
            pending = _Pending(threading.Event())
            self._pending[task_id] = pending
            self._tasks.append(task)
            self._condition.notify_all()
        if not pending.event.wait(timeout_ms / 1000.0):
            with self._lock:
                self._pending.pop(task_id, None)
            raise MobileLinkError("mobile_link.device_result_timeout")
        result = pending.result or {"ok": False, "error": "mobile_link.empty_result"}
        return dict(result)

    def next_task(self, device_id: str, wait_ms: int) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(0, min(wait_ms, 25_000)) / 1000.0
        with self._condition:
            while True:
                now = _now_ms()
                self._tasks[:] = [t for t in self._tasks if int(t["deadline_ms"]) > now]
                for i, task in enumerate(self._tasks):
                    if task["device_id"] == device_id:
                        return self._tasks.pop(i)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def submit_result(self, device_id: str, task_id: str, result: Mapping[str, Any]) -> bool:
        with self._lock:
            pending = self._pending.pop(task_id, None)
            if pending is None:
                return False
            pending.result = {
                "ok": bool(result.get("ok")),
                "task_id": task_id,
                "device_id": device_id,
                "data": result.get("data"),
                "error": result.get("error"),
                "completed_at_ms": _now_ms(),
            }
            pending.event.set()
            return True


class MobileLinkHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple[str, int], broker: MobileBodyBroker, desktop_token: str) -> None:
        self.broker = broker
        self.desktop_token = desktop_token
        super().__init__(address, MobileLinkHandler)


class MobileLinkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TiangongMobileLink/1"
    sys_version = ""

    @property
    def mobile(self) -> MobileLinkHttpServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(dict(payload), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise MobileLinkError("mobile_link.transfer_encoding_forbidden")
        raw = self.headers.get("Content-Length") or "0"
        if not raw.isascii() or not raw.isdecimal() or int(raw) > MAX_JSON_BYTES:
            raise MobileLinkError("mobile_link.content_length_invalid")
        data = self.rfile.read(int(raw))
        value = json.loads(data.decode("utf-8")) if data else {}
        if not isinstance(value, dict):
            raise MobileLinkError("mobile_link.json_object_required")
        return value

    def _bearer_device(self) -> dict[str, Any] | None:
        auth = str(self.headers.get("Authorization") or "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        return self.mobile.broker.authenticate(token)

    def _admin_ok(self) -> bool:
        client_ip = ipaddress.ip_address(self.client_address[0])
        if not client_ip.is_loopback:
            return False
        supplied = str(self.headers.get("X-Tiangong-Token") or "")
        expected = self.mobile.desktop_token
        return bool(expected and supplied and hmac.compare_digest(expected, supplied))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/mobile/v1/status":
            self._json(200, {"ok": True, "schema": "tiangong.mobile.link.v1"})
            return
        if parsed.path == "/admin/v1/devices":
            if not self._admin_ok():
                self._json(401, {"ok": False, "error": "unauthorized"}); return
            self._json(200, {"ok": True, "devices": self.mobile.broker.list_devices()}); return
        if parsed.path == "/mobile/v1/tasks/next":
            device = self._bearer_device()
            if not device:
                self._json(401, {"ok": False, "error": "unauthorized"}); return
            wait_ms = int((parse_qs(parsed.query).get("wait_ms") or ["25000"])[0])
            task = self.mobile.broker.next_task(str(device["device_id"]), wait_ms)
            self._json(200, {"ok": True, "task": task}); return
        self._json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self._body()
            if parsed.path == "/admin/v1/pairing-code":
                if not self._admin_ok():
                    self._json(401, {"ok": False, "error": "unauthorized"}); return
                self._json(200, {"ok": True, **self.mobile.broker.create_pairing_code()}); return
            if parsed.path == "/admin/v1/dispatch":
                if not self._admin_ok():
                    self._json(401, {"ok": False, "error": "unauthorized"}); return
                result = self.mobile.broker.enqueue(str(body.get("action") or ""), body.get("arguments") if isinstance(body.get("arguments"), Mapping) else {}, timeout_ms=int(body.get("timeout_ms") or 30000))
                self._json(200, result); return
            if parsed.path == "/admin/v1/revoke":
                if not self._admin_ok():
                    self._json(401, {"ok": False, "error": "unauthorized"}); return
                self._json(200, {"ok": self.mobile.broker.revoke(str(body.get("device_id") or ""))}); return
            if parsed.path == "/mobile/v1/pair":
                result = self.mobile.broker.pair(str(body.get("code") or ""), str(body.get("device_name") or "Android"), list(body.get("capabilities") or []))
                self._json(200, {"ok": True, **result}); return
            device = self._bearer_device()
            if not device:
                self._json(401, {"ok": False, "error": "unauthorized"}); return
            device_id = str(device["device_id"])
            if parsed.path == "/mobile/v1/heartbeat":
                data = self.mobile.broker.heartbeat(device_id, list(body.get("capabilities") or []))
                self._json(200, {"ok": True, "device": data}); return
            prefix, suffix = "/mobile/v1/tasks/", "/result"
            if parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
                task_id = parsed.path[len(prefix):-len(suffix)]
                accepted = self.mobile.broker.submit_result(device_id, task_id, body)
                self._json(200 if accepted else 409, {"ok": accepted}); return
            self._json(404, {"ok": False, "error": "not_found"})
        except (MobileLinkError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)[:160]})


def create_mobile_link_server(runtime: object, environ: Mapping[str, str] | None = None) -> MobileLinkHttpServer | None:
    env = dict(os.environ if environ is None else environ)
    if env.get("TIANGONG_MOBILE_LINK", "0") != "1":
        return None
    host = _safe_mobile_host(env.get("TIANGONG_MOBILE_BIND_HOST", "127.0.0.1"))
    try:
        port = int(env.get("TIANGONG_MOBILE_PORT", "7186"))
    except ValueError as exc:
        raise MobileLinkError("mobile_link.port_invalid") from exc
    if not 1024 <= port <= 65535 or port == 7184:
        raise MobileLinkError("mobile_link.port_invalid")
    token = env.get("TIANGONG_DESKTOP_TOKEN", "")
    if len(token) < 32:
        raise MobileLinkError("mobile_link.desktop_token_missing")
    state_root = Path(getattr(getattr(runtime, "config", None), "state_root"))
    broker = MobileBodyBroker(state_root)
    setattr(runtime, "mobile_body_broker", broker)
    return MobileLinkHttpServer((host, port), broker, token)


__all__ = ["MOBILE_ACTIONS", "MobileBodyBroker", "MobileLinkError", "MobileLinkHttpServer", "create_mobile_link_server"]
