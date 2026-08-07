from __future__ import annotations

import pytest

from life_service.learning_workflow import (
    build_draft,
    confirm_draft,
    discard_draft,
    publish_draft,
)


def _draft(decision: dict[str, object] | None = None) -> dict[str, object]:
    return build_draft(
        life_id="life_wf",
        scope={"scope_sha256": "abc"},
        decision=decision or {
            "target": "skill",
            "title": "Research skill",
            "summary": "Research then retain the result.",
            "risk_level": "A3",
            "draft_artifact": {"content": "# Research skill"},
        },
        source="autonomous",
    )


def test_skill_draft_waits_for_user_and_confirm_publishes_same_authority():
    draft = _draft()
    assert draft["status"] == "awaiting_user"
    assert draft["requires_confirmation"] is True
    assert draft["can_confirm_learning"] is True
    assert draft["can_discard_learning"] is True

    approved = confirm_draft(draft, draft_sha256=draft["draft_sha256"])
    assert approved["status"] == "approved"
    assert approved["can_confirm_learning"] is False
    assert approved["can_discard_learning"] is False

    published, artifact = publish_draft(approved, capabilities={})
    assert published["status"] == "published"
    assert published["registered"] is True
    assert artifact is not None
    assert artifact["kind"] == "skill"


def test_already_published_or_discarded_drafts_are_terminal():
    draft = _draft()
    approved = confirm_draft(draft, draft_sha256=draft["draft_sha256"])
    published, _artifact = publish_draft(approved, capabilities={})
    with pytest.raises(ValueError, match="not approved"):
        publish_draft(published, capabilities={})
    with pytest.raises(ValueError, match="terminal"):
        discard_draft(published)

    discarded = discard_draft(draft)
    assert discarded["status"] == "discarded"
    with pytest.raises(ValueError, match="terminal"):
        discard_draft(discarded)
