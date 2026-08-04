"""Short-lived, control-plane-only WeChat iLink QR login sessions."""

from __future__ import annotations

import base64
import http.client
import json
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlencode, urlsplit

from contracts import canonical_json_bytes


WECHAT_ILINK_ORIGIN = "https://ilinkai.weixin.qq.com"
WECHAT_ILINK_APP_ID = "bot"
WECHAT_ILINK_CLIENT_VERSION = "132102"
WECHAT_LOGIN_TTL_MS = 300_000
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_QR_TEXT_BYTES = 262_144


class WechatLoginError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WechatLoginError("wechat.login.response_duplicate_key")
        result[key] = value
    return result


def _normalize_origin(value: object) -> str:
    text = str(value or "").strip()
    if text and "://" not in text:
        text = "https://" + text
    parsed = urlsplit(text)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (host != "ilinkai.weixin.qq.com" and not host.endswith(".weixin.qq.com"))
    ):
        raise WechatLoginError("wechat.login.redirect_origin_forbidden")
    return f"https://{host}"


class WechatLoginTransport(Protocol):
    def request_json(
        self,
        method: str,
        origin: str,
        path: str,
        *,
        body: Mapping[str, Any] | None,
        bot_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]: ...


class HttpWechatLoginTransport:
    """HTTPS-only iLink login transport with a narrow redirect allowlist."""

    def request_json(
        self,
        method: str,
        origin: str,
        path: str,
        *,
        body: Mapping[str, Any] | None,
        bot_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        normalized = _normalize_origin(origin)
        if method not in {"GET", "POST"} or not path.startswith("/ilink/bot/") or "#" in path:
            raise WechatLoginError("wechat.login.request_forbidden")
        if not 1 <= timeout_seconds <= 30:
            raise WechatLoginError("wechat.login.timeout_invalid")
        host = urlsplit(normalized).hostname
        assert host is not None
        wire = None if body is None else canonical_json_bytes(dict(body))
        headers = {
            "Accept": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "iLink-App-Id": WECHAT_ILINK_APP_ID,
            "iLink-App-ClientVersion": WECHAT_ILINK_CLIENT_VERSION,
            "X-WECHAT-UIN": base64.b64encode(
                str(secrets.randbits(32)).encode("ascii")
            ).decode("ascii"),
        }
        if wire is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(wire))
        if bot_token:
            headers["Authorization"] = f"Bearer {bot_token}"
        connection = http.client.HTTPSConnection(host, 443, timeout=timeout_seconds)
        response = None
        try:
            connection.request(method, path, body=wire, headers=headers)
            response = connection.getresponse()
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise WechatLoginError("wechat.login.response_too_large")
            if response.status < 200 or response.status >= 300:
                raise WechatLoginError("wechat.login.platform_http_rejected")
            try:
                value = json.loads(
                    raw.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            except WechatLoginError:
                raise
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise WechatLoginError("wechat.login.response_json_invalid") from exc
            if not isinstance(value, dict):
                raise WechatLoginError("wechat.login.response_shape_invalid")
            ret = value.get("ret")
            if isinstance(ret, bool) or ret not in {None, 0, "0"}:
                raise WechatLoginError("wechat.login.platform_rejected")
            return value
        except WechatLoginError:
            raise
        except Exception as exc:
            raise WechatLoginError("wechat.login.transport_failed") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()


@dataclass(frozen=True)
class WechatLoginOutcome:
    public: dict[str, object]
    credentials: dict[str, str] | None = None


@dataclass
class _LoginSession:
    session_key: str
    qrcode: str
    qrcode_url: str
    origin: str
    bot_type: str
    started_at_ms: int
    polling: bool = False


class WechatLoginManager:
    def __init__(self, transport: WechatLoginTransport | None = None) -> None:
        self._transport = transport or HttpWechatLoginTransport()
        self._lock = threading.RLock()
        self._sessions: dict[str, _LoginSession] = {}
        self._status: dict[str, object] = {"state": "missing_credentials"}

    def _clean_expired(self, now_ms: int) -> None:
        expired = [
            key
            for key, item in self._sessions.items()
            if now_ms - item.started_at_ms >= WECHAT_LOGIN_TTL_MS
        ]
        for key in expired:
            self._sessions.pop(key, None)

    def snapshot(self, *, now_ms: int) -> dict[str, object]:
        with self._lock:
            self._clean_expired(now_ms)
            if not self._sessions and self._status.get("state") in {
                "waiting_login",
                "waiting_confirm",
                "need_verifycode",
            }:
                self._status = {"state": "login_expired"}
            return dict(self._status)

    def mark_configured(self, account_id: str, *, running: bool) -> None:
        with self._lock:
            self._sessions.clear()
            self._status = {
                "state": "starting" if running else "available",
                "account_id": account_id,
            }

    def start(
        self,
        payload: Mapping[str, object],
        *,
        now_ms: int,
        local_tokens: tuple[str, ...] = (),
    ) -> WechatLoginOutcome:
        allowed = {"session_key", "bot_type"}
        if any(key not in allowed for key in payload):
            raise WechatLoginError("wechat.login.start_fields_invalid")
        bot_type = str(payload.get("bot_type") or "3").strip()
        if not bot_type.isdecimal() or len(bot_type) > 8:
            raise WechatLoginError("wechat.login.bot_type_invalid")
        session_key = str(payload.get("session_key") or uuid.uuid4()).strip()
        if not session_key or len(session_key) > 128 or any(
            not (char.isalnum() or char in "_-") for char in session_key
        ):
            raise WechatLoginError("wechat.login.session_key_invalid")
        safe_local_tokens = [
            str(token) for token in local_tokens
            if isinstance(token, str) and 0 < len(token.encode("utf-8")) <= 8_192
        ][-10:]
        response = self._transport.request_json(
            "POST",
            WECHAT_ILINK_ORIGIN,
            "/ilink/bot/get_bot_qrcode?bot_type=" + quote(bot_type, safe=""),
            body={"local_token_list": safe_local_tokens},
            bot_token="",
            timeout_seconds=20,
        )
        qrcode = str(response.get("qrcode") or "").strip()
        qrcode_url = str(
            response.get("qrcode_img_content") or response.get("qrcode_url") or ""
        ).strip()
        if not qrcode:
            raise WechatLoginError("wechat.login.qrcode_missing")
        if not qrcode_url:
            qrcode_url = qrcode
        if len(qrcode.encode("utf-8")) > 16_384 or len(qrcode_url.encode("utf-8")) > _MAX_QR_TEXT_BYTES:
            raise WechatLoginError("wechat.login.qrcode_too_large")
        session = _LoginSession(
            session_key=session_key,
            qrcode=qrcode,
            qrcode_url=qrcode_url,
            origin=WECHAT_ILINK_ORIGIN,
            bot_type=bot_type,
            started_at_ms=now_ms,
        )
        with self._lock:
            self._clean_expired(now_ms)
            # A newly generated QR code supersedes every older pending login.
            # Otherwise an old scan can confirm later and overwrite the account
            # chosen by the latest UI flow.
            self._sessions.clear()
            self._sessions[session_key] = session
            self._status = {
                "state": "waiting_login",
                "session_key": session_key,
                "qrcode_url": qrcode_url,
            }
        return WechatLoginOutcome(
            {
                "ok": True,
                "session_key": session_key,
                "qrcode_url": qrcode_url,
                "message": "二维码已生成，请用手机微信扫描。",
            }
        )

    def wait(
        self,
        payload: Mapping[str, object],
        *,
        now_ms: int,
        existing_credentials: Mapping[str, str] | None = None,
    ) -> WechatLoginOutcome:
        allowed = {"session_key", "verify_code"}
        if any(key not in allowed for key in payload):
            raise WechatLoginError("wechat.login.wait_fields_invalid")
        requested = str(payload.get("session_key") or "").strip()
        verify_code = str(payload.get("verify_code") or "").strip()
        if len(verify_code) > 32 or any(ord(char) < 32 for char in verify_code):
            raise WechatLoginError("wechat.login.verify_code_invalid")
        with self._lock:
            self._clean_expired(now_ms)
            session_key = requested or (next(reversed(self._sessions)) if self._sessions else "")
            session = self._sessions.get(session_key)
            if session is not None and session.polling:
                return WechatLoginOutcome(
                    {
                        "ok": True,
                        "connected": False,
                        "status": "login_check_in_progress",
                        "session_key": session_key,
                        "qrcode_url": session.qrcode_url,
                        "message": "正在确认登录状态，请稍候。",
                    }
                )
            if session is not None:
                session.polling = True
        if session is None:
            with self._lock:
                self._status = {"state": "login_expired" if requested else "missing_credentials"}
            return WechatLoginOutcome(
                {
                    "ok": False,
                    "connected": False,
                    "error": "login_session_missing",
                    "message": "登录二维码已失效，请重新生成。",
                }
            )

        def superseded() -> WechatLoginOutcome:
            return WechatLoginOutcome(
                {
                    "ok": False,
                    "connected": False,
                    "error": "login_session_superseded",
                    "message": "已有新的登录二维码，本次旧登录结果已丢弃。",
                }
            )

        try:
            query = {"qrcode": session.qrcode}
            if verify_code:
                query["verify_code"] = verify_code
            response = self._transport.request_json(
                "GET",
                session.origin,
                "/ilink/bot/get_qrcode_status?" + urlencode(query),
                body=None,
                bot_token="",
                timeout_seconds=20,
            )
            status = str(response.get("status") or "wait").strip().lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,64}", status):
                raise WechatLoginError("wechat.login.status_invalid")

            new_origin = None
            if status == "scaned_but_redirect":
                new_origin = _normalize_origin(response.get("redirect_host"))
                state = "waiting_confirm"
                message = "二维码已扫描，请在手机微信确认。"
            elif status in {"wait", "waiting", "scanned"}:
                state = "waiting_confirm" if status == "scanned" else "waiting_login"
                message = "仍在等待扫码确认。"
            elif status == "need_verifycode":
                state = "need_verifycode"
                message = "手机微信要求配对数字，请填写后再次确认。"
            elif status in {"expired", "qrcode_expired"}:
                with self._lock:
                    if self._sessions.get(session_key) is not session:
                        return superseded()
                    self._sessions.pop(session_key, None)
                    self._status = {"state": "login_expired"}
                return WechatLoginOutcome(
                    {
                        "ok": False,
                        "connected": False,
                        "status": status,
                        "error": "login_expired",
                        "message": "二维码已过期，请重新生成。",
                    }
                )
            elif status == "binded_redirect" and existing_credentials is not None:
                credentials = dict(existing_credentials)
                with self._lock:
                    if self._sessions.get(session_key) is not session:
                        return superseded()
                    self._sessions.pop(session_key, None)
                    self._status = {"state": "starting", "account_id": credentials.get("account_id", "")}
                return WechatLoginOutcome(
                    {
                        "ok": True,
                        "connected": True,
                        "account_id": credentials.get("account_id", ""),
                        "message": "此微信 Bot 已连接，正在恢复连接。",
                    },
                    credentials,
                )
            elif status == "confirmed":
                account_id = str(response.get("ilink_bot_id") or "").strip()
                credentials = {
                    "account_id": account_id,
                    "bot_token": str(response.get("bot_token") or "").strip(),
                    "cursor": str(response.get("get_updates_buf") or "").strip(),
                    "user_id": str(response.get("ilink_user_id") or account_id).strip(),
                }
                limits = {"account_id": 256, "bot_token": 8_192, "cursor": 524_288, "user_id": 256}
                if (
                    not credentials["account_id"]
                    or not credentials["bot_token"]
                    or not credentials["user_id"]
                    or any(len(value.encode("utf-8")) > limits[key] for key, value in credentials.items())
                    or any(any(ord(char) < 32 for char in value) for value in credentials.values())
                ):
                    raise WechatLoginError("wechat.login.credentials_incomplete")
                with self._lock:
                    if self._sessions.get(session_key) is not session:
                        return superseded()
                    self._sessions.pop(session_key, None)
                    self._status = {"state": "starting", "account_id": credentials["account_id"]}
                return WechatLoginOutcome(
                    {
                        "ok": True,
                        "connected": True,
                        "account_id": credentials["account_id"],
                        "message": "微信已连接，正在启动消息通道。",
                    },
                    credentials,
                )
            else:
                state = "waiting_confirm"
                message = f"微信返回状态：{status}"

            with self._lock:
                if self._sessions.get(session_key) is not session:
                    return superseded()
                if new_origin is not None:
                    session.origin = new_origin
                self._status = {
                    "state": state,
                    "session_key": session_key,
                    "qrcode_url": session.qrcode_url,
                    "raw_status": status,
                }
            return WechatLoginOutcome(
                {
                    "ok": True,
                    "connected": False,
                    "status": status,
                    "session_key": session_key,
                    "qrcode_url": session.qrcode_url,
                    "need_verify_code": status == "need_verifycode",
                    "message": message,
                }
            )
        finally:
            with self._lock:
                current = self._sessions.get(session_key)
                if current is session:
                    current.polling = False


__all__ = [
    "HttpWechatLoginTransport",
    "WechatLoginError",
    "WechatLoginManager",
    "WechatLoginOutcome",
    "WechatLoginTransport",
    "WECHAT_LOGIN_TTL_MS",
]
