from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import random
import re
import shutil
import struct
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .json_guards import error_payload, loads_json_object
from .reply_sanitizer import strip_internal_reply_markers
from urllib.parse import parse_qs, quote, urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

import httpx


CONFIG_PATH = Path.home() / ".tiangong" / "v3" / "gateway_links.json"
WECHAT_INBOX_ROOT = Path.home() / ".tiangong" / "v3" / "wechat_inbox"
WECHAT_ATTACHMENT_INDEX_PATH = Path.home() / ".tiangong" / "v3" / "wechat_latest_attachments.json"
WECHAT_CONTEXT_TOKEN_PATH = Path.home() / ".tiangong" / "v3" / "wechat_context_tokens.json"
WECHAT_ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WECHAT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
WECHAT_ILINK_APP_ID = "bot"
WECHAT_ILINK_CHANNEL_VERSION = "2.4.6"
WECHAT_ILINK_CLIENT_VERSION = (2 << 16) | (4 << 8) | 6
WECHAT_LOGIN_TTL_SECONDS = 5 * 60
WECHAT_MAX_ATTACHMENT_BYTES = 128 * 1024 * 1024
WECHAT_LOCAL_ATTACHMENT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
WECHAT_ATTACHMENT_INDEX_MAX_KEYS = 200
WECHAT_ATTACHMENT_INDEX_KEEP_KEYS = 120
WECHAT_ATTACHMENT_CONTEXT_LIMIT = 32
WECHAT_CONTENT_DEDUP_WINDOW_SECONDS = 60
WECHAT_CONTENT_DEDUP_TTL_SECONDS = 5 * 60
WECHAT_SESSION_EXPIRED_ERRCODE = -14
WECHAT_RATE_LIMIT_ERRCODE = -2
WECHAT_SEND_RATE_LIMIT_RETRIES = 2
WECHAT_SEND_RATE_LIMIT_DELAY_SECONDS = 2.0
WECHAT_UPLOAD_TIMEOUT_SECONDS = 120.0
GATEWAY_PENDING_TEXT_MERGE_LIMIT = 4000

_WECHAT_CDN_ALLOWLIST = {
    "novac2c.cdn.weixin.qq.com",
    "ilinkai.weixin.qq.com",
    "wx.qlogo.cn",
    "thirdwx.qlogo.cn",
    "res.wx.qq.com",
    "mmbiz.qpic.cn",
    "mmbiz.qlogo.cn",
}

_WECHAT_FILE_PLACEHOLDER_RE = re.compile(r"(?:^|\n)\s*\[文件\]\s*([^\r\n]+)")

WECHAT_MEDIA_IMAGE = 1
WECHAT_MEDIA_VIDEO = 2
WECHAT_MEDIA_FILE = 3
WECHAT_MEDIA_VOICE = 4

WECHAT_ITEM_TEXT = 1
WECHAT_ITEM_IMAGE = 2
WECHAT_ITEM_VOICE = 3
WECHAT_ITEM_FILE = 4
WECHAT_ITEM_VIDEO = 5

WECHAT_MSG_TYPE_BOT = 2
WECHAT_MSG_STATE_FINISH = 2

MEDIA_DELIVERY_EXTS: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg",
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp",
    ".mp3", ".wav", ".ogg", ".opus", ".m4a", ".flac", ".silk",
    ".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".md", ".epub",
    ".xlsx", ".xls", ".ods", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".pptx", ".ppt", ".odp", ".key",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".apk", ".ipa",
    ".html", ".htm",
)

_MEDIA_EXT_ALTERNATION = "|".join(
    sorted((ext.lstrip(".") for ext in MEDIA_DELIVERY_EXTS), key=len, reverse=True)
)

_WECHAT_MEDIA_TAG_RE = re.compile(
    r'''[`"']?MEDIA:\s*'''
    r'''(?P<path>`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|'''
    r'''(?:~/|/|[A-Za-z]:[/\\])\S+(?:[^\S\n]+\S+)*?\.(?:''' + _MEDIA_EXT_ALTERNATION + r'''))'''
    r'''(?=[\s`"',;:)\]}，。；、！？）】》]|$)[`"']?''',
    re.IGNORECASE,
)

_WECHAT_LOCAL_FILE_RE = re.compile(
    r'''(?<![/:\w.])(?P<path>(?:~/|/|[A-Za-z]:[/\\])[^`"'<>\r\n|]*?\.(?:'''
    + _MEDIA_EXT_ALTERNATION
    + r'''))(?=[\s`"',;:)\]}，。；、！？）】》]|$)''',
    re.IGNORECASE,
)


DEFAULT_SETTINGS: dict[str, Any] = {
    "wechat": {
        "enabled": False,
        "mode": "direct_bot",
        "direct": {
            "enabled": False,
            "base_url": WECHAT_ILINK_BASE_URL,
            "cdn_base_url": WECHAT_CDN_BASE_URL,
            "bot_type": "3",
            "bot_token": "",
            "account_id": "",
            "user_id": "",
            "get_updates_buf": "",
            "long_poll_timeout_ms": 35000,
            "max_attachment_bytes": WECHAT_MAX_ATTACHMENT_BYTES,
            "local_attachment_fallback": True,
            "local_attachment_max_age_seconds": WECHAT_LOCAL_ATTACHMENT_MAX_AGE_SECONDS,
            "auto_reply": True,
            "typing_indicator": True,
            "typing_refresh_seconds": 5,
            "progress_events": True,
            "progress_initial_delay_seconds": 8,
            "progress_interval_seconds": 45,
            "progress_min_interval_seconds": 18,
            "status_probe_min_interval_seconds": 8,
        },
        "callback": {
            "enabled": False,
            "provider": "official_account",
            "host": "127.0.0.1",
            "port": 7188,
            "path": "/wechat/callback",
            "token": "",
            "encoding_aes_key": "",
            "receive_id": "",
            "auto_reply": True,
            "sync_reply": True,
        },
    },
    "feishu": {
        "enabled": False,
        "mode": "long_connection",
        "app_id": "",
        "app_secret": "",
        "verification_token": "",
        "encrypt_key": "",
    },
}


SECRET_KEYS = {
    "app_secret",
    "verification_token",
    "encrypt_key",
    "token",
    "encoding_aes_key",
    "receive_id",
    "bot_token",
}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif isinstance(value, str) and value and set(value) == {"*"}:
            continue
        else:
            merged[key] = value
    return merged


def _normalize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(settings if isinstance(settings, dict) else {})

    wechat = normalized.setdefault("wechat", {})
    if not isinstance(wechat, dict):
        wechat = deepcopy(DEFAULT_SETTINGS["wechat"])
        normalized["wechat"] = wechat
    if wechat.get("mode") == "openclaw_weixin":
        wechat["mode"] = "direct_bot"
    wechat.pop("openclaw", None)

    direct = wechat.get("direct") if isinstance(wechat.get("direct"), dict) else {}
    wechat["direct"] = _deep_merge(DEFAULT_SETTINGS["wechat"]["direct"], direct)
    callback = wechat.get("callback") if isinstance(wechat.get("callback"), dict) else {}
    wechat["callback"] = _deep_merge(DEFAULT_SETTINGS["wechat"]["callback"], callback)
    if wechat.get("mode") not in {"direct_bot", "callback"}:
        wechat["mode"] = "direct_bot"

    feishu = normalized.get("feishu") if isinstance(normalized.get("feishu"), dict) else {}
    normalized["feishu"] = _deep_merge(DEFAULT_SETTINGS["feishu"], feishu)
    return normalized


def _mask(value: Any) -> str:
    text = str(value or "")
    return "********" if text else ""


def _public_copy(data: dict[str, Any]) -> dict[str, Any]:
    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if key in SECRET_KEYS:
            return _mask(value)
        return value

    return walk(data)


def load_link_settings() -> dict[str, Any]:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")) if CONFIG_PATH.exists() else {}
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    return _normalize_settings(_deep_merge(DEFAULT_SETTINGS, raw))


def save_link_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_link_settings()
    next_settings = _normalize_settings(_deep_merge(current, payload if isinstance(payload, dict) else {}))
    from .settings_persistence import atomic_write_json

    # 含密钥：原子写但绝不落 .bak 副本，避免密钥扩散。
    atomic_write_json(CONFIG_PATH, next_settings, backup=False)
    return next_settings


def typed_error_system_status(
    *,
    code: str,
    diagnostic: str,
    source_component: str = "backend.v3",
) -> dict[str, Any]:
    """G3 typed system_status card for fixed backend error text.

    Fixed fallback sentences must never become an assistant message; the
    Gateway projects this card as ``system_status`` with ``origin=system``.
    """
    return {
        "schema": "tiangong.v3.typed_system_status.v1",
        "origin": "system",
        "code": str(code or "internal_failure"),
        "diagnostic": str(diagnostic or "")[:4000],
        "source_component": str(source_component),
        "assistant_message": None,
    }


def _sha1_sorted(*parts: Any) -> str:
    return hashlib.sha1("".join(sorted(str(part or "") for part in parts)).encode("utf-8")).hexdigest()


def _xml_to_dict(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    return {child.tag: (child.text or "") for child in root}


def _decode_wechat_aes_key(encoding_aes_key: str) -> bytes:
    key_text = str(encoding_aes_key or "").strip()
    if len(key_text) != 43:
        raise ValueError("invalid_encoding_aes_key")
    return base64.b64decode(key_text + "=")


def _decrypt_wechat_xml(encrypted: str, encoding_aes_key: str) -> tuple[str, str]:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = _decode_wechat_aes_key(encoding_aes_key)
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    decryptor = cipher.decryptor()
    plain = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    pad = plain[-1]
    if pad < 1 or pad > 32:
        raise ValueError("invalid_wechat_padding")
    plain = plain[:-pad]
    xml_len = struct.unpack("!I", plain[16:20])[0]
    xml_text = plain[20:20 + xml_len].decode("utf-8")
    receive_id = plain[20 + xml_len:].decode("utf-8", errors="ignore")
    return xml_text, receive_id


def _extract_wechat_text(message: dict[str, str]) -> str:
    msg_type = (message.get("MsgType") or "").lower()
    if msg_type == "text":
        return message.get("Content", "")
    if msg_type == "link":
        return "\n".join(part for part in [message.get("Title"), message.get("Description"), message.get("Url")] if part)
    if msg_type == "location":
        return message.get("Label") or f"{message.get('Location_X', '')},{message.get('Location_Y', '')}".strip(",")
    if msg_type == "event":
        return f"[WeChat event] {message.get('Event', '')}".strip()
    return f"[WeChat {msg_type or 'message'}] {message.get('MediaId') or message.get('PicUrl') or ''}".strip()


def _wechat_passive_text_xml(to_user: str, from_user: str, content: str) -> str:
    safe_to = xml_escape(str(to_user or ""))
    safe_from = xml_escape(str(from_user or ""))
    safe_content = xml_escape(str(content or ""))
    return (
        "<xml>"
        f"<ToUserName>{safe_to}</ToUserName>"
        f"<FromUserName>{safe_from}</FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType>text</MsgType>"
        f"<Content>{safe_content}</Content>"
        "</xml>"
    )


def _reply_text_from_result(result: dict[str, Any]) -> str:
    if isinstance(result.get("data"), dict):
        return strip_internal_reply_markers(result["data"].get("huifu") or "")
    return ""


def _low_information_wechat_interim(text: str, meta: dict[str, Any] | None = None) -> bool:
    clean = str(strip_internal_reply_markers(text) or "").strip()
    if not clean:
        return True
    info = meta if isinstance(meta, dict) else {}
    tool_name = str(info.get("tool_name") or "").strip().lower()
    source = str(info.get("source") or "").strip()
    compact = re.sub(r"\s+", "", clean).lower()
    compact = compact.strip("。！？!?.,，：:；;~～ ")
    if bool(info.get("fallback")) and tool_name == "omni_body" and _low_information_wechat_text(clean):
        return True
    if source == "model_reply_before_tool_call" and tool_name == "omni_body" and _low_information_wechat_text(clean):
        return True
    return False


def _low_information_wechat_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(strip_internal_reply_markers(text) or "")).lower()
    compact = compact.strip("。！？!?.,，：:；;~～ ")
    return compact in {
        "正在omnibody",
        "正在执行omni_body",
        "正在执行omnibody",
        "正在使用omnibody",
        "正在调用omnibody",
        "正在调用omnibody工具",
        "准备执行omnibody",
        "准备调用omnibody",
    }


def _compact_response(value: Any, limit: int = 800) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key in ("ret", "errcode", "errmsg", "message_id", "msgid", "client_id"):
            if key in value:
                compact[key] = value.get(key)
        if compact:
            return compact
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _extract_feishu_text(event_data: Any) -> tuple[str, dict[str, Any]]:
    event = getattr(event_data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    sender_id = getattr(sender, "sender_id", None)
    raw_content = getattr(message, "content", "") or ""
    message_type = getattr(message, "message_type", "") or ""
    text = raw_content
    try:
        parsed = json.loads(raw_content) if raw_content else {}
        if isinstance(parsed, dict):
            text = parsed.get("text") or parsed.get("content") or raw_content
    except Exception:
        pass
    metadata = {
        "message_id": getattr(message, "message_id", "") or "",
        "chat_id": getattr(message, "chat_id", "") or "",
        "thread_id": getattr(message, "thread_id", "") or "",
        "message_type": message_type,
        "sender_type": getattr(sender, "sender_type", "") or "",
        "tenant_key": getattr(sender, "tenant_key", "") or "",
        "open_id": getattr(sender_id, "open_id", "") or "",
        "user_id": getattr(sender_id, "user_id", "") or "",
        "union_id": getattr(sender_id, "union_id", "") or "",
    }
    return str(text or "").strip(), metadata


def _normalize_url(value: Any, fallback: str = WECHAT_ILINK_BASE_URL) -> str:
    text = str(value or "").strip() or fallback
    if text.startswith("//"):
        text = "https:" + text
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    return text.rstrip("/")


def _safe_path_segment(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    safe = "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in text)
    safe = safe.strip("._-")[:96]
    return safe or fallback


def _safe_filename(value: Any, fallback: str = "attachment.bin") -> str:
    name = Path(str(value or fallback).replace("\x00", "")).name.strip()
    name = "".join(ch if ch >= " " else "_" for ch in name)
    if not name or name in {".", ".."}:
        name = fallback
    if len(name) > 160:
        stem = Path(name).stem[:100] or "attachment"
        suffix = Path(name).suffix[:24]
        name = f"{stem}{suffix}"
    return name


@dataclass
class _GatewaySessionEvent:
    channel: str
    session_key: str
    user_name: str
    conversation_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    direct: dict[str, Any] = field(default_factory=dict)
    auto_reply: bool = True
    to_user_id: str = ""
    context_token: str = ""
    context_token_source: str = "missing"
    message_key: str = ""
    run_id: str = ""
    generation: int = 0
    received_at: float = field(default_factory=time.time)
    merged_count: int = 1


def _gateway_session_key(channel: str, conversation_id: str, user_name: str = "") -> str:
    raw = conversation_id or user_name or "default"
    return f"{channel}:{_safe_path_segment(raw)}"


def _merge_attachment_lists(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(left or []) + list(right or []):
        if not isinstance(item, dict):
            continue
        key = "\n".join(
            [
                str(item.get("path") or ""),
                str(item.get("source_path") or ""),
                str(item.get("name") or ""),
                str(item.get("status") or ""),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(deepcopy(item))
    return merged


def _merge_gateway_session_events(
    existing: _GatewaySessionEvent,
    incoming: _GatewaySessionEvent,
) -> _GatewaySessionEvent:
    text_parts: list[str] = []
    for value in (existing.text, incoming.text):
        clean = str(value or "").strip()
        if clean and clean not in text_parts:
            text_parts.append(clean)
    merged_text = "\n\n".join(text_parts)
    truncated = False
    if len(merged_text) > GATEWAY_PENDING_TEXT_MERGE_LIMIT:
        merged_text = merged_text[-GATEWAY_PENDING_TEXT_MERGE_LIMIT:]
        truncated = True

    metadata = deepcopy(incoming.metadata or {})
    metadata["merged_count"] = existing.merged_count + incoming.merged_count
    metadata["merged_previous_message_key"] = existing.message_key
    metadata["merged_latest_message_key"] = incoming.message_key
    if truncated:
        metadata["merged_text_truncated"] = True

    return _GatewaySessionEvent(
        channel=incoming.channel,
        session_key=incoming.session_key,
        user_name=incoming.user_name or existing.user_name,
        conversation_id=incoming.conversation_id or existing.conversation_id,
        text=merged_text,
        metadata=metadata,
        attachments=_merge_attachment_lists(existing.attachments, incoming.attachments),
        direct=deepcopy(incoming.direct or existing.direct),
        auto_reply=incoming.auto_reply,
        to_user_id=incoming.to_user_id or existing.to_user_id,
        context_token=incoming.context_token or existing.context_token,
        context_token_source=incoming.context_token_source or existing.context_token_source,
        message_key=incoming.message_key or existing.message_key,
        run_id=incoming.run_id or existing.run_id,
        generation=incoming.generation,
        received_at=existing.received_at,
        merged_count=existing.merged_count + incoming.merged_count,
    )


def _classify_gateway_pending_kind(text: str, has_attachments: bool = False) -> str:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not compact:
        return "new_task"
    interrupt_exact = {
        "停止", "停下", "别做了", "取消", "中断", "stop", "cancel",
        "停止吧", "停下吧", "取消吧", "中断吧", "不用做了", "别继续了",
    }
    if compact in interrupt_exact:
        return "interrupt"
    amend_markers = (
        "不要", "别出现", "删掉", "去掉", "改成", "换成", "压缩到", "控制在", "补充",
        "追加", "加上", "少一点", "多一点", "重新按", "按照", "改一下", "修一下",
    )
    if any(mark in compact for mark in amend_markers):
        return "amend"
    amend_exact = {
        "继续", "继续吧", "接着", "接着吧", "接着来", "接着做",
        "然后", "然后呢", "再", "再来", "继续处理",
    }
    if compact in amend_exact:
        return "amend"
    status_exact = {
        "进度", "进度呢", "什么情况", "怎么回事", "结果呢", "到哪了", "到哪一步了",
    }
    status_markers = (
        "完成了吗", "完成没", "好了没", "做完了吗", "做完没", "完事了吗", "完事没",
        "到哪了", "到哪一步", "还没好吗", "半天没有结果", "卡住了吗", "卡住了",
        "还在跑吗", "还在处理吗", "有结果了吗", "出结果了吗",
    )
    looks_like_status = compact in status_exact or any(mark in compact for mark in status_markers)
    if looks_like_status and (not has_attachments or len(compact) <= 24):
        return "status_probe"
    return "new_task"


def _gateway_step_is_low_information(step: dict[str, Any]) -> bool:
    title = str(step.get("title") or "").strip()
    summary = str(step.get("summary") or "").strip()
    compact = re.sub(r"\s+", "", summary).lower().strip("。！？!?.,，：:；;~～ ")
    return title == "模型阶段回复" and compact in {
        "正在omnibody",
        "正在执行omni_body",
        "正在执行omnibody",
    }


def _gateway_visible_run_steps(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in (run.get("steps") or [])
        if isinstance(step, dict) and not _gateway_step_is_low_information(step)
    ]


def _gateway_run_status_reply(run_status: dict[str, Any], active_request_id: str = "") -> str:
    run = run_status.get("run") if isinstance(run_status, dict) else None
    if not isinstance(run, dict):
        return "还在处理上一条任务，但暂时读不到详细进度。我会继续尝试完成；如果要中断，请回复“停止”。"
    phase = str(run.get("phase") or "running").strip() or "running"
    steps = _gateway_visible_run_steps(run)
    latest = steps[-1] if steps else {}
    done_count = sum(1 for step in steps if str(step.get("status") or "") == "done")
    title = str(latest.get("title") or "后台执行").strip()
    summary = str(latest.get("summary") or "").strip()
    if phase == "finished":
        if bool(run.get("ok", True)):
            reply = "上一条任务已经完成。"
        else:
            reply = "上一条任务已经结束，但没有正常完成。"
        if summary:
            reply += f"结果摘要：{summary[:120]}"
        elif title:
            reply += f"最后步骤：{title}"
        return reply
    reply = f"还在处理上一条任务（{phase}）。当前步骤：{title}"
    if summary:
        reply += f"：{summary[:120]}"
    if steps:
        reply += f"。已记录 {done_count}/{len(steps)} 个步骤完成"
    reply += "。需要中断请回复“停止”，要补充要求请直接说。"
    return reply


def _gateway_run_progress_reply(run_status: dict[str, Any], prefix: str = "正在处理") -> str:
    run = run_status.get("run") if isinstance(run_status, dict) else None
    if not isinstance(run, dict):
        return f"{prefix}。我会在关键步骤更新进度。"
    phase = str(run.get("phase") or "running").strip() or "running"
    steps = _gateway_visible_run_steps(run)
    if not steps:
        return f"{prefix}（{phase}）。我会在关键步骤更新进度。"
    latest = steps[-1]
    title = str(latest.get("title") or "后台执行").strip()
    summary = str(latest.get("summary") or "").strip()
    done_count = sum(1 for step in steps if str(step.get("status") or "") == "done")
    reply = f"{prefix}（{phase}）。当前步骤：{title}"
    if summary:
        reply += f"：{summary[:100]}"
    reply += f"。已完成 {done_count}/{len(steps)} 个记录步骤。"
    return reply


class _WechatDirectRunEventBridge:
    """微信外部是消息，内部按运行事件做节流投递。"""

    def __init__(self, manager: Any, event: _GatewaySessionEvent) -> None:
        self.manager = manager
        self.event = event
        self.enabled = bool(event.direct.get("progress_events", True))
        self.initial_delay = max(0.0, float(event.direct.get("progress_initial_delay_seconds") or 8))
        self.interval = max(10.0, float(event.direct.get("progress_interval_seconds") or 45))
        self.min_interval = max(3.0, float(event.direct.get("progress_min_interval_seconds") or 18))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._last_sent_at = 0.0
        self._last_sent_text = ""

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"wechat-run-events-{_safe_path_segment(self.event.session_key)}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=20.0)

    def _heartbeat_loop(self) -> None:
        if self._stop.wait(self.initial_delay):
            return
        self.emit_status(prefix="正在处理", reason="initial_delay", force=True)
        while not self._stop.wait(self.interval):
            self.emit_status(prefix="仍在处理", reason="heartbeat", force=True)

    def emit_status(self, prefix: str, reason: str, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": "progress_events_disabled"}
        try:
            status = self.manager.qiaojie.run_status(self.event.run_id)
        except Exception as exc:
            status = {"ok": False, "error": str(exc)}
        text = _gateway_run_progress_reply(status, prefix=prefix)
        return self.emit(text, {"source": "gateway_run_event", "event": reason}, force=force)

    def emit_interim(self, text: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": "progress_events_disabled"}
        clean = strip_internal_reply_markers(text)
        clean = str(clean or "").strip()
        if not clean:
            return {"ok": False, "skipped": "empty_interim_reply"}
        if _low_information_wechat_interim(clean, meta):
            self._record_progress_result(
                clean,
                {"ok": False, "skipped": "low_information_interim"},
                kind="interim",
            )
            return {"ok": False, "skipped": "low_information_interim"}
        return self.emit(clean, meta if isinstance(meta, dict) else {}, force=False)

    def emit(self, text: str, meta: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": "progress_events_disabled"}
        clean = str(strip_internal_reply_markers(text) or "").strip()
        if not clean:
            return {"ok": False, "skipped": "empty_progress_event"}
        if self._stop.is_set():
            return {"ok": False, "skipped": "stopped"}
        if not self.manager._gateway_event_is_current(self.event):
            return {"ok": False, "skipped": "stale_generation"}
        if self.manager._gateway_event_has_pending_followup(self.event):
            return {"ok": False, "skipped": "superseded_by_pending", "pending": True}
        now = time.time()
        with self._lock:
            if clean == self._last_sent_text:
                result = {"ok": False, "skipped": "duplicate_progress_event"}
                self._record_progress_result(clean, result, kind="progress")
                return result
            if not force and self._last_sent_at and now - self._last_sent_at < self.min_interval:
                result = {"ok": False, "skipped": "progress_rate_limited"}
                self._record_progress_result(clean, result, kind="progress")
                return result
            try:
                send_result = self.manager._send_wechat_direct_text(
                    self.event.direct,
                    self.event.to_user_id,
                    clean,
                    context_token=self.event.context_token,
                    run_id=self.event.run_id,
                )
                self._last_sent_at = time.time()
                self._last_sent_text = clean
                result = {"ok": bool(send_result.get("ok")), "delivery": send_result}
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            self._record_progress_result(clean, result, kind=str((meta or {}).get("event") or "progress"))
            return result

    def _record_progress_result(self, text: str, result: dict[str, Any], kind: str = "progress") -> None:
        self.manager._set_status(
            self.event.channel,
            "running",
            last_progress_event_preview=_preview_text(text),
            last_progress_event_kind=kind,
            last_progress_event_at=time.time(),
            last_progress_event_result=result,
            last_session_key=self.event.session_key,
        )
        self.manager._set_gateway_session_status(
            self.event.session_key,
            "running",
            last_progress_event_preview=_preview_text(text),
            last_progress_event_kind=kind,
            last_progress_event_at=time.time(),
            last_progress_event_result=result,
            generation=self.event.generation,
        )


def _wechat_direct_singleton_name(account_id: str) -> str:
    digest = hashlib.sha256(str(account_id or "default").encode("utf-8")).hexdigest()[:24]
    return f"TiangongV3WechatDirect_{digest}"


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok and exit_code.value == 259)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_wechat_direct_singleton(account_id: str) -> Any | None:
    name = _wechat_direct_singleton_name(account_id)
    lock_dir = Path.home() / ".tiangong" / "v3" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            lock_pid = 0
            try:
                raw = json.loads(lock_path.read_text(encoding="utf-8"))
                lock_pid = int(raw.get("pid") or 0)
            except Exception:
                lock_pid = 0
            if not _process_is_alive(lock_pid):
                try:
                    lock_path.unlink(missing_ok=True)
                    continue
                except Exception:
                    pass
            return None
        payload = json.dumps({"pid": os.getpid(), "created_at": time.time()}, ensure_ascii=False)
        os.write(fd, payload.encode("utf-8"))
        return ("file_lock", fd, lock_path)
    return None


def _release_wechat_direct_singleton(handle: Any | None) -> None:
    if not handle:
        return
    kind = handle[0] if isinstance(handle, tuple) else ""
    if kind == "file_lock":
        try:
            os.close(handle[1])
        except Exception:
            pass
        try:
            Path(handle[2]).unlink(missing_ok=True)
        except Exception:
            pass


def _mime_from_filename(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _mask_reply_path_protected_spans(content: str) -> str:
    text = str(content or "")
    chars = list(text)
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"```[^\n]*\n.*?```", text, re.DOTALL):
        spans.append(match.span())
    for match in re.finditer(r"`[^`\n]+`", text):
        spans.append(match.span())
    for match in re.finditer(r"(?m)^\s{0,3}>[^\n]*(?:\n\s{0,3}>[^\n]*)*", text):
        spans.append(match.span())
    for match in re.finditer(r'"(?:\\.|[^"\\])*"', text):
        if "MEDIA:" in match.group(0):
            spans.append(match.span())
    for start, end in spans:
        for index in range(max(0, start), min(len(chars), end)):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _trim_delivery_path(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "`\"'":
        text = text[1:-1].strip()
    text = text.lstrip("`\"'").rstrip("`\"',.;:)}]，。；、！？）】》")
    if re.match(r"^/[A-Za-z]:/", text):
        text = text[1:].replace("/", "\\")
    return text


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    chars = list(text)
    for start, end in sorted(spans, reverse=True):
        del chars[start:end]
    return "".join(chars)


def _extract_wechat_media_candidates(content: str) -> tuple[list[dict[str, Any]], str]:
    text = str(content or "")
    has_voice_tag = "[[audio_as_voice]]" in text
    scan_text = _mask_reply_path_protected_spans(text)
    candidates: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for match in _WECHAT_MEDIA_TAG_RE.finditer(scan_text):
        path = _trim_delivery_path(match.group("path"))
        if not path:
            continue
        candidates.append({
            "path": os.path.expanduser(path),
            "source": "media_tag",
            "is_voice": has_voice_tag,
        })
        spans.append(match.span())
    cleaned = _remove_spans(text, spans)
    cleaned = cleaned.replace("[[audio_as_voice]]", "").replace("[[as_document]]", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return candidates, cleaned


def _extract_wechat_local_file_candidates(content: str) -> tuple[list[dict[str, Any]], str]:
    text = str(content or "")
    scan_text = _mask_reply_path_protected_spans(text)
    candidates: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for match in _WECHAT_LOCAL_FILE_RE.finditer(scan_text):
        raw = _trim_delivery_path(text[match.start("path"):match.end("path")])
        if not raw:
            continue
        candidates.append({
            "path": os.path.expanduser(raw),
            "source": "local_path",
            "is_voice": False,
        })
        spans.append((match.start("path"), match.end("path")))
    cleaned = _remove_spans(text, spans)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return candidates, cleaned


def _wechat_delivery_kind(path: str, is_voice: bool = False) -> str:
    del is_voice
    mime = _mime_from_filename(Path(path).name)
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "document"


def _resolve_wechat_delivery_candidates(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        raw_path = str(candidate.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            path = Path(raw_path).expanduser()
            resolved = path.resolve(strict=False)
        except Exception as exc:
            errors.append({"path": raw_path, "source": candidate.get("source"), "error": f"resolve_failed:{exc}"})
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        suffix = resolved.suffix.lower()
        if suffix not in MEDIA_DELIVERY_EXTS:
            errors.append({"path": str(resolved), "source": candidate.get("source"), "error": f"unsupported_extension:{suffix}"})
            continue
        if not resolved.exists():
            errors.append({"path": str(resolved), "source": candidate.get("source"), "error": "not_found"})
            continue
        if not resolved.is_file():
            errors.append({"path": str(resolved), "source": candidate.get("source"), "error": "not_file"})
            continue
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            errors.append({"path": str(resolved), "source": candidate.get("source"), "error": f"stat_failed:{exc}"})
            continue
        files.append({
            "path": str(resolved),
            "source": candidate.get("source"),
            "kind": _wechat_delivery_kind(str(resolved), bool(candidate.get("is_voice"))),
            "name": resolved.name,
            "bytes": size,
            "is_voice": bool(candidate.get("is_voice")),
        })
    return files, errors


def _wechat_cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    return f"{_normalize_url(cdn_base_url, WECHAT_CDN_BASE_URL)}/download?encrypted_query_param={quote(str(encrypted_query_param), safe='')}"


def _wechat_cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{_normalize_url(cdn_base_url, WECHAT_CDN_BASE_URL)}/upload"
        f"?encrypted_query_param={quote(str(upload_param), safe='')}"
        f"&filekey={quote(str(filekey), safe='')}"
    )


def _assert_wechat_cdn_url(url: str) -> None:
    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("media_url_scheme_not_allowed")
    host = (parsed.hostname or "").lower()
    if host not in _WECHAT_CDN_ALLOWLIST:
        raise ValueError(f"media_url_host_not_allowed:{host}")


def _parse_wechat_aes_key(aes_key_b64: str) -> bytes:
    decoded = base64.b64decode(str(aes_key_b64 or ""))
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected_aes_key_length:{len(decoded)}")


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _raise_ntstatus(status: int, name: str) -> None:
    if int(status) < 0:
        raise OSError(f"{name}:{status:#x}")


def _windows_aes_crypt(
    data: bytes,
    key: bytes,
    *,
    mode_name: str = "ECB",
    decrypt: bool = False,
    iv: bytes | None = None,
) -> bytes:
    if os.name != "nt":
        raise RuntimeError("windows_aes_unavailable")
    import ctypes
    from ctypes import wintypes

    bcrypt = ctypes.WinDLL("bcrypt")
    bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.ULONG,
    ]
    bcrypt.BCryptOpenAlgorithmProvider.restype = wintypes.LONG
    bcrypt.BCryptCloseAlgorithmProvider.argtypes = [wintypes.HANDLE, wintypes.ULONG]
    bcrypt.BCryptCloseAlgorithmProvider.restype = wintypes.LONG
    bcrypt.BCryptGetProperty.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.ULONG,
    ]
    bcrypt.BCryptGetProperty.restype = wintypes.LONG
    bcrypt.BCryptSetProperty.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    bcrypt.BCryptSetProperty.restype = wintypes.LONG
    bcrypt.BCryptGenerateSymmetricKey.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    bcrypt.BCryptGenerateSymmetricKey.restype = wintypes.LONG
    bcrypt.BCryptDestroyKey.argtypes = [wintypes.HANDLE]
    bcrypt.BCryptDestroyKey.restype = wintypes.LONG
    crypt_args = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.ULONG,
    ]
    bcrypt.BCryptEncrypt.argtypes = crypt_args
    bcrypt.BCryptEncrypt.restype = wintypes.LONG
    bcrypt.BCryptDecrypt.argtypes = crypt_args
    bcrypt.BCryptDecrypt.restype = wintypes.LONG

    mode_value = "ChainingModeCBC" if mode_name.upper() == "CBC" else "ChainingModeECB"
    h_alg = wintypes.HANDLE()
    h_key = wintypes.HANDLE()
    _raise_ntstatus(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0), "BCryptOpenAlgorithmProvider")
    try:
        mode_buffer = ctypes.create_unicode_buffer(mode_value)
        _raise_ntstatus(
            bcrypt.BCryptSetProperty(
                h_alg,
                "ChainingMode",
                ctypes.cast(mode_buffer, ctypes.c_void_p),
                ctypes.sizeof(mode_buffer),
                0,
            ),
            "BCryptSetProperty",
        )
        object_len = wintypes.ULONG()
        bytes_read = wintypes.ULONG()
        _raise_ntstatus(
            bcrypt.BCryptGetProperty(
                h_alg,
                "ObjectLength",
                ctypes.byref(object_len),
                ctypes.sizeof(object_len),
                ctypes.byref(bytes_read),
                0,
            ),
            "BCryptGetProperty",
        )
        key_object = ctypes.create_string_buffer(object_len.value)
        key_buffer = ctypes.create_string_buffer(key, len(key))
        _raise_ntstatus(
            bcrypt.BCryptGenerateSymmetricKey(
                h_alg,
                ctypes.byref(h_key),
                key_object,
                object_len.value,
                key_buffer,
                len(key),
                0,
            ),
            "BCryptGenerateSymmetricKey",
        )
        try:
            in_buffer = ctypes.create_string_buffer(data, len(data))
            out_len = wintypes.ULONG()
            iv_len = len(iv or b"")
            iv_buffer = ctypes.create_string_buffer(iv, iv_len) if iv else None
            iv_ptr = ctypes.cast(iv_buffer, ctypes.c_void_p) if iv_buffer else None
            crypt_fn = bcrypt.BCryptDecrypt if decrypt else bcrypt.BCryptEncrypt
            _raise_ntstatus(
                crypt_fn(h_key, in_buffer, len(data), None, iv_ptr, iv_len, None, 0, ctypes.byref(out_len), 0),
                "BCryptCryptSize",
            )
            out_buffer = ctypes.create_string_buffer(out_len.value)
            iv_buffer = ctypes.create_string_buffer(iv, iv_len) if iv else None
            iv_ptr = ctypes.cast(iv_buffer, ctypes.c_void_p) if iv_buffer else None
            _raise_ntstatus(
                crypt_fn(
                    h_key,
                    in_buffer,
                    len(data),
                    None,
                    iv_ptr,
                    iv_len,
                    out_buffer,
                    out_len.value,
                    ctypes.byref(out_len),
                    0,
                ),
                "BCryptCrypt",
            )
            return out_buffer.raw[:out_len.value]
        finally:
            if h_key:
                bcrypt.BCryptDestroyKey(h_key)
    finally:
        if h_alg:
            bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def _load_cryptography_cipher():
    for dll_dir in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[2]):
        try:
            os.add_dll_directory(str(dll_dir))
        except Exception:
            pass
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    return Cipher, algorithms, modes


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    try:
        Cipher, algorithms, modes = _load_cryptography_cipher()
    except Exception as exc:
        try:
            return _windows_aes_crypt(_pkcs7_pad(plaintext), key, mode_name="ECB")
        except Exception as fallback_exc:
            raise RuntimeError(f"wechat_media_crypto_unavailable:{exc}; fallback:{fallback_exc}") from fallback_exc

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    try:
        Cipher, algorithms, modes = _load_cryptography_cipher()
    except Exception as exc:
        try:
            padded = _windows_aes_crypt(ciphertext, key, mode_name="ECB", decrypt=True)
        except Exception as fallback_exc:
            raise RuntimeError(f"wechat_media_crypto_unavailable:{exc}; fallback:{fallback_exc}") from fallback_exc
        if not padded:
            return padded
        pad_len = padded[-1]
        if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
            return padded[:-pad_len]
        return padded

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _aes_padded_size(size: int) -> int:
    return ((int(size) + 1 + 15) // 16) * 16


def _media_reference(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    if not isinstance(value, dict):
        return {}
    media = value.get("media")
    return media if isinstance(media, dict) else {}


def _media_aes_key_b64(item: dict[str, Any], key: str, media: dict[str, Any]) -> str:
    value = item.get(key)
    node = value if isinstance(value, dict) else {}
    image_hex = str(node.get("aeskey") or node.get("aes_key_hex") or "").strip()
    if image_hex:
        try:
            return base64.b64encode(bytes.fromhex(image_hex)).decode("ascii")
        except ValueError:
            pass
    return str(media.get("aes_key") or media.get("aeskey") or "").strip()


def _download_ilink_media_bytes(
    media: dict[str, Any],
    *,
    cdn_base_url: str,
    aes_key_b64: str,
    timeout: float,
    max_bytes: int,
) -> bytes:
    encrypted_query_param = str(media.get("encrypt_query_param") or media.get("encrypted_query_param") or "").strip()
    full_url = str(media.get("full_url") or media.get("url") or "").strip()
    if encrypted_query_param:
        url = _wechat_cdn_download_url(cdn_base_url, encrypted_query_param)
    elif full_url:
        _assert_wechat_cdn_url(full_url)
        url = full_url
    else:
        raise RuntimeError("media_reference_missing_url")
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    length = response.headers.get("content-length")
    if length:
        try:
            if int(length) > max_bytes:
                raise RuntimeError(f"attachment_too_large:{length}>{max_bytes}")
        except ValueError:
            pass
    data = response.content
    if len(data) > max_bytes:
        raise RuntimeError(f"attachment_too_large:{len(data)}>{max_bytes}")
    if aes_key_b64:
        data = _aes128_ecb_decrypt(data, _parse_wechat_aes_key(aes_key_b64))
    return data


def _cache_wechat_attachment(
    data: bytes,
    filename: str,
    *,
    conversation_id: str,
    message_id: str,
    kind: str,
    mime: str,
) -> dict[str, Any]:
    safe_conversation = _safe_path_segment(conversation_id or "default")
    safe_message = _safe_path_segment(message_id or uuid.uuid4().hex)
    safe_name = _safe_filename(filename)
    target_dir = WECHAT_INBOX_ROOT / safe_conversation / safe_message
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    root = WECHAT_INBOX_ROOT.resolve()
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(root):
        raise ValueError("attachment_path_escape_rejected")
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "status": "available",
        "source": "wechat_direct",
        "kind": kind,
        "type": kind,
        "name": safe_name,
        "path": str(target),
        "size": len(data),
        "size_bytes": len(data),
        "sha256": digest,
        "mime": mime,
        "message_id": str(message_id or ""),
        "received_at": time.time(),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wechat_max_attachment_bytes(direct: dict[str, Any]) -> int:
    try:
        max_bytes = int(direct.get("max_attachment_bytes") or WECHAT_MAX_ATTACHMENT_BYTES)
    except Exception:
        max_bytes = WECHAT_MAX_ATTACHMENT_BYTES
    return max(1024, min(max_bytes, 512 * 1024 * 1024))


def _cache_wechat_attachment_from_path(
    source_path: Path,
    filename: str,
    *,
    conversation_id: str,
    message_id: str,
    kind: str,
    mime: str,
    max_bytes: int,
) -> dict[str, Any]:
    source = Path(source_path).resolve(strict=True)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    size = source.stat().st_size
    if size > max_bytes:
        raise RuntimeError(f"attachment_too_large:{size}>{max_bytes}")
    safe_conversation = _safe_path_segment(conversation_id or "default")
    safe_message = _safe_path_segment(message_id or uuid.uuid4().hex)
    safe_name = _safe_filename(filename or source.name)
    target_dir = WECHAT_INBOX_ROOT / safe_conversation / safe_message
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    root = WECHAT_INBOX_ROOT.resolve()
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(root):
        raise ValueError("attachment_path_escape_rejected")
    shutil.copy2(source, target)
    digest = _sha256_file(target)
    return {
        "status": "available",
        "source": "wechat_direct",
        "resolved_by": "local_file_fallback",
        "kind": kind,
        "type": kind,
        "name": safe_name,
        "path": str(target),
        "source_path": str(source),
        "size": size,
        "size_bytes": size,
        "sha256": digest,
        "mime": mime,
        "message_id": str(message_id or ""),
        "received_at": time.time(),
    }


def _attachment_error(filename: str, kind: str, message_id: str, exc: Exception) -> dict[str, Any]:
    return {
        "status": "error",
        "source": "wechat_direct",
        "kind": kind,
        "type": kind,
        "name": _safe_filename(filename),
        "message_id": str(message_id or ""),
        "error": str(exc)[:500],
        "received_at": time.time(),
    }


def _wechat_local_attachment_roots(direct: dict[str, Any]) -> list[Path]:
    configured = direct.get("local_attachment_roots")
    roots: list[Path] = []
    if isinstance(configured, list):
        roots.extend(Path(str(item)) for item in configured if str(item or "").strip())
    home = Path.home()
    xwechat_root = home / "Documents" / "xwechat_files"
    try:
        if xwechat_root.exists():
            for file_root in xwechat_root.glob("*/msg/file"):
                if file_root.exists():
                    roots.extend(path for path in file_root.iterdir() if path.is_dir())
                    roots.append(file_root)
    except Exception:
        pass
    wechat_files_root = home / "Documents" / "WeChat Files"
    try:
        if wechat_files_root.exists():
            for file_root in wechat_files_root.glob("*/FileStorage/File"):
                if file_root.exists():
                    roots.extend(path for path in file_root.iterdir() if path.is_dir())
                    roots.append(file_root)
    except Exception:
        pass
    wxwork_root = home / "Documents" / "WXWork"
    try:
        if wxwork_root.exists():
            for file_root in wxwork_root.glob("*/File"):
                if file_root.exists():
                    roots.extend(path for path in file_root.iterdir() if path.is_dir())
                    roots.append(file_root)
    except Exception:
        pass
    roots.extend([
        home / "Downloads",
        home / "Desktop",
        Path("C:/common_attachment"),
        home / "AppData" / "Local" / "Temp",
        xwechat_root,
        wechat_files_root,
        wxwork_root,
    ])
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _local_attachment_max_age_seconds(direct: dict[str, Any]) -> int:
    try:
        value = int(direct.get("local_attachment_max_age_seconds") or WECHAT_LOCAL_ATTACHMENT_MAX_AGE_SECONDS)
    except Exception:
        value = WECHAT_LOCAL_ATTACHMENT_MAX_AGE_SECONDS
    return max(60, min(value, 30 * 24 * 60 * 60))


def _find_local_attachment_by_name(filename: str, direct: dict[str, Any]) -> list[Path]:
    safe_name = _safe_filename(filename)
    if not safe_name:
        return []
    max_age = _local_attachment_max_age_seconds(direct)
    min_mtime = time.time() - max_age
    deadline = time.monotonic() + 8.0
    matches: list[Path] = []
    seen: set[str] = set()

    def add_candidate(path: Path) -> None:
        try:
            resolved = path.resolve()
            key = str(resolved).lower()
            if key in seen or not resolved.is_file():
                return
            stat = resolved.stat()
            if stat.st_size <= 0 or stat.st_mtime < min_mtime:
                return
            seen.add(key)
            matches.append(resolved)
        except Exception:
            return

    for root in _wechat_local_attachment_roots(direct):
        if time.monotonic() > deadline or len(matches) >= 25:
            break
        try:
            if not root.exists():
                continue
            if root.is_file():
                if root.name == safe_name:
                    add_candidate(root)
                continue
            direct_child = root / safe_name
            if direct_child.exists():
                add_candidate(direct_child)
            for path in root.rglob(safe_name):
                add_candidate(path)
                if time.monotonic() > deadline or len(matches) >= 25:
                    break
        except Exception:
            continue
    matches.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return matches


def _extract_wechat_file_placeholders(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _WECHAT_FILE_PLACEHOLDER_RE.finditer(str(text or "")):
        name = _safe_filename(match.group(1))
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _is_wechat_attachment_followup(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    if not compact:
        return False
    has_attachment_ref = any(marker in compact for marker in (
        "这个文件",
        "这个附件",
        "这个压缩包",
        "这个zip",
        "刚才的文件",
        "刚才那个文件",
        "上面的文件",
        "刚发的",
        "刚才发的",
        "那个包",
        "这个包",
        "附件",
        "压缩包",
        "zip",
        "文件",
    ))
    has_file_task = any(marker in compact for marker in (
        "放到桌面",
        "放桌面",
        "复制",
        "移动",
        "保存",
        "解压",
        "打开",
        "读取",
        "看看",
        "修复",
        "测试",
        "运行",
        "打包",
    ))
    has_status_probe = any(marker in compact for marker in (
        "收到没",
        "收到了吗",
        "收到没有",
        "有没有收到",
        "到了没",
        "到没",
        "看到了吗",
        "看到没",
        "找到了吗",
        "找到没",
        "再看看",
        "落点",
        "落哪",
        "在哪",
        "路径",
        "保存在哪",
        "放哪",
        "放到哪",
    ))
    short_task_followup = has_file_task and len(compact) <= 24
    return (has_attachment_ref and has_file_task) or has_status_probe or short_task_followup


def _attachment_from_cached_wechat_file(path: Path, *, resolved_by: str = "wechat_inbox_cache") -> dict[str, Any]:
    source = Path(path).resolve(strict=True)
    root = WECHAT_INBOX_ROOT.resolve()
    if not source.is_file() or not source.is_relative_to(root):
        raise ValueError("wechat_cached_attachment_path_rejected")
    stat = source.stat()
    return {
        "status": "available",
        "source": "wechat_direct",
        "resolved_by": resolved_by,
        "kind": "file",
        "type": "file",
        "name": _safe_filename(source.name),
        "path": str(source),
        "size": stat.st_size,
        "size_bytes": stat.st_size,
        "sha256": _sha256_file(source),
        "mime": _mime_from_filename(source.name),
        "message_id": source.parent.name,
        "received_at": stat.st_mtime,
    }


def _wechat_attachment_index_keys(conversation_id: str, to_user_id: str) -> list[str]:
    keys: list[str] = []
    for value in (conversation_id, to_user_id):
        key = _safe_path_segment(value or "")
        if key and key not in keys:
            keys.append(key)
    return keys


def _load_wechat_attachment_index() -> dict[str, Any]:
    try:
        if not WECHAT_ATTACHMENT_INDEX_PATH.exists():
            return {}
        data = json.loads(WECHAT_ATTACHMENT_INDEX_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_wechat_attachment_index(index: dict[str, Any]) -> None:
    WECHAT_ATTACHMENT_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WECHAT_ATTACHMENT_INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(WECHAT_ATTACHMENT_INDEX_PATH)


def _compact_wechat_attachment_index_items(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root = WECHAT_INBOX_ROOT.resolve()
    items: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("status") != "available":
            continue
        raw_path = str(attachment.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            path = Path(raw_path).resolve(strict=True)
            if not path.is_file() or not path.is_relative_to(root):
                continue
            path_key = str(path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            stat = path.stat()
            items.append({
                "path": path_key,
                "name": _safe_filename(attachment.get("name") or path.name),
                "kind": str(attachment.get("kind") or attachment.get("type") or "file"),
                "mime": str(attachment.get("mime") or _mime_from_filename(path.name)),
                "size": int(attachment.get("size") or attachment.get("size_bytes") or stat.st_size),
                "sha256": str(attachment.get("sha256") or "")[:64],
                "message_id": str(attachment.get("message_id") or path.parent.name),
                "received_at": float(attachment.get("received_at") or stat.st_mtime),
            })
        except Exception:
            continue
    items.sort(key=lambda item: float(item.get("received_at") or 0), reverse=True)
    return items[:5]


def _attachments_from_wechat_index_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    attachments: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            path = Path(raw_path).resolve(strict=True)
            path_key = str(path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            attachment = _attachment_from_cached_wechat_file(path, resolved_by="wechat_attachment_index")
            for key in ("kind", "type", "mime", "message_id"):
                if item.get(key):
                    attachment[key] = item.get(key)
            attachments.append(attachment)
        except Exception:
            continue
    return attachments


def _remember_wechat_attachments_on_disk(keys: list[str], attachments: list[dict[str, Any]]) -> None:
    items = _compact_wechat_attachment_index_items(attachments)
    clean_keys = [key for key in keys if key]
    if not items or not clean_keys:
        return
    index = _load_wechat_attachment_index()
    for key in clean_keys:
        index[key] = items
    if len(index) > WECHAT_ATTACHMENT_INDEX_MAX_KEYS:
        def latest_time(value: Any) -> float:
            indexed = value if isinstance(value, list) else []
            times = [float(item.get("received_at") or 0) for item in indexed if isinstance(item, dict)]
            return max(times) if times else 0.0

        ordered = sorted(index.keys(), key=lambda key: latest_time(index.get(key)), reverse=True)
        keep = set(ordered[:WECHAT_ATTACHMENT_INDEX_KEEP_KEYS])
        index = {key: value for key, value in index.items() if key in keep}
    _save_wechat_attachment_index(index)


def _lookup_wechat_attachments_on_disk(keys: list[str]) -> list[dict[str, Any]]:
    index = _load_wechat_attachment_index()
    for key in keys:
        if not key:
            continue
        attachments = _attachments_from_wechat_index_items(index.get(key))
        if attachments:
            return attachments
    return []


def _wechat_context_token_key(account_id: str, user_id: str) -> str:
    return f"{account_id or 'default'}:{user_id or 'unknown'}"


def _load_wechat_context_tokens() -> dict[str, str]:
    try:
        if not WECHAT_CONTEXT_TOKEN_PATH.exists():
            return {}
        data = json.loads(WECHAT_CONTEXT_TOKEN_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if str(value or "").strip()}
    except Exception:
        return {}


def _save_wechat_context_tokens(tokens: dict[str, str]) -> None:
    WECHAT_CONTEXT_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WECHAT_CONTEXT_TOKEN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(WECHAT_CONTEXT_TOKEN_PATH)


def _remember_wechat_context_token_on_disk(account_id: str, user_id: str, context_token: str) -> None:
    token = str(context_token or "").strip()
    if not token or not user_id:
        return
    tokens = _load_wechat_context_tokens()
    tokens[_wechat_context_token_key(account_id, user_id)] = token
    if len(tokens) > 500:
        keep_keys = list(tokens.keys())[-300:]
        tokens = {key: tokens[key] for key in keep_keys if key in tokens}
    _save_wechat_context_tokens(tokens)


def _lookup_wechat_context_token_on_disk(account_id: str, user_id: str) -> str:
    if not user_id:
        return ""
    tokens = _load_wechat_context_tokens()
    return tokens.get(_wechat_context_token_key(account_id, user_id), "")


def _forget_wechat_context_token_on_disk(account_id: str, user_id: str) -> None:
    if not user_id:
        return
    tokens = _load_wechat_context_tokens()
    key = _wechat_context_token_key(account_id, user_id)
    if key not in tokens:
        return
    tokens.pop(key, None)
    _save_wechat_context_tokens(tokens)


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _ilink_response_message(response: dict[str, Any]) -> str:
    return str(response.get("errmsg") or response.get("message") or response.get("msg") or "").strip()


def _is_ilink_success_response(response: dict[str, Any]) -> bool:
    ret = _int_or_none(response.get("ret"))
    errcode = _int_or_none(response.get("errcode"))
    return ret in (0, None) and errcode in (0, None)


def _is_ilink_session_expired_response(response: dict[str, Any]) -> bool:
    ret = _int_or_none(response.get("ret"))
    errcode = _int_or_none(response.get("errcode"))
    message = _ilink_response_message(response).lower()
    if ret == WECHAT_SESSION_EXPIRED_ERRCODE or errcode == WECHAT_SESSION_EXPIRED_ERRCODE:
        return True
    return (ret == WECHAT_RATE_LIMIT_ERRCODE or errcode == WECHAT_RATE_LIMIT_ERRCODE) and message == "unknown error"


def _is_ilink_rate_limited_response(response: dict[str, Any]) -> bool:
    ret = _int_or_none(response.get("ret"))
    errcode = _int_or_none(response.get("errcode"))
    return ret == WECHAT_RATE_LIMIT_ERRCODE or errcode == WECHAT_RATE_LIMIT_ERRCODE


def _latest_cached_wechat_attachments(conversation_id: str, to_user_id: str, limit: int = 3) -> list[dict[str, Any]]:
    keys = _wechat_attachment_index_keys(conversation_id, to_user_id)
    seen_keys: set[str] = set()
    files: list[Path] = []
    for key in keys:
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        root = WECHAT_INBOX_ROOT / key
        if not root.exists():
            continue
        try:
            for path in root.rglob("*"):
                if path.is_file():
                    files.append(path)
        except Exception:
            continue
    files.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    attachments: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for path in files:
        if len(attachments) >= max(1, limit):
            break
        try:
            resolved = str(path.resolve(strict=True))
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            attachments.append(_attachment_from_cached_wechat_file(path))
        except Exception:
            continue
    return attachments


def _latest_file_placeholder_from_events(conversation_id: str) -> str:
    candidates = [_safe_path_segment(conversation_id or ""), "default"]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = Path.home() / ".tiangong" / "v3" / "duihua_shijian" / candidate / "events.jsonl"
        try:
            if not path.exists() or path.stat().st_size > 8 * 1024 * 1024:
                continue
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-120:]
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if str(event.get("role") or "").lower() != "user":
                    continue
                names = _extract_wechat_file_placeholders(str(event.get("content") or ""))
                if names:
                    return names[-1]
        except Exception:
            continue
    return ""


def _resolve_local_file_placeholder(
    filename: str,
    message: dict[str, Any],
    direct: dict[str, Any],
    conversation_id: str,
) -> dict[str, Any]:
    message_id = str(message.get("message_id") or message.get("seq") or message.get("client_id") or uuid.uuid4().hex)
    safe_name = _safe_filename(filename)
    if not direct.get("local_attachment_fallback", True):
        return _attachment_error(safe_name, "file", message_id, RuntimeError("local_attachment_fallback_disabled"))
    try:
        matches = _find_local_attachment_by_name(safe_name, direct)
        if not matches:
            return _attachment_error(safe_name, "file", message_id, RuntimeError("local_attachment_not_found"))
        attachment = _cache_wechat_attachment_from_path(
            matches[0],
            safe_name,
            conversation_id=conversation_id,
            message_id=message_id,
            kind="file",
            mime=_mime_from_filename(safe_name),
            max_bytes=_wechat_max_attachment_bytes(direct),
        )
        attachment["candidate_count"] = len(matches)
        return attachment
    except Exception as exc:
        return _attachment_error(safe_name, "file", message_id, exc)


def _collect_ilink_attachments(message: dict[str, Any], direct: dict[str, Any], conversation_id: str) -> list[dict[str, Any]]:
    cdn_base_url = _normalize_url(direct.get("cdn_base_url") or WECHAT_CDN_BASE_URL, WECHAT_CDN_BASE_URL)
    max_bytes = _wechat_max_attachment_bytes(direct)
    message_id = str(message.get("message_id") or message.get("seq") or message.get("client_id") or uuid.uuid4().hex)
    attachments: list[dict[str, Any]] = []

    def handle_item(item: dict[str, Any], suffix: str = "") -> None:
        candidates: list[tuple[str, str, str, str, float]] = []
        if isinstance(item.get("file_item"), dict):
            filename = str(item["file_item"].get("file_name") or "document.bin")
            candidates.append(("file", "file_item", filename, _mime_from_filename(filename), 60.0))
        if isinstance(item.get("image_item"), dict):
            candidates.append(("image", "image_item", f"image_{message_id}{suffix}.jpg", "image/jpeg", 30.0))
        if isinstance(item.get("video_item"), dict):
            candidates.append(("video", "video_item", f"video_{message_id}{suffix}.mp4", "video/mp4", 120.0))
        if isinstance(item.get("voice_item"), dict):
            candidates.append(("audio", "voice_item", f"voice_{message_id}{suffix}.silk", "audio/silk", 60.0))
        for kind, key, filename, mime, timeout in candidates:
            media = _media_reference(item, key)
            if not media or not (
                media.get("encrypt_query_param")
                or media.get("encrypted_query_param")
                or media.get("full_url")
                or media.get("url")
            ):
                continue
            try:
                data = _download_ilink_media_bytes(
                    media,
                    cdn_base_url=cdn_base_url,
                    aes_key_b64=_media_aes_key_b64(item, key, media),
                    timeout=timeout,
                    max_bytes=max_bytes,
                )
                attachments.append(_cache_wechat_attachment(
                    data,
                    filename,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    kind=kind,
                    mime=mime,
                ))
            except Exception as exc:
                attachments.append(_attachment_error(filename, kind, message_id, exc))

    for index, item in enumerate(message.get("item_list") or []):
        if not isinstance(item, dict):
            continue
        handle_item(item, suffix=f"_{index}")
        ref_message = item.get("ref_msg") if isinstance(item.get("ref_msg"), dict) else {}
        ref_item = ref_message.get("message_item") if isinstance(ref_message.get("message_item"), dict) else {}
        if ref_item:
            handle_item(ref_item, suffix=f"_{index}_ref")
    return attachments


def _text_from_attachments(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""
    rows = []
    for item in attachments[:WECHAT_ATTACHMENT_CONTEXT_LIMIT]:
        status = str(item.get("status") or "")
        name = str(item.get("name") or "attachment")
        path = str(item.get("path") or "")
        if status == "available" and path:
            rows.append(f"- {name}: {path}")
        else:
            rows.append(f"- {name}: 接收失败 {item.get('error') or ''}".strip())
    return "用户发送了附件：\n" + "\n".join(rows)


def _wechat_attachment_context_note(attachments: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for item in attachments[:WECHAT_ATTACHMENT_CONTEXT_LIMIT]:
        if not isinstance(item, dict) or item.get("status") != "available":
            continue
        name = str(item.get("name") or Path(str(item.get("path") or "")).name or "attachment").strip()
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        parts = [f"- name={name}", f"path={path}"]
        size = item.get("size") or item.get("size_bytes")
        if size:
            parts.append(f"size={size}")
        sha256 = str(item.get("sha256") or "").strip()
        if sha256:
            parts.append(f"sha256={sha256[:16]}")
        resolved_by = str(item.get("resolved_by") or "").strip()
        if resolved_by:
            parts.append(f"resolved_by={resolved_by}")
        rows.append("; ".join(parts))
    if not rows:
        return ""
    return (
        "[微信附件上下文]\n"
        "用户刚通过微信发送或追问最近附件。以下 path 是本轮可直接操作的真实本地落点；"
        "如用户询问收到没、在哪里、放到桌面、解压、查看、修复、运行，请优先使用这些 path，"
        "不要只扫描当前工作区。\n"
        + "\n".join(rows)
    )


def _prepend_wechat_attachment_context(text: str, attachments: list[dict[str, Any]]) -> str:
    note = _wechat_attachment_context_note(attachments)
    clean_text = str(text or "").strip()
    if not note:
        return clean_text
    if "[微信附件上下文]" in clean_text:
        return clean_text
    return f"{note}\n\n{clean_text}" if clean_text else note


def _wechat_uin_header() -> str:
    number = str(random.getrandbits(32))
    return base64.b64encode(number.encode("utf-8")).decode("ascii")


def _ilink_headers(token: str = "", *, json_body: bool = True) -> dict[str, str]:
    headers = {
        "iLink-App-Id": WECHAT_ILINK_APP_ID,
        "iLink-App-ClientVersion": str(WECHAT_ILINK_CLIENT_VERSION),
    }
    if json_body:
        headers.update({
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _wechat_uin_header(),
        })
        if str(token or "").strip():
            headers["Authorization"] = f"Bearer {str(token).strip()}"
    return headers


def _ilink_base_info() -> dict[str, str]:
    return {
        "channel_version": WECHAT_ILINK_CHANNEL_VERSION,
        "bot_agent": "TiangongZaowu/3.0.0",
    }


def _ilink_post_json(
    base_url: str,
    endpoint: str,
    body: dict[str, Any],
    *,
    token: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"{_normalize_url(base_url)}/{endpoint.lstrip('/')}"
    response = httpx.post(url, json=body, headers=_ilink_headers(token), timeout=timeout)
    response.raise_for_status()
    if not response.text.strip():
        return {"_http_status": response.status_code}
    data = response.json()
    if isinstance(data, dict):
        data.setdefault("_http_status", response.status_code)
        return data
    return {"data": data, "_http_status": response.status_code}


def _ilink_get_json(base_url: str, endpoint: str, *, timeout: float = 35.0) -> dict[str, Any]:
    url = f"{_normalize_url(base_url)}/{endpoint.lstrip('/')}"
    response = httpx.get(url, headers=_ilink_headers(json_body=False), timeout=timeout)
    response.raise_for_status()
    if not response.text.strip():
        return {"_http_status": response.status_code}
    data = response.json()
    if isinstance(data, dict):
        data.setdefault("_http_status", response.status_code)
        return data
    return {"data": data, "_http_status": response.status_code}


def _extract_ilink_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in message.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        text_item = item.get("text_item") if isinstance(item.get("text_item"), dict) else {}
        voice_item = item.get("voice_item") if isinstance(item.get("voice_item"), dict) else {}
        file_item = item.get("file_item") if isinstance(item.get("file_item"), dict) else {}
        if text_item.get("text"):
            parts.append(str(text_item.get("text") or ""))
        elif voice_item.get("text"):
            parts.append(str(voice_item.get("text") or ""))
        elif file_item.get("file_name"):
            parts.append(f"[文件] {file_item.get('file_name')}")
    return "\n".join(part.strip() for part in parts if str(part).strip()).strip()


def _split_text(text: str, limit: int = 1800) -> list[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    chunks: list[str] = []
    while clean:
        if len(clean) <= limit:
            chunks.append(clean)
            break
        cut = clean.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(clean[:cut].strip())
        clean = clean[cut:].strip()
    return chunks


def _preview_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _build_ilink_text_message(
    to_user_id: str,
    text: str,
    context_token: str = "",
    run_id: str = "",
    client_id: str = "",
) -> dict[str, Any]:
    client_id = client_id or f"tiangong-wechat-{uuid.uuid4().hex}"
    return {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
            "context_token": context_token or None,
            "run_id": run_id or None,
        }
    }


def _get_wechat_upload_url(
    base_url: str,
    *,
    token: str,
    to_user_id: str,
    media_type: int,
    filekey: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey_hex: str,
) -> dict[str, Any]:
    return _ilink_post_json(
        base_url,
        "ilink/bot/getuploadurl",
        {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
            "base_info": _ilink_base_info(),
        },
        token=token,
        timeout=30,
    )


def _upload_wechat_ciphertext(upload_url: str, ciphertext: bytes) -> str:
    response = httpx.post(
        upload_url,
        content=ciphertext,
        headers={"Content-Type": "application/octet-stream"},
        timeout=WECHAT_UPLOAD_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise RuntimeError(f"cdn_upload_http_{response.status_code}:{response.text[:200]}")
    encrypted_param = response.headers.get("x-encrypted-param")
    if not encrypted_param:
        raise RuntimeError(f"cdn_upload_missing_x_encrypted_param:{response.text[:200]}")
    return encrypted_param


def _wechat_outbound_media_type(path: str, force_file_attachment: bool = False) -> int:
    mime = _mime_from_filename(Path(path).name)
    ext = Path(path).suffix.lower()
    if mime.startswith("image/") and not force_file_attachment:
        return WECHAT_MEDIA_IMAGE
    if mime.startswith("video/") and not force_file_attachment:
        return WECHAT_MEDIA_VIDEO
    if ext == ".silk" and not force_file_attachment:
        return WECHAT_MEDIA_VOICE
    return WECHAT_MEDIA_FILE


def _build_wechat_outbound_media_item(
    path: str,
    *,
    encrypt_query_param: str,
    aes_key_for_api: str,
    ciphertext_size: int,
    plaintext_size: int,
    filename: str,
    rawfilemd5: str,
    force_file_attachment: bool = False,
) -> tuple[int, dict[str, Any]]:
    mime = _mime_from_filename(filename)
    ext = Path(path).suffix.lower()
    media_ref = {
        "encrypt_query_param": encrypt_query_param,
        "aes_key": aes_key_for_api,
        "encrypt_type": 1,
    }
    if mime.startswith("image/") and not force_file_attachment:
        return WECHAT_MEDIA_IMAGE, {
            "type": WECHAT_ITEM_IMAGE,
            "image_item": {
                "media": media_ref,
                "mid_size": ciphertext_size,
            },
        }
    if mime.startswith("video/") and not force_file_attachment:
        return WECHAT_MEDIA_VIDEO, {
            "type": WECHAT_ITEM_VIDEO,
            "video_item": {
                "media": media_ref,
                "video_size": ciphertext_size,
                "play_length": 0,
                "video_md5": rawfilemd5,
            },
        }
    if ext == ".silk" and not force_file_attachment:
        return WECHAT_MEDIA_VOICE, {
            "type": WECHAT_ITEM_VOICE,
            "voice_item": {
                "media": media_ref,
                "encode_type": 6,
                "bits_per_sample": 16,
                "sample_rate": 24000,
                "playtime": 0,
            },
        }
    return WECHAT_MEDIA_FILE, {
        "type": WECHAT_ITEM_FILE,
        "file_item": {
            "media": media_ref,
            "file_name": filename,
            "len": str(plaintext_size),
        },
    }


def _build_ilink_media_message(
    to_user_id: str,
    media_item: dict[str, Any],
    context_token: str = "",
    run_id: str = "",
    client_id: str = "",
) -> dict[str, Any]:
    client_id = client_id or f"tiangong-wechat-{uuid.uuid4().hex}"
    return {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": WECHAT_MSG_TYPE_BOT,
            "message_state": WECHAT_MSG_STATE_FINISH,
            "item_list": [media_item],
            "context_token": context_token or None,
            "run_id": run_id or None,
        },
        "base_info": _ilink_base_info(),
    }


class _WechatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _WeChatCallbackHandler(BaseHTTPRequestHandler):
    manager: "GatewayLinkManager"

    def _settings(self) -> dict[str, Any]:
        return self.manager.settings.get("wechat", {}).get("callback", {})

    def _send_text(self, text: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(str(text or "").encode("utf-8"))

    def _send_xml(self, text: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.end_headers()
        self.wfile.write(str(text or "").encode("utf-8"))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(max(0, min(length, 2 * 1024 * 1024)))

    def do_GET(self):
        parsed_url = urlparse(self.path)
        settings = self._settings()
        if parsed_url.path != str(settings.get("path") or "/wechat/callback"):
            self._send_text("not_found", 404)
            return
        query = {k: v[-1] if v else "" for k, v in parse_qs(parsed_url.query).items()}
        token = str(settings.get("token") or "")
        if not token:
            self._send_text("missing_token", 400)
            return
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")
        echostr = query.get("echostr", "")
        msg_signature = query.get("msg_signature", "")
        try:
            if msg_signature:
                expected = _sha1_sorted(token, timestamp, nonce, echostr)
                if expected != msg_signature:
                    self._send_text("invalid_signature", 403)
                    return
                plain, receive_id = _decrypt_wechat_xml(echostr, str(settings.get("encoding_aes_key") or ""))
                configured_receive_id = str(settings.get("receive_id") or "")
                if configured_receive_id and receive_id and receive_id != configured_receive_id:
                    self._send_text("invalid_receive_id", 403)
                    return
                self._send_text(plain)
                return
            signature = query.get("signature", "")
            if _sha1_sorted(token, timestamp, nonce) != signature:
                self._send_text("invalid_signature", 403)
                return
            self._send_text(echostr)
        except Exception as exc:
            self.manager._set_status("wechat_callback", "error", error=str(exc))
            self._send_text(f"error:{exc}", 500)

    def do_POST(self):
        parsed_url = urlparse(self.path)
        settings = self._settings()
        if parsed_url.path != str(settings.get("path") or "/wechat/callback"):
            self._send_text("not_found", 404)
            return
        query = {k: v[-1] if v else "" for k, v in parse_qs(parsed_url.query).items()}
        token = str(settings.get("token") or "")
        body = self._read_body()
        try:
            xml_text = body.decode("utf-8", errors="replace")
            xml_data = _xml_to_dict(xml_text)
            encrypted = xml_data.get("Encrypt", "")
            if encrypted:
                msg_signature = query.get("msg_signature", "")
                expected = _sha1_sorted(token, query.get("timestamp", ""), query.get("nonce", ""), encrypted)
                if msg_signature != expected:
                    self._send_text("invalid_signature", 403)
                    return
                xml_text, receive_id = _decrypt_wechat_xml(encrypted, str(settings.get("encoding_aes_key") or ""))
                configured_receive_id = str(settings.get("receive_id") or "")
                if configured_receive_id and receive_id and receive_id != configured_receive_id:
                    self._send_text("invalid_receive_id", 403)
                    return
                xml_data = _xml_to_dict(xml_text)
            elif query.get("signature") and _sha1_sorted(token, query.get("timestamp", ""), query.get("nonce", "")) != query.get("signature"):
                self._send_text("invalid_signature", 403)
                return

            text = _extract_wechat_text(xml_data)
            if text:
                dispatch_kwargs = {
                    "text": text,
                    "channel": "wechat_callback",
                    "user_name": xml_data.get("FromUserName") or "wechat",
                    "conversation_id": xml_data.get("FromUserName") or "",
                    "metadata": {"raw": xml_data, "provider": settings.get("provider", "official_account")},
                }
                if bool(settings.get("sync_reply", True)):
                    result = self.manager.dispatch_inbound(**dispatch_kwargs)
                    reply = _reply_text_from_result(result)
                    dispatch_result = {"ok": result.get("ok")}
                    if result.get("error"):
                        dispatch_result["error"] = result.get("error")
                    self.manager._set_status(
                        "wechat_callback",
                        "running",
                        last_message_at=time.time(),
                        last_receive_preview=_preview_text(text),
                        last_result=dispatch_result,
                        last_reply_preview=_preview_text(reply),
                        encrypted=bool(encrypted),
                    )
                    if bool(settings.get("auto_reply", True)) and reply:
                        if encrypted:
                            self.manager._set_status(
                                "wechat_callback",
                                "running",
                                last_passive_reply_skipped="encrypted_callback_reply_requires_encrypt",
                            )
                        else:
                            self._send_xml(
                                _wechat_passive_text_xml(
                                    xml_data.get("FromUserName") or "",
                                    xml_data.get("ToUserName") or "",
                                    reply,
                                )
                            )
                            return
                else:
                    threading.Thread(
                        target=self.manager.dispatch_inbound,
                        kwargs=dispatch_kwargs,
                        daemon=True,
                    ).start()
                    self.manager._set_status(
                        "wechat_callback",
                        "running",
                        last_message_at=time.time(),
                        last_receive_preview=_preview_text(text),
                    )
            self._send_text("success")
        except Exception as exc:
            self.manager._set_status("wechat_callback", "error", error=str(exc))
            self._send_text(f"error:{exc}", 500)

    def log_message(self, *args):
        pass


class GatewayLinkManager:
    def __init__(self, qiaojie: Any):
        self.qiaojie = qiaojie
        self.settings = load_link_settings()
        self._lock = threading.RLock()
        self._wechat_server: _WechatHTTPServer | None = None
        self._wechat_thread: threading.Thread | None = None
        self._wechat_direct_thread: threading.Thread | None = None
        self._wechat_direct_stop: threading.Event | None = None
        self._wechat_direct_singleton: Any | None = None
        self._wechat_seen_message_ids: set[str] = set()
        self._wechat_seen_content_keys: dict[str, float] = {}
        self._wechat_context_tokens: dict[str, str] = {}
        self._wechat_typing_tickets: dict[str, dict[str, Any]] = {}
        self._wechat_login_sessions: dict[str, dict[str, Any]] = {}
        self._wechat_latest_attachments: dict[str, list[dict[str, Any]]] = {}
        self._gateway_generation = 0
        self._gateway_session_threads: dict[str, threading.Thread] = {}
        self._gateway_session_generations: dict[str, int] = {}
        self._gateway_session_pending: dict[str, _GatewaySessionEvent] = {}
        self._gateway_session_status: dict[str, dict[str, Any]] = {}
        self._feishu_thread: threading.Thread | None = None
        self._feishu_client: Any = None
        self._status: dict[str, dict[str, Any]] = {
            "wechat_direct": {"state": "disabled"},
            "wechat_callback": {"state": "disabled"},
            "feishu": {"state": "disabled"},
        }

    def start(self):
        self.reload()

    def stop(self):
        with self._lock:
            self._gateway_generation += 1
            self._gateway_session_pending.clear()
            if self._wechat_direct_stop is not None:
                self._wechat_direct_stop.set()
            if self._wechat_server is not None:
                try:
                    self._wechat_server.shutdown()
                    self._wechat_server.server_close()
                except Exception:
                    pass
            self._wechat_server = None
            self._wechat_thread = None
            self._wechat_direct_thread = None
            self._wechat_direct_stop = None
            self._release_wechat_direct_singleton()

    def reload(self, settings: dict[str, Any] | None = None):
        with self._lock:
            self.settings = _normalize_settings(settings or load_link_settings())
            self.stop()
            self._status["wechat_direct"] = {"state": "disabled"}
            self._status["wechat_callback"] = {"state": "disabled"}
            self._status["feishu"] = {"state": "disabled"}

            wechat = self.settings.get("wechat", {})
            direct = wechat.get("direct", {})
            callback = wechat.get("callback", {})
            if wechat.get("enabled") and wechat.get("mode") == "direct_bot" and direct.get("enabled"):
                self._start_wechat_direct(direct)
            if wechat.get("enabled") and wechat.get("mode") == "callback" and callback.get("enabled"):
                self._start_wechat_callback(callback)

            feishu = self.settings.get("feishu", {})
            if feishu.get("enabled"):
                self._start_feishu(feishu)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = save_link_settings(payload)
        self.reload(settings)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "settings": _public_copy(self.settings),
                "config_path": str(CONFIG_PATH),
                "links": deepcopy(self._status),
                "gateway_sessions": self._gateway_sessions_public_locked(),
                "tiangong_openai": {
                    "base_url": f"http://127.0.0.1:{getattr(self.qiaojie, 'duankou', 7174)}/v1",
                    "model": "tiangong-qiyuan",
                    "models_url": f"http://127.0.0.1:{getattr(self.qiaojie, 'duankou', 7174)}/v1/models",
                },
            }

    def action(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        action = str(payload.get("action") or "").strip()
        if action == "wechat_direct_login_start":
            return self._wechat_direct_login_start(payload)
        if action == "wechat_direct_login_wait":
            return self._wechat_direct_login_wait(payload)
        if action == "wechat_direct_start":
            return self._wechat_direct_start()
        if action == "wechat_direct_stop":
            return self._wechat_direct_stop_action()
        if action.startswith("wechat_openclaw"):
            return {"ok": False, "error": "legacy_connector_removed", "message": "当前版本已改为天工造物网关直连微信。"}
        return {"ok": False, "error": f"unknown_action:{action}"}

    def dispatch_inbound(
        self,
        text: str,
        channel: str,
        user_name: str = "",
        conversation_id: str = "",
        metadata: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        clean_text = str(text or "").strip()
        if not clean_text:
            return {"ok": False, "error": "empty_message"}
        clean_attachments = attachments if isinstance(attachments, list) else []
        clean_metadata = metadata or {}
        interim_reply_callback = None
        if isinstance(clean_metadata, dict) and callable(clean_metadata.get("interim_reply_callback")):
            interim_reply_callback = clean_metadata.get("interim_reply_callback")
            clean_metadata = dict(clean_metadata)
            clean_metadata.pop("interim_reply_callback", None)
        if clean_attachments and isinstance(clean_metadata, dict):
            clean_metadata = dict(clean_metadata)
            clean_metadata["attachments"] = clean_attachments
        context = {
            "recent_messages": [],
            "summary": f"inbound channel={channel} conversation={conversation_id}",
            "metadata": clean_metadata,
            "conversation_id": conversation_id or channel,
            "session_id": conversation_id or channel,
        }
        if callable(interim_reply_callback):
            context["interim_reply_callback"] = interim_reply_callback
        request_id = str(clean_metadata.get("request_id") or clean_metadata.get("run_id") or "").strip() if isinstance(clean_metadata, dict) else ""
        if request_id:
            context["request_id"] = request_id
            context["active_id"] = request_id
        if clean_attachments:
            context["attachments"] = clean_attachments
            context["chat_attachments"] = clean_attachments
        try:
            raw = self.qiaojie.chuli_duihua(clean_text, user_name or channel, context)
            data = loads_json_object(raw, source="gateway_inbound_chat")
            return {"ok": not bool(data.get("cuowu")), "data": data}
        except Exception as exc:
            return error_payload(exc, source="gateway_inbound_chat")

    def _remember_wechat_attachments(
        self,
        conversation_id: str,
        to_user_id: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        if not attachments:
            return
        keys = _wechat_attachment_index_keys(conversation_id, to_user_id)
        with self._lock:
            for key in keys:
                if key:
                    self._wechat_latest_attachments[key] = deepcopy(attachments)
            if len(self._wechat_latest_attachments) > 200:
                for key in list(self._wechat_latest_attachments.keys())[:-120]:
                    self._wechat_latest_attachments.pop(key, None)
        try:
            _remember_wechat_attachments_on_disk(keys, attachments)
        except Exception as exc:
            self._set_status(
                "wechat_direct",
                "running",
                last_attachment_index_error=str(exc)[:500],
                last_attachment_index_error_at=time.time(),
            )

    def _lookup_wechat_attachments(self, conversation_id: str, to_user_id: str) -> list[dict[str, Any]]:
        keys = _wechat_attachment_index_keys(conversation_id, to_user_id)
        with self._lock:
            for key in keys:
                items = self._wechat_latest_attachments.get(key)
                if items:
                    return deepcopy(items)
        attachments = _lookup_wechat_attachments_on_disk(keys)
        if attachments:
            with self._lock:
                for key in keys:
                    if key:
                        self._wechat_latest_attachments[key] = deepcopy(attachments)
            return attachments
        return []

    def _wechat_content_seen_recently(self, content_key: str) -> bool:
        if not content_key:
            return False
        now = time.time()
        with self._lock:
            self._wechat_seen_content_keys = {
                key: seen_at
                for key, seen_at in self._wechat_seen_content_keys.items()
                if now - seen_at <= WECHAT_CONTENT_DEDUP_TTL_SECONDS
            }
            previous = self._wechat_seen_content_keys.get(content_key)
            if previous is not None and now - previous <= WECHAT_CONTENT_DEDUP_WINDOW_SECONDS:
                return True
            self._wechat_seen_content_keys[content_key] = now
            return False

    def _set_status(self, key: str, state: str, **extra: Any):
        with self._lock:
            current = dict(self._status.get(key) or {})
            current.update(extra)
            current["state"] = state
            current["updated_at"] = time.time()
            self._status[key] = current

    def _gateway_sessions_public_locked(self) -> dict[str, Any]:
        now = time.time()
        public: dict[str, Any] = {}
        for session_key, status in list(self._gateway_session_status.items())[-50:]:
            item = deepcopy(status)
            thread = self._gateway_session_threads.get(session_key)
            generation = self._gateway_session_generations.get(session_key)
            active = bool(thread and thread.is_alive() and generation == self._gateway_generation)
            item["active"] = active
            item["pending"] = session_key in self._gateway_session_pending
            item["current_generation"] = self._gateway_generation
            item["thread_generation"] = generation
            updated_at = float(item.get("updated_at") or now)
            item["status_age_seconds"] = round(max(0.0, now - updated_at), 3)
            public[session_key] = item
        return public

    def _set_gateway_session_status(self, session_key: str, state: str, **extra: Any) -> None:
        with self._lock:
            current = dict(self._gateway_session_status.get(session_key) or {})
            current.update(extra)
            current["state"] = state
            current["updated_at"] = time.time()
            self._gateway_session_status[session_key] = current

    def _gateway_event_is_current(self, event: _GatewaySessionEvent) -> bool:
        with self._lock:
            return (
                event.generation == self._gateway_generation
                and self._gateway_session_generations.get(event.session_key) == event.generation
            )

    def _gateway_event_has_pending_followup(self, event: _GatewaySessionEvent) -> bool:
        with self._lock:
            pending = self._gateway_session_pending.get(event.session_key)
            return bool(
                pending
                and pending.generation == event.generation
                and self._gateway_session_generations.get(event.session_key) == event.generation
            )

    def _gateway_final_suppressed(self, event: _GatewaySessionEvent) -> bool:
        with self._lock:
            status = self._gateway_session_status.get(event.session_key) or {}
            return str(status.get("suppress_final_request_id") or "") == str(event.run_id or "")

    def _submit_gateway_event(self, event: _GatewaySessionEvent) -> dict[str, Any]:
        if not event.run_id:
            event.run_id = f"gw_{uuid.uuid4().hex}"
        worker: threading.Thread | None = None
        with self._lock:
            event.generation = self._gateway_generation
            current_thread = self._gateway_session_threads.get(event.session_key)
            current_generation = self._gateway_session_generations.get(event.session_key)
            current_active = bool(
                current_thread
                and current_thread.is_alive()
                and current_generation == event.generation
            )
            pending_kind = _classify_gateway_pending_kind(event.text, has_attachments=bool(event.attachments)) if current_active else ""
            active_request_id = str((self._gateway_session_status.get(event.session_key) or {}).get("active_request_id") or "") if current_active else ""

            if current_active:
                pass
            else:
                worker = threading.Thread(
                    target=self._run_gateway_session,
                    args=(event,),
                    daemon=True,
                    name=f"gateway-session-{_safe_path_segment(event.session_key)}",
                )
                self._gateway_session_threads[event.session_key] = worker
                self._gateway_session_generations[event.session_key] = event.generation
                self._gateway_session_status[event.session_key] = {
                    "state": "running",
                    "channel": event.channel,
                    "conversation_id": event.conversation_id,
                    "user_name": event.user_name,
                    "last_receive_preview": _preview_text(event.text),
                    "last_attachment_count": len(event.attachments),
                    "active_request_id": event.run_id,
                    "generation": event.generation,
                    "updated_at": time.time(),
                }

        if current_active:
            if pending_kind == "status_probe" and active_request_id:
                try:
                    status_result = self.qiaojie.run_status(active_request_id)
                except Exception as exc:
                    status_result = {"ok": False, "error": str(exc)}
                immediate_reply = _gateway_run_status_reply(status_result, active_request_id)
                now = time.time()
                min_interval = max(0.0, float(event.direct.get("status_probe_min_interval_seconds") or 8))
                with self._lock:
                    status = dict(self._gateway_session_status.get(event.session_key) or {})
                    still_current = (
                        event.generation == self._gateway_generation
                        and self._gateway_session_generations.get(event.session_key) == event.generation
                        and str(status.get("active_request_id") or "") == active_request_id
                    )
                    if not still_current:
                        return {
                            "ok": False,
                            "queued": False,
                            "session_key": event.session_key,
                            "pending_kind": pending_kind,
                            "active_request_id": active_request_id,
                            "skipped": "stale_active_request",
                        }
                    last_sent_at = float(status.get("last_status_probe_reply_at") or 0.0)
                    last_reply = str(status.get("last_status_probe_reply") or "")
                    rate_limited = bool(last_reply == immediate_reply and last_sent_at and now - last_sent_at < min_interval)
                    status.update(
                        {
                            "state": "running",
                            "channel": event.channel,
                            "conversation_id": event.conversation_id,
                            "user_name": event.user_name,
                            "last_receive_preview": _preview_text(event.text),
                            "pending_kind": pending_kind,
                            "active_request_id": active_request_id,
                            "run_status_result": status_result,
                            "immediate_reply_preview": _preview_text(immediate_reply),
                            "last_status_probe_reply": immediate_reply,
                            "generation": event.generation,
                            "updated_at": now,
                        }
                    )
                    if not rate_limited:
                        status["last_status_probe_reply_at"] = now
                    else:
                        status["last_status_probe_rate_limited_at"] = now
                    self._gateway_session_status[event.session_key] = status
                return {
                    "ok": True,
                    "queued": False,
                    "session_key": event.session_key,
                    "pending_kind": pending_kind,
                    "active_request_id": active_request_id,
                    "immediate_reply": "" if rate_limited else immediate_reply,
                    "immediate_reply_skipped": "rate_limited" if rate_limited else "",
                }

            if pending_kind in {"interrupt", "amend"} and active_request_id:
                try:
                    action = "stop" if pending_kind == "interrupt" else "guide"
                    control_result = self.qiaojie.run_control({
                        "action": action,
                        "request_id": active_request_id,
                        "message": event.text,
                    })
                except Exception as exc:
                    control_result = {"ok": False, "error": str(exc)}
                with self._lock:
                    status = dict(self._gateway_session_status.get(event.session_key) or {})
                    still_current = (
                        event.generation == self._gateway_generation
                        and self._gateway_session_generations.get(event.session_key) == event.generation
                        and str(status.get("active_request_id") or "") == active_request_id
                    )
                    if not still_current:
                        return {
                            "ok": False,
                            "queued": False,
                            "session_key": event.session_key,
                            "pending_kind": pending_kind,
                            "active_request_id": active_request_id,
                            "run_control_result": control_result,
                            "skipped": "stale_active_request",
                        }
                    status.update(
                        {
                            "state": "running",
                            "channel": event.channel,
                            "conversation_id": event.conversation_id,
                            "user_name": event.user_name,
                            "last_receive_preview": _preview_text(event.text),
                            "pending_kind": pending_kind,
                            "active_request_id": active_request_id,
                            "run_control_result": control_result,
                            "generation": event.generation,
                            "updated_at": time.time(),
                        }
                    )
                    if pending_kind == "interrupt" and control_result.get("ok"):
                        status["suppress_final_request_id"] = active_request_id
                        status["suppress_final_requested_at"] = time.time()
                    self._gateway_session_status[event.session_key] = status
                if pending_kind == "interrupt":
                    immediate_reply = (
                        "已收到停止请求，正在中断当前任务。"
                        if control_result.get("ok")
                        else "没能中断当前任务，请稍后再试；如果任务已经完成，我会返回最终结果。"
                    )
                    return {
                        "ok": bool(control_result.get("ok")),
                        "queued": False,
                        "session_key": event.session_key,
                        "pending_kind": pending_kind,
                        "active_request_id": active_request_id,
                        "run_control_result": control_result,
                        "immediate_reply": immediate_reply,
                    }
                if control_result.get("ok"):
                    return {
                        "ok": True,
                        "queued": False,
                        "session_key": event.session_key,
                        "pending_kind": pending_kind,
                        "active_request_id": active_request_id,
                        "run_control_result": control_result,
                        "immediate_reply": "收到，已把这条补充到当前任务。我会继续在关键步骤更新进度。",
                    }

            with self._lock:
                event.generation = self._gateway_generation
                existing = self._gateway_session_pending.get(event.session_key)
                self._gateway_session_pending[event.session_key] = (
                    _merge_gateway_session_events(existing, event) if existing else event
                )
                pending = self._gateway_session_pending[event.session_key]
                self._set_gateway_session_status(
                    event.session_key,
                    "queued",
                    channel=event.channel,
                    conversation_id=event.conversation_id,
                    user_name=event.user_name,
                    last_receive_preview=_preview_text(event.text),
                    pending_preview=_preview_text(pending.text),
                    pending_kind=_classify_gateway_pending_kind(event.text, has_attachments=bool(event.attachments)),
                    pending_merged_count=pending.merged_count,
                    pending_attachment_count=len(pending.attachments),
                    generation=event.generation,
                )
                return {
                    "ok": True,
                    "queued": True,
                    "session_key": event.session_key,
                    "pending_merged_count": pending.merged_count,
                }

        if worker is None:
            return {"ok": False, "queued": False, "session_key": event.session_key, "error": "worker_not_created"}

        try:
            worker.start()
            return {"ok": True, "queued": False, "session_key": event.session_key}
        except Exception as exc:
            with self._lock:
                if self._gateway_session_threads.get(event.session_key) is worker:
                    self._gateway_session_threads.pop(event.session_key, None)
                    self._gateway_session_generations.pop(event.session_key, None)
            self._set_gateway_session_status(event.session_key, "error", error=f"start_failed:{exc}")
            return {"ok": False, "queued": False, "session_key": event.session_key, "error": str(exc)}

    def _run_gateway_session(self, event: _GatewaySessionEvent) -> None:
        current = event
        while True:
            if not self._gateway_event_is_current(current):
                self._set_gateway_session_status(
                    current.session_key,
                    "stale",
                    stale_reason="generation_changed_before_process",
                    generation=current.generation,
                )
                return

            try:
                self._process_gateway_event(current)
            except Exception as exc:
                error_delivery_result: dict[str, Any] = {"ok": False, "skipped": "not_wechat_direct"}
                if current.channel == "wechat_direct" and current.auto_reply and current.to_user_id:
                    try:
                        error_delivery_result = self._send_wechat_direct_text(
                            current.direct,
                            current.to_user_id,
                            "处理过程中出错了，当前任务没有正常完成。请重新发送任务，或补充更具体的要求后再试。",
                            context_token=current.context_token,
                            run_id=current.run_id,
                        )
                    except Exception as send_exc:
                        error_delivery_result = {"ok": False, "error": str(send_exc)}
                self._set_gateway_session_status(
                    current.session_key,
                    "error",
                    error=f"process_failed:{exc}",
                    error_delivery_result=error_delivery_result,
                    generation=current.generation,
                )
                self._set_status(
                    current.channel,
                    "error",
                    error=f"process_failed:{exc}",
                    error_delivery_result=error_delivery_result,
                    last_message_at=time.time(),
                )

            with self._lock:
                pending = self._gateway_session_pending.pop(current.session_key, None)
                if (
                    pending
                    and pending.generation == self._gateway_generation
                    and self._gateway_session_generations.get(current.session_key) == current.generation
                ):
                    self._gateway_session_status[current.session_key] = {
                        **dict(self._gateway_session_status.get(current.session_key) or {}),
                        "state": "running",
                        "draining_pending": True,
                        "pending_merged_count": pending.merged_count,
                        "last_receive_preview": _preview_text(pending.text),
                        "updated_at": time.time(),
                    }
                    current = pending
                    continue

                thread = self._gateway_session_threads.get(current.session_key)
                if thread is threading.current_thread():
                    self._gateway_session_threads.pop(current.session_key, None)
                    self._gateway_session_generations.pop(current.session_key, None)
                status = dict(self._gateway_session_status.get(current.session_key) or {})
                status.update(
                    {
                        "state": "idle",
                        "active": False,
                        "pending": False,
                        "draining_pending": False,
                        "updated_at": time.time(),
                    }
                )
                self._gateway_session_status[current.session_key] = status
                return

    def _process_gateway_event(self, event: _GatewaySessionEvent) -> None:
        self._set_gateway_session_status(
            event.session_key,
            "running",
            channel=event.channel,
            conversation_id=event.conversation_id,
            user_name=event.user_name,
            last_receive_preview=_preview_text(event.text),
            last_attachment_count=len(event.attachments),
            merged_count=event.merged_count,
            active_request_id=event.run_id,
            generation=event.generation,
        )

        typing_stop_event: threading.Event | None = None
        typing_start_result: dict[str, Any] = {"ok": False, "skipped": "disabled"}
        typing_stop_result: dict[str, Any] = {"ok": False, "skipped": "not_started"}
        typing_enabled = (
            event.channel == "wechat_direct"
            and event.auto_reply
            and bool(event.direct.get("typing_indicator", True))
        )
        if typing_enabled:
            try:
                typing_stop_event, typing_start_result = self._start_wechat_direct_typing_feedback(
                    event.direct,
                    event.to_user_id,
                    context_token=event.context_token,
                )
                self._set_status(
                    event.channel,
                    "running",
                    last_typing_result=typing_start_result,
                    last_typing_at=time.time(),
                    last_session_key=event.session_key,
                )
            except Exception as exc:
                typing_start_result = {"ok": False, "error": str(exc)}
                self._set_status(
                    event.channel,
                    "running",
                    last_typing_result=typing_start_result,
                    last_typing_at=time.time(),
                    last_session_key=event.session_key,
                )

        metadata = deepcopy(event.metadata or {})
        metadata["gateway_session_key"] = event.session_key
        metadata["gateway_generation"] = event.generation
        metadata["gateway_received_at"] = event.received_at
        metadata["gateway_merged_count"] = event.merged_count
        metadata["request_id"] = event.run_id
        metadata["run_id"] = event.run_id
        progress_bridge: _WechatDirectRunEventBridge | None = None
        if event.channel == "wechat_direct" and event.auto_reply:
            progress_bridge = _WechatDirectRunEventBridge(self, event)
            progress_bridge.start()

            def _interim_reply_callback(text: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
                if progress_bridge is None:
                    return {"ok": False, "skipped": "progress_bridge_missing"}
                return progress_bridge.emit_interim(text, meta)

            metadata["interim_reply_callback"] = _interim_reply_callback

        try:
            result = self.dispatch_inbound(
                text=event.text,
                channel=event.channel,
                user_name=event.user_name,
                conversation_id=event.conversation_id,
                metadata=metadata,
                attachments=event.attachments,
            )
        finally:
            if progress_bridge is not None:
                progress_bridge.stop()
            if typing_stop_event is not None:
                typing_stop_event.set()
            if typing_enabled and typing_start_result.get("ok"):
                try:
                    typing_stop_result = self._send_wechat_direct_typing(
                        event.direct,
                        event.to_user_id,
                        2,
                        context_token=event.context_token,
                    )
                except Exception as exc:
                    typing_stop_result = {"ok": False, "error": str(exc)}
                self._set_status(
                    event.channel,
                    "running",
                    last_typing_stop_result=typing_stop_result,
                    last_typing_stop_at=time.time(),
                    last_session_key=event.session_key,
                )

        reply = ""
        if isinstance(result.get("data"), dict):
            reply = strip_internal_reply_markers(result["data"].get("huifu") or "")
        dispatch_result: dict[str, Any] = {"ok": result.get("ok")}
        if result.get("error"):
            dispatch_result["error"] = result.get("error")

        has_pending_followup = self._gateway_event_has_pending_followup(event)
        suppress_final = self._gateway_final_suppressed(event)

        if not self._gateway_event_is_current(event):
            delivery_result: dict[str, Any] = {"ok": False, "skipped": "stale_generation"}
        elif suppress_final:
            delivery_result = {"ok": False, "skipped": "stop_requested", "request_id": event.run_id}
        elif event.auto_reply and reply and has_pending_followup:
            delivery_result = {
                "ok": False,
                "skipped": "superseded_by_pending",
                "pending": True,
                "suppressed_reply_chars": len(reply),
            }
        elif event.auto_reply and reply:
            if self._gateway_event_has_pending_followup(event):
                delivery_result = {
                    "ok": False,
                    "skipped": "superseded_by_pending",
                    "pending": True,
                    "suppressed_reply_chars": len(reply),
                }
            elif self._gateway_final_suppressed(event):
                delivery_result = {"ok": False, "skipped": "stop_requested", "request_id": event.run_id}
            else:
                try:
                    delivery_result = self._deliver_gateway_reply(event, reply)
                except Exception as exc:
                    delivery_result = {"ok": False, "error": str(exc), "to_user_id": event.to_user_id}
                    self._set_status(
                        event.channel,
                        "error",
                        error=f"send_failed:{exc}",
                        last_message_at=time.time(),
                        last_session_key=event.session_key,
                    )
        elif event.auto_reply and not dispatch_result.get("ok"):
            fallback_error_reply = "处理过程中出错了，当前任务没有正常完成。请重新发送任务，或补充更具体的要求后再试。"
            try:
                delivery_result = self._deliver_gateway_reply(event, fallback_error_reply)
            except Exception as exc:
                delivery_result = {"ok": False, "error": str(exc), "to_user_id": event.to_user_id}
        elif not event.auto_reply:
            delivery_result = {"ok": False, "skipped": "auto_reply_disabled"}
        else:
            delivery_result = {"ok": False, "skipped": "no_reply"}

        self._set_status(
            event.channel,
            "running",
            last_message_at=time.time(),
            last_session_key=event.session_key,
            last_result=dispatch_result,
            last_reply_preview=_preview_text(reply),
            last_send_result=delivery_result,
            last_pending_followup=has_pending_followup,
            last_suppress_final=suppress_final,
            last_typing_result=typing_start_result,
            last_typing_stop_result=typing_stop_result,
        )
        self._set_gateway_session_status(
            event.session_key,
            "running",
            last_result=dispatch_result,
            last_reply_preview=_preview_text(reply),
            last_delivery_result=delivery_result,
            last_pending_followup=has_pending_followup,
            last_suppress_final=suppress_final,
            last_typing_result=typing_start_result,
            last_typing_stop_result=typing_stop_result,
            processed_at=time.time(),
            generation=event.generation,
        )

    def _delivery_plan_for_reply(self, reply: str) -> dict[str, Any]:
        media_candidates, cleaned = _extract_wechat_media_candidates(reply)
        if media_candidates:
            local_candidates = []
        else:
            local_candidates, cleaned = _extract_wechat_local_file_candidates(cleaned)
        native_files, file_errors = _resolve_wechat_delivery_candidates(media_candidates + local_candidates)
        return {
            "kind": "mixed" if native_files else "text",
            "text": cleaned,
            "text_chars": len(str(cleaned or "")),
            "native_files": native_files,
            "file_errors": file_errors,
            "pipeline": "gateway_delivery_media_v1",
        }

    def _deliver_gateway_reply(self, event: _GatewaySessionEvent, reply: str) -> dict[str, Any]:
        plan = self._delivery_plan_for_reply(reply)
        if event.channel == "wechat_direct":
            text = str(plan.get("text") or "").strip()
            text_result: dict[str, Any]
            if text:
                if _low_information_wechat_text(text):
                    return {
                        "ok": False,
                        "skipped": "low_information_final_reply",
                        "text_preview": _preview_text(text),
                    }
                text_result = self._send_wechat_direct_text(
                    event.direct,
                    event.to_user_id,
                    text,
                    context_token=event.context_token,
                    run_id=event.run_id,
                )
            else:
                text_result = {"ok": True, "skipped": "empty_text_after_media_extraction", "parts": 0}

            file_results: list[dict[str, Any]] = []
            for native_file in plan.get("native_files") or []:
                try:
                    file_result = self._send_wechat_direct_file(
                        event.direct,
                        event.to_user_id,
                        str(native_file.get("path") or ""),
                        context_token=event.context_token,
                        run_id=event.run_id,
                        force_file_attachment=(native_file.get("kind") == "document"),
                    )
                except Exception as exc:
                    file_result = {
                        "ok": False,
                        "path": native_file.get("path"),
                        "name": native_file.get("name"),
                        "error": str(exc),
                    }
                file_results.append(file_result)

            invalid_files = plan.get("file_errors") or []
            failed_files = [item for item in file_results if not item.get("ok")]
            if invalid_files or failed_files:
                lines = ["有文件未能作为微信附件发送："]
                for item in invalid_files[:3]:
                    lines.append(f"- {Path(str(item.get('path') or '')).name or item.get('path')}: {item.get('error')}")
                for item in failed_files[:3]:
                    lines.append(f"- {item.get('name') or Path(str(item.get('path') or '')).name}: {item.get('error')}")
                try:
                    self._send_wechat_direct_text(
                        event.direct,
                        event.to_user_id,
                        "\n".join(lines),
                        context_token=event.context_token,
                        run_id=event.run_id,
                    )
                except Exception:
                    pass

            result = {
                "ok": bool(text_result.get("ok")) and not invalid_files and not failed_files,
                "to_user_id": event.to_user_id,
                "text_result": text_result,
                "file_results": file_results,
                "file_errors": invalid_files,
                "native_files_sent": len([item for item in file_results if item.get("ok")]),
            }
            result["delivery_plan"] = plan
            return result
        return {"ok": False, "skipped": f"unsupported_channel:{event.channel}", "delivery_plan": plan}

    def _release_wechat_direct_singleton(self, expected_handle: Any | None = None) -> None:
        with self._lock:
            handle = self._wechat_direct_singleton
            if expected_handle is not None and handle is not expected_handle:
                return
            self._wechat_direct_singleton = None
        _release_wechat_direct_singleton(handle)

    def _wechat_direct_login_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        direct = self.settings.get("wechat", {}).get("direct", {})
        bot_type = str(payload.get("bot_type") or direct.get("bot_type") or "3").strip()
        session_key = str(payload.get("session_key") or uuid.uuid4()).strip()
        local_tokens = []
        existing_token = str(direct.get("bot_token") or "").strip()
        if existing_token:
            local_tokens.append(existing_token)
        try:
            response = _ilink_post_json(
                WECHAT_ILINK_BASE_URL,
                f"ilink/bot/get_bot_qrcode?bot_type={quote(bot_type)}",
                {"local_token_list": local_tokens[-10:]},
                timeout=20,
            )
            qrcode = str(response.get("qrcode") or "").strip()
            qrcode_url = str(response.get("qrcode_img_content") or response.get("qrcode_url") or "").strip()
            if not qrcode:
                return {"ok": False, "error": "missing_qrcode", "raw": response}
            with self._lock:
                self._wechat_login_sessions[session_key] = {
                    "session_key": session_key,
                    "qrcode": qrcode,
                    "qrcode_url": qrcode_url,
                    "started_at": time.time(),
                    "current_base_url": WECHAT_ILINK_BASE_URL,
                    "bot_type": bot_type,
                }
            self._set_status("wechat_direct", "waiting_login", session_key=session_key, qrcode_url=qrcode_url)
            return {
                "ok": True,
                "session_key": session_key,
                "qrcode_url": qrcode_url,
                "message": "二维码已生成，请用手机微信扫描。",
            }
        except Exception as exc:
            self._set_status("wechat_direct", "error", error=str(exc))
            return {"ok": False, "error": str(exc), "message": "生成微信登录二维码失败。"}

    def _wechat_direct_login_wait(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_key = str(payload.get("session_key") or "").strip()
        verify_code = str(payload.get("verify_code") or "").strip()
        with self._lock:
            if not session_key and self._wechat_login_sessions:
                session_key = next(reversed(self._wechat_login_sessions))
            session = deepcopy(self._wechat_login_sessions.get(session_key) or {})
        if not session:
            return {"ok": False, "connected": False, "error": "no_active_login", "message": "没有进行中的微信登录，请先生成二维码。"}
        if time.time() - float(session.get("started_at") or 0) > WECHAT_LOGIN_TTL_SECONDS:
            with self._lock:
                self._wechat_login_sessions.pop(session_key, None)
            self._set_status("wechat_direct", "login_expired")
            return {"ok": False, "connected": False, "error": "login_expired", "message": "二维码已过期，请重新生成。"}

        endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(str(session.get('qrcode') or ''))}"
        if verify_code:
            endpoint += f"&verify_code={quote(verify_code)}"
        try:
            response = _ilink_get_json(str(session.get("current_base_url") or WECHAT_ILINK_BASE_URL), endpoint, timeout=40)
            status = str(response.get("status") or "wait")
            if status == "scaned_but_redirect" and response.get("redirect_host"):
                new_base = _normalize_url(str(response.get("redirect_host") or ""))
                with self._lock:
                    if session_key in self._wechat_login_sessions:
                        self._wechat_login_sessions[session_key]["current_base_url"] = new_base
                self._set_status("wechat_direct", "waiting_confirm", session_key=session_key)
                return {"ok": True, "connected": False, "status": status, "message": "已扫描，正在切换验证线路。"}
            if status in {"wait", "scaned"}:
                self._set_status("wechat_direct", "waiting_confirm", session_key=session_key)
                return {"ok": True, "connected": False, "status": status, "message": "等待手机微信确认。"}
            if status == "need_verifycode":
                self._set_status("wechat_direct", "need_verifycode", session_key=session_key)
                return {"ok": True, "connected": False, "status": status, "need_verify_code": True, "message": "手机微信需要配对数字，请输入后再确认。"}
            if status in {"expired", "verify_code_blocked"}:
                with self._lock:
                    self._wechat_login_sessions.pop(session_key, None)
                self._set_status("wechat_direct", "login_expired")
                return {"ok": False, "connected": False, "status": status, "message": "二维码已失效，请重新生成。"}
            if status == "binded_redirect":
                with self._lock:
                    self._wechat_login_sessions.pop(session_key, None)
                if str(self.settings.get("wechat", {}).get("direct", {}).get("bot_token") or "").strip():
                    self._wechat_direct_start()
                    return {"ok": True, "connected": True, "already_connected": True, "message": "此微信 Bot 已连接，已启动网关。"}
                self._set_status("wechat_direct", "missing_credentials")
                return {"ok": False, "connected": False, "status": status, "message": "微信提示已绑定，但本机没有可用凭据，请重新生成二维码。"}
            if status == "confirmed":
                account_id = str(response.get("ilink_bot_id") or "").strip()
                bot_token = str(response.get("bot_token") or "").strip()
                if not account_id or not bot_token:
                    return {"ok": False, "connected": False, "error": "missing_credentials", "raw": response, "message": "微信已确认，但没有返回完整凭据。"}
                settings = deepcopy(self.settings)
                settings.setdefault("wechat", {})["enabled"] = True
                settings["wechat"]["mode"] = "direct_bot"
                direct = settings["wechat"].setdefault("direct", {})
                direct.update({
                    "enabled": True,
                    "base_url": _normalize_url(response.get("baseurl") or WECHAT_ILINK_BASE_URL),
                    "bot_type": str(session.get("bot_type") or "3"),
                    "bot_token": bot_token,
                    "account_id": account_id,
                    "user_id": str(response.get("ilink_user_id") or "").strip(),
                    "get_updates_buf": "",
                    "auto_reply": bool(direct.get("auto_reply", True)),
                })
                with self._lock:
                    self._wechat_login_sessions.pop(session_key, None)
                saved = save_link_settings(settings)
                self.reload(saved)
                return {"ok": True, "connected": True, "account_id": account_id, "message": "微信已连接，天工造物网关已启动。"}
            self._set_status("wechat_direct", "waiting_confirm", session_key=session_key, raw_status=status)
            return {"ok": True, "connected": False, "status": status, "message": f"微信返回状态：{status}"}
        except httpx.TimeoutException:
            self._set_status("wechat_direct", "waiting_confirm", session_key=session_key)
            return {"ok": True, "connected": False, "status": "wait", "message": "仍在等待扫码确认。"}
        except Exception as exc:
            self._set_status("wechat_direct", "error", error=str(exc))
            return {"ok": False, "connected": False, "error": str(exc), "message": "确认微信登录状态失败。"}

    def _wechat_direct_start(self) -> dict[str, Any]:
        settings = deepcopy(self.settings)
        settings.setdefault("wechat", {})["enabled"] = True
        settings["wechat"]["mode"] = "direct_bot"
        direct = settings["wechat"].setdefault("direct", {})
        direct["enabled"] = True
        if not str(direct.get("bot_token") or "").strip():
            self._set_status("wechat_direct", "missing_credentials")
            return {"ok": False, "error": "missing_credentials", "message": "请先扫码登录微信 Bot。"}
        saved = save_link_settings(settings)
        self.reload(saved)
        return {"ok": True, "message": "微信直连已启动。", "status": self.status()}

    def _wechat_direct_stop_action(self) -> dict[str, Any]:
        settings = deepcopy(self.settings)
        wechat = settings.setdefault("wechat", {})
        direct = wechat.setdefault("direct", {})
        direct["enabled"] = False
        if wechat.get("mode") == "direct_bot":
            wechat["enabled"] = False
        saved = save_link_settings(settings)
        self.reload(saved)
        return {"ok": True, "message": "微信直连已停止。", "status": self.status()}

    def _start_wechat_direct(self, direct: dict[str, Any]):
        token = str(direct.get("bot_token") or "").strip()
        account_id = str(direct.get("account_id") or "").strip()
        if not token:
            self._set_status("wechat_direct", "missing_credentials")
            return
        if self._wechat_direct_thread and self._wechat_direct_thread.is_alive():
            self._set_status("wechat_direct", "running", account_id=account_id)
            return
        self._release_wechat_direct_singleton()
        singleton = _acquire_wechat_direct_singleton(account_id)
        if singleton is None:
            self._set_status("wechat_direct", "standby", account_id=account_id, reason="another_backend_instance_active")
            return
        self._wechat_direct_singleton = singleton
        stop_event = threading.Event()
        self._wechat_direct_stop = stop_event
        self._set_status("wechat_direct", "starting", account_id=account_id)
        self._wechat_direct_thread = threading.Thread(
            target=self._run_wechat_direct,
            args=(deepcopy(direct), stop_event, singleton),
            daemon=True,
        )
        try:
            self._wechat_direct_thread.start()
        except Exception:
            self._release_wechat_direct_singleton()
            raise

    def _run_wechat_direct(self, direct: dict[str, Any], stop_event: threading.Event, singleton: Any | None = None):
        base_url = _normalize_url(direct.get("base_url") or WECHAT_ILINK_BASE_URL)
        token = str(direct.get("bot_token") or "").strip()
        account_id = str(direct.get("account_id") or "").strip()
        get_updates_buf = str(direct.get("get_updates_buf") or "")
        timeout_ms = max(5000, int(direct.get("long_poll_timeout_ms") or 35000))
        auto_reply = bool(direct.get("auto_reply", True))
        backoff = 1.0

        try:
            _ilink_post_json(base_url, "ilink/bot/msg/notifystart", {"base_info": _ilink_base_info()}, token=token, timeout=10)
        except Exception:
            pass
        self._set_status("wechat_direct", "running", account_id=account_id, base_url=base_url)

        while not stop_event.is_set():
            try:
                response = _ilink_post_json(
                    base_url,
                    "ilink/bot/getupdates",
                    {"get_updates_buf": get_updates_buf, "base_info": _ilink_base_info()},
                    token=token,
                    timeout=(timeout_ms / 1000) + 5,
                )
                ret = response.get("ret", 0)
                if ret not in (0, None):
                    self._set_status("wechat_direct", "error", error=response.get("errmsg") or response.get("errcode") or ret)
                    time.sleep(min(backoff, 30))
                    backoff = min(backoff * 1.8, 30)
                    continue
                backoff = 1.0
                new_buf = str(response.get("get_updates_buf") or get_updates_buf or "")
                buf_changed = bool(new_buf and new_buf != get_updates_buf)
                if buf_changed:
                    get_updates_buf = new_buf
                    self._persist_wechat_direct_buffer(token, get_updates_buf)
                messages = response.get("msgs") if isinstance(response.get("msgs"), list) else []
                poll_extra: dict[str, Any] = {
                    "last_poll_at": time.time(),
                    "last_poll_message_count": len(messages),
                    "last_poll_buf_changed": buf_changed,
                }
                if messages:
                    poll_extra["last_poll_batch_at"] = poll_extra["last_poll_at"]
                for message in messages:
                    if stop_event.is_set():
                        break
                    if isinstance(message, dict):
                        self._handle_wechat_direct_message(message, direct, auto_reply)
                self._set_status("wechat_direct", "running", account_id=account_id, base_url=base_url, **poll_extra)
            except httpx.TimeoutException:
                self._set_status(
                    "wechat_direct",
                    "running",
                    account_id=account_id,
                    base_url=base_url,
                    last_poll_at=time.time(),
                    last_poll_message_count=0,
                )
            except Exception as exc:
                self._set_status("wechat_direct", "error", error=str(exc), account_id=account_id, base_url=base_url)
                time.sleep(min(backoff, 30))
                backoff = min(backoff * 1.8, 30)

        try:
            _ilink_post_json(base_url, "ilink/bot/msg/notifystop", {"base_info": _ilink_base_info()}, token=token, timeout=5)
        except Exception:
            pass
        self._set_status("wechat_direct", "disabled", account_id=account_id)
        self._release_wechat_direct_singleton(singleton)

    def _persist_wechat_direct_buffer(self, token: str, get_updates_buf: str):
        with self._lock:
            current_token = str(self.settings.get("wechat", {}).get("direct", {}).get("bot_token") or "")
            if current_token != token:
                return
            settings = deepcopy(self.settings)
            settings["wechat"]["direct"]["get_updates_buf"] = get_updates_buf
            self.settings = settings
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _wechat_typing_cache_key(self, direct: dict[str, Any], ilink_user_id: str) -> str:
        base_url = _normalize_url(direct.get("base_url") or WECHAT_ILINK_BASE_URL)
        token = str(direct.get("bot_token") or "").strip()
        token_digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:12] if token else "no-token"
        return f"{base_url}|{token_digest}|{ilink_user_id}"

    def _get_wechat_typing_ticket(self, direct: dict[str, Any], ilink_user_id: str, context_token: str = "") -> str:
        ilink_user_id = str(ilink_user_id or "").strip()
        if not ilink_user_id:
            raise RuntimeError("typing_ilink_user_id_missing")
        cache_key = self._wechat_typing_cache_key(direct, ilink_user_id)
        now = time.time()
        with self._lock:
            cached = dict(self._wechat_typing_tickets.get(cache_key) or {})
            ticket = str(cached.get("ticket") or "").strip()
            if ticket and float(cached.get("expires_at") or 0) > now + 60:
                return ticket

        base_url = _normalize_url(direct.get("base_url") or WECHAT_ILINK_BASE_URL)
        token = str(direct.get("bot_token") or "").strip()
        body: dict[str, Any] = {
            "ilink_user_id": ilink_user_id,
            "base_info": _ilink_base_info(),
        }
        if context_token:
            body["context_token"] = context_token
        response = _ilink_post_json(base_url, "ilink/bot/getconfig", body, token=token, timeout=10)
        if response.get("ret") not in (0, None):
            raise RuntimeError(response.get("errmsg") or response.get("ret") or "typing_getconfig_failed")
        ticket = str(response.get("typing_ticket") or "").strip()
        if not ticket and isinstance(response.get("data"), dict):
            ticket = str(response["data"].get("typing_ticket") or "").strip()
        if not ticket:
            raise RuntimeError("typing_ticket_missing")
        with self._lock:
            self._wechat_typing_tickets[cache_key] = {
                "ticket": ticket,
                "expires_at": now + 20 * 60 * 60,
            }
        return ticket

    def _send_wechat_direct_typing(
        self,
        direct: dict[str, Any],
        to_user_id: str,
        command: int,
        context_token: str = "",
    ) -> dict[str, Any]:
        base_url = _normalize_url(direct.get("base_url") or WECHAT_ILINK_BASE_URL)
        token = str(direct.get("bot_token") or "").strip()
        ilink_user_id = str(to_user_id or direct.get("user_id") or "").strip()
        ticket = self._get_wechat_typing_ticket(direct, ilink_user_id, context_token=context_token)
        body = {
            "ilink_user_id": ilink_user_id,
            "to_user_id": str(to_user_id or ilink_user_id),
            "typing_ticket": ticket,
            "command": int(command),
            "status": int(command),
            "base_info": _ilink_base_info(),
        }
        response = _ilink_post_json(base_url, "ilink/bot/sendtyping", body, token=token, timeout=10)
        if response.get("ret") not in (0, None):
            cache_key = self._wechat_typing_cache_key(direct, ilink_user_id)
            with self._lock:
                self._wechat_typing_tickets.pop(cache_key, None)
            raise RuntimeError(response.get("errmsg") or response.get("ret") or "sendtyping_failed")
        return {
            "ok": True,
            "to_user_id": str(to_user_id or ""),
            "command": int(command),
            "http_status": response.get("_http_status"),
            "response": _compact_response(response),
        }

    def _start_wechat_direct_typing_feedback(
        self,
        direct: dict[str, Any],
        to_user_id: str,
        context_token: str = "",
    ) -> tuple[threading.Event | None, dict[str, Any]]:
        start_result = self._send_wechat_direct_typing(direct, to_user_id, 1, context_token=context_token)
        stop_event = threading.Event()
        refresh_seconds = max(3, int(direct.get("typing_refresh_seconds") or 5))

        def refresh_loop():
            while not stop_event.wait(refresh_seconds):
                try:
                    self._send_wechat_direct_typing(direct, to_user_id, 1, context_token=context_token)
                except Exception as exc:
                    self._set_status(
                        "wechat_direct",
                        "running",
                        last_typing_refresh_error=str(exc),
                        last_typing_refresh_error_at=time.time(),
                    )
                    return

        threading.Thread(target=refresh_loop, daemon=True).start()
        return stop_event, start_result

    def _handle_wechat_direct_message(self, message: dict[str, Any], direct: dict[str, Any], auto_reply: bool):
        if message.get("message_type") not in (1, "1"):
            self._set_status(
                "wechat_direct",
                "running",
                last_ignored_at=time.time(),
                last_ignored_reason="unsupported_message_type",
                last_ignored_message_type=str(message.get("message_type") or ""),
                last_ignored_message_id=str(message.get("message_id") or message.get("seq") or message.get("client_id") or ""),
            )
            return
        message_key = str(message.get("message_id") or message.get("seq") or message.get("client_id") or "")
        if message_key:
            with self._lock:
                if message_key in self._wechat_seen_message_ids:
                    self._set_status(
                        "wechat_direct",
                        "running",
                        last_ignored_at=time.time(),
                        last_ignored_reason="duplicate_message",
                        last_ignored_message_id=message_key,
                    )
                    return
                self._wechat_seen_message_ids.add(message_key)
                if len(self._wechat_seen_message_ids) > 1000:
                    self._wechat_seen_message_ids = set(list(self._wechat_seen_message_ids)[-500:])
        account_id = str(direct.get("account_id") or "").strip()
        configured_user_id = str(direct.get("user_id") or "").strip()
        to_user_id = str(message.get("from_user_id") or "").strip()
        if account_id and to_user_id == account_id:
            self._set_status(
                "wechat_direct",
                "running",
                last_ignored_at=time.time(),
                last_ignored_reason="self_message",
                last_ignored_message_id=message_key,
                last_receive_user_id=to_user_id,
            )
            return
        if configured_user_id and to_user_id and to_user_id != configured_user_id:
            self._set_status(
                "wechat_direct",
                "running",
                last_ignored_at=time.time(),
                last_ignored_reason="unexpected_sender",
                last_ignored_message_id=message_key,
                last_receive_user_id=to_user_id,
                expected_user_id=configured_user_id,
            )
            return
        conversation_id = str(message.get("session_id") or message.get("group_id") or to_user_id)
        text = _extract_ilink_text(message)
        attachments = _collect_ilink_attachments(message, direct, conversation_id) if to_user_id else []
        if to_user_id:
            existing_available_names = {
                str(item.get("name") or "").lower()
                for item in attachments
                if isinstance(item, dict) and item.get("status") == "available"
            }
            for placeholder_name in _extract_wechat_file_placeholders(text):
                if placeholder_name.lower() not in existing_available_names:
                    attachment = _resolve_local_file_placeholder(placeholder_name, message, direct, conversation_id)
                    attachments.append(attachment)
                    if attachment.get("status") == "available":
                        existing_available_names.add(placeholder_name.lower())
            if not attachments and _is_wechat_attachment_followup(text):
                attachments = self._lookup_wechat_attachments(conversation_id, to_user_id)
                if not attachments:
                    attachments = _latest_cached_wechat_attachments(conversation_id, to_user_id)
                if not attachments:
                    history_name = _latest_file_placeholder_from_events(conversation_id)
                    if history_name:
                        attachments = [_resolve_local_file_placeholder(history_name, message, direct, conversation_id)]
        if attachments:
            self._remember_wechat_attachments(conversation_id, to_user_id, attachments)
        if not text and attachments:
            text = _text_from_attachments(attachments)
        persist_user_message = text
        attachment_context_note = _wechat_attachment_context_note(attachments) if attachments else ""
        incoming_context_token = str(message.get("context_token") or "").strip()
        context_token = incoming_context_token
        context_token_source = "message"
        if to_user_id:
            with self._lock:
                if incoming_context_token:
                    self._wechat_context_tokens[to_user_id] = incoming_context_token
                else:
                    context_token = self._wechat_context_tokens.get(to_user_id, "")
                    context_token_source = "cache" if context_token else "missing"
            if incoming_context_token:
                try:
                    _remember_wechat_context_token_on_disk(account_id, to_user_id, incoming_context_token)
                except Exception as exc:
                    self._set_status(
                        "wechat_direct",
                        "running",
                        last_context_token_store_error=str(exc)[:500],
                        last_context_token_store_error_at=time.time(),
                    )
            elif not context_token:
                context_token = _lookup_wechat_context_token_on_disk(account_id, to_user_id)
                if context_token:
                    context_token_source = "disk"
                    with self._lock:
                        self._wechat_context_tokens[to_user_id] = context_token
        if (not text and not attachments) or not to_user_id:
            self._set_status(
                "wechat_direct",
                "running",
                last_ignored_at=time.time(),
                last_ignored_reason="empty_text_or_user",
                last_ignored_message_id=message_key,
                last_receive_preview=_preview_text(text),
                last_receive_user_id=to_user_id,
                last_attachment_count=len(attachments),
            )
            return
        session_key = _gateway_session_key("wechat_direct", conversation_id, to_user_id)
        pending_kind_for_dedup = _classify_gateway_pending_kind(text, has_attachments=bool(attachments))
        with self._lock:
            current_thread = self._gateway_session_threads.get(session_key)
            current_generation = self._gateway_session_generations.get(session_key)
            current_active_for_dedup = bool(
                current_thread
                and current_thread.is_alive()
                and current_generation == self._gateway_generation
            )
        skip_content_dedup = pending_kind_for_dedup in {"status_probe", "interrupt", "amend"} and current_active_for_dedup
        content_fingerprint_parts = [to_user_id, text]
        for item in attachments:
            if isinstance(item, dict) and item.get("status") == "available":
                content_fingerprint_parts.append(str(item.get("path") or item.get("name") or ""))
        content_key = hashlib.sha256("\n".join(content_fingerprint_parts).encode("utf-8", errors="ignore")).hexdigest()
        if not skip_content_dedup and self._wechat_content_seen_recently(content_key):
            self._set_status(
                "wechat_direct",
                "running",
                last_ignored_at=time.time(),
                last_ignored_reason="duplicate_content",
                last_ignored_message_id=message_key,
                last_receive_preview=_preview_text(text),
                last_receive_user_id=to_user_id,
                last_attachment_count=len(attachments),
            )
            return
        received_at = time.time()
        available_attachments = [item for item in attachments if isinstance(item, dict) and item.get("status") == "available"]
        attachment_errors = [item for item in attachments if isinstance(item, dict) and item.get("status") != "available"]
        self._set_status(
            "wechat_direct",
            "running",
            last_receive_at=received_at,
            last_receive_preview=_preview_text(text),
            last_receive_user_id=to_user_id,
            last_receive_message_id=message_key,
            last_receive_context_token_present=bool(context_token),
            last_receive_context_token_source=context_token_source,
            last_attachment_count=len(attachments),
            last_attachment_paths=[item.get("path") for item in available_attachments[:WECHAT_ATTACHMENT_CONTEXT_LIMIT] if item.get("path")],
            last_attachment_source_paths=[item.get("source_path") for item in available_attachments[:WECHAT_ATTACHMENT_CONTEXT_LIMIT] if item.get("source_path")],
            last_attachment_resolved_by=[item.get("resolved_by") for item in available_attachments[:WECHAT_ATTACHMENT_CONTEXT_LIMIT] if item.get("resolved_by")],
            last_attachment_errors=[{"name": item.get("name"), "error": item.get("error")} for item in attachment_errors[:5]],
        )
        metadata = {
            "raw": message,
            "session_id": message.get("session_id") or "",
            "group_id": message.get("group_id") or "",
            "message_id": message.get("message_id") or "",
            "attachments": attachments,
            "attachment_context": attachment_context_note,
            "persist_user_message": persist_user_message,
            "current_user_text": persist_user_message,
        }
        event = _GatewaySessionEvent(
            channel="wechat_direct",
            session_key=session_key,
            user_name=to_user_id,
            conversation_id=conversation_id,
            text=text,
            metadata=metadata,
            attachments=attachments,
            direct=deepcopy(direct),
            auto_reply=auto_reply,
            to_user_id=to_user_id,
            context_token=context_token,
            context_token_source=context_token_source,
            message_key=message_key,
            run_id=str(message.get("run_id") or ""),
            received_at=received_at,
        )
        submit_result = self._submit_gateway_event(event)
        immediate_reply = str((submit_result or {}).get("immediate_reply") or "").strip()
        if immediate_reply and auto_reply:
            try:
                immediate_send_result = self._send_wechat_direct_text(
                    direct,
                    to_user_id,
                    immediate_reply,
                    context_token=context_token,
                    run_id=str((submit_result or {}).get("active_request_id") or event.run_id),
                )
            except Exception as exc:
                immediate_send_result = {"ok": False, "error": str(exc)}
            submit_result = {
                **(submit_result or {}),
                "immediate_send_result": immediate_send_result,
            }
        self._set_status(
            "wechat_direct",
            "running",
            last_receive_at=received_at,
            last_session_key=session_key,
            last_submit_result=submit_result,
            last_receive_preview=_preview_text(text),
            last_receive_user_id=to_user_id,
            last_receive_message_id=message_key,
            last_receive_context_token_present=bool(context_token),
            last_receive_context_token_source=context_token_source,
            last_attachment_count=len(attachments),
        )

    def _send_wechat_direct_text(self, direct: dict[str, Any], to_user_id: str, text: str, context_token: str = "", run_id: str = ""):
        base_url = _normalize_url(direct.get("base_url") or WECHAT_ILINK_BASE_URL)
        token = str(direct.get("bot_token") or "").strip()
        account_id = str(direct.get("account_id") or "").strip()
        context_token = str(context_token or "").strip()
        context_token_cleared = False
        sent_parts: list[dict[str, Any]] = []
        for index, part in enumerate(_split_text(text), start=1):
            client_id = f"tiangong-wechat-{uuid.uuid4().hex}"
            chunk_context_token = context_token
            retried_without_context_token = False
            rate_limit_retries = 0
            while True:
                body = _build_ilink_text_message(to_user_id, part, chunk_context_token, run_id, client_id=client_id)
                body["base_info"] = _ilink_base_info()
                response = _ilink_post_json(base_url, "ilink/bot/sendmessage", body, token=token, timeout=15)
                if _is_ilink_success_response(response):
                    break
                if _is_ilink_session_expired_response(response) and chunk_context_token:
                    retried_without_context_token = True
                    context_token_cleared = True
                    chunk_context_token = ""
                    context_token = ""
                    with self._lock:
                        self._wechat_context_tokens.pop(to_user_id, None)
                    try:
                        _forget_wechat_context_token_on_disk(account_id, to_user_id)
                    except Exception as exc:
                        self._set_status(
                            "wechat_direct",
                            "running",
                            last_context_token_clear_error=str(exc)[:500],
                            last_context_token_clear_error_at=time.time(),
                        )
                    continue
                if _is_ilink_rate_limited_response(response) and rate_limit_retries < WECHAT_SEND_RATE_LIMIT_RETRIES:
                    rate_limit_retries += 1
                    time.sleep(WECHAT_SEND_RATE_LIMIT_DELAY_SECONDS * rate_limit_retries)
                    continue
                raise RuntimeError(
                    _ilink_response_message(response)
                    or response.get("ret")
                    or response.get("errcode")
                    or "sendmessage_failed"
                )
            sent_parts.append({
                "index": index,
                "chars": len(part),
                "client_id": client_id,
                "ret": response.get("ret"),
                "errcode": response.get("errcode"),
                "http_status": response.get("_http_status"),
                "context_token_used": bool(chunk_context_token),
                "retried_without_context_token": retried_without_context_token,
                "rate_limit_retries": rate_limit_retries,
                "response_keys": sorted(str(key) for key in response.keys())[:8],
                "response": _compact_response(response),
            })
        return {
            "ok": True,
            "to_user_id": to_user_id,
            "context_token_present": bool(str(context_token or "").strip()),
            "context_token_cleared": context_token_cleared,
            "parts": len(sent_parts),
            "last_client_id": sent_parts[-1]["client_id"] if sent_parts else "",
            "last_http_status": sent_parts[-1].get("http_status") if sent_parts else None,
            "last_response": sent_parts[-1]["response"] if sent_parts else {},
            "last_response_keys": sent_parts[-1]["response_keys"] if sent_parts else [],
        }

    def _send_wechat_direct_file(
        self,
        direct: dict[str, Any],
        to_user_id: str,
        file_path: str,
        *,
        context_token: str = "",
        run_id: str = "",
        force_file_attachment: bool = False,
    ) -> dict[str, Any]:
        base_url = _normalize_url(direct.get("base_url") or WECHAT_ILINK_BASE_URL)
        cdn_base_url = _normalize_url(direct.get("cdn_base_url") or WECHAT_CDN_BASE_URL, WECHAT_CDN_BASE_URL)
        token = str(direct.get("bot_token") or "").strip()
        account_id = str(direct.get("account_id") or "").strip()
        context_token = str(context_token or "").strip()
        if not token:
            raise RuntimeError("wechat_bot_token_missing")

        path = Path(str(file_path or "")).expanduser().resolve(strict=True)
        if not path.is_file():
            raise RuntimeError(f"not_file:{path}")
        plaintext = path.read_bytes()
        max_bytes = int(direct.get("max_attachment_bytes") or WECHAT_MAX_ATTACHMENT_BYTES)
        if len(plaintext) > max_bytes:
            raise RuntimeError(f"file_too_large:{len(plaintext)}>{max_bytes}")

        filekey = uuid.uuid4().hex
        aes_key = os.urandom(16)
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        media_type = _wechat_outbound_media_type(str(path), force_file_attachment=force_file_attachment)
        upload_response = _get_wechat_upload_url(
            base_url,
            token=token,
            to_user_id=to_user_id,
            media_type=media_type,
            filekey=filekey,
            rawsize=rawsize,
            rawfilemd5=rawfilemd5,
            filesize=_aes_padded_size(rawsize),
            aeskey_hex=aes_key.hex(),
        )
        if not _is_ilink_success_response(upload_response):
            raise RuntimeError(f"getuploadurl_failed:{_ilink_response_message(upload_response) or _compact_response(upload_response)}")
        upload_payload = upload_response.get("data") if isinstance(upload_response.get("data"), dict) else upload_response
        upload_param = str(upload_payload.get("upload_param") or "")
        upload_full_url = str(upload_payload.get("upload_full_url") or "")
        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = _wechat_cdn_upload_url(cdn_base_url, upload_param, filekey)
        else:
            raise RuntimeError(f"getuploadurl_missing_upload_url:{_compact_response(upload_response)}")

        ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)
        encrypted_query_param = _upload_wechat_ciphertext(upload_url, ciphertext)
        aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")
        _, media_item = _build_wechat_outbound_media_item(
            str(path),
            encrypt_query_param=encrypted_query_param,
            aes_key_for_api=aes_key_for_api,
            ciphertext_size=len(ciphertext),
            plaintext_size=rawsize,
            filename=path.name,
            rawfilemd5=rawfilemd5,
            force_file_attachment=force_file_attachment,
        )

        client_id = f"tiangong-wechat-{uuid.uuid4().hex}"
        chunk_context_token = context_token
        retried_without_context_token = False
        rate_limit_retries = 0
        while True:
            body = _build_ilink_media_message(
                to_user_id,
                media_item,
                context_token=chunk_context_token,
                run_id=run_id,
                client_id=client_id,
            )
            response = _ilink_post_json(base_url, "ilink/bot/sendmessage", body, token=token, timeout=30)
            if _is_ilink_success_response(response):
                break
            if _is_ilink_session_expired_response(response) and chunk_context_token:
                retried_without_context_token = True
                chunk_context_token = ""
                context_token = ""
                with self._lock:
                    self._wechat_context_tokens.pop(to_user_id, None)
                try:
                    _forget_wechat_context_token_on_disk(account_id, to_user_id)
                except Exception as exc:
                    self._set_status(
                        "wechat_direct",
                        "running",
                        last_context_token_clear_error=str(exc)[:500],
                        last_context_token_clear_error_at=time.time(),
                    )
                continue
            if _is_ilink_rate_limited_response(response) and rate_limit_retries < WECHAT_SEND_RATE_LIMIT_RETRIES:
                rate_limit_retries += 1
                time.sleep(WECHAT_SEND_RATE_LIMIT_DELAY_SECONDS * rate_limit_retries)
                continue
            raise RuntimeError(
                _ilink_response_message(response)
                or response.get("ret")
                or response.get("errcode")
                or "sendmedia_failed"
            )

        return {
            "ok": True,
            "to_user_id": to_user_id,
            "path": str(path),
            "name": path.name,
            "bytes": rawsize,
            "md5": rawfilemd5,
            "media_type": media_type,
            "client_id": client_id,
            "context_token_used": bool(chunk_context_token),
            "retried_without_context_token": retried_without_context_token,
            "rate_limit_retries": rate_limit_retries,
            "last_http_status": response.get("_http_status"),
            "last_response": _compact_response(response),
        }

    def _start_wechat_callback(self, callback: dict[str, Any]):
        host = str(callback.get("host") or "127.0.0.1")
        port = int(callback.get("port") or 7188)
        path = str(callback.get("path") or "/wechat/callback")

        manager = self

        class Handler(_WeChatCallbackHandler):
            pass

        Handler.manager = manager
        try:
            server = _WechatHTTPServer((host, port), Handler)
            self._wechat_server = server
            self._wechat_thread = threading.Thread(target=server.serve_forever, daemon=True)
            self._wechat_thread.start()
            self._set_status(
                "wechat_callback",
                "running",
                host=host,
                port=port,
                path=path,
                callback_url=f"http://{host}:{port}{path}",
            )
        except Exception as exc:
            self._set_status("wechat_callback", "error", error=str(exc), host=host, port=port, path=path)

    def _start_feishu(self, feishu: dict[str, Any]):
        app_id = str(feishu.get("app_id") or "").strip()
        app_secret = str(feishu.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            self._set_status("feishu", "missing_credentials")
            return
        if self._feishu_thread and self._feishu_thread.is_alive():
            self._set_status("feishu", "running")
            return

        self._set_status("feishu", "starting")
        self._feishu_thread = threading.Thread(target=self._run_feishu, args=(deepcopy(feishu),), daemon=True)
        self._feishu_thread.start()

    def _run_feishu(self, feishu: dict[str, Any]):
        try:
            import lark_oapi as lark

            def on_message(data: Any):
                text, metadata = _extract_feishu_text(data)
                if text:
                    result = self.dispatch_inbound(
                        text=text,
                        channel="feishu",
                        user_name=metadata.get("open_id") or metadata.get("user_id") or "feishu",
                        conversation_id=metadata.get("chat_id") or metadata.get("thread_id") or "",
                        metadata=metadata,
                    )
                    self._set_status("feishu", "running", last_message_at=time.time(), last_result=result)

            handler = (
                lark.EventDispatcherHandler
                .builder(str(feishu.get("encrypt_key") or ""), str(feishu.get("verification_token") or ""))
                .register_p2_im_message_receive_v1(on_message)
                .build()
            )
            self._feishu_client = lark.ws.Client(
                str(feishu.get("app_id") or ""),
                str(feishu.get("app_secret") or ""),
                event_handler=handler,
                log_level=lark.LogLevel.INFO,
            )
            self._set_status("feishu", "running")
            self._feishu_client.start()
        except ImportError as exc:
            self._set_status("feishu", "missing_dependency", error=str(exc))
        except Exception as exc:
            self._set_status("feishu", "error", error=str(exc))
