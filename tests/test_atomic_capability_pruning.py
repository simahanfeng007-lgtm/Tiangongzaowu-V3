"""Removed metadata wrappers cannot masquerade as content review or execute."""
import hashlib
import json
from pathlib import Path

import pytest

from capability_dictionary import DictionaryError, load_dictionary
from capability_dictionary.composition import compile_task_composition, composition_prompt
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
from tests.test_task_generated_composition import gateway, prepare, register


REMOVED = (
    "qc.content.calendar_check", "qc.course.plan_check", "qc.kb.ingestion_check",
    "qc.meeting.minutes_check", "qc.novel.chapter_check", "qc.research.evidence_check",
    "qc.sales.script_check", "qc.seo.people_first_check", "qc.sheet.analysis_report_check",
    "qc.voice_authorized.delivery_check", "qc.writing.ai_tone_check", "rubric.evaluate",
)


def program(*actions):
    return {"tools": [{"id": "observe", "description": "Observe actual content and identity",
                       "actions": list(actions)}],
            "skill": {"id": "inspect", "description": "Collect observations for the reviewer",
                      "steps": [{"id": "s", "tool": "observe", "depends_on": []}]}}


@pytest.mark.parametrize("action", REMOVED)
def test_removed_assessment_cannot_admit_a_program_or_claim_success(action, gateway, tmp_path):
    path = tmp_path / "actual.md"
    raw = "已完成 TODO 授权失败：这些词不能证明内容合格。".encode()
    path.write_bytes(raw)
    release = load_dictionary()
    assert action not in release.tools and action not in composition_prompt(release)
    # A valid write preceding the retired action must not run partially.
    value = program({"action": "file.write", "target": "must-not-write.md", "args": {"content": "no"}},
                    {"action": action, "target": path.name, "args": {}})
    with pytest.raises(DictionaryError, match="composition.action_unavailable"):
        register(gateway, value)
    assert not (tmp_path / "must-not-write.md").exists()
    events = gateway.store.list_execution_events(gateway.request_id, run_id=gateway.run_id,
                                                  generation=gateway.generation)
    assert not any(event.event_type == "composition.registered" for event in events)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="removed-assessment"))
    discovered = runtime.run("system.capabilities", None, {"include_actions": True})
    assert action not in discovered["actions"]
    rejected = runtime.run(action, path.name, {})
    assert rejected["success"] is False
    assert path.read_bytes() == raw


def test_existing_observers_form_a_registered_chain_with_real_content(gateway, tmp_path):
    raw = "真实内容：TODO；文件存在并不代表任务完成。\n".encode()
    (tmp_path / "actual.md").write_bytes(raw)
    value = program(*[{"action": action, "target": "actual.md", "args": {}}
                      for action in ("file.read", "file.hash", "preview.generate")])
    registered = register(gateway, value)
    compiled = compile_task_composition(value)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="real-observers"))
    outputs = []
    for ordinal, leaf in enumerate(compiled["leaves"], 1):
        prepared = prepare(gateway, registered, leaf, ordinal)
        effect = {key: prepared[key] for key in ("effect_id", "logical_effect_id", "attempt_id", "step_id")}
        started = gateway.provider(gateway.payload("start_effect", now_ms=2100 + ordinal * 10, **effect))
        assert started["dispatch_permitted"]
        call = leaf["invocation"]
        observed = runtime.run(call["action"], call["target"], call["args"])
        assert observed["success"], observed
        outputs.append(observed)
        gateway.provider(gateway.payload("finish_effect", now_ms=2200 + ordinal * 10, outcome="succeeded",
            result_summary={"ok": True, "dictionary_sha256": observed["dictionary_sha256"]}, **effect))
    assert outputs[0]["content"] == raw.decode()
    assert outputs[1]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert outputs[1]["size_bytes"] == len(raw)
    assert outputs[2]["result"]["text_preview"] == raw.decode()
    assert all("acceptance" not in result and "score" not in result
               for output in outputs for result in (output, output.get("result", {})))
    events = gateway.store.list_execution_events(gateway.request_id, run_id=gateway.run_id,
                                                  generation=gateway.generation)
    saved = next(event for event in events if event.event_type == "composition.registered")
    assert json.loads(saved.payload["program_json"]) == compiled
    assert len([event for event in events if event.event_type == "step.committed"]) == 3


@pytest.mark.parametrize("action", ["file.read", "file.hash", "preview.generate"])
def test_observers_do_not_invent_evidence_for_missing_files(action, tmp_path):
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="missing-observation"))
    assert runtime.run(action, "missing.md", {})["success"] is False


def test_current_tool_guidance_does_not_send_model_to_retired_skill_or_assessment(tmp_path):
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path), run_id="guidance"))
    for action in ("delivery.v33.info", "v34.professional_apps.info", "writing.chapter.plan.create",
                   "course.lesson_plan.create", "seo.content.brief.create"):
        target = "fixture-" + action.replace(".", "-") + ".md" if action.endswith(".create") else None
        result = runtime.run(action, target, {})
        assert result["success"], result
        shown = json.dumps(result, ensure_ascii=False)
        if target:
            shown += (tmp_path / target).read_text()
        assert "skill.route" not in shown and "skill.get" not in shown
        assert not any(name in shown for name in REMOVED)
    templates = Path(__file__).resolve().parents[1] / "src/omni_body_skill/templates"
    for template in templates.glob("*.md"):
        assert not any(name in template.read_text() for name in REMOVED), template.name
