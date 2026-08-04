"""L0 projection helpers for learned v3 abilities.

The ability registry is a learned-capability ledger first.  A learned ability
may be visible as a Skill/Capability, but it is not a model-callable Tool until
an explicit tool release record exists.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


REGISTRY_SCHEMA = "tiangong.v3.ability_registry.v2"
L0_ABILITY_SCHEMA = "tiangong.v3.l0_ability_projection.v1"

_HASH_NAMESPACE = "tiangong.v3.l0.ability"
_RELEASE_STATES = {
    "not_requested",
    "requested",
    "review_required",
    "validated",
    "released",
    "revoked",
    "blocked",
}
_RISK_LEVELS = {
    "A0": "a0_safe",
    "A1": "a1_low",
    "A2": "a2_normal",
    "A3": "a3_elevated",
    "A4": "a4_review_required",
    "A5": "a5_critical",
}


def read_json_compat(path: Path, default: Any | None = None) -> Any:
    """Read JSON files produced by Windows tools, including optional UTF-8 BOM."""
    try:
        if not path.exists():
            return {} if default is None else default
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {} if default is None else default


def registry_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        rows = raw.get("nengli_liebiao") or raw.get("nengli_list") or raw.get("abilities") or raw.get("items") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    return [item for item in rows if isinstance(item, dict)]


def stable_l0_ref(prefix: str, *parts: Any) -> str:
    payload = json.dumps([_HASH_NAMESPACE, prefix, *parts], ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def ability_status(ability: dict[str, Any]) -> str:
    raw = str(
        ability.get("status")
        or ability.get("zhuangtai")
        or ability.get("promotion_stage")
        or ""
    ).strip().lower()
    return {
        "jihuo": "active",
        "active": "active",
        "daijihuo": "review_ready",
        "review_ready": "review_ready",
        "candidate": "candidate",
        "draft": "draft",
        "disabled": "disabled",
        "tingyong": "disabled",
        "revoked": "revoked",
    }.get(raw, raw or "unknown")


def learning_usable(ability: dict[str, Any]) -> bool:
    return (
        ability_status(ability) == "active"
        and ability.get("candidate_only") is not True
        and ability.get("review_required") is not True
        and ability.get("activation_allowed") is not False
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _release_state(ability: dict[str, Any], l0: dict[str, Any]) -> str:
    raw = str(
        ability.get("tool_release_state")
        or ability.get("releaseState")
        or l0.get("tool_release_state")
        or ""
    ).strip().lower()
    if raw in _RELEASE_STATES:
        return raw
    if ability.get("tool_callable") is True and ability.get("registers_tool") is True:
        return "released"
    if ability.get("review_required") is True:
        return "review_required"
    return "not_requested"


def _has_explicit_tool_release(ability: dict[str, Any], l0: dict[str, Any]) -> bool:
    raw_state = str(
        ability.get("tool_release_state")
        or ability.get("releaseState")
        or l0.get("tool_release_state")
        or ""
    ).strip().lower()
    if raw_state in {"requested", "review_required", "validated", "released", "revoked", "blocked"}:
        return True
    if ability.get("tool_ref") or l0.get("tool_ref"):
        return True
    if ability.get("tool_callable") is True or ability.get("registers_tool") is True:
        return True
    return False


def _legacy_learning_source(ability: dict[str, Any]) -> bool:
    source = str(ability.get("laiyuan") or ability.get("source") or "").strip().lower()
    schema = str(ability.get("schema") or "").strip().lower()
    return bool(
        source in {"zizhu_xuexi", "autonomous_learning", "learning_registry"}
        or "learning_ability" in schema
        or ability.get("laiyuan_card_id")
        or ability.get("learned_by")
        or ability.get("auto_drafted") is True
    )


def _legacy_auto_release_allowed(
    ability: dict[str, Any],
    l0: dict[str, Any],
    *,
    status: str,
    risk_label: str,
    release_state: str,
) -> bool:
    """Expose pre-release-format active learning abilities as read-only tools.

    Older learned ability rows were created before the tool-release fields
    existed.  This does not mutate those rows; it only gives them the same
    model-visible read-only wrapper during projection.
    """
    reviewed_high_risk = False
    if risk_label in {"A3", "A4"}:
        review = ability.get("model_review") if isinstance(ability.get("model_review"), dict) else {}
        decision = str(review.get("decision") or review.get("raw_decision") or "").strip().lower()
        reviewer = str(review.get("reviewer") or "").strip().lower()
        reviewed_high_risk = bool(
            reviewer == "llm"
            and decision in {"learn", "approve", "approved", "accept", "accepted"}
            and (
                ability.get("confirmed_by")
                or ability.get("learned_by")
                or ability.get("activated_by")
                or ability.get("released_by")
            )
        )
    return bool(
        release_state == "not_requested"
        and not _has_explicit_tool_release(ability, l0)
        and status == "active"
        and (risk_label in {"A0", "A1", "A2"} or reviewed_high_risk)
        and ability.get("candidate_only") is not True
        and ability.get("review_required") is not True
        and _legacy_learning_source(ability)
    )


def build_l0_projection(ability: dict[str, Any]) -> dict[str, Any]:
    ability_id = str(ability.get("id") or ability.get("ability_id") or "unknown").strip() or "unknown"
    existing = ability.get("l0") if isinstance(ability.get("l0"), dict) else {}
    status = ability_status(ability)
    release_state = _release_state(ability, existing)
    risk_label = str(ability.get("risk_level") or ability.get("riskLevel") or ability.get("fengxian_dengji") or "A3").upper()
    compat_auto_release = _legacy_auto_release_allowed(
        ability,
        existing,
        status=status,
        risk_label=risk_label,
        release_state=release_state,
    )
    if compat_auto_release:
        release_state = "released"
    skill_ref = str(existing.get("skill_ref") or ability.get("skill_ref") or stable_l0_ref("skill", ability_id))
    capability_ref = str(existing.get("capability_ref") or ability.get("capability_ref") or stable_l0_ref("capability", ability_id))
    learning_ref = str(
        existing.get("learning_ref")
        or ability.get("learning_ref")
        or stable_l0_ref("learning", ability.get("laiyuan_card_id") or ability.get("laiyuan_jingyan_id") or ability_id)
    )
    tool_ref = existing.get("tool_ref") or ability.get("tool_ref")
    if release_state == "released" and not tool_ref:
        tool_ref = stable_l0_ref("tool", ability_id)
    if release_state != "released":
        tool_ref = None

    validation_refs = _as_list(existing.get("validation_refs") or ability.get("validation_refs"))
    if not validation_refs and isinstance(ability.get("model_review"), dict):
        validation_refs.append(stable_l0_ref("validation", ability_id, "model_review"))

    evidence_refs = _as_list(existing.get("evidence_refs") or ability.get("evidence_refs"))
    if ability.get("laiyuan_card_id"):
        evidence_refs.append(stable_l0_ref("evidence", "learning_card", ability.get("laiyuan_card_id")))
    if ability.get("laiyuan_jingyan_id"):
        evidence_refs.append(stable_l0_ref("evidence", "experience", ability.get("laiyuan_jingyan_id")))

    model_visible_skill = learning_usable({**ability, "l0": existing})
    tool_callable = ability.get("tool_callable") is True or compat_auto_release
    registers_tool = ability.get("registers_tool") is True or compat_auto_release
    model_visible_tool = bool(
        release_state == "released"
        and tool_ref
        and tool_callable
        and registers_tool
        and model_visible_skill
    )

    return {
        "schema": L0_ABILITY_SCHEMA,
        "learning_ref": learning_ref,
        "learning_state": "active" if status == "active" else ("assessing" if status in {"draft", "review_ready"} else "proposed"),
        "skill_ref": skill_ref,
        "skill_state": "active" if status == "active" else ("registered" if status in {"draft", "review_ready"} else "discovered"),
        "capability_ref": capability_ref,
        "capability_state": "active" if status == "active" else ("registered" if status in {"draft", "review_ready"} else "discovered"),
        "tool_ref": tool_ref,
        "tool_state": "available" if model_visible_tool else "unknown",
        "tool_release_state": release_state,
        "compat_auto_release": compat_auto_release,
        "model_visible_skill": model_visible_skill,
        "model_visible_tool": model_visible_tool,
        "risk_view": {
            "risk_ref": existing.get("risk_ref") or stable_l0_ref("risk", ability_id, risk_label),
            "level": _RISK_LEVELS.get(risk_label, "unknown"),
            "subject_ref": capability_ref,
        },
        "policy_refs": _as_list(existing.get("policy_refs") or ability.get("policy_refs"))
        or [stable_l0_ref("policy", ability_id, "legacy_active_learning_compat_projection" if compat_auto_release else "no_auto_tool_release")],
        "validation_refs": list(dict.fromkeys(str(item) for item in validation_refs if item)),
        "verification_refs": _as_list(existing.get("verification_refs") or ability.get("verification_refs")),
        "evidence_refs": list(dict.fromkeys(str(item) for item in evidence_refs if item)),
        "decision_ref": existing.get("decision_ref") or ability.get("decision_ref"),
        "trace_refs": _as_list(existing.get("trace_refs") or ability.get("trace_refs")),
    }


def with_l0_projection(ability: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ability, dict):
        return {}
    projected = dict(ability)
    l0 = build_l0_projection(projected)
    projected["l0"] = l0
    projected["learning_ref"] = l0["learning_ref"]
    projected["skill_ref"] = l0["skill_ref"]
    projected["capability_ref"] = l0["capability_ref"]
    projected["tool_ref"] = l0["tool_ref"]
    projected["tool_release_state"] = l0["tool_release_state"]
    projected["model_visible_skill"] = l0["model_visible_skill"]
    projected["model_visible_tool"] = l0["model_visible_tool"]
    if l0.get("compat_auto_release"):
        ability_id = str(projected.get("id") or projected.get("ability_id") or "unknown").strip() or "unknown"
        tool_name = str(projected.get("tool_name") or f"skill_{ability_id}").strip()
        projected["tool_callable"] = True
        projected["registers_tool"] = True
        projected["tool_name"] = tool_name
        projected["tool_names"] = [tool_name] if tool_name else []
        projected.setdefault("tool_kind", "learned_skill")
        projected.setdefault("tool_effect", "read")
        projected.setdefault("tool_risk_level", str(projected.get("risk_level") or projected.get("riskLevel") or "A2").upper())
        projected.setdefault("tool_release_policy", "legacy_active_learning_compat_projection")
    return projected


def tool_released(ability: dict[str, Any]) -> bool:
    if not isinstance(ability, dict):
        return False
    projected = with_l0_projection(ability)
    l0 = projected.get("l0") if isinstance(projected.get("l0"), dict) else {}
    return bool(
        learning_usable(projected)
        and l0.get("tool_release_state") == "released"
        and l0.get("tool_ref")
        and projected.get("tool_callable") is True
        and projected.get("registers_tool") is True
    )


def release_block_reasons(ability: dict[str, Any]) -> list[str]:
    projected = with_l0_projection(ability)
    l0 = projected.get("l0") if isinstance(projected.get("l0"), dict) else {}
    reasons: list[str] = []
    if not learning_usable(projected):
        reasons.append("learning_not_active")
    if l0.get("tool_release_state") != "released":
        reasons.append("tool_not_released")
    if not l0.get("tool_ref"):
        reasons.append("missing_tool_ref")
    if projected.get("tool_callable") is not True:
        reasons.append("tool_callable_false")
    if projected.get("registers_tool") is not True:
        reasons.append("registers_tool_false")
    return reasons
