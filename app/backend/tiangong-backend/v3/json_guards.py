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
    return {
        "error_code": type(exc).__name__,
        "error": str(exc) or type(exc).__name__,
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
    return {
        "error_code": "backend_error",
        "error": raw or "backend_error",
        "detail": raw or "backend_error",
        "source": source,
        "raw_preview": "",
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
