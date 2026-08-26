# 2026-08-26 add: tiangong_shell 单测 —— 只测 ini 解析 + 启动参数构造，不真起子进程
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shell"))

import tiangong_shell as shell  # noqa: E402


FULL_INI = """\
[gateway]
desktop_token = test-desktop-token-12345678901234567890
port = 17173
backend_token = test-backend-token-123456789012345678
life_token = test-life-token-1234567890123456789012
communication_token = test-comm-token-12345678901234567890
artifact_token = test-artifact-token-123456789012345678
workspace_root = C:\\tiangong\\workspace
release_source_root = C:\\tiangong\\src
"""


def _write_ini(tmp_path, text=FULL_INI):
    ini = tmp_path / "tiangong-launcher.ini"
    ini.write_text(text, encoding="utf-8")
    return ini


def test_load_launcher_config_full(tmp_path):
    config = shell.load_launcher_config(_write_ini(tmp_path))
    assert config["port"] == 17173
    assert config["desktop_token"].startswith("test-desktop-token-")
    assert config["workspace_root"] == "C:\\tiangong\\workspace"
    assert config["release_source_root"] == "C:\\tiangong\\src"


def test_load_launcher_config_rejects_bad_ini(tmp_path):
    with pytest.raises(shell.LauncherError):
        shell.load_launcher_config(tmp_path / "missing.ini")  # 文件不存在
    with pytest.raises(shell.LauncherError):
        shell.load_launcher_config(_write_ini(tmp_path, "[other]\nport = 1\n"))  # 缺 [gateway]
    with pytest.raises(shell.LauncherError):
        shell.load_launcher_config(_write_ini(tmp_path, "[gateway]\nport = abc\n"))  # port 非法
    with pytest.raises(shell.LauncherError):
        shell.load_launcher_config(_write_ini(tmp_path, "[gateway]\nport = 80\n"))  # token 为空


def test_build_child_env_maps_all_tokens(tmp_path):
    config = shell.load_launcher_config(_write_ini(tmp_path))
    env = shell.build_child_env(config, base_env={})
    assert env["TIANGONG_GATEWAY_ENVIRONMENT"] == "test"
    assert env["TIANGONG_GATEWAY_DEPLOYMENT_MODE"] == "embedded"
    assert env["TIANGONG_GATEWAY_PORT"] == "17173"
    assert env["TIANGONG_DESKTOP_TOKEN"] == config["desktop_token"]
    assert env["TIANGONG_BACKEND_INTERNAL_TOKEN"] == config["backend_token"]
    assert env["TIANGONG_LIFE_INTERNAL_TOKEN"] == config["life_token"]
    assert env["TIANGONG_GATEWAY_COMMUNICATION_TOKEN"] == config["communication_token"]
    assert env["TIANGONG_GATEWAY_LIFE_INTENT_TOKEN"] == config["artifact_token"]
    assert env["TIANGONG_GATEWAY_WORKSPACE_ROOT"] == config["workspace_root"]
    assert env["TIANGONG_GATEWAY_RELEASE_SOURCE_ROOT"] == config["release_source_root"]
    assert env["PYTHONUTF8"] == "1"


def test_build_child_command_prefers_pythonw(tmp_path):
    runtime = tmp_path / "runtime" / "python312"
    runtime.mkdir(parents=True)
    (runtime / "pythonw.exe").write_bytes(b"MZ")
    command = shell.build_child_command(tmp_path)
    assert command[0].endswith("pythonw.exe")
    assert command[1:] == ["-m", "total_gateway"]
    with pytest.raises(shell.LauncherError):
        shell.build_child_command(tmp_path / "empty")


def test_occupied_port_falls_back_and_writes_back(tmp_path):
    ini = _write_ini(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        occupied = blocker.getsockname()[1]
        port = shell.find_available_port(occupied)
        assert port != occupied and 1 <= port <= 65535
        shell.write_back_port(ini, port)
    config = shell.load_launcher_config(ini)
    assert config["port"] == port
