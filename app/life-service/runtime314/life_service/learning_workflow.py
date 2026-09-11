"""Learning drafts and the P10 old-publication freeze.

Knowledge retains its existing consent/risk path. Skill/Tool proposals retain
source material, but user confirmation is not permission to publish a complete
legacy capability. P8/P9 source review remains a separate authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from contracts import canonical_sha256
from .complete_core import utc_now


LEARNING_WORKFLOW_SCHEMA = "tiangong.life.learning-workflow.v1"
_TARGETS = {"knowledge", "skill", "tool"}
LEGACY_PUBLICATION_FROZEN = "life.learning.legacy_publication_frozen"
MIGRATION_REQUIRED = "migration_required"


def legacy_publication_blocked(record: Mapping[str, Any]) -> bool:
    """Only explicit Knowledge may use the old learning publication sink.

    Inspect structural artifact metadata, not natural-language content. A model
    cannot relabel a Skill payload as Knowledge to obtain old publication rights.
    This does not classify ordinary document/file writes or P8/P9 source data.
    """
    if not isinstance(record, Mapping):
        return True
    declared = []
    for key in ("target", "kind", "artifact_kind"):
        value = record.get(key)
        if value:
            text = str(value).strip().casefold().replace("_", "-")
            if text.startswith("learning-"):
                text = text[len("learning-"):]
            try:
                declared.append(_target(text))
            except ValueError:
                return True
    if not declared or any(kind != "knowledge" for kind in declared):
        return True
    # Explicit executable fields remain forbidden even when kind is relabelled.
    if record.get("skill_spec") is not None:
        return True
    for key in ("required_actions", "action_bindings", "registers_tool", "tool_callable"):
        if record.get(key):
            return True
    execution = record.get("execution")
    artifact = execution.get("artifact") if isinstance(execution, Mapping) else None
    return isinstance(artifact, Mapping) and legacy_publication_blocked(artifact)


def frozen_publication_result() -> dict[str, Any]:
    return {"ok": False, "status": MIGRATION_REQUIRED,
            "reason_code": LEGACY_PUBLICATION_FROZEN, "error_code": LEGACY_PUBLICATION_FROZEN,
            "publication_frozen": True, "registered": False, "retryable": False,
            "may_publish": False, "may_authorize": False, "may_execute": False}


def freeze_learning_publication(record: Mapping[str, Any]) -> dict[str, Any]:
    """Retain the draft/evidence, not an approval or a new capability artifact."""
    value = deepcopy(dict(record))
    if not legacy_publication_blocked(value) or value.get("status") in {"published", "discarded"}:
        return value
    if value.get("status") == MIGRATION_REQUIRED and value.get("publication_frozen") is True:
        return value
    value["publication_freeze"] = {"reason_code": LEGACY_PUBLICATION_FROZEN,
        "prior_status": value.get("status"), "policy": "p10-r1"}
    value.update(status=MIGRATION_REQUIRED, registered=False, publication_frozen=True,
                 retryable=False, can_confirm_learning=False, can_discard_learning=True,
                 may_publish=False, may_authorize=False, may_execute=False,
                 governance_note="旧式完整 Skill/Tool 发布已冻结；草稿与证据保留，等待源码演化迁移。")
    return value


def _risk(value: Any) -> int:
    text = str(value or "A0").strip().upper()
    if len(text) == 2 and text[0] == "A" and text[1].isdigit():
        return max(0, min(5, int(text[1])))
    return 0


def _target(value: Any) -> str:
    text = str(value or "knowledge").strip().casefold().replace("_", "-")
    aliases = {"knowledge-base": "knowledge", "knowledgebase": "knowledge", "kb": "knowledge", "capability": "skill"}
    text = aliases.get(text, text)
    if text not in _TARGETS:
        raise ValueError("learning target is unsupported")
    return text


def _text(value: Any, fallback: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    return (text or fallback)[:limit]


def build_draft(*, life_id: str, scope: Mapping[str, Any], decision: Mapping[str, Any], source: str = "autonomous") -> dict[str, Any]:
    """Normalize one LLM learning decision without registering an artifact."""
    target = _target(decision.get("target") or decision.get("artifact_kind"))
    direct = str(source) == "user_direct"
    risk = _risk(decision.get("risk_level") or decision.get("risk"))
    if target in {"skill", "tool"}:
        risk = max(3, risk)
    requested = _text(decision.get("request") or decision.get("topic"), "life learning")
    fingerprint = canonical_sha256({
        "domain": "tiangong.life.learning-fingerprint.v1",
        "life_id": life_id,
        "source": str(source),
        "target": target,
        "request": requested.casefold(),
    })
    needs_confirmation = not direct and (risk >= 3 or target in {"skill", "tool"})
    status = "awaiting_user" if needs_confirmation else "approved"
    draft = {
        "schema": LEARNING_WORKFLOW_SCHEMA,
        "learning_id": "learn_" + fingerprint[:40],
        "life_id": life_id,
        "fingerprint": fingerprint,
        "scope_sha256": str(scope.get("scope_sha256") or ""),
        "source": str(source),
        "status": status,
        "risk_level": f"A{risk}",
        "target": target,
        "kind": f"learning_{target}",
        "title": _text(decision.get("title"), requested, 180),
        "summary": _text(decision.get("summary") or decision.get("reason"), "LLM selected this learning path from the current activity scope."),
        "learning_plan": deepcopy(decision.get("learning_plan") or decision.get("plan") or []),
        "draft_artifact": deepcopy(decision.get("draft_artifact") or decision.get("artifact") or {}),
        "update_of": _text(decision.get("update_of"), "", 160),
        "requires_confirmation": needs_confirmation,
        "can_confirm_learning": needs_confirmation,
        "can_discard_learning": status == "awaiting_user",
        "registered": False,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "governance_note": "当前仅为预览；在用户确认前不会注册任何技能或工具。" if needs_confirmation else "已批准的直通/低风险学习已具备发布条件。",
    }
    draft["draft_sha256"] = canonical_sha256({"domain": "tiangong.life.learning-draft.v1", "draft": draft})
    return draft


def confirm_draft(record: Mapping[str, Any], *, draft_sha256: str = "") -> dict[str, Any]:
    value = deepcopy(dict(record))
    if str(value.get("status") or "") != "awaiting_user" or not value.get("requires_confirmation"):
        raise ValueError("learning draft is not awaiting confirmation")
    expected = str(value.get("draft_sha256") or "")
    if draft_sha256 and draft_sha256 != expected:
        raise ValueError("learning draft has changed")
    value.update({
        "status": "approved",
        "can_confirm_learning": False,
        "can_discard_learning": False,
        "approved_at": utc_now(),
        "updated_at": utc_now(),
        "governance_note": "用户已确认预览；能力发布仍受独立发布边界约束。",
    })
    return value


def discard_draft(record: Mapping[str, Any], *, reason: str = "user_declined") -> dict[str, Any]:
    value = deepcopy(dict(record))
    if str(value.get("status") or "") in {"published", "discarded"}:
        raise ValueError("learning draft is terminal")
    value.update({
        "status": "discarded",
        "discard_reason": _text(reason, "user_declined", 400),
        "discarded_at": utc_now(),
        "updated_at": utc_now(),
        "can_confirm_learning": False,
        "can_discard_learning": False,
        "registered": False,
    })
    return value


def publish_draft(record: Mapping[str, Any], *, capabilities: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    value = deepcopy(dict(record))
    if str(value.get("status") or "") != "approved":
        raise ValueError("learning draft is not approved")
    if legacy_publication_blocked(value):
        return freeze_learning_publication(value), None
    artifact: dict[str, Any] | None = None
    value.update({
        "status": "published",
        "registered": True,
        "artifact_id": "" if artifact is None else artifact["artifact_id"],
        "published_at": utc_now(),
        "updated_at": utc_now(),
        "can_confirm_learning": False,
        "can_discard_learning": False,
    })
    return value, artifact


__all__ = ["LEARNING_WORKFLOW_SCHEMA", "build_draft", "confirm_draft", "discard_draft", "publish_draft"]
