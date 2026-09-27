"""MCP (Model Context Protocol) client for omni_body actions.

v1 设计（2026-08-22，"作 omni_body action 接入"方案）：

- **安全边界**：服务器进程只能来自用户管理的
  ``~/.tiangong/v3/mcp_servers.json``——模型只能引用已配置的服务器名，
  绝不能自造命令行。配置文件是唯一的 spawn 授权面。
- **权限链全复用**：mcp.tool.call 注册为 A3，走网关既有确认链；
  mcp.servers.list / mcp.tools.list 为 A0 只读。
- **进程生命周期**：每次调用独立 spawn → initialize → 请求 → close。
  无僵尸进程、无陈旧会话、无并发争用；代价是每次约百毫秒启动，
  桌面场景可接受。持久会话留作后续。stdout/stderr 各有独立读线程
  （stderr 持续消费防止服务器日志撑爆管道缓冲导致死锁，只保留尾部
  用于诊断）；超时/失败清理时按进程树收割（Windows npx/uvx 的 .cmd
  shim 之下还有真实孙进程）。
- **传输**：JSON-RPC 2.0 over stdio，按行分隔（MCP stdio 传输）。
- **资源上限**：默认 30s 超时 / 256KB 输出上限 / 512 行读取上限，
  均可被 args 有限度覆盖；子进程环境做白名单最小化继承。

配置格式（用户手写，带 ``mcp.servers.list`` 可视化校验）::

    {
      "servers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/some/root"],
          "env": {"NODE_OPTIONS": "--enable-source-maps"},
          "enabled": true
        }
      }
    }
"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOLS = {PROTOCOL_VERSION, "2025-06-18", "2025-03-26", "2024-11-05"}
CLIENT_INFO = {"name": "tiangong-omni-body", "version": "3.5"}

CONFIG_PATH = Path.home() / ".tiangong" / "v3" / "mcp_servers.json"

DEFAULT_TIMEOUT_MS = 30_000
MAX_TIMEOUT_MS = 120_000
MAX_OUTPUT_BYTES = 256 * 1024
MAX_LINE_BYTES = 4 * 1024 * 1024
# stderr 尾部诊断缓冲与 stdout 读线程的生产侧上限（防失控服务器把
# _lines 队列灌成无界内存）。
_STDERR_TAIL_BYTES = 4096
_MAX_QUEUED_LINES = 512

# 子进程环境白名单：Windows 进程启动所需的最小集合 + PATH（npx/node/
# uvx 常见发行方式需要）。绝不整份继承宿主环境（防泄漏宿主凭据变量）。
_INHERIT_ENV_KEYS = (
    "SystemRoot", "SystemDrive", "ComSpec", "PATHEXT", "windir", "OS",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PATH", "TEMP", "TMP", "USERPROFILE", "HOME",
    "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
)


class McpClientError(RuntimeError):
    """MCP 调用失败（配置缺失/进程失败/协议错误/超时）。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def load_server_config(config_path: Path | None = None) -> Dict[str, Dict[str, Any]]:
    """读取并校验服务器配置；文件缺失返回空表（未配置≠错误）。"""
    path = config_path if config_path is not None else CONFIG_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise McpClientError("mcp.config.invalid", str(exc)) from exc
    servers = data.get("servers") if isinstance(data, dict) else None
    if servers is None:
        return {}
    if not isinstance(servers, dict):
        raise McpClientError("mcp.config.invalid", "servers must be an object")
    clean: Dict[str, Dict[str, Any]] = {}
    for name, raw in servers.items():
        try:
            if not isinstance(raw, dict):
                continue
            if (any(not isinstance(raw.get(k, {}), dict) for k in ("env", "headers", "env_refs", "header_env", "environment"))
                    or any(not isinstance(raw.get(k, []), list) for k in ("args", "applications"))):
                raise McpClientError("mcp.config.invalid", "connection fields have invalid types")
            transport = str(raw.get("transport") or "stdio")
            if transport not in {"stdio", "streamable_http"}:
                raise McpClientError("mcp.config.transport_invalid", str(name))
            command = str(raw.get("command") or "").strip()
            url = str(raw.get("url") or "").strip()
            if transport == "stdio" and not command:
                continue
            if transport == "streamable_http":
                parsed = urllib.parse.urlsplit(url)
                if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                        or parsed.username or parsed.password or parsed.fragment):
                    raise McpClientError("mcp.config.url_invalid", str(name))
                if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                    raise McpClientError("mcp.config.https_required", str(name))
            args = [str(item) for item in raw.get("args") or [] if str(item).strip()]
            env = {
                str(key): str(value)
                for key, value in (raw.get("env") or {}).items()
                if str(key).strip()
            } if isinstance(raw.get("env"), dict) else {}
            clean[str(name).strip()] = {
                "transport": transport,
                "command": command,
                "args": args,
                "env": env,
                "enabled": raw.get("enabled") is not False,
                "url": url,
                "headers": {str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
                "env_refs": {str(k): str(v) for k, v in (raw.get("env_refs") or {}).items()},
                "header_env": {str(k): str(v) for k, v in (raw.get("header_env") or {}).items()},
                "cwd": str(raw.get("cwd") or Path.home()),
                "applications": [str(v) for v in raw.get("applications", [])],
                "environment": {str(k): str(v) for k, v in (raw.get("environment") or {}).items()
                                if k in {"location", "workspace", "platform", "account_label"}},
            }
        except (McpClientError, ValueError) as exc:
            # One unavailable application must not suppress unrelated services.
            clean[str(name).strip()] = {
                "transport": "unavailable", "command": "", "args": [], "env": {}, "enabled": False,
                "url": "", "headers": {}, "env_refs": {}, "header_env": {}, "cwd": "",
                "applications": [v for v in raw.get("applications", []) if isinstance(v, str)]
                    if isinstance(raw, dict) and isinstance(raw.get("applications", []), list) else [],
                "environment": {}, "configuration_error": getattr(exc, "code", "mcp.config.invalid"),
            }
    return clean


def list_servers() -> List[Dict[str, Any]]:
    """已配置服务器的只读清单（不含 env 值，防凭据泄漏给模型）。"""
    rows = []
    for name, cfg in sorted(load_server_config().items()):
        missing = sorted({v for key in ("env_refs", "header_env") for v in cfg[key].values()
                          if not os.environ.get(v)})
        parsed = urllib.parse.urlsplit(cfg["url"])
        rows.append({
            "server": name,
            "enabled": bool(cfg["enabled"]),
            "transport": cfg["transport"],
            "command": cfg["command"],
            "argument_count": len(cfg["args"]),
            "env_keys": sorted(set(cfg["env"]) | set(cfg["env_refs"])),
            "credential_env_names": sorted(set(cfg["env_refs"].values()) | set(cfg["header_env"].values())),
            "missing_env_names": missing,
            "endpoint_origin": urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")),
            "environment": _redact(cfg["environment"], cfg),
            "applications": cfg["applications"],
            "configuration_error": cfg.get("configuration_error"),
            "connection_state": "configuration_invalid" if cfg.get("configuration_error") else "disabled" if not cfg["enabled"] else "configuration_incomplete" if missing else "configured_not_connected",
            "config_path": str(CONFIG_PATH),
        })
    return rows


def application_connections(*, app_id: str = "", offset: int = 0, limit: int = 20) -> Dict[str, Any]:
    """Project the existing application dictionary and owner MCP configuration.

    This is a view, not a second registry or proof that a backend implements an
    application. Only explicit owner associations select connection candidates.
    """
    from capability_dictionary import load_dictionary
    release = load_dictionary()
    servers = list_servers()
    apps = [a for a in release.applications["apps"] if not app_id or a["app_id"] == app_id]
    start, count = max(0, int(offset)), max(1, min(50, int(limit)))
    rows = []
    for app in apps[start:start + count]:
        connections = [s for s in servers if app["app_id"] in s["applications"]]
        connectable = any(s["connection_state"] == "configured_not_connected" for s in connections)
        rows.append({"app_id": app["app_id"], "name": app["name"], "adapter": app.get("adapter"),
            "declared_actions": len(app["actions"]),
            "implemented_definitions": sum(bool(release.tools[n]["runtime"].get("implemented")) for n in app["actions"]),
            "connections": connections, "connection_state": "configured_not_verified" if connectable else "configuration_blocked" if connections else "not_configured",
            "setup_required": [] if connectable else ["resolve_connection_configuration"] if connections else ["owner_configured_mcp_server", "environment_location", "application_association"],
            "next_action": "mcp.tools.list" if connectable else None})
    return {"applications": rows, "total": len(apps), "offset": start,
            "next_offset": start + len(rows) if start + len(rows) < len(apps) else None,
            "dictionary_sha256": release.sha256}


def _redact(value: Any, cfg: Dict[str, Any]) -> Any:
    secrets = {str(v) for key in ("env", "headers") for v in cfg.get(key, {}).values() if str(v)}
    secrets.update(os.environ[v] for key in ("env_refs", "header_env")
                   for v in cfg.get(key, {}).values() if os.environ.get(v))
    secrets.update(v.split(" ", 1)[1] for v in tuple(secrets)
                   if " " in v and v.split(" ", 1)[0].lower() in {"bearer", "basic"})
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "<credential-redacted>")
        return value
    if isinstance(value, list):
        return [_redact(v, cfg) for v in value]
    if isinstance(value, dict):
        return {_redact(k, cfg): _redact(v, cfg) for k, v in value.items()}
    return value


def _resolve_server(server: str) -> Dict[str, Any]:
    name = str(server or "").strip()
    if not name:
        raise McpClientError("mcp.server.required", "target or args.server is required")
    config = load_server_config()
    cfg = config.get(name)
    if cfg is None:
        raise McpClientError(
            "mcp.server.unknown",
            f"'{name}' is not configured; known: {sorted(config.keys())}",
        )
    if cfg.get("configuration_error"):
        raise McpClientError(cfg["configuration_error"], name)
    if not cfg["enabled"]:
        raise McpClientError("mcp.server.disabled", name)
    for field, destination in (("env_refs", "env"), ("header_env", "headers")):
        for key, variable in cfg[field].items():
            value = os.environ.get(variable)
            if not value:
                raise McpClientError("mcp.credentials.missing", variable)
            cfg[destination][key] = value
    return cfg


def _safe_env(extra: Dict[str, str]) -> Dict[str, str]:
    env = {key: os.environ[key] for key in _INHERIT_ENV_KEYS if os.environ.get(key)}
    env.update(extra)
    return env


class _ServerProcess:
    """一个 MCP 服务器子进程的完整生命周期（行分隔 JSON-RPC）。"""

    def __init__(self, cfg: Dict[str, Any], timeout_ms: int):
        self.timeout_ms = max(1_000, min(int(timeout_ms), MAX_TIMEOUT_MS))
        self.deadline = time.monotonic() + self.timeout_ms / 1000
        self.cfg = cfg
        # Windows 下 npx/uvx 实为 .cmd  shim，CreateProcess 只认可执行
        # 本体——先经 PATHEXT 解析成真实路径，否则配置里的 "npx" 会
        # 直接 FileNotFoundError。解析失败即干净报错，不落到进程层。
        command = shutil.which(cfg["command"])
        if command is None:
            raise McpClientError(
                "mcp.server.command_not_found",
                f"'{cfg['command']}' is not on PATH",
            )
        # POSIX 下放进独立进程组，超时清理才能整组收割（与 Windows 的
        # taskkill /T 对应）。
        try:
            self._proc = subprocess.Popen(
                [command, *cfg["args"]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_safe_env(cfg["env"]),
                cwd=cfg.get("cwd") or str(Path.home()),
                # 桌面冻结应用内 spawn 控制台进程必须隐窗，否则每次调用
                # 都会闪一个黑色控制台（与本仓 sandbox_runtime 同款约定）。
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise McpClientError("mcp.server.spawn_failed", str(exc)) from exc
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._stderr_tail = b""
        self._stdout_flood = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # stderr 必须有独立消费者：stdio MCP 服务器（npx 安装日志、node
        # 警告）普遍往 stderr 打日志，PIPE 无人读时写满管道缓冲（Windows
        # 约 64KB）即阻塞服务器进程，表现为首次调用就超时。
        self._stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_reader.start()
        self._next_id = 0

    def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        received_bytes = 0
        try:
            while True:
                raw = self._proc.stdout.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                received_bytes += len(raw)
                if received_bytes > 2 * MAX_LINE_BYTES:
                    self._stdout_flood = True
                    break
                if self._lines.qsize() >= _MAX_QUEUED_LINES:
                    # 消费侧上限（512 行无响应即 flood）远小于此；到这里的
                    # 只可能是失控服务器，停止排队防止内存无界增长。
                    self._stdout_flood = True
                    break
                self._lines.put(raw)
                if len(raw) > MAX_LINE_BYTES:
                    break
        except Exception:
            pass
        finally:
            self._lines.put(None)

    def _stderr_loop(self) -> None:
        assert self._proc.stderr is not None
        try:
            while True:
                chunk = self._proc.stderr.read(4096)
                if not chunk:
                    break
                self._stderr_tail = (self._stderr_tail + chunk)[-_STDERR_TAIL_BYTES:]
        except Exception:
            pass

    def _send(self, payload: Dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > MAX_LINE_BYTES:
            raise McpClientError("mcp.request.too_large", str(len(data)))
        # A server can stop reading stdin before the client starts waiting for
        # stdout. Bound the write too; a full pipe must not bypass the deadline.
        sent = queue.Queue(maxsize=1)
        def write():
            try:
                self._proc.stdin.write(data + b"\n")
                self._proc.stdin.flush()
                sent.put(None)
            except (BrokenPipeError, OSError, ValueError) as exc:
                sent.put(type(exc).__name__)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            error = sent.get(timeout=max(0, self.deadline - time.monotonic()))
        except queue.Empty:
            self._kill_tree()
            writer.join(timeout=1)
            raise McpClientError("mcp.timeout", f"{self.timeout_ms}ms") from None
        if error:
            raise McpClientError("mcp.server.exited", error)

    def _recv_response(self, request_id: int) -> Dict[str, Any]:
        deadline = self.deadline
        received = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpClientError("mcp.timeout", f"{self.timeout_ms}ms")
            try:
                raw = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise McpClientError("mcp.timeout", f"{self.timeout_ms}ms") from None
            if raw is None:
                # stdout EOF：诊断信息直接取 stderr 读线程的尾部缓冲。
                # 此处绝不能同步 read() stderr——服务器已死但其孙进程
                # （npx shim 的 worker）仍持有写端时该读永不返回，
                # timeout_ms 保护会被整个架空。
                if self._stdout_flood:
                    raise McpClientError(
                        "mcp.protocol.flood",
                        f"server produced >{_MAX_QUEUED_LINES} queued stdout lines",
                    )
                detail = _redact(self._stderr_tail.decode("utf-8", errors="replace").strip()[-400:], self.cfg)
                raise McpClientError("mcp.server.exited", detail)
            received += 1
            if len(raw) > MAX_LINE_BYTES:
                raise McpClientError("mcp.response.too_large", "response line exceeds limit")
            if received > 512:
                raise McpClientError("mcp.protocol.flood", ">512 lines without response")
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception:
                continue  # 服务器 banner/日志行，跳过
            if not isinstance(message, dict):
                continue
            if type(message.get("id")) is int and message["id"] == request_id:
                return message

    def request(self, method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        response = self._recv_response(request_id)
        if isinstance(response.get("error"), dict):
            err = response["error"]
            raise McpClientError("mcp.rpc.error", _redact(f"{err.get('code')}: {err.get('message')}", self.cfg))
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpClientError("mcp.protocol.invalid", "result is not an object")
        return result

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def handshake(self) -> Dict[str, Any]:
        info = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        if info.get("protocolVersion") not in SUPPORTED_PROTOCOLS:
            raise McpClientError("mcp.protocol.unsupported", str(info.get("protocolVersion")))
        self.protocol_version = info["protocolVersion"]
        self.notify("notifications/initialized")
        return info

    def _kill_tree(self) -> None:
        """按进程树收割：Windows 的 .cmd shim 之下还有真实孙进程。"""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
                return
            except Exception:
                pass
        else:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                return
            except Exception:
                pass
        try:
            self._proc.kill()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._proc.stdin is not None and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=3)
            return
        except Exception:
            pass
        self._kill_tree()
        # kill 后仍要 wait 收尸，避免句柄/僵尸泄漏。
        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise McpClientError("mcp.http.redirect_denied", "configure the final endpoint explicitly")


class _HttpServer:
    """Owner-configured Streamable HTTP; one bounded session per invocation."""

    handshake = _ServerProcess.handshake

    def __init__(self, cfg: Dict[str, Any], timeout_ms: int):
        self.cfg = cfg
        self.timeout_ms = max(1_000, min(int(timeout_ms), MAX_TIMEOUT_MS))
        self.deadline = time.monotonic() + self.timeout_ms / 1000
        self._next_id = 0
        self.session_id = None
        self.protocol_version = None
        self.opener = urllib.request.build_opener(_NoRedirect())

    def _headers(self):
        # Protocol fields cannot be overridden by stored credential headers.
        headers = {k: v for k, v in self.cfg["headers"].items()
                   if k.lower() not in {"content-type", "accept", "mcp-session-id", "mcp-protocol-version", "host"}}
        headers.update({"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, payload):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise McpClientError("mcp.timeout")
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > MAX_LINE_BYTES:
            raise McpClientError("mcp.request.too_large")
        request = urllib.request.Request(self.cfg["url"], data=raw, headers=self._headers(), method="POST")
        try:
            with self.opener.open(request, timeout=remaining) as response:
                sid = response.headers.get("Mcp-Session-Id")
                if sid:
                    if len(sid) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in sid):
                        raise McpClientError("mcp.protocol.invalid", "invalid session id")
                    if self.session_id and sid != self.session_id:
                        raise McpClientError("mcp.protocol.invalid", "session changed")
                    self.session_id = sid
                if "id" not in payload:
                    return {}
                mime = response.headers.get_content_type()
                if mime == "application/json":
                    body = b"".join(self._response_chunks(response))
                    value = json.loads(body)
                    if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] != payload["id"]:
                        raise McpClientError("mcp.protocol.invalid", "response identity mismatch")
                    return value
                if mime != "text/event-stream":
                    raise McpClientError("mcp.protocol.invalid", "expected JSON or SSE")
                pending = b""
                for chunk in self._response_chunks(response):
                    pending = (pending + chunk).replace(b"\r\n", b"\n")
                    while b"\n\n" in pending:
                        event, pending = pending.split(b"\n\n", 1)
                        data = [line[5:].lstrip() for line in event.split(b"\n") if line.startswith(b"data:")]
                        if data:
                            value = json.loads(b"\n".join(data))
                            if isinstance(value, dict) and type(value.get("id")) is int and value["id"] == payload["id"]:
                                return value
                raise McpClientError("mcp.outcome.unknown", "stream ended without matching response")
        except McpClientError:
            raise
        except urllib.error.HTTPError as exc:
            raise McpClientError("mcp.http.status", str(exc.code)) from None
        except (TimeoutError, OSError, ValueError) as exc:
            raise McpClientError("mcp.transport.failed", type(exc).__name__) from None

    def _response_chunks(self, response):
        size = 0
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise McpClientError("mcp.timeout", f"{self.timeout_ms}ms")
            # urllib exposes an HTTPResponse buffered socket. read1 performs
            # one underlying read, so a peer dripping bytes cannot reset the
            # whole deadline while read(n)/readline waits for a complete body.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_LINE_BYTES + 1 - size))
            if not chunk:
                return
            size += len(chunk)
            if size > MAX_LINE_BYTES:
                raise McpClientError("mcp.response.too_large")
            yield chunk

    def request(self, method, params=None):
        self._next_id += 1
        response = self._post({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})
        if isinstance(response.get("error"), dict):
            raise McpClientError("mcp.rpc.error", str(response["error"].get("code")))
        if not isinstance(response.get("result"), dict):
            raise McpClientError("mcp.protocol.invalid", "result is not an object")
        return response["result"]

    def notify(self, method):
        self._post({"jsonrpc": "2.0", "method": method})

    def close(self):
        if self.session_id:
            try:
                request = urllib.request.Request(self.cfg["url"], headers=self._headers(), method="DELETE")
                with self.opener.open(request, timeout=1):
                    pass
            except Exception:
                pass  # Session cleanup never changes the observed tool outcome.


def _connect(server: str, timeout_ms: int):
    cfg = _resolve_server(server)
    proc: _ServerProcess | None = None
    try:
        proc = _HttpServer(cfg, timeout_ms) if cfg["transport"] == "streamable_http" else _ServerProcess(cfg, timeout_ms)
        info = proc.handshake()
        return info, proc
    except Exception:
        if proc is not None:
            proc.close()
        raise


def list_tools(server: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS, cursor: str | None = None,
               capture=None) -> Dict[str, Any]:
    info, proc = _connect(server, timeout_ms)
    try:
        result = _redact(proc.request("tools/list", {"cursor": cursor} if cursor else {}), proc.cfg)
        evidence = capture(result) if capture else None
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise McpClientError("mcp.protocol.invalid", "tools is not a list")
        # 输出上限：整体序列化超预算即留名占位并停止——放不下就丢，
        # 绝不把超预算的完整工具（大 inputSchema 可达数 MB）放大进
        # 模型上下文与审计记录。
        trimmed: List[Dict[str, Any]] = []
        budget = MAX_OUTPUT_BYTES
        truncated = False
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            row = dict(tool)
            desc = str(row.get("description") or "")
            if len(desc.encode("utf-8")) > 4096:
                row["description"] = desc[:2048] + "…"
            size = len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
            if size > budget:
                trimmed.append({"name": str(row.get("name") or ""), "_truncated": True})
                truncated = True
                break
            budget -= size
            trimmed.append(row)
        return {
            "server": server,
            "server_info": _redact(info.get("serverInfo") or {}, proc.cfg),
            "tools": trimmed,
            "truncated": truncated,
            "next_cursor": result.get("nextCursor"),
            "complete": not truncated and not result.get("nextCursor"),
            "evidence": evidence,
            "connection_fingerprint": _configuration_fingerprint(proc.cfg),
        }
    finally:
        proc.close()


def call_tool(
    server: str,
    tool: str,
    arguments: Dict[str, Any] | None = None,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    capture=None,
) -> Dict[str, Any]:
    tool_name = str(tool or "").strip()
    if not tool_name:
        raise McpClientError("mcp.tool.required", "args.tool is required")
    if arguments is not None and not isinstance(arguments, dict):
        raise McpClientError("mcp.arguments.invalid", "args.arguments must be an object")
    info, proc = _connect(server, timeout_ms)
    try:
        try:
            result = _redact(proc.request("tools/call", {"name": tool_name, "arguments": dict(arguments or {})}), proc.cfg)
            evidence = capture(result) if capture else None
        except Exception as exc:
            # The call may already have changed the remote application. Neither
            # a transport error nor a failed local evidence write proves rollback.
            code = exc.code if isinstance(exc, McpClientError) else type(exc).__name__
            raise McpClientError("mcp.outcome.unknown", code) from None
        content = result.get("content")
        texts: List[str] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text") or ""))
        joined = "\n".join(texts)
        encoded = joined.encode("utf-8")
        truncated = len(encoded) > MAX_OUTPUT_BYTES
        if truncated:
            joined = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
        structured = result.get("structuredContent")
        structured_bytes = len(json.dumps(structured, ensure_ascii=False).encode()) if structured is not None else 0
        omitted_modalities = sorted({str(b.get("type")) for b in content or []
                                     if isinstance(b, dict) and b.get("type") != "text"})
        return {
            "server": server,
            "tool": tool_name,
            "is_error": bool(result.get("isError")),
            "text": joined,
            "truncated": truncated or structured_bytes > MAX_OUTPUT_BYTES,
            "structured": structured if isinstance(structured, dict) and structured_bytes <= MAX_OUTPUT_BYTES else None,
            "structured_truncated": structured_bytes > MAX_OUTPUT_BYTES,
            "content_types_requiring_observation": omitted_modalities,
            "evidence": evidence,
            "connection_fingerprint": _configuration_fingerprint(proc.cfg),
            "observation_scope": "server_report_not_independent_target_readback",
        }
    finally:
        proc.close()


def _configuration_fingerprint(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


__all__ = [
    "CONFIG_PATH",
    "DEFAULT_TIMEOUT_MS",
    "McpClientError",
    "call_tool",
    "list_servers",
    "list_tools",
    "load_server_config",
]
