"""Authoritative learning draft and publication state machine.

LLM output is treated as a proposal.  The model chooses *how* to learn from
the supplied activity scope, while this module enforces the user's publication
contract: A0--A2 knowledge may commit automatically; every Skill/Tool is at
least A3 and remains an unregistered preview until user confirmation.  A
direct user request bypasses the card but is still recorded and auditable.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from contracts import canonical_sha256
from .complete_core import utc_now


LEARNING_WORKFLOW_SCHEMA = "tiangong.life.learning-workflow.v1"
_TARGETS = {"knowledge", "skill", "tool"}


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
        "governance_note": "Preview only; no Skill or Tool is registered before user confirmation." if needs_confirmation else "Approved direct/low-risk learning is ready for publication.",
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
        "governance_note": "User confirmed the preview; publication may now write the artifact.",
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
    target = _target(value.get("target"))
    artifact: dict[str, Any] | None = None
    if target in {"skill", "tool"}:
        artifact_id = "cap_" + canonical_sha256({
            "domain": "tiangong.life.learning-artifact.v1",
            "life_id": value.get("life_id"), "fingerprint": value.get("fingerprint"), "target": target,
        })[:40]
        previous_id = str(value.get("update_of") or "")
        previous = capabilities.get(previous_id) if previous_id else None
        artifact = {
            "schema": "tiangong.life.capability.v1",
            "artifact_id": artifact_id,
            "kind": target,
            "title": value.get("title"),
            "summary": value.get("summary"),
            "content": deepcopy(value.get("draft_artifact") or {}),
            "status": "published",
            "version": int((previous or {}).get("version") or 0) + 1,
            "previous_artifact_id": previous_id,
            "rollback_available": bool(previous_id and isinstance(previous, Mapping)),
            "published_at": utc_now(),
            "learning_id": value.get("learning_id"),
        }
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
