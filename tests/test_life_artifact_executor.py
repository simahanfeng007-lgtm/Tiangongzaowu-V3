from __future__ import annotations

import pytest

from life_service.artifact_executor import (
    ArtifactExecutorError,
    compile_artifact,
    persist_artifact_bundle,
    persist_current_pointer,
    publish_artifact,
    rollback_pointer,
)


def _learning(kind: str = "skill") -> dict[str, object]:
    return {
        "life_id": "life_executor", "learning_id": "learn_executor", "target": kind,
        "title": "Research digest", "summary": "Research then retain the result.", "risk_level": "A3",
        "draft_artifact": {
            "content": "# Research digest", "required_actions": ["web.search"],
            "steps": [{"step_id": "search", "action_id": "web.search", "arguments_template": {"query": "{{input.topic}}"}}],
        },
    }


def test_skill_compilation_binds_only_available_existing_actions_and_promotes_risk():
    artifact = compile_artifact(_learning(), action_catalog=[{"action_id": "web.search", "risk": "A4", "available": True}])
    assert artifact["kind"] == "skill"
    assert artifact["risk_level"] == "A4"
    assert artifact["required_actions"] == ["web.search"]
    assert publish_artifact(artifact)["status"] == "published"


def test_skill_compilation_rejects_unknown_or_unavailable_actions():
    with pytest.raises(ArtifactExecutorError, match="unknown:web.search"):
        compile_artifact(_learning(), action_catalog=[])
    with pytest.raises(ArtifactExecutorError, match="unavailable:web.search"):
        compile_artifact(_learning(), action_catalog=[{"action_id": "web.search", "risk": "A3", "available": False}])


def test_knowledge_has_no_tool_binding_and_rollback_is_a_pointer(tmp_path):
    knowledge = _learning("knowledge")
    knowledge["risk_level"] = "A0"
    knowledge["draft_artifact"] = {"content": "# Persisted knowledge"}
    previous = publish_artifact(compile_artifact(knowledge))
    newer = publish_artifact(compile_artifact(knowledge, previous_artifact=previous))
    pointer = rollback_pointer(newer, previous)
    directory = persist_artifact_bundle(tmp_path / "artifacts", previous)
    persist_artifact_bundle(tmp_path / "artifacts", previous, publication={"publisher": "knowledge_store"})
    current = persist_current_pointer(tmp_path / "artifacts", life_id="life_executor", lineage_id=previous["lineage_id"], pointer=pointer)
    assert previous["required_actions"] == []
    assert pointer["to_artifact_id"] == previous["artifact_id"]
    assert (directory / "artifact.json").is_file()
    assert (directory / "knowledge.md").is_file()
    assert (directory / "publication.json").is_file()
    assert current.is_file()


def test_bundle_persistence_keeps_atomic_staging_below_windows_path_limit(tmp_path):
    knowledge = publish_artifact(compile_artifact({
        **_learning("knowledge"),
        "risk_level": "A0",
        "draft_artifact": {"content": "# Deep path knowledge"},
    }))
    base_target = tmp_path / "artifacts" / knowledge["life_id"] / knowledge["artifact_id"] / "artifact.json"
    padding = "d" * max(0, 225 - len(str(base_target)))

    directory = persist_artifact_bundle(tmp_path / padding / "artifacts", knowledge)

    assert (directory / "artifact.json").is_file()
    assert (directory / "knowledge.md").is_file()
