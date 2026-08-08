from __future__ import annotations

"""Execution-integrity invariants for Tiangong V3.

This module is intentionally thin. It does not choose tools, execute actions,
or judge task quality. It only:

1. establishes a conservative Runtime execution floor from the current user text;
2. treats the LLM's real tool call/result as its execution submission; and
3. blocks terminal completion when an explicit tool-required request has no
   matching successful tool evidence.

Runtime owns factual execution integrity. The LLM still owns semantic
understanding, planning, tool choice, replanning and answer quality.
"""

import re
from typing import Any

ACT_REQUIRED = "ACT_REQUIRED"
ACT_FORBIDDEN = "ACT_FORBIDDEN"
ACT_UNKNOWN = "UNKNOWN"

# Global user controls only. Scoped negation such as
# "查看目录，但不要修改" must not disable the requested observation.
_RESPONSE_ONLY_MARKERS = (
    "不要使用工具", "不要调用工具", "无需使用工具", "无需调用工具",
    "只告诉我", "只解释", "只分析", "只讨论", "先分析", "先讨论",
)
_GLOBAL_NO_ACTION_PATTERNS = (
    r"^(?:先|暂时)?(?:不要|别|不用|无需)(?:执行|操作|动|处理|修改|改|运行|调用工具|使用工具)(?:任何)?(?:操作|动作|工具)?$",
    r"^(?:先|暂时)?(?:不要|别)(?:做|干|动)(?:任何)?(?:东西|事情|操作)?$",
)

# These cues establish command context; they never choose a tool.
_REQUEST_CUES = (
    "请", "帮我", "帮忙", "给我", "替我", "直接", "一下", "现在", "马上", "立刻",
    "开始", "先", "只", "再", "接着", "那就", "那么就", "把", "将", "不就行了",
)
_STRONG_REQUEST_CUES = ("请", "帮我", "帮忙", "替我", "直接", "现在", "马上", "立刻", "开始", "不就行了")
_SEQUENCE_MARKERS = ("然后", "然后再", "再帮我", "并且", "同时", "接着", "那就", "那么就")
_EXPLANATION_MARKERS = ("解释", "说明", "讲讲", "告诉我怎么", "告诉我如何", "分析怎么", "分析如何")
_STATUS_MARKERS = ("结果", "状态", "情况", "是否", "是什么", "什么意思", "怎么样", "为什么", "怎么回事")

_AMBIGUOUS_TARGETS = (
    "那个目录", "某个目录", "一个目录", "那个文件夹", "某个文件夹",
    "那个文件", "某个文件", "那个附件", "某个附件",
)
_DIRECTORY_TERMS = ("目录", "文件夹", "workspace", "工作区", "当前路径")
_FILE_TERMS = ("文件", "文档", "附件", "压缩包", "pdf", "表格")
_OBSERVATION_ANCHORS = _DIRECTORY_TERMS + _FILE_TERMS + (
    "日志", "配置", "数据库", "系统", "环境", "服务", "进程", "端口", "仓库", "repository", "repo",
)
_MUTATION_ANCHORS = ("文件", "目录", "文件夹", "代码", "源码", "项目", "仓库", "repository", "repo", "配置", "脚本")
_ARTIFACT_ANCHORS = (
    "word", "docx", "excel", "xlsx", "ppt", "pptx", "pdf", "zip", "报告", "文档", "文件", "表格", "压缩包", "桌面",
)
_DELIVERY_ANCHORS = _ARTIFACT_ANCHORS + ("邮件", "email", "附件")

# Four factual classes only. These are not task taxonomies and never prescribe
# a concrete capability. High precision is more important than recall: an
# uncertain instruction remains UNKNOWN and falls through to the existing V3
# chain/LLM instead of becoming a new hard blocker.
_LOCAL_OBSERVE_VERBS = (
    "读取", "读一下", "读下", "查看", "看一下", "看下", "看看", "列出", "列一下",
    "检查", "扫描", "浏览", "打开",
)
_SEARCH_VERBS = ("搜索", "搜一下", "查询", "查一下", "帮我查", "帮我搜")
_MUTATION_VERBS = ("修改", "改一下", "改下", "修复", "删除", "移除", "复制", "移动", "重命名")
_ARTIFACT_VERBS = ("写入", "创建", "新建", "生成", "保存")
_EXTERNAL_EFFECT_VERBS = ("下载", "克隆", "拉取", "安装", "部署", "打包", "压缩", "解压", "导出")
_EXECUTION_VERBS = ("运行", "跑一下", "执行", "测试", "验证", "启动", "编译", "构建")
_DELIVERY_STRONG_VERBS = ("上传", "提交", "交付")
_DELIVERY_ARTIFACT_VERBS = ("发送", "发给我", "发我", "传给我")

_COMPLETION_CLAIM_RE = re.compile(
    r"(?:已经|已)(?:完成|读取|读完|查看|检查|执行|下载|修改|写入|生成|发送|处理|打开|运行|测试|上传|部署)"
    r"|(?:完成了|办妥了?|搞定了?|读完了|读取完毕|查看完毕|检查完毕|执行完毕|下载完成|处理完成|运行完成|测试完成)",
    re.IGNORECASE,
)
_DEVIATION_SIGNAL_RE = re.compile(r"^[?？]{1,4}$")
_LOCAL_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s]+|(?:^|\s)(?:\.{0,2}[\\/])[^\s]+)")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\.[a-z0-9]{1,8}(?:$|[，。；：,.;:！!？?])", re.IGNORECASE)

_PREPARATION_ACTIONS = frozenset({"skill.route", "skill.get", "skill.read"})
_NEGATION_PREFIXES = (
    "不要", "别", "先别", "先不要", "不用", "无需", "禁止", "暂不", "暂时不要",
    "别再", "不要再", "不是让你", "不是叫你",
)
_EXTERNAL_EFFECT_TOKENS = frozenset({
    "download", "clone", "pull", "install", "deploy", "package", "compress", "extract", "fix", "export",
})
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
    if any(marker.replace(" ", "").lower() in compact for marker in _RESPONSE_ONLY_MARKERS):
        return True
    normalized = _intent_compact(text)
    return any(re.fullmatch(pattern, normalized) for pattern in _GLOBAL_NO_ACTION_PATTERNS)


def _meta_or_hypothetical(text: str) -> bool:
    """Keep the pre-existing narrow V3 discussion boundary stable."""
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
    """Only the existing high-confidence no-side-effect discussion cases."""
    return bool(
        _is_high_confidence_capability_question(user_text)
        or _is_hypothetical_action_discussion(user_text)
        or _is_deferred_action_explanation(user_text)
    )


def _verb_occurs_affirmatively(compact: str, verb: str) -> bool:
    for match in re.finditer(re.escape(verb), compact):
        left = compact[max(0, match.start() - 14):match.start()]
        clause_left = re.split(r"[，。；：,.;:！!？?]", left)[-1]
        if any(left.endswith(prefix) for prefix in _NEGATION_PREFIXES):
            continue
        if any(re.search(re.escape(prefix) + r"[^，。；：,.;:！!？?]{0,8}$", clause_left) for prefix in _NEGATION_PREFIXES):
            continue
        return True
    return False


def _has_affirmative(compact: str, verbs: tuple[str, ...]) -> bool:
    return any(_verb_occurs_affirmatively(compact, verb) for verb in verbs)


def _has_request_cue(compact: str) -> bool:
    return any(cue in compact for cue in _REQUEST_CUES)


def _has_strong_request_cue(compact: str) -> bool:
    return any(cue in compact for cue in _STRONG_REQUEST_CUES)


def _leading_verb(compact: str, verbs: tuple[str, ...]) -> bool:
    lead = compact.lstrip("，。；：,.;:！!？?")
    return any(lead.startswith(verb) for verb in verbs)


def _has_anchor(text: str, compact: str, anchors: tuple[str, ...]) -> bool:
    return bool(
        any(anchor.lower() in compact for anchor in anchors)
        or _LOCAL_PATH_RE.search(text)
        or _URL_RE.search(text)
        or _SUFFIX_RE.search(compact)
    )


def _looks_like_status_or_question(text: str, compact: str) -> bool:
    if any(marker in compact for marker in _STATUS_MARKERS):
        return True
    stripped = str(text or "").strip()
    return stripped.endswith(("?", "？", "吗", "么"))


def _explanation_only(compact: str) -> bool:
    return bool(
        any(marker in compact for marker in _EXPLANATION_MARKERS)
        and not any(marker in compact for marker in _SEQUENCE_MARKERS)
    )


def _chinese_requested_fact_kinds(text: str) -> list[str]:
    compact = _compact(text)
    if not compact or _explanation_only(compact):
        return []

    kinds: list[str] = []
    cue = _has_request_cue(compact)
    strong_cue = _has_strong_request_cue(compact)
    questionish = _looks_like_status_or_question(text, compact)

    local_observe = _has_affirmative(compact, _LOCAL_OBSERVE_VERBS)
    search_observe = _has_affirmative(compact, _SEARCH_VERBS)
    if (
        local_observe
        and _has_anchor(text, compact, _OBSERVATION_ANCHORS)
        and (cue or _leading_verb(compact, _LOCAL_OBSERVE_VERBS))
    ) or (
        search_observe
        and (strong_cue or _leading_verb(compact, _SEARCH_VERBS))
        and not (questionish and not strong_cue)
    ):
        kinds.append("observation")

    mutation = _has_affirmative(compact, _MUTATION_VERBS)
    artifact_effect = _has_affirmative(compact, _ARTIFACT_VERBS)
    external_effect = _has_affirmative(compact, _EXTERNAL_EFFECT_VERBS)
    if (
        mutation
        and (
            _has_anchor(text, compact, _MUTATION_ANCHORS)
            or _verb_occurs_affirmatively(compact, "修复")
        )
        and (cue or _leading_verb(compact, _MUTATION_VERBS))
    ) or (
        artifact_effect
        and _has_anchor(text, compact, _ARTIFACT_ANCHORS)
        and (cue or _leading_verb(compact, _ARTIFACT_VERBS))
    ) or (
        external_effect
        and (cue or _leading_verb(compact, _EXTERNAL_EFFECT_VERBS))
    ):
        kinds.append("effect")

    execution = _has_affirmative(compact, _EXECUTION_VERBS)
    if (
        execution
        and (strong_cue or _leading_verb(compact, _EXECUTION_VERBS))
        and not (questionish and not strong_cue)
    ):
        kinds.append("execution")

    strong_delivery = _has_affirmative(compact, _DELIVERY_STRONG_VERBS)
    artifact_delivery = _has_affirmative(compact, _DELIVERY_ARTIFACT_VERBS)
    if (
        strong_delivery
        and (strong_cue or _leading_verb(compact, _DELIVERY_STRONG_VERBS))
        and not (questionish and not strong_cue)
    ) or (
        artifact_delivery
        and _has_anchor(text, compact, _DELIVERY_ANCHORS)
        and (cue or _leading_verb(compact, _DELIVERY_ARTIFACT_VERBS))
    ):
        kinds.append("delivery")

    return kinds


def _english_requested_fact_kinds(text: str) -> list[str]:
    english = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not english:
        return []
    if any(marker in english for marker in ("explain how", "tell me how", "what would you do", "how would you")) and not any(
        marker in english for marker in (" then ", " and then ", " go ahead ")
    ):
        return []
    explicit = bool(re.search(r"(?:^|\b)(please|for me|must|now|directly|go ahead|do it|can you|could you)\b", english))
    tokens = re.findall(r"[a-z]+", english)
    if not tokens:
        return []
    first = tokens[0]
    anchors = set(tokens)
    fact_kinds: list[str] = []
    observation_words = {"read", "list", "inspect", "check", "scan", "browse", "open", "search", "query", "find"}
    effect_words = {"modify", "edit", "fix", "delete", "remove", "copy", "move", "rename", "download", "clone", "pull", "install", "deploy", "package", "compress", "extract", "export"}
    execution_words = {"run", "execute", "test", "verify", "start", "compile", "build"}
    delivery_words = {"send", "upload", "submit", "deliver"}
    artifact_words = {"file", "directory", "folder", "workspace", "attachment", "repo", "repository", "project", "report", "document", "pdf", "zip"}

    if any(word in anchors for word in observation_words) and (
        explicit or first in observation_words
    ) and (
        bool(anchors.intersection(artifact_words)) or bool(anchors.intersection({"search", "query", "find"}))
    ):
        fact_kinds.append("observation")
    if any(word in anchors for word in effect_words) and (explicit or first in effect_words):
        fact_kinds.append("effect")
    if any(word in anchors for word in execution_words) and (explicit or first in execution_words):
        fact_kinds.append("execution")
    if any(word in anchors for word in delivery_words) and (explicit or first in delivery_words):
        fact_kinds.append("delivery")
    return fact_kinds


def _requested_fact_kinds(user_text: object) -> list[str]:
    text = str(user_text or "")
    kinds = _chinese_requested_fact_kinds(text)
    for kind in _english_requested_fact_kinds(text):
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def runtime_execution_floor(user_text: object) -> str:
    """Conservative pre-LLM execution floor.

    ACT_REQUIRED means a real external/tool action is unambiguously required.
    UNKNOWN intentionally preserves the existing V3/LLM decision path.
    """
    text = str(user_text or "").strip()
    if not text:
        return ACT_UNKNOWN
    if _response_only(text) or is_execution_discussion_only(text):
        return ACT_FORBIDDEN
    if _requested_fact_kinds(text):
        return ACT_REQUIRED
    return ACT_UNKNOWN


def _requested_object_kind(user_text: object, fact_kind: str) -> str:
    compact = _compact(user_text)
    if fact_kind == "observation":
        if any(term in compact for term in _DIRECTORY_TERMS):
            return "directory"
        if any(term in compact for term in _FILE_TERMS) or _LOCAL_PATH_RE.search(str(user_text or "")):
            return "file"
    return ""


def build_action_obligations(user_text: Any) -> list[dict[str, Any]]:
    """Build factual obligations, never a concrete tool plan.

    The Runtime floor is the anti-escape fallback. The LLM's actual tool call
    and ToolResult are its execution submission; a self-declared "work" mode is
    never accepted as proof that anything happened.
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
    # Local file/code mutations are factual only when the existing ToolResult
    # contract supplied authoritative write evidence above.
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
    """Reconcile the Runtime floor with the LLM's real tool submission."""
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
