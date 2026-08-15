"""One-shot P18.1 source patch for the large Zongdiaodu/Gutong files.

This script is deliberately deterministic and assertion-heavy. It edits the
existing production chain in place; it does not add a parallel Runtime.
Remove it after the branch has been patched and certified.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ZONG = ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "zongdiaodu.py"
GUTONG = ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "gutong" / "gutong_ceng.py"
HTTP = ROOT / "app" / "backend" / "tiangong-backend" / "v3" / "jineng" / "http_kehuduan.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def patch_zong() -> None:
    text = ZONG.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "def _llm_jixu_scoped(payload: Any, on_chunk=None, on_reasoning_chunk=None) -> tuple[ShentiZhuangtai, str]:",
        "def _llm_jixu_scoped(\n"
        "            payload: Any, on_chunk=None, on_reasoning_chunk=None,\n"
        "            provider_turn: Any = None, provider_tool_results: list[dict[str, Any]] | None = None,\n"
        "        ) -> tuple[ShentiZhuangtai, str]:",
        label="zong jixu signature",
    )
    old_call = """return self.gutong.jixu(
                            system_tishi, payload, shenti, xiaoxi,
                            on_text_chunk=on_chunk,
                            on_reasoning_chunk=on_reasoning_chunk,
                            assistant_messages=prior_texts,
                            stable_user_message=cache_stable_user_message,
                        )"""
    new_call = """return self.gutong.jixu(
                            system_tishi, payload, shenti, xiaoxi,
                            on_text_chunk=on_chunk,
                            on_reasoning_chunk=on_reasoning_chunk,
                            assistant_messages=prior_texts,
                            stable_user_message=cache_stable_user_message,
                            provider_turn=provider_turn,
                            provider_tool_results=provider_tool_results,
                        )"""
    text = replace_once(text, old_call, new_call, label="zong scoped gutong call")
    old_call2 = """return self.gutong.jixu(
                    system_tishi, payload, shenti, xiaoxi,
                    on_text_chunk=on_chunk,
                    on_reasoning_chunk=on_reasoning_chunk,
                    assistant_messages=prior_texts,
                    stable_user_message=cache_stable_user_message,
                )"""
    new_call2 = """return self.gutong.jixu(
                    system_tishi, payload, shenti, xiaoxi,
                    on_text_chunk=on_chunk,
                    on_reasoning_chunk=on_reasoning_chunk,
                    assistant_messages=prior_texts,
                    stable_user_message=cache_stable_user_message,
                    provider_turn=provider_turn,
                    provider_tool_results=provider_tool_results,
                )"""
    text = replace_once(text, old_call2, new_call2, label="zong fallback gutong call")

    single_old = """shenti, next_huifu = _llm_jixu_scoped(
                    model_quality_payload,
                    on_chunk=_on_text_chunk,
                    on_reasoning_chunk=_on_reasoning_chunk,
                )"""
    single_new = """shenti, next_huifu = _llm_jixu_scoped(
                    model_quality_payload,
                    on_chunk=_on_text_chunk,
                    on_reasoning_chunk=_on_reasoning_chunk,
                    provider_turn=huifu,
                    provider_tool_results=[gongju_jieguo] if isinstance(gongju_jieguo, dict) else None,
                )"""
    text = replace_once(text, single_old, single_new, label="zong single native result")

    parallel_old = """shenti, next_huifu = _llm_jixu_scoped(
                        _simple_chain_model_payload(combined),
                        on_chunk=_on_text_chunk,
                        on_reasoning_chunk=_on_reasoning_chunk,
                    )"""
    parallel_new = """shenti, next_huifu = _llm_jixu_scoped(
                        _simple_chain_model_payload(combined),
                        on_chunk=_on_text_chunk,
                        on_reasoning_chunk=_on_reasoning_chunk,
                        provider_turn=huifu,
                        provider_tool_results=[
                            raw for _tn, _ta, raw, _call_id, _index in parallel_results
                            if isinstance(raw, dict)
                        ],
                    )"""
    text = replace_once(text, parallel_old, parallel_new, label="zong parallel native results")
    ZONG.write_text(text, encoding="utf-8")


def patch_gutong() -> None:
    text = GUTONG.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "        stable_user_message: str = \"\",\n    ) -> tuple[ShentiZhuangtai, str]:",
        "        stable_user_message: str = \"\",\n"
        "        provider_turn: Any = None,\n"
        "        provider_tool_results: list[dict[str, Any]] | None = None,\n"
        "    ) -> tuple[ShentiZhuangtai, str]:",
        label="gutong jixu signature",
    )
    old = """huifu = self.llm(
                system_tishi,
                yonghu_tishi,
                on_text_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message or None,
            )"""
    new = """huifu = self.llm(
                system_tishi,
                yonghu_tishi,
                on_text_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message or None,
                prior_provider_turn=provider_turn,
                provider_tool_results=provider_tool_results,
            )"""
    text = replace_once(text, old, new, label="gutong llm native metadata")
    GUTONG.write_text(text, encoding="utf-8")


def patch_http() -> None:
    text = HTTP.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "        prior_assistant_messages: list[Any] | None = None,\n        stable_user_message: str | None = None,\n    ) -> str:",
        "        prior_assistant_messages: list[Any] | None = None,\n"
        "        stable_user_message: str | None = None,\n"
        "        prior_provider_turn: Any = None,\n"
        "        provider_tool_results: list[dict[str, Any]] | None = None,\n"
        "    ) -> str:",
        label="http llm signature",
    )
    anchor = """            payload = MOXING_SHIPEI.goujian_qingqiu(
                pid,
                effective_system_tishi,
                yonghu_tishi,
                st,
                gongju_dingyi=gongju_dingyi,
                model_name=model_name,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message,
            )"""
    replacement = anchor + """
            if isinstance(prior_provider_turn, ProviderTurnEnvelope) and provider_tool_results:
                # Internal transport metadata only. Transports remove these keys
                # before network release and bind results through ToolCallBinding.
                payload["__provider_turn"] = prior_provider_turn
                payload["__provider_tool_results"] = [
                    dict(item) for item in provider_tool_results if isinstance(item, dict)
                ]"""
    text = replace_once(text, anchor, replacement, label="http provider context attach")

    lambda1 = """return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None: self.llm_diaoyong(
                system,
                user,
                identity,
                on_text_chunk=on_text_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message,
            )"""
    lambda1_new = """return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None, prior_provider_turn=None, provider_tool_results=None: self.llm_diaoyong(
                system,
                user,
                identity,
                on_text_chunk=on_text_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
                prior_assistant_messages=prior_assistant_messages,
                stable_user_message=stable_user_message,
                prior_provider_turn=prior_provider_turn,
                provider_tool_results=provider_tool_results,
            )"""
    text = replace_once(text, lambda1, lambda1_new, label="http bound callback")
    lambda2 = """return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None: self.llm_diaoyong(
            system,
            user,
            on_text_chunk=on_text_chunk,
            on_reasoning_chunk=on_reasoning_chunk,
            prior_assistant_messages=prior_assistant_messages,
            stable_user_message=stable_user_message,
        )"""
    lambda2_new = """return lambda system, user, on_text_chunk=None, on_reasoning_chunk=None, prior_assistant_messages=None, stable_user_message=None, prior_provider_turn=None, provider_tool_results=None: self.llm_diaoyong(
            system,
            user,
            on_text_chunk=on_text_chunk,
            on_reasoning_chunk=on_reasoning_chunk,
            prior_assistant_messages=prior_assistant_messages,
            stable_user_message=stable_user_message,
            prior_provider_turn=prior_provider_turn,
            provider_tool_results=provider_tool_results,
        )"""
    text = replace_once(text, lambda2, lambda2_new, label="http default callback")
    HTTP.write_text(text, encoding="utf-8")


def main() -> None:
    patch_zong()
    patch_gutong()
    patch_http()
    print("P18.1 native roundtrip source patch applied")


if __name__ == "__main__":
    main()
