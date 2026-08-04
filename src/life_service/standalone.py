"""Optional standalone host for the same LifeKernel used by embedded 7184.

Normal desktop mode never starts this listener.  It exists for maintenance,
forensic inspection, and isolated Life development.  The writer lease in
``EmbeddedLifeRuntime`` prevents it from writing the same life root while the
embedded application is active.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .embedded_runtime import EmbeddedLifeRuntime

_MAX_BODY = 8 * 1024 * 1024


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], runtime: EmbeddedLifeRuntime, token: str) -> None:
        super().__init__(address, _Handler)
        self.runtime = runtime
        self.token = token


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _authorized(self) -> bool:
        provided = str(self.headers.get("X-Tiangong-Token") or "")
        return bool(provided) and hmac.compare_digest(
            provided.encode("utf-8"), self.server.token.encode("utf-8")
        )

    def _write(self, status: int, payload: dict[str, Any], content_type: str) -> None:
        raw = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method: str) -> None:
        if not self._authorized():
            self._write(401, {"ok": False, "reason_code": "life.auth.required"}, "application/problem+json")
            return
        payload = None
        if method == "POST":
            raw_length = str(self.headers.get("Content-Length") or "0")
            try:
                length = int(raw_length)
            except ValueError:
                self._write(400, {"ok": False, "reason_code": "life.body.length_invalid"}, "application/problem+json")
                return
            if length < 0 or length > _MAX_BODY:
                self._write(413, {"ok": False, "reason_code": "life.body.too_large"}, "application/problem+json")
                return
            try:
                decoded = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write(400, {"ok": False, "reason_code": "life.body.invalid_json"}, "application/problem+json")
                return
            if not isinstance(decoded, dict):
                self._write(400, {"ok": False, "reason_code": "life.body.object_required"}, "application/problem+json")
                return
            payload = decoded
        status, value, content_type = self.server.runtime.request(method, self.path, payload)
        self._write(status, value, content_type)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tiangong LifeKernel standalone host")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7175)
    parser.add_argument("--gateway-state-root", type=Path, default=None)
    parser.add_argument("--token", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host != "127.0.0.1" or not 1 <= args.port <= 65535:
        raise SystemExit("standalone LifeKernel must bind 127.0.0.1 on a valid port")
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / ".tiangong-v3"))
    gateway_state_root = (
        args.gateway_state_root.resolve(strict=False)
        if args.gateway_state_root is not None
        else (appdata / "tiangong-v3-qiyuan" / "runtime" / "gateway").resolve(strict=False)
    )
    token = str(
        args.token
        or os.environ.get("TIANGONG_LIFE_STANDALONE_TOKEN")
        or os.environ.get("TIANGONG_LIFE_INTERNAL_TOKEN")
        or ""
    ).strip()
    if len(token) < 32:
        token = secrets.token_urlsafe(48)
        print(f"TIANGONG_LIFE_STANDALONE_TOKEN={token}", flush=True)
    runtime = EmbeddedLifeRuntime.from_environment(
        gateway_state_root=gateway_state_root,
        mode="standalone",
    )
    server = _Server((args.host, args.port), runtime, token)
    stop = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        if not stop.is_set():
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _stop)
    try:
        print(f"Tiangong LifeKernel standalone listening on http://{args.host}:{args.port}", flush=True)
        server.serve_forever(poll_interval=0.25)
        return 0
    finally:
        server.server_close()
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
