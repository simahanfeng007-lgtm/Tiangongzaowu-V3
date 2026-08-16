
"""Tiangong Omni Body v3.5 Model Protocol Adapter Layer.

This module is a protocol adapter, not an agent. It does not decide the task
workflow. It converts provider-native tool-call shapes into a canonical
omni_body call and renders omni_body results back into provider-native
``tool_result`` / ``functionResponse`` / XML text forms.
"""
from __future__ import annotations

import fnmatch
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

TOOL_NAME = "omni_body"
PROFILE_SCHEMA = "tiangong.v3.omni_body.model_adapter_profiles.v1"
CANONICAL_SCHEMA = "tiangong.v3.omni_body.canonical_tool_call.v1"

MODEL_ADAPTER_ACTIONS: Dict[str, Dict[str, Any]] = {
    "model.adapter.info": {"risk": "A0", "implemented": True, "summary": "Inspect v3.5 model protocol adapter layer."},
    "model.adapter.list": {"risk": "A0", "implemented": True, "summary": "List supported model/provider profiles and protocol styles."},
    "model.adapter.detect": {"risk": "A0", "implemented": True, "summary": "Detect adapter profile from provider/model/endpoint/payload shape."},
    "model.adapter.render_tool_schema": {"risk": "A0", "implemented": True, "summary": "Render omni_body as provider-native tool/function declaration."},
    "model.adapter.parse_tool_call": {"risk": "A0", "implemented": True, "summary": "Parse provider-native tool calls into CanonicalOmniCall records."},
    "model.adapter.render_tool_result": {"risk": "A0", "implemented": True, "summary": "Render omni_body result back into provider-native tool result format."},
    "model.adapter.roundtrip_test": {"risk": "A0", "implemented": True, "summary": "Run profile-specific parse/render fixture tests without calling external APIs."},
}


def _module_root() -> Path:
    return Path(__file__).resolve().parent


def _load_profiles() -> Dict[str, Any]:
    path = _module_root() / "profiles.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _profiles() -> Dict[str, Dict[str, Any]]:
    return dict(_load_profiles().get("profiles", {}))


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _json_loads_maybe(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return {}
    # Providers occasionally wrap JSON in fences or emit single quotes.
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I).strip()
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # Conservative fallback for Python-ish dict strings. Avoid eval.
    try:
        fixed = re.sub(r"'([^']*)'", lambda m: json.dumps(m.group(1), ensure_ascii=False), s)
        return json.loads(fixed)
    except Exception:
        return {"_raw_arguments": value}


def _profile_match_score(profile_id: str, profile: Dict[str, Any], provider: str, model: str, endpoint: str, payload: Any) -> int:
    score = 0
    p = _norm(provider)
    m = _norm(model)
    e = _norm(endpoint)
    if p and (p == _norm(profile.get("provider")) or p in [_norm(x) for x in profile.get("aliases", [])]):
        score += 90
    for alias in profile.get("aliases", []):
        a = _norm(alias)
        if a and (a in m or a in e):
            score += 40
    for pat in profile.get("model_patterns", []):
        if m and fnmatch.fnmatch(m, _norm(pat)):
            score += 60
    if profile_id in {"gpt_openai_chat", "deepseek_openai", "minimax_openai", "glm_openai", "mimo_openai", "kimi_openai", "doubao_openai"}:
        if _payload_has_openai_tool_calls(payload):
            score += 20
    if profile.get("call_style") == "openai_responses_function_call" and _payload_has_openai_responses_function_call(payload):
        score += 70
    if profile.get("call_style") == "anthropic_tool_use" and _payload_has_anthropic_tool_use(payload):
        score += 30
    if profile.get("call_style") == "xml_or_tag_tool_call" and isinstance(payload, str) and ("<tool" in payload.lower() or "<function" in payload.lower()):
        score += 50
    return score


def detect_profile(provider: str | None = None, model: str | None = None, endpoint: str | None = None, payload: Any = None, preferred_profile: str | None = None) -> Dict[str, Any]:
    profiles = _profiles()
    if preferred_profile and preferred_profile in profiles:
        p = dict(profiles[preferred_profile])
        p["profile_id"] = preferred_profile
        p["detect_reason"] = "explicit preferred_profile"
        p["confidence"] = 1.0
        return p
    scored: List[Tuple[int, str, Dict[str, Any]]] = []
    for pid, meta in profiles.items():
        score = _profile_match_score(pid, meta, provider or "", model or "", endpoint or "", payload)
        scored.append((score, pid, meta))
    scored.sort(reverse=True, key=lambda x: x[0])
    best_score, pid, meta = scored[0]
    # Default to OpenAI-style because most listed domestic APIs expose OpenAI-compatible tools.
    if best_score <= 0:
        pid = "gpt_openai_chat"
        meta = profiles[pid]
        best_score = 10
    out = dict(meta)
    out["profile_id"] = pid
    out["confidence"] = min(1.0, best_score / 100)
    out["detect_score"] = best_score
    out["detect_reason"] = "profile/provider/model/payload match"
    return out


def _omni_parameters_schema(strict: bool = False) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "要执行的 Omni Body 动作。直接从已实现 action 中选择，例如 file.write/file.read/file.list/code.read/code.write/code.patch_replace/shell.run/quality.run_tests/qc.*/deliverable.package/docx.create/pptx.create/sheet.create/pdf.extract_text/web.search。优先直接调用生产 action。"},
            "target": {"type": "string", "description": "主目标：文件路径、URL、对象ID、输出路径或空字符串。"},
            "args": {
                "type": "object",
                "description": (
                    "动作专用参数。例如 content(文件内容)、query(搜索词)、command(shell命令)、job(任务描述)等。"
                ),
            },
            "_task_profile": {
                "type": "object",
                "description": "可选的任务理解建议。轻量任务可省略；Runtime 会在工具执行前移除，并只把用户目标与真实证据作为验收权威。",
                "properties": {
                    "schema": {"type": "string", "enum": ["tiangong.v3.task_profile.v2"]},
                    "proposed_level": {"type": "string", "enum": ["L1", "L2", "L3"]},
                    "desired_facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "fact_id": {"type": "string"},
                                "kind": {"type": "string", "enum": ["observation", "effect", "execution", "delivery"]},
                                "target": {"type": "string"},
                                "success_condition": {"type": "string"},
                            },
                            "required": ["fact_id", "kind"],
                            "additionalProperties": False,
                        },
                    },
                    "plan_hint": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step_id": {"type": "string"},
                                "action": {"type": "string"},
                                "target": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                                "acceptance": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["step_id", "action"],
                            "additionalProperties": False,
                        },
                    },
                    "constraints": {
                        "type": "object",
                        "properties": {
                            "forbidden_tools": {"type": "array", "items": {"type": "string"}},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["schema", "proposed_level"],
                "additionalProperties": False,
            },
        },
        "required": ["action"],
        "additionalProperties": True,
    }
    schema["properties"]["action"]["minLength"] = 1
    schema["properties"]["action"]["description"] += (
        " 规范补丁动作是 code.patch_replace；file.patch_replace 仅为兼容别名。"
        " 受托管小说专用动作不属于通用动作集，只能由已经实际读取的受托管全书 Skill 提供。"
        " 单章、少量章节、一次性小说协作包、大纲、人物表、线索表或审校文档必须遵循匹配到的交付 Skill，使用 file.write/docx.create 和对应 qc，禁止自行虚构全书规模后升级为整书工程。"
    )
    schema["properties"]["target"]["description"] += (
        " 所有 file/code/quality 路径动作必须提供非空且位于工作区内的 target；不得用空值暗示工作区根。"
    )
    schema["properties"]["args"]["description"] += (
        " code.patch_replace 要求 find，file.copy/move 要求 destination；非法结构会在任何副作用前被拒绝。"
    )
    schema["required"] = ["action"]
    schema["additionalProperties"] = False
    if strict:
        schema["additionalProperties"] = False
    return schema


def _tool_description() -> str:
    return (
        "强类型契约：Windows shell.run 使用 cmd.exe，禁止 head/cat/grep/bash/mkdir -p；文件操作与语法检查优先使用结构化 action。"
        "路径类 action 的 target 必须非空且位于工作区内；code.patch_replace 必须提供 args.find。参数错误后禁止原样重试。\n"
        "天工 Omni Body 唯一工具入口。只传 action/target/args；风险等级由 Runtime 独立裁决。"
        "可在顶层 _task_profile 提供建议级别、目标事实和可变 plan_hint；轻量任务可省略。"
        "Runtime 只从用户目标建立硬事实义务，计划不会成为额外验收项。\n"
        "可用 action 及能力：\n"
        "文件: file.read(读文本)/file.write(写文件)/file.list(列目录)/file.mkdir(建目录)/file.copy/file.move/file.delete_to_trash/file.search/file.hash.\n"
        "文档: docx.create(生成Word)/pptx.create(生成PPT)/pptx.read(检查PPT结构与视觉证据)/sheet.create(生成Excel)/sheet.read(读Excel)/pdf.extract_text(提取PDF文字)/pdf.create_from_text(文字转PDF)/mindmap.create(思维导图).\n"
        "代码: python.run(运行Python脚本)/shell.run(运行Shell命令)/code.read/code.write/code.patch_replace/quality.python_syntax/quality.run_tests.\n"
        "压缩: zip.create/zip.extract.\n"
        "图片: image.info/image.create_canvas/image.resize/image.crop/image.rotate/image.add_text/image.compose/image.convert.\n"
        "音视频: audio.tone/audio.trim/audio.concat/video.info/video.cut/video.extract_audio/video.add_audio/video.slideshow.\n"
        "搜索: browser.search_web(网页搜索)/web.search/http.get(读URL)/browser.open.\n"
        "质检交付: qc.*(质量检查)/deliverable.package(交付打包).\n"
        "系统: life.body.state.query/life.activity.query/system.capabilities/system.health/rollback.list/rollback.apply.\n"
        "受托管小说能力采用最小暴露：通用工具说明不公布其动作名。只有用户明确要求全书/长期多章工程或续作已有托管项目，并且模型实际读取对应受托管 Skill 后，才按该 Skill 公布的动作执行。单章、少量章节、一次性协作资料包不得升级为整书项目。\n"
        "技能选择有双通道：系统已提供 active/related Skill 时直接读取该 Skill；没有匹配且任务需要专用流程时，模型可调用 skill.route 再 skill.get/skill.read。选定后以具体 Skill 的作用域和工作流为准，通用动作说明不得覆盖它。\n"
    )


def render_tool_schema(profile_id: str | None = None, provider: str | None = None, model: str | None = None, style: str | None = None) -> Dict[str, Any]:
    prof = detect_profile(provider=provider, model=model, preferred_profile=profile_id)
    schema_style = style or prof.get("schema_style") or "openai_tools"
    params = _omni_parameters_schema(strict=bool(prof.get("supports_strict_schema")))
    if schema_style == "anthropic_tools":
        return {
            "profile": prof,
            "tool_schema": [{"name": TOOL_NAME, "description": _tool_description(), "input_schema": params}],
        }
    if schema_style == "openai_responses_tools":
        return {
            "profile": prof,
            "tool_schema": [{
                "type": "function",
                "name": TOOL_NAME,
                "description": _tool_description(),
                "parameters": params,
                "strict": bool(prof.get("supports_strict_schema")),
            }],
        }
    if schema_style == "gemini_function_declarations":
        return {
            "profile": prof,
            "tool_schema": {"functionDeclarations": [{"name": TOOL_NAME, "description": _tool_description(), "parameters": params}]},
        }
    if schema_style == "xml_prompt_contract":
        prompt = (
            "可用工具：omni_body。按以下 XML 输出工具调用：\n"
            "<tool_call><name>omni_body</name><arguments>{\"action\":\"file.write\",\"target\":\"output.txt\",\"args\":{\"content\":\"...\"}}</arguments></tool_call>\n"
            "arguments 必须是 JSON；不要把完整交付流程藏在一个请求里。"
            "可选在顶层 _task_profile 给出 schema、proposed_level、desired_facts、"
            "可变 plan_hint 与 constraints；轻量任务可省略，计划不参与硬验收。"
        )
        return {"profile": prof, "tool_schema": prompt}
    # OpenAI-compatible: GPT/DeepSeek/MiniMax/GLM/MiMo/Kimi/Doubao
    return {
        "profile": prof,
        "tool_schema": [{
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": _tool_description(),
                "parameters": params,
            },
        }],
    }


def _payload_has_openai_tool_calls(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("tool_calls"), list):
        return True
    try:
        msg = payload.get("choices", [{}])[0].get("message", {})
        return isinstance(msg.get("tool_calls"), list)
    except Exception:
        return False


def _payload_has_openai_responses_function_call(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("type") == "function_call":
        return True
    output = payload.get("output")
    return isinstance(output, list) and any(isinstance(item, dict) and item.get("type") == "function_call" for item in output)


def _payload_has_anthropic_tool_use(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    content = payload.get("content")
    if isinstance(content, list):
        return any(isinstance(x, dict) and x.get("type") == "tool_use" for x in content)
    return False


def _extract_openai_tool_calls(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("tool_calls"), list):
            return payload["tool_calls"]
        if "function" in payload and ("id" in payload or "type" in payload):
            return [payload]
        if isinstance(payload.get("message"), dict) and isinstance(payload["message"].get("tool_calls"), list):
            return payload["message"]["tool_calls"]
        try:
            return payload.get("choices", [{}])[0].get("message", {}).get("tool_calls", []) or []
        except Exception:
            return []
    return []


def _extract_openai_responses_calls(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if payload.get("type") == "function_call":
        return [payload]
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    return [dict(item) for item in output if isinstance(item, dict) and item.get("type") == "function_call"]


def _canonical_from_arguments(arguments: Any, call_id: str | None, provider: str, profile: str, raw_name: str | None = None) -> Dict[str, Any]:
    args_obj = _json_loads_maybe(arguments)
    if not isinstance(args_obj, dict):
        args_obj = {"_raw_arguments": args_obj}
    # Tolerate common aliases emitted by weaker tool-call models.
    action = args_obj.get("action") or args_obj.get("command") or args_obj.get("operation") or args_obj.get("op")
    target = args_obj.get("target") or args_obj.get("path") or args_obj.get("url") or args_obj.get("resource") or ""
    payload = args_obj.get("args") if isinstance(args_obj.get("args"), dict) else args_obj.get("payload") if isinstance(args_obj.get("payload"), dict) else {}
    # Confirmation fields are intentionally ignored. The model cannot authorize risk.
    args_obj.pop("confirm", None)
    args_obj.pop("confirmed", None)
    if not action and raw_name and raw_name != TOOL_NAME:
        # Some providers emit the inner action as the function name. Preserve it.
        action = raw_name
    return {
        "schema": CANONICAL_SCHEMA,
        "tool": TOOL_NAME,
        "action": str(action or "").strip(),
        "target": str(target or ""),
        "args": dict(payload or {}),
        "call_id": str(call_id or f"call_{uuid.uuid4().hex[:12]}"),
        "provider": provider,
        "profile": profile,
        "raw_function_name": raw_name or TOOL_NAME,
        "valid": bool(action),
    }


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on", "确认", "是", "已确认"}


def parse_tool_calls(payload: Any = None, text: str | None = None, provider: str | None = None, model: str | None = None, profile_id: str | None = None) -> Dict[str, Any]:
    sample = payload if payload is not None else text
    prof = detect_profile(provider=provider, model=model, payload=sample, preferred_profile=profile_id)
    profile = prof["profile_id"]
    call_style = prof.get("call_style")
    calls: List[Dict[str, Any]] = []
    if call_style == "openai_responses_function_call" or _payload_has_openai_responses_function_call(payload):
        for item in _extract_openai_responses_calls(payload):
            calls.append(_canonical_from_arguments(
                item.get("arguments") or {},
                item.get("call_id") or item.get("id"),
                prof.get("provider", ""),
                profile,
                item.get("name") or TOOL_NAME,
            ))
    elif call_style == "anthropic_tool_use" or _payload_has_anthropic_tool_use(payload):
        for block in (payload or {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(_canonical_from_arguments(block.get("input") or {}, block.get("id"), prof.get("provider", ""), profile, block.get("name") or TOOL_NAME))
    elif call_style == "xml_or_tag_tool_call" or (isinstance(text, str) and "<tool" in text.lower()):
        calls.extend(_parse_xml_tool_calls(text or str(payload or ""), prof.get("provider", ""), profile))
    elif _is_gemini_payload(payload):
        calls.extend(_parse_gemini_calls(payload, prof.get("provider", ""), profile))
    else:
        for tc in _extract_openai_tool_calls(payload):
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            calls.append(_canonical_from_arguments(fn.get("arguments") or tc.get("arguments") or {}, tc.get("id"), prof.get("provider", ""), profile, fn.get("name") or tc.get("name") or TOOL_NAME))
    return {
        "schema": "tiangong.v3.omni_body.model_adapter.parse_result.v1",
        "profile": prof,
        "count": len(calls),
        "calls": calls,
        "all_valid": all(c.get("valid") for c in calls) if calls else False,
    }


def _parse_xml_tool_calls(text: str, provider: str, profile: str) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    # <tool_call><name>omni_body</name><arguments>{...}</arguments></tool_call>
    for block in re.findall(r"<tool_call[^>]*>(.*?)</tool_call>", text, flags=re.I | re.S):
        name = _tag(block, "name") or TOOL_NAME
        args = _tag(block, "arguments") or _tag(block, "args") or "{}"
        call_id = _tag(block, "id") or None
        calls.append(_canonical_from_arguments(args, call_id, provider, profile, name))
    # <function name="omni_body">{...}</function> or <function=omni_body>{...}</function>
    for m in re.finditer(r"<function(?:\s+name=[\"']([^\"']+)[\"']|=([^>]+))?[^>]*>(.*?)</function>", text, flags=re.I | re.S):
        name = (m.group(1) or m.group(2) or TOOL_NAME).strip()
        args = m.group(3).strip()
        calls.append(_canonical_from_arguments(args, None, provider, profile, name))
    return calls


def _tag(block: str, name: str) -> str | None:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, flags=re.I | re.S)
    if not m:
        return None
    return m.group(1).strip()


def _is_gemini_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if "functionCall" in payload:
        return True
    try:
        parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return any(isinstance(p, dict) and "functionCall" in p for p in parts)
    except Exception:
        return False


def _parse_gemini_calls(payload: Dict[str, Any], provider: str, profile: str) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    if "functionCall" in payload:
        parts = [payload]
    else:
        parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", []) or []
    out = []
    for p in parts:
        fc = p.get("functionCall") if isinstance(p, dict) else None
        if not isinstance(fc, dict):
            continue
        out.append(_canonical_from_arguments(fc.get("args") or {}, fc.get("id"), provider, profile, fc.get("name") or TOOL_NAME))
    return out


def render_tool_result(result: Dict[str, Any], call_id: str | None = None, profile_id: str | None = None, provider: str | None = None, model: str | None = None) -> Dict[str, Any]:
    prof = detect_profile(provider=provider, model=model, preferred_profile=profile_id)
    style = prof.get("result_style")
    cid = call_id or result.get("call_id") or result.get("tool_call_id") or f"call_{uuid.uuid4().hex[:12]}"
    if style == "anthropic_tool_result":
        return {"profile": prof, "tool_result": {"type": "tool_result", "tool_use_id": cid, "content": json.dumps(result, ensure_ascii=False)}}
    if style == "openai_responses_function_call_output":
        return {"profile": prof, "tool_result": {"type": "function_call_output", "call_id": cid, "output": json.dumps(result, ensure_ascii=False)}}
    if style == "gemini_function_response":
        return {"profile": prof, "tool_result": {"functionResponse": {"name": TOOL_NAME, "response": result}}}
    if style == "xml_tool_result":
        return {"profile": prof, "tool_result": f"<tool_result id=\"{cid}\" name=\"{TOOL_NAME}\">{json.dumps(result, ensure_ascii=False)}</tool_result>"}
    return {"profile": prof, "tool_result": {"role": "tool", "tool_call_id": cid, "name": TOOL_NAME, "content": json.dumps(result, ensure_ascii=False)}}


def _fixture_for(profile_id: str) -> Any:
    if profile_id == "mimo_anthropic":
        return {"content": [{"type": "tool_use", "id": "toolu_1", "name": "omni_body", "input": {"action": "skill.route", "args": {"job": "做一份商业PPT"}}}]}
    if profile_id == "gpt_openai_responses":
        return {"id": "resp_1", "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_resp_1", "name": "omni_body", "arguments": "{\"action\":\"skill.route\",\"args\":{\"job\":\"做一份企业AI培训方案Word\"}}"}]}
    if profile_id == "minimax_raw_xml":
        return '<tool_call><id>mm_1</id><name>omni_body</name><arguments>{"action":"skill.route","args":{"job":"写会议纪要"}}</arguments></tool_call>'
    return {"choices": [{"message": {"tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "omni_body", "arguments": "{\"action\":\"skill.route\",\"args\":{\"job\":\"做一份企业AI培训方案Word\"}}"}}]}}]}


def roundtrip_test(profile_id: str | None = None, provider: str | None = None, model: str | None = None) -> Dict[str, Any]:
    profiles = _profiles()
    targets = [profile_id] if profile_id else list(profiles.keys())
    rows = []
    for pid in targets:
        if pid not in profiles:
            rows.append({"profile": pid, "ok": False, "error": "unknown profile"})
            continue
        fixture = _fixture_for(pid)
        parsed = parse_tool_calls(payload=fixture if isinstance(fixture, dict) else None, text=fixture if isinstance(fixture, str) else None, profile_id=pid)
        ok_parse = parsed["count"] >= 1 and parsed["all_valid"]
        fake_result = {"schema": "tiangong.v3.omni_body.v1", "ok": True, "zhuangtai": "wancheng", "action": parsed["calls"][0]["action"] if parsed["calls"] else "", "result": {"demo": True}}
        rendered = render_tool_result(fake_result, call_id=(parsed["calls"][0].get("call_id") if parsed["calls"] else None), profile_id=pid)
        rows.append({"profile": pid, "ok": bool(ok_parse and rendered.get("tool_result")), "parsed": parsed, "rendered": rendered})
    return {"schema": "tiangong.v3.omni_body.model_adapter.roundtrip.v1", "success": all(r["ok"] for r in rows), "profiles_tested": len(rows), "results": rows}


def handle_model_adapter_action(runtime: Any, op_id: str, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    if action == "model.adapter.info":
        doc = _load_profiles()
        return {"success": True, "result": {"schema": doc.get("schema"), "profile_count": len(doc.get("profiles", {})), "profiles": list(doc.get("profiles", {}).keys()), "canonical_schema": doc.get("internal_canonical_call_schema")}}
    if action == "model.adapter.list":
        doc = _load_profiles()
        provider = _norm(args.get("provider") or target)
        profiles = []
        for pid, meta in doc.get("profiles", {}).items():
            if provider and provider not in _norm(meta.get("provider")) and provider not in [_norm(x) for x in meta.get("aliases", [])]:
                continue
            row = dict(meta)
            row["profile_id"] = pid
            profiles.append(row)
        return {"success": True, "schema": doc.get("schema"), "count": len(profiles), "profiles": profiles, "result": {"profiles": profiles}}
    if action == "model.adapter.detect":
        payload = args.get("payload") or args.get("request") or args.get("response")
        prof = detect_profile(provider=args.get("provider") or target, model=args.get("model"), endpoint=args.get("endpoint"), payload=payload, preferred_profile=args.get("profile") or args.get("profile_id"))
        return {"success": True, "profile": prof, "result": {"profile": prof}, "evidence": {"profile_id": prof.get("profile_id"), "confidence": prof.get("confidence")}}
    if action == "model.adapter.render_tool_schema":
        rendered = render_tool_schema(profile_id=args.get("profile") or args.get("profile_id") or target, provider=args.get("provider"), model=args.get("model"), style=args.get("style"))
        return {"success": True, "result": rendered, "profile": rendered.get("profile")}
    if action == "model.adapter.parse_tool_call":
        parsed = parse_tool_calls(payload=args.get("payload") or args.get("message") or args.get("response"), text=args.get("text"), provider=args.get("provider") or target, model=args.get("model"), profile_id=args.get("profile") or args.get("profile_id"))
        return {"success": parsed.get("count", 0) > 0, "result": parsed, "evidence": {"count": parsed.get("count"), "all_valid": parsed.get("all_valid")}}
    if action == "model.adapter.render_tool_result":
        rendered = render_tool_result(args.get("result") or {}, call_id=args.get("call_id"), profile_id=args.get("profile") or args.get("profile_id") or target, provider=args.get("provider"), model=args.get("model"))
        return {"success": True, "result": rendered, "profile": rendered.get("profile")}
    if action == "model.adapter.roundtrip_test":
        result = roundtrip_test(profile_id=args.get("profile") or args.get("profile_id") or target, provider=args.get("provider"), model=args.get("model"))
        return {"success": bool(result.get("success")), "result": result, "evidence": {"profiles_tested": result.get("profiles_tested"), "success": result.get("success")}}
    return {"success": False, "message": f"Unknown model adapter action: {action}"}
