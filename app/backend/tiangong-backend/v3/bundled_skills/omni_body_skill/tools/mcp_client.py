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
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "tiangong-omni-body", "version": "3.5"}

CONFIG_PATH = Path.home() / ".tiangong" / "v3" / "mcp_servers.json"

DEFAULT_TIMEOUT_MS = 30_000
MAX_TIMEOUT_MS = 120_000
MAX_OUTPUT_BYTES = 256 * 1024
MAX_LINE_BYTES = 4 * 1024 * 1024
# stderr 尾部诊断缓冲与 stdout 读线程的生产侧上限（防失控服务器把
# _lines 队列灌成无界内存）。
_STDERR_TAIL_BYTES = 4096
_MAX_QUEUED_LINES = 100_000

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
        if not isinstance(raw, dict):
            continue
        command = str(raw.get("command") or "").strip()
        if not command:
            continue
        args = [str(item) for item in raw.get("args") or [] if str(item).strip()]
        env = {
            str(key): str(value)
            for key, value in (raw.get("env") or {}).items()
            if str(key).strip()
        } if isinstance(raw.get("env"), dict) else {}
        clean[str(name).strip()] = {
            "command": command,
            "args": args,
            "env": env,
            "enabled": raw.get("enabled") is not False,
        }
    return clean


def list_servers() -> List[Dict[str, Any]]:
    """已配置服务器的只读清单（不含 env 值，防凭据泄漏给模型）。"""
    rows = []
    for name, cfg in sorted(load_server_config().items()):
        rows.append({
            "server": name,
            "enabled": bool(cfg["enabled"]),
            "command": cfg["command"],
            "args": cfg["args"],
            "env_keys": sorted(cfg["env"].keys()),
            "config_path": str(CONFIG_PATH),
        })
    return rows


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
    if not cfg["enabled"]:
        raise McpClientError("mcp.server.disabled", name)
    return cfg


def _safe_env(extra: Dict[str, str]) -> Dict[str, str]:
    env = {key: os.environ[key] for key in _INHERIT_ENV_KEYS if os.environ.get(key)}
    env.update(extra)
    return env


class _ServerProcess:
    """一个 MCP 服务器子进程的完整生命周期（行分隔 JSON-RPC）。"""

    def __init__(self, cfg: Dict[str, Any], timeout_ms: int):
        self.timeout_ms = max(1_000, min(int(timeout_ms), MAX_TIMEOUT_MS))
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
                cwd=str(Path.home()),
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
        try:
            for raw in self._proc.stdout:
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
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_LINE_BYTES:
            raise McpClientError("mcp.request.too_large", str(len(data)))
        try:
            self._proc.stdin.write(data + b"\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            # 服务器已退出（stdin 断裂）时转成结构化错误，避免裸
            # BrokenPipeError 逃出 mcp.* 错误码体系。
            raise McpClientError("mcp.server.exited", f"stdin broken: {exc}") from None

    def _recv_response(self, request_id: int) -> Dict[str, Any]:
        deadline = time.monotonic() + self.timeout_ms / 1000
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
                detail = self._stderr_tail.decode("utf-8", errors="replace").strip()[-400:]
                raise McpClientError("mcp.server.exited", detail)
            received += 1
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
            if message.get("id") == request_id:
                return message

    def request(self, method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        response = self._recv_response(request_id)
        if isinstance(response.get("error"), dict):
            err = response["error"]
            raise McpClientError("mcp.rpc.error", f"{err.get('code')}: {err.get('message')}")
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


def _connect(server: str, timeout_ms: int) -> tuple[Dict[str, Any], _ServerProcess]:
    cfg = _resolve_server(server)
    proc: _ServerProcess | None = None
    try:
        proc = _ServerProcess(cfg, timeout_ms)
        info = proc.handshake()
        return info, proc
    except Exception:
        if proc is not None:
            proc.close()
        raise


def list_tools(server: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Dict[str, Any]:
    info, proc = _connect(server, timeout_ms)
    try:
        result = proc.request("tools/list", {})
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
            "server_info": info.get("serverInfo") or {},
            "tools": trimmed,
            "truncated": truncated,
        }
    finally:
        proc.close()


def call_tool(
    server: str,
    tool: str,
    arguments: Dict[str, Any] | None = None,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> Dict[str, Any]:
    tool_name = str(tool or "").strip()
    if not tool_name:
        raise McpClientError("mcp.tool.required", "args.tool is required")
    if not isinstance(arguments or {}, dict):
        raise McpClientError("mcp.arguments.invalid", "args.arguments must be an object")
    info, proc = _connect(server, timeout_ms)
    try:
        result = proc.request("tools/call", {"name": tool_name, "arguments": dict(arguments or {})})
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
        return {
            "server": server,
            "tool": tool_name,
            "is_error": bool(result.get("isError")),
            "text": joined,
            "truncated": truncated,
            "structured": result.get("structuredContent")
            if isinstance(result.get("structuredContent"), dict)
            else None,
        }
    finally:
        proc.close()


__all__ = [
    "CONFIG_PATH",
    "DEFAULT_TIMEOUT_MS",
    "McpClientError",
    "call_tool",
    "list_servers",
    "list_tools",
    "load_server_config",
]
