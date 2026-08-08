from __future__ import annotations

"""Execution-integrity invariants for Tiangong V3.

This module is intentionally thin. It does not choose tools, execute actions,
or judge task quality. It only:

1. establishes a conservative Runtime execution floor from the current user text;
2. treats the LLM's real tool call/result as its execution submission; and
3. blocks terminal completion when an explicit actionable request has no
   matching successful tool evidence.

The design keeps Runtime responsible for factual execution integrity while the
LLM remains responsible for semantic understanding, planning and tool choice.
"""

import re
from typing import Any

ACT_REQUIRED = "ACT_REQUIRED"
ACT_FORBIDDEN = "ACT_FORBIDDEN"
ACT_UNKNOWN = "UNKNOWN"

_RESPONSE_ONLY_MARKERS = (
    "不要使用工具", "不要调用工具", "不要执行", "无需执行", "先别执行", "先不要执行",
    "先别读", "先不要读", "不要读", "别读", "只告诉我", "只解释", "只分析", "只讨论",
    "先分析", "先讨论", "暂时不要动", "先别动",
)
_REQUEST_CUES = (
    "请", "帮我", "帮忙", "给我", "替我", "直接", "一下", "现在", "马上", "立刻",
    "开始", "把", "将", "不就行了",
)
_AMBIGUOUS_TARGETS = (
    "那个目录", "某个目录", "一个目录", "那个文件夹", "某个文件夹",
    "那个文件", "某个文件", "那个附件", "某个附件",
)
_DIRECTORY_TERMS = ("目录", "文件夹", "workspace", "工作区", "当前路径")
_FILE_TERMS = ("文件", "文档", "附件", "压缩包", "pdf", "表格", "源码", "代码")

# Only four factual classes are maintained. They are not task taxonomies and
# do not prescribe a tool. Existing specialised completion checks remain in
# zongdiaodu.py for write evidence, deliverables and verification.
_OBSERVE_VERBS = (
    "读取", "读一下", "读下", "读", "查看", "看一下", "看下", "看看", "列出", "列一下", "列",
    "检查", "扫描", "浏览", "打开", "搜索", "搜一下", "搜", "查询", "查一下", "查",
)
_EFFECT_VERBS = (
    "修改", "改一下", "改下", "改", "写入", "写", "创建", "生成", "删除", "移除", "复制",
    "移动", "重命名", "保存", "下载", "克隆", "拉取", "安装", "部署", "打包", "压缩", "解压",
    "修复", "导出",
)
_EXECUTE_VERBS = (
    "运行", "跑一下", "跑", "执行", "测试", "验证", "启动", "编译", "构建",
)
_DELIVER_VERBS = (
    "发送", "发给我", "发我", "传给我", "上传", "提交", "交付",
)

_ENGLISH_ACTION_RE = re.compile(
    r"\b(read|list|inspect|check|scan|browse|open|search|query|find|modify|edit|fix|write|create|generate|"
    r"delete|remove|copy|move|rename|save|download|clone|pull|install|deploy|package|compress|extract|"
    r"run|execute|test|verify|start|compile|build|send|upload|submit|deliver|export)\b",
    re.IGNORECASE,
)
_ENGLISH_REQUEST_RE = re.compile(
    r"(?:^|\b)(please|for\s+me|must|now|directly|go\s+ahead|do\s+it|can\s+you|could\s+you)\b",
    re.IGNORECASE,
)
_COMPLETION_CLAIM_RE = re.compile(
    r"(?:已经|已)(?:完成|读取|读完|查看|检查|执行|下载|修改|写入|生成|发送|处理|打开|运行|测试|上传|部署)"
    r"|(?:完成了|办妥了?|搞定了?|读完了|读取完毕|查看完毕|检查完毕|执行完毕|下载完成|处理完成|运行完成|测试完成)",
    re.IGNORECASE,
)
_DEVIATION_SIGNAL_RE = re.compile(r"^[?？]{1,4}$")
_LOCAL_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s]+|(?:^|\s)(?:\.{0,2}[\\/])[^\s]+)")

# Internal preparation can happen before the requested action. It must never
# by itself discharge the user's execution floor.
_PREPARATION_ACTIONS = frozenset({
    "skill.route", "skill.get", "skill.read",
})

_NEGATION_PREFIXES = (
    "不要", "别", "先别", "先不要", "不用", "无需", "禁止", "暂不", "暂时不要",
    "别再", "不要再", "不是让你", "不是叫你",
)

_LOCAL_WRITE_TOKENS = frozenset({
    "write", "append", "create", "delete", "remove", "copy", "move", "rename", "patch",
})
_EXTERNAL_EFFECT_TOKENS = frozenset({
    "download", "clone", "pull", "install", "deploy", "package", "compress", "extract", "fix", "export",
})

# Observation retains the existing strict read-only evidence map where the
# user supplied a high-confidence local object. This prevents an unrelated
# successful action from being used to claim that a file/directory was read.
_OBSERVATION_ACTIONS = {
    "directory": frozenset({"file.list"}),
    "file": frozenset({"file.read", "code.read", "sheet.read", "pdf.extract_text"}),
}


def _compact(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _intent_compact(text: object) -> str:
    return re.sub(r"[\s\?\？\!\！\.\。\,\，\;\；\:\：]+", "", str(text or "").lower())


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
    intent = _intent_compact(text)
    if not request_cued and any(word in intent for word in ("怎么读", "如何读", "怎么查看", "如何查看", "为什么", "原理", "是什么意思")):
        return True
    if not request_cued and (
        intent.startswith(("怎么", "如何", "为什么"))
        or intent.endswith(("是什么", "怎么样", "可以吗", "行吗", "好吗", "吗", "么"))
    ):
        return True
    if any(mark in compact for mark in ("解释怎么", "解释如何", "说明怎么", "说明如何", "告诉我怎么", "告诉我如何")):
        if not any(joiner in compact for joiner in ("然后", "然后再", "再帮我", "并且", "同时", "接着")):
            return True
    if not request_cued and re.search(r"(?:你会|会不会|你能|是否|能否).*(?:吗|么|\?|？)$", compact):
        return True
    return False


def _is_high_confidence_capability_question(user_text: object) -> bool:
    compact = _intent_compact(user_text)
    if not compact:
        return False
    request_hints = ("帮我", "一下", "现在", "直接", "请", "给我", "替我", "马上")
    if any(marker in compact for marker in request_hints):
        return False
    if re.fullmatch(r"(?:你|模型|系统|工具)(?:会|是否会).+(?:吗|么)", compact):
        return True
    if re.fullmatch(r"(?:你|模型|系统|工具)会不会.+", compact):
        return True
    return False


def _is_hypothetical_action_discussion(user_text: object) -> bool:
    text = str(user_text or "")
    compact = _intent_compact(text)
    if not compact:
        return False

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
    if any(marker in compact for marker in chinese_conditions) and any(marker in compact for marker in chinese_planning):
        return True

    english = re.sub(r"\s+", " ", text.strip().lower())
    english_condition = any(marker in english for marker in ("if ", "suppose ", "assuming ", "were to "))
    english_planning = any(
        marker in english
        for marker in ("what would you do", "how would you", "what is your approach", "what's your approach")
    )
    return bool(english_condition and english_planning)


def _is_deferred_action_explanation(user_text: object) -> bool:
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
    """Return True only for high-confidence no-side-effect discussion turns."""
    return bool(
        _is_high_confidence_capability_question(user_text)
        or _is_hypothetical_action_discussion(user_text)
        or _is_deferred_action_explanation(user_text)
        or _meta_or_hypothetical(str(user_text or ""))
    )


def _verb_occurs_affirmatively(compact: str, verb: str) -> bool:
    for match in re.finditer(re.escape(verb), compact):
        left = compact[max(0, match.start() - 8):match.start()]
        if any(left.endswith(prefix) for prefix in _NEGATION_PREFIXES):
            continue
        return True
    return False


def _english_action_is_negated(text: str, action_start: int) -> bool:
    left = text[max(0, action_start - 24):action_start].lower()
    return bool(re.search(r"(?:do\s+not|don't|dont|never|without)\s+$", left))


def _all_action_verbs() -> tuple[str, ...]:
    return _OBSERVE_VERBS + _EFFECT_VERBS + _EXECUTE_VERBS + _DELIVER_VERBS


def _high_confidence_action_request(user_text: object) -> bool:
    text = str(user_text or "").strip()
    compact = _compact(text)
    if not compact or _response_only(text) or is_execution_discussion_only(text) or _meta_or_hypothetical(text):
        return False

    matched_cn = [verb for verb in _all_action_verbs() if _verb_occurs_affirmatively(compact, verb)]
    if matched_cn:
        if any(cue in compact for cue in _REQUEST_CUES):
            return True
        lead = compact.lstrip("，。；：,.;:！!？?")
        if any(lead.startswith(verb) for verb in matched_cn):
            return True

    english = re.sub(r"\s+", " ", text.strip().lower())
    action_match = _ENGLISH_ACTION_RE.search(english)
    if not action_match or _english_action_is_negated(english, action_match.start()):
        return False
    if _ENGLISH_REQUEST_RE.search(english):
        return True
    return bool(action_match.start() == 0)


def runtime_execution_floor(user_text: object) -> str:
    """Conservative pre-LLM execution floor."""
    text = str(user_text or "").strip()
    if not text:
        return ACT_UNKNOWN
    if _response_only(text) or is_execution_discussion_only(text):
        return ACT_FORBIDDEN
    if _high_confidence_action_request(text):
        return ACT_REQUIRED
    return ACT_UNKNOWN


def _requested_fact_kinds(user_text: object) -> list[str]:
    compact = _compact(user_text)
    kinds: list[str] = []
    groups = (
        ("observation", _OBSERVE_VERBS),
        ("effect", _EFFECT_VERBS),
        ("execution", _EXECUTE_VERBS),
        ("delivery", _DELIVER_VERBS),
    )
    for kind, verbs in groups:
        if any(_verb_occurs_affirmatively(compact, verb) for verb in verbs):
            kinds.append(kind)
    return kinds or ["action"]


def _requested_object_kind(user_text: object, fact_kind: str) -> str:
    compact = _compact(user_text)
    if fact_kind == "observation":
        if any(term in compact for term in _DIRECTORY_TERMS):
            return "directory"
        if any(term in compact for term in _FILE_TERMS) or _LOCAL_PATH_RE.search(str(user_text or "")):
            return "file"
    return ""


def build_action_obligations(user_text: Any) -> list[dict[str, Any]]:
    """Build factual obligations from the Runtime floor, not a tool plan.

    The LLM's actual tool call/result is treated as its execution submission;
    Runtime never trusts a self-declared ``mode=work`` to prove execution.
    """
    text = str(user_text or "").strip()
    if runtime_execution_floor(text) != ACT_REQUIRED:
        return []

    compact = _compact(text)
    ambiguous = any(term in compact for term in _AMBIGUOUS_TARGETS)
    path_match = _LOCAL_PATH_RE.search(text)
    explicit_target = path_match.group(0).strip() if path_match else ""

    obligations: list[dict[str, Any]] = []
    for index, fact_kind in enumerate(_requested_fact_kinds(text), start=1):
        object_kind = _requested_object_kind(text, fact_kind)
        obligations.append({
            "id": f"execution:{fact_kind}:{index}",
            "kind": fact_kind,
            "object_kind": object_kind,
            "floor": ACT_REQUIRED,
            "status": "needs_clarification" if ambiguous else "pending",
            "actionable": not ambiguous,
            "target_path": explicit_target if fact_kind in {"observation", "effect"} else "",
            "evidence_policy": "successful_real_tool_result",
            "source": "current_user_message",
        })
    return obligations


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip().strip("`\"'").replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/").lower()


def _payload_target(payload: dict[str, Any]) -> str:
    tool_args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else {}
    nested = tool_args.get("args") if isinstance(tool_args.get("args"), dict) else {}
    for source in (nested, tool_args):
        for key in ("target", "path", "directory", "dir", "file", "source", "destination", "output_path"):
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


def _contract(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_result_contract")
    return value if isinstance(value, dict) else {}


def _payload_fact_kinds(payload: Any) -> set[str]:
    if not isinstance(payload, dict) or not bool(payload.get("ok")):
        return set()
    action = str(payload.get("tool_action") or payload.get("action") or "").strip().lower()
    if not action or action in _PREPARATION_ACTIONS:
        return set()

    facts: set[str] = {"action"}
    contract = _contract(payload)

    evidence = contract.get("write_evidence") if isinstance(contract.get("write_evidence"), dict) else None
    if bool(contract.get("observed_write_effect")) or (
        isinstance(evidence, dict)
        and evidence.get("authoritative") is True
        and (evidence.get("changed_files") or evidence.get("deleted_files") or evidence.get("verified_unchanged_files"))
    ):
        facts.add("effect")

    action_tokens = set(part for part in re.split(r"[._-]+", action) if part)
    if action in {"file.list", "file.read", "code.read", "sheet.read", "pdf.extract_text"} or action_tokens.intersection(
        {"read", "list", "inspect", "search", "query", "find", "info", "browse", "scan"}
    ):
        facts.add("observation")
    if action_tokens.intersection(_EXTERNAL_EFFECT_TOKENS):
        facts.add("effect")
    if action_tokens.intersection(_LOCAL_WRITE_TOKENS) and "effect" not in facts:
        pass
    if action_tokens.intersection({"run", "execute", "test", "verify", "start", "compile", "build"}):
        facts.add("execution")
    if action_tokens.intersection({"send", "upload", "submit", "deliver", "export"}):
        facts.add("delivery")
    return facts


def _observation_object_matches(payload: dict[str, Any], obligation: dict[str, Any]) -> bool:
    object_kind = str(obligation.get("object_kind") or "").strip()
    if not object_kind:
        return True
    action = str(payload.get("tool_action") or payload.get("action") or "").strip().lower()
    allowed = _OBSERVATION_ACTIONS.get(object_kind)
    if allowed is None:
        return True
    return action in allowed


def _successful_fact(payload: Any, obligation: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    required_kind = str(obligation.get("kind") or "action").strip().lower() or "action"
    if required_kind not in _payload_fact_kinds(payload):
        return False
    if required_kind == "observation" and not _observation_object_matches(payload, obligation):
        return False
    if required_kind in {"observation", "effect"} and not _target_matches(payload, obligation):
        return False
    return True


def obligation_is_satisfied(obligation: dict[str, Any], quality_history: list[dict[str, Any]] | None) -> bool:
    if not bool(obligation.get("actionable", True)):
        return False
    return any(_successful_fact(payload, obligation) for payload in (quality_history or []))


def requires_evidence_safe_closeout(reasons: list[str] | None) -> bool:
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
    """Reconcile Runtime floor with the LLM's real execution submission.

    The LLM submission is the actual tool call/result, not a self-declared
    intent flag. This avoids the circular failure mode where a model that does
    not want to act simply reports ``mode=chat``.
    """
    if not isinstance(run_state, dict) or not isinstance(payload, dict):
        return
    obligations = run_state.get("obligations")
    if not isinstance(obligations, list):
        return

    action = str(payload.get("tool_action") or payload.get("action") or "").strip()
    target = _payload_target(payload)
    fact_kinds = sorted(_payload_fact_kinds(payload))

    for obligation in obligations:
        if not isinstance(obligation, dict) or obligation.get("status") == "satisfied":
            continue
        if not bool(obligation.get("actionable", True)):
            continue
        if _successful_fact(payload, obligation):
            obligation["status"] = "satisfied"
            obligation["satisfied_by_action"] = action
            obligation["llm_submission_action"] = action
            obligation["llm_submission_target"] = target
            obligation["observed_fact_kinds"] = fact_kinds
            obligation["evidence_ok"] = True
            obligation["evidence_round"] = int(run_state.get("round") or 0)
        elif action:
            obligation["last_attempt_action"] = action
            obligation["last_attempt_target"] = target
            obligation["last_attempt_fact_kinds"] = fact_kinds
            obligation["last_attempt_ok"] = bool(payload.get("ok"))
