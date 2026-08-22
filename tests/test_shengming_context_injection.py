"""生命链对话注入修复（2026-08-22）的契约测试。

旧实现 import 不存在的 v3.shengming.life_panel 模块且被双层 except 吞成
空串——生命状态从未进入对话。新实现从 7184 网关拉权威面板；失效时返回
可见降级说明而非空串。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import pytest


@pytest.fixture()
def fake_gateway(monkeypatch: pytest.MonkeyPatch):
    state = {"panel": {"ok": True, "summary": {}, "budget": {}, "boundaries": {}}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/v1/v3/life/panel":
                body = json.dumps(state["panel"]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TIANGONG_GATEWAY_URL", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _context(monkeypatch: pytest.MonkeyPatch, enabled: str = "1"):
    monkeypatch.setenv("TIANGONG_SHENGMING_CONTEXT", enabled)
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for entry in (str(root / "app" / "backend" / "tiangong-backend"), str(root / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from v3.zongdiaodu import _shengming_context_string

    return _shengming_context_string()


def test_panel_summary_is_injected_from_authoritative_gateway(fake_gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gateway["panel"] = {
        "ok": True,
        "summary": {
            "completed_tasks_today": 3,
            "next_heavy_tick_minutes": 5,
            "recent_action": {"title": "整理知识库", "value_score": 0.8},
        },
        "budget": {"used": 2, "success_limit": 20},
        "boundaries": {
            "share": {"quiet_if_user_active": True},
            "autonomy": {"card_only_risks": ["A3", "A4"]},
        },
    }
    text = _context(monkeypatch)
    assert text.startswith("[后台生命链]")
    assert "完成 3 项" in text
    assert "整理知识库" in text
    assert "用户活跃时不主动打扰" in text
    assert "A3+任务仅生成卡片" in text


def test_gateway_failure_degrades_visibly_never_silent_empty(fake_gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIANGONG_GATEWAY_URL", "http://127.0.0.1:1")
    text = _context(monkeypatch)
    # 旧行为是静默空串；新契约是可见降级 + 不臆测指令。
    assert text.startswith("[后台生命链] 状态暂不可用")
    assert "不要臆测" in text


def test_kill_switch_returns_empty(fake_gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _context(monkeypatch, enabled="0") == ""


def test_flag_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIANGONG_SHENGMING_CONTEXT", raising=False)
    from pathlib import Path
    import sys

    root = Path(__file__).resolve().parents[1]
    for entry in (str(root / "app" / "backend" / "tiangong-backend"), str(root / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from v3 import peizhi

    monkeypatch.delattr(peizhi, "SHENGMING_LIFE_CHAIN_ENABLED", raising=False)
    import importlib

    importlib.reload(peizhi)
    try:
        assert peizhi.SHENGMING_LIFE_CHAIN_ENABLED is True
    finally:
        importlib.reload(peizhi)
