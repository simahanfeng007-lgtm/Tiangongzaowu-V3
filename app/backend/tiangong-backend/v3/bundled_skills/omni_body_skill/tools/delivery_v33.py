
"""
Tiangong Omni Body v3.3 Expanded Delivery Pack
==============================================
Tool-only extension. These are deterministic delivery actions and quality gates;
they do not plan autonomously and do not perform hidden agent loops.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

V33_DELIVERY_ACTIONS: Dict[str, Dict[str, Any]] = {
    "delivery.v33.info": {
        "risk": "A0",
        "implemented": True,
        "summary": "Inspect v3.3 expanded delivery pack actions, rubrics, and skill groups."
    },
    "writing.chapter.plan.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a web-novel chapter beat plan with hook/conflict/payoff/end-cliffhanger."
    },
    "qc.novel.chapter_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check web-novel chapter delivery for hook, POV, conflict, scene beats, emotional escalation, payoff, cliffhanger, and AI tone."
    },
    "poster.brief.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a commercial poster/design brief with audience, hierarchy, copy, visual, CTA, and export specs."
    },
    "qc.poster.commercial_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check poster/image campaign delivery for hierarchy, readability, CTA, brand consistency, export specs, and image technical readiness."
    },
    "spreadsheet.analysis.plan.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a spreadsheet analysis plan with questions, data dictionary, cleaning, analysis, charts, and decision outputs."
    },
    "qc.sheet.analysis_report_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check spreadsheet analysis deliverables for data dictionary, cleaning log, formulas, summary, insights, and decisions."
    },
    "meeting.minutes.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create structured meeting minutes with decisions, action items, owners, deadlines, risks, and follow-up."
    },
    "qc.meeting.minutes_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check meeting minutes for agenda, decisions, action items, owners, deadlines, risks, and follow-up readiness."
    },
    "sales.script.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create B2B sales script with ICP, opening, diagnosis questions, value proof, objections, and close."
    },
    "qc.sales.script_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check sales script for ICP fit, pain diagnosis, consultative flow, proof, objections, next step, and compliance."
    },
    "course.lesson_plan.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a course/lesson plan with learning objectives, assessment, activities, timing, materials, and differentiation."
    },
    "qc.course.plan_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check lesson/course plan for measurable objectives, sequence, practice, assessment, timing, materials, and learner fit."
    },
    "kb.ingestion_manifest.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a knowledge-base ingestion manifest with source inventory, chunking plan, metadata, QA pairs, and validation plan."
    },
    "qc.kb.ingestion_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check knowledge-base ingestion plan for source traceability, chunking, metadata, permissions, QA coverage, and retrieval validation."
    },
    "voice.consent_pack.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create an authorized voice/audio production consent pack and quality checklist; does not clone voices."
    },
    "qc.voice_authorized.delivery_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check authorized voice/audio delivery for consent, speaker identity, usage scope, transcript, quality, watermark/disclosure, and risk controls."
    },
    "seo.content.brief.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create people-first SEO/web content brief with audience intent, experience, evidence, structure, helpfulness, and credibility signals."
    },
    "qc.seo.people_first_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check SEO/web content for people-first helpfulness, scannability, evidence, credibility, originality, and anti-fluff."
    },
    "content.calendar.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a multi-channel content calendar with goals, audience, topics, formats, owners, deadlines, and metrics."
    },
    "qc.content.calendar_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Check content calendar for cadence, channel fit, objective alignment, owner/date clarity, asset requirements, and measurement."
    }
}

RUBRIC_WEIGHTS_V33: Dict[str, Dict[str, int]] = {
    "webnovel_chapter": {
        "opening_hook": 16, "scene_goal_conflict": 14, "pov_consistency": 12,
        "emotional_escalation": 14, "specific_detail": 10, "dialogue_action_balance": 10,
        "payoff_or_reversal": 12, "ending_cliffhanger": 12,
    },
    "poster_campaign": {
        "audience_and_offer": 14, "visual_hierarchy": 16, "headline_clarity": 14,
        "readability": 12, "brand_consistency": 10, "cta": 12,
        "export_specs": 10, "risk_and_compliance": 12,
    },
    "spreadsheet_analysis": {
        "business_question": 14, "data_dictionary": 12, "cleaning_log": 12,
        "formula_integrity": 14, "insights": 16, "visual_summary": 10,
        "decision_recommendations": 14, "auditability": 8,
    },
    "meeting_minutes": {
        "agenda_context": 10, "decisions": 18, "action_items": 18,
        "owners_deadlines": 18, "risks_blockers": 10, "follow_up": 12,
        "clarity": 8, "source_traceability": 6,
    },
    "sales_script": {
        "icp_fit": 12, "opening_permission": 10, "pain_diagnosis": 16,
        "value_proposition": 14, "proof": 12, "objection_handling": 14,
        "next_step_close": 14, "compliance": 8,
    },
    "course_plan": {
        "measurable_objectives": 16, "learner_profile": 10, "sequence": 12,
        "active_practice": 14, "assessment": 16, "materials": 8,
        "timing": 10, "differentiation": 8, "reflection": 6,
    },
    "kb_ingestion": {
        "source_inventory": 14, "permissions": 10, "chunking_strategy": 14,
        "metadata_schema": 12, "qa_pairs": 12, "retrieval_tests": 16,
        "update_policy": 10, "failure_cases": 12,
    },
    "authorized_voice_audio": {
        "consent_record": 20, "identity_scope": 16, "script_transcript": 10,
        "audio_quality": 12, "disclosure_watermark": 14, "storage_security": 10,
        "usage_limits": 10, "revocation_plan": 8,
    },
    "seo_people_first": {
        "audience_intent": 14, "first_hand_value": 14, "evidence": 14,
        "scannability": 12, "originality": 12, "trust_signals": 12,
        "anti_fluff": 10, "helpful_next_action": 12,
    },
    "content_calendar": {
        "objective_alignment": 14, "audience_segments": 10, "channel_fit": 12,
        "cadence": 12, "asset_requirements": 12, "owners_deadlines": 14,
        "measurement": 14, "risk_buffer": 12,
    },
}

GENERIC_AI_PHRASES_V33 = [
    "在当今", "赋能", "闭环", "抓手", "生态", "降本增效", "全方位", "多维度",
    "显著提升", "深度融合", "未来可期", "打造", "助力", "全面提升", "强势来袭",
]


def handle_v33_action(runtime: Any, op_id: str, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    table = {
        "delivery.v33.info": _v33_info,
        "writing.chapter.plan.create": _writing_chapter_plan_create,
        "qc.novel.chapter_check": _qc_novel_chapter,
        "poster.brief.create": _poster_brief_create,
        "qc.poster.commercial_check": _qc_poster,
        "spreadsheet.analysis.plan.create": _spreadsheet_analysis_plan_create,
        "qc.sheet.analysis_report_check": _qc_sheet_analysis,
        "meeting.minutes.create": _meeting_minutes_create,
        "qc.meeting.minutes_check": _qc_meeting_minutes,
        "sales.script.create": _sales_script_create,
        "qc.sales.script_check": _qc_sales_script,
        "course.lesson_plan.create": _course_lesson_plan_create,
        "qc.course.plan_check": _qc_course_plan,
        "kb.ingestion_manifest.create": _kb_ingestion_manifest_create,
        "qc.kb.ingestion_check": _qc_kb_ingestion,
        "voice.consent_pack.create": _voice_consent_pack_create,
        "qc.voice_authorized.delivery_check": _qc_voice_authorized,
        "seo.content.brief.create": _seo_content_brief_create,
        "qc.seo.people_first_check": _qc_seo_people_first,
        "content.calendar.create": _content_calendar_create,
        "qc.content.calendar_check": _qc_content_calendar,
    }
    fn = table.get(action)
    if fn is None:
        return {"success": False, "op_id": op_id, "action": action, "message": f"v3.3 action not implemented: {action}"}
    result = fn(runtime, target, args)
    # v3.3.1 boundary repair: these high-level *.create actions are now
    # explicitly treated as template/skeleton helpers, not complete skill execution.
    if action in {
        "writing.chapter.plan.create", "poster.brief.create", "spreadsheet.analysis.plan.create",
        "meeting.minutes.create", "sales.script.create", "course.lesson_plan.create",
        "kb.ingestion_manifest.create", "voice.consent_pack.create", "seo.content.brief.create",
        "content.calendar.create",
    } and isinstance(result, dict):
        result.setdefault("result", {})
        if isinstance(result.get("result"), dict):
            result["result"].setdefault("tool_boundary", {
                "role": "template_or_skeleton_helper",
                "not_final_delivery": True,
                "model_must_complete_content": True,
                "next_required_steps": ["produce actual content with model", "write/create final artifact", "run qc.*", "repair until pass", "deliverable.package"],
            })
        result["not_final_delivery"] = True
        result["llm_note"] = "This action creates a skeleton/brief only. The model must continue the Skill workflow; do not treat it as final completion."
    return result


def _resolve(runtime: Any, target: str | None, must_exist: bool = False) -> Path:
    return runtime._resolve(target, must_exist=must_exist)


def _rel(runtime: Any, path: Path) -> str:
    return runtime._rel(path)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_text_any(path: Path, max_chars: int = 300_000) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".json", ".csv", ".py", ".js", ".ts", ".html", ".xml", ".opml", ".srt", ".vtt"}:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    if suffix == ".docx":
        return _zip_xml_text(path, ("word/",))[:max_chars]
    if suffix == ".pptx":
        return _zip_xml_text(path, ("ppt/slides/",))[:max_chars]
    if suffix == ".xlsx":
        return _xlsx_preview(path)[:max_chars]
    if suffix == ".pdf":
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            return "\n".join((p.extract_text() or "") for p in reader.pages)[:max_chars]
        except Exception:
            return ""
    return ""


def _zip_xml_text(path: Path, prefixes: Tuple[str, ...]) -> str:
    out: List[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if any(name.startswith(p) for p in prefixes) and name.endswith(".xml"):
                    raw = zf.read(name).decode("utf-8", errors="ignore")
                    text = re.sub(r"<[^>]+>", " ", raw)
                    text = re.sub(r"\s+", " ", text).strip()
                    if text:
                        out.append(text)
    except Exception:
        pass
    return "\n".join(out)


def _xlsx_preview(path: Path) -> str:
    rows: List[str] = []
    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=False)
        try:
            for ws in wb.worksheets:
                rows.append(f"[sheet {ws.title}]")
                for r in ws.iter_rows(max_row=80, values_only=True):
                    rows.append(" | ".join("" if c is None else str(c) for c in r))
        finally:
            wb.close()
    except Exception:
        return _zip_xml_text(path, ("xl/worksheets/",))
    return "\n".join(rows)


def _issue(code: str, message: str, severity: str = "medium", repair: str = "") -> Dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "repair": repair or message}


def _score(issues: List[Dict[str, Any]], warnings: List[Dict[str, Any]] | None = None) -> int:
    score = 100
    for it in issues:
        score -= {"critical": 24, "high": 14, "medium": 8, "low": 3}.get(it.get("severity", "medium"), 6)
    for it in warnings or []:
        score -= 2 if it.get("severity", "low") == "low" else 4
    return max(0, min(100, score))


def _grade(score: int) -> str:
    if score >= 90: return "world_class_ready"
    if score >= 80: return "delivery_ready"
    if score >= 70: return "acceptable_with_minor_repair"
    if score >= 60: return "needs_repair"
    return "not_ready"


def _has_any(text: str, words: List[str]) -> bool:
    lower = text.lower()
    return any(w.lower() in lower for w in words)


def _generic_ai_issues(text: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for phrase in GENERIC_AI_PHRASES_V33:
        count = text.count(phrase)
        if count >= 2:
            issues.append(_issue("generic_phrase", f"泛化表达重复：{phrase} ×{count}", "low", "替换为具体事实、场景、动作或可验证证据。"))
    vague = len(re.findall(r"(显著|全面|大幅|有效|深度|极大|明显).{0,8}(提升|优化|改善|增强|赋能)", text))
    if vague > 4:
        issues.append(_issue("vague_claims", "模糊效果词过多，缺少具体证据。", "medium", "补充量化口径、案例、限制条件或删去泛化效果词。"))
    return issues[:15]


def _section_markdown(title: str, sections: List[Tuple[str, Any]]) -> str:
    out = [f"# {title}", "", f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for sec, val in sections:
        out.append(f"## {sec}")
        if isinstance(val, list):
            for item in val:
                out.append(f"- {item}")
        elif isinstance(val, dict):
            for k, v in val.items():
                out.append(f"- {k}：{v}")
        else:
            out.append(str(val or "待补充。"))
        out.append("")
    return "\n".join(out)


def _v33_info(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "result": {
            "schema": "tiangong.v3.delivery_expansion.v33.v1",
            "version": "3.3.1",
            "principle": "tool-only expanded skill pack: create actions are skeleton helpers; skill.route returns Skill for the model to execute.",
            "rubrics": sorted(RUBRIC_WEIGHTS_V33.keys()),
            "quality_gates": sorted(k for k in V33_DELIVERY_ACTIONS if k.startswith("qc.")),
            "create_actions": sorted(k for k in V33_DELIVERY_ACTIONS if not k.startswith("qc.") and k != "delivery.v33.info"),
        },
        "evidence": {"path": "delivery_v33", "exists": True, "bytes": 0},
    }


def _writing_chapter_plan_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "webnovel_chapter_plan.md")
    title = args.get("title", "网文章节交付计划")
    premise = args.get("premise", "待补充世界观/主线矛盾")
    pov = args.get("pov", "主角视角")
    sections = [
        ("章节定位", {"章节标题": title, "主线前情": premise, "视角": pov, "目标字数": args.get("target_words", "2500-3500")}),
        ("前500字钩子", ["开场必须出现异常/冲突/诱惑/危机之一", "第一场景不要解释世界观，先让人物做选择", "明确读者想继续看的问题"]),
        ("场景节拍", ["场景目标", "阻碍/冲突", "代价升级", "角色反应", "小反转或新信息", "阶段性回报"]),
        ("人物与情绪", ["主角欲望", "对手压力", "情绪曲线：压迫→选择→爆发/反转", "具体动作替代抽象心理"]),
        ("结尾钩子", ["未解决问题", "下一章必须点开的信息差", "一句强情绪或强悬念收束"]),
        ("质检动作", ["qc.novel.chapter_check", "qc.writing.ai_tone_check"]),
    ]
    _write(output, _section_markdown(str(title), sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_novel_chapter(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    text = str(args.get("content") or "")
    path = None
    if target:
        path = _resolve(runtime, target, must_exist=True)
        text += "\n" + _read_text_any(path)
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if len(text) < int(args.get("min_chars", 1800)):
        issues.append(_issue("too_short", "章节长度不足，难形成完整阅读节奏。", "high", "补足至少两个场景节拍和一次情绪升级。"))
    first = text[:600]
    if not _has_any(first, ["突然", "血", "死", "危", "门", "响", "秘密", "系统", "选择", "不对", "疯", "杀", "跪", "醒来", "倒计时"]):
        issues.append(_issue("weak_opening_hook", "前600字缺少强钩子/异常/危机/选择。", "high", "把冲突、危险、诱惑或异变前置到开头。"))
    if not _has_any(text, ["冲突", "阻止", "代价", "威胁", "敌", "逼", "选择", "失败", "赌"]):
        issues.append(_issue("weak_conflict", "场景目标与阻碍不明显。", "high", "明确主角目标、阻碍者和失败代价。"))
    if not _has_any(text[-800:], ["可是", "然而", "没想到", "下一刻", "真正", "背后", "来不及", "门外", "声音", "名单", "真相"]):
        warnings.append(_issue("weak_cliffhanger", "结尾悬念或翻页动力不足。", "low", "用信息差、反转或未解决危险收束。"))
    if text.count("我") > 20 and text.count("他") > 20:
        warnings.append(_issue("pov_mixed", "人称/视角可能混杂。", "low", "统一第一人称或第三人称有限视角。"))
    warnings.extend(_generic_ai_issues(text))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "webnovel_chapter", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": len(text.encode('utf-8')), "score": score}}


def _poster_brief_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "poster_campaign_brief.md")
    sections = [
        ("目标与受众", {"目标": args.get("objective", "转化/报名/咨询"), "受众": args.get("audience", "待明确"), "场景": args.get("channel", "朋友圈/海报/落地页")}),
        ("层级结构", ["主标题：一句话表达利益", "副标题：解释对象和结果", "3个以内卖点", "信任证明", "CTA与二维码/联系方式"]),
        ("视觉要求", ["主体视觉", "品牌色/禁用色", "字号层级", "留白", "移动端可读性"]),
        ("文案", {"主标题": args.get("headline", "待补充"), "副标题": args.get("subhead", "待补充"), "CTA": args.get("cta", "立即咨询")}),
        ("导出规格", {"尺寸": args.get("size", "1080x1920"), "格式": "PNG/JPG/PDF", "质检": "qc.poster.commercial_check + qc.image.delivery_check"}),
    ]
    _write(output, _section_markdown("商业海报/视觉交付Brief", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_poster(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    text = str(args.get("brief") or args.get("content") or "")
    path = None
    image_info: Dict[str, Any] = {}
    if target:
        path = _resolve(runtime, target, must_exist=True)
        if path.suffix.lower() in {".md", ".txt", ".json"}:
            text += "\n" + _read_text_any(path)
        else:
            try:
                from PIL import Image, ImageStat  # type: ignore
                with Image.open(path) as im:
                    image_info = {"width": im.width, "height": im.height, "mode": im.mode, "format": im.format}
                    if im.width < 1000 or im.height < 1000:
                        warnings.append(_issue("low_resolution", "图像分辨率偏低。", "low", "导出宽高至少 1080px 级别。"))
                    gray = im.convert("L")
                    stat = ImageStat.Stat(gray)
                    if stat.stddev and stat.stddev[0] < 8:
                        # MM-P1-2: a near-solid/blank poster is not "high
                        # quality" — it must fail instead of passing quietly.
                        issues.append(_issue(
                            "solid_color_image",
                            "图像接近纯色/空白，缺乏有效视觉内容。",
                            "critical",
                            "添加真实图形、文字与视觉层次后重新导出。",
                        ))
                    elif stat.stddev and stat.stddev[0] < 24:
                        warnings.append(_issue("low_contrast", "整体对比度偏低，标题可读性可能不足。", "low", "增强标题区明暗对比或加遮罩。"))
            except Exception as exc:
                issues.append(_issue("image_unreadable", f"图片不可读：{exc}", "critical", "重新导出 PNG/JPG。"))
    combined = text
    for word, sev in [("受众", "high"), ("主标题", "medium"), ("CTA", "high"), ("信任", "medium"), ("尺寸", "low")]:
        if word not in combined:
            issues.append(_issue(f"missing_{word}", f"缺少海报交付要素：{word}", sev, f"补充 {word}。"))
    warnings.extend(_generic_ai_issues(combined))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "poster_campaign", "score": score, "grade": _grade(score), "image_info": image_info, "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(combined.encode('utf-8')), "score": score}}


def _spreadsheet_analysis_plan_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "spreadsheet_analysis_plan.md")
    questions = args.get("questions") if isinstance(args.get("questions"), list) else ["核心业务问题是什么？", "哪些指标决定结论？", "数据是否完整可信？"]
    sections = [
        ("业务问题", questions),
        ("数据字典", ["字段名", "含义", "类型", "来源", "缺失/异常规则"]),
        ("清洗计划", ["去重", "空值处理", "异常值", "日期/金额格式", "口径统一"]),
        ("分析计划", ["描述统计", "分组对比", "趋势", "贡献度", "异常定位", "可视化"]),
        ("交付物", ["原始数据备份", "清洗后数据", "分析表", "图表", "结论摘要", "行动建议"]),
        ("质检动作", ["qc.sheet.analysis_report_check", "qc.sheet.delivery_check"]),
    ]
    _write(output, _section_markdown("表格分析交付计划", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_sheet_analysis(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else str(args.get("content") or "")
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for word, sev in [("数据字典", "medium"), ("清洗", "high"), ("结论", "high"), ("建议", "high"), ("口径", "medium")]:
        if word not in text:
            issues.append(_issue(f"missing_{word}", f"缺少表格分析要素：{word}", sev, f"补充 {word}。"))
    if path and path.suffix.lower() == ".xlsx":
        preview = text.splitlines()
        if len(preview) < 5:
            warnings.append(_issue("few_rows", "表格可读行数较少，可能不是完整分析交付。", "low", "确认是否包含数据、透视/汇总或结论表。"))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "spreadsheet_analysis", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}


def _meeting_minutes_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "meeting_minutes.md")
    decisions = args.get("decisions") if isinstance(args.get("decisions"), list) else []
    actions = args.get("action_items") if isinstance(args.get("action_items"), list) else []
    sections = [
        ("会议信息", {"主题": args.get("topic", "待补充"), "时间": args.get("date", "待补充"), "参会人": ", ".join(args.get("attendees", [])) if isinstance(args.get("attendees"), list) else "待补充"}),
        ("议程", args.get("agenda", ["待补充"]) if isinstance(args.get("agenda"), list) else [str(args.get("agenda"))]),
        ("关键结论/决策", decisions or ["待补充：每条决策写清楚背景、结论、影响范围。"]),
        ("行动项", actions or ["待补充：任务 / 负责人 / 截止时间 / 验收标准。"]),
        ("风险与阻塞", args.get("risks", ["暂无记录"]) if isinstance(args.get("risks"), list) else [str(args.get("risks"))]),
        ("下次跟进", args.get("follow_up", "待补充时间、负责人和议题。")),
        ("质检动作", ["qc.meeting.minutes_check"]),
    ]
    _write(output, _section_markdown("会议纪要与行动跟进", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_meeting_minutes(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else str(args.get("content") or "")
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    required = [("议程", "medium"), ("决策", "high"), ("行动项", "high"), ("负责人", "high"), ("截止", "high"), ("风险", "medium"), ("跟进", "medium")]
    for word, sev in required:
        if word not in text:
            issues.append(_issue(f"missing_{word}", f"纪要缺少：{word}", sev, f"补充 {word}，避免会后无法执行。"))
    if not re.search(r"\d{4}[-/.年]\d{1,2}|明天|下周|月底|周[一二三四五六日天]", text):
        warnings.append(_issue("no_deadline_signal", "行动项缺少明确时间信号。", "low", "为每个行动项补截止时间。"))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "meeting_minutes", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}


def _sales_script_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "sales_script.md")
    sections = [
        ("ICP与场景", {"行业/岗位": args.get("icp", "待明确"), "触达渠道": args.get("channel", "电话/微信/会议"), "目标": args.get("objective", "约到下一步沟通")}),
        ("开场", ["先说明身份与来意", "请求30秒许可", "一句话指出可能相关的问题"]),
        ("诊断问题", ["当前流程怎么做？", "最耗时/最卡的环节是什么？", "是否有培训/落地预算？", "谁参与决策？", "近期是否有项目窗口？"]),
        ("价值表达", ["把能力映射到对方痛点", "给出案例/数据/交付物样例", "避免空泛夸大"]),
        ("异议处理", ["没预算", "没时间", "已有供应商", "担心效果", "需要领导确认"]),
        ("收口", ["明确下一步动作", "约时间", "发送资料", "确认负责人"]),
        ("合规边界", ["不承诺不可验证效果", "不索要敏感隐私", "记录来源与同意"]),
    ]
    _write(output, _section_markdown("B2B销售话术交付", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_sales_script(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else str(args.get("content") or "")
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    required = [("ICP", "medium"), ("开场", "medium"), ("诊断", "high"), ("价值", "high"), ("案例", "medium"), ("异议", "high"), ("下一步", "high"), ("合规", "medium")]
    for word, sev in required:
        if word not in text:
            issues.append(_issue(f"missing_{word}", f"销售话术缺少：{word}", sev, f"补充 {word} 模块。"))
    if text.count("我们") > 20 and text.count("你") < 8 and text.count("您") < 8:
        warnings.append(_issue("seller_centered", "话术偏自说自话，客户诊断不足。", "low", "增加客户问题、确认句和复述句。"))
    warnings.extend(_generic_ai_issues(text))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "sales_script", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}


def _course_lesson_plan_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "course_lesson_plan.md")
    sections = [
        ("课程信息", {"主题": args.get("topic", "待补充"), "对象": args.get("learners", "待明确"), "时长": args.get("duration", "45-90分钟")}),
        ("可测量学习目标", ["学员能够……", "学员能独立完成……", "学员能解释/判断……"]),
        ("先备知识与材料", ["设备/软件", "案例资料", "练习文件", "教师演示素材"]),
        ("教学流程", ["导入5-10分钟", "概念讲解", "示范", "分步练习", "综合任务", "展示反馈", "总结"]),
        ("练习与评价", ["过程性检查", "最终作品", "评分标准", "常见错误纠正"]),
        ("分层支持", ["基础学员提示", "进阶挑战", "补救材料"]),
        ("质检动作", ["qc.course.plan_check"]),
    ]
    _write(output, _section_markdown("课程/教案交付", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_course_plan(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else str(args.get("content") or "")
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    required = [("学习目标", "high"), ("对象", "medium"), ("流程", "medium"), ("练习", "high"), ("评价", "high"), ("材料", "medium"), ("时长", "medium"), ("分层", "low")]
    for word, sev in required:
        if word not in text:
            issues.append(_issue(f"missing_{word}", f"课程方案缺少：{word}", sev, f"补充 {word}。"))
    if not re.search(r"\d+\s*(分钟|min|课时|小时)", text):
        warnings.append(_issue("no_timing_detail", "缺少具体时间分配。", "low", "给每个环节标注分钟数。"))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "course_plan", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}


def _kb_ingestion_manifest_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "kb_ingestion_manifest.json")
    sources = args.get("sources") if isinstance(args.get("sources"), list) else []
    data = {
        "schema": "tiangong.v3.kb_ingestion_manifest.v1",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_inventory": sources,
        "permissions": args.get("permissions", "需确认资料来源、授权范围和敏感信息处理方式"),
        "chunking_strategy": args.get("chunking_strategy", {"max_chars": 1200, "overlap": 120, "split_by": ["heading", "paragraph", "table_row"]}),
        "metadata_schema": ["source_id", "title", "version", "date", "owner", "topic", "confidentiality", "page_or_section"],
        "qa_pairs_required": max(10, len(sources) * 3),
        "retrieval_tests": ["关键词检索", "同义改写检索", "跨章节问题", "拒答边界", "引用回溯"],
        "update_policy": "每次资料变更需重跑抽样检索和引用校验。",
    }
    _write_json(output, data)
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size, "sources": len(sources)}}


def _qc_kb_ingestion(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else json.dumps(args, ensure_ascii=False)
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    required = [("source_inventory", "high"), ("permissions", "high"), ("chunking", "high"), ("metadata", "medium"), ("qa_pairs", "medium"), ("retrieval", "high"), ("update", "medium")]
    for word, sev in required:
        if word.lower() not in text.lower():
            issues.append(_issue(f"missing_{word}", f"知识库入库缺少：{word}", sev, f"补充 {word}。"))
    if "confidentiality" not in text.lower() and "敏感" not in text:
        warnings.append(_issue("weak_confidentiality", "缺少资料密级/敏感信息处理字段。", "low", "给每份资料标注密级和可用范围。"))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "kb_ingestion", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}


def _voice_consent_pack_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "authorized_voice_consent_pack.md")
    sections = [
        ("授权边界", {"声音主体": args.get("speaker", "本人/已授权主体"), "用途": args.get("usage", "待明确"), "期限": args.get("valid_until", "待明确"), "渠道": args.get("channels", "待明确")}),
        ("必须保留的证据", ["书面授权/录音授权", "样本来源", "脚本文本", "生成文件清单", "水印/披露说明", "撤回机制"]),
        ("质量要求", ["口齿清晰", "响度一致", "无明显爆音/底噪", "与脚本一致", "导出 WAV/MP3"]),
        ("禁用场景", ["冒充他人", "无授权克隆", "欺诈/诈骗", "政治误导", "绕过平台风控"]),
        ("质检动作", ["qc.voice_authorized.delivery_check"]),
    ]
    _write(output, _section_markdown("授权声音/音频交付同意包", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_voice_authorized(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else str(args.get("content") or "")
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for word, sev in [("授权", "critical"), ("用途", "high"), ("期限", "medium"), ("脚本", "medium"), ("水印", "medium"), ("撤回", "medium")]:
        if word not in text:
            issues.append(_issue(f"missing_{word}", f"授权声音交付缺少：{word}", sev, f"补充 {word} 证据。"))
    if _has_any(text, ["无授权", "冒充", "绕过"]) and "禁用" not in text and "禁止" not in text:
        issues.append(_issue("unsafe_voice_use", "文本中出现无授权/冒充/绕过等高风险用途。", "critical", "停止交付，改为授权 TTS 或本人声音流程。"))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "authorized_voice_audio", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}


def _seo_content_brief_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "seo_people_first_content_brief.md")
    sections = [
        ("用户意图", {"目标读者": args.get("audience", "待明确"), "搜索意图": args.get("intent", "信息/比较/购买/操作"), "核心问题": args.get("query", "待补充")}),
        ("有用性设计", ["直接回答问题", "给出一手经验或具体案例", "列出限制条件", "提供下一步操作", "避免为了SEO堆词"]),
        ("可信度", ["作者/组织经验", "来源与引用", "更新时间", "可验证数据", "风险提示"]),
        ("结构", ["标题", "摘要", "目录/小标题", "步骤/清单", "FAQ", "CTA"]),
        ("质检动作", ["qc.seo.people_first_check", "qc.writing.ai_tone_check"]),
    ]
    _write(output, _section_markdown("People-first SEO/网页内容Brief", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _qc_seo_people_first(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else str(args.get("content") or "")
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for word, sev in [("用户", "high"), ("问题", "medium"), ("经验", "medium"), ("来源", "high"), ("步骤", "medium"), ("限制", "medium"), ("下一步", "medium")]:
        if word not in text:
            issues.append(_issue(f"missing_{word}", f"People-first内容缺少：{word}", sev, f"补充 {word}。"))
    if len(text) > 0 and len(re.findall(r"https?://|doi:|来源|参考|引用", text, flags=re.I)) < 2:
        warnings.append(_issue("weak_trust_signals", "可信度信号不足。", "low", "补充来源、案例、作者经验或更新日期。"))
    warnings.extend(_generic_ai_issues(text))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "seo_people_first", "score": score, "grade": _grade(score), "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}


def _content_calendar_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "content_calendar.csv")
    topics = args.get("topics") if isinstance(args.get("topics"), list) else ["主题1", "主题2", "主题3", "主题4"]
    headers = ["date", "channel", "audience", "objective", "topic", "format", "owner", "asset_needed", "cta", "metric", "status"]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for i, topic in enumerate(topics, 1):
            writer.writerow({
                "date": f"week_{i}", "channel": args.get("channel", "微信/抖音/小红书/官网"), "audience": args.get("audience", "目标受众"),
                "objective": args.get("objective", "获客/转化/教育"), "topic": topic, "format": args.get("format", "图文/短视频/直播切片"),
                "owner": args.get("owner", "待分配"), "asset_needed": "文案/主图/视频/落地页", "cta": args.get("cta", "咨询/报名/领取资料"),
                "metric": args.get("metric", "曝光/点击/线索/转化"), "status": "planned",
            })
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size, "rows": len(topics)}}


def _qc_content_calendar(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    path = _resolve(runtime, target, must_exist=True) if target else None
    text = _read_text_any(path) if path else str(args.get("content") or "")
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for word, sev in [("date", "high"), ("channel", "medium"), ("audience", "medium"), ("objective", "high"), ("owner", "high"), ("asset", "medium"), ("metric", "high")]:
        if word.lower() not in text.lower():
            issues.append(_issue(f"missing_{word}", f"内容日历缺少字段：{word}", sev, f"补充 {word} 列或内容。"))
    rows = max(0, len(text.splitlines()) - 1)
    if rows < 4:
        warnings.append(_issue("too_few_calendar_items", "内容日历条目偏少。", "low", "至少规划4周或10条以上内容。"))
    score = _score(issues, warnings)
    return {"success": True, "result": {"type": "content_calendar", "score": score, "grade": _grade(score), "rows_estimate": rows, "issues": issues, "warnings": warnings, "acceptance": score >= 80}, "evidence": {"path": _rel(runtime, path) if path else "content", "exists": bool(path), "bytes": path.stat().st_size if path else len(text.encode('utf-8')), "score": score}}
