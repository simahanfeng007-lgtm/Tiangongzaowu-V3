"""P2-30 regression: communication failures must not all report 409."""

from __future__ import annotations

import unittest

from communication_service.embedded_runtime import EmbeddedCommunicationService


class _CodeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _FakeRuntime:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def wechat_login_start(self, payload, now_ms):
        if self._error is not None:
            raise self._error
        return {"ok": True}


class CommunicationErrorStatusTests(unittest.TestCase):
    def _request(self, error: Exception) -> tuple[int, dict]:
        service = EmbeddedCommunicationService(_FakeRuntime(error))
        status, payload, _content_type = service.request(
            "POST",
            "/api/v1/internal/control/wechat/login/start",
            {"payload": {}},
        )
        return status, payload

    def test_input_error_is_400(self) -> None:
        status, payload = self._request(_CodeError("communication.input.wechat_missing_parameter"))
        self.assertEqual(status, 400)
        self.assertEqual(payload["reason_code"], "communication.input.wechat_missing_parameter")

    def test_conflict_stays_409(self) -> None:
        status, payload = self._request(_CodeError("communication.conflict.lease_exists"))
        self.assertEqual(status, 409)
        self.assertEqual(payload["reason_code"], "communication.conflict.lease_exists")

    def test_unavailable_is_503(self) -> None:
        status, payload = self._request(_CodeError("communication.unavailable.wechat_dependency"))
        self.assertEqual(status, 503)
        self.assertEqual(payload["reason_code"], "communication.unavailable.wechat_dependency")

    def test_internal_failure_is_500_not_409(self) -> None:
        status, payload = self._request(RuntimeError("boom"))
        self.assertEqual(status, 500)
        self.assertEqual(payload["reason_code"], "communication.embedded.failed")


if __name__ == "__main__":
    unittest.main()
