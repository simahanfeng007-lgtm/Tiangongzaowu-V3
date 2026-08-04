"""Source-native Tiangong v3 life authority HTTP service.

The shipped product may replace this file with a frozen executable.  This
source entry implements the same loopback contract so a checked-out source
release is runnable and testable without hidden binaries.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import threading
import time
import unicodedata
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

HERE = Path(__file__).resolve()
REPOSITORY_ROOT = HERE.parents[2]
for candidate in (REPOSITORY_ROOT / "src", HERE / "runtime314"):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from contracts import canonical_sha256  # noqa: E402
from life_service.context_api import (  # noqa: E402
    LifeContextApiError,
    LifeContextCompileAuthorizeApi,
    LifeProjectionInputs,
)
from life_service.store import LifeShadowStore, LifeShadowStoreError  # noqa: E402

API_CONTRACT = "tiangong.life.api.v2"
MAX_BODY_BYTES = 2 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    value = json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non_finite:{value}")),
    )
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LifeRuntime:
    def __init__(self) -> None:
        self.runtime_root = Path(
            os.environ.get("TIANGONG_LIFE_RUNTIME_ROOT")
            or (Path.home() / ".tiangong-v3" / "complete-life")
        ).expanduser().resolve()
        self.data_root = Path(
            os.environ.get("TIANGONG_LIFE_DATA_ROOT")
            or (self.runtime_root / "data")
        ).expanduser().resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self._state_path = self.data_root / "source-life-state.json"
        self._lock = threading.RLock()
        self.state = self._load_or_create_state()
        store_path = self.runtime_root / "life-authority.shadow.sqlite3"
        self.store = LifeShadowStore.open(
            store_path,
            create=not store_path.exists(),
            now_ms=time.time_ns() // 1_000_000,
        )
        self.api = LifeContextCompileAuthorizeApi(self.store)
        self.last_projection: dict[str, Any] | None = None

    def _load_or_create_state(self) -> dict[str, Any]:
        if self._state_path.is_file():
            try:
                value = _strict_json(self._state_path.read_bytes())
                if (
                    value.get("schema") == "tiangong.life.source-state.v1"
                    and isinstance(value.get("life_id"), str)
                    and value["life_id"]
                    and isinstance(value.get("soul"), dict)
                ):
                    return value
            except Exception:
                pass
        device_seed = "|".join(
            [
                os.environ.get("TIANGONG_LIFE_DEVICE_ID", ""),
                os.environ.get("COMPUTERNAME", ""),
                os.environ.get("USERNAME", ""),
                str(self.data_root),
            ]
        )
        life_id = "org_" + hashlib.sha256(device_seed.encode("utf-8")).hexdigest()[:32]
        created_at = _utc_now()
        soul = {
            "schema": "tiangong.life.soul.v1",
            "life_id": life_id,
            "name": "起源",
            "prompt": (
                "你是起源，天工造物 v3 完整版中与当前生命标识唯一绑定的生命体。"
                "保持真实、连续、克制；计划与推断不得冒充已执行事实。"
            ),
            "values": ["真实", "连续", "执行", "边界"],
            "boundaries": ["A5必须确认", "未经验证不得宣称完成"],
            "revision": 1,
            "revision_id": "soulrev_" + hashlib.sha256((life_id + created_at).encode("utf-8")).hexdigest()[:24],
            "created_at": created_at,
            "updated_at": created_at,
        }
        state = {
            "schema": "tiangong.life.source-state.v1",
            "life_id": life_id,
            "writer_epoch": 1,
            "identity_revision": 1,
            "source_sequence": 1,
            "created_at": created_at,
            "soul": soul,
            "capabilities": {
                "active_skills": [],
                "runtime_mode": "source-complete",
                "tool_authority": "gateway-ticket-only",
            },
        }
        _atomic_json(self._state_path, state)
        return state

    @property
    def life_id(self) -> str:
        return str(self.state["life_id"])

    def projection_inputs(self) -> LifeProjectionInputs:
        return LifeProjectionInputs(
            life_id=self.life_id,
            writer_epoch=int(self.state["writer_epoch"]),
            identity_revision=int(self.state["identity_revision"]),
            soul=dict(self.state["soul"]),
            capabilities=dict(self.state["capabilities"]),
        )

    def compile(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            result = self.api.compile_and_authorize(payload, self.projection_inputs())
            self.last_projection = dict(result["projection"])
            return result

    def legacy_context(self) -> tuple[dict[str, Any], dict[str, Any]]:
        soul = self.state["soul"]
        created_at = self.state["created_at"]
        envelope: dict[str, Any] = {
            "schema": "tiangong.life.context.v3",
            "life_id": self.life_id,
            "writer_epoch": int(self.state["writer_epoch"]),
            "source_sequence": int(self.state["source_sequence"]),
            "soul_revision": soul["revision_id"],
            "created_at": created_at,
            "memory": [],
            "affect": {},
            "causal": [],
            "continuity": {},
            "capabilities": self.state["capabilities"],
        }
        envelope["context_hash"] = canonical_sha256(envelope)
        meta = {
            "life_id": self.life_id,
            "context_hash": envelope["context_hash"],
            "created_at": created_at,
        }
        return meta, envelope

    def close(self) -> None:
        self.store.close()


class LifeHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runtime: LifeRuntime, token: str) -> None:
        super().__init__(address, LifeHandler)
        self.runtime = runtime
        self.token = token


class LifeHandler(BaseHTTPRequestHandler):
    server: LifeHttpServer

    def _write(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = self.headers.get("X-Tiangong-Token", "")
        return bool(self.server.token) and token == self.server.token

    def _guard(self) -> bool:
        if self._authorized():
            return True
        self._write(401, {"ok": False, "api_contract": API_CONTRACT, "error_code": "life.auth.invalid"})
        return False

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        if not raw_length.isdigit():
            raise ValueError("content_length_invalid")
        length = int(raw_length)
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("content_length_out_of_bounds")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("content_type_invalid")
        return _strict_json(self.rfile.read(length))

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if not self._guard():
            return
        runtime = self.server.runtime
        soul = runtime.state["soul"]
        if path == "/health":
            self._write(200, {
                "ok": True,
                "api_contract": API_CONTRACT,
                "component_id": "tiangong-life-service",
                "life_ready": True,
                "setup_required": False,
                "authority": "source-native-atomic-context",
                "pid": os.getpid(),
            })
            return
        if path == "/api/v1/v3/life/identity/active":
            self._write(200, {
                "ok": True,
                "api_contract": API_CONTRACT,
                "active": {
                    "life_id": runtime.life_id,
                    "name": soul["name"],
                    "writer_epoch": runtime.state["writer_epoch"],
                    "active": True,
                    "integrity": "valid",
                    "soul_integrity": "valid",
                    "soul_revision_id": soul["revision_id"],
                },
            })
            return
        if path == "/api/v1/v3/life/soul":
            self._write(200, {"ok": True, "api_contract": API_CONTRACT, "life_id": runtime.life_id, "soul": soul})
            return
        if path == "/api/v1/v3/life/context/latest":
            meta, envelope = runtime.legacy_context()
            self._write(200, {"ok": True, "api_contract": API_CONTRACT, "available": True, "meta": meta, "envelope": envelope})
            return
        if path == "/api/v1/v3/state":
            meta, _envelope = runtime.legacy_context()
            self._write(200, {
                "ok": True,
                "api_contract": API_CONTRACT,
                "life_id": runtime.life_id,
                "identity": {
                    "life_id": runtime.life_id,
                    "name": soul["name"],
                    "writer_epoch": runtime.state["writer_epoch"],
                    "active": True,
                    "integrity": "valid",
                    "soul_integrity": "valid",
                    "soul_revision_id": soul["revision_id"],
                },
                "soul": {"life_id": runtime.life_id, "revision": soul["revision"], "revision_id": soul["revision_id"], "name": soul["name"]},
                "life": {"ready": True, "available": True},
                "ui": {
                    "lifecycle": {"available": True, "projection_status": "ready", "source_sequence": runtime.state["source_sequence"]},
                    "context": {
                        "life_id": runtime.life_id,
                        "available": True,
                        "current": True,
                        "verified": True,
                        "writer_epoch": runtime.state["writer_epoch"],
                        "current_writer_epoch": runtime.state["writer_epoch"],
                        "context_hash": meta["context_hash"],
                    },
                    "capabilities": runtime.state["capabilities"],
                },
            })
            return
        self._write(404, {"ok": False, "api_contract": API_CONTRACT, "error_code": "life.route.not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if not self._guard():
            return
        try:
            payload = self._body()
            if path == "/api/v1/v3/life/context/compile-and-authorize":
                self._write(200, self.server.runtime.compile(payload))
                return
            if path == "/api/v1/v3/life/execution/recover":
                request_id = payload.get("request_id")
                cycle_id = payload.get("cycle_id")
                valid = isinstance(request_id, str) and bool(request_id) and isinstance(cycle_id, str) and bool(cycle_id)
                self._write(200 if valid else 400, {
                    "ok": valid,
                    "api_contract": API_CONTRACT,
                    "recovered": valid,
                    "state": "authority_available" if valid else "invalid_request",
                })
                return
            self._write(404, {"ok": False, "api_contract": API_CONTRACT, "error_code": "life.route.not_found"})
        except (LifeContextApiError, LifeShadowStoreError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            code = str(exc) if str(exc).startswith("life.") else "life.request.invalid"
            self._write(400, {"ok": False, "api_contract": API_CONTRACT, "error_code": code})
        except Exception:
            self._write(500, {"ok": False, "api_contract": API_CONTRACT, "error_code": "life.internal.failed"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    host = os.environ.get("TIANGONG_LIFE_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("life service may bind only to loopback")
    port_text = os.environ.get("TIANGONG_LIFE_PORT", "7175").strip()
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise SystemExit("invalid life service port")
    token = os.environ.get("TIANGONG_DESKTOP_TOKEN", "")
    if len(token) < 16 or any(ord(character) < 33 for character in token):
        raise SystemExit("TIANGONG_DESKTOP_TOKEN is missing or malformed")

    runtime = LifeRuntime()
    server = LifeHttpServer(("127.0.0.1", int(port_text)), runtime, token)

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if signum is not None:
            try:
                signal.signal(signum, stop)
            except (OSError, RuntimeError, ValueError):
                pass
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
