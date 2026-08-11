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

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ACT_REQUIRED = "ACT_REQUIRED"
ACT_FORBIDDEN = "ACT_FORBIDDEN"
ACT_UNKNOWN = "UNKNOWN"

TASK_LEVELS = ("L0", "L1", "L2", "L3")
_TASK_LEVEL_RANK = {level: index for index, level in enumerate(TASK_LEVELS)}
TASK_PROFILE_ARG_KEY = "_task_profile"
_TASK_CONTRACT_SCHEMA = "tiangong.v3.life_task_state.v1"
_TASK_PROFILE_SCHEMA = "tiangong.v3.task_profile.v2"

_HARD_TOOL_BAN_MARKERS = (
    "不要使用工具", "不要调用工具", "无需使用工具", "无需调用工具",
)
_TEXT_ONLY_MARKERS = ("只告诉我", "只解释", "只分析", "只讨论")
_GLOBAL_NO_ACTION_PATTERNS = (
    r"^(?:先|暂时)?(?:不要|别|不用|无需)(?:执行|操作|动|处理|修改|改|运行|调用工具|使用工具)(?:任何)?(?:操作|动作|工具)?$",
    r"^(?:先|暂时)?(?:不要|别)(?:做|干|动)(?:任何)?(?:东西|事情|操作)?$",
)

_REQUEST_CUES = (
    "请", "帮我", "帮忙", "给我", "替我", "直接", "一下", "现在", "马上", "立刻",
    "开始", "先", "只", "再", "接着", "然后", "然后再", "那就", "那么就", "把", "将", "不就行了",
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
_STRICT_FILE_TERMS = ("文件", "文档", "压缩包", "pdf", "表格")
_OBSERVATION_ANCHORS = _DIRECTORY_TERMS + _FILE_TERMS + (
    "日志", "配置", "数据库", "系统", "环境", "服务", "进程", "端口", "仓库", "repository", "repo",
    "网页", "网站", "浏览器", "链接",
)
_MUTATION_ANCHORS = (
    "文件", "目录", "文件夹", "代码", "源码", "项目", "仓库", "repository", "repo", "配置", "脚本",
    "错误", "bug", "故障",
)
_ARTIFACT_ANCHORS = (
    "word", "docx", "excel", "xlsx", "ppt", "pptx", "pdf", "zip", "报告", "文档", "文件", "表格", "压缩包", "桌面",
)
_DELIVERY_ANCHORS = _ARTIFACT_ANCHORS + ("邮件", "email", "附件", "消息", "微信")
_EXECUTION_ANCHORS = (
    "代码", "源码", "项目", "程序", "脚本", "接口", "api", "数据库", "服务", "环境", "命令", "语法",
    "构建", "编译", "单元测试", "测试用例", "测试集", "文件",
)

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
_RUN_EXECUTION_VERBS = ("运行", "跑一下", "执行", "启动", "编译", "构建")
_VERIFY_EXECUTION_VERBS = ("测试", "验证")
_EXECUTION_VERBS = _RUN_EXECUTION_VERBS + _VERIFY_EXECUTION_VERBS
_DELIVERY_STRONG_VERBS = ("上传", "提交", "交付", "发邮件", "发消息", "发微信", "发布", "分享")
_DELIVERY_ARTIFACT_VERBS = ("发送", "发给我", "发我", "传给我")

_COMPLETION_CLAIM_RE = re.compile(
    r"(?:已经|已)(?:完成|读取|读完|查看|检查|执行|下载|修改|写入|生成|发送|处理|打开|运行|测试|上传|部署)"
    r"|(?:完成了|办妥了?|搞定了?|读完了|读取完毕|查看完毕|检查完毕|执行完毕|下载完成|处理完成|运行完成|测试完成)",
    re.IGNORECASE,
)
_DEVIATION_SIGNAL_RE = re.compile(r"^[?？]{1,4}$")
_LOCAL_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s]+|(?:^|\s)(?:\.{0,2}[\\/])[^\s]+)")
_RELATIVE_FILE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})(?![A-Za-z0-9_.-])"
)
_BARE_ASCII_FILE_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_-][A-Za-z0-9_.-]*\.[A-Za-z0-9]{1,8})(?![A-Za-z0-9_.-])")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"\.[a-z0-9]{1,8}(?:$|[》〉」』】）)\]}'\"，。；：,.;:！!？?])", re.IGNORECASE)

_PREPARATION_ACTIONS = frozenset({"skill.route", "skill.get", "skill.read"})
_NEGATION_PREFIXES = (
    "不要", "别", "先别", "先不要", "不用", "无需", "禁止", "严禁", "绝不", "暂不", "暂时不要",
    "别再", "不要再", "不是让你", "不是叫你",
)
_EXTERNAL_EFFECT_TOKENS = frozenset({
    "download", "clone", "pull", "install", "deploy", "package", "compress", "extract", "fix", "export",
})
_OBSERVATION_ACTIONS = {
    "directory": frozenset({"file.list"}),
    "file": frozenset({"file.read", "code.read", "sheet.read", "pdf.extract_text"}),
}

_NEGATIVE_EXISTENCE_RE = re.compile(
    r"(?:如果|若|如若|假如).{0,24}(?:不存在|没有|找不到|未找到|缺失)"
    r"|(?:不存在|没有这个文件|找不到|未找到|缺失).{0,24}(?:告诉我|说明|回复|结束|即可)"
    r"|\bif\s+(?:it\s+|the\s+(?:file|path)\s+)?(?:does\s+not|doesn't)\s+exist\b"
    r"|\bif\s+(?:the\s+(?:file|path)\s+is\s+)?missing\b"
    r"|\bif\s+(?:it\s+is\s+)?not\s+found\b",
    re.IGNORECASE,
)

_TASK_LEVEL_THREE_ACTIONS = frozenset({
    "file.delete_to_trash",
    "rollback.apply",
    "shell.run",
    "python.run",
    "zip.extract",
})
_TASK_LEVEL_THREE_TOKENS = frozenset({
    "delete", "trash", "publish", "deploy", "install", "send", "upload",
    "submit", "share", "external", "shell", "python", "rollback",
})
_TASK_LEVEL_TWO_EXECUTION_PREFIXES = ("quality.", "qc.")
_TASK_PREPARATION_ACTIONS = frozenset({"skill.route", "skill.get", "skill.read"})
_TASK_NON_TOOL_STEPS = frozenset({"deliver_result"})


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def normalize_task_level(value: Any, default: str = "L0") -> str:
    level = str(value or "").strip().upper()
    return level if level in _TASK_LEVEL_RANK else default


def max_task_level(*levels: Any) -> str:
    normalized = [normalize_task_level(level) for level in levels]
    return max(normalized, key=lambda item: _TASK_LEVEL_RANK[item], default="L0")


def _registry_payloads() -> list[dict[str, Any]]:
    registry_root = Path(__file__).resolve().parents[1] / "omni_body_skill" / "registry"
    payloads: list[dict[str, Any]] = []
    for name in (
        "actions.json",
        "actions.appbus.merged.json",
        "app_actions.json",
        "professional_app_actions.json",
    ):
        try:
            payload = json.loads((registry_root / name).read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


@lru_cache(maxsize=1)
def declared_action_metadata() -> dict[str, dict[str, Any]]:
    """Return a compact authoritative action view from the existing registries."""

    metadata: dict[str, dict[str, Any]] = {}
    container_keys = (
        "actions", "capabilities", "base_plus_app_actions", "skill_router_actions",
        "v34_professional_app_actions",
    )
    for payload in _registry_payloads():
        containers: list[Any] = [payload]
        containers.extend(payload.get(key) for key in container_keys)
        for container in containers:
            if isinstance(container, dict):
                iterator = container.items()
            elif isinstance(container, list):
                iterator = (
                    (item.get("id") or item.get("action") or item.get("name"), item)
                    for item in container
                    if isinstance(item, dict)
                )
            else:
                continue
            for raw_name, raw_meta in iterator:
                name = str(raw_name or "").strip().lower()
                if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", name):
                    continue
                meta = raw_meta if isinstance(raw_meta, dict) else {}
                current = metadata.setdefault(name, {})
                for key in ("risk", "effect", "allowed_effect", "summary", "implemented"):
                    if key in meta and key not in current:
                        current[key] = meta[key]
    return metadata


def action_minimum_task_level(action: Any, metadata: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """Compute the Runtime floor from a real positive action, never prompt prose."""

    name = str(action or "").strip().lower()
    if not name or name in _TASK_PREPARATION_ACTIONS:
        return "L0", []
    meta = dict(metadata or declared_action_metadata().get(name) or {})
    risk = str(meta.get("risk") or "").strip().upper()
    tokens = set(part for part in re.split(r"[._-]+", name) if part)
    reasons: list[str] = [f"action:{name}"]
    if name in _TASK_LEVEL_THREE_ACTIONS or tokens.intersection(_TASK_LEVEL_THREE_TOKENS) or risk == "A5":
        reasons.append("external_or_destructive")
        if risk:
            reasons.append(f"tool_risk:{risk}")
        return "L3", reasons
    if name.startswith(_TASK_LEVEL_TWO_EXECUTION_PREFIXES):
        reasons.extend(("bounded_execution", f"tool_risk:{risk or 'unknown'}"))
        return "L2", reasons
    if risk in {"A4"}:
        reasons.extend(("high_risk_tool", f"tool_risk:{risk}"))
        return "L3", reasons
    if risk in {"A1", "A2", "A3"}:
        reasons.extend(("state_mutation", f"tool_risk:{risk}"))
        return "L2", reasons
    if risk == "A0" or name:
        reasons.append(f"tool_risk:{risk or 'unknown'}")
        return "L1" if risk == "A0" else "L2", reasons
    return "L2", reasons + ["unknown_action"]


def extract_forbidden_actions(user_text: Any) -> list[str]:
    """Extract only scoped negative tool constraints from registered action names."""

    text = str(user_text or "")
    lowered = text.lower()
    forbidden: list[str] = []
    for action in declared_action_metadata():
        for match in re.finditer(re.escape(action), lowered):
            left = lowered[max(0, match.start() - 96):match.start()]
            # A period is part of every registered action identifier, so it
            # cannot also be treated as a clause boundary here.
            clause_left = re.split(r"[，。；：,;:！!？?\n]", left)[-1]
            if re.search(
                r"(?:不得|不要|不许|禁止|严禁|别|无需|不用|do\s+not|don't|must\s+not|never)"
                r"[^，。；：,;:！!？?\n]{0,64}$",
                clause_left,
                re.IGNORECASE,
            ):
                if action not in forbidden:
                    forbidden.append(action)
                break
    return forbidden


def extract_model_task_profile(tool_args: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Remove the model-only profile before the governed tool validates arguments."""

    cleaned = dict(tool_args) if isinstance(tool_args, dict) else {}
    top_level_profile = cleaned.pop(TASK_PROFILE_ARG_KEY, None)
    if top_level_profile is None:
        top_level_profile = cleaned.pop("task_profile", None)
    nested = dict(cleaned.get("args")) if isinstance(cleaned.get("args"), dict) else {}
    profile = nested.pop(TASK_PROFILE_ARG_KEY, None)
    if profile is None:
        profile = nested.pop("task_profile", None)
    if profile is None:
        profile = top_level_profile
    if isinstance(cleaned.get("args"), dict):
        cleaned["args"] = nested
    return cleaned, profile if isinstance(profile, dict) else None


def _sanitize_plan_step(value: Any, index: int) -> dict[str, Any] | None:
    """Keep a model plan as a mutable hint; it never becomes acceptance authority."""

    if not isinstance(value, dict):
        return None
    action = str(value.get("action") or "").strip().lower()
    if not action:
        return None
    return {
        "step_id": str(value.get("step_id") or value.get("id") or f"S{index}").strip()[:48] or f"S{index}",
        "action": action,
        "target": str(value.get("target") or "").strip()[:1000],
        "depends_on": [str(item).strip()[:48] for item in (value.get("depends_on") or []) if str(item).strip()][:12],
        "acceptance_hint": [
            str(item).strip()[:120]
            for item in (value.get("acceptance") or value.get("evidence") or [])
            if str(item).strip()
        ][:12],
        "source": "model_advisory",
    }


def _sanitize_advisory_fact(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip().lower()
    if kind not in {"observation", "effect", "execution", "delivery"}:
        return None
    return {
        "fact_id": str(value.get("fact_id") or value.get("id") or f"M{index}").strip()[:64] or f"M{index}",
        "kind": kind,
        "target_path": str(value.get("target_path") or value.get("target") or "").strip()[:1000],
        "success_condition": str(value.get("success_condition") or value.get("description") or "").strip()[:500],
        "source": "model_advisory",
        "authority": "advisory",
    }


def _goal_fact_from_obligation(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip().lower()
    if kind not in {"observation", "effect", "execution", "delivery"}:
        return None
    fact = {
        "fact_id": str(value.get("id") or f"R{index}").strip()[:96] or f"R{index}",
        "kind": kind,
        "object_kind": str(value.get("object_kind") or "").strip(),
        "target_path": str(value.get("target_path") or "").strip()[:1000],
        "required": True,
        "actionable": bool(value.get("actionable", True)),
        "status": str(value.get("status") or "pending"),
        "evidence_policy": "successful_real_tool_result",
        "source": "runtime_user_goal",
        "authority": "runtime",
    }
    if str(value.get("evidence_predicate") or "").strip():
        fact["evidence_predicate"] = str(value.get("evidence_predicate") or "").strip()[:64]
    if str(value.get("requires_prior_kind") or "").strip():
        fact["requires_prior_kind"] = str(value.get("requires_prior_kind") or "").strip()[:32]
    return fact


def _required_stability(level: Any) -> int:
    # L0 has no work intention. L1 closes on one factual signal; L2/L3 need
    # two independent factual signals.  The life contract owns this decision;
    # external evidence checks may raise uncertainty but are not another judge.
    return {"L0": 0, "L1": 1, "L2": 2, "L3": 2}[normalize_task_level(level)]


def _completion_percentage(
    *,
    required_count: int,
    satisfied_count: int,
    required_stability: int,
    stability_count: int,
    evidence_uncertainty: float,
    constraint_risk: float,
) -> float:
    """Return a transparent progress projection, never a second terminal judge."""

    fact_coverage = (
        min(1.0, satisfied_count / required_count)
        if required_count
        else 1.0
    )
    stability_coverage = (
        min(1.0, stability_count / required_stability)
        if required_stability
        else 1.0
    )
    evidence_confidence = max(0.0, 1.0 - float(evidence_uncertainty or 0.0))
    safety = max(0.0, 1.0 - float(constraint_risk or 0.0))
    return round(
        100.0
        * (
            0.55 * fact_coverage
            + 0.20 * stability_coverage
            + 0.15 * evidence_confidence
            + 0.10 * safety
        ),
        1,
    )


def _task_contract_hash_payload(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": contract.get("schema"),
        "contract_id": contract.get("contract_id"),
        "plan_version": contract.get("plan_version"),
        "desired_facts": contract.get("desired_facts"),
        "advisory_facts": contract.get("advisory_facts"),
        "plan_hint": contract.get("plan_hint"),
        "constraints": contract.get("constraints"),
        "effective_level": contract.get("effective_level"),
        "phase": contract.get("phase"),
        "goal_state": contract.get("goal_state"),
        "acceptance_status": contract.get("acceptance_status"),
        "clarification_required": contract.get("clarification_required"),
    }


def _refresh_task_contract_hash(contract: dict[str, Any]) -> None:
    contract["plan_sha256"] = hashlib.sha256(
        _canonical_json(_task_contract_hash_payload(contract)).encode("utf-8")
    ).hexdigest()


def initialize_task_contract(user_text: Any, *, chat_mode: bool = False) -> dict[str, Any]:
    user_value = str(user_text or "")
    constraints = {"forbidden_tools": extract_forbidden_actions(user_text)}
    runtime_obligations = [] if chat_mode else build_action_obligations(user_text)
    desired_facts = [
        fact
        for index, item in enumerate(runtime_obligations, start=1)
        if (fact := _goal_fact_from_obligation(item, index)) is not None
    ]
    seed = {"user_text": user_value, "constraints": constraints, "desired_facts": desired_facts}
    contract_id = "goal_" + hashlib.sha256(_canonical_json(seed).encode("utf-8")).hexdigest()[:24]
    pending = [item for item in desired_facts if item.get("required") and item.get("status") != "satisfied"]
    phase = "INACTIVE" if chat_mode else "ACTIVE"
    contract = {
        "schema": _TASK_CONTRACT_SCHEMA,
        "profile_schema": _TASK_PROFILE_SCHEMA,
        "contract_id": contract_id,
        "plan_id": contract_id,
        "plan_version": 1,
        "source": "runtime_user_goal",
        "proposed_level": "L0",
        "runtime_minimum_level": "L0",
        "effective_level": "L0",
        "level_reasons": ["chat_mode"] if chat_mode else ["runtime_user_goal"],
        "phase": phase,
        "intent_active": not chat_mode,
        "desired_facts": desired_facts,
        "advisory_facts": [],
        "plan_hint": [],
        "steps": [],  # compatibility projection; advisory only
        "constraints": constraints,
        "advisory_constraints": {},
        "validation_issues": [],
        "acceptance_status": "not_applicable" if chat_mode else "pending",
        "clarification_required": bool(
            not chat_mode and any(term in _compact(user_value) for term in _AMBIGUOUS_TARGETS)
        ),
        "profile_status": "not_applicable" if chat_mode else "optional_not_received",
        "profile_retry_count": 0,
        "profile_required_pending": False,
        "required_stability": 0,
        "stability_signals": [],
        "reopen_count": 0,
        "transition_history": [{"from": None, "to": phase, "reason": "chat_mode" if chat_mode else "goal_registered"}],
        "goal_state": {
            "outcome_gap": 0.0 if not pending else 1.0,
            "evidence_uncertainty": 0.0 if not pending else 1.0,
            "constraint_risk": 0.0,
            "continuation_value": 0.0 if chat_mode else (1.0 if pending else 0.5),
            "completion_percentage": 100.0 if chat_mode else (35.0 if pending else 80.0),
        },
    }
    _refresh_task_contract_hash(contract)
    return contract


def reconcile_task_contract(
    existing: Any,
    model_profile: Any,
    *,
    user_text: Any,
    action: Any,
    target: Any = "",
    record_action: bool = True,
) -> dict[str, Any]:
    """Merge optional model advice while Runtime keeps fact and risk authority."""

    contract = dict(existing) if isinstance(existing, dict) else initialize_task_contract(user_text)
    profile = model_profile if isinstance(model_profile, dict) else {}
    action_name = str(action or "").strip().lower()
    prior_effective = normalize_task_level(contract.get("effective_level"))
    raw_proposed = str(profile.get("proposed_level") or "").strip().upper()
    proposed = normalize_task_level(raw_proposed, prior_effective if prior_effective != "L0" else ("L1" if action_name else "L0"))
    current_level, current_reasons = action_minimum_task_level(action_name)
    runtime_level = max_task_level(contract.get("runtime_minimum_level"), current_level)
    effective = max_task_level(prior_effective, proposed, runtime_level)

    plan_hint = [
        step
        for index, raw in enumerate(profile.get("plan_hint") or profile.get("steps") or [], start=1)
        if (step := _sanitize_plan_step(raw, index)) is not None
    ][:24]
    advisory_facts = [
        fact
        for index, raw in enumerate(profile.get("desired_facts") or [], start=1)
        if (fact := _sanitize_advisory_fact(raw, index)) is not None
    ][:24]
    previous_core = _canonical_json({
        "plan_hint": contract.get("plan_hint") or [],
        "advisory_facts": contract.get("advisory_facts") or [],
    })
    plan_supplied = "plan_hint" in profile or "steps" in profile
    facts_supplied = "desired_facts" in profile
    next_plan = (
        plan_hint
        if plan_supplied
        else [dict(item) for item in contract.get("plan_hint") or [] if isinstance(item, dict)]
    )
    next_advisory = (
        advisory_facts
        if facts_supplied
        else [dict(item) for item in contract.get("advisory_facts") or [] if isinstance(item, dict)]
    )
    next_core = _canonical_json({"plan_hint": next_plan, "advisory_facts": next_advisory})

    constraints = {"forbidden_tools": extract_forbidden_actions(user_text)}
    advisory_constraints = profile.get("constraints") if isinstance(profile.get("constraints"), dict) else {}
    validation_issues: list[str] = []
    known_actions = declared_action_metadata()
    for step in next_plan:
        step_action = str(step.get("action") or "").strip().lower()
        if step_action and step_action not in known_actions and step_action not in _TASK_NON_TOOL_STEPS:
            validation_issues.append(f"advisory_unknown_action:{step_action}")

    level_reasons = [str(item) for item in contract.get("level_reasons") or [] if str(item).strip()]
    for reason in current_reasons:
        if reason not in level_reasons:
            level_reasons.append(reason)
    if _TASK_LEVEL_RANK[effective] > _TASK_LEVEL_RANK[proposed]:
        level_reasons.append("runtime_prevented_downgrade")
    contract.update({
        "schema": _TASK_CONTRACT_SCHEMA,
        "profile_schema": _TASK_PROFILE_SCHEMA,
        "plan_version": int(contract.get("plan_version") or 1) + (1 if previous_core != next_core else 0),
        "source": "runtime_user_goal_with_model_advice" if profile else str(contract.get("source") or "runtime_user_goal"),
        "proposed_level": proposed,
        "runtime_minimum_level": runtime_level,
        "effective_level": effective,
        "level_reasons": list(dict.fromkeys(level_reasons)),
        "plan_hint": next_plan,
        "steps": next_plan,
        "advisory_facts": next_advisory,
        "constraints": constraints,
        "advisory_constraints": advisory_constraints,
        "validation_issues": list(dict.fromkeys(validation_issues)),
        "profile_status": "model_advice_received" if profile else "optional_not_received",
        "profile_retry_count": 0,
        "profile_required_pending": False,
        "required_stability": _required_stability(effective),
    })
    _refresh_task_contract_hash(contract)
    return contract


def task_contract_forbids_action(contract: Any, action: Any) -> bool:
    if not isinstance(contract, dict):
        return False
    forbidden = {
        str(item).strip().lower()
        for item in (contract.get("constraints") or {}).get("forbidden_tools") or []
        if str(item).strip()
    }
    return str(action or "").strip().lower() in forbidden


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
    if any(marker.replace(" ", "").lower() in compact for marker in _HARD_TOOL_BAN_MARKERS):
        return True
    if any(marker.replace(" ", "").lower() in compact for marker in _TEXT_ONLY_MARKERS):
        if not any(marker in compact for marker in _SEQUENCE_MARKERS):
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


def _sequenced_verb(compact: str, verbs: tuple[str, ...]) -> bool:
    prefixes = ("先", "再", "然后", "然后再", "接着", "那就", "那么就", "就")
    return any(prefix + verb in compact for prefix in prefixes for verb in verbs)


def _has_anchor(text: str, compact: str, anchors: tuple[str, ...]) -> bool:
    return bool(
        any(anchor.lower() in compact for anchor in anchors)
        or _LOCAL_PATH_RE.search(text)
        or _BARE_ASCII_FILE_RE.search(text)
        or _URL_RE.search(text)
        or _SUFFIX_RE.search(compact)
    )


def _looks_like_status_or_question(text: str, compact: str) -> bool:
    if any(marker in compact for marker in _STATUS_MARKERS):
        return True
    stripped = str(text or "").strip()
    return stripped.endswith(("?", "？", "吗", "么"))


def _explanation_only(compact: str) -> bool:
    explanation_positions = [
        compact.find(marker)
        for marker in _EXPLANATION_MARKERS
        if marker in compact
    ]
    if not explanation_positions or any(marker in compact for marker in _SEQUENCE_MARKERS):
        return False
    action_positions = [
        compact.find(verb)
        for verb in (
            _LOCAL_OBSERVE_VERBS
            + _SEARCH_VERBS
            + _MUTATION_VERBS
            + _ARTIFACT_VERBS
            + _EXTERNAL_EFFECT_VERBS
            + _EXECUTION_VERBS
            + _DELIVERY_STRONG_VERBS
            + _DELIVERY_ARTIFACT_VERBS
        )
        if verb in compact and _has_affirmative(compact, (verb,))
    ]
    # "说明如何创建" is discussion; "生成…失败就说明原因" is work with
    # a fallback explanation.  Relative order is more reliable than the mere
    # presence of an explanation word.
    return not action_positions or min(explanation_positions) <= min(action_positions)


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
        and _has_anchor(text, compact, _MUTATION_ANCHORS)
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

    run_execution = _has_affirmative(compact, _RUN_EXECUTION_VERBS)
    verify_execution = (
        _has_affirmative(compact, _VERIFY_EXECUTION_VERBS)
        and _has_anchor(text, compact, _EXECUTION_ANCHORS)
    )
    if (
        (run_execution or verify_execution)
        and (
            strong_cue
            or _leading_verb(compact, _EXECUTION_VERBS)
            or _sequenced_verb(compact, _EXECUTION_VERBS)
        )
        and not (questionish and not strong_cue)
    ):
        kinds.append("execution")

    strong_delivery = _has_affirmative(compact, _DELIVERY_STRONG_VERBS)
    artifact_delivery = _has_affirmative(compact, _DELIVERY_ARTIFACT_VERBS)
    if (
        strong_delivery
        and (
            strong_cue
            or _leading_verb(compact, _DELIVERY_STRONG_VERBS)
            or _sequenced_verb(compact, _DELIVERY_STRONG_VERBS)
        )
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
    delivery_words = {"send", "upload", "submit", "deliver", "publish", "share"}
    artifact_words = {"file", "directory", "folder", "workspace", "attachment", "repo", "repository", "project", "report", "document", "pdf", "zip"}

    def has_standalone_action(words: set[str]) -> bool:
        return any(
            re.search(rf"(?<![a-z0-9_.-]){re.escape(word)}(?![a-z0-9_.-])", english)
            for word in words
        )

    if has_standalone_action(observation_words) and (
        explicit or first in observation_words
    ) and (
        bool(anchors.intersection(artifact_words)) or bool(anchors.intersection({"search", "query", "find"}))
    ):
        fact_kinds.append("observation")
    if has_standalone_action(effect_words) and (explicit or first in effect_words):
        fact_kinds.append("effect")
    if has_standalone_action(execution_words) and (explicit or first in execution_words):
        fact_kinds.append("execution")
    if has_standalone_action(delivery_words) and (explicit or first in delivery_words):
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


def _requested_object_kind(user_text: object, fact_kind: str, target: Any = "") -> str:
    compact = _compact(user_text)
    text = str(user_text or "")
    if fact_kind == "observation":
        target_text = str(target or "").strip()
        if target_text:
            normalized = _normalize_path(target_text)
            if normalized.endswith("/"):
                return "directory"
            if _BARE_ASCII_FILE_RE.fullmatch(target_text) or re.search(r"\.[A-Za-z0-9]{1,8}$", normalized):
                return "file"
        if any(term in compact for term in _DIRECTORY_TERMS):
            return "directory"
        if (
            any(term in compact for term in _STRICT_FILE_TERMS)
            or _LOCAL_PATH_RE.search(text)
            or _BARE_ASCII_FILE_RE.search(text)
        ):
            return "file"
    return ""


def _extract_explicit_targets(user_text: object) -> list[str]:
    text = str(user_text or "")
    declared_actions = declared_action_metadata()
    candidates: list[tuple[int, str]] = []
    path_spans: list[tuple[int, int]] = []
    for match in _LOCAL_PATH_RE.finditer(text):
        value = match.group(0).strip()
        if value:
            candidates.append((match.start(), value))
            path_spans.append(match.span())
    for match in _RELATIVE_FILE_PATH_RE.finditer(text):
        if any(start <= match.start(1) and match.end(1) <= end for start, end in path_spans):
            continue
        value = match.group(1).strip()
        if value:
            candidates.append((match.start(1), value))
            path_spans.append(match.span(1))
    for match in _BARE_ASCII_FILE_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in path_spans):
            continue
        candidate = match.group(1).strip()
        if candidate.lower() in declared_actions:
            continue
        prefix = text[max(0, match.start(1) - 24):match.start(1)]
        if re.search(
            r"(?:(?:不得|不要|不许|禁止|严禁|别|无需|不用)\s*)?"
            r"(?:调用|执行|运行|使用|改用|call|invoke|execute|use)\s*$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        candidates.append((match.start(1), candidate))

    targets: list[str] = []
    seen: set[str] = set()
    for _, value in sorted(candidates, key=lambda item: item[0]):
        normalized = _normalize_path(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            targets.append(value)
    return targets


def _extract_explicit_target(user_text: object) -> str:
    targets = _extract_explicit_targets(user_text)
    return targets[0] if targets else ""


def _requests_existence_resolution(user_text: object) -> bool:
    """Whether absence is an explicitly acceptable observation outcome."""

    return bool(_NEGATIVE_EXISTENCE_RE.search(str(user_text or "")))


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
    explicit_targets = _extract_explicit_targets(text)
    requires_sha256 = bool(re.search(r"sha\s*[-_]?\s*256|sha256|计算.{0,12}(?:哈希|hash)|(?:哈希|hash).{0,12}计算", text, re.IGNORECASE))
    obligations: list[dict[str, Any]] = []
    obligation_index = 0
    fact_kinds = _requested_fact_kinds(text)
    if requires_sha256 and "execution" not in fact_kinds:
        fact_kinds.append("execution")
    for fact_kind in fact_kinds:
        use_explicit_target = fact_kind in {"observation", "effect"} or (fact_kind == "execution" and requires_sha256)
        targets = explicit_targets if use_explicit_target and explicit_targets else [""]
        for target in targets:
            object_kind = _requested_object_kind(text, fact_kind, target)
            obligation_index += 1
            obligation = {
                "id": f"execution:{fact_kind}:{obligation_index}",
                "kind": fact_kind,
                "object_kind": object_kind,
                "floor": ACT_REQUIRED,
                "status": "needs_clarification" if ambiguous else "pending",
                "actionable": not ambiguous,
                "target_path": target,
                "evidence_policy": "successful_real_tool_result",
                "source": "current_user_message",
            }
            if fact_kind == "execution" and requires_sha256:
                obligation["evidence_predicate"] = "sha256_digest"
                if "effect" in fact_kinds:
                    obligation["requires_prior_kind"] = "effect"
            elif (
                fact_kind == "observation"
                and object_kind == "file"
                and _requests_existence_resolution(text)
            ):
                # A verified absence is a real observation, not a failed read.
                # The predicate binds the query pattern and directory to the
                # requested file before accepting either exists=True or False.
                obligation["evidence_predicate"] = "existence_resolved"
            obligations.append(obligation)
    return obligations


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip().strip("`\"'").replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/").lower()


def _payload_targets(payload: dict[str, Any]) -> list[str]:
    tool_args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else {}
    nested = tool_args.get("args") if isinstance(tool_args.get("args"), dict) else {}
    targets: list[str] = []
    seen: set[str] = set()
    for source in (nested, tool_args):
        for key in ("target", "path", "directory", "dir", "file", "source", "destination", "output_path"):
            value = source.get(key)
            values = value if isinstance(value, (list, tuple, set)) else [value]
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    continue
                target = item.strip()
                normalized = _normalize_path(target)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    targets.append(target)
    return targets


def _payload_target(payload: dict[str, Any]) -> str:
    targets = _payload_targets(payload)
    return targets[0] if targets else ""


def _target_matches(payload: dict[str, Any], obligation: dict[str, Any]) -> bool:
    expected = _normalize_path(obligation.get("target_path"))
    if not expected:
        return True
    actual_targets = [_normalize_path(value) for value in _payload_targets(payload)]
    if not actual_targets:
        return False
    for actual in actual_targets:
        if actual == expected:
            return True
        # Governed tools may report an absolute workspace path while the user
        # names the same target relative to that workspace.
        if actual.endswith("/" + expected):
            return True
    return False


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
    ) or (
        contract.get("ok") is True
        and contract.get("write_effect") is True
        and bool(contract.get("paths"))
    ):
        facts.add("effect")

    tool_result = payload.get("tool_result") if isinstance(payload.get("tool_result"), dict) else {}
    result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
    if (
        action == "learning.ingest"
        and bool(result.get("card_id"))
        and bool(result.get("status"))
        and result.get("authority") == "life_kernel"
    ):
        facts.add("effect")

    action_tokens = set(part for part in re.split(r"[._-]+", action) if part)
    if action in {"file.list", "file.read", "code.read", "sheet.read", "pdf.extract_text"} or action_tokens.intersection(
        {"read", "list", "inspect", "search", "query", "find", "info", "browse", "scan", "open", "health", "status", "get", "show", "describe"}
    ):
        facts.add("observation")
    if action_tokens.intersection(_EXTERNAL_EFFECT_TOKENS):
        facts.add("effect")
    if action.startswith(("quality.", "qc.")) or action_tokens.intersection(
        {"run", "execute", "test", "verify", "start", "compile", "build", "syntax", "lint", "hash"}
    ):
        facts.add("execution")
    if (
        action in {"file.read", "code.read", "sheet.read", "pdf.extract_text"}
        and contract.get("ok") is True
        and contract.get("write_effect") is False
        and bool(contract.get("paths"))
    ):
        facts.add("execution")
    if action_tokens.intersection({"send", "upload", "submit", "deliver", "export", "post", "publish", "share"}):
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


def _payload_nested_args(payload: dict[str, Any]) -> dict[str, Any]:
    tool_args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else {}
    nested = tool_args.get("args") if isinstance(tool_args.get("args"), dict) else {}
    return nested


def _payload_result(payload: dict[str, Any]) -> dict[str, Any]:
    tool_result = payload.get("tool_result") if isinstance(payload.get("tool_result"), dict) else {}
    result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
    return result


def _payload_resolves_existence(payload: dict[str, Any], obligation: dict[str, Any]) -> bool:
    """Accept a positive or negative existence fact only when target-bound."""

    expected = _normalize_path(obligation.get("target_path"))
    if not expected or not bool(payload.get("ok")):
        return False
    action = str(payload.get("tool_action") or payload.get("action") or "").strip().lower()
    if action in {"file.read", "code.read", "sheet.read", "pdf.extract_text", "file.exists", "file.stat"}:
        return _target_matches(payload, obligation)
    if action != "file.list":
        return False

    nested = _payload_nested_args(payload)
    raw_pattern = next(
        (
            nested.get(key)
            for key in ("pattern", "name", "filename")
            if str(nested.get(key) or "").strip()
        ),
        "",
    )
    pattern = _normalize_path(raw_pattern)
    expected_name = expected.rsplit("/", 1)[-1]
    if not pattern or pattern != expected_name:
        return False

    # For a relative path such as docs/a.txt, the list root must be docs.
    if "/" in expected:
        expected_parent = expected.rsplit("/", 1)[0]
        actual_roots = [_normalize_path(value) for value in _payload_targets(payload)]
        if not any(root == expected_parent or root.endswith("/" + expected_parent) for root in actual_roots):
            return False

    result = _payload_result(payload)
    count = result.get("count")
    if isinstance(count, bool):
        return False
    try:
        return int(count) >= 0 and str(count).strip() != ""
    except (TypeError, ValueError):
        return False


def _payload_has_evidence_predicate(
    payload: dict[str, Any],
    predicate: str,
    obligation: dict[str, Any],
) -> bool:
    if predicate == "existence_resolved":
        return _payload_resolves_existence(payload, obligation)
    if predicate != "sha256_digest":
        return True
    pending: list[Any] = [payload.get("tool_result"), payload.get("tool_result_contract")]
    seen = 0
    while pending and seen < 256:
        current = pending.pop()
        seen += 1
        if isinstance(current, dict):
            for key, value in current.items():
                normalized_key = str(key or "").strip().lower().replace("-", "").replace("_", "")
                if normalized_key == "sha256" and re.fullmatch(r"[a-fA-F0-9]{64}", str(value or "").strip()):
                    return True
                if isinstance(value, (dict, list, tuple)):
                    pending.append(value)
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return False


def _successful_fact(payload: Any, obligation: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    required_kind = str(obligation.get("kind") or "action").strip().lower() or "action"
    required_action = str(obligation.get("required_action") or "").strip().lower()
    actual_action = str(payload.get("tool_action") or payload.get("action") or "").strip().lower()
    if required_action and actual_action != required_action:
        return False
    if required_kind not in _payload_fact_kinds(payload):
        return False
    evidence_predicate = str(obligation.get("evidence_predicate") or "").strip()
    if (
        required_kind == "observation"
        and evidence_predicate != "existence_resolved"
        and not _observation_object_matches(payload, obligation)
    ):
        return False
    if evidence_predicate and not _payload_has_evidence_predicate(payload, evidence_predicate, obligation):
        return False
    if (
        evidence_predicate != "existence_resolved"
        and (required_kind in {"observation", "effect"} or evidence_predicate)
        and not _target_matches(payload, obligation)
    ):
        return False
    return True


def obligation_is_satisfied(obligation: dict[str, Any], quality_history: list[dict[str, Any]] | None) -> bool:
    if not bool(obligation.get("actionable", True)):
        return False
    history = [item for item in (quality_history or []) if isinstance(item, dict)]
    prior_kind = str(obligation.get("requires_prior_kind") or "").strip().lower()
    for index, payload in enumerate(history):
        if not _successful_fact(payload, obligation):
            continue
        if not prior_kind:
            return True
        prior_obligation = {
            "kind": prior_kind,
            "target_path": obligation.get("target_path"),
            "actionable": True,
        }
        if any(_successful_fact(prior, prior_obligation) for prior in history[:index]):
            return True
    return False


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
    obligations: list[dict[str, Any]] | None = None,
) -> list[str]:
    active_obligations = obligations if isinstance(obligations, list) else build_action_obligations(user_text)
    active_obligations = [
        item for item in active_obligations
        if isinstance(item, dict) and bool(item.get("actionable", True))
    ]
    if not active_obligations:
        return []
    blockers: list[str] = []
    for obligation in active_obligations:
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
        prior_kind = str(obligation.get("requires_prior_kind") or "").strip().lower()
        prior_round_ok = True
        if prior_kind:
            current_round = int(run_state.get("round") or 0)
            prior_round_ok = any(
                isinstance(item, dict)
                and str(item.get("kind") or "").strip().lower() == prior_kind
                and item.get("status") == "satisfied"
                and int(item.get("evidence_round") or 0) < current_round
                for item in obligations
            )
        if prior_round_ok and _successful_fact(payload, obligation):
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


def _action_fact_kind(action: Any) -> str:
    name = str(action or "").strip().lower()
    tokens = set(part for part in re.split(r"[._-]+", name) if part)
    if name in {"file.list", "file.read", "code.read", "sheet.read", "pdf.extract_text"} or tokens.intersection(
        {"read", "list", "inspect", "search", "query", "find", "info", "browse", "scan", "open", "health", "status", "get", "show", "describe"}
    ):
        return "observation"
    if tokens.intersection({"send", "upload", "submit", "deliver", "export", "post", "publish", "share"}):
        return "delivery"
    if name.startswith(("quality.", "qc.")) or tokens.intersection(
        {"run", "execute", "test", "verify", "start", "compile", "build", "syntax", "lint", "hash"}
    ):
        return "execution"
    return "effect"


def build_task_contract_obligations(contract: Any) -> list[dict[str, Any]]:
    """Project only Runtime-owned desired facts into the existing hard gate.

    Model plan hints and advisory facts are deliberately excluded: a suggested
    action sequence may change as observations arrive and is never a reason to
    reject otherwise sufficient real-world evidence.
    """

    if not isinstance(contract, dict):
        return []
    obligations: list[dict[str, Any]] = []
    for index, fact in enumerate(contract.get("desired_facts") or [], start=1):
        if not isinstance(fact, dict) or fact.get("required") is False:
            continue
        if str(fact.get("authority") or "runtime") != "runtime":
            continue
        kind = str(fact.get("kind") or "").strip().lower()
        if kind not in {"observation", "effect", "execution", "delivery"}:
            continue
        obligation = {
            "id": str(fact.get("fact_id") or f"goal:{index}:{kind}"),
            "kind": kind,
            "object_kind": str(fact.get("object_kind") or ""),
            "floor": ACT_REQUIRED,
            "status": str(fact.get("status") or "pending"),
            "actionable": bool(fact.get("actionable", True)),
            "target_path": str(fact.get("target_path") or ""),
            "evidence_policy": "successful_real_tool_result",
            "source": "runtime_user_goal",
        }
        if str(fact.get("evidence_predicate") or "").strip():
            obligation["evidence_predicate"] = str(fact.get("evidence_predicate") or "").strip()
        if str(fact.get("requires_prior_kind") or "").strip():
            obligation["requires_prior_kind"] = str(fact.get("requires_prior_kind") or "").strip()
        obligations.append(obligation)
    return obligations


def merge_action_obligations(*groups: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for raw in group if isinstance(group, list) else []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            key = (
                str(item.get("kind") or "").strip().lower(),
                _normalize_path(item.get("target_path")),
                str(item.get("required_action") or "").strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _transition_task_phase(contract: dict[str, Any], phase: str, reason: str) -> None:
    previous = str(contract.get("phase") or "ACTIVE")
    if previous == phase:
        return
    contract["phase"] = phase
    history = [dict(item) for item in contract.get("transition_history") or [] if isinstance(item, dict)]
    history.append({"from": previous, "to": phase, "reason": str(reason or "")[:200]})
    contract["transition_history"] = history[-24:]


def _sync_goal_facts_from_obligations(contract: dict[str, Any], obligations: Any) -> list[dict[str, Any]]:
    current = [dict(item) for item in contract.get("desired_facts") or [] if isinstance(item, dict)]
    if not isinstance(obligations, list):
        return current
    prior = {str(item.get("fact_id") or ""): item for item in current if str(item.get("fact_id") or "")}
    synced: list[dict[str, Any]] = []
    for index, obligation in enumerate(obligations, start=1):
        fact = _goal_fact_from_obligation(obligation, index)
        if fact is None:
            continue
        old = prior.get(str(fact.get("fact_id") or ""), {})
        for key in ("evidence_round", "evidence_action", "evidence_target", "observed_fact_kinds"):
            if key in old:
                fact[key] = old[key]
        if str(obligation.get("status") or "") == "satisfied":
            fact["status"] = "satisfied"
            fact["evidence_round"] = int(obligation.get("evidence_round") or old.get("evidence_round") or 0)
            fact["evidence_action"] = str(obligation.get("satisfied_by_action") or old.get("evidence_action") or "")
            fact["evidence_target"] = str(obligation.get("llm_submission_target") or old.get("evidence_target") or "")
            fact["observed_fact_kinds"] = list(obligation.get("observed_fact_kinds") or old.get("observed_fact_kinds") or [])
        synced.append(fact)
    return synced or current


def update_task_contract_evidence(
    contract: Any,
    payload: Any,
    *,
    round_number: int = 0,
    obligations: Any = None,
) -> dict[str, Any]:
    if not isinstance(contract, dict) or not isinstance(payload, dict):
        return contract if isinstance(contract, dict) else {}
    updated = dict(contract)
    was_deactivated = str(updated.get("phase") or "") == "DEACTIVATED"
    desired_facts = _sync_goal_facts_from_obligations(updated, obligations)
    if not isinstance(obligations, list):
        for fact in desired_facts:
            obligation = {
                "kind": fact.get("kind"),
                "object_kind": fact.get("object_kind"),
                "target_path": fact.get("target_path"),
                "actionable": fact.get("actionable", True),
                "evidence_predicate": fact.get("evidence_predicate"),
            }
            prior_kind = str(fact.get("requires_prior_kind") or "").strip().lower()
            prior_round_ok = not prior_kind or any(
                str(item.get("kind") or "").strip().lower() == prior_kind
                and item.get("status") == "satisfied"
                and int(item.get("evidence_round") or 0) < int(round_number or 0)
                for item in desired_facts
            )
            if fact.get("status") != "satisfied" and prior_round_ok and _successful_fact(payload, obligation):
                fact["status"] = "satisfied"
                fact["evidence_round"] = int(round_number or 0)
                fact["evidence_action"] = str(payload.get("tool_action") or payload.get("action") or "")
                fact["evidence_target"] = _payload_target(payload)
                fact["observed_fact_kinds"] = sorted(_payload_fact_kinds(payload))

    required = [item for item in desired_facts if item.get("required") is not False and bool(item.get("actionable", True))]
    satisfied = [item for item in required if item.get("status") == "satisfied"]
    pending_count = max(0, len(required) - len(satisfied))
    outcome_gap = round(pending_count / len(required), 4) if required else 0.0
    evidence_uncertainty = outcome_gap
    payload_ok = bool(payload.get("ok"))
    constraint_risk = 0.0 if payload_ok else 1.0
    signals = [str(item) for item in updated.get("stability_signals") or [] if str(item).strip()]
    if payload_ok and str(payload.get("tool_action") or payload.get("action") or "").strip():
        signal = f"evidence_round:{int(round_number or 0)}"
        if signal not in signals:
            signals.append(signal)
    required_stability = _required_stability(updated.get("effective_level"))

    updated["desired_facts"] = desired_facts
    updated["stability_signals"] = signals[-24:]
    updated["required_stability"] = required_stability
    updated["goal_state"] = {
        "outcome_gap": outcome_gap,
        "evidence_uncertainty": evidence_uncertainty,
        "constraint_risk": constraint_risk,
        "continuation_value": round(max(outcome_gap, evidence_uncertainty, constraint_risk), 4),
        "completion_percentage": _completion_percentage(
            required_count=len(required),
            satisfied_count=len(satisfied),
            required_stability=required_stability,
            stability_count=len(signals),
            evidence_uncertainty=evidence_uncertainty,
            constraint_risk=constraint_risk,
        ),
    }
    if was_deactivated:
        if not payload_ok or pending_count:
            updated["reopen_count"] = int(updated.get("reopen_count") or 0) + 1
            updated["intent_active"] = True
            updated["acceptance_status"] = "pending"
            _transition_task_phase(updated, "REOPENED", "contradictory_evidence")
        else:
            updated["intent_active"] = False
            updated["acceptance_status"] = "accepted"
    elif pending_count:
        updated["intent_active"] = True
        updated["acceptance_status"] = "pending"
        _transition_task_phase(updated, "ACTIVE", "goal_gap_remains")
    elif required and len(signals) < required_stability:
        updated["intent_active"] = True
        updated["acceptance_status"] = "candidate"
        _transition_task_phase(updated, "VERIFYING", "facts_covered_waiting_for_stability")
    elif required:
        updated["intent_active"] = True
        updated["acceptance_status"] = "candidate"
        _transition_task_phase(updated, "SATISFIED", "facts_covered_by_real_evidence")
    else:
        updated["acceptance_status"] = "not_applicable"
    _refresh_task_contract_hash(updated)
    return updated


def decide_task_contract_completion(
    contract: Any,
    *,
    evidence_reasons: Any = None,
    evidence_status: Any = "complete",
    final_reply: Any = None,
    has_real_observation: bool = False,
) -> tuple[dict[str, Any], bool, str, list[str]]:
    """Let the life contract make the one authoritative completion decision.

    ``evidence_reasons`` are observations produced by the evidence checker.
    They can increase uncertainty and keep the intention active, but the
    checker does not infer task meaning or own a terminal state.
    """

    if not isinstance(contract, dict):
        return {}, False, "incomplete", ["life_task_contract_missing"]

    updated = dict(contract)
    reasons: list[str] = []
    for raw in evidence_reasons if isinstance(evidence_reasons, list) else []:
        text = str(raw or "").strip()
        if text and text not in reasons:
            reasons.append(text)

    evidence_terminal = str(evidence_status or "").strip().lower()
    if evidence_terminal == "clarify" or bool(updated.get("clarification_required")):
        updated = transition_task_contract_terminal(
            updated, "awaiting_user", ["clarification_required"]
        )
        return updated, True, "clarify", []

    all_desired = [
        item
        for item in updated.get("desired_facts") or []
        if isinstance(item, dict) and item.get("required") is not False
    ]
    clarification_facts = [
        item
        for item in all_desired
        if not bool(item.get("actionable", True))
        or str(item.get("status") or "").strip().lower() == "needs_clarification"
    ]
    if clarification_facts:
        updated = transition_task_contract_terminal(
            updated,
            "awaiting_user",
            [
                f"life_goal_needs_clarification:{str(item.get('fact_id') or item.get('kind') or 'fact')}"
                for item in clarification_facts[:12]
            ],
        )
        return updated, True, "clarify", []

    desired = [
        item
        for item in all_desired
        if bool(item.get("actionable", True))
    ]
    satisfied = [item for item in desired if item.get("status") == "satisfied"]
    pending = [item for item in desired if item.get("status") != "satisfied"]
    signals = [str(item) for item in updated.get("stability_signals") or [] if str(item).strip()]
    required_stability = int(updated.get("required_stability") or _required_stability(updated.get("effective_level")))
    reply_text = str(final_reply or "").strip()

    if reasons:
        goal_state = dict(updated.get("goal_state") or {})
        goal_state["evidence_uncertainty"] = 1.0
        goal_state["continuation_value"] = 1.0
        goal_state["completion_percentage"] = _completion_percentage(
            required_count=len(desired),
            satisfied_count=len(satisfied),
            required_stability=required_stability,
            stability_count=len(signals),
            evidence_uncertainty=1.0,
            constraint_risk=float(goal_state.get("constraint_risk") or 0.0),
        )
        updated["goal_state"] = goal_state
        updated["acceptance_status"] = "pending"
        updated["intent_active"] = True
        _transition_task_phase(updated, "VERIFYING", "evidence_uncertainty_remains")
        updated["evidence_check"] = {"ok": False, "reasons": reasons[:12]}
        _refresh_task_contract_hash(updated)
        status = "failed" if evidence_terminal == "failed" else "incomplete"
        return updated, False, status, reasons

    if pending:
        pending_reasons = [
            f"life_goal_pending:{str(item.get('fact_id') or item.get('kind') or 'fact')}"
            for item in pending[:12]
        ]
        updated["acceptance_status"] = "pending"
        updated["intent_active"] = True
        _transition_task_phase(updated, "ACTIVE", "goal_gap_remains")
        updated["evidence_check"] = {"ok": True, "reasons": []}
        _refresh_task_contract_hash(updated)
        return updated, False, "incomplete", pending_reasons

    if required_stability and len(signals) < required_stability:
        stability_reasons = [
            f"life_stability_pending:{len(signals)}/{required_stability}"
        ]
        updated["acceptance_status"] = "candidate"
        updated["intent_active"] = True
        _transition_task_phase(updated, "VERIFYING", "stability_not_yet_reached")
        updated["evidence_check"] = {"ok": True, "reasons": []}
        _refresh_task_contract_hash(updated)
        return updated, False, "incomplete", stability_reasons

    # No explicit Runtime fact means semantic authority stays with the model:
    # stopping tool selection and producing a substantive reply is its claim
    # that the subjective goal is satisfied.  Runtime still requires either a
    # real observation or a direct answer before accepting that claim.
    if not desired and not (has_real_observation or reply_text):
        updated["acceptance_status"] = "pending"
        updated["intent_active"] = True
        _transition_task_phase(updated, "ACTIVE", "no_outcome_signal")
        _refresh_task_contract_hash(updated)
        return updated, False, "incomplete", ["life_outcome_signal_missing"]

    goal_state = dict(updated.get("goal_state") or {})
    goal_state.update({
        "outcome_gap": 0.0,
        "evidence_uncertainty": 0.0,
        "constraint_risk": 0.0,
        "continuation_value": 0.0,
        "completion_percentage": 100.0,
    })
    updated["goal_state"] = goal_state
    updated["acceptance_status"] = "candidate"
    updated["intent_active"] = True
    updated["evidence_check"] = {"ok": True, "reasons": []}
    _transition_task_phase(updated, "SATISFIED", "biological_goal_satisfied")
    _refresh_task_contract_hash(updated)
    return updated, True, "complete", []


def transition_task_contract_terminal(contract: Any, status: Any, reasons: Any = None) -> dict[str, Any]:
    """Persist the authoritative life decision into its terminal phase."""

    if not isinstance(contract, dict):
        return {}
    updated = dict(contract)
    terminal = str(status or "").strip().lower()
    reason_text = "; ".join(str(item).strip() for item in (reasons or []) if str(item).strip()) if isinstance(reasons, list) else str(reasons or "")
    signals = [str(item) for item in updated.get("stability_signals") or [] if str(item).strip()]
    if terminal in {"complete", "chat_reply"}:
        if "life_terminal_commit" not in signals:
            signals.append("life_terminal_commit")
        updated["stability_signals"] = signals[-24:]
        updated["intent_active"] = False
        updated["acceptance_status"] = "not_applicable" if terminal == "chat_reply" else "accepted"
        goal_state = dict(updated.get("goal_state") or {})
        goal_state.update({
            "outcome_gap": 0.0,
            "evidence_uncertainty": 0.0,
            "continuation_value": 0.0,
            "completion_percentage": 100.0,
        })
        updated["goal_state"] = goal_state
        _transition_task_phase(updated, "DEACTIVATED", reason_text or "life_goal_complete")
    elif terminal in {"clarify", "awaiting_user", "confirm_pending"}:
        updated["intent_active"] = True
        updated["acceptance_status"] = "pending"
        _transition_task_phase(updated, "WAITING", reason_text or terminal)
    elif terminal in {"interrupted", "force_stopped"}:
        updated["intent_active"] = False
        updated["acceptance_status"] = "interrupted"
        _transition_task_phase(updated, "INTERRUPTED", reason_text or terminal)
    elif terminal in {"failed", "incomplete"}:
        updated["intent_active"] = False
        updated["acceptance_status"] = "blocked"
        _transition_task_phase(updated, "BLOCKED", reason_text or terminal)
    _refresh_task_contract_hash(updated)
    return updated
