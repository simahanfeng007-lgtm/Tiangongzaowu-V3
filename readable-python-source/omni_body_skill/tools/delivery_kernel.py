"""
Tiangong Omni Body v3.2 Delivery Kernel
======================================

This module keeps the system a tool, not an agent. It provides deterministic
content observations, template operations, preview extraction, packaging, and basic
repair-plan generation. The model must still choose actions and iterate.
"""
from __future__ import annotations

import ast
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from .delivery_v33 import V33_DELIVERY_ACTIONS, handle_v33_action  # type: ignore
except ImportError:
    V33_DELIVERY_ACTIONS = {}
    handle_v33_action = None  # type: ignore

try:
    from .skill_router import SKILL_ROUTER_ACTIONS, handle_skill_router_action  # type: ignore
except ImportError:
    SKILL_ROUTER_ACTIONS = {}
    handle_skill_router_action = None  # type: ignore

try:
    from .pro_apps_v34 import PRO_APP_ACTIONS, handle_pro_app_action  # type: ignore
except ImportError:
    PRO_APP_ACTIONS = {}
    handle_pro_app_action = None  # type: ignore

try:
    from ..model_adapters.core import MODEL_ADAPTER_ACTIONS, handle_model_adapter_action  # type: ignore
except ImportError:
    # The skill can also be mounted with its root directly on sys.path, where
    # ``tools`` and ``model_adapters`` are sibling top-level packages.
    try:
        from model_adapters.core import MODEL_ADAPTER_ACTIONS, handle_model_adapter_action  # type: ignore
    except ImportError:
        MODEL_ADAPTER_ACTIONS = {}
        handle_model_adapter_action = None  # type: ignore

try:
    from .novel_system import NOVEL_SYSTEM_ACTIONS, handle_novel_system_action  # type: ignore
except ImportError:
    NOVEL_SYSTEM_ACTIONS = {}
    handle_novel_system_action = None  # type: ignore

DELIVERY_ACTIONS: Dict[str, Dict[str, Any]] = {
    "delivery.kernel.info": {"risk": "A0", "implemented": True, "summary": "Inspect v3.2 delivery kernel standards, rubrics, and available quality gates."},
    "template.list": {"risk": "A0", "implemented": True, "summary": "List delivery templates and rubrics shipped with the package."},
    "template.apply": {"risk": "A2", "implemented": True, "summary": "Apply a template skeleton and create a structured draft markdown/json file."},
    "preview.generate": {"risk": "A0", "implemented": True, "summary": "Generate lightweight preview/summary evidence for docx/pptx/xlsx/image/video/text deliverables."},

    "qc.docx.delivery_check": {"risk": "A0", "implemented": True, "summary": "Observe actual document text and explicit managed manifest structure; no keyword, length-based completion or quality score."},
    "qc.ppt.delivery_check": {"risk": "A0", "implemented": True, "summary": "Observe real PPT text and structure. Only explicit min_slides is checked; layout and content quality belong to the adversarial judge. No keyword, aspect-ratio or quality score gate."},
    "qc.sheet.delivery_check": {"risk": "A0", "implemented": True, "summary": "Observe up to 1000 rows of CSV or first worksheet; report truncation, duplicates and blanks as facts. No formula evaluation or quality verdict."},
    "qc.code.delivery_check": {"risk": "A0", "implemented": True, "summary": "Read source inventory and Python syntax without executing project code or commands. Use quality.run_tests through its execution authority for tests. Content quality unassessed."},
    "qc.video.delivery_check": {"risk": "A0", "implemented": True, "summary": "Read ffprobe metadata and explicit duration constraints; does not observe audiovisual content, prove playability or score hooks and CTA."},
    "qc.image.delivery_check": {"risk": "A0", "implemented": True, "summary": "Decode image and report dimensions, format and luminance statistics. No visual meaning, text readability or quality verdict."},

    "writing.outline.create": {"risk": "A2", "implemented": True, "summary": "Create a structured outline markdown for proposal, deck, research, novel, or video script workflows."},
    "research.evidence_table.create": {"risk": "A2", "implemented": True, "summary": "Create a structured research evidence table CSV/Markdown from supplied sources."},
    "repair.plan": {"risk": "A2", "implemented": True, "summary": "Write a repair plan file from QC issues; does not autonomously modify deliverables."},
    "deliverable.package": {"risk": "A2", "implemented": True, "summary": "Package final deliverables, QC reports, source notes, and manifests into a zip archive."},
}
DELIVERY_ACTIONS.update(V33_DELIVERY_ACTIONS)
DELIVERY_ACTIONS.update(SKILL_ROUTER_ACTIONS)
DELIVERY_ACTIONS.update(PRO_APP_ACTIONS)
DELIVERY_ACTIONS.update(MODEL_ADAPTER_ACTIONS)
DELIVERY_ACTIONS.update(NOVEL_SYSTEM_ACTIONS)

RUBRIC_WEIGHTS = {
    "business_proposal": {
        "customer_focus": 15,
        "executive_summary": 15,
        "problem_solution_fit": 15,
        "evidence_and_proof": 15,
        "implementation_plan": 12,
        "risk_and_assumptions": 10,
        "commercial_actionability": 10,
        "clarity": 8,
    },
    "executive_ppt": {
        "single_big_idea": 16,
        "storyline": 16,
        "slide_titles": 14,
        "evidence": 14,
        "visual_density": 12,
        "audience_transformation": 10,
        "cta": 10,
        "consistency": 8,
    },
    "code_project": {
        "correctness": 18,
        "tests": 16,
        "readability": 15,
        "maintainability": 15,
        "security": 12,
        "documentation": 10,
        "packaging": 8,
        "rollback": 6,
    },
    "research_review": {
        "question": 12,
        "search_strategy": 14,
        "screening": 12,
        "evidence_table": 14,
        "citation_traceability": 14,
        "synthesis": 14,
        "limitations": 10,
        "uncertainty": 10,
    },
    "short_video": {
        "hook": 16,
        "narrative": 14,
        "vertical_mobile_fit": 14,
        "caption_sound": 14,
        "pace": 12,
        "brand_message": 10,
        "cta": 10,
        "technical_export": 10,
    },
}


def handle_delivery_action(runtime: Any, op_id: str, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    if action in globals().get("NOVEL_SYSTEM_ACTIONS", {}):
        if globals().get("handle_novel_system_action") is None:
            return {"success": False, "op_id": op_id, "action": action, "message": "novel system unavailable"}
        return globals()["handle_novel_system_action"](runtime, op_id, action, target, args)
    if action in globals().get("MODEL_ADAPTER_ACTIONS", {}):
        if globals().get("handle_model_adapter_action") is None:
            return {"success": False, "op_id": op_id, "action": action, "message": "v3.5 model adapter layer unavailable"}
        return globals()["handle_model_adapter_action"](runtime, op_id, action, target, args)
    if action in globals().get("PRO_APP_ACTIONS", {}):
        if globals().get("handle_pro_app_action") is None:
            return {"success": False, "op_id": op_id, "action": action, "message": "v3.4 professional app layer unavailable"}
        return globals()["handle_pro_app_action"](runtime, op_id, action, target, args)
    if action in globals().get("SKILL_ROUTER_ACTIONS", {}):
        if globals().get("handle_skill_router_action") is None:
            return {"success": False, "op_id": op_id, "action": action, "message": "v3.3.1 skill router unavailable"}
        return globals()["handle_skill_router_action"](runtime, op_id, action, target, args)
    if action in globals().get("V33_DELIVERY_ACTIONS", {}):
        if globals().get("handle_v33_action") is None:
            return {"success": False, "op_id": op_id, "action": action, "message": "v3.3 delivery expansion unavailable"}
        return globals()["handle_v33_action"](runtime, op_id, action, target, args)
    if action == "delivery.kernel.info":
        return _delivery_kernel_info(runtime, target, args)
    if action == "template.list":
        return _template_list(runtime, target, args)
    if action == "template.apply":
        return _template_apply(runtime, target, args)
    if action == "preview.generate":
        return _preview_generate(runtime, target, args)
    if action == "qc.docx.delivery_check":
        return _qc_docx(runtime, target, args)
    if action == "qc.ppt.delivery_check":
        return _qc_ppt(runtime, target, args)
    if action == "qc.sheet.delivery_check":
        return _qc_sheet(runtime, target, args)
    if action == "qc.code.delivery_check":
        return _qc_code(runtime, target, args)
    if action == "qc.video.delivery_check":
        return _qc_video(runtime, target, args)
    if action == "qc.image.delivery_check":
        return _qc_image(runtime, target, args)
    if action == "writing.outline.create":
        return _writing_outline_create(runtime, target, args)
    if action == "research.evidence_table.create":
        return _research_evidence_table_create(runtime, target, args)
    if action == "repair.plan":
        return _repair_plan(runtime, target, args)
    if action == "deliverable.package":
        return _deliverable_package(runtime, target, args)
    return {"success": False, "op_id": op_id, "action": action, "message": f"Delivery action not implemented: {action}"}


def _resolve(runtime: Any, target: str | None, must_exist: bool = False) -> Path:
    return runtime._resolve(target, must_exist=must_exist)


def _rel(runtime: Any, path: Path) -> str:
    return runtime._rel(path)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _text_prefix(parts, max_chars: int) -> str:
    chunks = []
    remaining = max_chars
    for part in parts:
        value = ("\n" if chunks else "") + str(part)
        chunks.append(value[:remaining])
        remaining -= min(len(value), remaining)
        if remaining <= 0:
            break
    return "".join(chunks)


def _read_text_any(path: Path, max_chars: int = 300_000) -> str:
    """Read an actual, bounded text prefix; failed parsing is never empty success."""
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".json", ".csv", ".py", ".js", ".ts", ".html", ".xml", ".opml"}:
        with path.open(encoding="utf-8-sig", errors="strict") as stream:
            return stream.read(max_chars)
    if suffix == ".docx":
        import docx
        doc = docx.Document(str(path))
        def parts():
            yield from (p.text for p in doc.paragraphs)
            for table in doc.tables:
                for row in table.rows:
                    yield " | ".join(cell.text for cell in row.cells)
        return _text_prefix(parts(), max_chars)
    if suffix == ".pptx":
        from pptx import Presentation
        presentation = Presentation(str(path))
        def parts():
            for number, slide in enumerate(presentation.slides, 1):
                yield f"[slide {number}]"
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        yield shape.text
        return _text_prefix(parts(), max_chars)
    if suffix == ".xlsx":
        import openpyxl
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=False)
        try:
            def parts():
                for sheet in workbook.worksheets:
                    yield f"[sheet {sheet.title}]"
                    for row in sheet.iter_rows(values_only=True):
                        yield " | ".join("" if value is None else str(value) for value in row)
            return _text_prefix(parts(), max_chars)
        finally:
            workbook.close()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return _text_prefix((page.extract_text() or "" for page in reader.pages), max_chars)
    raise ValueError("preview.text_format_unsupported:" + suffix)


def _text_scope(path: Path) -> str:
    return {".docx": "body_paragraphs_and_tables_only; headers_footnotes_images_layout_not_observed",
            ".pptx": "slide_shape_text_only; notes_charts_images_layout_not_observed",
            ".xlsx": "cell_values_and_formulas_only; formulas_not_recalculated; charts_layout_not_observed",
            ".pdf": "extractable_page_text_only; no_OCR_or_render"}.get(path.suffix.lower(), "UTF-8_text_prefix")


def _sentence_stats(text: str) -> Dict[str, Any]:
    sentences = [s.strip() for s in re.split(r"[。！？.!?]\s*", text) if s.strip()]
    lengths = [len(s) for s in sentences]
    return {
        "sentences": len(sentences),
        "avg_sentence_chars": round(sum(lengths) / max(1, len(lengths)), 1),
        "long_sentence_count": sum(1 for n in lengths if n > 90),
    }


def _issue(code: str, message: str, severity: str = "medium", repair: str = "") -> Dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "repair": repair or message}


def _delivery_kernel_info(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return {
        "success": True,
        "result": {
            "schema": "tiangong.v3.delivery_kernel.v1",
            "version": "3.3.0",
            "principle": "tool-only: deterministic actions, evidence, quality gates, repair plans; no autonomous planning.",
            "rubrics": sorted(RUBRIC_WEIGHTS.keys()),
            "quality_gates": sorted(k for k in DELIVERY_ACTIONS if k.startswith("qc.")),
            "root": str(root),
        },
        "evidence": {"path": "delivery_kernel", "exists": True, "bytes": 0},
    }


def _template_list(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "templates" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {"templates": []}
    return {"success": True, "result": data, "evidence": {"path": _rel(runtime, manifest) if manifest.exists() else "templates", "exists": manifest.exists(), "bytes": manifest.stat().st_size if manifest.exists() else 0}}


def _template_apply(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    template_id = str(args.get("template_id") or args.get("id") or "business_proposal").strip()
    output = _resolve(runtime, target or args.get("output") or f"{template_id}_draft.md")
    variables = args.get("variables") if isinstance(args.get("variables"), dict) else {}
    skeleton = _template_skeleton(template_id, variables)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(skeleton, encoding="utf-8")
    result: Dict[str, Any] = {
        "success": True,
        "output": {
            "path": _rel(runtime, output),
            "exists": output.exists(),
            "bytes": output.stat().st_size,
            "template_id": template_id,
        },
    }
    try:
        from .ppt_design import template_asset_root  # type: ignore
        design_root = template_asset_root()
    except Exception:
        design_root = Path(__file__).resolve().parents[1]
    design_source = design_root / "templates" / f"{template_id}.design.json"
    if design_source.is_file():
        design = json.loads(design_source.read_text(encoding="utf-8", errors="strict"))
        if not isinstance(design, dict) or design.get("schema") != "tiangong.v3.ppt_design.v1":
            raise ValueError(f"invalid machine-readable design contract for template {template_id}")
        design_output = _resolve(runtime, args.get("design_output") or output.with_suffix(".design.json"))
        design_output.parent.mkdir(parents=True, exist_ok=True)
        _write_json(design_output, design)
        result["design_spec"] = {
            "path": _rel(runtime, design_output),
            "exists": design_output.exists(),
            "bytes": design_output.stat().st_size,
            "schema": design.get("schema"),
        }
        result["next_action_args"] = {
            "template_id": template_id,
            "design_spec": _rel(runtime, design_output),
        }
    return result


def _template_skeleton(template_id: str, v: Dict[str, Any]) -> str:
    title = v.get("title") or {
        "business_proposal": "商业方案初稿",
        "executive_ppt": "商业汇报故事线",
        "code_project": "代码工程交付说明",
        "research_review": "资料/论文综述初稿",
        "short_video": "短视频脚本与交付说明",
    }.get(template_id, f"{template_id} 模板")
    audience = v.get("audience", "待明确受众")
    if template_id == "business_proposal":
        sections = ["执行摘要", "受众与决策目标", "现状问题", "解决方案", "实施路径", "收益与证据", "风险与假设", "报价/资源", "行动建议"]
    elif template_id == "executive_ppt":
        sections = ["Big Idea", "受众现状", "核心结论", "三条支撑证据", "反对意见与回应", "实施路径", "决策请求"]
    elif template_id == "code_project":
        sections = ["需求边界", "架构设计", "运行方式", "测试证据", "安全与回滚", "交付清单"]
    elif template_id == "research_review":
        sections = ["研究问题", "搜索策略", "纳入/排除标准", "证据表", "综合结论", "局限性", "不确定性与下一步"]
    elif template_id == "short_video":
        sections = ["目标受众", "前3秒钩子", "脚本", "镜头节奏", "字幕/配乐", "封面", "CTA", "导出规格"]
    else:
        sections = ["目标", "输入", "流程", "质检", "交付"]
    body = [f"# {title}", "", f"- 受众：{audience}", f"- 交付目标：{v.get('objective', '待明确')}", f"- 版本：v0.1", ""]
    for sec in sections:
        body.append(f"## {sec}")
        body.append(v.get(sec, "待补充。"))
        body.append("")
    return "\n".join(body)


def _preview_generate(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True)
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        return _qc_video(runtime, target, {})
    preview = {"path": _rel(runtime, path), "suffix": suffix, "bytes": path.stat().st_size}
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        from PIL import Image
        with Image.open(path) as image:
            image.load()
            preview.update(width=image.width, height=image.height, mode=image.mode, format=image.format,
                           visual_content="not_observed", observation_scope="decoded_image_properties_only")
    else:
        maximum = args.get("max_chars", 12000)
        if type(maximum) is not int or not 1 <= maximum <= 300000:
            raise ValueError("preview.max_chars_must_be_integer_1_to_300000")
        text = _read_text_any(path, max_chars=maximum + 1)
        truncated = len(text) > maximum
        observed = text[:maximum]
        excerpt = observed[:1500]
        preview.update(text_chars=len(observed), text_preview=excerpt,
                       total_extracted_text_chars=None if truncated else len(observed),
                       line_count=observed.count("\n") + 1 if observed else 0,
                       line_count_in_observed_range=observed.count("\n") + 1 if observed else 0,
                       observed_char_range=[0, len(observed)], preview_char_range=[0, len(excerpt)],
                       text_truncated=truncated, preview_truncated=truncated or len(excerpt) < len(observed),
                       observation_scope=_text_scope(path), rendered=False)
    return {"success": True, "result": preview, "evidence": {"path": _rel(runtime, path), "exists": True, "bytes": path.stat().st_size}}


def _qc_docx(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True)
    if str(args.get("document_type") or args.get("mode") or "").lower() in {"long_document", "managed_long_document", "longform"}:
        text = _read_text_any(path, max_chars=300001)
        observed = _qc_managed_long_document(runtime, path, text[:300000], args)
        observed["result"].update(text_truncated=len(text) > 300000,
            observed_char_range=[0, min(len(text), 300000)], count_scope="observed_char_range",
            observation_scope=_text_scope(path), rendered=False)
        return observed
    observed = _preview_generate(runtime, target, args)
    observed["result"].update(assessment_mode="content_observation_only", content_quality="unassessed")
    return observed


def _qc_managed_long_document(runtime: Any, path: Path, text: str, args: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    manifest_path: Path | None = None
    manifest: Dict[str, Any] = {}
    raw_manifest = args.get("project_manifest")
    if not isinstance(raw_manifest, str) or not raw_manifest.strip():
        issues.append(_issue("project_manifest_required", "受管超长文档 QC 必须提供项目 manifest。", "critical", "传入工作区内 project_manifest.json。"))
    else:
        try:
            manifest_path = _resolve(runtime, raw_manifest, must_exist=True)
            loaded = json.loads(manifest_path.read_text(encoding="utf-8", errors="strict"))
            if not isinstance(loaded, dict):
                raise ValueError("manifest root must be an object")
            manifest = loaded
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            issues.append(_issue("project_manifest_invalid", f"项目 manifest 不可解析：{exc}", "critical", "修复 manifest 后重新质检。"))

    target_words = manifest.get("target_words")
    if not isinstance(target_words, int) or isinstance(target_words, bool) or target_words < 1:
        issues.append(_issue("target_words_invalid", "manifest.target_words 必须是正整数。", "critical", "写入真实目标字数。"))
        target_words = None
    chapter_files = manifest.get("chapter_files")
    if not isinstance(chapter_files, list) or not chapter_files or not all(isinstance(item, str) and item.strip() for item in chapter_files):
        issues.append(_issue("chapter_files_invalid", "manifest.chapter_files 必须是非空相对路径数组。", "critical", "列出全部章节源文件。"))
        chapter_files = []

    missing: List[str] = []
    unsafe: List[str] = []
    if manifest_path is not None:
        project_root = manifest_path.parent.resolve()
        seen: set[str] = set()
        for item in chapter_files:
            folded = item.replace("\\", "/").casefold()
            if folded in seen:
                issues.append(_issue("duplicate_chapter_path", f"章节路径重复：{item}", "critical", "修复章节清单并保持唯一顺序。"))
                continue
            seen.add(folded)
            candidate = (project_root / item).resolve()
            try:
                candidate.relative_to(project_root)
            except ValueError:
                unsafe.append(item)
                continue
            if candidate.is_symlink() or not candidate.is_file():
                missing.append(item)
        if unsafe:
            issues.append(_issue("unsafe_chapter_paths", f"章节路径逃逸项目目录：{unsafe[:5]}", "critical", "只使用项目内相对路径。"))
        if missing:
            issues.append(_issue("missing_chapter_files", f"缺少 {len(missing)} 个章节源文件。", "critical", "先补齐章节再汇编。"))

    compact_chars = len(re.sub(r"\s+", "", text))
    structural_valid = not issues
    report = {"type": "managed_long_document_delivery", "assessment_mode": "content_observation_only",
        "content_quality": "unassessed", "target_words": target_words,
        "effective_chars": compact_chars, "count_unit": "non_whitespace_characters_not_words",
        "chapter_file_count": len(chapter_files), "missing_chapter_files": missing,
        "manifest_valid": structural_valid, "issues": issues,
        "completion_authority": "adversarial_judge"}
    return {"success": True, "result": report, "evidence": {"path": _rel(runtime, path),
        "manifest": _rel(runtime, manifest_path) if manifest_path else "",
        "exists": path.exists(), "bytes": path.stat().st_size}}



def _qc_ppt(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True)
    inspection = _ppt_inspection(path)
    slides = list(inspection.get("slides") or [])
    visual_count = sum(int(slide.get("visual_count") or 0) > 0 for slide in slides)
    checks = []
    if "min_slides" in args:
        checks.append({"field": "slide_count", "minimum": int(args["min_slides"]),
                       "actual": len(slides), "satisfied": len(slides) >= int(args["min_slides"])})
    report = {"type": "executive_ppt_delivery", "assessment_mode": "content_observation_only",
        "content_quality": "unassessed", "completion_authority": "adversarial_judge",
        "slides": len(slides), "inspection": inspection,
        "visual_coverage": visual_count / max(1, len(slides)),
        "native_visual_count": int(inspection.get("native_visual_count") or 0),
        "aspect_ratio": inspection.get("aspect_ratio"), "font_names": inspection.get("font_names", []),
        "constraint_checks": checks, "layout_observation": "structure_only_not_rendered",
        "issues": [], "warnings": []}
    return {"success": True, "result": report, "evidence": {"path": _rel(runtime, path),
            "exists": True, "bytes": path.stat().st_size}}


def _ppt_inspection(path: Path) -> Dict[str, Any]:
    # Missing parser and corrupt containers are failures, never empty success.
    from .ppt_design import inspect_presentation
    return inspect_presentation(path)


def _ppt_slides(path: Path) -> List[Dict[str, Any]]:
    return list(_ppt_inspection(path).get("slides") or [])


def _qc_sheet(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True)
    rows = _read_sheet_rows(path)
    truncated = len(rows) > 1000
    rows = rows[:1000]
    seen = set()
    duplicates = 0
    for row in rows[1:]:
        key = tuple(str(v) for v in row)
        duplicates += key in seen
        seen.add(key)
    return {"success": True, "result": {"type": "sheet_delivery",
        "assessment_mode": "content_observation_only", "content_quality": "unassessed",
        "rows": len(rows), "cols": max((len(r) for r in rows), default=0), "truncated": truncated,
        "row_scope": "first_worksheet_or_csv_first_1000_rows", "preview": [[v if v is None or isinstance(v, (str, int, float, bool)) else str(v) for v in row] for row in rows[:20]],
        "preview_truncated": len(rows) > 20, "duplicate_rows_in_observed_range": duplicates,
        "blank_cells_in_observed_range": sum(v is None or str(v).strip() == "" for r in rows for v in r),
        "formula_evaluation": "not_performed", "completion_authority": "adversarial_judge"},
        "evidence": {"path": _rel(runtime, path), "exists": True, "bytes": path.stat().st_size}}


def _read_sheet_rows(path: Path) -> List[List[Any]]:
    from itertools import islice
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as f:
            return list(islice(csv.reader(f), 1001))
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=False)
    try:
        return [list(row) for row in wb.active.iter_rows(max_row=1001, values_only=True)]
    finally:
        wb.close()


def _qc_code(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    root = _resolve(runtime, target or ".", must_exist=True)
    suffixes = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cpp", ".cc", ".c", ".h", ".cs", ".php", ".rb", ".swift", ".kt"}
    miniapp_mode = str(args.get("project_type") or args.get("mode") or "").lower() in {"wechat_miniapp", "wechat_miniprogram", "miniapp", "miniprogram"}
    if miniapp_mode:
        suffixes.update({".json", ".wxml", ".wxss"})
    candidates = [root] if root.is_file() else sorted(p for p in root.rglob("*")
        if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts
        and not any(part.startswith(".omni_") for part in p.parts))
    files = [p for p in candidates if p.suffix.lower() in suffixes]
    syntax_errors, unreadable = [], []
    total_lines = 0
    for p in files[:500]:
        try:
            checked = _resolve(runtime, str(p), must_exist=True)
            if checked.stat().st_size > 2 * 1024 * 1024:
                unreadable.append({"file": _rel(runtime, p), "reason": "over_2_MiB"})
                continue
            text = checked.read_text(encoding="utf-8-sig", errors="strict")
            total_lines += len(text.splitlines())
            if p.suffix.lower() == ".py":
                try:
                    ast.parse(text)
                except SyntaxError as exc:
                    syntax_errors.append({"file": _rel(runtime, p), "line": exc.lineno, "message": exc.msg})
        except (OSError, UnicodeError) as exc:
            unreadable.append({"file": _rel(runtime, p), "reason": type(exc).__name__})
    issues = _miniapp_project_issues(runtime, root, candidates) if miniapp_mode and root.is_dir() else []
    report = {"type": "code_project_delivery", "assessment_mode": "content_observation_only",
        "content_quality": "unassessed", "completion_authority": "adversarial_judge",
        "files_checked": min(len(files), 500), "files_total": len(files), "truncated": len(files) > 500,
        "total_lines": total_lines, "syntax_errors": syntax_errors, "unreadable_files": unreadable,
        "syntax_scope": "Python AST only", "issues": issues,
        "test_files": [_rel(runtime, p) for p in candidates if "test" in p.name.lower()][:50],
        "test_execution": {"executed": False, "required_action": "quality.run_tests",
            "reason": "A read-only observation does not execute project code or supplied commands."}}
    return {"success": True, "result": report, "evidence": {"path": _rel(runtime, root), "exists": True}}


def _miniapp_project_issues(runtime: Any, root: Path, candidates: List[Path]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    by_relative = {path.relative_to(root).as_posix(): path for path in candidates}
    for required in ("app.js", "app.json", "project.config.json"):
        if required not in by_relative:
            issues.append(_issue("miniapp_required_file", f"小程序缺少 {required}。", "critical", f"创建有效的 {required}。"))
    app_config: Dict[str, Any] = {}
    for relative, path in by_relative.items():
        if path.suffix.lower() != ".json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
            if not isinstance(value, dict):
                raise ValueError("JSON root must be an object")
            if relative == "app.json":
                app_config = value
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            issues.append(_issue("miniapp_invalid_json", f"{relative} 不是有效 JSON 对象：{exc}", "critical", "修复 JSON 语法和根结构。"))
    pages = app_config.get("pages") if isinstance(app_config, dict) else None
    if not isinstance(pages, list) or not pages or not all(isinstance(page, str) and page.strip() for page in pages):
        issues.append(_issue("miniapp_pages_invalid", "app.json.pages 必须是非空页面路径数组。", "critical", "声明至少一个真实页面。"))
        pages = []
    for page in pages:
        normalized = page.replace("\\", "/").strip("/")
        if not normalized or ".." in Path(normalized).parts:
            issues.append(_issue("miniapp_page_path_unsafe", f"页面路径不安全：{page}", "critical", "只使用项目内规范相对路径。"))
            continue
        for suffix in (".js", ".wxml", ".wxss"):
            expected = normalized + suffix
            if expected not in by_relative:
                issues.append(_issue("miniapp_page_file_missing", f"页面缺少 {expected}。", "critical", "补齐页面 JS/WXML/WXSS 文件。"))
    return issues


def _qc_video(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True)
    info = _ffprobe(runtime, path)
    if not info:
        return {"success": False, "message": "Video metadata unavailable; no content or playability assessment was performed."}
    checks = []
    if "max_duration" in args:
        actual = float(info.get("duration") or 0)
        checks.append({"field": "duration", "maximum": float(args["max_duration"]), "actual": actual,
                       "satisfied": actual <= float(args["max_duration"])})
    return {"success": True, "result": {"type": "short_video_delivery", "video_info": info,
        "assessment_mode": "metadata_observation_only", "content_quality": "unassessed",
        "audio_content": "not_observed", "video_content": "not_observed", "playback": "not_verified",
        "constraint_checks": checks, "completion_authority": "adversarial_judge"},
        "evidence": {"path": _rel(runtime, path), "exists": True, "bytes": path.stat().st_size}}


def _ffprobe(runtime: Any, path: Path) -> Dict[str, Any]:
    ffprobe = getattr(runtime, "ffprobe", None) or shutil.which("ffprobe")
    if not ffprobe:
        return {}
    try:
        cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            return {}
        data = json.loads(out.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return {}
        fmt = data.get("format") or {}
        width = streams[0].get("width")
        height = streams[0].get("height")
        if not width or not height:
            return {}
        return {"duration": float(fmt.get("duration") or 0), "width": width, "height": height}
    except Exception:
        return {}


def _qc_image(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True)
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        im.load()
        stat = ImageStat.Stat(im.convert("L"))
        info = {"width": im.width, "height": im.height, "mode": im.mode, "format": im.format,
                "luminance_stddev": stat.stddev[0] if stat.stddev else None}
    checks = [{"field": field, "minimum": int(args[key]), "actual": info[field],
               "satisfied": info[field] >= int(args[key])}
              for key, field in (("min_width", "width"), ("min_height", "height")) if key in args]
    return {"success": True, "result": {"type": "image_delivery", "image_info": info,
        "assessment_mode": "decoded_image_properties_only", "content_quality": "unassessed",
        "visual_content": "not_observed", "constraint_checks": checks,
        "completion_authority": "adversarial_judge"},
        "evidence": {"path": _rel(runtime, path), "exists": True, "bytes": path.stat().st_size}}


def _ai_tone_issues(text: str) -> List[Dict[str, Any]]:
    return []


def _writing_outline_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    outline_type = str(args.get("type") or "business_proposal")
    output = _resolve(runtime, target or f"{outline_type}_outline.md")
    variables = args.get("variables") if isinstance(args.get("variables"), dict) else {}
    content = _template_skeleton(outline_type, variables)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size, "outline_type": outline_type}}


def _research_evidence_table_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "research_evidence_table.csv")
    sources = args.get("sources") if isinstance(args.get("sources"), list) else []
    headers = ["id", "title", "year", "source", "method", "sample", "key_finding", "limitations", "relevance", "url_or_doi"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for idx, s in enumerate(sources, 1):
            row = {h: "" for h in headers}
            row.update(s if isinstance(s, dict) else {"title": str(s)})
            row["id"] = row.get("id") or idx
            writer.writerow({h: row.get(h, "") for h in headers})
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size, "rows": len(sources)}}


def _repair_plan(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or args.get("output") or "repair_plan.md")
    issues = args.get("issues") if isinstance(args.get("issues"), list) else []
    source_action = args.get("source_action", "qc")
    lines = ["# 返工计划", "", f"- 来源动作：{source_action}", f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "", "## 待处理问题"]
    if not issues:
        lines.append("暂无问题。")
    for i, issue in enumerate(issues, 1):
        if isinstance(issue, dict):
            lines.append(f"{i}. **{issue.get('severity','')} / {issue.get('code','')}**：{issue.get('message','')}")
            lines.append(f"   - 修复：{issue.get('repair','')}")
        else:
            lines.append(f"{i}. {issue}")
    lines.append("\n## 返工原则\n先修 critical/high，再修 medium，最后处理 low；每次返工后重新运行对应 qc.* delivery_check。")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size, "issue_count": len(issues)}}


def _deliverable_package(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or args.get("output") or "delivery_package.zip")
    items = args.get("items") if isinstance(args.get("items"), list) else []
    if output.suffix.lower() != ".zip":
        raise ValueError("deliverable.package output must be a new .zip file")
    if output.exists():
        raise FileExistsError("deliverable.package refuses to overwrite an existing output")
    if not items or any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError("deliverable.package requires a non-empty items list")

    output_resolved = output.resolve(strict=False)
    entries: List[Tuple[Path, str]] = []
    manifest_items: List[Dict[str, Any]] = []
    seen_archives: set[str] = set()
    for item in items:
        source = _resolve(runtime, item, must_exist=True)
        if source.is_symlink() or (not source.is_file() and not source.is_dir()):
            raise ValueError("deliverable.package items must be regular files or directories")
        source_resolved = source.resolve(strict=True)
        if source_resolved == output_resolved:
            raise ValueError("deliverable.package input and output must be different paths")
        if source.is_dir() and output_resolved.is_relative_to(source_resolved):
            raise ValueError("deliverable.package output cannot be inside an input directory")
        source_files = [source] if source.is_file() else sorted(source.rglob("*"))
        file_count = 0
        total_bytes = 0
        for child in source_files:
            if child.is_symlink():
                raise ValueError("deliverable.package refuses symbolic links")
            if not child.is_file():
                continue
            child_resolved = child.resolve(strict=True)
            if child_resolved == output_resolved:
                raise ValueError("deliverable.package input and output must be different paths")
            # Items may live in user-authorized roots outside the workspace;
            # those are archived relative to their own source directory.
            archive_name = (
                child.name
                if source.is_file()
                else (
                    child.relative_to(runtime.workspace).as_posix()
                    if child.is_relative_to(runtime.workspace)
                    else f"{source.name}/{child.relative_to(source).as_posix()}"
                )
            )
            folded = archive_name.casefold()
            if folded in seen_archives or folded == "delivery_manifest.json":
                raise ValueError("deliverable.package contains a duplicate archive path")
            seen_archives.add(folded)
            entries.append((child, archive_name))
            file_count += 1
            total_bytes += child.stat().st_size
        manifest_items.append(
            {
                "path": _rel(runtime, source),
                "type": "file" if source.is_file() else "dir",
                "file_count": file_count,
                "bytes": total_bytes,
            }
        )
    if not entries:
        raise ValueError("deliverable.package cannot create a manifest-only archive")

    manifest = {
        "schema": "tiangong.v3.delivery_package.v1",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "items": manifest_items,
        "notes": args.get("notes", ""),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(
        f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temporary.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for source, archive_name in entries:
                    zf.write(source, archive_name)
                zf.writestr(
                    "DELIVERY_MANIFEST.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
            stream.flush()
            os.fsync(stream.fileno())
        with zipfile.ZipFile(temporary, "r") as verify:
            if verify.testzip() is not None:
                raise ValueError("deliverable.package temporary archive failed CRC verification")
            expected_names = {archive_name for _, archive_name in entries}
            expected_names.add("DELIVERY_MANIFEST.json")
            if set(verify.namelist()) != expected_names:
                raise ValueError("deliverable.package temporary archive membership mismatch")
            stored_manifest = json.loads(verify.read("DELIVERY_MANIFEST.json").decode("utf-8"))
            if stored_manifest != manifest:
                raise ValueError("deliverable.package temporary manifest readback mismatch")
        if output.exists():
            raise FileExistsError("deliverable.package output appeared before atomic commit")
        os.rename(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "success": True,
        "output": {
            "path": _rel(runtime, output),
            "exists": output.is_file(),
            "bytes": output.stat().st_size,
            "item_count": len(manifest["items"]),
            "file_count": len(entries),
        },
    }
