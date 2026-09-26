from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from tiangong_kernel.l0_primitives.context import ContextKind
    from tiangong_kernel.l0_primitives.event import EventState, EventType
    from tiangong_kernel.l0_primitives.memory import MemoryKind, MemoryState
    from tiangong_kernel.l0_primitives.message import MessageRole, MessageState
    from tiangong_kernel.l0_primitives.retrieval import RetrievalKind
except Exception:  # pragma: no cover - packaged runtime may not expose the kernel during isolated tests.
    ContextKind = EventState = EventType = MemoryKind = MemoryState = MessageRole = MessageState = RetrievalKind = None


DUIHUA_SHIJIAN_ROOT = Path.home() / ".tiangong" / "v3" / "duihua_shijian"
MAX_SHIJIAN_FILE_BYTES = 4 * 1024 * 1024
MAX_XIAOXI_CHARS = 6000
MAX_RECENT_XIAOXI = 80

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_LOCAL_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s`'\"<>|，。；;、,!?！？]+")
_QUOTED_LOCAL_PATH_RE = re.compile(r"[`'\"]([A-Za-z]:[\\/][^`'\"<>|]+)[`'\"]")
_PATH_TRAILING_CHARS = "，。；;、,.!?！？:：)]}）】》\"'"
_WECHAT_ATTACHMENT_CONTEXT_PREFIX = "[微信附件上下文]"
_MEDIA_HISTORY_LINE_RE = re.compile(r"(?im)^\s*MEDIA:\s*(?P<path>.+?)\s*$")
_DELIVERABLE_FILE_SUFFIXES = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".py",
    ".rar",
    ".txt",
    ".wav",
    ".xls",
    ".xlsx",
    ".zip",
}


def _enum_value(enum_obj: Any, name: str, fallback: str) -> str:
    try:
        return str(getattr(enum_obj, name).value)
    except Exception:
        return fallback


def _ref(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _short_text(value: Any, limit: int = MAX_XIAOXI_CHARS) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[已截断]"


def _clean_local_path(path_text: str) -> str:
    text = str(path_text or "").strip().strip("`")
    for marker in ("，并", "。并", "；并", ";并", "，且", "，然后", "。然后"):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
    return text.rstrip(_PATH_TRAILING_CHARS)


def _strip_wechat_attachment_context(text: str) -> str:
    clean = str(text or "").strip()
    if not clean.startswith(_WECHAT_ATTACHMENT_CONTEXT_PREFIX):
        return clean
    if "\n\n" in clean:
        return clean.split("\n\n", 1)[1].strip()
    return ""


def _normalize_history_path(path_text: str) -> str:
    path = _clean_local_path(path_text)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:].replace("/", "\\")
    return path


def _extract_media_history_paths(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in _MEDIA_HISTORY_LINE_RE.finditer(str(text or "")):
        path = _normalize_history_path(match.group("path"))
        key = path.replace("\\", "/").rstrip("/").lower()
        if path and key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _strip_media_history_lines(text: str) -> str:
    clean = _MEDIA_HISTORY_LINE_RE.sub("", str(text or ""))
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def _attachment_cards_from_wechat_context(text: str) -> list[str]:
    raw = str(text or "")
    if not raw.strip().startswith(_WECHAT_ATTACHMENT_CONTEXT_PREFIX):
        return []
    cards: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        fields: dict[str, str] = {}
        for part in line[2:].split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()
        path = fields.get("path") or ""
        name = fields.get("name") or (Path(path).name if path else "")
        if not path and not name:
            continue
        bits = [f"name={name}" if name else "", f"path={path}" if path else ""]
        if fields.get("size"):
            bits.append(f"size={fields['size']}")
        if fields.get("sha256"):
            bits.append(f"sha256={fields['sha256']}")
        cards.append("- received_file: " + "; ".join(bit for bit in bits if bit))
    return cards[:5]


def _history_user_content(raw_content: str) -> str:
    clean = _strip_wechat_attachment_context(raw_content)
    cards = _attachment_cards_from_wechat_context(raw_content)
    if cards:
        return (clean + "\n[文件卡]\n" + "\n".join(cards)).strip()
    return clean


def _history_assistant_content(raw_content: str) -> str:
    media_paths = _extract_media_history_paths(raw_content)
    clean = _strip_media_history_lines(raw_content)
    mentioned_paths = [path for path in _extract_local_paths(clean) if path not in media_paths]
    status = "attachment_reference" if media_paths else "reply"
    lines: list[str] = []
    if clean:
        lines.append("聊天回复: " + _short_text(" ".join(clean.split()), 900))
    result_lines = [f"- task_status={status}"]
    for path in media_paths[:5]:
        result_lines.append(f"- delivered_file={path}")
    for path in mentioned_paths[:5]:
        result_lines.append(f"- mentioned_path={path}")
    if len(result_lines) > 1 or status == "completed":
        lines.append("[结果卡]\n" + "\n".join(result_lines))
    return "\n".join(lines).strip()


def _extract_local_paths(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for pattern in (_QUOTED_LOCAL_PATH_RE, _LOCAL_PATH_RE):
        for match in pattern.finditer(str(text or "")):
            raw = _clean_local_path(match.group(1) if pattern is _QUOTED_LOCAL_PATH_RE else match.group(0))
            key = raw.replace("\\", "/").rstrip("/").lower()
            if raw and key not in seen:
                seen.add(key)
                out.append(raw)
    return out


def _path_for_followup_task(path_text: str, xiaoxi: str) -> str:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return ""


def _recent_followup_path(messages: Sequence[Mapping[str, Any]], xiaoxi: str) -> str:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return ""


def _path_is_existing_deliverable_file(path_text: str) -> bool:
    try:
        path = Path(_clean_local_path(path_text)).expanduser()
    except Exception:
        return False
    if path.suffix.lower() not in _DELIVERABLE_FILE_SUFFIXES:
        return False
    try:
        return path.is_file()
    except Exception:
        return False


def _is_file_resend_followup(xiaoxi: str) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def _direct_media_resend_reply(path_text: str) -> str:
    path = _clean_local_path(path_text)
    name = Path(path).name or path
    return f"公子，已按原文件附件再发一遍：{name}\n\nMEDIA:{path}"


def _content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _safe_huihua_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return "default"
    text = _SAFE_ID_RE.sub("_", text)[:96].strip("._-")
    return text or "default"


def _message_huihua_id(raw: Mapping[str, Any]) -> str:
    for key in (
        "session_id",
        "sessionId",
        "active_session_id",
        "activeSessionId",
        "conversation_id",
        "conversationId",
        "thread_id",
        "duihua_id",
        "chat_id",
    ):
        value = raw.get(key)
        if value:
            return _safe_huihua_id(value)
    return ""


def _strict_session_context(conversation_context: Mapping[str, Any]) -> bool:
    policy = str(
        conversation_context.get("short_context_policy")
        or conversation_context.get("context_scope")
        or conversation_context.get("conversation_scope")
        or ""
    ).strip().lower()
    if policy in {"session", "session_only", "conversation", "conversation_only"}:
        return True
    if conversation_context.get("session_isolation") is True:
        return True
    return False


def _message_allowed_for_huihua(raw: Mapping[str, Any], huihua_id: str, *, strict: bool) -> bool:
    if not strict:
        return True
    message_huihua_id = _message_huihua_id(raw)
    if not message_huihua_id:
        return False
    return message_huihua_id == _safe_huihua_id(huihua_id)


def queding_huihua_id(conversation_context: Optional[Mapping[str, Any]], fallback_request_id: str = "") -> str:
    ctx = conversation_context or {}
    for key in (
        "session_id",
        "active_session_id",
        "activeSessionId",
        "conversation_id",
        "thread_id",
        "duihua_id",
        "chat_id",
    ):
        value = ctx.get(key)
        if value:
            return _safe_huihua_id(value)
    return _safe_huihua_id(fallback_request_id or ctx.get("request_id") or ctx.get("active_id") or "default")


def _huihua_dir(huihua_id: str) -> Path:
    return DUIHUA_SHIJIAN_ROOT / _safe_huihua_id(huihua_id)


def _events_path(huihua_id: str) -> Path:
    return _huihua_dir(huihua_id) / "events.jsonl"


def _deleted_path(huihua_id: str) -> Path:
    return _huihua_dir(huihua_id) / "deleted.tombstone.json"


def _compact_message(raw: Mapping[str, Any], huihua_id: str = "", *, strict_session: bool = False) -> Dict[str, Any]:
    if huihua_id and not _message_allowed_for_huihua(raw, huihua_id, strict=strict_session):
        return {}
    role = str(raw.get("role") or raw.get("message_role") or raw.get("sender") or "").strip().lower()
    if role in ("human", "me"):
        role = "user"
    if role in ("ai", "bot"):
        role = "assistant"
    content = _short_text(_strip_wechat_attachment_context(raw.get("content") or raw.get("text") or raw.get("message") or ""))
    if not content:
        return {}
    item: Dict[str, Any] = {
        "role": role or "unknown",
        "content": content,
    }
    at = raw.get("at") or raw.get("created_at") or raw.get("time") or raw.get("timestamp") or raw.get("createdAt")
    if at:
        item["at"] = at
    request_id = raw.get("request_id") or raw.get("requestId")
    if request_id:
        item["request_id"] = request_id
    message_huihua_id = _message_huihua_id(raw) or (_safe_huihua_id(huihua_id) if huihua_id else "")
    if message_huihua_id:
        item["session_id"] = message_huihua_id
        item["conversation_id"] = message_huihua_id
    return item


def _recent_from_context(conversation_context: Mapping[str, Any], huihua_id: str = "") -> List[Dict[str, Any]]:
    raw = (
        conversation_context.get("recent_messages")
        or conversation_context.get("recentMessages")
        or conversation_context.get("messages")
        or []
    )
    if not isinstance(raw, list):
        return []
    strict_session = _strict_session_context(conversation_context) or bool(huihua_id)
    items: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        compact = _compact_message(item, huihua_id, strict_session=strict_session)
        if compact:
            items.append(compact)
    return items[-MAX_RECENT_XIAOXI:]


def duqu_duihua_shijian(huihua_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    path = _events_path(huihua_id)
    if not path.exists() or _deleted_path(huihua_id).exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    events: List[Dict[str, Any]] = []
    for line in lines[-max(1, limit * 2) :]:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        role = str(data.get("role") or data.get("message_role") or "").lower()
        raw_content = str(data.get("content") or "")
        if role == "user":
            content = _short_text(_history_user_content(raw_content))
        elif role == "assistant":
            content = _short_text(_history_assistant_content(raw_content))
        else:
            content = ""
        if role in ("user", "assistant") and content:
            events.append(
                {
                    "role": role,
                    "content": content,
                    "at": data.get("created_at") or data.get("created_ms"),
                    "request_id": data.get("request_id"),
                    "source": "duihua_shijian",
                }
            )
    return events[-limit:]


def _history_sort_key(value: Any, fallback: int) -> tuple[float, int]:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric = numeric / 1000.0
        return numeric, fallback
    text = str(value or "").strip()
    if text:
        try:
            parsed = time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
            return time.mktime(parsed), fallback
        except Exception:
            pass
    return float("inf"), fallback


def _merge_recent_messages(
    context_messages: Sequence[Mapping[str, Any]],
    event_messages: Sequence[Mapping[str, Any]],
    huihua_id: str = "",
) -> List[Dict[str, Any]]:
    merged_rows: List[Tuple[Tuple[float, int], Dict[str, Any]]] = []
    seen: set[Tuple[str, str, str]] = set()
    for index, raw in enumerate(list(context_messages) + list(event_messages)):
        compact = _compact_message(raw, huihua_id)
        if not compact:
            continue
        role = compact.get("role", "")
        content = compact.get("content", "")
        key = (role, content, str(compact.get("request_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged_rows.append((_history_sort_key(compact.get("at"), index), compact))
    merged_rows.sort(key=lambda item: item[0])
    return [item for _, item in merged_rows[-MAX_RECENT_XIAOXI:]]


def buquan_conversation_context(
    conversation_context: Optional[Mapping[str, Any]],
    xiaoxi: str = "",
    request_id: str = "",
) -> Dict[str, Any]:
    ctx = dict(conversation_context or {})
    request_id = str(request_id or ctx.get("request_id") or ctx.get("active_id") or "").strip()
    if request_id:
        ctx.setdefault("request_id", request_id)
        ctx.setdefault("active_id", request_id)
    huihua_id = queding_huihua_id(ctx, request_id)
    ctx["session_id"] = huihua_id
    ctx["conversation_id"] = huihua_id
    ctx["duihua_id"] = huihua_id
    ctx.setdefault("context_scope", "session")
    ctx.setdefault("short_context_policy", "session_only")
    ctx.setdefault("allow_cross_session_memory", True)
    event_messages = duqu_duihua_shijian(huihua_id)
    context_messages = _recent_from_context(ctx, huihua_id)
    ctx["duihua_shijian"] = event_messages
    ctx["recent_messages"] = _merge_recent_messages(context_messages, event_messages, huihua_id)
    if xiaoxi:
        ctx["current_user_message"] = str(xiaoxi)
    return ctx


def _without_current(messages: Sequence[Mapping[str, Any]], xiaoxi: str) -> List[Dict[str, Any]]:
    current = _short_text(xiaoxi, 2000)
    items = [_compact_message(item) for item in messages]
    items = [item for item in items if item]
    while items and items[-1].get("role") == "user" and items[-1].get("content") == current:
        items.pop()
    return items


def _looks_like_new_task_request(xiaoxi: str) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def _is_short_followup(xiaoxi: str) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def _zhidai_hit(xiaoxi: str) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def _is_jishu_shuxing_followup(xiaoxi: str) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def _has_current_attachments(conversation_context: Mapping[str, Any]) -> bool:
    for key in ("attachments", "chat_attachments", "files"):
        value = conversation_context.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, Mapping):
                continue
            if item.get("path") or item.get("name") or item.get("status"):
                return True
    return False


def _topic_supports_jishu_followup(topic: str, evidence: Sequence[str]) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def _recent_user_corrections(messages: Sequence[Mapping[str, Any]], *, limit: int = 3) -> List[str]:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return []


def _candidate_terms(text: str) -> List[str]:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return []


def _choose_topic(messages: Sequence[Mapping[str, Any]]) -> Tuple[str, List[str]]:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return "", []


def xujie_duihua(xiaoxi: str, conversation_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return {
        "followup_resolved": False,
        "reason": "model_interprets_history",
        "recent_event_count": len((conversation_context or {}).get("duihua_shijian") or []),
    }


def _l0_refs(role: str) -> Dict[str, str]:
    message_role_name = "USER" if role == "user" else "ASSISTANT" if role == "assistant" else "UNKNOWN"
    return {
        "event_ref": _ref("event"),
        "message_ref": _ref("message"),
        "content_ref": _ref("content"),
        "context_ref": _ref("context"),
        "memory_ref": _ref("memory"),
        "retrieval_ref": _ref("retrieval"),
        "trace_ref": _ref("trace"),
        "span_ref": _ref("span"),
        "event_type": _enum_value(EventType, "MESSAGE_ADDED", "message_added"),
        "event_state": _enum_value(EventState, "RECORDED", "recorded"),
        "message_role": _enum_value(MessageRole, message_role_name, role),
        "message_state": _enum_value(MessageState, "RECORDED", "recorded"),
        "context_kind": _enum_value(ContextKind, "CONVERSATION", "conversation"),
        "memory_kind": _enum_value(MemoryKind, "WORKING", "working"),
        "memory_state": _enum_value(MemoryState, "ACTIVE", "active"),
        "retrieval_kind": _enum_value(RetrievalKind, "EVENT_RETRIEVAL", "event_retrieval"),
    }


def xie_duihua_shijian(
    conversation_context: Optional[Mapping[str, Any]],
    role: str,
    content: str,
    *,
    request_id: str = "",
    xujie: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    text = _short_text(content)
    if not text:
        return None
    ctx = conversation_context or {}
    huihua_id = queding_huihua_id(ctx, request_id)
    if _deleted_path(huihua_id).exists():
        return None
    path = _events_path(huihua_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_SHIJIAN_FILE_BYTES:
            rollover = path.with_name(f"events.{_now_ms()}.jsonl")
            path.replace(rollover)
        record = {
            "created_ms": _now_ms(),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session_id": huihua_id,
            "conversation_id": huihua_id,
            "request_id": request_id or ctx.get("request_id") or ctx.get("active_id"),
            "role": role,
            "content": text,
            "content_digest": _content_digest(text),
            "l0_refs": _l0_refs(role),
        }
        if xujie:
            record["context_carryover"] = {
                "followup_resolved": bool(xujie.get("followup_resolved")),
                "topic": xujie.get("topic"),
                "confidence": xujie.get("confidence"),
                "resolved_query": xujie.get("resolved_query"),
                "reason": xujie.get("reason"),
            }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return path
    except Exception:
        return None


def xie_duihua_xiaoxi(
    conversation_context: Optional[Mapping[str, Any]],
    xiaoxi: str,
    xujie: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    ctx = conversation_context or {}
    return xie_duihua_shijian(ctx, "user", xiaoxi, request_id=str(ctx.get("request_id") or ctx.get("active_id") or ""), xujie=xujie)


def xie_duihua_huifu(
    conversation_context: Optional[Mapping[str, Any]],
    huifu: str,
    xujie: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    ctx = conversation_context or {}
    return xie_duihua_shijian(ctx, "assistant", huifu, request_id=str(ctx.get("request_id") or ctx.get("active_id") or ""), xujie=xujie)


def jilu_shanchu_mubei(
    conversation_context: Optional[Mapping[str, Any]],
    *,
    reason: str = "user_deleted_conversation",
) -> Optional[Path]:
    ctx = conversation_context or {}
    huihua_id = queding_huihua_id(ctx)
    path = _deleted_path(huihua_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "created_ms": _now_ms(),
            "session_id": huihua_id,
            "conversation_id": huihua_id,
            "reason": reason,
            "l0_refs": {
                "event_ref": _ref("event"),
                "context_ref": _ref("context"),
                "event_type": _enum_value(EventType, "STATE_CHANGED", "state_changed"),
                "context_kind": _enum_value(ContextKind, "CONVERSATION", "conversation"),
            },
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        events_path = _events_path(huihua_id)
        if events_path.exists():
            events_path.unlink()
        return path
    except Exception:
        return None


def qingkong_duihua_shijian(
    conversation_context: Optional[Mapping[str, Any]],
    *,
    reason: str = "user_cleared_conversation",
) -> bool:
    ctx = conversation_context or {}
    huihua_id = queding_huihua_id(ctx)
    try:
        path = _events_path(huihua_id)
        if path.exists():
            path.unlink()
        marker = _huihua_dir(huihua_id) / "cleared.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({
                "created_ms": _now_ms(),
                "session_id": huihua_id,
                "conversation_id": huihua_id,
                "reason": reason,
                "l0_refs": {
                    "event_ref": _ref("event"),
                    "context_ref": _ref("context"),
                    "event_type": _enum_value(EventType, "STATE_CHANGED", "state_changed"),
                    "context_kind": _enum_value(ContextKind, "CONVERSATION", "conversation"),
                },
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False
