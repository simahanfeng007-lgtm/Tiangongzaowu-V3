from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any


class TiangongJsonError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        source: str = "json",
        preview: str = "",
        status: int = 400,
        original: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.source = source
        self.preview = preview
        self.status = status
        self.original = original


def preview_text(value: Any, limit: int = 500) -> str:
    text = str(value or "")
    return text.replace("\r", " ").replace("\n", " ").strip()[:limit]


def _decode_raw(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def json_error_message(code: str, *, source: str = "json") -> str:
    if code in {"empty_json", "empty_json_body"}:
        return f"{source} 返回了空 JSON 响应"
    if code in {"invalid_json", "invalid_json_body"}:
        return f"{source} 返回的内容不是合法 JSON"
    if code == "json_not_object":
        return f"{source} 返回的 JSON 不是对象"
    return f"{source} JSON 解析失败"


def make_json_error(raw: Any, exc: Exception | None = None, *, source: str = "json", body: bool = False) -> TiangongJsonError:
    text = _decode_raw(raw)
    stripped = text.strip()
    if not stripped:
        code = "empty_json_body" if body else "empty_json"
    else:
        code = "invalid_json_body" if body else "invalid_json"
    return TiangongJsonError(
        code,
        json_error_message(code, source=source),
        source=source,
        preview=preview_text(text),
        status=400 if body else 502,
        original=str(exc or ""),
    )


def loads_json_object(
    raw: Any,
    *,
    source: str = "json",
    default_empty: dict | None = None,
    body: bool = False,
) -> dict:
    text = _decode_raw(raw)
    if not text.strip():
        if default_empty is not None:
            return dict(default_empty)
        raise make_json_error(text, source=source, body=body)
    try:
        data = json.loads(text)
    except JSONDecodeError as exc:
        raise make_json_error(text, exc, source=source, body=body) from exc
    if not isinstance(data, dict):
        raise TiangongJsonError(
            "json_not_object",
            json_error_message("json_not_object", source=source),
            source=source,
            preview=preview_text(text),
            status=400 if body else 502,
        )
    return data


# bug-fix: Kimi#13 失败兜底人话映射表：error 字段给"人话文案+可操作建议"，
# 原始异常文本只进 detail，不再直接暴露给用户（2026-08-26，凌霜）
_YUANSHI_CUOWU_RENHUA = (
    (("max retries exceeded", "connection refused", "connection reset", "connectionerror",
      "connecterror", "connect timeout", "getaddrinfo", "name or service not known",
      "temporary failure in name resolution", "ssl", "certificate", "proxy"),
     "network_unreachable", "网络连不上模型服务：请检查网络或代理是否可用，稍后重试。"),
    (("readtimeout", "read timeout", "timeouterror", "timed out", "timeout"),
     "llm_timeout", "模型服务响应超时：请稍后重试，或在设置里切换更快的模型。"),
    (("429", "rate limit", "quota", "insufficient"),
     "rate_limited", "请求太频繁或额度不足：请稍等再试，或到服务商控制台检查用量。"),
    (("401", "403", "unauthorized", "forbidden", "api key", "apikey", "invalid_api_key"),
     "auth_failed", "鉴权失败：请检查 API Key 是否正确，以及账号权限和余额。"),
    (("404", "not found"),
     "endpoint_not_found", "接口或模型不存在：请检查模型名与 Base URL 配置。"),
    (("500", "502", "503", "504", "internal server error", "bad gateway", "service unavailable"),
     "provider_error", "模型服务商暂时异常：请稍后重试，或切换其他模型。"),
)

# 内部短码 → 人话（chat_failed / backend_error 等不再裸露）
_DUANMA_RENHUA = {
    "chat_failed": "对话没有完成：调用模型失败，请稍后重试。",
    "backend_error": "后端服务暂时不可用：请稍后重试；若持续出现，请检查模型配置。",
    "chat_runtime": "对话运行出错：请稍后重试。",
}


def _renhua_cuowu(raw: Any, default_code: str = "backend_error") -> tuple[str, str]:
    """把原始异常文本/内部短码翻成 (error_code, 人话文案)；原始文本由调用方放 detail。"""
    text = str(raw or "")
    lowered = text.lower()
    for keys, code, human in _YUANSHI_CUOWU_RENHUA:
        if any(key in lowered for key in keys):
            return code, human
    short = text.strip()
    if short in _DUANMA_RENHUA:
        return short, _DUANMA_RENHUA[short]
    return default_code, "服务暂时不可用：请稍后重试；若持续出现，请检查网络与模型配置。"


def normalize_exception(exc: Exception, *, source: str = "backend") -> dict:
    if isinstance(exc, TiangongJsonError):
        return {
            "error_code": exc.code,
            "error": exc.message,
            "detail": exc.original or exc.message,
            "source": exc.source or source,
            "raw_preview": exc.preview,
        }
    if isinstance(exc, JSONDecodeError):
        normalized = make_json_error("", exc, source=source)
        return {
            "error_code": normalized.code,
            "error": normalized.message,
            "detail": str(exc),
            "source": source,
            "raw_preview": normalized.preview,
        }
    # bug-fix: Kimi#13 兜底分支：error 换人话，原始异常只进 detail（2026-08-26，凌霜）
    code, human = _renhua_cuowu(exc, default_code=type(exc).__name__)
    return {
        "error_code": code,
        "error": human,
        "detail": str(exc) or type(exc).__name__,
        "source": source,
        "raw_preview": "",
    }


def looks_like_json_parse_error(text: Any) -> bool:
    lowered = str(text or "").lower()
    return (
        "expecting value: line 1 column 1" in lowered
        or "jsondecodeerror" in lowered
        or "invalid_json" in lowered
        or "empty_json" in lowered
        or "non_json_response" in lowered
    )


def normalize_error_text(text: Any, *, source: str = "backend") -> dict:
    raw = str(text or "")
    if looks_like_json_parse_error(raw):
        if "empty_json" in raw.lower() or "line 1 column 1" in raw.lower():
            code = "empty_json"
        else:
            code = "invalid_json"
        return {
            "error_code": code,
            "error": json_error_message(code, source=source),
            "detail": preview_text(raw, 800),
            "source": source,
            "raw_preview": preview_text(raw),
        }
    # bug-fix: Kimi#13 兜底分支：原始异常文本（如 HTTPSConnectionPool/Max retries exceeded）
    # 不再作为 error 直出用户，换人话+可操作建议；原文进 detail（2026-08-26，凌霜）
    code, human = _renhua_cuowu(raw, default_code="backend_error")
    return {
        "error_code": code,
        "error": human,
        "detail": preview_text(raw, 800),
        "source": source,
        "raw_preview": preview_text(raw),
    }


def error_payload(exc: Exception, *, source: str = "backend", ok_key: bool = True) -> dict:
    data = normalize_exception(exc, source=source)
    if ok_key:
        data["ok"] = False
    return data


def chat_error_payload(exc: Exception, *, source: str = "chat") -> dict:
    data = normalize_exception(exc, source=source)
    return {
        "cuowu": data["error"],
        "error_code": data["error_code"],
        "detail": data.get("detail", ""),
        "source": data.get("source", source),
        "raw_preview": data.get("raw_preview", ""),
        "zhuangtai": "shibai",
    }


def chat_error_text_payload(text: Any, *, source: str = "chat") -> dict:
    data = normalize_error_text(text, source=source)
    return {
        "cuowu": data["error"],
        "error_code": data["error_code"],
        "detail": data.get("detail", ""),
        "source": data.get("source", source),
        "raw_preview": data.get("raw_preview", ""),
        "zhuangtai": "shibai",
    }
