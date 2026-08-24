"""MCP omni_body 接入（v1）端到端测试。

用真实子进程（sys.executable 驱动的假 MCP 服务器脚本）走完整
stdio JSON-RPC 链路：handshake -> tools/list -> tools/call，以及
超时、未知/禁用服务器、错误工具、配置解析与安全边界。
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from omni_body_skill.tools import mcp_client
from omni_body_skill.tools.mcp_client import McpClientError


FAKE_SERVER = textwrap.dedent(
    """
    import json, sys, time
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        if "id" not in msg:
            continue
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-mcp", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": [
                {"name": "echo", "description": "echo tool", "inputSchema": {"type": "object", "properties": {"payload": {"type": "string"}}}},
                {"name": "fail", "description": "always fails", "inputSchema": {"type": "object"}},
                {"name": "slow", "description": "sleeps 10s", "inputSchema": {"type": "object"}},
            ]}
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments") or {}
            if name == "fail":
                result = {"content": [{"type": "text", "text": "boom"}], "isError": True}
            elif name == "slow":
                time.sleep(10)
                result = {"content": [{"type": "text", "text": "finally"}]}
            else:
                result = {"content": [{"type": "text", "text": "echo:" + str(args.get("payload", ""))}]}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "method not found"}}) + "\\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.fixture()
def mcp_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server_script = tmp_path / "fake_mcp_server.py"
    server_script.write_text(FAKE_SERVER, encoding="utf-8")
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "fake": {
                        "command": sys.executable,
                        "args": [str(server_script)],
                        "env": {"FAKE_SECRET": "should-not-leak"},
                    },
                    "disabled_one": {
                        "command": sys.executable,
                        "args": [str(server_script)],
                        "enabled": False,
                    },
                }
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", config)
    return config


def test_servers_list_hides_env_values(mcp_setup) -> None:
    rows = mcp_client.list_servers()
    assert [row["server"] for row in rows] == ["disabled_one", "fake"]
    fake = next(row for row in rows if row["server"] == "fake")
    assert fake["env_keys"] == ["FAKE_SECRET"]  # only key names, never values
    assert "should-not-leak" not in json.dumps(rows, ensure_ascii=False)


def test_unknown_and_disabled_servers_fail_closed(mcp_setup) -> None:
    with pytest.raises(McpClientError) as caught:
        mcp_client.list_tools("nope")
    assert caught.value.code == "mcp.server.unknown"
    with pytest.raises(McpClientError) as caught:
        mcp_client.list_tools("disabled_one")
    assert caught.value.code == "mcp.server.disabled"


def test_tools_list_end_to_end(mcp_setup) -> None:
    result = mcp_client.list_tools("fake", timeout_ms=15000)
    assert result["server_info"]["name"] == "fake-mcp"
    assert [tool["name"] for tool in result["tools"]] == ["echo", "fail", "slow"]


def test_tool_call_end_to_end(mcp_setup) -> None:
    result = mcp_client.call_tool("fake", "echo", {"payload": "nihao"}, timeout_ms=15000)
    assert result["text"] == "echo:nihao"
    assert result["is_error"] is False


def test_tool_error_is_reported_not_raised(mcp_setup) -> None:
    result = mcp_client.call_tool("fake", "fail", {}, timeout_ms=15000)
    assert result["is_error"] is True
    assert result["text"] == "boom"


def test_timeout_kills_slow_tool(mcp_setup) -> None:
    with pytest.raises(McpClientError) as caught:
        mcp_client.call_tool("fake", "slow", {}, timeout_ms=1500)
    assert caught.value.code == "mcp.timeout"


def test_missing_config_means_empty_not_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", tmp_path / "absent.json")
    assert mcp_client.list_servers() == []
    with pytest.raises(McpClientError) as caught:
        mcp_client.list_tools("anything")
    assert caught.value.code == "mcp.server.unknown"


def test_command_not_found_is_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置里的命令不在 PATH 上（如未装 node 时的 npx）须干净报错。"""
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps({"servers": {"ghost": {"command": "tiangong-no-such-command-xyz"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", config)
    with pytest.raises(McpClientError) as caught:
        mcp_client.list_tools("ghost", timeout_ms=5000)
    assert caught.value.code == "mcp.server.command_not_found"


def test_omni_dispatch_and_risk_classification(mcp_setup) -> None:
    from omni_body_skill.tools.pro_apps_v34 import PRO_APP_ACTIONS, handle_pro_app_action

    assert PRO_APP_ACTIONS["mcp.servers.list"]["risk"] == "A0"
    assert PRO_APP_ACTIONS["mcp.tools.list"]["risk"] == "A0"
    # Any MCP tool may have side effects: A3 rides the gateway confirmation chain.
    assert PRO_APP_ACTIONS["mcp.tool.call"]["risk"] == "A3"

    class _StubRuntime:
        pass

    listed = handle_pro_app_action(_StubRuntime(), "x", "mcp.servers.list", None, {})
    assert listed["success"] is True
    assert listed["result"]["count"] == 2

    echoed = handle_pro_app_action(
        _StubRuntime(),
        "x",
        "mcp.tool.call",
        "fake",
        {"tool": "echo", "arguments": {"payload": "hi"}, "timeout_ms": 15000},
    )
    assert echoed["success"] is True
    assert echoed["result"]["text"] == "echo:hi"


# ---------- stderr 管道与进程生命周期回归 ----------

NOISY_SERVER = textwrap.dedent(
    """
    import json, sys
    # 话痨服务器：先写 300KB stderr（远超 Windows 管道缓冲 ~64KB），
    # 再正常服务协议。旧客户端 stderr 无人消费，服务器阻塞在写 stderr，
    # 表现为首次调用即超时。
    sys.stderr.write("E" * 300_000)
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg.get("method") == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "noisy", "version": "1.0"}}
        elif msg.get("method") == "tools/call":
            result = {"content": [{"type": "text", "text": "echo:" + str((msg["params"].get("arguments") or {}).get("payload", ""))}]}
        else:
            result = {"tools": []}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


def _write_config(tmp_path: Path, name: str, script_body: str, *, extra: dict | None = None) -> Path:
    script = tmp_path / f"{name}_server.py"
    script.write_text(script_body, encoding="utf-8")
    servers = {
        name: {"command": sys.executable, "args": [str(script)]},
    }
    if extra:
        servers.update(extra)
    config = tmp_path / f"{name}_mcp_servers.json"
    config.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return config


def test_noisy_stderr_server_still_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stderr 写满管道缓冲后服务器仍能正常响应（死锁回归）。"""
    config = _write_config(tmp_path, "noisy", NOISY_SERVER)
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", config)
    result = mcp_client.call_tool("noisy", "echo", {"payload": "ok"}, timeout_ms=15_000)
    assert result["text"] == "echo:ok"


CRASH_SERVER = textwrap.dedent(
    """
    import json, subprocess, sys
    # 崩溃服务器：initialize 时先 spawn 孙进程（继承 stderr 写端、存活
    # 10s），随后自身退出且不回包。旧客户端在 stdout EOF 后同步 read()
    # stderr，被孙进程的写端挂住，3s 超时的调用实测挂到 60s。
    for line in sys.stdin:
        msg = json.loads(line.strip())
        if msg.get("method") == "initialize":
            # 孙进程只持有 stderr 写端（stdout 走 DEVNULL），精确复现
            # "服务器已死 + 孙进程挂住 stderr" 的崩溃现场。
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], stdout=subprocess.DEVNULL)
            sys.exit(3)
    """
)


def test_server_exit_with_grandchild_stderr_holder_returns_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _time

    config = _write_config(tmp_path, "crashy", CRASH_SERVER)
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", config)
    started = _time.monotonic()
    with pytest.raises(McpClientError) as caught:
        mcp_client.call_tool("crashy", "echo", {}, timeout_ms=5_000)
    elapsed = _time.monotonic() - started
    assert caught.value.code == "mcp.server.exited"
    # 诊断走 stderr 尾部缓冲，绝不等待孙进程放开写端（孙进程存活 10s）。
    assert elapsed < 4.5, f"call blocked for {elapsed:.1f}s despite grandchild stderr holder"


def test_list_tools_enforces_output_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """单个工具带 3MB inputSchema 时清单必须截断到预算内（放大回归）。"""
    big_server = textwrap.dedent(
        """
        import json, sys
        for line in sys.stdin:
            msg = json.loads(line.strip())
            if "id" not in msg:
                continue
            if msg.get("method") == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "big", "version": "1.0"}}
            else:
                result = {"tools": [
                    {"name": "small", "description": "ok", "inputSchema": {"type": "object"}},
                    {"name": "huge", "description": "giant schema", "inputSchema": {"type": "object", "properties": {"blob": {"type": "string", "default": "x" * 3_000_000}}}},
                    {"name": "after", "description": "never reached", "inputSchema": {"type": "object"}},
                ]}
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
            sys.stdout.flush()
        """
    )
    config = _write_config(tmp_path, "big", big_server)
    monkeypatch.setattr(mcp_client, "CONFIG_PATH", config)
    result = mcp_client.list_tools("big", timeout_ms=15_000)
    assert result["truncated"] is True
    serialized = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    assert serialized <= 256 * 1024 + 2_048, f"tools list inflated to {serialized} bytes"
    names = [tool["name"] for tool in result["tools"]]
    assert "after" not in names  # 超预算后立即停止
    assert result["tools"][-1].get("_truncated") is True


def test_is_error_failure_shape_carries_error_code(mcp_setup) -> None:
    from omni_body_skill.tools.pro_apps_v34 import handle_pro_app_action

    class _StubRuntime:
        pass

    failed = handle_pro_app_action(
        _StubRuntime(),
        "x",
        "mcp.tool.call",
        "fake",
        {"tool": "fail", "timeout_ms": 15000},
    )
    # isError 与 McpClientError 两种失败形状一致：error 键始终在场。
    assert failed["success"] is False
    assert failed["error"] == "mcp.tool.is_error"
    assert failed["result"]["is_error"] is True
