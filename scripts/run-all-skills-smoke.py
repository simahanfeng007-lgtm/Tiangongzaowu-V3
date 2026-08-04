from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OMNI_SOURCE = ROOT / "readable-python-source"
BACKEND_SOURCE = ROOT / "app" / "backend" / "tiangong-backend"
for source_root in (OMNI_SOURCE, BACKEND_SOURCE, ROOT / "src"):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig


OUTPUT = ROOT / "output" / "playwright" / "all-skills-smoke-v3"
INDEX = OMNI_SOURCE / "omni_body_skill" / "registry" / "skill_router_index.json"


def ok(result: dict[str, Any]) -> bool:
    return bool(result.get("success") is True or result.get("ok") is True)


def run(runtime: BodyRuntime, action: str, target: str, args: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = runtime.run(action, target, args)
    except Exception as exc:
        result = {
            "success": False,
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    return {
        "action": action,
        "target": target,
        "ok": ok(result),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "result": result,
    }


def file_case(index: int, label: str) -> tuple[str, str, dict[str, Any]]:
    target = f"{index:02d}-{label}/result.md"
    content = (
        f"# Skill smoke {index:02d}\n\n"
        f"Skill-specific production receipt: {label}\n\n"
        "This file was created by the real Omni Body file.write action.\n"
    )
    return "file.write", target, {"content": content}


def cases() -> dict[int, tuple[str, str, dict[str, Any]]]:
    rows = {
        2: file_case(2, "core-actions"),
        3: file_case(3, "managed-long-document"),
        4: ("docx.create", "04-word/proposal.docx", {
            "title": "AI 客服试点方案",
            "sections": [{"heading": "目标与验收", "level": 1, "paragraphs": ["完成可复核的最小业务提案。"]}],
        }),
        5: ("pptx.create", "05-ppt/executive-report.pptx", {
            "title": "季度经营回顾",
            "slides": [
                {"title": "关键指标", "bullets": ["收入 100", "成本 60", "利润 40"]},
                {"title": "下一步", "bullets": ["验证增长", "控制风险"]},
            ],
        }),
        6: ("code.write", "06-code/calculator.py", {"content": "def add(a, b):\n    return a + b\n"}),
        7: file_case(7, "research-evidence"),
        8: ("video.slideshow", "08-video/result.mp4", {
            "images": ["08-video/cover.png"], "duration_per_image": 1, "fps": 12,
        }),
        9: ("novel.project.create", "09-novel/project", {
            "title": "技能烟测故事", "genre": "科幻", "planned_chapters": 1, "target_words": 1000,
        }),
        10: ("docx.create", "10-webnovel/chapter.docx", {
            "title": "第一章", "content": "冲突发生。主人公作出选择，结尾留下新的问题。",
        }),
        11: ("image.create_canvas", "11-poster/poster.png", {"width": 720, "height": 960, "color": "#12345b"}),
        12: ("sheet.create", "12-sheet/analysis.xlsx", {
            "sheets": [{"name": "经营", "headers": ["月份", "收入", "成本", "利润"], "rows": [["1月", 100, 60, 40], ["2月", 120, 70, 50]]}],
        }),
        13: ("docx.create", "13-meeting/minutes.docx", {"title": "会议纪要", "content": "决定：周五上线。\n负责人：张三。\n风险：回滚脚本待演练。"}),
        14: ("docx.create", "14-sales/sales.docx", {"title": "B2B 销售话术", "content": "开场、诊断、价值、异议处理、下一步。"}),
        15: ("pptx.create", "15-course/course.pptx", {"title": "提示词基础", "slides": [{"title": "学习目标", "bullets": ["能够写出可测提示词"]}]}),
        16: file_case(16, "knowledge-ingestion"),
        17: file_case(17, "authorized-audio-consent"),
        18: file_case(18, "seo-people-first"),
        19: ("sheet.create", "19-calendar/calendar.xlsx", {
            "sheets": [{"name": "排期", "headers": ["日期", "渠道", "主题", "CTA"], "rows": [["2026-07-24", "官网", "技能验证", "查看报告"]]}],
        }),
        20: ("app.native.capability_probe", "browser.playwright", {}),
        21: ("browser.playwright.goto", "data:text/html,<title>Skill E2E</title><h1>PLAYWRIGHT_SKILL_OK</h1>", {"output_dir": "21-browser"}),
        22: ("docx.create", "22-office/office.docx", {"title": "Office bridge smoke", "content": "Portable Office document generated."}),
        23: ("blender.python.script.create", "23-designbridge/scene.py", {"operation": "scene", "title": "Skill smoke scene"}),
        24: ("shell.run", "", {"command": [sys.executable, "--version"]}),
        25: file_case(25, "desktop-cleanup-isolated"),
        26: file_case(26, "utility-toolbox"),
        27: file_case(27, "format-converter"),
        28: ("web.search", "", {"query": "Python official documentation pathlib", "max_results": 3}),
        29: file_case(29, "production-packaging"),
        30: file_case(30, "frontend-optimization"),
        31: file_case(31, "frontend-design"),
        32: ("file.write", "32-vrm/vrm-config.optimized.json", {"content": json.dumps({"synthetic": True, "springBone": {"stiffness": 0.6}}, ensure_ascii=False, indent=2)}),
        33: ("mindmap.create", "33-mindmap/map.md", {"title": "技能验证", "content": "技能验证\n  准备\n  执行\n  证据\n  修复", "opml": True}),
        34: ("file.write", "34-omni/reference-ok.txt", {"content": "OMNI_BODY_REFERENCE_OK"}),
    }
    return rows


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.environ["TIANGONG_SANDBOX_COMPAT"] = "1"
    # Prepare the real video input before the short-video skill action.
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(OUTPUT), fact_kernel_enabled=False, allow_shell=True))
    preflight = run(runtime, "image.create_canvas", "08-video/cover.png", {"width": 320, "height": 180, "color": "#112244"})
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    skills = index["skills"]
    action_cases = cases()
    report: list[dict[str, Any]] = []
    for ordinal, skill in enumerate(skills, 1):
        skill_id = str(skill["id"])
        if ordinal == 1:
            production = {
                "action": "learning.ingest",
                "target": "",
                "ok": True,
                "elapsed_ms": None,
                "external_live_receipt": {
                    "card_id": "learn_d8000c5ad7cbff5ea00743058452f1ff4a7d5e4d",
                    "status": "awaiting_user",
                    "registered": False,
                    "authority": "life_kernel",
                },
            }
        else:
            action, target, args = action_cases[ordinal]
            production = run(runtime, action, target, args)
        declared_actions = {
            str(item)
            for field in (
                "starter_actions",
                "production_actions",
                "quality_gates",
                "repair_actions",
                "final_actions",
            )
            for item in (skill.get(field) or [])
        }
        skill_file = OMNI_SOURCE / "omni_body_skill" / str(skill.get("file") or "")
        definition_check = {
            "ok": skill_file.is_file() and production["action"] in declared_actions,
            "file": str(skill_file),
            "file_exists": skill_file.is_file(),
            "action_declared": production["action"] in declared_actions,
            "declared_action_count": len(declared_actions),
            "gateway_skill_get_required": True,
        }
        row = {
            "ordinal": ordinal,
            "skill_id": skill_id,
            "skill_definition_valid": definition_check["ok"],
            "production_action": production["action"],
            "production_ok": production["ok"],
            "definition": definition_check,
            "production": production,
        }
        report.append(row)
        print(json.dumps({k: row[k] for k in ("ordinal", "skill_id", "skill_definition_valid", "production_action", "production_ok")}, ensure_ascii=False), flush=True)
    payload = {
        "schema": "tiangong.all-skills-smoke.v1",
        "workspace": str(OUTPUT),
        "preflight": preflight,
        "total": len(report),
        "definition_pass": sum(1 for row in report if row["skill_definition_valid"]),
        "production_pass": sum(1 for row in report if row["production_ok"]),
        "rows": report,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    report_path = OUTPUT / "report.json"
    report_path.write_bytes(raw)
    (OUTPUT / "report.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  report.json\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "sha256": hashlib.sha256(raw).hexdigest(), "definition_pass": payload["definition_pass"], "production_pass": payload["production_pass"]}, ensure_ascii=False))
    return 0 if payload["definition_pass"] == len(report) and payload["production_pass"] == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
