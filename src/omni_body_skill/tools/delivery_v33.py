
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
    "poster.brief.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a commercial poster/design brief with audience, hierarchy, copy, visual, CTA, and export specs."
    },
    "qc.poster.commercial_check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Observe actual poster brief text or decoded image properties; no commercial-quality score or visual-content claim."
    },
    "spreadsheet.analysis.plan.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a spreadsheet analysis plan with questions, data dictionary, cleaning, analysis, charts, and decision outputs."
    },
    "meeting.minutes.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create structured meeting minutes with decisions, action items, owners, deadlines, risks, and follow-up."
    },
    "sales.script.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create B2B sales script with ICP, opening, diagnosis questions, value proof, objections, and close."
    },
    "course.lesson_plan.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a course/lesson plan with learning objectives, assessment, activities, timing, materials, and differentiation."
    },
    "kb.ingestion_manifest.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a knowledge-base ingestion manifest with source inventory, chunking plan, metadata, QA pairs, and validation plan."
    },
    "voice.consent_pack.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create an authorized voice/audio production consent pack and quality checklist; does not clone voices."
    },
    "seo.content.brief.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create people-first SEO/web content brief with audience intent, experience, evidence, structure, helpfulness, and credibility signals."
    },
    "content.calendar.create": {
        "risk": "A2",
        "implemented": True,
        "summary": "Create a multi-channel content calendar with goals, audience, topics, formats, owners, deadlines, and metrics."
    },
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


def handle_v33_action(runtime: Any, op_id: str, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    table = {
        "delivery.v33.info": _v33_info,
        "writing.chapter.plan.create": _writing_chapter_plan_create,
        "poster.brief.create": _poster_brief_create,
        "qc.poster.commercial_check": _qc_poster,
        "spreadsheet.analysis.plan.create": _spreadsheet_analysis_plan_create,
        "meeting.minutes.create": _meeting_minutes_create,
        "sales.script.create": _sales_script_create,
        "course.lesson_plan.create": _course_lesson_plan_create,
        "kb.ingestion_manifest.create": _kb_ingestion_manifest_create,
        "voice.consent_pack.create": _voice_consent_pack_create,
        "seo.content.brief.create": _seo_content_brief_create,
        "content.calendar.create": _content_calendar_create,
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
                "next_required_steps": ["produce requested content", "write the actual artifact", "read back content and applicable structural observations", "repair observed problems", "submit current evidence to the adversarial reviewer"],
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






def _has_any(text: str, words: List[str]) -> bool:
    lower = text.lower()
    return any(w.lower() in lower for w in words)


def _generic_ai_issues(text: str) -> List[Dict[str, Any]]:
    return []


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
            "principle": "create actions are optional skeleton helpers; the model composes task-local Tools and Skill from current goals and observations; only the adversarial reviewer decides task completion.",
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
        ("内容观察", ["读取实际产物；按用户要求由对抗智能体验收。元数据不代表内容质量。"]),
    ]
    _write(output, _section_markdown(str(title), sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


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
    if not target:
        return {"success": False, "message": "A real poster artifact is required for observation."}
    path = _resolve(runtime, target, must_exist=True)
    if path.suffix.lower() in {".md", ".txt", ".json"}:
        text = _read_text_any(path)
        return {"success": True, "result": {"type": "poster_brief_observation",
            "text": text[:12000], "truncated": len(text) > 12000,
            "assessment_mode": "brief_text_only", "visual_content": "not_observed",
            "content_quality": "unassessed", "completion_authority": "adversarial_judge"},
            "evidence": {"path": _rel(runtime, path), "exists": True, "bytes": path.stat().st_size}}
    from .delivery_kernel import _qc_image
    result = _qc_image(runtime, target, args)
    result["result"]["type"] = "poster_image_observation"
    return result


def _spreadsheet_analysis_plan_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "spreadsheet_analysis_plan.md")
    questions = args.get("questions") if isinstance(args.get("questions"), list) else ["核心业务问题是什么？", "哪些指标决定结论？", "数据是否完整可信？"]
    sections = [
        ("业务问题", questions),
        ("数据字典", ["字段名", "含义", "类型", "来源", "缺失/异常规则"]),
        ("清洗计划", ["去重", "空值处理", "异常值", "日期/金额格式", "口径统一"]),
        ("分析计划", ["描述统计", "分组对比", "趋势", "贡献度", "异常定位", "可视化"]),
        ("交付物", ["原始数据备份", "清洗后数据", "分析表", "图表", "结论摘要", "行动建议"]),
        ("内容观察", ["读取实际产物；按用户要求由对抗智能体验收。元数据不代表内容质量。"]),
    ]
    _write(output, _section_markdown("表格分析交付计划", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


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
        ("内容观察", ["读取实际产物；按用户要求由对抗智能体验收。元数据不代表内容质量。"]),
    ]
    _write(output, _section_markdown("会议纪要与行动跟进", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


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


def _course_lesson_plan_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "course_lesson_plan.md")
    sections = [
        ("课程信息", {"主题": args.get("topic", "待补充"), "对象": args.get("learners", "待明确"), "时长": args.get("duration", "45-90分钟")}),
        ("可测量学习目标", ["学员能够……", "学员能独立完成……", "学员能解释/判断……"]),
        ("先备知识与材料", ["设备/软件", "案例资料", "练习文件", "教师演示素材"]),
        ("教学流程", ["导入5-10分钟", "概念讲解", "示范", "分步练习", "综合任务", "展示反馈", "总结"]),
        ("练习与评价", ["过程性检查", "最终作品", "评分标准", "常见错误纠正"]),
        ("分层支持", ["基础学员提示", "进阶挑战", "补救材料"]),
        ("内容观察", ["读取实际产物；按用户要求由对抗智能体验收。元数据不代表内容质量。"]),
    ]
    _write(output, _section_markdown("课程/教案交付", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


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


def _voice_consent_pack_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "authorized_voice_consent_pack.md")
    sections = [
        ("授权边界", {"声音主体": args.get("speaker", "本人/已授权主体"), "用途": args.get("usage", "待明确"), "期限": args.get("valid_until", "待明确"), "渠道": args.get("channels", "待明确")}),
        ("必须保留的证据", ["书面授权/录音授权", "样本来源", "脚本文本", "生成文件清单", "水印/披露说明", "撤回机制"]),
        ("质量要求", ["口齿清晰", "响度一致", "无明显爆音/底噪", "与脚本一致", "导出 WAV/MP3"]),
        ("禁用场景", ["冒充他人", "无授权克隆", "欺诈/诈骗", "政治误导", "绕过平台风控"]),
        ("内容观察", ["读取实际产物；按用户要求由对抗智能体验收。元数据不代表内容质量。"]),
    ]
    _write(output, _section_markdown("授权声音/音频交付同意包", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


def _seo_content_brief_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    output = _resolve(runtime, target or "seo_people_first_content_brief.md")
    sections = [
        ("用户意图", {"目标读者": args.get("audience", "待明确"), "搜索意图": args.get("intent", "信息/比较/购买/操作"), "核心问题": args.get("query", "待补充")}),
        ("有用性设计", ["直接回答问题", "给出一手经验或具体案例", "列出限制条件", "提供下一步操作", "避免为了SEO堆词"]),
        ("可信度", ["作者/组织经验", "来源与引用", "更新时间", "可验证数据", "风险提示"]),
        ("结构", ["标题", "摘要", "目录/小标题", "步骤/清单", "FAQ", "CTA"]),
        ("内容观察", ["读取实际产物；按用户要求由对抗智能体验收。元数据不代表内容质量。"]),
    ]
    _write(output, _section_markdown("People-first SEO/网页内容Brief", sections))
    return {"success": True, "output": {"path": _rel(runtime, output), "exists": True, "bytes": output.stat().st_size}}


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
