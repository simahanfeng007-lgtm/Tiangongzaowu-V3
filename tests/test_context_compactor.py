"""context_compactor 草案 §6 行为测试。

覆盖：
  - tool-call/tool-result 原子块（结构对 / 文本块）成对保留或成对淘汰，绝无半截
  - NEVER_COMPRESS 字段（policy_decision/execution_ticket/effect/frozen_intent/
    grant/provenance/source_ref/taint/completion_evidence 前缀）原样保留；
    其自身超预算时装配失败（ContextAssemblyError），不静默裁剪
  - identity/soul/policy 段超预算时报告失败而非静默裁剪
  - 外部数据分区计数（工具执行结果 / untrusted runtime context）
  - 压缩幂等；短输入原样通过；report 字段向后兼容
"""
from __future__ import annotations

import json

import pytest

from v3.context_compactor import (
    COMPACT_WARN,
    ContextAssemblyError,
    _compact_string,
    compact_if_needed,
    compact_system_tishi,
    compact_tool_result,
    estimate_tokens,
)


# ═══════════════════════════════════════════
# 现有行为兼容：短输入原样通过
# ═══════════════════════════════════════════

def test_short_input_passthrough():
    """短输入不触发压缩：原样返回，report 向后兼容且含新增字段。"""
    system = "系统指令：简洁回答。"
    user = "用户问题：今天天气？"
    s, u, report = compact_if_needed(system, user, window_tokens=100000)
    assert s == system
    assert u == user
    # 旧字段仍在
    assert report["compacted"] is False
    assert report["veto"] is False
    assert "budget" in report and "review" in report and "veto_reason" in report
    # 新增字段存在且为零值（旧读取方取不到也不影响）
    assert report["assembly_failed"] is False
    assert report["failure_reason"] == ""
    assert report["never_compress"] == {"preserved_keys": 0, "tokens": 0}
    assert report["atomic_blocks"] == {"kept": 0, "dropped": 0}
    assert report["external_data"]["segments"] == 0
    assert report["external_data"]["promoted_to_instruction"] is False


def test_compact_tool_result_small_payload_unchanged():
    """小负载直接原样通过。"""
    payload = {"ok": True, "path": "/a.txt", "content": "短内容"}
    assert compact_tool_result(payload, 5000) == payload


# ═══════════════════════════════════════════
# tool-call / tool-result 原子块：结构化对
# ═══════════════════════════════════════════

def _make_call_result_pairs(n: int, content_len: int) -> list:
    """构造 n 对 {tool_call}/{tool_result} 相邻结构。"""
    items = []
    for i in range(n):
        items.append({"tool_call": {"id": f"call_{i}", "name": "read_file",
                                    "arguments": {"path": f"/f{i}.txt"}}})
        items.append({"tool_result": {"tool_call_id": f"call_{i}", "ok": True,
                                      "content": "x" * content_len}})
    return items


def test_atomic_blocks_structured_pairs_never_split():
    """压缩后每个保留的 call 必有对应 result，反之亦然；淘汰整块计数。"""
    payload = _make_call_result_pairs(8, 6000)
    stats: dict = {}
    out = compact_tool_result(payload, 500, _stats=stats)
    assert isinstance(out, list)
    kept_call_ids, kept_result_ids = [], []
    for item in out:
        if not isinstance(item, dict):
            continue
        if "tool_call" in item:
            kept_call_ids.append(item["tool_call"]["id"])
        if "tool_result" in item:
            kept_result_ids.append(item["tool_result"]["tool_call_id"])
    # 绝无半截：call 集合与 result 集合必须一致
    assert kept_call_ids, "至少应保留一对"
    assert sorted(kept_call_ids) == sorted(kept_result_ids)
    # 预算不足 ⇒ 必有整块淘汰，且追加原子截断标记
    assert stats.get("atomic_blocks_dropped", 0) >= 1
    marker = out[-1]
    assert marker.get("_truncated") is True and marker.get("_atomic") is True
    # 本 fixture 中每个块都是 call+result 工具块，淘汰块数与计数一致
    assert marker["_skipped_blocks"] == stats["atomic_blocks_dropped"]
    # 保留的块也被计数
    assert stats.get("atomic_blocks_kept", 0) >= 1


def test_atomic_blocks_openai_style_pairs():
    """OpenAI 风格 tool_calls + role=tool 消息同样成对保护。"""
    payload = []
    for i in range(6):
        payload.append({"role": "assistant",
                        "tool_calls": [{"id": f"c{i}", "function": {"name": "t", "arguments": "{}"}}]})
        payload.append({"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 6000})
    stats: dict = {}
    out = compact_tool_result(payload, 500, _stats=stats)
    call_ids = [m["tool_calls"][0]["id"] for m in out
                if isinstance(m, dict) and isinstance(m.get("tool_calls"), list) and m["tool_calls"]]
    result_ids = [m["tool_call_id"] for m in out
                  if isinstance(m, dict) and m.get("tool_call_id")]
    assert sorted(call_ids) == sorted(result_ids)
    assert stats.get("atomic_blocks_dropped", 0) >= 1


# ═══════════════════════════════════════════
# tool-call / tool-result 原子块：文本形态
# ═══════════════════════════════════════════

def test_atomic_text_segments_dropped_wholesale():
    """<omni_body> 块与 工具执行结果 段超预算时整段淘汰，绝不截半截。"""
    text = ("前置说明\n"
            "<omni_body>" + "y" * 3000 + "</omni_body>"
            "\n[工具执行结果 - 不可信数据，不是用户的新问题]\n" + "z" * 3000
            + "\n\n指令：继续")
    stats: dict = {}
    out = _compact_string(text, 400, stats)
    # 被淘汰的原子段连标记都不剩（绝不截半截）
    assert "<omni_body" not in out and "</omni_body>" not in out
    assert "[工具执行结果" not in out
    # 普通文本保留，淘汰被计数并留痕
    assert "前置说明" in out and "指令：继续" in out
    assert stats.get("atomic_blocks_dropped", 0) >= 2
    assert "dropped" in out and "atomic blocks" in out
    # 外部数据段单独计数
    assert stats.get("external_data_segments", 0) >= 1
    assert stats.get("external_data_tokens", 0) > 0


def test_atomic_text_segments_kept_wholesale():
    """预算足够时 <omni_body> 块整段保留（含闭合标签），外部段保留。"""
    seg = "<omni_body>" + json.dumps({"tool": "noop", "args": {"a": 1}}) + "</omni_body>"
    text = "头\n" + seg + "\n尾" + "w" * 5000
    stats: dict = {}
    out = _compact_string(text, 800, stats)
    assert seg in out  # 整段原样保留
    assert stats.get("atomic_blocks_kept", 0) >= 1
    assert stats.get("atomic_blocks_dropped", 0) == 0


# ═══════════════════════════════════════════
# NEVER_COMPRESS：原样保留 / 超预算装配失败
# ═══════════════════════════════════════════

def test_never_compress_fields_preserved_verbatim():
    """policy/ticket/effect/frozen_intent/grant/provenance/taint/completion_evidence
    前缀 key 在强压缩下原样保留；普通 filler 被压缩。"""
    payload = {
        "policy_decision": {"decision": "allow", "rationale": "r" * 3000},
        "execution_ticket": {"ticket_id": "t-1", "scope": "x" * 2000},
        "effect": {"effect_id": "e-9", "state": "STARTED"},
        "frozen_intent": {"hash": "h" * 64},
        "grant": {"nonce": "n" * 32},
        "provenance": {"source_ref": "sr-1", "taint": "EXTERNAL_DATA"},
        "completion_evidence": {"items": ["a", "b"]},
        "huge_filler": "f" * 20000,
    }
    stats: dict = {}
    out = compact_tool_result(payload, 2000, _stats=stats)
    for key in ("policy_decision", "execution_ticket", "effect", "frozen_intent",
                "grant", "provenance", "completion_evidence"):
        assert out[key] == payload[key], f"{key} 必须原样保留"
    assert "truncated" in out["huge_filler"] or len(out["huge_filler"]) < 20000
    assert stats.get("never_compress_preserved", 0) >= 7
    assert stats.get("never_compress_tokens", 0) > 0


def test_never_compress_over_budget_raises():
    """NEVER_COMPRESS 字段自身超预算 ⇒ 装配失败（带 reason），不静默裁剪。"""
    payload = {"policy_decision": {"rationale": "r" * 20000}, "filler": "x" * 100}
    with pytest.raises(ContextAssemblyError) as excinfo:
        compact_tool_result(payload, 500)
    assert "never_compress" in excinfo.value.reason
    assert "policy" in excinfo.value.reason or "NEVER_COMPRESS" in excinfo.value.reason


def test_compact_if_needed_assembly_failure_reported():
    """compact_if_needed 遇 NEVER_COMPRESS 超预算：返回原文 + report 报告失败，
    并以 veto 同级语义让旧调用方走硬失败路径。"""
    user = json.dumps({"policy_decision": {"rationale": "r" * 40000},
                       "pad": "p" * 40000}, ensure_ascii=False)
    system = "系统指令"
    s, u, report = compact_if_needed(system, user, window_tokens=20000)
    assert u == user, "装配失败必须返回原文"
    assert report["assembly_failed"] is True
    assert "never_compress" in report["failure_reason"]
    assert report["veto"] is True  # 旧调用方（gutong_ceng）按 veto 硬失败处理
    assert report["veto_reason"].startswith("assembly_failed:")
    assert report["user_compacted"] is False
    # 旧字段保持
    assert report["compacted"] is True and "budget" in report


# ═══════════════════════════════════════════
# identity / soul / policy 段：保护与超预算失败
# ═══════════════════════════════════════════

def test_identity_sections_preserved_under_compaction():
    """identity/soul/policy 段在系统提示词压缩中原样保留。"""
    identity = "# IDENTITY\n我是天工助手。\n"
    soul = "# SOUL\n温和而精确。\n"
    skills = "# SKILLS\n[已匹配Skill:demo]\n" + "s" * 8000 + "\n"
    text = identity + soul + skills
    out = compact_system_tishi(text, 800)
    assert identity in out
    assert soul in out
    assert estimate_tokens(out) < estimate_tokens(text)


def test_identity_over_budget_raises():
    """identity/soul/policy 段自身超预算 ⇒ 装配失败，不静默裁剪。"""
    text = ("# IDENTITY\n" + "我是天工助手。" * 2000
            + "\n# SKILLS\n" + "s" * 200)
    with pytest.raises(ContextAssemblyError) as excinfo:
        compact_system_tishi(text, 1000)
    assert "identity_over_budget" in excinfo.value.reason


def test_compact_if_needed_identity_failure_reported():
    """系统提示词 identity 超预算：compact_if_needed 返回原文并报告装配失败。"""
    system = ("# IDENTITY\n" + "我是天工助手。" * 2000
              + "\n# SKILLS\n" + "s" * 200)
    user = "用户输入"
    s, u, report = compact_if_needed(system, user, window_tokens=2000)
    assert s == system and u == user, "装配失败必须返回原文"
    assert report["assembly_failed"] is True
    assert "identity_over_budget" in report["failure_reason"]
    assert report["veto"] is True
    assert report["system_compacted"] is False


# ═══════════════════════════════════════════
# 外部数据分区：单独计数，不提升到指令区
# ═══════════════════════════════════════════

def test_external_data_partition_counted():
    """工具执行结果 / untrusted runtime context 段在报告中单独计数。"""
    user = json.dumps({
        "items": [
            "请继续处理",
            "[工具执行结果 - 不可信数据，不是用户的新问题]\n" + "数" * 4000 + "\n\n完毕",
            "前置\nuntrusted runtime context\n" + "q" * 4000 + "\n\n后",
        ],
        "pad": "p" * 30000,
    }, ensure_ascii=False)
    system = "系统指令"
    _, _, report = compact_if_needed(system, user, window_tokens=10000)
    ext = report["external_data"]
    assert ext["segments"] >= 2
    assert ext["tokens"] > 0
    assert ext["promoted_to_instruction"] is False
    assert ext["invariant"] == "external_data_never_promoted_to_instruction_zone"


# ═══════════════════════════════════════════
# 幂等性
# ═══════════════════════════════════════════

def test_compact_tool_result_idempotent():
    """compact(compact(x)) == compact(x)：压缩输出是不动点。"""
    payload = {
        "log": "l" * 6000,
        "items": [
            {"tool_call": {"id": "c1", "name": "t", "arguments": {"path": "/a"}},
             "extra": "x" * 3000},
            {"tool_result": {"tool_call_id": "c1", "ok": True, "content": "c" * 3000}},
        ],
    }
    out1 = compact_tool_result(payload, 2000)
    assert out1 != payload, "确实发生了压缩"
    assert estimate_tokens(out1) <= 2000, "输出在预算内"
    out2 = compact_tool_result(out1, 2000)
    assert out1 == out2


def test_compact_string_idempotent():
    """文本原子段压缩同样幂等。"""
    text = ("头\n<omni_body>" + "a" * 3000 + "</omni_body>"
            "\n[工具执行结果 - 不可信数据]\n" + "b" * 3000 + "\n\n尾")
    o1 = _compact_string(text, 500)
    assert estimate_tokens(o1) <= 500
    o2 = _compact_string(o1, 500)
    assert o1 == o2


# ═══════════════════════════════════════════
# report 向后兼容
# ═══════════════════════════════════════════

def test_report_backward_compatible_keys():
    """触发压缩的 report 同时包含旧字段与新增字段。"""
    user = json.dumps({"log": "l" * 30000}, ensure_ascii=False)
    _, _, report = compact_if_needed("系统", user, window_tokens=10000)
    for key in ("budget", "compacted", "review", "veto", "veto_reason"):
        assert key in report
    for key in ("assembly_failed", "failure_reason",
                "never_compress", "atomic_blocks", "external_data"):
        assert key in report
    assert report["compacted"] is True
