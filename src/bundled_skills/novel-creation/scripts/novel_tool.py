#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


TRACKING_FILES = [
    "角色关系.json",
    "时间线.json",
    "伏笔清单.json",
    "资源账本.json",
    "情感弧线.json",
    "支线进度.json",
    "世界状态.json",
    "因果链.json",
    "信息边界.json",
]

STAGE_REQUIREMENTS = {
    "L0": ["project.json", "pipeline_state.json", "创作宪法.md", "story_contract.json"],
    "L1": ["创意/创意策划书.md"],
    "L2": ["设定/世界设定.md", "设定/人物设定.md", "设定/冲突网络.md"],
    "L3": ["大纲/全书大纲.md", "大纲/细纲.md"],
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename so a crash cannot leave a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    _atomic_write(path, text)


def write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def chinese_chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def dialogue_chars(text: str) -> int:
    patterns = [
        r"“([^”]+)”",
        r"\"([^\"]+)\"",
        r"「([^」]+)」",
    ]
    total = 0
    for pattern in patterns:
        for match in re.findall(pattern, text):
            total += chinese_chars(match)
    return total


def chapter_number_from_name(path: Path) -> int:
    match = re.search(r"第\s*(\d+)\s*章", path.stem)
    if match:
        return int(match.group(1))
    nums = re.findall(r"\d+", path.stem)
    return int(nums[0]) if nums else 0


def status_path(project_dir: Path, chapter_num: int) -> Path:
    return project_dir / "正文" / f"第{chapter_num:02d}章.status.json"


def story_contract_path(project_dir: Path) -> Path:
    return project_dir / "story_contract.json"


def chapter_card_path(project_dir: Path, chapter_num: int) -> Path:
    return project_dir / "章节卡" / f"第{chapter_num:02d}章.json"


def split_terms(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        values = re.split(r"[,\n;；、|]+", str(raw))
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def first_heading(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def command_init(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    title = args.title.strip()
    genre = args.genre.strip()
    project_dir.mkdir(parents=True, exist_ok=True)
    for name in ["创意", "设定", "大纲", "章节卡", "正文", "审核报告", "追踪数据", "发布", "监控数据", "snapshots"]:
        (project_dir / name).mkdir(exist_ok=True)

    project = {
        "title": title,
        "genre": genre,
        "target_reader": args.target_reader,
        "chapter_target": args.chapters,
        "mode": args.mode,
        "brief": args.brief,
        "created_at": now(),
    }
    write_json(project_dir / "project.json", project)
    write_json(
        project_dir / "pipeline_state.json",
        {
            "project": title,
            "current_stage": "L0",
            "last_completed_stage": None,
            "current_chapter": 0,
            "updated_at": now(),
        },
    )
    write_json(
        story_contract_path(project_dir),
        {
            "schema": "novel.story_contract.v1",
            "title": title,
            "genre": genre,
            "core_promise": args.brief,
            "main_characters": [],
            "active_volume": "",
            "style_notes": [],
            "forbidden_drift": [],
            "updated_at": now(),
        },
    )
    constitution = f"""# {title} - 创作宪法

## 基本定位

- 题材：{genre}
- 目标读者：{args.target_reader}
- 计划章节：{args.chapters}
- 模式：{args.mode}

## 内容要求

依据用户目标记录内容、风格与连续性要求。尚未声明的规则不自动成为验收条件。
工具只观察文件与记录；内容质量和任务完成由最终对抗裁判判断。

## 项目备注

{args.brief or "待补充。"}
"""
    write_text(project_dir / "创作宪法.md", constitution)

    for filename in TRACKING_FILES:
        write_json(project_dir / "追踪数据" / filename, {"items": [], "updated_at": now()})

    print(json.dumps({"ok": True, "project_dir": str(project_dir), "created": True}, ensure_ascii=False, indent=2))
    return 0


def command_contract_init(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    project = read_json(project_dir / "project.json")
    contract = read_json(story_contract_path(project_dir), {
        "schema": "novel.story_contract.v1",
        "title": project.get("title") or project_dir.name,
        "genre": project.get("genre") or "",
    })
    updates = {
        "schema": "novel.story_contract.v1",
        "title": args.title or contract.get("title") or project.get("title") or project_dir.name,
        "genre": args.genre or contract.get("genre") or project.get("genre") or "",
        "core_promise": args.core_promise or contract.get("core_promise") or "",
        "main_characters": split_terms(args.main_characters) or split_terms(contract.get("main_characters")),
        "active_volume": args.active_volume or contract.get("active_volume") or "",
        "style_notes": split_terms(args.style_notes) or split_terms(contract.get("style_notes")),
        "forbidden_drift": split_terms(args.forbidden_drift) or split_terms(contract.get("forbidden_drift")),
        "updated_at": now(),
    }
    write_json(story_contract_path(project_dir), updates)
    print(json.dumps({"ok": True, "contract": str(story_contract_path(project_dir)), "data": updates}, ensure_ascii=False, indent=2))
    return 0


def command_chapter_card(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    chapter_num = int(args.chapter_num)
    card = {
        "schema": "novel.chapter_card.v1",
        "chapter": chapter_num,
        "title": args.title.strip(),
        "pov": args.pov.strip(),
        "time": args.time.strip(),
        "location": args.location.strip(),
        "characters": split_terms(args.characters),
        "must_include": split_terms(args.must_include),
        "must_not_include": split_terms(args.must_not_include),
        "conflict": args.conflict.strip(),
        "ending_hook": args.ending_hook.strip(),
        "updated_at": now(),
    }
    path = chapter_card_path(project_dir, chapter_num)
    write_json(path, card)
    print(json.dumps({"ok": True, "chapter_card": str(path), "data": card}, ensure_ascii=False, indent=2))
    return 0


def missing_for_stage(project_dir: Path, stage: str) -> list[str]:
    missing: list[str] = []
    for rel in STAGE_REQUIREMENTS.get(stage, []):
        if not (project_dir / rel).exists():
            missing.append(rel)
    return missing


def command_gate(args: argparse.Namespace) -> int:
    # Legacy command name retained as an observation entry point, not a gate.
    project_dir = Path(args.project_dir).expanduser().resolve()
    stage = args.stage.upper()
    print(json.dumps({"ok": True, "schema": "novel.observation.v2", "stage": stage,
        "missing_stage_files": missing_for_stage(project_dir, stage),
        "content_quality": "unassessed", "completion_authority": "adversarial_judge"}, ensure_ascii=False, indent=2))
    return 0


def audit_text(text: str, min_chars: int | None = None) -> dict[str, Any]:
    total, dchars = chinese_chars(text), dialogue_chars(text)
    return {
        "schema": "novel.observation.v2", "status": "observed",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chinese_chars": total, "dialogue_chars": dchars,
        "dialogue_ratio": round(dchars / total, 4) if total else 0,
        "configured_min_chars": min_chars,
        "meets_configured_count": total >= min_chars if min_chars is not None else None,
        "ending_excerpt": text.strip()[-120:],
        "content_quality": "unassessed", "completion_authority": "adversarial_judge",
        "observed_at": now(),
    }


def command_audit(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    chapter_path = Path(args.chapter).expanduser().resolve()
    chapter_num = int(args.chapter_num or chapter_number_from_name(chapter_path))
    if chapter_num <= 0:
        print(json.dumps({"ok": False, "error": "chapter_num_required"}))
        return 2
    result = audit_text(read_text(chapter_path), args.min_chars)
    result.update({"chapter": chapter_num, "chapter_path": str(chapter_path)})
    report_path = project_dir / "审核报告" / f"第{chapter_num:02d}章-观察.json"
    write_json(report_path, result)
    # Keep old passed/failed status files as historical records.
    print(json.dumps({"ok": True, "report": str(report_path), **result}, ensure_ascii=False, indent=2))
    return 0


def contract_check_text(project_dir: Path, chapter_path: Path, chapter_num: int) -> dict[str, Any]:
    text = read_text(chapter_path)
    story_path, card_path = story_contract_path(project_dir), chapter_card_path(project_dir, chapter_num)
    story, card = read_json(story_path), read_json(card_path)
    fields = [("title", card.get("title")), ("characters", card.get("characters")),
              ("must_include", card.get("must_include")), ("must_not_include", card.get("must_not_include")),
              ("forbidden_drift", story.get("forbidden_drift"))]
    fields.extend((field, card.get(field)) for field in ("pov", "time", "location"))
    return {
        "schema": "novel.contract-observation.v2", "status": "observed",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "heading": first_heading(text),
        "sources": [{"path": str(path), "exists": path.is_file(),
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
                    for path in (story_path, card_path)],
        "literal_matches": [{"field": field, "term": term, "present": term in text}
                            for field, value in fields for term in split_terms(value)],
        "scope": "literal presence only; no inference about plot or compliance",
        "content_quality": "unassessed", "completion_authority": "adversarial_judge", "observed_at": now(),
    }


def command_contract_check(args: argparse.Namespace) -> int:
    project_dir, chapter_path = Path(args.project_dir).expanduser().resolve(), Path(args.chapter).expanduser().resolve()
    chapter_num = int(args.chapter_num or chapter_number_from_name(chapter_path))
    if chapter_num <= 0:
        print(json.dumps({"ok": False, "error": "chapter_num_required"}))
        return 2
    result = contract_check_text(project_dir, chapter_path, chapter_num)
    result.update({"chapter": chapter_num, "chapter_path": str(chapter_path)})
    report_path = project_dir / "审核报告" / f"第{chapter_num:02d}章-契约观察-v2.json"
    write_json(report_path, result)
    print(json.dumps({"ok": True, "report": str(report_path), **result}, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    project = read_json(project_dir / "project.json")
    chapter_files = sorted((project_dir / "正文").glob("第*章*.md")) if (project_dir / "正文").exists() else []
    statuses = sorted((project_dir / "正文").glob("第*章.status.json")) if (project_dir / "正文").exists() else []
    passed = 0
    failed = 0
    for path in statuses:
        data = read_json(path)
        if data.get("status") == "passed":
            passed += 1
        elif data.get("status") == "failed":
            failed += 1
    summary = {
        "ok": project_dir.exists(),
        "project_dir": str(project_dir),
        "title": project.get("title"),
        "genre": project.get("genre"),
        "chapters": len(chapter_files),
        "status_files": len(statuses),
        "historical_passed": passed,
        "historical_failed": failed,
        "content_quality": "unassessed", "completion_authority": "adversarial_judge",
        "missing_by_stage": {stage: missing_for_stage(project_dir, stage) for stage in ("L0", "L1", "L2", "L3")},
        "story_contract": str(story_contract_path(project_dir)) if story_contract_path(project_dir).exists() else None,
        "chapter_cards": len(list((project_dir / "章节卡").glob("第*章.json"))) if (project_dir / "章节卡").exists() else 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


def command_package(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    project = read_json(project_dir / "project.json")
    title = str(project.get("title") or project_dir.name)
    chapters: list[tuple[int, Path, dict[str, Any]]] = []
    for chapter_path in sorted((project_dir / "正文").glob("第*章*.md")):
        num = chapter_number_from_name(chapter_path)
        if num <= 0:
            continue
        chapters.append((num, chapter_path, audit_text(read_text(chapter_path))))
    if not chapters:
        print(json.dumps({"ok": False, "error": "no_chapter_files"}, ensure_ascii=False, indent=2))
        return 1
    output = Path(args.output).expanduser().resolve() if args.output else project_dir / "发布" / f"{title}_发布包_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    lines = [
        "===== 待审阅章节汇编 =====",
        f"作品：{title}",
        f"生成时间：{now()}",
        f"收录章节：{len(chapters)}",
        "",
    ]
    for num, path, status in chapters:
        lines.extend([
            f"===== 第{num:02d}章 =====",
            f"来源：{path.name}",
            f"字数：{status.get('chinese_chars', '')}",
            "",
            read_text(path).strip(),
            "",
        ])
    if output.exists() or output.with_suffix(output.suffix + ".sha256.txt").exists():
        raise FileExistsError(f"Package output exists: {output}")
    write_text(output, "\n".join(lines))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    sha_path = output.with_suffix(output.suffix + ".sha256.txt")
    write_text(sha_path, digest + "\n")
    print(json.dumps({"ok": True, "package": str(output), "sha256": str(sha_path), "digest": digest, "chapters": len(chapters), "content_quality": "unassessed", "completion_authority": "adversarial_judge"}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal novel creation project tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--genre", default="未定")
    p.add_argument("--chapters", type=int, default=50)
    p.add_argument("--mode", choices=["fast", "monitor", "strict"], default="monitor")
    p.add_argument("--target-reader", default="网文读者")
    p.add_argument("--brief", default="")
    p.set_defaults(func=command_init)

    p = sub.add_parser("gate")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--stage", required=True, choices=["L0", "L1", "L2", "L3", "L4", "l0", "l1", "l2", "l3", "l4"])
    p.add_argument("--chapter-num", type=int)
    p.set_defaults(func=command_gate)

    p = sub.add_parser("contract-init")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--genre", default="")
    p.add_argument("--core-promise", default="")
    p.add_argument("--main-characters", default="")
    p.add_argument("--active-volume", default="")
    p.add_argument("--style-notes", default="")
    p.add_argument("--forbidden-drift", default="")
    p.set_defaults(func=command_contract_init)

    p = sub.add_parser("chapter-card")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--chapter-num", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--pov", default="")
    p.add_argument("--time", default="")
    p.add_argument("--location", default="")
    p.add_argument("--characters", default="")
    p.add_argument("--must-include", default="")
    p.add_argument("--must-not-include", default="")
    p.add_argument("--conflict", default="")
    p.add_argument("--ending-hook", default="")
    p.set_defaults(func=command_chapter_card)

    p = sub.add_parser("audit")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--chapter", required=True)
    p.add_argument("--chapter-num", type=int)
    p.add_argument("--min-chars", type=int, default=None, help="Optional count observation; never a content approval gate")
    p.set_defaults(func=command_audit)

    p = sub.add_parser("contract-check")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--chapter", required=True)
    p.add_argument("--chapter-num", type=int)
    p.set_defaults(func=command_contract_check)

    p = sub.add_parser("status")
    p.add_argument("--project-dir", required=True)
    p.set_defaults(func=command_status)

    p = sub.add_parser("package")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--output")
    p.set_defaults(func=command_package)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
