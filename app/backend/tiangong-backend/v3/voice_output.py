"""Auditable voice-output capability negotiation.

This module deliberately separates an actual synthesized reply from a local
audio sample.  A sample is never uploaded or treated as a cloned voice by
default.  Native-model output is available only when an administrator has
explicitly configured a compatible audio endpoint and credentials.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCHEMA = "tiangong.v3.voice-output.v1"
MODES = frozenset({"auto", "native_model", "edge_tts", "browser_tts"})
MAX_TEXT_CHARS = 4_000
MAX_AUDIO_BYTES = 12 * 1024 * 1024


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _native_configured() -> bool:
    return bool(
        _text(os.environ.get("TIANGONG_VOICE_NATIVE_ENDPOINT"), 2_000)
        and _text(os.environ.get("TIANGONG_VOICE_NATIVE_API_KEY"), 2_000)
        and _text(os.environ.get("TIANGONG_VOICE_NATIVE_MODEL"), 200)
    )


def _edge_tts_available() -> bool:
    return importlib.util.find_spec("edge_tts") is not None


def capabilities() -> dict[str, Any]:
    native = _native_configured()
    edge = _edge_tts_available()
    return {
        "ok": True,
        "schema": SCHEMA,
        "default_mode": "auto",
        "capabilities": {
            "native_model": native,
            "edge_tts": edge,
            "browser_tts": True,
            # A recorded sample alone is not a speaker model or consent.
            "voice_cloning": False,
        },
        "reasons": {
            "native_model": "configured" if native else "native_endpoint_not_configured",
            "edge_tts": "installed" if edge else "edge_tts_not_installed",
            "voice_cloning": "no_authorized_cloning_provider_configured",
        },
    }


def _native_synthesize(text: str, voice_id: str) -> tuple[bytes, str]:
    endpoint = _text(os.environ.get("TIANGONG_VOICE_NATIVE_ENDPOINT"), 2_000)
    api_key = _text(os.environ.get("TIANGONG_VOICE_NATIVE_API_KEY"), 2_000)
    model = _text(os.environ.get("TIANGONG_VOICE_NATIVE_MODEL"), 200)
    if not endpoint or not api_key or not model:
        raise RuntimeError("native_endpoint_not_configured")
    payload = json.dumps({
        "model": model,
        "input": text,
        "voice": voice_id,
        "response_format": "mp3",
    }, ensure_ascii=False).encode("utf-8")
    request = Request(endpoint, data=payload, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    })
    try:
        with urlopen(request, timeout=45) as response:  # nosec B310: configured administrator endpoint
            body = response.read(MAX_AUDIO_BYTES + 1)
            content_type = str(response.headers.get("Content-Type") or "audio/mpeg").split(";", 1)[0].strip()
    except HTTPError as exc:
        raise RuntimeError(f"native_http_{exc.code}") from exc
    except URLError as exc:
        raise RuntimeError("native_unreachable") from exc
    if not body or len(body) > MAX_AUDIO_BYTES:
        raise RuntimeError("native_invalid_audio_size")
    if content_type.startswith("application/json"):
        raise RuntimeError("native_returned_json")
    return body, content_type or "audio/mpeg"


def _edge_synthesize(text: str, voice_id: str) -> tuple[bytes, str]:
    try:
        from edge_tts import Communicate
    except ImportError as exc:
        raise RuntimeError("edge_tts_not_installed") from exc
    selected_voice = voice_id or "zh-CN-XiaoxiaoNeural"
    handle = tempfile.NamedTemporaryFile(prefix="tiangong-voice-", suffix=".mp3", delete=False)
    output = Path(handle.name)
    handle.close()
    try:
        asyncio.run(Communicate(text, selected_voice).save(str(output)))
        body = output.read_bytes()
    except Exception as exc:
        raise RuntimeError(f"edge_tts_failed:{type(exc).__name__}") from exc
    finally:
        output.unlink(missing_ok=True)
    if not body or len(body) > MAX_AUDIO_BYTES:
        raise RuntimeError("edge_tts_invalid_audio_size")
    return body, "audio/mpeg"


def synthesize(payload: dict[str, Any] | None, configured_voice: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    settings = configured_voice if isinstance(configured_voice, dict) else {}
    text = _text(body.get("text"), MAX_TEXT_CHARS)
    if not text:
        return {"ok": False, "schema": SCHEMA, "reason_code": "voice_output.text_required"}
    requested = _text(body.get("mode") or settings.get("output_mode") or "auto", 32)
    mode = requested if requested in MODES else "auto"
    voice_id = _text(body.get("voice_id") or settings.get("native_voice_id") or settings.get("provider_voice_id"), 160)
    attempts: list[str] = []

    if mode in {"auto", "native_model"}:
        if _native_configured():
            try:
                audio, mime = _native_synthesize(text, voice_id)
                return {
                    "ok": True, "schema": SCHEMA, "engine": "native_model",
                    "mime": mime, "audio_base64": base64.b64encode(audio).decode("ascii"),
                    "text_chars": len(text), "attempts": attempts + ["native_model"],
                }
            except RuntimeError as exc:
                attempts.append(str(exc))
        else:
            attempts.append("native_endpoint_not_configured")
        if mode == "native_model":
            return {"ok": False, "schema": SCHEMA, "reason_code": "voice_output.native_unavailable", "attempts": attempts}

    if mode in {"auto", "edge_tts"}:
        if _edge_tts_available():
            try:
                audio, mime = _edge_synthesize(text, voice_id)
                return {
                    "ok": True, "schema": SCHEMA, "engine": "edge_tts",
                    "mime": mime, "audio_base64": base64.b64encode(audio).decode("ascii"),
                    "text_chars": len(text), "attempts": attempts + ["edge_tts"],
                }
            except RuntimeError as exc:
                attempts.append(str(exc))
        else:
            attempts.append("edge_tts_not_installed")

    return {
        "ok": False, "schema": SCHEMA,
        "reason_code": "voice_output.browser_fallback_required",
        "attempts": attempts,
    }
