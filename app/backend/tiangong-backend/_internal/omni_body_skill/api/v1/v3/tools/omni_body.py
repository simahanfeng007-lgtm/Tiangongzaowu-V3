from __future__ import annotations

import json
import hashlib
import importlib
import importlib.util
import os
import sys
import threading
from pathlib import Path
from typing import Any

TOOL_SCHEMA = "tiangong.v3.omni_body.v1"
TOOL_NAME = "omni_body"
_RUNTIME_IMPORT_LOCK = threading.RLock()
_PINNED_SKILL_ROOT: Path | None = None
_LIFE_ACTIVITY_QUERY_PROVIDER: Any = None
_BODY_STATE_QUERY_PROVIDER: Any = None
_LEARNING_INGEST_PROVIDER: Any = None

TOOL_DESCRIPTION: dict[str, Any] = {
    "name": "omni_body",
    "description": "统一身体工具入口 / 应用能力总线 / Skill分发器 / 专业应用桥接层 / 模型协议适配层。必须传 action；target 与 args 按动作需要提供，宿主会将缺失值规范化为空字符串和空对象；可用 skill.route/skill.get/skill.read 返回模型可执行Skill，也可执行文件、文档、表格、图片、音视频、质量门、打包、专业应用桥接。learning.ingest 只有在宿主提供验证 token 时才可创建待确认学习卡；该动作不会编译、激活或发布工具。它是工具，不是智能体；不跑隐藏工作流。",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "minLength": 1,
                "description": "明确动作名，如 life.body.state.query / life.activity.query / skill.route / skill.get / skill.read / file.read / docx.create / pptx.create / pptx.read / qc.ppt.delivery_check / app.adapter.health / deliverable.package / learning.ingest"
            },
            "target": {
                "type": "string",
                "description": "主操作对象路径、资源ID、应用对象或输出路径"
            },
            "args": {
                "type": "object",
                "description": "动作专用参数；不得用 goal 代替 action"
            },
        },
        "required": ["action"],
        "additionalProperties": False
    },
    "risk": "A4",
    "toolKind": "executable",
    "effect": "execute",
    "planOnly": False,
}

# Some v3 loaders inspect TOOL_SPEC rather than TOOL_DESCRIPTION.
TOOL_SPEC = TOOL_DESCRIPTION


def _find_skill_root() -> Path | None:
    """Keep this loaded wrapper and its verifier on one source directory.

    The host selects a new wrapper for a new source generation; changing a
    default environment variable must not hot-switch an existing wrapper.
    This path pin is not a source-byte attestation or publication approval.
    """
    global _PINNED_SKILL_ROOT
    with _RUNTIME_IMPORT_LOCK:
        if _PINNED_SKILL_ROOT is not None:
            root = _PINNED_SKILL_ROOT
            if root.resolve() == root and (root / "tools" / "omni_body_tool.py").is_file():
                return root
            return None  # A missing pinned version never falls back to another.
        # A deployed wrapper belongs to its enclosing skill package even when
        # the host's default already names the next version. Standalone legacy
        # wrappers may use the explicit host/user-root fallback once.
        candidates = list(Path(__file__).resolve().parents)
        env_root = os.environ.get("TIANGONG_OMNI_BODY_ROOT")
        if env_root:
            candidates.append(Path(env_root).expanduser())
        if str(os.environ.get("TIANGONG_OMNI_BODY_ALLOW_USER_ROOT") or "").strip().lower() in {"1", "true", "yes", "on"}:
            candidates.append(Path.home() / ".tiangong" / "v3" / "omni_body_skill")
        for candidate in candidates:
            root = candidate.resolve()
            if (root / "tools" / "omni_body_tool.py").is_file():
                _PINNED_SKILL_ROOT = root
                return root
        return None


def _import_runtime_unlocked() -> tuple[Any | None, Any | None, str | None]:
    root = _find_skill_root()
    if root is None:
        return (None, None, "[IMPORT_ERROR] omni_body skill root not found")
    try:
        package_dir = root / "tools"
        module_path = package_dir / "omni_body_tool.py"
        package_name = "_tiangong_omni_" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
        if package_name not in sys.modules:
            package_init = root / "__init__.py"
            if not package_init.is_file():
                raise ImportError("omni_body root package is missing __init__.py")
            package_spec = importlib.util.spec_from_file_location(
                package_name,
                package_init,
                submodule_search_locations=[str(root)],
            )
            if package_spec is None or package_spec.loader is None:
                raise ImportError("cannot create exact omni_body package spec")
            package = importlib.util.module_from_spec(package_spec)
            sys.modules[package_name] = package
            package_spec.loader.exec_module(package)
        module_name = f"{package_name}.tools.omni_body_tool"
        module = sys.modules.get(module_name)
        if module is None:
            # Import the child through the package machinery so ``tools`` is
            # initialized before ``omni_body_tool``.  Inserting the child into
            # ``sys.modules`` first makes tools/__init__.py observe a partially
            # initialized module and breaks the package's lazy root exports.
            module = importlib.import_module(module_name)
        loaded_path = Path(str(getattr(module, "__file__", ""))).resolve(strict=False)
        if loaded_path != module_path.resolve(strict=False):
            raise ImportError(f"omni_body runtime source mismatch: {loaded_path}")
        setter = getattr(module, "set_life_activity_query_provider", None)
        if callable(setter):
            setter(_LIFE_ACTIVITY_QUERY_PROVIDER)
        body_state_setter = getattr(module, "set_body_state_query_provider", None)
        if callable(body_state_setter):
            body_state_setter(_BODY_STATE_QUERY_PROVIDER)
        learning_setter = getattr(module, "set_learning_ingest_provider", None)
        if callable(learning_setter):
            learning_setter(_LEARNING_INGEST_PROVIDER)
        return (module.BodyRuntime, module.BodyRuntimeConfig, None)
    except Exception as exc:
        return (None, None, f"[IMPORT_ERROR] cannot import exact omni_body runtime: {exc}")


def _import_runtime() -> tuple[Any | None, Any | None, str | None]:
    with _RUNTIME_IMPORT_LOCK:
        return _import_runtime_unlocked()


def set_life_activity_query_provider(provider: Any) -> None:
    """Bind omni_body to the sole in-process Life authority."""

    global _LIFE_ACTIVITY_QUERY_PROVIDER
    if provider is not None and not callable(provider):
        raise TypeError("life activity query provider must be callable")
    with _RUNTIME_IMPORT_LOCK:
        _LIFE_ACTIVITY_QUERY_PROVIDER = provider
        runtime_class, _config_class, error = _import_runtime_unlocked()
        if runtime_class is None:
            raise RuntimeError(error or "omni_body runtime unavailable")


def set_body_state_query_provider(provider: Any) -> None:
    """Bind omni_body to Total Gateway's combined Life/body read authority."""

    global _BODY_STATE_QUERY_PROVIDER
    if provider is not None and not callable(provider):
        raise TypeError("body state query provider must be callable")
    with _RUNTIME_IMPORT_LOCK:
        _BODY_STATE_QUERY_PROVIDER = provider
        runtime_class, _config_class, error = _import_runtime_unlocked()
        if runtime_class is None:
            raise RuntimeError(error or "omni_body runtime unavailable")


def set_learning_ingest_provider(provider: Any) -> None:
    """Bind omni_body to the Gateway-owned pending-only learning path."""

    global _LEARNING_INGEST_PROVIDER
    if provider is not None and not callable(provider):
        raise TypeError("learning ingest provider must be callable")
    with _RUNTIME_IMPORT_LOCK:
        _LEARNING_INGEST_PROVIDER = provider
        runtime_class, _config_class, error = _import_runtime_unlocked()
        if runtime_class is None:
            raise RuntimeError(error or "omni_body runtime unavailable")


def _verify_capability_unlocked(grant, *, action, target, payload, workspace, runtime_meta):
    root = _find_skill_root()
    if root is None:
        raise ValueError("omni_body capability verifier root not found")
    module_path = root / "tools" / "omni_capability.py"
    module_name = "_tiangong_omni_capability_" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load exact capability verifier")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    loaded_path = Path(str(getattr(module, "__file__", ""))).resolve(strict=False)
    if loaded_path != module_path.resolve(strict=False):
        raise ImportError(f"omni_body capability verifier source mismatch: {loaded_path}")
    return module.verify_capability_grant(
        grant,
        action=action,
        target=target,
        args=payload,
        workspace=workspace,
        runtime_meta=runtime_meta,
    )


def _verify_capability(grant, *, action, target, payload, workspace, runtime_meta):
    with _RUNTIME_IMPORT_LOCK:
        return _verify_capability_unlocked(
            grant,
            action=action,
            target=target,
            payload=payload,
            workspace=workspace,
            runtime_meta=runtime_meta,
        )


def _bad_args(message: str) -> dict[str, Any]:
    return {
        "schema": TOOL_SCHEMA,
        "ok": False,
        "zhuangtai": "cuowu",
        "gongju": TOOL_NAME,
        "cuowu": message,
    }


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是", "确认", "已确认"}


def _extract_evidence(result: dict[str, Any], target: str, workspace: str = "") -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if isinstance(result.get("evidence"), dict):
        raw = dict(result.get("evidence") or {})
    elif isinstance(result.get("output"), dict):
        raw = dict(result.get("output") or {})
    elif isinstance(result.get("destination"), dict):
        raw = dict(result.get("destination") or {})
    elif isinstance(result.get("source"), dict):
        raw = dict(result.get("source") or {})

    path = raw.get("path") or raw.get("rel_path") or result.get("path") or result.get("target") or target
    original_path = str(path or "")
    if original_path and workspace and not original_path.startswith(("http://", "https://")):
        try:
            candidate = Path(original_path).expanduser()
            if not candidate.is_absolute():
                candidate = (Path(workspace).expanduser() / candidate).resolve(strict=False)
            path = str(candidate)
        except Exception:
            path = original_path
    size = raw.get("bytes")
    if size is None:
        size = raw.get("size_bytes")
    if size is None:
        size = raw.get("size")
    evidence: dict[str, Any] = {
        "path": str(path or ""),
        "rel_path": original_path if original_path and original_path != str(path or "") else "",
        "exists": bool(raw.get("exists", result.get("exists", False))),
        "sha256": raw.get("sha256") or "",
        "bytes": int(size or 0) if isinstance(size, (int, float, str)) and str(size).isdigit() else 0,
    }
    # Keep structured evidence for frontend/audit without forcing model to read the full result.
    if raw:
        evidence["raw"] = raw
    for key in ("op_id", "action", "risk_level", "elapsed_seconds"):
        if key in result:
            evidence[key] = result[key]
    if "requires_adapter" in result:
        evidence["requires_adapter"] = result.get("requires_adapter")
    if "snapshots" in result:
        evidence["rollback_available"] = True
    return evidence


def _error_code_from_result(result: dict[str, Any]) -> str:
    if result.get("requires_adapter"):
        return f"[ADAPTER_REQUIRED] {result.get('requires_adapter')}: {result.get('message') or result.get('reason') or ''}".strip()
    if result.get("needs_confirmation"):
        return "[POLICY_ERROR] A0-A4 actions must not request confirmation; A5 is rejected before execution"
    if result.get("error_type"):
        return f"[{result.get('error_type')}] {result.get('message') or ''}".strip()
    if result.get("message"):
        return str(result.get("message"))
    return "[EXECUTION_FAILED] action returned failure"


def _host_verified_learning_intent() -> bool:
    """Read backend-only request authority without accepting model arguments."""

    try:
        from v3.run_context import current_run_context  # type: ignore

        return bool(current_run_context().learning_intent_verified)
    except Exception:
        return False


def run_omni_body(args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = dict(args or {})
    runtime_meta = args.pop("__runtime", {}) if isinstance(args.get("__runtime"), dict) else {}
    capability_grant = args.pop("__capability_grant", {}) if isinstance(args.get("__capability_grant"), dict) else {}
    action = str(args.get("action") or "").strip()
    target = str(args.get("target") or args.get("path") or "").strip()
    payload = args.get("args") if isinstance(args.get("args"), dict) else {}
    payload = dict(payload or {})
    # Confirmation is authority, not model input.  Only a verified signed
    # capability grant may inject it below.
    payload.pop("confirmed", None)

    if not action:
        return _bad_args("[BAD_ARGS] missing action")
    if not capability_grant:
        return _bad_args("[CAPABILITY_REQUIRED] Gateway-signed Omni capability grant is required")

    BodyRuntime, BodyRuntimeConfig, import_error = _import_runtime()
    if import_error or BodyRuntime is None or BodyRuntimeConfig is None:
        out = _bad_args(import_error or "[IMPORT_ERROR] runtime unavailable")
        out.update({"action": action, "target": target})
        return out

    workspace = str(args.get("workspace") or os.environ.get("TIANGONG_OMNI_BODY_WORKSPACE") or os.getcwd())
    verified_grant: dict[str, Any] = {}
    if capability_grant:
        try:
            verified_grant = _verify_capability(
                capability_grant,
                action=action,
                target=target,
                payload=payload,
                workspace=workspace,
                runtime_meta=runtime_meta,
            )
        except Exception as exc:
            return _bad_args(f"[CAPABILITY_REJECTED] {exc}")
    allow_shell = verified_grant.get("allow_shell") is True
    allow_python = verified_grant.get("allow_python") is True
    allow_absolute_paths = verified_grant.get("allow_absolute_paths") is True
    require_confirmation_for_a4 = False
    # A0-A4 execute without confirmation. A5 never reaches Omni Body.
    payload.pop("confirmed", None)

    try:
        rt = BodyRuntime(BodyRuntimeConfig(
            workspace=workspace,
            allow_shell=allow_shell,
            allow_python=allow_python,
            allow_absolute_paths=allow_absolute_paths,
            require_confirmation_for_a4=require_confirmation_for_a4,
            fact_kernel_enabled=bool(runtime_meta.get("fact_kernel_enabled", True)),
            fact_ledger_root=str(runtime_meta.get("ledger_root") or ""),
            run_id=str(runtime_meta.get("run_id") or ""),
            request_id=str(runtime_meta.get("request_id") or ""),
            generation=(runtime_meta.get("generation") if type(runtime_meta.get("generation")) is int else -1),
            principal_scope_hash=str(runtime_meta.get("principal_scope_hash") or ""),
            skill_activation_sha256=str(runtime_meta.get("skill_activation_sha256") or ""),
            gateway_url=str(runtime_meta.get("gateway_url") or ""),
            session_id=str(runtime_meta.get("session_id") or ""),
            step_id=str(runtime_meta.get("step_id") or ""),
            task_node_id=str(runtime_meta.get("task_node_id") or ""),
            host_verified_learning_intent=_host_verified_learning_intent(),
            # D-21：网关随签发下发的用户指定路径根（用户原文提取，模型不可自报）。
            user_path_roots=[str(item) for item in (runtime_meta.get("user_path_roots") or []) if str(item or "").strip()],
        ))
        result = rt.run(action=action, target=target, args=payload)
    except Exception as exc:
        return {
            "schema": TOOL_SCHEMA,
            "ok": False,
            "zhuangtai": "cuowu",
            "gongju": TOOL_NAME,
            "action": action,
            "target": target,
            "cuowu": f"[RUNTIME_ERROR] {exc}",
            "result": {},
            "llm_brief": f"omni_body {action} failed for {target}: {exc}",
            "evidence": {"path": target, "exists": False, "sha256": "", "bytes": 0},
        }

    if not isinstance(result, dict):
        result = {"result": result, "success": True}

    if result.get("ok") is False:
        ok = False
    else:
        ok = bool(result.get("success", result.get("ok", False)))
    evidence = _extract_evidence(result, target, workspace)
    response: dict[str, Any] = {
        "schema": TOOL_SCHEMA,
        "ok": ok,
        "zhuangtai": "wancheng" if ok else "cuowu",
        "gongju": TOOL_NAME,
        "action": action,
        "target": target,
        "result": result,
        "llm_brief": f"omni_body {action} {'completed' if ok else 'failed'} for {target or '[no target]'}",
        "evidence": evidence,
        "runtime_source": str(Path(sys.modules[BodyRuntime.__module__].__file__).resolve(strict=False)),
        "fact_kernel_enabled": bool(runtime_meta.get("fact_kernel_enabled", True)),
        "fact_ledger_root": str(runtime_meta.get("ledger_root") or ""),
    }
    for key in ("execution_outcome", "verification_status", "deliverable_status", "verified", "changed", "artifact_records", "execution_result", "execution_envelope"):
        if key in result:
            response[key] = result[key]
    if not ok:
        response["cuowu"] = _error_code_from_result(result)
        response["llm_brief"] = f"omni_body {action} failed for {target or '[no target]'}: {response['cuowu']}"
    return response


# Some hosts call tool files through a generic run(args) function.
def run(args: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_omni_body(args)


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}
    print(json.dumps(run_omni_body(payload), ensure_ascii=False, indent=2))
