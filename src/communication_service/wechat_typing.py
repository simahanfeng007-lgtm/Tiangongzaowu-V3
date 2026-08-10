"""WeChat iLink typing-indicator feedback for the active channel runtime."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from contracts import canonical_json_bytes


_HOST = "ilinkai.weixin.qq.com"
_APP_ID = "bot"
_CHANNEL_VERSION = "2.4.6"
_CLIENT_VERSION = "132102"
_GET_CONFIG_PATH = "/ilink/bot/getconfig"
_SEND_TYPING_PATH = "/ilink/bot/sendtyping"
_MAX_RESPONSE_BYTES = 1_048_576
_TICKET_TTL_SECONDS = 20 * 60 * 60


def _base_info() -> dict[str, str]:
    return {
        "channel_version": _CHANNEL_VERSION,
        "bot_agent": "TiangongZaowu/3.0.0",
    }


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WechatTypingError("wechat.typing.response_duplicate_key")
        result[key] = value
    return result


class WechatTypingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class WechatTypingTransport:
    """HTTPS-only iLink transport for getconfig/sendtyping."""

    def __init__(self, *, max_response_bytes: int = _MAX_RESPONSE_BYTES) -> None:
        if not 1_024 <= max_response_bytes <= 8_388_608:
            raise ValueError("WeChat typing response limit is invalid")
        self._max_response_bytes = max_response_bytes

    def _post_json(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        bot_token: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if path not in {_GET_CONFIG_PATH, _SEND_TYPING_PATH}:
            raise WechatTypingError("wechat.typing.path_forbidden")
        token = str(bot_token or "").strip()
        if (
            not token
            or token != bot_token
            or "\x00" in token
            or len(token.encode("utf-8")) > 8_192
            or not 1 <= timeout_seconds <= 60
        ):
            raise WechatTypingError("wechat.typing.credentials_or_timeout.invalid")
        wire = canonical_json_bytes(dict(body))
        uin = base64.b64encode(str(secrets.randbits(32)).encode("ascii")).decode("ascii")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "AuthorizationType": "ilink_bot_token",
            "Content-Type": "application/json",
            "Content-Length": str(len(wire)),
            "iLink-App-Id": _APP_ID,
            "iLink-App-ClientVersion": _CLIENT_VERSION,
            "X-WECHAT-UIN": uin,
        }
        connection = http.client.HTTPSConnection(_HOST, 443, timeout=timeout_seconds)
        response = None
        try:
            connection.request("POST", path, body=wire, headers=headers)
            response = connection.getresponse()
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise WechatTypingError("wechat.typing.response_too_large")
            if response.status < 200 or response.status >= 300:
                raise WechatTypingError("wechat.typing.http_rejected")
            try:
                value = json.loads(
                    raw.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
            except WechatTypingError:
                raise
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                raise WechatTypingError("wechat.typing.response_json_invalid") from exc
            if not isinstance(value, dict):
                raise WechatTypingError("wechat.typing.response_shape_invalid")
            ret = value.get("ret")
            if isinstance(ret, bool) or ret not in {None, 0, "0"}:
                raise WechatTypingError(
                    "wechat.typing.platform_rejected:"
                    + str(value.get("errmsg") or value.get("ret") or "unknown")[:120]
                )
            return value
        except WechatTypingError:
            raise
        except Exception as exc:
            raise WechatTypingError("wechat.typing.transport_failed") from exc
        finally:
            if response is not None:
                response.close()
            connection.close()

    def get_config(
        self,
        *,
        bot_token: str,
        ilink_user_id: str,
        context_token: str = "",
    ) -> str:
        body: dict[str, Any] = {
            "ilink_user_id": ilink_user_id,
            "base_info": _base_info(),
        }
        if context_token:
            body["context_token"] = context_token
        response = self._post_json(
            _GET_CONFIG_PATH,
            body,
            bot_token=bot_token,
            timeout_seconds=10,
        )
        ticket = str(response.get("typing_ticket") or "").strip()
        if not ticket and isinstance(response.get("data"), dict):
            ticket = str(response["data"].get("typing_ticket") or "").strip()
        if not ticket:
            raise WechatTypingError("wechat.typing.ticket_missing")
        return ticket

    def send_typing(
        self,
        *,
        bot_token: str,
        ilink_user_id: str,
        to_user_id: str,
        typing_ticket: str,
        status: int,
    ) -> None:
        command = 1 if int(status) == 1 else 2
        self._post_json(
            _SEND_TYPING_PATH,
            {
                "ilink_user_id": ilink_user_id,
                "to_user_id": to_user_id or ilink_user_id,
                "typing_ticket": typing_ticket,
                "command": command,
                "status": command,
                "base_info": _base_info(),
            },
            bot_token=bot_token,
            timeout_seconds=10,
        )


@dataclass
class _TypingSession:
    stop_event: threading.Event
    thread: threading.Thread
    bot_token: str
    ilink_user_id: str
    to_user_id: str
    typing_ticket: str


class WechatTypingManager:
    """Tracks one typing session per WeChat conversation while the model works."""

    def __init__(
        self,
        transport: WechatTypingTransport | None = None,
        *,
        refresh_seconds: float = 5.0,
        max_seconds: float = 120.0,
    ) -> None:
        self._transport = transport or WechatTypingTransport()
        self._refresh_seconds = max(3.0, float(refresh_seconds))
        self._max_seconds = max(30.0, float(max_seconds))
        self._sessions: dict[str, _TypingSession] = {}
        self._tickets: dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _ticket_key(bot_token: str, ilink_user_id: str) -> str:
        token_digest = hashlib.sha1(bot_token.encode("utf-8")).hexdigest()[:12]
        return f"{token_digest}|{ilink_user_id}"

    def _typing_ticket(
        self,
        *,
        bot_token: str,
        ilink_user_id: str,
        context_token: str,
    ) -> str:
        key = self._ticket_key(bot_token, ilink_user_id)
        now = time.time()
        with self._lock:
            cached = self._tickets.get(key)
            if cached is not None and cached[1] > now + 60:
                return cached[0]
        ticket = self._transport.get_config(
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            context_token=context_token,
        )
        with self._lock:
            self._tickets[key] = (ticket, now + _TICKET_TTL_SECONDS)
        return ticket

    def start(
        self,
        *,
        bot_token: str,
        ilink_user_id: str,
        session_key: str,
        context_token: str = "",
        to_user_id: str | None = None,
    ) -> dict[str, object]:
        if not session_key or not ilink_user_id or not bot_token:
            return {"ok": False, "error": "wechat.typing.arguments.invalid"}
        with self._lock:
            existing = self._sessions.get(session_key)
            if existing is not None and not existing.stop_event.is_set():
                return {"ok": True, "already_running": True}
        ticket = self._typing_ticket(
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            context_token=context_token,
        )
        resolved_to = to_user_id or ilink_user_id
        self._transport.send_typing(
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            to_user_id=resolved_to,
            typing_ticket=ticket,
            status=1,
        )
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._refresh_loop,
            args=(session_key, stop_event, bot_token, ilink_user_id, resolved_to, ticket),
            name=f"wechat-typing-{session_key[:8]}",
            daemon=True,
        )
        session = _TypingSession(
            stop_event=stop_event,
            thread=thread,
            bot_token=bot_token,
            ilink_user_id=ilink_user_id,
            to_user_id=resolved_to,
            typing_ticket=ticket,
        )
        with self._lock:
            self._sessions[session_key] = session
        thread.start()
        return {"ok": True}

    def _refresh_loop(
        self,
        session_key: str,
        stop_event: threading.Event,
        bot_token: str,
        ilink_user_id: str,
        to_user_id: str,
        typing_ticket: str,
    ) -> None:
        deadline = time.time() + self._max_seconds
        try:
            while not stop_event.wait(self._refresh_seconds):
                if time.time() >= deadline:
                    break
                try:
                    self._transport.send_typing(
                        bot_token=bot_token,
                        ilink_user_id=ilink_user_id,
                        to_user_id=to_user_id,
                        typing_ticket=typing_ticket,
                        status=1,
                    )
                except Exception:
                    break
        finally:
            with self._lock:
                current = self._sessions.get(session_key)
                if current is not None and current.stop_event is stop_event:
                    self._sessions.pop(session_key, None)

    def stop(self, session_key: str) -> dict[str, object]:
        with self._lock:
            session = self._sessions.pop(session_key, None)
        if session is None:
            return {"ok": False, "error": "wechat.typing.not_running"}
        session.stop_event.set()
        try:
            self._transport.send_typing(
                bot_token=session.bot_token,
                ilink_user_id=session.ilink_user_id,
                to_user_id=session.to_user_id,
                typing_ticket=session.typing_ticket,
                status=2,
            )
        except Exception:
            pass
        return {"ok": True}

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._tickets.clear()
        for session in sessions:
            session.stop_event.set()
            try:
                self._transport.send_typing(
                    bot_token=session.bot_token,
                    ilink_user_id=session.ilink_user_id,
                    to_user_id=session.to_user_id,
                    typing_ticket=session.typing_ticket,
                    status=2,
                )
            except Exception:
                pass


__all__ = [
    "WechatTypingError",
    "WechatTypingManager",
    "WechatTypingTransport",
]
