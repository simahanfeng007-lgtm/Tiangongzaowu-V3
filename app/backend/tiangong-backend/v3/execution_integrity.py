from __future__ import annotations

"""Execution-integrity invariants for Tiangong V3.

This module deliberately does not choose tools or execute actions.  It only
tracks high-confidence user action obligations and verifies whether the
runtime has real tool evidence before an action-oriented turn may terminate.
"""

import re
from typing import Any

_RESPONSE_ONLY_MARKERS = (
    "不要使用工具", "不要调用工具", "不要执行", "无需执行", "先别执行", "先不要执行",
    "先别读", "先不要读", "不要读", "别读", "只告诉我", "只解释", "只分析", "只讨论",
    "先分析", "先讨论", "暂时不要动", "先别动",
)
_REQUEST_CUES = ("请", "帮我", "帮忙", "给我", "替我", "直接", "一下", "现在", "马上", "不就行了")
_DIRECTORY_TERMS = ("目录", "文件夹", "workspace", "工作区", "当前路径")
_FILE_TERMS = ("文件", "文档", "附件", "压缩包", "pdf", "表格")
_OBSERVE_VERBS = ("读取", "读一下", "读下", "读", "查看", "看一下", "看下", "看看", "列出", "列一下", "列", "检查", "扫描", "浏览", "打开")
_AMBIGUOUS_TARGETS = ("那个目录", "某个目录", "一个目录", "那个文件夹", "某个文件夹", "那个文件", "某个文件")
_EVIDENCE_ACTIONS = {
    # Directory observation is intentionally strict: a successful unrelated read
    # must never satisfy "read/list this directory".  Runtime verifies evidence;
    # it still does not prescribe which tool the LLM must attempt first.
    "observe_directory": frozenset({"file.list"}),
    "observe_file": frozenset({"file.read", "code.read", "sheet.read"}),
}
_COMPLETION_CLAIM_RE = re.compile(
    r"(?:已经|已)(?:完成|读取|读完|查看|检查|执行|下载|修改|写入|生成|发送|处理|打开)"
    r"|(?:完成了|办妥了?|搞定了?|读完了|读取完毕|查看完毕|检查完毕|执行完毕|下载完成|处理完成)",
    re.IGNORECASE,
)
_DEVIATION_SIGNAL_RE = re.compile(r"^[?？]{1,4}$")
_LOCAL_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s]+|(?:^|\s)(?:\.{0,2}[\\/])[^\s]+)")


def _compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def is_deviation_signal(text: Any) -> bool:
    return bool(_DEVIATION_SIGNAL_RE.fullmatch(_compact(text)))


def has_execution_completion_claim(text: Any) -> bool:
    return bool(_COMPLETION_CLAIM_RE.search(str(text or "")))


def _response_only(text: str) -> bool:
    compact = _compact(text)
    return any(marker.replace(" ", "").lower() in compact for marker in _RESPONSE_ONLY_MARKERS)


def _meta_or_hypothetical(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return True
    if compact.startswith(("如果", "假如", "假设", "要是")) and any(word in compact for word in ("怎么", "如何", "会怎样", "会怎么")):
        return True
    request_cued = any(cue in compact for cue in _REQUEST_CUES)
    if not request_cued and any(word in compact for word in ("怎么读", "如何读", "怎么查看", "如何查看", "为什么", "原理", "是什么意思")):
        return True
    if not request_cued and re.search(r"(?:你会|会不会|你能|是否|能否).*(?:吗|么|\?|？)$", compact):
        return True
    return False



def _intent_compact(text: object) -> str:
    return re.sub(r"[\s\?\？\!\！\.\。\,\，\;\；\:\：]+", "", str(text or "").lower())


def _is_high_confidence_capability_question(user_text: object) -> bool:
    """A capability inquiry that is unlikely to be a polite execution request."""
    compact = _intent_compact(user_text)
    if not compact:
        return False
    # Preserve polite execution requests such as "你能帮我读一下目录吗？".
    request_hints = ("帮我", "一下", "现在", "直接", "请", "给我", "替我", "马上")
    if any(marker in compact for marker in request_hints):
        return False
    if re.fullmatch(r"(?:你|模型|系统|工具)(?:会|是否会).+(?:吗|么)", compact):
        return True
    if re.fullmatch(r"(?:你|模型|系统|工具)会不会.+", compact):
        return True
    return False


def _is_hypothetical_action_discussion(user_text: object) -> bool:
    """High-confidence hypothetical/planning discussion, not a real action request."""
    text = str(user_text or "")
    compact = _intent_compact(text)
    if not compact:
        return False

    # Keep genuine conditional commands executable.
    # Example: "如果目录里有 package.json，就读一下当前目录".
    if re.search(
        r"(?:那么就|那就|就)(?:帮我)?(?:读|读取|看|查看|列|修改|改|修复|删除|运行|执行|下载|创建|生成|写|搜索|查)",
        compact,
    ):
        return False

    chinese_conditions = ("如果", "假如", "假设", "要是", "倘若", "若是")
    chinese_planning = (
        "你会怎么做", "你会如何做", "你会怎么处理", "你会如何处理",
        "你准备怎么做", "你打算怎么做", "会怎么做", "会如何做",
        "会怎么处理", "会如何处理", "你的方案是什么", "方案是什么",
        "你会怎么分析", "你会如何分析",
    )
    if any(marker in compact for marker in chinese_conditions) and any(
        marker in compact for marker in chinese_planning
    ):
        return True

    english = re.sub(r"\s+", " ", text.strip().lower())
    english_condition = any(
        marker in english for marker in ("if ", "suppose ", "assuming ", "were to ")
    )
    english_planning = any(
        marker in english
        for marker in (
            "what would you do", "how would you", "what is your approach", "what's your approach"
        )
    )
    return bool(english_condition and english_planning)


def _is_deferred_action_explanation(user_text: object) -> bool:
    """The user explicitly defers action and asks only for explanation/planning."""
    compact = _intent_compact(user_text)
    if not compact:
        return False
    defer_markers = ("先别", "先不要", "暂时别", "暂时不要", "先不用")
    explanation_markers = (
        "只告诉我", "只跟我说", "只说", "只解释", "只分析",
        "先告诉我", "先跟我说", "先说说", "告诉我你准备", "告诉我怎么", "告诉我如何",
    )
    return bool(
        any(marker in compact for marker in defer_markers)
        and any(marker in compact for marker in explanation_markers)
    )


def is_execution_discussion_only(user_text: object) -> bool:
    """Return True only for high-confidence no-side-effect discussion turns.

    This function never chooses a tool and never executes anything.  It is a
    narrow terminal/tool-exposure invariant: discussion-only turns should not
    receive native execution tools, while polite and conditional real commands
    remain fully available to the LLM.
    """
    return bool(
        _is_high_confidence_capability_question(user_text)
        or _is_hypothetical_action_discussion(user_text)
        or _is_deferred_action_explanation(user_text)
    )


def build_action_obligations(user_text: Any) -> list[dict[str, Any]]:
    """Return only high-confidence observation obligations.

    The obligation describes the *outcome/evidence*, never a required tool.
    This preserves LLM tool-selection autonomy.
    """
    text = str(user_text or "").strip()
    compact = _compact(text)
    if not compact or _response_only(text) or _meta_or_hypothetical(text):
        return []
    if not any(verb in compact for verb in _OBSERVE_VERBS):
        return []

    kind = ""
    if any(term in compact for term in _DIRECTORY_TERMS):
        kind = "observe_directory"
    elif any(term in compact for term in _FILE_TERMS) or _LOCAL_PATH_RE.search(text):
        kind = "observe_file"
    if not kind:
        return []

    ambiguous = any(term in compact for term in _AMBIGUOUS_TARGETS)
    path_match = _LOCAL_PATH_RE.search(text)
    explicit_target = path_match.group(0).strip() if path_match else ""
    if ambiguous:
        target_hint = "unresolved"
    elif explicit_target:
        target_hint = "explicit_path"
    elif kind == "observe_directory":
        target_hint = "current_workspace"
    else:
        target_hint = "explicit_or_context_file"
    return [{
        "id": f"execution:{kind}:1",
        "kind": kind,
        "status": "needs_clarification" if ambiguous else "pending",
        "actionable": not ambiguous,
        "target_hint": target_hint,
        "target_path": explicit_target,
        "evidence_policy": "successful_observation",
        "source": "current_user_message",
    }]


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip().strip("`\"'").replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/").lower()


def _payload_target(payload: dict[str, Any]) -> str:
    tool_args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else {}
    nested = tool_args.get("args") if isinstance(tool_args.get("args"), dict) else {}
    for source in (nested, tool_args):
        for key in ("target", "path", "directory", "dir", "file"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _target_matches(payload: dict[str, Any], obligation: dict[str, Any]) -> bool:
    expected = _normalize_path(obligation.get("target_path"))
    if not expected:
        return True
    actual = _normalize_path(_payload_target(payload))
    if not actual:
        return False
    return actual == expected


def _successful_observation(payload: Any, obligation: dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or not bool(payload.get("ok")):
        return False
    action = str(payload.get("tool_action") or payload.get("action") or "").strip().lower()
    kind = str(obligation.get("kind") or "").strip()
    allowed_actions = _EVIDENCE_ACTIONS.get(kind, frozenset())
    if action not in allowed_actions:
        return False
    return _target_matches(payload, obligation)


def obligation_is_satisfied(obligation: dict[str, Any], quality_history: list[dict[str, Any]] | None) -> bool:
    if not bool(obligation.get("actionable", True)):
        return False
    return any(_successful_observation(payload, obligation) for payload in (quality_history or []))


def requires_evidence_safe_closeout(reasons: list[str] | None) -> bool:
    """True when terminal prose must be generated by Runtime, not by the LLM.

    At this point the model has already had its bounded replanning opportunity.
    A missing execution-evidence obligation is a factual invariant, so allowing a
    final no-tool LLM "polish" can only weaken integrity by inventing completion.
    """
    for reason in reasons or []:
        text = str(reason or "").strip()
        if text.startswith("execution_obligation:") or text.startswith("execution_claim_without_evidence"):
            return True
    return False


def execution_integrity_blockers(
    user_text: Any,
    quality_history: list[dict[str, Any]] | None,
    *,
    final_reply: Any = None,
) -> list[str]:
    obligations = [item for item in build_action_obligations(user_text) if bool(item.get("actionable", True))]
    if not obligations:
        return []
    blockers: list[str] = []
    for obligation in obligations:
        if not obligation_is_satisfied(obligation, quality_history):
            blockers.append(f"execution_obligation:{obligation.get('kind')}:missing_evidence")
    if blockers and has_execution_completion_claim(final_reply):
        blockers.append("execution_claim_without_evidence")
    return blockers


def update_run_state_obligations(run_state: dict[str, Any] | None, payload: dict[str, Any] | None) -> None:
    if not isinstance(run_state, dict) or not isinstance(payload, dict):
        return
    obligations = run_state.get("obligations")
    if not isinstance(obligations, list):
        return
    for obligation in obligations:
        if not isinstance(obligation, dict) or obligation.get("status") == "satisfied":
            continue
        if not bool(obligation.get("actionable", True)):
            continue
        action = str(payload.get("tool_action") or payload.get("action") or "").strip()
        if _successful_observation(payload, obligation):
            obligation["status"] = "satisfied"
            obligation["satisfied_by_action"] = action
            obligation["evidence_ok"] = True
            obligation["evidence_round"] = int(run_state.get("round") or 0)
        elif action:
            obligation["last_attempt_action"] = action
            obligation["last_attempt_ok"] = bool(payload.get("ok"))
