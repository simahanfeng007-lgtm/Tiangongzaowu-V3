"""
Tiangong Omni Body v3.4 Professional Application Adapter Layer
=============================================================

Tool-only application bridge layer. This module does not plan, reason, or run
hidden skill workflows. It gives the model professional application primitives:
- health/probe of real adapters;
- script/request-pack generation for native application bridges;
- safe local execution for APIs/CLIs that are available;
- honest failure when credentials, applications, or display backends are absent.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

PRO_APP_ACTIONS: Dict[str, Dict[str, Any]] = {
    "v34.professional_apps.info": {"risk": "A0", "implemented": True, "summary": "Inspect v3.4 professional app adapter layer and native/fallback execution modes."},
    "app.adapter.health": {"risk": "A0", "implemented": True, "summary": "Probe professional application adapters, executables, Python modules, environment credentials, and bridge readiness."},
    "app.adapter.matrix": {"risk": "A0", "implemented": True, "summary": "Return professional application action matrix: native, script bridge, API pack, portable fallback, or unavailable."},
    "app.native.capability_probe": {"risk": "A0", "implemented": True, "summary": "Probe one named app adapter and return concrete installation/credential gaps."},
    "app.bridge.script.create": {"risk": "A2", "implemented": True, "summary": "Generate a native bridge script for Playwright, Office, WPS, Adobe, Blender, Figma, Feishu, GitHub, Docker, or desktop automation."},
    "app.bridge.pack.create": {"risk": "A2", "implemented": True, "summary": "Create a zip bridge pack containing native script, runbook, manifest, and adapter contract."},

    "browser.playwright.script.create": {"risk": "A2", "implemented": True, "summary": "Generate Playwright Python automation script; does not require browser runtime to be installed."},
    "browser.playwright.goto": {"risk": "A2", "implemented": True, "summary": "Use Playwright to open a URL and save HTML/screenshot when available; fails honestly if Playwright/browser missing."},
    "browser.playwright.screenshot": {"risk": "A2", "implemented": True, "summary": "Capture a real Chromium screenshot; missing browser is an execution failure."},
    "browser.playwright.extract_text": {"risk": "A0", "implemented": True, "summary": "Extract text from a real browser DOM with explicit preview truncation and saved full text."},
    "browser.playwright.pdf": {"risk": "A2", "implemented": True, "summary": "Export PDF through real Chromium; missing browser is an execution failure."},

    "microsoft.graph.request_pack.create": {"risk": "A2", "implemented": True, "summary": "Create Microsoft Graph request pack for OneDrive/SharePoint/Excel/DriveItem workflows; does not execute without token."},
    "microsoft.office.com.script.create": {"risk": "A2", "implemented": True, "summary": "Generate Windows Office COM Python bridge script for Word/Excel/PowerPoint automation."},
    "microsoft.word.native.export_pdf": {"risk": "A2", "implemented": True, "summary": "Export DOCX using native Windows Word COM with args.execute=true and a fresh args.output PDF, then reopen the result. Missing native environment fails; no generated-script success."},
    "microsoft.excel.native.chart.create": {"risk": "A2", "implemented": False, "summary": "Native Excel chart requirement retained, backend not implemented. A PDF export script is not a chart operation. Discover a configured Excel MCP backend."},
    "microsoft.powerpoint.native.export_pdf": {"risk": "A2", "implemented": True, "summary": "Export PPTX using native Windows PowerPoint COM with args.execute=true and a fresh args.output PDF, then reopen the result. Missing native environment fails."},
    "wps.native.script.create": {"risk": "A2", "implemented": True, "summary": "Generate WPS automation bridge notes/scripts for Writer/Spreadsheets/Presentation."},

    "adobe.photoshop.uxp.script.create": {"risk": "A2", "implemented": True, "summary": "Generate Photoshop UXP/PSJS script for layer/text/export operations."},
    "adobe.premiere.jsx.script.create": {"risk": "A2", "implemented": True, "summary": "Generate Premiere ExtendScript JSX bridge script for import/sequence/export operations."},
    "adobe.aftereffects.jsx.script.create": {"risk": "A2", "implemented": True, "summary": "Generate After Effects JSX bridge script for comp/text/render-queue operations."},

    "blender.python.script.create": {"risk": "A2", "implemented": True, "summary": "Generate Blender Python script for scene/model/material/camera/render operations."},
    "blender.python.run": {"risk": "A4", "implemented": True, "summary": "Run Blender Python script through blender --background when Blender is installed; Runtime applies sandbox and A5 hard rejection."},

    "figma.api.request_pack.create": {"risk": "A2", "implemented": True, "summary": "Create Figma REST API request pack for file/nodes/images/components; does not execute without token."},
    "canva.api.request_pack.create": {"risk": "A2", "implemented": True, "summary": "Create Canva integration/request pack or design brief payload; does not execute without adapter credentials."},
    "feishu.api.request_pack.create": {"risk": "A2", "implemented": True, "summary": "Create Feishu OpenAPI request pack for docs/sheets/messages; does not execute without app credentials."},
    "wechat_work.webhook.request_pack.create": {"risk": "A2", "implemented": True, "summary": "Create Enterprise WeChat webhook request pack; sending requires explicit webhook and confirmation in host."},

    "git.status": {"risk": "A0", "implemented": True, "summary": "Run git status --porcelain safely in workspace if git is installed."},
    "git.diff": {"risk": "A0", "implemented": True, "summary": "Run git diff safely in workspace if git is installed."},
    "git.log": {"risk": "A0", "implemented": True, "summary": "Run git log safely in workspace if git is installed."},
    "git.add": {"risk": "A3", "implemented": True, "summary": "Run git add on explicit paths if git is installed."},
    "git.commit": {"risk": "A4", "implemented": True, "summary": "Run git commit inside the workspace when git is installed; Runtime rejects A5 operations."},

    "docker.health": {"risk": "A0", "implemented": True, "summary": "Check Docker CLI/daemon availability without mutating containers."},
    "docker.ps": {"risk": "A0", "implemented": True, "summary": "Run docker ps if Docker is available."},
    "docker.compose.config": {"risk": "A0", "implemented": True, "summary": "Validate/print docker compose config for a compose file."},

    "sqlite.query": {"risk": "A2", "implemented": True, "summary": "Run a SQLite query against a workspace database; SELECT by default, writes require confirmed=true."},

    "mcp.servers.list": {"risk": "A0", "implemented": True, "summary": "List user-configured MCP servers from ~/.tiangong/v3/mcp_servers.json (read-only, no env values)."},
    "mcp.tools.list": {"risk": "A0", "implemented": True, "summary": "Connect to a configured MCP server and list its tools with input schemas."},
    "mcp.tool.call": {"risk": "A3", "implemented": True, "summary": "Call one tool on a configured MCP server; A3 confirmation chain applies because MCP tools can have arbitrary side effects."},
}

APP_PROFILES: Dict[str, Dict[str, Any]] = {
    "browser.playwright": {
        "label": "Playwright Browser",
        "modules": ["playwright"],
        "executables": [],
        "env": [],
        "native_actions": ["browser.playwright.goto", "browser.playwright.screenshot", "browser.playwright.extract_text", "browser.playwright.pdf"],
        "bridge_actions": ["browser.playwright.script.create", "app.bridge.pack.create"],
        "official_path": "Playwright drives Chromium/Firefox/WebKit through a browser automation API; use for browser apps and UI flows.",
    },
    "microsoft.office": {
        "label": "Microsoft Office",
        "modules": ["win32com"],
        "executables": [],
        "env": ["MS_GRAPH_TOKEN"],
        "native_actions": ["microsoft.word.native.export_pdf", "microsoft.powerpoint.native.export_pdf", "microsoft.excel.native.chart.create"],
        "bridge_actions": ["microsoft.office.com.script.create", "microsoft.graph.request_pack.create"],
        "official_path": "Local Office COM on Windows for desktop automation; Microsoft Graph for OneDrive/SharePoint/Excel resources.",
    },
    "wps": {
        "label": "WPS Office",
        "modules": [],
        "executables": ["wps", "et", "wpp"],
        "env": [],
        "native_actions": [],
        "bridge_actions": ["wps.native.script.create"],
        "official_path": "WPS automation varies by installation; use local macro/automation bridge where available, otherwise file-level docx/xlsx/pptx fallback.",
    },
    "adobe.photoshop": {
        "label": "Adobe Photoshop",
        "modules": [],
        "executables": ["Photoshop", "photoshop"],
        "env": [],
        "native_actions": ["adobe.photoshop.document.create", "adobe.photoshop.layer.create", "adobe.photoshop.text.add", "adobe.photoshop.export.png"],
        "bridge_actions": ["adobe.photoshop.uxp.script.create"],
        "official_path": "Photoshop UXP/PSJS scripting bridge for native layer/document/export control; portable PNG/layer-json fallback remains available.",
    },
    "adobe.video": {
        "label": "Adobe Premiere / After Effects",
        "modules": [],
        "executables": ["Premiere Pro", "After Effects", "Adobe Premiere Pro"],
        "env": [],
        "native_actions": [],
        "bridge_actions": ["adobe.premiere.jsx.script.create", "adobe.aftereffects.jsx.script.create"],
        "official_path": "ExtendScript JSX bridge for Adobe video applications; actual render requires installed app and manual/host execution.",
    },
    "blender": {
        "label": "Blender",
        "modules": [],
        "executables": ["blender"],
        "env": [],
        "native_actions": ["blender.python.run"],
        "bridge_actions": ["blender.python.script.create"],
        "official_path": "Blender Python API through blender --background for modeling/render automation.",
    },
    "figma": {
        "label": "Figma",
        "modules": [],
        "executables": [],
        "env": ["FIGMA_TOKEN"],
        "native_actions": [],
        "bridge_actions": ["figma.api.request_pack.create"],
        "official_path": "Figma REST/API request pack; execution requires token and network access.",
    },
    "canva": {
        "label": "Canva",
        "modules": [],
        "executables": [],
        "env": ["CANVA_TOKEN"],
        "native_actions": [],
        "bridge_actions": ["canva.api.request_pack.create"],
        "official_path": "Canva integration bridge/request payload; execution requires configured Canva integration.",
    },
    "feishu": {
        "label": "Feishu / Lark",
        "modules": [],
        "executables": [],
        "env": ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_ACCESS_TOKEN"],
        "native_actions": [],
        "bridge_actions": ["feishu.api.request_pack.create"],
        "official_path": "Feishu OpenAPI request pack; execution requires app credentials/tenant token.",
    },
    "wechat_work": {
        "label": "Enterprise WeChat",
        "modules": [],
        "executables": [],
        "env": ["WECHAT_WORK_WEBHOOK"],
        "native_actions": [],
        "bridge_actions": ["wechat_work.webhook.request_pack.create"],
        "official_path": "Webhook/request pack only; sending must be confirmed by host.",
    },
    "git": {
        "label": "Git CLI",
        "modules": [],
        "executables": ["git"],
        "env": [],
        "native_actions": ["git.status", "git.diff", "git.log", "git.add", "git.commit"],
        "bridge_actions": [],
        "official_path": "Direct git CLI with explicit arguments and workspace cwd.",
    },
    "docker": {
        "label": "Docker CLI",
        "modules": [],
        "executables": ["docker"],
        "env": [],
        "native_actions": ["docker.health", "docker.ps", "docker.compose.config"],
        "bridge_actions": [],
        "official_path": "Docker CLI probe/read-only actions in v3.4; mutating container lifecycle remains future A4/A5 expansion.",
    },
    "sqlite": {
        "label": "SQLite",
        "modules": ["sqlite3"],
        "executables": [],
        "env": [],
        "native_actions": ["sqlite.query"],
        "bridge_actions": [],
        "official_path": "stdlib sqlite3 local database query/limited update executor.",
    },
    "mcp": {
        "label": "Model Context Protocol",
        "modules": [],
        "executables": [],
        "env": [],
        "native_actions": ["mcp.servers.list", "mcp.tools.list", "mcp.tool.call"],
        "bridge_actions": [],
        "official_path": "MCP stdio servers declared by the user in ~/.tiangong/v3/mcp_servers.json; the model can only reference configured server names and can never invent spawn commands.",
    },
}


def handle_pro_app_action(runtime: Any, op_id: str, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    if action == "v34.professional_apps.info":
        return _info(runtime, target, args)
    if action == "app.adapter.health":
        return _health(runtime, target, args)
    if action == "app.adapter.matrix":
        return _matrix(runtime, target, args)
    if action == "app.native.capability_probe":
        return _capability_probe(runtime, target, args)
    if action == "app.bridge.script.create":
        return _bridge_script_create(runtime, target, args)
    if action == "app.bridge.pack.create":
        return _bridge_pack_create(runtime, target, args)
    if action.startswith("browser.playwright."):
        return _browser_playwright(runtime, action, target, args, op_id=op_id)
    if action.startswith("mcp."):
        return _mcp_action(runtime, action, target, args, op_id=op_id)
    if action == "microsoft.graph.request_pack.create":
        return _request_pack(runtime, target, args, provider="microsoft_graph")
    if action == "microsoft.office.com.script.create":
        return _office_com_script(runtime, target, args)
    if action in {"microsoft.word.native.export_pdf", "microsoft.powerpoint.native.export_pdf", "microsoft.excel.native.chart.create"}:
        return _office_native_action(runtime, action, target, args)
    if action == "wps.native.script.create":
        return _wps_script(runtime, target, args)
    if action == "adobe.photoshop.uxp.script.create":
        return _adobe_photoshop_uxp(runtime, target, args)
    if action == "adobe.premiere.jsx.script.create":
        return _adobe_premiere_jsx(runtime, target, args)
    if action == "adobe.aftereffects.jsx.script.create":
        return _adobe_aftereffects_jsx(runtime, target, args)
    if action == "blender.python.script.create":
        return _blender_script(runtime, target, args)
    if action == "blender.python.run":
        return _blender_run(runtime, target, args)
    if action == "figma.api.request_pack.create":
        return _request_pack(runtime, target, args, provider="figma")
    if action == "canva.api.request_pack.create":
        return _request_pack(runtime, target, args, provider="canva")
    if action == "feishu.api.request_pack.create":
        return _request_pack(runtime, target, args, provider="feishu")
    if action == "wechat_work.webhook.request_pack.create":
        return _request_pack(runtime, target, args, provider="wechat_work")
    if action.startswith("git."):
        return _git_action(runtime, action, target, args)
    if action.startswith("docker."):
        return _docker_action(runtime, action, target, args)
    if action == "sqlite.query":
        return _sqlite_query(runtime, target, args)
    return {"success": False, "op_id": op_id, "action": action, "message": f"v3.4 action not implemented: {action}"}


def _resolve(runtime: Any, target: str | None, must_exist: bool = False) -> Path:
    return runtime._resolve(target, must_exist=must_exist)


def _rel(runtime: Any, path: Path) -> str:
    return runtime._rel(path)


def _write_text(runtime: Any, target: str, text: str) -> Dict[str, Any]:
    path = _resolve(runtime, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return runtime._file_evidence(path)


def _write_json(runtime: Any, target: str, data: Any) -> Dict[str, Any]:
    return _write_text(runtime, target, json.dumps(data, ensure_ascii=False, indent=2))


def _which_any(names: List[str]) -> List[str]:
    found: List[str] = []
    for name in names:
        w = shutil.which(name)
        if w and w not in found:
            found.append(w)
    return found


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _profile_health(profile_id: str) -> Dict[str, Any]:
    p = APP_PROFILES[profile_id]
    modules = {m: _module_available(m) for m in p.get("modules", [])}
    executables = {e: shutil.which(e) for e in p.get("executables", [])}
    env = {e: bool(os.environ.get(e)) for e in p.get("env", [])}
    native_ready = any(modules.values()) or any(executables.values()) or (profile_id in {"sqlite"})
    credential_ready = all(env.values()) if env else True
    bridge_ready = bool(p.get("bridge_actions"))
    gaps: List[str] = []
    for m, ok in modules.items():
        if not ok:
            gaps.append(f"missing_python_module:{m}")
    if p.get("executables") and not any(executables.values()):
        gaps.append("missing_executable:" + "/".join(p.get("executables", [])))
    for e, ok in env.items():
        if not ok:
            gaps.append(f"missing_env:{e}")
    if not p.get("executables") and not p.get("modules") and p.get("env") and not credential_ready:
        native_ready = False
    status = "native_ready" if native_ready and credential_ready else "bridge_ready" if bridge_ready else "not_ready"
    return {
        "profile_id": profile_id,
        "label": p["label"],
        "status": status,
        "native_ready": native_ready,
        "credential_ready": credential_ready,
        "bridge_ready": bridge_ready,
        "modules": modules,
        "executables": executables,
        "env": env,
        "native_actions": p.get("native_actions", []),
        "bridge_actions": p.get("bridge_actions", []),
        "official_path": p.get("official_path"),
        "gaps": gaps,
    }


def _info(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "result": {
            "schema": "tiangong.v3.omni_body.professional_apps.v1",
            "version": "3.4.0",
            "tool_boundary": "omni_body remains a tool: app adapters execute explicit actions or generate native bridge packs; no hidden skill workflow.",
            "execution_modes": ["native_cli_or_api", "native_desktop_bridge", "generated_bridge_script", "request_pack", "portable_fallback", "adapter_required"],
            "profile_count": len(APP_PROFILES),
            "action_count": len(PRO_APP_ACTIONS),
            "profiles": list(APP_PROFILES.keys()),
            "required_model_loop": "observe actual capabilities and environment -> model generates task-local Tools and Skill -> execute registered actions -> read back actual results -> repair from evidence -> submit to the adversarial reviewer; a generated bridge pack is not native execution",
        },
        "evidence": {"path": "v34_professional_apps", "exists": True, "bytes": len(PRO_APP_ACTIONS)},
    }


def _health(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    only = args.get("app") or target
    profiles = [str(only)] if only else sorted(APP_PROFILES.keys())
    rows = []
    for pid in profiles:
        if pid in APP_PROFILES:
            rows.append(_profile_health(pid))
    return {
        "success": True,
        "result": {
            "schema": "tiangong.v3.omni_body.professional_app_health.v1",
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "profiles": rows,
            "summary": {
                "native_ready": sum(1 for r in rows if r["status"] == "native_ready"),
                "bridge_ready": sum(1 for r in rows if r["status"] == "bridge_ready"),
                "not_ready": sum(1 for r in rows if r["status"] == "not_ready"),
            },
        },
        "evidence": {"path": "adapter_health", "exists": True, "bytes": len(rows)},
    }


def _matrix(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    items = []
    for pid, profile in APP_PROFILES.items():
        h = _profile_health(pid)
        items.append({
            "app": pid,
            "label": profile["label"],
            "status": h["status"],
            "native_actions": profile.get("native_actions", []),
            "bridge_actions": profile.get("bridge_actions", []),
            "recommended_first_action": ((profile.get("native_actions") or profile.get("bridge_actions") or [None])[0]),
            "gaps": h["gaps"],
        })
    return {"success": True, "result": {"schema": "tiangong.v3.omni_body.app_matrix.v1", "apps": items}, "evidence": {"path": "adapter_matrix", "exists": True, "bytes": len(items)}}


def _capability_probe(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    app = str(args.get("app") or target or "").strip()
    if not app:
        return {"success": False, "message": "app.native.capability_probe requires target or args.app"}
    if app not in APP_PROFILES:
        choices = [pid for pid in APP_PROFILES if app.lower() in pid.lower() or app.lower() in APP_PROFILES[pid]["label"].lower()]
        return {"success": False, "message": f"unknown app profile: {app}", "suggestions": choices[:10]}
    h = _profile_health(app)
    return {"success": True, "result": h, "evidence": {"path": app, "exists": True, "bytes": len(json.dumps(h, ensure_ascii=False))}}


def _bridge_script_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(args.get("kind") or args.get("app") or args.get("bridge") or "").lower().strip()
    if not kind:
        return {"success": False, "message": "app.bridge.script.create requires args.kind/app"}
    mapping = {
        "playwright": _playwright_script,
        "browser": _playwright_script,
        "office": _office_com_text,
        "word": _office_com_text,
        "excel": _office_com_text,
        "powerpoint": _office_com_text,
        "wps": _wps_script_text,
        "photoshop": _photoshop_uxp_text,
        "premiere": _premiere_jsx_text,
        "aftereffects": _aftereffects_jsx_text,
        "after_effects": _aftereffects_jsx_text,
        "blender": _blender_script_text,
        "figma": lambda a: _request_pack_text("figma", a)["script"],
        "feishu": lambda a: _request_pack_text("feishu", a)["script"],
        "canva": lambda a: _request_pack_text("canva", a)["script"],
        "github": lambda a: _request_pack_text("github", a)["script"],
        "docker": _docker_bridge_text,
    }
    fn = mapping.get(kind)
    if not fn:
        return {"success": False, "message": f"unknown bridge kind: {kind}", "known": sorted(mapping)}
    ext = {"playwright":"py", "browser":"py", "office":"py", "word":"py", "excel":"py", "powerpoint":"py", "wps":"md", "photoshop":"psjs", "premiere":"jsx", "aftereffects":"jsx", "after_effects":"jsx", "blender":"py", "docker":"sh"}.get(kind, "py")
    out = str(target or args.get("output") or f"bridges/{kind}_bridge.{ext}")
    text = fn(args)
    evidence = _write_text(runtime, out, text)
    return {"success": True, "result": {"kind": kind, "script_path": evidence["rel_path"], "execution_mode": "bridge_script_generated", "not_executed": True}, "evidence": evidence}


def _bridge_pack_create(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    app = str(args.get("app") or args.get("kind") or "professional_app").lower().strip()
    base = str(args.get("base_dir") or f"bridge_packs/{app}_{int(time.time())}")
    base_path = _resolve(runtime, base)
    base_path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "tiangong.v3.omni_body.bridge_pack.v1",
        "app": app,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool_boundary": "Bridge pack is evidence and execution script, not proof of native app execution.",
        "inputs": args,
        "run_steps": [],
    }
    script_res = _bridge_script_create(runtime, str(Path(base) / f"{app}_bridge"), {**args, "kind": app})
    manifest["script_result"] = script_res.get("result")
    manifest["run_steps"] = [
        "Inspect manifest.json and bridge script.",
        "Install/enable the native app adapter listed in app.adapter.health.",
        "Run the script in the native application context or host bridge.",
        "Return generated artifacts to omni_body for qc.* quality gates and deliverable.package.",
    ]
    _write_json(runtime, str(Path(base) / "manifest.json"), manifest)
    _write_text(runtime, str(Path(base) / "RUNBOOK.md"), _runbook_text(app, manifest))
    zip_target = str(target or args.get("output") or f"{base}.zip")
    # Use runtime zip action so evidence/rollback logic remains consistent.
    zip_res = runtime.run("zip.create", zip_target, {"sources": [base]})
    return {"success": bool(zip_res.get("success")), "result": {"app": app, "bridge_dir": base, "zip_result": zip_res}, "evidence": zip_res.get("evidence") or zip_res.get("output") or {}}


def _runbook_text(app: str, manifest: Dict[str, Any]) -> str:
    return f"""# {app} Bridge Pack Runbook

This pack is generated by omni_body v3.4 Professional Apps.

## Boundary
- This is a tool output, not autonomous execution.
- Native execution requires the actual application/API credentials/session.
- After execution, feed outputs back to omni_body QC actions.

## Steps
{chr(10).join('- ' + s for s in manifest.get('run_steps', []))}
"""


def _playwright_script(args: Dict[str, Any]) -> str:
    url = args.get("url") or args.get("target") or "https://example.com"
    screenshot = args.get("screenshot") or "playwright_screenshot.png"
    html = args.get("html") or "playwright_page.html"
    text = args.get("text") or "playwright_text.txt"
    wait = int(args.get("wait_ms") or 1000)
    return f'''import os
from pathlib import Path
from playwright.sync_api import sync_playwright

url = {url!r}

def launch_chromium(playwright):
    errors = []
    try:
        return playwright.chromium.launch(headless=True), "playwright-default"
    except Exception as exc:
        errors.append("playwright-default: " + str(exc).splitlines()[0])
    candidates = []
    configured = os.environ.get("TIANGONG_PLAYWRIGHT_EXECUTABLE", "").strip()
    if configured:
        candidates.append(Path(configured))
    cache_root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if cache_root.is_dir():
        candidates.extend(sorted(cache_root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True))
    candidates.extend([
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ])
    for executable in candidates:
        if not executable.is_file():
            continue
        try:
            return playwright.chromium.launch(headless=True, executable_path=str(executable)), str(executable)
        except Exception as exc:
            errors.append(str(executable) + ": " + str(exc).splitlines()[0])
    raise RuntimeError("No working Chromium executable. " + " | ".join(errors))

with sync_playwright() as p:
    browser, launch_source = launch_chromium(p)
    page = browser.new_page(viewport={{"width": 1365, "height": 768}})
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout({wait})
    Path({html!r}).write_text(page.content(), encoding="utf-8")
    Path({text!r}).write_text(page.locator("body").inner_text(timeout=10000), encoding="utf-8")
    page.screenshot(path={screenshot!r}, full_page=True)
    browser.close()
print("PLAYWRIGHT_BRIDGE_DONE", url, launch_source)
'''


def _launch_playwright_chromium(playwright: Any) -> Tuple[Any, str, List[str]]:
    errors: List[str] = []
    try:
        return playwright.chromium.launch(headless=True), "playwright-default", errors
    except Exception as exc:
        errors.append(f"playwright-default: {str(exc).splitlines()[0]}")

    candidates: List[Path] = []
    configured = str(os.environ.get("TIANGONG_PLAYWRIGHT_EXECUTABLE") or "").strip()
    if configured:
        candidates.append(Path(configured))
    # 随包分发的浏览器目录（main.js 注入 PLAYWRIGHT_BROWSERS_PATH）。
    packaged_root = str(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if packaged_root:
        packaged = Path(packaged_root)
        if packaged.is_dir():
            candidates.extend(sorted(packaged.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True))
            candidates.extend(sorted(packaged.glob("chromium-*/chrome-linux64/chrome"), reverse=True))
    if sys.platform.startswith("linux"):
        for name in ("google-chrome", "chromium", "chromium-browser"):
            executable = shutil.which(name)
            if executable:
                candidates.append(Path(executable))
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        cache_root = Path(local_app_data) / "ms-playwright"
        if cache_root.is_dir():
            candidates.extend(sorted(cache_root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True))
        # 每用户安装的 Chrome（Windows 最常见安装形态）此前不在候选里，
        # 导致"发布包没带浏览器 + Chrome 装在 LOCALAPPDATA"的机器上
        # browser.* 全部失败（2026-08-22 修复）。
        candidates.append(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe")
    program_files = str(os.environ.get("PROGRAMFILES") or "").strip()
    if program_files:
        candidates.append(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe")
        # 64 位 Edge 的常规安装位置。
        candidates.append(Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    program_files_x86 = str(os.environ.get("PROGRAMFILES(X86)") or "").strip()
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe")
        candidates.append(Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe")

    seen: set[str] = set()
    for executable in candidates:
        key = os.path.normcase(str(executable))
        if key in seen or not executable.is_file():
            continue
        seen.add(key)
        try:
            browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
            return browser, str(executable), errors
        except Exception as exc:
            errors.append(f"{executable}: {str(exc).splitlines()[0]}")
    raise RuntimeError("No working Chromium executable. " + " | ".join(errors))


def _browser_playwright(runtime: Any, action: str, target: str | None, args: Dict[str, Any], *, op_id: str | None = None) -> Dict[str, Any]:
    url = str(args.get("url") or args.get("source") or target or "").strip()
    if action == "browser.playwright.script.create":
        out = str(target or args.get("output") or "bridges/playwright_bridge.py")
        evidence = _write_text(runtime, out, _playwright_script({**args, "url": url or args.get("url")}))
        return {"success": True, "result": {"execution_mode": "bridge_script_generated", "script_path": evidence["rel_path"], "not_executed": True}, "evidence": evidence}
    if not url:
        return {"success": False, "message": f"{action} requires target or args.url"}
    if not _module_available("playwright"):
        return {"success": False, "execution_state": "not_executed", "requires_adapter": "playwright_python_and_browsers",
                "message": "Playwright is unavailable; no browser action was executed."}
    interaction_started = False
    browser = None
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        parsed = urllib.parse.urlsplit(url)
        if not parsed.scheme:
            url = _resolve(runtime, url, must_exist=True).as_uri()
        elif parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise ValueError("remote file URL is not supported")
            from urllib.request import url2pathname
            url = _resolve(runtime, url2pathname(parsed.path), must_exist=True).as_uri()
        elif parsed.scheme not in {"http", "https", "data"} or parsed.username or parsed.password:
            raise ValueError("unsupported browser URL")
        timeout_ms = max(1000, min(60000, int(args.get("timeout_ms") or 30000)))
        deadline = time.monotonic() + timeout_ms / 1000
        def remaining():
            cancel = getattr(getattr(runtime, "config", None), "cancel_check", None)
            if callable(cancel) and cancel():
                raise RuntimeError("browser action cancelled")
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                raise TimeoutError("browser action deadline")
            return left
        out_dir = _resolve(runtime, str(args.get("output_dir") or f"browser_playwright/{op_id or uuid.uuid4().hex}"))
        local_input = None
        if urllib.parse.urlsplit(url).scheme == "file":
            from urllib.request import url2pathname
            local_input = Path(url2pathname(urllib.parse.urlsplit(url).path))
        outputs = [out_dir / "page.html", out_dir / "text.txt"]
        if args.get("output"):
            outputs.append(_resolve(runtime, str(args["output"])))
        for output in outputs:
            if local_input and (output == local_input or (output.exists() and os.path.samefile(output, local_input))):
                raise ValueError("browser output cannot overwrite its source page")
        out_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser, launch_source, launch_errors = _launch_playwright_chromium(p)
            try:
                page = browser.new_page(viewport={"width": max(1, min(4096, int(args.get("width") or 1365))),
                                                   "height": max(1, min(4096, int(args.get("height") or 768)))})
                # Local subresources obey the same path authority as the page.
                def route(request_route):
                    address = urllib.parse.urlsplit(request_route.request.url)
                    if address.scheme == "file":
                        try:
                            from urllib.request import url2pathname
                            _resolve(runtime, url2pathname(address.path), must_exist=True)
                        except Exception:
                            request_route.abort()
                            return
                    if address.scheme not in {"http", "https", "file", "data", "blob", "about"}:
                        request_route.abort()
                    else:
                        request_route.continue_()
                page.route("**/*", route)
                response = page.goto(url, wait_until=str(args.get("wait_until") or "domcontentloaded"), timeout=remaining())
                if action == "browser.chrome.click":
                    selector = args.get("selector")
                    if not isinstance(selector, str) or not selector.strip():
                        raise ValueError("browser.chrome.click requires args.selector")
                    interaction_started = True
                    page.locator(selector).click(timeout=remaining())
                wait_ms = max(0, min(10000, int(args.get("wait_ms") or 0)))
                if wait_ms:
                    page.wait_for_timeout(min(wait_ms, remaining()))
                html_path, text_path = out_dir / "page.html", out_dir / "text.txt"
                html_text = page.content()
                body_text = page.locator("body").inner_text(timeout=remaining())
                if max(len(html_text.encode()), len(body_text.encode())) > 4 * 1024 * 1024:
                    raise ValueError("browser observation exceeds 4 MiB; narrow the page scope")
                html_path.write_text(html_text, encoding="utf-8")
                text_path.write_text(body_text, encoding="utf-8")
                preview_chars = max(1, min(200000, int(args.get("max_chars") or 8000)))
                links = page.locator("a[href]")
                result: Dict[str, Any] = {
                    "url": url, "final_url": page.url, "initial_http_status": response.status if response else None,
                    "execution_mode": "real_browser", "session_scope": "isolated_action",
                    "browser_launch_source": launch_source, "browser_launch_fallbacks": launch_errors,
                    "html": runtime._file_evidence(html_path), "text": runtime._file_evidence(text_path),
                    "body_preview": body_text[:preview_chars], "body_chars": len(body_text),
                    "preview_truncated": len(body_text) > preview_chars,
                    "full_text_available": True,
                    "links": links.evaluate_all("nodes => nodes.slice(0,200).map(a => ({text:a.innerText,url:a.href}))"),
                    "links_truncated": links.count() > 200,
                }
                if action in {"browser.playwright.screenshot", "browser.playwright.goto", "browser.chrome.click"}:
                    shot_path = _resolve(runtime, str(args["output"])) if args.get("output") else out_dir / "screenshot.png"
                    shot_path.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(shot_path), full_page=bool(args.get("full_page", True)), timeout=remaining())
                    result["screenshot"] = runtime._file_evidence(shot_path)
                if action == "browser.playwright.pdf":
                    pdf_path = _resolve(runtime, str(args["output"])) if args.get("output") else out_dir / "page.pdf"
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    remaining()
                    page.pdf(path=str(pdf_path), print_background=True)
                    result["pdf"] = runtime._file_evidence(pdf_path)
                remaining()
            finally:
                browser.close()
        return {"success": True, "result": result, "evidence": result.get("screenshot") or result.get("pdf") or result.get("text") or {}}
    except Exception as exc:
        return {"success": False, "message": str(exc), "ambiguous_effect": interaction_started,
                "execution_state": "unknown" if interaction_started else "failed",
                "reconciliation_required": interaction_started}


def _request_pack_text(provider: str, args: Dict[str, Any]) -> Dict[str, str]:
    title = str(args.get("title") or args.get("operation") or provider)
    if provider == "microsoft_graph":
        endpoint = args.get("endpoint") or "/me/drive/root:/path/to/file:/content"
        script = f'''# Microsoft Graph request pack
# Requires: MS_GRAPH_TOKEN with required delegated/application permissions.
import os, requests
base = "https://graph.microsoft.com/v1.0"
endpoint = {endpoint!r}
token = os.environ["MS_GRAPH_TOKEN"]
res = requests.get(base + endpoint, headers={{"Authorization": "Bearer " + token}})
print(res.status_code)
print(res.text[:4000])
'''
        http = f"GET https://graph.microsoft.com/v1.0{endpoint}\nAuthorization: Bearer $MS_GRAPH_TOKEN\n"
    elif provider == "figma":
        file_key = args.get("file_key") or "<FIGMA_FILE_KEY>"
        script = f'''# Figma API request pack
import os, requests
file_key = {file_key!r}
res = requests.get(f"https://api.figma.com/v1/files/{{file_key}}", headers={{"X-Figma-Token": os.environ["FIGMA_TOKEN"]}})
print(res.status_code)
print(res.text[:4000])
'''
        http = f"GET https://api.figma.com/v1/files/{file_key}\nX-Figma-Token: $FIGMA_TOKEN\n"
    elif provider == "feishu":
        script = '''# Feishu OpenAPI request pack
# Requires FEISHU_ACCESS_TOKEN or tenant token. Fill endpoint/body before execution.
import os, requests, json
endpoint = "https://open.feishu.cn/open-apis/docx/v1/documents"
headers = {"Authorization": "Bearer " + os.environ["FEISHU_ACCESS_TOKEN"], "Content-Type": "application/json"}
body = {"folder_token": "<folder_token>", "title": "New Document"}
res = requests.post(endpoint, headers=headers, data=json.dumps(body, ensure_ascii=False).encode("utf-8"))
print(res.status_code, res.text[:4000])
'''
        http = "POST https://open.feishu.cn/open-apis/docx/v1/documents\nAuthorization: Bearer $FEISHU_ACCESS_TOKEN\nContent-Type: application/json\n\n{\"title\":\"New Document\"}\n"
    elif provider == "wechat_work":
        script = '''# Enterprise WeChat webhook request pack
# Requires WECHAT_WORK_WEBHOOK. Sending must be explicitly confirmed by the host.
import os, requests, json
webhook = os.environ["WECHAT_WORK_WEBHOOK"]
body = {"msgtype":"text", "text":{"content":"<message>"}}
res = requests.post(webhook, json=body, timeout=20)
print(res.status_code, res.text)
'''
        http = "POST $WECHAT_WORK_WEBHOOK\nContent-Type: application/json\n\n{\"msgtype\":\"text\",\"text\":{\"content\":\"<message>\"}}\n"
    elif provider == "canva":
        script = '''# Canva integration request pack
# Requires a configured Canva integration/token. Keep this as a payload template unless host adapter is mounted.
payload = {"design_type":"presentation", "title":"<title>", "brief":"<brief>", "assets":[]}
print(payload)
'''
        http = "# Canva request pack placeholder; route through host Canva adapter.\n"
    elif provider == "github":
        script = '''# GitHub API request pack
import os, requests
repo = "owner/repo"
res = requests.get(f"https://api.github.com/repos/{repo}", headers={"Authorization":"Bearer "+os.environ["GITHUB_TOKEN"]})
print(res.status_code, res.text[:4000])
'''
        http = "GET https://api.github.com/repos/owner/repo\nAuthorization: Bearer $GITHUB_TOKEN\n"
    else:
        script = f"# {provider} request pack for {title}\n"
        http = f"# {provider} request pack\n"
    return {"script": script, "http": http}


def _request_pack(runtime: Any, target: str | None, args: Dict[str, Any], provider: str) -> Dict[str, Any]:
    base = str(target or args.get("output_dir") or f"request_packs/{provider}_{int(time.time())}")
    base_path = _resolve(runtime, base)
    base_path.mkdir(parents=True, exist_ok=True)
    texts = _request_pack_text(provider, args)
    (base_path / "request.py").write_text(texts["script"], encoding="utf-8")
    (base_path / "request.http").write_text(texts["http"], encoding="utf-8")
    manifest = {"schema": "tiangong.v3.omni_body.request_pack.v1", "provider": provider, "requires_credentials": True, "inputs": args, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "not_executed": True}
    (base_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_res = runtime.run("zip.create", f"{base}.zip", {"sources": [base]})
    return {"success": bool(zip_res.get("success")), "result": {"provider": provider, "request_dir": base, "zip_result": zip_res, "not_executed": True}, "evidence": zip_res.get("evidence") or zip_res.get("output") or {}}


def _office_com_text(args: Dict[str, Any]) -> str:
    app = str(args.get("office_app") or args.get("app") or "Word").lower()
    in_file = args.get("input") or "input.docx"
    out_file = args.get("output") or "output.pdf"
    progid = {"word": "Word.Application", "excel": "Excel.Application", "powerpoint": "PowerPoint.Application", "ppt": "PowerPoint.Application"}.get(app, "Word.Application")
    return f'''# Windows Office COM bridge. Requires Windows + Microsoft Office + pywin32.
import win32com.client
from pathlib import Path
app = win32com.client.Dispatch({progid!r})
app.Visible = False
src = str(Path({in_file!r}).resolve())
dst = str(Path({out_file!r}).resolve())
try:
    if {app!r} in ("word", ""):
        doc = app.Documents.Open(src)
        doc.ExportAsFixedFormat(dst, 17)  # wdExportFormatPDF
        doc.Close(False)
    elif {app!r} == "excel":
        wb = app.Workbooks.Open(src)
        wb.ExportAsFixedFormat(0, dst)
        wb.Close(False)
    else:
        pres = app.Presentations.Open(src, WithWindow=False)
        pres.SaveAs(dst, 32)  # ppSaveAsPDF
        pres.Close()
finally:
    app.Quit()
print("OFFICE_COM_BRIDGE_DONE", dst)
'''


def _office_com_script(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    out = str(target or args.get("output") or "bridges/office_com_bridge.py")
    ev = _write_text(runtime, out, _office_com_text(args))
    return {"success": True, "result": {"execution_mode": "bridge_script_generated", "script_path": ev["rel_path"], "not_executed": True}, "evidence": ev}


def _office_native_action(runtime: Any, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    if action == "microsoft.excel.native.chart.create":
        return {"success": False, "execution_state": "not_executed", "requires_adapter": "excel_native_chart",
                "message": "Native Excel chart creation is not implemented by the PDF-export bridge."}
    if platform.system().lower() != "windows" or not _module_available("win32com") or args.get("execute") is not True:
        return {"success": False, "execution_state": "not_executed",
                "requires_adapter": "windows_office_com", "message": "Native export requires Windows Office COM and args.execute=true. Use the separate script.create action if a script is desired."}
    started = False
    try:
        from .omni_body_tool import _resolve_python_interpreter
        interpreter = _resolve_python_interpreter()
        if not interpreter:
            raise RuntimeError("Office bridge Python interpreter unavailable")
        source = _resolve(runtime, target or args.get("input"), must_exist=True)
        if not args.get("output"):
            raise ValueError("Native PDF export requires args.output")
        output = _resolve(runtime, str(args["output"]))
        if output == source or (output.exists() and os.path.samefile(output, source)):
            raise ValueError("PDF output cannot overwrite its source")
        if output.exists():
            raise ValueError("Use a fresh output path so a stale PDF cannot prove this export")
        output.parent.mkdir(parents=True, exist_ok=True)
        app = "powerpoint" if "powerpoint" in action else "word"
        script = _office_com_text({**args, "app": app, "input": str(source), "output": str(output)})
        temporary = _resolve(runtime, f".omni_temp/office_{uuid.uuid4().hex}.py")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(script, encoding="utf-8")
        started = True
        response = subprocess.run([interpreter, str(temporary)], cwd=str(runtime.workspace), capture_output=True,
                                  text=True, timeout=max(1, min(120, int(args.get("timeout") or 120))))
        if response.returncode:
            raise RuntimeError(f"Native Office export returned {response.returncode}")
        from pypdf import PdfReader
        reader = PdfReader(str(output))
        pages = len(reader.pages)
        evidence = runtime._file_evidence(output)
        return {"success": True, "result": {"execution_mode": "native_office_com",
                "output": evidence, "pdf_pages": pages, "content_quality": "unassessed"}, "evidence": evidence}
    except Exception as exc:
        return {"success": False, "message": str(exc), "execution_state": "unknown" if started else "not_executed",
                "ambiguous_effect": started, "reconciliation_required": started}


def _wps_script_text(args: Dict[str, Any]) -> str:
    return f"""# WPS Native Bridge Notes

WPS automation support varies by edition/platform. Use this as a host bridge contract:

- Input: {args.get('input', '<input file>')}
- Output: {args.get('output', '<output file>')}
- Desired operation: {args.get('operation', 'open/edit/export')}

Fallback already available in omni_body:
- wps.writer.docx.create -> docx.create
- wps.spreadsheet.sheet.create -> sheet.create
- wps.presentation.pptx.create -> pptx.create

For true UI automation, mount a WPS-specific adapter that implements observe/execute/verify and returns v3 evidence.
"""


def _wps_script(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    ev = _write_text(runtime, str(target or args.get("output") or "bridges/wps_bridge.md"), _wps_script_text(args))
    return {"success": True, "result": {"execution_mode": "bridge_contract_generated", "not_executed": True}, "evidence": ev}


def _photoshop_uxp_text(args: Dict[str, Any]) -> str:
    width = int(args.get("width") or 1080)
    height = int(args.get("height") or 1920)
    title = str(args.get("text") or args.get("title") or "Omni Body")
    out = args.get("output") or "omni_export.png"
    return f'''// Photoshop UXP/PSJS bridge. Run inside Photoshop UXP scripting.
const app = require('photoshop').app;
const {{ batchPlay }} = require('photoshop').action;

async function main() {{
  const doc = await app.documents.add({{ width: {width}, height: {height}, resolution: 72, name: "Omni Body Design" }});
  await batchPlay([
    {{ _obj: "make", _target: [{{ _ref: "contentLayer" }}], using: {{ _obj: "contentLayer", type: {{ _obj: "solidColorLayer", color: {{ _obj: "RGBColor", red: 16, grain: 22, blue: 32 }} }} }} }},
    {{ _obj: "make", _target: [{{ _ref: "textLayer" }}], using: {{ _obj: "textLayer", textKey: {title!r}, textStyleRange: [{{ _obj: "textStyleRange", from: 0, to: {len(title)}, textStyle: {{ _obj: "textStyle", size: {{ _unit: "pointsUnit", _value: 72 }}, fontPostScriptName: "ArialMT" }} }}] }} }}
  ], {{}});
  // Export path is handled by host plugin permissions. Desired output: {out}
}}
main();
'''


def _adobe_photoshop_uxp(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    ev = _write_text(runtime, str(target or args.get("output") or "bridges/photoshop_omni.psjs"), _photoshop_uxp_text(args))
    return {"success": True, "result": {"execution_mode": "photoshop_uxp_script_generated", "script_path": ev["rel_path"], "not_executed": True}, "evidence": ev}


def _premiere_jsx_text(args: Dict[str, Any]) -> str:
    media = args.get("media") or []
    if not isinstance(media, list):
        media = [media]
    return f'''// Premiere Pro ExtendScript bridge
// Import media and create a basic project sequence. Actual export requires Media Encoder preset path.
var media = {json.dumps(media, ensure_ascii=False)};
for (var i = 0; i < media.length; i++) {{
    app.project.importFiles([media[i]], 1, app.project.rootItem, 0);
}}
alert("OMNI Premiere bridge imported " + media.length + " media items. Configure sequence/export in host adapter.");
'''


def _adobe_premiere_jsx(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    ev = _write_text(runtime, str(target or args.get("output") or "bridges/premiere_omni.jsx"), _premiere_jsx_text(args))
    return {"success": True, "result": {"execution_mode": "premiere_jsx_script_generated", "not_executed": True}, "evidence": ev}


def _aftereffects_jsx_text(args: Dict[str, Any]) -> str:
    text = str(args.get("text") or "Omni Body")
    return f'''// After Effects ExtendScript bridge
app.beginUndoGroup("Omni Body Comp");
var comp = app.project.items.addComp("OmniBodyComp", 1080, 1920, 1, 10, 30);
var layer = comp.layers.addText({text!r});
layer.property("Position").setValue([540, 960]);
app.endUndoGroup();
alert("OMNI After Effects bridge created comp.");
'''


def _adobe_aftereffects_jsx(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    ev = _write_text(runtime, str(target or args.get("output") or "bridges/aftereffects_omni.jsx"), _aftereffects_jsx_text(args))
    return {"success": True, "result": {"execution_mode": "aftereffects_jsx_script_generated", "not_executed": True}, "evidence": ev}


def _blender_script_text(args: Dict[str, Any]) -> str:
    title = str(args.get("title") or "Omni Body Scene")
    output = args.get("output_image") or "render.png"
    return f'''# Blender Python bridge. Run with: blender --background --python this_script.py
import bpy, math
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
cube = bpy.context.object
cube.name = {title!r}
mat = bpy.data.materials.new("OmniMaterial")
mat.diffuse_color = (0.1, 0.35, 0.9, 1.0)
cube.data.materials.append(mat)
bpy.ops.object.light_add(type='AREA', location=(0, -3, 5))
light = bpy.context.object
light.data.energy = 500
light.data.size = 4
bpy.ops.object.camera_add(location=(4, -6, 4), rotation=(math.radians(60), 0, math.radians(34)))
bpy.context.scene.camera = bpy.context.object
bpy.context.scene.render.resolution_x = 1080
bpy.context.scene.render.resolution_y = 1080
bpy.context.scene.render.filepath = {output!r}
bpy.ops.wm.save_as_mainfile(filepath={str(args.get('output_blend') or 'omni_scene.blend')!r})
bpy.ops.render.render(write_still=True)
print("BLENDER_BRIDGE_DONE", {output!r})
'''


def _blender_script(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    ev = _write_text(runtime, str(target or args.get("output") or "bridges/blender_omni.py"), _blender_script_text(args))
    return {"success": True, "result": {"execution_mode": "blender_python_script_generated", "not_executed": True}, "evidence": ev}


def _blender_run(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    blender = shutil.which(str(args.get("blender_path") or "blender"))
    if not blender:
        return {"success": False, "requires_adapter": "blender_cli", "message": "blender executable not found"}
    script_path = _resolve(runtime, target or args.get("script"), must_exist=True)
    res = subprocess.run([blender, "--background", "--python", str(script_path)], cwd=str(runtime.workspace), text=True, capture_output=True, timeout=int(args.get("timeout", 300)))
    return {"success": res.returncode == 0, "result": {"returncode": res.returncode, "stdout": res.stdout[-4000:], "stderr": res.stderr[-4000:]}, "evidence": {"path": str(script_path), "exists": script_path.exists(), "bytes": script_path.stat().st_size}}


def _docker_bridge_text(args: Dict[str, Any]) -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
docker version
docker ps
# Add explicit docker build/run commands only after user confirmation.
"""


def _git_action(runtime: Any, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    git = shutil.which("git")
    if not git:
        return {"success": False, "requires_adapter": "git_cli", "message": "git executable not found"}
    cwd = _resolve(runtime, target or args.get("repo") or ".")
    if not cwd.exists():
        return {"success": False, "message": f"repo path does not exist: {cwd}"}
    if cwd.is_file():
        cwd = cwd.parent
    cmd_map = {
        "git.status": [git, "status", "--porcelain=v1"],
        "git.diff": [git, "diff", "--", *[str(x) for x in args.get("paths", [])]],
        "git.log": [git, "log", f"-{int(args.get('limit', 5))}", "--oneline"],
        "git.add": [git, "add", "--", *[str(x) for x in args.get("paths", ["."])]],
    }
    if action == "git.commit":
        msg = str(args.get("message") or "omni_body commit")
        cmd = [git, "commit", "-m", msg]
    else:
        cmd = cmd_map.get(action)
    if not cmd:
        return {"success": False, "message": f"Unsupported git action: {action}"}
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=int(args.get("timeout", 60)))
    success = res.returncode == 0 or (action == "git.diff" and res.returncode == 1)
    return {"success": success, "result": {"cmd": cmd[1:], "returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}, "evidence": {"path": str(cwd), "exists": True, "bytes": len(res.stdout)}}


def _docker_action(runtime: Any, action: str, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    docker = shutil.which("docker")
    if action == "docker.health":
        if not docker:
            return {"success": True, "result": {"docker_cli": False, "daemon_ready": False, "message": "docker executable not found"}, "evidence": {"path": "docker", "exists": False, "bytes": 0}}
        res = subprocess.run([docker, "version", "--format", "{{json .}}"], capture_output=True, text=True, timeout=15)
        return {"success": True, "result": {"docker_cli": True, "daemon_ready": res.returncode == 0, "stdout": res.stdout, "stderr": res.stderr}, "evidence": {"path": docker, "exists": True, "bytes": len(res.stdout)}}
    if not docker:
        return {"success": False, "requires_adapter": "docker_cli", "message": "docker executable not found"}
    if action == "docker.ps":
        cmd = [docker, "ps", "--format", "json"]
    elif action == "docker.compose.config":
        compose_file = str(target or args.get("file") or "docker-compose.yml")
        cmd = [docker, "compose", "-f", compose_file, "config"]
    else:
        return {"success": False, "message": f"unsupported docker action: {action}"}
    res = subprocess.run(cmd, cwd=str(runtime.workspace), capture_output=True, text=True, timeout=int(args.get("timeout", 60)))
    return {"success": res.returncode == 0, "result": {"cmd": cmd[1:], "returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr}, "evidence": {"path": "docker", "exists": True, "bytes": len(res.stdout)}}


def _mcp_action(runtime: Any, action: str, target: str | None, args: Dict[str, Any], *, op_id: str = "observation") -> Dict[str, Any]:
    """MCP 接入（v1）：服务器只能来自用户配置文件，模型不可自造命令。

    权限链全复用：mcp.tool.call 注册为 A3，由网关确认链把关——任何
    MCP 工具都可能有任意副作用，宁可多确认一次。
    """
    from .mcp_client import (
        DEFAULT_TIMEOUT_MS,
        McpClientError,
        call_tool,
        application_connections,
        list_servers,
        list_tools,
    )

    def _timeout_ms() -> int:
        try:
            return int(args.get("timeout_ms") or DEFAULT_TIMEOUT_MS)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_MS

    try:
        def capture(value):
            if not callable(getattr(runtime, "_resolve", None)):
                return None  # Standalone client tests have no product object workspace.
            import hashlib
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            return _write_text(runtime, f"mcp_observations/{digest}.json", raw)

        if action == "mcp.servers.list":
            servers = list_servers()
            return {
                "success": True,
                "result": {
                    "servers": servers,
                    "count": len(servers),
                    **application_connections(app_id=str(args.get("app_id") or ""),
                        offset=int(args.get("offset") or 0), limit=int(args.get("limit") or 20)),
                    "hint": "现有 MCP 配置保存服务、环境位置和 applications 关联。用 mcp.tools.list 连接并读取实际工具；按 next_cursor/next_offset 继续发现。配置存在不等于应用已接通。",
                },
            }
        if action == "mcp.tools.list":
            server = str(target or args.get("server") or "").strip()
            result = list_tools(server, timeout_ms=_timeout_ms(), cursor=args.get("cursor"), capture=capture)
            return {"success": True, "result": result}
        if action == "mcp.tool.call":
            server = str(target or args.get("server") or "").strip()
            tool = str(args.get("tool") or args.get("name") or "").strip()
            arguments = args.get("arguments", {})
            result = call_tool(server, tool, arguments, timeout_ms=_timeout_ms(), capture=capture)
            return {
                "success": not result.get("is_error"),
                # 两种失败（isError / McpClientError）保持同一形状：
                # error + message + result 三键齐全，下游按 error 分类。
                "error": "" if not result.get("is_error") else "mcp.tool.is_error",
                "result": result,
                "message": "MCP tool reported isError" if result.get("is_error") else "",
                # isError describes a tool failure, not a rollback guarantee.
                "ambiguous_effect": bool(result.get("is_error")),
                "reconciliation_required": bool(result.get("is_error")),
            }
    except McpClientError as exc:
        return {"success": False, "error": exc.code, "result": None, "message": str(exc),
                "ambiguous_effect": exc.code == "mcp.outcome.unknown",
                "execution_state": "unknown" if exc.code == "mcp.outcome.unknown" else "not_executed",
                "reconciliation_required": exc.code == "mcp.outcome.unknown"}
    return {"success": False, "error": "mcp.action.unknown", "result": None, "message": f"unknown mcp action: {action}"}


def _sqlite_query(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    db = _resolve(runtime, target or args.get("database") or "data.sqlite")
    query = str(args.get("query") or "").strip()
    if not query:
        return {"success": False, "message": "sqlite.query requires args.query"}
    read_only = args.get("confirmed") is not True
    if not read_only:
        db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5) if read_only else sqlite3.connect(str(db), timeout=5)
    deadline = time.monotonic() + max(1, min(120, int(args.get("timeout") or 30)))
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
    def authorize(operation, arg1, arg2, database, trigger):
        if operation in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
            return sqlite3.SQLITE_DENY
        if operation == sqlite3.SQLITE_FUNCTION and str(arg2).lower() == "load_extension":
            return sqlite3.SQLITE_DENY
        if operation == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in {"temp_store_directory", "data_store_directory"}:
            return sqlite3.SQLITE_DENY
        if read_only:
            if operation == sqlite3.SQLITE_PRAGMA:
                return sqlite3.SQLITE_OK if str(arg1).lower() in {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list", "database_list", "compile_options"} else sqlite3.SQLITE_DENY
            if operation not in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    committed = False
    try:
        cur = conn.cursor()
        cur.execute(query, args.get("params") or [])
        headers = [d[0] for d in cur.description] if cur.description else []
        limit = max(1, min(10000, int(args.get("max_rows") or 100)))
        rows = cur.fetchmany(limit + 1)
        truncated = len(rows) > limit
        rows = rows[:limit]
        # A cursor with RETURNING must finish before commit. Discarding unseen
        # rows is explicit, and never presented as a complete query result.
        if not read_only:
            while cur.fetchmany(1024):
                if time.monotonic() >= deadline:
                    raise TimeoutError("SQLite query deadline")
            conn.commit()
            committed = True
        out_csv = args.get("output_csv")
        ev = runtime._file_evidence(db)
        csv_ev = None
        if out_csv and headers:
            if truncated:
                raise ValueError("result exceeds max_rows; refusing a silently partial CSV export")
            csv_path = _resolve(runtime, str(out_csv))
            if csv_path == db or (csv_path.exists() and os.path.samefile(csv_path, db)):
                raise ValueError("CSV output cannot overwrite its database")
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = csv_path.with_name(csv_path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("x", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows([[v.hex() if isinstance(v, bytes) else v for v in row] for row in rows])
                temporary.replace(csv_path)
            finally:
                temporary.unlink(missing_ok=True)
            csv_ev = runtime._file_evidence(csv_path)
        serialized_rows = [[{"type": "blob", "hex": v.hex()} if isinstance(v, bytes) else v for v in row] for row in rows]
        return {"success": True, "result": {"headers": headers, "rows": serialized_rows,
            "row_count": None if truncated else len(rows), "returned_rows": len(rows),
            "truncated": truncated, "read_only": read_only,
            "changed": conn.total_changes, "csv": csv_ev}, "evidence": csv_ev or ev}
    except Exception as exc:
        conn.rollback()
        return {"success": False, "message": str(exc), "ambiguous_effect": committed,
                "execution_state": "committed_export_failed" if committed else "failed_without_commit"}
    finally:
        conn.close()
