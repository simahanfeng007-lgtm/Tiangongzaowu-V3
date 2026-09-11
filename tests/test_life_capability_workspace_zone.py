from __future__ import annotations

import json
from contracts import canonical_sha256


def _seed_mapping(root, artifact):
    """A file from a pre-P10 installation, never runtime publication."""
    kind=artifact['kind']; zone='skills/life' if kind=='skill' else 'tools/life'
    target=root/zone/(artifact['skill_spec']['skill_id']+'.md')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(artifact['document']['content'],encoding='utf-8')
    return target


from life_service.embedded_runtime import EmbeddedLifeRuntime
from total_gateway.runtime import (
    life_capability_workspace_mapper,
    life_capability_workspace_remover,
)


def _artifact(kind: str = "skill") -> dict[str, object]:
    return {
        "artifact_id": "art_test",
        "kind": kind,
        "title": "测试能力",
        "summary": "测试摘要",
        "skill_spec": {
            "skill_id": f"test_{kind}_v1",
            "steps": [
                {
                    "step_id": "s4_write_skill_draft",
                    "action_id": "omni_body",
                    "arguments_template": {
                        "action": "file.write",
                        "target": f"skills/life/test_{kind}_v1.md",
                        "args": {"content": f"# {kind} 完整版\n\n这是完整内容。\n"},
                    },
                }
            ],
        },
        "document": {
            "format": "markdown",
            "name": "SKILL.md",
            "content": "# 测试能力\n\n这是摘要版。\n",
        },
    }


def test_skill_maps_to_skills_life_zone(tmp_path):
    result=life_capability_workspace_mapper(tmp_path)(_artifact('skill'))
    assert result['reason_code']=='life.learning.legacy_publication_frozen'
    assert result['registered'] is False
    assert list(tmp_path.iterdir())==[]


def test_tool_maps_to_tools_life_zone(tmp_path):
    result=life_capability_workspace_mapper(tmp_path)(_artifact('tool'))
    assert result['reason_code']=='life.learning.legacy_publication_frozen'
    assert result['registered'] is False
    assert list(tmp_path.iterdir())==[]


def test_mapper_is_idempotent_and_falls_back_without_workspace(tmp_path):
    mapper=life_capability_workspace_mapper(tmp_path)
    assert mapper(_artifact()) == mapper(_artifact())
    assert life_capability_workspace_mapper(None)(_artifact())['publication_frozen'] is True
    assert life_capability_workspace_mapper(None)({'kind':'knowledge'}) == {}


def test_remover_deletes_exactly_the_mapped_zone_file(tmp_path):
    mapper = life_capability_workspace_mapper(tmp_path)
    remover = life_capability_workspace_remover(tmp_path)
    artifact = _artifact("skill")
    _seed_mapping(tmp_path, artifact)
    target = tmp_path / "skills" / "life" / "test_skill_v1.md"
    assert target.is_file()

    result = remover(artifact)
    assert result["removed"] is True
    assert not target.exists()
    assert remover(artifact)["removed"] is False
    assert life_capability_workspace_remover(None)(artifact) == {}


def test_remover_only_touches_the_life_zone(tmp_path):
    mapper = life_capability_workspace_mapper(tmp_path)
    remover = life_capability_workspace_remover(tmp_path)
    other = tmp_path / "skills" / "life" / "user_manual.md"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("用户自己的文件", encoding="utf-8")
    artifact = _artifact("tool")
    _seed_mapping(tmp_path, artifact)
    remover(artifact)
    assert not (tmp_path / "tools" / "life" / "test_tool_v1.md").exists()
    assert other.read_text(encoding="utf-8") == "用户自己的文件"


def _capability(artifact_id: str, skill_id: str, workspace_path: str) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "kind": "skill",
        "title": f"测试技能 {skill_id}",
        "summary": "测试摘要",
        "lineage_id": artifact_id,
        "artifact_sha256": "a" * 64,
        "status": "published",
        "origin": "life_learning",
        "skill_spec": {"skill_id": skill_id, "steps": [], "task_intents": []},
        "required_actions": ["omni_body"],
        "document": {"format": "markdown", "name": "SKILL.md", "content": f"# {skill_id}\n\n内容\n"},
        "publication": {"publisher": "life_skill_overlay", "registered": True, "workspace_path": workspace_path},
    }


def _pointer(life_id: str, artifact_id: str) -> dict[str, object]:
    return {
        "schema": "tiangong.life.capability-pointer.v1",
        "life_id": life_id,
        "lineage_id": artifact_id,
        "kind": "skill",
        "status": "active",
        "current_artifact_id": artifact_id,
        "current_artifact_sha256": "a" * 64,
        "history": [],
    }


def test_capability_discard_removes_only_its_workspace_mapping(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mapper = life_capability_workspace_mapper(workspace)
    remover = life_capability_workspace_remover(workspace)

    life = EmbeddedLifeRuntime(
        data_root=tmp_path / "life-data",
        runtime_root=tmp_path / "life-runtime",
        mode="embedded",
    )
    try:
        life.scheduler.stop(timeout_seconds=2)
        life.set_capability_workspace_mapper(mapper)
        life.set_capability_workspace_remover(remover)
        life_id = str(life._active()["life_id"])
        scope = life._scope_state(life_id)

        target = _capability("art_target", "target_skill_v1", "skills/life/target_skill_v1.md")
        other = _capability("art_other", "other_skill_v1", "skills/life/other_skill_v1.md")
        scope["capabilities"] = {"art_target": target, "art_other": other}
        scope["capability_pointers"] = {
            "art_target": _pointer(life_id, "art_target"),
            "art_other": _pointer(life_id, "art_other"),
        }
        _seed_mapping(workspace, target)
        _seed_mapping(workspace, other)
        # Existing current files from the old installation; not new publication.
        for pointer in scope['capability_pointers'].values():
            pointer['pointer_sha256']=canonical_sha256(pointer)
            key='ptr_'+canonical_sha256({'life_id':life_id,'lineage_id':pointer['lineage_id']})[:24]
            directory=life.paths.artifact_root/key;directory.mkdir(parents=True,exist_ok=True)
            (directory/'current.json').write_text(json.dumps(pointer),encoding='utf-8')
        user_file = workspace / "skills" / "life" / "user_manual.md"
        user_file.write_text("用户自己的文件", encoding="utf-8")
        target_file = workspace / "skills" / "life" / "target_skill_v1.md"
        other_file = workspace / "skills" / "life" / "other_skill_v1.md"
        assert target_file.is_file()
        assert other_file.is_file()

        result = life._capability_discard(
            {"life_id": life_id, "artifact_id": "art_target", "reason": "隔离实验"}
        )
        assert result["ok"] is True
        assert result["workspace_mapping_removed"] is True
        assert not target_file.exists()
        assert other_file.exists()
        assert other_file.read_text(encoding="utf-8") == "# other_skill_v1\n\n内容\n"
        assert user_file.read_text(encoding="utf-8") == "用户自己的文件"
        assert "art_target" not in scope["capabilities"]
        assert scope["capability_pointers"]["art_target"]["status"] == "disabled"
    finally:
        life.close()
