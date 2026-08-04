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
_EN_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*\b")
_QUOTED_RE = re.compile(r"[\"'“”‘’「」『』《》]([^\"'“”‘’「」『』《》]{2,48})[\"'“”‘’「」『』《》]")
_ZH_TECH_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_.-]{1,24}(?:AI|API|SDK|Agent|Code|模型|工具|项目|系统|框架|平台|插件|记忆|上下文)")
_ZH_SUBJECT_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9_.-]{2,32}?)(?:这个(?:单词|词|项目|工具|人)?|是什么|是谁|怎么样|好用|靠谱吗|意思|是男|是女|能干嘛|怎么用)"
)
_ZH_QUESTION_SUBJECT_RE = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9_.-]{2,32}?)(?:为什么|怎么|如何|能不能|能|可以|适合|跟|和|有什么用|有什么|好不好|难不难|难吗|靠谱吗|准吗|准确吗|收费吗|开源吗|是什么意思|是什么|是啥|是谁)"
)
_ZH_ASSERTION_SUBJECT_RE = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9_.-]{2,32}?)\s*是(?:一个|一种|当前|本轮|上一轮|用于|可用于|智能体|代码|知识|数据|语音|检索|工作|对话)"
)
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

_COMMON_EN_TOKENS = {
    "a",
    "an",
    "and",
    "api",
    "app",
    "assistant",
    "code",
    "docs",
    "github",
    "http",
    "https",
    "llm",
    "model",
    "open",
    "sdk",
    "the",
    "tool",
    "tools",
    "url",
    "user",
}

_XUJIE_ZHIDAI = (
    "这个",
    "那个",
    "这些",
    "那些",
    "它",
    "他",
    "她",
    "刚才",
    "上面",
    "前面",
    "上一轮",
    "前一个",
    "这块",
    "这东西",
    "好用",
    "靠谱么",
    "靠谱吗",
    "能用",
    "适合",
    "推荐",
    "值得",
    "区别",
    "优缺点",
    "怎么用",
    "多少钱",
    "贵吗",
)

_XUJIE_ZHISHI_CI = (
    "这个",
    "那个",
    "这些",
    "那些",
    "它",
    "他",
    "她",
    "刚才",
    "上面",
    "前面",
    "上一轮",
    "前一个",
    "这块",
    "这东西",
)

_XUJIE_FANWEN_CI = (
    "好用",
    "靠谱",
    "安全",
    "安装",
    "能用",
    "商用",
    "开源",
    "收费",
    "性能",
    "兼容",
    "部署",
    "风险",
    "限制",
    "配置",
    "原理",
    "教程",
    "价格",
    "文档",
    "接口",
    "速度",
    "准确",
    "稳定",
    "成本",
    "效果",
    "质量",
    "适合",
    "推荐",
    "值得",
    "区别",
    "优缺点",
    "怎么用",
    "多少钱",
    "贵吗",
    "行吗",
    "可以吗",
    "能不能",
    "要不要",
)

_FUJIAN_ZHIDAI_CI = (
    "这个文件",
    "这个文档",
    "这个附件",
    "这个压缩包",
    "这个zip",
    "这个表格",
    "这个图片",
    "这个pdf",
    "这份文档",
    "刚才的文件",
    "刚才那个文件",
    "上面的文件",
    "这张图",
    "这篇文章",
)

_FUJIAN_RENWU_CI = (
    "总结",
    "读",
    "看",
    "改",
    "算",
    "提取",
    "翻译",
    "分析",
    "打开",
    "发",
    "导出",
    "识别",
    "放",
    "放到",
    "复制",
    "移动",
    "保存",
    "解压",
    "拷贝",
    "拷到",
    "传",
    "处理",
    "运行",
    "测试",
)

_JISHU_SHUXING_CI = (
    "安装",
    "部署",
    "开源",
    "商用",
    "性能",
    "兼容",
    "配置",
    "接口",
    "文档",
    "速度",
    "准确",
    "稳定",
    "成本",
)

_JISHU_ZHUTI_CI = (
    "AI",
    "API",
    "SDK",
    "Agent",
    "Code",
    "模型",
    "工具",
    "项目",
    "系统",
    "框架",
    "平台",
    "插件",
    "记忆",
    "上下文",
    "文件",
    "数据库",
    "表格",
    "检索",
    "部署",
    "代码",
    "软件",
    "应用",
    "IDE",
    "RAG",
)

_XUJIE_SHENGLUE_DUANWEN = {
    "继续",
    "继续说",
    "继续讲",
    "然后呢",
    "为什么",
    "为啥",
    "怎么做",
    "下一步",
    "展开",
    "展开说",
    "详细说",
    "详细说说",
    "重新说",
    "重新解释",
    "重新解释一下",
    "换个说法",
}

_XUJIE_DONGZUO_DUANWEN_CI = (
    "压缩",
    "压成",
    "压为",
    "打包",
    "打成",
    "zip",
    "压缩包",
    "发给我",
    "发送给我",
    "发我",
    "传给我",
    "交付",
    "查收",
)

_YONGHU_JIUPIAN_CI = (
    "不对",
    "不是",
    "别",
    "不要",
    "不是让",
    "不是叫",
    "我不是",
    "我要的是",
    "我想要的是",
    "应该是",
    "而是",
    "纠正",
    "错了",
    "方向错",
    "改成",
    "换成",
)

_GAOYOUXIAN_RENWU_CI = (
    "代码",
    "写代码",
    "源码",
    "程序",
    "可运行",
    "游戏",
    "mud",
    "md",
    "markdown",
    "文件",
    "目录",
    "项目",
)

_XIN_ZHUTI_XIANSUO = (
    "天气",
    "时间",
    "几点",
    "日期",
    "新闻",
    "股票",
    "股价",
    "汇率",
    "日程",
    "文件",
    "文件夹",
    "目录",
    "D盘",
    "C盘",
    "E盘",
)

_XIN_RENWU_QIDONG = (
    "帮我",
    "帮忙",
    "查一下",
    "查下",
    "搜索",
    "搜一下",
    "看一下",
    "看下",
    "打开",
    "创建",
    "删除",
    "修改",
    "运行",
    "修复",
    "写",
    "生成",
    "翻译",
    "总结",
    "整理",
    "提醒",
    "发邮件",
    "下载",
)

_XIN_RENWU_NEIRONG_CI = (
    "在桌面",
    "桌面上",
    "保存到桌面",
    "放到桌面",
    "用txt",
    "txt写",
    "写完",
    "写一",
    "创建一个",
    "新建一个",
    "生成一个",
    "写到",
    "写进",
    "第一章",
    "第二章",
    "第三章",
    "微信发给我",
    "微信发送给我",
)

_XIN_RENWU_DONGCI = (
    "写",
    "创建",
    "新建",
    "生成",
    "保存",
    "放到",
    "发送",
    "发给我",
    "微信发",
)

_ZH_SUBJECT_PREFIXES = ("请问", "帮我查一下", "帮我查", "搜索一下", "搜索下", "搜一下", "查一下", "查下", "解释一下")


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
    status = "completed" if media_paths or re.search(r"(已完成|已办好|已写好|已生成|已发送|已附上|办妥|完成)", clean) else "reply"
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
    path = _clean_local_path(path_text)
    if not path:
        return ""
    compact = re.sub(r"\s+", "", str(xiaoxi or "")).lower()
    if _is_file_resend_followup(xiaoxi) and Path(path).suffix.lower() in _DELIVERABLE_FILE_SUFFIXES:
        return path
    needs_directory = any(marker in compact for marker in ("打包", "zip", "测试", "构建", "工程", "项目", "代码", "修复", "运行"))
    suffix = Path(path).suffix.lower()
    if needs_directory and suffix and suffix not in {".zip"}:
        return str(Path(path).parent)
    return path


def _recent_followup_path(messages: Sequence[Mapping[str, Any]], xiaoxi: str) -> str:
    for item in reversed(list(messages)[-24:]):
        content = _strip_wechat_attachment_context(str(item.get("content") or ""))
        paths = _extract_local_paths(content)
        if not paths:
            continue
        for raw_path in paths:
            path = _path_for_followup_task(raw_path, xiaoxi)
            if path:
                return path
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
    compact = re.sub(r"\s+", "", str(xiaoxi or "")).lower()
    if not compact:
        return False
    negates_resend = any(marker in compact for marker in (
        "不是再发",
        "不是重发",
        "不是重新发",
        "不要再发",
        "别再发",
        "不是发一遍",
        "不是再发一次",
    ))
    asks_or_requests_revision = any(marker in compact for marker in (
        "修改",
        "修复",
        "修一下",
        "改一下",
        "改写",
        "改完",
        "修改完",
        "修完",
        "查到的问题",
        "对不上",
        "不对齐",
    ))
    if negates_resend or asks_or_requests_revision:
        return False
    creates_new_artifact = any(marker in compact for marker in (
        "创建",
        "新建",
        "生成",
        "写一个",
        "做一个",
        "压缩成",
        "打包成",
        "里面放",
        "里面写",
    ))
    explicit_resend = any(marker in compact for marker in (
        "再发",
        "重新发",
        "重发",
        "发我一遍",
        "发给我一遍",
        "再给我",
        "再传",
    ))
    original_file_send = (
        any(marker in compact for marker in ("原文件", "附件", "聊天框"))
        and any(marker in compact for marker in ("发", "传", "给我", "发送"))
    )
    referenced_file_send = (
        any(marker in compact for marker in ("刚才", "刚刚", "那个", "这个", "zip", "压缩包", "文件"))
        and any(marker in compact for marker in ("发", "传", "发送"))
    )
    if creates_new_artifact and not explicit_resend:
        return False
    return explicit_resend or original_file_send or referenced_file_send


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
    compact = re.sub(r"\s+", "", str(xiaoxi or ""))
    if not compact:
        return False
    if compact.startswith(("继续", "接着", "然后", "再")) and len(compact) <= 40:
        return False
    has_new_task_cue = any(word in compact for word in _XIN_RENWU_NEIRONG_CI)
    has_action = any(word in compact for word in _XIN_RENWU_DONGCI)
    if has_new_task_cue and has_action:
        return True
    if compact.startswith(("你在", "请在", "在桌面", "桌面上")) and has_action:
        return True
    if len(compact) > 18 and any(compact.startswith(word) for word in _XIN_RENWU_QIDONG):
        return True
    return False


def _is_short_followup(xiaoxi: str) -> bool:
    text = str(xiaoxi or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    if _looks_like_new_task_request(text):
        return False
    has_explicit_path = bool(re.search(r"(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/]|/[A-Za-z0-9_\-.]+)", text))
    starts_new_task = any(compact.startswith(word) for word in _XIN_RENWU_QIDONG)
    if has_explicit_path and (starts_new_task or len(compact) > 28):
        return False
    if any(word in compact.lower() for word in _FUJIAN_ZHIDAI_CI) and any(word in compact for word in _FUJIAN_RENWU_CI):
        return False
    if any(word in compact for word in _XUJIE_ZHISHI_CI):
        return True
    if compact in _XUJIE_SHENGLUE_DUANWEN:
        return True
    if (
        not has_explicit_path
        and len(compact) <= 30
        and any(word in compact for word in _XUJIE_DONGZUO_DUANWEN_CI)
        and not any(word in compact for word in ("哪个", "哪些", "什么范围", "哪一个", "哪份"))
    ):
        return True
    if compact.startswith(("继续", "接着", "然后", "再")) and len(compact) <= 40:
        return True
    if any(word in compact for word in _XIN_ZHUTI_XIANSUO):
        return False
    if any(compact.startswith(word) for word in _XIN_RENWU_QIDONG) and not any(word in compact for word in _XUJIE_ZHISHI_CI):
        return False
    if len(compact) <= 18 and any(word in compact for word in _XUJIE_FANWEN_CI):
        return True
    if len(compact) <= 8 and compact in {"怎么样", "咋样", "如何"}:
        return True
    return False


def _zhidai_hit(xiaoxi: str) -> bool:
    compact = re.sub(r"\s+", "", str(xiaoxi or ""))
    return any(word in compact for word in _XUJIE_ZHIDAI)


def _is_jishu_shuxing_followup(xiaoxi: str) -> bool:
    compact = re.sub(r"\s+", "", str(xiaoxi or ""))
    return any(word in compact for word in _JISHU_SHUXING_CI)


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
    text = " ".join([str(topic or ""), *[str(item or "") for item in evidence]])
    text = re.sub(r"\b(?:user|assistant|unknown):\s*", "", text)
    if re.search(r"[A-Za-z0-9]", text):
        return True
    return any(word in text for word in _JISHU_ZHUTI_CI)


def _recent_user_corrections(messages: Sequence[Mapping[str, Any]], *, limit: int = 3) -> List[str]:
    corrections: List[str] = []
    for item in reversed(list(messages)[-16:]):
        role = str(item.get("role") or "").lower()
        if role != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        compact = re.sub(r"\s+", "", content.lower())
        if not any(marker.lower() in compact for marker in _YONGHU_JIUPIAN_CI):
            continue
        if not any(marker.lower() in compact for marker in _GAOYOUXIAN_RENWU_CI):
            continue
        corrections.append(_short_text(content, 260))
        if len(corrections) >= limit:
            break
    return list(reversed(corrections))


def _candidate_terms(text: str) -> List[str]:
    terms: List[str] = []
    for match in _ZH_QUESTION_SUBJECT_RE.finditer(text):
        term = match.group(1).strip("，。！？、：:；;,.!?()[]{}<> \n\t")
        if 2 <= len(term) <= 32:
            terms.append(term)
    for match in _ZH_ASSERTION_SUBJECT_RE.finditer(text):
        term = match.group(1).strip("，。！？、：:；;,.!?()[]{}<> \n\t")
        if 2 <= len(term) <= 32:
            terms.append(term)
    for match in _ZH_SUBJECT_RE.finditer(text):
        term = match.group(1).strip("，。！？、：:；;,.!?()[]{}<> \n\t")
        for prefix in _ZH_SUBJECT_PREFIXES:
            if term.startswith(prefix):
                term = term[len(prefix) :].strip()
        if 2 <= len(term) <= 32 and term not in {"这个", "那个", "刚才", "上面", "前面"}:
            terms.append(term)
    for match in _QUOTED_RE.finditer(text):
        term = match.group(1).strip()
        if term:
            terms.append(term)
    for match in _ZH_TECH_RE.finditer(text):
        term = match.group(0).strip("，。！？、：:；;,.!?()[]{}<>")
        for prefix in ("是一个", "是一种", "是当前", "是本轮", "是上一轮", "用于", "可用于"):
            if term.startswith(prefix):
                term = term[len(prefix) :].strip()
        if 2 <= len(term) <= 48:
            terms.append(term)
    for match in _EN_TOKEN_RE.finditer(text):
        term = match.group(0).strip()
        if len(term) < 2:
            continue
        if term.lower() in _COMMON_EN_TOKENS:
            continue
        if term.isupper() or any(ch.isupper() for ch in term[1:]) or "-" in term or "." in term:
            terms.append(term)
    dedup: List[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(term)
    return dedup


def _choose_topic(messages: Sequence[Mapping[str, Any]]) -> Tuple[str, List[str]]:
    scores: Dict[str, float] = {}
    evidence: Dict[str, List[str]] = {}
    recent = list(messages)[-10:]
    for index, item in enumerate(recent):
        role = str(item.get("role") or "").lower()
        content = str(item.get("content") or "")
        if not content:
            continue
        recency = index + 1
        role_weight = 1.4 if role == "user" else 1.0
        for term in _candidate_terms(content):
            score = recency * role_weight
            if role == "user" and ("是什么" in content or "什么意思" in content or "搜索" in content or "查" in content):
                score += 6
            if role == "assistant" and term.lower() in scores:
                score += 2
            key = term
            scores[key] = scores.get(key, 0.0) + score
            evidence.setdefault(key, [])
            if len(evidence[key]) < 3:
                evidence[key].append(f"{role or 'unknown'}: {_short_text(content, 120)}")
    if not scores:
        return "", []
    topic = max(scores.items(), key=lambda item: (item[1], len(item[0])))[0]
    return topic, evidence.get(topic, [])[:3]


def xujie_duihua(xiaoxi: str, conversation_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    ctx = conversation_context or {}
    messages = _without_current(_recent_from_context(ctx), xiaoxi)
    corrections = _recent_user_corrections(messages)
    compact = re.sub(r"\s+", "", str(xiaoxi or "")).lower()
    if _has_current_attachments(ctx) and any(word in compact for word in _FUJIAN_ZHIDAI_CI):
        return {
            "followup_resolved": False,
            "reason": "current_attachment_context",
            "recent_event_count": len(ctx.get("duihua_shijian") or []),
        }
    if not _is_short_followup(xiaoxi):
        return {
            "followup_resolved": False,
            "reason": "not_short_followup",
            "recent_event_count": len(ctx.get("duihua_shijian") or []),
        }
    recent_path = _recent_followup_path(messages, xiaoxi)
    topic, evidence = _choose_topic(messages)
    if recent_path:
        topic = recent_path
        evidence = [f"recent_path: {recent_path}", *evidence[:2]]
    if not topic:
        if corrections:
            correction_terms = _candidate_terms(corrections[-1])
            topic = correction_terms[0] if correction_terms else "最近用户纠偏任务"
            evidence = [f"user: {_short_text(item, 120)}" for item in corrections[-3:]]
        else:
            return {
                "followup_resolved": False,
                "reason": "no_recent_topic",
                "recent_event_count": len(ctx.get("duihua_shijian") or []),
            }
    if recent_path and _is_file_resend_followup(xiaoxi) and _path_is_existing_deliverable_file(recent_path):
        confidence = 0.96
        resolved_query = f"请将已有文件 {recent_path} 作为微信原文件附件直接发给用户；不要重新创建、不要重新压缩、不要扫描桌面。"
        evidence_lines = "\n".join(f"- {line}" for line in evidence) if evidence else "- 最近对话主题"
        prompt_block = (
            "[上下文续接]\n"
            f"本轮用户短问是在请求重发上一轮文件：{recent_path}\n"
            f"请把本轮输入理解为：{resolved_query}\n"
            "这是确定性的附件重发请求，不是继续执行旧创建/压缩任务。\n"
            "最终回复必须包含 MEDIA:<文件路径>，并且不要改去其他历史 workspace 或旧项目。\n"
            "[续接依据]\n"
            f"{evidence_lines}"
        )
        return {
            "followup_resolved": True,
            "topic": recent_path,
            "confidence": confidence,
            "resolved_query": resolved_query,
            "target_path": recent_path,
            "prompt_block": prompt_block,
            "effective_xiaoxi": f"{resolved_query}\n\n{prompt_block}",
            "direct_reply": _direct_media_resend_reply(recent_path),
            "reason": "short_followup_resend_recent_file",
            "evidence": evidence,
            "recent_event_count": len(ctx.get("duihua_shijian") or []),
        }
    if _is_jishu_shuxing_followup(xiaoxi) and not _topic_supports_jishu_followup(topic, evidence):
        return {
            "followup_resolved": False,
            "reason": "technical_followup_without_technical_topic",
            "topic": topic,
            "recent_event_count": len(ctx.get("duihua_shijian") or []),
        }
    confidence = 0.9 if recent_path else (0.86 if _zhidai_hit(xiaoxi) else 0.72)
    if recent_path:
        resolved_query = f"请继续处理路径 {recent_path}：{str(xiaoxi).strip()}".strip()
    else:
        resolved_query = f"{topic} {str(xiaoxi).strip()}".strip()
    if corrections:
        if recent_path:
            resolved_query = f"请继续处理路径 {recent_path}：{corrections[-1]} {str(xiaoxi).strip()}".strip()
        else:
            resolved_query = f"{corrections[-1]} {str(xiaoxi).strip()}".strip()
    evidence_lines = "\n".join(f"- {line}" for line in evidence) if evidence else "- 最近对话主题"
    correction_lines = "\n".join(f"- user: {line}" for line in corrections)
    correction_block = ""
    if correction_lines:
        correction_block = (
            "\n[最近用户纠偏 - 优先级最高]\n"
            f"{correction_lines}\n"
            "如果纠偏否定了旧计划或旧交付物，必须作废旧计划，按用户纠偏后的目标继续。"
        )
    prompt_block = (
        "[上下文续接]\n"
        f"本轮用户短问是在续接上一轮主题：{topic}\n"
        f"请把本轮输入理解为：{resolved_query}\n"
        + (f"本轮锁定目标路径：{recent_path}\n除非该路径不存在，不要改去其他历史 workspace 或旧项目。\n" if recent_path else "")
        + "不要把“这个/它/好用么/刚才那个”等指代词理解为询问助手自身。\n"
        + "[续接依据]\n"
        + f"{evidence_lines}"
        + f"{correction_block}"
    )
    effective_xiaoxi = f"{resolved_query}\n\n{prompt_block}"
    return {
        "followup_resolved": True,
        "topic": topic,
        "confidence": confidence,
        "resolved_query": resolved_query,
        "target_path": recent_path,
        "prompt_block": prompt_block,
        "effective_xiaoxi": effective_xiaoxi,
        "reason": "short_followup_with_recent_topic",
        "evidence": evidence,
        "recent_event_count": len(ctx.get("duihua_shijian") or []),
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
