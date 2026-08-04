from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from communication_service.wechat_login import (
    HttpWechatLoginTransport,
    WECHAT_LOGIN_TTL_MS,
    WechatLoginError,
    WechatLoginManager,
)


class _Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(
        self,
        method,
        origin,
        path,
        *,
        body,
        bot_token,
        timeout_seconds,
    ):
        self.calls.append((method, origin, path, body, bot_token, timeout_seconds))
        return self.responses.pop(0)


class WechatLoginTests(unittest.TestCase):
    def test_status_get_keeps_platform_identity_headers_without_a_body(self) -> None:
        class Response:
            status = 200

            @staticmethod
            def read(_limit):
                return b'{"ret":0,"status":"wait"}'

            @staticmethod
            def close():
                return None

        class Connection:
            calls = []

            def __init__(self, host, port, *, timeout):
                self.host = host
                self.port = port
                self.timeout = timeout

            def request(self, method, path, *, body, headers):
                self.calls.append((method, path, body, dict(headers)))

            @staticmethod
            def getresponse():
                return Response()

            @staticmethod
            def close():
                return None

        with patch(
            "communication_service.wechat_login.http.client.HTTPSConnection",
            Connection,
        ):
            result = HttpWechatLoginTransport().request_json(
                "GET",
                "https://ilinkai.weixin.qq.com",
                "/ilink/bot/get_qrcode_status?qrcode=opaque",
                body=None,
                bot_token="",
                timeout_seconds=20,
            )
        self.assertEqual(result["status"], "wait")
        method, _path, body, headers = Connection.calls[-1]
        self.assertEqual(method, "GET")
        self.assertIsNone(body)
        self.assertEqual(headers["AuthorizationType"], "ilink_bot_token")
        self.assertTrue(headers["X-WECHAT-UIN"])
        self.assertNotIn("Content-Length", headers)
        self.assertNotIn("Content-Type", headers)

    def test_qr_login_is_short_lived_and_returns_secrets_only_to_the_runtime(self) -> None:
        transport = _Transport(
            [
                {"qrcode": "opaque-qr", "qrcode_img_content": "https://qr.example/content"},
                {
                    "status": "confirmed",
                    "ilink_bot_id": "bot-account",
                    "ilink_user_id": "wechat-user",
                    "bot_token": "secret-token",
                    "get_updates_buf": "cursor-1",
                },
            ]
        )
        manager = WechatLoginManager(transport)
        started = manager.start({}, now_ms=1_000)
        self.assertTrue(started.public["ok"])
        self.assertEqual(started.public["qrcode_url"], "https://qr.example/content")
        session_key = str(started.public["session_key"])

        confirmed = manager.wait({"session_key": session_key}, now_ms=2_000)
        self.assertTrue(confirmed.public["connected"])
        self.assertNotIn("bot_token", confirmed.public)
        self.assertEqual(confirmed.credentials["bot_token"], "secret-token")
        self.assertIn("qrcode=opaque-qr", transport.calls[-1][2])

    def test_expired_session_never_calls_platform_status(self) -> None:
        transport = _Transport([{"qrcode": "opaque-qr", "qrcode_url": "qr-content"}])
        manager = WechatLoginManager(transport)
        started = manager.start({}, now_ms=1_000)
        result = manager.wait(
            {"session_key": started.public["session_key"]},
            now_ms=1_000 + WECHAT_LOGIN_TTL_MS,
        )
        self.assertFalse(result.public["ok"])
        self.assertEqual(len(transport.calls), 1)

    def test_platform_redirect_cannot_escape_the_wechat_https_allowlist(self) -> None:
        transport = _Transport(
            [
                {"qrcode": "opaque-qr", "qrcode_url": "qr-content"},
                {"status": "scaned_but_redirect", "redirect_host": "https://evil.example"},
            ]
        )
        manager = WechatLoginManager(transport)
        started = manager.start({}, now_ms=1_000)
        with self.assertRaisesRegex(WechatLoginError, "redirect_origin_forbidden"):
            manager.wait({"session_key": started.public["session_key"]}, now_ms=2_000)

    def test_new_qr_supersedes_old_session_without_polling_it(self) -> None:
        transport = _Transport(
            [
                {"qrcode": "old", "qrcode_url": "old-qr"},
                {"qrcode": "new", "qrcode_url": "new-qr"},
            ]
        )
        manager = WechatLoginManager(transport)
        old = manager.start({"session_key": "old"}, now_ms=1_000)
        manager.start({"session_key": "new"}, now_ms=2_000)
        result = manager.wait({"session_key": old.public["session_key"]}, now_ms=3_000)
        self.assertFalse(result.public["ok"])
        self.assertEqual(result.public["error"], "login_session_missing")
        self.assertEqual(len(transport.calls), 2)

    def test_concurrent_wait_is_single_flight(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class BlockingTransport:
            def __init__(self):
                self.calls = []

            def request_json(self, method, origin, path, *, body, bot_token, timeout_seconds):
                self.calls.append((method, path))
                if method == "POST":
                    return {"qrcode": "qr", "qrcode_url": "qr-content"}
                entered.set()
                release.wait(2)
                return {
                    "status": "confirmed",
                    "ilink_bot_id": "bot",
                    "ilink_user_id": "user",
                    "bot_token": "secret",
                }

        transport = BlockingTransport()
        manager = WechatLoginManager(transport)
        started = manager.start({}, now_ms=1_000)
        outcomes = []
        worker = threading.Thread(
            target=lambda: outcomes.append(manager.wait({"session_key": started.public["session_key"]}, now_ms=2_000)),
            daemon=True,
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        duplicate = manager.wait({"session_key": started.public["session_key"]}, now_ms=2_001)
        self.assertEqual(duplicate.public["status"], "login_check_in_progress")
        release.set()
        worker.join(2)
        self.assertEqual(len([call for call in transport.calls if call[0] == "GET"]), 1)
        self.assertTrue(outcomes[0].public["connected"])

    def test_oversized_or_malformed_platform_status_is_rejected(self) -> None:
        transport = _Transport(
            [
                {"qrcode": "qr", "qrcode_url": "qr-content"},
                {"status": "x" * 65},
            ]
        )
        manager = WechatLoginManager(transport)
        started = manager.start({}, now_ms=1_000)
        with self.assertRaisesRegex(WechatLoginError, "status_invalid"):
            manager.wait({"session_key": started.public["session_key"]}, now_ms=2_000)


if __name__ == "__main__":
    unittest.main()
