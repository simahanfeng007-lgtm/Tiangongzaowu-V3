from __future__ import annotations

import json

from v3 import duihua_qiaojie
from v3 import peizhi
from v3.context_compactor import compact_if_needed
from v3.zongdiaodu import _tool_dispatch_meta


def test_non_json_context_compaction_uses_the_active_stats_collector() -> None:
    system, user, report = compact_if_needed(
        "系统指令",
        "这不是 JSON。" + ("内容" * 40_000),
        window_tokens=4_000,
    )

    assert system == "系统指令"
    assert len(user) < 80_010
    assert report["compacted"] is True
    assert report["user_compacted"] is True


def test_learned_skill_registry_writer_uses_the_declared_registry_schema(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "nengli_liebiao.json"
    monkeypatch.setattr(peizhi, "NENGLI_ZHUCE_LUJING", target)

    duihua_qiaojie._write_registry_rows({}, [{"id": "learned_demo"}])

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema"] == "tiangong.v3.ability_registry.v2"
    assert payload["nengli_liebiao"] == [{"id": "learned_demo"}]


def test_tool_dispatch_metadata_has_one_authoritative_value_per_field() -> None:
    payload = _tool_dispatch_meta(
        {
            "schema": "tiangong.code.workflow.v1",
            "currentSkillId": "code_detail_plan",
            "currentSkillLabel": "既有阶段",
            "currentFocus": "保持上下文",
            "nextSkillId": "verify",
            "nextSkillLabel": "验证",
        },
        "file.read",
        {"path": "README.md"},
        "读取文件",
        2,
    )

    assert payload is not None
    assert payload["currentSkillLabel"] == "代码工程-本次落地"
    assert payload["currentFocus"] == "执行本轮最小必要修改。"
    assert payload["nextSkillId"] == "verify"
    assert payload["nextSkillLabel"] == "验证"
