from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class DesktopProductRegressionTests(unittest.TestCase):
    def test_life_header_uses_theme_surface_instead_of_fixed_dark_overlay(self):
        css = source("app/frontend-v2/styles/life.css")
        header = css.split(".life-page-header {", 1)[1].split("}", 1)[0]
        self.assertIn("var(--surface-solid)", header)
        self.assertIn("var(--accent)", header)
        self.assertNotIn("rgba(18, 24, 22", header)

    def test_life_submenus_use_bounded_inner_scroll_viewports(self):
        panel = source("app/frontend-v2/renderer/plugins/life-panel.mjs")
        css = source("app/frontend-v2/styles/life.css")
        self.assertIn("content.dataset.lifeActiveTab = activeTab", panel)
        self.assertIn("--life-window-standard: 280px", css)
        self.assertIn(".life-tab-view.life-two-column", css)
        self.assertIn("grid-auto-rows: var(--life-window-standard)", css)
        self.assertIn("overflow-y: auto", css)
        self.assertIn("overscroll-behavior: contain", css)
        self.assertIn('[data-life-active-tab="settings"]', css)

    def test_life_machine_terms_are_mapped_to_chinese(self):
        panel = source("app/frontend-v2/renderer/plugins/life-panel.mjs")
        for mapping in (
            'extraversion: "外向性"',
            'agreeableness: "宜人性"',
            'conscientiousness: "尽责性"',
            'openness: "开放性"',
            'arousal_set_point: "唤醒基准"',
            'dominance_set_point: "掌控基准"',
            'emotional_reactivity: "情绪反应强度"',
            'recovery_tendency: "情绪恢复倾向"',
            'valence_set_point: "愉悦度基准"',
            'embedded_life_runtime: "内置生命运行时"',
        ):
            self.assertIn(mapping, panel)
        self.assertIn('return /[A-Za-z]{2,}/.test(translated) ? "系统字段" : translated;', panel)
        self.assertNotIn('label: "系统 Skill"', panel)
        self.assertNotIn('label: "系统 Ability"', panel)
        self.assertNotIn('label: "可执行 Tool"', panel)
        self.assertNotIn('sectionTitle("Soul"', panel)
        self.assertNotIn(">保存 Soul<", panel)

    def test_knowledge_cards_and_chat_retrieval_are_visible_in_product_surface(self):
        panel = source("app/frontend-v2/renderer/plugins/knowledge-panel.mjs")
        css = source("app/frontend-v2/styles/knowledge.css")
        runtime = source("app/frontend-v2/renderer/runtime/http-runtime.mjs")
        backend = source("src/total_gateway/embedded_backend.py")
        gateway_runtime = source("src/total_gateway/runtime.py")
        orchestration = source("src/total_gateway/orchestration.py")

        self.assertIn('id="knowledgeExtractedCard"', panel)
        self.assertIn("card.key_points", panel)
        self.assertIn("card.content_extract", panel)
        self.assertIn("knowledge-extracted-card", css)
        self.assertIn('apiJson("/api/v1/gateway/desktop/inbound"', runtime)
        self.assertIn('data.get("knowledge_references")', backend)
        self.assertIn('self._module._knowledge_action("search"', backend)
        self.assertIn('context["knowledge_references"] = cards[:6]', backend)
        self.assertIn("def retrieve_knowledge(query: str)", gateway_runtime)
        self.assertIn("knowledge_retriever=(", gateway_runtime)
        self.assertIn('"knowledge_references": knowledge_references', orchestration)
        self.assertIn(
            "external_content_count=len(attachments) + len(knowledge_references)",
            orchestration,
        )
        self.assertNotIn('"knowledge_references": [],', orchestration)

    def test_learned_skill_controls_preserve_release_tools(self):
        panel = source("app/frontend-v2/renderer/plugins/skills-panel.mjs")
        actions = source("app/frontend-v2/renderer/core/actions.mjs")
        runtime = source("app/frontend-v2/renderer/runtime/http-runtime.mjs")
        life_api = source("app/frontend-v2/renderer/runtime/life-api.mjs")

        self.assertIn('data-delete-skill="${escHtml(id)}"', panel)
        self.assertIn('data-activate-skill="${escHtml(id)}"', panel)
        self.assertIn('ability.runtimeUsable ? "可用" : "激活"', panel)
        self.assertIn('ability.source === "backend_tool_registry"', panel)
        self.assertIn("actions.activateSkill?.({ artifact_id:", panel)
        self.assertIn("const payload = ability && typeof ability === \"object\"", actions)
        self.assertIn("return runtime.deleteSkill({ ...payload, actor: \"user\" });", actions)
        self.assertIn("return runtime.activateSkill({ ...payload, actor: \"user\" });", actions)
        self.assertIn("LIFE_API_ROUTES.capabilityActivate", runtime)
        self.assertIn(
            'capabilityActivate: route("POST", "/api/v1/v3/life/capability/activate"',
            life_api,
        )

    def test_startup_has_one_authoritative_gateway_only(self):
        main = source("app/main.js")
        start = main.index("const serviceSupervisor = new ServiceSupervisor")
        end = main.index("async function createWindow", start)
        services = main[start:end]
        self.assertIn('name: "total-gateway", phase: 0', services)
        self.assertNotIn('name: "backend"', services)
        self.assertNotIn('name: "life"', services)
        self.assertNotIn('name: "communication"', services)
        self.assertNotIn('LEGACY_MULTI_PROCESS', main)
        self.assertIn('TIANGONG_GATEWAY_DEPLOYMENT_MODE', main)


    def test_workspace_junctions_and_renderer_open_paths_fail_closed(self):
        main = source("app/main.js")
        open_start = main.index("function canonicalExistingPath")
        open_end = main.index("async function stopServicesForWorkspaceChange", open_start)
        open_functions = main[open_start:open_end]
        script = f"""
const fs = require("fs");
const os = require("os");
const path = require("path");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "tg-open-boundary-"));
const workspace = path.join(root, "workspace");
const runtime = path.join(root, "runtime");
const outside = path.join(root, "outside");
for (const item of [workspace, runtime, outside]) fs.mkdirSync(item);
fs.writeFileSync(path.join(workspace, "safe.pdf"), "ok");
fs.writeFileSync(path.join(workspace, "danger.exe"), "bad");
const junction = path.join(workspace, "escape");
fs.symlinkSync(outside, junction, "junction");
const RENDERER_OPEN_FILE_EXTENSIONS = new Set(["pdf", "png", "txt"]);
const shellCalls = [];
const shell = {{ openPath: async (target) => {{ shellCalls.push(target); return ""; }} }};
function exists(value) {{ return fs.existsSync(value); }}
function isDirectory(value) {{ try {{ return fs.statSync(value).isDirectory(); }} catch {{ return false; }} }}
function committedWorkspaceRoot() {{ return workspace; }}
function runtimeStateRoot() {{ return runtime; }}
{open_functions}
(async () => {{
  const safe = await openRendererPath(path.join(workspace, "safe.pdf"));
  const executable = await openRendererPath(path.join(workspace, "danger.exe"));
  const outsideDirectory = await openRendererPath(outside);
  const junctionEscape = await openRendererPath(junction);
  console.log(JSON.stringify({{ safe, executable, outsideDirectory, junctionEscape, shellCalls }}));
}})().finally(() => fs.rmSync(root, {{ recursive: true, force: true }}));
"""
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True,
            text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["safe"]["ok"])
        self.assertEqual(payload["executable"]["error"], "path_type_not_allowed")
        self.assertEqual(payload["outsideDirectory"]["error"], "path_outside_allowed_roots")
        self.assertEqual(payload["junctionEscape"]["error"], "path_outside_allowed_roots")
        self.assertEqual(len(payload["shellCalls"]), 1)
        self.assertIn("fs.realpathSync.native(resolved)", main)

    def test_remote_download_and_qr_rendering_do_not_bypass_trust_boundary(self):
        main = source("app/main.js")
        start = main.index("function isPrivateNetworkAddress")
        end = main.index("async function downloadHttpToFile", start)
        guards = main[start:end]
        script = f"""
const dns = require("dns");
const net = require("net");
{guards}
(async () => {{
  const urls = ["http://127.0.0.1/x", "http://[::1]/x", "http://localhost/x", "https://u:p@example.com/x"];
  const errors = [];
  for (const url of urls) {{ try {{ await assertSafeRemoteUrl(url); errors.push(""); }} catch (e) {{ errors.push(e.message); }} }}
  const publicResult = await assertSafeRemoteUrl("https://8.8.8.8/x");
  console.log(JSON.stringify({{ errors, publicHost: publicResult.url.hostname }}));
}})();
"""
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True,
            text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(all(payload["errors"]))
        self.assertEqual(payload["publicHost"], "8.8.8.8")
        self.assertIn("MAX_REMOTE_DOWNLOAD_BYTES", main)
        self.assertIn("const lookup = (_hostname, options, callback)", main)

        settings = source("app/frontend-v2/renderer/plugins/settings-panel.mjs")
        qr_start = settings.index("function renderQr")
        qr_end = settings.index("function renderLinks", qr_start)
        qr_logic = settings[qr_start:qr_end]
        self.assertIn("qrTextToSvgDataUrl(qrcodeUrl)", qr_logic)
        self.assertNotIn("looksLikeImageUrl", qr_logic)
        self.assertIn("if (isDataImage)", qr_logic)
        self.assertIn("linkWechatQrWrap.hidden = true", qr_logic)

    def test_sse_unbounded_line_is_cancelled_and_terminal_cancels_reader(self):
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        script = f"""
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
globalThis.tiangongDesktop = {{ getGatewayUrl: () => "http://127.0.0.1:7184", getGatewayHeaders: () => ({{}}) }};
const {{ fetchSse }} = await import({json.dumps(module_url)});
function responseFor(text, cancels) {{
  const chunk = new TextEncoder().encode(text); let sent = false;
  return {{ ok: true, status: 200, headers: {{ get: (name) => name === "content-type" ? "text/event-stream" : "" }}, body: {{ getReader: () => ({{
    read: async () => sent ? ({{ done: true }}) : (sent = true, {{ done: false, value: chunk }}),
    cancel: async (reason) => cancels.push(String(reason))
  }}) }} }};
}}
const errors = [], hugeCancels = [], terminalCancels = [], done = [];
globalThis.fetch = async () => responseFor("data: " + "x".repeat(2 * 1024 * 1024 + 20), hugeCancels);
await fetchSse("/legacy", {{}}, {{ onError: (value) => errors.push(value) }});
globalThis.fetch = async () => responseFor('event: done\\ndata: {{"reply":"ok"}}\\n\\n', terminalCancels);
await fetchSse("/legacy", {{}}, {{ onDone: (value) => done.push(value.reply), onError: (value) => errors.push(value) }});
console.log(JSON.stringify({{ errors, hugeCancels, terminalCancels, done }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(any("safe buffer limit" in item for item in payload["errors"]))
        self.assertEqual(payload["hugeCancels"], ["sse_buffer_too_large"])
        self.assertEqual(payload["terminalCancels"], ["sse_terminal_received"])
        self.assertEqual(payload["done"], ["ok"])

    def test_model_key_and_custom_endpoint_use_one_atomic_desktop_transaction(self):
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        script = f"""
const values = new Map();
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
globalThis.localStorage = {{
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
}};
const calls = [];
globalThis.tiangongDesktop = {{
  setProviderApiKey: (payload) => {{ calls.push(["setProviderApiKey", payload]); throw new Error("legacy credential path must not be used"); }},
  setModelSettings: async (payload) => {{
    calls.push(["setModelSettings", payload]);
    return {{
      ok: true,
      configured_provider: payload.provider,
      configured_base_url: payload.base_url,
      configured_model_name: payload.model_name,
      providers: [],
      provider_profiles: {{}},
    }};
  }},
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
await runtime.setSettings({{
  modelProvider: "openai",
  modelBaseUrl: "https://models.example.test/v1",
  modelName: "custom-model",
  modelApiKey: "secret-value",
  modelThinkingDepth: "auto",
}});
console.log(JSON.stringify({{
  calls,
  stored: values.get("tiangong_frontend_settings") || "",
}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual([item[0] for item in payload["calls"]], ["setModelSettings"])
        model_payload = payload["calls"][0][1]
        self.assertEqual(model_payload["api_key"], "secret-value")
        self.assertEqual(model_payload["base_url"], "https://models.example.test/v1")
        self.assertEqual(model_payload["reasoning_mode"], "auto")
        self.assertNotIn("secret-value", payload["stored"])

    def test_model_credential_never_falls_back_to_renderer_http(self):
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        script = f"""
const values = new Map();
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
globalThis.localStorage = {{
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
}};
const fetchBodies = [];
globalThis.fetch = async (_url, options = {{}}) => {{
  fetchBodies.push(String(options.body || ""));
  return {{ ok: true, status: 200, text: async () => '{{"ok":true}}' }};
}};
globalThis.tiangongDesktop = {{
  setModelSettings: async () => {{ throw new Error("safe-storage-unavailable"); }},
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
let error = "";
try {{
  await runtime.setSettings({{
    modelProvider: "openai",
    modelBaseUrl: "https://models.example.test/v1",
    modelName: "custom-model",
    modelApiKey: "must-never-hit-http",
  }});
}} catch (value) {{ error = value.message; }}
console.log(JSON.stringify({{ error, fetchBodies }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("safe-storage-unavailable", payload["error"])
        self.assertEqual(payload["fetchBodies"], [])
        self.assertNotIn("must-never-hit-http", completed.stdout)

    def test_custom_endpoint_key_deletion_uses_the_same_endpoint_binding_transaction(self):
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        preload = source("app/preload.js")
        main = source("app/main.js")
        self.assertNotIn('credentials:setProviderApiKey', preload)
        self.assertNotIn('credentials:deleteProviderApiKey', preload)
        self.assertNotIn('handleTrusted("credentials:setProviderApiKey"', main)
        self.assertNotIn('handleTrusted("credentials:deleteProviderApiKey"', main)
        script = f"""
const values = new Map();
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
globalThis.localStorage = {{
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
}};
const calls = [];
globalThis.tiangongDesktop = {{
  getModelSettings: async () => ({{
    ok: true,
    configured_provider: "openai",
    configured_base_url: "https://models.example.test/v1",
    configured_model_name: "custom-model",
  }}),
  setModelSettings: async (payload) => {{ calls.push(payload); return {{ ok: true }}; }},
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
await runtime.deleteProviderApiKey("openai");
console.log(JSON.stringify(calls));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        calls = json.loads(completed.stdout)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["provider"], "openai")
        self.assertEqual(calls[0]["base_url"], "https://models.example.test/v1")
        self.assertEqual(calls[0]["model_name"], "custom-model")
        self.assertTrue(calls[0]["clear_api_key"])

    def test_failed_model_transaction_does_not_publish_speculative_renderer_settings(self):
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        script = f"""
const values = new Map();
values.set("tiangong_frontend_settings", JSON.stringify({{
  modelProvider: "old-provider",
  modelBaseUrl: "https://old.example.test/v1",
  modelName: "old-model",
}}));
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
globalThis.localStorage = {{
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
}};
globalThis.tiangongDesktop = {{
  setModelSettings: async () => ({{ ok: false, error: "invalid endpoint" }}),
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
let error = "";
try {{
  await runtime.setSettings({{
    modelProvider: "new-provider",
    modelBaseUrl: "https://new.example.test/v1",
    modelName: "new-model",
    modelApiKey: "never-store-me",
  }});
}} catch (value) {{ error = value.message; }}
console.log(JSON.stringify({{ error, stored: JSON.parse(values.get("tiangong_frontend_settings")) }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("invalid endpoint", payload["error"])
        self.assertEqual(payload["stored"]["modelProvider"], "old-provider")
        self.assertEqual(payload["stored"]["modelBaseUrl"], "https://old.example.test/v1")
        self.assertEqual(payload["stored"]["modelName"], "old-model")
        self.assertNotIn("never-store-me", json.dumps(payload["stored"]))

    def test_partial_body_name_save_preserves_authoritative_soul_prompt_and_refreshes_cache(self):
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        script = f"""
const values = new Map();
values.set("tiangong_frontend_settings", JSON.stringify({{
  personaName: "旧缓存名",
  soulPrompt: "STALE_PROMPT_MUST_NOT_BE_WRITTEN",
}}));
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
globalThis.localStorage = {{
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
}};
const calls = [];
const response = (payload) => ({{
  ok: true,
  status: 200,
  statusText: "OK",
  text: async () => JSON.stringify(payload),
}});
globalThis.fetch = async (url, options = {{}}) => {{
  const path = new URL(String(url)).pathname;
  const method = String(options.method || "GET").toUpperCase();
  const body = options.body ? JSON.parse(options.body) : null;
  calls.push({{ path, method, body }});
  if (path === "/api/v1/v3/life/soul" && method === "GET") {{
    return response({{ ok: true, soul: {{ name: "权威旧名", prompt: "SIGNED_AUTHORITATIVE_PROMPT" }} }});
  }}
  if (path === "/api/v1/v3/life/soul/update" && method === "POST") {{
    return response({{ ok: true, soul: {{ ...body.soul, revision: 8 }} }});
  }}
  if (path === "/api/v1/body/settings" && method === "POST") {{
    return response({{
      ok: true,
      profile: body.profile,
      user: body.user,
      voice: body.voice,
      presentation: {{ ...body.presentation, configured: true }},
      ui: body.ui,
    }});
  }}
  throw new Error(`unexpected ${{method}} ${{path}}`);
}};
globalThis.tiangongDesktop = {{
  getGatewayUrl: () => "http://127.0.0.1:7184",
  getGatewayHeaders: () => ({{ "X-Tiangong-Token": "test" }}),
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
await runtime.setSettings({{ personaName: "权威新名" }});
const soulWrite = calls.find((item) => item.path === "/api/v1/v3/life/soul/update");
const stored = JSON.parse(values.get("tiangong_frontend_settings"));
console.log(JSON.stringify({{ calls, soulWrite, stored }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["soulWrite"]["body"]["soul"]["name"], "权威新名")
        self.assertEqual(
            payload["soulWrite"]["body"]["soul"]["prompt"],
            "SIGNED_AUTHORITATIVE_PROMPT",
        )
        self.assertNotIn("STALE_PROMPT_MUST_NOT_BE_WRITTEN", json.dumps(payload["soulWrite"]))
        self.assertEqual(payload["stored"]["personaName"], "权威新名")
        self.assertEqual(payload["stored"]["soulPrompt"], "SIGNED_AUTHORITATIVE_PROMPT")

    def test_settings_read_caches_authoritative_soul_for_body_count_reload(self):
        module_url = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").as_uri()
        script = f"""
const values = new Map();
values.set("tiangong_frontend_settings", JSON.stringify({{ soulPrompt: "stale" }}));
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
globalThis.localStorage = {{
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
}};
const response = (payload) => ({{
  ok: true,
  status: 200,
  statusText: "OK",
  text: async () => JSON.stringify(payload),
}});
globalThis.fetch = async (url) => {{
  const path = new URL(String(url)).pathname;
  if (path === "/api/v1/v3/life/soul") return response({{
    ok: true,
    soul: {{ name: "缓存后的权威名", prompt: "刷新后实际人格底稿" }},
  }});
  if (path === "/api/v1/body/settings") return response({{
    ok: true, profile: {{}}, user: {{}}, voice: {{}},
    presentation: {{ configured: false }}, ui: {{}},
  }});
  if (path === "/api/v1/v3/life/panel") return response({{ ok: true, settings: {{}} }});
  return response({{ ok: true }});
}};
globalThis.tiangongDesktop = {{
  getGatewayUrl: () => "http://127.0.0.1:7184",
  getGatewayHeaders: () => ({{}}),
  getModelSettings: async () => ({{ ok: true }}),
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
const settings = await runtime.getSettings();
const stored = JSON.parse(values.get("tiangong_frontend_settings"));
console.log(JSON.stringify({{ settings, stored }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["settings"]["personaName"], "缓存后的权威名")
        self.assertEqual(payload["settings"]["soulPrompt"], "刷新后实际人格底稿")
        self.assertEqual(payload["stored"]["personaName"], "缓存后的权威名")
        self.assertEqual(payload["stored"]["soulPrompt"], "刷新后实际人格底稿")

    def test_corrupted_local_conversation_is_bounded_and_quota_failure_is_nonfatal(self):
        module_url = (ROOT / "app/frontend-v2/renderer/core/state.mjs").as_uri()
        script = f"""
const values = new Map();
values.set("linyuanzhe.sessions", JSON.stringify([{{ id: "s", title: "x", messages: [
  {{ id: "good", role: "assistant", content: "a".repeat(20000) }},
  {{ id: "bad", role: "assistant injected", content: "bad" }}
] }}]));
values.set("linyuanzhe.activeSessionId", "s");
globalThis.localStorage = {{ getItem: (key) => values.get(key) ?? null, setItem: () => {{ throw new Error("quota"); }}, removeItem: () => {{}} }};
globalThis.window = globalThis;
const {{ createState }} = await import({json.dumps(module_url)});
const state = createState();
const before = state.snapshot();
const added = state.addMessage("invalid role", "b".repeat(20000));
console.log(JSON.stringify({{ beforeCount: before.messages.length, beforeLength: before.messages[0].content.length, role: added.role, addedLength: added.content.length }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT, check=False,
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["beforeCount"], 1)
        self.assertEqual(payload["beforeLength"], 15000)
        self.assertEqual(payload["role"], "assistant")
        self.assertEqual(payload["addedLength"], 15000)

    def test_life_cards_use_explicit_regions_and_wrap_long_content(self):
        panel = source("app/frontend-v2/renderer/plugins/life-panel.mjs")
        css = source("app/frontend-v2/styles/life.css")
        pages_css = source("app/frontend-v2/styles/pages.css")

        for class_name in (
            "life-schedule-plan",
            "life-will-overview",
            "life-will-reason",
            "life-will-actions",
            "life-will-goals",
            "life-will-drive",
            "life-will-drift",
            "life-reflection-learning",
            "life-reflection-cards",
            "life-reflection-values",
            "life-capability-owned",
            "life-capability-artifacts",
            "life-artifact-grid",
        ):
            self.assertIn(class_name, panel)
            self.assertIn(f".{class_name}", css)

        self.assertIn('"overview overview"', css)
        self.assertIn('grid-template-areas: "plan"', css)
        self.assertIn('"values"', css)
        self.assertIn('"cards"', css)
        self.assertIn(
            ".life-reflection-cards .life-reflection-list {\n"
            "  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));",
            css,
        )
        self.assertIn('grid-template-areas:\n    "owned"\n    "artifacts";', css)
        self.assertNotIn(".life-will-layout > .life-card:nth-child", css)
        self.assertNotIn(".life-reflection-layout > .life-card:first-child", css)

        for selector in (
            ".life-section-title h3",
            ".life-card-copy strong",
            ".life-kv-row strong",
            ".life-task-main strong",
            ".life-summary-card-title strong",
        ):
            start = css.index(selector)
            block = css[start:css.index("}", start)]
            self.assertIn("overflow-wrap: anywhere", block)
            self.assertIn("white-space: normal", block)

        title_wrap_start = pages_css.index(".panel-title > span,\n.settings-subtitle")
        title_wrap_block = pages_css[title_wrap_start:pages_css.index("}", title_wrap_start)]
        self.assertIn("min-width: 0", title_wrap_block)
        self.assertIn("white-space: normal", title_wrap_block)
        self.assertIn("overflow-wrap: anywhere", title_wrap_block)

        pill_start = pages_css.index(".mini-pill {")
        pill_block = pages_css[pill_start:pages_css.index("}", pill_start)]
        self.assertIn("max-width: 100%", pill_block)
        self.assertIn("overflow-wrap: anywhere", pill_block)

    def test_workspace_change_uses_persisted_ipc_restart_and_rollback_boundary(self):
        main = source("app/main.js")
        preload = source("app/preload.js")
        runtime = source("app/frontend-v2/renderer/runtime/http-runtime.mjs")
        settings_panel = source("app/frontend-v2/renderer/plugins/settings-panel.mjs")

        self.assertIn('handleTrusted("workspace:setRoot"', main)
        self.assertIn('handleTrusted("workspace:getRoot"', main)
        self.assertIn("WORKSPACE_PREFERENCE_SCHEMA", main)
        self.assertIn("validateWorkspaceRoot", main)
        self.assertIn('fs.openSync(temporary, "wx", 0o600)', main)
        self.assertIn("fs.renameSync(temporary, filePath)", main)
        self.assertIn("delete process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT", main)
        self.assertIn("stopServicesForWorkspaceChange", main)
        self.assertIn("startServicesForWorkspaceChange", main)
        workspace_stop_start = main.index("async function stopServicesForWorkspaceChange")
        workspace_stop_end = main.index("async function startServicesForWorkspaceChange", workspace_stop_start)
        workspace_stop = main[workspace_stop_start:workspace_stop_end]
        self.assertIn('serviceSupervisor.stop("total-gateway"', workspace_stop)
        self.assertNotIn('serviceSupervisor.stop("backend"', workspace_stop)
        self.assertNotIn("drainAll", workspace_stop)
        self.assertNotIn('stop("life"', workspace_stop)
        self.assertNotIn('stop("communication"', workspace_stop)
        workspace_restart_start = main.index("async function startServicesForWorkspaceChange")
        workspace_restart_end = main.index("async function applyWorkspaceRootChange", workspace_restart_start)
        workspace_restart = main[workspace_restart_start:workspace_restart_end]
        self.assertEqual(workspace_restart.count("?.ready === true"), 1)
        self.assertNotIn("?.running === true", workspace_restart)
        self.assertIn("workspace-root-rollback", main)
        self.assertIn("workspaceChangeTail", main)
        self.assertIn("workspaceChangeRevision", main)
        self.assertIn("workspace_revision_conflict", main)
        self.assertIn('ipcRenderer.invoke("workspace:getRoot")', preload)
        self.assertIn('ipcRenderer.invoke("workspace:setRoot", request)', preload)
        self.assertIn("commitDesktopWorkspace", runtime)
        self.assertIn("expectedRevision", runtime)
        self.assertIn("readDesktopWorkspaceAuthority", runtime)
        self.assertIn("saveWorkspace.disabled = true", settings_panel)
        self.assertIn("chooseWorkspaceButton.disabled = true", settings_panel)

        apply_start = main.index("async function applyWorkspaceRootChange")
        apply_end = main.index("async function setWorkspaceRoot", apply_start)
        workspace_apply = main[apply_start:apply_end]
        self.assertIn("!services.backendReady", workspace_apply)
        self.assertIn("!services.totalGatewayReady", workspace_apply)
        self.assertNotIn("!services.lifeReady", workspace_apply)
        self.assertNotIn("!services.communicationReady", workspace_apply)
        self.assertEqual(
            workspace_apply.count("process.env.TIANGONG_WORKSPACE_ROOT = workspace"),
            1,
        )
        self.assertEqual(
            workspace_apply.count("process.env.TIANGONG_FORCE_WORKSPACE_ROOT = workspace"),
            1,
        )
        self.assertEqual(
            workspace_apply.count(
                "process.env.TIANGONG_WORKSPACE_ROOT = previousWorkspace"
            ),
            1,
        )
        self.assertEqual(
            workspace_apply.count(
                "process.env.TIANGONG_FORCE_WORKSPACE_ROOT = previousWorkspace"
            ),
            1,
        )

        choose_start = runtime.index("async chooseWorkspaceRoot(root)")
        choose_end = runtime.index("async chooseStorageRoot()", choose_start)
        self.assertNotIn("/api/v1/workspace/settings", runtime[choose_start:choose_end])

    def test_model_settings_have_an_electron_control_path_when_7184_is_offline(self):
        main = source("app/main.js")
        preload = source("app/preload.js")
        runtime = source("app/frontend-v2/renderer/runtime/http-runtime.mjs")
        self.assertIn('handleTrusted("model:getSettings"', main)
        self.assertIn('handleTrusted("model:setSettings"', main)
        self.assertIn('ipcRenderer.invoke("model:getSettings")', preload)
        self.assertIn('ipcRenderer.invoke("model:setSettings"', preload)
        self.assertIn("async function readModelSettings()", runtime)
        self.assertIn("async function writeModelSettings(payload)", runtime)
        settings_start = runtime.index("async function writeModelSettings(payload)")
        settings_end = runtime.index("function responseErrorDetail", settings_start)
        settings_control = runtime[settings_start:settings_end]
        self.assertIn("bridge.setModelSettings", settings_control)
        self.assertIn('apiJson("/api/v1/llm/settings"', settings_control)

        helper_start = main.index("function backendControlJsonRequest")
        helper_end = main.index("function sha256File", helper_start)
        helper = main[helper_start:helper_end]
        self.assertIn('"X-Tiangong-Token": DESKTOP_API_TOKEN', helper)
        self.assertIn('const controlUrl = TOTAL_GATEWAY_URL;', helper)
        self.assertIn('"/api/v1/llm/settings"', helper)
        self.assertIn('serviceSupervisor.start(modelRuntimeServiceName())', helper)

        runtime_name_start = main.index("function modelRuntimeServiceName")
        restart_start = main.index("async function restartBackendForCredentialChange", runtime_name_start)
        restart_end = main.index("async function setProviderApiKey", restart_start)
        credential_restart = main[runtime_name_start:restart_end]
        self.assertIn('return "total-gateway";', credential_restart)
        self.assertIn('serviceSupervisor.stop(serviceName', credential_restart)
        self.assertIn('serviceSupervisor.start(serviceName)', credential_restart)
        self.assertNotIn('serviceSupervisor.stop("life"', credential_restart)
        self.assertNotIn('serviceSupervisor.stop("communication"', credential_restart)

        gateway_wait_start = main.index("async function waitForTotalGateway")
        gateway_wait_end = main.index("async function startTotalGateway", gateway_wait_start)
        gateway_wait = main[gateway_wait_start:gateway_wait_end]
        self.assertIn("totalGatewayHealthCheck(3000)", gateway_wait)
        process_wait_end = gateway_wait.index("async function waitForTotalGatewayReadiness")
        process_wait = gateway_wait[:process_wait_end]
        readiness_wait = gateway_wait[process_wait_end:]
        self.assertNotIn("totalGatewayReadyCheck", process_wait)
        self.assertIn("totalGatewayReadyCheck(3000)", readiness_wait)
        self.assertIn("CREDENTIAL_RESTART_TIMEOUT_MS", readiness_wait)
        self.assertIn("waitForTotalGatewayReadiness()", credential_restart)
        self.assertIn("ready: () => totalGatewayReadyCheck(3000)", main)

        secure_start = main.index("async function secureModelSettingsUpdate")
        secure_end = main.index("function applyProviderApiKey", secure_start)
        secure_update = main[secure_start:secure_end]
        self.assertLess(
            secure_update.index('desktopModelSettingsRequest("POST", settings)'),
            secure_update.index('setProviderApiKey({ provider: credentialId, apiKey })'),
        )

    def test_overlapping_workspace_changes_reject_the_stale_revision(self):
        main = source("app/main.js")
        start = main.index("async function applyWorkspaceRootChange")
        end = main.index("function desktopProviderCredentialsPath", start)
        production_functions = main[start:end]
        script = f"""
let workspaceCommittedRoot = "C:/old";
let workspaceChangeRevision = 0;
let workspaceChangePending = 0;
let workspaceChangeTail = Promise.resolve();
let startCalls = 0;
const persisted = [];
function committedWorkspaceRoot() {{ return workspaceCommittedRoot; }}
function sameWindowsPath(left, right) {{ return String(left).toLowerCase() === String(right).toLowerCase(); }}
function validateWorkspaceRoot(value) {{ return String(value); }}
function writeWorkspacePreference(value) {{ persisted.push(value); }}
function writeDesktopDiagnostic() {{}}
async function stopServicesForWorkspaceChange() {{}}
async function startServicesForWorkspaceChange() {{
  startCalls += 1;
  await new Promise((resolve) => setTimeout(resolve, 20));
  return {{ backendReady: true, lifeReady: true, totalGatewayReady: true, communicationReady: true }};
}}
{production_functions}
(async () => {{
  const first = setWorkspaceRoot({{ workspace: "C:/new", expectedRevision: 0 }});
  const stale = setWorkspaceRoot({{ workspace: "C:/old", expectedRevision: 0 }});
  const [firstResult, staleResult] = await Promise.all([first, stale]);
  console.log(JSON.stringify({{
    firstResult,
    staleResult,
    workspaceCommittedRoot,
    workspaceChangeRevision,
    startCalls,
    persisted,
  }}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertTrue(payload["firstResult"]["ok"])
        self.assertEqual(payload["firstResult"]["workspace"], "C:/new")
        self.assertEqual(payload["staleResult"]["error"], "workspace_revision_conflict")
        self.assertEqual(payload["workspaceCommittedRoot"], "C:/new")
        self.assertEqual(payload["workspaceChangeRevision"], 1)
        self.assertEqual(payload["startCalls"], 1)
        self.assertEqual(payload["persisted"], ["C:/new"])

    def test_workspace_preference_write_failure_preserves_last_good_mapping(self):
        main = source("app/main.js")
        start = main.index("function writeWorkspacePreference")
        end = main.index("function applyWorkspacePreference", start)
        production_function = main[start:end]
        script = """
const fs = require("fs");
const os = require("os");
const path = require("path");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "tiangong-workspace-pref-"));
const preference = path.join(root, "workspace-preference.json");
const WORKSPACE_PREFERENCE_SCHEMA = "tiangong.desktop.workspace-preference.v1";
function workspacePreferencePath() { return preference; }
function validateWorkspaceRoot(value) { return String(value); }
function isFile(value) { try { return fs.statSync(value).isFile(); } catch { return false; } }
""" + production_function + """
try {
  writeWorkspacePreference("C:/last-good");
  const first = fs.readFileSync(preference, "utf8");
  const originalRename = fs.renameSync;
  fs.renameSync = () => { throw new Error("injected_rename_failure"); };
  let failure = "";
  try { writeWorkspacePreference("C:/must-not-commit"); } catch (error) { failure = error.message; }
  fs.renameSync = originalRename;
  const after = fs.readFileSync(preference, "utf8");
  const residue = fs.readdirSync(root).filter((name) => name.endsWith(".tmp"));
  console.log(JSON.stringify({ first, after, failure, residue }));
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
"""
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True,
            text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertEqual(payload["first"], payload["after"])
        self.assertIn("C:/last-good", payload["after"])
        self.assertEqual(payload["failure"], "injected_rename_failure")
        self.assertEqual(payload["residue"], [])

    def test_invalid_environment_workspace_falls_back_to_persisted_mapping(self):
        main = source("app/main.js")
        start = main.index("function applyWorkspacePreference")
        end = main.index("function committedWorkspaceRoot", start)
        production_function = main[start:end]
        script = """
let workspaceCommittedRoot = "";
const diagnostics = [];
process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = "C:/broken";
process.env.TIANGONG_OMNI_BODY_WORKSPACE = "C:/broken";
function validateWorkspaceRoot(value) {
  if (value === "C:/broken") throw new Error("workspace_directory_invalid");
  return value;
}
function readWorkspacePreference() { return "C:/persisted"; }
function writeDesktopDiagnostic(kind, detail) { diagnostics.push([kind, String(detail)]); }
""" + production_function + """
applyWorkspacePreference();
console.log(JSON.stringify({
  desktop: process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT,
  omni: process.env.TIANGONG_OMNI_BODY_WORKSPACE,
  committed: workspaceCommittedRoot,
  diagnostics,
}));
"""
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True,
            text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertEqual(payload["desktop"], "C:/persisted")
        self.assertEqual(payload["omni"], "C:/persisted")
        self.assertEqual(payload["committed"], "C:/persisted")
        self.assertEqual(payload["diagnostics"][0][0], "workspace-environment-invalid")

    def test_source_restart_restores_committed_workspace_to_every_authority(self):
        main = source("app/main.js")
        start = main.index("function applyWorkspacePreference")
        end = main.index("function committedWorkspaceRoot", start)
        production_function = main[start:end]
        script = """
let workspaceCommittedRoot = "";
const SOURCE_MODE = true;
const SOURCE_ISOLATION = { workspaceRoot: "C:/source-default" };
process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT = "C:/source-default";
process.env.TIANGONG_WORKSPACE_ROOT = "C:/source-default";
process.env.TIANGONG_FORCE_WORKSPACE_ROOT = "C:/source-default";
process.env.TIANGONG_OMNI_BODY_WORKSPACE = "C:/source-default";
function sameWindowsPath(left, right) { return String(left).toLowerCase() === String(right).toLowerCase(); }
function validateWorkspaceRoot(value) { return String(value); }
function readWorkspacePreference() { return "C:/committed-repository"; }
function writeDesktopDiagnostic() {}
""" + production_function + """
applyWorkspacePreference();
console.log(JSON.stringify({
  desktop: process.env.TIANGONG_DESKTOP_WORKSPACE_ROOT,
  workspace: process.env.TIANGONG_WORKSPACE_ROOT,
  force: process.env.TIANGONG_FORCE_WORKSPACE_ROOT,
  omni: process.env.TIANGONG_OMNI_BODY_WORKSPACE,
  committed: workspaceCommittedRoot,
}));
"""
        completed = subprocess.run(
            ["node", "-e", script], cwd=ROOT, check=False, capture_output=True,
            text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertEqual(set(payload.values()), {"C:/committed-repository"})

    def test_model_updates_are_serialized_and_desktop_retries_are_idempotent(self):
        main = source("app/main.js")
        runtime = source("app/frontend-v2/renderer/runtime/http-runtime.mjs")
        self.assertIn("let modelSettingsChangeTail = Promise.resolve()", main)
        self.assertIn("modelSettingsChangeTail.then(() => secureModelSettingsUpdate", main)
        self.assertIn('handleTrusted("model:setSettings", async (_event, payload = {}) => queueSecureModelSettingsUpdate', main)
        self.assertIn("const SSE_STATUS_MISS_LIMIT = 80", runtime)
        self.assertIn("return this.sendStream(payload);", runtime)
        self.assertNotIn('apiJson("/api/v1/gateway/internal/inbound"', runtime)
        inbound_start = runtime.index('apiJson("/api/v1/gateway/desktop/inbound"')
        inbound_end = runtime.index("gatewayRequestId =", inbound_start)
        inbound = runtime[inbound_start:inbound_end]
        self.assertIn("message_id: requestId", inbound)
        self.assertNotIn('message_id: messageId("tg")', inbound)

    def test_tool_run_stage_reply_streams_through_gateway_and_final_replaces_it(self):
        module_url = (
            ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs"
        ).as_uri()
        gateway_request_id = "req_" + "a" * 64
        script = f"""
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
const values = new Map();
globalThis.localStorage = {{
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key)
}};
globalThis.tiangongDesktop = {{
  getGatewayUrl: () => "http://127.0.0.1:7184",
  getGatewayHeaders: () => ({{ "X-Tiangong-Token": "test" }})
}};
const gatewayRequestId = {json.dumps(gateway_request_id)};
const calls = [];
let nativeStatusCalls = 0;
let presentationStatusCalls = 0;
globalThis.fetch = async (url) => {{
  const parsed = new URL(String(url));
  calls.push(parsed.pathname + parsed.search);
  let payload;
  if (parsed.pathname === "/api/v1/llm/status") {{
    payload = {{ credential_state: "configured" }};
  }} else if (parsed.pathname === "/api/v1/gateway/desktop/inbound") {{
    payload = {{
      schema: "tiangong.gateway.desktop-inbound-acceptance.v1",
      ok: true,
      gateway_request_id: gatewayRequestId
    }};
  }} else if (parsed.pathname === "/api/v1/gateway/desktop/status") {{
    nativeStatusCalls += 1;
    const finished = nativeStatusCalls >= 5;
    payload = {{
      ok: true,
      gateway_request_id: gatewayRequestId,
      run: {{
        request_id: gatewayRequestId,
        gateway_request_id: gatewayRequestId,
        status: finished ? "COMPLETED" : "RUNNING",
        updated_at: String(nativeStatusCalls),
        ...(finished ? {{ final_response: "最终完成" }} : {{}})
      }},
      events: [],
      event_cursor: {{ next_seq: 0 }}
    }};
  }} else if (parsed.pathname === "/api/v1/run/status") {{
    presentationStatusCalls += 1;
    const stageReply = presentationStatusCalls === 1
      ? "第一版临时回复"
      : "第二版临时回复";
    payload = {{
      ok: true,
      gateway_request_id: gatewayRequestId,
      run: {{
        request_id: gatewayRequestId,
        gateway_request_id: gatewayRequestId,
        session_id: "session-1",
        status: "RUNNING",
        model_turns: presentationStatusCalls,
        last_model_content: stageReply,
        steps: []
      }},
      events: [{{
        seq: presentationStatusCalls,
        type: "MODEL_TURN_FINISHED",
        public: {{ tool_call_count: 1, context: {{}} }}
      }}],
      event_cursor: {{ next_seq: presentationStatusCalls }}
    }};
  }} else if (parsed.pathname === "/api/v1/artifacts") {{
    payload = {{
      schema: "tiangong.gateway.artifact-cards.v1",
      gateway_request_id: gatewayRequestId,
      presentation_request_id: gatewayRequestId,
      artifacts: []
    }};
  }} else {{
    throw new Error(`unexpected fetch ${{parsed.pathname}}`);
  }}
  return {{
    ok: true,
    status: 200,
    statusText: "OK",
    text: async () => JSON.stringify(payload)
  }};
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
const stageReplies = [];
const toolCalls = [];
const toolResults = [];
const result = await runtime.sendStream({{
  message: "检查并调用工具",
  requestId: "frontend-request-1",
  sessionId: "session-1"
}}, {{
  onStageText: (text) => stageReplies.push(text),
  onToolCall: (event) => toolCalls.push(event),
  onToolResult: (event) => toolResults.push(event)
}});
console.log(JSON.stringify({{ result, stageReplies, toolCalls, toolResults, calls }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertTrue(payload["result"]["ok"])
        self.assertEqual(payload["result"]["stdout"], "最终完成")
        self.assertEqual(payload["stageReplies"], ["第一版临时回复", "第二版临时回复"])
        self.assertTrue(
            any(call.startswith("/api/v1/gateway/desktop/status") for call in payload["calls"])
        )
        native_status_calls = [
            call for call in payload["calls"]
            if call.startswith("/api/v1/gateway/desktop/status")
        ]
        # The terminal waiter is the sole native-status reader.  Progress
        # presentation must not create a second concurrent native polling
        # loop for the same desktop request.
        self.assertEqual(len(native_status_calls), 5)
        self.assertTrue(
            any(call.startswith("/api/v1/run/status?") for call in payload["calls"])
        )

        conversation = source("app/frontend-v2/renderer/plugins/conversation-panel.mjs")
        self.assertIn('`${last.id || ""}\\u0000${last.content || ""}`', conversation)

    def test_late_status_poll_cannot_overwrite_terminal_reply(self):
        module_url = (
            ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs"
        ).as_uri()
        gateway_request_id = "req_" + "b" * 64
        script = f"""
globalThis.window = globalThis;
globalThis.location = {{ protocol: "file:", origin: "null" }};
const values = new Map();
globalThis.localStorage = {{
  getItem: (key) => values.has(key) ? values.get(key) : null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key)
}};
globalThis.tiangongDesktop = {{
  getGatewayUrl: () => "http://127.0.0.1:7184",
  getGatewayHeaders: () => ({{ "X-Tiangong-Token": "test" }})
}};
const gatewayRequestId = {json.dumps(gateway_request_id)};
globalThis.fetch = async (url) => {{
  const parsed = new URL(String(url));
  let payload;
  if (parsed.pathname === "/api/v1/llm/status") {{
    payload = {{ credential_state: "configured" }};
  }} else if (parsed.pathname === "/api/v1/gateway/desktop/inbound") {{
    payload = {{ schema: "tiangong.gateway.desktop-inbound-acceptance.v1", ok: true, gateway_request_id: gatewayRequestId }};
  }} else if (parsed.pathname === "/api/v1/gateway/desktop/status") {{
    payload = {{ ok: true, gateway_request_id: gatewayRequestId, run: {{ request_id: gatewayRequestId, gateway_request_id: gatewayRequestId, status: "COMPLETED", final_response: "FINAL" }} }};
  }} else if (parsed.pathname === "/api/v1/run/status") {{
    await new Promise((resolve) => setTimeout(resolve, 2200));
    payload = {{
      ok: true,
      gateway_request_id: gatewayRequestId,
      run: {{ request_id: gatewayRequestId, gateway_request_id: gatewayRequestId, status: "RUNNING", last_model_content: "STALE_STAGE" }},
      events: [{{ seq: 1, type: "MODEL_TURN_FINISHED", public: {{ tool_call_count: 1, context: {{}} }} }}],
      event_cursor: {{ next_seq: 1 }}
    }};
  }} else if (parsed.pathname === "/api/v1/artifacts") {{
    payload = {{ schema: "tiangong.gateway.artifact-cards.v1", gateway_request_id: gatewayRequestId, presentation_request_id: gatewayRequestId, artifacts: [] }};
  }} else {{
    throw new Error(`unexpected fetch ${{parsed.pathname}}`);
  }}
  return {{ ok: true, status: 200, statusText: "OK", text: async () => JSON.stringify(payload) }};
}};
const {{ createHttpRuntime }} = await import({json.dumps(module_url)});
const runtime = createHttpRuntime();
const stageReplies = [];
const result = await runtime.sendStream({{ message: "race", requestId: "frontend-race", sessionId: "s" }}, {{
  onStageText: (text) => stageReplies.push(text)
}});
await new Promise((resolve) => setTimeout(resolve, 1000));
console.log(JSON.stringify({{ result, stageReplies }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertTrue(payload["result"]["ok"])
        self.assertEqual(payload["result"]["stdout"], "FINAL")
        self.assertEqual(payload["stageReplies"], [])

    def test_frontend_kernel_never_projects_raw_html_error_pages(self):
        module_url = (
            ROOT / "app/frontend-v2/renderer/runtime/frontend-kernel.mjs"
        ).as_uri()
        html = "<!DOCTYPE HTML><html><body><h1>Forbidden</h1><script>secret</script></body></html>"
        script = f"""
const {{ createFrontendKernel }} = await import({json.dumps(module_url)});
const kernel = createFrontendKernel({{
  locationRef: {{ protocol: "http:", origin: "http://127.0.0.1:8765" }},
  fetchImpl: async () => ({{
    ok: false,
    status: 403,
    statusText: "Forbidden",
    text: async () => {json.dumps(html)},
  }}),
}});
const result = await kernel.optional("/api/v1/life/panel");
console.log(JSON.stringify({{
  ok: result.ok,
  code: result.error?.code,
  message: result.error?.message,
}}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script], cwd=ROOT,
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "backend_non_json")
        self.assertEqual(payload["message"], "后端返回了网页错误页或非 JSON 响应（状态码 403）。")
        self.assertNotIn("DOCTYPE", payload["message"])
        self.assertNotIn("script", payload["message"])

    def test_body_ui_separates_life_and_user_and_exposes_no_soul_editor(self):
        body = source("app/frontend-v2/renderer/plugins/body-panel.mjs")
        runtime = source("app/frontend-v2/renderer/runtime/http-runtime.mjs")
        conversation = source("app/frontend-v2/renderer/plugins/conversation-panel.mjs")
        conversation_css = source("app/frontend-v2/styles/conversation.css")

        self.assertIn("人物与称呼", body)
        self.assertIn("生命名字", body)
        self.assertIn("希望生命如何称呼你", body)
        self.assertIn("userCallsign", body)
        self.assertIn("userWork", body)
        self.assertIn("saveSettingsWithRetry", body)
        self.assertIn("attempt < 3", body)
        self.assertNotIn("bodySoulPrompt", body)
        self.assertNotIn("bodyClearSoul", body)
        self.assertNotIn("body-soul-card", body)
        # 新权威映射：callsign 与 userName 是独立字段，保存时各自回退到已存值，
        # 不再把 userName 当作 callsign 的回退来源。
        self.assertIn("name: next.userName ?? saved.userName", runtime)
        self.assertIn("callsign: next.userCallsign ?? saved.userCallsign", runtime)
        # userWork 与 userTitle 同样是独立字段，各自回退到已存值。
        self.assertIn("title: next.userTitle ?? saved.userTitle", runtime)
        self.assertIn("work: next.userWork ?? saved.userWork", runtime)
        self.assertIn("function renderUserAvatar(container, settings)", conversation)
        self.assertIn("renderUserAvatar(avatar, currentSettings)", conversation)
        self.assertIn('name.textContent = item.role === "user" ? userDisplayName(currentSettings)', conversation)
        self.assertIn('"userAvatarDataUrl"', conversation)
        self.assertIn("renderMessages(state.snapshot().messages)", conversation)
        self.assertIn(".message-avatar img", conversation_css)
        self.assertIn('import { renderUserAvatar } from "../core/user-avatar.mjs"', body)
        self.assertIn("renderUserAvatar(userAvatarEditor, { userAvatarDataUrl }", body)
        self.assertIn('import { renderUserAvatar as renderSharedUserAvatar }', conversation)

        message_start = conversation.index("function createMessageNode(item)")
        message_end = conversation.index("function progressAnchor(progress)", message_start)
        self.assertNotIn('avatar.textContent = "你"', conversation[message_start:message_end])

        for required in (
            "bodyChooseAvatar",
            "userChooseAvatar",
            "bodyChooseVoiceSample",
            "bodyTestVoice",
        ):
            self.assertIn(required, body)

    def test_vrm_local_frame_is_csp_allowed_and_navigation_is_bounded(self):
        index = source("app/frontend-v2/index.html")
        main = source("app/main.js")
        self.assertIn("frame-src 'self' file:", index)
        self.assertNotIn("frame-src 'none'", index)
        self.assertIn('normalizeFsPath(path.join(__dirname, "桌面宠物.html"))', main)
        self.assertIn('mainWindow.webContents.on("will-frame-navigate", (details)', main)
        self.assertIn('if (!isTrustedAppFrameUrl(details?.url || "")) details.preventDefault();', main)
        self.assertNotIn('on("console-message", (_event, level, message', main)
        self.assertIn('on("console-message", (details)', main)
        self.assertIn('details?.lineNumber', main)

    def test_vrc_avatar_intake_is_main_process_preflight_not_renderer_file_access(self):
        main = source("app/main.js")
        preload = source("app/preload.js")
        pet = source("app/桌面宠物.html")
        importer = source("app/vrc-import.js")
        self.assertIn('const { preflightVrcAvatarSource } = require("./vrc-import")', main)
        self.assertIn('handleTrusted("vrcImport:chooseSource"', main)
        self.assertIn('ipcRenderer.invoke("vrcImport:chooseSource", mode)', preload)
        self.assertIn('chooseVrcAvatarSource(\'file\')', pet)
        self.assertIn('chooseVrcAvatarSource(\'project\')', pet)
        self.assertIn('cache_or_downloaded_avatar_import_supported: false', importer)
        self.assertIn('source_symlink_rejected', importer)
        self.assertIn('".unitypackage"', importer)

    def test_vrm_import_uses_desktop_dialog_and_transactional_frame_protocol(self):
        main = source("app/main.js")
        preload = source("app/preload.js")
        inspector = source("app/frontend-v2/renderer/plugins/vrm-inspector-panel.mjs")
        avatar_panel = source("app/frontend-v2/renderer/plugins/avatar-panel.mjs")
        avatar_import = source(
            "app/frontend-v2/renderer/avatar/avatar-import-controller.mjs"
        )
        pet = source("app/桌面宠物.html")
        builder = source("electron-builder.config.cjs")
        exporter = source("scripts/export-core-source.py")
        avatar_release_gate = source(
            "scripts/verify-app-asar-avatar-contract.mjs"
        )
        builtin_manifest = json.loads(
            source("app/assets/avatar/builtin-models.json")
        )

        self.assertIn('"three":"./node_modules/three/build/three.module.js"', pet)
        self.assertIn('"three/addons/":"./node_modules/three/examples/jsm/"', pet)

        # 核心源码归档不携带无再分发权的 VRM/VRMA 二进制。仍须保留可验证
        # 的逻辑模型清单，并由主进程在运行时过滤缺失字节，避免悬空条目被
        # 自动加载；当前仅保留许可允许分发的 AvatarSample A 逻辑条目，正式
        # app.asar 则以独立硬门证明模型字节没有泄漏。
        models = builtin_manifest["models"]
        self.assertEqual(
            {model["id"] for model in models},
            {"tiangong-z1"},
        )
        for model in models:
            self.assertRegex(model["contentHash"], r"^[0-9a-f]{64}$")
            self.assertGreater(model["byteLength"], 1024)
            self.assertTrue(model["relativePath"].endswith(".vrm"))
        self.assertIn(
            'return resolved.startsWith(`${appRoot}${path.sep}`) && isFile(resolved);',
            main,
        )
        self.assertIn('"!assets/avatars/imported/*.vrm"', builder)
        self.assertIn('".vrm"', exporter)
        self.assertIn('".vrma"', exporter)
        for forbidden in (
            '"assets/avatars/imported/天工造物z1.vrm"',
            '"assets/avatars/imported/造物v2.vrm"',
        ):
            self.assertIn(forbidden, avatar_release_gate)

        self.assertIn('handleTrusted("dialog:chooseVrmModel"', main)
        self.assertIn('filters: [{ name: "VRM 模型", extensions: ["vrm"] }]', main)
        self.assertIn("MAX_VRM_IMPORT_BYTES", main)
        self.assertIn('ipcRenderer.invoke("dialog:chooseVrmModel")', preload)
        self.assertIn('postVrmCommand(bodyFrame, "import-model"', inspector)
        self.assertIn('"tiangong-vrm-import-result"', inspector)
        self.assertIn("event.source !== bodyFrame?.contentWindow", inspector)
        self.assertIn("async function importCustomVRMBuffer", pet)
        self.assertIn("if(!/\\.vrm$/i.test(name))", pet)
        self.assertIn("data.type!=='tiangong-vrm-command'", pet)
        self.assertIn('type: "tiangong-vrm-ready"', pet)

        # direct 模式的窄桥与事务提交链也必须保持接通；素材是否随源码分发
        # 不得削弱用户从桌面选择并导入本机 VRM 的能力。
        self.assertIn('handleTrusted("avatar:chooseImportFile"', main)
        self.assertIn('handleTrusted("avatar:commitCandidate"', main)
        self.assertIn('ipcRenderer.invoke("avatar:chooseImportFile")', preload)
        self.assertIn('ipcRenderer.invoke("avatar:commitCandidate"', preload)
        self.assertIn("bridge.importCustomModel()", avatar_panel)
        self.assertIn("desktop.avatarImport.commitCandidate", avatar_import)
        self.assertIn("data-avatar-empty-state", avatar_panel)
        self.assertIn("尚未添加身体模型", avatar_panel)
        self.assertIn('modelSelect.disabled = models.length === 0', avatar_panel)
        self.assertIn('result?.status === "cancelled"', avatar_panel)
        self.assertIn('result?.code === "user_cancelled"', avatar_panel)
        self.assertIn("describeAvatarProjection", avatar_panel)
        self.assertIn(".vrm-empty-state", source("app/frontend-v2/styles/vrm-inspector.css"))

        importer = pet[pet.index("async function importCustomVRMBuffer"):pet.index("// Load VRM from the legacy local file picker")]
        self.assertLess(importer.index("await loadVRM(buffer,name)"), importer.index("await saveVRMToDB(buffer)"))
        self.assertLess(importer.index("await saveVRMToDB(buffer)"), importer.index("localStorage.setItem(CUSTOM_VRM_LABEL_KEY,name)"))

        loader = pet[pet.index("function loadVRM(arrayBuffer"):pet.index("function getSavedVRM()")]
        # P3 共享引擎提取后：loadVRM 委托 avatarHarness.loadModelFromBytes 解析；
        # 失败经 catch 重新抛出（错误传播契约保持）。
        self.assertIn("avatarHarness.loadModelFromBytes(arrayBuffer,{label})", loader)
        self.assertIn("throw err", loader)

    def test_local_open_button_uses_os_result_and_never_claims_false_success(self):
        main = source("app/main.js")
        preload = source("app/preload.js")
        renderer = source("app/frontend-v2/renderer/core/message-renderer.mjs")

        self.assertIn('handleTrusted("shell:openPath"', main)
        self.assertIn("const error = await shell.openPath(target);", main)
        self.assertIn("return { ok: !error, error: error || \"\", path: target };", main)
        self.assertIn('ipcRenderer.invoke("shell:openPath", targetPath)', preload)
        self.assertIn('buttonState(button, "打开失败")', renderer)
        self.assertIn('buttonState(button, opened ? "已打开" : "打开失败")', renderer)

    def test_clean_source_setup_bootstraps_hashing_and_electron_explicitly(self):
        provision = source("scripts/provision-embedded-python.ps1")
        setup = source("scripts/setup-source.ps1")
        node_installer = source("scripts/install-node-dependencies.mjs")

        self.assertIn("function Get-FileDigest", provision)
        self.assertIn("[Security.Cryptography.MD5]::Create()", provision)
        self.assertIn("[Security.Cryptography.SHA256]::Create()", provision)
        self.assertNotIn("Get-FileHash", provision)

        self.assertIn('"install-node-dependencies.mjs"', setup)
        self.assertIn('["--prefix", appRoot, "ci", "--ignore-scripts"]', node_installer)
        self.assertIn('join(electronRoot, "install.js")', node_installer)
        self.assertIn("ELECTRON_FALLBACK_MIRROR", node_installer)
        self.assertIn('"node_modules\\electron\\dist\\electron.exe"', setup)


if __name__ == "__main__":
    unittest.main()
