"""Omni Body adapter for the authoritative novel system."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict

from v3.novel_system import NovelSystemEngine, NovelSystemError


NOVEL_SYSTEM_ACTIONS: Dict[str, Dict[str, Any]] = {
    "novel.project.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a managed long-form novel project and its canonical state directories.",
    },
    "novel.project.status": {
        "risk": "A1",
        "implemented": True,
        "summary": "Read canonical novel progress, open event debt, and emotional triggers.",
    },
    "novel.project.recover": {
        "risk": "A2",
        "implemented": True,
        "summary": "Finish idempotent prepared chapter transactions after interruption.",
    },
    "novel.blueprint.update": {
        "risk": "A2",
        "implemented": True,
        "summary": "Replace one strongly typed staged blueprint section before compilation.",
    },
    "novel.blueprint.patch": {
        "risk": "A2",
        "implemented": True,
        "summary": "Merge one staged blueprint object or selected item without replacing its whole section.",
    },
    "novel.blueprint.upsert_many": {
        "risk": "A2",
        "implemented": True,
        "summary": "Merge a batch of blueprint list items by canonical id or chapter number without dropping existing items.",
    },
    "novel.blueprint.assist": {
        "risk": "A1",
        "implemented": True,
        "summary": "Calculate dependency-ordered repair batches, error energy, ages, and reference suggestions.",
    },
    "novel.reference.resolve": {
        "risk": "A1",
        "implemented": True,
        "summary": "Resolve human labels to exact canonical ids or ranked non-authoritative suggestions.",
    },
    "novel.timeline.calculate": {
        "risk": "A1",
        "implemented": True,
        "summary": "Calculate age, earliest route arrival, or interval overlap without changing the story.",
    },
    "novel.timeline.shift_suffix": {
        "risk": "A2",
        "implemented": True,
        "summary": "Atomically insert time before a pivot event by shifting its chronological suffix and recomputing chapter intervals.",
    },
    "novel.timeline.normalize": {
        "risk": "A2",
        "implemented": True,
        "summary": "Atomically resolve all currently deterministic overlap and travel-gap conflicts with strictly improving suffix shifts.",
    },
    "novel.mobility.align_initial_many": {
        "risk": "A2",
        "implemented": True,
        "summary": "Atomically align multiple character initial locations to their calculated first physical scenes.",
    },
    "novel.blueprint.compile": {
        "risk": "A2",
        "implemented": True,
        "summary": "Validate the whole story graph and freeze the immutable original blueprint.",
    },
    "novel.plan.rebase": {
        "risk": "A2",
        "implemented": True,
        "summary": "Replan future events and chapters against accepted facts while preserving protected anchors.",
    },
    "novel.chapter.checkout": {
        "risk": "A2",
        "implemented": True,
        "summary": "Issue a state-hash-bound chapter lease and compiled writing context.",
    },
    "novel.chapter.submit": {
        "risk": "A2",
        "implemented": True,
        "summary": "Validate, score, settle, and atomically commit a chapter and its factual delta.",
    },
    "novel.scene.design": {
        "risk": "A2",
        "implemented": True,
        "summary": "Score 2-3 emotional payoff candidates and bind the strongest valid scene to a chapter.",
    },
    "novel.context.query": {
        "risk": "A1",
        "implemented": True,
        "summary": "Retrieve authoritative character, event, relationship, foreshadow, chapter, or emotion context.",
    },
    "novel.project.audit": {
        "risk": "A1",
        "implemented": True,
        "summary": "Audit blueprint integrity, state hashes, event deadlines, and accepted chapter files.",
    },
}


_WORKSPACE_DIRECTORIES = (
    "创意",
    "设定",
    "大纲",
    "章节卡",
    "正文",
    "审核报告",
    "追踪数据",
    "发布",
    "监控数据",
    "snapshots",
)
_PLANNING_SECTIONS = ("story", "characters", "world", "calendar", "locations", "plot_events", "chapters")
_PLANNING_SECTION_TYPES = {
    "story": dict,
    "characters": list,
    "world": dict,
    "calendar": dict,
    "locations": list,
    "plot_events": list,
    "chapters": list,
}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return parsed if parsed > 0 else 0


def _chapter_file_sort_key(path: Path) -> tuple[int, str]:
    match = re.match(r"^第\s*(\d+)\s*章", path.stem)
    return (_positive_int(match.group(1)) if match else 0, path.name)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _markdown_projection(title: str, sections: tuple[tuple[str, Any], ...]) -> str:
    rows = [f"# {title}", "", "> 本文件由小说权威系统自动投影；续写以 `.novel-system` 结构化状态为准。", ""]
    for heading, value in sections:
        rows.extend((f"## {heading}", "", "```json", json.dumps(value, ensure_ascii=False, indent=2), "```", ""))
    return "\n".join(rows).rstrip() + "\n"


def _sync_managed_workspace(project_root: Path) -> Dict[str, Any]:
    """Project canonical novel state into a complete, portable human workspace."""
    root = project_root.expanduser().resolve()
    system = root / ".novel-system"
    manifest = _read_json(system / "manifest.json")
    original = _read_json(system / "blueprints" / "original.json")
    rolling = _read_json(system / "blueprints" / "rolling.json")
    staged = _read_json(system / "blueprints" / "staged.json")
    blueprint = rolling or original or staged
    state = _read_json(system / "state" / "current.json")
    for directory in _WORKSPACE_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)

    missing: list[str] = []
    for name in _PLANNING_SECTIONS:
        value = blueprint.get(name)
        expected = _PLANNING_SECTION_TYPES[name]
        if not isinstance(value, expected) or not value:
            missing.append(name)
    if not original:
        missing.append("blueprint.original")
    if not rolling:
        missing.append("blueprint.rolling")
    project = blueprint.get("project") if isinstance(blueprint.get("project"), dict) else {}
    manifest_planned = _positive_int(manifest.get("planned_chapters"))
    blueprint_planned = _positive_int(project.get("planned_chapters"))
    planned = manifest_planned or blueprint_planned
    if not planned:
        missing.append("project.planned_chapters")
    if manifest_planned and blueprint_planned and manifest_planned != blueprint_planned:
        missing.append("project.planned_chapters_mismatch")
    manifest_target_words = _positive_int(manifest.get("target_words"))
    blueprint_target_words = _positive_int(project.get("target_words"))
    target_words = manifest_target_words or blueprint_target_words
    if not target_words:
        missing.append("project.target_words")
    if manifest_target_words and blueprint_target_words and manifest_target_words != blueprint_target_words:
        missing.append("project.target_words_mismatch")
    chapter_rows = blueprint.get("chapters") if isinstance(blueprint.get("chapters"), list) else []
    if planned and len(chapter_rows) == planned:
        chapter_numbers = [
            _positive_int(item.get("number")) if isinstance(item, dict) else 0
            for item in chapter_rows
        ]
        if chapter_numbers != list(range(1, planned + 1)):
            missing.append("chapters.numbering")
        if any(not isinstance(item, dict) or not str(item.get("title") or "").strip() for item in chapter_rows):
            missing.append("chapters.titles")
    elif "chapters" not in missing:
        missing.append("chapters.count")
    state_issues: list[str] = []
    next_chapter = _positive_int(state.get("next_chapter")) if state else 0
    if original and rolling:
        if not state:
            state_issues.append("state.current")
        elif not next_chapter or (planned and next_chapter > planned + 1):
            state_issues.append("state.next_chapter")
        if state and not str(state.get("state_hash") or "").strip():
            state_issues.append("state.state_hash")
    if not next_chapter:
        next_chapter = 1
    planning_complete = not missing and not state_issues
    recovery_required = bool(state_issues) and not missing
    latest = sorted((root / "正文").glob("第*.md"), key=_chapter_file_sort_key)
    latest_chapter = str(latest[-1]) if latest else ""

    project_projection = {
        "schema": "tiangong.novel.workspace.v1",
        "authority": ".novel-system",
        "title": manifest.get("title") or project.get("title") or root.name,
        "genre": manifest.get("genre") or project.get("genre") or "",
        "planned_chapters": planned,
        "target_words": target_words,
        "planning_complete": planning_complete,
        "next_chapter": next_chapter,
    }
    _atomic_json(root / "project.json", project_projection)
    _atomic_json(
        root / "pipeline_state.json",
        {
            "schema": "tiangong.novel.pipeline-state.v1",
            "mode": "resume" if planning_complete else "initialize_or_repair",
            "planning_complete": planning_complete,
            "missing_planning_sections": missing,
            "state_issues": state_issues,
            "recovery_required": recovery_required,
            "next_chapter": next_chapter,
            "latest_chapter": latest_chapter,
            "canonical_state_hash": state.get("state_hash") or "",
        },
    )
    _atomic_text(
        root / "工程说明.md",
        "# 小说工程说明\n\n"
        "- `.novel-system/`：权威蓝图、事实状态、章节事务与断点。\n"
        "- `设定/`、`大纲/`、`追踪数据/`：权威状态的人可读投影。\n"
        "- `正文/`：已通过章节事务验收的正式正文。\n"
        "- 继续续写时先调用 `novel.project.status`；规划完整则从 `next_chapter` 继续，不完整则先补齐蓝图并编译。\n",
    )
    _atomic_text(root / "创作宪法.md", _markdown_projection("创作宪法", (("故事契约", blueprint.get("story") or {}), ("质量与连续性设置", blueprint.get("settings") or {}))))
    _atomic_text(root / "设定" / "世界设定.md", _markdown_projection("世界设定", (("世界规则", blueprint.get("world") or {}), ("历法", blueprint.get("calendar") or {}), ("地点", blueprint.get("locations") or []), ("路线", blueprint.get("routes") or []), ("日程", blueprint.get("schedules") or []), ("成长规则", blueprint.get("progression_rules") or []))))
    _atomic_text(root / "设定" / "人物设定.md", _markdown_projection("人物设定", (("人物", blueprint.get("characters") or []),)))
    _atomic_text(root / "设定" / "冲突网络.md", _markdown_projection("冲突网络", (("关系", blueprint.get("relationships") or []), ("情感账户", blueprint.get("emotional_accounts") or []))))
    _atomic_text(root / "大纲" / "全书大纲.md", _markdown_projection("全书大纲", (("故事", blueprint.get("story") or {}), ("情节事件", blueprint.get("plot_events") or []), ("伏笔", blueprint.get("foreshadows") or []))))
    _atomic_text(root / "大纲" / "细纲.md", _markdown_projection("章节细纲", (("章节", chapter_rows),)))
    _atomic_json(root / "追踪数据" / "权威状态.json", state)
    _atomic_json(root / "追踪数据" / "人物关系.json", {"items": blueprint.get("relationships") or []})
    _atomic_json(root / "追踪数据" / "时间线.json", {"calendar": blueprint.get("calendar") or {}, "events": blueprint.get("plot_events") or []})
    _atomic_json(root / "追踪数据" / "伏笔清单.json", {"items": blueprint.get("foreshadows") or []})
    return {
        "ok": True,
        "project_folder": str(root),
        "prose_folder": str(root / "正文"),
        "latest_chapter": latest_chapter,
        "planning_complete": planning_complete,
        "missing_planning_sections": missing,
        "state_issues": state_issues,
        "recovery_required": recovery_required,
        "resume_action": "novel.chapter.checkout" if planning_complete else "novel.project.recover" if recovery_required else "novel.blueprint.update",
    }


def handle_novel_system_action(
    runtime: Any,
    op_id: str,
    action: str,
    target: str | None,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        project_root = runtime._resolve(target, must_exist=action != "novel.project.create")
        engine = NovelSystemEngine(project_root)
        if action == "novel.project.create":
            result = engine.create_project(args)
        elif action == "novel.project.status":
            result = engine.status()
        elif action == "novel.project.recover":
            result = engine.recover()
        elif action == "novel.blueprint.update":
            result = engine.update_blueprint(args)
        elif action == "novel.blueprint.patch":
            result = engine.patch_blueprint(args)
        elif action == "novel.blueprint.upsert_many":
            result = engine.upsert_blueprint_many(args)
        elif action == "novel.blueprint.assist":
            result = engine.assist_blueprint(args)
        elif action == "novel.reference.resolve":
            result = engine.resolve_reference(args)
        elif action == "novel.timeline.calculate":
            result = engine.timeline_calculate(args)
        elif action == "novel.timeline.shift_suffix":
            result = engine.shift_timeline_suffix(args)
        elif action == "novel.timeline.normalize":
            result = engine.normalize_timeline(args)
        elif action == "novel.mobility.align_initial_many":
            result = engine.align_initial_locations_many(args)
        elif action == "novel.blueprint.compile":
            result = engine.compile_blueprint(args)
        elif action == "novel.plan.rebase":
            result = engine.rebase_plan(args)
        elif action == "novel.chapter.checkout":
            result = engine.checkout_chapter(args)
        elif action == "novel.chapter.submit":
            result = engine.submit_chapter(args)
        elif action == "novel.scene.design":
            result = engine.design_scene(args)
        elif action == "novel.context.query":
            result = engine.context_query(args)
        elif action == "novel.project.audit":
            result = engine.audit(args)
        else:
            return {"success": False, "ok": False, "status": "NOVEL_ACTION_NOT_IMPLEMENTED", "action": action}
        sync_actions = {
            "novel.project.create",
            "novel.project.status",
            "novel.project.recover",
            "novel.blueprint.update",
            "novel.blueprint.patch",
            "novel.blueprint.upsert_many",
            "novel.blueprint.compile",
            "novel.plan.rebase",
            "novel.chapter.submit",
        }
        workspace = _sync_managed_workspace(project_root) if action in sync_actions else None
        payload = {"op_id": op_id, "action": action, **result}
        if workspace is not None:
            payload["workspace"] = workspace
            payload["delivery"] = {
                "project_folder": workspace["project_folder"],
                "prose_folder": workspace["prose_folder"],
                "latest_chapter": workspace["latest_chapter"],
            }
        return payload
    except NovelSystemError as exc:
        return {"op_id": op_id, "action": action, **exc.payload()}
