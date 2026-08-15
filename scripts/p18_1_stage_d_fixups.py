"""Small deterministic and idempotent Stage-D fixups."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = ROOT / "app/backend/tiangong-backend/v3/model_endpoint.py"
BRIDGE = ROOT / "app/backend/tiangong-backend/v3/duihua_qiaojie.py"


def ensure_replaced(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1:
        return text.replace(old, new, 1)
    if old_count == 0 and new_count >= 1:
        return text
    raise RuntimeError(f"{label}: unexpected old={old_count} new={new_count}")


def patch_endpoint() -> None:
    text = ENDPOINT.read_text(encoding="utf-8")
    for old, new, label in (
        ('"deepseek", "deepseek_v4", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', '"deepseek", "deepseek", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', "deepseek literal identity"),
        ('"zhipu", "glm_5_2", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', '"zhipu", "zhipu", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', "zhipu literal identity"),
        ('"minimax", "minimax_m3", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', '"minimax", "minimax", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,', "minimax literal identity"),
    ):
        text = ensure_replaced(text, old, new, label)
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
    text = ensure_replaced(text, old, new, "endpoint reasoning precedence")
    ENDPOINT.write_text(text, encoding="utf-8")


def patch_bridge() -> None:
    text = BRIDGE.read_text(encoding="utf-8")
    old_import = "    from .settings_persistence import atomic_write_json\n"
    if old_import in text:
        text = text.replace(old_import, "", 1)
    old_call = "    atomic_write_json(API_PEIZHI_LUJING, data)"
    new_call = "    _atomic_write_json(API_PEIZHI_LUJING, data)"
    text = ensure_replaced(text, old_call, new_call, "use bridge atomic writer")
    BRIDGE.write_text(text, encoding="utf-8")


patch_endpoint()
patch_bridge()
print("P18.1 Stage D endpoint fixups applied")
