"""Rejected HTTP uploads preserve their response without unbounded draining."""
from __future__ import annotations

import socket
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from total_gateway import server


def test_rejected_post_with_delayed_body_returns_401_without_dispatch():
    from tests.test_desktop_gateway_api import DesktopGatewayApiTests

    case = DesktopGatewayApiTests("test_connection_probe_uses_authenticated_backend_route")
    case.setUp()
    try:
        send = case.connection.send

        def delayed_send(data):
            if data == b"{}":
                time.sleep(0.02)
            return send(data)

        with mock.patch.object(case.connection, "send", side_effect=delayed_send):
            response, payload = case.request("POST", "/api/v1/llm/probe", {}, token="", origin=None)
        assert response.status == 401
        assert response.getheader("Connection") == "close"
        assert payload["reason_code"] == "desktop_api.unauthorized"
        assert case.upstream.calls == []
        response, payload = case.request("POST", "/api/v1/llm/probe", {})
        assert response.status == 200 and payload["ok"] is True
        assert len(case.upstream.calls) == 1
    finally:
        case.tearDown()


def handler():
    connection = mock.Mock()
    connection.gettimeout.return_value = 7.0
    return SimpleNamespace(connection=connection, wfile=mock.Mock(), rfile=mock.Mock())


def test_rejection_drain_stops_at_byte_budget(monkeypatch):
    value = handler()
    value.rfile.read1.side_effect = lambda count: b"x" * count
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: 0.0))
    server.GatewayRequestHandler._finish_rejected_connection(value)
    assert sum(call.args[0] for call in value.rfile.read1.call_args_list) == 64 * 1024
    value.wfile.flush.assert_called_once_with()
    value.connection.shutdown.assert_called_once_with(socket.SHUT_WR)
    assert value.connection.settimeout.call_args == mock.call(7.0)


def test_rejection_drain_has_one_absolute_deadline(monkeypatch):
    value = handler()
    value.rfile.read1.return_value = b"x"
    ticks = iter((10.0, 10.1, 10.2, 10.3))
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    server.GatewayRequestHandler._finish_rejected_connection(value)
    assert value.rfile.read1.call_count == 2
    timeouts = [call.args[0] for call in value.connection.settimeout.call_args_list]
    assert timeouts[:-1] == pytest.approx([0.15, 0.05])
    assert timeouts[-1] == 7.0


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionResetError()])
def test_rejection_drain_restores_timeout_after_peer_error(monkeypatch, error):
    value = handler()
    value.rfile.read1.side_effect = error
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: 0.0))
    server.GatewayRequestHandler._finish_rejected_connection(value)
    value.rfile.read1.assert_called_once()
    assert value.connection.settimeout.call_args == mock.call(7.0)


def test_rejection_drain_stops_on_eof(monkeypatch):
    value = handler()
    value.rfile.read1.return_value = b""
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: 0.0))
    server.GatewayRequestHandler._finish_rejected_connection(value)
    value.rfile.read1.assert_called_once()
    assert value.connection.settimeout.call_args == mock.call(7.0)
