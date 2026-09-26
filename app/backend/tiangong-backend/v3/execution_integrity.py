from __future__ import annotations

"""Execution-integrity invariants for Tiangong V3.

This module is intentionally thin. It does not choose tools, execute actions,
or judge task quality. It only:

1. records real tool submissions and their structured results;
2. reconciles explicitly registered facts with those results; and
3. preserves factual failures without deriving requirements from prose.

Runtime owns factual execution integrity. The LLM still owns semantic
understanding, planning, tool choice, replanning and answer quality.
"""

import ast
import hashlib
import json
import re
import shlex
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


_AMBIGUOUS_TARGETS = (
    "那个目录", "某个目录", "一个目录", "那个文件夹", "某个文件夹",
    "那个文件", "某个文件", "那个附件", "某个附件",
)

# Four factual classes only. These are not task taxonomies and never prescribe
# a concrete capability. High precision is more important than recall: an
# uncertain instruction remains UNKNOWN and falls through to the existing V3
# chain/LLM instead of becoming a new hard blocker.

_RELATIVE_FILE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
)

_PREPARATION_ACTIONS = frozenset({"skill.route", "skill.get", "skill.read"})
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


@lru_cache(maxsize=1)
def declared_action_metadata() -> dict[str, dict[str, Any]]:
    from capability_dictionary import load_dictionary
    return load_dictionary().action_metadata()


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
    """Compatibility API: natural-language interpretation belongs to the model."""
    return []


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
    for key in ("requirement_version", "minimum_test_count"):
        if type(value.get(key)) is int:
            fact[key] = value[key]
    for key in ("target_state", "evidence_dependency_paths"):
        if key in value:
            fact[key] = value[key]
    return fact


def _required_stability(level: Any) -> int:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return 0


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
        "clarification_required": False,
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


# 引号内片段是"数据"（文件名/内容/称呼），不是请求动词：义务动词匹配前
# 剥离，防止文件名叫"验证通过.txt"/"真机测试记录.txt"就凭空派生
# execution 义务（真机 2026-08-29 复现：写文件成功却被要求"测试证据"）。
_QUOTED_SPAN_RE = re.compile(
    r'"[^"\n]{1,200}"|'
    r'"[^"\n]{1,200}"|'
    r"'[^'\n]{1,200}'|"
    r"「[^」\n]{1,200}」|"
    r"『[^』\n]{1,200}』"
)


def _strip_quoted_spans(text: Any) -> str:
    return _QUOTED_SPAN_RE.sub(" ", str(text or ""))


def _intent_compact(text: object) -> str:
    return re.sub(r"[\s\?\？\!\！\.\。\,\，\;\；\:\：]+", "", str(text or "").lower())


def is_deviation_signal(text: Any) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def has_execution_completion_claim(text: Any) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def is_execution_discussion_only(user_text: object) -> bool:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return False


def runtime_execution_floor(user_text: object) -> str:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return ACT_UNKNOWN


def request_target_bindings(user_text: Any) -> list[dict[str, str]]:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return []


def required_request_outputs(user_text: Any) -> list[str]:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return []


# 前端在用户消息后附加的呈现层契约横幅：是给模型的执行纪律说明，
# 不是用户意图。义务推导若把它算进意图，会从横幅的"执行/检查/复用"
# 等词凭空派生假义务（真机 2026-08-29：wordcount 任务被横幅派生出
# 两条"观察代码文件"义务，写码+跑测全过仍被判缺证据）。
_FRONTEND_CONTRACT_BANNER = "【连续执行契约】"


def build_action_obligations(user_text: Any) -> list[dict[str, Any]]:
    """Compatibility API: natural-language interpretation belongs to the model."""
    return []


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
    contract = _contract(payload)
    if contract.get("ok") is True and contract.get("write_effect") is True:
        for value in contract.get("paths") or []:
            if isinstance(value, str) and value.strip() and _normalize_path(value) not in seen:
                seen.add(_normalize_path(value))
                targets.append(value)
    evidence = contract.get("write_evidence")
    if isinstance(evidence, dict) and evidence.get("authoritative") is True:
        witnessed = []
        for key in ("changed_files", "deleted_files", "verified_unchanged_files", "post"):
            values = evidence.get(key)
            if isinstance(values, list):
                witnessed.extend(values)
        for item in witnessed:
            value = item.get("path") if isinstance(item, dict) else item
            if isinstance(value, str) and value.strip() and _normalize_path(value) not in seen:
                seen.add(_normalize_path(value))
                targets.append(value)
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
    if _file_list_mentions_target(payload, expected):
        return True
    return False


def _file_list_mentions_target(payload: dict[str, Any], expected: str) -> bool:
    action = str(payload.get("tool_action") or payload.get("action") or "").strip().lower()
    if action != "file.list" or not expected:
        return False
    result = _payload_result(payload)
    rows = result.get("entries")
    if not isinstance(rows, list):
        return False
    expected_name = expected.rsplit("/", 1)[-1]
    expected_parent = expected.rsplit("/", 1)[0] if "/" in expected else ""
    roots = [_normalize_path(value) for value in _payload_targets(payload)]
    result_root = _normalize_path(result.get("root"))
    if result_root:
        roots.append(result_root)
    parent_matches = not expected_parent or any(
        root == expected_parent or root.endswith("/" + expected_parent)
        for root in roots
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("path", "rel_path"):
            value = _normalize_path(row.get(key))
            if value == expected or value.endswith("/" + expected):
                return True
        if parent_matches and _normalize_path(row.get("name")) == expected_name:
            return True
    return False


def _contract(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_result_contract")
    return value if isinstance(value, dict) else {}


def execution_result_ok(payload: Any) -> bool:
    """Actual execution evidence, never an admission/registration ACK.

    Legacy observations retain their existing shape. Explicit receipt metadata
    and nested handler/process failures always take precedence over outer ok.
    Only known result-envelope fields are traversed; file content is not status.
    """
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return False
    if _execution_test_count(payload) == 0:
        return False
    pending = [payload]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if not isinstance(value, dict) or id(value) in seen:
            continue
        seen.add(id(value))
        if (str(value.get("receipt_role") or "").lower() in {"admission", "planning", "registration"}
                or value.get("plan_only") is True
                or value.get("stage") == "composition_parent_handoff"
                or value.get("ok") is False or value.get("success") is False):
            return False
        state = str(value.get("execution_state") or "").lower()
        if state and state not in {"completed", "succeeded"}:
            return False
        if str(value.get("commit_state") or "").lower() == "discarded":
            return False
        if str(value.get("status") or "").lower() in {
            "failed", "failed_final", "error", "timeout", "timed_out", "ambiguous", "cancelled",
        }:
            return False
        execution = value.get("execution")
        if isinstance(execution, dict):
            code = execution.get("returncode")
            if type(code) is not int or code != 0 or execution.get("ok") is not True:
                return False
        for key in ("tool_result", "tool_result_contract", "result", "execution"):
            nested = value.get(key)
            if isinstance(nested, dict):
                pending.append(nested)
    return True


def _is_test_script_path(value: Any) -> bool:
    return re.fullmatch(r"(?:test[^/]*|[^/]+_test)\.py", _normalize_path(value).rsplit("/", 1)[-1]) is not None


def _python_code_test_runner(code: Any) -> str | None:
    """Recognize invoked test APIs/argv in inline Python without executing it."""
    if not isinstance(code, str) or len(code) > 200000:
        return None
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return None
    if sum(1 for _ in ast.walk(tree)) > 20000:
        return None
    imports: dict[str, str] = {}
    values: dict[str, ast.AST] = {}
    instances: dict[str, str] = {}
    runners: set[str] = set()

    def name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return instances.get(node.id, imports.get(node.id, node.id))
        if isinstance(node, ast.Attribute):
            return name(node.value) + "." + node.attr
        if isinstance(node, ast.Call):
            return name(node.func) + "()"
        return ""

    def argv(node: ast.AST, depth: int = 0) -> list[str] | None:
        if depth > 12:
            return None
        if isinstance(node, ast.Name) and node.id in values:
            return argv(values[node.id], depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = argv(node.left, depth + 1), argv(node.right, depth + 1)
            return left + right if left is not None and right is not None else None
        if not isinstance(node, (ast.List, ast.Tuple)):
            return None
        return [part.value if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else "python" if name(part) == "sys.executable" else "" for part in node.elts]

    def inspect_call(node: ast.AST) -> None:
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            function = name(call.func)
            if function in {"unittest.main", "unittest.TestProgram", "unittest.TextTestRunner().run"}:
                runners.add("unittest")
            elif function == "pytest.main":
                runners.add("pytest")
            elif function in {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen"}:
                argument = call.args[0] if call.args else next((kw.value for kw in call.keywords if kw.arg == "args"), None)
                arguments = argv(argument) if argument is not None else None
                if arguments and len(arguments) >= 3:
                    executable = _normalize_path(arguments[0]).rsplit("/", 1)[-1]
                    if (re.fullmatch(r"(?:python(?:w|[0-9.]+)?|py)(?:\.exe)?", executable)
                            and arguments[1] == "-m" and arguments[2] in {"unittest", "pytest"}):
                        runners.add(arguments[2])

    def statements(items: list[ast.stmt]) -> None:
        for statement in items:
            if isinstance(statement, ast.Import):
                for entry in statement.names:
                    imports[entry.asname or entry.name] = entry.name
            elif isinstance(statement, ast.ImportFrom) and statement.module:
                for entry in statement.names:
                    imports[entry.asname or entry.name] = statement.module + "." + entry.name
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                if value is None:
                    continue
                inspect_call(value)
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        values[target.id] = value
                        if isinstance(value, ast.Call) and name(value.func) == "unittest.TextTestRunner":
                            instances[target.id] = "unittest.TextTestRunner()"
            elif isinstance(statement, ast.Expr):
                inspect_call(statement.value)
            elif (isinstance(statement, ast.If) and isinstance(statement.test, ast.Compare)
                    and isinstance(statement.test.left, ast.Name) and statement.test.left.id == "__name__"
                    and len(statement.test.ops) == 1 and isinstance(statement.test.ops[0], ast.Eq)
                    and len(statement.test.comparators) == 1
                    and isinstance(statement.test.comparators[0], ast.Constant)
                    and statement.test.comparators[0].value == "__main__"):
                statements(statement.body)
    statements(tree.body)
    return next(iter(runners)) if len(runners) == 1 else None


def _execution_test_runner(payload: dict[str, Any]) -> str | None:
    """Recognize actual executable/argv positions, never path or code text."""
    def argv_runner(argv: list[str]) -> str | None:
        if not argv or any(not isinstance(part, str) for part in argv):
            return None
        words = [part.strip("\"'") for part in argv]
        executable = _normalize_path(words[0]).rsplit("/", 1)[-1]
        if re.fullmatch(r"pytest(?:\.exe)?", executable):
            return "pytest"
        if not re.fullmatch(r"(?:python(?:w|[0-9.]+)?|py)(?:\.exe)?", executable):
            return None
        index = 1
        while index < len(words):
            argument = words[index]
            if argument == "-m":
                module = words[index + 1] if index + 1 < len(words) else ""
                return module if module in {"unittest", "pytest"} else None
            if argument in {"-c", "--", "-"} or not argument.startswith("-"):
                return None
            index += 2 if argument in {"-X", "-W"} else 1
        return None

    actual = _payload_result(payload).get("command")
    if isinstance(actual, list) and actual:
        if actual[0] == "__tiangong_windows_utf8_cmd_v1__" and len(actual) == 2:
            command = actual[1]
        else:
            runner = argv_runner(actual)
            if runner is not None:
                return runner
            if str(payload.get("tool_action") or payload.get("action") or "") == "python.run":
                return _python_code_test_runner(_payload_nested_args(payload).get("code"))
            return None
    else:
        # Legacy shell receipts retain their actual submitted command here.
        # Python argv are script arguments, not interpreter/module arguments.
        if str(payload.get("tool_action") or payload.get("action") or "") == "python.run":
            return _python_code_test_runner(_payload_nested_args(payload).get("code"))
        if str(payload.get("tool_action") or payload.get("action") or "") not in {"shell.run", "command.run", "run"}:
            return None
        command = _payload_nested_args(payload).get("command")
    if not isinstance(command, str):
        return None
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|\n")
        lexer.whitespace_split = True
        lexer.commenters = ""
        segments, current = [], []
        for token in lexer:
            if token and all(char in ";&|\n" for char in token):
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
    except ValueError:
        return None
    runners = {runner for segment in segments if (runner := argv_runner(segment)) is not None}
    return next(iter(runners)) if len(runners) == 1 else None


def _execution_test_count(payload: dict[str, Any]) -> int | None:
    """Strict framework summary from a real process result, not arbitrary text.

    None means this is not a recognized test process. Zero means its summary
    does not prove any passing tests (including failures hidden by shell echo).
    """
    result = _payload_result(payload)
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return None
    runner = _execution_test_runner(payload)
    output = str(execution.get("stderr") or "") + "\n" + str(execution.get("stdout") or "")
    summaries = list(re.finditer(r"(?m)^Ran (\d+) tests? in [^\r\n]+[\r\n]+\s*(OK|FAILED(?:\s*\([^\r\n]*\))?)\s*(?:\r?\n|$)", output))
    # The controlled Python profile executes an existing script directly and
    # has no shell command to identify `python -m unittest`. Use its sealed
    # target plus the actual framework summary, never a declaration field.
    tool_args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else {}
    native_test_script = (
        str(payload.get("tool_action") or payload.get("action") or "") == "python.run"
        and _is_test_script_path(tool_args.get("target"))
    )
    if runner == "unittest" or native_test_script:
        summary = summaries[-1] if summaries else None
        return int(summary.group(1)) if summary and summary.group(2) == "OK" else 0
    if runner == "pytest":
        summaries = re.findall(r"(?m)^=*[ \t]*(\d+ passed[^\r\n]*? in [0-9.]+s)[ \t=]*$", output)
        if not summaries or re.search(r"\b\d+ (?:failed|error|errors)\b", summaries[-1]):
            return 0
        return int(re.match(r"\d+", summaries[-1]).group())
    return None


def _payload_fact_kinds(payload: Any) -> set[str]:
    if not execution_result_ok(payload):
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
    if action_has_observation_semantics(action):
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
    if object_kind == "file" and action == "file.list":
        return _file_list_mentions_target(
            payload, _normalize_path(obligation.get("target_path"))
        )
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
    if not expected or not execution_result_ok(payload):
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
    if predicate == "tests_passed":
        count = _execution_test_count(payload)
        minimum = max(1, int(obligation.get("minimum_test_count") or 1))
        if count is not None:
            return count >= minimum
        result = _payload_result(payload)
        tests = result.get("test_results")
        return (str(payload.get("tool_action") or "") == "quality.run_tests"
                and isinstance(tests, dict) and type(tests.get("passed")) is int
                and tests["passed"] >= minimum and tests.get("failed") == 0)
    if predicate == "command_execution":
        action = str(payload.get("tool_action") or "")
        return bool(action.startswith(("quality.", "qc.")) or set(action.split(".")).intersection(
            {"run", "execute", "start", "compile", "build", "test", "syntax", "lint"}
        ))
    if predicate == "program_execution":
        execution = _payload_result(payload).get("execution")
        action = str(payload.get("tool_action") or "")
        return (action in {"python.run", "shell.run", "command.run", "run"}
                and isinstance(execution, dict) and type(execution.get("returncode")) is int
                and execution["returncode"] == 0 and execution.get("ok") is True
                and _execution_test_count(payload) is None)
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
    if (required_kind == "delivery" and obligation.get("delivery_mode") == "local_artifact"
            and "delivery" not in _payload_fact_kinds(payload)):
        # A successful local mutation must still carry artifact paths. The
        # final delivery gate reopens/checks those artifacts before completion.
        if not _contract(payload).get("paths"):
            return False
        required_kind = "effect"
    required_action = str(obligation.get("required_action") or "").strip().lower()
    actual_action = str(payload.get("tool_action") or payload.get("action") or "").strip().lower()
    if required_action and actual_action != required_action:
        return False
    if required_kind not in _payload_fact_kinds(payload):
        return False
    if obligation.get("target_state") == "present" and _target_was_removed(payload, obligation):
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


def _bind_submission_evidence(obligation: dict[str, Any], payload: dict[str, Any]) -> None:
    """Bind freshness to the actual submitted program and its explicit inputs.

    This is evidence bookkeeping, not source-code interpretation. Undeclared
    imports are not claimed to be covered by the argument dependency snapshot.
    """
    obligation["llm_submission_target"] = _payload_target(payload)
    if obligation.get("evidence_predicate") not in {"tests_passed", "program_execution", "command_execution"}:
        return
    dependencies = [str(value) for value in obligation.get("evidence_dependency_paths") or [] if value]
    if obligation["llm_submission_target"]:
        dependencies.append(obligation["llm_submission_target"])
    args = payload.get("tool_args") or {}
    for source in (args, _payload_nested_args(payload)):
        for key in ("argv", "arguments", "command", "cmd"):
            value = source.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str):
                    # argv elements are structured arguments; command text uses
                    # shell token syntax, never the deleted prose path classifier.
                    if isinstance(value, list):
                        tokens = [item]
                    else:
                        try:
                            tokens = shlex.split(item, posix=False)
                        except ValueError:
                            tokens = []
                    for token in tokens:
                        token = token.strip('"\'')
                        if token and not token.startswith("-") and ("/" in token or "\\" in token or Path(token).suffix):
                            dependencies.append(token)
    obligation["evidence_submission_paths"] = list(dict.fromkeys(dependencies))


def obligation_is_satisfied(obligation: dict[str, Any], quality_history: list[dict[str, Any]] | None) -> bool:
    if not bool(obligation.get("actionable", True)):
        return False
    history = [item for item in (quality_history or []) if isinstance(item, dict)]
    prior_kind = str(obligation.get("requires_prior_kind") or "").strip().lower()
    satisfied = False
    evidence_obligation = dict(obligation)
    for index, payload in enumerate(history):
        if not _successful_fact(payload, obligation):
            if satisfied and _obligation_evidence_invalidated(payload, evidence_obligation):
                satisfied = False
            continue
        if not prior_kind:
            satisfied = True
            _bind_submission_evidence(evidence_obligation, payload)
            continue
        prior_obligation = {
            "kind": prior_kind,
            "target_path": obligation.get("target_path"),
            "actionable": True,
        }
        if any(_successful_fact(prior, prior_obligation) for prior in history[:index]):
            satisfied = True
            _bind_submission_evidence(evidence_obligation, payload)
    return satisfied


def _obligation_evidence_invalidated(payload: dict[str, Any], obligation: dict[str, Any]) -> bool:
    """Do not reuse a prior target observation after mutation or contradiction."""
    if obligation.get("evidence_predicate") in {"tests_passed", "program_execution", "command_execution"}:
        count = _execution_test_count(payload)
        if obligation.get("evidence_predicate") == "tests_passed" and count is not None and (not execution_result_ok(payload) or count < int(obligation.get("minimum_test_count") or 1)):
            return True
        if (obligation.get("evidence_predicate") in {"program_execution", "command_execution"}
                and count is None and isinstance(_payload_result(payload).get("execution"), dict)
                and not execution_result_ok(payload)):
            # A failing helper/test is not a contradiction of an earlier,
            # different program execution. File mutations below still revoke
            # evidence when an actual dependency changed.
            prior_target = _normalize_path(obligation.get("llm_submission_target"))
            attempt_target = _normalize_path(_payload_target(payload))
            if not prior_target or not attempt_target or prior_target == attempt_target:
                return True
        dependencies = [*list(obligation.get("evidence_dependency_paths") or []),
                        *list(obligation.get("evidence_submission_paths") or [])]
        if obligation.get("llm_submission_target"):
            dependencies.append(obligation["llm_submission_target"])
        evidence = _contract(payload).get("write_evidence")
        if isinstance(evidence, dict) and evidence.get("authoritative") is True:
            changed = [*list(evidence.get("changed_files") or []), *list(evidence.get("deleted_files") or [])]
        elif _contract(payload).get("observed_write_effect"):
            changed = _payload_targets(payload)
        else:
            changed = []
        for path in changed:
            value = path.get("path") if isinstance(path, dict) else path
            actual = _normalize_path(value)
            if any(actual == _normalize_path(target) or actual.endswith("/" + _normalize_path(target)) for target in dependencies):
                return True
    if not obligation.get("target_path") or not _target_matches(payload, obligation):
        return False
    if obligation.get("target_state") == "present" and _target_was_removed(payload, obligation):
        return True
    kind = str(obligation.get("kind") or "")
    contract = _contract(payload)
    action = str(payload.get("tool_action") or payload.get("action") or "")
    evidence = contract.get("write_evidence")
    mutated = bool(contract.get("observed_write_effect")) or (
        isinstance(evidence, dict) and evidence.get("authoritative") is True
        and bool(evidence.get("changed_files") or evidence.get("deleted_files"))
    )
    if kind in {"observation", "execution"} and mutated:
        return True
    if not execution_result_ok(payload) and _action_fact_kind(action) == kind:
        return True
    return bool(not execution_result_ok(payload) and contract.get("may_mutate") is True)


def _target_was_removed(payload: dict[str, Any], obligation: dict[str, Any]) -> bool:
    evidence = _contract(payload).get("write_evidence")
    if not isinstance(evidence, dict) or evidence.get("authoritative") is not True:
        return False
    removed = list(evidence.get("deleted_files") or [])
    removed.extend(row.get("path") for row in evidence.get("post") or []
                   if isinstance(row, dict) and row.get("exists") is False)
    expected = _normalize_path(obligation.get("target_path"))
    return bool(expected and any(_normalize_path(path) == expected or _normalize_path(path).endswith("/" + expected)
                                 for path in removed if isinstance(path, str)))


def reconcile_completion_evidence(contract: Any, obligations: Any, history: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Produce the same current evidence view for Life and the Runtime gate.

    This reads existing observations; it neither executes a tool nor changes
    the user's requirement set. Historical satisfied flags are not authority.
    """
    current = [dict(item) for item in obligations or [] if isinstance(item, dict)]
    observations = [item for item in history or [] if isinstance(item, dict)]
    for item in current:
        if item.get("actionable", True):
            satisfied = obligation_is_satisfied(item, observations)
            item["status"] = "satisfied" if satisfied else "pending"
            item["evidence_ok"] = satisfied
    updated = dict(contract) if isinstance(contract, dict) else {}
    if updated and isinstance(obligations, list):
        updated["desired_facts"] = _sync_goal_facts_from_obligations(updated, current)
        updated["completion_evidence"] = {
            "schema": "tiangong.v3.completion-evidence.v1",
            "required": len(current),
            "satisfied": sum(item.get("status") == "satisfied" for item in current),
            "requirements_sha256": hashlib.sha256(_canonical_json([
                {key: item.get(key) for key in ("id", "kind", "target_path", "evidence_predicate", "status")}
                for item in current
            ]).encode("utf-8")).hexdigest(),
        }
        _refresh_task_contract_hash(updated)
    return updated, current


def completion_progress_fingerprint(run_state: Any) -> str:
    """Content/condition progress for loop control, never proof of completion."""
    if not isinstance(run_state, dict):
        return ""
    evidence = set()
    for payload in run_state.get("observations") or []:
        if not execution_result_ok(payload):
            continue
        contract = _contract(payload)
        write = contract.get("write_evidence") or {}
        result = _payload_result(payload)
        digest = {
            "action": payload.get("tool_action"), "targets": _payload_targets(payload),
            "post": write.get("post") if isinstance(write, dict) else None,
            "content": result.get("content"), "sha256": result.get("sha256"),
            "stdout": (result.get("execution") or {}).get("stdout") if isinstance(result.get("execution"), dict) else result.get("stdout"),
        }
        evidence.add(_canonical_json(digest))
    value = {"evidence": sorted(evidence), "obligations": [
        (item.get("id"), item.get("status")) for item in run_state.get("obligations") or [] if isinstance(item, dict)
    ]}
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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
        if not isinstance(obligation, dict):
            continue
        if obligation.get("status") == "satisfied":
            if not _obligation_evidence_invalidated(payload, obligation):
                continue
            obligation["status"] = "pending"
            obligation["evidence_ok"] = False
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
            _bind_submission_evidence(obligation, payload)
            obligation["observed_fact_kinds"] = fact_kinds
            obligation["evidence_ok"] = True
            obligation["evidence_round"] = int(run_state.get("round") or 0)
        elif action:
            obligation["last_attempt_action"] = action
            obligation["last_attempt_target"] = target
            obligation["last_attempt_fact_kinds"] = fact_kinds
            obligation["last_attempt_ok"] = bool(payload.get("ok"))


def action_has_observation_semantics(action: Any) -> bool:
    """Classify an action only; this never authorizes it or proves a read."""
    name = str(action or "").strip().lower()
    tokens = set(part for part in re.split(r"[._-]+", name) if part)
    return bool(name in {"file.list", "file.read", "code.read", "sheet.read", "pdf.extract_text"} or tokens.intersection(
        {"read", "list", "inspect", "search", "query", "find", "info", "browse", "scan", "open", "health", "status", "get", "show", "describe"}
    ))


def _action_fact_kind(action: Any) -> str:
    name = str(action or "").strip().lower()
    tokens = set(part for part in re.split(r"[._-]+", name) if part)
    if action_has_observation_semantics(name):
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
        for key in ("requirement_version", "minimum_test_count"):
            if type(fact.get(key)) is int:
                obligation[key] = fact[key]
        for key in ("target_state", "evidence_dependency_paths"):
            if key in fact:
                obligation[key] = fact[key]
        obligations.append(obligation)
    return obligations


def merge_action_obligations(*groups: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for group in groups:
        for raw in group if isinstance(group, list) else []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            key = (
                str(item.get("kind") or "").strip().lower(),
                _normalize_path(item.get("target_path")),
                str(item.get("required_action") or "").strip().lower(),
                str(item.get("evidence_predicate") or "").strip().lower(),
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
        # 权威写入证据内嵌独立校验（codex 的路径/后缀/内容匹配检查、
        # sandbox broker 的增量对账）＝与写入动作相互独立的第二个事实
        # 信号：写入被观测 + 写入被核验。避免"写完还必须再读一次"对
        # 简单文件创建的一刀切（真机 2026-08-29：1/2 卡死三轮）。
        try:
            verified_contract = _contract(payload)
            verified_evidence = verified_contract.get("write_evidence")
            if (
                verified_contract.get("observed_write_effect") is True
                and isinstance(verified_evidence, dict)
                and verified_evidence.get("authoritative") is True
            ):
                verified_signal = f"write_verified:{int(round_number or 0)}"
                if verified_signal not in signals:
                    signals.append(verified_signal)
        except Exception:
            pass
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
    required_stability = 0
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


def _local_artifact_delivery(text: str) -> bool:
    """Retired prose classifier; the model selects deliveries."""
    return False
