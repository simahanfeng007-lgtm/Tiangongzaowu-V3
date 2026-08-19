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


BLACKLIST = [
    "浮现出一抹",
    "嘴角勾起",
    "深吸一口气",
    "死死盯着",
    "瞳孔收缩",
    "握紧拳头",
    "倒吸一口凉气",
    "电光火石之间",
    "时间仿佛静止",
    "空气仿佛凝固",
    "命运齿轮",
    "灵魂深处",
    "心中一震",
    "心头一震",
    "脑海中回荡",
    "不知为何",
]

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
    try:
        data = json.loads(read_text(path))
        return data if isinstance(data, dict) else dict(default or {})
    except Exception as exc:
        # A corrupt file silently resetting the contract to defaults is worse
        # than a loud warning; keep the default-return behavior but report it.
        print(f"[novel_tool] read_json failed ({path}): {exc}", file=sys.stderr)
        return dict(default or {})


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


def blacklist_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for item in BLACKLIST:
        count = text.count(item)
        if count:
            hits.append({"phrase": item, "count": count})
    return hits


def ending_strength(text: str) -> dict[str, Any]:
    clean = text.strip()
    tail = clean[-120:] if clean else ""
    hook_marks = ["？", "!", "！", "却", "忽然", "突然", "门外", "身后", "下一刻", "真相", "秘密"]
    score = sum(1 for mark in hook_marks if mark in tail)
    return {
        "tail": tail,
        "score": score,
        "ok": score > 0,
    }


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

## 读者契约

1. 每章至少推进一个剧情、关系或信息状态。
2. 角色不能知道自己没有获知的信息。
3. 伏笔必须记录，回收必须有因果。
4. 章节结尾避免完全闭环。

## 风格红线

1. 避免模板化AI腔。
2. 少用空泛情绪标签，多用动作和场景承压。
3. 设定服务冲突，不做说明书堆砌。

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
    project_dir = Path(args.project_dir).expanduser().resolve()
    stage = args.stage.upper()
    missing = missing_for_stage(project_dir, stage)
    errors = [f"missing:{item}" for item in missing]

    if stage == "L4" and args.chapter_num:
        chapter = int(args.chapter_num)
        if chapter > 1:
            prev_status = status_path(project_dir, chapter - 1)
            data = read_json(prev_status)
            if data.get("status") != "passed":
                errors.append(f"previous_chapter_not_passed:{prev_status}")

    ok = not errors
    print(json.dumps({"ok": ok, "stage": stage, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def audit_text(text: str, min_chars: int) -> dict[str, Any]:
    total = chinese_chars(text)
    dchars = dialogue_chars(text)
    ratio = dchars / total if total else 0
    hits = blacklist_hits(text)
    dashes = text.count("——")
    ending = ending_strength(text)
    errors: list[str] = []
    warnings: list[str] = []
    if total < min_chars:
        errors.append(f"word_count_below_min:{total}<{min_chars}")
    if len(hits) >= 5:
        errors.append(f"too_many_blacklist_hits:{len(hits)}")
    elif hits:
        warnings.append(f"blacklist_hits:{len(hits)}")
    if dashes > 3:
        warnings.append(f"too_many_em_dashes:{dashes}")
    if ratio < 0.18:
        warnings.append(f"low_dialogue_ratio:{ratio:.1%}")
    if not ending["ok"]:
        warnings.append("weak_ending_hook")
    return {
        "status": "passed" if not errors else "failed",
        "chinese_chars": total,
        "dialogue_chars": dchars,
        "dialogue_ratio": round(ratio, 4),
        "blacklist_hits": hits,
        "em_dashes": dashes,
        "ending": ending,
        "errors": errors,
        "warnings": warnings,
        "audited_at": now(),
    }


def command_audit(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    chapter_path = Path(args.chapter).expanduser().resolve()
    chapter_num = int(args.chapter_num or chapter_number_from_name(chapter_path))
    if chapter_num <= 0:
        print(json.dumps({"ok": False, "error": "chapter_num_required"}, ensure_ascii=False, indent=2))
        return 2
    text = read_text(chapter_path)
    result = audit_text(text, int(args.min_chars))
    result.update({"chapter": chapter_num, "chapter_path": str(chapter_path)})

    report_lines = [
        f"# 第{chapter_num:02d}章审核报告",
        "",
        f"- 状态：{result['status']}",
        f"- 中文字数：{result['chinese_chars']}",
        f"- 对话占比：{result['dialogue_ratio']:.1%}",
        f"- 破折号：{result['em_dashes']}",
        f"- 黑名单命中：{len(result['blacklist_hits'])}",
        f"- 结尾钩子分：{result['ending']['score']}",
        "",
        "## Errors",
        *(f"- {item}" for item in result["errors"]),
        "",
        "## Warnings",
        *(f"- {item}" for item in result["warnings"]),
    ]
    report_path = project_dir / "审核报告" / f"第{chapter_num:02d}章-审核报告.md"
    write_text(report_path, "\n".join(report_lines).strip() + "\n")
    write_json(status_path(project_dir, chapter_num), result)

    print(json.dumps({"ok": result["status"] == "passed", "status": result["status"], "report": str(report_path), "status_file": str(status_path(project_dir, chapter_num)), "errors": result["errors"], "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


def contract_check_text(project_dir: Path, chapter_path: Path, chapter_num: int) -> dict[str, Any]:
    text = read_text(chapter_path)
    story = read_json(story_contract_path(project_dir))
    card = read_json(chapter_card_path(project_dir, chapter_num))
    errors: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {
        "heading": first_heading(text),
        "story_contract": str(story_contract_path(project_dir)),
        "chapter_card": str(chapter_card_path(project_dir, chapter_num)),
    }

    if not story:
        errors.append(f"missing_story_contract:{story_contract_path(project_dir)}")
    if not card:
        errors.append(f"missing_chapter_card:{chapter_card_path(project_dir, chapter_num)}")
    if errors:
        return {
            "status": "failed",
            "errors": errors,
            "warnings": warnings,
            "evidence": evidence,
            "checked_at": now(),
        }

    title = str(card.get("title") or "").strip()
    heading = evidence["heading"]
    if title and title not in heading and title not in text[:300]:
        errors.append(f"chapter_title_mismatch:expected={title};heading={heading}")

    required_terms: list[str] = []
    required_terms.extend(split_terms(card.get("characters")))
    required_terms.extend(split_terms(card.get("must_include")))
    for term in required_terms:
        if term and term not in text:
            errors.append(f"required_term_missing:{term}")

    forbidden_terms: list[str] = []
    forbidden_terms.extend(split_terms(story.get("forbidden_drift")))
    forbidden_terms.extend(split_terms(card.get("must_not_include")))
    for term in forbidden_terms:
        if term and term in text:
            errors.append(f"forbidden_term_present:{term}")

    for field in ["pov", "time", "location"]:
        value = str(card.get(field) or "").strip()
        if value and value not in text:
            warnings.append(f"chapter_card_field_not_explicit:{field}={value}")

    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "warnings": warnings,
        "evidence": evidence,
        "checked_at": now(),
    }


def command_contract_check(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).expanduser().resolve()
    chapter_path = Path(args.chapter).expanduser().resolve()
    chapter_num = int(args.chapter_num or chapter_number_from_name(chapter_path))
    if chapter_num <= 0:
        print(json.dumps({"ok": False, "error": "chapter_num_required"}, ensure_ascii=False, indent=2))
        return 2
    result = contract_check_text(project_dir, chapter_path, chapter_num)
    result.update({"chapter": chapter_num, "chapter_path": str(chapter_path)})
    report_path = project_dir / "审核报告" / f"第{chapter_num:02d}章-契约检查.json"
    write_json(report_path, result)
    print(json.dumps({"ok": result["status"] == "passed", "status": result["status"], "report": str(report_path), "errors": result["errors"], "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


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
        "passed": passed,
        "failed": failed,
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
        status = read_json(status_path(project_dir, num))
        if status.get("status") == "passed":
            chapters.append((num, chapter_path, status))
    if not chapters:
        print(json.dumps({"ok": False, "error": "no_passed_chapters"}, ensure_ascii=False, indent=2))
        return 1
    output = Path(args.output).expanduser().resolve() if args.output else project_dir / "发布" / f"{title}_发布包_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    lines = [
        "===== 发布包 =====",
        f"作品：{title}",
        f"生成时间：{now()}",
        f"通过章节：{len(chapters)}",
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
    write_text(output, "\n".join(lines))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    sha_path = output.with_suffix(output.suffix + ".sha256.txt")
    write_text(sha_path, digest + "\n")
    print(json.dumps({"ok": True, "package": str(output), "sha256": str(sha_path), "digest": digest, "chapters": len(chapters)}, ensure_ascii=False, indent=2))
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
    p.add_argument("--min-chars", type=int, default=2500)
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
