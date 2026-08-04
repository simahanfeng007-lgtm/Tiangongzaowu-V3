from __future__ import annotations

import argparse
import marshal
import os
import shutil
import tempfile
from pathlib import Path
from types import CodeType, FunctionType


CLASSIFIER_SOURCE = r'''
def _interaction_mode(current_request, attachments):
    """Classify the current turn before Skill routing.

    This combines the v3.3.2 command/meta distinction with the v3.7 work
    vocabulary.  It intentionally classifies the user's concrete request,
    never the presence of a Skill keyword by itself.
    """
    import re

    text = str(current_request or "").strip()
    lower = text.lower()
    compact = re.sub(r"\s+", "", lower)
    attachments = list(attachments or [])
    if not compact:
        return "chat"

    # v3.3.2 guard: capability/meta questions may mention Word, tools, writing,
    # etc. but do not authorize execution unless the user also gives a command.
    capability_question = bool(re.search(
        r"(?:你|她|模型|工具|系统).{0,10}(?:会不会|能不能|能否|可以吗|能吗|会吗|支持不支持|能做到吗|可以做到吗)",
        compact,
        re.IGNORECASE,
    )) or bool(re.search(
        r"(?:你|她|模型|工具|系统)(?:会|能|可以|支持).{0,12}(?:吗|么|不)$",
        compact,
        re.IGNORECASE,
    )) or any(marker in compact for marker in (
        "你会不会", "你会吗", "你能不能", "你能否", "你能吗", "你可以吗",
        "你可不可以", "她会不会", "她能不能", "模型会不会", "工具能不能",
        "支持不支持", "能不能做到", "能否做到", "可以做到吗", "能做吗", "会做吗",
    ))
    explicit_command = any(marker in compact for marker in (
        "帮我", "给我", "替我", "请你", "请帮", "麻烦", "把这个", "把它", "将这个",
        "直接", "现在", "马上", "立刻", "开始", "按照", "按这个", "根据", "发给我",
        "发我", "放桌面", "放到桌面", "保存到", "保存为", "跑一下", "查一下", "搜一下",
    ))
    if capability_question and not explicit_command:
        return "chat"

    # Conversation about a capability/topic is still chat unless a concrete
    # target, artifact, attachment, path or command is also present.
    conversational = any(marker in compact for marker in (
        "聊聊", "聊一聊", "讨论一下", "谈谈", "说说", "随便聊", "只是问问",
    ))

    create_actions = (
        "修复", "修改", "创建", "新建", "生成", "做", "写入", "写", "撰写", "创作", "编写",
        "制作", "保存", "安装", "打包", "删除", "清理", "整理", "移动", "复制", "重命名",
        "替换", "转换", "导出", "迁移", "部署", "实现", "融合", "升级", "追加", "覆盖",
    )
    inspect_actions = (
        "读取", "打开", "查看", "看看", "查查", "检查", "排查", "分析", "对比", "比较",
        "核对", "确认", "搜索", "查找", "搜一下", "查一下", "运行", "执行", "测试", "验证",
        "下载", "上传", "解压",
    )
    action_markers = create_actions + inspect_actions
    english_action = bool(re.search(
        r"\b(?:fix|repair|modify|edit|create|generate|write|save|install|build|package|delete|move|copy|rename|replace|migrate|deploy|implement|upgrade|run|execute|test|verify|read|open|inspect|analy[sz]e|compare|search|find|download|upload|extract)\b",
        lower,
        re.IGNORECASE,
    ))

    concrete_scope = any(marker in compact for marker in (
        "文件", "文档", "word", "docx", "ppt", "pptx", "excel", "xlsx", "表格", "pdf",
        "代码", "脚本", "项目", "工程", "程序", "网页", "网站", "桌面", "目录", "路径",
        "安装包", "压缩包", "图片", "截图", "视频", "音频", "附件", "链接", "微信",
        "日志", "聊天记录", "运行记录", "执行链", "上下文链", "后端", "前端", "数据库",
        "缓存", "快捷方式", "这个问题", "这个内容", "这个包", "这个程序",
    )) or bool(re.search(r"(?:[a-zA-Z]:\\|/[^\s]+|\.(?:txt|md|json|csv|docx?|xlsx?|pptx?|pdf|zip|py|js|ts|html|css|exe)\b)", text, re.IGNORECASE))

    delivery_intent = any(marker in compact for marker in (
        "发给我", "发我", "发送", "传给我", "给我发", "发到微信", "交付", "查收",
        "打包发", "压缩包", "下载给我", "把文件给我", "保存到", "保存为", "放桌面", "放到桌面",
    ))
    attachment_action = bool(attachments) and any(marker in compact for marker in (
        "修改", "编辑", "处理", "提取", "识别", "分析", "读取", "查看", "总结", "转换", "导出",
        "根据附件", "处理附件", "修改这个文件",
    ))
    diagnostic_scope = concrete_scope and any(marker in compact for marker in (
        "看看", "查查", "检查", "排查", "分析", "对比", "比较", "核对", "确认", "读取", "打开", "查看",
        "为什么", "什么原因", "哪里出问题", "怎么回事",
    ))
    office_or_artifact_action = concrete_scope and any(marker in compact for marker in action_markers)
    starts_as_command = bool(re.match(
        r"^(?:你)?(?:先|再|直接|现在|马上|立刻|请)?(?:帮我|给我|替我|把|将)?(?:修复|修改|创建|新建|生成|做|写入|写|撰写|创作|编写|制作|保存|安装|打包|删除|清理|整理|移动|复制|重命名|替换|转换|导出|迁移|部署|实现|融合|升级|追加|覆盖|读取|打开|查看|看看|查查|检查|排查|分析|对比|比较|核对|确认|搜索|查找|运行|执行|测试|验证|下载|上传|解压)",
        compact,
        re.IGNORECASE,
    ))
    requested_action = explicit_command and (any(marker in compact for marker in action_markers) or english_action)

    if conversational and not (explicit_command or concrete_scope or attachments):
        return "chat"
    if attachment_action or delivery_intent or diagnostic_scope or office_or_artifact_action or starts_as_command or requested_action:
        return "work"
    if english_action and (explicit_command or concrete_scope):
        return "work"
    return "chat"
'''


RELATED_SKILLS_SOURCE = r'''
def _related_skills(current_request, attachments, mode):
    """Select the relevant Skill and preload its bounded procedure.

    v3.3.2 loaded matching Skill text before the model turn; v3.7 provides the
    stronger scored router and execution loop.  Keep both properties here.
    """
    skills = []
    has_image = any(_attachment_is_image(item) for item in attachments)
    has_video = any(_attachment_is_video(item) for item in attachments)
    has_file = any(not _attachment_is_image(item) and not _attachment_is_video(item) for item in attachments)
    if has_image:
        skills.append(_visual_skill())
    if has_video:
        skills.append(_video_skill())
    if has_file:
        skills.append(_file_reading_skill())

    attachment_understanding_only = (has_image or has_video or has_file) and mode == "chat"
    if not attachment_understanding_only and mode == "work":
        try:
            from omni_body_skill.tools.skill_router import (
                SKILL_CATALOG,
                _route_result,
                _score_skill,
                _skill_get,
            )

            scored = []
            context = {"attachments": _attachment_manifest(attachments)}
            for skill in SKILL_CATALOG:
                score, reasons = _score_skill(skill, current_request, context)
                scored.append((score, skill, reasons))
            scored.sort(key=lambda item: item[0], reverse=True)
            if scored and scored[0][0] > 0:
                score, skill, reasons = scored[0]
                result = _route_result(skill, score, reasons, top_matches=[])
                card = result.get("result", {}).get("skill_card", {})
                if isinstance(card, dict):
                    card = dict(card)
                    loaded = _skill_get(None, card.get("id", ""), {"skill_id": card.get("id", "")})
                    payload = loaded.get("result", {}) if isinstance(loaded, dict) else {}
                    markdown = payload.get("markdown", "") if isinstance(payload, dict) else ""
                    card["procedure_loaded"] = bool(markdown)
                    card["procedure_markdown"] = str(markdown)[:6000]
                    card["procedure_source"] = payload.get("skill", {}).get("file", card.get("file", "")) if isinstance(payload, dict) else card.get("file", "")
                    skills.append(card)
        except Exception:
            pass
    return skills[:3]
'''


WORK_CONTRACT_SOURCE = r'''
def _work_contract(skills):
    loaded = sum(1 for item in (skills or []) if isinstance(item, dict) and item.get("procedure_loaded"))
    return (
        "【后端工作循环契约】\n"
        f"0. 后端已选择并预载相关 Skill 正文（已加载 {loaded} 个）；优先严格遵循【相关 Skill 展示】中的 "
        "procedure_markdown，不要重复调用 skill.route。仅当 procedure_loaded=false 时调用 skill.get/skill.read。\n"
        "1. 只使用万能工具 omni_body；每轮最多并行 2 个互不依赖的调用，有依赖时顺序执行。\n"
        "2. 按 Skill 声明的 starter_actions、production_actions、quality_gates、repair_actions、final_actions 推进。"
        "已有 docx.create、pptx.create、sheet.create 等原生 action 时，不得改用 Python、Shell 或手工 ZIP 拼装代替。\n"
        "3. 每轮读取真实工具结果后再决定下一步，执行 -> QC -> 修复 -> 再 QC；不得把 Skill 命中、文件存在或工具返回本身当作完成。\n"
        "4. 只有真实验收通过才结束循环；A0-A4 按当前权限自动执行，A5 等待用户签名授权。\n"
        "5. 最终回复必须引用实际交付路径、QC 证据和失败项；禁止宣称未被工具事实证明的结果。"
    )
'''


CASES = (
    ("你帮我在桌面写一个测试文件吧，word文档，内容是测试用", [], "work"),
    ("帮我做一个PPT", [], "work"),
    ("修复这个问题", [], "work"),
    ("你看看3.3.2和3.7的skill调用逻辑", [], "work"),
    (r"检查 D:\example\tiangongv3 的执行链", [], "work"),
    ("看看这个附件并总结", [{"name": "a.pdf"}], "work"),
    ("这张图是什么意思", [{"name": "a.png"}], "chat"),
    ("你会做Word吗", [], "chat"),
    ("你能写代码吗", [], "chat"),
    ("我们聊聊写小说", [], "chat"),
    ("晚上好呀", [], "chat"),
)


def compiled_function(source: str, name: str) -> CodeType:
    namespace: dict[str, object] = {}
    exec(compile(source, "backend_entry_patched_v2.py", "exec"), namespace)
    return namespace[name].__code__  # type: ignore[union-attr]


def classifier_code() -> CodeType:
    return compiled_function(CLASSIFIER_SOURCE, "_interaction_mode")


def replace_named_code(root: CodeType, name: str, replacement: CodeType) -> tuple[CodeType, int]:
    count = 0
    constants: list[object] = []
    for item in root.co_consts:
        if isinstance(item, CodeType):
            if item.co_name == name:
                constants.append(replacement)
                count += 1
            else:
                updated, nested_count = replace_named_code(item, name, replacement)
                constants.append(updated)
                count += nested_count
        else:
            constants.append(item)
    return root.replace(co_consts=tuple(constants)), count


def run_self_test(code: CodeType) -> None:
    classifier = FunctionType(code, {"__builtins__": __builtins__})
    failures = []
    for text, attachments, expected in CASES:
        actual = classifier(text, attachments)
        if actual != expected:
            failures.append((text, expected, actual))
    if failures:
        raise RuntimeError(f"classifier self-test failed: {failures!r}")


def patch_pyc(target: Path, backup: Path | None) -> None:
    raw = target.read_bytes()
    if len(raw) < 17:
        raise RuntimeError(f"invalid pyc: {target}")
    header, payload = raw[:16], raw[16:]
    root = marshal.loads(payload)
    if not isinstance(root, CodeType):
        raise RuntimeError("pyc payload is not a code object")

    replacements = {
        "_interaction_mode": classifier_code(),
        "_related_skills": compiled_function(RELATED_SKILLS_SOURCE, "_related_skills"),
        "_work_contract": compiled_function(WORK_CONTRACT_SOURCE, "_work_contract"),
    }
    run_self_test(replacements["_interaction_mode"])
    updated = root
    for name, replacement in replacements.items():
        updated, count = replace_named_code(updated, name, replacement)
        if count != 1:
            raise RuntimeError(f"expected exactly one {name}, found {count}")

    if backup is not None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)

    output = header + marshal.dumps(updated)
    with tempfile.NamedTemporaryFile(prefix=target.name + ".", suffix=".tmp", dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(output)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    # Read back and prove that the on-disk artifact contains the new function.
    check = marshal.loads(target.read_bytes()[16:])
    found: dict[str, list[CodeType]] = {name: [] for name in replacements}

    def collect(code: CodeType) -> None:
        for item in code.co_consts:
            if isinstance(item, CodeType):
                if item.co_name in found:
                    found[item.co_name].append(item)
                collect(item)

    collect(check)
    for name, items in found.items():
        if len(items) != 1:
            raise RuntimeError(f"patched artifact verification failed: {name} count={len(items)}")
    run_self_test(found["_interaction_mode"][0])


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch Tiangong backend Skill routing classifier in-place.")
    parser.add_argument("target", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    replacement = classifier_code()
    run_self_test(replacement)
    if args.self_test:
        print(f"SELF_TEST_OK cases={len(CASES)}")
        return 0
    patch_pyc(args.target.resolve(), args.backup.resolve() if args.backup else None)
    print(f"PATCH_OK target={args.target.resolve()} cases={len(CASES)}")
    if args.backup:
        print(f"BACKUP={args.backup.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
