from __future__ import annotations

from total_gateway.runtime import life_capability_workspace_mapper


def _artifact(kind: str = "skill") -> dict[str, object]:
    return {
        "artifact_id": "art_test",
        "kind": kind,
        "title": "测试能力",
        "summary": "测试摘要",
        "skill_spec": {"skill_id": f"test_{kind}_v1", "steps": []},
        "document": {
            "format": "markdown",
            "name": "SKILL.md",
            "content": "# 测试能力\n\n完整内容。\n",
        },
    }


def test_skill_maps_to_skills_life_zone(tmp_path):
    mapper = life_capability_workspace_mapper(tmp_path)
    result = mapper(_artifact("skill"))
    assert result["workspace_path"] == "skills/life/test_skill_v1.md"
    target = tmp_path / "skills" / "life" / "test_skill_v1.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "# 测试能力\n\n完整内容。\n"


def test_tool_maps_to_tools_life_zone(tmp_path):
    mapper = life_capability_workspace_mapper(tmp_path)
    result = mapper(_artifact("tool"))
    assert result["workspace_path"] == "tools/life/test_tool_v1.md"
    assert (tmp_path / "tools" / "life" / "test_tool_v1.md").is_file()


def test_mapper_is_idempotent_and_falls_back_without_workspace(tmp_path):
    mapper = life_capability_workspace_mapper(tmp_path)
    assert mapper(_artifact()) == mapper(_artifact())
    assert life_capability_workspace_mapper(None)(_artifact()) == {}
