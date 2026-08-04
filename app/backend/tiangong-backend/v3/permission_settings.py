"""Runtime permission settings and tool-boundary checks."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .path_resolver import (
    classify_path,
    resolve_path_text,
)
from .runtime_environment import collect_runtime_environment, runtime_environment_summary


SETTINGS_PATH = Path.home() / ".tiangong" / "v3" / "permission_settings.json"

PERMISSION_MODES = {
    "request_approval": "\u8bf7\u6c42\u6279\u51c6",
    "auto_approval": "\u66ff\u6211\u5ba1\u6279",
    "full_access": "\u5b8c\u5168\u8bbf\u95ee\u6743\u9650",
    "custom": "\u81ea\u5b9a\u4e49(config)",
}

DEFAULT_SETTINGS = {
    "permission_mode": "full_access",
    "filesystem_policy": "full_access",
    "network_policy": "enabled",
    "terminal_policy": "auto",
    "allow_roots": [],
    "confirm_roots": [],
    "deny_roots": [],
    "a5_blocked": True,
}

PATH_ARG_KEYS = {
    "path", "paths", "dir_path", "directory", "dir", "folder", "workdir", "cwd",
    "target", "target_path", "source", "src", "file", "file_path", "filepath",
    "destination", "dst", "output", "output_path", "output_name", "save_as",
    "image_path", "first_frame_image", "last_frame_image", "project_dir", "archive",
}

MODEL_AUTHORITY_FIELDS = {
    "confirm", "confirmed", "confirmation", "confirmation_id", "confirmation_sha256",
    "allow_shell", "allow_python", "allow_absolute_paths",
    "__capability_grant", "__runtime",
    # D-10：模型不得自报风险档；风险档只来自工具注册表声明。
    "risk",
    # D-08：模型/router 只能提议，不得断言 provenance/授权事实。
    "source_ref", "source_refs", "source_type", "provenance",
    "authorization", "authorized",
}

NETWORK_ACTIONS = {
    "browser.search_web", "browser_search_web", "browser.open", "browser.chrome.goto",
    "browser.chrome.open", "browser.chrome.download", "browser.download", "http.get", "http_get",
    "web.search", "web_search", "search_web", "web.read", "web.fetch", "web.open",
    "web_readability_extract", "web.readability_extract", "read_url", "fetch_url",
    "web.download", "download_url", "web.image_search", "image_search", "search_image",
}

READ_ACTIONS = {
    "skill.route", "skill.get", "skill.read", "skill.list", "skill.step.check", "skill.progress.report",
    "system.capabilities", "system.health", "system.app_registry", "system.action_schema",
    "file.list", "file.read", "file.search", "file.hash", "code.read", "sheet.read",
    "pdf.extract_text", "image.info", "video.info", "rollback.list",
}
WRITE_ACTIONS = {
    "file.write", "file.append", "file.copy", "file.rename", "file.mkdir",
    "zip.create", "zip.extract", "code.write", "code.patch_replace",
    "docx.create", "pptx.create", "sheet.create", "mindmap.create",
    "pdf.create_from_text", "image.create_canvas", "image.resize", "image.crop",
    "image.rotate", "image.add_text", "image.compose", "image.convert",
    "audio.tone", "audio.trim", "audio.concat", "video.cut",
    "video.extract_audio", "video.add_audio", "video.slideshow",
    "deliverable.package", "preview.generate", "template.apply",
}
DESTRUCTIVE_ACTIONS = {"file.delete_to_trash", "file.move", "rollback.apply"}
TERMINAL_ACTIONS = {"python.run", "quality.run_tests", "shell.run"}

A5_COMMAND_PATTERNS = (
    r"\bformat\s+[a-z]:",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\bbootrec\b",
    r"\breagentc\b",
    r"\bmanage-bde\b",
    r"\bshutdown\s+/(?:s|r|p|h)\b",
    r"\breg\s+delete\s+HKLM\\",
    r"\bremove-item\b.*\b(?:c:\\|[a-z]:\\)\s*(?:-|/)?recurse\b",
    r"\brm\s+-rf\s+/(?:\s|$)",
)

RUNTIME_CONTEXT_KEYWORDS = (
    "\u684c\u9762",
    "\u4e0b\u8f7d",
    "\u6587\u6863",
    "\u56fe\u7247",
    "\u89c6\u9891",
    "\u97f3\u4e50",
    "\u76d8",
    "\u8def\u5f84",
    "\u6587\u4ef6",
    "\u6587\u4ef6\u5939",
    "\u76ee\u5f55",
    "\u6839\u76ee\u5f55",
    "\u5220\u9664",
    "\u5b89\u88c5",
    "\u6743\u9650",
    "\u6743\u9650\u6a21\u5f0f",
    "\u8bbf\u95ee\u6743\u9650",
    "\u9ad8\u5371",
    "a5",
    "\u547d\u4ee4",
    "\u7ec8\u7aef",
    "desktop",
    "downloads",
    "documents",
    "drive",
    "path",
    "file",
    "folder",
    "directory",
    "permission",
    "permission_mode",
    "policy",
    "root",
    "terminal",
    "install",
)


def _load_raw() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_mode(value: Any) -> str:
    text = str(value or "").strip()
    aliases = {
        "readonly": "request_approval",
        "workspace_write": "request_approval",
        "workspace_full": "full_access",
        "full": "full_access",
        "danger-full-access": "full_access",
        "never": "full_access",
    }
    text = aliases.get(text, text)
    return text if text in PERMISSION_MODES else DEFAULT_SETTINGS["permission_mode"]


def _normalize_roots(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    roots = []
    for item in value:
        raw = str(item or "").strip()
        if not raw:
            continue
        try:
            roots.append(str(Path(os.path.expandvars(raw)).expanduser().resolve(strict=False)))
        except Exception:
            roots.append(raw)
    return roots


def duqu_permission_settings() -> dict[str, Any]:
    raw = _load_raw()
    merged = {**DEFAULT_SETTINGS, **raw}
    merged["permission_mode"] = _normalize_mode(merged.get("permission_mode"))
    merged["mode_label"] = PERMISSION_MODES[merged["permission_mode"]]
    merged["allow_roots"] = _normalize_roots(merged.get("allow_roots"))
    merged["confirm_roots"] = _normalize_roots(merged.get("confirm_roots"))
    merged["deny_roots"] = _normalize_roots(merged.get("deny_roots"))
    merged["network_policy"] = "disabled" if str(merged.get("network_policy")).lower() == "disabled" else "enabled"
    merged["terminal_policy"] = "ask" if str(merged.get("terminal_policy")).lower() == "ask" else "auto"
    merged["a5_blocked"] = bool(merged.get("a5_blocked", True))
    merged["ok"] = True
    merged["settings_path"] = str(SETTINGS_PATH)
    merged["source"] = "configured" if SETTINGS_PATH.exists() else "default"
    return merged


def baocun_permission_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    current = duqu_permission_settings()
    for key in (
        "permission_mode",
        "filesystem_policy",
        "network_policy",
        "terminal_policy",
        "allow_roots",
        "confirm_roots",
        "deny_roots",
        "a5_blocked",
    ):
        if key in payload:
            current[key] = payload[key]
    current["permission_mode"] = _normalize_mode(current.get("permission_mode"))
    current["allow_roots"] = _normalize_roots(current.get("allow_roots"))
    current["confirm_roots"] = _normalize_roots(current.get("confirm_roots"))
    current["deny_roots"] = _normalize_roots(current.get("deny_roots"))
    current["updated_at"] = int(time.time())
    save_data = {k: current[k] for k in DEFAULT_SETTINGS.keys() if k in current}
    save_data["updated_at"] = current["updated_at"]
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return duqu_permission_settings()


def permission_status(*, refresh: bool = False) -> dict[str, Any]:
    settings = duqu_permission_settings()
    env = runtime_environment_summary(refresh=refresh)
    return {
        "ok": True,
        "schema": "tiangong.v3.permission_status.v1",
        **settings,
        "runtime": env,
        "home": env.get("home") or "",
        "desktop": env.get("desktop") or "",
        "downloads": env.get("downloads") or "",
        "documents": env.get("documents") or "",
        "workspace": env.get("workspace") or "",
        "drive_roots": env.get("drive_roots") or [],
    }


def runtime_context_needed(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(item.lower() in lowered for item in RUNTIME_CONTEXT_KEYWORDS)


def build_runtime_context_prompt(message: str = "", *, force: bool = False) -> str:
    status = permission_status()
    # 常驻边界（不依赖关键词触发，保持简短防 token 挤压）：
    # 工作区边界 / 用户指定直通 / 区外写需确认 / 禁区。
    policy_lines = [
        "[\u8def\u5f84\u89c4\u5219]",
        f"- \u5de5\u4f5c\u533a: {status.get('workspace') or ''}\uff1b\u9ed8\u8ba4\u53ea\u5728\u5de5\u4f5c\u533a\u5185\u8bfb\u5199\uff0c\u7edd\u5bf9\u8def\u5f84\u539f\u6837\u4f20\u9012\u4e0d\u505a\u6539\u5199\u3002",
        "- \u7528\u6237\u672c\u8f6e\u6d88\u606f\u91cc\u660e\u786e\u7ed9\u51fa\u7684\u8def\u5f84\uff08\u7edd\u5bf9\u8def\u5f84/\u76d8\u7b26/\u684c\u9762\u7b49\u522b\u540d/\u6587\u4ef6\u540d\uff09\u89c6\u4e3a\u7528\u6237\u6307\u5b9a\uff0c\u53ef\u76f4\u901a\u3002",
        "- \u5de5\u4f5c\u533a\u5916\u4e14\u7528\u6237\u672a\u6307\u5b9a\u7684\u5199\u5165\u5fc5\u987b\u5148\u5411\u7528\u6237\u786e\u8ba4\uff1b\u51ed\u636e\u76ee\u5f55\uff08.ssh/.aws/.gnupg\uff09\u4e0e\u4efb\u4f55 .env \u6587\u4ef6\u8bfb\u5199\u7686\u62d2\u3002",
    ]
    if not force and not runtime_context_needed(message):
        return "\n".join(policy_lines)
    lowered_message = str(message or "").lower()
    runtime = status.get("runtime") if isinstance(status.get("runtime"), dict) else {}
    user = runtime.get("user") if isinstance(runtime.get("user"), dict) else {}
    drives = ", ".join(status.get("drive_roots") or [])
    mode_label = status.get("mode_label") or status.get("permission_mode") or ""
    mode_value = status.get("permission_mode") or ""
    mode_text = mode_label if not mode_value or mode_value == mode_label else f"{mode_label} ({mode_value})"
    lines = [
        "[\u8fd0\u884c\u73af\u5883\u4e8b\u5b9e]",
        f"- \u5f53\u524d\u7528\u6237: {user.get('whoami') or user.get('username') or ''}",
        f"- \u7528\u6237\u76ee\u5f55: {status.get('home') or ''}",
        f"- \u684c\u9762: {status.get('desktop') or ''}",
        f"- \u4e0b\u8f7d: {status.get('downloads') or ''}",
        f"- \u6587\u6863: {status.get('documents') or ''}",
        f"- \u5de5\u4f5c\u533a: {status.get('workspace') or ''}",
        f"- \u53ef\u7528\u76d8: {drives}",
        f"- \u6743\u9650\u6a21\u5f0f: {mode_text}",
        "- \u8def\u5f84\u89e3\u6790: \u4e0d\u731c\u7528\u6237\u76ee\u5f55\u6216\u76d8\u7b26\uff1b\u6d89\u53ca\u8def\u5f84\u65f6\u5148\u6309\u8fd0\u884c\u65f6\u4e8b\u5b9e\u89e3\u6790\u3002",
        "- \u6743\u9650\u8fb9\u754c: full_access/\u5b8c\u5168\u8bbf\u95ee\u6743\u9650\u53ea\u8868\u793a\u666e\u901a\u8bfb\u5199\u548c\u5de5\u5177\u8c03\u7528\u53ef\u81ea\u52a8\u653e\u884c\uff1bA5\u3001\u9ad8\u5371\u3001\u7cfb\u7edf\u6839\u76ee\u5f55\u6216\u76d8\u7b26\u6839\u76ee\u5f55\u7684\u7834\u574f\u6027\u64cd\u4f5c\u4ecd\u5fc5\u987b\u963b\u65ad\uff0c\u4e0d\u56e0 full_access \u653e\u884c\u3002",
        *policy_lines[1:],
    ]
    if "\u684c\u9762" in lowered_message or "desktop" in lowered_message:
        lines.append(
            f"- \u684c\u9762\u76ee\u6807\u89c4\u5219: \u5f53\u7528\u6237\u8bf4\u201c\u684c\u9762\u201d\u3001\u201c\u64cd\u4f5c\u684c\u9762\u201d\u6216\u201c\u684c\u9762\u6839\u8def\u5f84\u201d\u65f6\uff0c\u76ee\u6807\u6839\u8def\u5f84\u5fc5\u987b\u662f {status.get('desktop') or ''}\uff0c\u4e0d\u8981\u9000\u56de\u7528\u6237\u76ee\u5f55\u3002"
        )
    return "\n".join(lines)


def _risk_rank(value: Any) -> int:
    # D-10：旧风险词表（DI/ZHONG/GAO/YANZHONG）回退已下线。只接受注册表 A0-A5
    # 声明；任何无法识别的输入一律按 A4 上限处理（fail-closed），deny 语义不变。
    text = str(value or "").strip().upper()
    if re.match(r"^A[0-5]$", text):
        return int(text[1])
    return 4


def _action_kind(tool_name: str, args: dict[str, Any]) -> str:
    name = str(tool_name or "").strip()
    if name != "omni_body":
        return "execute"
    action = str((args or {}).get("action") or "").strip().lower()
    if action in NETWORK_ACTIONS or action.startswith("browser."):
        return "network"
    if action in TERMINAL_ACTIONS:
        return "terminal"
    if action in DESTRUCTIVE_ACTIONS:
        return "destructive"
    if action in WRITE_ACTIONS:
        return "write"
    if action in READ_ACTIONS:
        return "read"
    if action.startswith(("file.", "code.", "docx.", "pptx.", "sheet.", "image.", "video.", "audio.", "zip.")):
        return "write"
    return "execute"


def _is_abs_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", text):
        return True
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    try:
        return Path(text).expanduser().is_absolute()
    except Exception:
        return False


def _is_url(value: str) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://", "data:", "file://"))


def _rewrite_paths(tool_name: str, args: dict[str, Any], env: dict[str, Any], user_message: str = "") -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    # 路径一律保持模型给出的原样下发：绝对路径按绝对路径走，相对路径按工作区
    # 解析（网关对相对路径的解析一致）。唯一的改写是把"桌面/D盘"这类用户面向
    # 的别名解析成真实路径——不这么做执行层会落错目录。不再存在任何静默重映射。
    rewritten = json.loads(json.dumps(args or {}, ensure_ascii=False, default=str))
    resolved_items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    def visit(container: Any, location: str = "") -> None:
        if isinstance(container, dict):
            for key, value in list(container.items()):
                key_text = str(key)
                child_location = f"{location}.{key_text}" if location else key_text
                if isinstance(value, (dict, list)):
                    visit(value, child_location)
                    continue
                if key_text.casefold() not in PATH_ARG_KEYS:
                    continue
                if not isinstance(value, str) or not value.strip() or _is_url(value):
                    continue
                result = resolve_path_text(value, intent=_action_kind(tool_name, args), env=env)
                if not result.get("ok"):
                    errors.append({"key": child_location, **result})
                    continue
                resolved_items.append({"key": child_location, **result})
                source = str(result.get("source") or "")
                if source in {"known_folder", "drive"}:
                    container[key] = result.get("resolved_path") or value
        elif isinstance(container, list):
            for index, value in enumerate(container):
                visit(value, f"{location}[{index}]")

    visit(rewritten)
    return rewritten, resolved_items, errors


def _find_model_authority_field(value: Any, location: str = "args") -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            current = f"{location}.{key_text}"
            if key_text in MODEL_AUTHORITY_FIELDS or key_text.startswith("__"):
                return current
            nested = _find_model_authority_field(item, current)
            if nested:
                return nested
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nested = _find_model_authority_field(item, f"{location}[{index}]")
            if nested:
                return nested
    return ""


def _command_is_a5(command: str) -> bool:
    lowered = str(command or "").lower()
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in A5_COMMAND_PATTERNS)


def _decision_payload(status: str, reason: str, *, tool_name: str, risk: str, action: str, details: dict[str, Any] | None = None, human: str = "") -> dict[str, Any]:
    details = details if isinstance(details, dict) else {}
    if status == "allow":
        return {"ok": True, "allowed": True, "status": status, "reason": reason, "tool_name": tool_name, "risk": risk, "action": action, **details}
    return {
        "ok": False,
        "allowed": False,
        "denied": True,
        "status": "denied",
        "cuowu": human or f"[POLICY_DENIED] {reason}",
        "reason": reason,
        "tool_name": tool_name,
        "risk": risk,
        "action": action,
        **details,
    }


def check_tool_permission(tool_name: str, tool_args: dict[str, Any] | None, yingshe: Any = None, user_message: str = "") -> dict[str, Any]:
    args = tool_args if isinstance(tool_args, dict) else {}
    settings = duqu_permission_settings()
    authority_field = _find_model_authority_field(args)
    if authority_field:
        return _decision_payload(
            "deny",
            f"model supplied authority field is forbidden: {authority_field}",
            tool_name=tool_name,
            risk="A5",
            action=_action_kind(tool_name, args),
            details={"permission_mode": settings.get("permission_mode")},
        )
    env = collect_runtime_environment()
    rewritten, paths, path_errors = _rewrite_paths(tool_name, args, env, user_message)
    # D-10：风险档只取工具注册表声明（yingshe.fengxian_dengji）；模型自报
    # risk 通道已删除——模型参数里的 "risk" 键在上面的 authority 字段检查中
    # 直接拒绝。取不到注册声明时按 A4 上限处理，不再缺省 A1。
    risk = str(getattr(yingshe, "fengxian_dengji", "") or "A4").upper()
    rank = _risk_rank(risk)
    action = _action_kind(tool_name, rewritten)
    details = {
        "permission_mode": settings.get("permission_mode"),
        "mode_label": settings.get("mode_label"),
        "rewritten_args": rewritten,
        "resolved_paths": paths,
        "path_errors": path_errors,
    }

    if path_errors:
        return _decision_payload("deny", path_errors[0].get("error") or "path_resolution_failed", tool_name=tool_name, risk=risk, action=action, details=details)
    if settings.get("a5_blocked", True) and rank >= 5:
        return _decision_payload("deny", "A5 action is blocked", tool_name=tool_name, risk=risk, action=action, details=details)
    if action == "terminal" and _command_is_a5(str(rewritten.get("command") or "")):
        return _decision_payload("deny", "A5 terminal command is blocked", tool_name=tool_name, risk="A5", action=action, details=details)

    # The legacy confirmation endpoint is retired.  There is therefore one
    # hard authorization boundary for execution: A5 above.  A1-A4 host paths,
    # terminal work and network work proceed continuously in every UI mode;
    # the gateway independently recomputes path/credential/root impact and
    # rejects anything that is actually A5.  Keeping old workspace/confirm
    # branches active here created confirmation cards that could never be
    # approved and made the UI's "full access" setting untrue.
    return _decision_payload(
        "allow",
        "A1-A4 continuous execution permitted",
        tool_name=tool_name,
        risk=risk,
        action=action,
        details=details,
    )

def policy_check_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    tool_name = str(payload.get("tool_name") or payload.get("name") or "")
    tool_args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else payload.get("args")
    if not isinstance(tool_args, dict):
        tool_args = {}
    return check_tool_permission(tool_name, tool_args, None, str(payload.get("user_message") or ""))
