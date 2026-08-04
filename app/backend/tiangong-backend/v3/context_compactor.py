"""
上下文压缩器 — 第一性原理驱动
═══════════════════════════════════════════
v3 每次 LLM 调用仅发送 system + user 两条消息，
无历史累积。上下文爆炸风险全在单条消息的体积上：
  - system_tishi = 灵魂指令 + 工具定义 + skills 注入 + 动态上下文
  - yonghu_tishi  = 原始请求 + 工具结果 JSON + 质检分析

核心机制：
  ① Token 估算（字符启发式，中文 1.6 字/token，拉丁 3.5 字/token）
  ② 规则裁剪（键值限制、列表截断、重复折叠）
  ③ 第一性原理原子事实提取
  ④ 对抗性审查评分（四维，原子事实覆盖率一票否决）
  ⑤ tool-call/tool-result 原子块保护：消息列表与文本段按 call+result 成块
     （含 {tool_call:…}/{tool_result:…} 结构对、<omni_body>…</omni_body>
     文本块、"工具执行结果"/untrusted 标记段），整块保留或整块淘汰并计数，
     绝不单截一侧、绝不截半截
  ⑥ 审查 veto 硬失败：compact_if_needed 在 veto 时返回原文并置
     report["veto"]=True，绝不把失真压缩结果交给调用方
  ⑦ NEVER_COMPRESS：policy_decision/execution_ticket/effect/frozen_intent/
     grant/provenance/source_ref/taint/completion_evidence 前缀 key，
     以及系统提示词中的 identity/soul/policy 段，任何压缩路径下原样保留；
     其自身超预算时抛 ContextAssemblyError（装配失败），绝不静默裁剪
  ⑧ 外部数据结构化分区：工具执行结果 / untrusted runtime context 标记段
     单独计数（report["external_data"]）；压缩只发生在 yonghu_tishi 侧，
     外部数据永远不会被提升到 system 指令区
  ⑨ 压缩报告扩展字段（never_compress/atomic_blocks/external_data/
     assembly_failed/failure_reason）向后兼容：旧读取方取不到的键不影响
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# ═══════════════════════════════════════════
# Token 估算
# ═══════════════════════════════════════════

_CJK_RANGE=re.compile(r'[一-鿿㐀-䶿豈-﫿]')
_LATIN_RANGE=re.compile(r'[a-zA-Z0-9\s]')
CHARS_PER_TOKEN_CJK=1.6
CHARS_PER_TOKEN_LATIN=3.5
CHARS_PER_TOKEN_OTHER=2.5

def _detect_window_tokens() -> int:
    """从 model_stream_config 获取当前模型的上下文窗口。"""
    try:
        from .model_stream_config import get_context_window
        from .peizhi import duqu_moren_provider, infer_provider_id, MOREN_PROVIDER
        pid = infer_provider_id(duqu_moren_provider(MOREN_PROVIDER))
        window = get_context_window(pid)
        return int(os.environ.get("TIANGONG_CONTEXT_WINDOW_TOKENS", str(window)))
    except Exception:
        pass
    return int(os.environ.get("TIANGONG_CONTEXT_WINDOW_TOKENS", "262144"))

DEFAULT_WINDOW_TOKENS = _detect_window_tokens()
SYSTEM_BUDGET_PCT = 0.40
TOOL_RESULT_BUDGET_PCT = 0.50

# 压缩触发阈值 — 窗口占比，按模型窗口自动适配
COMPACT_WARN = float(os.environ.get("COMPACT_WARN", "0.70"))
COMPACT_URGENT = float(os.environ.get("COMPACT_URGENT", "0.85"))

MAX_LIST_ITEMS=int(os.environ.get("COMPACT_MAX_LIST_ITEMS","200"))
MAX_STRING_LEN=int(os.environ.get("COMPACT_MAX_STRING_LEN","8000"))
MAX_DICT_KEYS=int(os.environ.get("COMPACT_MAX_DICT_KEYS","60"))
MAX_NEST_DEPTH=8


class ContextAssemblyError(RuntimeError):
    """上下文装配失败：NEVER_COMPRESS 字段或 identity/soul/policy 段自身
    超出预算。携带 reason；调用方必须如实报错/中止，绝不静默裁剪这些内容。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# NEVER_COMPRESS key 前缀（小写、去前导下划线后比较）：
# 政策决定/执行票据/effect/frozen intent/授权/来源引用/taint/最低完成证据
_NEVER_COMPRESS_PREFIXES=(
    'policy_decision','execution_ticket','effect','frozen_intent',
    'grant','provenance','source_ref','taint','completion_evidence',
)


def estimate_tokens(text: Any) -> int:
    """启发式 token 估算"""
    if not isinstance(text, str):
        try:
            s = json.dumps(text, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(text)
    else:
        s = text
    if not s:
        return 0
    cjk = len(_CJK_RANGE.findall(s))
    latin = len(_LATIN_RANGE.findall(s))
    other = len(s) - cjk - latin
    return max(1, int(cjk / CHARS_PER_TOKEN_CJK + latin / CHARS_PER_TOKEN_LATIN + other / CHARS_PER_TOKEN_OTHER))


_PROTECTED_KEYS={'path','paths','file','filename','id','request_id','status','error','ok','action','tool','name','type','line','row','col','sha256','hash','mode','encoding','exit_code','code','count','total','size','length','lines','stderr','stdout','command','args',
                 'role','content','tool_calls','tool_call_id','function','arguments'}


def _is_protected(key: str) -> bool:
    kl=key.lower().strip('_')
    return any(p in kl for p in _PROTECTED_KEYS)


# ═══════════════════════════════════════════
# NEVER_COMPRESS — 政策/票据/effect/provenance 永不压缩
# ═══════════════════════════════════════════

def _is_never_compress(key: Any) -> bool:
    """判定 key 是否命中 NEVER_COMPRESS 前缀（大小写不敏感，忽略前导下划线）。"""
    if not isinstance(key,str):
        return False
    kl=key.lower().lstrip('_')
    return any(kl.startswith(p) for p in _NEVER_COMPRESS_PREFIXES)


def _never_compress_skeleton(value: Any, depth: int=0) -> tuple[Any,int]:
    """抽取仅含 NEVER_COMPRESS 字段路径的最小骨架与命中字段数。

    用于预算可行性判定：骨架自身超预算 ⇒ 任何不裁剪这些字段的装配
    都不可能满足预算 ⇒ 必须装配失败，而不是静默裁剪这些字段。"""
    if depth>MAX_NEST_DEPTH+4:
        return None,0
    if isinstance(value,dict):
        out: dict={}
        count=0
        for k,v in value.items():
            if _is_never_compress(k):
                out[k]=v          # 原样带出，绝不截断
                count+=1
            else:
                sub,c=_never_compress_skeleton(v,depth+1)
                if c:
                    out[k]=sub
                    count+=c
        return (out,count) if count else (None,0)
    if isinstance(value,(list,tuple)):
        out_l: list=[]
        count=0
        for item in value:
            sub,c=_never_compress_skeleton(item,depth+1)
            if c:
                out_l.append(sub)
                count+=c
        return (out_l,count) if count else (None,0)
    return None,0


# ═══════════════════════════════════════════
# tool-call / tool-result 原子块保护
# ═══════════════════════════════════════════

_TOOL_RESULT_ROLES={'tool','tool_result','tool_response','function','observation'}

# 结构化 {tool_call:…} / {tool_result:…} 键形式
_TOOL_CALL_KEYS=('tool_call','tool_calls','function_call')
_TOOL_RESULT_KEYS=('tool_result','tool_response','tool_output')

# 文本形态原子段标记：<omni_body> 工具调用块、工具执行结果/untrusted 外部数据段
_OMNI_BODY_OPEN='<omni_body'
_OMNI_BODY_CLOSE='</omni_body>'
_TOOL_RESULT_TEXT_MARKERS=('[工具执行结果',)
_UNTRUSTED_CONTEXT_RE=re.compile(r'untrusted runtime context',re.IGNORECASE)


def _is_tool_call_message(item: Any) -> bool:
    """识别 assistant 的 tool_call（OpenAI 风格 / 本地类型 / {tool_call:…} 键形式）。"""
    if not isinstance(item, dict):
        return False
    if isinstance(item.get('tool_calls'), list) and item['tool_calls']:
        return True
    if any(k in item for k in _TOOL_CALL_KEYS):
        return True
    t=str(item.get('type') or item.get('leixing') or '').lower()
    return t in {'tool_call','function_call','tool_calls'}


def _is_tool_result_message(item: Any) -> bool:
    """识别 tool_result（role=tool / tool_call_id / 本地类型 / {tool_result:…} 键形式）。"""
    if not isinstance(item, dict):
        return False
    if item.get('tool_call_id'):
        return True
    role=str(item.get('role') or '').lower()
    if role in _TOOL_RESULT_ROLES:
        return True
    if any(k in item for k in _TOOL_RESULT_KEYS):
        return True
    t=str(item.get('type') or item.get('leixing') or '').lower()
    return t in {'tool_result','tool_response'}


def _is_text_tool_call(item: Any) -> bool:
    """文本形态工具调用：含 <omni_body>…</omni_body> 块。"""
    return isinstance(item,str) and _OMNI_BODY_OPEN in item


def _is_text_tool_result(item: Any) -> bool:
    """文本形态工具结果：'[工具执行结果' 或 'untrusted runtime context' 标记段。"""
    return isinstance(item,str) and (
        any(m in item for m in _TOOL_RESULT_TEXT_MARKERS)
        or _UNTRUSTED_CONTEXT_RE.search(item) is not None)


def _group_tool_atomic_blocks(data: list) -> list[list[int]] | None:
    """把消息/文本列表分组成原子块（下标组）。

    规则：tool_call（消息或 <omni_body> 文本块）与其后连续的 tool_result
    （消息或 工具执行结果/untrusted 文本段）为同一块；其余元素各自成块；
    不与前面 call 配对的孤儿 result 自成一块。
    列表不含任何 tool-call/result 结构时返回 None（走通用压缩）。
    """
    def _is_call(x: Any) -> bool:
        return _is_tool_call_message(x) or _is_text_tool_call(x)
    def _is_result(x: Any) -> bool:
        return _is_tool_result_message(x) or _is_text_tool_result(x)
    if not any(_is_call(x) or _is_result(x) for x in data):
        return None
    blocks: list[list[int]]=[]
    i=0
    n=len(data)
    while i<n:
        if _is_call(data[i]):
            block=[i]
            j=i+1
            while j<n and _is_result(data[j]):
                block.append(j)
                j+=1
            blocks.append(block)
            i=j
        else:
            blocks.append([i])
            i+=1
    return blocks


def _block_has_tool_content(data: list, block: list[int]) -> bool:
    """块内是否真含 tool-call/result 内容（原子块淘汰计数的口径）。"""
    return any(
        _is_tool_call_message(data[i]) or _is_tool_result_message(data[i])
        or _is_text_tool_call(data[i]) or _is_text_tool_result(data[i])
        for i in block)


def _compact_message_blocks(data: list, blocks: list[list[int]],
                            budget_tokens: int, depth: int,
                            _stats: dict | None=None) -> list:
    """按原子块压缩消息列表。

    块是保留/淘汰的最小单位：预算不足时整块丢弃并追加截断标记，
    绝不允许只保留 call 丢 result 或只保留 result 丢 call。
    单块自身超预算时在块内压缩（截断字符串/裁剪字段），块内消息一条不丢。
    淘汰/保留的含工具内容块数计入 _stats["atomic_blocks_*"]。
    """
    per_block=max(100, budget_tokens // max(1, min(len(blocks), MAX_LIST_ITEMS)))
    result: list=[]
    kept_blocks=0
    for block in blocks[:MAX_LIST_ITEMS]:
        per_message=max(50, per_block // max(1, len(block)))
        compacted_block=[_compact_value(data[i], per_message, depth+1, _stats) for i in block]
        if result and estimate_tokens(result + compacted_block) > budget_tokens:
            break
        result.extend(compacted_block)
        kept_blocks+=1
    dropped_blocks=blocks[kept_blocks:]
    skipped=sum(len(b) for b in dropped_blocks)
    if skipped>0:
        result.append({"_truncated": True, "_skipped": skipped,
                       "_skipped_blocks": len(dropped_blocks), "_atomic": True})
        if _stats is not None:
            # 只统计真正含 tool-call/result 内容的块，普通单元素块不计
            _stats["atomic_blocks_dropped"]=_stats.get("atomic_blocks_dropped",0)+sum(
                1 for b in dropped_blocks if _block_has_tool_content(data,b))
            _stats["atomic_blocks_kept"]=_stats.get("atomic_blocks_kept",0)+sum(
                1 for b in blocks[:kept_blocks] if _block_has_tool_content(data,b))
    return result


def _compact_dict(data: dict, budget_tokens: int, depth: int = 0,
                  _stats: dict | None=None) -> dict:
    if depth>MAX_NEST_DEPTH:
        # 触发深度上限也不裁剪 NEVER_COMPRESS 字段：本层命中者原样带出
        nc={k:v for k,v in data.items() if _is_never_compress(k)}
        if nc:
            nc["_truncated"]="max_depth"
            return nc
        return {"_truncated":"max_depth"}
    result: dict[str,Any]={}
    keys=list(data.keys())
    # ① NEVER_COMPRESS 字段：原样保留，不参与预算分摊、不参与键数裁剪
    for key in [k for k in keys if _is_never_compress(k)]:
        result[key]=data[key]
        if _stats is not None:
            _stats["never_compress_preserved"]=_stats.get("never_compress_preserved",0)+1
            _stats["never_compress_tokens"]=_stats.get("never_compress_tokens",0)+estimate_tokens(data[key])
    rest=[k for k in keys if not _is_never_compress(k)]
    safe=[k for k in rest if _is_protected(k)]
    other=[k for k in rest if not _is_protected(k)]
    ordered=safe+other
    for key in ordered[:MAX_DICT_KEYS]:
        value=data[key]
        vt=estimate_tokens(value)
        rt=budget_tokens-estimate_tokens(result)
        if vt<=max(50,rt//max(1,len(ordered)-ordered.index(key))):
            result[key]=value
        else:
            result[key]=_compact_value(value,max(50,rt//3),depth+1,_stats)
    if len(rest)>MAX_DICT_KEYS:
        result["_truncated_keys"]=len(rest)-MAX_DICT_KEYS
    return result


def _compact_list(data: list, budget_tokens: int, depth: int = 0,
                  _stats: dict | None=None) -> list:
    """压缩列表，保持元素类型一致。截断标记以 dict 形式插入。

    若列表是消息/文本序列且含 tool-call/tool-result 结构，则按原子块处理：
    call 与其 result 成对保留或成对淘汰，绝不单截。
    """
    if not data:
        return data
    blocks=_group_tool_atomic_blocks(data)
    if blocks is not None:
        return _compact_message_blocks(data, blocks, budget_tokens, depth, _stats)
    per_item = max(100, budget_tokens // min(len(data), MAX_LIST_ITEMS))
    result = []
    for item in data[:MAX_LIST_ITEMS]:
        result.append(_compact_value(item, per_item, depth + 1, _stats))
        if estimate_tokens(result) > budget_tokens:
            break
    if len(data) > len(result):
        # 截断标记与列表元素同类型，避免类型突变
        result.append({"_truncated": True, "_skipped": len(data) - len(result)})
    return result


def _find_atomic_text_spans(text: str) -> list[tuple[int,int,str]]:
    """定位文本中的原子段，返回 (start,end,kind) 列表（无则空表）。

    kind='omni_body'：<omni_body>…</omni_body>（未闭合则到文末）——工具调用文本块；
    kind='external'：'[工具执行结果' / 'untrusted runtime context' 标记段
    （标记行起 → 下一空行或文末）——属外部数据分区。
    """
    spans: list[tuple[int,int,str]]=[]
    pos=0
    while True:
        start=text.find(_OMNI_BODY_OPEN,pos)
        if start==-1:
            break
        close=text.find(_OMNI_BODY_CLOSE,start)
        end=close+len(_OMNI_BODY_CLOSE) if close!=-1 else len(text)
        spans.append((start,end,'omni_body'))
        pos=end
    for marker in _TOOL_RESULT_TEXT_MARKERS:
        for m in re.finditer(re.escape(marker)+r'[^\n]*\n?',text):
            sep=text.find('\n\n',m.end())
            spans.append((m.start(),sep if sep!=-1 else len(text),'external'))
    for m in _UNTRUSTED_CONTEXT_RE.finditer(text):
        # 段起点回退到行首，避免把标记所在行的前缀文本切进两半
        start=text.rfind('\n',0,m.start())+1
        sep=text.find('\n\n',m.end())
        spans.append((start,sep if sep!=-1 else len(text),'external'))
    if not spans:
        return []
    # 合并重叠/包含区间（omni_body 优先级高于 external）
    spans.sort()
    merged: list[list]=[]
    for s,e,k in spans:
        if merged and s<merged[-1][1]:
            merged[-1][1]=max(merged[-1][1],e)
            if k=='omni_body':
                merged[-1][2]='omni_body'
        else:
            merged.append([s,e,k])
    return [(s,e,k) for s,e,k in merged]


def _segment_text_atomic(text: str) -> list[tuple[str,str]] | None:
    """把文本切成 (kind, 段文本) 序列；无原子段时返回 None（走通用截断）。"""
    spans=_find_atomic_text_spans(text)
    if not spans:
        return None
    segments: list[tuple[str,str]]=[]
    cursor=0
    for s,e,kind in spans:
        if s>cursor:
            segments.append(('plain',text[cursor:s]))
        segments.append((kind,text[s:e]))
        cursor=e
    if cursor<len(text):
        segments.append(('plain',text[cursor:]))
    return segments


def _truncate_head_tail(text: str, budget_tokens: int) -> str:
    """通用头尾截断（旧行为，仅用于无原子段的普通文本）。"""
    half_budget = max(100, budget_tokens // 2)
    half_chars = int(half_budget * 3)
    head = text[:half_chars]
    tail = text[-half_chars:] if len(text) > half_chars * 2 else ""
    skipped = len(text) - len(head) - len(tail)
    if tail:
        return head + f"\n...[truncated {skipped} chars, ~{estimate_tokens(text) - estimate_tokens(head) - estimate_tokens(tail)} tokens]...\n" + tail
    return head + f"\n...[truncated {len(text) - len(head)} chars]"


def _compact_string(text: str, budget_tokens: int, _stats: dict | None=None) -> str:
    """截断字符串到预算内，保留头尾。

    含原子段（<omni_body> 块 / 工具执行结果 / untrusted 标记段）时：
    原子段要么整段保留、要么整段淘汰并计入 _stats，绝不截半截；
    外部数据段（external）无论去留都计入外部数据分区统计。"""
    if not text or estimate_tokens(text) <= budget_tokens:
        return text
    segments=_segment_text_atomic(text)
    if segments is None:
        return _truncate_head_tail(text, budget_tokens)
    # 为淘汰标记预留预算，保证输出仍大致 ≤ budget_tokens（幂等前提）
    budget=max(50, budget_tokens-60)
    kept: list[str]=[]
    used=0
    dropped_blocks=0
    dropped_tokens=0
    for kind,seg in segments:
        st=estimate_tokens(seg)
        if kind=='plain':
            remain=budget-used
            if remain<=50:
                continue  # 预算耗尽的普通段直接舍弃
            piece=_truncate_head_tail(seg,remain) if st>remain else seg
            kept.append(piece)
            used+=estimate_tokens(piece)
            continue
        # 原子段：external 先计入外部数据分区统计（无论最终去留）
        if kind=='external' and _stats is not None:
            _stats["external_data_segments"]=_stats.get("external_data_segments",0)+1
            _stats["external_data_tokens"]=_stats.get("external_data_tokens",0)+st
        if used+st<=budget:
            kept.append(seg)
            used+=st
            if _stats is not None:
                _stats["atomic_blocks_kept"]=_stats.get("atomic_blocks_kept",0)+1
        else:
            dropped_blocks+=1
            dropped_tokens+=st
    out=''.join(kept)
    if dropped_blocks>0:
        out+=f"\n...[dropped {dropped_blocks} atomic blocks, ~{dropped_tokens} tokens]...\n"
        if _stats is not None:
            _stats["atomic_blocks_dropped"]=_stats.get("atomic_blocks_dropped",0)+dropped_blocks
    return out


def _compact_value(value: Any, budget_tokens: int, depth: int = 0,
                   _stats: dict | None=None) -> Any:
    if isinstance(value, dict):
        return _compact_dict(value, budget_tokens, depth, _stats)
    elif isinstance(value, list):
        return _compact_list(value, budget_tokens, depth, _stats)
    elif isinstance(value, str):
        return _compact_string(value, budget_tokens, _stats)
    elif isinstance(value, float):
        import math
        if math.isnan(value) or math.isinf(value):
            return None  # NaN/Inf → null，避免非标准JSON
        return value
    elif isinstance(value, (int, bool, type(None))):
        return value
    else:
        return str(value)[:500]


def compact_tool_result(result: Any, max_tokens: int,
                        _stats: dict | None=None) -> Any:
    """压缩工具结果到预算内。

    NEVER_COMPRESS 字段（policy_decision/execution_ticket/effect/
    frozen_intent/grant/provenance/source_ref/taint/completion_evidence
    前缀）在任何情况下原样保留；这些字段自身超出 max_tokens 时抛
    ContextAssemblyError（装配失败），绝不静默裁剪。
    _stats 为内部统计收集器（可选），供 compact_if_needed 汇总进报告。"""
    current=estimate_tokens(result)
    if current<=max_tokens: return result
    # 预算可行性：NEVER_COMPRESS 骨架自身超预算 ⇒ 装配必然失败
    skeleton,nc_count=_never_compress_skeleton(result)
    if nc_count and estimate_tokens(skeleton)>max_tokens:
        raise ContextAssemblyError(
            f"never_compress_over_budget: {nc_count} 个 NEVER_COMPRESS 字段约 "
            f"{estimate_tokens(skeleton)} tokens，超出预算 {max_tokens}；"
            f"装配失败，不静默裁剪政策/票据/effect/provenance 字段")
    return _compact_value(result,max_tokens,0,_stats)


# ═══════════════════════════════════════════
# 系统提示词 identity/soul/policy 保护段
# ═══════════════════════════════════════════

# 保护段标题（行首标题，中英文，大小写不敏感）：
#   Markdown 标题含 identity/soul/policy；中文书名号标题含 身份/灵魂；
#   或 <identity>/<soul>/<policy> 标签行
_PROTECTED_SECTION_HEADING_RE=re.compile(
    r'^[ \t]*(?:'
    r'#{1,6}[ \t]+[^\n]*\b(?:identity|soul|policy)\b[^\n]*'
    r'|【[^】]*(?:身份|灵魂|identity|soul|policy)[^】]*】[^\n]*'
    r'|<(?:identity|soul|policy)\b[^\n]*'
    r')', re.IGNORECASE|re.MULTILINE)
# 任意段标题（用于界定保护段的结束位置）
_GENERIC_SECTION_HEADING_RE=re.compile(
    r'^[ \t]*(?:#{1,6}[ \t]+\S|【[^】]+】)', re.MULTILINE)


def _extract_identity_spans(text: str) -> list[tuple[int,int]]:
    """定位 identity/soul/policy 保护段，返回 (start,end) 列表（无则空表）。

    段 = 保护标题行起 → 下一个任意段标题或文末。重叠段合并。"""
    spans: list[tuple[int,int]]=[]
    for h in _PROTECTED_SECTION_HEADING_RE.finditer(text):
        nxt=_GENERIC_SECTION_HEADING_RE.search(text,h.end())
        spans.append((h.start(),nxt.start() if nxt else len(text)))
    if not spans:
        return []
    spans.sort()
    merged=[list(spans[0])]
    for s,e in spans[1:]:
        if s<merged[-1][1]:
            merged[-1][1]=max(merged[-1][1],e)
        else:
            merged.append([s,e])
    return [(s,e) for s,e in merged]


def _compact_system_tishi_plain(text: str, max_tokens: int) -> str:
    """旧版系统提示词压缩（策略1 裁 skills 注入 + 策略2 比例截断）。"""
    # 策略1：找到 skills 注入段，裁剪每段内容
    skill_blocks = list(re.finditer(r'\[已匹配Skill:[^\]]+\]\n', text))
    if skill_blocks:
        first_start = skill_blocks[0].start()
        for i, m in enumerate(skill_blocks):
            block_start = m.end()
            next_start = skill_blocks[i + 1].start() if i + 1 < len(skill_blocks) else len(text)
            # 找自然段结束
            for sep in ['\n\n[', '\n---\n', '\n════']:
                idx = text.find(sep, block_start)
                if idx != -1 and idx < next_start:
                    next_start = idx
            block_text = text[block_start:next_start]
            if estimate_tokens(block_text) > 1000:
                # 只保留 skill 块的前 3000 字符
                text = text[:block_start] + block_text[:3000] + '\n...[skill content trimmed]\n' + text[next_start:]
        # 重新估算，如果仍然超预算则跳到策略2
        if estimate_tokens(text) <= max_tokens:
            return text

    # 策略2：按比例截断整个文本
    current = estimate_tokens(text)
    ratio = max_tokens / max(1, current)
    max_chars = int(len(text) * ratio * 0.95)  # 5% 安全余量
    truncated = text[:max_chars]
    return truncated + f'\n...[system prompt trimmed from ~{current} to ~{estimate_tokens(truncated)} tokens]'


def compact_system_tishi(text: str, max_tokens: int,
                         _stats: dict | None=None) -> str:
    """压缩系统提示词：保留核心指令，裁剪 skills 注入。

    identity/soul/policy 段为 NEVER_COMPRESS 区域：一律原样保留；
    这些段自身超出 max_tokens 时抛 ContextAssemblyError（装配失败），
    绝不静默裁剪身份/灵魂/政策内容。"""
    current = estimate_tokens(text)
    if current <= max_tokens:
        return text
    spans=_extract_identity_spans(text)
    if not spans:
        return _compact_system_tishi_plain(text,max_tokens)
    p_tokens=sum(estimate_tokens(text[s:e]) for s,e in spans)
    if _stats is not None:
        _stats["identity_sections"]=_stats.get("identity_sections",0)+len(spans)
        _stats["identity_tokens"]=_stats.get("identity_tokens",0)+p_tokens
    if p_tokens>max_tokens:
        raise ContextAssemblyError(
            f"identity_over_budget: identity/soul/policy 段约 {p_tokens} tokens，"
            f"超出系统提示词预算 {max_tokens}；装配失败，不静默裁剪")
    # 保护段原样保留在原位置；其余文本在剩余预算内按旧策略压缩
    rest_budget=max(50, max_tokens-p_tokens)
    parts: list[tuple[bool,str]]=[]
    cursor=0
    for s,e in spans:
        if s>cursor:
            parts.append((False,text[cursor:s]))
        parts.append((True,text[s:e]))
        cursor=e
    if cursor<len(text):
        parts.append((False,text[cursor:]))
    plain_joined=''.join(p for flag,p in parts if not flag)
    if estimate_tokens(plain_joined)>rest_budget:
        plain_joined=_compact_system_tishi_plain(plain_joined,rest_budget)
    out: list[str]=[]
    placed=False
    for flag,p in parts:
        if flag:
            out.append(p)
        elif not placed:
            out.append(plain_joined)
            placed=True
    return ''.join(out)


# ═══════════════════════════════════════════
# 原子事实提取器
# ═══════════════════════════════════════════

def extract_atomic_facts(data: Any, prefix: str="") -> list[dict]:
    """从工具结果中提取原子事实。类别判定用 key 名后缀模式匹配。"""
    facts: list[dict]=[]

    # ── 类型判定：精确匹配 + 后缀模式 ──
    _STATUS_KEYS = {'ok', 'status', 'error', 'code', 'exit_code', 'result', 'state'}
    _PATH_KEYS = {'path', 'file', 'filename', 'target', 'source', 'dest', 'destination',
                  'directory', 'folder', 'location', 'url', 'uri', 'link'}
    _COUNT_KEYS = {'count', 'total', 'size', 'lines', 'length', 'num', 'quantity', 'amount', 'entries', 'items'}
    _HASH_KEYS = {'sha256', 'sha1', 'md5', 'hash', 'checksum', 'digest'}

    def _key_type(key: str) -> str | None:
        """根据 key 名判定事实类型。先精确匹配，再后缀匹配。"""
        kl = key.lower().strip('_')
        if kl in _STATUS_KEYS:
            return 'status'
        if kl in _PATH_KEYS:
            return 'path'
        if kl in _COUNT_KEYS:
            return 'count'
        if kl in _HASH_KEYS:
            return 'hash'
        # 后缀模式：*_path, *_code, *_count, *_file, *_hash
        if kl.endswith('_path') or kl.endswith('_file') or kl.endswith('_dir') or kl.endswith('_url'):
            return 'path'
        if kl.endswith('_code') or kl.endswith('_status') or kl.endswith('_state') or kl.endswith('_result'):
            return 'status'
        if kl.endswith('_count') or kl.endswith('_total') or kl.endswith('_size') or kl.endswith('_lines') or kl.endswith('_num') or kl.endswith('_files') or kl.endswith('_entries') or kl.endswith('_items'):
            return 'count'
        if kl.endswith('_hash') or kl.endswith('_sha') or kl.endswith('_md5') or kl.endswith('_digest'):
            return 'hash'
        return None

    def _add(path: str, ftype: str, value: Any):
        facts.append({"path": f"{prefix}.{path}".strip('.'), "type": ftype, "value": str(value)[:500]})

    def _walk(obj: Any, cur_path: str="", depth: int=0):
        if depth > 6 or obj is None:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                sp = f"{cur_path}.{k}" if cur_path else k
                kt = _key_type(k)
                if kt is not None:
                    _add(sp, kt, v)
                elif k == 'content':
                    s = str(v) if not isinstance(v, str) else v
                    lines = s.split('\n')
                    if len(lines) > 20:
                        _add(f"{sp}.head", 'content_head', '\n'.join(lines[:5]))
                        _add(f"{sp}.tail", 'content_tail', '\n'.join(lines[-5:]))
                        _add(f"{sp}.line_count", 'count', len(lines))
                    else:
                        _add(sp, 'content', s[:200])
                elif k == 'files' and isinstance(v, list):
                    _add(sp, 'file_count', len(v))
                    names = [str(x.get('name') or x.get('path') or x)[:80] if isinstance(x, dict) else str(x)[:80] for x in v[:5]]
                    if len(v) > 5:
                        names.append(f"...[{len(v) - 5} more]")
                        names += [str(x.get('name') or x.get('path') or x)[:80] if isinstance(x, dict) else str(x)[:80] for x in v[-3:]]
                    _add(f"{sp}.sample", 'file_list', ', '.join(names))
                else:
                    _walk(v, sp, depth + 1)
        elif isinstance(obj, list):
            _add(cur_path, 'list_count', len(obj))
            for i, item in enumerate(obj[:5]):
                _walk(item, f"{cur_path}[{i}]", depth + 1)
            if len(obj) > 5:
                _add(cur_path, 'list_rest', f"...[{len(obj) - 5} more items]")
        elif isinstance(obj, str) and len(obj) < 200:
            _add(cur_path, 'value', obj)

    _walk(data)
    return facts


def extract_facts_from_text(text: str) -> list[dict]:
    """从纯文本中提取原子事实"""
    facts: list[dict]=[]
    for m in re.finditer(r'(?:/[\w./-]+|\\\\[\w\\.-]+|\\\\\?\\[A-Z]:\\[\\w\\./-]+|[A-Z]:\\[\\w\\./-]+)',text):
        facts.append({"path":f"text.path.{m.start()}","type":"path","value":m.group()})
    for m in re.finditer(r'\b([1-9]\d{2,})\b',text):
        facts.append({"path":f"text.num.{m.start()}","type":"number","value":str(int(m.group(1)))})
    for m in re.finditer(r'\b(ok|error|failed|success|true|false|exit_code|status)\s*[:=]\s*(\S+)',text,re.IGNORECASE):
        facts.append({"path":f"text.status.{m.start()}","type":"status","value":f"{m.group(1)}={m.group(2)}"})
    return facts


# ═══════════════════════════════════════════
# 对抗性审查评分器
# ═══════════════════════════════════════════

class AdversarialReviewer:
    """四维评分：
    ① 原子事实覆盖率（一票否决 ≥99.5%）
    ② 对齐蕴含（30%）
    ③ 自洽一致性（20%）
    ④ 结构完整性（10%）
    """

    def __init__(self,original: Any,compacted: Any):
        self.original=original
        self.compacted=compacted
        self.orig_facts: list[dict]=extract_atomic_facts(original)
        self.comp_facts: list[dict]=extract_atomic_facts(compacted)
        self.scores: dict[str,float]={}

    def review(self) -> dict:
        a, hard_cov = self._atomic_coverage()
        b = self._alignment_entailment()
        c = self._self_consistency()
        d = self._structural_integrity()
        self.scores = {"atomic_coverage": a, "alignment": b, "consistency": c, "structure": d}

        # 注入检测：compacted 中出现了 original 没有的硬事实
        injected_hard = self._detect_injection()
        if injected_hard:
            total = 0.0
            veto = True
            veto_reason = f"注入攻击: compacted 注入了 {injected_hard} 条 original 不存在的硬事实"
        # 否决条件：硬事实（路径/状态/计数）有丢失
        elif hard_cov < 0.995:
            total = min(a, 0.40)
            veto = True
            veto_reason = "硬事实丢失 (路径/状态/计数)"
        else:
            total = a * 0.40 + b * 0.30 + c * 0.20 + d * 0.10
            veto = False
            veto_reason = ""

        missing = self._find_missing_facts()
        inconsistencies = self._find_inconsistencies()
        return {
            "total_score": round(total, 4), "veto": veto,
            "veto_reason": veto_reason,
            "dimensions": self.scores,
            "original_fact_count": len(self.orig_facts),
            "compacted_fact_count": len(self.comp_facts),
            "missing_facts": missing[:10],
            "inconsistencies": inconsistencies[:5],
            "passed": total >= 0.80 and not veto,
        }

    def _atomic_coverage(self) -> tuple[float, float]:
        """返回 (综合得分, 硬事实覆盖率)。硬事实丢失触发否决。"""
        if not self.orig_facts:
            return 1.0, 1.0

        HARD_TYPES = {'path', 'status', 'count', 'hash', 'file_count', 'file_list', 'list_count', 'number'}
        SOFT_TYPES = {'content', 'content_head', 'content_tail', 'value'}

        hard_orig = [f for f in self.orig_facts if f['type'] in HARD_TYPES]
        hard_comp = [f for f in self.comp_facts if f['type'] in HARD_TYPES]
        soft_orig = [f for f in self.orig_facts if f['type'] in SOFT_TYPES]
        soft_comp = [f for f in self.comp_facts if f['type'] in SOFT_TYPES]

        # 硬事实：精确匹配
        hard_orig_set = {(f['path'], f['type'], f['value']) for f in hard_orig}
        hard_comp_set = {(f['path'], f['type'], f['value']) for f in hard_comp}
        hard_matched = len(hard_orig_set & hard_comp_set)

        # 软事实：前缀包含即匹配
        soft_matched = 0
        orig_soft_map: dict[tuple, str] = {}
        for f in soft_orig:
            k = (f['path'], f['type'])
            if k not in orig_soft_map:
                orig_soft_map[k] = f['value']
        for f in soft_comp:
            k = (f['path'], f['type'])
            if k in orig_soft_map:
                ov = orig_soft_map[k]
                cv = f['value']
                if ov == cv or ov.startswith(cv) or cv.startswith(ov[:50]):
                    soft_matched += 1

        total_hard = len(hard_orig)
        total_soft = len(soft_orig)

        hard_cov = hard_matched / max(1, total_hard) if total_hard > 0 else 1.0
        soft_cov = soft_matched / max(1, total_soft) if total_soft > 0 else 1.0

        # 综合得分：硬70% + 软30%
        overall = hard_cov * 0.70 + soft_cov * 0.30
        return overall, hard_cov

    def _alignment_entailment(self) -> float:
        if not self.orig_facts: return 1.0
        critical={'path','status','count','hash'}
        co=[f for f in self.orig_facts if f['type'] in critical]
        cc=[f for f in self.comp_facts if f['type'] in critical]
        if not co: return 1.0
        op={f['path'] for f in co}
        cp={f['path'] for f in cc}
        pc=len(op&cp)/max(1,len(op))
        ov_set={(f['path'],f['value']) for f in co}
        cv_set={(f['path'],f['value']) for f in cc}
        vm=len(ov_set&cv_set)/max(1,len(ov_set))
        return 0.6*pc+0.4*vm

    def _self_consistency(self) -> float:
        if len(self.comp_facts)<2: return 1.0
        by_path: dict[str,list[dict]]={}
        for f in self.comp_facts:
            by_path.setdefault(f['path'],[]).append(f)
        issues=0; total=len(by_path)
        for path,group in by_path.items():
            if len(group)>=2 and len({f['value'] for f in group})>1: issues+=1
        return max(0.0,1.0-(issues/max(1,total))*0.5)

    def _structural_integrity(self) -> float:
        score=1.0
        if isinstance(self.compacted,str):
            if 'truncated' in self.compacted.lower(): score-=0.2
        elif isinstance(self.compacted,dict):
            if '_truncated_keys' in self.compacted: score-=0.15
            if '_truncated' in self.compacted: score-=0.15
        elif isinstance(self.compacted,list):
            last=self.compacted[-1] if self.compacted else None
            if isinstance(last,str) and 'truncated' in last.lower(): score-=0.2
        try:
            json.dumps(self.compacted,ensure_ascii=False)
        except (TypeError,ValueError): score-=0.3
        return max(0.0,score)

    def _detect_injection(self) -> int:
        """检测 compacted 是否注入了 original 不存在的硬事实。
        返回注入的硬事实数量。"""
        HARD_TYPES = {'path', 'status', 'count', 'hash', 'file_count', 'file_list', 'list_count', 'number'}
        orig_hard = {(f['path'], f['type']) for f in self.orig_facts if f['type'] in HARD_TYPES}
        comp_hard = {(f['path'], f['type']) for f in self.comp_facts if f['type'] in HARD_TYPES}
        injected = comp_hard - orig_hard
        return len(injected)

    def _find_missing_facts(self) -> list[dict]:
        comp_set={(f['path'],f['type'],f['value']) for f in self.comp_facts}
        return [f for f in self.orig_facts if (f['path'],f['type'],f['value']) not in comp_set and f['type'] in ('path','status','count','hash')]

    def _find_inconsistencies(self) -> list[str]:
        by_path: dict[str,list[str]]={}
        for f in self.comp_facts: by_path.setdefault(f['path'],[]).append(f['value'])
        issues=[]
        for path,vals in by_path.items():
            unique=list(set(vals))
            if len(unique)>1: issues.append(f"{path}: {unique}")
        return issues


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def check_budget(system_tishi: str, yonghu_tishi: str,
                 window_tokens: int=DEFAULT_WINDOW_TOKENS) -> dict:
    st=estimate_tokens(system_tishi); ut=estimate_tokens(yonghu_tishi)
    total=st+ut
    return {"total_estimated_tokens":total,"system_tokens":st,"user_tokens":ut,
            "window_tokens":window_tokens,"ratio":round(total/max(1,window_tokens),4),
            "needs_compact": total > window_tokens * COMPACT_WARN,
            "urgent": total > window_tokens * COMPACT_URGENT}


def _finalize_report_stats(report: dict, stats: dict) -> dict:
    """把本次压缩的统计并入 report。新增字段向后兼容：旧读取方取不到不影响。"""
    report["never_compress"]={
        "preserved_keys": int(stats.get("never_compress_preserved",0)),
        "tokens": int(stats.get("never_compress_tokens",0)),
    }
    report["atomic_blocks"]={
        "kept": int(stats.get("atomic_blocks_kept",0)),
        "dropped": int(stats.get("atomic_blocks_dropped",0)),
    }
    # 外部数据结构化分区：只计数、不搬家——外部数据仅存在于 yonghu_tishi
    # 压缩路径，system_tishi（指令区）压缩从不读取其内容，
    # 因此压缩不会把外部数据提升到指令区（结构不变式，记录如下）
    report["external_data"]={
        "segments": int(stats.get("external_data_segments",0)),
        "tokens": int(stats.get("external_data_tokens",0)),
        "promoted_to_instruction": False,
        "invariant": "external_data_never_promoted_to_instruction_zone",
    }
    if stats.get("identity_sections"):
        report["identity_sections"]={
            "count": int(stats.get("identity_sections",0)),
            "tokens": int(stats.get("identity_tokens",0)),
        }
    return report


def _mark_assembly_failed(report: dict, exc: ContextAssemblyError) -> dict:
    """装配失败按 veto 同级硬失败上报：旧调用方读到 veto=True 即会
    走"保留原文、如实报错"路径，无需改动调用契约。"""
    report["assembly_failed"]=True
    report["failure_reason"]=exc.reason
    report["veto"]=True
    report["veto_reason"]=f"assembly_failed: {exc.reason}"
    return report


def compact_if_needed(system_tishi: str, yonghu_tishi: str,
                      window_tokens: int=DEFAULT_WINDOW_TOKENS) -> tuple[str,str,dict]:
    """按需压缩。审查 veto 与装配失败（NEVER_COMPRESS / identity 超预算）
    均为硬失败：返回原文并置相应 report 字段，绝不把失真压缩结果
    交给调用方（由调用方决定降级用原文或如实报错）。"""
    budget=check_budget(system_tishi,yonghu_tishi,window_tokens)
    stats: dict={}
    report={"budget":budget,"compacted":False,"review":None,"veto":False,"veto_reason":"",
            "assembly_failed":False,"failure_reason":""}
    if not budget["needs_compact"]:
        return system_tishi,yonghu_tishi,_finalize_report_stats(report,stats)
    report["compacted"]=True
    sb=int(window_tokens*SYSTEM_BUDGET_PCT); ub=int(window_tokens*TOOL_RESULT_BUDGET_PCT)
    if budget["system_tokens"]>sb:
        try:
            system_tishi=compact_system_tishi(system_tishi,sb,_stats=stats)
            report["system_compacted"]=True
        except ContextAssemblyError as exc:
            # identity/soul/policy 段自身超预算：装配失败，返回原文
            _mark_assembly_failed(report,exc)
            report["system_compacted"]=False
            return system_tishi,yonghu_tishi,_finalize_report_stats(report,stats)
    if budget["user_tokens"]>ub:
        original_user=yonghu_tishi
        try:
            parsed=json.loads(yonghu_tishi)
            compacted=compact_tool_result(parsed,ub,_stats=stats)
            reviewer=AdversarialReviewer(parsed,compacted)
            review=reviewer.review()
            report["review"]=review
            if review.get("veto"):
                report["veto"]=True
                report["veto_reason"]=str(review.get("veto_reason") or "")
                report["user_compacted"]=False
                return system_tishi,original_user,_finalize_report_stats(report,stats)
            yonghu_tishi=json.dumps(compacted,ensure_ascii=False,indent=2)
        except ContextAssemblyError as exc:
            # NEVER_COMPRESS 字段自身超预算：装配失败，返回原文
            _mark_assembly_failed(report,exc)
            report["user_compacted"]=False
            return system_tishi,original_user,_finalize_report_stats(report,stats)
        except (json.JSONDecodeError,TypeError):
            if estimate_tokens(yonghu_tishi)>ub:
                compacted_text=_compact_string(yonghu_tishi,ub,stats)
                reviewer=AdversarialReviewer(original_user,compacted_text)
                review=reviewer.review()
                report["review"]=review
                if review.get("veto"):
                    report["veto"]=True
                    report["veto_reason"]=str(review.get("veto_reason") or "")
                    report["user_compacted"]=False
                    return system_tishi,original_user,_finalize_report_stats(report,stats)
                yonghu_tishi=compacted_text
        report["user_compacted"]=True
    return system_tishi,yonghu_tishi,_finalize_report_stats(report,stats)
