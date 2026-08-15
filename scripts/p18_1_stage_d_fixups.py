"""Small deterministic Stage-D fixups after the settings migration."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = ROOT / "app/backend/tiangong-backend/v3/model_endpoint.py"
BRIDGE = ROOT / "app/backend/tiangong-backend/v3/duihua_qiaojie.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


def patch_endpoint() -> None:
    text = ENDPOINT.read_text(encoding="utf-8")
    for old, new, label in (
        ('"deepseek", "deepseek_v4", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', '"deepseek", "deepseek", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', "deepseek literal identity"),
        ('"zhipu", "glm_5_2", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', '"zhipu", "zhipu", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', "zhipu literal identity"),
        ('"minimax", "minimax_m3", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', '"minimax", "minimax", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', "minimax literal identity"),
    ):
        text = replace_once(text, old, new, label)
    old = '''    reasoning_mode = ""
    try:
        reasoning = peizhi.duqu_model_reasoning_config(optimization_family, base_url, model_name)
        reasoning_mode = str(reasoning.get("configured_mode") or "")
    except Exception:
        reasoning_mode = ""
'''
    new = '''    # Endpoint-scoped raw reasoning is authoritative for unknown models. Known
    # model family settings remain compatible with the existing L4 config.
    reasoning_mode = str(profile.get("reasoning_mode") or "").strip()
    if not reasoning_mode:
        try:
            reasoning = peizhi.duqu_model_reasoning_config(optimization_family, base_url, model_name)
            reasoning_mode = str(reasoning.get("configured_mode") or "")
        except Exception:
            reasoning_mode = ""
'''
    text = replace_once(text, old, new, "endpoint reasoning precedence")
    ENDPOINT.write_text(text, encoding="utf-8")


def patch_bridge() -> None:
    text = BRIDGE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    from .settings_persistence import atomic_write_json\n",
        "",
        "remove nonexistent settings_persistence import",
    )
    text = replace_once(
        text,
        "    atomic_write_json(API_PEIZHI_LUJING, data)",
        "    _atomic_write_json(API_PEIZHI_LUJING, data)",
        "use bridge atomic writer",
    )
    BRIDGE.write_text(text, encoding="utf-8")


patch_endpoint()
patch_bridge()
print("P18.1 Stage D endpoint fixups applied")
