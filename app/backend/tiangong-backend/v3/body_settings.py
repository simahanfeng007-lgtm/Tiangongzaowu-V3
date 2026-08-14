from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .peizhi import BODY_SETTINGS_LUJING, SOUL_LUJING


VOICE_PRESETS = [
    {
        "id": "xiaoxiao_warm",
        "label": "晓晓·温柔",
        "lang": "zh-CN",
        "rate": 0.95,
        "pitch": 1.06,
        "volume": 1.0,
        "preferred_names": ["Xiaoxiao", "Microsoft Xiaoxiao", "zh-CN-XiaoxiaoNeural"],
    },
    {
        "id": "xiaoyi_gentle",
        "label": "晓伊·轻语",
        "lang": "zh-CN",
        "rate": 0.90,
        "pitch": 1.10,
        "volume": 1.0,
        "preferred_names": ["Xiaoyi", "Microsoft Xiaoyi", "zh-CN-XiaoyiNeural"],
    },
    {
        "id": "xiaoxuan_bright",
        "label": "晓萱·明亮",
        "lang": "zh-CN",
        "rate": 0.97,
        "pitch": 1.02,
        "volume": 1.0,
        "preferred_names": ["Xiaoxuan", "Microsoft Xiaoxuan", "zh-CN-XiaoxuanNeural"],
    },
    {
        "id": "yunxi_calm",
        "label": "云希·沉稳",
        "lang": "zh-CN",
        "rate": 0.93,
        "pitch": 0.95,
        "volume": 1.0,
        "preferred_names": ["Yunxi", "Microsoft Yunxi", "zh-CN-YunxiNeural"],
    },
    {
        "id": "xiaohan_clear",
        "label": "晓涵·清澈",
        "lang": "zh-CN",
        "rate": 1.0,
        "pitch": 1.0,
        "volume": 1.0,
        "preferred_names": ["Xiaohan", "Microsoft Xiaohan", "zh-CN-XiaohanNeural"],
    },
    {
        "id": "custom",
        "label": "授权声线配置",
        "lang": "zh-CN",
        "rate": 1.0,
        "pitch": 1.0,
        "volume": 1.0,
        "preferred_names": [],
    },
]

VOICE_PRESET_IDS = {item["id"] for item in VOICE_PRESETS}

DEFAULT_BODY_SETTINGS = {
    "schema": "tiangong.v3.body_settings.v2",
    "profile": {
        "name": "起源",
        "avatar_data_url": "",
        "body_preset": "standard",
        "soul": "",
    },
    "user": {
        "name": "",
        "title": "",
        "display_name": "",
        "callsign": "",
        "work": "",
        "avatar_data_url": "",
        "profile_summary": "",
        "context_enabled": True,
    },
    "voice": {
        "reply_read_aloud": False,
        "preset_id": "qiyuan_clear",
        "system_voice_name": "",
        "custom_voice_name": "",
        "custom_voice_path": "",
        "output_mode": "auto",
        "native_voice_id": "",
        "sample_consent": False,
        "lang": "zh-CN",
        "rate": 1.0,
        "pitch": 1.04,
        "volume": 1.0,
    },
    "presentation": {
        "configured": False,
        "camera": {
            "focus": 0.0,
            "height": 0.0,
            "distance": 0.0,
            "side": 0.0,
        },
        "lighting": {
            "key": 1.0,
            "angle": 0.0,
            "ambient": 1.0,
            "exposure": 1.0,
        },
    },
    "ui": {
        "theme_style": "ink_teal",
    },
}


def _safe_float(value: Any, fallback: float, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = fallback
    return max(lower, min(upper, number))


def _safe_text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _read_soul() -> str:
    try:
        if SOUL_LUJING.exists():
            return SOUL_LUJING.read_text(encoding="utf-8-sig")
    except Exception:
        return ""
    try:
        from .gutong.soul_jiazai import duqu_soul

        return duqu_soul()
    except Exception:
        return ""


def _merge_settings(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(DEFAULT_BODY_SETTINGS)
    if isinstance(raw, dict):
        raw_profile = raw.get("profile")
        raw_user = raw.get("user")
        raw_voice = raw.get("voice")
        raw_presentation = raw.get("presentation")
        raw_ui = raw.get("ui")
        if isinstance(raw_profile, dict):
            data["profile"].update(raw_profile)
        if isinstance(raw_user, dict):
            data["user"].update(raw_user)
        if isinstance(raw_voice, dict):
            data["voice"].update(raw_voice)
        if isinstance(raw_presentation, dict):
            raw_camera = raw_presentation.get("camera")
            raw_lighting = raw_presentation.get("lighting")
            if isinstance(raw_camera, dict):
                data["presentation"]["camera"].update(raw_camera)
            if isinstance(raw_lighting, dict):
                data["presentation"]["lighting"].update(raw_lighting)
        if isinstance(raw_ui, dict):
            data["ui"].update(raw_ui)
    profile = data["profile"]
    user = data["user"]
    voice = data["voice"]
    presentation = data["presentation"]
    camera = presentation["camera"]
    lighting = presentation["lighting"]
    ui = data["ui"]
    presentation["configured"] = bool(presentation.get("configured"))
    profile["name"] = _safe_text(profile.get("name"), 32) or "起源"
    profile["avatar_data_url"] = _safe_text(profile.get("avatar_data_url"), 2_000_000)
    profile["body_preset"] = _safe_text(profile.get("body_preset"), 32) or "standard"
    profile["soul"] = str(profile.get("soul") if profile.get("soul") is not None else "")
    user["name"] = _safe_text(user.get("name"), 32)
    user["title"] = _safe_text(user.get("title"), 64)
    user["display_name"] = _safe_text(user.get("display_name"), 32)
    user["callsign"] = _safe_text(user.get("callsign"), 32)
    user["work"] = _safe_text(user.get("work"), 80)
    user["avatar_data_url"] = _safe_text(user.get("avatar_data_url"), 2_000_000)
    user["profile_summary"] = _safe_text(user.get("profile_summary"), 1200)
    user["context_enabled"] = bool(user.get("context_enabled", True))
    voice["reply_read_aloud"] = bool(voice.get("reply_read_aloud"))
    preset_id = _safe_text(voice.get("preset_id"), 32) or "qiyuan_clear"
    voice["preset_id"] = preset_id if preset_id in VOICE_PRESET_IDS else "custom"
    voice["system_voice_name"] = _safe_text(voice.get("system_voice_name"), 160)
    voice["custom_voice_name"] = _safe_text(voice.get("custom_voice_name"), 80)
    voice["custom_voice_path"] = _safe_text(voice.get("custom_voice_path"), 1000)
    output_mode = _safe_text(voice.get("output_mode"), 32) or "auto"
    voice["output_mode"] = output_mode if output_mode in {"auto", "native_model", "edge_tts", "browser_tts"} else "auto"
    voice["native_voice_id"] = _safe_text(voice.get("native_voice_id"), 160)
    voice["sample_consent"] = bool(voice.get("sample_consent"))
    voice["lang"] = _safe_text(voice.get("lang"), 20) or "zh-CN"
    voice["rate"] = _safe_float(voice.get("rate"), 1.0, 0.5, 1.6)
    voice["pitch"] = _safe_float(voice.get("pitch"), 1.04, 0.5, 1.8)
    voice["volume"] = _safe_float(voice.get("volume"), 1.0, 0.0, 1.0)
    camera["focus"] = _safe_float(camera.get("focus"), 0.0, -0.5, 0.5)
    camera["height"] = _safe_float(camera.get("height"), 0.0, -0.5, 0.5)
    camera["distance"] = _safe_float(camera.get("distance"), 0.0, -2.0, 2.0)
    camera["side"] = _safe_float(camera.get("side"), 0.0, -1.0, 1.0)
    lighting["key"] = _safe_float(lighting.get("key"), 1.0, 0.15, 3.0)
    lighting["angle"] = _safe_float(lighting.get("angle"), 0.0, -1.8, 1.8)
    lighting["ambient"] = _safe_float(lighting.get("ambient"), 1.0, 0.15, 2.4)
    lighting["exposure"] = _safe_float(lighting.get("exposure"), 1.0, 0.55, 1.9)
    theme_style = _safe_text(ui.get("theme_style"), 32) or "ink_teal"
    ui["theme_style"] = theme_style if theme_style in {"ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"} else "ink_teal"
    data["schema"] = DEFAULT_BODY_SETTINGS["schema"]
    return data


def _read_raw_settings() -> dict[str, Any]:
    try:
        if BODY_SETTINGS_LUJING.exists():
            raw = json.loads(BODY_SETTINGS_LUJING.read_text(encoding="utf-8-sig"))
            return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}
    return {}


def load_body_settings() -> dict[str, Any]:
    data = _merge_settings(_read_raw_settings())
    if not data["profile"].get("soul"):
        data["profile"]["soul"] = _read_soul()
    return _public_settings(data)


def save_body_settings(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    current = _merge_settings(_read_raw_settings())
    body = payload if isinstance(payload, dict) else {}
    profile_payload = body.get("profile") if isinstance(body.get("profile"), dict) else {}
    user_payload = body.get("user") if isinstance(body.get("user"), dict) else {}
    voice_payload = body.get("voice") if isinstance(body.get("voice"), dict) else {}
    presentation_payload = body.get("presentation") if isinstance(body.get("presentation"), dict) else {}
    camera_payload = presentation_payload.get("camera") if isinstance(presentation_payload.get("camera"), dict) else {}
    lighting_payload = presentation_payload.get("lighting") if isinstance(presentation_payload.get("lighting"), dict) else {}
    ui_payload = body.get("ui") if isinstance(body.get("ui"), dict) else {}

    profile = current["profile"]
    user = current["user"]
    voice = current["voice"]
    camera = current["presentation"]["camera"]
    lighting = current["presentation"]["lighting"]
    ui = current["ui"]

    if "name" in profile_payload or "persona_name" in body:
        profile["name"] = _safe_text(profile_payload.get("name") or body.get("persona_name"), 32) or profile["name"]
    if "avatar_data_url" in profile_payload:
        profile["avatar_data_url"] = _safe_text(profile_payload.get("avatar_data_url"), 2_000_000)
    if "body_preset" in profile_payload:
        profile["body_preset"] = _safe_text(profile_payload.get("body_preset"), 32) or profile["body_preset"]
    if "soul" in profile_payload or "soul" in body:
        profile["soul"] = str(profile_payload.get("soul") if "soul" in profile_payload else body.get("soul") or "")

    if "name" in user_payload or "user_name" in body:
        user["name"] = _safe_text(user_payload.get("name") or body.get("user_name"), 32)
    if "title" in user_payload or "user_title" in body:
        user["title"] = _safe_text(user_payload.get("title") or body.get("user_title"), 64)
    if "avatar_data_url" in user_payload or "user_avatar_data_url" in body:
        user["avatar_data_url"] = _safe_text(user_payload.get("avatar_data_url") or body.get("user_avatar_data_url"), 2_000_000)
    if "display_name" in user_payload:
        user["display_name"] = _safe_text(user_payload.get("display_name"), 32)
    if "callsign" in user_payload:
        user["callsign"] = _safe_text(user_payload.get("callsign"), 32)
    if "work" in user_payload:
        user["work"] = _safe_text(user_payload.get("work"), 80)
    if "profile_summary" in user_payload:
        user["profile_summary"] = _safe_text(user_payload.get("profile_summary"), 1200)
    if "context_enabled" in user_payload:
        user["context_enabled"] = bool(user_payload.get("context_enabled"))

    if "reply_read_aloud" in voice_payload:
        voice["reply_read_aloud"] = bool(voice_payload.get("reply_read_aloud"))
    if "preset_id" in voice_payload:
        preset_id = _safe_text(voice_payload.get("preset_id"), 32)
        voice["preset_id"] = preset_id if preset_id in VOICE_PRESET_IDS else "custom"
    if "system_voice_name" in voice_payload:
        voice["system_voice_name"] = _safe_text(voice_payload.get("system_voice_name"), 160)
    if "custom_voice_name" in voice_payload:
        voice["custom_voice_name"] = _safe_text(voice_payload.get("custom_voice_name"), 80)
    if "custom_voice_path" in voice_payload:
        voice["custom_voice_path"] = _safe_text(voice_payload.get("custom_voice_path"), 1000)
    if "output_mode" in voice_payload:
        voice["output_mode"] = _safe_text(voice_payload.get("output_mode"), 32) or voice["output_mode"]
    if "native_voice_id" in voice_payload:
        voice["native_voice_id"] = _safe_text(voice_payload.get("native_voice_id"), 160)
    if "sample_consent" in voice_payload:
        voice["sample_consent"] = bool(voice_payload.get("sample_consent"))
    if "lang" in voice_payload:
        voice["lang"] = _safe_text(voice_payload.get("lang"), 20) or voice["lang"]
    if "rate" in voice_payload:
        voice["rate"] = _safe_float(voice_payload.get("rate"), voice["rate"], 0.5, 1.6)
    if "pitch" in voice_payload:
        voice["pitch"] = _safe_float(voice_payload.get("pitch"), voice["pitch"], 0.5, 1.8)
    if "volume" in voice_payload:
        voice["volume"] = _safe_float(voice_payload.get("volume"), voice["volume"], 0.0, 1.0)
    if "focus" in camera_payload:
        camera["focus"] = _safe_float(camera_payload.get("focus"), camera["focus"], -0.5, 0.5)
    if "height" in camera_payload:
        camera["height"] = _safe_float(camera_payload.get("height"), camera["height"], -0.5, 0.5)
    if "distance" in camera_payload:
        camera["distance"] = _safe_float(camera_payload.get("distance"), camera["distance"], -2.0, 2.0)
    if "side" in camera_payload:
        camera["side"] = _safe_float(camera_payload.get("side"), camera["side"], -1.0, 1.0)
    if "key" in lighting_payload:
        lighting["key"] = _safe_float(lighting_payload.get("key"), lighting["key"], 0.15, 3.0)
    if "angle" in lighting_payload:
        lighting["angle"] = _safe_float(lighting_payload.get("angle"), lighting["angle"], -1.8, 1.8)
    if "ambient" in lighting_payload:
        lighting["ambient"] = _safe_float(lighting_payload.get("ambient"), lighting["ambient"], 0.15, 2.4)
    if "exposure" in lighting_payload:
        lighting["exposure"] = _safe_float(lighting_payload.get("exposure"), lighting["exposure"], 0.55, 1.9)
    if camera_payload or lighting_payload:
        current["presentation"]["configured"] = True
    if "theme_style" in ui_payload:
        theme_style = _safe_text(ui_payload.get("theme_style"), 32)
        ui["theme_style"] = theme_style if theme_style in {"ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"} else ui["theme_style"]

    current = _merge_settings(current)
    from .settings_persistence import atomic_write_json

    atomic_write_json(BODY_SETTINGS_LUJING, current, backup=False)

    if "soul" in profile_payload or "soul" in body:
        SOUL_LUJING.parent.mkdir(parents=True, exist_ok=True)
        SOUL_LUJING.write_text(current["profile"].get("soul") or "", encoding="utf-8")

    return _public_settings(current)


def _public_settings(data: dict[str, Any]) -> dict[str, Any]:
    current = _merge_settings(data)
    custom_path = current["voice"].get("custom_voice_path") or ""
    custom_state = "empty"
    if custom_path:
        try:
            custom_state = "available" if Path(custom_path).expanduser().exists() else "missing"
        except Exception:
            custom_state = "unknown"
    return {
        "ok": True,
        "schema": current["schema"],
        "profile": current["profile"],
        "user": current["user"],
        "presentation": current["presentation"],
        "ui": current["ui"],
        "voice": {
            **current["voice"],
            "custom_voice_state": custom_state,
        },
        "voice_presets": deepcopy(VOICE_PRESETS),
    }
